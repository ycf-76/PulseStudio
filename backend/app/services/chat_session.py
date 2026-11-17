"""Chat Session / Message 持久化服务。"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ChatMessage, ChatSession, User
from app.db.session import AsyncSessionLocal

logger = logging.getLogger(__name__)


def _session_to_dict(session: ChatSession) -> dict[str, Any]:
    return {
        "id": session.id,
        "user_id": session.user_id,
        "work_id": session.work_id,
        "folder_id": session.folder_id or "",
        "title": session.title,
        "kind": session.kind or "main",
        "created_at": session.created_at.isoformat() if session.created_at else None,
        "updated_at": session.updated_at.isoformat() if session.updated_at else None,
    }


def _message_to_dict(message: ChatMessage) -> dict[str, Any]:
    return {
        "id": message.id,
        "session_id": message.session_id,
        "role": message.role,
        "content": message.content,
        "agent_meta": message.agent_meta,
        "created_at": message.created_at.isoformat() if message.created_at else None,
    }


async def get_session(session_id: str) -> dict[str, Any] | None:
    async with AsyncSessionLocal() as db:
        session = await db.get(ChatSession, session_id)
        return _session_to_dict(session) if session else None


async def create_session(
    user_id: str, title: str = "新会话", work_id: str | None = None, folder_id: str | None = None
) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        user = await db.get(User, user_id)
        if user is None:
            user = User(id=user_id, nickname=f"user_{user_id[:8]}")
            db.add(user)
            await db.flush()
        session = ChatSession(
            user_id=user_id, title=title or "新会话", work_id=work_id, folder_id=folder_id or ""
        )
        db.add(session)
        await db.commit()
        await db.refresh(session)
        return _session_to_dict(session)


async def ensure_session(
    session_id: str,
    user_id: str,
    title: str = "新会话",
    work_id: str | None = None,
    folder_id: str | None = None,
) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        session = await db.get(ChatSession, session_id)
        if session is None:
            user = await db.get(User, user_id)
            if user is None:
                user = User(id=user_id, nickname=f"user_{user_id[:8]}")
                db.add(user)
                await db.flush()
            session = ChatSession(
                id=session_id,
                user_id=user_id,
                title=title or "新会话",
                work_id=work_id,
                folder_id=folder_id or "",
            )
            db.add(session)
            await db.commit()
            await db.refresh(session)
        return _session_to_dict(session)


async def list_sessions(
    user_id: str, limit: int = 50, work_id: str | None = None
) -> list[dict[str, Any]]:
    async with AsyncSessionLocal() as db:
        stmt = select(ChatSession).where(ChatSession.user_id == user_id)
        if work_id is not None:
            stmt = stmt.where(ChatSession.work_id == work_id)
        stmt = stmt.order_by(desc(ChatSession.updated_at)).limit(limit)
        result = await db.scalars(stmt)
        return [_session_to_dict(s) for s in result.all()]


async def add_message(
    session_id: str,
    role: str,
    content: str,
    agent_meta: dict[str, Any] | None = None,
    owner_id: str | None = None,
) -> dict[str, Any] | None:
    """追加消息。

    owner_id 传入时做归属校验：会话已存在且不属于该用户则返回 None
    （调用方转 404，不泄露会话存在性）。会话不存在时按 owner_id 落库，
    保留原有的"先写消息后补会话"引导路径。

    owner_id 未传入且会话不存在时返回 None：调用方（Agent 回合、后台续跑等）
    都是"会话先存在再写消息"的链路，此时会话缺失只可能是已被删除。
    若在这里兜底成 user_id="system" 的会话，会复活出无人可访问的僵尸会话，
    而同一请求方随后订阅该会话 SSE 会因归属不符被 403 拒绝。
    """
    async with AsyncSessionLocal() as db:
        session = await db.get(ChatSession, session_id)
        if session is None:
            if owner_id is None:
                logger.warning(
                    "[chat_session] add_message skipped: session %s 不存在且未提供 owner_id，"
                    "疑似会话已被删除，拒绝以 system 归属复活",
                    session_id,
                )
                return None
            owner = owner_id
            user = await db.get(User, owner)
            if user is None:
                user = User(id=owner, nickname=f"user_{owner[:8]}")
                db.add(user)
                await db.flush()
            session = ChatSession(id=session_id, user_id=owner, title="新会话")
            db.add(session)
            await db.flush()
        elif owner_id is not None and session.user_id != owner_id:
            return None

        message = ChatMessage(
            session_id=session_id,
            role=role,
            content=content,
            agent_meta=agent_meta,
        )
        db.add(message)

        session.updated_at = datetime.now(UTC)

        await db.commit()
        await db.refresh(message)
        return _message_to_dict(message)


async def mark_clarification_resolved(session_id: str) -> int:
    """把该会话中所有 workflow_status=awaiting_clarification 的 assistant 消息标记为 completed。

    用户提交澄清后调用，防止刷新页面时旧澄清卡片重新出现。
    返回受影响行数。
    """
    async with AsyncSessionLocal() as db:
        stmt = (
            select(ChatMessage)
            .where(
                ChatMessage.session_id == session_id,
                ChatMessage.role == "assistant",
            )
            .order_by(ChatMessage.created_at)
        )
        result = await db.scalars(stmt)
        count = 0
        for msg in result.all():
            meta = msg.agent_meta
            if isinstance(meta, dict) and meta.get("workflow_status") == "awaiting_clarification":
                meta["workflow_status"] = "completed"
                msg.agent_meta = meta
                count += 1
        if count:
            await db.commit()
        return count


async def mark_cut_plan_resolved(
    session_id: str,
    resolved: str,
    error: str | None = None,
    version: int | None = None,
    asset_url: str | None = None,
    db: AsyncSession | None = None,
) -> int:
    """把该会话最新一条口播叠加方案消息标记为已决策（confirmed/cancelled/failed）。

    用户确认/取消叠加方案后调用。此前 resolved 标记只存在于前端内存
    （SSE 回写 agentMeta），刷新/重进会话后从 DB 重建就丢了，导致
    awaiting_cut_plan_confirmation 消息永远渲染成"待确认"死卡。
    与 mark_clarification_resolved 同一套治本思路：状态以 DB 为准。

    匹配范围：最新一条 workflow_status=awaiting_cut_plan_confirmation
    （真待确认）或 cut_plan_resolved=confirmed 且尚未落 version（渲染中）的
    assistant 消息。返回受影响行数（0=没找到，调用方自行兜底）。

    db：请求路径调用方传入自己的会话；后台任务不传则自开（请求结束后
    请求级会话已关闭）。测试传 MagicMock 时立即抛错被调用方吞掉，零连接开销。
    """
    if db is not None:
        return await _mark_cut_plan_resolved_on(
            db,
            session_id,
            resolved,
            error,
            version,
            asset_url,
        )
    async with AsyncSessionLocal() as own_db:
        return await _mark_cut_plan_resolved_on(
            own_db,
            session_id,
            resolved,
            error,
            version,
            asset_url,
        )


async def _mark_cut_plan_resolved_on(
    db: AsyncSession,
    session_id: str,
    resolved: str,
    error: str | None,
    version: int | None,
    asset_url: str | None,
) -> int:
    stmt = (
        select(ChatMessage)
        .where(
            ChatMessage.session_id == session_id,
            ChatMessage.role == "assistant",
        )
        .order_by(desc(ChatMessage.created_at))
    )
    result = await db.scalars(stmt)
    for msg in result.all():
        meta = msg.agent_meta
        if not isinstance(meta, dict):
            continue
        ws = meta.get("workflow_status")
        cr = meta.get("cut_plan_resolved")
        is_pending = ws == "awaiting_cut_plan_confirmation"
        is_rendering = cr == "confirmed" and not (meta.get("cut_plan_versions"))
        if not (is_pending or is_rendering):
            continue
        meta["cut_plan_resolved"] = resolved
        if error:
            meta["cut_plan_error"] = error
        else:
            meta.pop("cut_plan_error", None)
        if version is not None:
            versions = meta.get("cut_plan_versions")
            if not isinstance(versions, list):
                versions = []
            versions.append({"version": version, "asset_url": asset_url})
            meta["cut_plan_versions"] = versions
        meta["workflow_status"] = "completed"
        # JSON 列原地改不一定被 SQLAlchemy 追踪，必须重新赋值
        msg.agent_meta = dict(meta)
        await db.commit()
        return 1
    return 0


async def mark_awaiting_status_resolved(
    session_id: str,
    awaiting_status: str,
    resolved_key: str,
    resolved_value: str,
    error_key: str | None = None,
    error: str | None = None,
    pending_values: set[str] | None = None,
    db: AsyncSession | None = None,
) -> int:
    """把该会话最新一条 workflow_status=awaiting_status 的 assistant 消息标记为已决策。

    mark_cut_plan_resolved 的同类推广（2026-11-03 死卡修复）：resolved 标记
    此前只活在前端内存（SSE 回写 agentMeta），刷新/重进会话即丢，awaiting
    消息永远渲染成可交互的死卡。落库后前端经 normalizeAgentMeta 映射回
    storyboardResolved / guardianResolved / confirmationResolved 判稳。

    pending_values：已标记为这些 resolved 值的消息也算命中（用于
    「接受时先标 confirmed，渲染完成后回写最终态」的两段式回写——
    后台任务要能翻找刚标过 confirmed 的那条）。

    db：请求路径调用方传入自己的会话（复用连接、事务随请求走）；
    后台任务不传则自开。测试传 MagicMock 时在 `await db.scalars` 处
    立即抛 TypeError，被调用方 except 吞掉——零真实连接开销。
    返回受影响行数（0=没找到）。
    """
    if db is not None:
        return await _mark_awaiting_status_resolved_on(
            db,
            session_id,
            awaiting_status,
            resolved_key,
            resolved_value,
            error_key,
            error,
            pending_values,
        )
    async with AsyncSessionLocal() as own_db:
        return await _mark_awaiting_status_resolved_on(
            own_db,
            session_id,
            awaiting_status,
            resolved_key,
            resolved_value,
            error_key,
            error,
            pending_values,
        )


async def _mark_awaiting_status_resolved_on(
    db: AsyncSession,
    session_id: str,
    awaiting_status: str,
    resolved_key: str,
    resolved_value: str,
    error_key: str | None,
    error: str | None,
    pending_values: set[str] | None,
) -> int:
    stmt = (
        select(ChatMessage)
        .where(
            ChatMessage.session_id == session_id,
            ChatMessage.role == "assistant",
        )
        .order_by(desc(ChatMessage.created_at))
    )
    result = await db.scalars(stmt)
    for msg in result.all():
        meta = msg.agent_meta
        if not isinstance(meta, dict):
            continue
        is_pending = meta.get("workflow_status") == awaiting_status
        is_in_flight = bool(pending_values) and meta.get(resolved_key) in pending_values
        if not (is_pending or is_in_flight):
            continue
        meta[resolved_key] = resolved_value
        if error_key:
            if error:
                meta[error_key] = error
            else:
                meta.pop(error_key, None)
        meta["workflow_status"] = "completed"
        # JSON 列原地改不一定被 SQLAlchemy 追踪，必须整体重新赋值
        msg.agent_meta = dict(meta)
        await db.commit()
        return 1
    return 0


async def list_messages(session_id: str, limit: int = 200) -> list[dict[str, Any]]:
    async with AsyncSessionLocal() as db:
        stmt = (
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.created_at)
            .limit(limit)
        )
        result = await db.scalars(stmt)
        return [_message_to_dict(m) for m in result.all()]


async def get_last_session_id(session_id: str) -> str | None:
    """从上一条 assistant 消息的 agent_meta.sessionId 取，供多轮追问注入上游输出。"""
    async with AsyncSessionLocal() as db:
        stmt = (
            select(ChatMessage)
            .where(
                ChatMessage.session_id == session_id,
                ChatMessage.role == "assistant",
            )
            .order_by(desc(ChatMessage.created_at))
            .limit(1)
        )
        message = await db.scalar(stmt)
        if message and message.agent_meta:
            return message.agent_meta.get("sessionId")
        return None


async def get_recent_messages(
    session_id: str,
    limit: int = 6,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """取最近 N 条消息（倒序取+反转），支持 offset 跳过最新消息。

    用于 _agentic_loop 的会话历史注入。offset=1 时跳过最新一条
    （避免重复注入当前消息——路由层在调用 process 之前已写入）。
    """
    async with AsyncSessionLocal() as db:
        stmt = (
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(desc(ChatMessage.created_at))
            .limit(limit)
            .offset(offset)
        )
        result = await db.scalars(stmt)
        messages = [_message_to_dict(m) for m in result.all()]
        messages.reverse()
        return messages


async def update_session(session_id: str, title: str | None = None) -> dict[str, Any] | None:
    async with AsyncSessionLocal() as db:
        session = await db.get(ChatSession, session_id)
        if session is None:
            return None
        if title is not None:
            session.title = title
        session.updated_at = datetime.now(UTC)
        await db.commit()
        await db.refresh(session)
        return _session_to_dict(session)


async def get_creative_state(session_id: str) -> dict[str, Any] | None:
    async with AsyncSessionLocal() as db:
        session = await db.get(ChatSession, session_id)
        if session is None:
            return None
        return session.creative_state


async def update_creative_state(session_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
    async with AsyncSessionLocal() as db:
        session = await db.get(ChatSession, session_id)
        if session is None:
            return None
        current = session.creative_state or {}
        current.update(updates)
        session.creative_state = current
        session.updated_at = datetime.now(UTC)
        await db.commit()
        await db.refresh(session)
        return session.creative_state


async def delete_session(session_id: str) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        session = await db.get(ChatSession, session_id)
        if session is None:
            return {"deleted": False}
        await db.delete(session)
        await db.commit()
        return {"deleted": True, "id": session_id}


# ═══════════════════════════════════════════════════════════════
# 会话摘要：阈值触发 LLM 摘要，避免长对话上下文断裂
# ═══════════════════════════════════════════════════════════════

_SUMMARY_ROLE = "system"
_SUMMARY_META_KEY = "conversation_summary"


async def count_messages(session_id: str) -> int:
    """统计会话消息总数（不含 system/summary 消息）。"""
    async with AsyncSessionLocal() as db:
        stmt = select(ChatMessage).where(
            ChatMessage.session_id == session_id,
            ChatMessage.role.in_(["user", "assistant"]),
        )
        result = await db.scalars(stmt)
        return len(result.all())


async def get_session_summary(session_id: str) -> str | None:
    """取会话的缓存摘要（如果有的话）。"""
    async with AsyncSessionLocal() as db:
        stmt = (
            select(ChatMessage)
            .where(
                ChatMessage.session_id == session_id,
                ChatMessage.role == _SUMMARY_ROLE,
            )
            .order_by(desc(ChatMessage.created_at))
            .limit(1)
        )
        msg = await db.scalar(stmt)
        if msg and msg.agent_meta and msg.agent_meta.get(_SUMMARY_META_KEY):
            return msg.content
    return None


async def get_session_summary_meta(session_id: str) -> dict | None:
    """取会话摘要的元数据（包含 summarized_count）。"""
    async with AsyncSessionLocal() as db:
        stmt = (
            select(ChatMessage)
            .where(
                ChatMessage.session_id == session_id,
                ChatMessage.role == _SUMMARY_ROLE,
                ChatMessage.agent_meta[_SUMMARY_META_KEY].as_boolean().is_(True),
            )
            .order_by(desc(ChatMessage.created_at))
            .limit(1)
        )
        msg = await db.scalar(stmt)
        if msg and msg.agent_meta:
            return msg.agent_meta
    return None


async def save_session_summary(
    session_id: str,
    summary_text: str,
    summarized_count: int,
) -> None:
    """保存/更新会话摘要。summarized_count 记录本次摘要覆盖了多少条消息。"""
    async with AsyncSessionLocal() as db:
        existing_stmt = (
            select(ChatMessage)
            .where(
                ChatMessage.session_id == session_id,
                ChatMessage.role == _SUMMARY_ROLE,
                ChatMessage.agent_meta[_SUMMARY_META_KEY].as_boolean().is_(True),
            )
            .order_by(desc(ChatMessage.created_at))
            .limit(1)
        )
        existing = await db.scalar(existing_stmt)

        if existing:
            existing.content = summary_text
            existing.agent_meta = {
                _SUMMARY_META_KEY: True,
                "summarized_count": summarized_count,
                "updated_at": datetime.now(UTC).isoformat(),
            }
        else:
            msg = ChatMessage(
                session_id=session_id,
                role=_SUMMARY_ROLE,
                content=summary_text,
                agent_meta={
                    _SUMMARY_META_KEY: True,
                    "summarized_count": summarized_count,
                },
            )
            db.add(msg)

        session = await db.get(ChatSession, session_id)
        if session:
            session.updated_at = datetime.now(UTC)

        await db.commit()


async def get_messages_with_summary(
    session_id: str,
    recent_limit: int = 10,
    offset: int = 1,
) -> dict[str, Any]:
    """取会话历史，带摘要支持。

    返回:
        {
            "summary": str | None,   # 旧消息的摘要（如果有）
            "recent": list[dict],    # 最近 N 条完整消息
            "total_count": int,      # 会话总消息数
            "needs_summary": bool,   # 是否需要生成/更新摘要
        }
    """
    total = await count_messages(session_id)
    recent = await get_recent_messages(session_id, limit=recent_limit, offset=offset)
    summary = await get_session_summary(session_id)

    return {
        "summary": summary,
        "recent": recent,
        "total_count": total,
        "needs_summary": False,
    }
