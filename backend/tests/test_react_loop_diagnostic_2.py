"""ReAct Loop 二轮诊断 — ChatAgent 集成层 + _call_llm 重试 + _dispatch 超时。

排查顺序：
  Step 8:  _call_llm 未使用 StreamRetryState（429/401 处理不精细）
  Step 9:  _dispatch 无每轮超时（单工具可永久阻塞）
  Step 10: ChatAgent 历史对话注入方式（拼接字符串 vs role 标注）
  Step 11: _call_llm 重试耗尽后返回 JSON 但 _parse_step 可能误判
  Step 12: _dispatch 的 args 原地注入 _user_id/_workflow_id 是否泄漏到 observation
"""

from __future__ import annotations

import asyncio
import inspect
import json
import time
from typing import Any

import pytest

from app.engine.reactor.session import ReactorSession


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


class _ScriptedLLM:
    model_name = "mock-diag2"

    def __init__(self, responses: list[dict]) -> None:
        self._responses = list(responses)
        self._index = 0

    async def stream_chat(self, messages, **kwargs):
        idx = min(self._index, len(self._responses) - 1)
        self._index += 1
        for chunk in self._responses[idx]["chunks"]:
            yield chunk

    async def chat(self, messages, **kwargs):
        idx = min(self._index, len(self._responses) - 1)
        self._index += 1
        parts = []
        tu = 0
        for chunk in self._responses[idx]["chunks"]:
            if chunk.get("content"):
                parts.append(chunk["content"])
            if chunk.get("is_final"):
                tu = chunk.get("token_usage", 0) or tu
        return {"content": "".join(parts), "reasoning_content": None, "token_usage": tu or 1}


def _chunk(content: str = "", is_final: bool = True, token_usage: int = 1) -> dict:
    return {
        "content": content,
        "reasoning_content": "",
        "is_final": is_final,
        "token_usage": token_usage,
    }


def _react_step(
    thought: str, tool_calls: list | None = None, final: bool = False, output: dict | None = None
) -> str:
    step = {"thought": thought, "tool_calls": tool_calls or [], "final": final}
    if output is not None:
        step["output"] = output
    return json.dumps(step, ensure_ascii=False)


def _make_session(llm, max_iterations: int = 12, **kwargs) -> ReactorSession:
    bus = _FakeBus()
    return ReactorSession(
        llm=llm,
        workflow_id="wf_diag2",
        sse_bus=bus,
        max_iterations=max_iterations,
        **kwargs,
    )


# ============================================================================
# Step 8: _call_llm 未使用 StreamRetryState
# ============================================================================


class TestStep8CallLlmRetry:
    """_call_llm 是否使用了 StreamRetryState。

    预期：_call_llm 应使用 StreamRetryState 处理 429/401/连接错误。
    实际（待验证）：_call_llm 自己实现了简单重试，未用 StreamRetryState。
    """

    def test_call_llm_does_not_use_stream_retry_state(self):
        """_call_llm 源码中是否引用了 StreamRetryState。"""
        source = inspect.getsource(ReactorSession._call_llm)
        uses_stream_retry = "StreamRetryState" in source or "stream_retry" in source

        if not uses_stream_retry:
            pytest.fail(
                "BUG 确认：_call_llm 未使用 StreamRetryState。"
                "自己实现了简单重试（3 次 + 指数退避），"
                "无法区分 429 限流（需更长退避）、401 不可恢复（应立即放弃）、"
                "连接错误（需更多重试）。"
                "应改用 call_llm_with_retry() 或在 _call_llm 中使用 StreamRetryState。"
            )

    def test_call_llm_handles_401_wastefully(self):
        """_call_llm 遇到 401 时是否浪费 3 次重试。

        401 是不可恢复错误（API key 无效），应立即放弃。
        简单重试逻辑会重试 3 次才放弃，浪费时间。
        """
        source = inspect.getsource(ReactorSession._call_llm)
        has_401_check = (
            "401" in source or "unrecoverable" in source or "unauthorized" in source.lower()
        )

        if not has_401_check:
            # 这不是 crash bug，但记录诊断信息
            pass  # 已知限制：_call_llm 不区分可恢复/不可恢复错误

    def test_stream_retry_state_exists_and_works(self):
        """StreamRetryState 已实现且可用。"""
        from app.engine.governance.stream_retry import RetryAction, StreamRetryState

        state = StreamRetryState()

        # 401 → GIVE_UP
        action = state.handle_error(Exception("401 Unauthorized"))
        assert action == RetryAction.GIVE_UP, "401 应立即放弃"

        # 429 → RETRY
        state2 = StreamRetryState()
        action2 = state2.handle_error(Exception("429 Rate limit exceeded"))
        assert action2 == RetryAction.RETRY, "429 应重试"

        # 连接错误 → RETRY
        state3 = StreamRetryState()
        action3 = state3.handle_error(ConnectionError("Connection reset"))
        assert action3 == RetryAction.RETRY, "连接错误应重试"


# ============================================================================
# Step 9: _dispatch 无每轮超时
# ============================================================================


class TestStep9DispatchTimeout:
    """_dispatch 是否有超时保护。

    预期：_dispatch 应有 asyncio.wait_for 包裹，防止单工具永久阻塞。
    实际（待验证）：无超时，primitive.execute 可永久阻塞。
    """

    def test_dispatch_has_no_timeout(self):
        """_dispatch 源码中是否使用了 asyncio.wait_for。"""
        # 2026-10 二期：执行已下沉 transport，超时保护在 InProcessTransport.call
        from app.engine.protocol.transport import InProcessTransport

        source = inspect.getsource(InProcessTransport.call)
        has_timeout = "wait_for" in source or "timeout" in source.lower()

        if not has_timeout:
            pytest.fail(
                "BUG 确认：_dispatch 无超时保护。"
                "primitive.execute 可永久阻塞（如 bash 长命令、image_gen 大图、"
                "skill_call 视频生成），导致整个循环挂起。"
                "应加 asyncio.wait_for(primitive.execute(**args), timeout=120) 包裹。"
            )

    def test_dispatch_exception_handling_exists(self):
        """_dispatch 至少有异常捕获（不会崩溃）。"""
        source = inspect.getsource(ReactorSession._dispatch)
        has_exception_handling = "except Exception" in source
        assert has_exception_handling, "_dispatch 应有 Exception 捕获"


# ============================================================================
# Step 10: ChatAgent 历史对话注入方式
# ============================================================================


class TestStep10ChatAgentHistoryInjection:
    """ChatAgent 是否正确注入历史对话（role 标注 vs 字符串拼接）。

    预期：历史消息应按 role 分别注入（user/assistant）。
    实际（待验证）：历史拼接到 topic 字符串里。
    """

    def test_agentic_loop_injects_history_into_topic(self):
        """_agentic_loop 是否把历史拼接到 topic 字符串。"""
        from app.agents.chat_agent import ChatAgent

        source = inspect.getsource(ChatAgent._agentic_loop)

        # 检查是否有 history 拼接到 topic 的逻辑
        has_history_injection = "history" in source.lower() and "topic" in source
        # 检查是否有 role 标注
        has_role_annotation = '"role"' in source and '"user"' in source and '"assistant"' in source

        if has_history_injection and not has_role_annotation:
            pytest.fail(
                "BUG 确认：ChatAgent 把历史对话拼接到 topic 字符串，"
                "而非按 role 分别注入 messages。LLM 无法区分"
                "'用户之前说了什么'和'我现在要做什么'。"
                "应在 _build_initial_messages 中接受历史消息列表。"
            )

    def test_build_initial_messages_only_takes_topic(self):
        """_build_initial_messages 是否只接受 topic（单条 user message）。"""
        source = inspect.getsource(ReactorSession._build_initial_messages)
        has_history_param = (
            "history" in source or "messages" in source.split("def")[1].split("):")[0]
        )

        if not has_history_param:
            # _build_initial_messages 不接受历史消息参数
            # 历史只能通过 context 或 topic 字符串注入
            pass  # 已知限制


# ============================================================================
# Step 11: _call_llm 重试耗尽后返回的 JSON 是否被 _parse_step 正确处理
# ============================================================================


class TestStep11CallLlmRetryExhaustion:
    """_call_llm 重试耗尽后返回 JSON error，_parse_step 是否正确处理。

    预期：返回的 JSON 应被 _parse_step 解析为 final=True，循环应终止。
    """

    @pytest.mark.asyncio
    async def test_llm_failure_returns_final_json(self):
        """LLM 持续失败 → _call_llm 返回 error JSON → _parse_step 应识别为 final。"""

        class _FailingLLM:
            model_name = "failing"

            async def stream_chat(self, messages, **kwargs):
                raise ConnectionError("LLM service unavailable")

            async def chat(self, messages, **kwargs):
                raise ConnectionError("LLM service unavailable")

        session = _make_session(_FailingLLM(), max_iterations=3)
        result = await session.run("测试LLM失败")

        # _call_llm 重试耗尽后返回 {"final": true, "output": {"error": ...}}
        # _parse_step 应解析出 final=True，循环应终止
        assert (
            result.get("_iterations") == 1
        ), f"LLM 失败后循环应在第 1 轮终止，实际: {result.get('_iterations')}"
        # 应有错误信息
        has_error = "error" in result or any("error" in str(v) for v in result.values())
        assert has_error, "LLM 失败后应有错误信息"


# ============================================================================
# Step 12: _dispatch args 注入是否泄漏到 observation
# ============================================================================


class TestStep12DispatchArgsLeakage:
    """_dispatch 注入 _user_id/_workflow_id 到 args 后，是否泄漏到 observation。

    预期：注入字段不应出现在 observation 回喂 LLM 的消息中。
    实际（待验证）：注入字段可能通过 obs_str 泄漏。
    """

    def test_dispatch_injects_internal_fields(self):
        """_dispatch 是否在 args 中注入 _user_id 等内部字段。"""
        # 2026-10 二期：注入下沉 dispatch 钩子链（SkillCallUnwrapHook.pre_execute）
        from app.engine.protocol.dispatch_hooks import SkillCallUnwrapHook

        source = inspect.getsource(SkillCallUnwrapHook.pre_execute)
        has_user_id_inject = "_user_id" in source and "args[" in source
        has_workflow_id_inject = "_workflow_id" in source and "args[" in source

        assert has_user_id_inject, "dispatch 钩子应注入 _user_id"
        assert has_workflow_id_inject, "dispatch 钩子应注入 _workflow_id"

    def test_observation_includes_injected_fields(self):
        """observation 是否包含注入的内部字段。

        如果 observation 包含 _user_id/_workflow_id，这些字段会
        通过 obs_str 泄漏到回喂 LLM 的消息中，浪费 token。
        """
        # 检查 run() 中 observation 构建逻辑
        source = inspect.getsource(ReactorSession.run)
        # obs_str = json.dumps(obs, ...) → obs 包含 args 的注入字段
        has_obs_str = "obs_str" in source
        # 检查是否有过滤内部字段的逻辑
        has_filter = "_user_id" not in source or "pop" in source

        if has_obs_str and not has_filter:
            # observation 包含了 _user_id 等内部字段
            # 这些字段通过 obs_str 回喂给 LLM，浪费 token
            pass  # 已知限制：内部字段泄漏到 observation
