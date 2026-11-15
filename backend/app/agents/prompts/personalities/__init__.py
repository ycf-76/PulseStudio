"""AI 人格加载器：从预设 .md 文件或用户自定义文本构建人格 prompt 注入块。

预设人格文件位于 prompts/personalities/ 目录，每个 .md 文件名即人格 ID。
用户自定义人格存于 user_profiles.ai_personality_custom 字段。
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

PERSONALITIES_DIR = Path(__file__).resolve().parent

_PERSONALITY_PRESETS: dict[str, str] | None = None

_PRESET_LABELS: dict[str, str] = {
    "default": "默认（专业简洁）",
    "spicy": "毒舌损友",
    "dramatic": "话痨戏精",
    "deadpan": "冷幽默",
    "netslang": "网感型",
    "custom": "自定义",
}

_PRESET_DESCRIPTIONS: dict[str, str] = {
    "default": "靠谱、专业、不废话的 AI 搭档",
    "spicy": "嘴毒但靠谱的朋友——损你是真损，帮你也是真帮",
    "dramatic": "话痨戏精，情绪拉满，热情是真的专业也是真的",
    "deadpan": "一本正经地搞笑，表面严肃内核腹黑",
    "netslang": "网感拉满，热梗信手拈来，真的在网上冲浪的专业选手",
    "custom": "自己写人格设定，想怎么玩怎么玩",
}


def _load_presets() -> dict[str, str]:
    """扫描 personalities/ 目录加载所有 .md 预设。"""
    presets: dict[str, str] = {}
    for p in PERSONALITIES_DIR.glob("*.md"):
        name = p.stem
        try:
            content = p.read_text(encoding="utf-8").strip()
            if content:
                presets[name] = content
        except Exception as e:
            logger.warning(f"[personality] failed to load preset {name}: {e}")
    return presets


def get_personality_presets() -> dict[str, str]:
    """获取所有预设人格内容（懒加载 + 缓存）。"""
    global _PERSONALITY_PRESETS
    if _PERSONALITY_PRESETS is None:
        _PERSONALITY_PRESETS = _load_presets()
    return _PERSONALITY_PRESETS


def list_personalities() -> list[dict[str, str]]:
    """列出所有可用人格（预设 + custom），供前端选择器使用。"""
    result: list[dict[str, str]] = []
    for pid, label in _PRESET_LABELS.items():
        desc = _PRESET_DESCRIPTIONS.get(pid, "")
        result.append({
            "id": pid,
            "label": label,
            "description": desc,
        })
    return result


def build_personality_prompt(
    ai_personality: str | None = "default",
    ai_personality_custom: str | None = None,
) -> str:
    """根据用户选择的人格构建 prompt 注入块。

    返回空串表示无注入（default 人格不注入，保持 SOUL.md 原样）。
    custom 人格使用 ai_personality_custom 文本。
    预设人格从 .md 文件加载。
    """
    if not ai_personality or ai_personality == "default":
        return ""

    if ai_personality == "custom":
        if not ai_personality_custom or not ai_personality_custom.strip():
            return ""
        cleaned = ai_personality_custom.strip()[:2000]
        return f"【AI 人格设定 — 你必须按以下人格说话和行动】\n{cleaned}"

    presets = get_personality_presets()
    content = presets.get(ai_personality)
    if not content:
        logger.warning(f"[personality] unknown preset: {ai_personality}, skipping")
        return ""

    return f"【AI 人格设定 — 你必须按以下人格说话和行动】\n{content}"


def reload_presets() -> None:
    """强制重新加载预设文件（热更新用）。"""
    global _PERSONALITY_PRESETS
    _PERSONALITY_PRESETS = None