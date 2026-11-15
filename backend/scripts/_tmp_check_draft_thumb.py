"""临时验证脚本：检查侧边栏草稿列表缩略图是否渲染封面（验证后删除）。"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.security import create_jwt

BASE = "http://localhost:3002"
USER_ID = "OwdN5Jwjo_9XA_iAvuht-Q"


async def main() -> None:
    from playwright.async_api import async_playwright

    token = create_jwt(USER_ID)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        await page.goto(f"{BASE}/login", wait_until="domcontentloaded")
        await page.evaluate(
            "([t]) => { localStorage.setItem('token', t); }", [token]
        )
        await page.goto(f"{BASE}/chat", wait_until="domcontentloaded")
        try:
            await page.wait_for_selector(".mint-content-tabs", state="attached", timeout=30000)
        except Exception as exc:
            print("等待侧边栏超时:", exc)
            print((await page.content())[:500])
            await browser.close()
            return

        # 先切到「内容」Tab 让侧边栏内容面板显示
        await page.evaluate(
            """() => {
              for (const btn of document.querySelectorAll('.mint-sidebar-tab')) {
                if (btn.textContent.includes('内容')) btn.click();
              }
            }"""
        )
        await page.wait_for_timeout(1200)

        await page.evaluate(
            """() => {
              for (const btn of document.querySelectorAll('.mint-content-tab')) {
                if (btn.textContent.includes('草稿')) btn.click();
              }
            }"""
        )
        await page.wait_for_timeout(4000)
        await page.mouse.wheel(0, 600)
        await page.wait_for_timeout(2500)

        result = await page.evaluate(
            """() => {
              const containers = [...document.querySelectorAll('.mint-creation-group-items')];
              const container = containers[1];
              if (!container) return { error: 'no draft container', count: containers.length };
              const firstRow = container.querySelector('.mint-work-row');
              const thumb = firstRow ? firstRow.querySelector('.mint-work-thumb') : null;
              const chain = [];
              let el = thumb;
              while (el && chain.length < 12) {
                const cs = getComputedStyle(el);
                const rect = el.getBoundingClientRect();
                chain.push({
                  cls: (el.className || '').toString().slice(0, 40),
                  display: cs.display,
                  visibility: cs.visibility,
                  opacity: cs.opacity,
                  transform: cs.transform.slice(0, 30),
                  w: Math.round(rect.width),
                  h: Math.round(rect.height),
                  x: Math.round(rect.x),
                  y: Math.round(rect.y),
                });
                el = el.parentElement;
              }
              return {
                hasIO: typeof IntersectionObserver !== 'undefined',
                vw: window.innerWidth,
                vh: window.innerHeight,
                chain,
                groups: [...container.querySelectorAll('.mint-content-subgroup')].map((g) => ({
                  label: (g.querySelector('.mint-content-subgroup-header span') || {}).textContent || '',
                  rows: [...g.querySelectorAll('.mint-work-row')].slice(0, 6).map((row) => {
                    const t = row.querySelector('.mint-work-thumb');
                    const frame = t ? t.querySelector('iframe') : null;
                    return {
                      title: (row.querySelector('.mint-work-title') || {}).textContent || '',
                      iframe: t ? t.querySelectorAll('iframe').length : -1,
                      img: t ? t.querySelectorAll('img').length : -1,
                      iconText: t ? (t.querySelector('.wmt-icon-text') || {}).textContent || '' : '',
                      iframeLen: frame ? (frame.srcdoc || '').length : 0,
                    };
                  }),
                })),
              };
            }"""
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())