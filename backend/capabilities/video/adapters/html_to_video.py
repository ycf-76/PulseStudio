"""HTML 动画渲染器 v2 — 逐帧透明截图替代录屏+绿幕抠像（ADR-001）。

v1 的问题（已验证的成片缺陷）：
  - page.video 录屏：webm、帧率不可控、起录时机靠 sleep 兜底 → 卡顿/时长漂移
  - 绿幕 chromakey 抠像：文字抗锯齿边缘染绿、半透明元素报废、多一代画质损失

v2 路线（对齐 OVE / Remotion 的确定性逐帧模型）：
  1. 渲染 worker 子进程：Playwright 加载 HTML（模板契约：window.seekTo(t)）
  2. 逐帧：seekTo(t) → screenshot(omit_background=True) → PNG（带 alpha）
  3. worker 帧落盘后 os._exit 硬退出（本机 browser.close() 会挂起，见
     _render_worker.py 文档）；父进程 taskkill /T 清理进程树防泄漏
  4. 帧序列输出两份：
     - {stem}_frames/frame_000000.png  — 给 compose_with_overlays 做 rgba 叠加
     - {path}.mp4（黑底预览版）        — 给人工预览闸门播放

零件可缺：没装 playwright/ffmpeg → 降级返回 None（调用方处理）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

FRAME_FPS = 30
_FRAME_PATTERN = "frame_%06d.png"
# 每帧截图预算（含 seekTo evaluate），用于计算 worker 超时
_PER_FRAME_BUDGET = 0.6
_WORKER_LAUNCH_BUDGET = 60.0


def build_frame_times(duration: float, fps: int = FRAME_FPS) -> list[float]:
    """生成逐帧时间点：[0, duration)，帧数 = ceil(duration*fps)。

    duration ≤ 0 → [0.0]（单帧，保证渲染器至少出一帧）。
    """
    if duration <= 0:
        return [0.0]
    n = int(duration * fps + 0.999)  # ceil
    return [i / fps for i in range(n)]


def frames_dir_for(output_path: str | Path) -> Path:
    """帧序列目录约定：a/b.mp4 → a/b_frames/。"""
    p = Path(output_path)
    return p.parent / f"{p.stem}_frames"


def resolve_ffmpeg() -> str | None:
    """ffmpeg 解析链：系统 PATH → imageio-ffmpeg 包 → FFMPEG_PATH 环境变量。

    Playwright 自带的 ffmpeg-win64.exe 是精简版（仅 vp9，无 libx264/aac），
    不用于成片编码。
    """
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    env_override = os.environ.get("FFMPEG_PATH")
    if env_override and Path(env_override).is_file():
        return env_override
    # imageio-ffmpeg 包自带完整静态二进制（含 libx264）
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def _kill_process_tree(pid: int) -> None:
    """Windows taskkill 强杀进程树（带走 worker 的 chromium 孙进程）。"""
    if sys.platform != "win32":
        return
    subprocess.run(
        ["taskkill", "/PID", str(pid), "/T", "/F"],
        capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW,
    )


async def render_html_to_video(
    html_content: str,
    output_path: str | Path,
    width: int = 1080,
    height: int = 1920,
    duration: float = 5.0,
    fps: int = FRAME_FPS,
) -> Path | None:
    """逐帧渲染 HTML 动画 → 透明 PNG 帧序列 + 黑底预览 mp4。

    模板契约：HTML 内必须定义 window.seekTo(t: number)（见 overlay_templates v2）。

    Returns:
        预览 mp4 路径（帧序列在同目录 {stem}_frames/）；
        playwright 缺失 / worker 失败 / ffmpeg 缺失 → None。
    """
    worker = Path(__file__).parent / "_render_worker.py"
    if not worker.is_file():
        logger.error("render worker missing: %s", worker)
        return None

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    frames_dir = frames_dir_for(output)
    if frames_dir.exists():
        shutil.rmtree(frames_dir)
    frames_dir.mkdir(parents=True)

    n_frames = len(build_frame_times(duration, fps))
    html_file = frames_dir / "_overlay.html"
    html_file.write_text(html_content, encoding="utf-8")

    timeout = _WORKER_LAUNCH_BUDGET + n_frames * _PER_FRAME_BUDGET
    proc = await asyncio.create_subprocess_exec(
        sys.executable, str(worker),
        str(html_file), str(frames_dir),
        str(width), str(height), str(duration), str(fps),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        logger.error("render worker timed out after %.0fs, killing tree", timeout)
        _kill_process_tree(proc.pid)
        return None
    finally:
        # 正常退出也确保 chromium 孙进程被带走（worker 是硬退出不做清理）
        _kill_process_tree(proc.pid)

    status_file = frames_dir / "_worker_status.json"
    if proc.returncode != 0 or not status_file.is_file():
        logger.error("render worker failed, rc=%s", proc.returncode)
        return None
    try:
        status = json.loads(status_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.error("render worker status unreadable")
        return None
    if not status.get("ok"):
        logger.error("render worker error: %s", status.get("error"))
        return None

    frames = sorted(frames_dir.glob("frame_*.png"))
    if len(frames) != n_frames:
        logger.error(
            "frame count mismatch: %d produced, %d expected", len(frames), n_frames,
        )
        return None

    # 黑底预览版（人工闸门用；PNG 帧序列供 compose rgba 叠加）
    if not await _encode_preview(
        frames_dir, output, fps, width, height, duration=n_frames / fps,
    ):
        return None
    return output


async def _run_ffmpeg(cmd: list[str]) -> tuple[int, str]:
    """异步执行 ffmpeg，返回 (returncode, stderr 尾部)。"""
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    return proc.returncode, stderr.decode(errors="replace")[-600:]


async def _encode_preview(
    frames_dir: Path, output: Path, fps: int, width: int, height: int,
    duration: float | None = None,
) -> bool:
    """帧序列 → 黑底 mp4 预览（h264，正常播放器可看）。

    duration 缺省按帧数/fps 推算。color 源必须定长定率（d= r=）：
    曾用无限 color 源 + overlay=shortest=1，在本机 ffmpeg 上提前 EOF
    （36 帧只编出 8-11 帧）→ 成片叠加被截短（v4 demo 事故）。
    """
    ffmpeg = resolve_ffmpeg()
    if not ffmpeg:
        logger.warning("ffmpeg unavailable, skip preview encode (frames kept)")
        return False

    if duration is None:
        n = len(list(frames_dir.glob("frame_*.png")))
        duration = n / fps if n else 0.0

    rc, err = await _run_ffmpeg([
        ffmpeg, "-y", "-loglevel", "error",
        "-framerate", str(fps), "-i", str(frames_dir / _FRAME_PATTERN),
        "-f", "lavfi", "-i",
        f"color=c=black:s={width}x{height}:d={duration:.3f}:r={fps}",
        "-filter_complex",
        "[0:v]format=rgba[fg];[1:v][fg]overlay=shortest=1,format=yuv420p[v]",
        "-map", "[v]", "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        str(output),
    ])
    if rc != 0:
        logger.error("preview encode failed: %s", err)
        return False
    return True


async def _probe_video_size(video_path: str | Path) -> tuple[int, int] | None:
    """探测视频分辨率（PyAV 优先，ffprobe 兜底，失败返回 None）。"""
    try:
        import av

        def _av_probe() -> tuple[int, int]:
            with av.open(str(video_path)) as container:
                for stream in container.streams:
                    if stream.type == "video":
                        return (
                            stream.codec_context.width,
                            stream.codec_context.height,
                        )
            raise ValueError("no video stream")

        return await asyncio.to_thread(_av_probe)
    except Exception:
        pass

    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "csv=p=0:s=x", str(video_path),
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        w, _, h = stdout.decode().strip().partition("x")
        return int(w), int(h)
    except (OSError, ValueError):
        return None


async def compose_with_overlays(
    base_video: str | Path,
    overlay_videos: list[tuple[float, float, str | Path]],
    output_path: str | Path,
    fps: int = FRAME_FPS,
) -> Path:
    """主视频 + 多段时间窗 rgba 叠加 → 成片（替代 chromakey 路线）。

    叠加输入统一 scale 到主视频分辨率 — 防御画布错配：ffmpeg overlay
    会把超出主视频画幅的部分裁掉（渲染画布 > 底片时字幕会整体不可见）。

    Args:
        base_video: 底层口播视频
        overlay_videos: [(start, end, vid_path)] — vid_path 对应的
            {stem}_frames/ 帧目录存在则走 PNG rgba（无损）；
            否则按普通视频输入（兼容旧产物）。
        output_path: 成片输出
    """
    ffmpeg = resolve_ffmpeg()
    if ffmpeg is None:
        raise RuntimeError("ffmpeg not available (PATH / Playwright bundled / FFMPEG_PATH)")

    base_size = await _probe_video_size(base_video)
    scale_expr = (
        f"scale={base_size[0]}:{base_size[1]}" if base_size else ""
    )

    inputs: list[str] = ["-i", str(base_video)]
    overlay_specs: list[tuple[int, float, float]] = []  # (input_idx, start, end)

    for start, end, vp in overlay_videos:
        vp = Path(vp)
        fdir = frames_dir_for(vp)
        if fdir.exists() and any(fdir.glob("frame_*.png")):
            inputs += ["-framerate", str(fps), "-i", str(fdir / _FRAME_PATTERN)]
        else:
            # 兼容：无帧目录（旧产物/外部视频），按原样输入
            inputs += ["-i", str(vp)]
        overlay_specs.append((len(overlay_specs) + 1, start, end))

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    if not overlay_specs:
        # 无叠加：直接流复制（不重编码）
        rc, err = await _run_ffmpeg([ffmpeg, "-y", "-loglevel", "error",
                                     "-i", str(base_video), "-c", "copy", str(output)])
        if rc == 0:
            return output
        logger.warning("stream copy failed (%s), fallback to file copy", err[:150])
        shutil.copyfile(base_video, output)
        return output

    # 滤镜链：每个 overlay 缩放到底片尺寸、平移到 start，链式叠加，时间窗内启用
    filter_parts: list[str] = []
    cur = "[0:v]"
    for i, (idx, start, end) in enumerate(overlay_specs):
        olabel = f"[o{i}]"
        chain = f"[{idx}:v]format=rgba"
        if scale_expr:
            chain += f",{scale_expr}"
        chain += f",setpts=PTS-STARTPTS+{start}/TB{olabel}"
        filter_parts.append(chain)
        out = f"[v{i}]"
        filter_parts.append(
            f"{cur}{olabel}overlay=eof_action=pass:"
            f"enable='between(t,{start:.3f},{end:.3f})'{out}"
        )
        cur = out
    # 统一输出像素格式
    filter_parts.append(f"{cur}format=yuv420p[vout]")

    cmd = [
        ffmpeg, "-y", "-loglevel", "error", *inputs,
        "-filter_complex", ";".join(filter_parts),
        "-map", "[vout]", "-map", "0:a?", "-c:v", "libx264",
        "-preset", "medium", "-crf", "19", "-c:a", "copy",
        str(output),
    ]
    rc, err = await _run_ffmpeg(cmd)
    if rc != 0:
        raise RuntimeError(f"compose failed: {err}")
    return output
