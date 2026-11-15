# -*- coding: utf-8 -*-
"""10.26 live8 真机验收：deck 自动配图（54.1s 原素材全链，真实万相生图）。

直接运行（backend 目录下）：
  .venv\\Scripts\\python.exe tests\\test_matting_live8.py

阶段：
  1. auto_deck + deck_theme="auto" + bottom_right + matting → 闸门停点
     （LLM 产 image_prompt + 万相真实生图 → deck_auto/img_NN.jpg）
  2. 抽渲染页 PNG — 人工看配图版式（背景图 + 罩层 + 文字浮层）
  3. approve → 成片（时长 ≈ 源片）
"""
import asyncio
import json
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

VIDEO = BACKEND / "uploads" / "videos" / "live_person_talk.mp4"
OUT_FRAMES = BACKEND / "outputs" / "talking_head_overlay" / "live8_frames"


async def run_round(params: dict) -> list[dict]:
    from capabilities.video.skills.talking_head_overlay import (
        TalkingHeadOverlaySkill,
    )

    events = []
    async for ev in TalkingHeadOverlaySkill().execute({
        "video_path": str(VIDEO),
        "gate_mode": "standalone",
        "pip": True,
        "matting": True,
        "slide_duration": 9.0,
        "pip_scale": 0.3,
        "pip_position": "bottom_right",
        **params,
    }):
        events.append(ev)
        name = ev.get("event") or ("success" if ev.get("success") else "error")
        print(f"  event={name} {str(ev.get('message', ev.get('error', '')))[:70]}")
    return events


async def main() -> None:
    from capabilities.video.adapters.silence_detect import probe_duration
    from capabilities.video.adapters.html_to_video import resolve_ffmpeg

    ff = resolve_ffmpeg()
    src_dur = await probe_duration(str(VIDEO))
    print(f"[live8] 源 {src_dur:.2f}s")

    # ── 阶段1：首轮（whisper + LLM + 万相生图）→ 闸门停点 ──
    print("[live8] 阶段1 auto_deck + theme=auto + 配图")
    ev1 = await run_round({"auto_deck": True, "deck_theme": "auto"})
    gate = next(e for e in ev1 if e.get("event") == "deck_preview_ready")
    slides_json = Path(gate["data"]["slides_json_path"])
    data = json.loads(slides_json.read_text(encoding="utf-8"))
    slides = data["slides"]
    n = len(slides)
    theme = data.get("theme")
    with_ip = [s for s in slides if s.get("image_prompt")]
    with_img = [s for s in slides if s.get("image")
                and Path(s["image"]).is_file()]
    print(f"[live8] 断言1：{n} 页 theme={theme}，"
          f"image_prompt {len(with_ip)}/{n}，生图落盘 {len(with_img)}/{n}")
    assert with_ip, "LLM 未产出任何 image_prompt"
    assert len(with_img) == len(with_ip), "有 prompt 的页应全部生图成功"
    for s in slides:
        print(f"    - [{s['slide_type']}] {s['title']} | img="
              f"{Path(s['image']).name if s.get('image') else '无'} | "
              f"prompt={s.get('image_prompt', '无')[:60]}")

    # ── 阶段2：抽渲染页 PNG 人工看版式 ──
    OUT_FRAMES.mkdir(parents=True, exist_ok=True)
    deck_pngs = sorted(slides_json.parent.glob("slide_*.png"))
    assert len(deck_pngs) == n, f"渲染页 {len(deck_pngs)} != {n}"
    for p in deck_pngs[:3]:
        subprocess.run(["cmd", "/c", "copy", "/y", str(p),
                        str(OUT_FRAMES / p.name)], capture_output=True)

    # ── 阶段3：approve → 成片 ──
    print("[live8] 阶段2 approve（xfade 转场合成）")
    ev2 = await run_round({
        "deck_confirm": "approve", "deck_slides_json": str(slides_json),
    })
    out2 = Path(next(
        e for e in ev2 if e.get("event") == "pip_done"
    )["data"]["output_path"])
    dur2 = await probe_duration(str(out2))
    assert abs(dur2 - src_dur) < 1.0, f"时长偏差: {dur2} vs {src_dur}"
    (OUT_FRAMES / "slides_live8.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8",
    )
    print(f"[live8] PASS：{out2.name} {dur2:.2f}s（源 {src_dur:.2f}s），"
          f"{n} 页全配图，theme={theme}")
    print(f"[live8] 渲染页样张（前3页）在 {OUT_FRAMES} 人工看配图版式")


if __name__ == "__main__":
    asyncio.run(main())
