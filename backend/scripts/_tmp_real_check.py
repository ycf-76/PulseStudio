import json
from playwright.sync_api import sync_playwright

TOKEN = open(r"D:\My_Project\多智能体小红书发布平台\_tmp_token.txt", encoding="ascii").read().strip()
BASE = "http://127.0.0.1:3001"


def main():
    errs = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.add_init_script(
            "localStorage.setItem('token', %s);" % json.dumps(TOKEN)
        )
        page.goto(BASE + "/portfolio", wait_until="domcontentloaded")
        page.wait_for_timeout(2500)

        geo = page.evaluate(
            """() => {
            const grid = document.querySelector('.mw-grid');
            const card = document.querySelector('.wc');
            if (!grid || !card) return null;
            const g = grid.getBoundingClientRect();
            const c = card.getBoundingClientRect();
            const cs = getComputedStyle(grid);
            return {gridW: Math.round(g.width), cardW: Math.round(c.width), cardH: Math.round(c.height),
                    cols: cs.gridTemplateColumns.split(' ').length, gap: cs.columnGap,
                    count: document.querySelectorAll('.wc').length};
        }"""
        )
        print("GEO", geo)
        page.screenshot(path=r"D:\My_Project\多智能体小红书发布平台\backend\outputs\_real_grid.png", full_page=True)

        first = page.locator(".wc").first
        first.click()
        page.wait_for_timeout(2000)
        print("detail url =", page.url)
        info = page.evaluate(
            """() => ({
            title: (document.querySelector('.dt-title')||{}).textContent,
            pages: document.querySelectorAll('.dt-page').length,
            imgs: document.querySelectorAll('.dt-page img').length,
            loadedImgs: [...document.querySelectorAll('.dt-page img')].filter(i => i.naturalWidth > 0).length,
            iframes: document.querySelectorAll('.dt-page-iframe').length,
            bodyText: ((document.querySelector('.dt-body-text')||{}).textContent||'').slice(0, 30),
            tags: document.querySelectorAll('.dt-body-tag').length,
            stats: document.querySelectorAll('.dt-stat').length,
            empty: !!document.querySelector('.da-empty-state'),
            loading: !!document.querySelector('.da-loading'),
            hero: document.querySelectorAll('.dt-hero').length,
        })"""
        )
        print("DETAIL", info)
        page.screenshot(path=r"D:\My_Project\多智能体小红书发布平台\backend\outputs\_real_detail.png", full_page=True)

        page.goto(BASE + "/portfolio", wait_until="domcontentloaded")
        page.wait_for_timeout(1500)
        page.evaluate("localStorage.setItem('portfolio-view-mode','list')")
        page.reload(wait_until="domcontentloaded")
        page.wait_for_timeout(2000)
        lst = page.evaluate(
            """() => {
            const c = document.querySelector('.wc');
            if (!c) return null;
            const r = c.getBoundingClientRect();
            return {w: Math.round(r.width), h: Math.round(r.height), list: !!document.querySelector('.wc--list')};
        }"""
        )
        print("LIST", lst)
        page.screenshot(path=r"D:\My_Project\多智能体小红书发布平台\backend\outputs\_real_list.png", full_page=True)
        print("errors", errs)
        browser.close()


if __name__ == "__main__":
    main()