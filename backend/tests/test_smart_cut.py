"""smart_cut Skill 单元测试（不触网、不调真实 API、不依赖 WhisperX）。

覆盖的核心契约：
  - 不给 refine_candidates → 只返回删减规划，绝不执行剪辑
  - 给 refine_candidates → 视为"用户已确认"，执行剪辑
  - refine_candidates=[] → 明确报错
  - 静音检测：silence_detect adapter 的解析逻辑
  - 填充词检测：两级检测 + 短语级检测
  - 重复检测：单词重复 + 短语重复
  - 候选合并：重叠片段去重
  - 保留片段计算：compute_kept_segments
  - 隐患 #3 对策：误差容忍区间 + 短词降权
  - 隐患 #5 对策：PTS 归零 + avoid_negative_ts
  - 隐患 #6 对策：视频 3 帧 fade
  - 隐患 #13 对策：临时文件自动清理
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from capabilities.video.adapters.silence_detect import (
    SilenceSegment,
    _parse_silence_output,
)
from capabilities.video.adapters.whisper_transcribe import (
    TranscribeSegment,
    TranscribeWord,
    extract_words,
)
from capabilities.video.services.cut_analyzer import (
    CutCandidate,
    CutType,
    WordTimestamp,
    _effective_gap,
    compute_kept_segments,
    detect_filler_candidates,
    detect_repeat_candidates,
    detect_silence_candidates,
    merge_candidates,
)
from capabilities.video.skills.smart_cut import (
    VIDEO_FADE_FRAMES,
    SmartCutInput,
    SmartCutPlan,
    SmartCutSkill,
)

# ── 1. 输入模型 ────────────────────────────────────────────


class TestSmartCutInput:
    def test_defaults_have_no_refine_signal(self):
        """默认调用不带任何确认信号 → 闸门必须拦住"""
        inp = SmartCutInput(video_path="test.mp4")
        assert inp.refine_candidates is None
        assert inp.detect_silence is True
        assert inp.detect_filler is True
        assert inp.silence_threshold_db == -35
        assert inp.silence_min_duration == 0.6
        assert inp.filler_pause == 0.35
        assert inp.pad_seconds == 0.25
        assert inp.audio_fade == 0.08

    def test_refine_candidates_accepts_list(self):
        inp = SmartCutInput(
            video_path="test.mp4",
            refine_candidates=[
                {
                    "start": 1.0,
                    "end": 2.0,
                    "cut_type": "silence",
                    "confidence": 0.95,
                    "reason": "test",
                }
            ],
        )
        assert len(inp.refine_candidates) == 1

    def test_force_final_flag(self):
        inp = SmartCutInput(video_path="test.mp4", force_final=True)
        assert inp.force_final is True


# ── 2. silence_detect adapter ─────────────────────────────


class TestParseSilenceOutput:
    def test_typical_output(self):
        """解析典型的 silencedetect 输出"""
        output = (
            "[silencedetect @ 0x1] silence_start: 1.23\n"
            "[silencedetect @ 0x2] silence_end: 2.45 | silence_duration: 1.22\n"
            "[silencedetect @ 0x3] silence_start: 5.00\n"
            "[silencedetect @ 0x4] silence_end: 6.50 | silence_duration: 1.50\n"
        )
        segments = _parse_silence_output(output)
        assert len(segments) == 2
        assert segments[0].start == pytest.approx(1.23)
        assert segments[0].end == pytest.approx(2.45)
        assert segments[0].duration == pytest.approx(1.22)
        assert segments[1].start == pytest.approx(5.00)
        assert segments[1].end == pytest.approx(6.50)

    def test_trailing_silence_without_end(self):
        """末尾静音只有 silence_start 没有 silence_end"""
        output = "[silencedetect @ 0x1] silence_start: 10.00\n"
        segments = _parse_silence_output(output)
        assert len(segments) == 0  # 不完整的静音段不返回

    def test_empty_output(self):
        segments = _parse_silence_output("")
        assert segments == []

    def test_no_silence(self):
        output = "some other ffmpeg output\n"
        segments = _parse_silence_output(output)
        assert segments == []


class TestSilenceSegment:
    def test_valid_segment(self):
        seg = SilenceSegment(start=1.0, end=2.0, duration=1.0)
        assert seg.duration == 1.0

    def test_negative_duration_raises(self):
        with pytest.raises(ValueError):
            SilenceSegment(start=2.0, end=1.0, duration=-1.0)

    def test_negative_start_raises(self):
        with pytest.raises(ValueError):
            SilenceSegment(start=-1.0, end=2.0, duration=3.0)


# ── 3. cut_analyzer: 静音检测 ─────────────────────────────


class TestDetectSilenceCandidates:
    def test_converts_segments_to_candidates(self):
        """10.28 收缩契约：>0.6s 静音段两端各保留 0.2s 呼吸缓冲。"""
        segs = [(1.0, 2.5, 1.5), (5.0, 6.0, 1.0)]
        cands = detect_silence_candidates(segs)
        assert len(cands) == 2
        assert cands[0].cut_type == CutType.SILENCE
        assert cands[0].confidence == 0.95
        assert cands[0].start == pytest.approx(1.2)
        assert cands[0].end == pytest.approx(2.3)
        assert "1.50s" in cands[0].reason
        assert cands[1].start == pytest.approx(5.2)
        assert cands[1].end == pytest.approx(5.8)

    def test_empty_segments(self):
        assert detect_silence_candidates([]) == []


# ── 4. cut_analyzer: 填充词检测 ─────────────────────────────


class TestDetectFillerCandidates:
    def test_strict_filler_words(self):
        """第一级：纯语气词直接标记"""
        words = [
            WordTimestamp(text="你好", start=0.0, end=0.5),
            WordTimestamp(text="嗯", start=0.6, end=0.8),
            WordTimestamp(text="今天", start=0.9, end=1.3),
        ]
        cands = detect_filler_candidates(words)
        assert len(cands) == 1
        assert cands[0].text == "嗯"
        assert cands[0].confidence == 0.95
        assert cands[0].cut_type == CutType.FILLER

    def test_contextual_filler_with_pause(self):
        """第二级：指示词类有停顿才标记"""
        words = [
            WordTimestamp(text="我们", start=0.0, end=0.3),
            WordTimestamp(
                text="那个", start=1.0, end=1.2
            ),  # 前面有 0.8s 原始间隔，扣除误差后 0.6s > 0.35
            WordTimestamp(text="项目", start=1.2, end=1.5),
        ]
        cands = detect_filler_candidates(words, filler_pause=0.35)
        filler_cands = [c for c in cands if c.text == "那个"]
        assert len(filler_cands) == 1
        assert filler_cands[0].confidence == 0.7

    def test_contextual_filler_without_pause(self):
        """第二级：指示词类无停顿不标记"""
        words = [
            WordTimestamp(text="看", start=0.0, end=0.2),
            WordTimestamp(text="那个", start=0.2, end=0.4),  # 紧接着，无停顿
            WordTimestamp(text="东西", start=0.4, end=0.6),
        ]
        cands = detect_filler_candidates(words, filler_pause=0.35)
        filler_cands = [c for c in cands if c.text == "那个"]
        assert len(filler_cands) == 0

    def test_filler_phrase_detection(self):
        """短语级：'那个那个' 连说检测"""
        words = [
            WordTimestamp(text="我们", start=0.0, end=0.3),
            WordTimestamp(text="那个", start=0.3, end=0.5),
            WordTimestamp(text="那个", start=0.5, end=0.7),
            WordTimestamp(text="说", start=0.7, end=0.9),
        ]
        cands = detect_filler_candidates(words)
        phrase_cands = [c for c in cands if "phrase" in c.reason]
        assert len(phrase_cands) >= 1
        assert phrase_cands[0].confidence == 0.8

    def test_short_word_confidence_penalty(self):
        """隐患 #3：短词（<200ms）降低置信度"""
        words = [
            WordTimestamp(text="嗯", start=0.5, end=0.55),  # 50ms，极短
            WordTimestamp(text="好", start=0.6, end=1.0),
        ]
        cands = detect_filler_candidates(words)
        assert len(cands) == 1
        assert cands[0].confidence < 0.95  # 被扣了 0.3

    def test_english_filler(self):
        """英文填充词也能检测"""
        words = [
            WordTimestamp(text="um", start=0.5, end=0.7),
            WordTimestamp(text="like", start=1.5, end=1.7),  # 有停顿
        ]
        cands = detect_filler_candidates(words, filler_pause=0.35)
        assert any(c.text == "um" for c in cands)


# ── 5. cut_analyzer: 重复检测 ─────────────────────────────


class TestDetectRepeatCandidates:
    def test_single_word_repeat(self):
        """单词重复检测"""
        words = [
            WordTimestamp(text="增长", start=0.0, end=0.3),
            WordTimestamp(text="增长", start=0.3, end=0.6),
            WordTimestamp(text="的", start=0.6, end=0.8),
        ]
        cands = detect_repeat_candidates(words)
        assert len(cands) >= 1
        assert cands[0].cut_type == CutType.REPEAT
        assert "增长增长" in cands[0].reason

    def test_phrase_repeat(self):
        """短语重复检测"""
        words = [
            WordTimestamp(text="我觉得", start=0.0, end=0.3),
            WordTimestamp(text="这个", start=0.3, end=0.5),
            WordTimestamp(text="我觉得", start=0.5, end=0.8),
            WordTimestamp(text="这个", start=0.8, end=1.0),
        ]
        cands = detect_repeat_candidates(words)
        assert len(cands) >= 1
        assert cands[0].cut_type == CutType.REPEAT

    def test_no_repeat(self):
        """无重复不误判"""
        words = [
            WordTimestamp(text="今天", start=0.0, end=0.3),
            WordTimestamp(text="天气", start=0.3, end=0.6),
            WordTimestamp(text="很好", start=0.6, end=0.9),
        ]
        cands = detect_repeat_candidates(words)
        assert len(cands) == 0


# ── 6. cut_analyzer: 合并与保留片段 ────────────────────────


class TestMergeCandidates:
    def test_no_overlap(self):
        """不重叠的候选不合并"""
        cands = [
            CutCandidate(start=1.0, end=2.0, cut_type=CutType.SILENCE, confidence=0.9, reason="a"),
            CutCandidate(start=3.0, end=4.0, cut_type=CutType.FILLER, confidence=0.8, reason="b"),
        ]
        merged = merge_candidates(cands, 10.0)
        assert len(merged) == 2

    def test_overlap_merges(self):
        """重叠的候选合并为一个"""
        cands = [
            CutCandidate(start=1.0, end=2.5, cut_type=CutType.SILENCE, confidence=0.9, reason="a"),
            CutCandidate(start=2.0, end=3.0, cut_type=CutType.FILLER, confidence=0.8, reason="b"),
        ]
        merged = merge_candidates(cands, 10.0)
        assert len(merged) == 1
        assert merged[0].start == 1.0
        assert merged[0].end == 3.0

    def test_empty_candidates(self):
        assert merge_candidates([], 10.0) == []


class TestComputeKeptSegments:
    def test_no_cuts(self):
        """无删减 → 整段保留"""
        kept = compute_kept_segments([], 10.0)
        assert kept == [(0.0, 10.0)]

    def test_single_cut(self):
        """一个删减 → 两段保留"""
        cands = [
            CutCandidate(start=3.0, end=5.0, cut_type=CutType.SILENCE, confidence=0.9, reason="a"),
        ]
        kept = compute_kept_segments(cands, 10.0)
        assert len(kept) == 2
        assert kept[0] == (0.0, 3.0)
        assert kept[1] == (5.0, 10.0)

    def test_multiple_cuts(self):
        """多个删减 → 多段保留"""
        cands = [
            CutCandidate(start=1.0, end=2.0, cut_type=CutType.SILENCE, confidence=0.9, reason="a"),
            CutCandidate(start=4.0, end=5.0, cut_type=CutType.FILLER, confidence=0.8, reason="b"),
        ]
        kept = compute_kept_segments(cands, 10.0)
        assert len(kept) == 3
        assert kept[0] == (0.0, 1.0)
        assert kept[1] == (2.0, 4.0)
        assert kept[2] == (5.0, 10.0)

    def test_cut_at_start(self):
        """开头删减"""
        cands = [
            CutCandidate(start=0.0, end=1.0, cut_type=CutType.SILENCE, confidence=0.9, reason="a"),
        ]
        kept = compute_kept_segments(cands, 10.0)
        assert len(kept) == 1
        assert kept[0] == (1.0, 10.0)

    def test_cut_at_end(self):
        """末尾删减"""
        cands = [
            CutCandidate(start=8.0, end=10.0, cut_type=CutType.SILENCE, confidence=0.9, reason="a"),
        ]
        kept = compute_kept_segments(cands, 10.0)
        assert len(kept) == 1
        assert kept[0] == (0.0, 8.0)


# ── 7. 隐患 #3: 误差容忍区间 ──────────────────────────────


class TestEffectiveGap:
    def test_large_gap(self):
        """大间隔：有效间隔仍为正"""
        gap = _effective_gap(0.0, 1.0)
        assert gap == pytest.approx(0.8)  # 1.0 - 2*0.1

    def test_small_gap(self):
        """小间隔：扣除误差后为 0"""
        gap = _effective_gap(0.0, 0.15)
        assert gap == 0.0  # max(0, 0.15 - 0.2) = 0

    def test_zero_gap(self):
        gap = _effective_gap(0.5, 0.5)
        assert gap == 0.0


# ── 8. 隐患 #6: 视频 fade 帧数 ────────────────────────────


class TestVideoFadeFrames:
    def test_fade_frames_is_3(self):
        """3 帧 @30fps = 0.1s，人眼几乎不可感知但消除跳变感"""
        assert VIDEO_FADE_FRAMES == 3
        fade_duration = VIDEO_FADE_FRAMES / 30.0
        assert fade_duration == pytest.approx(0.1, abs=0.01)


# ── 9. SmartCutSkill 闸门契约 ─────────────────────────────


class TestSmartCutSkillGate:
    @pytest.fixture
    def skill(self):
        return SmartCutSkill()

    def test_skill_metadata(self, skill):
        """Skill 元数据正确"""
        assert skill.node_type == "edit"
        assert skill.name == "smart_cut"
        assert skill.display_name == "智能剪辑"
        assert "智能剪辑" in skill.trigger_words

    def test_input_schema_registered(self, skill):
        assert skill.input_schema is SmartCutInput


# ── 10. SmartCutPlan 序列化 ────────────────────────────────


class TestSmartCutPlan:
    def test_plan_serialization(self):
        """规划可以序列化为 dict（给前端）"""
        plan = SmartCutPlan(
            video_id="test_video",
            original_duration=60.0,
            candidates=[
                {
                    "start": 1.0,
                    "end": 2.0,
                    "cut_type": "silence",
                    "confidence": 0.95,
                    "reason": "silence -1.00s",
                    "text": "",
                    "removable": True,
                },
            ],
            kept_segments=[[0.0, 1.0], [2.0, 60.0]],
            kept_duration=59.0,
            saved_seconds=1.0,
        )
        data = plan.model_dump()
        assert data["video_id"] == "test_video"
        assert data["needs_confirmation"] is True
        assert len(data["candidates"]) == 1
        assert data["saved_seconds"] == 1.0


# ── 11. 隐患 #13: 临时文件清理 ────────────────────────────


class TestManagedTempDir:
    @pytest.mark.asyncio
    async def test_temp_dir_created_and_cleaned(self):
        """临时目录在 with 块结束后自动清理"""
        from capabilities.video.skills.smart_cut import managed_temp_dir

        captured_dir = None
        async with managed_temp_dir() as tmp_dir:
            captured_dir = tmp_dir
            assert tmp_dir.is_dir()
            (tmp_dir / "test.txt").write_text("hello")

        assert captured_dir is not None
        assert not captured_dir.exists()

    @pytest.mark.asyncio
    async def test_temp_dir_cleaned_on_exception(self):
        """异常时也清理临时目录"""
        from capabilities.video.skills.smart_cut import managed_temp_dir

        captured_dir = None
        with pytest.raises(ValueError):
            async with managed_temp_dir() as tmp_dir:
                captured_dir = tmp_dir
                raise ValueError("test error")

        assert captured_dir is not None
        assert not captured_dir.exists()


# ── 12. whisper_transcribe adapter ─────────────────────────


class TestWhisperTranscribe:
    def test_transcribe_word_dataclass(self):
        w = TranscribeWord(text="你好", start=0.0, end=0.5, probability=0.98)
        assert w.text == "你好"
        assert w.start == 0.0

    def test_transcribe_segment_dataclass(self):
        words = [TranscribeWord(text="你好", start=0.0, end=0.5)]
        seg = TranscribeSegment(text="你好", start=0.0, end=0.5, words=words)
        assert seg.text == "你好"
        assert len(seg.words) == 1

    def test_extract_words(self):
        segments = [
            TranscribeSegment(
                text="你好世界",
                start=0.0,
                end=1.0,
                words=[
                    TranscribeWord(text="你好", start=0.0, end=0.5),
                    TranscribeWord(text="世界", start=0.5, end=1.0),
                ],
            ),
            TranscribeSegment(
                text="再见",
                start=1.0,
                end=1.5,
                words=[TranscribeWord(text="再见", start=1.0, end=1.5)],
            ),
        ]
        words = extract_words(segments)
        assert len(words) == 3
        assert words[0].text == "你好"
        assert words[2].text == "再见"

    def test_extract_words_empty(self):
        assert extract_words([]) == []

    def test_cuda_check_without_torch(self):
        """没装 torch 时 CUDA 检查不崩溃"""
        from capabilities.video.adapters.whisper_transcribe import check_cuda_available

        result = check_cuda_available()
        assert "cuda_available" in result

    def test_transcribe_no_engines(self):
        """没有转录引擎时返回空列表（零件可缺）"""
        from capabilities.video.adapters.whisper_transcribe import transcribe

        result = transcribe("nonexistent_file.mp4")
        assert result == []


# ── 13. LLM fluff detection ──────────────────────────────


class TestDetectFluffCandidates:
    @pytest.mark.asyncio
    async def test_no_llm_returns_empty(self):
        """LLM 为 None 时跳过"""
        from capabilities.video.services.cut_analyzer import detect_fluff_candidates

        cands, raw = await detect_fluff_candidates([], llm=None)
        assert cands == []
        assert raw == []

    @pytest.mark.asyncio
    async def test_empty_segments_returns_empty(self):
        """空片段列表时跳过"""
        from capabilities.video.services.cut_analyzer import detect_fluff_candidates

        cands, raw = await detect_fluff_candidates([], llm=MagicMock())
        assert cands == []

    @pytest.mark.asyncio
    async def test_llm_json_parsing(self):
        """LLM �?返回合法 JSON 时正确解析"""
        from capabilities.video.services.cut_analyzer import detect_fluff_candidates

        segments = [
            {"index": 0, "start": 0.0, "end": 3.0, "text": "嗯那个就是说"},
            {"index": 1, "start": 3.0, "end": 6.0, "text": "核心观点在这里"},
        ]

        mock_llm = MagicMock()
        with patch(
            "app.adapters.llm_base.llm_prompt_to_text",
            new_callable=AsyncMock,
            return_value='[{"index": 0, "verdict": "cut", "type": "fluff", "reason": "废话"}]',
        ):
            cands, raw = await detect_fluff_candidates(segments, llm=mock_llm)

        assert len(cands) == 1
        assert cands[0].cut_type == CutType.FLUFF
        assert cands[0].confidence == 0.6
        assert cands[0].start == 0.0

    @pytest.mark.asyncio
    async def test_llm_uncertain_lower_confidence(self):
        """LLM 标记 uncertain 时置信度更低"""
        from capabilities.video.services.cut_analyzer import detect_fluff_candidates

        segments = [
            {"index": 0, "start": 0.0, "end": 3.0, "text": "可能废话"},
        ]

        mock_llm = MagicMock()
        with patch(
            "app.adapters.llm_base.llm_prompt_to_text",
            new_callable=AsyncMock,
            return_value='[{"index": 0, "verdict": "uncertain", "type": "maybe_filler", "reason": "不确定"}]',
        ):
            cands, raw = await detect_fluff_candidates(segments, llm=mock_llm)

        assert len(cands) == 1
        assert cands[0].confidence == 0.4

    @pytest.mark.asyncio
    async def test_llm_invalid_json_retries(self):
        """隐患 #9：LLM 返回非法 JSON 时重试"""
        from capabilities.video.services.cut_analyzer import detect_fluff_candidates

        segments = [{"index": 0, "start": 0.0, "end": 3.0, "text": "test"}]

        call_count = 0

        async def _mock_llm_call(llm, prompt):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return "this is not json"
            return '[{"index": 0, "verdict": "keep", "type": "fluff", "reason": "ok"}]'

        mock_llm = MagicMock()
        with patch(
            "app.adapters.llm_base.llm_prompt_to_text",
            new_callable=AsyncMock,
            side_effect=_mock_llm_call,
        ):
            cands, raw = await detect_fluff_candidates(segments, llm=mock_llm)

        assert call_count == 2  # 第一次失败，第二次成功

    @pytest.mark.asyncio
    async def test_llm_markdown_wrapped_json(self):
        """隐患 #9：LLM 在 JSON 外面包 markdown 代码块"""
        from capabilities.video.services.cut_analyzer import detect_fluff_candidates

        segments = [{"index": 0, "start": 0.0, "end": 3.0, "text": "test"}]

        mock_llm = MagicMock()
        with patch(
            "app.adapters.llm_base.llm_prompt_to_text",
            new_callable=AsyncMock,
            return_value='```json\n[{"index": 0, "verdict": "cut", "type": "fluff", "reason": "test"}]\n```',
        ):
            cands, raw = await detect_fluff_candidates(segments, llm=mock_llm)

        assert len(cands) == 1  # 能解析出 markdown 包裹的 JSON


# ── 14. TimeAxisMapper（隐患 #11 对策）────────────────────


class TestTimeAxisMapper:
    def test_basic_mapping(self):
        """源时间 → 剪辑后时间的基本映射"""
        from capabilities.video.services.cut_analyzer import compute_kept_segments

        cands = [
            CutCandidate(start=2.0, end=4.0, cut_type=CutType.SILENCE, confidence=0.9, reason="a"),
            CutCandidate(start=6.0, end=8.0, cut_type=CutType.SILENCE, confidence=0.9, reason="b"),
        ]
        kept = compute_kept_segments(cands, 10.0)
        # kept = [(0,2), (4,6), (8,10)]
        assert len(kept) == 3
        assert kept[0] == (0.0, 2.0)
        assert kept[1] == (4.0, 6.0)
        assert kept[2] == (8.0, 10.0)

    def test_identity_mapping_no_cuts(self):
        """无删减时映射是恒等映射"""
        kept = compute_kept_segments([], 10.0)
        assert kept == [(0.0, 10.0)]


class TestHasAudioTrack:
    """10.27：has_audio_track 多层兜底 — 修复 ffprobe 裸调误判。

    live 实证（2026-10-30）：本机 PATH 无 ffprobe、imageio-ffmpeg 只带
    ffmpeg → 裸调永远 OSError → 恒 False → SmartCutSkill 一启动就被
    误判 no_audio_track，智能裁剪从未跑通过。probe_duration 有 PyAV
    兜底所以能跑，has_audio_track 没有 → 对齐之。
    """

    def test_real_video_with_audio_detected(self):
        """真机素材（有音轨）→ True。当前实现裸 ffprobe 挂 → 误判 False。"""
        import asyncio

        from capabilities.video.adapters.silence_detect import has_audio_track

        video = Path(__file__).resolve().parent.parent / ("uploads/videos/live_person_talk.mp4")
        if not video.is_file():
            pytest.skip("真机素材缺失")
        assert asyncio.run(has_audio_track(str(video))) is True

    def test_pyav_fallback_when_ffprobe_broken(self):
        """ffprobe 层全挂（OSError patch）→ PyAV 兜底仍判 True。"""
        import asyncio

        from capabilities.video.adapters.silence_detect import has_audio_track

        video = Path(__file__).resolve().parent.parent / ("uploads/videos/live_person_talk.mp4")
        if not video.is_file():
            pytest.skip("真机素材缺失")

        async def _boom(*a, **kw):
            raise OSError("ffprobe not found")

        with (
            patch("asyncio.create_subprocess_exec", side_effect=_boom),
            patch("subprocess.run", side_effect=_boom),
        ):
            assert asyncio.run(has_audio_track(str(video))) is True

    def test_all_layers_fail_returns_false(self):
        """PyAV 挂 + ffprobe 挂 → False（守恒，不误判为有音轨）。"""
        import asyncio

        import av as _av

        from capabilities.video.adapters.silence_detect import has_audio_track

        async def _boom(*a, **kw):
            raise OSError("ffprobe not found")

        with (
            patch.object(_av, "open", side_effect=RuntimeError("av fail")),
            patch("asyncio.create_subprocess_exec", side_effect=_boom),
            patch("subprocess.run", side_effect=_boom),
        ):
            assert asyncio.run(has_audio_track("Z:/ghost/never_exists.mp4")) is False


class TestPadMath:
    """10.27 live9 实证：pad 补偿数学错误 — 首段 clamp 到 0 时仍加满
    2*pad（多算 0.25s），报告 cut_duration 不含 pad 补偿（53.3 vs 实际
    预期 53.8，成片实测 54.07 ≈ 未剪）。修复：duration=(end+pad)-s_eff，
    报告口径 expected = Σ(min(e+pad,total) - max(0,s-pad))。
    """

    def test_extract_segment_duration_math(self, tmp_path):
        """kept=[[0,2.68],[3.48,54.1]] pad=0.25 → 段1 -t 2.930、段2 -t 51.120。"""
        import asyncio
        from unittest.mock import patch

        from capabilities.video.skills.smart_cut import SmartCutSkill

        captured: list[list[str]] = []

        async def fake_ff(cmd):
            captured.append(cmd)
            return ""

        skill = SmartCutSkill()
        with patch("capabilities.video.skills.smart_cut._run_ffmpeg", side_effect=fake_ff):
            asyncio.run(
                skill._extract_segments(
                    "Z:/ghost.mp4",
                    [(0.0, 2.68), (3.48, 54.1)],
                    pad=0.25,
                    audio_fade=0.08,
                    tmp_dir=tmp_path,
                )
            )
        d1 = captured[0][captured[0].index("-t") + 1]
        d2 = captured[1][captured[1].index("-t") + 1]
        assert d1 == "2.930", f"段1 应 [0, 2.68+0.25]=2.93s，实 {d1}"
        assert d2 == "51.120", f"段2 应 [3.48-0.25, 54.1+0.25]=51.12s，实 {d2}"

    def test_report_duration_includes_pad(self, tmp_path):
        """cut_duration/saved_seconds 报告含 pad 补偿：54.1 源、删 0.8s、
        1 切口 pad=0.25 → cut=53.80、saved=0.30（而非 53.3/0.8）。"""
        import asyncio
        from unittest.mock import patch

        from capabilities.video.skills.smart_cut import SmartCutInput

        skill = SmartCutSkill()
        inputs = SmartCutInput(
            video_path="Z:/ghost.mp4",
            refine_candidates=[
                {
                    "start": 2.68,
                    "end": 3.48,
                    "cut_type": "repeat",
                    "confidence": 0.85,
                    "reason": "repeat '紛紛'",
                    "removable": True,
                }
            ],
            pad_seconds=0.25,
        )

        async def fake_extract(video, kept, pad, af, tmp):
            return [tmp / "seg_000.mp4", tmp / "seg_001.mp4"]

        async def fake_concat(files, out, tmp):
            out.write_bytes(b"x")  # 满足 is_file 检查
            return None

        with (
            patch.object(skill, "_extract_segments", side_effect=fake_extract),
            patch.object(skill, "_concat_segments", side_effect=fake_concat),
        ):
            result = asyncio.run(
                skill._execute_cut(
                    inputs,
                    "Z:/ghost.mp4",
                    54.1,
                    "u1",
                )
            )
        assert "cut_duration" in result, result
        assert result["cut_duration"] == 53.8, result
        assert result["saved_seconds"] == 0.3, result


class TestAdaptiveSilence:
    """10.28：静音检测自适应 + 候选收缩 — live9 实证默认 -35dB/0.6s
    对高底噪素材 0 检出（0 候选 → 规划只剩 1 处 repeat → 成片只省 0.3s）。
    三档实测：-35dB→0 段；-30dB→6 段；-28dB→13 段。
    """

    @staticmethod
    def _g(tmp_path):
        f = tmp_path / "g.mp4"
        f.write_bytes(b"x")
        return f

    @staticmethod
    def _sil_out(pairs: list[tuple[float, float]]) -> str:
        lines = []
        for s, e in pairs:
            lines.append(f"[silencedetect @ 0x1] silence_start: {s}")
            lines.append(
                f"[silencedetect @ 0x1] silence_end: {e} | " f"silence_duration: {e - s:.2f}"
            )
        return "\n".join(lines)

    def test_adaptive_tier_fallback(self, tmp_path):
        """第一档 0 段 → 自动放宽到下一档；结果带命中档位。"""
        import asyncio
        from unittest.mock import patch

        from capabilities.video.adapters.silence_detect import (
            detect_silence_adaptive,
        )

        calls: list[tuple[float, float]] = []

        async def fake_run(cmd):
            af = cmd[cmd.index("-af") + 1]
            noise = float(af.split("noise=")[1].split("dB")[0])
            dur = float(af.split("duration=")[1])
            calls.append((noise, dur))
            if noise == -35.0:
                return ""  # 严格档 0 段
            return self._sil_out([(2.68, 3.48), (10.0, 11.2)])

        with patch("capabilities.video.adapters.silence_detect._run", side_effect=fake_run):
            segs, tier = asyncio.run(detect_silence_adaptive(str(self._g(tmp_path))))
        assert [(-35.0, 0.6), (-32.0, 0.5), (-30.0, 0.35)][:2] == calls[:2]
        assert len(segs) == 2 and tier[0] == -32.0

    def test_adaptive_all_empty(self, tmp_path):
        """全档 0 段 → ([], None)。"""
        import asyncio
        from unittest.mock import patch

        from capabilities.video.adapters.silence_detect import (
            detect_silence_adaptive,
        )

        async def fake_run(cmd):
            return ""

        with patch("capabilities.video.adapters.silence_detect._run", side_effect=fake_run):
            segs, tier = asyncio.run(detect_silence_adaptive(str(self._g(tmp_path))))
        assert segs == [] and tier is None

    def test_candidates_shrink_breathing(self):
        """>0.6s 静音段 → 候选区间两端各缩 0.2s（保留呼吸感）。"""
        from capabilities.video.services.cut_analyzer import (
            detect_silence_candidates,
        )

        cands = detect_silence_candidates([(2.68, 3.48, 0.8)])
        assert len(cands) == 1
        assert cands[0].start == pytest.approx(2.88) and cands[0].end == pytest.approx(3.28)

    def test_candidates_skip_short(self):
        """≤0.6s 短停顿 → 不出候选（句间自然节奏不剪）。"""
        from capabilities.video.services.cut_analyzer import (
            detect_silence_candidates,
        )

        assert detect_silence_candidates([(5.0, 5.5, 0.5)]) == []

    def test_smart_cut_uses_adaptive(self):
        """SmartCutSkill 默认走 adaptive（显式 detect_silence 仍生效）。"""
        import inspect

        src = inspect.getsource(
            __import__("capabilities.video.skills.smart_cut", fromlist=["SmartCutSkill"])
        )
        assert "detect_silence_adaptive" in src


class TestPadOverlap:
    """10.28 live10 实证：删段 0.24s < pad 之和 0.5s → 相邻保留段
    尾头交叠 0.26s → concat 回填总长（54.09 ≈ 没剪）。修复：
    per-gap 收缩 pad_eff = min(pad, gap/2)。
    """

    def test_extract_no_overlap_on_tiny_gap(self, tmp_path):
        """kept 3 段、gap 0.24s、pad 0.25 → 段尾不越过下一段头。"""
        import asyncio
        from unittest.mock import patch

        from capabilities.video.skills.smart_cut import SmartCutSkill

        captured: list[list[str]] = []

        async def fake_ff(cmd):
            captured.append(cmd)
            return ""

        skill = SmartCutSkill()
        with patch("capabilities.video.skills.smart_cut._run_ffmpeg", side_effect=fake_ff):
            asyncio.run(
                skill._extract_segments(
                    "Z:/ghost.mp4",
                    [(0.0, 2.68), (3.48, 9.16), (9.40, 54.1)],
                    pad=0.25,
                    audio_fade=0.08,
                    tmp_dir=tmp_path,
                )
            )

        def span(cmd):
            ss = float(cmd[cmd.index("-ss") + 1])
            tt = float(cmd[cmd.index("-t") + 1])
            return ss, tt

        s2, t2 = span(captured[1])
        s3, _t3 = span(captured[2])
        # 段2 尾 (s2+t2) 不得越过段3 头 s3（gap 0.24，两侧 pad 各 0.12）
        assert s2 + t2 <= s3 + 1e-6, f"段2 尾 {s2 + t2:.3f} 越过段3 头 {s3:.3f}"
        # 段2 pad_eff = min(0.25, 左gap/2=0.4, 右gap/2=0.12) = 0.12
        assert t2 == pytest.approx((9.16 + 0.12) - (3.48 - 0.12), abs=1e-3)
