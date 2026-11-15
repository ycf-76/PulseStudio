"""工具传输抽象（二期契约带②）：内核与能力实现之间的唯一执行通道。

- ToolTransport：协议三方法的最小内核侧形态（get_tool / call）。
- InProcessTransport：进程内直调 primitives，行为与一期前的
  `_dispatch` 执行段逐字节等价（timeout / TypeError / 异常 → observation）。
- HttpTransport：三期外置能力服务时落在这里，内核零改动切换。

红线：session 只经 transport 执行工具，不直接触碰 primitive 实现。
"""
from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_SECONDS = 120


@dataclass(frozen=True)
class ToolHandle:
    """工具元数据视图：权限门禁与清单消费，执行走 transport.call。"""

    name: str
    # 保持与 primitive.required_permissions 同构（Permission 枚举集合），
    # 内核门禁只读不解释
    required_permissions: frozenset = field(default=frozenset())
    description: str = ""


class ToolTransport(ABC):
    """能力执行通道抽象。"""

    @abstractmethod
    def get_tool(self, name: str) -> ToolHandle | None:
        """按名取工具元数据；None = 未知工具。"""

    @abstractmethod
    def list_tools(self) -> list[ToolHandle]:
        """全部可用工具清单（tools/list 语义）。"""

    @abstractmethod
    async def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        """执行工具，返回 observation dict。异常一律转 {"ok": False, ...}。"""


class InProcessTransport(ToolTransport):
    """进程内直调：包装 primitives dict，行为与旧 _dispatch 执行段等价。

    等价清单（对照重构前 session.py 执行段）：
    - unknown tool → {"ok": False, "error": ..., "available_tools": [...]}
    - asyncio.wait_for 超时 → {"ok": False, "error": "... timed out ..."}
    - TypeError（参数不匹配）→ {"ok": False, "error": "invalid arguments ..."}
    - 其他异常 → {"ok": False, "error": str(e)}
    - 非 dict 返回值 → {"ok": True, "value": result}
    """

    def __init__(
        self,
        primitives: dict[str, Any],
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._primitives = primitives
        self._timeout = timeout_seconds

    def get_tool(self, name: str) -> ToolHandle | None:
        primitive = self._primitives.get(name)
        if primitive is None:
            return None
        return ToolHandle(
            name=name,
            required_permissions=frozenset(getattr(primitive, "required_permissions", ()) or ()),
            description=getattr(primitive, "description", "") or "",
        )

    def list_tools(self) -> list[ToolHandle]:
        return [self.get_tool(n) for n in self._primitives]  # type: ignore[misc]

    async def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        primitive = self._primitives.get(name)
        if primitive is None:
            return {
                "ok": False,
                "error": f"unknown tool: {name}",
                "available_tools": list(self._primitives.keys()),
            }
        try:
            result = await asyncio.wait_for(
                primitive.execute(**args),
                timeout=self._timeout,
            )
            if not isinstance(result, dict):
                result = {"ok": True, "value": result}
            return result
        except asyncio.TimeoutError:
            logger.error(f"[transport] primitive {name} timed out after {self._timeout}s")
            return {"ok": False, "error": f"tool '{name}' timed out after {self._timeout}s"}
        except TypeError as e:
            return {"ok": False, "error": f"invalid arguments for {name}: {e}"}
        except Exception as e:
            logger.exception(f"[transport] primitive {name} failed")
            return {"ok": False, "error": str(e)}


class HttpTransport(ToolTransport):
    """HTTP 外置能力服务客户端（三期）。

    对端实现工具协议三方法：
      GET  /health              → {"ok": true}
      GET  /tools               → {"tools": [{"name", "description"}, ...]}
      POST /tools/{name}/call   → body {"arguments": {...}}，返回 observation dict

    client 可注入（测试用 httpx.ASGITransport 直连 app，免起端口）；
    生产默认按 base_url 惰性建 AsyncClient。
    """

    def __init__(
        self,
        base_url: str,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        client: Any | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._client = client
        self._tools_cache: list[ToolHandle] | None = None

    def _ensure_client(self):
        if self._client is None:
            import httpx

            self._client = httpx.AsyncClient(
                base_url=self._base_url, timeout=self._timeout
            )
        return self._client

    def get_tool(self, name: str) -> ToolHandle | None:
        if self._tools_cache is None:
            raise RuntimeError(
                f"[transport] remote tool list not loaded; await refresh_tools() first (tool={name})"
            )
        for handle in self._tools_cache:
            if handle.name == name:
                return handle
        return None

    def list_tools(self) -> list[ToolHandle]:
        return list(self._tools_cache or [])

    async def refresh_tools(self) -> list[ToolHandle]:
        client = self._ensure_client()
        resp = await client.get("/tools")
        resp.raise_for_status()
        data = resp.json()
        self._tools_cache = [
            ToolHandle(name=t["name"], description=t.get("description", ""))
            for t in data.get("tools", [])
        ]
        return self._tools_cache

    async def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        client = self._ensure_client()
        try:
            resp = await client.post(
                f"/tools/{name}/call", json={"arguments": args}
            )
            resp.raise_for_status()
            data = resp.json()
            result = data.get("result", data)
            if not isinstance(result, dict):
                result = {"ok": True, "value": result}
            return result
        except Exception as e:
            logger.exception(f"[transport] remote call {name} failed")
            return {"ok": False, "error": f"remote tool '{name}' failed: {e}"}

    async def aclose(self) -> None:
        if self._client is not None and hasattr(self._client, "aclose"):
            await self._client.aclose()


class CompositeTransport(ToolTransport):
    """按工具名路由：注册在 remote_services 里的走对应 HttpTransport，
    其余走进程内。内核持有这一个 transport，切换零改动。"""

    def __init__(
        self,
        in_process: ToolTransport,
        remote_services: dict[str, ToolTransport] | None = None,
    ) -> None:
        self._in_process = in_process
        self._remote = dict(remote_services or {})

    def register_remote(self, prefix_tool_names: set[str], transport: ToolTransport) -> None:
        """把一组工具名绑定到某个远端 transport。"""
        for name in prefix_tool_names:
            self._remote[name] = transport

    def _route(self, name: str) -> ToolTransport:
        return self._remote.get(name, self._in_process)

    def get_tool(self, name: str) -> ToolHandle | None:
        return self._route(name).get_tool(name)

    def list_tools(self) -> list[ToolHandle]:
        handles = list(self._in_process.list_tools())
        seen = {h.name for h in handles}
        for transport in self._remote.values():
            for handle in transport.list_tools():
                if handle.name not in seen:
                    handles.append(handle)
                    seen.add(handle.name)
        return handles

    async def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        return await self._route(name).call(name, args)
