#!/usr/bin/env python3
"""端到端实测：只给主题、content 为空，走真实 compose_deck(llm) 路径，
渲染后对每一页做密度审计，统计失败率。"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

OUT = ROOT.parent / "ppt" / "_e2e_probe"

CASES = [
    ("5个提升专注力的方法", "knowledge_list", "swiss"),
    ("秋天值得读的三本书", "opinion_quote", "inkwash"),
    ("从0到1做一个小红书账号", "tutorial_steps", "cream"),
    ("为什么年轻人开始存钱", "data_insight", "swiss"),
    ("租房改造前后对比", "compare_pick", "journal"),
]


async def main() -> int:
    from card_audit import analyze
    from playwright.async_api import async_playwright

    from app.adapters.deepseek import DeepSeekAdapter
    from app.cards.composer import compose_deck
    from app.config import get_settings
    from gen_ppt_cards import _absolutize_uploads, _ensure_tables, _render_html

    s = get_settings()
    llm = DeepSeekAdapter(api_key=s.deepseek_api_key, model="deepseek-chat", temperature=0.75)
    OUT.mkdir(parents=True, exist_ok=True)
    await _ensure_tables()

    results = []
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = await b.new_context(viewport={"width": 1080, "height": 1440}, device_scale_factor=1)
        pg = await ctx.new_page()
        for i, (topic, recipe, theme) in enumerate(CASES, 1):
            print(f"\n=== [{i}] {topic} | {recipe} | {theme} | content='' ===")
            try:
                draft = await compose_deck(
                    topic=topic, content="", user_id="local", llm=llm,
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
                tag = "PASS" if r["pass"] else "FAIL"
                if not r["pass"]:
                    fails += 1
                print(f"    {tag} {name} 跨度{r['span']*100:.0f}% 底空{r['bottom_dead']*100:.0f}% "
                      f"最大带{r['max_gap']*100:.0f}% {r['problems']}")
            results.append((topic, "ok", len(pages), fails))
        await b.close()

    print("\n===== 汇总 =====")
    for topic, status, npages, fails in results:
        print(f"{status:14s} {topic}  页数={npages} 失败页={fails}")
    total_pages = sum(n for _, st, n, _ in results if st == "ok")
    total_fails = sum(f for _, st, _, f in results if st == "ok")
    errs = sum(1 for _, st, _, _ in results if st != "ok")
    print(f"\n合计：{total_pages} 页，FAIL {total_fails} 页，compose 异常 {errs} 组")
    if total_pages:
        print(f"单页合格率 = {(total_pages - total_fails) / total_pages * 100:.0f}%")
    return 0


sys.exit(asyncio.run(main()))