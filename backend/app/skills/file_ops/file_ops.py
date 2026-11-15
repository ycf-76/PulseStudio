"""File operation skills: read, write, edit — with PathGuard workspace restriction.

Red line: all file operations are restricted to WORKSPACE_ROOT.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from app.engine.schemas import Permission
from app.core.skill_base import Skill
from app.core.skill_registry import register
from app.engine.reactor.file_ops_core import (
    edit_file as _core_edit,
    read_file as _core_read,
    resolve_and_guard,
    workspace_root as _workspace_root,
    write_file as _core_write,
)

# ---------------------------------------------------------------------------
# PathGuard
# ---------------------------------------------------------------------------

def _workspace_root() -> Path:
    """Return the workspace root, guarding all file operations.

    优先级：会话级 cwd（用户在侧边栏指定的文件夹）> WORKSPACE_ROOT 环境变量
    > 默认 backend/data/workspace/。

    安全红线：任何情况下都不回退到项目根目录，否则 LLM 可经 file_read/grep
    读到 backend/.env 里的全部密钥（FILE_READ 属于默认低危权限，任何节点自动具备）。
    """
    from app.core.context_vars import current_workspace_root

    bound = current_workspace_root.get()
    if bound:
        return Path(bound).resolve()

    root = os.environ.get("WORKSPACE_ROOT", "")
    if root:
        return Path(root).resolve()
    # 默认：backend/data/workspace/（与 checkpoints 同级，物理隔离 .env 与源码）
    backend_dir = Path(__file__).resolve().parent.parent.parent
    return (backend_dir / "data" / "workspace").resolve()


def _resolve_and_guard(raw_path: str) -> Path:
    """Resolve path and enforce it stays within WORKSPACE_ROOT.

    Supports relative paths (against the workspace root) and absolute paths
    that stay inside it — same <cwd> semantics as the kernel primitives.

    Raises ValueError if the resolved path escapes the workspace.
    """
    ws = _workspace_root()
    if not ws.exists():
        ws.mkdir(parents=True, exist_ok=True)

    candidate = Path(raw_path)
    p = (candidate if candidate.is_absolute() else ws / raw_path).resolve()

    # Must be under workspace root
    try:
        p.relative_to(ws)
    except ValueError:
        raise ValueError(
            f"path {raw_path!r} resolves to {p}, which is outside workspace {ws}"
        )
    return p


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class FileReadInput(BaseModel):
    path: str = Field(..., description="文件路径（相对于工作区根目录）")
    offset: int = Field(default=0, ge=0, description="起始行号（0-based）")
    limit: int = Field(default=200, ge=1, le=2000, description="最多读取行数")


class FileWriteInput(BaseModel):
    path: str = Field(..., description="文件路径（相对于工作区根目录）")
    content: str = Field(..., description="文件内容")


class FileEditInput(BaseModel):
    path: str = Field(..., description="文件路径（相对于工作区根目录）")
    old_string: str = Field(..., description="要替换的原始文本片段（需在文件中唯一匹配）")
    new_string: str = Field(default="", description="替换后的文本片段")


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------

@register
class FileReadSkill(Skill):
    """读取工作区内文件内容，支持分段读取。"""

    node_type = "file"
    name = "file_read"
    description = "读取文件：读取工作区内文件内容。输入 path（和可选 offset/limit），返回文件内容"
    input_schema = FileReadInput
    required_permissions = [Permission.FILE_READ]

    async def execute(self, inputs: dict[str, Any]) -> dict[str, Any]:
        in_ = FileReadInput.model_validate(inputs)
        return _core_read(in_.path, in_.offset, in_.limit)


@register
class FileWriteSkill(Skill):
    """原子写入文件到工作区。"""

    node_type = "file"
    name = "file_write"
    description = "写入文件：将内容写入工作区内文件（原子写入）。输入 path 和 content，返回写入结果"
    input_schema = FileWriteInput
    required_permissions = [Permission.FILE_WRITE]

    async def execute(self, inputs: dict[str, Any]) -> dict[str, Any]:
        in_ = FileWriteInput.model_validate(inputs)
        return _core_write(in_.path, in_.content)


@register
class FileEditSkill(Skill):
    """精确替换文件中唯一匹配的文本片段。"""

    node_type = "file"
    name = "file_edit"
    description = (
        "编辑文件：精确替换文件中的文本片段。"
        "输入 path、old_string、new_string，返回替换结果"
    )
    input_schema = FileEditInput
    required_permissions = [Permission.FILE_WRITE]

    async def execute(self, inputs: dict[str, Any]) -> dict[str, Any]:
        in_ = FileEditInput.model_validate(inputs)
        return _core_edit(in_.path, in_.old_string, in_.new_string)