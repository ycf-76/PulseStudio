"""Agent 自我进化 Skill：动态创建/更新 prompt skill。

Agent 在 ReAct Loop 中可调用此 skill 来：
1. 创建新的 prompt skill（.md 文件 + 即时注册）
2. 更新已有 prompt skill 的内容

这是自我进化闭环的最后一环：复盘 → 提炼改进 → 写入 skill → 下次生效。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from app.core.skill_base import Skill
from app.core.skill_registry import register
from app.engine.schemas import Permission

logger = logging.getLogger(__name__)

_SKILLS_DIR = Path(__file__).resolve().parent.parent.parent / "agents" / "prompts" / "skills"


class SkillCreateInput(BaseModel):
    filename: str = Field(
        description="Skill 文件名（如 'my-copywrite-style.md'），只允许 .md 后缀"
    )
    content: str = Field(
        description="Skill 完整内容，必须包含 YAML frontmatter（node_type/name/description）"
    )
    overwrite: bool = Field(
        default=False,
        description="是否覆盖已有 skill（默认 false，防止误覆盖）"
    )


class SkillCreateOutput(BaseModel):
    success: bool
    skill_name: str = ""
    node_type: str = ""
    message: str = ""


def _validate_frontmatter(content: str) -> tuple[bool, str, str]:
    """验证 YAML frontmatter 中有 node_type 和 name。"""
    if not content.strip().startswith("---"):
        return False, "", ""
    parts = content.split("---", 2)
    if len(parts) < 3:
        return False, "", ""

    fm = parts[1].strip()
    node_type_match = re.search(r'^node_type:\s*(.+)$', fm, re.MULTILINE)
    name_match = re.search(r'^name:\s*(.+)$', fm, re.MULTILINE)

    node_type = node_type_match.group(1).strip().strip('"').strip("'") if node_type_match else ""
    name = name_match.group(1).strip().strip('"').strip("'") if name_match else ""

    return bool(node_type and name), node_type, name


@register
class SkillCreate(Skill):
    """Agent 自我进化：动态创建 prompt skill。

    Agent 在复盘后，可将提炼的改进模式写入新的 skill，
    下次同类型任务自动加载生效。

    安全护栏：
    - 只允许写 .md 文件
    - 必须有合法 frontmatter（node_type + name）
    - 默认不覆盖已有 skill
    - 文件名只允许小写字母/数字/连字符
    """

    node_type = "evolve"
    name = "skill_create"
    display_name = "创建/更新 Skill"
    description = "创建新的 prompt skill 或更新已有 skill，用于自我进化闭环"
    skill_category = "evolve"
    skill_priority = "low"

    input_schema = SkillCreateInput
    output_schema = SkillCreateOutput
    required_permissions = [Permission.SKILL_MANAGE]

    async def execute(self, inputs: dict[str, Any]) -> dict[str, Any]:
        filename = inputs.get("filename", "")
        content = inputs.get("content", "")
        overwrite = inputs.get("overwrite", False)

        context = inputs.get("_context")
        user_id = ""
        if context and hasattr(context, "user_id"):
            user_id = str(context.user_id) or ""

        if not user_id:
            return SkillCreateOutput(
                success=False,
                message="无法获取当前用户身份，skill 创建需要用户隔离",
            ).model_dump()

        if not filename.endswith(".md"):
            return SkillCreateOutput(
                success=False,
                message="只允许创建 .md 后缀的 skill 文件",
            ).model_dump()

        if not re.match(r'^[a-z0-9][a-z0-9\-]*\.md$', filename):
            return SkillCreateOutput(
                success=False,
                message="文件名只允许小写字母、数字、连字符（如 my-copywrite-style.md）",
            ).model_dump()

        valid, node_type, skill_name = _validate_frontmatter(content)
        if not valid:
            return SkillCreateOutput(
                success=False,
                message="Skill 内容必须包含合法 YAML frontmatter（node_type 和 name 字段）",
            ).model_dump()

        user_skills_dir = _SKILLS_DIR / "__user__" / user_id
        target_path = user_skills_dir / filename

        if target_path.exists() and not overwrite:
            return SkillCreateOutput(
                success=False,
                skill_name=skill_name,
                node_type=node_type,
                message=f"Skill 文件 {filename} 已存在，需设置 overwrite=true 才能覆盖",
            ).model_dump()

        try:
            from app.core.prompt_skills import register_prompt_skill_from_content
            skill_cls = register_prompt_skill_from_content(filename, content, user_id=user_id)

            logger.info(
                f"[skill_create] Agent created/updated skill: "
                f"{skill_cls.node_type}.{skill_cls.name} -> {filename} (owner={user_id})"
            )

            return SkillCreateOutput(
                success=True,
                skill_name=skill_cls.name,
                node_type=skill_cls.node_type,
                message=f"Skill {skill_cls.node_type}.{skill_cls.name} 已创建并即时注册生效（用户 {user_id} 私有）",
            ).model_dump()

        except Exception as e:
            logger.warning(f"[skill_create] failed: {e}")
            return SkillCreateOutput(
                success=False,
                skill_name=skill_name,
                node_type=node_type,
                message=f"Skill 创建失败: {e}",
            ).model_dump()