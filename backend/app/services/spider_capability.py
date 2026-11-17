"""spider 能力访问层（五期-E）：local(进程内) / remote(HTTP) 按配置切换。

- 调用方（accounts.py / spider_sync skill）只 import 这里
  ——remote 模式下主进程完全不加载 Scrapling/Playwright。
- 配置（五期-B）：capability.toml [spider] runtime 字段为唯一控制源。
  runtime="inprocess" → 进程内；runtime="service" → 读 Settings.spider_service_url 走 HTTP。
- 向后兼容：toml 无 [spider] 段时仍读 Settings.spider_service_url。
- 复用 grid_capability.py / media_capability.py 的范式。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _service_url() -> str:
    from app.core.capability_loader import get_capability_service_url

    return get_capability_service_url("spider")


async def start_sync(
    account_id: str,
    user_id: str,
    platform: str,
    force: bool = False,
) -> None:
    """平台数据同步：local / remote 按配置切换。

    local: 直接调 runner.start_sync()（进程内调度子进程 Spider）
    remote: HTTP 调 capability-service spider_sync（隔离进程调度 Spider）
    """
    url = _service_url()

    if url:
        from app.engine.protocol.transport import HttpTransport

        transport = HttpTransport(url, timeout_seconds=300)
        try:
            await transport.call(
                "spider_sync",
                {
                    "account_id": account_id,
                    "user_id": user_id,
                    "platform": platform,
                    "force": force,
                },
            )
        finally:
            await transport.aclose()
        return

    from capabilities.scrapling.spiders.runner import start_sync as _start_sync

    await _start_sync(account_id, user_id, platform, force=force)
