"""MCP 桥接 API —— 后端与浏览器扩展之间的请求中转入口。

三个端点对应 frontend/src/views/McpBridgeView.vue 的调用：
- POST /api/mcp/bridge/register   桥接页面上线/心跳（前端每 30s 一次）
- GET  /api/mcp/bridge/stream     SSE 长连接，后端把 request 事件推给前端
- POST /api/mcp/bridge/result/{call_id}  前端把扩展执行结果回传

鉴权说明：浏览器 EventSource 无法携带 Authorization 头，且桥接页面是用户
本机自建的通道，因此这三个端点不要求 JWT；它们只在本进程内维护连接状态，
不读写任何用户数据。真正的业务动作仍由业务端点（需 JWT）发起。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.services.mcp_bridge import mcp_bridge

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/mcp/bridge", tags=["mcp-bridge"])


class RegisterRequest(BaseModel):
    page: str = Field(default="", description="桥接页面标识，仅用于日志排查")


@router.post("/register")
async def register_bridge(body: RegisterRequest | None = None) -> dict[str, Any]:
    """桥接页面注册/心跳，返回当前在线连接数与待回传调用数。"""
    page = body.page if body else ""
    return mcp_bridge.register(page)


@router.get("/stream")
async def bridge_stream(request: Request) -> StreamingResponse:
    """SSE 事件流：推送 request 事件给桥接页面，空闲时发心跳保活。"""

    async def event_stream():
        async for chunk in mcp_bridge.stream():
            if await request.is_disconnected():
                break
            yield chunk

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/result/{call_id}")
async def post_result(call_id: str, result: dict[str, Any]) -> dict[str, Any]:
    """回传扩展执行结果；call_id 不存在（已超时或重复回传）返回 404。"""
    if not mcp_bridge.resolve(call_id, result):
        raise HTTPException(404, f"call_id 不存在或已超时: {call_id}")
    return {"ok": True, "call_id": call_id}
