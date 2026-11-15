"""侧会话写副作用拦截测试（Task 5）。

覆盖：
1. side_guard 纯函数：写权限集合、判定、描述、签名；
2. ReactorSession 分发前置拦截 → 暂停 + SSE + pending_write 落库；
3. 逐次放行（allow_once）只放行一次；
4. pending_write 服务与 409 检查；
5. /confirm-side-write 批准/拒绝的恢复语义（服务层）。
"""

import json
import os
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite://")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-side-session")

from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import Base, ChatSession, ChatMessage, User
from app.engine.schemas import Permission
from app.engine.reactor.side_guard import (
    WRITE_PERMISSIONS,
    call_signature,
    declared_permissions_for_skill,
    describe_write_request,
    is_write_call,
    write_permissions_of,
)
from app.services import side_session


# ---------- 纯函数 ----------

def test_write_permissions_exact_set():
    assert WRITE_PERMISSIONS == frozenset({
        Permission.FILE_WRITE,
        Permission.BASH_EXEC,
        Permission.NET_HTTP_POST,
        Permission.SKILL_IMPORT,
        Permission.SKILL_MANAGE,
        Permission.BROWSER_AUTOMATE,
        Permission.WORK_WRITE,
    })
    # 只读白名单不含其中任何项
    assert not (WRITE_PERMISSIONS & {Permission.FILE_READ, Permission.NET_HTTP_GET, Permission.XHS_SEARCH})


def test_write_permissions_of_intersects():
    out = write_permissions_of([Permission.FILE_WRITE, Permission.FILE_READ])
    assert out == {Permission.FILE_WRITE}
    assert write_permissions_of([Permission.FILE_READ]) == set()


def test_is_write_call_direct_primitive():
    assert is_write_call([Permission.FILE_WRITE]) is True
    assert is_write_call([Permission.BASH_EXEC]) is True
    assert is_write_call([Permission.FILE_READ]) is False
    assert is_write_call([]) is False


def test_is_write_call_skill_declares_write():
    assert is_write_call([], declared_skill_permissions=[Permission.FILE_WRITE]) is True
    assert is_write_call([], declared_skill_permissions=[Permission.FILE_READ]) is False


def test_is_write_call_undeclared_skill_defaults_read():
    # 争议项按写处理：无法判定时宁可弹确认也不静默放行
    assert is_write_call([], declared_skill_permissions=None) is True


def test_describe_write_request_contains_tool_and_cn():
    desc = describe_write_request(
        "file_write", {"path": "a/b.txt", "content": "x"},
        {Permission.FILE_WRITE},
    )
    assert "file_write" in desc
    assert "写" in desc or "副作用" in desc


def test_call_signature_stable():
    a = call_signature("file_write", {"path": "p", "content": "c"})
    b = call_signature("file_write", {"content": "c", "path": "p"})
    assert a == b


def test_declared_permissions_for_skill_readonly_skill():
    # read_file 等只读技能声明 file:read；未知技能返回 None（争议项按写处理）
    perms = declared_permissions_for_skill("read_file")
    if perms is not None:
        assert Permission.FILE_WRITE not in perms


# ---------- ReactorSession 拦截 ----------

def _llm_script(*steps: dict):
    """stream_chat 跨 step 依序吐 steps（每次调用消耗一个响应）。"""
    responses = [json.dumps(s, ensure_ascii=False) for s in steps]
    state = {"i": 0}

    async def _stream(messages):
        idx = min(state["i"], len(responses) - 1)
        state["i"] += 1
        yield {"content": responses[idx], "is_final": True, "token_usage": 10}

    llm = AsyncMock()
    llm.stream_chat = _stream
    llm.model_name = "mock"
    return llm


class _StubTransport:
    def __init__(self):
        self.calls = []

    def get_tool(self, name):
        from app.engine.reactor.primitives import PRIMITIVES_BY_NAME

        if name not in PRIMITIVES_BY_NAME:
            raise KeyError(name)
        return PRIMITIVES_BY_NAME[name]

    async def call(self, name, args):
        self.calls.append((name, args))
        return {"ok": True, "value": "done"}


def _make_session(llm, side_session_id="side1"):
    from app.engine.reactor.session import ReactorSession

    sse_bus = AsyncMock()
    sse_bus.publish = AsyncMock()
    session = ReactorSession(
        llm=llm,
        allowed_permissions=set(Permission),
        sse_bus=sse_bus,
        workflow_id="wf1",
    )
    session._transport = _StubTransport()
    session._skills = []  # _dispatch 前置的技能归属检查用
    # 隔离 Guardian 风险审查（固定 ALLOW），专注被测的 side guard
    from types import SimpleNamespace

    from app.engine.governance.guardian import GuardianAction

    guardian = AsyncMock()
    guardian.check = AsyncMock(return_value=SimpleNamespace(
        action=GuardianAction.ALLOW, risk_level="low",
        hint=None, user_hint=None, confirmation_prompt=None,
        reason="", to_observation=lambda: "",
    ))
    session._guardian = guardian
    session.side_session_id = side_session_id
    return session


@pytest.mark.asyncio
async def test_reactor_pauses_on_side_write(monkeypatch):
    session = _make_session(_llm_script(
        {"thought": "写文件", "tool_calls": [{"name": "file_write", "arguments": {"path": "a.txt", "content": "hi"}}], "final": False},
        {"thought": "done", "final": True, "output": {"summary": "ok"}},
    ))
    called = {}

    async def fake_set_pending_write(side_id, info):
        called["side_id"] = side_id
        called["info"] = info

    monkeypatch.setattr(side_session, "set_pending_write", fake_set_pending_write)

    result = await session.run("帮我把结果写进文件")

    assert result.get("_awaiting_side_write_confirmation") is True
    assert result.get("ended_reason") == "side_write_paused"
    assert "_loop_state_path" in result
    # 写调用未被执行
    assert session._transport.calls == []
    # pending_write 落库（经服务模块属性，测试可 patch）
    assert called["side_id"] == "side1"
    assert called["info"]["tool_name"] == "file_write"
    # SSE 事件
    events = [c.args[1] for c in session.sse_bus.publish.call_args_list]
    assert "side_write_confirmation_required" in events


@pytest.mark.asyncio
async def test_main_session_not_guarded():
    session = _make_session(_llm_script(
        {"thought": "写文件", "tool_calls": [{"name": "file_write", "arguments": {"path": "a.txt", "content": "hi"}}], "final": False},
        {"thought": "done", "final": True, "output": {"summary": "ok"}},
    ))
    session.side_session_id = None  # 主会话
    result = await session.run("写文件")
    assert result.get("_awaiting_side_write_confirmation") is not True
    assert len(session._transport.calls) == 1


@pytest.mark.asyncio
async def test_allow_once_passes_exactly_once():
    sig = call_signature("file_write", {"path": "a.txt", "content": "hi"})
    session = _make_session(_llm_script(
        {"thought": "写文件", "tool_calls": [{"name": "file_write", "arguments": {"path": "a.txt", "content": "hi"}}], "final": False},
        {"thought": "再写一次", "tool_calls": [{"name": "file_write", "arguments": {"path": "a.txt", "content": "hi"}}], "final": False},
        {"thought": "done", "final": True, "output": {"summary": "ok"}},
    ))
    session._side_write_allow_once = sig
    result = await session.run("写文件")
    # 第一发放行，第二发再次拦截
    assert len(session._transport.calls) == 1
    assert result.get("_awaiting_side_write_confirmation") is True


@pytest.mark.asyncio
async def test_readonly_tool_not_guarded():
    session = _make_session(_llm_script(
        {"thought": "读文件", "tool_calls": [{"name": "file_read", "arguments": {"path": "a.txt"}}], "final": False},
        {"thought": "done", "final": True, "output": {"summary": "ok"}},
    ))
    result = await session.run("读一下文件")
    assert result.get("_awaiting_side_write_confirmation") is not True
    assert len(session._transport.calls) == 1


# ---------- pending_write 服务 ----------

@pytest.fixture()
async def db_env(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    sf = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    async def _setup():
        async with engine.begin() as conn:
            for t in [User.__table__, ChatSession.__table__, ChatMessage.__table__, Base.metadata.tables["side_snapshots"]]:
                await conn.run_sync(t.create, checkfirst=True)
        async with sf() as db:
            db.add(User(id="u1", nickname="t"))
            db.add(ChatSession(id="main1", user_id="u1", kind="main"))
            db.add(ChatSession(
                id="side1", user_id="u1", kind="side", parent_session_id="main1",
                side_meta={"status": "active", "has_side_effects": False, "pending_write": None},
            ))
            await db.commit()

    monkeypatch.setattr(side_session, "AsyncSessionLocal", sf)
    yield _setup
    await engine.dispose()


@pytest.mark.asyncio
async def test_pending_write_roundtrip(db_env):
    await db_env()
    await side_session.set_pending_write("side1", {"tool_name": "file_write", "arguments": {"path": "a"}})
    row = await side_session.get_side_session_row("side1")
    assert row["pending_write"]["tool_name"] == "file_write"
    assert row["has_side_effects"] is False

    await side_session.approve_side_write("side1")
    row = await side_session.get_side_session_row("side1")
    assert row["pending_write"] is None
    assert row["has_side_effects"] is True

    await side_session.set_pending_write("side1", {"tool_name": "bash_exec"})
    await side_session.deny_side_write("side1")
    row = await side_session.get_side_session_row("side1")
    assert row["pending_write"] is None
    assert row["has_side_effects"] is True  # deny 不清历史副作用标记


@pytest.mark.asyncio
async def test_ensure_no_pending_write_raises_409(db_env):
    await db_env()
    # 无 pending_write -> 放行
    await side_session.ensure_no_pending_write("side1")
    # 有 pending_write -> 409
    await side_session.set_pending_write("side1", {"tool_name": "file_write"})
    with pytest.raises(HTTPException) as exc:
        await side_session.ensure_no_pending_write("side1")
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_confirm_side_write_service_flow(db_env):
    """批准/拒绝的服务侧语义（恢复回放在路由层，复用 loop_state）。"""
    await db_env()
    await side_session.set_pending_write("side1", {"tool_name": "file_write", "arguments": {}})
    await side_session.approve_side_write("side1")
    row = await side_session.get_side_session_row("side1")
    assert row["status"] == "active"
    assert row["has_side_effects"] is True