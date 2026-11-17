"""reactor _call_llm 重试可见性测试（不打真实网络）。

背景（2026-11-07）：LLM 调用失败重试期间用户端完全静默，3 分钟无回复。
契约：
  1. RETRY 分支 → 发射 llm_retry_status SSE（含 attempt/delay/label）
  2. FALLBACK 分支 → 同样发射 llm_retry_status（切换备用通道）
  3. GIVE_UP 分支 → 返回的 final JSON 带**中文友好**错误信息（不再是
     英文异常原文 "LLM call failed: ..."）
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.engine.reactor import session as session_mod  # noqa: E402
from app.engine.reactor.session import ReactorSession  # noqa: E402


async def _no_sleep(_seconds: float) -> None:
    return None


@pytest.fixture
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """重试退避归零，避免测试真实等待（最长退避可达 60s）。"""
    monkeypatch.setattr(session_mod.asyncio, "sleep", _no_sleep)


class _FakeBus:
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


def _chunk(content: str = "", is_final: bool = True, token_usage: int = 1) -> dict:
    return {
        "content": content,
        "reasoning_content": "",
        "is_final": is_final,
        "token_usage": token_usage,
    }


def _make_session(llm) -> tuple[ReactorSession, _FakeBus]:
    bus = _FakeBus()
    sess = ReactorSession(
        llm=llm,
        workflow_id="wf_retry_sse",
        sse_bus=bus,
        max_iterations=3,
    )
    return sess, bus


class _FlakyThenOkLLM:
    """stream_chat 第 1 次抛连接错误，之后正常返回。"""

    model_name = "flaky-ok"

    def __init__(self) -> None:
        self.calls = 0

    async def stream_chat(self, messages, **kwargs):
        self.calls += 1
        if self.calls == 1:
            raise ConnectionError("connection reset by peer")
        yield _chunk(content='{"thought": "ok", "final": true, "output": {"done": true}}')

    async def chat(self, messages, **kwargs):
        return {
            "content": '{"thought": "ok", "final": true, "output": {"done": true}}',
            "token_usage": 1,
        }


class _AlwaysFailingLLM:
    model_name = "always-fail"

    async def stream_chat(self, messages, **kwargs):
        raise ConnectionError("connection timed out")
        yield _chunk()  # noqa: unreachable — 使本函数成为 async generator

    async def chat(self, messages, **kwargs):
        raise ConnectionError("connection timed out")


@pytest.mark.asyncio
async def test_retry_emits_llm_retry_status_sse(no_sleep: None):
    """RETRY 分支：发射 llm_retry_status，payload 带 attempt/delay/中文 label。"""
    sess, bus = _make_session(_FlakyThenOkLLM())

    content, usage, reasoning = await sess._call_llm([{"role": "user", "content": "hi"}])

    # 最终成功
    assert "final" in content
    # 必须发射过 llm_retry_status 事件
    retry_events = [c for c in bus.calls if c["event_type"] == "llm_retry_status"]
    assert (
        len(retry_events) >= 1
    ), f"LLM 重试时应发射 llm_retry_status SSE，实际事件: {[c['event_type'] for c in bus.calls]}"
    payload = retry_events[0]["payload"]
    assert payload.get("attempt") == 1
    assert payload.get("delay", 0) > 0
    assert "重试" in payload.get("label", ""), "label 必须是用户可读的中文提示"


@pytest.mark.asyncio
async def test_give_up_returns_friendly_chinese_error(no_sleep: None):
    """GIVE_UP 分支：final JSON 里的错误信息是中文友好文案，不是英文异常原文。"""
    sess, bus = _make_session(_AlwaysFailingLLM())

    content, usage, reasoning = await sess._call_llm([{"role": "user", "content": "hi"}])

    data = json.loads(content)
    assert data.get("final") is True
    err_text = str(data.get("output", {}).get("error", ""))
    assert (
        "模型服务" in err_text or "暂时不可用" in err_text
    ), f"重试耗尽后应返回中文友好错误，实际: {err_text!r}"
    # 前端可展示的 SSE 错误提示也应发射（llm_retry_status 终态）
    fail_events = [
        c for c in bus.calls if c["event_type"] == "llm_retry_status" and c["payload"].get("final")
    ]
    assert len(fail_events) >= 1, "重试耗尽后应发射一条 final 的 llm_retry_status 提示"
