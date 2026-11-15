"""10.20 二期真机验收：真人视频 → 抠像 + 按讲解内容生成的幻灯片。

直接运行（backend 目录下）：
  .venv\\Scripts\\python.exe tests\\test_matting_live2.py

全链：whisper 转录 → LLM 要点幻灯片（deck_orchestrator）→
talking_head_overlay(pip+matting+deck_images) → 成片 + 抽帧。
"""
import asyncio
import json
import os
import sys
from pathlib import Path

# ct2 与系统旧 cuDNN 冲突 native fastfail → whisper 强制 CPU
# （RVM 走 torch 自带 cuDNN 9.1 不受影响，2026-10-29 实证）
os.environ.setdefault("WHISPER_DEVICE", "cpu")

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

VIDEO = BACKEND / "uploads" / "videos" / "live_person_talk.mp4"
WORK = BACKEND / "outputs" / "matting_live2"


async def _transcribe_stage() -> None:
    """阶段 1（独立子进程）：whisper 转录 → transcript.json。

    ct2 构造 WhisperModel 会把系统旧 cuDNN DLL 拉进进程，同进程再跑
    torch CUDA 必 fastfail（0xC0000409，2026-10-29 实证）→ 转录必须
    与 RVM 分进程。
    """
    from capabilities.video.adapters.whisper_transcribe import transcribe

    segs = await asyncio.to_thread(transcribe, str(VIDEO), "small", "zh")
    payload = [
        {"start": s.start, "end": s.end, "text": s.text.strip()}
        for s in segs
    ]
    (WORK / "transcript.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8",
    )
    print(f"[live2] 转录完成 {len(payload)} 段 → transcript.json")


async def main() -> None:
    import json as _json

    transcript_file = WORK / "transcript.json"
    if not transcript_file.is_file():
        print("[live2] 先跑 --transcribe 生成 transcript.json")
        sys.exit(2)
    rows = _json.loads(transcript_file.read_text(encoding="utf-8"))
    text = " ".join(f"[{r['start']:.0f}-{r['end']:.0f}s] {r['text']}" for r in rows)
    print(f"[live2] 1/3 转录就绪 {len(rows)} 段：{text[:120]}…")
    if not text.strip():
        print("[live2] FAIL：转录为空")
        sys.exit(1)

    print("[live2] 2/3 LLM 生成要点幻灯片…")
    from app.adapters.deepseek import DeepSeekAdapter
    from app.config import Settings
    from capabilities.video.services.deck_orchestrator import generate_deck_slides

    llm = DeepSeekAdapter(api_key=Settings().deepseek_api_key)
    deck_dir = WORK / "deck"
    try:
        slides = await generate_deck_slides(
            text, llm, deck_dir,
            width=1080, height=1920,
            person_clearance_px=640,  # 人物小窗区（556px+margin）让位
        )
    except Exception as e:
        print(f"[live2] LLM 异常：{e}")
        slides = []
    if not slides:
        # LLM 降级（402 余额不足等）：转录直切 6 组要点 — 内容粗糙但
        # 不阻塞排版/时长验收（降级哲学，与 deck 服务契约一致）
        print("[live2] LLM 不可用 → 转录直切 fallback 幻灯片")
        from capabilities.video.services.deck_orchestrator import (
            render_deck_slides,
        )

        sents = [r["text"] for r in rows if r["text"].strip()]
        chunk = max(1, len(sents) // 6)
        groups = [
            sents[i:i + chunk] for i in range(0, len(sents), chunk)
        ][:6]
        fake = [
            {"title": (g[0][:12] if g else f"要点 {i+1}"),
             "bullets": [s[:18] for s in g[:3]] or ["（无内容）"]}
            for i, g in enumerate(groups)
        ]
        slides = await asyncio.to_thread(
            render_deck_slides, fake, deck_dir, 1080, 1920, 640,
        )
    if not slides:
        print("[live2] FAIL：幻灯片生成降级为空")
        sys.exit(1)
    print(f"[live2] 幻灯片 {len(slides)} 张：{[p.name for p in slides]}")

    print("[live2] 3/3 抠像 + 融合合成（RVM GPU，约 6 分钟）…")
    from capabilities.video.skills.talking_head_overlay import (
        TalkingHeadOverlaySkill,
    )

    events = []
    async for ev in TalkingHeadOverlaySkill().execute({
        "video_path": str(VIDEO),
        "gate_mode": "auto_confirm",
        "pip": True,
        "matting": True,
        "deck_images": [str(p) for p in slides],
        "slide_duration": 9.0,
        "pip_scale": 0.3,
        "pip_position": "bottom_right",
        "bg_color": "#141428",
    }):
        events.append(ev)
        name = ev.get("event") or ("success" if ev.get("success") else "error")
        print(f"[live2] event={name} {ev.get('message', ev.get('error', ''))}")

    output = None
    for ev in events:
        if ev.get("event") == "pip_done":
            output = Path(ev["data"]["output_path"])
    if output is None or not output.is_file():
        print("[live2] FAIL：未产出成片")
        sys.exit(1)

    from capabilities.video.adapters.silence_detect import probe_duration

    out_dur = await probe_duration(str(output))
    print(f"[live2] 成片：{output}（{out_dur:.2f}s）")

    from capabilities.video.adapters.html_to_video import resolve_ffmpeg
    import subprocess

    ff = resolve_ffmpeg()
    frames_dir = output.parent / f"{output.stem}_frames"
    frames_dir.mkdir(exist_ok=True)
    for tag, t in (("first", 1.0), ("mid", out_dur / 2), ("end", out_dur - 1.0)):
        subprocess.run([
            ff, "-y", "-ss", f"{t:.2f}", "-i", str(output),
            "-frames:v", "1", str(frames_dir / f"{tag}.png"),
        ], capture_output=True)
    print(f"[live2] 抽帧：{[p.name for p in sorted(frames_dir.glob('*.png'))]}")
    print("[live2] PASS：10.20 二期真机验收完成")


if __name__ == "__main__":
    WORK.mkdir(parents=True, exist_ok=True)
    if "--transcribe" in sys.argv:
        asyncio.run(_transcribe_stage())
    else:
        asyncio.run(main())
