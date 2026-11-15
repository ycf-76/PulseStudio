d298d9b feat(studio): 原片抽帧 adapter，产出帧序列与元信息
diff --git a/backend/capabilities/video/adapters/studio_assets.py b/backend/capabilities/video/adapters/studio_assets.py
new file mode 100644
index 0000000..055d4b6
--- /dev/null
+++ b/backend/capabilities/video/adapters/studio_assets.py
@@ -0,0 +1,160 @@
+"""原片抽帧 adapter — Video Studio 制作地基（Task 1）。
+
+纯工具模块：原片 → JPEG 帧序列 + 时长/宽高元信息探测。
+不含任何风格常量（颜色/字体等归 overlay_templates / themes 层）。
+
+元信息探测链（本机无 ffprobe，imageio_ffmpeg 只带 ffmpeg）：
+  1. PyAV（av 包）读容器 duration 与 video stream width/height
+  2. 失败兜底：`ffmpeg -i` stderr 的 Duration 行 / 分辨率字段
+
+Windows 编码安全：subprocess 一律 str 参数列表（禁止 shell=True），
+ffmpeg 输出重定向到临时文件再读（errors="replace"）。
+"""
+
+from __future__ import annotations
+
+import logging
+import re
+import subprocess
+import tempfile
+from dataclasses import dataclass
+from pathlib import Path
+
+from capabilities.video.adapters.html_to_video import resolve_ffmpeg
+
+logger = logging.getLogger(__name__)
+
+FRAME_PATTERN = "frame_%06d.jpg"
+
+
+@dataclass
+class FrameSeqInfo:
+    """帧序列产物元信息。"""
+
+    frame_dir: Path
+    count: int
+    fps: float
+    width: int
+    height: int
+
+
+def extract_frames(video_path: str, out_dir: Path, fps: float = 24.0) -> FrameSeqInfo:
+    """原片 → frame_%06d.jpg 帧序列（-vf fps=N -qscale:v 2，start_number=0）。
+
+    输出目录中旧 frame_*.jpg 先清除，保证 count 精确。
+
+    Raises:
+        RuntimeError: ffmpeg 缺失、抽帧失败或 0 帧产出。
+    """
+    ffmpeg = resolve_ffmpeg()
+    if not ffmpeg:
+        raise RuntimeError("ffmpeg not available (PATH / imageio-ffmpeg / FFMPEG_PATH)")
+
+    out_dir = Path(out_dir)
+    out_dir.mkdir(parents=True, exist_ok=True)
+    for old in out_dir.glob("frame_*.jpg"):
+        old.unlink()
+
+    width, height = _probe_size(video_path)
+
+    cmd = [
+        ffmpeg, "-y", "-loglevel", "error", "-i", str(video_path),
+        "-vf", f"fps={fps:g}", "-qscale:v", "2",
+        "-start_number", "0", str(out_dir / FRAME_PATTERN),
+    ]
+    _run_ffmpeg(cmd)
+
+    frames = sorted(out_dir.glob("frame_*.jpg"))
+    if not frames:
+        raise RuntimeError(f"extract_frames produced no frames: {video_path}")
+    return FrameSeqInfo(
+        frame_dir=out_dir, count=len(frames), fps=float(fps),
+        width=width, height=height,
+    )
+
+
+def probe_duration(media_path: str) -> float:
+    """媒体时长（秒）：PyAV 容器 duration 优先，ffmpeg -i stderr 兜底。
+
+    Raises:
+        RuntimeError: 两条探测链都失败。
+    """
+    try:
+        import av
+
+        with av.open(str(media_path)) as container:
+            if container.duration:
+                return container.duration / 1_000_000.0
+    except Exception as exc:  # 探测链必须走完兜底，吞掉一切 PyAV 异常
+        logger.debug("av duration probe failed for %s: %s", media_path, exc)
+
+    dur = _duration_from_ffmpeg_stderr(media_path)
+    if dur is None:
+        raise RuntimeError(f"probe_duration failed (PyAV + ffmpeg fallback): {media_path}")
+    return dur
+
+
+def _probe_size(video_path: str) -> tuple[int, int]:
+    """宽高：PyAV 优先，ffmpeg -i stderr 兜底，仍失败返回 (0, 0)。"""
+    try:
+        import av
+
+        with av.open(str(video_path)) as container:
+            for stream in container.streams:
+                if stream.type == "video":
+                    return (
+                        int(stream.codec_context.width),
+                        int(stream.codec_context.height),
+                    )
+    except Exception as exc:
+        logger.debug("av size probe failed for %s: %s", video_path, exc)
+    return _size_from_ffmpeg_stderr(video_path)
+
+
+def _run_ffmpeg(cmd: list[str]) -> None:
+    """执行 ffmpeg；stdout/stderr 落临时文件后读回（Windows 编码安全）。
+
+    Raises:
+        RuntimeError: 非零退出码（附 stderr 尾部）。
+    """
+    with tempfile.TemporaryDirectory() as td:
+        log = Path(td) / "ffmpeg.log"
+        with open(log, "wb") as f:
+            proc = subprocess.run(cmd, stdout=f, stderr=f, check=False)
+        text = log.read_text(encoding="utf-8", errors="replace")
+    if proc.returncode != 0:
+        raise RuntimeError(f"ffmpeg failed (rc={proc.returncode}): {text[-600:]}")
+
+
+_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):([\d.]+)")
+_SIZE_RE = re.compile(r",\s*(\d{2,5})x(\d{2,5})[,\s]")
+
+
+def _ffmpeg_info_stderr(media_path: str) -> str:
+    """`ffmpeg -i`（无输出文件，rc=1 属预期）的 stderr 文本。"""
+    ffmpeg = resolve_ffmpeg()
+    if not ffmpeg:
+        return ""
+    with tempfile.TemporaryDirectory() as td:
+        log = Path(td) / "ffmpeg_i.log"
+        with open(log, "wb") as f:
+            subprocess.run(
+                [ffmpeg, "-hide_banner", "-i", str(media_path)],
+                stdout=f, stderr=f, check=False,
+            )
+        return log.read_text(encoding="utf-8", errors="replace")
+
+
+def _duration_from_ffmpeg_stderr(media_path: str) -> float | None:
+    """从 `ffmpeg -i` stderr 的 Duration 行解析秒数。"""
+    m = _DURATION_RE.search(_ffmpeg_info_stderr(media_path))
+    if not m:
+        return None
+    h, mnt, s = m.groups()
+    return int(h) * 3600 + int(mnt) * 60 + float(s)
+
+
+def _size_from_ffmpeg_stderr(video_path: str) -> tuple[int, int]:
+    """从 `ffmpeg -i` stderr 的视频流行解析 WxH（如 `320x568,`）。"""
+    m = _SIZE_RE.search(_ffmpeg_info_stderr(video_path))
+    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)
diff --git a/backend/tests/test_studio_assets.py b/backend/tests/test_studio_assets.py
new file mode 100644
index 0000000..b79921a
--- /dev/null
+++ b/backend/tests/test_studio_assets.py
@@ -0,0 +1,14 @@
+def test_extract_frames_counts_and_meta(tmp_path):
+    # 用 ffmpeg lavfi 造 2s 30fps 测试视频
+    from capabilities.video.adapters.studio_assets import extract_frames, probe_duration
+    import subprocess
+    from capabilities.video.adapters.html_to_video import resolve_ffmpeg
+    src = tmp_path / "src.mp4"
+    subprocess.run([resolve_ffmpeg(), "-y", "-f", "lavfi",
+        "-i", "testsrc=duration=2:size=320x568:rate=30",
+        "-pix_fmt", "yuv420p", str(src)], check=True, capture_output=True)
+    info = extract_frames(str(src), tmp_path / "frames", fps=24.0)
+    assert info.count == 48 and abs(info.fps - 24.0) < 0.01
+    assert info.width == 320 and info.height == 568
+    assert (info.frame_dir / ("frame_%06d.jpg" % 0)).is_file()
+    assert abs(probe_duration(str(src)) - 2.0) < 0.1
