"""tool_call_start/end 必须下发子智能体身份（执行轨迹分组真名的回归锁）。

背景：ToolCallCell 的子智能体分组头此前只有一条「猜」出来的路——
`agentMeta.collab.subAgentNames`，而后端从未写入 `agentMeta.collab`
（实测 grep 后端 app/ 下无该键），ReactSession 主循环的 tool_call_start
也没有 sub_agent_id 字段 → 前端轨迹永远只显示「子智能体」这种无信息标签，
即「前端假大空」的又一实例。

本测试锁定契约（2026-10-19）：
1. spawn_agent 的 tool_call_start 带 sub_agent_name = task_name（真名，调用前已知）
2. tool_call_end 带权威 sub_agent_id / sub_agent_name（来自 dispatch 返回值）
3. 带 agent_id 参数的协作工具（如 wait_agent）start 时即带 sub_agent_id
4. 普通工具两个字段均为空串（不污染父行分组）
"""

from __future__ import annotations

import pytest

from app.engine.reactor.session import ReactorSession


class _FakeBus:
    """记录 publish 调用参数的最小替身。"""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def publish(self, workflow_id, event_type, payload, turn_id=None):
        self.calls.append({"event_type": event_type, "payload": payload})
        return "evt_fake"


class _FakeStreamLLM:
    """按 chunk 列表产出流式响应的 LLM 替身。

    每次调用返回一整段 JSON（模拟一次完整 ReAct 响应），
    steps 按 iteration 依次取用。
    """

    def __init__(self, steps: list[dict]) -> None:
        self._steps = steps
        self._i = 0

    async def stream_chat(self, messages):  # noqa: ANN001, ANN201
        import json

        step = self._steps[min(self._i, len(self._steps) - 1)]
        self._i += 1
        yield {"content": json.dumps(step, ensure_ascii=False)}
        yield {"content": "", "is_final": True, "token_usage": 10}


def _step_with_tools(tool_calls: list[dict]) -> dict:
    """一个非 final 的工具步：走到 dispatch 才会 break。"""
    return {"thought": "派生子智能体并行准备素材", "tool_calls": tool_calls, "final": False}


def _final_step() -> dict:
    return {
        "thought": "素材已齐，产出终稿",
        "tool_calls": [],
        "final": True,
        "output": {"_message": "DONE"},
    }


def _payloads(bus: _FakeBus, event_type: str) -> list[dict]:
    return [c["payload"] for c in bus.calls if c["event_type"] == event_type]


@pytest.mark.asyncio
async def test_spawn_agent_carries_real_sub_agent_identity():
    """spawn_agent：start 带 task_name 真名，end 带 dispatch 返回的 agent_id/name。"""
    bus = _FakeBus()
    session = ReactorSession(
        llm=_FakeStreamLLM([
            _step_with_tools([{
                "name": "spawn_agent",
                "arguments": {
                    "task_name": "通勤妆容素材组",
                    "task_description": "搜集近 7 天早八妆爆款",
                    "role": "researcher",
                },
            }]),
            _final_step(),
        ]),
        workflow_id="wf_sub_id",
        sse_bus=bus,
    )

    # dispatch 替身：reactor 调 self._dispatch(call) 拿 observation。
    async def _fake_dispatch(call):  # noqa: ANN001, ANN202
        return {
            "ok": True,
            "agent_id": "agent_9f3c",
            "name": "通勤妆容素材组",
            "message": "Sub-agent spawned",
        }

    session._dispatch = _fake_dispatch  # type: ignore[method-assign]

    await session.run("帮我做一期早八通勤妆")

    starts = _payloads(bus, "tool_call_start")
    assert starts, "tool_call_start 未下发"
    assert starts[0]["sub_agent_name"] == "通勤妆容素材组"
    # agent_id 在 spawn 前未知，start 阶段应为空串而不是编造的 id
    assert starts[0]["sub_agent_id"] == ""

    ends = _payloads(bus, "tool_call_end")
    assert ends, "tool_call_end 未下发"
    assert ends[0]["sub_agent_id"] == "agent_9f3c"
    assert ends[0]["sub_agent_name"] == "通勤妆容素材组"


@pytest.mark.asyncio
async def test_tool_with_agent_id_arg_carries_id_at_start():
    """wait_agent 这类带 agent_id 的工具，start 阶段就带得出身份。"""
    bus = _FakeBus()
    session = ReactorSession(
        llm=_FakeStreamLLM([
            _step_with_tools([{
                "name": "wait_agent",
                "arguments": {"agent_id": "agent_9f3c"},
            }]),
            _final_step(),
        ]),
        workflow_id="wf_wait_agent",
        sse_bus=bus,
    )

    async def _fake_dispatch(call):  # noqa: ANN001, ANN202
        return {"ok": True, "output": "素材已回收"}

    session._dispatch = _fake_dispatch  # type: ignore[method-assign]

    await session.run("等子智能体回来")

    starts = _payloads(bus, "tool_call_start")
    assert starts[0]["sub_agent_id"] == "agent_9f3c"
    # 名字此时不知道（wait_agent 参数里没有 task_name），留空让前端用标签兜底
    assert starts[0]["sub_agent_name"] == ""

    ends = _payloads(bus, "tool_call_end")
    # dispatch 没回 agent_id，不应把 wait_agent 误归属到某个子智能体
    assert ends[0]["sub_agent_id"] == "agent_9f3c"


@pytest.mark.asyncio
async def test_plain_tool_emits_empty_sub_agent_fields():
    """普通工具（xhs_search 等）不得被误判成子智能体行。"""
    bus = _FakeBus()
    session = ReactorSession(
        llm=_FakeStreamLLM([
            _step_with_tools([{
                "name": "xhs_search",
                "arguments": {"query": "早八妆", "limit": 20},
            }]),
            _final_step(),
        ]),
        workflow_id="wf_plain_tool",
        sse_bus=bus,
    )

    async def _fake_dispatch(call):  # noqa: ANN001, ANN202
        return {"ok": True, "count": 20, "results": []}

    session._dispatch = _fake_dispatch  # type: ignore[method-assign]

    await session.run("搜一下早八妆热点")

    for payload in _payloads(bus, "tool_call_start") + _payloads(bus, "tool_call_end"):
        assert payload["sub_agent_id"] == ""
        assert payload["sub_agent_name"] == ""
