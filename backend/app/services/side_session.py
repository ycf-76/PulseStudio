"""侧会话（Side Session）服务：快照构建、生命周期、过期轮次计算。

设计依据：PRD-主任务与侧会话-Side-Session.md v0.3 §12 决策记录。
- 继承粒度三档 full/recent_n/digest，默认 recent_n，N=10（可调 5–50）
- 并行侧会话上限 5（活跃状态计）
- 快照过期：基准点后新增 ≥5 个用户轮次
- 快照只含创建时刻已落库消息（list_messages 天然满足，FR-A8）
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from app.db.models import ChatMessage, ChatSession, SideSnapshot, generate_ulid
from app.db.session import AsyncSessionLocal
from app.services import chat_session

VALID_GRANULARITIES = ("full", "recent_n", "digest")
DEFAULT_RECENT_N = 10
MIN_RECENT_N = 5
MAX_RECENT_N = 50
EXPIRED_TURN_THRESHOLD = 5
MAX_SIDE_SESSIONS = 5
COST_HINT_CHARS = 32_000 * 2  # 32k tokens 的字符近似（1 token ≈ 2 chars），可配置常量
_MID_LEVEL_CHARS = 6_000
_DIGEST_GOAL_MAX_CHARS = 2_000


def build_snapshot_content(
    granularity: str,
    messages: list[dict[str, Any]],
    summary: str,
    recent_n: int = DEFAULT_RECENT_N,
) -> dict[str, Any]:
    """按继承粒度构建快照内容，返回 {"goal", "summary", "messages"}。

    full -> 全量；recent_n -> 尾部 recent_n 条；digest -> goal=首条 user 消息、messages 空。
    messages 按 role ∈ {user, assistant} 过滤（system/summary 消息不进快照）。
    """
    chat_messages = [
        {"role": m["role"], "content": m["content"]}
        for m in messages
        if m.get("role") in ("user", "assistant")
    ]
    if granularity == "digest":
        goal = next(
            (m["content"] for m in chat_messages if m["role"] == "user"), ""
        )[:_DIGEST_GOAL_MAX_CHARS]
        return {"goal": goal, "summary": summary or "", "messages": []}
    if granularity == "recent_n":
        chat_messages = chat_messages[-recent_n:]
    goal = next((m["content"] for m in chat_messages if m["role"] == "user"), "")
    return {"goal": goal[:_DIGEST_GOAL_MAX_CHARS], "summary": summary or "", "messages": chat_messages}


def token_level_of(content: dict[str, Any]) -> str:
    """快照字符总量 -> low/mid/high（供 FR-F2 相对量级提示）。"""
    total = len(content.get("goal") or "") + len(content.get("summary") or "")
    for m in content.get("messages") or []:
        total += len(m.get("content") or "")
    if total > COST_HINT_CHARS:
        return "high"
    if total > _MID_LEVEL_CHARS:
        return "mid"
    return "low"


def _side_meta_default() -> dict[str, Any]:
    return {"status": "active", "has_side_effects": False, "pending_write": None}


def _snapshot_to_dict(snap: SideSnapshot) -> dict[str, Any]:
    return {
        "id": snap.id,
        "session_id": snap.session_id,
        "baseline_message_id": snap.baseline_message_id,
        "granularity": snap.granularity,
        "recent_n": snap.recent_n,
        "content": snap.content,
        "token_level": snap.token_level,
    }


async def get_snapshot(side_session_id: str) -> dict[str, Any] | None:
    """读侧会话快照（Task 4 上下文构建用）。"""
    async with AsyncSessionLocal() as db:
        snap = await db.scalar(
            select(SideSnapshot).where(SideSnapshot.session_id == side_session_id)
        )
        return _snapshot_to_dict(snap) if snap else None


async def compute_expired_turns(main_session_id: str, baseline_message_id: str) -> int:
    """主会话中基准点之后新增的用户轮次数（ULID 字符串有序可直接比较）。

    baseline 为 ""（主会话创建时无消息）时返回全部 user 消息数。
    """
    async with AsyncSessionLocal() as db:
        stmt = (
            select(ChatMessage)
            .where(ChatMessage.session_id == main_session_id, ChatMessage.role == "user")
            .order_by(ChatMessage.id)
        )
        result = await db.scalars(stmt)
        messages = result.all()
    if not baseline_message_id:
        return len(messages)
    return sum(1 for m in messages if m.id > baseline_message_id)


async def create_side_session(
    main_session_id: str,
    user_id: str,
    granularity: str,
    recent_n: int = DEFAULT_RECENT_N,
) -> dict[str, Any]:
    """创建侧会话：校验 -> 落库 ChatSession(kind=side) + SideSnapshot。"""
    if granularity not in VALID_GRANULARITIES:
        raise ValueError(f"无效的继承粒度：{granularity}")
    if not (MIN_RECENT_N <= recent_n <= MAX_RECENT_N):
        raise ValueError(f"recent_n 取值范围为 {MIN_RECENT_N}–{MAX_RECENT_N}")

    async with AsyncSessionLocal() as db:
        main = await db.get(ChatSession, main_session_id)
        if main is None:
            raise ValueError("主会话不存在")
        if main.kind != "main":
            raise ValueError("侧会话不能再创建侧会话")
        if main.user_id != user_id:
            raise ValueError("无权在该主会话下创建侧会话")

        existing_sides = (
            await db.scalars(
                select(ChatSession).where(
                    ChatSession.parent_session_id == main_session_id,
                    ChatSession.kind == "side",
                )
            )
        ).all()
        active_count = sum(
            1 for s in existing_sides if (s.side_meta or {}).get("status", "active") == "active"
        )
        if active_count >= MAX_SIDE_SESSIONS:
            raise ValueError(f"活跃侧会话已达上限（{MAX_SIDE_SESSIONS} 个），请先归档或删除")

        messages = await _list_messages_in(db, main_session_id)
        summary = await chat_session.get_session_summary(main_session_id) or ""
        content = build_snapshot_content(granularity, messages, summary, recent_n)
        # 基准轮次 = 快照基准点前的用户轮次数（侧会话 system prompt 展示“基于主任务第 N 轮”）
        content["baseline_turn"] = sum(1 for m in messages if m["role"] == "user")

        now = datetime.now(UTC)
        side = ChatSession(
            id=generate_ulid(),
            user_id=user_id,
            work_id=main.work_id,
            folder_id=main.folder_id,
            title="侧会话",
            kind="side",
            parent_session_id=main_session_id,
            side_meta=_side_meta_default(),
            created_at=now,
            updated_at=now,
        )
        db.add(side)
        await db.flush()

        baseline = messages[-1]["id"] if messages else ""
        snap = SideSnapshot(
            session_id=side.id,
            baseline_message_id=baseline,
            granularity=granularity,
            recent_n=recent_n if granularity == "recent_n" else None,
            content=content,
            token_level=token_level_of(content),
        )
        db.add(snap)
        await db.commit()
        await db.refresh(side)

        return {
            "id": side.id,
            "title": side.title,
            "granularity": granularity,
            "recent_n": recent_n if granularity == "recent_n" else None,
            "baseline_message_id": baseline,
            "baseline_turn": content.get("baseline_turn", 0),
            "token_level": snap.token_level,
            "created_at": side.created_at.isoformat() if side.created_at else None,
        }


async def _list_messages_in(db, session_id: str, limit: int = 200) -> list[dict[str, Any]]:
    from sqlalchemy import desc as _desc

    from app.db.models import ChatMessage as _CM

    # 先倒序取最新 limit 条，再反转为正序——超过 limit 时必须保留**最新**上下文，
    # baseline（快照基准点）也必须落在最新消息上。
    stmt = (
        select(_CM)
        .where(_CM.session_id == session_id)
        .order_by(_desc(_CM.created_at), _desc(_CM.id))
        .limit(limit)
    )
    rows = list((await db.scalars(stmt)).all())
    rows.reverse()
    return [
        {"id": m.id, "role": m.role, "content": m.content}
        for m in rows
    ]


async def list_side_sessions(main_session_id: str) -> list[dict[str, Any]]:
    """列出主会话下全部侧会话，含实时计算的 expired_turns（不入库）。"""
    async with AsyncSessionLocal() as db:
        stmt = (
            select(ChatSession)
            .where(ChatSession.parent_session_id == main_session_id, ChatSession.kind == "side")
            .order_by(ChatSession.created_at)
        )
        sides = (await db.scalars(stmt)).all()
        if not sides:
            return []
        side_ids = [s.id for s in sides]
        snaps = {
            s.session_id: s
            for s in (
                await db.scalars(
                    select(SideSnapshot).where(SideSnapshot.session_id.in_(side_ids))
                )
            ).all()
        }
        out = []
        for side in sides:
            meta = side.side_meta or {}
            snap = snaps.get(side.id)
            baseline = snap.baseline_message_id if snap else ""
            out.append({
                "id": side.id,
                "title": side.title,
                "status": meta.get("status", "active"),
                "has_side_effects": bool(meta.get("has_side_effects")),
                "granularity": snap.granularity if snap else "",
                "baseline_message_id": baseline,
                "baseline_turn": (snap.content or {}).get("baseline_turn", 0),
                "expired_turns": await compute_expired_turns(main_session_id, baseline),
                "token_level": snap.token_level if snap else "low",
            })
        return out


async def get_side_session_row(side_session_id: str) -> dict[str, Any] | None:
    """读侧会话行（含 side_meta 全量，供 409 检查与确认流程）。"""
    async with AsyncSessionLocal() as db:
        side = await db.get(ChatSession, side_session_id)
        if side is None or side.kind != "side":
            return None
        meta = side.side_meta or _side_meta_default()
        return {
            "id": side.id,
            "user_id": side.user_id,
            "title": side.title,
            "status": meta.get("status", "active"),
            "has_side_effects": bool(meta.get("has_side_effects")),
            "pending_write": meta.get("pending_write"),
        }


async def set_pending_write(side_session_id: str, info: dict[str, Any] | None) -> None:
    """记录/清除待确认写操作（Reactor 拦截时写入，确认/拒绝时清除）。"""
    async with AsyncSessionLocal() as db:
        side = await db.get(ChatSession, side_session_id)
        if side is None or side.kind != "side":
            raise ValueError("侧会话不存在")
        meta = dict(side.side_meta or _side_meta_default())
        meta["pending_write"] = info
        side.side_meta = meta
        await db.commit()


async def approve_side_write(side_session_id: str) -> None:
    """批准待确认写操作：清 pending_write，标记 has_side_effects=True。"""
    async with AsyncSessionLocal() as db:
        side = await db.get(ChatSession, side_session_id)
        if side is None or side.kind != "side":
            raise ValueError("侧会话不存在")
        meta = dict(side.side_meta or _side_meta_default())
        meta["pending_write"] = None
        meta["has_side_effects"] = True
        side.side_meta = meta
        await db.commit()


async def deny_side_write(side_session_id: str) -> None:
    """拒绝待确认写操作：只清 pending_write，不中断会话（FR-D5 只读继续）。"""
    async with AsyncSessionLocal() as db:
        side = await db.get(ChatSession, side_session_id)
        if side is None or side.kind != "side":
            raise ValueError("侧会话不存在")
        meta = dict(side.side_meta or _side_meta_default())
        meta["pending_write"] = None
        side.side_meta = meta
        await db.commit()


async def ensure_no_pending_write(side_session_id: str) -> None:
    """侧会话存在未决写确认时拒绝新请求（Review Focus 3 → 409）。"""
    row = await get_side_session_row(side_session_id)
    if row is not None and row.get("pending_write"):
        from fastapi import HTTPException

        raise HTTPException(status_code=409, detail="侧会话有写操作待确认，请先处理确认弹窗")


async def set_side_status(side_session_id: str, status: str) -> None:
    if status not in ("active", "archived"):
        raise ValueError(f"无效状态：{status}")
    async with AsyncSessionLocal() as db:
        side = await db.get(ChatSession, side_session_id)
        if side is None or side.kind != "side":
            raise ValueError("侧会话不存在")
        meta = dict(side.side_meta or _side_meta_default())
        meta["status"] = status
        side.side_meta = meta
        await db.commit()


async def delete_side_session(side_session_id: str) -> None:
    """删除侧会话行（SideSnapshot 级联删除）；不动主会话及其消息。"""
    async with AsyncSessionLocal() as db:
        side = await db.get(ChatSession, side_session_id)
        if side is None or side.kind != "side":
            raise ValueError("侧会话不存在")
        await db.delete(side)
        await db.commit()


# ===== 提炼式回写（PRD FR-B / AC-04）=====

DIGEST_CAP_RATIO = 0.3  # 摘要 ≤ 对应侧会话问答总字符数 × 30%（硬约束）


def check_digest_cap(digest: str, side_messages: list[dict[str, Any]]) -> bool:
    """摘要长度硬约束：≤ 侧会话问答总字符数 × 30%；无问答素材时拒绝。"""
    if not digest or not digest.strip():
        return False
    total = sum(len(m.get("content") or "") for m in side_messages)
    if total <= 0:
        return False
    return len(digest) <= total * DIGEST_CAP_RATIO


def _get_llm(temperature: float = 0.3):
    """摘要草稿用 LLM（独立函数便于测试替换）。"""
    from app.engine.factory import get_deepseek_llm

    return get_deepseek_llm(temperature=temperature)


async def generate_digest_draft(side_session_id: str) -> str:
    """由侧会话问答生成摘要草稿（用户可编辑后再带回）。"""
    async with AsyncSessionLocal() as db:
        messages = await _list_messages_in(db, side_session_id)
    if not messages:
        raise ValueError("侧会话暂无可提炼的问答内容")

    transcript = "\n".join(
        f"{'用户' if m['role'] == 'user' else '助手'}: {m['content']}" for m in messages
    )
    llm = _get_llm(temperature=0.3)
    result = await llm.chat([
        {"role": "system", "content": "你是摘要助手。把给定的侧会话问答提炼为一段带回主任务的结论摘要：只保留结论、决策与关键参数，不要复现问答过程。直接输出摘要正文，不要寒暄。"},
        {"role": "user", "content": f"侧会话问答记录：\n{transcript}"},
    ])
    digest = (result.get("content") or "").strip()
    if not digest:
        raise ValueError("摘要生成失败，请手动填写摘要后带回")
    return digest


async def bring_back_to_main(
    side_session_id: str, digest: str, side_title: str = "",
) -> dict[str, Any]:
    """把用户确认的摘要作为特殊消息注入主会话（多条注入互不覆盖）。

    特殊消息：role="user" + agent_meta.message_type="side_session_digest"。
    返回 {"main_session_id", "main_message_id"}。
    """
    if not digest or not digest.strip():
        raise ValueError("带回内容不能为空")
    async with AsyncSessionLocal() as db:
        side = await db.get(ChatSession, side_session_id)
        if side is None or side.kind != "side":
            raise ValueError("侧会话不存在")
        main_session_id = side.parent_session_id
        if not main_session_id:
            raise ValueError("侧会话缺少主会话归属")

        messages = await _list_messages_in(db, side_session_id)
        if not check_digest_cap(digest, messages):
            raise ValueError(
                "摘要超出长度上限（不超过侧会话问答总字符数的 30%），请精简后再带回"
            )

    result = await chat_session.add_message(
        main_session_id,
        "user",
        digest.strip(),
        {
            "message_type": "side_session_digest",
            "side_session_id": side_session_id,
            "side_session_title": side_title or side.title or "侧会话",
        },
    )
    if not isinstance(result, dict) or not result.get("id"):
        raise ValueError("主会话不存在或已被删除，无法带回结论")
    return {
        "main_session_id": main_session_id,
        "main_message_id": result["id"],
    }


async def finalize_main_session(
    main_session_id: str, decisions: dict[str, str], user_id: str | None = None
) -> dict[str, Any]:
    """主任务收尾：按 decisions 处理各侧会话（keep->archived / discard->delete）。

    未出现在 decisions 中的侧会话保持原状。
    decisions 里的 side_id 必须属于该主会话（且 user_id 归属一致），
    否则跳过不执行（防跨用户/跨会话越权操纵他人侧会话）。
    """
    results: dict[str, str] = {}
    for side_id, decision in decisions.items():
        async with AsyncSessionLocal() as db:
            side = await db.get(ChatSession, side_id)
        if side is None or side.kind != "side" or side.parent_session_id != main_session_id:
            results[side_id] = "skipped"
            continue
        if user_id is not None and side.user_id != user_id:
            results[side_id] = "skipped"
            continue
        if decision == "keep":
            await set_side_status(side_id, "archived")
            results[side_id] = "archived"
        elif decision == "discard":
            await delete_side_session(side_id)
            results[side_id] = "deleted"
        else:
            raise ValueError(f"无效决策：{decision}")
    return {"results": results}