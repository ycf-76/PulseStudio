"""内部端点路由（五期-C）：主进程接收能力服务回推的进度事件。

这些端点不对外暴露（无鉴权），仅供同机部署的 capability-service
子进程通过 httpx 回推进度事件。生产部署时靠网络隔离（内网端口）
或防火墙规则保护。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/internal", tags=["internal"])


class ProgressEventRequest(BaseModel):
    """能力服务回推的进度事件，schema 与 sse_bus.publish() 对齐。"""

    workflow_id: str
    event_type: str
    payload: dict[str, Any] = {}
    turn_id: str | None = None


@router.post("/events")
async def receive_capability_event(req: ProgressEventRequest) -> dict[str, Any]:
    """接收能力服务回推的进度事件，转发进 SSE 总线。

    事件 schema 与 sse_bus.publish() 完全对齐，前端零改动。
    """
    from app.services.sse_bus import sse_bus

    event_id = await sse_bus.publish(
        workflow_id=req.workflow_id,
        event_type=req.event_type,
        payload=req.payload,
        turn_id=req.turn_id,
    )
    return {"ok": True, "event_id": event_id}
