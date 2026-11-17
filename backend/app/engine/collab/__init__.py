"""Multi-Agent Collaboration Layer.

Ref: Codex multi_agents_v2 architecture:
  - 6 collaboration tools: spawn_agent, send_message, wait_agent,
    interrupt_agent, list_agents, followup_task
  - Concurrency slots: max_concurrent_threads_per_session
  - Agent path tree: hierarchical parent-child relationships
  - Inter-agent messaging: queue-based async communication
  - Two modes: ExplicitRequestOnly (safe) and Proactive (aggressive)

Our adaptation:
  - AgentManager: central registry for spawned agents
  - AgentHandle: lightweight reference to a running agent
  - InterAgentBus: message passing between agents
  - ConcurrencyPool: bounded parallel agent execution
  - CollaborationTools: tool definitions for LLM to call
"""

from app.engine.collab.agent_manager import AgentHandle, AgentManager, AgentStatus, CollabMode
from app.engine.collab.concurrency_pool import ConcurrencyPool
from app.engine.collab.message_bus import AgentMessage, InterAgentBus
from app.engine.collab.persistence import (
    delete_collab_state,
    load_collab_state,
    save_collab_state,
    update_agent_status,
)
from app.engine.collab.tools import CollaborationTools

__all__ = [
    "AgentManager",
    "AgentHandle",
    "AgentStatus",
    "CollabMode",
    "InterAgentBus",
    "AgentMessage",
    "ConcurrencyPool",
    "CollaborationTools",
    "save_collab_state",
    "load_collab_state",
    "delete_collab_state",
    "update_agent_status",
]
