"""auto_short_video 确认闸门的单元测试（不触网、不调真实 API）。

覆盖的核心契约（与 multi_image_video 同一条硬约束）：
  - 不给 refine_shots → 只返回分镜草稿，绝不生成配图/配音/成片
  - 给 refine_shots  → 视为"用户已确认"，从生成配图开始执行
  - refine_shots=[]  → 明确报错
  - refine_title      → 标题不被退化成 topic 占位
  - 运镜字段 motion / motion_prompt 双向互认，卡片编辑不丢
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from capabilities.video.skills.auto_short_video import (
    AutoShortVideoInput,
    AutoShortVideoSkill,
    _build_transcript,
    _normalize_shot,
)


SCRIPT = {
    "title": "深圳周末去哪儿",
    "hook": "别再去人挤人的地方了",
    "shots": [
        {"caption": "第一句口播", "image_prompt": "city skyline", "duration": 4.0, "motion": "ken-burns"},
        {"caption": "第二句口播", "image_prompt": "street food", "duration": 4.0, "motion": "ken-burns"},
    ],
    "closing": "关注我",
}


@pytest.fixture
def skill():
    return AutoShortVideoSkill()


@pytest.fixture
def fake_compose(tmp_path, monkeypatch):
    """把产物目录挪到 tmp_path，并让 compose_video 真的写出一个文件。

    技能在 compose 返回后会 read_bytes() 入库，Mock 返回一个不存在的 Path
    会在那里炸掉，测试就变成"合成失败"路径，断言的全是错的东西。
    """
    monkeypatch.setattr("capabilities.video.skills.auto_short_video._OUTPUT_DIR", tmp_path)
    captured: dict = {}

    async def _compose(storyboard, output_path, **kwargs):
        captured["storyboard"] = storyboard
        p = Path(output_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"\x00\x00\x00\x18ftypmp42")
        return p

    monkeypatch.setattr(
        "capabilities.video.adapters.video_compose.compose_video", AsyncMock(side_effect=_compose)
    )
    return captured


# ── 1. 输入模型 ────────────────────────────────────────────

class TestAutoShortVideoInput:
    def test_defaults_have_no_refine_signal(self):
        """默认调用不带任何确认信号 → 闸门必须拦住"""
        inp = AutoShortVideoInput(topic="深圳周末去哪儿")
        assert inp.refine_shots is None
        assert inp.refine_title is None
        assert inp.target_duration == 30.0
        assert inp.ratio == "9:16"

    def test_topic_min_length(self):
        with pytest.raises(Exception):
            AutoShortVideoInput(topic="x")

    def test_refine_shots_accepts_list(self):
        inp = AutoShortVideoInput(topic="测试主题", refine_shots=[{"caption": "a"}], refine_title="标题")
        assert inp.refine_shots == [{"caption": "a"}]
        assert inp.refine_title == "标题"


# ── 2. 运镜字段归一化 ──────────────────────────────────────

class TestNormalizeShot:
    def test_motion_mirrored_to_motion_prompt(self):
        out = _normalize_shot({"motion": "ken-burns"})
        assert out["motion_prompt"] == "ken-burns"

    def test_motion_prompt_mirrored_back_to_motion(self):
        """前端卡片只写 motion_prompt，回放时技能读的是 motion"""
        out = _normalize_shot({"motion_prompt": "推进"})
        assert out["motion"] == "推进"

    def test_existing_values_not_overwritten(self):
        out = _normalize_shot({"motion": "static", "motion_prompt": "推进"})
        assert out["motion"] == "static"
        assert out["motion_prompt"] == "推进"


# ── 3. 确认闸门 ────────────────────────────────────────────

@pytest.mark.asyncio
class TestConfirmGate:
    async def test_default_call_stops_at_draft_and_never_generates(self, skill):
        """不传 refine_shots → 停在草稿，配图/配音/合成绝不执行"""
        with patch(
            "capabilities.video.skills.auto_short_video._generate_script", new=AsyncMock(return_value=SCRIPT)
        ) as gen, patch(
            "capabilities.video.skills.auto_short_video._generate_shot_images", new=AsyncMock()
        ) as images, patch(
            "capabilities.video.skills.auto_short_video._generate_narration", new=AsyncMock()
        ) as narration:
            result = await skill.execute({"topic": "深圳周末去哪儿", "_user_id": "u1"})

        assert result["mode"] == "preview"
        assert result["needs_confirmation"] is True
        assert result["title"] == "深圳周末去哪儿"
        assert len(result["shots"]) == 2
        # 闸门的关键：付费/耗时的生成函数一次都不能被调用
        images.assert_not_called()
        narration.assert_not_called()
        gen.assert_called_once()

    async def test_confirm_hint_directs_caller_to_refine_shots(self, skill):
        """草稿必须告诉调用方下一步该带什么参数回来"""
        with patch(
            "capabilities.video.skills.auto_short_video._generate_script", new=AsyncMock(return_value=SCRIPT)
        ):
            result = await skill.execute({"topic": "深圳周末去哪儿", "_user_id": "u1"})

        assert "refine_shots" in result["confirm_hint"]
        assert "未经用户确认" in result["confirm_hint"]

    async def test_draft_shots_carry_motion_prompt_for_card_editing(self, skill):
        """草稿里的镜头要带 motion_prompt，前端分镜卡片的编辑才不会落空"""
        with patch(
            "capabilities.video.skills.auto_short_video._generate_script", new=AsyncMock(return_value=SCRIPT)
        ):
            result = await skill.execute({"topic": "深圳周末去哪儿", "_user_id": "u1"})

        assert all(s.get("motion_prompt") for s in result["shots"])

    async def test_empty_script_returns_error_not_draft(self, skill):
        with patch(
            "capabilities.video.skills.auto_short_video._generate_script",
            new=AsyncMock(return_value={"title": "t", "shots": []}),
        ):
            result = await skill.execute({"topic": "深圳周末去哪儿", "_user_id": "u1"})

        assert "error" in result

    async def test_refine_empty_list_returns_error(self, skill):
        with patch(
            "capabilities.video.skills.auto_short_video._generate_shot_images", new=AsyncMock()
        ) as images:
            result = await skill.execute({
                "topic": "深圳周末去哪儿",
                "refine_shots": [],
                "_user_id": "u1",
            })

        assert "error" in result
        images.assert_not_called()

    async def test_refine_shots_skips_script_generation(self, skill, fake_compose):
        """用户已确认 → 不再消耗一次 LLM 重编脚本"""
        edited = [
            {"caption": "改过的第一句", "image_prompt": "a", "duration": 5.0, "motion_prompt": "推进"},
        ]
        with patch(
            "capabilities.video.skills.auto_short_video._generate_script", new=AsyncMock(return_value=SCRIPT)
        ) as gen, patch(
            "capabilities.video.skills.auto_short_video._generate_shot_images",
            new=AsyncMock(side_effect=lambda shots, ratio, uid: shots),
        ), patch(
            "capabilities.video.skills.auto_short_video._generate_narration",
            new=AsyncMock(return_value=(None, "none")),
        ), patch(
            "app.services.asset_library.save_video_asset",
            new=AsyncMock(return_value={
                "id": "v1", "original_url": "/f/final.mp4", "file_size": 123,
            }),
        ):
            result = await skill.execute({
                "topic": "深圳周末去哪儿",
                "refine_shots": edited,
                "refine_title": "深圳周末去哪儿（改）",
                "need_narration": False,
                "_user_id": "u1",
            })

        gen.assert_not_called()
        assert result["success"] is True
        assert result["title"] == "深圳周末去哪儿（改）"

    async def test_refine_without_title_falls_back_to_topic(self, skill, fake_compose):
        """没回传标题时退回 topic，不能是空字符串"""
        with patch(
            "capabilities.video.skills.auto_short_video._generate_script", new=AsyncMock(return_value=SCRIPT)
        ) as gen, patch(
            "capabilities.video.skills.auto_short_video._generate_shot_images",
            new=AsyncMock(side_effect=lambda shots, ratio, uid: shots),
        ), patch(
            "app.services.asset_library.save_video_asset",
            new=AsyncMock(return_value={
                "id": "v1", "original_url": "/f/final.mp4", "file_size": 123,
            }),
        ):
            result = await skill.execute({
                "topic": "深圳周末去哪儿",
                "refine_shots": [{"caption": "c", "image_prompt": "a", "duration": 3.0}],
                "need_narration": False,
                "_user_id": "u1",
            })

        gen.assert_not_called()
        assert result["success"] is True
        assert result["title"] == "深圳周末去哪儿"

    async def test_refine_keeps_user_edited_motion(self, skill, fake_compose):
        """卡片里改的运镜要真的进入 storyboard"""
        with patch(
            "capabilities.video.skills.auto_short_video._generate_shot_images",
            new=AsyncMock(side_effect=lambda shots, ratio, uid: shots),
        ), patch(
            "app.services.asset_library.save_video_asset",
            new=AsyncMock(return_value={
                "id": "v1", "original_url": "/f/final.mp4", "file_size": 123,
            }),
        ):
            result = await skill.execute({
                "topic": "深圳周末去哪儿",
                "refine_shots": [{"caption": "c", "image_prompt": "a", "duration": 3.0, "motion_prompt": "拉远"}],
                "need_narration": False,
                "_user_id": "u1",
            })

        assert result["success"] is True
        shot = fake_compose["storyboard"]["shots"][0]
        assert shot["motion"] == "拉远"
        assert shot["motion_prompt"] == "拉远"


# ── 4. 技能元数据 ──────────────────────────────────────────

class TestSkillMetadata:
    def test_registered(self):
        skill = AutoShortVideoSkill()
        assert skill.name == "auto_short_video"
        assert skill.node_type == "produce"

    def test_input_schema_exposes_refine_fields(self):
        fields = AutoShortVideoInput.model_fields
        assert "refine_shots" in fields
        assert "refine_title" in fields


# ── 5. Step 4.5 叠加增强内嵌（接入主业务管线）─────────────

class _FakeOverlaySkill:
    """替身 talking_head_overlay — 记录调用参数，按脚本吐事件。"""

    calls: list[dict] = []
    script: list[dict] = []  # 每次 execute yield 的事件序列

    def __init__(self):
        pass

    async def execute(self, params):
        _FakeOverlaySkill.calls.append(params)
        for ev in _FakeOverlaySkill.script:
            yield ev


@pytest.mark.asyncio
class TestOverlayIntegration:
    async def test_默认不增强_overlay从未实例化(self, skill, fake_compose):
        """enhance_overlay 缺省 False — 主管线零改动零影响。"""
        _FakeOverlaySkill.calls = []
        with patch(
            "capabilities.video.skills.auto_short_video._generate_script",
            new=AsyncMock(return_value=SCRIPT),
        ), patch(
            "capabilities.video.skills.auto_short_video._generate_shot_images",
            new=AsyncMock(side_effect=lambda shots, ratio, uid: shots),
        ), patch(
            "capabilities.video.skills.auto_short_video._generate_narration",
            new=AsyncMock(return_value=(None, "none")),
        ), patch(
            "app.services.asset_library.save_video_asset",
            new=AsyncMock(return_value={"id": "v1", "original_url": "/f/final.mp4", "file_size": 123}),
        ), patch(
            "capabilities.video.skills.talking_head_overlay.TalkingHeadOverlaySkill",
            new=_FakeOverlaySkill,
        ):
            result = await skill.execute({
                "topic": "深圳周末去哪儿",
                "refine_shots": [{"caption": "c", "image_prompt": "a", "duration": 3.0}],
                "need_narration": False,
                "_user_id": "u1",
            })

        assert result["success"] is True
        assert _FakeOverlaySkill.calls == []  # 不增强绝不碰 overlay
        assert "enhanced_asset_id" not in result

    async def test_增强成功_产出双资产(self, skill, fake_compose, tmp_path):
        """enhance_overlay=True → overlay 收到成片路径 + AUTO_CONFIRM，
        增强片另存资产（不覆盖原片），双 asset 返回。"""
        enhanced_mp4 = tmp_path / "enhanced.mp4"
        enhanced_mp4.write_bytes(b"\x00\x00\x00\x18ftypmp42")
        _FakeOverlaySkill.calls = []
        _FakeOverlaySkill.script = [
            {"event": "overlay_plan_ready", "data": {}},
            {"event": "overlay_compose_done", "data": {"output_path": str(enhanced_mp4)}},
        ]
        saved = []

        async def _save(**kw):
            saved.append(kw)
            n = len(saved)
            return {"id": f"v{n}", "original_url": f"/f/out{n}.mp4", "file_size": 456}

        with patch(
            "capabilities.video.skills.auto_short_video._generate_script",
            new=AsyncMock(return_value=SCRIPT),
        ), patch(
            "capabilities.video.skills.auto_short_video._generate_shot_images",
            new=AsyncMock(side_effect=lambda shots, ratio, uid: shots),
        ), patch(
            "capabilities.video.skills.auto_short_video._generate_narration",
            new=AsyncMock(return_value=(None, "none")),
        ), patch(
            "app.services.asset_library.save_video_asset", new=AsyncMock(side_effect=_save),
        ), patch(
            "capabilities.video.skills.talking_head_overlay.TalkingHeadOverlaySkill",
            new=_FakeOverlaySkill,
        ):
            result = await skill.execute({
                "topic": "深圳周末去哪儿",
                "refine_shots": [{"caption": "c", "image_prompt": "a", "duration": 3.0}],
                "need_narration": False,
                "enhance_overlay": True,
                "_user_id": "u1",
            })

        assert result["success"] is True
        assert result["asset_id"] == "v1"
        assert result["enhanced_asset_id"] == "v2"
        assert result["enhanced_url"] == "/f/out2.mp4"
        # overlay 收到成片路径 + 无人值守模式
        assert len(_FakeOverlaySkill.calls) == 1
        assert _FakeOverlaySkill.calls[0]["gate_mode"] == "auto_confirm"
        assert _FakeOverlaySkill.calls[0]["video_path"].endswith("final.mp4")
        # 增强片元数据带链路溯源
        assert saved[1]["metadata"]["chained_from"] == "auto_short_video"
        assert saved[1]["filename"].endswith("_enhanced.mp4")

    async def test_增强失败_降级返回原成片(self, skill, fake_compose):
        """overlay_error / 零件缺失 → success=True + 降级 note，主管线不阻断。"""
        _FakeOverlaySkill.calls = []
        _FakeOverlaySkill.script = [
            {"event": "overlay_error", "data": {"error": "whisper 不可用"}},
        ]
        with patch(
            "capabilities.video.skills.auto_short_video._generate_script",
            new=AsyncMock(return_value=SCRIPT),
        ), patch(
            "capabilities.video.skills.auto_short_video._generate_shot_images",
            new=AsyncMock(side_effect=lambda shots, ratio, uid: shots),
        ), patch(
            "capabilities.video.skills.auto_short_video._generate_narration",
            new=AsyncMock(return_value=(None, "none")),
        ), patch(
            "app.services.asset_library.save_video_asset",
            new=AsyncMock(return_value={"id": "v1", "original_url": "/f/final.mp4", "file_size": 123}),
        ), patch(
            "capabilities.video.skills.talking_head_overlay.TalkingHeadOverlaySkill",
            new=_FakeOverlaySkill,
        ):
            result = await skill.execute({
                "topic": "深圳周末去哪儿",
                "refine_shots": [{"caption": "c", "image_prompt": "a", "duration": 3.0}],
                "need_narration": False,
                "enhance_overlay": True,
                "_user_id": "u1",
            })

        assert result["success"] is True  # 主成片照常
        assert result["asset_id"] == "v1"
        assert "enhanced_asset_id" not in result
        assert any("降级返回原成片" in d for d in result["degradation"])

    async def test_增强_传入已知文本时间轴(self, skill, fake_compose, tmp_path):
        """上游有 script 原文 → transcript 传给 overlay（跳过 whisper）。

        实机依据（2026-10-28）：whisper 对 TTS 合成音频误听率高
        （"90%"→"九十辈子"），内嵌路径必须喂原文。
        """
        enhanced_mp4 = tmp_path / "enhanced.mp4"
        enhanced_mp4.write_bytes(b"\x00\x00\x00\x18ftypmp42")
        _FakeOverlaySkill.calls = []
        _FakeOverlaySkill.script = [
            {"event": "overlay_compose_done", "data": {"output_path": str(enhanced_mp4)}},
        ]
        with patch(
            "capabilities.video.skills.auto_short_video._generate_script",
            new=AsyncMock(return_value=SCRIPT),
        ), patch(
            "capabilities.video.skills.auto_short_video._generate_shot_images",
            new=AsyncMock(side_effect=lambda shots, ratio, uid: shots),
        ), patch(
            "capabilities.video.skills.auto_short_video._generate_narration",
            new=AsyncMock(return_value=(None, "none")),
        ), patch(
            "app.services.asset_library.save_video_asset",
            new=AsyncMock(return_value={"id": "v1", "original_url": "/f/final.mp4", "file_size": 123}),
        ), patch(
            "capabilities.video.skills.talking_head_overlay.TalkingHeadOverlaySkill",
            new=_FakeOverlaySkill,
        ), patch(
            "capabilities.video.adapters.silence_detect.probe_duration",
            new=AsyncMock(return_value=8.0),
        ):
            await skill.execute({
                "topic": "深圳周末去哪儿",
                "refine_shots": [
                    {"caption": "第一句口播", "image_prompt": "a", "duration": 4.0},
                    {"caption": "第二句口播", "image_prompt": "b", "duration": 4.0},
                ],
                "need_narration": False,
                "enhance_overlay": True,
                "_user_id": "u1",
            })

        transcript = _FakeOverlaySkill.calls[0]["transcript"]
        # 原文逐字来自 script（不是 whisper 转写产物）
        assert [s["text"] for s in transcript] == ["第一句口播", "第二句口播"]
        # 时间轴覆盖整片成片实际时长
        assert transcript[0]["start"] == 0.0
        assert transcript[-1]["end"] == 8.0
        # 词级时间戳存在且词边界与段边界对齐（规划按词对齐叠加起点）
        assert transcript[0]["words"][0]["start"] == 0.0
        assert transcript[0]["words"][-1]["end"] == transcript[0]["end"]
        assert transcript[1]["words"][-1]["end"] == 8.0


# ── 5. 已知文本时间轴构造（跳过 whisper）──────────────────

class TestBuildTranscript:
    def test_empty_shots(self):
        assert _build_transcript([], 10.0) == []

    def test_zero_duration(self):
        assert _build_transcript([{"caption": "句子", "duration": 3.0}], 0.0) == []

    def test_single_shot_spans_whole_video(self):
        tr = _build_transcript([{"caption": "只有一句话", "duration": 100.0}], 7.5)
        assert tr[0]["start"] == 0.0
        assert tr[0]["end"] == 7.5

    def test_proportional_to_shot_duration(self):
        """镜时长比例 = 时间轴比例（合成时长与脚本时长不同 → 归一化缩放）。"""
        tr = _build_transcript([
            {"caption": "第一段", "duration": 4.0},
            {"caption": "第二段", "duration": 1.0},
        ], 10.0)
        assert tr[0]["end"] == 8.0
        assert tr[1]["start"] == 8.0
        assert tr[1]["end"] == 10.0

    def test_words_monotonic_and_cover_span(self):
        tr = _build_transcript([{"caption": "字幕要对齐每个词", "duration": 3.0}], 3.0)
        words = tr[0]["words"]
        assert len(words) >= 2
        assert words[0]["start"] == 0.0
        assert words[-1]["end"] == 3.0
        for prev, cur in zip(words, words[1:]):
            assert cur["start"] >= prev["start"] - 1e-6
            assert cur["end"] >= prev["end"] - 1e-6
        assert "".join(w["text"] for w in words) == "字幕要对齐每个词"

    def test_empty_caption_skipped(self):
        tr = _build_transcript([
            {"caption": "有文案", "duration": 3.0},
            {"caption": "   ", "duration": 3.0},
        ], 6.0)
        assert len(tr) == 1
