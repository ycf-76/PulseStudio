"""SSE event bus for real-time event streaming.

Implements:
- Single instance per workflow
- Event publishing with ULID event_id
- Last-Event-ID continuation
- 15s heartbeat
- DB persistence for replay
- P0: 终态事件后延迟清理，防止内存泄漏 OOM
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel

logger = logging.getLogger(__name__)

_TERMINAL_TYPES = frozenset(
    {
        "workflow_completed",
        "workflow_error",
        "workflow_terminated",
        "workflow_failed",
        "workflow_suspended",
        "workflow_cancelled",
        "shortcut_completed",
        "agent_loop_done",
    }
)

_CLEANUP_DELAY_SECONDS = 300

# Chat 驱动 Agent 适配层新增事件类型（PRD v2 3.3）。SSE 总线本身按字符串接受
# 任意事件类型，这里只做显式声明，便于引用与文档对齐。
EVENT_INTENT_PARSED = "intent_parsed"
EVENT_AGENT_CHAT_ERROR = "agent_chat_error"
EVENT_DRAFT_PATCH = "draft_patch"
EVENT_CARD_DRAFT_READY = "card_draft_ready"


class SSEEvent(BaseModel):
    """SSE event structure."""

    event_id: str
    event_type: str
    payload: dict[str, Any]
    timestamp: str
    # 回合隔离信封（渲染层重构 L1）：同一 session 连续多轮对话时，
    # 订阅方带 turn_id 只回放/接收本回合事件，杜绝上一轮输出窜显。
    # None = 旧行为（不传 turn_id 的兼容路径）。
    turn_id: str | None = None


class SSEEventBus:
    """SSE event bus for workflow event streaming.

    Singleton pattern: one instance per workflow_id.

    ⚠️  SCALABILITY LIMIT: This bus is process-local (in-memory defaultdict).
    Multi-worker deployments (e.g., uvicorn --workers N) or multi-pod
    Kubernetes setups will NOT share events across processes.
    A user connected to worker-1 will miss events published on worker-2.

    Mitigation options (pick one when scaling beyond single-process):
    - Redis Pub/Sub: publish() also PUBLISHes to a Redis channel;
      subscribe() also SUBSCRIBEs and fans out to local queues.
    - PostgreSQL LISTEN/NOTIFY: same idea, fewer infra dependencies.
    - External message broker (NATS, Kafka): overkill for this use case.

    Until then, deploy with a single worker process:
        uvicorn app.main:app --workers 1

    P0: 终态事件发布后，延迟 CLEANUP_DELAY_SECONDS 秒清理该 workflow 的
    事件历史和订阅者，防止长期运行导致内存无限增长（OOM）。
    延迟清理给断线重连留出时间窗口。

    ⚠️ 2026-11-06 修复「智能体跑到一半流断掉」：chat 场景 workflow_id 就是
    session_id，上一回合的终态事件会在 T+300s 排一次清理。用户常在 5 分钟内
    追问/确认续跑，于是这次清理正好落在**下一回合执行中途**：它把
    _event_history 和 _subscribers 整条 pop 掉——仍在连接的 SSE 订阅者队列
    被移出集合，后续所有事件（cut_plan_confirmation_required / 工具进度 /
    正文）发布时再也投不进该队列，前端只收到心跳、事件永久静默，表现为
    「跑一半断开了」。修复：记录每个 workflow 的最后活动时间，清理回调发现
    距最后活动不足 CLEANUP_DELAY_SECONDS 就顺延，且任何非终态事件都会取消
    待执行的清理句柄。
    """

    _instance: SSEEventBus | None = None

    def __new__(cls) -> SSEEventBus:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._subscribers = defaultdict(set)
            cls._instance._event_history = defaultdict(list)
            cls._instance._cleanup_handles = {}
            cls._instance._last_activity = {}
        return cls._instance

    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue]] = defaultdict(set)
        self._event_history: dict[str, list[SSEEvent]] = defaultdict(list)
        # 待执行的延迟清理句柄 / 每个 workflow 最后一次事件时间（monotonic）
        self._cleanup_handles: dict[str, asyncio.TimerHandle] = {}
        self._last_activity: dict[str, float] = {}

    async def publish(
        self,
        workflow_id: str,
        event_type: str,
        payload: dict[str, Any],
        turn_id: str | None = None,
    ) -> str:
        """Publish event to all subscribers of a workflow.

        Args:
            workflow_id: Workflow ID
            event_type: Event type (27 types defined in protocol)
            payload: Event payload
            turn_id: 回合 ID（可选）。传入后订阅方可按回合过滤回放/实时事件。

        Returns:
            event_id: Generated event ID (evt_ + ULID)
        """
        import ulid

        event_id = f"evt_{ulid.new()}"
        event = SSEEvent(
            event_id=event_id,
            event_type=event_type,
            payload=payload,
            timestamp=datetime.now(UTC).isoformat(),
            turn_id=turn_id,
        )

        # Persist to history (for Last-Event-ID continuation)
        self._event_history[workflow_id].append(event)
        self._last_activity[workflow_id] = time.monotonic()

        # Push to all subscriber queues
        queues = self._subscribers.get(workflow_id, set())
        for queue in queues:
            await queue.put(event)

        logger.debug(f"[{workflow_id}] Published {event_type}: {event_id}")

        # P0: 终态事件 → 延迟清理，防止内存泄漏
        if event_type in _TERMINAL_TYPES:
            # 2026-10-21：终态事件会关闭 SSE 流（订阅端 return），
            # 谁在什么路径上发的必须有据可查——此前出现"暂停被当完成、
            # 幽灵 agent_loop_done 关流"却无从定位发布方，补调用栈摘要。
            try:
                import traceback

                _frames = traceback.extract_stack()
                _caller_chain = " <- ".join(
                    f"{f.filename.rsplit('/', 1)[-1]}:{f.lineno}:{f.name}" for f in _frames[-6:-1]
                )
                logger.info(
                    f"[{workflow_id}] TERMINAL '{event_type}' published by: {_caller_chain}"
                )
            except Exception:
                pass
            self._schedule_cleanup(workflow_id)
        else:
            # 非终态事件 = 新回合/续跑仍在进行：取消上一回合排下的清理，
            # 否则它会在本回合执行中途把订阅队列和历史一起删掉（事件静默）。
            self._cancel_cleanup(workflow_id)

        return event_id

    def _cancel_cleanup(self, workflow_id: str) -> None:
        """取消该 workflow 待执行的延迟清理。"""
        handle = self._cleanup_handles.pop(workflow_id, None)
        if handle is not None:
            try:
                handle.cancel()
            except Exception:
                pass

    def _schedule_cleanup(self, workflow_id: str) -> None:
        """终态事件后延迟清理事件历史和订阅者。

        延迟 CLEANUP_DELAY_SECONDS 秒（默认5分钟），给断线重连留时间。
        同一 workflow 只保留一个待执行句柄，重复终态不会叠加多个清理。
        """
        self._cancel_cleanup(workflow_id)
        try:
            loop = asyncio.get_running_loop()
            self._cleanup_handles[workflow_id] = loop.call_later(
                _CLEANUP_DELAY_SECONDS,
                self._cleanup_workflow,
                workflow_id,
            )
            logger.info(
                f"[{workflow_id}] cleanup scheduled in {_CLEANUP_DELAY_SECONDS}s "
                f"after terminal event"
            )
        except RuntimeError:
            logger.warning(f"[{workflow_id}] no event loop, cleanup will not be scheduled")

    def _cleanup_workflow(self, workflow_id: str, force: bool = False) -> None:
        """清理指定 workflow 的事件历史和订阅者。

        force=False（延迟回调）：以下任一情况都顺延，不直接清理：
        - 还有在线 SSE 订阅者。暂停等待确认可能远超 CLEANUP_DELAY_SECONDS 且
          期间零事件（心跳不经过 publish，不刷新 _last_activity），此时 pop 掉
          订阅队列会让后续确认卡/正文事件无处投递，前端永久静默（2026-11-12 修）。
        - 距最后一次事件不足 CLEANUP_DELAY_SECONDS（会话仍在活跃），清空历史
          会让 Last-Event-ID 回放失效。

        force=True（手动清理）：保持强制语义，不做顺延。
        """
        self._cleanup_handles.pop(workflow_id, None)

        if not force:
            # 在线订阅者优先于「最后活动时间」判断：订阅者还连着就绝不能删队列。
            live_subscribers = self._subscribers.get(workflow_id)
            if live_subscribers:
                logger.info(
                    f"[{workflow_id}] SSE cleanup deferred: {len(live_subscribers)} "
                    f"live subscriber(s) still connected, keeping history and queues"
                )
                self._schedule_cleanup(workflow_id)
                return

            idle = time.monotonic() - self._last_activity.get(workflow_id, 0.0)
            if idle < _CLEANUP_DELAY_SECONDS:
                logger.info(
                    f"[{workflow_id}] SSE cleanup deferred: last activity "
                    f"{idle:.0f}s ago (< {_CLEANUP_DELAY_SECONDS}s), keeping "
                    f"history and subscribers"
                )
                self._schedule_cleanup(workflow_id)
                return

        history_count = len(self._event_history.get(workflow_id, []))
        sub_count = len(self._subscribers.get(workflow_id, set()))

        self._event_history.pop(workflow_id, None)
        self._subscribers.pop(workflow_id, None)
        self._last_activity.pop(workflow_id, None)

        logger.info(
            f"[{workflow_id}] SSE cleanup: removed {history_count} history events, "
            f"{sub_count} subscriber queues"
        )

    def cleanup_workflow(self, workflow_id: str) -> None:
        """公开接口：手动清理指定 workflow 的 SSE 数据（不受活跃度顺延影响）。"""
        self._cleanup_workflow(workflow_id, force=True)

    async def subscribe(
        self,
        workflow_id: str,
        last_event_id: str | None = None,
        turn_id: str | None = None,
    ) -> AsyncIterator[str]:
        """Subscribe to workflow events.

        Args:
            workflow_id: Workflow ID
            last_event_id: Last received event ID (for continuation)
            turn_id: 回合 ID（可选）。传入后：
                - 回放只含本回合事件（断线重连同样只续本回合）
                - 实时推送也只放行本回合事件（并发回合不互窜）
                不传则维持旧行为（回放全部历史、实时不过滤）。

        Yields:
            SSE formatted strings: "id: {event_id}\\nevent: {type}\\ndata: {json}\\n\\n"
        """
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers[workflow_id].add(queue)

        def _of_this_turn(ev: SSEEvent) -> bool:
            if turn_id is None:
                return True
            if ev.turn_id is None:
                return True
            return ev.turn_id == turn_id

        try:
            # Replay missed events.
            # - 带 last_event_id：从该 id 之后 replay（断线重连场景）
            # - 不带 last_event_id：replay 全部历史（首次订阅场景）。
            #   原因：后端 start_workflow 用 asyncio.create_task 启动 graph，
            #   graph 执行可能极快（search 调 API + 其他节点 stub），
            #   事件在前端 SSE 连接建立前就发完了。首次订阅 replay 全部历史，
            #   保证前端不丢事件。若 history 中已有 terminal 事件，replay 后直接结束。
            # - 带 turn_id：只回放本回合事件（回合隔离，见 SSEEvent.turn_id）。
            history = self._event_history.get(workflow_id, [])
            if last_event_id:
                replay_list = self._replay_events(workflow_id, last_event_id)
            else:
                replay_list = list(history)
            for event in replay_list:
                if not _of_this_turn(event):
                    continue
                yield self._format_sse(event)
                if event.event_type in _TERMINAL_TYPES:
                    logger.debug(
                        f"[{workflow_id}] Stream ended after replaying terminal event {event.event_type}"
                    )
                    return

            # Real-time subscription
            heartbeat_counter = 0
            while True:
                try:
                    # Wait for event with timeout for heartbeat
                    event = await asyncio.wait_for(queue.get(), timeout=5.0)
                    heartbeat_counter = 0
                    if not _of_this_turn(event):
                        # 异回合事件（同一 session 上的并发/后续回合）：不转发
                        logger.debug(
                            f"[{workflow_id}] Dropped off-turn event {event.event_type} "
                            f"(event turn={event.turn_id}, subscriber turn={turn_id})"
                        )
                        continue
                    yield self._format_sse(event)
                    # 终态事件：结束订阅，关闭 SSE 流
                    if event.event_type in _TERMINAL_TYPES:
                        logger.debug(
                            f"[{workflow_id}] Stream ended after terminal event {event.event_type}"
                        )
                        return
                except TimeoutError:
                    # Send heartbeat every 5 seconds
                    heartbeat_counter += 1
                    yield ": heartbeat\n\n"
                    if heartbeat_counter > 24:
                        # 60 seconds without events, check connection
                        logger.debug(f"[{workflow_id}] Heartbeat check")
                    if heartbeat_counter > 72:
                        # 2026-11-08 连接配额治理：此前心跳检查只打日志不关流，
                        # 泄漏的 SSE 流（暂停态会话 / 半开连接）会永久占用浏览器
                        # 每主机 6 连接配额——新请求全部排队（图片生成「没有任何
                        # 反应」→ Failed to fetch）。改为 6 分钟完全无事件即关流：
                        # 活跃回合任何事件都会重置计数；客户端带 Last-Event-ID
                        # 自动重连续传，resume 场景也会重新订阅，事件不丢。
                        logger.info(
                            f"[{workflow_id}] Closing idle SSE stream after "
                            f"{heartbeat_counter * 5}s without events"
                        )
                        return
        finally:
            self._subscribers[workflow_id].discard(queue)

    def _replay_events(self, workflow_id: str, last_event_id: str) -> list[SSEEvent]:
        """Replay events after last_event_id."""
        events = self._event_history.get(workflow_id, [])
        found = False
        replay = []
        for event in events:
            if found:
                replay.append(event)
            if event.event_id == last_event_id:
                found = True
        return replay

    def _format_sse(self, event: SSEEvent) -> str:
        """Format event as SSE string."""
        data = json.dumps(
            {
                "event_id": event.event_id,
                "type": event.event_type,
                "payload": event.payload,
                "timestamp": event.timestamp,
                "turn_id": event.turn_id,
            }
        )
        return f"id: {event.event_id}\nevent: {event.event_type}\ndata: {data}\n\n"


# Singleton instance
sse_bus = SSEEventBus()
