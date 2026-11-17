"""侧会话 REST API 集成测试。

用独立 SQLite 内存引擎 monkeypatch 服务的 AsyncSessionLocal，
httpx.ASGITransport 直挂路由（照 tests/test_card_edit_http.py 模式）。
"""

import os
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite://")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-side-session")

import httpx
import pytest
import pytest_asyncio  # noqa: F401
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.deps import get_current_user
from app.api.routers import chat_session as chat_session_router
from app.db.models import Base, ChatSession, User
from app.services import chat_session, side_session


@pytest.fixture()
def db_env(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    sf = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    async def _setup():
        async with engine.begin() as conn:
            for t in [
                User.__table__,
                ChatSession.__table__,
                Base.metadata.tables["chat_messages"],
                Base.metadata.tables["side_snapshots"],
            ]:
                await conn.run_sync(t.create, checkfirst=True)
        async with sf() as db:
            db.add(User(id="u1", nickname="测试"))
            db.add(ChatSession(id="main1", user_id="u1", title="主会话"))
            await db.commit()

    monkeypatch.setattr(side_session, "AsyncSessionLocal", sf)
    monkeypatch.setattr(chat_session, "AsyncSessionLocal", sf)
    yield _setup, sf


@pytest.fixture()
async def client(db_env):
    _setup, sf = db_env
    await _setup()
    app = FastAPI()
    app.include_router(chat_session_router.router)
    from app.api.routers import side_session as side_session_router

    app.include_router(side_session_router.router)
    app.dependency_overrides[get_current_user] = lambda: "u1"
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


@pytest.mark.asyncio
async def test_create_side_session_returns_201_with_default_recent_n(client):
    resp = await client.post(
        "/api/chat/sessions/main1/side-sessions", json={"granularity": "recent_n"}
    )
    assert resp.status_code == 201
    data = resp.json()["data"]
    assert data["recent_n"] == 10
    assert data["granularity"] == "recent_n"
    assert data["token_level"] in ("low", "mid", "high")


@pytest.mark.asyncio
async def test_sixth_active_side_session_returns_400(client):
    for _ in range(5):
        r = await client.post(
            "/api/chat/sessions/main1/side-sessions", json={"granularity": "digest"}
        )
        assert r.status_code == 201
    r6 = await client.post("/api/chat/sessions/main1/side-sessions", json={"granularity": "digest"})
    assert r6.status_code == 400


@pytest.mark.asyncio
async def test_side_session_cannot_create_side_session(client):
    r = await client.post("/api/chat/sessions/main1/side-sessions", json={"granularity": "full"})
    side_id = r.json()["data"]["id"]
    resp = await client.post(
        f"/api/chat/sessions/{side_id}/side-sessions", json={"granularity": "full"}
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_list_side_sessions_contains_expired_turns(client):
    await client.post("/api/chat/sessions/main1/side-sessions", json={"granularity": "full"})
    resp = await client.get("/api/chat/sessions/main1/side-sessions")
    assert resp.status_code == 200
    sides = resp.json()["data"]
    assert len(sides) == 1
    assert sides[0]["expired_turns"] == 0
    assert sides[0]["status"] == "active"


@pytest.mark.asyncio
async def test_list_side_sessions_contains_baseline_turn(client):
    """面板徽标「基于主任务第 N 轮」依赖 baseline_turn（快照 content 内落库值）。"""
    r = await client.post("/api/chat/sessions/main1/side-sessions", json={"granularity": "full"})
    baseline_turn = r.json()["data"]["baseline_turn"]
    resp = await client.get("/api/chat/sessions/main1/side-sessions")
    sides = resp.json()["data"]
    assert sides[0]["baseline_turn"] == baseline_turn


@pytest.mark.asyncio
async def test_list_main_sessions_includes_kind(client):
    """前端主会话侧边栏需按 kind 过滤侧会话（侧会话只在专用面板出现）。"""
    await client.post("/api/chat/sessions/main1/side-sessions", json={"granularity": "full"})
    resp = await client.get("/api/chat/sessions")
    assert resp.status_code == 200
    sessions = resp.json()["data"]
    kinds = {s["id"]: s["kind"] for s in sessions}
    assert kinds["main1"] == "main"
    side_ids = [k for k, v in kinds.items() if v == "side"]
    assert len(side_ids) == 1


@pytest.mark.asyncio
async def test_patch_side_session_renames(client):
    r = await client.post("/api/chat/sessions/main1/side-sessions", json={"granularity": "full"})
    side_id = r.json()["data"]["id"]
    resp = await client.patch(f"/api/side-sessions/{side_id}", json={"title": "查文档"})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["title"] == "查文档"
    assert data["status"] == "active"


@pytest.mark.asyncio
async def test_finalize_keep_archives_side_session(client):
    r = await client.post("/api/chat/sessions/main1/side-sessions", json={"granularity": "full"})
    side_id = r.json()["data"]["id"]
    resp = await client.post(
        "/api/chat/sessions/main1/finalize", json={"decisions": {side_id: "keep"}}
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["results"][side_id] == "archived"
    sides = (await client.get("/api/chat/sessions/main1/side-sessions")).json()["data"]
    assert sides[0]["status"] == "archived"


@pytest.mark.asyncio
async def test_finalize_discard_deletes_side_session(client):
    r = await client.post("/api/chat/sessions/main1/side-sessions", json={"granularity": "full"})
    side_id = r.json()["data"]["id"]
    resp = await client.post(
        "/api/chat/sessions/main1/finalize", json={"decisions": {side_id: "discard"}}
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["results"][side_id] == "deleted"
    sides = (await client.get("/api/chat/sessions/main1/side-sessions")).json()["data"]
    assert sides == []


@pytest.mark.asyncio
async def test_delete_side_session_returns_204(client):
    r = await client.post("/api/chat/sessions/main1/side-sessions", json={"granularity": "full"})
    side_id = r.json()["data"]["id"]
    resp = await client.delete(f"/api/side-sessions/{side_id}")
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_archived_side_session_rejects_new_messages(client):
    r = await client.post("/api/chat/sessions/main1/side-sessions", json={"granularity": "full"})
    side_id = r.json()["data"]["id"]
    await client.post("/api/chat/sessions/main1/finalize", json={"decisions": {side_id: "keep"}})
    resp = await client.post(
        f"/api/chat/sessions/{side_id}/messages",
        json={"role": "user", "content": "还在吗"},
    )
    assert resp.status_code == 403


# ---------- Task 6: 提炼式回写 ----------

from app.services.side_session import check_digest_cap  # noqa: E402


def test_check_digest_cap_boundary():
    side_messages = [
        {"role": "user", "content": "a" * 100},
        {"role": "assistant", "content": "b" * 100},
    ]
    assert check_digest_cap("c" * 60, side_messages) is True  # 恰 30%
    assert check_digest_cap("c" * 61, side_messages) is False  # 31% 拒绝
    assert check_digest_cap("短摘要", []) is False  # 无问答拒绝


@pytest.mark.asyncio
async def test_bring_back_injects_special_message(client):
    r = await client.post("/api/chat/sessions/main1/side-sessions", json={"granularity": "digest"})
    side_id = r.json()["data"]["id"]
    # 准备侧会话问答素材
    await client.post(
        f"/api/chat/sessions/{side_id}/messages", json={"role": "user", "content": "a" * 200}
    )
    await client.post(
        f"/api/chat/sessions/{side_id}/messages", json={"role": "assistant", "content": "b" * 200}
    )

    from app.api.routers import side_session as side_router_mod

    resp = await client.post(
        f"/api/side-sessions/{side_id}/bring-back",
        json={"digest": "结论：采用方案A" + "x" * 100},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["main_session_id"] == "main1"
    assert data["main_message_id"]

    # 主会话出现特殊消息
    msgs = (await client.get("/api/chat/sessions/main1/messages")).json()["data"]
    digest_msgs = [
        m for m in msgs if (m.get("agent_meta") or {}).get("message_type") == "side_session_digest"
    ]
    assert len(digest_msgs) == 1
    assert digest_msgs[0]["role"] == "user"
    assert digest_msgs[0]["agent_meta"]["side_session_id"] == side_id
    assert digest_msgs[0]["content"].startswith("结论：采用方案A")


@pytest.mark.asyncio
async def test_double_bring_back_creates_two_messages(client):
    r = await client.post("/api/chat/sessions/main1/side-sessions", json={"granularity": "digest"})
    side_id = r.json()["data"]["id"]
    await client.post(
        f"/api/chat/sessions/{side_id}/messages", json={"role": "user", "content": "a" * 300}
    )
    await client.post(
        f"/api/chat/sessions/{side_id}/messages", json={"role": "assistant", "content": "b" * 300}
    )

    await client.post(
        f"/api/side-sessions/{side_id}/bring-back", json={"digest": "第一次结论" + "x" * 160}
    )
    await client.post(
        f"/api/side-sessions/{side_id}/bring-back", json={"digest": "第二次结论" + "y" * 160}
    )

    msgs = (await client.get("/api/chat/sessions/main1/messages")).json()["data"]
    digest_msgs = [
        m for m in msgs if (m.get("agent_meta") or {}).get("message_type") == "side_session_digest"
    ]
    assert len(digest_msgs) == 2
    assert digest_msgs[0]["content"] != digest_msgs[1]["content"]


@pytest.mark.asyncio
async def test_bring_back_over_cap_returns_422(client):
    r = await client.post("/api/chat/sessions/main1/side-sessions", json={"granularity": "digest"})
    side_id = r.json()["data"]["id"]
    await client.post(
        f"/api/chat/sessions/{side_id}/messages", json={"role": "user", "content": "a" * 100}
    )
    resp = await client.post(f"/api/side-sessions/{side_id}/bring-back", json={"digest": "x" * 200})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_bring_back_when_main_session_deleted_returns_400(client, db_env):
    """主会话已删除时带回结论必须明确失败，不得伪造 main_message_id。"""
    _setup, sf = db_env
    r = await client.post("/api/chat/sessions/main1/side-sessions", json={"granularity": "digest"})
    side_id = r.json()["data"]["id"]
    await client.post(
        f"/api/chat/sessions/{side_id}/messages", json={"role": "user", "content": "a" * 200}
    )
    await client.post(
        f"/api/chat/sessions/{side_id}/messages", json={"role": "assistant", "content": "b" * 200}
    )

    async with sf() as db:
        main = await db.get(ChatSession, "main1")
        await db.delete(main)
        await db.commit()

    resp = await client.post(
        f"/api/side-sessions/{side_id}/bring-back",
        json={"digest": "结论：采用方案A" + "x" * 100},
    )
    assert resp.status_code == 400, resp.text
    assert "已被删除" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_draft_endpoint_returns_digest(client, monkeypatch):
    r = await client.post("/api/chat/sessions/main1/side-sessions", json={"granularity": "digest"})
    side_id = r.json()["data"]["id"]
    await client.post(
        f"/api/chat/sessions/{side_id}/messages", json={"role": "user", "content": "a" * 100}
    )
    await client.post(
        f"/api/chat/sessions/{side_id}/messages", json={"role": "assistant", "content": "b" * 100}
    )

    from app.services import side_session as svc

    async def fake_llm_chat(messages, response_format=None):
        return {"content": "摘要草稿：结论是方案A", "token_usage": 5}

    class _FakeLLM:
        async def chat(self, messages, response_format=None):
            return await fake_llm_chat(messages, response_format)

    monkeypatch.setattr(svc, "_get_llm", lambda temperature=0.3: _FakeLLM())
    resp = await client.post(f"/api/side-sessions/{side_id}/bring-back/draft")
    assert resp.status_code == 200
    assert resp.json()["data"]["digest"] == "摘要草稿：结论是方案A"


@pytest.mark.asyncio
async def test_other_user_cannot_access_side_sessions(db_env):
    _setup, sf = db_env
    await _setup()
    app = FastAPI()
    app.include_router(chat_session_router.router)
    from app.api.routers import side_session as side_session_router

    app.include_router(side_session_router.router)
    app.dependency_overrides[get_current_user] = lambda: "u2"
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        resp = await c.post("/api/chat/sessions/main1/side-sessions", json={"granularity": "full"})
        assert resp.status_code == 403


@pytest.mark.asyncio
async def test_finalize_ignores_side_sessions_not_owned_by_user(client, db_env):
    """finalize 越权防护：decisions 里塞他人侧会话 ID 不得影响他人数据。"""
    _setup, sf = db_env
    async with sf() as db:
        db.add(User(id="u2", nickname="另一个用户"))
        db.add(ChatSession(id="main2", user_id="u2", title="别人的主会话"))
        db.add(
            ChatSession(
                id="foreign_side",
                user_id="u2",
                kind="side",
                parent_session_id="main2",
                title="别人的侧会话",
                side_meta={"status": "active"},
            )
        )
        await db.commit()

    resp = await client.post(
        "/api/chat/sessions/main1/finalize",
        json={"decisions": {"foreign_side": "discard"}},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["results"]["foreign_side"] == "skipped"
    async with sf() as db:
        row = await db.get(ChatSession, "foreign_side")
        assert row is not None  # 未被删除


@pytest.mark.asyncio
async def test_concurrent_side_write_confirm_only_one_succeeds(client, db_env, monkeypatch):
    """并发确认竞态：同一侧会话的写确认同一时刻只允许一个成功，其余 409。"""
    import asyncio

    _setup, sf = db_env
    r = await client.post("/api/chat/sessions/main1/side-sessions", json={"granularity": "digest"})
    side_id = r.json()["data"]["id"]
    await side_session.set_pending_write(
        side_id,
        {
            "tool_name": "file_write",
            "arguments": {"path": "a.txt"},
            "description": "写文件",
            "permissions": ["file_write"],
        },
    )

    from app.engine.reactor.loop_state import save_loop_state

    state_path = save_loop_state(
        {
            "messages": [{"role": "user", "content": "hi"}],
            "context_snapshot": {},
        }
    )

    # 后台续跑用假会话，避免测试环境拉起真实 LLM
    class _FakeSession:
        turn_id = None
        side_session_id = None
        _side_write_allow_once = None

        async def run(self, *args, **kwargs):
            return {"ended_reason": "completed", "summary": "ok"}

    class _FakeFactory:
        def build_session(self, *args, **kwargs):
            return _FakeSession()

    import app.agents.registry as registry_mod

    monkeypatch.setattr(registry_mod, "AgentRegistry", lambda: _FakeFactory())

    async def _call():
        return await client.post(
            f"/api/chat/sessions/{side_id}/side-write-confirm",
            json={"action": "approve", "loop_state_path": state_path},
        )

    r1, r2 = await asyncio.gather(_call(), _call())
    assert sorted([r1.status_code, r2.status_code]) == [200, 409]


@pytest.mark.asyncio
async def test_archived_side_session_rejects_agent_chat(db_env):
    """archived 侧会话对 /agent 主对话入口同样只读（PRD FR-C）。"""
    _setup, sf = db_env
    await _setup()
    async with sf() as db:
        db.add(
            ChatSession(
                id="side_arch",
                user_id="u1",
                kind="side",
                parent_session_id="main1",
                title="已归档",
                side_meta={"status": "archived"},
            )
        )
        await db.commit()

    from fastapi import FastAPI as _FA

    from app.api.routers import chat_agent as chat_agent_router
    from app.db.session import get_db

    app = _FA()
    app.include_router(chat_agent_router.router)
    app.dependency_overrides[get_current_user] = lambda: "u1"

    async def _fake_db():
        yield object()

    app.dependency_overrides[get_db] = _fake_db
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        resp = await c.post(
            "/api/v1/chat/agent",
            json={"message": "继续干活", "session_id": "side_arch"},
        )
        assert resp.status_code == 403


# ---------- 会话归属契约：删除后不得被 system 归属复活 ----------


@pytest.mark.asyncio
async def test_add_message_without_owner_does_not_resurrect_deleted_session(db_env):
    """会话被删除后，无 owner_id 的写入必须丢弃，不得复活成 system 归属的僵尸会话。

    背景：Agent 回合 / 后台续跑写消息时都不传 owner_id。若用户删了会话而流程
    还在跑，旧实现会以 user_id="system" 重建同名会话，同一请求方随后订阅
    该会话 SSE 必然 403（归属不符），DB 里还留下无人可访问、无法列举的僵尸会话。
    """
    _setup, sf = db_env
    await _setup()

    assert await chat_session.add_message("ghost_session", "assistant", "迟到的写入") is None

    async with sf() as db:
        assert await db.get(ChatSession, "ghost_session") is None
        assert await db.get(User, "system") is None


@pytest.mark.asyncio
async def test_add_message_with_owner_keeps_bootstrap_path(client, db_env):
    """带 owner_id 的写入保留"先写消息后补会话"引导路径，且归属为真实用户。"""
    _setup, sf = db_env

    resp = await client.post(
        "/api/chat/sessions/boot_session/messages",
        json={"role": "user", "content": "引导消息"},
    )
    assert resp.status_code == 200, resp.text

    async with sf() as db:
        row = await db.get(ChatSession, "boot_session")
        assert row is not None
        assert row.user_id == "u1"
