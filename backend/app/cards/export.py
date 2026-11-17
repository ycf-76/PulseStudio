"""Export the same compiled HTML used for chat preview using a local Chromium.

五期-A 改造：拆成两步
  1. prepare_card_htmls() — 纯逻辑，零 Playwright 依赖
  2. render_htmls_to_pngs() — Playwright 渲染，可走 browser-service

调用方改走 browser_capability.render_card_pngs()，自动 local/remote 切换。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from app.cards.renderer import render_page
from app.cards.schema import CardDeck, ImageBlock
from app.services.asset_library import path_from_url


async def prepare_card_htmls(deck: CardDeck, user_id: str) -> list[dict[str, Any]]:
    """准备卡片 HTML 列表（纯逻辑，零 Playwright 依赖）。

    Returns:
        [{"html": str, "width": int, "height": int, "has_images": bool}, ...]
    """
    from app.cards.operations import verify_assets

    await verify_assets(deck, user_id)
    pages = []
    for index, card in enumerate(deck.pages):
        html = render_page(deck, card, index).replace(
            "<head>", '<head><base href="http://card.local/">', 1
        )
        pages.append(
            {
                "html": html,
                "width": deck.width,
                "height": deck.height,
                "has_images": any(isinstance(block, ImageBlock) for block in card.blocks),
            }
        )
    return pages


async def render_htmls_to_pngs(
    pages: list[dict[str, Any]],
) -> list[bytes]:
    """HTML 列表 → PNG 列表（Playwright 渲染，local 模式）。

    remote 模式下由 browser_capability.render_card_pngs() 调用 browser-service。
    """
    from playwright.async_api import async_playwright

    browser_path = os.getenv("CARD_CHROMIUM_PATH", "").strip()
    if not browser_path:
        bundled = (
            Path(__file__).resolve().parents[3]
            / "pw-browsers "
            / "chromium-1228"
            / "chrome-win64"
            / "chrome.exe"
        )
        if bundled.is_file():
            browser_path = str(bundled)
    if browser_path and not Path(browser_path).is_file():
        raise RuntimeError("CARD_CHROMIUM_PATH 指向的浏览器不存在")

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            executable_path=browser_path or None,
            headless=True,
        )
        try:
            first_w = pages[0]["width"] if pages else 1080
            first_h = pages[0]["height"] if pages else 1440
            context = await browser.new_context(
                viewport={"width": first_w, "height": first_h},
                device_scale_factor=1,
            )

            async def serve_local_asset(route):
                url = route.request.url
                if url.startswith("http://card.local/uploads/"):
                    path = path_from_url(url.removeprefix("http://card.local"))
                    if path is not None and path.is_file():
                        await route.fulfill(
                            body=path.read_bytes(),
                            content_type={
                                ".jpg": "image/jpeg",
                                ".png": "image/png",
                                ".webp": "image/webp",
                                ".gif": "image/gif",
                            }.get(path.suffix.lower(), "application/octet-stream"),
                        )
                        return
                await route.abort()

            await context.route("**/*", serve_local_asset)
            page = await context.new_page()
            pngs: list[bytes] = []
            for i, pg in enumerate(pages):
                await page.set_content(pg["html"], wait_until="load", timeout=15000)
                if pg.get("has_images"):
                    ready = await page.locator("article img").evaluate_all(
                        "images => images.every(img => img.complete && img.naturalWidth > 0)"
                    )
                    if not ready:
                        raise ValueError(f"第 {i + 1} 张卡片的图片未加载")
                overflow = await page.locator("article").evaluate(
                    "el => el.scrollHeight > el.clientHeight + 2 || el.scrollWidth > el.clientWidth + 2"
                )
                if overflow:
                    raise ValueError(f"第 {i + 1} 张卡片内容溢出，请缩短文案")
                pngs.append(await page.locator("article").screenshot(type="png", timeout=15000))
            await context.close()
            return pngs
        finally:
            await browser.close()


async def render_png_pages(deck: CardDeck, user_id: str) -> list[bytes]:
    """兼容入口：prepare + render 一步完成（local 模式）。"""
    pages = await prepare_card_htmls(deck, user_id)
    return await render_htmls_to_pngs(pages)
