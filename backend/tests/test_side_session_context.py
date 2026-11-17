"""侧会话上下文构建测试（Task 4）。

验证 ChatAgent 在 kind=side 会话中：
1. system prompt 注入快照块（基准轮次/目标/结论/快照对话）；
2. 上下文历史只来自侧会话自身消息（list_messages 以侧会话 id 调用）；
3. 非侧会话不受影响（get_snapshot 返回 None 时不注入）。
"""

import asyncio
import os
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite://")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-side-session")

from app.agents.chat_agent import ChatAgent
from app.engine.reactor.session import ReactorSession
from app.services import chat_session, side_session


class _ScriptedLLM:
    model_name = "mock-chat"

    def __init__(
        self, response: str = '{"thought": "完成", "final": true, "output": {"result": "done"}}'
    ):
        self._response = response

    async def chat(self, messages, response_format=None):
        return {"content": self._response, "reasoning_content": None, "token_usage": 1}


class _RecordingSession(ReactorSession):
    """记录 run 收到的 input_data 与 context_manager。"""

    def __init__(self, llm=None):
        self.llm = llm
        self.primitives = {}
        self.allowed_permissions = set()
        self.max_iterations = 4
        self.system_prompt = "你是小红书创作助手。"
        self.observer = None
        self.sse_bus = None
        self.workflow_id = ""
        self.auto_approve = True
        self._guardian = None
        self.turn_id = None
        self.last_input = None
        self.last_cm = None

    async def run(self, message, context=None, context_manager=None):
        self.last_input = dict(context or {})
        self.last_cm = context_manager
        return {"result": "done"}


class _FakeRegistry:
    def __init__(self, session):
        self._session = session

    def build_session(self, agent_id, workflow_id=None, creation_type=None, auto_approve=True):
        return self._session


_SIDE_SNAPSHOT = {
    "session_id": "side1",
    "baseline_message_id": "01BASELINE",
    "granularity": "recent_n",
    "recent_n": 10,
    "content": {
        "goal": "做一份AI教育方案",
        "summary": "已确定选题方向",
        "messages": [{"role": "user", "content": "主任务里聊过的内容"}],
        "baseline_turn": 3,
    },
    "token_level": "low",
}


def _run_chat(monkeypatch, session, *, is_side: bool, snapshot=None, side_messages=None):
    captured: dict = {}

    async def fake_list_messages(session_id, limit=200):
        captured["list_messages_session_id"] = session_id
        return side_messages or []

    async def fake_summary(session_id):
        return None

    async def fake_creative_state(session_id):
        return None

    async def fake_get_snapshot(sid):
        captured["get_snapshot_session_id"] = sid
        return snapshot

    async def fake_last_sid(session_id):
        return None

    async def fake_update_state(session_id, snap):
        return None

    monkeypatch.setattr(chat_session, "list_messages", fake_list_messages)
    monkeypatch.setattr(chat_session, "get_session_summary", fake_summary)
    monkeypatch.setattr(chat_session, "get_creative_state", fake_creative_state)
    monkeypatch.setattr(chat_session, "get_last_session_id", fake_last_sid)
    monkeypatch.setattr(chat_session, "update_creative_state", fake_update_state)
    monkeypatch.setattr(side_session, "get_snapshot", fake_get_snapshot)

    agent = ChatAgent(registry=_FakeRegistry(session), llm=_ScriptedLLM())
    # db 传哨兵对象（非 None）以触发上下文注入块；块内 DB 依赖均已被 patch
    return (
        asyncio.run(
            agent.process("侧会话提问", "side1" if is_side else "main1", "u1", db=object())
        ),
        captured,
    )


def test_side_session_system_prompt_contains_snapshot(monkeypatch):
    session = _RecordingSession(llm=_ScriptedLLM())
    result, _ = _run_chat(
        monkeypatch,
        session,
        is_side=True,
        snapshot=_SIDE_SNAPSHOT,
        side_messages=[{"id": "m1", "role": "user", "content": "侧会话自己的消息"}],
    )
    assert session.last_cm is not None
    sp = session.last_cm._system_prompt
    assert "侧会话背景" in sp
    assert "第 3 轮" in sp
    assert "做一份AI教育方案" in sp
    assert "已确定选题方向" in sp
    assert "主任务里聊过的内容" in sp
    assert "你是小红书创作助手。" in sp


def test_side_session_history_is_only_own_messages(monkeypatch):
    session = _RecordingSession(llm=_ScriptedLLM())
    _run_chat(
        monkeypatch,
        session,
        is_side=True,
        snapshot=_SIDE_SNAPSHOT,
        side_messages=[{"id": "m1", "role": "user", "content": "侧会话自己的消息"}],
    )
    items = [(i.role, i.content) for i in session.last_cm.history._items]
    assert ("user", "侧会话自己的消息") in items
    assert (
        "user",
        "主任务里聊过的内容",
    ) not in items  # 主会话内容只经快照进 system prompt，不进历史


def test_side_session_list_messages_called_with_side_id(monkeypatch):
    session = _RecordingSession(llm=_ScriptedLLM())
    _, captured = _run_chat(
        monkeypatch,
        session,
        is_side=True,
        snapshot=_SIDE_SNAPSHOT,
        side_messages=[{"id": "m1", "role": "user", "content": "侧会话自己的消息"}],
    )
    assert captured["list_messages_session_id"] == "side1"
    assert captured["get_snapshot_session_id"] == "side1"


def test_main_session_not_affected_by_snapshot(monkeypatch):
    session = _RecordingSession(llm=_ScriptedLLM())
    _run_chat(
        monkeypatch,
        session,
        is_side=False,
        snapshot=None,
        side_messages=[{"id": "m1", "role": "user", "content": "主会话消息"}],
    )
    assert session.last_cm is not None
    assert "侧会话背景" not in session.last_cm._system_prompt


def test_side_write_pause_maps_to_awaiting_status(monkeypatch):
    """Reactor 返回写暂停信号 → ChatAgent 映射为 awaiting_side_write_confirmation（review #3）。"""

    class _PausingSession(_RecordingSession):
        async def run(self, message, context=None, context_manager=None):
            self.last_cm = context_manager
            return {
                "ok": False,
                "_awaiting_side_write_confirmation": True,
                "_loop_state_path": "loop_abc",
                "side_write": {"tool_name": "file_write", "permissions": ["file_write"]},
            }

    session = _PausingSession(llm=_ScriptedLLM())
    result, _ = _run_chat(monkeypatch, session, is_side=False, snapshot=None)
    assert result.status == "awaiting_side_write_confirmation"
    assert result.output.output["_loop_state_path"] == "loop_abc"
