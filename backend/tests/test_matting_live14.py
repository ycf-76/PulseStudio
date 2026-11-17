# -*- coding: utf-8 -*-
"""10.31 live14 真机验收：M2 = P0-2 内容触发式素材卡 + P1-4 SFX 音效层。

直接运行（backend 目录下）：
  .venv\\Scripts\\python.exe tests\\test_matting_live14.py

链路：smart_cut → overlay_decor 首轮（deck 规划停点）→ approve 回调轮
（触发式卡窗 + pop 三档弹入 + karaoke + SFX 混音 → 原片叠加合成）。

机器检查（终判仍由用户连播实看 + 听 SFX）：
  - decor_done.effect=pop / sfx 非空
  - 触发对齐：卡窗 start == 该页 trigger 词在词流中的 start（≥2 页）
  - 卡出现前顶带无卡文字像素、卡窗口顶带有卡文字像素（说到才弹）
  - SFX 事件时刻清单打印（供用户听感核对）
"""

import asyncio
import json
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

VIDEO = BACKEND / "uploads" / "videos" / "live12_src.mp4"
OUT_FRAMES = BACKEND / "outputs" / "talking_head_overlay" / "live14_frames"


async def main() -> None:
    from PIL import Image

    from capabilities.video.adapters.html_to_video import resolve_ffmpeg
    from capabilities.video.adapters.silence_detect import probe_duration
    from capabilities.video.skills.smart_cut import SmartCutSkill
    from capabilities.video.skills.talking_head_overlay import (
        TalkingHeadOverlaySkill,
        _find_trigger_start,
    )

    ff = resolve_ffmpeg()
    src_dur = await probe_duration(str(VIDEO))
    print(f"[live14] 源 {src_dur:.2f}s  {VIDEO.name}")

    # ── 阶段0：智能裁剪 ──
    print("[live14] 阶段0 smart_cut（plan 轮）")
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

    print("[live14] 阶段1 decor 首轮（deck 规划 + 停点）")
    ev1 = await run_round({})
    gate = next(e for e in ev1 if e.get("event") == "deck_preview_ready")
    slides_json = Path(gate["data"]["slides_json_path"])
    data = json.loads(slides_json.read_text(encoding="utf-8"))
    slides, theme, n = data["slides"], data.get("theme"), len(data["slides"])
    triggers = [
        (
            (s.get("trigger") or {}).get("text", "")
            if isinstance(s.get("trigger"), dict)
            else str(s.get("trigger") or "")
        )
        for s in slides
    ]
    print(f"[live14] 断言1：{n} 页 theme={theme} triggers={triggers}")

    # ── 阶段2：approve 回调轮（触发式弹卡 + SFX 合成） ──
    print("[live14] 阶段2 approve（触发式弹卡 + SFX 合成）")
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
    # 断言2：pop + SFX 链路接通
    assert done["data"].get("effect") == "pop", done["data"]
    sfx = done["data"].get("sfx") or []
    assert sfx, f"SFX 未混入：{done['data']}"
    assert done["data"].get("karaoke") is True, done["data"]
    wins = [tuple(w) for w in done["data"]["windows"]]
    print(f"[live14] 断言2：effect=pop，SFX={sfx}，windows={wins}")

    # ── 断言3：触发对齐 — 卡窗 start == 词流中 trigger 词 start ──
    rows = json.loads((slides_json.parent / "transcript_rows.json").read_text("utf-8"))
    aligned = 0
    for i, (a, _b) in enumerate(wins):
        tg = triggers[i] if i < len(triggers) else ""
        hit = _find_trigger_start(rows, tg) if tg else None
        if hit is not None and abs(hit - a) < 0.001:
            aligned += 1
            mark = "触发"
        else:
            mark = "回退"
        print(f"    卡{i + 1} 窗={a:.2f}-{_b:.2f} trigger「{tg}」hit={hit} → {mark}")
    print(f"[live14] 断言3：触发对齐 {aligned}/{n} 页")
    assert aligned >= min(2, n), "触发对齐页数不足 — LLM trigger 契约可能未落实"

    # ── 断言4：说到才弹 — 卡出现前顶带无卡文字、窗口内顶带有卡文字 ──
    OUT_FRAMES.mkdir(parents=True, exist_ok=True)

    def grab(t: float, name: str) -> Path:
        fp = OUT_FRAMES / name
        subprocess.run(
            [ff, "-y", "-ss", f"{max(0.0, t):.2f}", "-i", str(out), "-frames:v", "1", str(fp)],
            capture_output=True,
        )
        return fp

    def band_stats(fp: Path) -> tuple[int, int]:
        img = Image.open(fp).convert("RGB")
        W, H = img.size
        band = img.crop((0, int(H * 0.05), W, int(H * 0.32)))
        white = dark = 0
        for r, g, b in band.getdata():
            if r > 215 and g > 215 and b > 215:
                white += 1
            elif r < 50 and g < 50 and b < 50:
                dark += 1
        return white, dark

    checked = 0
    for i, (a, _b) in enumerate(wins[:3], 1):
        if a < 0.3:
            continue
        before = grab(a - 0.12, f"pre{i:02d}_{a - 0.12:.2f}s.png")
        during = grab(a + 0.9, f"card{i:02d}_{a + 0.9:.2f}s.png")
        pop0 = grab(a + 0.02, f"pop{i:02d}_{a + 0.02:.2f}s.png")
        wb, db = band_stats(before)
        wd, dd = band_stats(during)
        # 卡带顶部黑色渐变压暗：亮背景 → 白像素显著下降；
        # 中性背景 → 近黑像素显著上升（渐变 + 描边）
        ok = (wd < wb * 0.85 and wd > 1000) or (dd > db * 1.3 + 300)
        print(
            f"    卡{i} 顶带 白 前{wb}/中{wd} 黑 前{db}/中{dd}"
            f" → {'可见' if ok else '未检出'}（pop 帧 {pop0.name}）"
        )
        if ok:
            checked += 1
    print(f"[live14] 断言4：卡可见对 {checked}/" f"{max(0, min(3, len(wins)))} 窗")
    assert checked >= 1, "卡窗口顶带未检出卡带像素 — 卡可能未渲染"

    # SFX 时刻清单（供听感核对：卡入场瞬间应有短音效）
    print(
        f"[live14] SFX 事件："
        + ", ".join(
            f"{s}@{a:.2f}s"
            for s, (a, _b) in zip(sfx, [w for w in wins if w[0] >= 0.15][: len(sfx)])
        )
    )

    print(
        f"[live14] PASS：全链 {src_dur:.2f}s → {dur:.2f}s"
        f"（省 {cut['saved_seconds']:.2f}s），{n} 卡（触发对齐 {aligned}），"
        f"SFX {len(sfx)} 处，theme={theme}"
    )
    print(f"[live14] 成片：{out}")
    print(f"[live14] 抽帧：{OUT_FRAMES}（请连播成片实看 + 听 SFX 裁决）")


if __name__ == "__main__":
    asyncio.run(main())
