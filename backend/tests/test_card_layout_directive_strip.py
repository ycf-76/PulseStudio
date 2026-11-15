"""卡片内容里的「分页/排版指令」必须被剥离，不能渲染进卡片正文。

背景（2026-11-07 实测）：Reactor LLM 把「第1页（封面，必须有超大主标题）：…」
「要求：每页输出一份结构完整的独立 HTML 文档…」这类制作指令当正文传给
compose_card_deck，_plan 原样灌给规划 LLM，最终制作步骤被渲染进卡片。

三层修复对应三组测试：
1. _strip_layout_directives 确定性剥离（单测）；
2. _plan 喂给规划 LLM 的原文已剥离（FakeLLM 捕获 prompt）；
3. CardComposeInput 接受 platform 字段（此前被 model_fields 过滤静默丢弃）。
"""

from __future__ import annotations

import json

import pytest

from app.cards.composer import _plan, _strip_layout_directives
from app.skills.produce.card_compose import CardComposeInput

_DIRTY_CONTENT = (
    "第1页（封面，必须有超大主标题）：2026计算机凉了？先别急着转行，这话只说对了一半。\n"
    "第2页（数据反差）：整体就业率88.3%，985/211院校95%以上，普通二本68.65%。\n"
    "【P3 冰的一面】传统开发岗竞争比1:15。\n"
    "要求：每页输出一份结构完整的独立 HTML 文档（单 doctype、单 html、单 head、单 body），"
    "封面页主标题不得为空。"
)


class _CaptureLLM:
    """捕获 prompt 并返回一份合法 Plan JSON，用于断言喂进模型的原文。"""

    def __init__(self):
        self.captured_prompt = ""

    async def chat(self, messages, max_tokens=None, **kwargs):
        self.captured_prompt = messages[-1]["content"]
        return {"content": json.dumps({
            "subtitle": "计算机没有寒冬，只有分层",
            "sections": [
                {"kind": "list", "title": "数据反差",
                 "items": [{"text": "整体就业率88.3%", "note": "985/211超95%，二本68.65%"}]},
            ],
            "closing": "评论区扣1",
        }, ensure_ascii=False)}


def test_strip_layout_directives_removes_page_markers():
    cleaned = _strip_layout_directives(_DIRTY_CONTENT)
    # 制作指令必须消失
    assert "第1页" not in cleaned
    assert "第2页" not in cleaned
    assert "【P3" not in cleaned
    assert "要求：每页输出" not in cleaned
    assert "单 doctype" not in cleaned
    # 真实内容必须保留
    assert "2026计算机凉了" in cleaned
    assert "整体就业率88.3%" in cleaned
    assert "竞争比1:15" in cleaned


def test_strip_layout_directives_keeps_normal_content():
    normal = "第一步：磨豆。中细研磨，像细砂糖一样。\n第二步：滤纸预热。用95度热水淋湿滤纸。"
    assert _strip_layout_directives(normal) == normal


@pytest.mark.asyncio
async def test_plan_prompt_receives_sanitized_content():
    llm = _CaptureLLM()
    await _plan("2026计算机不是寒冬，是分层", _DIRTY_CONTENT, llm)
    assert "2026计算机凉了" in llm.captured_prompt
    assert "第1页（封面" not in llm.captured_prompt
    assert "要求：每页输出" not in llm.captured_prompt
    # 提示词里必须明确「制作指令不是内容」的纪律
    assert "制作指令" in llm.captured_prompt


def test_card_compose_input_accepts_platform():
    args = CardComposeInput.model_validate({"topic": "测试主题", "platform": "douyin"})
    assert args.platform == "douyin"
