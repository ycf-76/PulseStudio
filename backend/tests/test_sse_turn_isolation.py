"""SSE 回合隔离测试（渲染层重构 L1，问题 6 根治）。

同一 session 连续多轮对话时，SSE bus 以 session_id 为 key 复用：
- 不传 turn_id 的订阅会回放全部历史（旧行为，向后兼容）
- 传 turn_id 的订阅只回放/接收本回合事件，上一轮输出绝不泄漏

约定（与前端 TurnRuntime 沙箱构成双保险：后端不泄、前端不接）。
"""

import asyncio
import json
import uuid

import pytest

from app.services.sse_bus import sse_bus


def _sid() -> str:
    """每个用例独立 session key——bus 是进程内单例，_event_history 跨用例共享。"""
    return f"test_sess_{uuid.uuid4().hex[:12]}"


async def _drain(agen, max_events: int = 20) -> list[str]:
    """收集 SSE 流直到终态断流（或达到上限兜底）。"""
    out = []
    async for chunk in agen:
        out.append(chunk)
        if len(out) >= max_events:
            break
    return out


def _payloads(chunks: list[str]) -> list[dict]:
    """从 SSE 原始块解析 data JSON。"""
    docs = []
    for chunk in chunks:
        for line in chunk.split("\n"):
            if line.startswith("data: "):
                docs.append(json.loads(line[6:]))
    return docs


@pytest.mark.asyncio
async def test_same_turn_events_all_delivered():
    """同回合事件全量送达（含 turn_id 顶层字段）。"""
    sid = _sid()
    turn = "turn_aaa"
    await sse_bus.publish(sid, "chat_turn_started", {"session_id": sid}, turn_id=turn)
    await sse_bus.publish(sid, "agent_message_delta", {"delta": "HELLO"}, turn_id=turn)
    await sse_bus.publish(sid, "workflow_completed", {"workflow_id": sid}, turn_id=turn)

    chunks = await _drain(sse_bus.subscribe(sid, None, turn))
    docs = _payloads(chunks)
    types = [d["type"] for d in docs]
    assert types == ["chat_turn_started", "agent_message_delta", "workflow_completed"]
    assert all(d["turn_id"] == turn for d in docs)
    assert any(d["payload"].get("delta") == "HELLO" for d in docs)


@pytest.mark.asyncio
async def test_off_turn_events_never_leak():
    """二回合订阅零泄漏：先完结上一回合，再发起新回合，新订阅只收新回合。"""
    sid = _sid()
    old_turn = "turn_old"
    new_turn = "turn_new"

    # 上一回合：完整跑完并终态（旧内容打标，后面断言它零出现）
    await sse_bus.publish(sid, "chat_turn_started", {"session_id": sid}, turn_id=old_turn)
    await sse_bus.publish(
        sid, "agent_message_delta", {"delta": "OLD_TURN_OUTPUT"}, turn_id=old_turn
    )
    await sse_bus.publish(sid, "workflow_completed", {"workflow_id": sid}, turn_id=old_turn)

    # 新回合开始
    await sse_bus.publish(sid, "chat_turn_started", {"session_id": sid}, turn_id=new_turn)
    await sse_bus.publish(
        sid, "agent_message_delta", {"delta": "NEW_TURN_OUTPUT"}, turn_id=new_turn
    )
    await sse_bus.publish(sid, "workflow_completed", {"workflow_id": sid}, turn_id=new_turn)

    chunks = await _drain(sse_bus.subscribe(sid, None, new_turn))
    raw = "".join(chunks)
    docs = _payloads(chunks)
    types = [d["type"] for d in docs]
    assert types == ["chat_turn_started", "agent_message_delta", "workflow_completed"]
    assert all(d["turn_id"] == new_turn for d in docs)
    assert "NEW_TURN_OUTPUT" in raw
    assert "OLD_TURN_OUTPUT" not in raw
    assert old_turn not in raw


@pytest.mark.asyncio
async def test_legacy_subscription_without_turn_id_replays_all():
    """向后兼容：不传 turn_id 的订阅维持旧行为（回放全部历史）。"""
    sid = _sid()
    await sse_bus.publish(sid, "chat_turn_started", {"session_id": sid}, turn_id="turn_x")
    await sse_bus.publish(sid, "agent_message_delta", {"delta": "ANY"}, turn_id="turn_y")
    await sse_bus.publish(sid, "workflow_completed", {"workflow_id": sid}, turn_id="turn_z")

    chunks = await _drain(sse_bus.subscribe(sid))
    docs = _payloads(chunks)
    types = [d["type"] for d in docs]
    # 全部历史回放（含三个不同回合的事件），遇终态断流
    assert types == ["chat_turn_started", "agent_message_delta", "workflow_completed"]
    turn_ids = {d["turn_id"] for d in docs}
    assert turn_ids == {"turn_x", "turn_y", "turn_z"}
