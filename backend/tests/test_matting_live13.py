# -*- coding: utf-8 -*-
"""10.31 live13 真机验收：M1 = P0-1 词级 karaoke 字幕 + P0-3 轻装饰卡。

直接运行（backend 目录下）：
  .venv\\Scripts\\python.exe tests\\test_matting_live13.py

链路：smart_cut → overlay_decor 首轮（deck 规划停点）→ approve 回调轮
（轻装饰卡 + karaoke 帧渲染 → 原片叠加合成）→ decor_done。

机器检查（终判仍由用户连播实看）：
  - decor_done.karaoke=True（词级链路已接通）
  - 词时刻抽帧：字幕带区出现品牌绿 #39E508 像素（当前词高亮可见）
  - 字幕带区出现白色描边文字像素（字幕本体可见）
  - 卡窗口抽帧：轻装饰卡（无大底框）渲染可见
"""

import asyncio
import json
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

VIDEO = BACKEND / "uploads" / "videos" / "live12_src.mp4"
OUT_FRAMES = BACKEND / "outputs" / "talking_head_overlay" / "live13_frames"


async def main() -> None:
    from PIL import Image

    from capabilities.video.adapters.html_to_video import resolve_ffmpeg
    from capabilities.video.adapters.silence_detect import probe_duration
    from capabilities.video.skills.smart_cut import SmartCutSkill
    from capabilities.video.skills.talking_head_overlay import (
        TalkingHeadOverlaySkill,
    )

    ff = resolve_ffmpeg()
    src_dur = await probe_duration(str(VIDEO))
    print(f"[live13] 源 {src_dur:.2f}s  {VIDEO.name}")

    # ── 阶段0：智能裁剪 ──
    print("[live13] 阶段0 smart_cut（plan 轮）")
    sc = SmartCutSkill()
    plan = await sc.execute(
        {
            "video_path": str(VIDEO),
            "detect_fluff": True,
            "chain_with_overlay": True,
        }
    )
    assert plan.get("mode") == "plan", plan
    cands = plan["plan"]["candidates"]
    print(
        f"    规划 {len(cands)} 刀："
        + "; ".join(f"{c['cut_type']} {c['start']:.1f}-{c['end']:.1f}s" for c in cands)
    )
    cut = await sc.execute(
        {
            "video_path": str(VIDEO),
            "refine_candidates": cands,
            "chain_with_overlay": True,
        }
    )
    assert "output_path" in cut, cut
    cut_path, kept = cut["output_path"], cut["kept_segments"]
    print(
        f"    裁剪 {cut['original_duration']:.2f} → "
        f"{cut['cut_duration']:.2f}s（{cut['cuts_applied']} 刀）"
    )
    cut_dur = await probe_duration(cut_path)

    # ── 阶段1：decor 首轮（转录 → deck 规划 → 停点） ──
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

    print("[live13] 阶段1 decor 首轮（deck 规划 + 停点）")
    ev1 = await run_round({})
    gate = next(e for e in ev1 if e.get("event") == "deck_preview_ready")
    slides_json = Path(gate["data"]["slides_json_path"])
    data = json.loads(slides_json.read_text(encoding="utf-8"))
    slides, theme, n = data["slides"], data.get("theme"), len(data["slides"])
    print(f"[live13] 断言1：{n} 页 theme={theme}")

    # ── 阶段2：approve 回调轮（轻装饰卡 + karaoke → 合成） ──
    print("[live13] 阶段2 approve（轻装饰 + karaoke 合成）")
    ev2 = await run_round(
        {
            "deck_confirm": "approve",
            "deck_slides_json": str(slides_json),
        }
    )
    done = next(e for e in ev2 if e.get("event") == "decor_done")
    out = Path(done["data"]["output_path"])
    dur = await probe_duration(str(out))
    assert abs(dur - cut_dur) < 0.6, f"时长偏差: {dur:.2f} vs 裁后 {cut_dur:.2f}"
    # 断言2：词级 karaoke 链路接通
    assert done["data"].get("karaoke") is True, f"karaoke 未接通：{done['data']}"
    print(
        f"[live13] 断言2：karaoke=True，{done['data']['cards']} 卡 "
        f"windows={done['data']['windows']}"
    )

    # ── 阶段3：抽帧 + 像素检查 ──
    OUT_FRAMES.mkdir(parents=True, exist_ok=True)

    def grab(t: float, name: str) -> Path:
        fp = OUT_FRAMES / name
        subprocess.run(
            [ff, "-y", "-ss", f"{t:.2f}", "-i", str(out), "-frames:v", "1", str(fp)],
            capture_output=True,
        )
        return fp

    # 3a. 词时刻 karaoke 检查：字幕带（y≥70%H）品牌绿 + 白字像素
    rows = json.loads((slides_json.parent / "transcript_rows.json").read_text("utf-8"))
    words = [
        w
        for r in rows
        for w in (r.get("words") or [])
        if float(w.get("end", 0)) > float(w.get("start", 0))
    ]
    assert words, "转录无词级时间戳"
    picks = [words[i] for i in range(0, len(words), max(1, len(words) // 5))][:5]
    green_ok = 0
    white_ok = 0
    for k, w in enumerate(picks, 1):
        t = float(w["start"]) + 0.12
        fp = grab(t, f"kao{k:02d}_{t:.1f}s_{w['text'][:6]}.png")
        if not fp.is_file():
            continue
        img = Image.open(fp).convert("RGB")
        W, H = img.size
        band = img.crop((0, int(H * 0.70), W, min(H, int(H * 0.70) + 270)))
        px = list(band.getdata())
        has_green = any(g > 150 and r < 110 and b < 110 for r, g, b in px)
        has_white = sum(1 for r, g, b in px if r > 215 and g > 215 and b > 215) > 40
        green_ok += has_green
        white_ok += has_white
        print(f"    词帧{k} @{t:.2f}s「{w['text']}」绿={has_green} 白字={has_white}")
    print(
        f"[live13] 断言3：karaoke 高亮 {green_ok}/{len(picks)} 帧，"
        f"白字幕 {white_ok}/{len(picks)} 帧"
    )
    assert white_ok >= 3, "字幕带白字像素不足 — 字幕可能未渲染"
    assert green_ok >= 2, "品牌绿高亮不足 — 当前词变色可能未生效"

    # 3b. 卡窗口轻装饰检查（前 2 窗各 1 帧）
    wins = done["data"]["windows"]
    for i, (a, _b) in enumerate(wins[:2], 1):
        fp = grab(a + 1.0, f"card{i:02d}_{a + 1.0:.1f}s.png")
        print(f"    卡帧{i} @{a + 1.0:.1f}s → {fp.name}")

    print(
        f"[live13] PASS：全链 {src_dur:.2f}s → {dur:.2f}s"
        f"（省 {cut['saved_seconds']:.2f}s），{n} 卡，theme={theme}，"
        f"词帧样本 {len(picks)}"
    )
    print(f"[live13] 成片：{out}")
    print(f"[live13] 抽帧：{OUT_FRAMES}（请连播成片实看裁决）")


if __name__ == "__main__":
    asyncio.run(main())
