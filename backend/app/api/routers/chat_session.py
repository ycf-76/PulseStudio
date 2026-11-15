"""Chat Session / Message API。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import get_current_user
from app.api.schemas.common import StandardResponse
from app.db.models import ChatSession
from app.services import chat_session

router = APIRouter(prefix="/api/chat/sessions", tags=["chat_session"])


class CreateSessionRequest(BaseModel):
    title: str = "新会话"
    work_id: str | None = None
    folder_id: str | None = None


class AddMessageRequest(BaseModel):
    role: str = Field(..., pattern="^(user|assistant|system)$")
    content: str = ""
    agent_meta: dict | None = None


class UpdateSessionRequest(BaseModel):
    title: str | None = None


async def _require_owned_session(session_id: str, user_id: str) -> None:
    """会话归属闸门：不存在或不属于当前用户一律 404。

    此前 messages/update/delete 仅按 session_id 查询，未校验归属，
    任意登录用户可跨账号读写他人会话（IDOR，E2E 实测复现）。
    统一用 404 而非 403，避免泄露会话是否存在。
    """
    session = await chat_session.get_session(session_id)
    if session is None or session.get("user_id") != user_id:
        raise HTTPException(status_code=404, detail="会话不存在")


@router.post("")
async def create_session(
    payload: CreateSessionRequest,
    user_id: str = Depends(get_current_user),
) -> StandardResponse[dict]:
    data = await chat_session.create_session(user_id, payload.title, work_id=payload.work_id, folder_id=payload.folder_id)
    return StandardResponse(data=data)


@router.get("")
async def list_sessions(
    work_id: str | None = None,
    user_id: str = Depends(get_current_user),
) -> StandardResponse[list]:
    data = await chat_session.list_sessions(user_id, work_id=work_id)
    return StandardResponse(data=data)


@router.patch("/{session_id}")
async def update_session(
    session_id: str,
    payload: UpdateSessionRequest,
    user_id: str = Depends(get_current_user),
) -> StandardResponse[dict]:
    await _require_owned_session(session_id, user_id)
    data = await chat_session.update_session(session_id, title=payload.title)
    if data is None:
        return StandardResponse(data={"updated": False})
    return StandardResponse(data=data)


@router.get("/{session_id}/messages")
async def list_messages(
    session_id: str,
    user_id: str = Depends(get_current_user),
) -> StandardResponse[list]:
    await _require_owned_session(session_id, user_id)
    data = await chat_session.list_messages(session_id)
    return StandardResponse(data=data)


@router.post("/{session_id}/messages")
async def add_message(
    session_id: str,
    payload: AddMessageRequest,
    user_id: str = Depends(get_current_user),
) -> StandardResponse[dict]:
    # 已归档侧会话拒绝新消息（PRD FR-C：archived 后只读）
    # 经服务模块属性访问 AsyncSessionLocal，保证测试 monkeypatch 生效
    async with chat_session.AsyncSessionLocal() as db:
        target = await db.get(ChatSession, session_id)
    if target is not None and target.kind == "side":
        status = (target.side_meta or {}).get("status", "active")
        if status == "archived":
            raise HTTPException(status_code=403, detail="侧会话已归档，不能继续对话")
    data = await chat_session.add_message(
        session_id,
        payload.role,
        payload.content,
        payload.agent_meta,
        owner_id=user_id,
    )
    if data is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return StandardResponse(data=data)


@router.delete("/{session_id}")
async def delete_session(
    session_id: str,
    user_id: str = Depends(get_current_user),
) -> StandardResponse[dict]:
    await _require_owned_session(session_id, user_id)
    data = await chat_session.delete_session(session_id)
    return StandardResponse(data=data)