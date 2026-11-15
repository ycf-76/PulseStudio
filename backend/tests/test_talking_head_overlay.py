"""叠加增强 Skill 测试 — Phase 3。

覆盖：
  - overlay_orchestrator（模板库、时序编排、锚点重映射、冲突检测）
  - overlay_templates（HTML 模板渲染）
  - html_to_video（Playwright 零件可缺）
  - talking_head_overlay（闸门契约）
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from capabilities.video.services.overlay_orchestrator import (
    OverlayPlan,
    OverlaySegment,
    OverlayTemplate,
    TimeMapper,
    detect_overlay_conflicts,
    remap_overlay_anchors,
    sort_overlays_by_priority,
    TEMPLATE_PRIORITY,
    TEMPLATE_LAYER,
)


# ── 1. OverlayTemplate 枚举 ──────────────────────────────

class TestOverlayTemplate:
    def test_all_templates_defined(self):
        assert len(OverlayTemplate) == 16  # v4: +arrow_point +circle_mark +full_card +split_compare

    def test_template_values(self):
        assert OverlayTemplate.CAPTION_POP.value == "caption_pop"
        assert OverlayTemplate.TITLE_CARD.value == "title_card"
        assert OverlayTemplate.STAT_CARD.value == "stat_card"


# ── 2. OverlaySegment / OverlayPlan 模型 ─────────────────

class TestOverlayModels:
    def test_overlay_segment_creation(self):
        seg = OverlaySegment(
            segment_id="o1",
            time_start=0.0,
            time_end=3.0,
            template=OverlayTemplate.CAPTION_POP,
            params={"text": "增长200%", "font_size": 48},
        )
        assert seg.segment_id == "o1"
        assert seg.template == OverlayTemplate.CAPTION_POP
        assert not seg.skipped

    def test_overlay_plan_creation(self):
        plan = OverlayPlan(
            video_id="test",
            segments=[
                OverlaySegment(
                    segment_id="o1", time_start=0.0, time_end=3.0,
                    template=OverlayTemplate.TITLE_CARD,
                    params={"title": "标题"},
                ),
            ],
        )
        assert len(plan.segments) == 1
        assert plan.needs_confirmation is True


# ── 3. TimeMapper（锚点重映射）───────────────────────────

class TestTimeMapper:
    def test_identity_mapping(self):
        """无删减时映射是恒等映射"""
        mapper = TimeMapper([(0.0, 10.0)])
        assert mapper.map(0.0) == 0.0
        assert mapper.map(5.0) == 5.0
        assert mapper.map(10.0) == 10.0

    def test_cut_middle(self):
        """删减中间段：[0,2] + [4,10] → [0,2] + [2,8]"""
        mapper = TimeMapper([(0.0, 2.0), (4.0, 10.0)])
        assert mapper.map(0.0) == 0.0
        assert mapper.map(2.0) == 2.0
        assert mapper.map(4.0) == 2.0
        assert mapper.map(7.0) == 5.0
        assert mapper.map(10.0) == 8.0

    def test_cut_returns_none_for_deleted_time(self):
        """被删时刻返回 None"""
        mapper = TimeMapper([(0.0, 2.0), (4.0, 10.0)])
        assert mapper.map(3.0) is None

    def test_total_cut_duration(self):
        mapper = TimeMapper([(0.0, 2.0), (4.0, 10.0)])
        assert mapper.total_cut_duration() == 8.0

    def test_multiple_cuts(self):
        """多处删减"""
        mapper = TimeMapper([(0.0, 2.0), (4.0, 6.0), (8.0, 10.0)])
        assert mapper.map(0.0) == 0.0
        assert mapper.map(4.0) == 2.0
        assert mapper.map(8.0) == 4.0
        assert mapper.total_cut_duration() == 6.0


# ── 4. remap_overlay_anchors ─────────────────────────────

class TestRemapOverlayAnchors:
    def test_remap_basic(self):
        plan = OverlayPlan(
            video_id="test",
            segments=[
                OverlaySegment(
                    segment_id="o1", time_start=0.0, time_end=2.0,
                    template=OverlayTemplate.CAPTION_POP,
                ),
                OverlaySegment(
                    segment_id="o2", time_start=4.0, time_end=6.0,
                    template=OverlayTemplate.TITLE_CARD,
                ),
            ],
        )
        kept = [(0.0, 2.0), (4.0, 10.0)]
        remapped = remap_overlay_anchors(plan, kept)

        assert remapped.segments[0].time_start == 0.0
        assert remapped.segments[0].time_end == 2.0
        assert remapped.segments[1].time_start == 2.0
        assert remapped.segments[1].time_end == 4.0

    def test_remap_skips_deleted_overlay(self):
        """叠加在删减段内 → 标记 skipped"""
        plan = OverlayPlan(
            video_id="test",
            segments=[
                OverlaySegment(
                    segment_id="o1", time_start=2.5, time_end=3.5,
                    template=OverlayTemplate.CAPTION_POP,
                ),
            ],
        )
        kept = [(0.0, 2.0), (4.0, 10.0)]
        remapped = remap_overlay_anchors(plan, kept)

        assert remapped.segments[0].skipped is True


# ── 5. sort_overlays_by_priority ─────────────────────────

class TestSortOverlays:
    def test_priority_sorting(self):
        segments = [
            OverlaySegment(segment_id="o1", time_start=0, time_end=3, template=OverlayTemplate.CAPTION_POP),
            OverlaySegment(segment_id="o2", time_start=0, time_end=3, template=OverlayTemplate.TITLE_CARD),
            OverlaySegment(segment_id="o3", time_start=0, time_end=3, template=OverlayTemplate.STAT_CARD),
        ]
        sorted_segs = sort_overlays_by_priority(segments)
        assert sorted_segs[0].template == OverlayTemplate.TITLE_CARD
        assert sorted_segs[1].template == OverlayTemplate.STAT_CARD
        assert sorted_segs[2].template == OverlayTemplate.CAPTION_POP


# ── 6. detect_overlay_conflicts ──────────────────────────

class TestDetectConflicts:
    def test_no_conflict_different_layers(self):
        segments = [
            OverlaySegment(segment_id="o1", time_start=0, time_end=5, template=OverlayTemplate.CAPTION_POP),
            OverlaySegment(segment_id="o2", time_start=0, time_end=5, template=OverlayTemplate.STAT_CARD),
        ]
        conflicts = detect_overlay_conflicts(segments)
        assert len(conflicts) == 0

    def test_conflict_same_layer(self):
        segments = [
            OverlaySegment(segment_id="o1", time_start=0, time_end=5, template=OverlayTemplate.CAPTION_POP),
            OverlaySegment(segment_id="o2", time_start=3, time_end=8, template=OverlayTemplate.CAPTION_KARAOKE),
        ]
        conflicts = detect_overlay_conflicts(segments)
        assert len(conflicts) == 1

    def test_no_conflict_non_overlapping(self):
        segments = [
            OverlaySegment(segment_id="o1", time_start=0, time_end=3, template=OverlayTemplate.CAPTION_POP),
            OverlaySegment(segment_id="o2", time_start=3, time_end=6, template=OverlayTemplate.CAPTION_KARAOKE),
        ]
        conflicts = detect_overlay_conflicts(segments)
        assert len(conflicts) == 0


# ── 7. HTML 模板渲染 ─────────────────────────────────────

class TestOverlayTemplates:
    def test_caption_pop_html(self):
        from capabilities.video.adapters.overlay_templates import caption_pop
        html = caption_pop(text="增长 200%", width=1080, height=1920, duration=3.0)
        assert "增长" in html
        assert "200%" in html
        assert "window.seekTo" in html  # v2: 确定性时间轴替代 CSS keyframes
        assert "1080px" in html

    def test_caption_karaoke_html(self):
        from capabilities.video.adapters.overlay_templates import caption_karaoke
        html = caption_karaoke(text="核心观点", width=1080, height=1920, duration=2.0)
        # v2: jieba 分词后词分别渲染（v1 整句一词是中文分词 bug）
        assert "核心" in html and "观点" in html
        assert "window.seekTo" in html

    def test_keyword_icon_html(self):
        from capabilities.video.adapters.overlay_templates import keyword_icon
        html = keyword_icon(keyword="增长", icon="📈", animation="fly_in_right")
        assert "增长" in html
        assert "📈" in html
        assert "window.seekTo" in html

    def test_stat_card_html(self):
        from capabilities.video.adapters.overlay_templates import stat_card
        html = stat_card(label="增长率", value="200%", icon="📊")
        assert "增长率" in html
        assert "200%" in html
        assert "📊" in html

    def test_title_card_html(self):
        from capabilities.video.adapters.overlay_templates import title_card
        html = title_card(title="3个增长秘诀", subtitle="深度解析")
        assert "3个增长秘诀" in html
        assert "深度解析" in html
        assert "window.seekTo" in html

    def test_templates_dict(self):
        from capabilities.video.adapters.overlay_templates import TEMPLATES
        assert "caption_pop" in TEMPLATES
        assert "caption_karaoke" in TEMPLATES
        assert "keyword_icon" in TEMPLATES
        assert "stat_card" in TEMPLATES
        assert "title_card" in TEMPLATES


# ── 8. html_to_video 零件可缺 ────────────────────────────

class TestHtmlToVideo:
    @pytest.mark.asyncio
    async def test_render_without_playwright(self):
        """没装 playwright 时返回 None（零件可缺）"""
        from capabilities.video.adapters.html_to_video import render_html_to_video
        with patch.dict("sys.modules", {"playwright": None, "playwright.async_api": None}):
            result = await render_html_to_video(
                html_content="<html></html>",
                output_path="/tmp/test.mp4",
            )
            # playwright 未安装时返回 None（不崩溃）

    @pytest.mark.asyncio
    async def test_compose_no_overlays(self):
        """无叠加层时直接复制视频"""
        from capabilities.video.adapters.html_to_video import compose_with_overlays
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
            f.write(b"fake video")
            tmp_path = f.name
        try:
            output = Path(tmp_path).parent / "output.mp4"
            result = await compose_with_overlays(
                base_video=tmp_path,
                overlay_videos=[],
                output_path=output,
            )
            assert result == output
        finally:
            Path(tmp_path).unlink(missing_ok=True)
            output.unlink(missing_ok=True)


# ── 9. LLM overlay plan generation ──────────────────────

class TestGenerateOverlayPlan:
    @pytest.mark.asyncio
    async def test_no_llm_returns_empty(self):
        from capabilities.video.services.overlay_orchestrator import generate_overlay_plan
        plan, raw = await generate_overlay_plan([], duration=10.0, llm=None)
        assert len(plan.segments) == 0

    @pytest.mark.asyncio
    async def test_llm_plan_parsing(self):
        from capabilities.video.services.overlay_orchestrator import generate_overlay_plan

        segments = [{"index": 0, "start": 0.0, "end": 3.0, "text": "增长秘诀"}]
        mock_llm = MagicMock()

        with patch(
            "app.adapters.llm_base.llm_prompt_to_text",
            new_callable=AsyncMock,
            return_value='[{"segment_id": "o1", "time_start": 0.0, "time_end": 3.0, "template": "title_card", "params": {"title": "增长秘诀"}}]',
        ):
            plan, raw = await generate_overlay_plan(segments, duration=10.0, llm=mock_llm)

        assert len(plan.segments) == 1
        assert plan.segments[0].template == OverlayTemplate.TITLE_CARD

    @pytest.mark.asyncio
    async def test_llm_invalid_template_kept_for_p1_generation(self):
        """P1 行为变更：未知模板名不再丢弃 — 保留为 str，
        渲染前由 template_generator 现场生成（失败才降级 skip）。"""
        from capabilities.video.services.overlay_orchestrator import (
            generate_overlay_plan,
            template_name,
        )

        segments = [{"index": 0, "start": 0.0, "end": 3.0, "text": "test"}]
        mock_llm = MagicMock()

        with patch(
            "app.adapters.llm_base.llm_prompt_to_text",
            new_callable=AsyncMock,
            return_value='[{"segment_id": "o1", "time_start": 0, "time_end": 3, "template": "nonexistent_template", "params": {}}]',
        ):
            plan, raw = await generate_overlay_plan(segments, duration=10.0, llm=mock_llm)

        assert len(plan.segments) == 1
        assert template_name(plan.segments[0].template) == "nonexistent_template"


# ── 10. talking_head_overlay 闸门契约 ────────────────────

class TestTalkingHeadOverlayGate:
    def test_skill_metadata(self):
        from capabilities.video.skills.talking_head_overlay import TalkingHeadOverlaySkill
        assert TalkingHeadOverlaySkill.node_type == "edit"
        assert TalkingHeadOverlaySkill.name == "talking_head_overlay"
        assert TalkingHeadOverlaySkill.needs_confirmation is True

    def test_overlay_input_model(self):
        from capabilities.video.skills.talking_head_overlay import OverlayInput, GateMode
        inp = OverlayInput(video_path="/tmp/test.mp4")
        assert inp.gate_mode == GateMode.STANDALONE
        assert inp.kept_segments is None

    def test_overlay_input_chained_mode(self):
        from capabilities.video.skills.talking_head_overlay import OverlayInput, GateMode
        inp = OverlayInput(
            video_path="/tmp/test.mp4",
            gate_mode=GateMode.CHAINED,
            kept_segments=[(0.0, 2.0), (4.0, 10.0)],
        )
        assert inp.gate_mode == GateMode.CHAINED
        assert len(inp.kept_segments) == 2


# ── 10.5 已知文本时间轴（跳过 whisper）──────────────────

class TestKnownTranscript:
    @pytest.mark.asyncio
    async def test_transcript_skips_whisper(self):
        """传入 transcript → 不调 whisper，translate 直接进规划。

        实机依据（2026-10-28 全链验收）：TTS 合成音频 whisper 误听
        （"90%"→"九十辈子"）导致叠加内容语义错位。
        """
        from capabilities.video.services.overlay_orchestrator import OverlayPlan
        from capabilities.video.skills.talking_head_overlay import (
            OverlayInput, TalkingHeadOverlaySkill,
        )

        skill = TalkingHeadOverlaySkill()
        calls: list = []

        async def _plan(seg_dicts, duration, llm, **kw):
            calls.append(seg_dicts)
            return OverlayPlan(video_id="t"), ""

        def _boom(*a, **kw):
            raise AssertionError("提供 transcript 时不应调用 whisper")

        with patch(
            "capabilities.video.adapters.whisper_transcribe.transcribe", new=_boom,
        ), patch(
            # 10.19 起 skill 模块级 import generate_overlay_plan →
            # patch 必须落在 skill 命名空间（patch 源模块不影响既有绑定）
            "capabilities.video.skills.talking_head_overlay.generate_overlay_plan",
            new=_plan,
        ), patch.object(skill, "_resolve_llm", return_value=None):
            plan, timed_words, seg_dicts = await skill._generate_plan(
                OverlayInput(
                    video_path="/tmp/x.mp4",
                    transcript=[
                        {"start": 0.0, "end": 2.0, "text": "第一句",
                         "words": [{"text": "第一", "start": 0.0, "end": 1.0}]},
                        {"start": 2.0, "end": 4.0, "text": "第二句", "words": []},
                    ],
                ),
                duration=4.0, tmp_dir=Path("/tmp"),
            )

        assert len(calls) == 1
        assert [s["text"] for s in calls[0]] == ["第一句", "第二句"]
        assert calls[0][0]["index"] == 0
        assert calls[0][1]["start"] == 2.0
        assert timed_words == [{"text": "第一", "start": 0.0, "end": 1.0}]

    @pytest.mark.asyncio
    async def test_transcript_invalid_entries_dropped(self):
        """坏元素（end<=start）丢弃；全丢 → 空规划（不崩）。"""
        from capabilities.video.services.overlay_orchestrator import OverlayPlan
        from capabilities.video.skills.talking_head_overlay import (
            OverlayInput, TalkingHeadOverlaySkill,
        )

        skill = TalkingHeadOverlaySkill()
        with patch.object(skill, "_resolve_llm", return_value=None):
            plan, timed_words, seg_dicts = await skill._generate_plan(
                OverlayInput(
                    video_path="/tmp/x.mp4",
                    transcript=[{"start": 3.0, "end": 1.0, "text": "坏段"}],
                ),
                duration=4.0, tmp_dir=Path("/tmp"),
            )

        assert isinstance(plan, OverlayPlan)
        assert seg_dicts == [] and timed_words == []


# ── 10.5.5 video_pipeline_progress 进度发射（口播 chat 融合 Stage A）──

class TestVideoPipelineProgress:
    """进度发射器单测 — docs/口播前端交互设计方案.md#3.4。

    验收标准：载荷含 skill/step/total/phase/label/detail/percent，
    phase 枚举 transcribe/plan/render/review；workflow_id 缺失零发射。
    """

    def _skill(self):
        from capabilities.video.skills.talking_head_overlay import (
            TalkingHeadOverlaySkill,
        )
        return TalkingHeadOverlaySkill()

    @staticmethod
    def _mock_bus():
        from unittest.mock import MagicMock
        bus = MagicMock()
        bus.publish = AsyncMock(return_value="evt")
        return bus

    @pytest.mark.asyncio
    async def test_emit_publishes_payload_with_phase(self):
        """发射载荷字段完整：skill/step/total/phase/label/detail/percent/turn_id。"""
        skill = self._skill()
        bus = self._mock_bus()
        with patch("app.services.sse_bus.sse_bus", new=bus):
            emit = skill._make_progress_emitter("wf-1", "turn-9")
            await emit(2, "plan", "叠加规划完成", "18 个叠加片段")

        bus.publish.assert_awaited_once()
        wf_id, event_type, payload = bus.publish.await_args.args
        assert wf_id == "wf-1"
        assert event_type == "video_pipeline_progress"
        assert payload["skill"] == "talking_head_overlay"
        assert payload["step"] == 2
        assert payload["total"] == 6
        assert payload["phase"] == "plan"
        assert payload["label"] == "叠加规划完成"
        assert payload["detail"] == "18 个叠加片段"
        assert payload["percent"] == 33  # int(2/6*100)
        assert bus.publish.await_args.kwargs["turn_id"] == "turn-9"

    @pytest.mark.asyncio
    async def test_emit_without_workflow_id_is_noop(self):
        """workflow_id 缺失（脚本直调/单测）→ 不发射，行为与改造前一致。"""
        skill = self._skill()
        bus = self._mock_bus()
        with patch("app.services.sse_bus.sse_bus", new=bus):
            emit = skill._make_progress_emitter("", "")
            await emit(1, "transcribe", "转写与词级对齐")

        bus.publish.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_percent_endpoints_and_empty_turn_id(self):
        """step=1 → 16%、step=6 → 100%；turn_id 空 → 传 None（不绑错消息）。"""
        skill = self._skill()
        bus = self._mock_bus()
        with patch("app.services.sse_bus.sse_bus", new=bus):
            emit = skill._make_progress_emitter("wf-2", "")
            await emit(1, "transcribe", "转写与词级对齐")
            await emit(6, "review", "口播增强完成")

        assert bus.publish.await_count == 2
        first, second = bus.publish.await_args_list
        assert first.args[2]["percent"] == 16   # int(1/6*100)
        assert first.args[2]["phase"] == "transcribe"
        assert second.args[2]["percent"] == 100
        assert second.args[2]["phase"] == "review"
        assert first.kwargs["turn_id"] is None

    @pytest.mark.asyncio
    async def test_execute_missing_video_emits_nothing(self):
        """视频不存在 → 校验先于任何发射，零进度事件。"""
        skill = self._skill()
        bus = self._mock_bus()
        with patch("app.services.sse_bus.sse_bus", new=bus):
            events = []
            async for chunk in skill.execute({
                "video_path": "Z:/nonexistent/nope.mp4",
                "_workflow_id": "wf-3",
            }):
                events.append(chunk)

        assert any(e.get("success") is False for e in events)
        bus.publish.assert_not_awaited()


# ── 10.6 分辨率自适应（样本多样性审计 2026-10-28）────────

class TestResolutionAdaptive:
    """16 个模板的字号/边距/位移全是按 1080×1920 设计的绝对 px。

    样本多样性审计暴露：此前只在 1080×1920 素材上真机验证过，
    横向/2K/4K 素材会布局错位或字号比例失衡。三层防线：
      - _design_canvas：短边双向归一到 1080（9:16 任意分辨率比例恒定）
      - _base_css：异常比例按设计基准等比缩放居中（不溢出）
    """

    def test_base_css_design_baseline_identity(self):
        from capabilities.video.adapters.overlay_templates import _base_css
        css = _base_css(1080, 1920)
        assert "width: 1080px; height: 1920px" in css
        assert "scale(1.000000)" in css
        assert "translate(0.000px, 0.000px)" in css

    def test_base_css_720p_vertical(self):
        """720×1280 短边归一后本该走 1080×1920 —— 直调也应等比缩小。"""
        from capabilities.video.adapters.overlay_templates import _base_css
        css = _base_css(720, 1280)
        assert "scale(0.666667)" in css
        assert "translate(0.000px, 0.000px)" in css

    def test_base_css_landscape_centers_and_shrinks(self):
        """1920×1080 横屏：按高度适配 0.5625，左右居中留边不溢出。"""
        from capabilities.video.adapters.overlay_templates import _base_css
        css = _base_css(1920, 1080)
        assert "scale(0.562500)" in css
        assert "translate(656.250px, 0.000px)" in css

    def test_base_css_square_centers(self):
        from capabilities.video.adapters.overlay_templates import _base_css
        css = _base_css(1080, 1080)
        # k = min(1, 0.5625) = 0.5625；水平居中 (1080-607.5)/2 = 236.25
        assert "scale(0.562500)" in css
        assert "translate(236.250px, 0.000px)" in css

    def test_template_renders_with_scale(self):
        from capabilities.video.adapters.overlay_templates import caption_pop
        html = caption_pop(text="分辨率", width=720, height=1280, duration=2.0)
        assert "scale(0.666667)" in html

    def test_design_canvas_upscale_small(self):
        from capabilities.video.skills.talking_head_overlay import TalkingHeadOverlaySkill
        assert TalkingHeadOverlaySkill._design_canvas(684, 1174) == (1080, 1854)

    def test_design_canvas_downscale_4k(self):
        """4K 竖屏 2160×3840 原样渲染会让字号只剩设计比例一半 → 缩回 1080×1920。"""
        from capabilities.video.skills.talking_head_overlay import TalkingHeadOverlaySkill
        assert TalkingHeadOverlaySkill._design_canvas(2160, 3840) == (1080, 1920)

    def test_design_canvas_baseline_untouched(self):
        from capabilities.video.skills.talking_head_overlay import TalkingHeadOverlaySkill
        assert TalkingHeadOverlaySkill._design_canvas(1080, 1920) == (1080, 1920)

    def test_design_canvas_landscape_passthrough(self):
        """横屏短边已 1080 → 原样交给 _base_css 缩放兜底。"""
        from capabilities.video.skills.talking_head_overlay import TalkingHeadOverlaySkill
        assert TalkingHeadOverlaySkill._design_canvas(1920, 1080) == (1920, 1080)


# ── 11. 临时文件清理 ─────────────────────────────────────

class TestOverlayTempDir:
    @pytest.mark.asyncio
    async def test_temp_dir_created_and_cleaned(self):
        from capabilities.video.skills.talking_head_overlay import managed_temp_dir
        captured_dir = None
        async with managed_temp_dir() as tmp_dir:
            captured_dir = tmp_dir
            assert tmp_dir.is_dir()
        assert captured_dir is not None
        assert not captured_dir.exists()


# ── 12. Phase 4: 更多模板 ────────────────────────────────

class TestMoreTemplates:
    def test_caption_typewriter_html(self):
        from capabilities.video.adapters.overlay_templates import caption_typewriter
        html = caption_typewriter(text="增长秘诀", width=1080, height=1920, duration=3.0)
        assert "增" in html and "长" in html and "秘" in html and "诀" in html
        assert "window.seekTo" in html

    def test_keyword_highlight_html(self):
        from capabilities.video.adapters.overlay_templates import keyword_highlight
        html = keyword_highlight(keyword="增长", highlight_color="#FF0000")
        assert "增长" in html
        assert "marker" in html  # v3: 亮色标记笔扫出
        assert "window.seekTo" in html

    def test_number_roll_html(self):
        from capabilities.video.adapters.overlay_templates import number_roll
        html = number_roll(label="增长率", value="0", target="200")
        assert "增长率" in html
        assert "counter" in html

    def test_bar_chart_grow_html(self):
        from capabilities.video.adapters.overlay_templates import bar_chart_grow
        html = bar_chart_grow(labels="A,B,C", values="30,60,90")
        assert "bar-fill" in html
        assert "window.seekTo" in html

    def test_lower_third_html(self):
        from capabilities.video.adapters.overlay_templates import lower_third
        html = lower_third(title="标题", subtitle="副标题")
        assert "标题" in html
        assert "副标题" in html
        assert "window.seekTo" in html

    def test_all_10_templates_in_dict(self):
        from capabilities.video.adapters.overlay_templates import TEMPLATES
        expected = {
            "caption_pop", "caption_karaoke", "keyword_icon",
            "stat_card", "title_card", "caption_typewriter",
            "keyword_highlight", "number_roll", "bar_chart_grow",
            "lower_third", "section_card", "emoji_sticker",  # v3 新增
            "arrow_point", "circle_mark", "full_card", "split_compare",  # v4 新增
        }
        assert set(TEMPLATES.keys()) == expected


# ── 13. 串联模式 + 闸门合并 ─────────────────────────────

class TestChainedMode:
    def test_smart_cut_input_chain_field(self):
        from capabilities.video.skills.smart_cut import SmartCutInput
        inp = SmartCutInput(video_path="/tmp/test.mp4", chain_with_overlay=True)
        assert inp.chain_with_overlay is True

    def test_overlay_input_kept_segments(self):
        from capabilities.video.skills.talking_head_overlay import OverlayInput, GateMode
        inp = OverlayInput(
            video_path="/tmp/test.mp4",
            gate_mode=GateMode.CHAINED,
            kept_segments=[(0.0, 2.0), (4.0, 10.0)],
        )
        assert len(inp.kept_segments) == 2

    def test_overlay_input_template_whitelist(self):
        from capabilities.video.skills.talking_head_overlay import OverlayInput
        inp = OverlayInput(
            video_path="/tmp/test.mp4",
            templates=["caption_pop", "title_card"],
        )
        assert len(inp.templates) == 2

    def test_gate_mode_enum(self):
        from capabilities.video.skills.talking_head_overlay import GateMode
        assert GateMode.STANDALONE.value == "standalone"
        assert GateMode.CHAINED.value == "chained"
        assert GateMode.AUTO_CONFIRM.value == "auto_confirm"


# ── 14. 锚点重映射 + 串联集成 ───────────────────────────

class TestChainedIntegration:
    def test_remap_preserves_non_overlapping(self):
        """不与删减段重叠的叠加锚点正确重映射"""
        plan = OverlayPlan(
            video_id="test",
            segments=[
                OverlaySegment(
                    segment_id="o1", time_start=5.0, time_end=8.0,
                    template=OverlayTemplate.CAPTION_POP,
                ),
            ],
        )
        kept = [(0.0, 3.0), (4.0, 10.0)]
        remapped = remap_overlay_anchors(plan, kept)
        assert not remapped.segments[0].skipped
        # source 5.0 在 [4,10] 段内，cut_time = 3.0 + (5.0-4.0) = 4.0
        assert remapped.segments[0].time_start == 4.0
        # source 8.0 在 [4,10] 段内，cut_time = 3.0 + (8.0-4.0) = 7.0
        assert remapped.segments[0].time_end == 7.0

    def test_remap_skips_fully_deleted_overlay(self):
        """叠加完全在删减段内 → skipped"""
        plan = OverlayPlan(
            video_id="test",
            segments=[
                OverlaySegment(
                    segment_id="o1", time_start=3.1, time_end=3.9,
                    template=OverlayTemplate.KEYWORD_ICON,
                ),
            ],
        )
        kept = [(0.0, 3.0), (4.0, 10.0)]
        remapped = remap_overlay_anchors(plan, kept)
        assert remapped.segments[0].skipped is True

    def test_conflict_detection_with_skipped(self):
        """skipped 片段不参与冲突检测"""
        segments = [
            OverlaySegment(segment_id="o1", time_start=0, time_end=5, template=OverlayTemplate.CAPTION_POP),
            OverlaySegment(segment_id="o2", time_start=3, time_end=8, template=OverlayTemplate.CAPTION_KARAOKE, skipped=True),
        ]
        conflicts = detect_overlay_conflicts(segments)
        assert len(conflicts) == 0


# ── 15. v4 主题色透传 ────────────────────────────────────

class TestV4PalettePassthrough:
    def test_full_card与split_compare不是字幕模板(self):
        """整屏卡有自己的文案，不走 caption 词级重建。"""
        from capabilities.video.skills.talking_head_overlay import TalkingHeadOverlaySkill
        assert "full_card" not in TalkingHeadOverlaySkill._CAPTION_TEMPLATES
        assert "split_compare" not in TalkingHeadOverlaySkill._CAPTION_TEMPLATES

    @pytest.mark.asyncio
    async def test_render_previews_透传palette(self):
        import tempfile

        import capabilities.video.adapters.overlay_templates as ot

        from capabilities.video.skills.talking_head_overlay import TalkingHeadOverlaySkill

        plan = OverlayPlan(
            video_id="", palette=["#FF4D4D", "#FFE066", "#4ECDC4"],
            segments=[
                OverlaySegment(
                    segment_id="o1", time_start=0.0, time_end=1.5,
                    template=OverlayTemplate.FULL_CARD, params={"text": "收束"},
                ),
            ],
        )
        captured = {}

        def spy(**kwargs):
            captured.update(kwargs)
            return "<html>x</html>"

        async def fake_render(**kwargs):
            return Path("fake.mp4")

        orig = ot.TEMPLATES["full_card"]
        ot.TEMPLATES["full_card"] = spy
        try:
            with patch(
                "capabilities.video.adapters.html_to_video.render_html_to_video",
                fake_render,
            ):
                with tempfile.TemporaryDirectory() as td:
                    pairs = await TalkingHeadOverlaySkill._render_previews(
                        TalkingHeadOverlaySkill, plan, [], Path(td), 1080, 1920,
                    )
        finally:
            ot.TEMPLATES["full_card"] = orig

        assert captured.get("palette") == ["#FF4D4D", "#FFE066", "#4ECDC4"]
        assert len(pairs) == 1


# ── 16. 空间冲突消解集成（2026-10-27 o6 叠影事故）────────

class TestApplyConstraintsResolvesConflicts:
    """_apply_constraints 必须强制消解同带/全屏冲突 —
    LLM 重规划屡次违反软规则，检测到不裁决等于没检测。"""

    @staticmethod
    def _plan():
        return OverlayPlan(
            video_id="",
            segments=[
                OverlaySegment(
                    segment_id="o1", time_start=12.46, time_end=15.22,
                    template=OverlayTemplate.SECTION_CARD,
                    params={"number": "01", "title": "不要对着空调直吹"},
                ),
                OverlaySegment(
                    segment_id="o2", time_start=13.80, time_end=17.0,
                    template=OverlayTemplate.KEYWORD_HIGHLIGHT,
                    params={"text": "长时间对着空调直吹"},
                ),
            ],
            palette=["#FF4D4D", "#FFE066", "#4ECDC4"],
        )

    def test_初规划冲突被消解(self):
        from capabilities.video.skills.talking_head_overlay import (
            GateMode,
            OverlayInput,
            TalkingHeadOverlaySkill,
        )

        parsed = OverlayInput(video_path="/tmp/x.mp4", gate_mode=GateMode.AUTO_CONFIRM)
        out = TalkingHeadOverlaySkill._apply_constraints(parsed, self._plan())
        by_id = {s.segment_id: s for s in out.segments}
        assert not by_id["o1"].skipped
        assert by_id["o2"].skipped  # 低优先级让位，不删除

    def test_白名单与冲突消解叠加生效(self):
        from capabilities.video.skills.talking_head_overlay import (
            GateMode,
            OverlayInput,
            TalkingHeadOverlaySkill,
        )

        parsed = OverlayInput(
            video_path="/tmp/x.mp4", gate_mode=GateMode.AUTO_CONFIRM,
            templates=["section_card", "keyword_highlight"],
        )
        out = TalkingHeadOverlaySkill._apply_constraints(parsed, self._plan())
        by_id = {s.segment_id: s for s in out.segments}
        assert len(by_id) == 2  # 白名单过滤不删除
        assert not by_id["o1"].skipped
        assert by_id["o2"].skipped