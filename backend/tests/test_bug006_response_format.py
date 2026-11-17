"""BUG-006 离线验证：_call_llm 按模型条件传 response_format=json_object。

- deepseek-v3 (deepseek-chat) → stream_chat/chat 应收到 {"type": "json_object"}
- deepseek-r1 / reasoner → 不传（r1 不支持，会 400）
- 旧签名桩 stream_chat(messages) → 不传（兼容第三方/测试桩）
- ModelRouter（model_name="model-router"）→ 透传（底层 v3 支持）

全程 mock LLM，不发任何真实请求、不产生费用。
"""

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.engine.reactor.session import ReactorSession


class RecordingLLM:
    """记录调用参数的 mock LLM（新版签名，接受 response_format）。"""

    def __init__(self, model: str, mode: str = "stream"):
        self.model = model
        self.mode = mode
        self.calls: list[dict] = []

    async def stream_chat(self, messages, response_format=None, tools=None):
        self.calls.append({"response_format": response_format})
        yield {
            "content": '{"thought": "ok", "final": true, "output": {"summary": "done"}}',
            "reasoning_content": None,
            "is_final": True,
            "token_usage": 10,
        }

    async def chat(self, messages, response_format=None, max_tokens=None):
        self.calls.append({"response_format": response_format})
        return {
            "content": '{"thought": "ok", "final": true, "output": {"summary": "done"}}',
            "reasoning_content": None,
            "token_usage": 10,
        }


class OldSignatureLLM:
    """旧签名桩：stream_chat(messages) / chat(messages)——多传参应避免。"""

    def __init__(self, model: str = "deepseek-chat"):
        self.model = model
        self.calls: list[dict] = []

    async def stream_chat(self, messages):
        self.calls.append({"args": messages})
        yield {
            "content": '{"final": true, "output": {"summary": "done"}}',
            "reasoning_content": None,
            "is_final": True,
            "token_usage": 10,
        }

    async def chat(self, messages):
        self.calls.append({"args": messages})
        return {
            "content": '{"final": true, "output": {"summary": "done"}}',
            "reasoning_content": None,
            "token_usage": 10,
        }


class KwArgsLLM:
    """**kwargs 风格桩：应视为接受 response_format。"""

    def __init__(self, model: str = "deepseek-chat"):
        self.model = model
        self.calls: list[dict] = []

    async def stream_chat(self, messages, **kwargs):
        self.calls.append(kwargs)
        yield {
            "content": '{"final": true, "output": {"summary": "done"}}',
            "reasoning_content": None,
            "is_final": True,
            "token_usage": 10,
        }


def _session(llm) -> ReactorSession:
    return ReactorSession(llm=llm, system_prompt="test system prompt (json format)")


async def _drive(session) -> None:
    # emit_delta=False 走主循环路径；流式优先
    await session._call_llm([{"role": "user", "content": "hi"}], emit_delta=False)


def test_v3_stream_passes_json_object():
    llm = RecordingLLM("deepseek-chat")
    asyncio.run(_drive(_session(llm)))
    assert llm.calls, "stream_chat 未被调用"
    assert llm.calls[0]["response_format"] == {"type": "json_object"}, llm.calls


def test_r1_stream_does_not_pass():
    llm = RecordingLLM("deepseek-r1")
    asyncio.run(_drive(_session(llm)))
    assert llm.calls[0]["response_format"] is None, llm.calls


def test_reasoner_does_not_pass():
    llm = RecordingLLM("deepseek-reasoner")
    asyncio.run(_drive(_session(llm)))
    assert llm.calls[0]["response_format"] is None, llm.calls


def test_model_router_passes():
    llm = RecordingLLM("model-router")
    asyncio.run(_drive(_session(llm)))
    assert llm.calls[0]["response_format"] == {"type": "json_object"}, llm.calls


def test_old_signature_stub_not_broken():
    llm = OldSignatureLLM()
    asyncio.run(_drive(_session(llm)))
    assert llm.calls, "旧签名 stream_chat 调用失败（兼容性破坏）"


def test_kwargs_stub_receives_format():
    llm = KwArgsLLM()
    asyncio.run(_drive(_session(llm)))
    assert llm.calls[0].get("response_format") == {"type": "json_object"}, llm.calls


def test_fallback_chat_passes_json_object():
    """流式不可用时回退 chat 路径也应传 response_format。"""
    llm = RecordingLLM("deepseek-chat")
    session = _session(llm)
    # 打掉 stream_chat 强制走 chat 回退
    llm.stream_chat = None  # type: ignore[assignment]
    asyncio.run(_drive(session))
    assert llm.calls, "chat 回退未被调用"
    assert llm.calls[0]["response_format"] == {"type": "json_object"}, llm.calls


def test_response_format_helper_purity():
    """helper 本身的判定矩阵，不经过事件循环。"""
    s_v3 = _session(RecordingLLM("deepseek-chat"))
    assert s_v3._reactor_response_format() == {"type": "json_object"}
    s_r1 = _session(RecordingLLM("deepseek-r1"))
    assert s_r1._reactor_response_format() is None
    s_none = _session(None)
    assert s_none._reactor_response_format() is None
    assert ReactorSession._fn_accepts_response_format(OldSignatureLLM().stream_chat) is False
    assert ReactorSession._fn_accepts_response_format(KwArgsLLM().stream_chat) is True


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as e:
                failures += 1
                print(f"FAIL {name}: {e}")
    print("ALL PASS" if failures == 0 else f"{failures} FAILURES")
    sys.exit(1 if failures else 0)
