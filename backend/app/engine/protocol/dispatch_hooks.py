"""dispatch 钩子（二期契约带②的注入口）。

内核 `_dispatch` 在两个固定时点跑钩子链：
- pre_clarify：澄清三分层**之前**——用于协议解包（如 skill_call 的嵌套参数），
  只影响澄清判定用的 (target, args)，不改主调用形态；
- pre_execute：guardian/权限**之前**——用于上下文注入等参数准备，
  产出的 (name, args) 直接交给 transport.call。

规则：
- 协议级钩子（SkillCallUnwrapHook）内置在内核，处理的是统一调用协议；
- 业务级钩子（如创作图片上下文注入）放能力侧（app/agents/dispatch_hooks.py），
  由工厂/路由在构造 ReactorSession 时显式注入——内核 grep 不到业务工具名。
- 钩子必须原地修改 args 并返回 (name, args)；禁止抛异常（内核包 try 兜底）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol

logger = logging.getLogger(__name__)


@dataclass
class DispatchContext:
    """单次 dispatch 的上下文快照。"""

    run_context: dict[str, Any]
    workflow_id: str
    turn_id: str | None


class DispatchHook(Protocol):
    async def pre_clarify(
        self, name: str, args: dict[str, Any], ctx: DispatchContext
    ) -> tuple[str, dict[str, Any]]:
        """返回澄清判定用的 (target, args)。默认恒等。"""
        ...

    async def pre_execute(
        self, name: str, args: dict[str, Any], ctx: DispatchContext
    ) -> tuple[str, dict[str, Any]]:
        """返回执行用的 (name, args)。默认恒等。"""
        ...


class _IdentityHook:
    """恒等钩子：保持链上每个位置都可调用。"""

    async def pre_clarify(self, name, args, ctx):
        return name, args

    async def pre_execute(self, name, args, ctx):
        return name, args


class SkillCallUnwrapHook:
    """协议级：skill_call 统一调用形态的解包与上下文透传。

    - pre_clarify：把澄清判定目标从 "skill_call" 解到内层技能名/参数，
      使澄清三分层能按真实技能判定；
    - pre_execute：向内层透传 _user_id / _workflow_id / _turn_id
      （SkillCallPrimitive 执行 skill 需要，SSE 归属也靠它）。
    """

    async def pre_clarify(
        self, name: str, args: dict[str, Any], ctx: DispatchContext
    ) -> tuple[str, dict[str, Any]]:
        if name != "skill_call":
            return name, args
        target = str(args.get("skill") or "")
        nested = args.get("arguments")
        return target, (nested if isinstance(nested, dict) else {})

    async def pre_execute(
        self, name: str, args: dict[str, Any], ctx: DispatchContext
    ) -> tuple[str, dict[str, Any]]:
        if name != "skill_call":
            return name, args
        if not args.get("_user_id"):
            args["_user_id"] = ctx.run_context.get("user_id")
        if not args.get("_workflow_id"):
            args["_workflow_id"] = ctx.workflow_id or ""
        if not args.get("_turn_id"):
            args["_turn_id"] = ctx.turn_id or ""
        return name, args


def unwrap_skill_call(name: str, args: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """skill_call 解包的纯函数形态（业务钩子复用）。"""
    if name != "skill_call":
        return name, args
    inner = str(args.get("skill") or "")
    nested = args.get("arguments")
    return inner, (nested if isinstance(nested, dict) else {})
