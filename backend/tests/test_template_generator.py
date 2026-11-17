# -*- coding: utf-8 -*-
"""P1 模板生成器测试 — LLM 运行时生成新模板 + 契约校验入库。

设计（对标 Remotion Claude "not template fill-ins"）：
  LLM 生成参数化 HTML（{var} 占位符），Python 侧 GeneratedTemplate
  包装成 (width, height, duration, **kwargs) -> str — 与 TEMPLATES
  duck-typing 兼容，零 exec（LLM 永不产出可执行代码）。

契约校验继承十轮 demo 全部防线：
  - seekTo 契约（worker 逐帧渲染的前提）
  - 禁真实时钟（setInterval/rAF/Date — 确定性渲染）
  - 选择器审计 + null guard 豁免（o17 事故）
  - {color} 消费（run7 palette 契约脱节教训）
  - 占位符白名单（防 LLM 编造变量渲染期 KeyError）
"""

from __future__ import annotations

import pytest

# ── 测试样板：合法的最小生成模板（含全部契约要素） ──

GOOD_TEMPLATE = """<!DOCTYPE html>
<html><head><style>
* { margin: 0; padding: 0; box-sizing: border-box; }
html, body { width: {width}px; height: {height}px; background: transparent; overflow: hidden; }
.badge {
    position: absolute; left: 50%; top: 40%;
    transform: translate(-50%, -50%);
    background: {color}; color: #111;
    font-size: 64px; font-weight: 900; padding: 20px 44px;
    border-radius: 20px; opacity: 0;
}
</style></head>
<body>
<div class="badge">{text}</div>
<script>
window.seekTo = function(t) {
    const el = document.querySelector('.badge');
    if (!el) return;
    const p = Math.min(1, t / 0.4);
    el.style.opacity = p;
    el.style.transform = 'translate(-50%,-50%) scale(' + (0.7 + 0.3 * p) + ')';
};
</script>
</body></html>"""


class TestValidateGeneratedHtml:
    """静态契约校验 — validate_generated_html(html) -> list[str] 错误清单。"""

    def test_合法模板_零错误(self):
        from capabilities.video.services.template_generator import (
            validate_generated_html,
        )

        errors = validate_generated_html(GOOD_TEMPLATE)
        assert errors == []

    def test_缺seekTo契约_报错(self):
        from capabilities.video.services.template_generator import (
            validate_generated_html,
        )

        bad = GOOD_TEMPLATE.replace(
            "window.seekTo = function(t)",
            "window.seek = function(t)",
        )
        errors = validate_generated_html(bad)
        assert any("seekTo" in e for e in errors)

    @pytest.mark.parametrize(
        "clock",
        [
            "setInterval(foo, 16)",
            "setTimeout(step, 100)",
            "requestAnimationFrame(step)",
            "new Date().getTime()",
            "performance.now()",
        ],
    )
    def test_真实时钟_全部报错(self, clock):
        """确定性渲染铁律：动画只由 seekTo(t) 驱动。"""
        from capabilities.video.services.template_generator import (
            validate_generated_html,
        )

        bad = GOOD_TEMPLATE.replace(
            "const p = Math.min(1, t / 0.4);",
            f"const p = Math.min(1, t / 0.4); const __clk = {clock};",
        )
        errors = validate_generated_html(bad)
        assert any("时钟" in e or "clock" in e.lower() for e in errors)

    def test_选择器引用不存在的class_报错(self):
        """o17 事故防线：querySelector 到 null 再取 .style → worker 崩。"""
        from capabilities.video.services.template_generator import (
            validate_generated_html,
        )

        bad = GOOD_TEMPLATE.replace(
            "document.querySelector('.badge')",
            "document.querySelector('.ghost')",
        )
        errors = validate_generated_html(bad)
        assert any("选择器" in e or ".ghost" in e for e in errors)

    def test_null_guard豁免_条件渲染合法(self):
        from capabilities.video.services.template_generator import (
            validate_generated_html,
        )

        ok = GOOD_TEMPLATE.replace(
            '<div class="badge">{text}</div>',
            '<div class="badge">{text}</div><div class="sub" id="x"></div>',
        ).replace(
            "if (!el) return;",
            "const sub = document.querySelector('.sub');\n"
            "    if (!el) return;\n    if (sub) sub.style.opacity = 1;",
        )
        errors = validate_generated_html(ok)
        assert errors == []

    def test_不消费color占位符_报错(self):
        """run7 教训：palette 规划↔渲染契约必须闭合。"""
        from capabilities.video.services.template_generator import (
            validate_generated_html,
        )

        bad = GOOD_TEMPLATE.replace("background: {color};", "background: #FFE066;")
        errors = validate_generated_html(bad)
        assert any("color" in e for e in errors)

    # ── 契约第 7 条：文字/背景对比度（2026-10-28 排版审计转化）──

    def test_低对比度_被拒(self):
        """白字压浅底（#999 on #fff ≈ 2.85:1）—— 审计工具里 VLM 反复报的
        "对比度过低"同类问题，在生成入口就拦下。"""
        from capabilities.video.services.template_generator import (
            validate_generated_html,
        )

        bad = GOOD_TEMPLATE.replace(
            "background: {color}; color: #111;",
            "background: #ffffff; color: #999999;",  # 2.85:1, 64px 也不到 3.0
        )
        errors = validate_generated_html(bad)
        assert any("对比度" in e for e in errors)

    def test_对比度达标_通过(self):
        from capabilities.video.services.template_generator import (
            validate_generated_html,
        )

        ok = GOOD_TEMPLATE.replace(
            "background: {color}; color: #111;",
            "background: #111111; color: #ffffff;",  # 18.9:1
        )
        errors = validate_generated_html(ok)
        assert not any("对比度" in e for e in errors)

    def test_大字号阈值放宽_3比1(self):
        """WCAG large text（≥48px）3.0:1 即可 —— 76px 白字压 #FF4D4D
        （3.27:1）合法；38px 同配色不合法。"""
        from capabilities.video.services.template_generator import (
            validate_generated_html,
        )

        large_ok = GOOD_TEMPLATE.replace(
            "background: {color}; color: #111;",
            "background: #FF4D4D; color: #ffffff;",
        )
        assert not any("对比度" in e for e in validate_generated_html(large_ok))

        small_bad = large_ok.replace("font-size: 64px", "font-size: 38px")
        assert any("对比度" in e for e in validate_generated_html(small_bad))

    def test_渐变背景与占位符背景_跳过不误报(self):
        """渐变/占位符背景无法静态算对比度 —— 不报错（留 VLM 审片兜底）。"""
        from capabilities.video.services.template_generator import (
            validate_generated_html,
        )

        ok = GOOD_TEMPLATE.replace(
            "background: {color}; color: #111;",
            "background: linear-gradient(90deg, #FF4D4D, #FFE066); color: #ffffff;",
        )
        assert not any("对比度" in e for e in validate_generated_html(ok))

    def test_半透明背景_跳过(self):
        """rgba alpha < 0.85 的底与下层混合色未知 —— 跳过。"""
        from capabilities.video.services.template_generator import (
            validate_generated_html,
        )

        ok = GOOD_TEMPLATE.replace(
            "background: {color}; color: #111;",
            "background: rgba(255,255,255,0.4); color: #ffffff;",
        )
        assert not any("对比度" in e for e in validate_generated_html(ok))

    def test_占位符不在白名单_报错(self):
        """防 LLM 编造变量名 → 渲染期替换 KeyError/留死占位符。"""
        from capabilities.video.services.template_generator import (
            validate_generated_html,
        )

        bad = GOOD_TEMPLATE.replace("{text}", "{magic_word}")
        errors = validate_generated_html(bad)
        assert any("magic_word" in e for e in errors)

    def test_CSS花括号不误报占位符(self):
        """CSS `{ margin: 0; }` 含冒号空格 ≠ {identifier}，不该报错。"""
        from capabilities.video.services.template_generator import (
            validate_generated_html,
        )

        errors = validate_generated_html(GOOD_TEMPLATE)
        assert not any("margin" in e for e in errors)


class TestGeneratedTemplate:
    """包装类 — LLM 产 HTML → (width, height, duration, **kwargs) -> str。"""

    def test_签名调用_占位符替换(self):
        from capabilities.video.services.template_generator import (
            GeneratedTemplate,
        )

        t = GeneratedTemplate("gen_test", GOOD_TEMPLATE)
        html = t(1080, 1920, 2.0, text="多喝水", color="#4ECDC4")
        assert "多喝水" in html
        assert "#4ECDC4" in html
        assert "width: 1080px" in html
        assert "height: 1920px" in html
        assert "{text}" not in html and "{color}" not in html

    def test_键名别名_text_title_sub_首个非空映射(self):
        """内置模板 v4.1 别名兼容同款：LLM 键名漂移不炸渲染。"""
        from capabilities.video.services.template_generator import (
            GeneratedTemplate,
        )

        t = GeneratedTemplate("gen_test", GOOD_TEMPLATE)
        assert "标题" in t(1080, 1920, 2.0, title="标题")
        assert "副题" in t(1080, 1920, 2.0, sub="副题")
        # text 优先级最高
        assert "主" in t(1080, 1920, 2.0, text="主", title="次")

    def test_color缺省_亮黄兜底(self):
        from capabilities.video.services.template_generator import (
            GeneratedTemplate,
        )

        t = GeneratedTemplate("gen_test", GOOD_TEMPLATE)
        assert "#FFE066" in t(1080, 1920, 2.0, text="x")

    def test_kwargs透传_未知键不炸(self):
        from capabilities.video.services.template_generator import (
            GeneratedTemplate,
        )

        t = GeneratedTemplate("gen_test", GOOD_TEMPLATE)
        html = t(1080, 1920, 2.0, text="x", whatever=1)
        assert "whatever" not in html  # 未知键被忽略，不出现在输出

    def test_HTML转义_params含尖括号不注入(self):
        """LLM 文案含 <script> 时必须转义 — 防注入。"""
        from capabilities.video.services.template_generator import (
            GeneratedTemplate,
        )

        t = GeneratedTemplate("gen_test", GOOD_TEMPLATE)
        html = t(1080, 1920, 2.0, text="<script>alert(1)</script>")
        assert "<script>alert" not in html


class TestGenerateTemplateFlow:
    """生成主流程 — mock LLM，失败反馈重试（复用 P0 闭环模式）。"""

    @staticmethod
    def _mock_llm(responses: list[str]):
        """对齐 llm_prompt_to_text 真实调用形态：chat(messages=[...])。"""

        class _L:
            def __init__(self):
                self.calls: list[str] = []
                self._resp = responses

            async def chat(self, messages=None, **kw):
                self.calls.append(messages[0]["content"])
                return {"content": self._resp.pop(0)}

        return _L()

    @pytest.mark.asyncio
    async def test_一次成功_注册并可调用(self, tmp_path, monkeypatch):
        from capabilities.video.services import template_generator as tg

        tg.GENERATED.clear()
        monkeypatch.setattr(tg, "_STORE_DIR", tmp_path)
        llm = self._mock_llm([GOOD_TEMPLATE])
        t = await tg.generate_template(llm, "gen_badge", "角标弹出")
        assert t is not None
        assert "gen_badge" in tg.GENERATED
        assert "多喝水" in t(1080, 1920, 2.0, text="多喝水")
        # 落盘可复用
        assert (tmp_path / "gen_badge.json").is_file()

    @pytest.mark.asyncio
    async def test_失败反馈重试_第二次成功(self, tmp_path, monkeypatch):
        from capabilities.video.services import template_generator as tg

        tg.GENERATED.clear()
        monkeypatch.setattr(tg, "_STORE_DIR", tmp_path)
        bad = GOOD_TEMPLATE.replace(
            "window.seekTo = function(t)",
            "window.x = function(t)",
        )
        llm = self._mock_llm([bad, GOOD_TEMPLATE])
        t = await tg.generate_template(llm, "gen_badge", "角标弹出")
        assert t is not None
        # 第二次 prompt 带第一次的失败反馈
        assert len(llm.calls) == 2
        assert "seekTo" in llm.calls[1]

    @pytest.mark.asyncio
    async def test_重试上限_返回None降级(self, tmp_path, monkeypatch):
        from capabilities.video.services import template_generator as tg

        tg.GENERATED.clear()
        monkeypatch.setattr(tg, "_STORE_DIR", tmp_path)
        bad = GOOD_TEMPLATE.replace(
            "window.seekTo = function(t)",
            "window.x = function(t)",
        )
        llm = self._mock_llm([bad, bad, bad])
        t = await tg.generate_template(llm, "gen_badge", "角标弹出")
        assert t is None
        assert "gen_badge" not in tg.GENERATED

    @pytest.mark.asyncio
    async def test_LLM异常_返回None不炸(self, tmp_path, monkeypatch):
        from capabilities.video.services import template_generator as tg

        tg.GENERATED.clear()
        monkeypatch.setattr(tg, "_STORE_DIR", tmp_path)

        class _Boom:
            async def chat(self, prompt, **kw):
                raise RuntimeError("api down")

        assert await tg.generate_template(_Boom(), "gen_x", "x") is None

    @pytest.mark.asyncio
    async def test_落盘加载_跨进程复用(self, tmp_path, monkeypatch):
        """计划书："生成后零边际成本" — 落盘 JSON 下次启动直接加载。"""
        from capabilities.video.services import template_generator as tg

        tg.GENERATED.clear()
        monkeypatch.setattr(tg, "_STORE_DIR", tmp_path)
        llm = self._mock_llm([GOOD_TEMPLATE])
        await tg.generate_template(llm, "gen_badge", "角标弹出")
        # 模拟新进程：清内存后从盘加载
        tg.GENERATED.clear()
        loaded = tg.load_generated_templates()
        assert "gen_badge" in loaded
        assert "多喝水" in loaded["gen_badge"](1080, 1920, 2.0, text="多喝水")

    @pytest.mark.asyncio
    async def test_损坏落盘文件_跳过不炸(self, tmp_path, monkeypatch):
        import json

        from capabilities.video.services import template_generator as tg

        tg.GENERATED.clear()
        monkeypatch.setattr(tg, "_STORE_DIR", tmp_path)
        (tmp_path / "broken.json").write_text(
            json.dumps({"name": "broken", "html": "<p>no contract</p>"}),
            encoding="utf-8",
        )
        loaded = tg.load_generated_templates()
        assert "broken" not in loaded  # 契约不过 → 不入库

    def test_生成器永不exec_LLM代码(self):
        """安全红线：实现里不得出现 exec/eval — LLM 产出只是数据。"""
        import inspect

        from capabilities.video.services import template_generator as tg

        src = inspect.getsource(tg)
        for banned in ("exec(", "eval(", "__import__"):
            assert banned not in src, f"template_generator 禁用 {banned}"


class TestP1Integration:
    """P1 集成 — 未知模板名穿越规划解析 + skill 渲染触发生成。"""

    def test_规划prompt_提名门槛已放开(self):
        """G1：原门槛"仅当 16 个模板都无法表达时"+"全片最多提名 2 个"
        把生成通道压到基本闲置 —— 放开为"内容需要独特视觉就大胆提名"。"""
        from capabilities.video.services.overlay_orchestrator import (
            OVERLAY_PLAN_PROMPT,
        )

        assert "全片最多提名 2 个" not in OVERLAY_PLAN_PROMPT
        assert "仅当 16 个模板都无法表达" not in OVERLAY_PLAN_PROMPT
        # 新指引仍在：gen_ 提名方式 + requirement 规格 + 内置优先的软引导
        assert "gen_" in OVERLAY_PLAN_PROMPT
        assert "requirement" in OVERLAY_PLAN_PROMPT

    def test_渲染生成预算_足够任意需求直出(self):
        """G1：预算是成本护栏（同片去重后按不同模板数计），不再是表达上限。
        8 个不同定制模板已远超真实单片需要。"""
        from capabilities.video.skills import talking_head_overlay as sk

        assert sk._GEN_BUDGET >= 8

    def test_规划解析_未知模板名保留为str(self):
        """P1 关键路径：LLM 提名 gen_progress_bar 不再被 ValueError 丢弃。"""
        from capabilities.video.services.overlay_orchestrator import (
            _parse_plan_output,
            template_name,
        )

        text = (
            '{"overlays": ['
            '{"segment_id": "o1", "time_start": 1.0, "time_end": 3.0, '
            '"template": "gen_progress_bar", '
            '"params": {"requirement": "进度条从0%涨到100%", "text": "进度"}}]}'
        )
        segs, _, _ = _parse_plan_output(text, 10.0)
        assert len(segs) == 1
        assert template_name(segs[0].template) == "gen_progress_bar"

    def test_未知模板_空间冲突消解不炸(self):
        """gen 模板无 TEMPLATE_LAYER 条目 → 默认 center 带参与消解。"""
        from capabilities.video.services.overlay_orchestrator import (
            OverlayPlan,
            OverlaySegment,
            resolve_overlay_conflicts,
        )

        plan = OverlayPlan(
            video_id="",
            segments=[
                OverlaySegment(
                    segment_id="o1",
                    time_start=0,
                    time_end=3,
                    template="gen_progress_bar",
                    params={},
                ),
                OverlaySegment(
                    segment_id="o2",
                    time_start=1,
                    time_end=4,
                    template="stat_card",
                    params={},
                ),
            ],
        )
        resolved = resolve_overlay_conflicts(plan)
        # 同 center 带 + 时间重叠 → 低优先级（gen=0）被 skip
        skipped = [s for s in resolved.segments if s.skipped]
        assert len(skipped) == 1
        assert skipped[0].segment_id == "o1"

    @pytest.mark.asyncio
    async def test_渲染入口_未知模板触发生成_成功后渲染(self, tmp_path, monkeypatch):
        """端到端最小链路：gen 段 → generate_template（mock LLM）→
        GENERATED 注册 → 模板函数真渲染出 HTML。"""
        from capabilities.video.services import template_generator as tg
        from capabilities.video.services.overlay_orchestrator import OverlayPlan, OverlaySegment
        from capabilities.video.skills.talking_head_overlay import (
            TalkingHeadOverlaySkill,
        )

        tg.GENERATED.clear()
        monkeypatch.setattr(tg, "_STORE_DIR", tmp_path)

        llm = TestGenerateTemplateFlow._mock_llm([GOOD_TEMPLATE])
        plan = OverlayPlan(
            video_id="",
            segments=[
                OverlaySegment(
                    segment_id="o1",
                    time_start=0,
                    time_end=2,
                    template="gen_badge",
                    params={"text": "多喝水", "color": "#4ECDC4"},
                ),
            ],
        )
        # 只测模板解析段（不真跑 Playwright）：直接调 skill 内部逻辑等价物
        # — _render_previews 全链路需渲染器，此处验证生成+注册+可调用
        tpl = await tg.generate_template(llm, "gen_badge", "角标弹出")
        assert tpl is not None and "gen_badge" in tg.GENERATED

        # skill 渲染入口的查找逻辑：TEMPLATES 无 → GENERATED 有
        from capabilities.video.adapters.overlay_templates import TEMPLATES

        tname = "gen_badge"
        fn = TEMPLATES.get(tname) or tg.GENERATED.get(tname)
        assert fn is not None
        html = fn(width=1080, height=1920, duration=2.0, text="多喝水", color="#4ECDC4")
        assert "多喝水" in html and "#4ECDC4" in html
