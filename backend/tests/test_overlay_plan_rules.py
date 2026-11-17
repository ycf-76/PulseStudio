"""规划层升级测试 — OVE 四条规则进 prompt + 密度自检（ADR-004）。"""

from __future__ import annotations

import pytest

from capabilities.video.services.overlay_orchestrator import (
    OVERLAY_PLAN_PROMPT,
    TEMPLATE_LAYER,
    TEMPLATE_PRIORITY,
    OverlayPlan,
    OverlaySegment,
    OverlayTemplate,
    _normalize_simplified,
    largest_overlay_gap,
    overlay_coverage,
)


class TestSimplifiedNormalization:
    def test_繁体转简体_地区变体(self):
        """zh-cn 模式：對著→对着；共享字"著"按地区用法转换。"""
        assert _normalize_simplified("長時間對著空調直吹") == "长时间对着空调直吹"

    def test_已简体文本不受影响(self):
        assert _normalize_simplified("显著 特点 安排上") == "显著 特点 安排上"

    def test_嵌套结构递归(self):
        out = _normalize_simplified({"title": "千萬別大意", "words": [{"text": "防曬"}]})
        assert out["title"] == "千万别大意"
        assert out["words"][0]["text"] == "防晒"

    def test_非字符串原样(self):
        assert _normalize_simplified(5) == 5
        assert _normalize_simplified(None) is None

    def test_prompt_简体约束(self):
        assert "简体中文" in OVERLAY_PLAN_PROMPT


class TestPromptRules:
    def test_prompt_含OVE四条规则(self):
        """OVE 效果的 80% 来自规则，必须注入规划 prompt。"""
        assert "词" in OVERLAY_PLAN_PROMPT and "对齐" in OVERLAY_PLAN_PROMPT  # 规则1: 图形落在词上
        assert "字面" in OVERLAY_PLAN_PROMPT  # 规则2: 字面化视觉
        assert "一个" in OVERLAY_PLAN_PROMPT and "动" in OVERLAY_PLAN_PROMPT  # 规则3: 一区一动物
        assert "克制" in OVERLAY_PLAN_PROMPT or "戏剧" in OVERLAY_PLAN_PROMPT  # 规则4: 特效克制

    def test_prompt_含密度约束(self):
        assert "10" in OVERLAY_PLAN_PROMPT  # 每 10 秒至少一个
        assert "最少" in OVERLAY_PLAN_PROMPT

    def test_prompt_列出全部10个模板(self):
        for t in OverlayTemplate:
            assert t.value in OVERLAY_PLAN_PROMPT, f"{t.value} 未在 prompt 中"


class TestV3VisualDiversity:
    def test_prompt_模板多样性_至少4种(self):
        assert "4 种" in OVERLAY_PLAN_PROMPT or "至少 4" in OVERLAY_PLAN_PROMPT

    def test_prompt_caption占比上限(self):
        assert "50%" in OVERLAY_PLAN_PROMPT

    def test_prompt_亮色记忆点要求(self):
        assert "亮色" in OVERLAY_PLAN_PROMPT

    def test_prompt_列举规则_section_card(self):
        assert "section_card" in OVERLAY_PLAN_PROMPT
        assert "第一" in OVERLAY_PLAN_PROMPT

    def test_prompt_accent_words引导(self):
        assert "accent_words" in OVERLAY_PLAN_PROMPT

    def test_新模板枚举与注册(self):
        assert OverlayTemplate.SECTION_CARD.value == "section_card"
        assert OverlayTemplate.EMOJI_STICKER.value == "emoji_sticker"
        for t in (OverlayTemplate.SECTION_CARD, OverlayTemplate.EMOJI_STICKER):
            assert t in TEMPLATE_PRIORITY
            assert t in TEMPLATE_LAYER


class TestOverlayCoverage:
    def _seg(self, sid: str, s: float, e: float) -> OverlaySegment:
        return OverlaySegment(
            segment_id=sid,
            time_start=s,
            time_end=e,
            template=OverlayTemplate.CAPTION_POP,
            params={},
        )

    def test_空规划_覆盖为零(self):
        plan = OverlayPlan(video_id="")
        assert overlay_coverage(plan, 10.0) == 0.0

    def test_单个叠加(self):
        plan = OverlayPlan(video_id="", segments=[self._seg("o1", 0, 3)])
        assert abs(overlay_coverage(plan, 10.0) - 0.3) < 1e-9

    def test_重叠叠加_按并集计算(self):
        """重叠窗口不重复计数。"""
        plan = OverlayPlan(
            video_id="",
            segments=[
                self._seg("o1", 0, 4),
                self._seg("o2", 2, 6),
            ],
        )
        assert abs(overlay_coverage(plan, 10.0) - 0.6) < 1e-9

    def test_skipped段不计入(self):
        seg = self._seg("o1", 0, 5)
        plan = OverlayPlan(
            video_id="",
            segments=[
                seg,
                OverlaySegment(
                    segment_id="o2",
                    time_start=5,
                    time_end=10,
                    template=OverlayTemplate.CAPTION_POP,
                    params={},
                    skipped=True,
                ),
            ],
        )
        assert abs(overlay_coverage(plan, 10.0) - 0.5) < 1e-9

    def test_越界时长clamp到1(self):
        plan = OverlayPlan(
            video_id="",
            segments=[
                self._seg("o1", 0, 10),
                self._seg("o2", 5, 10),
            ],
        )
        assert overlay_coverage(plan, 10.0) == 1.0

    def test_零时长_不崩溃(self):
        plan = OverlayPlan(video_id="", segments=[self._seg("o1", 0, 3)])
        assert overlay_coverage(plan, 0.0) == 0.0


class TestLargestOverlayGap:
    """分布均匀性指标（2026-10-28 样本多样性审计）。

    全局覆盖率盲区：100 个叠加全挤在前 10s 也照样达标 —
    后 2/3 片子零增强。用最长空白区间补这一刀。
    """

    def _seg(self, sid: str, s: float, e: float) -> OverlaySegment:
        return OverlaySegment(
            segment_id=sid,
            time_start=s,
            time_end=e,
            template=OverlayTemplate.CAPTION_POP,
            params={},
        )

    def test_空规划_空白等于全片(self):
        gap, s, e = largest_overlay_gap(OverlayPlan(video_id=""), 10.0)
        assert (gap, s, e) == (10.0, 0.0, 10.0)

    def test_连续覆盖_零空白(self):
        plan = OverlayPlan(
            video_id="",
            segments=[
                self._seg("o1", 0, 5),
                self._seg("o2", 5, 10),
            ],
        )
        assert largest_overlay_gap(plan, 10.0)[0] == 0.0

    def test_长尾空白_被识别(self):
        """180s 伪影场景：13 个叠加全挤在前 26s → 154s 空白。"""
        plan = OverlayPlan(
            video_id="",
            segments=[
                self._seg("o1", 0, 13),
                self._seg("o2", 13, 26),
            ],
        )
        gap, s, e = largest_overlay_gap(plan, 180.0)
        assert gap == 154.0 and (s, e) == (26.0, 180.0)

    def test_片头空白计入(self):
        plan = OverlayPlan(video_id="", segments=[self._seg("o1", 4, 10)])
        gap, s, e = largest_overlay_gap(plan, 10.0)
        assert gap == 4.0 and (s, e) == (0.0, 4.0)

    def test_中间空档_取最大者(self):
        plan = OverlayPlan(
            video_id="",
            segments=[
                self._seg("o1", 0, 2),
                self._seg("o2", 4, 6),
                self._seg("o3", 12, 20),
            ],
        )
        gap, s, e = largest_overlay_gap(plan, 20.0)
        assert gap == 6.0 and (s, e) == (6.0, 12.0)

    def test_重叠段合并后计算(self):
        """o1 0-4 与 o2 2-8 并集 0-8 → 空白只有片尾 2s。"""
        plan = OverlayPlan(
            video_id="",
            segments=[
                self._seg("o1", 0, 4),
                self._seg("o2", 2, 8),
            ],
        )
        gap, s, e = largest_overlay_gap(plan, 10.0)
        assert gap == 2.0 and (s, e) == (8.0, 10.0)

    def test_skipped段不计入(self):
        plan = OverlayPlan(
            video_id="",
            segments=[
                self._seg("o1", 0, 5),
                OverlaySegment(
                    segment_id="o2",
                    time_start=5,
                    time_end=10,
                    template=OverlayTemplate.CAPTION_POP,
                    params={},
                    skipped=True,
                ),
            ],
        )
        gap, s, e = largest_overlay_gap(plan, 10.0)
        assert gap == 5.0 and (s, e) == (5.0, 10.0)

    def test_零时长_不崩溃(self):
        plan = OverlayPlan(video_id="", segments=[self._seg("o1", 0, 3)])
        assert largest_overlay_gap(plan, 0.0) == (0.0, 0.0, 0.0)

    def test_真实合格产物_远低于阈值(self):
        """run12 实测最大空白 1.9~3.4s — 6s 阈值不误报。"""
        plan = OverlayPlan(
            video_id="",
            segments=[
                self._seg("o1", 0.0, 6.4),
                self._seg("o2", 8.28, 20.0),
            ],
        )
        gap, _, _ = largest_overlay_gap(plan, 22.0)
        assert gap < 6.0


class TestDensitySelfCheckWithGap:
    """集成：分布不均 → 带反馈重试；反馈点名空白区间。"""

    @pytest.mark.asyncio
    async def test_分布不均触发重试_反馈含空白区间(self):
        import json
        from unittest.mock import patch

        from capabilities.video.services.overlay_orchestrator import (
            generate_overlay_plan,
        )

        prompts: list[str] = []
        replies = [
            # 第一次：覆盖 0-10s（覆盖率 17% < 30%，分布也有 50s 空白）
            json.dumps(
                {
                    "palette": ["#FF4D4D"],
                    "overlays": [
                        {
                            "segment_id": "o1",
                            "time_start": 0.0,
                            "time_end": 10.0,
                            "template": "full_card",
                            "params": {"text": "开场", "color": "#FF4D4D"},
                        },
                    ],
                },
                ensure_ascii=False,
            ),
            # 第二次：覆盖全片
            json.dumps(
                {
                    "palette": ["#FF4D4D"],
                    "overlays": [
                        {
                            "segment_id": "o1",
                            "time_start": 0.0,
                            "time_end": 30.0,
                            "template": "full_card",
                            "params": {"text": "开场", "color": "#FF4D4D"},
                        },
                        {
                            "segment_id": "o2",
                            "time_start": 30.0,
                            "time_end": 60.0,
                            "template": "stat_card",
                            "params": {"label": "收束", "value": "3"},
                        },
                    ],
                },
                ensure_ascii=False,
            ),
        ]

        async def _fake(llm, prompt, max_tokens=None):
            prompts.append(prompt)
            return replies[min(len(prompts) - 1, 1)]

        segs = [{"index": 0, "start": 0.0, "end": 60.0, "text": "口播"}]
        with patch("app.adapters.llm_base.llm_prompt_to_text", new=_fake):
            plan, _ = await generate_overlay_plan(segs, 60.0, object())

        assert len(prompts) == 2  # 第一次被自检拦下 → 重试一次
        assert "分布不均" in prompts[1]
        assert "10s~60s" in prompts[1]  # 点名空白区间
        assert len(plan.segments) == 2  # 第二次（合格）结果被采纳

    @pytest.mark.asyncio
    async def test_仅分布不合格也重试(self):
        """覆盖率达标（叠加够长）但全挤在前段 → 仍要重试。"""
        import json
        from unittest.mock import patch

        from capabilities.video.services.overlay_orchestrator import (
            generate_overlay_plan,
        )

        prompts: list[str] = []
        bad = json.dumps(
            {
                "palette": ["#FF4D4D"],
                "overlays": [
                    {
                        "segment_id": "o1",
                        "time_start": 0.0,
                        "time_end": 40.0,
                        "template": "full_card",
                        "params": {"text": "开场", "color": "#FF4D4D"},
                    },
                ],
            },
            ensure_ascii=False,
        )  # 覆盖率 40/60 = 67% 达标，但 40-60s 空白 20s > 6s

        async def _fake(llm, prompt, max_tokens=None):
            prompts.append(prompt)
            return bad

        segs = [{"index": 0, "start": 0.0, "end": 60.0, "text": "口播"}]
        with patch("app.adapters.llm_base.llm_prompt_to_text", new=_fake):
            await generate_overlay_plan(segs, 60.0, object())

        assert len(prompts) == 3  # 三次都被拦（自检上限 2 次重试）
        assert "分布不均" in prompts[1]
        assert "整体密度不足" not in prompts[1]  # 覆盖率本身是够的


# ── v4 节奏 + 主题色 ─────────────────────────────────────


class TestV4RhythmPalettePrompt:
    def test_新模板枚举与注册(self):
        for value in ("arrow_point", "circle_mark", "full_card", "split_compare"):
            t = OverlayTemplate(value)
            assert t in TEMPLATE_PRIORITY, f"{value} 缺 priority"
            assert t in TEMPLATE_LAYER, f"{value} 缺 layer"

    def test_prompt_列出全部16个模板(self):
        assert len(OverlayTemplate) == 16
        for t in OverlayTemplate:
            assert t.value in OVERLAY_PLAN_PROMPT, f"{t.value} 未在 prompt 中"

    def test_prompt_主题色规则(self):
        assert "palette" in OVERLAY_PLAN_PROMPT
        assert "撞色" in OVERLAY_PLAN_PROMPT

    def test_prompt_节奏规则(self):
        assert "开场" in OVERLAY_PLAN_PROMPT
        assert "收束" in OVERLAY_PLAN_PROMPT
        assert "留白" in OVERLAY_PLAN_PROMPT


class TestParsePlanOutput:
    def test_新格式_含palette(self):
        from capabilities.video.services.overlay_orchestrator import _parse_plan_output

        text = (
            '{"palette": ["#ff4d4d", "#FFE066", "#4ECDC4"], '
            '"overlays": [{"segment_id": "o1", "time_start": 0, "time_end": 2, '
            '"template": "caption_pop", "params": {}}]}'
        )
        segs, palette, raw = _parse_plan_output(text, 10.0)
        assert len(segs) == 1
        assert palette == ["#FF4D4D", "#FFE066", "#4ECDC4"]
        assert len(raw) == 1

    def test_旧格式_数组兼容(self):
        from capabilities.video.services.overlay_orchestrator import _parse_plan_output

        text = (
            '[{"segment_id": "o1", "time_start": 0, "time_end": 2, '
            '"template": "caption_pop", "params": {}}]'
        )
        segs, palette, raw = _parse_plan_output(text, 10.0)
        assert len(segs) == 1
        assert palette == []

    def test_非法色剔除(self):
        from capabilities.video.services.overlay_orchestrator import _parse_plan_output

        text = '{"palette": ["red", "#GGGGGG", "#FF4D4D"], "overlays": []}'
        _, palette, _ = _parse_plan_output(text, 10.0)
        assert palette == ["#FF4D4D"]

    def test_shorthand色归一化(self):
        from capabilities.video.services.overlay_orchestrator import _parse_plan_output

        text = '{"palette": ["#4ec"], "overlays": []}'
        _, palette, _ = _parse_plan_output(text, 10.0)
        assert palette == ["#44EECC"]

    def test_重复段号重编号_2026run9实录(self):
        """2026-10-27 run9 实证：重规划 LLM 输出两个 o20（keyword_highlight
        + title_card 同名）→ 渲染文件 overlay_o20.mp4 互相覆盖 → 成片
        keyword_highlight 时间窗空屏 + VLM 被覆盖后的内容误导。
        修后重复段号必须重编号（保内容，换 id），渲染不串。"""
        from capabilities.video.services.overlay_orchestrator import _parse_plan_output

        text = (
            '{"overlays": ['
            '{"segment_id": "o20", "time_start": 5.0, "time_end": 7.0, '
            '"template": "keyword_highlight", "params": {"text": "a"}}, '
            '{"segment_id": "o20", "time_start": 7.0, "time_end": 8.0, '
            '"template": "title_card", "params": {"title": "b"}}]}'
        )
        segs, _, _ = _parse_plan_output(text, 60.0)
        ids = [s.segment_id for s in segs]
        assert len(set(ids)) == len(ids)
        assert ids[0] == "o20"
        assert ids[1] != "o20"
        # 重编号段内容不丢：模板/时间窗原样保留
        from capabilities.video.services.overlay_orchestrator import template_name

        assert template_name(segs[1].template) == "title_card"
        assert segs[1].time_start == 7.0


class TestEnsurePalette:
    def test_空盘_fallback第一组(self):
        from capabilities.video.services.overlay_orchestrator import _ensure_palette

        assert _ensure_palette([]) == ["#FF4D4D", "#FFE066", "#4ECDC4"]

    def test_不足3色_补齐且去重(self):
        from capabilities.video.services.overlay_orchestrator import _ensure_palette

        assert _ensure_palette(["#FF4D4D"]) == ["#FF4D4D", "#FFE066", "#4ECDC4"]

    def test_足3色_原样(self):
        from capabilities.video.services.overlay_orchestrator import _ensure_palette

        p = ["#AABBCC", "#DDEEFF", "#123456"]
        assert _ensure_palette(p) == p


class TestValidatePlanRhythm:
    _PAL = ["#FF4D4D", "#FFE066", "#4ECDC4"]

    @staticmethod
    def _plan(specs, palette=None):
        from capabilities.video.services.overlay_orchestrator import (
            OverlayPlan,
            OverlaySegment,
        )

        segs = [
            OverlaySegment(
                segment_id=f"o{i}",
                time_start=s,
                time_end=e,
                template=OverlayTemplate(tpl),
                params=params,
            )
            for i, (s, e, tpl, params) in enumerate(specs)
        ]
        return OverlayPlan(video_id="", segments=segs, palette=palette or [])

    def test_合规规划_无问题(self):
        from capabilities.video.services.overlay_orchestrator import validate_plan_rhythm

        plan = self._plan(
            [
                (0.0, 1.5, "full_card", {}),
                (1.8, 3.5, "caption_pop", {}),
                (9.0, 12.0, "full_card", {}),
            ],
            palette=self._PAL,
        )
        assert validate_plan_rhythm(plan, 12.0) == []

    def test_开场不足2个(self):
        from capabilities.video.services.overlay_orchestrator import validate_plan_rhythm

        plan = self._plan(
            [(5.0, 7.0, "caption_pop", {}), (9.0, 12.0, "full_card", {})],
            palette=self._PAL,
        )
        assert any("开场" in i for i in validate_plan_rhythm(plan, 12.0))

    def test_缺收束卡(self):
        from capabilities.video.services.overlay_orchestrator import validate_plan_rhythm

        plan = self._plan(
            [
                (0.0, 1.0, "caption_pop", {}),
                (1.5, 3.0, "caption_pop", {}),
                (10.5, 12.0, "keyword_icon", {}),
            ],
            palette=self._PAL,
        )
        assert any("收束" in i for i in validate_plan_rhythm(plan, 12.0))

    def test_palette缺失(self):
        from capabilities.video.services.overlay_orchestrator import validate_plan_rhythm

        plan = self._plan(
            [
                (0.0, 1.5, "full_card", {}),
                (1.8, 3.5, "caption_pop", {}),
                (9.0, 12.0, "full_card", {}),
            ],
        )
        assert any("palette" in i for i in validate_plan_rhythm(plan, 12.0))

    def test_用色不在盘内(self):
        from capabilities.video.services.overlay_orchestrator import validate_plan_rhythm

        plan = self._plan(
            [
                (0.0, 1.5, "full_card", {"text": "A", "color": "#123456"}),
                (1.8, 3.5, "caption_pop", {}),
                (9.0, 12.0, "full_card", {}),
            ],
            palette=self._PAL,
        )
        assert any("盘内" in i for i in validate_plan_rhythm(plan, 12.0))


class TestTemplateLayerMatchesLayout:
    """TEMPLATE_LAYER 必须与模板 CSS 真实布局一致（2026-10-27 叠影事故教训）。

    事故：keyword_highlight 实际渲染在屏幕正中（body flex 居中）却被标
    inline，同层冲突检测对它失效 → section_card 与之同屏叠字。
    布局事实（overlay_templates.py 逐模板核实）：
      bottom：flex-end + padding-bottom 180~260px（字幕带）
      center：垂直居中（full center / 靠左 / 靠右变体，同带同刻必撞）
      free：绝对定位 x/y（arrow_point / circle_mark）
      full：fixed inset 0 全屏覆盖（full_card / split_compare）
    """

    _EXPECT = {
        "caption_pop": "bottom",
        "caption_karaoke": "bottom",
        "caption_typewriter": "bottom",
        "lower_third": "bottom",
        "keyword_icon": "center",
        "keyword_highlight": "center",
        "stat_card": "center",
        "number_roll": "center",
        "bar_chart_grow": "center",
        "title_card": "center",
        "section_card": "center",
        "emoji_sticker": "center",
        "arrow_point": "free",
        "circle_mark": "free",
        "full_card": "full",
        "split_compare": "full",
    }

    def test_分层标签与布局事实一致(self):
        for value, expected in self._EXPECT.items():
            tpl = OverlayTemplate(value)
            assert TEMPLATE_LAYER[tpl] == expected, (
                f"{value}: TEMPLATE_LAYER={TEMPLATE_LAYER[tpl]!r} "
                f"与 CSS 布局事实 {expected!r} 不符"
            )


class TestResolveOverlayConflicts:
    """resolve_overlay_conflicts：规划后处理强制消解空间冲突。"""

    @staticmethod
    def _plan(specs):
        segs = [
            OverlaySegment(
                segment_id=f"o{i + 1}",
                time_start=s,
                time_end=e,
                template=OverlayTemplate(tpl),
                params=params or {},
            )
            for i, (s, e, tpl, *rest) in enumerate(specs)
            for params in [rest[0] if rest else None]
        ]
        return OverlayPlan(video_id="", segments=segs, palette=[])

    def test_无重叠_原样返回(self):
        from capabilities.video.services.overlay_orchestrator import resolve_overlay_conflicts

        plan = self._plan(
            [
                (0, 3, "caption_pop"),
                (3, 6, "stat_card"),
            ]
        )
        out = resolve_overlay_conflicts(plan)
        assert all(not s.skipped for s in out.segments)

    def test_o6事故复现_中部两卡同屏_低优先级被跳过(self):
        """section_card(8) vs keyword_highlight(5) 同刻同带 → highlight skipped。"""
        from capabilities.video.services.overlay_orchestrator import resolve_overlay_conflicts

        plan = self._plan(
            [
                (12.46, 15.22, "section_card"),
                (13.80, 17.00, "keyword_highlight"),
            ]
        )
        out = resolve_overlay_conflicts(plan)
        by_id = {s.segment_id: s for s in out.segments}
        assert not by_id["o1"].skipped
        assert by_id["o2"].skipped

    def test_首尾相接不算冲突(self):
        from capabilities.video.services.overlay_orchestrator import resolve_overlay_conflicts

        plan = self._plan(
            [
                (0, 3, "caption_pop"),
                (3, 6, "caption_karaoke"),
            ]
        )
        out = resolve_overlay_conflicts(plan)
        assert all(not s.skipped for s in out.segments)

    def test_不同带_字幕与中部卡共存(self):
        from capabilities.video.services.overlay_orchestrator import resolve_overlay_conflicts

        plan = self._plan(
            [
                (0, 5, "caption_pop"),
                (0, 5, "stat_card"),
            ]
        )
        out = resolve_overlay_conflicts(plan)
        assert all(not s.skipped for s in out.segments)

    def test_全屏卡覆盖一切_与中部卡同刻则中部卡让位(self):
        from capabilities.video.services.overlay_orchestrator import resolve_overlay_conflicts

        plan = self._plan(
            [
                (0, 5, "stat_card"),
                (2, 3.5, "full_card"),  # 优先级 11 > 8，胜出
            ]
        )
        out = resolve_overlay_conflicts(plan)
        by_id = {s.segment_id: s for s in out.segments}
        assert not by_id["o2"].skipped
        assert by_id["o1"].skipped

    def test_两个全屏卡重叠_先开始的胜出(self):
        from capabilities.video.services.overlay_orchestrator import resolve_overlay_conflicts

        plan = self._plan(
            [
                (2, 3.5, "full_card"),
                (0, 5, "full_card"),  # 同为 11，先开始者胜
            ]
        )
        out = resolve_overlay_conflicts(plan)
        by_id = {s.segment_id: s for s in out.segments}
        assert not by_id["o2"].skipped
        assert by_id["o1"].skipped

    def test_自由层与中部卡共存(self):
        """arrow_point 绝对定位小标注，与中部卡不强制互斥。"""
        from capabilities.video.services.overlay_orchestrator import resolve_overlay_conflicts

        plan = self._plan(
            [
                (0, 5, "stat_card"),
                (1, 4, "arrow_point"),
            ]
        )
        out = resolve_overlay_conflicts(plan)
        assert all(not s.skipped for s in out.segments)

    def test_同优先级_先开始者胜出(self):
        from capabilities.video.services.overlay_orchestrator import resolve_overlay_conflicts

        plan = self._plan(
            [
                (2, 6, "section_card"),
                (0, 4, "stat_card"),  # 同为 8，0s 先开始
            ]
        )
        out = resolve_overlay_conflicts(plan)
        by_id = {s.segment_id: s for s in out.segments}
        assert not by_id["o2"].skipped
        assert by_id["o1"].skipped

    def test_只标记不删除_其余字段不变(self):
        from capabilities.video.services.overlay_orchestrator import resolve_overlay_conflicts

        plan = self._plan(
            [
                (0, 5, "title_card", {"title": "A"}),
                (1, 4, "section_card", {"number": "01", "title": "B"}),
            ]
        )
        out = resolve_overlay_conflicts(plan)
        assert len(out.segments) == 2
        assert out.segments[0].params == {"title": "A"}
        assert out.segments[0].time_start == 0.0

    def test_已跳过片段不参与裁决(self):
        from capabilities.video.services.overlay_orchestrator import resolve_overlay_conflicts

        plan = self._plan(
            [
                (0, 5, "caption_pop"),
                (0, 5, "caption_karaoke"),
            ]
        )
        plan = plan.model_copy(
            update={
                "segments": [
                    s.model_copy(update={"skipped": True}) if s.segment_id == "o1" else s
                    for s in plan.segments
                ]
            }
        )
        out = resolve_overlay_conflicts(plan)
        by_id = {s.segment_id: s for s in out.segments}
        assert by_id["o1"].skipped  # 保持原状
        assert not by_id["o2"].skipped  # 唯一活跃者不被误伤
