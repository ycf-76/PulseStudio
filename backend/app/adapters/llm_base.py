"""Base LLM adapter protocol.

Phase 3 concrete adapters (DeepSeek, Qwen-VL, ImageGen) implement this.

P0 并发控制：全局 Semaphore 限制同时进行的 LLM API 调用数，
防止多工作流并发时触发 API 429 限速雪崩。
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any

from pydantic import BaseModel

logger = logging.getLogger(__name__)

LLM_CONCURRENCY_LIMIT = 5
_llm_semaphore = asyncio.Semaphore(LLM_CONCURRENCY_LIMIT)


class BaseLLM(ABC):
    """Abstract base for all LLM model adapters.

    Red line: All LLM calls go through adapters (Phase 3).
    Harness layer must not call LLM directly.

    P0: chat / stream_chat 外层包 Semaphore，确保同时最多
    LLM_CONCURRENCY_LIMIT 个 LLM API 调用在飞。
    """

    model_name: str

    @abstractmethod
    async def _chat_impl(
        self,
        messages: list[dict[str, Any]],
        response_format: dict[str, Any] | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        """子类实现：实际 LLM 调用逻辑。"""
        raise NotImplementedError

    @abstractmethod
    async def _stream_chat_impl(
        self,
        messages: list[dict[str, Any]],
        response_format: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """子类实现：实际 LLM 流式调用逻辑。"""
        raise NotImplementedError

    async def chat(
        self,
        messages: list[dict[str, Any]],
        response_format: dict[str, Any] | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        """Non-streaming chat, wrapped with concurrency semaphore.

        Returns: {"content": str, "reasoning_content": str | None, "token_usage": int}
        """
        async with _llm_semaphore:
            logger.debug(
                f"[llm_semaphore] acquired ({LLM_CONCURRENCY_LIMIT - _llm_semaphore._value}/{LLM_CONCURRENCY_LIMIT}), "
                f"model={getattr(self, 'model_name', '?')}"
            )
            return await self._chat_impl(messages, response_format, max_tokens=max_tokens)

    async def stream_chat(
        self,
        messages: list[dict[str, Any]],
        response_format: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Streaming chat, wrapped with concurrency semaphore.

        Yields: {"content": str | None, "reasoning_content": str | None,
                "tool_calls": list | None, "is_final": bool, "token_usage": int | None}
        """
        async with _llm_semaphore:
            logger.debug(
                f"[llm_semaphore] acquired ({LLM_CONCURRENCY_LIMIT - _llm_semaphore._value}/{LLM_CONCURRENCY_LIMIT}), "
                f"model={getattr(self, 'model_name', '?')}"
            )
            async for chunk in self._stream_chat_impl(messages, response_format, tools):
                yield chunk


async def llm_prompt_to_text(
    llm: Any, prompt: str, max_tokens: int | None = None,
) -> str:
    """把单条 prompt 通过适配器的对话接口发出，取回纯文本。

    2026-10-17 修复：`llm.ainvoke(prompt)` 在本项目**从来不存在**
    （BaseLLM 只提供 chat / stream_chat），而 video_orchestrator 与
    auto_short_video 各有一处这样调用 → 100% AttributeError 被外层
    except 吞掉、静默退默认分镜，用户 prompt 里的编排意图（风格、
    情绪、节奏）全部丢失。统一收口到这里，并兼容 ainvoke 形式的旧实现。

    注意：max_tokens 只在显式传入时才带。BaseLLM.chat 自身有
    `max_tokens=None` 默认值，而无条件传 None 会打死那些只声明
    `chat(messages, response_format)` 的第三方/测试用 LLM 实现
    （实测一脚踩雷，已回退为条件传参）。
    """
    if hasattr(llm, "chat"):
        kwargs: dict[str, Any] = {"messages": [{"role": "user", "content": prompt}]}
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        resp = await llm.chat(**kwargs)
        if isinstance(resp, dict):
            return resp.get("content") or ""
        return getattr(resp, "content", None) or str(resp)
    if hasattr(llm, "ainvoke"):
        resp = await llm.ainvoke(prompt)
        return getattr(resp, "content", None) or str(resp)
    raise TypeError(f"LLM 对象无可用对话接口：{type(llm).__name__}")


class ChatMessage(BaseModel):
    """Standardized chat message format."""

    role: str
    content: str | list[dict[str, Any]]  # Can be plain text or multimodal content


class LLMConfig(BaseModel):
    """Common LLM configuration."""

    model_name: str
    temperature: float = 0.7
    max_tokens: int = 2000
    top_p: float = 1.0