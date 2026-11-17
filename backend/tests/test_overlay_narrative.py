"""P2 两步链测试 — 叙事结构分析 → 叠加规划（参考 NarratoAI）。

链路：
  第一步 generate_narrative：LLM 输出叙事结构摘要
    （theme/palette/sections[]: role 开场-论点-转折-收束 + visual_intent）
  第二步 generate_overlay_plan(use_two_step=True)：叙事块拼在规划
    prompt 头部，LLM 在结构内落时间轴/模板/参数。

降级契约（与全管线一致）：叙事生成/解析失败 → 无声降级单步链路，
绝不阻断规划。
"""

from __future__ import annotations

import json

import pytest

from capabilities.video.services.overlay_orchestrator import (
    NARRATIVE_PROMPT,
    OverlayPlan,
    _parse_narrative_output,
    generate_narrative,
    generate_overlay_plan,
)


class _L:
    """Mock LLM — 响应队列逐个弹出，记录每次调用的 prompt。"""

    def __init__(self, *responses: str):
        self.calls: list[str] = []
        self._resp = list(responses)

    async def chat(self, messages=None, **kw):
        self.calls.append(messages[0]["content"])
        if not self._resp:
            raise AssertionError("mock LLM 响应队列耗尽")
        return {"content": self._resp.pop(0)}


_GOOD_NARRATIVE = {
    "theme": "夏天健康三提醒",
    "palette": ["#FF4D4D", "#FFE066", "#4ECDC4"],
    "sections": [
        {
            "section_id": "s1",
            "role": "opening",
            "time_start": 0.0,
            "time_end": 4.1,
            "summary": "警告开场",
            "visual_intent": "full_card 冲击开场",
            "key_points": ["三件事"],
        },
        {
            "section_id": "s2",
            "role": "argument",
            "time_start": 4.1,
            "time_end": 40.0,
            "summary": "三个论点",
            "visual_intent": "section_card 分段推进",
            "key_points": ["空调", "冰水", "防晒"],
        },
        {
            "section_id": "s3",
            "role": "closing",
            "time_start": 40.0,
            "time_end": 54.0,
            "summary": "收束",
            "visual_intent": "full_card 收尾",
            "key_points": [],
        },
    ],
}

_PLAN_JSON = json.dumps(
    {
        "palette": ["#FF4D4D", "#FFE066", "#4ECDC4"],
        "overlays": [
            # 连续覆盖全片 0-54s — 密度自检（覆盖率 ≥30% + 任意空档 ≤6s）
            # 双项均通过（单测聚焦两步链编排，不测重试）
            {
                "segment_id": "o1",
                "time_start": 0.0,
                "time_end": 27.0,
                "template": "full_card",
                "params": {"text": "夏天千万别大意", "color": "#FF4D4D"},
            },
            {
                "segment_id": "o2",
                "time_start": 27.0,
                "time_end": 54.0,
                "template": "section_card",
                "params": {"number": "01", "title": "空调直吹", "color": "#FFE066"},
            },
        ],
    },
    ensure_ascii=False,
)

_SEGS = [
    {
        "index": 0,
        "start": 0.0,
        "end": 4.1,
        "text": "夏天千万别大意",
        "words": [{"text": "夏天", "start": 0.0, "end": 0.5}],
    },
]


# ── 1. _parse_narrative_output（防御式解析）──────────────


class TestParseNarrative:
    def test_合法JSON_解析成功(self):
        out = _parse_narrative_output(json.dumps(_GOOD_NARRATIVE, ensure_ascii=False), 54.0)
        assert out is not None
        assert out["theme"] == "夏天健康三提醒"
        assert len(out["sections"]) == 3
        assert out["palette"] == ["#FF4D4D", "#FFE066", "#4ECDC4"]

    def test_代码围栏包裹_仍可解析(self):
        """LLM 爱加 markdown 围栏 — 与 _parse_plan_output 同策略容错。"""
        fenced = f"```json\n{json.dumps(_GOOD_NARRATIVE, ensure_ascii=False)}\n```"
        out = _parse_narrative_output(fenced, 54.0)
        assert out is not None
        assert out["theme"] == "夏天健康三提醒"

    def test_坏JSON_返回None(self):
        assert _parse_narrative_output("这不是JSON", 54.0) is None
        assert _parse_narrative_output("", 54.0) is None
        assert _parse_narrative_output("{broken", 54.0) is None

    def test_缺sections_返回None(self):
        assert _parse_narrative_output('{"theme": "x", "palette": []}', 54.0) is None

    def test_空sections_返回None(self):
        payload = json.dumps({"theme": "x", "palette": [], "sections": []})
        assert _parse_narrative_output(payload, 54.0) is None

    def test_非dict顶层_返回None(self):
        assert _parse_narrative_output('["a", "b"]', 54.0) is None

    def test_段落乱序_按time_start排序(self):
        n = json.loads(json.dumps(_GOOD_NARRATIVE))
        n["sections"].reverse()
        out = _parse_narrative_output(json.dumps(n, ensure_ascii=False), 54.0)
        assert out is not None
        starts = [s["time_start"] for s in out["sections"]]
        assert starts == sorted(starts)

    def test_时间越界_clamp到时长内(self):
        n = json.loads(json.dumps(_GOOD_NARRATIVE))
        n["sections"][0]["time_end"] = 999.0
        n["sections"][2]["time_start"] = -5.0
        out = _parse_narrative_output(json.dumps(n, ensure_ascii=False), 54.0)
        assert out is not None
        assert out["sections"][0]["time_end"] <= 54.0
        assert out["sections"][2]["time_start"] >= 0.0

    def test_时长为0_不clamp(self):
        """duration 探测失败时不能把所有段 clamp 成 0。"""
        out = _parse_narrative_output(json.dumps(_GOOD_NARRATIVE, ensure_ascii=False), 0.0)
        assert out is not None
        assert out["sections"][1]["time_end"] == 40.0

    def test_段缺失关键字段_丢弃该段保留其余(self):
        n = json.loads(json.dumps(_GOOD_NARRATIVE))
        del n["sections"][1]["time_start"]  # 无时间戳的段不可用
        out = _parse_narrative_output(json.dumps(n, ensure_ascii=False), 54.0)
        assert out is not None
        assert len(out["sections"]) == 2

    def test_繁体key_points归一化为简体(self):
        """转录词流偶发繁体会被叙事步回显 — 中间层统一简体（v1.4 模式）。
        run12 实证：key_points 出现 別/這/氣溫飆升。"""
        n = json.loads(json.dumps(_GOOD_NARRATIVE))
        n["sections"][0]["key_points"] = ["夏天別大意", "這三件事"]
        n["sections"][0]["summary"] = "氣溫飆升警示"
        out = _parse_narrative_output(json.dumps(n, ensure_ascii=False), 54.0)
        assert out is not None
        assert out["sections"][0]["key_points"] == ["夏天别大意", "这三件事"]
        assert out["sections"][0]["summary"] == "气温飙升警示"


# ── 2. generate_narrative（第一步）───────────────────────


class TestGenerateNarrative:
    @pytest.mark.asyncio
    async def test_成功返回叙事结构(self):
        llm = _L(json.dumps(_GOOD_NARRATIVE, ensure_ascii=False))
        out = await generate_narrative(llm, _SEGS, 54.0)
        assert out is not None
        assert out["theme"] == "夏天健康三提醒"
        assert len(llm.calls) == 1

    @pytest.mark.asyncio
    async def test_prompt含叙事角色与结构要求(self):
        llm = _L(json.dumps(_GOOD_NARRATIVE, ensure_ascii=False))
        await generate_narrative(llm, _SEGS, 54.0)
        p = llm.calls[0]
        for kw in ("开场", "论点", "收束", "visual_intent", "palette", "sections"):
            assert kw in p, f"叙事 prompt 缺少 {kw}"

    @pytest.mark.asyncio
    async def test_垃圾响应_降级None(self):
        llm = _L("我拒绝输出 JSON")
        assert await generate_narrative(llm, _SEGS, 54.0) is None

    @pytest.mark.asyncio
    async def test_feedback拼入叙事prompt(self):
        """P0 审查反馈进第一步 — 结构层反思（可解释中间层）。"""
        llm = _L(json.dumps(_GOOD_NARRATIVE, ensure_ascii=False))
        await generate_narrative(llm, _SEGS, 54.0, feedback="o3 文字塌缩请修")
        assert "o3 文字塌缩请修" in llm.calls[0]

    @pytest.mark.asyncio
    async def test_异常不抛出(self):
        class _Boom:
            async def chat(self, **kw):
                raise RuntimeError("网络炸了")

        assert await generate_narrative(_Boom(), _SEGS, 54.0) is None


# ── 3. generate_overlay_plan 两步链编排 ──────────────────


class TestTwoStepPlan:
    @pytest.mark.asyncio
    async def test_两步链_叙事块拼入规划prompt(self):
        llm = _L(
            json.dumps(_GOOD_NARRATIVE, ensure_ascii=False),
            _PLAN_JSON,
        )
        plan, _ = await generate_overlay_plan(_SEGS, 54.0, llm, use_two_step=True)
        # 第一次调用 = 叙事分析，第二次 = 叠加规划
        assert len(llm.calls) == 2
        assert "叙事" in llm.calls[0] or "narrative" in llm.calls[0].lower()
        plan_prompt = llm.calls[1]
        assert "夏天健康三提醒" in plan_prompt  # 叙事 JSON 已注入
        assert "visual_intent" in plan_prompt
        assert "section_card 分段推进" in plan_prompt  # 段意图进规划上下文

    @pytest.mark.asyncio
    async def test_叙事结构落到plan字段(self):
        llm = _L(
            json.dumps(_GOOD_NARRATIVE, ensure_ascii=False),
            _PLAN_JSON,
        )
        plan, _ = await generate_overlay_plan(_SEGS, 54.0, llm, use_two_step=True)
        assert isinstance(plan, OverlayPlan)
        assert plan.narrative is not None
        assert plan.narrative["theme"] == "夏天健康三提醒"
        assert len(plan.narrative["sections"]) == 3

    @pytest.mark.asyncio
    async def test_叙事失败_无声降级单步链(self):
        """第一步垃圾输出 → 直接单步规划，产物照常（绝不阻断）。"""
        llm = _L("垃圾响应", _PLAN_JSON)
        plan, _ = await generate_overlay_plan(_SEGS, 54.0, llm, use_two_step=True)
        assert len(llm.calls) == 2  # 叙事 1 次 + 单步规划 1 次
        assert "夏天健康三提醒" not in llm.calls[1]  # 无叙事块 = 单步形态
        assert plan.narrative is None
        assert len(plan.segments) == 2  # 规划照常产出

    @pytest.mark.asyncio
    async def test_默认单步_行为不变(self):
        """use_two_step 缺省 False — 现有调用方零改动零影响。"""
        llm = _L(_PLAN_JSON)
        plan, _ = await generate_overlay_plan(_SEGS, 54.0, llm)
        assert len(llm.calls) == 1
        assert plan.narrative is None
        assert len(plan.segments) == 2

    @pytest.mark.asyncio
    async def test_feedback两步链_双拼(self):
        """反馈同时进叙事（结构反思）与规划（细节修复）prompt。"""
        llm = _L(
            json.dumps(_GOOD_NARRATIVE, ensure_ascii=False),
            _PLAN_JSON,
        )
        await generate_overlay_plan(
            _SEGS,
            54.0,
            llm,
            feedback="o1 配色刺眼",
            use_two_step=True,
        )
        assert "o1 配色刺眼" in llm.calls[0]  # 叙事 prompt
        assert "o1 配色刺眼" in llm.calls[1]  # 规划 prompt

    @pytest.mark.asyncio
    async def test_无LLM_两步链安全空转(self):
        plan, raw = await generate_overlay_plan(_SEGS, 54.0, None, use_two_step=True)
        assert plan.segments == []
        assert raw == []

    @pytest.mark.asyncio
    async def test_叙事块不含palette另定指令冲突(self):
        """叙事块必须声明 palette 已定 — 避免与主 prompt '先定色盘' 打架。"""
        llm = _L(
            json.dumps(_GOOD_NARRATIVE, ensure_ascii=False),
            _PLAN_JSON,
        )
        await generate_overlay_plan(_SEGS, 54.0, llm, use_two_step=True)
        head = llm.calls[1].split("分析以下口播视频")[0]  # 叙事块在主 prompt 之前
        assert "palette" in head


# ── 4. NARRATIVE_PROMPT 内容契约 ─────────────────────────


class TestNarrativePrompt:
    def test_角色枚举齐全(self):
        """计划书 P2：开场/论点/转折/收束 四角色。"""
        for kw in ("opening", "argument", "transition", "closing", "开场", "论点", "转折", "收束"):
            assert kw in NARRATIVE_PROMPT, f"叙事 prompt 缺角色 {kw}"

    def test_要求输出theme与key_points(self):
        assert "theme" in NARRATIVE_PROMPT
        assert "key_points" in NARRATIVE_PROMPT

    def test_要求时间戳对齐词级数据(self):
        """叙事段时间边界必须落在词间隙 — 与叠加'图形落在词上'同源。"""
        assert "词" in NARRATIVE_PROMPT and "时间戳" in NARRATIVE_PROMPT

    def test_要求JSON输出(self):
        assert "JSON" in NARRATIVE_PROMPT
