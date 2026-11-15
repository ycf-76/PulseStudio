#!/usr/bin/env python3
import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.cards.composer import _fix_free_form_whitespace

HTML_DIR = Path(r"D:\My_Project\多智能体小红书发布平台\outputs\ppt-card-html")
OUT = Path(r"D:\My_Project\多智能体小红书发布平台\ppt\_verify")

GEO = """() => {
    const root = document.body.firstElementChild;
    const rr = root.getBoundingClientRect();
    return {
        bodyH: document.body.scrollHeight,
        rootCls: root.className || root.tagName,
        rootTop: Math.round(rr.top), rootH: Math.round(rr.height),
        display: getComputedStyle(root).display,
        jc: getComputedStyle(root).justifyContent,
        kids: [...root.children].map(el => {
            const r = el.getBoundingClientRect();
            return {c: el.className || el.tagName, t: Math.round(r.top), h: Math.round(r.height)};
        })
    };
}"""


async def main():
    from playwright.async_api import async_playwright
    from card_audit import analyze

    OUT.mkdir(parents=True, exist_ok=True)
    saved = []
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        pg = await b.new_page(viewport={"width": 1080, "height": 1440}, device_scale_factor=2)
        for f in sorted(HTML_DIR.glob("04_press_freeform_*.html")):
            raw = f.read_text(encoding="utf-8")
            raw = re.sub(
                r"<style>html,body\{margin:0;height:1440px;overflow:hidden\}body>\*:first-child[\s\S]*?</style>",
                "",
                raw,
            )
            html = _fix_free_form_whitespace(raw)
            await pg.set_content(html, wait_until="load", timeout=20000)
            await pg.wait_for_timeout(500)
            geo = await pg.evaluate(GEO)
            print("---", f.name, geo["rootCls"], "h=", geo["rootH"], "display=", geo["display"], "jc=", geo["jc"])
            for k in geo["kids"]:
                print(f"    {k['t']:5d} +{k['h']:5d}  {k['c']}")
            out = OUT / (f.stem + ".png")
            await pg.screenshot(path=str(out), type="png")
            saved.append(out)
        await b.close()

    print("===== audit =====")
    for s in saved:
        r = analyze(str(s))
        print(s.name, "PASS" if r.get("pass") else "FAIL",
              "span=", r.get("span"), "bottom=", r.get("bottom_dead"),
              "maxgap=", r.get("max_gap"), r.get("bands"))


asyncio.run(main())