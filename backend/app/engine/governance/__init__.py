"""Governance layer for Agent runtime.

Codex core insight (ref: codex-rs/core/src/session/input_queue.rs):
  Agent = Execution Layer + Governance Layer
  Execution: LLM + Tools + Loop  (we have this)
  Governance: Queue + Budget + Guardian + Compact + Hooks + ThreadStore + ContextWindow + StreamRetry + TimeReminder  (we lacked this)

This package implements the governance layer.
"""

from app.engine.governance.context_compact import CompactConfig, ContextCompactor
from app.engine.governance.context_manager import ContextManager
from app.engine.governance.context_window import (
    CompactScope,
    ContextWindowStatus,
    ContextWindowTracker,
)
from app.engine.governance.conversation_history import ConversationHistory, HistoryItem
from app.engine.governance.guardian import (
    Guardian,
    GuardianAction,
    GuardianDecision,
    GuardianPolicy,
)
from app.engine.governance.hooks import HookRegistry, create_default_hooks
from app.engine.governance.request_queue import LLMRequestQueue
from app.engine.governance.stream_retry import RetryAction, StreamRetryState, call_llm_with_retry
from app.engine.governance.thread_store import Checkpoint, ThreadEventType, ThreadStore
from app.engine.governance.time_reminder import TimeFragment, TimeReminder
from app.engine.governance.token_budget import TokenBudget, TokenBudgetConfig
from app.engine.governance.world_state import WorldState, WorldStateSection

__all__ = [
    "LLMRequestQueue",
    "TokenBudget",
    "TokenBudgetConfig",
    "ContextCompactor",
    "CompactConfig",
    "ContextWindowTracker",
    "ContextWindowStatus",
    "CompactScope",
    "Guardian",
    "GuardianAction",
    "GuardianDecision",
    "GuardianPolicy",
    "HookRegistry",
    "create_default_hooks",
    "StreamRetryState",
    "RetryAction",
    "call_llm_with_retry",
    "ThreadStore",
    "ThreadEventType",
    "Checkpoint",
    "TimeReminder",
    "TimeFragment",
    "WorldState",
    "WorldStateSection",
    "ConversationHistory",
    "HistoryItem",
    "ContextManager",
]
