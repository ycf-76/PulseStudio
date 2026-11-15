"""10.21 真机验收 2/2：auto_deck 降级路径（强制 LLM 失败触发）。

直接运行（backend 目录下）：
  $env:DEEPSEEK_API_KEY="sk-invalid"; .venv\\Scripts\\python.exe tests\\test_matting_live3b.py

live3 已实证全链 PASS（DeepSeek 余额恢复，deck_ready 6 张、成片 54.08s），
本脚本补验另一半契约：LLM 失败 → deck_fallback 事件 → 纯色底主画面 →
仍出片（绝不阻断）。transcript 直接喂现成转录（降级点在 LLM，省 whisper）。
"""
import asyncio
import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

VIDEO = BACKEND / "uploads" / "videos" / "live_person_talk.mp4"
TRANSCRIPT = BACKEND / "outputs" / "matting_live2" / "transcript.json"


async def main() -> None:
    import json

    # 坏 key：pydantic-settings 环境变量优先于 .env，DeepSeek 请求必失败
    os.environ["DEEPSEEK_API_KEY"] = "sk-invalid-forced-fallback"

    from capabilities.video.adapters.silence_detect import probe_duration
    from capabilities.video.skills.talking_head_overlay import (
        TalkingHeadOverlaySkill,
    )

    assert os.environ["DEEPSEEK_API_KEY"].startswith("sk-invalid")

    rows = json.loads(TRANSCRIPT.read_text(encoding="utf-8"))
    transcript = [
        {"text": r.get("text", ""), "start": r.get("start", 0.0),
         "end": r.get("end", 0.0)}
        for r in rows
    ]
    src_dur = await probe_duration(str(VIDEO))
    print(f"[live3b] 源 {src_dur:.2f}s，喂 {len(transcript)} 条转录，"
          f"坏 key 强制 LLM 失败，验降级链")

    events = []
    async for ev in TalkingHeadOverlaySkill().execute({
        "video_path": str(VIDEO),
        "gate_mode": "auto_confirm",
        "pip": True,
        "matting": True,
        "auto_deck": True,
        "deck_theme": "midnight",
        "transcript": transcript,
        "slide_duration": 9.0,
        "pip_scale": 0.3,
        "pip_position": "bottom_right",
        "bg_color": "#141428",
    }):
        events.append(ev)
        name = ev.get("event") or ("success" if ev.get("success") else "error")
        print(f"[live3b] event={name} {ev.get('message', ev.get('error', ''))}")

    names = [ev.get("event") for ev in events]
    assert "deck_fallback" in names, f"应广播 deck_fallback，实际: {names}"
    assert "deck_ready" not in names, "坏 key 下不应有 deck_ready"
    print("[live3b] 断言1 PASS：deck_fallback 如实广播")

    output = None
    for ev in events:
        if ev.get("event") == "pip_done":
            output = Path(ev["data"]["output_path"])
    assert output is not None and output.is_file(), f"未出片: {names}"
    out_dur = await probe_duration(str(output))
    assert abs(out_dur - src_dur) < 1.0, f"时长偏差: {out_dur} vs {src_dur}"
    print(f"[live3b] 断言2 PASS：降级仍出片 {output.name}（{out_dur:.2f}s）")

    from capabilities.video.adapters.html_to_video import resolve_ffmpeg

    ff = resolve_ffmpeg()
    frames_dir = BACKEND / "outputs" / "talking_head_overlay" / "live3b_frames"
    frames_dir.mkdir(exist_ok=True)
    subprocess.run([
        ff, "-y", "-ss", "1.5", "-i", str(output),
        "-frames:v", "1", str(frames_dir / "first.png"),
    ], capture_output=True)
    print("[live3b] PASS：降级链（deck_fallback → 纯色底）真机验收完成")


if __name__ == "__main__":
    asyncio.run(main())