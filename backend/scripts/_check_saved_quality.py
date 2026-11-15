#!/usr/bin/env python3
"""核对已落盘的 7 组卡片，并渲染检查每页密度。"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

USER_ID = "OwdN5Jwjo_9XA_iAvuht-Q"

TARGETS = [
    ("ca_a047c4a86d0b", "思维模型 · swiss"),
    ("ca_3aa118002bb8", "长安的荔枝 · inkwash"),
    ("ca_bae4f10ca575", "周末不摆烂 · journal"),
    ("ca_137906e1487d", "城市周报 · 自由风格"),
    ("ca_dc0da3f3b74d", "精神状态图鉴 · 自由风格"),
    ("ca_71b6bd24b817", "那个夏天 · 动漫插画"),
    ("ca_ac5b3abe2c6c", "比奇堡 · 插画"),
]


async def main() -> int:
    import gen_ppt_cards as gpc
    from playwright.async_api import async_playwright

    from app.services.creative_artifact import load_artifact
    from card_audit import analyze

    out_dir = ROOT.parent / "ppt" / "_save_probe"
    out_dir.mkdir(exist_ok=True)

    lines: list[str] = []
    async with async_playwright() as pw:
        browser = await gpc._launch_browser(pw)
        ctx = await browser.new_context(viewport={"width": 1080, "height": 1440})
        pg = await ctx.new_page()
        try:
            for aid, label in TARGETS:
                art = load_artifact(aid)
                if art is None:
                    lines.append(f"{aid}  {label}  [文件缺失]")
                    continue
                owner_ok = art.owner_user_id == USER_ID
                lines.append(
                    f"{aid}  {label}  pages={len(art.storyboard)}  "
                    f"owner={'OK' if owner_ok else 'MISMATCH'}"
                )
                for idx, page in enumerate(art.storyboard, 1):
                    html = str(page.content.get("htmlContent") or "")
                    if not html:
                        lines.append(f"   page{idx} 无 htmlContent（结构化块，跳过）")
                        continue
                    html = gpc._absolutize_uploads(html)
                    out = out_dir / f"{aid}_{idx:02d}.png"
                    await gpc._render_html(pg, html, out, f"{aid}_{idx:02d}")
                    r = analyze(str(out))
                    tag = "PASS" if r["pass"] else "FAIL"
                    lines.append(
                        f"   page{idx} {tag} span={r['span'] * 100:.0f}% "
                        f"maxgap={r['max_gap'] * 100:.0f}% {r['problems']}"
                    )
        finally:
            await ctx.close()
            await browser.close()

    (ROOT.parent / "ppt" / "_save_probe" / "report.txt").write_text(
        "\n".join(lines), encoding="utf-8"
    )
    return 0


sys.exit(asyncio.run(main()))