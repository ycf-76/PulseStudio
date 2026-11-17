"""reactor 协议 JSON 不得作为正文增量推送（「聊天输出裸 JSON」根因的回归锁）。

背景：reactor 引擎与 LLM 的对话协议本身是 JSON（prompt.py L64-77：
{"thought", "tool_calls", "final", "output"}）。旧行为把每段 LLM chunk 原样
emit 成 agent_message_delta，协议原文直达前端正文——用户看到的就是裸 JSON；
且前端 POST 侧「已有内容跳过格式化」的优化继而被人质，formatAgentOutput
的人话输出永远排不上号。

本测试锁定契约：
1. run() 主循环调用 _call_llm 时不再推送 agent_message_delta（emit_delta=False）
2. thought 仍由 decision_made 承载、终态仍 emit workflow_completed（零信息损失）
3. emit_delta=True 显式开启时机制仍在（留给未来「输出即用户散文」的调用方）
4. _call_llm 默认值即 False（防御未来调用点忘记传参）
"""

from __future__ import annotations

import pytest

from app.engine.reactor.session import ReactorSession


class _FakeBus:
    """记录 publish 调用参数的最小替身（同 test_resume_turn_id 的用法）。"""

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


class _FakeStreamLLM:
    """按 chunk 列表产出流式响应的 LLM 替身（协议 JSON 形态）。"""

    def __init__(self, chunks: list[dict]) -> None:
        self._chunks = chunks

    async def stream_chat(self, messages):  # noqa: ANN001
        for c in self._chunks:
            yield c


def _protocol_chunks() -> list[dict]:
    """一次完整的 final=true ReAct 响应，拆成 4 个 chunk（模拟真实流式）。"""
    return [
        {"content": '{"thought"'},
        {"content": ': "已获得用户需求，'},
        {"content": '直接产出终稿", "final": true, "output": {"_message": "PROTOCOL_OK"}}'},
        {"content": "", "is_final": True, "token_usage": 42},
    ]


def _event_types(bus: _FakeBus) -> list[str]:
    return [c["event_type"] for c in bus.calls]


@pytest.mark.asyncio
async def test_run_does_not_stream_protocol_json_as_body_delta():
    """核心回归：run() 全流程不得出现 agent_message_delta（协议 JSON 泄源已焊死）。"""
    bus = _FakeBus()
    session = ReactorSession(
        llm=_FakeStreamLLM(_protocol_chunks()),
        workflow_id="wf_no_proto_delta",
        sse_bus=bus,
    )

    output = await session.run("帮我写一篇手冲咖啡的小红书笔记")

    types = _event_types(bus)
    assert "agent_message_delta" not in types
    # 零信息损失：thought 出口与终态出口都在
    assert "decision_made" in types
    assert "workflow_completed" in types
    assert "node_completed" in types
    # decision_made 载荷确实是 thought 原文（用户信息不丢）
    decision = next(c for c in bus.calls if c["event_type"] == "decision_made")
    assert "已获得用户需求" in decision["payload"]["decision"]
    # 终态 output 仍是结构化 final_output
    assert output.get("_message") == "PROTOCOL_OK"


@pytest.mark.asyncio
async def test_call_llm_default_suppresses_delta():
    """_call_llm 默认不推送 delta（未来调用点忘记传参也安全）。"""
    bus = _FakeBus()
    session = ReactorSession(
        llm=_FakeStreamLLM(_protocol_chunks()),
        workflow_id="wf_default_off",
        sse_bus=bus,
    )

    content, token_usage, _reasoning = await session._call_llm([{"role": "user", "content": "x"}])

    assert "agent_message_delta" not in _event_types(bus)
    assert '"final": true' in content
    assert token_usage == 42


@pytest.mark.asyncio
async def test_call_llm_emit_delta_true_still_works():
    """机制保留：显式 emit_delta=True 时 agent_message_delta 照常推送。"""
    bus = _FakeBus()
    session = ReactorSession(
        llm=_FakeStreamLLM(_protocol_chunks()),
        workflow_id="wf_opt_in",
        sse_bus=bus,
    )

    await session._call_llm([{"role": "user", "content": "x"}], emit_delta=True)

    deltas = [c for c in bus.calls if c["event_type"] == "agent_message_delta"]
    assert len(deltas) == 3  # 3 个非空 content chunk
    assert deltas[0]["payload"]["delta"] == '{"thought"'
