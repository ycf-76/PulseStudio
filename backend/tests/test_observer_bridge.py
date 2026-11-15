import asyncio

from app.engine.factory import _make_observer
from app.services.sse_bus import sse_bus


def _patch_publish(monkeypatch, calls: list):
    async def fake_publish(workflow_id: str, event_type: str, payload: dict) -> str:
        calls.append((workflow_id, event_type, payload))
        return "evt_test"

    monkeypatch.setattr(sse_bus, "publish", fake_publish)


def test_observer_bridge_publishes_decision(monkeypatch):
    """emit_llm_stream 已随 loop 重构退役（session 不再发 LLM 流桥接）；
    改测 session 现用的 emit_decision 桥。"""
    calls: list = []
    _patch_publish(monkeypatch, calls)

    observer = _make_observer("wf_1")
    asyncio.run(observer.emit_decision("node1", "决定调工具"))

    assert calls == [
        ("wf_1", "decision_made", {"node_id": "node1", "decision": "决定调工具"})
    ]


def test_observer_bridge_publishes_tool_call_recovery_circuit(monkeypatch):
    calls: list = []
    _patch_publish(monkeypatch, calls)

    observer = _make_observer("wf_1")

    async def run():
        await observer.emit_tool_call_start("node1", "xhs_search", {"keyword": "AI"})
        await observer.emit_tool_call_end("node1", "xhs_search", True, "搜索到 3 条")
        await observer.emit_progress("node1", 1, 3, "第 1 步")

    asyncio.run(run())

    assert ("wf_1", "tool_call_start", {"node_id": "node1", "tool_name": "xhs_search", "inputs": {"keyword": "AI"}}) in calls
    assert ("wf_1", "tool_call_end", {"node_id": "node1", "tool_name": "xhs_search", "success": True, "summary": "搜索到 3 条"}) in calls
    assert ("wf_1", "progress_update", {"node_id": "node1", "current": 1, "total": 3, "label": "第 1 步"}) in calls
