"""Agent 注册表：用 Python 定义内置 Agent，替代损坏的 configs/*.yaml。

设计：
- `AgentDef` 用 Pydantic 定义（类型安全、IDE 可跳转）。
- `BUILTIN_AGENTS` 用 Python dict 定义内置 Agent。
- `AgentRegistry.build_session()` 把 `AgentDef` 装配成 `ReactorSession`。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.agents.base import AgentDef
from app.agents.ops_team import OPS_TEAM
from app.core.skill_registry import (
    SkillRegistry,
    ensure_builtin_skills_registered,
    get_skill_class_by_name,
)
from app.engine.reactor.session import (
    ReactorSession,
    build_reactor_session,
)
from app.engine.schemas import Permission
from app.skills.creation_type_map import CREATION_TYPE_SKILLS

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


def _load_prompt(name: str) -> str:
    """从 prompts/ 目录加载 prompt 文件。"""
    p = PROMPTS_DIR / f"{name}.md"
    if p.exists():
        return p.read_text(encoding="utf-8")
    logger.warning(f"[registry] prompt file not found: {p}")
    return ""


def _load_prompt_stack(*names: str) -> str:
    """按 Prompt Stack 顺序拼接多个 prompt 文件。

    SOUL.md（人格）→ AGENTS.md（分工）→ 其他按需层。
    层之间用双换行分隔。
    """
    parts: list[str] = []
    for name in names:
        content = _load_prompt(name)
        if content:
            parts.append(content.strip())
    return "\n\n".join(parts)


# AgentDef 实际定义在 base.py（registry 与 ops_team 都需要它，独立成模块
# 才能避免循环 import）；此处保留同名别名，既有 `from ...registry import AgentDef`
# 的调用方不受影响。

# 内置 Agent 定义（llm_model 使用前端展示名，由 get_deepseek_llm 归一化）
BUILTIN_AGENTS: dict[str, AgentDef] = {
    "search": AgentDef(
        agent_id="search",
        role="爆款搜索专家",
        description=(
            "搜索小红书热点、趋势、爆款笔记，以及采集用户给出的任意链接/网页内容。"
            "当需要搜索、查找、调研信息，或用户粘贴链接要求采集/读取/抓取该网页时主动使用。"
        ),
        llm_model="deepseek-v3",
        # url_collect 必须在此白名单里：子 Agent 走显式 skills 白名单时不吃
        # ALWAYS_LOAD_SKILLS，只挂 trending_search 会让「采集这个网页」退化
        # 成关键词搜索（旧路），拿到的是搜索结果而不是该页面正文。
        skills=["trending_search", "url_collect"],
        hard_rules=["search_result_non_empty", "rate_limit_check"],
        recovery={
            "max_attempts": 3,
            "strategies": ["retry", "broaden_keyword", "switch_model:deepseek-v3"],
        },
    ),
    "analyze": AgentDef(
        agent_id="analyze",
        role="爆款分析专家",
        description="分析爆款规律、选题方向、数据趋势。当需要数据分析、趋势洞察、竞品对比时主动使用。",
        llm_model="deepseek-v3",
        skills=["vl_analyze"],
        soft_semantic={"enabled": True},
        prompt_template=(
            "你是小红书爆款分析专家。\n"
            "请分析主题「{topic}」的爆款规律和选题方向。\n"
            "只输出 JSON，包含 insights（趋势洞察）和 recommendations（选题建议数组）。"
        ),
        recovery={
            "max_attempts": 3,
            "strategies": ["retry", "reduce_input", "switch_model:deepseek-v3"],
        },
    ),
    "copywrite": AgentDef(
        agent_id="copywrite",
        role="小红书文案创作专家",
        description="创作小红书笔记文案。当需要写文案、创作内容、生成标题和正文时主动使用。",
        llm_model="deepseek-r1",
        soft_semantic={"enabled": True},
        prompt_template=(
            "你是小红书文案创作专家。\n"
            "请为主题「{topic}」写一篇小红书笔记文案。\n"
            "只输出 JSON，包含 title（标题）、content（正文）、tags（标签数组）。"
        ),
        recovery={
            "max_attempts": 3,
            "strategies": ["retry", "simplify_prompt", "switch_model:deepseek-v3"],
        },
    ),
    "image_plan": AgentDef(
        agent_id="image_plan",
        role="图片规划专家",
        description="规划小红书卡片配图方案。当需要图片排版、视觉规划时主动使用。",
        llm_model="deepseek-v3",
        prompt_template=(
            "你是图片规划专家。\n"
            "请为主题「{topic}」规划小红书卡片配图方案。\n"
            "只输出 JSON，包含 pages（页数组，每页有 type 和 title）。"
        ),
    ),
    "image_gen": AgentDef(
        agent_id="image_gen",
        role="图片生成专家",
        description="生成图片方案描述。当需要 AI 生图、图片创作时主动使用。",
        llm_model="deepseek-v3",
        prompt_template=(
            "你是图片生成专家。\n"
            "请为主题「{topic}」描述需要生成的图片方案。\n"
            "只输出 JSON，包含 images（图片描述数组，每项含 prompt 和 style）。"
        ),
    ),
    "image_review": AgentDef(
        agent_id="image_review",
        role="图片审核专家",
        description="审核图片方案是否符合小红书规范。当需要图片合规检查时主动使用。",
        llm_model="deepseek-v3",
        prompt_template=(
            "你是图片审核专家。\n"
            "请审核图片方案是否符合小红书规范。\n"
            "只输出 JSON，包含 passed（布尔）和 issues（问题数组）。"
        ),
    ),
    "audit": AgentDef(
        agent_id="audit",
        role="合规审核专家",
        description="审核内容合规性，提供独立无偏见的审核视角。当需要检查、审核、验证内容时主动使用。",
        llm_model="deepseek-v3",
        soft_semantic={"enabled": True},
        prompt_template=(
            "你是小红书合规审核专家。\n"
            "请审核内容是否符合平台规范。\n"
            "只输出 JSON，包含 passed（布尔）和 issues（问题数组）。"
        ),
    ),
    "final_review": AgentDef(
        agent_id="final_review",
        role="终审确认专家",
        description="终审确认内容是否可发布。当需要最终发布前确认时主动使用。",
        llm_model="deepseek-v3",
        prompt_template=(
            "你是终审确认专家。\n"
            "请确认内容是否可发布。\n"
            "只输出 JSON，包含 review_status（passed 或 rejected）和 feedback。"
        ),
    ),
    "publish": AgentDef(
        agent_id="publish",
        role="发布专家",
        description="发布内容到平台（小红书发布已移除）。",
        llm_model="deepseek-v3",
        skills=[],
        recovery={"max_attempts": 2, "strategies": ["retry"]},
    ),
    "explore": AgentDef(
        agent_id="explore",
        role="探索研究专家",
        description="综合搜索+分析+文案的探索研究。当需要深度研究某个主题的全链路时主动使用。",
        llm_model="deepseek-r1",
        skills=["trending_search", "url_collect", "vl_analyze", "lively_girl"],
    ),
    "chat_agent": AgentDef(
        agent_id="chat_agent",
        role="小红书运营搭档",
        description="主对话 Agent，协调所有子 Agent 完成用户任务。",
        llm_model="deepseek-v3",
        skills=None,
        # 2026-11-02：20 → 40。chat_agent 是主对话入口，视频任务 20 步不够
        # （切割+拼接+叠加一条龙），文本任务不受影响。子 Agent 砍半后得 20，
        # 与旧主循环上限持平。
        max_iterations=40,
        prompt_template=_load_prompt_stack("SOUL", "AGENTS"),
        recovery={
            "max_attempts": 3,
            "strategies": ["retry", "simplify_prompt", "switch_model:deepseek-v3"],
        },
    ),
}


# 运营团队（10 个角色，persona + 技能白名单），与前端技能库轮播图一一对应
BUILTIN_AGENTS.update(OPS_TEAM)


_skills_cache: list[Any] | None = None


ALWAYS_LOAD_SKILLS: list[str] = [
    "file_read",
    "file_edit",
    "file_write",
    "skill_create",
    # 读链接是跨创作类型的通用能力（用户在任何环节都可能粘贴一个网页/笔记链接），
    # 且没有 bash 通路（bash 白名单不含 curl 的可用描述），必须常驻。
    "url_collect",
    # 转写是跨创作类型的通用工具（口播稿提取/字幕/剪辑前确认），
    # 且没有 bash 通路（dev_tools 白名单不放 whisper），必须常驻。
    "video_transcribe",
    # Messaging/platform tools are intentionally always available. They are a
    # small, stable set and the user may ask for Feishu/WeChat/email at any
    # point inside an otherwise creation-type-scoped session.
    "feishu_file_ops",
    "feishu_wiki_doc",
    "feishu_bitable",
    "wechat_send_text",
    "wechat_send_file",
    "email_send",
]


def _resolve_creation_type_skills(creation_type: str | None) -> list[str] | None:
    """按创作类型解析需要的Skill name列表。返回None表示全量加载。"""
    if creation_type is None:
        return None
    mapping = CREATION_TYPE_SKILLS.get(creation_type)
    if mapping is None:
        logger.warning(
            f"[registry] unknown creation_type={creation_type}, falling back to full skill set"
        )
        return None
    names: list[str] = []
    for group in mapping.values():
        names.extend(group)
    for name in ALWAYS_LOAD_SKILLS:
        if name not in names:
            names.append(name)
    return names


def _build_skills(skill_names: list[str] | None, creation_type: str | None = None) -> list[Any]:
    """按 skill name 从 SkillRegistry 实例化 Skill。

    若 skill_names 为 None 且 creation_type 为 None，动态装载全部已注册 Skill。
    若 creation_type 有值，按 CREATION_TYPE_SKILLS 映射表过滤，只加载该类型需要的子集。
    若 skill_names 显式指定（白名单模式），忽略 creation_type。

    缓存：全量 Skill 无状态，实例化一次后缓存复用；
    按 creation_type 过滤时不走缓存，每次从全量缓存中筛选。
    """
    global _skills_cache

    if skill_names is not None:
        skills: list[Any] = []
        for name in skill_names:
            skill_cls = get_skill_class_by_name(name)
            if skill_cls is None:
                logger.warning(f"[registry] unknown skill name: {name}")
                continue
            skills.append(skill_cls())
        return skills

    resolved = _resolve_creation_type_skills(creation_type)
    if resolved is not None:
        if _skills_cache is None:
            registry = SkillRegistry.instance()
            ensure_builtin_skills_registered()
            registry.scan_third_party(force=False)
            _skills_cache = [cls() for cls in registry.all_skill_classes()]
        name_set = set(resolved)
        filtered = [s for s in _skills_cache if s.name in name_set]

        logger.info(
            f"[registry] creation_type={creation_type}, skills={len(filtered)}/{len(_skills_cache)}"
        )
        return filtered

    if _skills_cache is not None:
        return _skills_cache
    registry = SkillRegistry.instance()
    ensure_builtin_skills_registered()
    registry.scan_third_party(force=False)
    _skills_cache = [cls() for cls in registry.all_skill_classes()]
    return _skills_cache


class AgentRegistry:
    """Agent 注册表。"""

    def __init__(self) -> None:
        self._agents: dict[str, AgentDef] = dict(BUILTIN_AGENTS)

    def get(self, agent_id: str) -> AgentDef | None:
        return self._agents.get(agent_id)

    def list_agents(self) -> list[AgentDef]:
        return list(self._agents.values())

    def register(self, agent_def: AgentDef) -> None:
        """注册或覆盖一个 Agent 定义（运行时动态注册）。"""
        existing = self._agents.get(agent_def.agent_id)
        if existing is not None and existing is not agent_def:
            logger.info(f"[AgentRegistry] override existing agent: " f"{agent_def.agent_id}")
        self._agents[agent_def.agent_id] = agent_def
        logger.debug(f"[AgentRegistry] registered: {agent_def.agent_id}")

    def unregister(self, agent_id: str) -> bool:
        """注销一个运行时注册的 Agent。内置 Agent 不可注销。

        Returns True if successfully removed, False if not found or builtin.
        """
        if agent_id in BUILTIN_AGENTS:
            logger.warning(f"[AgentRegistry] cannot unregister builtin agent: {agent_id}")
            return False
        if agent_id not in self._agents:
            return False
        del self._agents[agent_id]
        logger.info(f"[AgentRegistry] unregistered: {agent_id}")
        return True

    def build_session(
        self,
        agent_id: str,
        workflow_id: str | None = None,
        creation_type: str | None = None,
        auto_approve: bool = True,
    ) -> ReactorSession:
        """把 AgentDef 装配成 ReactorSession。

        模型锁定：`agent_def.llm_model` 决定具体模型，通过 llm 参数
        注入 build_reactor_session，不再被工厂默认值覆盖。
        """
        agent_def = self._agents[agent_id]

        llm = None
        if agent_def.llm_model:
            from app.engine.factory import get_deepseek_llm

            llm = get_deepseek_llm(model=agent_def.llm_model)

        allowed_permissions = {
            Permission.FILE_READ,
            Permission.FILE_WRITE,
            Permission.BASH_EXEC,
            Permission.XHS_SEARCH,
            Permission.NET_HTTP_POST,
            Permission.SKILL_MANAGE,
            Permission.WORK_WRITE,
        }

        # P0-1：技能必须在 build_reactor_session 之前收集并以 skills= 传入——
        # system_prompt 在 ReactorSession 构造期生成，事后赋值 session._skills
        # 会让「可用技能」目录缺节，LLM 不知道 skill_call，视频链路断在第 0 步。
        # P1 接线：技能池按 agent 定义裁剪——白名单 agent 只装白名单技能，
        # chat_agent（skills=None）按 creation_type 走 CREATION_TYPE_SKILLS
        # 映射 + ALWAYS_LOAD + hot/CREATE 保底（详见 _build_skills）；
        # 两者皆 None 时保持全量（兜底语义不变）
        ensure_builtin_skills_registered()
        skill_instances = _build_skills(agent_def.skills, creation_type)

        session = build_reactor_session(
            workflow_id=workflow_id or "",
            llm=llm,
            allowed_permissions=allowed_permissions,
            max_iterations=agent_def.max_iterations,
            auto_approve=auto_approve,
            skills=skill_instances,
            creation_type=creation_type,
            system_prompt_prefix=agent_def.persona or None,
        )

        return session


_registry: AgentRegistry | None = None


def get_agent_registry() -> AgentRegistry:
    """获取全局 AgentRegistry 单例。"""
    global _registry
    if _registry is None:
        _registry = AgentRegistry()
    return _registry
