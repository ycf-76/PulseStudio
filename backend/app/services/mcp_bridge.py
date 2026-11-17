"""MCP 桥接总线 —— 后端 ↔ 浏览器扩展的请求中转。

链路：
1. 浏览器端打开 frontend 的 McpBridgeView，SSE 订阅 /api/mcp/bridge/stream；
2. 后端通过 request() 下发 {"type": "request", "call_id", "action", "payload"}；
3. 前端把 request 转发给 Chrome 扩展（chrome.runtime.sendMessage）；
4. 前端把扩展结果 POST 回 /api/mcp/bridge/result/{call_id}；
5. 后端 request() 的 Future 被 resolve，调用方拿到结果。

与 /api/mcp/browser 不同：那边是后端内置 Playwright 直接开浏览器，
这里是「借用用户已登录的 Chrome」执行动作，因此必须有桥接页面在线。
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)


class BridgeOfflineError(RuntimeError):
    """没有浏览器桥接页面在线，无法下发请求。"""


class BridgeCallTimeout(RuntimeError):
    """桥接调用在超时时间内没有回传结果。"""


class McpBridgeHub:
    """桥接总线（进程内单例）。

    单进程内维护：订阅者队列（每个 SSE 连接一个）、待回传的 Future 表、
    最近一次心跳时间。多 worker 部署下请求与回传必须落在同一个 worker。
    """

    def __init__(self) -> None:
        self._subscribers: list[asyncio.Queue] = []
        self._pending: dict[str, asyncio.Future] = {}
        self._page: str = ""
        self._last_register: str | None = None

    @property
    def connected(self) -> int:
        return len(self._subscribers)

    def register(self, page: str = "") -> dict[str, Any]:
        """记录一次心跳（前端每 30s 调一次）。"""
        if page:
            self._page = page
        self._last_register = datetime.now(UTC).isoformat()
        logger.info(
            "[mcp_bridge] register page=%s connected=%d pending=%d",
            page or self._page,
            self.connected,
            len(self._pending),
        )
        return {
            "ok": True,
            "connected": self.connected,
            "page": self._page,
            "pending": len(self._pending),
            "last_register": self._last_register,
        }

    def status(self) -> dict[str, Any]:
        return {
            "connected": self.connected,
            "page": self._page,
            "pending": len(self._pending),
            "last_register": self._last_register,
        }

    async def stream(self) -> AsyncIterator[str]:
        """SSE 事件流：先发 hello，再推送 request 事件，空闲时发心跳。"""
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._subscribers.append(queue)
        logger.info("[mcp_bridge] SSE 订阅建立，当前连接数=%d", self.connected)
        try:
            yield self._format({"type": "hello", "message": "后端桥接已就绪，等待扩展请求"})
            while True:
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=15.0)
                except TimeoutError:
                    yield ": heartbeat\n\n"
                    continue
                yield self._format(msg)
        finally:
            if queue in self._subscribers:
                self._subscribers.remove(queue)
            logger.info("[mcp_bridge] SSE 订阅断开，当前连接数=%d", self.connected)

    def resolve(self, call_id: str, result: dict[str, Any]) -> bool:
        """回填一次桥接调用结果；call_id 未知或已超时返回 False。"""
        future = self._pending.pop(call_id, None)
        if future is None or future.done():
            logger.warning("[mcp_bridge] 回传结果找不到等待中的 call_id=%s", call_id)
            return False
        future.set_result(result)
        return True

    async def request(
        self,
        action: str,
        payload: dict[str, Any] | None = None,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        """下发一次桥接调用并等待扩展回传结果。"""
        if not self._subscribers:
            raise BridgeOfflineError("浏览器桥接页面未连接：请先打开 MCP 桥接页面并点击「连接」")

        call_id = uuid.uuid4().hex
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[call_id] = future

        msg = {
            "type": "request",
            "call_id": call_id,
            "action": action,
            "payload": payload or {},
        }
        try:
            self._subscribers[0].put_nowait(msg)
        except asyncio.QueueFull:
            self._pending.pop(call_id, None)
            self._subscribers.pop(0)
            raise BridgeOfflineError("桥接页面消费不过来（队列已满），请重启桥接页面") from None

        logger.info("[mcp_bridge] 下发请求 action=%s call_id=%s", action, call_id)
        try:
            return await asyncio.wait_for(future, timeout)
        except TimeoutError:
            self._pending.pop(call_id, None)
            logger.warning("[mcp_bridge] 请求超时 action=%s call_id=%s", action, call_id)
            raise BridgeCallTimeout(f"桥接调用 {action} 在 {timeout}s 内没有回传结果") from None

    @staticmethod
    def _format(msg: dict[str, Any]) -> str:
        return "data: " + json.dumps(msg, ensure_ascii=False) + "\n\n"


mcp_bridge = McpBridgeHub()
