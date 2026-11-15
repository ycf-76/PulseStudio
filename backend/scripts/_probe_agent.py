#!/usr/bin/env python3
"""端到端实测：从前端 HTTP 发一句自然语言给 ReAct agent，
看它能否自己走完 生图 → compose_card_deck 产出动漫风卡片。"""
import asyncio
import json
import os
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

OUT = ROOT.parent / "ppt" / "_agent_probe"
BASE = os.environ.get("PROBE_BASE", "http://127.0.0.1:8000")

INTENT = (
    "帮我做一套动漫风格的图文卡片，主题是「那个夏天，我们都在等一场雨」，"
    "内容就用：蝉在树上叫了整整一个七月；她把校服袖子卷到肘部在草稿纸上画了一朵云；"
    "雨来得比放学晚我们在走廊等到天黑；很多年后我还是会在下雨天抬头找那朵画歪了的云。"
    "封面要有一张动漫插画。"
)


async def main() -> int:
    from card_audit import analyze
    from playwright.async_api import async_playwright

    from app.security import create_jwt
    from gen_ppt_cards import _render_html

    token = create_jwt("local")
    headers = {"Authorization": f"Bearer {token}"}
    OUT.mkdir(parents=True, exist_ok=True)

    payload = {"intent": INTENT, "permissions": [], "max_iterations": 12}
    print(f"POST {BASE}/api/v1/reactor/run")
    print(f"intent = {INTENT}\n")

    async with httpx.AsyncClient(timeout=600) as client:
        resp = await client.post(f"{BASE}/api/v1/reactor/run", json=payload, headers=headers)
    print(f"HTTP {resp.status_code}")
    if resp.status_code != 200:
        print(resp.text[:1500])
        return 1
    body = resp.json()
    print(f"ok={body.get('ok')} iterations={body.get('iterations')} "
          f"tokens={body.get('token_usage')} {body.get('duration_ms')}ms")

    print("\n--- steps ---")
    for st in body.get("steps") or []:
        name = st.get("tool") or st.get("action") or st.get("type") or ""
        print(f"  {name}")

    output = body.get("output") or {}
    print("\n--- output keys ---", list(output.keys()))
    draft = output.get("card_draft")
    if not draft:
        for v in output.values():
            if isinstance(v, dict) and v.get("pages"):
                draft = v
                break
    if not draft:
        print("没有拿到 card_draft，agent 未产出卡片")
        print(json.dumps(output, ensure_ascii=False)[:2000])
        return 2

    pages = draft.get("pages") or []
    print(f"页数={len(pages)}")
    fails = 0
    async with async_playwright() as pw:
        b = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = await b.new_context(viewport={"width": 1080, "height": 1440}, device_scale_factor=1)
        pg = await ctx.new_page()
        for j, item in enumerate(pages, 1):
            html = item.get("htmlContent", "")
            if not html:
                continue
            name = f"ag_{j:02d}_{item.get('type')}"
            out = OUT / f"{name}.png"
            await _render_html(pg, html, out, name)
            r = analyze(str(out))
            if not r["pass"]:
                fails += 1
            print(f"  {'PASS' if r['pass'] else 'FAIL'} {name} span={r['span']*100:.0f}% "
                  f"bottom={r['bottom_dead']*100:.0f}% maxgap={r['max_gap']*100:.0f}% {r['problems']}")
        await b.close()

    print(f"\nagent 路径：{len(pages)} 页，FAIL={fails}")
    print(f"输出目录: {OUT}")
    return 0


sys.exit(asyncio.run(main()))