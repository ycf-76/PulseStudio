"""Link Grabber Client — 跨平台链接抓取（uv run grab）。

当前为 stub 实现，需要 uv + link-grabber 已安装才能真正工作。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class LinkGrabberClient:
    """跨平台链接抓取客户端（stub）。"""

    async def grab(self, url: str, **kw: Any) -> dict | None:
        logger.debug(f"[LinkGrabberClient] grab({url}) called (stub, no real grabber)")
        return None


link_grabber_client = LinkGrabberClient()
