"""续跑（confirm/clarify）回合盖章测试（P2#5）。

背景：确认/澄清卡的「续跑」会用一个新的 ReactorSession 恢复执行，此前它发布的
事件不带 turn_id。前端 resume 订阅一旦显式传 turnId（TurnRuntime 沙箱），
这些无章事件就会被整批拒收——续跑窗口收不到完成信号。

本测试锁定契约：
1. ReactorSession.turn_id 注入后，_emit_sse 发布的事件全部盖该 turn_id
2. ConfirmRequest / ClarifyRequest 接受 turn_id（缺省 None，旧前端不受影响）
3. 盖了首轮 turn_id 的续跑事件，能被「按首轮 turn_id 订阅」的流收到
"""

import uuid

import pytest

from app.services.sse_bus import sse_bus


def _sid() -> str:
    return f"test_resume_{uuid.uuid4().hex[:12]}"


async def _drain(agen, max_events: int = 5) -> list[str]:
    """收集 SSE 流直到收够事件（挂起态没有终态事件，流会一直挂着等心跳）。"""
    out = []
    async for chunk in agen:
        out.append(chunk)
        if len(out) >= max_events:
            break
    return out


class _FakeBus:
    """记录 publish 调用参数的最小替身（不依赖真实 bus 的订阅语义）。"""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def publish(self, workflow_id, event_type, payload, turn_id=None):
        self.calls.append(
            {
                "workflow_id": workflow_id,
                "event_type": event_type,
                "payload": payload,
                "turn_id": turn_id,
            }
        )
        return "evt_fake"


@pytest.mark.asyncio
async def test_reactor_session_stamps_turn_id_on_emit():
    """ReactorSession 注入 turn_id 后，内部事件全部盖该回合章。"""
    from app.engine.reactor.session import ReactorSession

    bus = _FakeBus()
    session = ReactorSession(workflow_id="wf_resume", sse_bus=bus)
    # 未注入时：None（旧行为，不盖章）
    await session._emit_sse("workflow_started", {"intent": "x"})
    assert bus.calls[-1]["turn_id"] is None

    session.turn_id = "turn_resume_1"
    await session._emit_sse("workflow_started", {"intent": "resume"})
    await session._emit_sse("agent_message_delta", {"delta": "HELLO_RESUME"})
    assert [c["turn_id"] for c in bus.calls[1:]] == ["turn_resume_1", "turn_resume_1"]
    assert bus.calls[-1]["event_type"] == "agent_message_delta"


@pytest.mark.asyncio
async def test_no_emit_without_workflow_id():
    """workflow_id 为空时 _emit_sse 直接短路（续跑会话默认即如此，不产生脏事件）。"""
    from app.engine.reactor.session import ReactorSession

    bus = _FakeBus()
    session = ReactorSession(workflow_id="", sse_bus=bus)
    session.turn_id = "turn_resume_1"
    await session._emit_sse("workflow_started", {"intent": "resume"})
    assert bus.calls == []


def test_confirm_and_clarify_requests_accept_turn_id():
    """两个续跑请求模型接受 turn_id，缺省 None（不传 = 旧行为）。"""
    from app.api.routers.chat_agent import ClarifyRequest, ConfirmRequest

    c = ConfirmRequest(session_id="s1", loop_state_path="/tmp/ls.json", action="approve")
    assert c.turn_id is None

    c2 = ConfirmRequest(
        session_id="s1", loop_state_path="/tmp/ls.json", action="approve", turn_id="turn_resume_1"
    )
    assert c2.turn_id == "turn_resume_1"

    q = ClarifyRequest(session_id="s1", loop_state_path="/tmp/ls.json", answers={"a": 1})
    assert q.turn_id is None

    q2 = ClarifyRequest(
        session_id="s1", loop_state_path="/tmp/ls.json", answers={}, turn_id="turn_resume_1"
    )
    assert q2.turn_id == "turn_resume_1"


@pytest.mark.asyncio
async def test_resume_events_visible_to_first_turn_subscription():
    """端到端契约：挂起中的回合（无终态事件），续跑事件能被首轮订阅收到。

    注意：bus 在终态事件处断流，所以「已完结的首轮」之后追加的事件不会被
    回放出来——但确认/澄清挂起不 publish 终态事件（状态由 HTTP 响应返回），
    故续跑事件可以被 resume 订阅完整接收。
    """
    sid = _sid()
    turn = "turn_first"

    await sse_bus.publish(sid, "chat_turn_started", {"session_id": sid}, turn_id=turn)
    # 挂起：非终态事件（awaiting_confirmation 由 HTTP 响应给出）
    await sse_bus.publish(sid, "node_started", {"node_id": "confirm_gate"}, turn_id=turn)
    # 续跑完成事件：与首轮同回合
    await sse_bus.publish(sid, "agent_loop_done", {"status": "completed"}, turn_id=turn)

    chunks = await _drain(sse_bus.subscribe(sid, None, turn), max_events=3)
    raw = "".join(chunks)
    assert "chat_turn_started" in raw
    assert "agent_loop_done" in raw


@pytest.mark.asyncio
async def test_terminal_event_stops_replay_before_resume_events():
    """反向锁定：首轮若已终态（workflow_completed），追加事件不会回放出来。

    这是既有限制而非缺陷——续跑走的是「未终态挂起」路径；若某回合真的已完结，
    resume 订阅收不到后续事件，前端仍有 3s 兜底填正文。
    """
    sid = _sid()
    turn = "turn_done"
    await sse_bus.publish(sid, "chat_turn_started", {"session_id": sid}, turn_id=turn)
    await sse_bus.publish(sid, "workflow_completed", {"workflow_id": sid}, turn_id=turn)
    await sse_bus.publish(sid, "agent_loop_done", {"status": "completed"}, turn_id=turn)

    chunks = []
    async for chunk in sse_bus.subscribe(sid, None, turn):
        chunks.append(chunk)
    raw = "".join(chunks)
    assert "workflow_completed" in raw
    assert "agent_loop_done" not in raw
