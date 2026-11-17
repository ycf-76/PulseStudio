"""DeepSeek 适配器超时与快速失败测试（不打真实网络）。

背景（2026-11-07）：用户发"帮我分析一下这个视频"后 3 分钟无回复。
根因链：
  1. AsyncOpenAI 客户端自身 max_retries=2（SDK 层静默重试，每次 120s 超时）
  2. 适配器内部又重试 3 次（connection error），每次 120s
  → 一次 LLM 调用最坏 4×120s+ 才暴露，全程无任何用户可见信号
修复契约：
  - 超时可配置（settings.deepseek_timeout，默认 60s）
  - SDK 层 max_retries=0（重试统一上收到可见层）
  - 连接错误内部重试 3 → 1（快速失败，交给上层 StreamRetryState 重试并发射 SSE）
  - 429 限流保留 3 次重试（429 响应快，重试代价小）
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any

import httpx
import pytest
from openai import APIConnectionError, RateLimitError

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.adapters import deepseek as ds_mod  # noqa: E402
from app.adapters.deepseek import DeepSeekAdapter  # noqa: E402


async def _no_sleep(_seconds: float) -> None:
    return None


@pytest.fixture
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """重试等待归零，避免测试真实等待。"""
    monkeypatch.setattr(ds_mod, "asyncio", types.SimpleNamespace(sleep=_no_sleep))


def _request() -> httpx.Request:
    return httpx.Request("POST", "https://api.deepseek.com/v1/chat/completions")


def _conn_error() -> APIConnectionError:
    return APIConnectionError(request=_request())


def _rate_limit_error() -> RateLimitError:
    return RateLimitError(
        "429 rate limited",
        response=httpx.Response(429, request=_request()),
        body=None,
    )


class _FakeCompletions:
    """按预置异常序列抛错，记录调用次数。"""

    def __init__(self, errors: list[Exception]) -> None:
        self._errors = list(errors)
        self.calls = 0

    async def create(self, **kwargs: Any):
        self.calls += 1
        if self._errors:
            raise self._errors.pop(0)
        raise AssertionError("不应再调用（测试只覆盖失败路径）")


def _adapter(timeout: float | None = None, **kwargs: Any) -> DeepSeekAdapter:
    params: dict[str, Any] = {"api_key": "fake-key"}
    if timeout is not None:
        params["timeout"] = timeout
    params.update(kwargs)
    return DeepSeekAdapter(**params)


# ═══════════════════════════════════════════════════════════════════
# 超时可配置
# ═══════════════════════════════════════════════════════════════════


def test_timeout_configurable():
    """超时可配置：timeout=45 → SDK 客户端 read 超时 45s。"""
    adapter = _adapter(timeout=45.0)
    assert float(adapter.client.timeout) == 45.0


def test_timeout_default_60s():
    """默认超时 60s（原 120s 是 3 分钟静默的元凶之一）。"""
    adapter = _adapter()
    assert float(adapter.client.timeout) == 60.0


def test_sdk_level_retries_disabled():
    """SDK 层静默重试必须关闭（max_retries=0），重试统一上收到可见层。"""
    adapter = _adapter()
    assert adapter.client.max_retries == 0


# ═══════════════════════════════════════════════════════════════════
# 连接错误快速失败
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_chat_connection_error_fails_fast(no_sleep: None):
    """连接错误：内部只重试 1 次（共 2 次尝试）后抛出，快速失败给上层。"""
    adapter = _adapter()
    fake = _FakeCompletions([_conn_error(), _conn_error(), _conn_error(), _conn_error()])
    adapter.client.chat.completions = fake  # type: ignore[assignment]

    with pytest.raises(APIConnectionError):
        await adapter.chat([{"role": "user", "content": "hi"}])

    assert fake.calls == 2, f"连接错误应 2 次尝试后快速失败，实际 {fake.calls} 次"


@pytest.mark.asyncio
async def test_chat_connection_error_recovers_on_second_attempt(no_sleep: None):
    """连接错误第 1 次失败、第 2 次成功 → 正常返回（快速失败≠不重试）。"""
    adapter = _adapter()

    class _OkCompletions:
        def __init__(self) -> None:
            self.calls = 0

        async def create(self, **kwargs: Any):
            self.calls += 1
            if self.calls == 1:
                raise _conn_error()
            return types.SimpleNamespace(
                choices=[
                    types.SimpleNamespace(
                        message=types.SimpleNamespace(content="ok", reasoning_content=None),
                        finish_reason="stop",
                    )
                ],
                usage=types.SimpleNamespace(
                    prompt_tokens=1,
                    completion_tokens=1,
                    total_tokens=2,
                ),
            )

    fake = _OkCompletions()
    adapter.client.chat.completions = fake  # type: ignore[assignment]

    result = await adapter.chat([{"role": "user", "content": "hi"}])
    assert result["content"] == "ok"
    assert fake.calls == 2


@pytest.mark.asyncio
async def test_chat_rate_limit_keeps_three_retries(no_sleep: None):
    """429 限流：保留 3 次重试（共 4 次尝试）——429 响应快，重试代价小。"""
    adapter = _adapter()
    fake = _FakeCompletions([_rate_limit_error() for _ in range(4)])
    adapter.client.chat.completions = fake  # type: ignore[assignment]

    with pytest.raises(RateLimitError):
        await adapter.chat([{"role": "user", "content": "hi"}])

    assert fake.calls == 4, f"429 应 4 次尝试（3 次重试），实际 {fake.calls} 次"


@pytest.mark.asyncio
async def test_stream_connection_error_fails_fast(no_sleep: None):
    """流式路径同样快速失败：连接错误 2 次尝试后抛出。"""
    adapter = _adapter()
    fake = _FakeCompletions([_conn_error(), _conn_error(), _conn_error(), _conn_error()])
    adapter.client.chat.completions = fake  # type: ignore[assignment]

    with pytest.raises(APIConnectionError):
        async for _ in adapter._stream_chat_impl([{"role": "user", "content": "hi"}]):
            pass

    assert fake.calls == 2, f"流式连接错误应 2 次尝试后快速失败，实际 {fake.calls} 次"
