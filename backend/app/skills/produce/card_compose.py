"""ReAct entry point for structured, editable social cards."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.agents.clarification_schema import ClarifyFieldMeta, ClarifyOption, ClarifyWhen
from app.cards.composer import compose_deck
from app.cards.recipe import DEFAULT_RECIPE, RECIPES, RecipeId, recipe_options
from app.cards.schema import THEMES, ThemeId
from app.core.skill_base import Skill
from app.core.skill_registry import register

# 主题预览图约定路径（与 recipe 预览图由同一套脚本产出）
THEME_PREVIEW_BASE = "/static/recipe-preview/theme"


def _theme_options() -> list[ClarifyOption]:
    """主题直接由 THEMES 编译 —— 加主题只改 schema.py，这里不用动。"""
    preferred = ["inkwash", "cream", "luxe_noir", "botanical", "journal",
                 "forest", "dune", "dopamine", "warm", "swiss", "ink", "terminal"]
    ordered = [n for n in preferred if n in THEMES] + [n for n in THEMES if n not in preferred]
    return [
        ClarifyOption(
            label=THEMES[name]["label"],
            value=name,
            description=THEMES[name]["hint"],
            preview_url=f"{THEME_PREVIEW_BASE}/{name}.png",
        )
        for name in ordered
    ]


class CardComposeInput(BaseModel):
    topic: str = Field(min_length=1, max_length=80, description="卡片主题")
    content: str = Field(default="", max_length=6000, description="用户提供的正文/要点；有现成文案应完整传入。只放内容本身，分页/排版/渲染要求等制作指令不要写进来")
    platform: str = Field(default="", max_length=20, description="发布平台（xiaohongshu/douyin/bilibili等）；只作记录，视觉仍由 theme_id/style_hint 决定")
    asset_ids: list[str] = Field(default_factory=list, max_length=3, description="用户已上传的素材 asset_id；不传则不使用图片")
    # 之前只写了 ["ink","warm"]，另外 7 套主题 LLM 根本选不到。统一走 schema.ThemeId。
    theme_id: ThemeId = Field(
        default="ink",
        description="视觉风格：杂志编辑/暖卡纸/瑞士极简/新中式墨韵/奶油温柔/极客终端/多巴胺Y2K/高奢黑金/手账贴纸",
    )
    image_layout: Literal["top", "side", "overlay"] = Field(default="top", description="图片布局：顶图封面、右图正文、全图覆字")
    recipe_id: RecipeId = Field(
        default=DEFAULT_RECIPE,
        description="整套骨架序列：干货清单/步骤教程/数据解读/观点金句/对比选购/时间线叙事",
    )
    style_hint: str = Field(
        default="",
        max_length=80,
        description="用户自定义视觉风格描述（如'报纸''赛博朋克''手账'），非空时自动走 LLM 直出 HTML 路径",
    )


@register
class ComposeCardDeckSkill(Skill):
    node_type = "produce"
    name = "compose_card_deck"
    display_name = "结构化图文卡片"
    description = (
        "制作小红书多页图文卡片（封面/排版/海报），输出结构化可编辑草稿；"
        "可使用当前用户已上传到素材库的 asset_id 放入图片位。"
        "不支持文生图时必须告知用户，而非伪装生成。"
        "recipe_id（整套骨架）与 theme_id（视觉风格）是审美决策："
        "用户明确说了就按他说的传；没说就**留空不要猜**，系统会让用户自己选。"
    )
    usage_hint = (
        "调用示例：skill_call(skill='compose_card_deck', arguments={"
        "'topic': '5个AI工具', 'content': '正文要点...', "
        "'recipe_id': 'knowledge_list', 'theme_id': 'inkwash'})。"
        "topic 必填；content 有现成文案时应完整传入，但只放内容本身——"
        "分页标记（第N页/【P1】）、HTML渲染要求等制作指令禁止写进 content；"
        "用户说的视觉风格（如「白色报纸风」）必须传 style_hint，不要发明 extra_context 字段；"
        "发布平台传 platform。"
        "recipe_id 和 theme_id 用户已选过则直接传，未选则留空触发澄清。"
    )
    skill_category = "create"
    skill_priority = "hot"
    trigger_words = ["做图文卡片", "知识卡片", "制作图文", "结构化卡片", "图文排版",
                      "做封面", "封面图", "封面卡片", "图文卡片", "小红书图文"]
    input_schema = CardComposeInput

    # 提示词只能「劝」LLM 别发明字段，劝不住的就用这里兜底（2026-11-07 实测
    # extra_context="视觉风格：白色报纸风" 被静默丢弃，风格落回默认 ink）。
    _STYLE_ALIAS_KEYS = ("extra_context", "visual_style", "style_note", "style")

    @classmethod
    def _normalize_compose_inputs(cls, inputs: dict[str, Any]) -> dict[str, Any]:
        """确定性归一化：标签→合法 id、发明出来的风格字段并入 style_hint。

        只动能确定性判断的字段；返回新 dict，不修改入参。
        """
        cleaned = {
            key: value for key, value in inputs.items()
            if key in CardComposeInput.model_fields and key != "_context"
        }

        # LLM 发明的风格字段（extra_context 等）→ 并入 style_hint，不再静默丢弃
        for alias in cls._STYLE_ALIAS_KEYS:
            value = inputs.get(alias)
            if isinstance(value, str) and value.strip() and not cleaned.get("style_hint"):
                cleaned["style_hint"] = value.strip()[:80]

        # recipe_id / theme_id 传了中文标签（如「数据解读」「手账贴纸」）→ 映射回合法 id；
        # theme_id 给了完全不认识的值（如「白色报纸风」）→ 当作 style_hint 走自由风格，
        # 而不是让 Literal 校验报一堆英文枚举错误。
        recipe_value = cleaned.get("recipe_id")
        if recipe_value and recipe_value not in RECIPES:
            for recipe in RECIPES.values():
                if recipe_value in (recipe.label, recipe.label.rstrip("型")):
                    cleaned["recipe_id"] = recipe.id
                    break
        theme_value = cleaned.get("theme_id")
        if theme_value and theme_value not in THEMES:
            label_map = {info["label"]: name for name, info in THEMES.items()}
            if theme_value in label_map:
                cleaned["theme_id"] = label_map[theme_value]
            else:
                cleaned.pop("theme_id")
                if not cleaned.get("style_hint"):
                    cleaned["style_hint"] = str(theme_value)[:80]
        return cleaned

    # 澄清字段：让用户拍板「整套骨架」与「视觉风格」，而不是由 LLM 自由发挥。
    #
    # ⚠️ 字段名必须与 CardComposeInput 的入参名一致：
    #   loop.py 的 Stage 0 用 `_fname in arguments` 判断是否已明确 —— 名字对不上时，
    #   LLM 明明传了 recipe_id 仍会被拦下来问一遍，且澄清结果也灌不进工具入参。
    #
    # 时机用 missing 而不是 always，配合 `_fname in _already_clarified` 的判定：
    #   第一次真正弹卡让用户选，答过之后自动记住，不再打断熟练用户。
    #   真正的可控感由成品上的常驻「换骨架 / 换风格」保证，不靠事前拦截。
    #
    # auto_default 的差异是刻意的：
    #   骨架 / 风格 = 用户最该拍板的两项 → False，第一次必须选（否则只会拿到 LLM 的猜测）
    #   图片用法 = 技术性强、用户通常没概念 → True，走默认值 + 非阻塞建议卡
    clarify_meta: dict[str, ClarifyFieldMeta] = {
        "recipe_id": ClarifyFieldMeta(
            hint="这套卡片用哪种骨架？",
            when=ClarifyWhen.missing,
            default=DEFAULT_RECIPE,
            auto_default=False,
            options=[ClarifyOption(**opt) for opt in recipe_options()],
        ),
        "theme_id": ClarifyFieldMeta(
            hint="视觉风格选哪个？",
            when=ClarifyWhen.missing,
            default="ink",
            auto_default=False,
            options=_theme_options(),
        ),
        "image_layout": ClarifyFieldMeta(
            hint="图片怎么用？",
            when=ClarifyWhen.missing,
            default="top",
            auto_default=True,
            options=[
                ClarifyOption(label="顶图封面", value="top", description="图片放封面顶部，正文用排版页"),
                ClarifyOption(label="右图正文", value="side", description="正文页左侧清单、右侧配图"),
                ClarifyOption(label="全图覆字", value="overlay", description="整页大图 + 底部压字，氛围感强"),
            ],
        ),
    }

    async def execute(self, inputs: dict[str, Any]) -> dict[str, Any]:
        context = inputs.get("_context")
        user_id = getattr(context, "user_id", None)
        if not user_id:
            return {"error": "无法确认当前用户身份，不能读取图片素材"}
        try:
            # 归一化兜底在前：标签→id、发明的风格字段→style_hint，再走 schema 校验
            args = CardComposeInput.model_validate(self._normalize_compose_inputs(inputs))
            draft = await compose_deck(
                topic=args.topic, content=args.content, user_id=str(user_id),
                llm=inputs.get("llm"), asset_ids=args.asset_ids,
                theme_id=args.theme_id, image_layout=args.image_layout,
                recipe_id=args.recipe_id, style_hint=args.style_hint,
            )
        except ValueError as exc:
            return {"error": str(exc), "_source": "structured_cards"}
        return {
            "card_draft": draft,
            "report": f"已制作 {len(draft['pages'])} 张可编辑结构化卡片，已在作品区提供预览。",
            "_source": "structured_cards",
        }