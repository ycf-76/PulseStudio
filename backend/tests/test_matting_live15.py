# -*- coding: utf-8 -*-
"""10.31 live15 真机验收：M3 = P1-5 强调 zoom + P2-6/7 fitText/motion tokens。

直接运行（backend 目录下）：
  .venv\\Scripts\\python.exe tests\\test_matting_live15.py

链路：smart_cut → overlay_decor 首轮（deck 规划停点）→ approve 回调轮
（触发式卡窗 + pop 弹入 + karaoke + SFX + zoompan 强调推近 → 叠加合成）。

机器检查（终判仍由用户连播实看：zoom 推近感 + 长句字幕不溢出）：
  - decor_done.zoom 1-5 窗（number 优先）、窗长 ∈ [0.25, 0.45]
  - zoompan 帧号换算（fps 显式防重定时 → 成片时长不漂）
  - 触发对齐 / 卡可见（M2 行为回归）
  - zoom 峰值帧 vs 窗外基准帧存档（中心条带差异计数，供目检）
"""

import asyncio
import json
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

VIDEO = BACKEND / "uploads" / "videos" / "live12_src.mp4"
OUT_FRAMES = BACKEND / "outputs" / "talking_head_overlay" / "live15_frames"


async def main() -> None:
    from PIL import Image

    from capabilities.video.adapters.html_to_video import resolve_ffmpeg
    from capabilities.video.adapters.silence_detect import probe_duration
    from capabilities.video.skills.smart_cut import SmartCutSkill
    from capabilities.video.skills.talking_head_overlay import (
        TalkingHeadOverlaySkill,
        _find_trigger_start,
        _zoompan_expr,
    )

    ff = resolve_ffmpeg()
    src_dur = await probe_duration(str(VIDEO))
    print(f"[live15] 源 {src_dur:.2f}s  {VIDEO.name}")

    # ── 阶段0：智能裁剪 ──
    print("[live15] 阶段0 smart_cut（plan 轮）")
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

    print("[live15] 阶段1 decor 首轮（deck 规划 + 停点）")
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
    emph = [
        (b.get("emphasis"), b.get("text"))
        for s in slides
        for b in (s.get("bullets") or [])
        if isinstance(b, dict) and b.get("emphasis") in ("number", "keyword")
    ]
    print(f"[live15] 断言1：{n} 页 theme={theme} triggers={triggers}")
    print(f"[live15] 断言1b：emphasis 候选 {len(emph)} 条 {emph}")

    # ── 阶段2：approve 回调轮（触发弹卡 + SFX + zoom 合成） ──
    print("[live15] 阶段2 approve（触发弹卡 + SFX + zoom 合成）")
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
    # 断言2：pop + SFX + zoom 链路接通
    assert done["data"].get("effect") == "pop", done["data"]
    sfx = done["data"].get("sfx") or []
    assert sfx, f"SFX 未混入：{done['data']}"
    assert done["data"].get("karaoke") is True, done["data"]
    wins = [tuple(w) for w in done["data"]["windows"]]
    zooms = [tuple(w) for w in (done["data"].get("zoom") or [])]
    print(f"[live15] 断言2：effect=pop，SFX={sfx}，windows={wins}")
    assert 1 <= len(zooms) <= 5, f"zoom 窗异常（≤5 次规则）：{zooms}"
    for zs, ze in zooms:
        assert 0.25 <= ze - zs <= 0.45 + 1e-6, f"zoom 窗长越界：{zs}-{ze}"
    print(f"[live15] 断言2b：zoom {len(zooms)} 窗 {zooms}（≤5、窗长合规）")

    # ── 断言3：触发对齐（M2 回归）— 卡窗 start == trigger 词 start ──
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
    print(f"[live15] 断言3：触发对齐 {aligned}/{n} 页")
    assert aligned >= min(2, n), "触发对齐页数不足 — M2 行为可能被 M3 破坏"

    # ── 断言4：卡可见（M2 回归）+ zoom 对照帧存档 ──
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
        wb, db = band_stats(before)
        wd, dd = band_stats(during)
        ok = (wd < wb * 0.85 and wd > 1000) or (dd > db * 1.3 + 300)
        print(
            f"    卡{i} 顶带 白 前{wb}/中{wd} 黑 前{db}/中{dd}" f" → {'可见' if ok else '未检出'}"
        )
        if ok:
            checked += 1
    print(f"[live15] 断言4：卡可见对 {checked}/" f"{max(0, min(3, len(wins)))} 窗（M2 回归）")
    assert checked >= 1, "卡窗口顶带未检出卡带像素 — 卡可能未渲染"

    # ── 断言5：zoompan 真机生效 — 帧号换算 + 峰值/窗外对照帧 ──
    fps = 30
    expr = _zoompan_expr(zooms, fps, 576, 1280)
    assert expr is not None and "between(in," in expr
    frames_s = [int(round(zs * fps)) for zs, _ze in zooms]
    print(
        f"[live15] 断言5：zoompan 帧号 {frames_s}（fps={fps} 显式防重定时，"
        f"成片 {dur:.2f}s ≈ 裁后 {cut_dur:.2f}s → 未漂移）"
    )

    def center_diff(fa: Path, fb: Path) -> int:
        ia = Image.open(fa).convert("L")
        ib = Image.open(fb).convert("L")
        W, H = ia.size
        ca = ia.crop((0, H // 2 - 40, W, H // 2 + 40))
        cb = ib.crop((0, H // 2 - 40, W, H // 2 + 40))
        return sum(1 for x, y in zip(ca.getdata(), cb.getdata()) if abs(x - y) > 24)

    for i, (zs, ze) in enumerate(zooms[:3], 1):
        peak_t = zs + (ze - zs) * 0.3
        pre = grab(zs - 0.25, f"zpre{i:02d}_{zs - 0.25:.2f}s.png")
        peak = grab(peak_t, f"zpeak{i:02d}_{peak_t:.2f}s.png")
        print(
            f"    zoom{i} 窗={zs:.2f}-{ze:.2f} 峰值帧 {peak.name} "
            f"中心条带帧差={center_diff(pre, peak)}（对照 {pre.name}）"
        )

    print(
        f"[live15] PASS：全链 {src_dur:.2f}s → {dur:.2f}s，"
        f"{n} 卡（触发对齐 {aligned}），SFX {len(sfx)} 处，"
        f"zoom {len(zooms)} 窗，theme={theme}"
    )
    print(f"[live15] 成片：{out}")
    print(f"[live15] 抽帧：{OUT_FRAMES}（请连播实看 zoom 推近感 + 听 SFX 裁决）")


if __name__ == "__main__":
    asyncio.run(main())
