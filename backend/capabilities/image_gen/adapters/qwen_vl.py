"""Qwen-VL adapter (image understanding).

Uses DashScope SDK.
Does NOT generate images (that's Wanx/Jimeng).
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from dashscope import MultiModalConversation

from app.adapters.llm_base import BaseLLM, LLMConfig

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = logging.getLogger(__name__)

_RATE_LIMITED_STATUS_CODES = {429, 400}


class QwenVLAdapter(BaseLLM):
    """Qwen-VL for image understanding.

    Input: image_url or base64 image.
    Output: structured tags (e.g. "color palette", "mood", "composition").
    """

    def __init__(
        self,
        api_key: str,
        model: str = "qwen-vl-max",
    ) -> None:
        # DashScope SDK 读模块级 dashscope.api_key / 环境变量，不认构造参数。
        # 此前只收下 api_key 从不注入 → app 启动链没有 load_dotenv 的场景
        # （如独立脚本直跑 skill）必然 "No api key provided" 被外层降级吞掉。
        import dashscope

        dashscope.api_key = api_key
        self.client = MultiModalConversation()
        self.model = model
        self._config = LLMConfig(model_name=model, temperature=0.3)

    @property
    def model_name(self) -> str:
        return self.model

    async def _chat_impl(
        self,
        messages: list[dict[str, Any]],
        response_format: dict[str, Any] | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        """Image understanding with optional JSON mode, 429 retry.

        2026-10-17 修复：基类 BaseLLM.chat() 无条件以关键字传 max_tokens
        （`self._chat_impl(messages, response_format, max_tokens=max_tokens)`），
        这里原先漏了这个参数，导致 VL 理解 100% TypeError 后被上层 except
        吞掉、静默降级。VL 自身有默认长度约束，多余参数忽略即可。
        """
        dashscope_messages = []
        for msg in messages:
            role = msg.get("role")
            content = msg.get("content")
            if isinstance(content, str):
                dashscope_messages.append({"role": role, "content": [{"text": content}]})
            else:
                dashscope_messages.append({"role": role, "content": content})

        max_retries = 3
        for attempt in range(max_retries + 1):
            try:
                resp = self.client.call(
                    model=self.model,
                    messages=dashscope_messages,
                    result_format="message",
                )
                if resp.status_code == 429 and attempt < max_retries:
                    wait = 2**attempt + 1
                    logger.warning(
                        f"[qwen-vl] 429 rate limited (attempt {attempt+1}/{max_retries+1}), "
                        f"retrying in {wait}s"
                    )
                    await asyncio.sleep(wait)
                    continue
                if resp.status_code != 200:
                    raise RuntimeError(f"Qwen-VL API error {resp.status_code}: {resp.message}")
                content = resp.output.choices[0].message.content
                return {
                    "content": content,
                    "reasoning_content": None,
                    "token_usage": resp.usage.total_tokens if resp.usage else 0,
                }
            except RuntimeError:
                raise
            except Exception as e:
                if attempt < max_retries and "429" in str(e) or "rate" in str(e).lower():
                    wait = 2**attempt + 1
                    logger.warning(
                        f"[qwen-vl] rate limited (attempt {attempt+1}/{max_retries+1}), "
                        f"retrying in {wait}s: {e}"
                    )
                    await asyncio.sleep(wait)
                    continue
                logger.error(f"Qwen-VL chat failed: {e}")
                raise
        return {"content": "", "reasoning_content": None, "token_usage": 0}

    async def _stream_chat_impl(
        self,
        messages: list[dict[str, Any]],
        response_format: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Qwen-VL streaming is not well-supported, fallback to non-streaming."""
        result = await self._chat_impl(messages, response_format)
        yield {
            "content": result["content"],
            "reasoning_content": None,
            "is_final": True,
            "token_usage": result["token_usage"],
        }
