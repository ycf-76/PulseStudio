"""Video Studio 确定性场景渲染 adapter（Task 2）。

场景页契约：window.seekTo(t) 设置第 t 秒确切状态。流程：
  子进程 Playwright 逐帧截图（studio_render_worker.py，file:// 打开场景）
  → 可选 ffmpeg 帧序编码（h264 yuv420p）+ 音轨混音（-shortest）；
  无 audio_path 时合成静音轨。

不含任何风格常量（颜色/字体/动效归 overlay_templates / themes 层）。

Windows 约束：subprocess 一律 str 参数列表 + CREATE_NO_WINDOW（禁止
shell=True）；超时 taskkill /T /F 杀进程树（先 taskkill 再 communicate 收尸）。
成败判定以 worker 落盘的 _worker_status.json 为准，不靠返回码猜测。
"""

from __future__ import annotations

import json
import logging
import math
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from capabilities.video.adapters.html_to_video import resolve_ffmpeg

logger = logging.getLogger(__name__)

FRAME_PATTERN = "frame_%06d.png"
# 每帧截图预算（含 seekTo evaluate），用于计算 worker 超时
_PER_FRAME_BUDGET = 1.0
_WORKER_LAUNCH_BUDGET = 60.0


@dataclass
class RenderResult:
    """场景渲染产物。video_path 仅在请求 out_video 且编码成功时非空。"""

    frames_dir: Path
    count: int
    fps: float
    elapsed: float
    video_path: Path | None


def render_scene(
    scene_html: Path,
    frames_dir: Path,
    duration: float,
    fps: int = 30,
    width: int = 1080,
    height: int = 1920,
    audio_path: str | None = None,
    out_video: Path | None = None,
) -> RenderResult:
    """确定性渲染场景 → frame_%06d.png 帧序列（+ 可选混音 mp4）。

    Raises:
        RuntimeError: worker 缺失/超时/失败、帧数不符或 ffmpeg 编码失败。
    """
    scene_html = Path(scene_html)
    frames_dir = Path(frames_dir)
    worker = Path(__file__).parent / "studio_render_worker.py"
    if not worker.is_file():
        raise RuntimeError(f"studio render worker missing: {worker}")

    # 与 studio_review 的帧完整性公式完全一致（math.ceil）——int(x+0.999) 在
    # duration*fps 略高于整数时会少 1 帧，review 误报缺帧打断自查闭环
    n_frames = max(1, math.ceil(duration * fps))
    frames_dir.mkdir(parents=True, exist_ok=True)
    # 清旧帧 + 旧状态文件：复用目录重渲时防止残留帧污染（image2 按 %06d
    # 顺序读到首个缺号为止，残留的高号帧会被误编入成片）
    for old in frames_dir.glob("frame_*.png"):
        old.unlink()
    status_file = frames_dir / "_worker_status.json"
    status_file.unlink(missing_ok=True)

    timeout = _WORKER_LAUNCH_BUDGET + n_frames * _PER_FRAME_BUDGET
    t0 = time.time()
    proc = subprocess.Popen(
        [
            sys.executable,
            str(worker),
            str(scene_html),
            str(frames_dir),
            str(width),
            str(height),
            str(duration),
            str(fps),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    try:
        _, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        logger.error("studio render worker timed out after %.0fs, killing tree", timeout)
        _kill_process_tree(proc.pid)
        try:
            proc.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
        raise RuntimeError(f"studio render worker timed out after {timeout:.0f}s")
    finally:
        # 正常退出也确保 chromium 孙进程被带走（worker 是硬退出不做清理）
        _kill_process_tree(proc.pid)

    err_tail = (stderr or b"").decode(errors="replace")[-600:]
    if proc.returncode != 0:
        raise RuntimeError(f"studio render worker failed (rc={proc.returncode}): {err_tail}")
    try:
        status = json.loads(status_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        raise RuntimeError("studio render worker status unreadable")
    if not status.get("ok"):
        raise RuntimeError(f"studio render worker error: {status.get('error')}")
    count = int(status.get("count") or 0)
    if count != n_frames:
        raise RuntimeError(f"frame count mismatch: {count} produced, {n_frames} expected")
    elapsed = float(status.get("elapsed", time.time() - t0))

    video_path: Path | None = None
    if out_video is not None:
        video_path = _encode_video(
            frames_dir,
            Path(out_video),
            fps,
            audio_path,
            video_duration=n_frames / fps,
        )
    return RenderResult(
        frames_dir=frames_dir,
        count=count,
        fps=float(fps),
        elapsed=elapsed,
        video_path=video_path,
    )


def _encode_video(
    frames_dir: Path,
    out_video: Path,
    fps: int,
    audio_path: str | None,
    video_duration: float,
) -> Path:
    """帧序列 → h264 yuv420p mp4；-shortest 混音（无音轨时合成静音轨）。"""
    ffmpeg = resolve_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("ffmpeg not available (PATH / imageio-ffmpeg / FFMPEG_PATH)")

    out_video.parent.mkdir(parents=True, exist_ok=True)
    if audio_path:
        audio_input = ["-i", str(audio_path)]
    else:
        # 静音轨必须定长：无限 anullsrc + -shortest 在本机 ffmpeg 上会过冲
        # （自验 0.5s 视频编出 0.998s；教训同 html_to_video「无限源必须定长」）
        # → -t 把输入截到视频时长，-shortest 语义不变
        audio_input = [
            "-f",
            "lavfi",
            "-t",
            f"{video_duration:.3f}",
            "-i",
            "anullsrc=r=44100:cl=mono",
        ]

    cmd = [
        ffmpeg,
        "-y",
        "-loglevel",
        "error",
        "-r",
        str(fps),
        "-i",
        str(frames_dir / FRAME_PATTERN),
        *audio_input,
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-shortest",
        str(out_video),
    ]
    # ffmpeg 输出落临时文件再读（Windows 编码安全，与 studio_assets 同法）
    with tempfile.TemporaryDirectory() as td:
        log = Path(td) / "ffmpeg.log"
        with open(log, "wb") as f:
            proc = subprocess.run(
                cmd,
                stdout=f,
                stderr=f,
                check=False,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
        text = log.read_text(encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(f"studio encode failed (rc={proc.returncode}): {text[-600:]}")
    return out_video


def _kill_process_tree(pid: int) -> None:
    """Windows taskkill 强杀进程树（带走 worker 的 chromium 孙进程）。"""
    if sys.platform != "win32":
        return
    subprocess.run(
        ["taskkill", "/PID", str(pid), "/T", "/F"],
        capture_output=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
