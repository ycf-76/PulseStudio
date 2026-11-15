"""SSE 延迟清理回归测试（2026-11-12 BUG-1）。

根因：终态事件后 300s 的定时清理只看 `_last_activity`，不检查是否还有在线
订阅者。暂停等待确认可能远超 300s 且期间零事件（心跳不走 publish），于是
在线订阅队列被整条 pop，后续 guardian 确认卡 / 正文 / agent_loop_done 事件
无处投递，前端只收到心跳 → 「任务跑一半没反应 / 思考中永转」。

修复：定时回调（force=False）发现还有在线订阅者就顺延，绝不删队列。
本文件锁定修复后的完整生命周期，含防内存泄漏（断连后仍要能清掉）。
"""

from __future__ import annotations

import asyncio
import time

import pytest

from app.services.sse_bus import SSEEventBus, _CLEANUP_DELAY_SECONDS


@pytest.fixture
def env():
    """隔离的 SSE 总线（单例）；用例结束后取消所有待执行的定时清理。"""
    bus = SSEEventBus()
    used: list[str] = []

    def make(tag: str) -> str:
        wid = f"sse-cleanup-test-{tag}"
        bus.cleanup_workflow(wid)
        used.append(wid)
        return wid

    yield bus, make

    for wid in used:
        bus._cancel_cleanup(wid)
        bus.cleanup_workflow(wid)


async def _publish_terminal_then_expire(bus, wid: str) -> None:
    """上一回合终态 + 模拟 301s 无事件的暂停态。"""
    await bus.publish(wid, "workflow_completed", {"reason": "paused"})
    bus._last_activity[wid] = time.monotonic() - (_CLEANUP_DELAY_SECONDS + 1)


async def test_live_subscriber_survives_idle_cleanup(env):
    """核心回归：暂停超时后在线订阅者仍在，后续确认卡事件必须收得到。"""
    bus, make = env
    wid = make("live")
    queue: asyncio.Queue = asyncio.Queue()
    bus._subscribers[wid].add(queue)

    await _publish_terminal_then_expire(bus, wid)
    while not queue.empty():  # 终态事件本身也会进队列，先排空避免假通过
        queue.get_nowait()

    bus._cleanup_workflow(wid)  # 等价于 call_later 定时回调到点

    assert queue in bus._subscribers.get(wid, set()), "在线订阅队列不得被清理"
    await bus.publish(wid, "guardian_confirmation_required", {"id": "g-1"})
    assert queue.qsize() == 1
    assert queue.get_nowait().event_type == "guardian_confirmation_required"


async def test_no_subscriber_cleanup_still_removes_history(env):
    """无在线连接时保持原有清理语义（否则内存会一直涨）。"""
    bus, make = env
    wid = make("no-subscriber")

    await _publish_terminal_then_expire(bus, wid)
    bus._cleanup_workflow(wid)

    assert wid not in bus._event_history
    assert wid not in bus._subscribers
    assert wid not in bus._last_activity


async def test_cleanup_proceeds_after_subscriber_disconnects(env):
    """顺延不能变成永不清理：SSE 连接断开后，下一次定时回调应清干净。"""
    bus, make = env
    wid = make("disconnect")
    queue: asyncio.Queue = asyncio.Queue()
    bus._subscribers[wid].add(queue)

    await _publish_terminal_then_expire(bus, wid)
    bus._cleanup_workflow(wid)
    assert queue in bus._subscribers[wid]  # 第一次顺延

    bus._subscribers[wid].discard(queue)  # 模拟 subscribe() finally 关闭连接
    bus._cleanup_workflow(wid)  # 下一次定时回调

    assert wid not in bus._event_history
    assert wid not in bus._subscribers


async def test_recent_activity_still_defers_cleanup(env):
    """原有「按最后活动时间顺延」逻辑不被破坏。"""
    bus, make = env
    wid = make("recent")

    await bus.publish(wid, "workflow_completed", {})  # _last_activity = now
    bus._cleanup_workflow(wid)

    assert wid in bus._event_history
    assert wid in bus._cleanup_handles


async def test_non_terminal_event_cancels_scheduled_cleanup(env):
    """新回合/续跑的非终态事件仍会取消待执行的清理句柄。"""
    bus, make = env
    wid = make("cancel")

    await bus.publish(wid, "workflow_completed", {})
    assert wid in bus._cleanup_handles
    await bus.publish(wid, "progress_update", {"label": "续跑中"})
    assert wid not in bus._cleanup_handles


async def test_manual_force_cleanup_ignores_live_subscriber(env):
    """cleanup_workflow() 是手动强制清理，语义保持不变（测试/脚本依赖它）。"""
    bus, make = env
    wid = make("force")
    queue: asyncio.Queue = asyncio.Queue()
    bus._subscribers[wid].add(queue)

    await bus.publish(wid, "workflow_completed", {})
    bus.cleanup_workflow(wid)

    assert wid not in bus._subscribers
    assert wid not in bus._event_history
