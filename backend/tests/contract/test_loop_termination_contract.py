"""一期契约测试：ended_reason 终止协议 + 事件 schema（docs/内核-能力分层架构方案.md §3.2 契约带①）。

锁定内容：
1. EndedReason 枚举成员不删不减（前端靠它渲染终态）
2. 四条可离线驱动的出口路径各自标注正确原因（completed / nudge_exhausted /
   max_iterations / token_budget）
3. _format_final_output 白名单放行 ended_reason，且继续滤掉大对象
4. 总线上出现的每个事件名都已在 EventType 登记
5. session 源码级插桩完整性：每个 reason 都有出口赋值点

全部离线 mock，不碰真机 LLM。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.engine.protocol.events import (
    EventType,
    EndedReason,
    LoopTermination,
    SCHEMA_VERSION,
    TERMINAL_EVENTS,
    validate_ended_reason,
)
from app.engine.reactor import session as session_module
from app.engine.reactor.session import ReactorSession


class _FakeBus:
    """记录 publish 调用参数的最小替身。"""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def publish(self, workflow_id, event_type, payload, turn_id=None):
        self.calls.append({"event_type": event_type, "payload": payload})
        return "evt_fake"


class _QueuedLLM:
    """每次 stream_chat 消费一个预置响应（chunk 列表），耗尽后给空 final。"""

    def __init__(self, responses: list[list[dict]]) -> None:
        self._responses = list(responses)
        self.calls = 0

    async def stream_chat(self, messages):
        self.calls += 1
        chunks = self._responses.pop(0) if self._responses else [
            {"content": "", "is_final": True, "token_usage": 0}
        ]
        for c in chunks:
            yield c


def _final_chunks() -> list[dict]:
    return [
        {"content": '{"thought": "直接产出终稿", "final": true, "output": {"_message": "OK"}}'},
        {"content": "", "is_final": True, "token_usage": 10},
    ]


def _text_chunks() -> list[dict]:
    """非结构化文本（无 tool_calls 且非 final）→ 触发 nudge。"""
    return [{"content": "我觉得还得再想想。", "is_final": True, "token_usage": 1}]


def _tool_call_chunks() -> list[dict]:
    """带未知工具调用的 ReAct JSON（dispatch 报 unknown tool，循环继续）。"""
    return [
        {"content": '{"thought": "调工具", "tool_calls": [{"name": "no_such_tool", "arguments": {}}]}'},
        {"content": "", "is_final": True, "token_usage": 1},
    ]


def _big_token_chunks() -> list[dict]:
    # chunk 聚合契约：token_usage 只在 is_final=True 的收尾 chunk 上生效
    return [
        {"content": '{"thought": "先想一步", "tool_calls": []}', "is_final": False},
        {"content": "", "is_final": True, "token_usage": 500_000},
    ]


def _event_names(bus: _FakeBus) -> set[str]:
    return {c["event_type"] for c in bus.calls}


# ---------- 1. 枚举契约 ----------

def test_ended_reason_members_frozen():
    """前端渲染依赖这组值：只许加，不许删/改。

    fake_final 为 2026-10 新增：Execution Gate 判定"零执行零产物"的假完成
    （format nudge 失败、duplicate call 放弃等）。前端只对 *_paused 做特殊
    分支，其余值按普通终态渲染，故新增该值不影响既有渲染路径。
    """
    assert {r.value for r in EndedReason} == {
        "completed", "max_iterations", "token_budget", "nudge_exhausted",
        "duplicate_calls", "fake_final", "clarification_paused",
        "guardian_paused", "storyboard_paused", "cut_plan_paused",
        "side_write_paused", "error",
    }


def test_schema_version_stable():
    assert SCHEMA_VERSION == "1.0"


def test_validate_ended_reason_falls_back():
    assert validate_ended_reason("token_budget") == "token_budget"
    assert validate_ended_reason("不存在的原因") == "completed"


# ---------- 2. 出口路径 ----------

@pytest.mark.asyncio
async def test_completed_path():
    bus = _FakeBus()
    session = ReactorSession(llm=_QueuedLLM([_final_chunks()]), workflow_id="wf_c1", sse_bus=bus)
    output = await session.run("写一篇手冲咖啡笔记")

    assert output["ended_reason"] == "completed"
    assert output["_message"] == "OK"
    term = next(c for c in bus.calls if c["event_type"] == "workflow_completed")
    assert term["payload"]["ended_reason"] == "completed"
    assert term["payload"]["schema_version"] == SCHEMA_VERSION


@pytest.mark.asyncio
async def test_nudge_exhausted_path():
    """nudge 用尽后接受输出，但 Execution Gate 会把它改写成 fake_final。

    原始触发原因保留在 _execution_gate.trigger，供前端/评测定位"为什么终止"。
    """
    bus = _FakeBus()
    session = ReactorSession(
        llm=_QueuedLLM([_text_chunks(), _text_chunks(), _text_chunks()]),
        workflow_id="wf_n1", sse_bus=bus,
    )
    output = await session.run("写一篇手冲咖啡笔记")

    assert output["_execution_gate"]["trigger"] == "nudge_exhausted"
    assert output["_fake_final"] is True
    assert output["ended_reason"] == "fake_final"


@pytest.mark.asyncio
async def test_max_iterations_path():
    bus = _FakeBus()
    session = ReactorSession(
        llm=_QueuedLLM([_tool_call_chunks()]),
        workflow_id="wf_m1", sse_bus=bus, max_iterations=1,
    )
    output = await session.run("写一篇手冲咖啡笔记")

    assert output["ended_reason"] == "max_iterations"
    assert output.get("_loop_truncated") is True


@pytest.mark.asyncio
async def test_token_budget_path():
    bus = _FakeBus()
    session = ReactorSession(llm=_QueuedLLM([_big_token_chunks()]), workflow_id="wf_t1", sse_bus=bus)
    output = await session.run("写一篇手冲咖啡笔记")

    assert output["ended_reason"] == "token_budget"
    assert output.get("_token_budget_exhausted") is True


# ---------- 3. 输出白名单契约 ----------

def test_format_final_output_whitelist():
    output = {
        "title": "标题",
        "ended_reason": "token_budget",
        "_iterations": 3,
        "_token_usage": 100,
        "_last_thought": "想法",
        # 大对象必须继续被滤掉（SSE 通道不搬运明细）
        "_steps": [{"iteration": 1}],
        "_loop_truncated": True,
    }
    display = ReactorSession._format_final_output(output)

    assert display["ended_reason"] == "token_budget"
    assert display["_iterations"] == 3
    assert "_steps" not in display
    assert "_loop_truncated" not in display


def test_format_final_output_defaults_completed():
    display = ReactorSession._format_final_output({})
    assert display["ended_reason"] == "completed"


# ---------- 4. 事件名全登记 ----------

@pytest.mark.asyncio
async def test_all_sse_events_registered():
    bus = _FakeBus()
    session = ReactorSession(llm=_QueuedLLM([_final_chunks()]), workflow_id="wf_e1", sse_bus=bus)
    await session.run("写一篇手冲咖啡笔记")

    registered = {e.value for e in EventType}
    unknown = _event_names(bus) - registered
    assert not unknown, f"总线上出现未登记事件: {unknown}"


def test_terminal_events_carry_ended_reason():
    assert "node_completed" in TERMINAL_EVENTS
    assert "workflow_completed" in TERMINAL_EVENTS
    payload = LoopTermination(ended_reason="max_iterations", iterations=12, token_usage=900)
    assert payload.ended_reason == EndedReason.MAX_ITERATIONS


# ---------- 5. 源码级插桩完整性 ----------

def test_all_reasons_have_exit_annotations():
    """静态锁：除 ERROR（上层捕获路径赋值）外，每个 reason 在 session.py
    都有 EndedReason.X 出口赋值点——防止未来加出口忘记标原因。"""
    src = Path(session_module.__file__).read_text(encoding="utf-8")
    missing = [
        r.name for r in EndedReason
        if r is not EndedReason.ERROR and f"EndedReason.{r.name}" not in src
    ]
    assert not missing, f"session.py 缺少出口插桩: {missing}"