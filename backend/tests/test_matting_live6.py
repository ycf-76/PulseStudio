"""10.24 live6 真机：幻灯片内容丰富度 — 真实 LLM（含自检重试）新旧对比。

复用 live5 slides.json 的 transcript（省 whisper）；新版 PNG 落
live6_frames，旧版对照 = deck_auto/slide_01-06.png（人工看图）。
断言：slide_type 混用（≥2 型且含 quote）+ emphasis 非全 none（A 生效）。
"""
import asyncio
import json
import shutil
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
OUT = BACKEND / "outputs" / "talking_head_overlay"
DECK_AUTO = OUT / "deck_auto"
FRAMES = OUT / "live6_frames"


async def main() -> None:
    from capabilities.video.services.deck_orchestrator import (
        generate_deck_slides,
    )
    from capabilities.video.skills.talking_head_overlay import (
        TalkingHeadOverlaySkill,
    )

    src = json.loads((DECK_AUTO / "slides.json").read_text(encoding="utf-8"))
    transcript = src["transcript"]
    assert transcript.strip(), "live5 transcript 缺失"

    llm = TalkingHeadOverlaySkill._resolve_llm()
    assert llm is not None, "DeepSeek 配置缺失"
    out_dir = DECK_AUTO / "live6_rich"
    if out_dir.exists():
        shutil.rmtree(out_dir)

    pngs = await generate_deck_slides(
        transcript, llm, out_dir, max_slides=6, theme="midnight",
        person_clearance_px=640,
    )
    assert pngs and all(p.is_file() for p in pngs), "新版生成失败"

    data = json.loads((out_dir / "slides.json").read_text(encoding="utf-8"))
    slides = data["slides"]
    types = [s.get("slide_type") for s in slides]
    ems = [b.get("emphasis") for s in slides for b in s["bullets"]]
    assert len(set(types)) >= 2 and "quote" in types, f"页型未混用: {types}"
    assert any(e != "none" for e in ems), f"emphasis 全 none: {ems}"

    print(f"[live6] PASS：新版 {len(slides)} 张 → {out_dir}")
    print(f"[live6] 页型分布: {types}")
    print(f"[live6] emphasis 分布: { {e: ems.count(e) for e in set(ems)} }")
    for i, s in enumerate(slides, 1):
        print(f"[live6]   {i:02d} [{s.get('slide_type')}] {s['title']}")

    FRAMES.mkdir(parents=True, exist_ok=True)
    for p in pngs:
        shutil.copyfile(p, FRAMES / p.name)
    shutil.copyfile(out_dir / "slides.json", FRAMES / "slides_live6.json")
    print(f"[live6] 素材在 {FRAMES}（旧版对照 = deck_auto/slide_01-06.png）")


if __name__ == "__main__":
    asyncio.run(main())
