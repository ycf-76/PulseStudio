"""Context variables for cross-cutting concerns (db session, etc).

Uses Python ContextVar for coroutine-safe context passing.
Mirrors the pattern used by PermissionGate.current_permissions.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

current_db_session: ContextVar["AsyncSession | None"] = ContextVar(
    "current_db_session", default=None
)

# 会话级工作目录（对标 Codex 的 per-turn <cwd>）：
# 一轮对话开始时由入口按 workspace_id 解析出绝对路径写入，
# 所有文件类 primitive（file_read / file_write / file_edit / glob /
# grep / bash）都以此为根，并对越界路径报错。
# 为空表示"未绑定本地文件夹"，回退到进程默认工作区。
current_workspace_root: ContextVar[str | None] = ContextVar("current_workspace_root", default=None)
