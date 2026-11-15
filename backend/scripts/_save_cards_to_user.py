#!/usr/bin/env python3
"""把 gen_ppt_cards.py 的 7 组卡片存进指定用户的「我的空间 → 平台生成」。

复跑 compose_deck 拿到 card_draft（含完整 card_deck，前端可编辑），
再调 save_card_draft_artifact 落盘。illustrated 链先查素材库，找不到就重生。
"""
import asyncio
import io
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

USER_ID = "OwdN5Jwjo_9XA_iAvuht-Q"


async def _find_asset_by_slug(slug: str):
    from sqlalchemy import desc, select

    from app.db.models import ImageAsset
    from app.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        stmt = (
            select(ImageAsset)
            .where(ImageAsset.user_id == USER_ID)
            .where(ImageAsset.filename.like(f"%{slug}%"))
            .order_by(desc(ImageAsset.created_at))
            .limit(1)
        )
        return (await db.execute(stmt)).scalar_one_or_none()


async def _illustrate(prompt: str, slug: str) -> list[str]:
    from PIL import Image

    from app.services.asset_library import save_asset
    from capabilities.image_gen.adapters.image_gen import get_default_adapter

    adapter = get_default_adapter()
    if adapter is None:
        raise RuntimeError("未配置生图能力（DASHSCOPE_API_KEY 缺失）")
    blobs = await adapter.generate(prompt, size="1024x1024", count=1)
    if not blobs:
        raise RuntimeError("生图返回空结果")
    fmt = (Image.open(io.BytesIO(blobs[0])).format or "PNG").upper()
    ctype = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}.get(fmt, "image/png")
    asset = await save_asset(
        USER_ID, f"{slug}.{fmt.lower()}", ctype, blobs[0], source="ai"
    )
    print(f"    插画入库 asset_id={asset['asset_id']}")
    return [asset["asset_id"]]


async def main() -> int:
    import argparse

    import gen_ppt_cards as gpc

    from playwright.async_api import async_playwright

    from app.adapters.deepseek import DeepSeekAdapter
    from app.cards.composer import compose_deck
    from app.config import get_settings
    from app.services.creative_artifact import save_card_draft_artifact
    from card_audit import analyze

    parser = argparse.ArgumentParser()
    parser.add_argument("--only", default="", help="只跑指定 slug")
    parser.add_argument("--no-render", action="store_true", help="跳过截图质检")
    args = parser.parse_args()

    s = get_settings()
    llm = DeepSeekAdapter(api_key=s.deepseek_api_key, model="deepseek-chat", temperature=0.7)
    await gpc._ensure_tables()

    specs = gpc.DECK_SPECS
    if args.only:
        specs = [x for x in specs if x["slug"] == args.only]

    print(f"目标 user_id = {USER_ID}")
    print(f"共 {len(specs)} 组预设\n")

    saved: list[tuple[str, str]] = []
    async with async_playwright() as pw:
        browser = await gpc._launch_browser(pw)
        try:
            for i, spec in enumerate(specs, 1):
                slug = spec["slug"]
                topic = spec["topic"]
                print(f"[{i}/{len(specs)}] {slug}  {topic}")

                kwargs = {
                    "topic": topic,
                    "content": spec["content"].strip(),
                    "user_id": USER_ID,
                    "llm": llm,
                }
                chain = spec["chain"]

                if chain == "illustrated":
                    kwargs.update(
                        theme_id=spec["theme_id"],
                        recipe_id=spec["recipe_id"],
                        image_layout=spec.get("image_layout", "top"),
                    )
                    existing = await _find_asset_by_slug(slug)
                    if existing is not None:
                        aid = existing.id
                        print(f"    复用素材 {aid}")
                    else:
                        aids = await _illustrate(spec["image_prompt"], slug)
                        aid = aids[0] if aids else ""
                    if aid:
                        kwargs["asset_ids"] = [aid]
                elif chain == "structured":
                    kwargs.update(theme_id=spec["theme_id"], recipe_id=spec["recipe_id"])
                else:
                    kwargs.update(style_hint=spec["style_hint"])

                try:
                    draft = await compose_deck(**kwargs)
                except Exception as exc:
                    print(f"    [SKIP] compose_deck 失败: {type(exc).__name__}: {exc}\n")
                    continue

                artifact_id = await save_card_draft_artifact(user_id=USER_ID, card_draft=draft)
                pages = draft.get("pages", [])
                print(f"    artifact={artifact_id or '(未创建)'}  页数={len(pages)}")
                if artifact_id:
                    saved.append((slug, artifact_id))

                png_dir = ROOT.parent / "ppt" / "_save_probe"
                png_dir.mkdir(exist_ok=True)
                if args.no_render:
                    print()
                    continue
                ctx = await browser.new_context(viewport={"width": 1080, "height": 1440})
                pg = await ctx.new_page()
                try:
                    for j, item in enumerate(pages, 1):
                        html = gpc._absolutize_uploads(item.get("htmlContent", ""))
                        if not html:
                            continue
                        out = png_dir / f"{slug}_{j:02d}_{item.get('type', '')}.png"
                        await gpc._render_html(pg, html, out, f"{slug}_{j:02d}")
                        r = analyze(str(out))
                        tag = "PASS" if r["pass"] else "FAIL"
                        print(
                            f"      page{j} {tag} span={r['span'] * 100:.0f}% "
                            f"maxgap={r['max_gap'] * 100:.0f}%"
                        )
                finally:
                    await ctx.close()
                print()

        finally:
            await browser.close()

    print(f"共落盘 {len(saved)} 组：")
    for slug, aid in saved:
        print(f"  {slug:26s} artifact:ca_{aid}")
    print("\n打开前端「我的空间 → 平台生成」刷新即可看到。")
    return 0


if __name__ == "__main__":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ.setdefault("PYTHONUTF8", "1")
    sys.exit(asyncio.run(main()))