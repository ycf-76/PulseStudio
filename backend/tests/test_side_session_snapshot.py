"""侧会话快照构建服务测试。

build_snapshot_content / token_level_of 为纯函数，直接测；
异步 DB 函数用独立 SQLite 内存引擎并 monkeypatch 服务的 AsyncSessionLocal。
"""

import os
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite://")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-side-session")

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import Base, ChatMessage, ChatSession, SideSnapshot, User, generate_ulid
from app.services import side_session

# ---------- 纯函数：build_snapshot_content ----------


def _msgs(*pairs: tuple[str, str]) -> list[dict]:
    return [{"id": generate_ulid(), "role": r, "content": c} for r, c in pairs]


def _ordered_ulids(n: int) -> list[str]:
    """生成严格递增的 n 个真实 ULID（逐毫秒推进，避免同毫秒随机位乱序）。"""
    import ulid

    base_s = 1_700_000_000.0
    return [str(ulid.from_timestamp(base_s + i * 0.001)) for i in range(n)]


def test_build_snapshot_full_contains_all():
    messages = _msgs(("user", "问题1"), ("assistant", "答1"), ("user", "问题2"))
    out = side_session.build_snapshot_content("full", messages, "摘要")
    assert len(out["messages"]) == 3
    assert out["summary"] == "摘要"
    assert out["messages"][0]["role"] == "user"


def test_build_snapshot_recent_n_only_tail():
    ids = _ordered_ulids(12)
    messages = [{"id": ids[i], "role": "user", "content": f"m{i}"} for i in range(12)]
    out = side_session.build_snapshot_content("recent_n", messages, "", recent_n=10)
    assert len(out["messages"]) == 10
    assert out["messages"][-1]["content"] == "m11"
    assert out["messages"][0]["content"] == "m2"


def test_build_snapshot_digest_goal_is_first_user_message():
    messages = _msgs(
        ("assistant", "欢迎"),
        ("user", "我想做一份AI教育方案"),
        ("assistant", "好的"),
    )
    out = side_session.build_snapshot_content("digest", messages, "任务摘要")
    assert out["goal"] == "我想做一份AI教育方案"
    assert out["messages"] == []
    assert out["summary"] == "任务摘要"


@pytest.mark.parametrize("granularity", ["full", "recent_n", "digest"])
def test_build_snapshot_empty_messages_no_error(granularity):
    out = side_session.build_snapshot_content(granularity, [], "")
    assert "messages" in out


def test_build_snapshot_filters_non_chat_roles():
    messages = _msgs(("user", "q"), ("system", "sys"), ("assistant", "a"))
    out = side_session.build_snapshot_content("full", messages, "")
    assert [m["role"] for m in out["messages"]] == ["user", "assistant"]


# ---------- 纯函数：token_level_of ----------


def test_token_level_boundary():
    small = {"goal": "", "summary": "", "messages": [{"role": "user", "content": "a" * 5000}]}
    assert side_session.token_level_of(small) == "low"
    mid = {"goal": "", "summary": "", "messages": [{"role": "user", "content": "a" * 7000}]}
    assert side_session.token_level_of(mid) == "mid"
    huge = {"goal": "", "summary": "", "messages": [{"role": "user", "content": "a" * 70_001}]}
    assert side_session.token_level_of(huge) == "high"


# ---------- DB：compute_expired_turns ----------


@pytest.fixture()
async def db_sessionmaker(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        for t in [
            User.__table__,
            ChatSession.__table__,
            ChatMessage.__table__,
            SideSnapshot.__table__,
        ]:
            await conn.run_sync(t.create, checkfirst=True)
    sf = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(side_session, "AsyncSessionLocal", sf)
    yield sf
    await engine.dispose()


@pytest.mark.asyncio
async def test_compute_expired_turns_counts_users_after_baseline(db_sessionmaker):
    async with db_sessionmaker() as db:
        db.add(User(id="u1", nickname="测试"))
        s = ChatSession(id="s1", user_id="u1", title="主会话")
        db.add(s)
        ids = _ordered_ulids(4)
        m1 = ChatMessage(id=ids[0], session_id="s1", role="user", content="q1")
        m2 = ChatMessage(id=ids[1], session_id="s1", role="assistant", content="a1")
        m3 = ChatMessage(id=ids[2], session_id="s1", role="user", content="q2")
        m4 = ChatMessage(id=ids[3], session_id="s1", role="user", content="q3")
        db.add_all([m1, m2, m3, m4])
        await db.commit()

    assert await side_session.compute_expired_turns("s1", m1.id) == 2
    assert await side_session.compute_expired_turns("s1", m4.id) == 0
    assert await side_session.compute_expired_turns("s1", "") == 3


@pytest.mark.asyncio
async def test_create_side_session_baseline_is_latest_when_over_200(db_sessionmaker, monkeypatch):
    """主会话消息超过 200 条时，快照基准点必须落在**最新**消息上（而非第 200 条）。"""
    from app.services import chat_session as _cs

    monkeypatch.setattr(_cs, "AsyncSessionLocal", db_sessionmaker)
    async with db_sessionmaker() as db:
        db.add(User(id="u1", nickname="测试"))
        db.add(ChatSession(id="s1", user_id="u1", title="主会话"))
        ids = _ordered_ulids(205)
        for i in range(205):
            db.add(ChatMessage(id=ids[i], session_id="s1", role="user", content=f"m{i}"))
        await db.commit()

    out = await side_session.create_side_session("s1", "u1", "full")
    assert out["baseline_message_id"] == ids[204]


def test_load_loop_state_rejects_path_traversal(monkeypatch, tmp_path):
    """loop_state 路径穿越防护：相对跳出与绝对路径一律按状态不存在处理。"""
    import app.engine.reactor.loop_state as ls

    monkeypatch.setattr(ls, "_STATE_DIR", tmp_path)
    with pytest.raises(FileNotFoundError):
        ls.load_loop_state("../outside.json")
    with pytest.raises(FileNotFoundError):
        ls.load_loop_state(str(tmp_path.parent / "elsewhere.json"))
    with pytest.raises(FileNotFoundError):
        ls.load_loop_state("..\\outside.json")
