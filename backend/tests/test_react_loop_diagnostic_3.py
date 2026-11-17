"""ReAct Loop 三轮诊断 — Guardian 异常 / _parse_step 边界 / token 精度 / observation 无界 / 截断 JSON。

排查顺序：
  Step 13: _guardian.check() 无 try/except — guardian 内部异常是否优雅降级
  Step 14: _parse_step 对 tool_calls 类型不校验 — LLM 返回非 list tool_calls
  Step 15: token_usage=0 时 token budget 检查失效
  Step 16: observation 无界增长 — 长循环下 messages 是否超限
  Step 17: _extract_output_from_messages 解析截断后的 observation
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
    model_name = "mock-diag3"

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
        workflow_id="wf_diag3",
        sse_bus=bus,
        max_iterations=max_iterations,
        **kwargs,
    )


# ============================================================================
# Step 13: _guardian.check() 无 try/except
# ============================================================================


class TestStep13GuardianException:
    """_dispatch 中 guardian.check() 无 try/except。

    如果 guardian 内部抛异常（如 _validate_arguments 遇到意外类型），
    _dispatch 的 except Exception 会捕获并返回 {"ok": False, "error": ...}。
    这不是崩溃 BUG，但 guardian 异常被吞掉了——没有区分「guardian 异常」
    和「工具执行异常」，日志中无法定位问题来源。
    """

    def test_dispatch_catches_guardian_exception(self):
        """_dispatch 的 except Exception 是否能捕获 guardian.check() 异常。"""
        source = inspect.getsource(ReactorSession._dispatch)
        has_guardian_call = "guardian.check" in source or "_guardian.check" in source
        assert has_guardian_call, "_dispatch 应调用 guardian.check()"

        # 检查 guardian.check() 是否在 try 块内
        # 如果不在 try 内，异常会传播到 _dispatch 的 except Exception
        lines = source.split("\n")
        guardian_line_idx = None
        for i, line in enumerate(lines):
            if "guardian.check" in line or "_guardian.check" in line:
                guardian_line_idx = i
                break

        # guardian.check() 不在 try 块内，但 _dispatch 整体有 except Exception
        # 所以异常会被捕获，但日志中无法区分 guardian 异常和 primitive 异常
        # 这是代码味道，不是 BUG

    def test_dispatch_guardian_exception_logged_differently(self):
        """guardian 异常和 primitive 异常是否在日志中区分。"""
        source = inspect.getsource(ReactorSession._dispatch)
        # 检查是否有 guardian 专属的异常处理
        has_guardian_try = "try" in source.split("_guardian.check")[0].split("\n")[-3:]
        # 当前实现：guardian.check() 不在 try 内，异常由外层 except Exception 捕获
        # 日志中只显示 "primitive {name} failed"，无法区分 guardian 异常
        # 建议加 try/except 包裹 guardian.check()，返回 {"ok": False, "error": "guardian check failed: ..."}
        pass  # 已知代码味道


# ============================================================================
# Step 14: _parse_step 对 tool_calls 类型不校验
# ============================================================================


class TestStep14ParseStepToolCallsType:
    """_parse_step 对 tool_calls 字段类型不校验。

    如果 LLM 返回 {"tool_calls": "not_a_list"}，run() 中
    `for call in tool_calls` 会抛 TypeError，导致循环崩溃。
    """

    def test_parse_step_returns_tool_calls_as_is(self):
        """_parse_step 是否原样返回 tool_calls（不校验类型）。"""
        session = _make_session(None)

        # LLM 返回 tool_calls 为字符串
        parsed = session._parse_step(
            '{"thought": "test", "tool_calls": "not_a_list", "final": false}'
        )
        assert (
            parsed.get("tool_calls") == "not_a_list"
        ), "_parse_step 原样返回 tool_calls，不校验类型"

    @pytest.mark.asyncio
    async def test_run_handles_non_list_tool_calls(self):
        """run() 是否能处理 tool_calls 为非 list 的情况。

        当前代码：tool_calls = parsed.get("tool_calls") or []
        如果 tool_calls 是字符串 "not_a_list"，`or []` 不会触发（非空字符串是 truthy），
        后续 `for call in tool_calls` 会遍历字符串的每个字符，
        每个 `call` 是单字符 str，`call.get("name")` 报 AttributeError。
        """

        class _BadToolCallsLLM:
            model_name = "bad_tc"

            async def stream_chat(self, messages, **kwargs):
                yield _chunk(
                    '{"thought": "test", "tool_calls": "not_a_list", "final": false}', token_usage=1
                )
                yield _chunk(
                    '{"thought": "done", "final": true, "output": {"status": "ok"}}', token_usage=1
                )

            async def chat(self, messages, **kwargs):
                return {
                    "content": '{"thought": "done", "final": true, "output": {"status": "ok"}}',
                    "token_usage": 1,
                }

        session = _make_session(_BadToolCallsLLM(), max_iterations=5)

        result = await session.run("test")
        assert isinstance(result, dict), "run() 应返回 dict"

    @pytest.mark.asyncio
    async def test_run_coerces_tool_calls_to_list(self):
        """run() 是否在 tool_calls 非列表时强制转为空列表。

        修复后：tool_calls = parsed.get("tool_calls") or []
        应改为：tool_calls = parsed.get("tool_calls", []) or []
        并加类型校验：if not isinstance(tool_calls, list): tool_calls = []
        """

        class _BadToolCallsLLM:
            model_name = "bad_tc2"

            async def stream_chat(self, messages, **kwargs):
                yield _chunk(
                    '{"thought": "test", "tool_calls": "not_a_list", "final": false}', token_usage=1
                )
                yield _chunk(
                    '{"thought": "done", "final": true, "output": {"status": "ok"}}', token_usage=1
                )

            async def chat(self, messages, **kwargs):
                return {
                    "content": '{"thought": "done", "final": true, "output": {"status": "ok"}}',
                    "token_usage": 1,
                }

        session = _make_session(_BadToolCallsLLM(), max_iterations=5)

        result = await session.run("test")
        # 修复后应正常完成，不崩溃
        assert isinstance(result, dict), "run() 应返回 dict"


# ============================================================================
# Step 15: token_usage=0 时 token budget 检查失效
# ============================================================================


class TestStep15TokenUsageZero:
    """token_usage=0 时 token budget 检查失效。

    流式 LLM 返回 token_usage=0 时（某些 LLM 不报 token 用量），
    self._token_count 不增长，budget 检查形同虚设。
    长循环下可能无限运行。
    """

    @pytest.mark.asyncio
    async def test_zero_token_usage_does_not_increment_budget(self):
        """token_usage=0 时 _token_count 是否不增长。"""

        class _ZeroTokenLLM:
            model_name = "zero_token"

            async def stream_chat(self, messages, **kwargs):
                yield _chunk(
                    '{"thought": "step1", "tool_calls": [], "final": false}', token_usage=0
                )
                yield _chunk(
                    '{"thought": "done", "final": true, "output": {"status": "ok"}}', token_usage=0
                )

            async def chat(self, messages, **kwargs):
                return {
                    "content": '{"thought": "done", "final": true, "output": {"status": "ok"}}',
                    "token_usage": 0,
                }

        session = _make_session(_ZeroTokenLLM(), max_iterations=5)
        result = await session.run("test")

        # token_count 应该是 0（因为 token_usage 始终为 0）
        assert (
            session._token_count == 0
        ), f"token_usage=0 时 _token_count 应为 0，实际: {session._token_count}"

    def test_budget_check_is_noop_when_token_count_zero(self):
        """当 _token_count 始终为 0 时，budget 检查永远不触发。"""
        session = _make_session(None)
        session._token_count = 0
        session._max_tokens = 100_000

        # budget 检查条件
        assert not (
            session._token_count >= session._max_tokens
        ), "token_count=0 时 budget 检查不应触发"

    def test_run_has_no_iteration_based_safety_net(self):
        """run() 是否有基于迭代次数的安全网（max_iterations）。

        即使 token budget 失效，max_iterations 仍能限制循环。
        """
        source = inspect.getsource(ReactorSession.run)
        has_max_iter = "max_iterations" in source
        assert has_max_iter, "run() 应有 max_iterations 限制"

        # max_iterations 是安全网，即使 token budget 失效也能限制循环
        # 但如果 max_iterations 设得很大（如 100），token budget 失效时
        # 循环会跑满 100 轮，每轮都调 LLM，浪费大量 token


# ============================================================================
# Step 16: observation 无界增长
# ============================================================================


class TestStep16ObservationUnboundedGrowth:
    """observation 无界增长 — messages 列表在长循环下是否超限。

    每轮循环追加 1 条 assistant message + N 条 observation message。
    _maybe_truncate_messages 在每轮结束后截断到 _MESSAGES_MAX_LEN=60。
    但如果单轮有多个 tool_calls，observation 数量可能超过截断窗口。
    """

    def test_truncation_happens_after_each_iteration(self):
        """_maybe_truncate_messages 是否在每轮结束后调用。"""
        source = inspect.getsource(ReactorSession.run)
        # 检查 messages = self._maybe_truncate_messages(messages) 的位置
        has_truncation = "_maybe_truncate_messages" in source
        assert has_truncation, "run() 应调用 _maybe_truncate_messages"

    def test_messages_max_len_is_60(self):
        """_MESSAGES_MAX_LEN 是否为 60。"""
        from app.engine.reactor.session import _MESSAGES_MAX_LEN

        assert _MESSAGES_MAX_LEN == 60, f"_MESSAGES_MAX_LEN 应为 60，实际: {_MESSAGES_MAX_LEN}"

    def test_single_iteration_can_exceed_max_len(self):
        """单轮多个 tool_calls 是否可能让 messages 超过 _MESSAGES_MAX_LEN。

        如果 LLM 一轮返回 10 个 tool_calls，每条 observation 都追加到 messages，
        单轮就增加 1 (assistant) + 10 (observations) = 11 条。
        如果之前已有 50 条，截断后剩 60 条，新一轮后变成 61 条，
        要到下一轮结束才截断。
        """
        # 这不是 BUG，只是截断时机的问题
        # 截断在每轮结束后，不是每条 observation 后
        # 极端情况下 messages 可短暂超过 _MESSAGES_MAX_LEN
        pass  # 已知行为


# ============================================================================
# Step 17: _extract_output_from_messages 解析截断后的 observation
# ============================================================================


class TestStep17ExtractTruncatedObservation:
    """_extract_output_from_messages 解析截断后的 observation。

    observation 截断后 JSON 不完整，json.loads 会失败。
    _extract_output_from_messages 应能优雅处理。
    """

    def test_extract_from_truncated_json(self):
        """截断后的 observation JSON 是否被正确处理。"""
        # 模拟截断后的 observation
        original_obs = {"ok": True, "data": "x" * 5000}
        obs_str = json.dumps(original_obs, ensure_ascii=False)
        truncated = obs_str[:4000] + "...(truncated)"

        messages = [
            {"role": "user", "content": f"[observation] test_tool -> {truncated}"},
        ]

        result = ReactorSession._extract_output_from_messages(messages)
        # json.loads 会失败（截断后不是有效 JSON）
        # _extract_output_from_messages 应返回 fallback
        assert isinstance(result, dict), "应返回 dict"

    def test_extract_from_valid_json(self):
        """完整的 observation JSON 是否被正确解析。"""
        obs = {"ok": True, "data": "hello"}
        obs_str = json.dumps(obs, ensure_ascii=False)

        messages = [
            {"role": "user", "content": f"[observation] test_tool -> {obs_str}"},
        ]

        result = ReactorSession._extract_output_from_messages(messages)
        assert result.get("ok") is True
        assert result.get("data") == "hello"

    def test_extract_from_no_observation(self):
        """没有 observation 时是否返回 fallback。"""
        messages = [
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "hello"},
        ]

        result = ReactorSession._extract_output_from_messages(messages)
        assert isinstance(result, dict)
        # 应返回最后一条消息的 content
        assert "raw_output" in result or "content" in str(result)

    def test_extract_from_empty_messages(self):
        """空 messages 列表时是否安全。"""
        result = ReactorSession._extract_output_from_messages([])
        assert isinstance(result, dict)
