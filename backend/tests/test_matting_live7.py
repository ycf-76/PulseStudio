"""10.25 live7 真机验收：E 主题自动选 + F xfade 转场链（54.1s 原素材全链）。

直接运行（backend 目录下）：
  .venv\\Scripts\\python.exe tests\\test_matting_live7.py

阶段：
  1. auto_deck + deck_theme="auto" + bottom_right + matting → 闸门停点
     （LLM 按内容气质自选主题 — slides.json theme ∈ 三主题白名单）
  2. approve → xfade 转场链合成 → 出片（时长 ≈ 源片）
  3. 抽帧：首个句边界切换点 前/中/后三帧（人工看转场与主题）
"""
import asyncio
import json
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

VIDEO = BACKEND / "uploads" / "videos" / "live_person_talk.mp4"
OUT_FRAMES = BACKEND / "outputs" / "talking_head_overlay" / "live7_frames"


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
    print(f"[live7] 源 {src_dur:.2f}s")

    # ── 阶段1：首轮（whisper 子进程 + LLM 自动选主题）→ 闸门停点 ──
    print("[live7] 阶段1 auto_deck + theme=auto（自动选主题）")
    ev1 = await run_round({"auto_deck": True, "deck_theme": "auto"})
    gate = next(e for e in ev1 if e.get("event") == "deck_preview_ready")
    assert gate["data"]["needs_confirmation"] is True
    slides_json = Path(gate["data"]["slides_json_path"])
    assert slides_json.is_file()
    data = json.loads(slides_json.read_text(encoding="utf-8"))
    theme = data.get("theme")
    assert theme in ("midnight", "cream", "sunset"), f"主题未落白名单: {theme}"
    rows_path = slides_json.parent / "transcript_rows.json"
    assert rows_path.is_file(), "句时间戳未持久化"
    n = gate["data"]["count"]
    print(f"[live7] 断言1 PASS：停点 + LLM 自选主题={theme} + {n} 张")

    # ── 阶段2：approve → xfade 转场合成 → 出片 ──
    print("[live7] 阶段2 approve（xfade 转场链合成）")
    ev2 = await run_round({
        "deck_confirm": "approve", "deck_slides_json": str(slides_json),
    })
    out2 = Path(next(
        e for e in ev2 if e.get("event") == "pip_done"
    )["data"]["output_path"])
    dur2 = await probe_duration(str(out2))
    assert abs(dur2 - src_dur) < 1.0, f"时长偏差: {dur2} vs {src_dur}"
    print(f"[live7] 断言2 PASS：成片 {out2.name}（{dur2:.2f}s ≈ 源 {src_dur:.2f}s）")

    # ── 阶段3：抽帧 — 首个句边界切换点 前/中/后 ──
    from capabilities.video.skills.talking_head_overlay import (
        _slide_durations_from_transcript,
    )

    rows = json.loads(rows_path.read_text(encoding="utf-8"))
    durs = _slide_durations_from_transcript(rows, n, src_dur)
    b0 = durs[0]
    OUT_FRAMES.mkdir(parents=True, exist_ok=True)
    for tag, t in (("before", b0 - 0.8), ("mid", b0 + 0.25), ("after", b0 + 1.2)):
        subprocess.run([
            ff, "-y", "-ss", f"{max(0.0, t):.2f}", "-i", str(out2),
            "-frames:v", "1", str(OUT_FRAMES / f"switch_{tag}.png"),
        ], capture_output=True)
    (OUT_FRAMES / "slides_live7.json").write_text(
        json.dumps({**data, "durations": durs}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    print(f"[live7] PASS：theme={theme}，durations={['%.1f' % d for d in durs]}")
    print(f"[live7] 抽帧素材在 {OUT_FRAMES}（switch_before/mid/after 人工看转场）")


if __name__ == "__main__":
    asyncio.run(main())
