"""侧会话（Side Session）REST API。

主会话维度：创建/列举/收尾（finalize）；
侧会话维度：改名/改状态/删除。
所有端点校验 current_user == 会话所有者，否则 403。
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from app.api.deps import get_current_user
from app.api.schemas.common import StandardResponse
from app.db.models import ChatSession
from app.services import side_session

router = APIRouter(tags=["side_session"])

main_router = APIRouter(prefix="/api/chat/sessions", tags=["side_session"])
side_router = APIRouter(prefix="/api/side-sessions", tags=["side_session"])


class CreateSideSessionRequest(BaseModel):
    granularity: str = Field(..., pattern="^(full|recent_n|digest)$")
    recent_n: int | None = None


class UpdateSideSessionRequest(BaseModel):
    title: str | None = None
    status: str | None = None


class FinalizeRequest(BaseModel):
    decisions: dict[str, str]


async def _get_owned_side(side_session_id: str, user_id: str) -> ChatSession:
    # 经服务模块属性访问 AsyncSessionLocal，保证测试 monkeypatch 生效
    async with side_session.AsyncSessionLocal() as db:
        side = await db.get(ChatSession, side_session_id)
    if side is None or side.kind != "side":
        raise HTTPException(status_code=404, detail="侧会话不存在")
    if side.user_id != user_id:
        raise HTTPException(status_code=403, detail="无权访问该侧会话")
    return side


async def _get_owned_main(main_session_id: str, user_id: str) -> ChatSession:
    async with side_session.AsyncSessionLocal() as db:
        main = await db.get(ChatSession, main_session_id)
    if main is None:
        raise HTTPException(status_code=404, detail="主会话不存在")
    if main.user_id != user_id:
        raise HTTPException(status_code=403, detail="无权访问该主会话")
    return main


@main_router.post("/{main_session_id}/side-sessions", status_code=201)
async def create_side_session(
    main_session_id: str,
    payload: CreateSideSessionRequest,
    user_id: str = Depends(get_current_user),
) -> StandardResponse[dict]:
    main = await _get_owned_main(main_session_id, user_id)
    if main.kind != "main":
        raise HTTPException(status_code=400, detail="侧会话不能再创建侧会话")
    try:
        data = await side_session.create_side_session(
            main_session_id,
            user_id,
            payload.granularity,
            payload.recent_n if payload.recent_n is not None else side_session.DEFAULT_RECENT_N,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return StandardResponse(data=data)


@main_router.get("/{main_session_id}/side-sessions")
async def list_side_sessions(
    main_session_id: str,
    user_id: str = Depends(get_current_user),
) -> StandardResponse[list]:
    await _get_owned_main(main_session_id, user_id)
    data = await side_session.list_side_sessions(main_session_id)
    return StandardResponse(data=data)


@main_router.post("/{main_session_id}/finalize")
async def finalize_main_session(
    main_session_id: str,
    payload: FinalizeRequest,
    user_id: str = Depends(get_current_user),
) -> StandardResponse[dict]:
    await _get_owned_main(main_session_id, user_id)
    try:
        data = await side_session.finalize_main_session(
            main_session_id, payload.decisions, user_id=user_id
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return StandardResponse(data=data)


@side_router.patch("/{side_session_id}")
async def update_side_session(
    side_session_id: str,
    payload: UpdateSideSessionRequest,
    user_id: str = Depends(get_current_user),
) -> StandardResponse[dict]:
    side = await _get_owned_side(side_session_id, user_id)
    if payload.title is not None:
        async with side_session.AsyncSessionLocal() as db:
            db_side = await db.get(ChatSession, side_session_id)
            db_side.title = payload.title
            await db.commit()
        side = db_side
    if payload.status is not None:
        try:
            await side_session.set_side_status(side_session_id, payload.status)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
    meta = side.side_meta or {}
    return StandardResponse(
        data={
            "id": side.id,
            "title": side.title,
            "status": meta.get("status", "active"),
        }
    )


@side_router.delete("/{side_session_id}", status_code=204)
async def delete_side_session(
    side_session_id: str,
    user_id: str = Depends(get_current_user),
) -> Response:
    await _get_owned_side(side_session_id, user_id)
    try:
        await side_session.delete_side_session(side_session_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return Response(status_code=204)


# ---------- 提炼式回写（PRD FR-B）----------

class BringBackRequest(BaseModel):
    digest: str = Field(..., min_length=1)


@side_router.post("/{side_session_id}/bring-back/draft")
async def generate_digest_draft(
    side_session_id: str,
    user_id: str = Depends(get_current_user),
) -> StandardResponse[dict]:
    """生成摘要草稿（用户可编辑后再带回）。"""
    await _get_owned_side(side_session_id, user_id)
    try:
        digest = await side_session.generate_digest_draft(side_session_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=502, detail="摘要生成失败，请稍后重试") from e
    return StandardResponse(data={"digest": digest})


@side_router.post("/{side_session_id}/bring-back")
async def bring_back(
    side_session_id: str,
    payload: BringBackRequest,
    user_id: str = Depends(get_current_user),
) -> StandardResponse[dict]:
    """把确认后的摘要注入主会话（特殊消息）。超长度上限返回 422。"""
    await _get_owned_side(side_session_id, user_id)
    try:
        data = await side_session.bring_back_to_main(side_session_id, payload.digest)
    except ValueError as e:
        msg = str(e)
        status = 422 if "30%" in msg or "长度上限" in msg else 400
        raise HTTPException(status_code=status, detail=msg) from e
    return StandardResponse(data=data)


# ---------- 写确认（逐次生效，PRD Q9）----------

class SideWriteConfirmRequest(BaseModel):
    action: str = Field(..., pattern="^(approve|reject)$")
    loop_state_path: str
    turn_id: str | None = None


# 每个侧会话一把确认锁：并发批准/拒绝时串行化，后到者读到 pending 已清空 → 409，
# 杜绝同一写签名被放行两次（review #2）。
_side_write_locks: dict[str, asyncio.Lock] = {}


def _get_side_write_lock(side_session_id: str) -> asyncio.Lock:
    lock = _side_write_locks.get(side_session_id)
    if lock is None:
        lock = asyncio.Lock()
        _side_write_locks[side_session_id] = lock
    return lock


@main_router.post("/{side_session_id}/side-write-confirm")
async def confirm_side_write(
    side_session_id: str,
    payload: SideWriteConfirmRequest,
    user_id: str = Depends(get_current_user),
) -> StandardResponse[dict]:
    """侧会话写操作确认：批准后逐次放行恢复执行；拒绝则以只读方式继续（FR-D5）。"""
    import asyncio
    import logging

    from app.engine.reactor.loop_state import cleanup_loop_state, load_loop_state
    from app.engine.reactor.side_guard import call_signature

    _logger = logging.getLogger("side_session.confirm")

    async with _get_side_write_lock(side_session_id):
        side = await _get_owned_side(side_session_id, user_id)
        meta = side.side_meta or {}
        pending = meta.get("pending_write")
        if not pending:
            raise HTTPException(status_code=409, detail="该侧会话没有待确认的写操作")

        try:
            state = load_loop_state(payload.loop_state_path)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="循环状态不存在或已过期")

        if payload.action == "approve":
            await side_session.approve_side_write(side_session_id)
            obs = f"[observation] 用户已批准此写操作：{pending.get('description', '')}。请继续执行。"
        else:
            await side_session.deny_side_write(side_session_id)
            obs = (
                f"[observation] 用户拒绝了此写操作：{pending.get('description', '')}。"
                "请以只读方式继续回答用户的问题，不要再尝试该写操作。"
            )
        state["messages"].append({"role": "user", "content": obs})

        _resume_session_id = side_session_id

        async def _run_resume_background():
            """后台续跑侧会话 ReactorSession（批准 = 逐次放行该调用一次）。"""
            from app.agents.registry import AgentRegistry
            from app.api.routers.chat_agent import sse_bus
            from app.core.context_vars import current_db_session as _cdbs
            from app.core.context_vars import current_workspace_root as _cwr
            from app.db.session import AsyncSessionLocal
            from app.services import chat_session as _chat_svc

            registry = AgentRegistry()
            session = registry.build_session("chat_agent", workflow_id=_resume_session_id)
            session.turn_id = payload.turn_id
            session.side_session_id = _resume_session_id
            if payload.action == "approve":
                session._side_write_allow_once = call_signature(
                    pending["tool_name"], pending["arguments"]
                )
            _bg_db = AsyncSessionLocal()
            _token = _cdbs.set(_bg_db)
            _cwd_token = _cwr.set(
                (state.get("context_snapshot", {}) or {}).get("workspace_root") or None
            )
            try:
                _ctx_snapshot = state.get("context_snapshot", {}) or {}
                context = dict(_ctx_snapshot)
                context.pop("extra", None)
                context.setdefault("user_id", user_id)
                context.setdefault("workflow_id", _resume_session_id)
                output = await session.run(
                    "", context=context, seed_messages=state.get("messages") or None
                )
            finally:
                _cdbs.reset(_token)
                _cwr.reset(_cwd_token)
                try:
                    cleanup_loop_state(payload.loop_state_path)
                except Exception:
                    pass

            _ended = output.get("ended_reason", "") if isinstance(output, dict) else ""
            if _ended in ("side_write_paused", "clarification_paused", "guardian_paused",
                          "storyboard_paused", "cut_plan_paused"):
                return  # 再次暂停，等待下一次确认

            output_data = output if isinstance(output, dict) else {}
            final_message = str(output_data.get("summary", "") or "").strip() or "本轮执行结束。"
            await _chat_svc.add_message(
                _resume_session_id, "assistant", final_message,
                {"workflow_status": "completed", "source": "react_loop"},
            )
            try:
                await sse_bus.publish(_resume_session_id, "agent_loop_done", {
                    "status": "completed",
                    "message": final_message,
                    "ended_reason": _ended or "completed",
                }, turn_id=payload.turn_id)
            except Exception:
                pass

        _bg_task = asyncio.get_running_loop().create_task(_run_resume_background())

        def _on_done(t: asyncio.Task) -> None:
            if t.cancelled():
                return
            exc = t.exception()
            if exc is not None:
                _logger.error(f"[side-write-confirm] background resume failed: {exc}", exc_info=exc)

        _bg_task.add_done_callback(_on_done)

    return StandardResponse(data={
        "side_session_id": side_session_id,
        "status": "approved" if payload.action == "approve" else "rejected",
    })


# include 必须在全部端点定义之后，否则后定义的路由不会注册
# （此前 confirm 端点被 include 吞掉，写确认请求 404）。
router.include_router(main_router)
router.include_router(side_router)
