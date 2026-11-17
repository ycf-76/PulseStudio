"""ContextManager: 统管三层上下文，对标 Codex ContextManager。

对标 Codex (codex-rs/core/src/context_manager/history.rs):
  - 持有 WorldState + ConversationHistory + TokenBudget
  - update_world_state() -> (fragments, rollout_item)
  - record_items() -> 追加新消息
  - for_prompt() -> 归一化后送 LLM
  - maybe_compact() -> 检查 token 预算，触发 compaction
  - take_snapshot() / from_snapshot() -> 持久化/恢复

Codex 核心结构 (ref: context_manager/history.rs):
  pub(crate) struct ContextManager {
      items: Arc<Vec<ResponseItemEnvelope>>,  // Layer 3: append-only 历史
      history_version: u64,                    // 历史版本号
      token_info: Option<TokenUsageInfo>,      // token 追踪
      reference_context_item: Option<TurnContextItem>,  // Layer 1: 初始上下文
      world_state_baseline: Option<WorldStateSnapshot>, // Layer 2: WorldState 快照
  }

每轮流程:
  1. 加载 WorldState 快照（从 DB creative_state）
  2. 加载 ConversationHistory（从 DB messages，全量）
  3. update_world_state() -> fragments 注入历史
  4. for_prompt() -> 送 LLM
  5. 工具产出 -> ingest_tool_output() -> 自动写入 WorldState
  6. maybe_compact() -> 可能触发 compaction
  7. 持久化 WorldState 快照 + ConversationHistory
"""

from __future__ import annotations

import logging
from typing import Any

from app.engine.governance.context_compact import CompactConfig, CompactStrategy, ContextCompactor
from app.engine.governance.context_window import CompactScope, ContextWindowTracker
from app.engine.governance.conversation_history import ConversationHistory, HistoryItem
from app.engine.governance.token_budget import BudgetAction, TokenBudget, TokenBudgetConfig
from app.engine.governance.world_state import WorldState

logger = logging.getLogger(__name__)


class ContextManager:
    """对标 Codex ContextManager: 统管三层上下文。

    持有:
      - WorldState: 结构化世界状态（独立于对话历史）
      - ConversationHistory: append-only 对话历史
      - TokenBudget: token 预算追踪
      - ContextWindowTracker: 上下文窗口追踪
      - ContextCompactor: 压缩器
    """

    def __init__(
        self,
        session_id: str,
        token_budget_config: TokenBudgetConfig | None = None,
    ) -> None:
        self.session_id = session_id
        self.world_state = WorldState()
        self.history = ConversationHistory()
        self._world_state_baseline: dict[str, Any] | None = None
        self._system_prompt: str = ""

        config = token_budget_config or TokenBudgetConfig()
        self.token_budget = TokenBudget(config)
        self.context_window = ContextWindowTracker(
            full_context_window=config.context_window_tokens,
            scope=CompactScope.BODY_AFTER_PREFIX,
        )
        self.compactor = ContextCompactor(
            config=CompactConfig(strategy=CompactStrategy.SUMMARIZE),
            token_budget=self.token_budget,
        )

    def set_system_prompt(self, prompt: str) -> None:
        """设置 Layer 1: System Prompt（永不压缩）。"""
        self._system_prompt = prompt

    def set_world_state_baseline(self, snapshot: dict[str, Any] | None) -> None:
        """设置上一轮的 WorldState 快照（用于 diff）。

        对标 Codex ContextManager::set_world_state_baseline()。
        """
        self._world_state_baseline = snapshot

    def update_world_state(self) -> tuple[str, dict[str, Any]]:
        """对标 Codex ContextManager::update_world_state()。

        计算 WorldState diff，返回:
          - fragments: diff 渲染的文本，注入对话历史
          - rollout_item: 当前快照，用于持久化
        """
        fragments, rollout_item = self.world_state.update_world_state(self._world_state_baseline)

        if fragments:
            self.history.add_system_message(fragments)
            logger.info(f"[context_manager] WorldState diff injected: " f"{len(fragments)} chars")

        self._world_state_baseline = self.world_state.take_snapshot()

        return fragments, rollout_item

    def ingest_tool_output(self, tool_name: str, output: dict[str, Any]) -> None:
        """工具产出 -> 自动写入 WorldState + ConversationHistory。

        对标 Codex: 工具执行后，ContextManager 自动:
          1. 更新 WorldState（事实写入）
          2. 追加 tool_output 到 History（过程记录）
        """
        self.world_state.ingest_tool_output(tool_name, output)
        self.history.add_tool_output(tool_name, output, ok=output.get("ok", True))

    def for_prompt(self) -> list[dict[str, str]]:
        """对标 Codex ContextManager::for_prompt() — 归一化后送 LLM。

        这是唯一的 LLM 出口。
        """
        return self.history.for_prompt(system_prompt=self._system_prompt)

    def record_items(self, items: list[HistoryItem]) -> None:
        """对标 Codex ContextManager::record_items() — 追加新消息。"""
        self.history.record_items(items)

    async def maybe_compact(self, llm: Any = None) -> bool:
        """检查是否需要 compaction，如果需要则执行。

        对标 Codex: run_pre_sampling_compact() 在每轮开始前检查。
        """
        action = self.token_budget.check(self.token_budget.config)
        if action == BudgetAction.COMPACT:
            messages = self.for_prompt()
            compacted = await self.compactor.compact(messages, llm=llm)

            new_items: list[HistoryItem] = []
            for msg in compacted:
                if msg.get("role") == "system":
                    new_items.append(
                        HistoryItem(
                            role="system",
                            content=msg.get("content", ""),
                            metadata={"compaction": True},
                        )
                    )
                elif msg.get("role") == "user":
                    new_items.append(
                        HistoryItem(
                            role="user",
                            content=msg.get("content", ""),
                        )
                    )
                elif msg.get("role") == "assistant":
                    new_items.append(
                        HistoryItem(
                            role="assistant",
                            content=msg.get("content", ""),
                        )
                    )

            self.history.replace_compacted_history(new_items)
            self.token_budget.advance_window()
            self.context_window.reset_after_compaction()

            logger.info(
                f"[context_manager] compaction complete, "
                f"window={self.token_budget.window_number}, "
                f"items={self.history.message_count}"
            )
            return True
        return False

    def take_snapshot(self) -> dict[str, Any]:
        """当前完整状态快照，用于持久化。"""
        return {
            "world_state": self.world_state.take_snapshot(),
            "history_version": self.history.history_version,
            "token_budget": self.token_budget.to_dict(),
        }

    @classmethod
    def from_snapshot(
        cls,
        session_id: str,
        snapshot: dict[str, Any],
        token_budget_config: TokenBudgetConfig | None = None,
    ) -> "ContextManager":
        """从持久化的快照恢复 ContextManager。"""
        cm = cls(session_id=session_id, token_budget_config=token_budget_config)

        ws_snapshot = snapshot.get("world_state", {})
        if ws_snapshot:
            cm.world_state = WorldState.from_snapshot(ws_snapshot)
            cm._world_state_baseline = None

        return cm
