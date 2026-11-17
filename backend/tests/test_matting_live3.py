"""10.21 真机验收：auto_deck 一键降级路径（DeepSeek 402 受限）。

直接运行（backend 目录下）：
  .venv\\Scripts\\python.exe tests\\test_matting_live3.py

链路（全自动，无 transcript、无脚本手串）：
  auto_deck=True → skill 内 whisper 子进程转录（ct2 DLL 隔离，10.20 实证②）
  → LLM 要点幻灯片（DeepSeek 402 → generate 返 []）
  → deck_fallback 事件 → 纯色底主画面 + RVM 抠像融合 → 成片。

预期：deck_fallback 事件出现、成片时长 ≈ 源 54.1s、纯色底 + 右下角人物。
LLM 充值后补跑全链出片（deck_ready 路径 + 三主题首帧人工看版式）。
"""

import asyncio
import os
import subprocess
import sys
from pathlib import Path

# 转录已隔离进 skill 子进程（自动 WHISPER_DEVICE=cpu），
# 主进程 GPU 全留给 RVM，无需再设环境变量

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

VIDEO = BACKEND / "uploads" / "videos" / "live_person_talk.mp4"


async def main() -> None:
    from capabilities.video.adapters.silence_detect import probe_duration
    from capabilities.video.skills.talking_head_overlay import (
        TalkingHeadOverlaySkill,
    )

    src_dur = await probe_duration(str(VIDEO))
    print(f"[live3] 源视频 {src_dur:.2f}s，开始 auto_deck 一键降级链验收")

    events = []
    async for ev in TalkingHeadOverlaySkill().execute(
        {
            "video_path": str(VIDEO),
            "gate_mode": "auto_confirm",
            "pip": True,
            "matting": True,
            "auto_deck": True,
            "deck_theme": "midnight",
            "slide_duration": 9.0,
            "pip_scale": 0.3,
            "pip_position": "bottom_right",
            "bg_color": "#141428",
            # 故意不传 transcript：验 skill 内 whisper 子进程兜底通道
        }
    ):
        events.append(ev)
        name = ev.get("event") or ("success" if ev.get("success") else "error")
        print(f"[live3] event={name} {ev.get('message', ev.get('error', ''))}")

    # ── 断言 1：deck_fallback 事件如实广播 ──
    names = [ev.get("event") for ev in events]
    assert "deck_fallback" in names, f"应广播 deck_fallback，实际事件: {names}"
    assert "deck_ready" not in names, "LLM 402 下不应出现 deck_ready"
    print("[live3] 断言1 PASS：deck_fallback 如实广播，未谎报 deck_ready")

    output = None
    for ev in events:
        if ev.get("event") == "pip_done":
            output = Path(ev["data"]["output_path"])
    # ── 断言 2：降级仍出片（绝不阻断）──
    assert output is not None and output.is_file(), f"未产出成片: events={names}"
    out_dur = await probe_duration(str(output))
    print(f"[live3] 断言2 PASS：成片 {output}（{out_dur:.2f}s，源 {src_dur:.2f}s）")

    # ── 断言 3：时长硬限（-t 生效，偏差 < 1s）──
    assert abs(out_dur - src_dur) < 1.0, f"时长偏差过大: {out_dur} vs {src_dur}"

    from capabilities.video.adapters.html_to_video import resolve_ffmpeg

    ff = resolve_ffmpeg()
    frames_dir = output.parent / f"{output.stem}_frames"
    frames_dir.mkdir(exist_ok=True)
    for tag, t in (("first", 1.0), ("mid", out_dur / 2), ("end", out_dur - 1.0)):
        subprocess.run(
            [
                ff,
                "-y",
                "-ss",
                f"{t:.2f}",
                "-i",
                str(output),
                "-frames:v",
                "1",
                str(frames_dir / f"{tag}.png"),
            ],
            capture_output=True,
        )
    print(f"[live3] 抽帧：{[p.name for p in sorted(frames_dir.glob('*.png'))]}")
    print("[live3] PASS：10.21 auto_deck 降级链真机验收完成" "（LLM 全链出片待充值后补验）")


if __name__ == "__main__":
    asyncio.run(main())
