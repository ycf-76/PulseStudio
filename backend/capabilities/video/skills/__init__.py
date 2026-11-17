"""视频技能包共享工具 — 仅纯函数，不 import 任何技能模块。

放在包 __init__ 里：各技能模块被 `from capabilities.video.skills.x import ...`
加载时必然先执行本文件，因此这里是唯一不会被循环导入牵连的落点。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def resolve_owner(inputs: dict[str, Any]) -> tuple[str, str]:
    """从技能入参解析产物归属 `(user_id, workflow_id)`。

    reactor 的 skill_call 会注入 `_context`（WorkflowContext，含 user_id /
    workflow_id）以及 `_user_id` / `_workflow_id`；脚本或单测直调时可能两者都
    没有 —— 此时返回空串，调用方回退旧路径，行为与改造前一致。
    """
    ctx = inputs.get("_context")
    user_id = str(getattr(ctx, "user_id", "") or inputs.get("_user_id") or "")
    workflow_id = str(getattr(ctx, "workflow_id", "") or inputs.get("_workflow_id") or "")
    return user_id, workflow_id


def scoped_dir(base: Path, *levels: str) -> Path:
    """按非空层级逐级挂载目录；层级全空时原样返回 base（兼容存量行为）。

    例：scoped_dir(outputs/talking_head_overlay, "u1", "wf-1")
        → outputs/talking_head_overlay/u1/wf-1
    """
    out = base
    for level in levels:
        if level:
            out = out / level
    return out
