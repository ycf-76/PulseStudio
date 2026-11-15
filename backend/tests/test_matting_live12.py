# -*- coding: utf-8 -*-
"""10.30 live12 真机验收：overlay_decor 装饰叠加模式全链（32.77s 用户素材）。

直接运行（backend 目录下）：
  .venv\\Scripts\\python.exe tests\\test_matting_live12.py

链路：smart_cut（静音+口误+离题裁剪）→ overlay_decor 首轮
（转录 → deck 规划 theme=auto → 闸门停点）→ approve 回调轮
（render_decor_cards 横幅卡 → _execute_decor 原片叠加）→ decor_done。

用户需求映射：原片全屏不抠人 / 素材卡同画面不遮五官 /
裁无关内容与空气时刻 / 口播节奏进出（fade 特效）。
"""
import asyncio
import json
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

VIDEO = BACKEND / "uploads" / "videos" / "live12_src.mp4"
OUT_FRAMES = BACKEND / "outputs" / "talking_head_overlay" / "live12_frames"


async def main() -> None:
    from capabilities.video.adapters.silence_detect import probe_duration
    from capabilities.video.adapters.html_to_video import resolve_ffmpeg
    from capabilities.video.skills.smart_cut import SmartCutSkill
    from capabilities.video.skills.talking_head_overlay import (
        TalkingHeadOverlaySkill,
    )

    ff = resolve_ffmpeg()
    src_dur = await probe_duration(str(VIDEO))
    print(f"[live12] 源 {src_dur:.2f}s  {VIDEO.name}")

    # ── 阶段0：智能裁剪（静音自适应 + 口误/重复 + LLM 离题） ──
    print("[live12] 阶段0 smart_cut（plan 轮）")
    sc = SmartCutSkill()
    plan = await sc.execute({
        "video_path": str(VIDEO),
        "detect_fluff": True,
        "chain_with_overlay": True,
    })
    assert plan.get("mode") == "plan", plan
    cands = plan["plan"]["candidates"]
    print(f"    规划 {len(cands)} 刀："
          + "; ".join(f"{c['cut_type']} {c['start']:.1f}-{c['end']:.1f}s"
                      for c in cands))
    cut = await sc.execute({
        "video_path": str(VIDEO),
        "refine_candidates": cands,
        "chain_with_overlay": True,
    })
    assert "output_path" in cut, cut
    cut_path, kept = cut["output_path"], cut["kept_segments"]
    print(f"    裁剪 {cut['original_duration']:.2f} → {cut['cut_duration']:.2f}s"
          f"（{cut['cuts_applied']} 刀）")
    cut_dur = await probe_duration(cut_path)

    # ── 阶段1：decor 首轮（转录 → deck 规划 → 闸门停点） ──
    base = {
        "video_path": str(VIDEO),
        "gate_mode": "standalone",
        "overlay_decor": True,
        "auto_deck": True,
        "deck_theme": "auto",
        "cut_video_path": cut_path,
        "kept_segments": kept,
    }

    async def run_round(extra: dict) -> list[dict]:
        events = []
        async for ev in TalkingHeadOverlaySkill().execute({**base, **extra}):
            events.append(ev)
            name = ev.get("event") or ("success" if ev.get("success") else "error")
            print(f"    event={name} {str(ev.get('message', ev.get('error', '')))[:70]}")
        return events

    print("[live12] 阶段1 decor 首轮（deck 规划 + 停点）")
    ev1 = await run_round({})
    gate = next(e for e in ev1 if e.get("event") == "deck_preview_ready")
    slides_json = Path(gate["data"]["slides_json_path"])
    data = json.loads(slides_json.read_text(encoding="utf-8"))
    slides, theme, n = data["slides"], data.get("theme"), len(data["slides"])
    print(f"[live12] 断言1：{n} 页 theme={theme}")
    for s in slides:
        print(f"    - [{s['slide_type']}] {s['title']} | "
              f"img={Path(s['image']).name if s.get('image') else '无'}")

    # ── 阶段2：approve 回调轮（横幅卡渲染 → 原片叠加合成） ──
    print("[live12] 阶段2 approve（decor 合成）")
    ev2 = await run_round({
        "deck_confirm": "approve", "deck_slides_json": str(slides_json),
    })
    done = next(e for e in ev2 if e.get("event") == "decor_done")
    out = Path(done["data"]["output_path"])
    dur = await probe_duration(str(out))
    assert abs(dur - cut_dur) < 0.6, f"时长偏差: {dur:.2f} vs 裁后 {cut_dur:.2f}"
    print(f"[live12] 断言2：成片 {dur:.2f}s ≈ 裁后 {cut_dur:.2f}s，"
          f"{done['data']['cards']} 卡 windows={done['data']['windows']}")

    # ── 阶段3：抽帧（卡进出三态：无卡/卡显示/卡将消失） ──
    OUT_FRAMES.mkdir(parents=True, exist_ok=True)
    wins = done["data"]["windows"]
    snaps = []
    for i, (a, b) in enumerate(wins, 1):
        mid_in = a + 1.0        # 淡入后
        near_end = b - 0.6      # 淡出前
        for tag, t in (("in", mid_in), ("out", near_end)):
            fp = OUT_FRAMES / f"card{i:02d}_{tag}_{t:.1f}s.png"
            subprocess.run(
                [ff, "-y", "-ss", f"{t:.2f}", "-i", str(out),
                 "-frames:v", "1", str(fp)],
                capture_output=True,
            )
            snaps.append(fp)
    got = [p for p in snaps if p.is_file()]
    print(f"[live12] 抽帧 {len(got)}/{len(snaps)} → {OUT_FRAMES}")
    print(f"[live12] PASS：decor 模式全链 {src_dur:.2f}s → {dur:.2f}s"
          f"（裁剪省 {cut['saved_seconds']:.2f}s），{n} 卡，theme={theme}")


if __name__ == "__main__":
    asyncio.run(main())
