"""内核纯净度契约（engine/ 全目录）：能力层泄漏防回潮。

锁定内容（2026-10 内核红线）：
1. engine/ 全部 .py **模块级**禁止 import app.skills.* / app.agents.*
   （延迟装配点的函数内 import 不受限——factory/session 的既有手法）
2. engine/ 业务工具名 grep 白名单：仅 factory.py（测试 mock 装配层）、
   world_state.py（section 语义名）、clarify_policy.py（node_type 词表）、
   events.py（遗留 wire 契约，已标注）豁免
3. primitives.py / prompt.py / guardian.py / session.py 等核心文件零业务名

全静态检查，离线。
"""
from __future__ import annotations

import ast
from pathlib import Path

ENGINE_DIR = Path("app/engine")

# 业务工具名禁词（内核不得出现，除白名单文件）
BUSINESS_NAMES = [
    "multi_image_video", "auto_short_video", "compose_card_deck",
    "xhs_search", "trending_search", "vl_analyze", "copywrite",
    "caption_hashtag", "card_design", "card_xiaohongshu", "card_compose",
    "card_image_gen", "ai_image_gen", "piper_tts", "tts_edge", "meme_render",
    "url_collect", "spider_sync", "bilibili_search", "generate_video",
    "compose_video", "quality_gate", "topic_evaluator",
]

# 纯净度豁免文件（相对 app/engine/）：装配/测试 mock 层与已标注的遗留契约
NAME_WHITELIST = {
    "factory.py",              # 测试 mock 装配层（_MockLLM mock 数据）
    "world_state.py",          # section 语义名（"copywrite" 是状态区标识）
    "clarify_policy.py",       # node_type 词表（"copywrite"/"card_design" 是节点类型值）
    "events.py",               # 遗留 wire 契约（multi_image_video_progress，前端在线消费）
}

# 模块级 import 禁止的顶层模块（能力层）
FORBIDDEN_TOP_MODULES = ("app.skills", "app.agents")


def _iter_engine_py():
    yield from sorted(ENGINE_DIR.rglob("*.py"))


def test_engine_no_module_level_capability_imports():
    """engine/ 模块级禁止 import 能力层（app.skills / app.agents）。"""
    offenders = []
    for py in _iter_engine_py():
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        for node in tree.body:  # 仅模块级
            targets: list[str] = []
            if isinstance(node, ast.Import):
                targets = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                targets = [node.module]
            for t in targets:
                if any(t == m or t.startswith(m + ".") for m in FORBIDDEN_TOP_MODULES):
                    offenders.append(f"{py}:{node.lineno} import {t}")
    assert not offenders, (
        "engine 模块级 import 能力层（违反内核红线）：\n" + "\n".join(offenders)
        + "\n（函数内延迟 import 的运行时装配点不受限；模块级依赖必须走注入/注册）"
    )


def test_engine_business_names_only_in_whitelisted_files():
    """业务工具名只允许出现在白名单文件（装配层/语义名/遗留契约）。"""
    offenders = []
    for py in _iter_engine_py():
        if py.name in NAME_WHITELIST:
            continue
        text = py.read_text(encoding="utf-8")
        for name in BUSINESS_NAMES:
            if name in text:
                offenders.append(f"{py}: {name}")
    assert not offenders, (
        "业务工具名渗入内核文件（应下沉到技能 usage_hint / 能力侧注册表）：\n"
        + "\n".join(offenders)
    )


def test_skill_call_description_is_business_agnostic():
    """skill_call 的 tools schema description 不得含业务技能名
    （description 直接进 LLM 上下文，是纯净化主战场之一）。"""
    from app.engine.reactor.primitives import SkillCallPrimitive

    desc = SkillCallPrimitive.description
    for name in BUSINESS_NAMES:
        assert name not in desc, f"skill_call description 含业务名: {name}"


def test_prompt_has_no_business_names():
    """prompt.py 零业务名（技能指引已下沉 usage_hint 动态聚合）。"""
    from app.engine.reactor import prompt as prompt_mod

    src = Path(prompt_mod.__file__).read_text(encoding="utf-8")
    for name in BUSINESS_NAMES:
        assert name not in src, f"prompt.py 残留业务名: {name}"


def test_usage_hint_aggregates_into_catalog():
    """usage_hint 机制工作：技能自带的用法指引进系统提示段（保住两步走纪律）。"""
    from app.core.skill_registry import ensure_builtin_skills_registered, SkillRegistry
    from app.engine.reactor.prompt import build_skill_catalog_section

    ensure_builtin_skills_registered()
    skills = SkillRegistry.instance().all_skill_classes()
    section = build_skill_catalog_section(skills)
    assert "两步走" in section and "refine_shots" in section, (
        "视频两步走纪律应来自技能 usage_hint（不得回填内核 prompt.py）"
    )
