"""ConversationHistory: append-only 对话历史。

对标 Codex ContextManager::items (Vec<ResponseItemEnvelope>):
  - record_items() 追加新消息
  - for_prompt() 归一化后送 LLM
  - 全量保留 tool_call + tool_output（不 humanize）
  - humanize 只用于前端展示，不影响 LLM 上下文
  - 支持 compaction: 由 ContextManager 触发

Codex 核心方法 (ref: context_manager/history.rs):
  - for_prompt(): 归一化 items 为 LLM 消息列表
  - record_items(): 追加新的 ResponseItem
  - clone_history(): 返回 items 的深拷贝（用于 compaction）
  - replace_compacted_history(): 原子替换整个历史
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class HistoryItem:
    """对标 Codex ResponseItem — 一条历史记录。"""
    role: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def to_llm_message(self) -> dict[str, str]:
        """转换为 LLM API 消息格式。"""
        if self.role == "tool_output":
            return {"role": "user", "content": self.content}
        if self.role == "tool_call":
            return {"role": "assistant", "content": self.content}
        return {"role": self.role, "content": self.content}


class ConversationHistory:
    """对标 Codex ContextManager::items — append-only 对话历史。

    核心不变量:
      - 每个 tool_call 必须有对应的 tool_output
      - tool_output 保留完整结构化数据（不 humanize）
      - humanize 只在 to_display_messages() 中使用
      - for_prompt() 是唯一的 LLM 出口
      - record_items() 是唯一的追加入口
    """

    def __init__(self) -> None:
        self._items: list[HistoryItem] = []
        self._history_version: int = 0

    @property
    def history_version(self) -> int:
        return self._history_version

    def record_items(self, items: list[HistoryItem]) -> None:
        """对标 Codex ContextManager::record_items() — 追加新消息。"""
        self._items.extend(items)
        self._history_version += 1

    def add_user_message(self, content: str) -> None:
        self._items.append(HistoryItem(role="user", content=content))
        self._history_version += 1

    def add_assistant_message(self, content: str) -> None:
        self._items.append(HistoryItem(role="assistant", content=content))
        self._history_version += 1

    def add_system_message(self, content: str) -> None:
        self._items.append(HistoryItem(role="system", content=content))
        self._history_version += 1

    def add_tool_call(self, tool_name: str, arguments: dict[str, Any]) -> None:
        self._items.append(HistoryItem(
            role="tool_call",
            content=json.dumps({"name": tool_name, "arguments": arguments}, ensure_ascii=False),
            metadata={"tool_name": tool_name},
        ))
        self._history_version += 1

    def add_tool_output(self, tool_name: str, output: Any, ok: bool = True) -> None:
        """保留完整结构化数据——这是核心改变。"""
        if isinstance(output, dict):
            obs_str = json.dumps(output, ensure_ascii=False, default=str)
        else:
            obs_str = str(output)
        self._items.append(HistoryItem(
            role="tool_output",
            content=f"[observation] {tool_name} -> {obs_str}",
            metadata={"tool_name": tool_name, "ok": ok},
        ))
        self._history_version += 1

    def add_observation(self, tool_name: str, obs_str: str) -> None:
        """兼容 ReactorSession 现有的 observation 格式。"""
        self._items.append(HistoryItem(
            role="tool_output",
            content=f"[observation] {tool_name} -> {obs_str}",
            metadata={"tool_name": tool_name},
        ))
        self._history_version += 1

    def for_prompt(self, system_prompt: str = "") -> list[dict[str, str]]:
        """对标 Codex ContextManager::for_prompt() — 归一化后送 LLM。

        这是唯一的 LLM 出口。所有给 LLM 的消息都经过它。
        """
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        for item in self._items:
            messages.append(item.to_llm_message())
        return messages

    def to_display_messages(self) -> list[dict[str, str]]:
        """转换为前端展示消息列表（humanize）。

        对标 Codex: 前端展示和 LLM 上下文是两条独立的路径。
        """
        messages: list[dict[str, str]] = []
        for item in self._items:
            if item.role == "tool_output":
                tool_name = item.metadata.get("tool_name", "")
                content = item.content
                if "✅" in content or "成功" in content:
                    display = f"✅ {tool_name} 执行成功"
                else:
                    display = content[:200]
                messages.append({"role": "user", "content": display})
            elif item.role == "tool_call":
                continue
            else:
                messages.append(item.to_llm_message())
        return messages

    @property
    def items(self) -> list[HistoryItem]:
        return list(self._items)

    def replace_compacted_history(self, new_items: list[HistoryItem]) -> None:
        """对标 Codex replace_compacted_history() — 原子替换整个历史。

        压缩后调用，同时递增 history_version。
        """
        self._items = new_items
        self._history_version += 1
        logger.info(
            f"[conversation_history] replaced with compacted history, "
            f"items={len(self._items)}, version={self._history_version}"
        )

    def estimate_tokens(self) -> int:
        """粗略估算当前 token 数。"""
        total = 0
        for item in self._items:
            total += len(item.content) // 4
        return total

    @property
    def message_count(self) -> int:
        return len(self._items)

    def to_dict(self) -> dict[str, Any]:
        """序列化为可持久化的字典。"""
        return {
            "history_version": self._history_version,
            "items": [
                {
                    "role": item.role,
                    "content": item.content,
                    "metadata": item.metadata,
                    "timestamp": item.timestamp,
                }
                for item in self._items
            ],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ConversationHistory":
        """从持久化的字典恢复。"""
        history = cls()
        history._history_version = data.get("history_version", 0)
        for item_data in data.get("items", []):
            history._items.append(HistoryItem(
                role=item_data.get("role", "user"),
                content=item_data.get("content", ""),
                metadata=item_data.get("metadata", {}),
                timestamp=item_data.get("timestamp", 0),
            ))
        return history