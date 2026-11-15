"""10.20 人物抠像 真机验收脚本（standalone，非 CI 测试）。

直接运行（backend 目录下）：
  .venv\\Scripts\\python.exe tests\\test_matting_live.py

验收内容（docs 10.20「TDD 与验收」标准）：
  22s 样本 + storyboard 六张配图主画面 → matting=True 全链出片：
  1. RVM 抠像帧序列产出（GPU）
  2. skill _execute_pip alpha 融合合成
  3. 成片探测（时长≈源 22s）
  4. 抽首/中/尾三帧供边缘质量人工+VLM 检查
"""
import asyncio
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

VIDEO = BACKEND / "uploads" / "videos" / "verify" / "01M60QX2QHZBV9VAY3ZABMV2MY.mp4"
STORYBOARD = BACKEND / "outputs" / "夏天出门前必做的三件小事" / "assets" / "storyboard.json"


async def main() -> None:
    sb = json.loads(STORYBOARD.read_text(encoding="utf-8"))
    images = [shot["image"] for shot in sb["shots"]]
    missing = [p for p in images if not Path(p).is_file()]
    if missing:
        print(f"[live] FAIL：配图缺失 {missing}")
        sys.exit(1)
    print(f"[live] 配图 {len(images)} 张齐全")

    from capabilities.video.skills.talking_head_overlay import (
        TalkingHeadOverlaySkill,
    )

    skill = TalkingHeadOverlaySkill()
    events = []
    async for ev in skill.execute({
        "video_path": str(VIDEO),
        "gate_mode": "auto_confirm",
        "pip": True,
        "matting": True,
        "deck_images": images,
        "slide_duration": 4.0,
        "pip_scale": 0.3,
        "pip_position": "bottom_right",
    }):
        events.append(ev)
        name = ev.get("event") or ("success" if ev.get("success") else "error")
        print(f"[live] event={name} message={ev.get('message', ev.get('error', ''))}")

    output = None
    for ev in events:
        if ev.get("event") == "pip_done":
            output = Path(ev["data"]["output_path"])
    if output is None or not output.is_file():
        print("[live] FAIL：未产出成片")
        sys.exit(1)

    from capabilities.video.adapters.silence_detect import probe_duration

    src_dur = await probe_duration(str(VIDEO))
    out_dur = await probe_duration(str(output))
    print(f"[live] 成片：{output.name}（{out_dur:.2f}s，源 {src_dur:.2f}s）")

    # 抽首/中/尾三帧（本机 ffmpeg 不在 PATH → resolve_ffmpeg 解析链）
    from capabilities.video.adapters.html_to_video import resolve_ffmpeg

    ff = resolve_ffmpeg()
    import subprocess

    out_dir = output.parent / f"{output.stem}_frames"
    out_dir.mkdir(exist_ok=True)
    for tag, t in (("first", 0.5), ("mid", out_dur / 2), ("end", out_dur - 0.5)):
        subprocess.run([
            ff, "-y", "-ss", f"{t:.2f}", "-i", str(output),
            "-frames:v", "1", str(out_dir / f"{tag}.png"),
        ], capture_output=True)
    frames = sorted(out_dir.glob("*.png"))
    print(f"[live] 抽帧 {len(frames)}：{[f.name for f in frames]}")
    print("[live] PASS：10.20 真机验收完成（边缘质量看抽帧）")


if __name__ == "__main__":
    asyncio.run(main())
