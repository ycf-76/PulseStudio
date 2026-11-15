#!/usr/bin/env python3
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

HTML_DIR = ROOT.parent / "outputs" / "ppt-card-html"

GEO = """() => {
    const card = document.querySelector('article') || document.body;
    const out = [];
    const walk = (el, depth) => {
        const r = el.getBoundingClientRect();
        out.push({
            tag: el.tagName.toLowerCase(),
            cls: el.className || '',
            top: Math.round(r.top),
            h: Math.round(r.height),
            d: depth,
        });
        if (depth < 3) [...el.children].forEach(c => walk(c, depth + 1));
    };
    walk(card, 0);
    return { cardH: Math.round(card.getBoundingClientRect().height), nodes: out };
}"""


async def main() -> int:
    from playwright.async_api import async_playwright

    from gen_ppt_cards import _absolutize_uploads

    f = HTML_DIR / "07_spongebob_illustrated_01_cover.html"
    raw = _absolutize_uploads(f.read_text(encoding="utf-8"))
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        pg = await b.new_page(viewport={"width": 1080, "height": 1440}, device_scale_factor=1)
        await pg.set_content(raw, wait_until="load", timeout=20000)
        await pg.wait_for_timeout(400)
        geo = await pg.evaluate(GEO)
        print("card height =", geo["cardH"])
        for n in geo["nodes"]:
            print(f"{'  ' * n['d']}{n['tag']:8s} {n['cls'][:34]:34s} top={n['top']:5d} h={n['h']:5d}")
        await b.close()
    return 0


sys.exit(asyncio.run(main()))