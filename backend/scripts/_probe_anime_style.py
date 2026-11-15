#!/usr/bin/env python3
"""验证：用户说「动漫风格」时，agent 会走 style_hint 自由风格路径，
看它到底产出什么（是否含插画）。"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

OUT = ROOT.parent / "ppt" / "_anime_probe"

TOPIC = "那个夏天，我们都在等一场雨"
CONTENT = (
    "蝉在树上叫了整整一个七月，教室的电扇转得很慢。\n"
    "她把校服袖子卷到肘部，在草稿纸上画了一朵云。\n"
    "雨来得比放学晚，我们在走廊等到天黑。"
)


async def main() -> int:
    from card_audit import analyze
    from playwright.async_api import async_playwright

    from app.adapters.deepseek import DeepSeekAdapter
    from app.cards.composer import compose_deck
    from app.config import get_settings
    from gen_ppt_cards import _absolutize_uploads, _ensure_tables, _render_html

    s = get_settings()
    llm = DeepSeekAdapter(api_key=s.deepseek_api_key, model="deepseek-chat", temperature=0.7)
    OUT.mkdir(parents=True, exist_ok=True)
    await _ensure_tables()

    draft = await compose_deck(
        topic=TOPIC, content=CONTENT, user_id="local", llm=llm,
        style_hint="动漫风格",
    )
    pages = draft["pages"]
    print(f"页数={len(pages)}")
    for j, item in enumerate(pages, 1):
        html = item.get("htmlContent", "")
        has_img = "<img" in html.lower()
        print(f"  page{j} type={item.get('type')} 含<img>={has_img} len(html)={len(html)}")

    async with async_playwright() as pw:
        b = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = await b.new_context(viewport={"width": 1080, "height": 1440}, device_scale_factor=1)
        pg = await ctx.new_page()
        for j, item in enumerate(pages, 1):
            html = _absolutize_uploads(item.get("htmlContent", ""))
            if not html:
                continue
            name = f"anime_{j:02d}_{item.get('type')}"
            out = OUT / f"{name}.png"
            await _render_html(pg, html, out, name)
            r = analyze(str(out))
            print(f"  {'PASS' if r['pass'] else 'FAIL'} {name} span={r['span']*100:.0f}% "
                  f"maxgap={r['max_gap']*100:.0f}% {r['problems']}")
        await b.close()
    print(f"输出目录: {OUT}")
    return 0


sys.exit(asyncio.run(main()))