"""卡片链路的确定性加固测试（非提示词层）。

2026-11-07 实测链路三处硬缺陷：
1. LLM 传中文标签 recipe_id="数据解读" → get_recipe 静默回退默认骨架，
   用户拍板的骨架被换掉且无人知晓（对应日志「Layer 2: params missing」循环）。
2. LLM 发明 extra_context="视觉风格：白色报纸风" → execute() 按 model_fields
   过滤静默丢弃 → 用户风格丢失（17:19 白色报纸风落成 ink 的根因）。
3. _render_free_form max_tokens=8192 被打满截断（日志 completion=8192），
   缺失页被静默丢弃，末页可能是不完整 HTML，无任何告警与补救。
"""

from __future__ import annotations

import json

import pytest

from app.cards.composer import Plan, Section, _render_free_form
from app.cards.recipe import RECIPES, get_recipe
from app.skills.produce.card_compose import ComposeCardDeckSkill

_normalize_compose_inputs = ComposeCardDeckSkill._normalize_compose_inputs


# ---------- 1) 参数归一化：标签→id、未知风格键并入 style_hint ----------


def test_normalize_recipe_label_to_id():
    cleaned = _normalize_compose_inputs({"recipe_id": "数据解读"})
    assert cleaned["recipe_id"] == "data_insight"


def test_normalize_theme_label_to_id():
    cleaned = _normalize_compose_inputs({"theme_id": "手账贴纸"})
    assert cleaned["theme_id"] == "journal"


def test_unknown_theme_value_moves_to_style_hint():
    cleaned = _normalize_compose_inputs({"theme_id": "白色报纸风"})
    assert "theme_id" not in cleaned
    assert "白色报纸风" in cleaned["style_hint"]


def test_extra_context_merged_into_style_hint():
    cleaned = _normalize_compose_inputs(
        {
            "extra_context": "视觉风格：白色报纸风",
            "platform": "douyin",
        }
    )
    assert "白色报纸风" in cleaned["style_hint"]
    assert cleaned["platform"] == "douyin"


def test_valid_ids_untouched():
    cleaned = _normalize_compose_inputs(
        {
            "recipe_id": "tutorial_steps",
            "theme_id": "swiss",
            "style_hint": "报纸",
        }
    )
    assert cleaned["recipe_id"] == "tutorial_steps"
    assert cleaned["theme_id"] == "swiss"
    assert cleaned["style_hint"] == "报纸"


def test_get_recipe_accepts_label_and_rejects_unknown():
    assert get_recipe("步骤教程型").id == "tutorial_steps"
    with pytest.raises(ValueError, match="data_insight"):
        get_recipe("不存在的骨架")


# ---------- 2) 自由风格渲染：截断检测 + 续写补救 ----------


def _html_page(title: str) -> str:
    return (
        f"<!doctype html><html><head><title>{title}</title></head>"
        f"<body><h1>{title}</h1></body></html>"
    )


class _ScriptedLLM:
    """按脚本依次返回响应；记录每次 prompt。"""

    def __init__(self, responses: list[str]):
        self.responses = list(responses)
        self.prompts: list[str] = []

    async def chat(self, messages, max_tokens=None, **kwargs):
        self.prompts.append(messages[-1]["content"])
        return {"content": self.responses.pop(0)}


def _plan_with(n: int) -> Plan:
    sections = [Section(kind="text", title=f"第{i+1}页", text=f"内容{i+1}") for i in range(n)]
    return Plan(subtitle="副标题", sections=sections, closing="收尾")


@pytest.mark.asyncio
async def test_truncated_output_triggers_continuation_and_merges():
    truncated = (
        "封面\n```html\n" + _html_page("封面页") + "\n```\n\n"
        "正文\n```html\n" + _html_page("正文页") + "\n```\n\n"
        "结尾\n```html\n<!doctype html><html><head><title>结尾"
        # ← 截断：无收尾 </html>、无闭合 ```
    )
    llm = _ScriptedLLM(
        [
            truncated,
            "结尾\n```html\n" + _html_page("结尾页") + "\n```\n",
        ]
    )
    draft = await _render_free_form(_plan_with(3), "测试主题", "报纸风", llm)
    assert len(llm.prompts) == 2, "截断后必须发起一次续写调用"
    assert "第3页「第3页」" in llm.prompts[1], "续写 prompt 必须点名还缺哪页"
    assert len(draft["pages"]) == 3
    assert draft["pages"][-1]["htmlContent"].endswith("</html>")


@pytest.mark.asyncio
async def test_continuation_still_truncated_raises():
    truncated = "封面\n```html\n" + _html_page("封面页") + "\n```\n\n结尾\n```html\n<!doctype"
    llm = _ScriptedLLM([truncated, truncated])
    with pytest.raises(ValueError, match="截断"):
        await _render_free_form(_plan_with(3), "测试主题", "报纸风", llm)


@pytest.mark.asyncio
async def test_complete_output_single_call():
    raw = "\n\n".join(
        f"{'封面' if i == 0 else '正文'}\n```html\n{_html_page(f'页{i}')}\n```" for i in range(3)
    )
    llm = _ScriptedLLM([raw])
    draft = await _render_free_form(_plan_with(3), "测试主题", "报纸风", llm)
    assert len(llm.prompts) == 1
    assert len(draft["pages"]) == 3
