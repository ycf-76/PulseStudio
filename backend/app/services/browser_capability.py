"""browser 能力访问层（五期-A + 五期-B 声明即拓扑）：local(进程内) / remote(HTTP) 按配置切换。

- 调用方只 import 这里，不直接 import cards.export 的 Playwright 函数
  ——remote 模式下主进程完全不加载 playwright。
- 配置（五期-B）：capability.toml [browser] runtime 字段为唯一控制源。
  runtime="inprocess" → 进程内 Playwright；runtime="service" → 读 Settings.browser_service_url 走 HTTP。
- 向后兼容：toml 无 [browser] 段时仍读 Settings.browser_service_url。
- 复用 grid_capability.py 的范式（核心下沉→服务注册→配置切换→双跑契约）。
"""
from __future__ import annotations

import base64
import json
import logging
from typing import Any

from app.cards.schema import CardDeck

logger = logging.getLogger(__name__)


def _service_url() -> str:
    from app.core.capability_loader import get_capability_service_url

    return get_capability_service_url("browser")


async def render_card_pngs(deck: CardDeck, user_id: str) -> list[bytes]:
    """卡片渲染：local / remote 按配置切换。

    local: prepare_card_htmls() + render_htmls_to_pngs()（进程内 Playwright）
    remote: prepare_card_htmls() + HTTP 调 browser-service render_htmls（隔离进程 Playwright）
    """
    from app.cards.export import prepare_card_htmls

    pages = await prepare_card_htmls(deck, user_id)
    url = _service_url()

    if url:
        from app.engine.protocol.transport import HttpTransport

        transport = HttpTransport(url, timeout_seconds=120)
        try:
            result = await transport.call(
                "render_htmls",
                {"pages_json": json.dumps(pages)},
            )
        finally:
            await transport.aclose()
        if not result.get("ok", True):
            logger.warning(f"[browser-cap] remote render failed: {result.get('error')}")
            raise RuntimeError(f"remote render_htmls failed: {result.get('error')}")
        b64s = result.get("pngs_b64", [])
        return [base64.b64decode(b) for b in b64s]

    from app.cards.export import render_htmls_to_pngs

    return await render_htmls_to_pngs(pages)