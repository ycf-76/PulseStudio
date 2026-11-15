"""cut_plan / guardian 确认分支顺序回归测试（2026-11-05）。

背景：session.py 主循环里 guardian 分支（needs_confirmation 且无 shots）排在
cut_plan 分支之前，导致 talking_head_overlay 的 overlay_plan_ready 信号永远被
guardian 通用卡截胡，专用叠加方案卡一次都没弹出来过（15:51 日志实锤）。
修复后 cut_plan 分支先行。本测试锁定该顺序，全部离线 mock。
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.engine.governance.guardian import GuardianAction
from app.engine.reactor.session import ReactorSession
from app.engine.schemas import Permission


class _FakeBus:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def publish(self, workflow_id, event_type, payload, turn_id=None):
        self.calls.append({"event_type": event_type, "payload": payload})
        return "evt_fake"


class _QueuedLLM:
    """每次 stream_chat 消费一个预置响应。"""

    def __init__(self, responses: list[list[dict]]) -> None:
        self._responses = list(responses)

    async def stream_chat(self, messages):
        chunks = self._responses.pop(0) if self._responses else [
            {"content": "", "is_final": True, "token_usage": 0}
        ]
        for c in chunks:
            yield c


def _skill_call_chunks() -> list[dict]:
    return [
        {"content": json.dumps({
            "thought": "规划叠加方案",
            "tool_calls": [{
                "name": "skill_call",
                "arguments": {"skill": "talking_head_overlay",
                              "arguments": {"video_path": "x.mp4"}},
            }],
        })},
        {"content": "", "is_final": True, "token_usage": 1},
    ]


_OVERLAY_PLAN_OBS = {
    "event": "overlay_plan_ready",
    "data": {"segments": [{"segment_id": "s1"}], "revision": None},
    "needs_confirmation": True,
    "message": "规划了 14 个叠加片段，请确认",
}


def _allow_guardian(session: ReactorSession) -> None:
    decision = MagicMock()
    decision.action = GuardianAction.ALLOW
    decision.risk_level = "medium"
    session._guardian = MagicMock()
    session._guardian.check = AsyncMock(return_value=decision)


async def test_overlay_plan_ready_hits_cut_plan_branch_not_guardian():
    """overlay_plan_ready + needs_confirmation → 必须走 cut_plan 专用卡，
    不能被 guardian 通用分支截胡。"""
    bus = _FakeBus()
    session = ReactorSession(
        llm=_QueuedLLM([_skill_call_chunks()]),
        skills=[],
        allowed_permissions=set(Permission),
        sse_bus=bus,
        workflow_id="test_order",
    )
    _allow_guardian(session)
    session._transport = MagicMock()
    session._transport.get_tool = MagicMock(return_value=MagicMock(required_permissions=()))
    session._transport.call = AsyncMock(return_value=dict(_OVERLAY_PLAN_OBS))

    result = await session.run("帮我做一期口播剪辑规划")

    assert result.get("ended_reason") == "cut_plan_paused"
    event_types = [c["event_type"] for c in bus.calls]
    assert "cut_plan_confirmation_required" in event_types
    assert "guardian_confirmation_required" not in event_types
    # 方案数据随事件下发（前端弹专用卡的数据源）
    cp = next(c for c in bus.calls if c["event_type"] == "cut_plan_confirmation_required")
    assert cp["payload"]["message"] == "规划了 14 个叠加片段，请确认"
    assert cp["payload"]["plan"]["segments"]
    assert cp["payload"]["loop_state_path"]


async def test_plain_needs_confirmation_still_hits_guardian_branch():
    """无 event 字段的裸 needs_confirmation（真 Guardian 拦截）仍走 guardian 分支。"""
    bus = _FakeBus()
    session = ReactorSession(
        llm=_QueuedLLM([_skill_call_chunks()]),
        skills=[],
        allowed_permissions=set(Permission),
        sse_bus=bus,
        workflow_id="test_order",
    )
    _allow_guardian(session)
    session._transport = MagicMock()
    session._transport.get_tool = MagicMock(return_value=MagicMock(required_permissions=()))
    session._transport.call = AsyncMock(return_value={
        "ok": False,
        "error": "requires user confirmation",
        "needs_confirmation": True,
        "confirmation_prompt": "是否执行？",
    })

    result = await session.run("帮我做一期口播剪辑规划")

    assert result.get("ended_reason") == "guardian_paused"
    event_types = [c["event_type"] for c in bus.calls]
    assert "guardian_confirmation_required" in event_types
    assert "cut_plan_confirmation_required" not in event_types
