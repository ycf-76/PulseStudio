"""SSE streaming router.

P0-1：SSE 鉴权 — JWT 校验 + 会话归属校验。
前端使用 fetch + AbortController（非 EventSource），可携带 Authorization header。
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.session import get_db
from app.services.notification_bus import notification_bus
from app.services.sse_bus import sse_bus

router = APIRouter(prefix="/api/sse", tags=["sse"])


@router.get("/chat/{session_id}")
async def subscribe_chat(
    session_id: str,
    request: Request,
    turn_id: str | None = None,
    user_id: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """Subscribe to chat session events via SSE (Agentic Loop tool calls).

    turn_id query 参数（可选，回合隔离）：传入后只回放/接收该回合的事件，
    防止同一 session 连续多轮对话时上一轮的输出窜进新消息。
    """
    from app.services import chat_session as cs

    existing = await cs.get_session(session_id)
    if not existing or existing["user_id"] != user_id:
        raise HTTPException(status_code=403, detail="无权访问此会话")

    last_event_id = request.headers.get("Last-Event-ID")

    async def event_stream():
        async for event_str in sse_bus.subscribe(session_id, last_event_id, turn_id):
            if await request.is_disconnected():
                break
            yield event_str

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/notifications")
async def subscribe_notifications(
    request: Request,
    user_id: str = Depends(get_current_user),
) -> StreamingResponse:
    """订阅全局通知事件（选题池监控完成等）。

    需要登录（JWT 鉴权），仅接受 Authorization: Bearer <jwt>。
    前端通过 fetch + AbortController 携带 header，不使用 ?token= 查询参数。
    """

    async def event_stream():
        async for event_str in notification_bus.subscribe():
            if await request.is_disconnected():
                break
            yield event_str

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
