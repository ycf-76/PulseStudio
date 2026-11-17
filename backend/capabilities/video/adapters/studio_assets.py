"""原片抽帧 adapter — Video Studio 制作地基（Task 1）。

纯工具模块：原片 → JPEG 帧序列 + 时长/宽高元信息探测。
不含任何风格常量（颜色/字体等归 overlay_templates / themes 层）。

元信息探测链（本机无 ffprobe，imageio_ffmpeg 只带 ffmpeg）：
  1. PyAV（av 包）读容器 duration 与 video stream width/height
  2. 失败兜底：`ffmpeg -i` stderr 的 Duration 行 / 分辨率字段

Windows 编码安全：subprocess 一律 str 参数列表（禁止 shell=True），
ffmpeg 输出重定向到临时文件再读（errors="replace"）。
"""

from __future__ import annotations

import logging
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from capabilities.video.adapters.html_to_video import resolve_ffmpeg

logger = logging.getLogger(__name__)

FRAME_PATTERN = "frame_%06d.jpg"


@dataclass
class FrameSeqInfo:
    """帧序列产物元信息。

    pattern 为实际写盘命名（frame_%06d.jpg，frame_ 前缀 + 6 位数字）。
    注意 frames/ 目录中同名 .png 文件是渲染器的截图产物（渲染期按 timeline
    fps 生成），素材引用一律用本 pattern（jpg 命名）。
    """

    frame_dir: Path
    count: int
    fps: float
    width: int
    height: int
    pattern: str


def extract_frames(video_path: str, out_dir: Path, fps: float = 24.0) -> FrameSeqInfo:
    """原片 → frame_%06d.jpg 帧序列（-vf fps=N -qscale:v 2，start_number=0）。

    输出目录中旧 frame_*.jpg 先清除，保证 count 精确。

    Raises:
        RuntimeError: ffmpeg 缺失、抽帧失败或 0 帧产出。
    """
    ffmpeg = resolve_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("ffmpeg not available (PATH / imageio-ffmpeg / FFMPEG_PATH)")

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("frame_*.jpg"):
        old.unlink()

    width, height = _probe_size(video_path)

    cmd = [
        ffmpeg,
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(video_path),
        "-vf",
        f"fps={fps:g}",
        "-qscale:v",
        "2",
        "-start_number",
        "0",
        str(out_dir / FRAME_PATTERN),
    ]
    _run_ffmpeg(cmd)

    frames = sorted(out_dir.glob("frame_*.jpg"))
    if not frames:
        raise RuntimeError(f"extract_frames produced no frames: {video_path}")
    return FrameSeqInfo(
        frame_dir=out_dir,
        count=len(frames),
        fps=float(fps),
        width=width,
        height=height,
        pattern=FRAME_PATTERN,
    )


def probe_duration(media_path: str) -> float:
    """媒体时长（秒）：PyAV 容器 duration 优先，ffmpeg -i stderr 兜底。

    Raises:
        RuntimeError: 两条探测链都失败。
    """
    try:
        import av

        with av.open(str(media_path)) as container:
            if container.duration:
                return container.duration / 1_000_000.0
    except Exception as exc:  # 探测链必须走完兜底，吞掉一切 PyAV 异常
        logger.debug("av duration probe failed for %s: %s", media_path, exc)

    dur = _duration_from_ffmpeg_stderr(media_path)
    if dur is None:
        raise RuntimeError(f"probe_duration failed (PyAV + ffmpeg fallback): {media_path}")
    return dur


def _probe_size(video_path: str) -> tuple[int, int]:
    """宽高：PyAV 优先，ffmpeg -i stderr 兜底，仍失败返回 (0, 0)。"""
    try:
        import av

        with av.open(str(video_path)) as container:
            for stream in container.streams:
                if stream.type == "video":
                    return (
                        int(stream.codec_context.width),
                        int(stream.codec_context.height),
                    )
    except Exception as exc:
        logger.debug("av size probe failed for %s: %s", video_path, exc)
    return _size_from_ffmpeg_stderr(video_path)


def _run_ffmpeg(cmd: list[str]) -> None:
    """执行 ffmpeg；stdout/stderr 落临时文件后读回（Windows 编码安全）。

    Raises:
        RuntimeError: 非零退出码（附 stderr 尾部）。
    """
    with tempfile.TemporaryDirectory() as td:
        log = Path(td) / "ffmpeg.log"
        with open(log, "wb") as f:
            proc = subprocess.run(cmd, stdout=f, stderr=f, check=False)
        text = log.read_text(encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed (rc={proc.returncode}): {text[-600:]}")


_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):([\d.]+)")
_SIZE_RE = re.compile(r",\s*(\d{2,5})x(\d{2,5})[,\s]")


def _ffmpeg_info_stderr(media_path: str) -> str:
    """`ffmpeg -i`（无输出文件，rc=1 属预期）的 stderr 文本。"""
    ffmpeg = resolve_ffmpeg()
    if not ffmpeg:
        return ""
    with tempfile.TemporaryDirectory() as td:
        log = Path(td) / "ffmpeg_i.log"
        with open(log, "wb") as f:
            subprocess.run(
                [ffmpeg, "-hide_banner", "-i", str(media_path)],
                stdout=f,
                stderr=f,
                check=False,
            )
        return log.read_text(encoding="utf-8", errors="replace")


def _duration_from_ffmpeg_stderr(media_path: str) -> float | None:
    """从 `ffmpeg -i` stderr 的 Duration 行解析秒数。"""
    m = _DURATION_RE.search(_ffmpeg_info_stderr(media_path))
    if not m:
        return None
    h, mnt, s = m.groups()
    return int(h) * 3600 + int(mnt) * 60 + float(s)


def _size_from_ffmpeg_stderr(video_path: str) -> tuple[int, int]:
    """从 `ffmpeg -i` stderr 的视频流行解析 WxH（如 `320x568,`）。"""
    m = _SIZE_RE.search(_ffmpeg_info_stderr(video_path))
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


# ── 底部烧录元素探测 ──────────────────────────────────────────────────
# 依据：烧录字幕/台标/水印是高对比密集文本，贴底排布 → 底部区域边缘密度
# 显著高于画面中部；自然画面（人脸/背景）边缘分布相对均匀。多帧投票压住
# 背景复杂度噪声（个别镜头底部有杂物不致误判）。
_BOTTOM_RATIO_TRIGGER = 1.5  # 单帧判定：底部密度 / 中部密度 阈值
_BOTTOM_VOTE_RATIO = 0.6  # 投票判定：≥60% 采样帧命中才报疑似


def _edge_densities(gray: "np.ndarray") -> tuple[float, float]:
    """灰度图 → (中部 50% 区域梯度密度, 底部 25% 区域梯度密度)。

    梯度用相邻像素差分（numpy，无 scipy 依赖）：gy 行间差分 (h-1,w)、
    gx 列间差分 (h,w-1)——行数不等，分别按行区间求均值再相加。
    """
    import numpy as np

    h = gray.shape[0]
    gy = np.abs(np.diff(gray.astype("float32"), axis=0))  # (h-1, w)
    gx = np.abs(np.diff(gray.astype("float32"), axis=1))  # (h, w-1)
    mid0, bot0 = h // 4, int(h * 0.75)
    mid = float(gy[mid0:bot0].mean() + gx[mid0:bot0].mean())
    bottom = float(gy[bot0:].mean() + gx[bot0:].mean())
    return mid, bottom


def probe_bottom_elements(frames_dir: str, sample: int = 5) -> dict:
    """探测帧轨底部是否疑似带烧录字幕/水印/台标（36.9s 实跑回灌）。

    从 frames_dir 现有 frame_*.jpg 均匀采样 sample 张，逐帧算
    底部 25% 与中部 50% 区域的梯度幅值密度之比；≥60% 采样帧比值 > 1.5
    判 suspected=True。任何失败（空轨/读图失败）返回 error 字段不抛异常——
    本探测是尽力而为的提示，不允许打断 prepare_assets。
    """
    import numpy as np
    from PIL import Image

    out: dict = {"suspected": False, "error": None}
    try:
        frames = sorted(Path(frames_dir).glob("frame_*.jpg"))
        if not frames:
            out["error"] = f"帧轨为空或目录不存在: {frames_dir}"
            return out
        n = len(frames)
        sample = max(1, min(int(sample), n))
        idxs = sorted({round(i * (n - 1) / max(1, sample - 1)) for i in range(sample)})
        ratios: list[float] = []
        for i in idxs:
            with Image.open(frames[i]) as img:
                gray = np.asarray(img.convert("L"))
            mid, bottom = _edge_densities(gray)
            ratios.append(bottom / max(mid, 1e-6))
        hits = sum(1 for r in ratios if r > _BOTTOM_RATIO_TRIGGER)
        out["suspected"] = bool(ratios) and hits / len(ratios) >= _BOTTOM_VOTE_RATIO
        out["ratio_stats"] = {
            "min": round(min(ratios), 3),
            "mean": round(float(np.mean(ratios)), 3),
        }
        out["sampled"] = [frames[i].name for i in idxs]
        return out
    except Exception as exc:
        out["suspected"] = False
        out["error"] = f"底部元素探测失败: {exc}"
        return out
