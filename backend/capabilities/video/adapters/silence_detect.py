"""Silence detection adapter — ffmpeg silencedetect wrapper.

设计依据：
  - fillercut 的 VAD 方案和 cutlist 的 GAP_CUT 参数
  - ffmpeg silencedetect 滤镜（对齐 ffmpeg-cookbook silencedetect 用法）
  - 异步执行对齐 video_compose.py 的 _run 模式

核心功能：
  - 用 ffmpeg silencedetect 检测视频中的静音段
  - 返回结构化的静音片段列表 [(start, end, duration), ...]
  - 支持自定义阈值和最短时长
"""

from __future__ import annotations

import asyncio
import logging
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class SilenceDetectError(Exception):
    """静音检测错误。"""

    def __init__(self, error_type: str, message: str):
        self.error_type = error_type
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class SilenceSegment:
    """一个静音片段。"""

    start: float
    end: float
    duration: float

    def __post_init__(self):
        if self.duration < 0:
            raise ValueError(f"SilenceSegment duration must be >= 0, got {self.duration}")
        if self.start < 0:
            raise ValueError(f"SilenceSegment start must be >= 0, got {self.start}")


async def _run(cmd: list[str]) -> str:
    """异步执行命令（对齐 video_compose.py 的 _run 模式）。

    cmd[0] 为字面量 "ffmpeg"/"ffprobe" 时先走 resolve_ffmpeg 解析链
    （PATH → FFMPEG_PATH → imageio-ffmpeg）— 本机 ffmpeg/ffprobe 均不在
    PATH，裸执行 WinError 2（2026-10-30 live9 实证，对齐 smart_cut
    _run_ffmpeg 的 10.20 修复模式）。
    优先 asyncio.create_subprocess_exec 异步非阻塞；
    Windows + uvicorn --reload 下 SelectorEventLoop 不支持
    create_subprocess_exec 时回退 asyncio.to_thread。
    """
    if cmd and cmd[0] in ("ffmpeg", "ffprobe"):
        from capabilities.video.adapters.html_to_video import resolve_ffmpeg

        exe = resolve_ffmpeg()
        if exe:
            cmd = [exe, *cmd[1:]]
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        returncode = process.returncode
    except NotImplementedError:
        result = await asyncio.to_thread(
            subprocess.run,
            cmd,
            capture_output=True,
        )
        stdout, stderr, returncode = result.stdout, result.stderr, result.returncode

    if returncode != 0:
        tail = "\n".join((stderr.decode(errors="replace") or "").splitlines()[-12:])
        raise SilenceDetectError("ffmpeg_failed", f"ffmpeg silencedetect 执行失败：\n{tail}")
    return stderr.decode(errors="replace")


def _parse_silence_output(output: str) -> list[SilenceSegment]:
    """解析 ffmpeg silencedetect 的 stderr 输出。

    ffmpeg silencedetect 输出格式（stderr）：
      [silencedetect @ 0x...] silence_start: 1.23
      [silencedetect @ 0x...] silence_end: 2.45 | silence_duration: 1.22

    也可能只有 silence_start 没有 silence_end（视频末尾静音）。
    """
    segments: list[SilenceSegment] = []
    starts: list[float] = []
    ends: list[float] = []

    for line in output.splitlines():
        m_start = re.search(r"silence_start:\s*([\d.]+)", line)
        if m_start:
            starts.append(float(m_start.group(1)))
            continue

        m_end = re.search(r"silence_end:\s*([\d.]+)", line)
        if m_end:
            ends.append(float(m_end.group(1)))

    for i, s in enumerate(starts):
        if i < len(ends):
            e = ends[i]
            segments.append(SilenceSegment(start=s, end=e, duration=e - s))
        else:
            logger.debug(f"silence_start {s:.3f}s without matching silence_end (trailing silence)")

    return segments


async def detect_silence(
    video_path: str,
    threshold_db: float = -35.0,
    min_duration: float = 0.6,
) -> list[SilenceSegment]:
    """检测视频中的静音段。

    对齐 fillercut 的 VAD 方案和 cutlist 的 GAP_CUT 参数。

    ffmpeg 命令（对齐 ffmpeg-cookbook silencedetect 用法）：
      ffmpeg -i input.mp4 -af silencedetect=n=-35dB:d=0.6 -f null -

    参数含义（对齐 cutlist）：
      n=-35dB: 音量低于 -35dB 视为静音
      d=0.6:   静音持续 0.6s 以上才标记（GAP_CUT）

    Args:
        video_path: 视频文件路径
        threshold_db: 静音阈值 dB（默认 -35，对齐 cutlist）
        min_duration: 最短静音时长秒（默认 0.6，对齐 cutlist GAP_CUT）

    Returns:
        静音片段列表，按 start 时间排序
    """
    if not Path(video_path).is_file():
        raise SilenceDetectError("file_not_found", f"视频文件不存在：{video_path}")

    cmd = [
        "ffmpeg",
        "-i",
        video_path,
        "-af",
        f"silencedetect=noise={threshold_db}dB:duration={min_duration}",
        "-f",
        "null",
        "-",
    ]

    try:
        output = await _run(cmd)
    except SilenceDetectError:
        raise
    except Exception as e:
        raise SilenceDetectError("ffmpeg_failed", f"ffmpeg silencedetect 执行异常：{e}") from e

    segments = _parse_silence_output(output)

    if segments:
        logger.info(
            f"silencedetect: found {len(segments)} silence segments, "
            f"total {sum(s.duration for s in segments):.2f}s"
        )
    else:
        logger.info("silencedetect: no silence segments found")

    return segments


ADAPTIVE_TIERS: list[tuple[float, float]] = [
    (-35.0, 0.6),
    (-32.0, 0.5),
    (-30.0, 0.35),
    (-28.0, 0.3),
]


async def detect_silence_adaptive(
    video_path: str,
    tiers: list[tuple[float, float]] | None = None,
) -> tuple[list[SilenceSegment], tuple[float, float] | None]:
    """自适应静音检测 — 10.28。

    live9 实证：默认 -35dB/0.6s 对高底噪素材恒 0 检出（句间停顿被
    底噪淹没）→ 0 候选 → 规划只剩 repeat → "空白时间没减"。本函数
    按档位依次放宽（安静素材仍走严格档，防把背景音当静音），取
    首次检出 >0 段的档。

    Returns:
        (segments, hit_tier) — hit_tier 为命中的 (threshold_db,
        min_duration)；全部档位 0 段时 ([], None)。
    """
    for tier in tiers or ADAPTIVE_TIERS:
        threshold_db, min_duration = tier
        segs = await detect_silence(
            video_path,
            threshold_db=threshold_db,
            min_duration=min_duration,
        )
        if segs:
            return segs, tier
    return [], None


async def has_audio_track(video_path: str) -> bool:
    """探测视频是否含音轨。

    解析链：PyAV（无外部依赖）→ ffprobe（系统 PATH）。
    对齐 probe_duration 的多层兜底模式 — 2026-10-30 live 实证：
    本机 PATH 无 ffprobe 且 imageio-ffmpeg 只带 ffmpeg，裸调 ffprobe
    永远 OSError → 恒 False → SmartCutSkill 被误判 no_audio_track，
    智能裁剪从未跑通过；PyAV 兜底修复。
    """
    # 1. PyAV 直接读容器流（最快，无子进程开销）
    try:
        import av

        def _av_has_audio() -> bool:
            with av.open(video_path) as container:
                return any(s.type == "audio" for s in container.streams)

        return await asyncio.to_thread(_av_has_audio)
    except Exception:
        pass

    # 2. ffprobe（系统 PATH 有 ffprobe 时）
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "a:0",
        "-show_entries",
        "stream=codec_name",
        "-of",
        "csv=p=0",
        video_path,
    ]
    try:
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await process.communicate()
            returncode = process.returncode
        except NotImplementedError:
            result = await asyncio.to_thread(
                subprocess.run,
                cmd,
                capture_output=True,
            )
            stdout, returncode = result.stdout, result.returncode
        return returncode == 0 and bool(stdout.decode().strip())
    except OSError:
        return False


async def probe_duration(video_path: str) -> float:
    """探测视频时长。

    解析链：PyAV（无外部依赖）→ ffprobe → ffmpeg stderr 解析 → 0.0。
    对齐 video_compose.py 的 _probe_duration 实现。
    """
    # 1. PyAV 直接读容器元数据（最快，无子进程开销）
    try:
        import av

        def _av_duration() -> float:
            with av.open(video_path) as container:
                return float(container.duration / 1e6) if container.duration else 0.0

        duration = await asyncio.to_thread(_av_duration)
        if duration > 0:
            return duration
    except Exception:
        pass

    # 2. ffprobe（系统 PATH 有 ffprobe 时）
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=nw=1:nk=1",
        video_path,
    ]
    try:
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await process.communicate()
        except (NotImplementedError, OSError):
            result = await asyncio.to_thread(
                subprocess.run,
                cmd,
                capture_output=True,
            )
            stdout = result.stdout
        return float(stdout.decode().strip())
    except (ValueError, OSError):
        return 0.0
