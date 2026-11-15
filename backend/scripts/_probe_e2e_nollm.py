#!/usr/bin/env python3
"""退化路径实测：HTTP preview 端点用的是 compose_deck(llm=None)，
按行拆分、无 LLM 规划。这条才是前端预览真实走的路径。"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

OUT = ROOT.parent / "ppt" / "_e2e_probe_nollm"

CASES = [
    ("5个提升专注力的方法", "knowledge_list", "swiss"),
    ("秋天值得读的三本书", "opinion_quote", "inkwash"),
    ("从0到1做一个小红书账号", "tutorial_steps", "cream"),
]

RAW = "多任务切换每小时损失23分钟\n番茄钟25分钟一个循环\n把手机放到另一个房间\n每天固定一段深度工作时段\n睡前不刷短视频"


async def main() -> int:
    from card_audit import analyze
    from playwright.async_api import async_playwright

    from app.cards.composer import compose_deck
    from gen_ppt_cards import _ensure_tables, _render_html

    OUT.mkdir(parents=True, exist_ok=True)
    await _ensure_tables()

    results = []
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = await b.new_context(viewport={"width": 1080, "height": 1440}, device_scale_factor=1)
        pg = await ctx.new_page()
        for i, (topic, recipe, theme) in enumerate(CASES, 1):
            print(f"\n=== [{i}] {topic} | {recipe} | {theme} | llm=None ===")
            try:
                draft = await compose_deck(
                    topic=topic, content=RAW, user_id="local", llm=None,
                    theme_id=theme, recipe_id=recipe,
                )
            except Exception as exc:
                print(f"  compose 失败: {type(exc).__name__}: {exc}")
                results.append((topic, "compose_error", 0, 0))
                continue
            pages = draft["pages"]
            print(f"  页数={len(pages)}")
            fails = 0
            for j, item in enumerate(pages, 1):
                html = item.get("htmlContent", "")
                if not html:
                    continue
                name = f"{i:02d}_{j:02d}_{item.get('type')}"
                out = OUT / f"{name}.png"
                await _render_html(pg, html, out, name)
                r = analyze(str(out))
                if not r["pass"]:
                    fails += 1
                print(f"    {'PASS' if r['pass'] else 'FAIL'} {name} span={r['span']*100:.0f}% "
                      f"bottom={r['bottom_dead']*100:.0f}% maxgap={r['max_gap']*100:.0f}%")
            results.append((topic, "ok", len(pages), fails))
        await b.close()

    print("\n===== SUMMARY (llm=None) =====")
    for topic, status, npages, fails in results:
        print(f"{status:14s} {topic}  pages={npages} failed={fails}")
    total = sum(n for _, st, n, _ in results if st == "ok")
    fails = sum(f for _, st, _, f in results if st == "ok")
    errs = sum(1 for _, st, _, _ in results if st != "ok")
    print(f"total pages={total} FAIL={fails} compose_error={errs}")
    if total:
        print(f"page pass rate = {(total - fails) / total * 100:.0f}%")
    return 0


sys.exit(asyncio.run(main()))