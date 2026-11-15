#!/usr/bin/env python3
"""从前端视角实测：真实 HTTP 请求 /api/cards/preview，看能否产出动漫风格卡片。

前端只发 topic + content + theme_id + image_layout，不带任何生图能力。
把返回的每页 htmlContent 渲染成 PNG 并做密度审计。
"""
import asyncio
import json
import os
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

OUT = ROOT.parent / "ppt" / "_frontend_probe"
BASE = os.environ.get("PROBE_BASE", "http://127.0.0.1:8000")

TOPIC = "那个夏天，我们都在等一场雨"
CONTENT = (
    "蝉在树上叫了整整一个七月，教室的电扇转得很慢。\n"
    "她把校服袖子卷到肘部，在草稿纸上画了一朵云。\n"
    "雨来得比放学晚，我们在走廊等到天黑。\n"
    "后来才知道，那场雨是这个夏天唯一没有结束的事。\n"
    "很多年后我还是会在下雨天抬头，找那朵画歪了的云。"
)


async def main() -> int:
    from card_audit import analyze
    from playwright.async_api import async_playwright

    from app.security import create_jwt
    from gen_ppt_cards import _render_html

    token = create_jwt("local")
    headers = {"Authorization": f"Bearer {token}"}
    OUT.mkdir(parents=True, exist_ok=True)

    payload = {
        "topic": TOPIC,
        "content": CONTENT,
        "theme_id": "cream",
        "image_layout": "top",
    }
    print(f"POST {BASE}/api/cards/preview")
    print(f"body = {json.dumps(payload, ensure_ascii=False)}")

    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(f"{BASE}/api/cards/preview", json=payload, headers=headers)
    print(f"HTTP {resp.status_code}")
    if resp.status_code != 200:
        print(resp.text[:800])
        return 1
    body = resp.json()
    data = body.get("data") or {}
    pages = data.get("pages") or []
    print(f"页数={len(pages)}")
    for p in pages:
        print(f"  type={p.get('type')} 有HTML={bool(p.get('htmlContent'))}")

    fails = 0
    async with async_playwright() as pw:
        b = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = await b.new_context(viewport={"width": 1080, "height": 1440}, device_scale_factor=1)
        pg = await ctx.new_page()
        for j, item in enumerate(pages, 1):
            html = item.get("htmlContent", "")
            if not html:
                continue
            name = f"fe_{j:02d}_{item.get('type')}"
            out = OUT / f"{name}.png"
            await _render_html(pg, html, out, name)
            r = analyze(str(out))
            if not r["pass"]:
                fails += 1
            print(f"  {'PASS' if r['pass'] else 'FAIL'} {name} span={r['span']*100:.0f}% "
                  f"bottom={r['bottom_dead']*100:.0f}% maxgap={r['max_gap']*100:.0f}% {r['problems']}")
        await b.close()

    print(f"\n前端 preview 路径：{len(pages)} 页，FAIL={fails}")
    print(f"输出目录: {OUT}")
    return 0


sys.exit(asyncio.run(main()))