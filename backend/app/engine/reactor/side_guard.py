"""侧会话写副作用判定（纯函数，PRD §8 R5 / Q5）。

规则（数值不可改，来自 PRD v0.3 §12 决策记录）：
- 写副作用权限集合 = FILE_WRITE, BASH_EXEC, NET_HTTP_POST,
  SKILL_IMPORT, SKILL_MANAGE, BROWSER_AUTOMATE；其余为只读白名单。
- 争议项按写处理（无法判定时宁可弹确认也不静默放行）。
- 确认逐次生效，无会话级放行（Q9）。
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from app.engine.schemas import Permission

WRITE_PERMISSIONS: frozenset[Permission] = frozenset(
    {
        Permission.FILE_WRITE,
        Permission.BASH_EXEC,
        Permission.NET_HTTP_POST,
        Permission.SKILL_IMPORT,
        Permission.SKILL_MANAGE,
        Permission.BROWSER_AUTOMATE,
        Permission.WORK_WRITE,
    }
)

_READONLY_PERMISSIONS: frozenset[Permission] = frozenset(
    {
        Permission.FILE_READ,
        Permission.NET_HTTP_GET,
        Permission.XHS_SEARCH,
    }
)

_PERM_CN = {
    Permission.FILE_WRITE: "写文件",
    Permission.BASH_EXEC: "执行命令",
    Permission.NET_HTTP_POST: "网络提交",
    Permission.SKILL_IMPORT: "导入技能",
    Permission.SKILL_MANAGE: "管理技能",
    Permission.BROWSER_AUTOMATE: "浏览器自动化",
    Permission.WORK_WRITE: "修改作品",
}


def write_permissions_of(
    required_permissions: Iterable[Permission],
    declared_skill_permissions: Iterable[Permission] | None = (),
) -> set[Permission]:
    """调用所需权限与写副作用集合的交集。

    declared_skill_permissions=None 表示技能权限不明（争议项）——返回非空集合以强制走确认。
    """
    required = set(required_permissions or ())
    if declared_skill_permissions is None:
        return required & WRITE_PERMISSIONS or {Permission.FILE_WRITE}
    declared = set(declared_skill_permissions or ())
    return (required | declared) & WRITE_PERMISSIONS


def is_write_call(
    required_permissions: Iterable[Permission],
    declared_skill_permissions: Iterable[Permission] | None = (),
) -> bool:
    """该调用是否含写副作用。争议项（技能权限不明）按写处理。"""
    return bool(write_permissions_of(required_permissions, declared_skill_permissions))


def describe_write_request(
    tool_name: str,
    arguments: dict[str, Any],
    write_perms: set[Permission],
) -> str:
    """生成给用户确认弹窗看的一句话描述。"""
    perm_labels = "、".join(
        _PERM_CN.get(p, p.value) for p in sorted(write_perms, key=lambda x: x.value)
    )
    args_preview = json.dumps(arguments, ensure_ascii=False, default=str)
    if len(args_preview) > 200:
        args_preview = args_preview[:200] + "…"
    return f"侧会话请求{perm_labels}（工具 {tool_name}，参数 {args_preview}）"


def call_signature(tool_name: str, arguments: dict[str, Any]) -> str:
    """调用签名（逐次放行时用于精确匹配一次调用）。"""
    return f"{tool_name}|{json.dumps(arguments, sort_keys=True, default=str)}"


def declared_permissions_for_skill(skill_name: str) -> tuple[Permission, ...] | None:
    """技能 frontmatter 声明的权限；无法判定时返回 None（争议项按写处理）。"""
    try:
        from app.core.skill_registry import get_skill_class_by_name

        skill_cls = get_skill_class_by_name(skill_name)
        if skill_cls is None:
            return None
        declared = tuple(getattr(skill_cls, "skill_permissions", ()) or ())
        return tuple(Permission(p) for p in declared)
    except Exception:
        return None
