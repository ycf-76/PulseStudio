"""ReAct Loop 四轮诊断 — 用户报告的 P0/P1 问题验证。

  Step 18: /tmp/ 硬编码 Windows 路径 (P0-1)
  Step 19: clarification/storyboard break 时 tool_call_start 无配对 tool_call_end (P0-2)
  Step 20: _extract_json_from_text 不处理字符串内花括号 (P1-4)
  Step 21: _maybe_truncate_messages 切断 assistant-observation 配对 (P1-5)
"""

from __future__ import annotations

import inspect
import json
import os
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, AsyncMock

import pytest

from app.engine.reactor.session import ReactorSession


class _FakeBus:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def publish(self, workflow_id, event_type, payload, turn_id=None):
        self.calls.append(
            {"workflow_id": workflow_id, "event_type": event_type, "payload": payload, "turn_id": turn_id}
        )
        return "evt_fake"


# ============================================================================
# Step 18: /tmp/ 硬编码 Windows 路径
# ============================================================================


class TestStep18TmpPath:
    """_STATE_DIR 默认值是否跨平台。

    Windows 上应使用 %TEMP% 或 %TMP%，Linux/macOS 上应使用 /tmp 或 $TMPDIR。
    """

    def test_state_dir_uses_platform_temp(self):
        """_STATE_DIR 默认值是否使用平台临时目录。"""
        from app.engine.reactor.loop_state import _STATE_DIR

        state_dir_str = str(_STATE_DIR)
        assert "reactor_loop_states" in state_dir_str

        if sys.platform == "win32":
            _tmp = os.environ.get("TEMP") or os.environ.get("TMP")
            if _tmp:
                assert _tmp.replace("\\", "/") in state_dir_str.replace("\\", "/"), (
                    f"Windows 上 _STATE_DIR 应使用 %TEMP%/{_tmp}，实际: {state_dir_str}"
                )
        else:
            _tmpdir = os.environ.get("TMPDIR")
            if _tmpdir:
                assert _tmpdir in state_dir_str or "/tmp" in state_dir_str

    def test_state_dir_respects_env_override(self):
        """REACTOR_STATE_DIR 环境变量是否覆盖默认值。"""
        import importlib
        import app.engine.reactor.loop_state as mod

        original = os.environ.get("REACTOR_STATE_DIR")
        try:
            os.environ["REACTOR_STATE_DIR"] = "/custom/state/dir"
            importlib.reload(mod)
            assert str(mod._STATE_DIR).replace("\\", "/") == "/custom/state/dir"
        finally:
            if original is None:
                os.environ.pop("REACTOR_STATE_DIR", None)
            else:
                os.environ["REACTOR_STATE_DIR"] = original
            importlib.reload(mod)

    def test_state_dir_is_not_hardcoded_tmp(self):
        """_STATE_DIR 不应硬编码为 /tmp/...（除非 Linux 无 TEMP 环境变量）。"""
        from app.engine.reactor.loop_state import _STATE_DIR

        state_dir_str = str(_STATE_DIR)
        if sys.platform == "win32":
            _tmp = os.environ.get("TEMP") or os.environ.get("TMP")
            if _tmp:
                assert not state_dir_str.startswith("/tmp"), (
                    f"Windows 上 _STATE_DIR 不应硬编码为 /tmp，实际: {state_dir_str}"
                )


# ============================================================================
# Step 19: clarification/storyboard break 时 tool_call_start 无配对 tool_call_end
# ============================================================================


class TestStep19SSEEventPairing:
    """clarification break 和 storyboard confirmation break 时，
    tool_call_start 是否有配对的 tool_call_end。
    """

    def test_clarification_break_emits_tool_call_end(self):
        """clarification break 路径是否在 break 前发 tool_call_end。"""
        source = inspect.getsource(ReactorSession.run)

        # 找 clarification break 代码块
        clarification_block_start = source.find("_awaiting_clarification")
        assert clarification_block_start != -1, "应存在 clarification break 逻辑"

        # 找 clarification break 中的 break 语句
        after_clarification = source[clarification_block_start:]
        break_pos = after_clarification.find("self._clarification_break = True")
        assert break_pos != -1

        # 检查 break 前（0 到 break_pos）是否有 tool_call_end
        block_before_break = after_clarification[:break_pos]
        has_tool_call_end = "tool_call_end" in block_before_break
        assert has_tool_call_end, (
            "BUG：clarification break 时 tool_call_start 已发但无配对 tool_call_end，"
            "前端进度条永远转"
        )

    def test_storyboard_break_emits_tool_call_end(self):
        """storyboard confirmation break 路径是否在 break 前发 tool_call_end。"""
        source = inspect.getsource(ReactorSession.run)

        storyboard_block_start = source.find("needs_confirmation")
        assert storyboard_block_start != -1, "应存在 storyboard confirmation break 逻辑"

        after_storyboard = source[storyboard_block_start:]
        break_pos = after_storyboard.find("self._clarification_break = True")
        if break_pos == -1:
            break_pos = after_storyboard.find("break")

        block_before_break = after_storyboard[:break_pos]
        has_tool_call_end = "tool_call_end" in block_before_break
        assert has_tool_call_end, (
            "BUG：storyboard confirmation break 时 tool_call_start 已发但无配对 tool_call_end，"
            "前端进度条永远转"
        )

    def test_normal_dispatch_emits_tool_call_end(self):
        """正常 dispatch 路径是否发 tool_call_end。"""
        source = inspect.getsource(ReactorSession.run)
        # 正常路径（非 break）应有 tool_call_end
        assert "tool_call_end" in source, "正常 dispatch 路径应发 tool_call_end"


# ============================================================================
# Step 20: _extract_json_from_text 不处理字符串内花括号
# ============================================================================


class TestStep20ExtractJsonFromStringBraces:
    """_extract_json_from_text 是否能正确处理字符串内的花括号。"""

    def test_json_with_braces_in_string(self):
        """LLM 输出中 thought 包含 {key} 格式时，是否能正确解析。"""
        text = 'Some text before {"thought": "use {key} format", "tool_calls": [], "final": false} some text after'
        result = ReactorSession._extract_json_from_text(text)
        assert result is not None, "应能从文本中提取 JSON"
        assert result.get("thought") == "use {key} format"
        assert result.get("tool_calls") == []
        assert result.get("final") is False

    def test_json_with_nested_braces_in_string(self):
        """LLM 输出中 thought 包含花括号（非 JSON 嵌套，只是文本中的花括号）。"""
        text = 'Here is the plan: {"thought": "use {key: val} format", "tool_calls": [], "final": true}'
        result = ReactorSession._extract_json_from_text(text)
        assert result is not None, "应能从文本中提取 JSON"
        assert result.get("final") is True

    def test_json_with_no_string_braces(self):
        """正常 JSON（无字符串内花括号）仍能正确解析。"""
        text = '{"thought": "normal", "tool_calls": [{"name": "bash", "arguments": {"command": "ls"}}], "final": false}'
        result = ReactorSession._extract_json_from_text(text)
        assert result is not None
        assert result.get("thought") == "normal"
        assert len(result.get("tool_calls", [])) == 1

    def test_no_json_in_text(self):
        """文本中无 JSON 时返回 None。"""
        text = "This is just plain text with no JSON at all."
        result = ReactorSession._extract_json_from_text(text)
        assert result is None

    def test_multiple_json_candidates(self):
        """文本中有多个候选 JSON 时，返回第一个有效的。"""
        text = 'First {not valid} then {"thought": "second", "final": true}'
        result = ReactorSession._extract_json_from_text(text)
        assert result is not None
        assert result.get("thought") == "second"


# ============================================================================
# Step 21: _maybe_truncate_messages 切断 assistant-observation 配对
# ============================================================================


class TestStep21TruncationPairing:
    """_maybe_truncate_messages 是否保护 assistant-observation 配对。"""

    def test_truncation_does_not_orphan_observation(self):
        """截断后第一条非 system 消息不应是孤立的 observation。"""
        from app.engine.reactor.session import _MESSAGES_MAX_LEN

        session = ReactorSession.__new__(ReactorSession)

        messages = [{"role": "system", "content": "system prompt"}]
        for i in range(50):
            messages.append({"role": "assistant", "content": f"thought {i}"})
            messages.append({"role": "user", "content": f"[observation] tool_{i} -> {{\"ok\": true}}"})

        messages.append({"role": "assistant", "content": "thought 50"})
        messages.append({"role": "user", "content": "[observation] tool_50 -> {\"ok\": true}"})

        truncated = session._maybe_truncate_messages(messages)

        non_system = [m for m in truncated if m.get("role") != "system"]
        if non_system:
            first = non_system[0]
            is_orphan_obs = (
                first.get("role") == "user"
                and str(first.get("content", "")).startswith("[observation]")
            )
            assert not is_orphan_obs, (
                "截断后第一条非 system 消息不应是孤立的 observation（无前导 assistant thought）"
            )

    def test_truncation_preserves_system_messages(self):
        """截断后 system 消息应完整保留。"""
        session = ReactorSession.__new__(ReactorSession)

        messages = [
            {"role": "system", "content": "system prompt 1"},
            {"role": "system", "content": "system prompt 2"},
        ]
        for i in range(100):
            messages.append({"role": "assistant", "content": f"thought {i}"})
            messages.append({"role": "user", "content": f"obs {i}"})

        truncated = session._maybe_truncate_messages(messages)
        system_msgs = [m for m in truncated if m.get("role") == "system"]
        assert len(system_msgs) == 2, "system 消息应完整保留"

    def test_short_messages_not_truncated(self):
        """短消息列表不应被截断。"""
        session = ReactorSession.__new__(ReactorSession)

        messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]

        truncated = session._maybe_truncate_messages(messages)
        assert len(truncated) == len(messages), "短消息列表不应被截断"