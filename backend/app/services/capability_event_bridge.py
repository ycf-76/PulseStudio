"""能力服务进度事件桥接（五期-C）：服务进程 → 主进程 SSE 总线。

当 TTS/视频合成等长任务在外部服务进程执行时，服务进程通过
emit_progress() 发射进度事件 → httpx 回推主进程 /internal/events
→ 主进程 sse_bus.publish() → 前端 SSE 订阅方收到。

进程内模式：工具直接调 sse_bus.publish()，不经过此桥。
服务模式：工具调 emit_progress() → 此桥 httpx 回推 → 主进程转发。

设计：
- 桥是 emit_progress() 的回调实现，由服务进程启动时注入
- 主进程侧 /internal/events 端点在 capability_service/main.py 注册
- 前端零改动：事件 schema 与 sse_bus.publish() 完全对齐
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)


def _main_process_events_url() -> str:
    """主进程 /internal/events 端点地址。

    来源：CAPABILITY_EVENTS_URL 环境变量。
    格式：http://host:port/internal/events（主进程 API 地址 + 端点路径）。
    空 = 不回推（进程内模式或未配置）。
    """
    return (os.environ.get("CAPABILITY_EVENTS_URL", "") or "").strip()


async def _httpx_progress_callback(event: dict[str, Any]) -> None:
    """emit_progress() 的回调实现：httpx POST 到主进程 /internal/events。"""
    url = _main_process_events_url()
    if not url:
        logger.debug(
            f"[event-bridge] no CAPABILITY_EVENTS_URL, event dropped: {event.get('event_type')}"
        )
        return

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
            resp = await client.post(url, json=event)
            if resp.status_code != 200:
                logger.warning(
                    f"[event-bridge] POST /internal/events failed: "
                    f"status={resp.status_code}, body={resp.text[:200]}"
                )
    except Exception as e:
        logger.warning(f"[event-bridge] POST /internal/events error: {e}")


def install_progress_bridge() -> bool:
    """安装进度事件桥接到 capability-service。

    在 capability-service 启动时调用：
    1. 读 CAPABILITY_EVENTS_URL 环境变量
    2. 非空 → 注入 httpx 回调到 emit_progress
    3. 空 → 不注入（进程内模式，进度直接走 sse_bus）

    Returns:
        True = 桥已安装（服务模式），False = 未安装（进程内模式）
    """
    url = _main_process_events_url()
    if not url:
        logger.info(
            "[event-bridge] CAPABILITY_EVENTS_URL not set, progress bridge not installed (inprocess mode)"
        )
        return False

    from capability_service.main import set_progress_callback

    set_progress_callback(_httpx_progress_callback)
    logger.info(f"[event-bridge] progress bridge installed → {url}")
    return True


async def emit_progress_via_bridge(
    workflow_id: str,
    event_type: str,
    payload: dict[str, Any],
    turn_id: str | None = None,
) -> None:
    """能力访问层统一进度发射入口。

    进程内模式：直接调 sse_bus.publish()。
    服务模式：调 capability_service.emit_progress()（服务进程内回推主进程）。

    能力访问层（如未来的 media_capability.py）应调此函数而非直接调 sse_bus，
    这样进程内/服务模式切换时调用方零改动。
    """
    from app.core.capability_loader import get_capability_service_url

    has_service = any(
        get_capability_service_url(cap) for cap in ("grid", "browser", "media", "spider")
    )

    if has_service:
        from capability_service.main import emit_progress

        await emit_progress(
            {
                "workflow_id": workflow_id,
                "event_type": event_type,
                "payload": payload,
                "turn_id": turn_id,
            }
        )
    else:
        from app.services.sse_bus import sse_bus

        await sse_bus.publish(workflow_id, event_type, payload, turn_id=turn_id)
