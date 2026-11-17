"""内核对外契约（一期定稿）：ended_reason 枚举 + SSE 事件类型 + schema 版本。

这是「契约带①（内核 ↔ 界面）」的合同文本：
- EndedReason：loop 每次结束必须给出的终止原因，前端据此区分
  「正常完成 / 被截断 / 等待用户」——修复「任务截断用户搞不清」的根因。
- EventType：SSE 事件名全集。前端只认这里的名字。
- 冻结规则：只加成员不删成员；语义变更必须升 SCHEMA_VERSION。

对应文档：docs/内核-能力分层架构方案.md §3.2 契约带①。
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel

# 协议版本：字段语义变更时 +1；只加字段不改语义时保持不变
SCHEMA_VERSION = "1.0"


class EndedReason(str, Enum):
    """ReAct loop 终止原因。

    正常终态：COMPLETED。
    资源截断：MAX_ITERATIONS / TOKEN_BUDGET / NUDGE_EXHAUSTED / DUPLICATE_CALLS。
    幻觉拦截：FAKE_FINAL（Execution Gate 检测到零执行零产物的假完成）。
    等待用户（loop 暂停、可恢复）：CLARIFICATION_PAUSED / GUARDIAN_PAUSED / STORYBOARD_PAUSED / CUT_PLAN_PAUSED。
    异常兜底：ERROR（由上层捕获路径赋值）。
    """

    COMPLETED = "completed"
    MAX_ITERATIONS = "max_iterations"
    TOKEN_BUDGET = "token_budget"
    NUDGE_EXHAUSTED = "nudge_exhausted"
    DUPLICATE_CALLS = "duplicate_calls"
    FAKE_FINAL = "fake_final"
    CLARIFICATION_PAUSED = "clarification_paused"
    GUARDIAN_PAUSED = "guardian_paused"
    STORYBOARD_PAUSED = "storyboard_paused"
    CUT_PLAN_PAUSED = "cut_plan_paused"
    SIDE_WRITE_PAUSED = "side_write_paused"
    ERROR = "error"


class EventType(str, Enum):
    """SSE 事件名全集（盘点自 app/ 全部 _emit_sse / bus.publish 调用点）。

    新增事件在这里登记后才允许出现在总线上（契约测试锁定）。
    """

    # 生命周期
    WORKFLOW_STARTED = "workflow_started"
    WORKFLOW_COMPLETED = "workflow_completed"
    WORKFLOW_ERROR = "workflow_error"
    NODE_STARTED = "node_started"
    NODE_COMPLETED = "node_completed"
    CHAT_TURN_STARTED = "chat_turn_started"
    AGENT_LOOP_DONE = "agent_loop_done"

    # 过程流
    PROGRESS_UPDATE = "progress_update"
    REASONING_COMPLETED = "reasoning_completed"
    REASONING_SUMMARY_TEXT_DELTA = "reasoning_summary_text_delta"
    AGENT_THINKING = "agent_thinking"
    AGENT_MESSAGE_DELTA = "agent_message_delta"
    DECISION_MADE = "decision_made"
    TOOL_CALL_START = "tool_call_start"
    TOOL_CALL_END = "tool_call_end"
    # 遗留 wire 契约：事件名含技能名，前端在线消费，更名需前端联动（三期外置时统一迁）
    MULTI_IMAGE_VIDEO_PROGRESS = "multi_image_video_progress"

    # 暂停等待用户（可恢复，对应 EndedReason 各 *_PAUSED）
    AGENT_CLARIFICATION_REQUIRED = "agent_clarification_required"
    GUARDIAN_CONFIRMATION_REQUIRED = "guardian_confirmation_required"
    STORYBOARD_CONFIRMATION_REQUIRED = "storyboard_confirmation_required"
    CUT_PLAN_CONFIRMATION_REQUIRED = "cut_plan_confirmation_required"
    SIDE_WRITE_CONFIRMATION_REQUIRED = "side_write_confirmation_required"

    # 产物
    CARD_DRAFT_READY = "card_draft_ready"


# 终态事件：必须携带 ended_reason
TERMINAL_EVENTS: frozenset[str] = frozenset(
    {
        EventType.NODE_COMPLETED.value,
        EventType.WORKFLOW_COMPLETED.value,
    }
)

# 终态事件白名单里禁止出现的大对象（明细走 POST 响应，不走 SSE）
_FORBIDDEN_IN_TERMINAL_SSE: frozenset[str] = frozenset(
    {
        "_steps",
        "_loop_truncated",
        "_token_budget_exhausted",
        "_duplicate_tool_calls",
    }
)


class LoopTermination(BaseModel):
    """node_completed / workflow_completed 的终止契约片段。

    session 的终态事件 payload 必须满足本模型（契约测试锁定）。
    """

    schema_version: str = SCHEMA_VERSION
    ended_reason: EndedReason
    iterations: int = 0
    token_usage: int = 0


def validate_ended_reason(value: Any) -> str:
    """宽松校验：接受枚举或字符串，非法值兜底 COMPLETED（防御未知路径漏标）。"""
    try:
        return EndedReason(value).value
    except ValueError:
        return EndedReason.COMPLETED.value
