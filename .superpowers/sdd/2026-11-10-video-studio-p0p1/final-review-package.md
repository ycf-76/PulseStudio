dd4cc37 feat(studio): P0 spike 集成验证（分层场景+确定性渲染+质检）
efce25c feat(studio): 场景本地预览静态服务
d1d7b24 feat(studio): 质检工具（contact sheet/响度/帧完整性/时长偏差）
70b6ee1 test(studio): 钉住 beats 相邻边界与 trigger 边界合法性
562d9fb feat(studio): 工作区脚手架与 timeline.json 校验契约
8350754 feat(studio): 确定性场景渲染 adapter（逐帧截图+编码混音）
d298d9b feat(studio): 原片抽帧 adapter，产出帧序列与元信息
 .../capabilities/video/adapters/studio_assets.py   | 160 +++++++++
 .../capabilities/video/adapters/studio_render.py   | 180 +++++++++++
 .../video/adapters/studio_render_worker.py         |  74 +++++
 .../capabilities/video/services/studio_preview.py  |  60 ++++
 .../capabilities/video/services/studio_review.py   | 360 +++++++++++++++++++++
 .../video/services/studio_workspace.py             | 187 +++++++++++
 .../tests/fixtures/studio_spike/scene/index.html   |  46 +++
 backend/tests/fixtures/studio_spike/scene/play.js  | 113 +++++++
 backend/tests/test_studio_assets.py                |  14 +
 backend/tests/test_studio_preview.py               |  10 +
 backend/tests/test_studio_render.py                |  32 ++
 backend/tests/test_studio_review.py                |  86 +++++
 backend/tests/test_studio_spike.py                 | 205 ++++++++++++
 backend/tests/test_studio_workspace.py             | 119 +++++++
 14 files changed, 1646 insertions(+)
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
diff --git a/backend/capabilities/video/adapters/studio_render.py b/backend/capabilities/video/adapters/studio_render.py
new file mode 100644
index 0000000..8eaeeaf
--- /dev/null
+++ b/backend/capabilities/video/adapters/studio_render.py
@@ -0,0 +1,180 @@
+"""Video Studio 确定性场景渲染 adapter（Task 2）。
+
+场景页契约：window.seekTo(t) 设置第 t 秒确切状态。流程：
+  子进程 Playwright 逐帧截图（studio_render_worker.py，file:// 打开场景）
+  → 可选 ffmpeg 帧序编码（h264 yuv420p）+ 音轨混音（-shortest）；
+  无 audio_path 时合成静音轨。
+
+不含任何风格常量（颜色/字体/动效归 overlay_templates / themes 层）。
+
+Windows 约束：subprocess 一律 str 参数列表 + CREATE_NO_WINDOW（禁止
+shell=True）；超时 taskkill /T /F 杀进程树（先 taskkill 再 communicate 收尸）。
+成败判定以 worker 落盘的 _worker_status.json 为准，不靠返回码猜测。
+"""
+
+from __future__ import annotations
+
+import json
+import logging
+import subprocess
+import sys
+import tempfile
+import time
+from dataclasses import dataclass
+from pathlib import Path
+
+from capabilities.video.adapters.html_to_video import resolve_ffmpeg
+
+logger = logging.getLogger(__name__)
+
+FRAME_PATTERN = "frame_%06d.png"
+# 每帧截图预算（含 seekTo evaluate），用于计算 worker 超时
+_PER_FRAME_BUDGET = 0.6
+_WORKER_LAUNCH_BUDGET = 60.0
+
+
+@dataclass
+class RenderResult:
+    """场景渲染产物。video_path 仅在请求 out_video 且编码成功时非空。"""
+
+    frames_dir: Path
+    count: int
+    fps: float
+    elapsed: float
+    video_path: Path | None
+
+
+def render_scene(
+    scene_html: Path,
+    frames_dir: Path,
+    duration: float,
+    fps: int = 30,
+    width: int = 1080,
+    height: int = 1920,
+    audio_path: str | None = None,
+    out_video: Path | None = None,
+) -> RenderResult:
+    """确定性渲染场景 → frame_%06d.png 帧序列（+ 可选混音 mp4）。
+
+    Raises:
+        RuntimeError: worker 缺失/超时/失败、帧数不符或 ffmpeg 编码失败。
+    """
+    scene_html = Path(scene_html)
+    frames_dir = Path(frames_dir)
+    worker = Path(__file__).parent / "studio_render_worker.py"
+    if not worker.is_file():
+        raise RuntimeError(f"studio render worker missing: {worker}")
+
+    n_frames = max(1, int(duration * fps + 0.999))
+    frames_dir.mkdir(parents=True, exist_ok=True)
+    # 清旧帧 + 旧状态文件：复用目录重渲时防止残留帧污染（image2 按 %06d
+    # 顺序读到首个缺号为止，残留的高号帧会被误编入成片）
+    for old in frames_dir.glob("frame_*.png"):
+        old.unlink()
+    status_file = frames_dir / "_worker_status.json"
+    status_file.unlink(missing_ok=True)
+
+    timeout = _WORKER_LAUNCH_BUDGET + n_frames * _PER_FRAME_BUDGET
+    t0 = time.time()
+    proc = subprocess.Popen(
+        [
+            sys.executable, str(worker),
+            str(scene_html), str(frames_dir),
+            str(width), str(height), str(duration), str(fps),
+        ],
+        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
+        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
+    )
+    try:
+        _, stderr = proc.communicate(timeout=timeout)
+    except subprocess.TimeoutExpired:
+        logger.error("studio render worker timed out after %.0fs, killing tree", timeout)
+        _kill_process_tree(proc.pid)
+        try:
+            proc.communicate(timeout=15)
+        except subprocess.TimeoutExpired:
+            proc.kill()
+        raise RuntimeError(f"studio render worker timed out after {timeout:.0f}s")
+    finally:
+        # 正常退出也确保 chromium 孙进程被带走（worker 是硬退出不做清理）
+        _kill_process_tree(proc.pid)
+
+    err_tail = (stderr or b"").decode(errors="replace")[-600:]
+    if proc.returncode != 0:
+        raise RuntimeError(
+            f"studio render worker failed (rc={proc.returncode}): {err_tail}"
+        )
+    try:
+        status = json.loads(status_file.read_text(encoding="utf-8"))
+    except (json.JSONDecodeError, OSError):
+        raise RuntimeError("studio render worker status unreadable")
+    if not status.get("ok"):
+        raise RuntimeError(f"studio render worker error: {status.get('error')}")
+    count = int(status.get("count") or 0)
+    if count != n_frames:
+        raise RuntimeError(f"frame count mismatch: {count} produced, {n_frames} expected")
+    elapsed = float(status.get("elapsed", time.time() - t0))
+
+    video_path: Path | None = None
+    if out_video is not None:
+        video_path = _encode_video(
+            frames_dir, Path(out_video), fps, audio_path,
+            video_duration=n_frames / fps,
+        )
+    return RenderResult(
+        frames_dir=frames_dir, count=count, fps=float(fps),
+        elapsed=elapsed, video_path=video_path,
+    )
+
+
+def _encode_video(
+    frames_dir: Path, out_video: Path, fps: int, audio_path: str | None,
+    video_duration: float,
+) -> Path:
+    """帧序列 → h264 yuv420p mp4；-shortest 混音（无音轨时合成静音轨）。"""
+    ffmpeg = resolve_ffmpeg()
+    if not ffmpeg:
+        raise RuntimeError("ffmpeg not available (PATH / imageio-ffmpeg / FFMPEG_PATH)")
+
+    out_video.parent.mkdir(parents=True, exist_ok=True)
+    if audio_path:
+        audio_input = ["-i", str(audio_path)]
+    else:
+        # 静音轨必须定长：无限 anullsrc + -shortest 在本机 ffmpeg 上会过冲
+        # （自验 0.5s 视频编出 0.998s；教训同 html_to_video「无限源必须定长」）
+        # → -t 把输入截到视频时长，-shortest 语义不变
+        audio_input = [
+            "-f", "lavfi", "-t", f"{video_duration:.3f}",
+            "-i", "anullsrc=r=44100:cl=mono",
+        ]
+
+    cmd = [
+        ffmpeg, "-y", "-loglevel", "error",
+        "-r", str(fps), "-i", str(frames_dir / FRAME_PATTERN),
+        *audio_input,
+        "-c:v", "libx264", "-pix_fmt", "yuv420p",
+        "-c:a", "aac", "-shortest",
+        str(out_video),
+    ]
+    # ffmpeg 输出落临时文件再读（Windows 编码安全，与 studio_assets 同法）
+    with tempfile.TemporaryDirectory() as td:
+        log = Path(td) / "ffmpeg.log"
+        with open(log, "wb") as f:
+            proc = subprocess.run(
+                cmd, stdout=f, stderr=f, check=False,
+                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
+            )
+        text = log.read_text(encoding="utf-8", errors="replace")
+    if proc.returncode != 0:
+        raise RuntimeError(f"studio encode failed (rc={proc.returncode}): {text[-600:]}")
+    return out_video
+
+
+def _kill_process_tree(pid: int) -> None:
+    """Windows taskkill 强杀进程树（带走 worker 的 chromium 孙进程）。"""
+    if sys.platform != "win32":
+        return
+    subprocess.run(
+        ["taskkill", "/PID", str(pid), "/T", "/F"],
+        capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW,
+    )
diff --git a/backend/capabilities/video/adapters/studio_render_worker.py b/backend/capabilities/video/adapters/studio_render_worker.py
new file mode 100644
index 0000000..0fc5170
--- /dev/null
+++ b/backend/capabilities/video/adapters/studio_render_worker.py
@@ -0,0 +1,74 @@
+"""Video Studio 场景渲染 worker — 独立子进程跑 Playwright 确定性逐帧截图。
+
+与 _render_worker.py（原片叠加渲染）同构，差异点（Task 2 契约）：
+  - page.goto(file://...) 打开磁盘场景文件：场景内相对路径资产才可用
+  - screenshot(omit_background=False)：场景负责完整合成，帧不抠 alpha
+  - 状态文件字段：ok / count / error
+
+本机 browser.close() 会挂起 → 帧全部落盘后 os._exit(0) 硬退出
+（见 _render_worker.py 文档）；父进程负责 taskkill /T 清理进程树。
+
+用法（命令行）:
+  python studio_render_worker.py <scene_html> <frames_dir> <width> <height> <duration> <fps>
+"""
+
+from __future__ import annotations
+
+import json
+import sys
+import time
+from pathlib import Path
+
+
+def main() -> int:
+    scene_html, frames_dir, width, height, duration, fps = sys.argv[1:7]
+    width, height, duration, fps = int(width), int(height), float(duration), int(fps)
+
+    frames_path = Path(frames_dir)
+    frames_path.mkdir(parents=True, exist_ok=True)
+
+    n_frames = max(1, int(duration * fps + 0.999))
+
+    from playwright.sync_api import sync_playwright
+
+    status_file = frames_path / "_worker_status.json"
+    t0 = time.time()
+
+    try:
+        with sync_playwright() as p:
+            browser = p.chromium.launch(args=["--force-device-scale-factor=1"])
+            page = browser.new_page(
+                viewport={"width": width, "height": height},
+                device_scale_factor=1,
+            )
+            # 磁盘场景文件走 file:// 打开，场景内相对路径资产才可用
+            page.goto(Path(scene_html).resolve().as_uri(), wait_until="load")
+            page.wait_for_timeout(300)  # 字体/资产渲染余量
+
+            for i in range(n_frames):
+                t = i / fps
+                page.evaluate("(t) => window.seekTo(t)", t)
+                page.screenshot(
+                    path=str(frames_path / ("frame_%06d.png" % i)),
+                    omit_background=False,
+                    type="png",
+                )
+
+            status_file.write_text(json.dumps({
+                "ok": True, "count": n_frames,
+                "elapsed": round(time.time() - t0, 2),
+            }), encoding="utf-8")
+            # 帧已全部落盘即达成目标；不调 browser.close()（本机会挂起）
+            sys.stdout.flush()
+            import os
+            os._exit(0)
+    except Exception as e:
+        status_file.write_text(json.dumps({
+            "ok": False, "count": 0, "error": f"{type(e).__name__}: {e}",
+        }), encoding="utf-8")
+        import os
+        os._exit(1)
+
+
+if __name__ == "__main__":
+    raise SystemExit(main())
diff --git a/backend/capabilities/video/services/studio_preview.py b/backend/capabilities/video/services/studio_preview.py
new file mode 100644
index 0000000..8d920d1
--- /dev/null
+++ b/backend/capabilities/video/services/studio_preview.py
@@ -0,0 +1,60 @@
+"""场景本地预览静态服务（Studio Preview Server）。
+
+职责：把 workspace 的 scene 目录以静态文件方式托管在 127.0.0.1 上，
+供 P0 spike 起预览给用户实时看场景画面。
+  - 服务只做静态托管；场景页实时预览的播放循环（rAF 调 seekTo）属场景代码职责
+  - 仅标准库 http.server/functools/threading，bind 127.0.0.1 不暴露局域网
+  - 无风格常量（颜色/字体/动效参数不在此模块）
+"""
+
+from __future__ import annotations
+
+import functools
+import threading
+from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
+from pathlib import Path
+
+
+class StudioPreviewServer:
+    """把 ws["scene"] 目录静态托管在 http://127.0.0.1:<port>/ 上。
+
+    port=0 时由系统分配实际端口，start() 从 server.server_address 读回。
+    """
+
+    def __init__(self, ws: dict, port: int = 0):
+        if "scene" not in ws:
+            raise ValueError("workspace 缺少 scene 键，无法启动预览服务")
+        scene = Path(ws["scene"])
+        if not scene.is_dir():
+            raise ValueError(f"场景目录不存在：{scene}")
+        # Windows 中文路径：给 http.server 的 directory 传 str 绝对路径
+        self._directory = str(scene.resolve())
+        self._port = port
+        self._httpd = None
+        self._thread = None
+        self._base = None
+
+    def start(self) -> str:
+        """启动静态服务，返回 http://127.0.0.1:<port>/。"""
+        handler = functools.partial(SimpleHTTPRequestHandler, directory=self._directory)
+        self._httpd = ThreadingHTTPServer(("127.0.0.1", self._port), handler)
+        self._httpd.daemon_threads = True
+        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
+        self._thread.start()
+        self._base = f"http://127.0.0.1:{self._httpd.server_address[1]}"
+        return self._base + "/"
+
+    def stop(self) -> None:
+        """停服务（幂等：未 start 或已 stop 时调用不炸）。"""
+        if self._httpd is None:
+            return
+        self._httpd.shutdown()
+        self._httpd.server_close()
+        self._thread.join(timeout=5)
+        self._httpd = None
+        self._thread = None
+        self._base = None
+
+    def url_for(self, rel: str) -> str:
+        """把 scene 目录下的相对路径转成预览 URL。"""
+        return f"{self._base}/{rel.lstrip('/')}"
diff --git a/backend/capabilities/video/services/studio_review.py b/backend/capabilities/video/services/studio_review.py
new file mode 100644
index 0000000..e8ae7d2
--- /dev/null
+++ b/backend/capabilities/video/services/studio_review.py
@@ -0,0 +1,360 @@
+"""Studio 质检工具（Task 4）— P2 创作 agent 的「眼睛」。
+
+四件套（不含任何风格常量，颜色/字体归 overlay_templates / themes 层）：
+  - contact_sheet：按时间点取最近存在帧拼网格图（列数 = ceil(sqrt(n))，
+    每格等比缩放到 cell 宽，下标注时间点）
+  - loudness_report：`ffmpeg -af astats=metadata=1 -f null -` 解析 stderr
+    Overall 段的 RMS level dB / Peak level dB（数字静音的 -inf 钉到 -120，
+    保证 JSON 可序列化）
+  - completeness_check：帧序完整（无缺号、无 0 字节、无越界多余帧），错误带帧号
+  - review_workspace：防御式汇总（每 beat contact sheet + 响度 + 帧完整性 +
+    时长偏差容差 1/fps+0.05 + 静音轨回归钉）。任何环节失败进 errors 列表，
+    绝不抛异常——它是 agent 自查通道，崩溃 = 自查失效。
+
+Windows 编码安全：subprocess 一律 str 参数列表 + CREATE_NO_WINDOW（禁
+shell=True）；ffmpeg 输出重定向到临时文件再读（errors="replace"）。
+"""
+
+from __future__ import annotations
+
+import bisect
+import logging
+import math
+import re
+import subprocess
+import sys
+import tempfile
+from pathlib import Path
+
+from PIL import Image, ImageDraw, ImageFont
+
+from capabilities.video.adapters.html_to_video import resolve_ffmpeg
+from capabilities.video.adapters.studio_assets import probe_duration
+
+logger = logging.getLogger(__name__)
+
+_LABEL_H = 24  # contact sheet 单格下方时间标注条高度
+_DB_FLOOR = -120.0  # astats -inf（数字静音）钉到的 dB 下限，保证 JSON 安全
+
+_FRAME_FILE_RE = re.compile(r"^frame_(\d+)\.(png|jpg)$")
+_DB_NUM = r"(-?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?|-?inf|nan)"
+_RMS_RE = re.compile(r"RMS level dB:\s*" + _DB_NUM)  # 勿改成 "RMS.*dB"，会误匹配 RMS peak dB
+_PEAK_RE = re.compile(r"Peak level dB:\s*" + _DB_NUM)
+_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):([\d.]+)")
+
+
+def _is_num(v: object) -> bool:
+    """数字且非 bool、非 NaN/Inf（与 studio_workspace.validate_timeline 同语义）。"""
+    if isinstance(v, bool) or not isinstance(v, (int, float)):
+        return False
+    if isinstance(v, float):
+        return math.isfinite(v)
+    return True
+
+
+def _run_ffmpeg(cmd: list[str]) -> tuple[int, str]:
+    """执行 ffmpeg；stdout/stderr 落临时文件后读回（Windows 编码安全）。"""
+    with tempfile.TemporaryDirectory() as td:
+        log = Path(td) / "ffmpeg.log"
+        with open(log, "wb") as f:
+            proc = subprocess.run(
+                cmd, stdout=f, stderr=f, check=False,
+                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
+            )
+        return proc.returncode, log.read_text(encoding="utf-8", errors="replace")
+
+
+def _ffmpeg_i_stderr(media_path: str) -> str:
+    """`ffmpeg -i`（无输出文件，rc=1 属预期）的 stderr 文本。"""
+    ffmpeg = resolve_ffmpeg()
+    if not ffmpeg:
+        return ""
+    _, text = _run_ffmpeg([ffmpeg, "-hide_banner", "-i", str(media_path)])
+    return text
+
+
+def _frame_index(frames_dir: Path) -> dict[int, Path]:
+    """帧目录 → {帧号: 路径}（frame_%06d.png/.jpg 兼容 Task1/Task2 产物）。"""
+    idx: dict[int, Path] = {}
+    for p in Path(frames_dir).iterdir():
+        m = _FRAME_FILE_RE.match(p.name)
+        if m:
+            idx[int(m.group(1))] = p
+    return idx
+
+
+def _nearest_frame(indices: list[int], target: int) -> int:
+    """离 target 最近的已存在帧号；距离并列取小号（确定性）。"""
+    pos = bisect.bisect_left(indices, target)
+    cands: list[int] = []
+    if pos < len(indices):
+        cands.append(indices[pos])
+    if pos > 0:
+        cands.append(indices[pos - 1])
+    return min(cands, key=lambda i: (abs(i - target), i))
+
+
+def contact_sheet(
+    frames_dir: Path, times: list[float], fps: float, out_png: Path, cell: int = 360,
+) -> Path:
+    """按时间点取帧拼网格图：round(t*fps) 最近帧号，戳到存在文件。
+
+    列数 = ceil(sqrt(n))，每格等比缩放到 cell 宽，下标注时间点。
+    帧目录为空 / times 为空 / fps 非法时抛异常（由 review_workspace 兜底捕获）。
+    """
+    if not _is_num(fps) or fps <= 0:
+        raise ValueError(f"contact_sheet: fps 必须 > 0，实际为 {fps!r}")
+    if not times:
+        raise ValueError("contact_sheet: times 为空")
+    idx_map = _frame_index(frames_dir)
+    if not idx_map:
+        raise RuntimeError(f"contact_sheet: 帧目录无 frame_*.png/jpg: {frames_dir}")
+
+    indices = sorted(idx_map)
+    cells: list[tuple[float, Image.Image]] = []
+    for t in times:
+        target = round(float(t) * float(fps))
+        im = Image.open(idx_map[_nearest_frame(indices, target)]).convert("RGB")
+        h = max(1, round(im.height * cell / im.width))
+        cells.append((float(t), im.resize((cell, h), Image.Resampling.LANCZOS)))
+
+    cols = math.ceil(math.sqrt(len(cells)))
+    rows = math.ceil(len(cells) / cols)
+    try:
+        font = ImageFont.load_default(size=14)
+    except TypeError:  # Pillow < 10.1 无 size 参数
+        font = ImageFont.load_default()
+
+    row_h = [
+        max(im.height for _, im in cells[r * cols:(r + 1) * cols]) + _LABEL_H
+        for r in range(rows)
+    ]
+    canvas = Image.new("RGB", (cols * cell, sum(row_h)), (24, 24, 24))
+    draw = ImageDraw.Draw(canvas)
+    y = 0
+    for r in range(rows):
+        for c, (t, im) in enumerate(cells[r * cols:(r + 1) * cols]):
+            x = c * cell
+            canvas.paste(im, (x, y))
+            label = f"t={t:.2f}s"
+            tw = draw.textlength(label, font=font)
+            draw.text((x + (cell - tw) / 2, y + im.height + 4), label,
+                      fill=(235, 235, 235), font=font)
+        y += row_h[r]
+
+    out_png = Path(out_png)
+    out_png.parent.mkdir(parents=True, exist_ok=True)
+    canvas.save(out_png)
+    return out_png
+
+
+def loudness_report(video_path: str) -> dict:
+    """响度（dB）：ffmpeg astats 解析 Overall 段 RMS/Peak level dB + 时长。
+
+    返回 {"rms": float, "peak": float, "duration": float}；rms/peak 为负 dB 值，
+    数字静音的 -inf 钉到 -120 保证 JSON 安全。duration 复用 Task 1 probe_duration。
+
+    Raises:
+        RuntimeError: ffmpeg 缺失、stderr 无 astats 数值（典型：无音轨）或探测失败。
+    """
+    ffmpeg = resolve_ffmpeg()
+    if not ffmpeg:
+        raise RuntimeError("ffmpeg not available (PATH / imageio-ffmpeg / FFMPEG_PATH)")
+    cmd = [
+        ffmpeg, "-hide_banner", "-i", str(video_path),
+        "-af", "astats=metadata=1", "-f", "null", "-",
+    ]
+    rc, text = _run_ffmpeg(cmd)
+
+    seg = text[text.rfind("Overall"):] if "Overall" in text else text
+    rms = _parse_db(_RMS_RE, seg)
+    peak = _parse_db(_PEAK_RE, seg)
+    if rms is None or peak is None:
+        raise RuntimeError(
+            f"loudness astats parse failed (rc={rc}, no dB values): {text[-400:]}")
+    return {"rms": rms, "peak": peak, "duration": probe_duration(str(video_path))}
+
+
+def _parse_db(pattern: re.Pattern[str], text: str) -> float | None:
+    """提取单个 dB 数值；-inf/nan 钉到 _DB_FLOOR，+inf 钉到 0（JSON 安全）。"""
+    m = pattern.search(text)
+    if not m:
+        return None
+    try:
+        v = float(m.group(1))
+    except ValueError:
+        return None
+    if math.isnan(v):
+        return _DB_FLOOR
+    if math.isinf(v):
+        return _DB_FLOOR if v < 0 else 0.0
+    return v
+
+
+def completeness_check(frames_dir: Path, expected: int) -> list[str]:
+    """帧序完整性：[0, expected) 无缺号、无 0 字节、无越界多余帧，错误带帧号。"""
+    if isinstance(expected, bool) or not isinstance(expected, int) or expected <= 0:
+        return [f"expected 应为正整数，实际为 {expected!r}"]
+    frames_dir = Path(frames_dir)
+    if not frames_dir.is_dir():
+        return [f"帧目录不存在: {frames_dir}"]
+    idx_map = _frame_index(frames_dir)
+    errs: list[str] = []
+    for i in range(expected):
+        if i not in idx_map:
+            errs.append(f"缺帧: frame_{i:06d}（编号 {i}，期望 0..{expected - 1}）")
+    for i in sorted(set(idx_map) - set(range(expected))):
+        errs.append(f"多余帧: {idx_map[i].name}（编号 {i} 超出期望范围 0..{expected - 1}）")
+    for i, p in sorted(idx_map.items()):
+        try:
+            size = p.stat().st_size
+        except OSError as exc:
+            errs.append(f"帧不可读: {p.name}（编号 {i}）: {exc}")
+            continue
+        if size == 0:
+            errs.append(f"0 字节帧: {p.name}（编号 {i}）")
+    return errs
+
+
+def review_workspace(ws: dict, timeline: dict, video_path: str) -> dict:
+    """质检汇总（防御式，绝不抛异常）：{"contact_sheets", "loudness", "errors"}。
+
+    errors 为中文错误列表（嵌字段路径/实测值，供创作 agent 自读自修）：
+      - 每 beat 的 t_start/t_end 抽帧 contact sheet 落 ws["review"]/beat_<id>_<t>.png
+      - 响度 loudness_report(video_path)
+      - 帧完整性 completeness_check(frames, expected=round(duration*fps))
+      - 时长偏差：probe_duration vs timeline.duration，> 1/fps + 0.05 报错
+      - 静音轨回归钉：无声轨或音轨时长≈0 报错（Task 2 补欠账）
+    """
+    errors: list[str] = []
+    result: dict = {"contact_sheets": [], "loudness": {}, "errors": errors}
+    try:
+        _review_inner(ws, timeline, video_path, result, errors)
+    except Exception as exc:  # 自查通道崩溃 = 自查失效，一切异常都进 errors
+        logger.exception("review_workspace unexpected failure")
+        errors.append(f"质检流程异常: {exc}")
+    return result
+
+
+def _review_inner(
+    ws: dict, timeline: dict, video_path: str, result: dict, errors: list[str],
+) -> None:
+    if not isinstance(ws, dict):
+        errors.append(f"ws 应为对象(dict)，实际为 {type(ws).__name__}")
+        return
+    frames_dir = ws.get("frames")
+    review_dir = ws.get("review")
+    if frames_dir is None or review_dir is None:
+        errors.append(
+            "ws 缺少 frames/review 目录键（应为 studio_workspace.create_workspace 产物），无法质检")
+        return
+    frames_dir = Path(frames_dir)
+    review_dir = Path(review_dir)
+
+    if not isinstance(timeline, dict):
+        errors.append(f"timeline 应为对象(dict)，实际为 {type(timeline).__name__}")
+        timeline = {}
+    fps = timeline.get("fps")
+    duration = timeline.get("duration")
+    beats = timeline.get("beats")
+    fps_ok = _is_num(fps) and fps > 0
+
+    # 1) 每 beat contact sheet
+    if not fps_ok:
+        errors.append(f"timeline.fps 无效（{fps!r}），跳过抽帧 contact sheet")
+    else:
+        if not isinstance(beats, list) or not beats:
+            errors.append("timeline.beats 缺失或为空，无 beat 可抽帧")
+        for i, beat in enumerate(beats if isinstance(beats, list) else []):
+            try:
+                if not isinstance(beat, dict):
+                    errors.append(f"beats[{i}] 应为对象(dict)，跳过抽帧")
+                    continue
+                bid = beat.get("id")
+                ts, te = beat.get("t_start"), beat.get("t_end")
+                if not isinstance(bid, str) or not bid:
+                    errors.append(f"beats[{i}].id 应为非空字符串，跳过抽帧")
+                    continue
+                if not (_is_num(ts) and _is_num(te)):
+                    errors.append(f"beats[{i}].t_start/t_end 应为数字，跳过抽帧")
+                    continue
+                safe_id = re.sub(r"[^0-9A-Za-z_-]+", "_", bid)
+                out = review_dir / f"beat_{safe_id}_{float(ts):.2f}.png"
+                result["contact_sheets"].append(
+                    str(contact_sheet(frames_dir, [ts, te], float(fps), out)))
+            except Exception as exc:
+                errors.append(f"beats[{i}] contact sheet 失败: {exc}")
+
+    # 2) 帧完整性（expected = round(duration*fps)）
+    if fps_ok and _is_num(duration) and duration > 0:
+        expected = round(duration * fps)
+        if expected <= 0:
+            errors.append(f"期望帧数 ≤ 0（duration={duration}×fps={fps}），帧完整性检查跳过")
+        else:
+            errors.extend(completeness_check(frames_dir, expected))
+    else:
+        errors.append(
+            f"timeline duration/fps 无效（duration={duration!r}, fps={fps!r}），帧完整性检查跳过")
+
+    # 3) 响度
+    try:
+        result["loudness"] = loudness_report(str(video_path))
+    except Exception as exc:
+        errors.append(f"响度检测失败: {exc}")
+
+    # 4) 时长偏差（容差 1/fps + 0.05）
+    if fps_ok and _is_num(duration) and duration > 0:
+        try:
+            actual = probe_duration(str(video_path))
+            tol = 1.0 / float(fps) + 0.05
+            if abs(actual - float(duration)) > tol:
+                errors.append(
+                    f"时长偏差超限: timeline.duration={duration}, 实测={actual:.3f}s, "
+                    f"容差={tol:.3f}s（1/fps+0.05）")
+        except Exception as exc:
+            errors.append(f"时长探测失败: {exc}")
+
+    # 5) 静音轨回归钉（Task 2 补欠账）
+    _audio_pin_errors(str(video_path), errors)
+
+
+def _audio_pin_errors(video_path: str, errors: list[str]) -> None:
+    """静音轨回归钉：无声轨或音轨时长≈0 报错；探测链全失败也报错。"""
+    info = _audio_track_info(video_path)
+    if info is None:
+        errors.append("音轨探测失败（PyAV + ffmpeg 两条链都不可用）")
+        return
+    has_audio, audio_dur = info
+    if not has_audio:
+        errors.append(
+            "视频无音轨（静音轨混流缺失？对照 studio_render._encode_video 的 -shortest 混音链路）")
+    elif audio_dur < 0.05:
+        errors.append(f"音轨时长异常（≈0，实测 {audio_dur:.3f}s，疑似音轨被截断）")
+
+
+def _audio_track_info(video_path: str) -> tuple[bool, float] | None:
+    """探测 (有无音轨, 音轨时长秒)：PyAV 优先，`ffmpeg -i` stderr 兜底；全败返回 None。"""
+    try:
+        import av
+
+        with av.open(video_path) as container:
+            audio = next((s for s in container.streams if s.type == "audio"), None)
+            if audio is None:
+                return False, 0.0
+            dur = 0.0
+            if audio.duration is not None and audio.time_base is not None:
+                dur = float(audio.duration * audio.time_base)
+            if dur <= 0 and container.duration:
+                dur = container.duration / 1_000_000.0
+            return True, dur
+    except Exception as exc:  # 探测链必须走完兜底，吞掉一切 PyAV 异常
+        logger.debug("av audio probe failed for %s: %s", video_path, exc)
+
+    text = _ffmpeg_i_stderr(video_path)
+    if not text:
+        return None
+    if "Audio:" not in text:
+        return False, 0.0
+    m = _DURATION_RE.search(text)
+    dur = (int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))) if m else 0.0
+    return True, dur
diff --git a/backend/capabilities/video/services/studio_workspace.py b/backend/capabilities/video/services/studio_workspace.py
new file mode 100644
index 0000000..6de4770
--- /dev/null
+++ b/backend/capabilities/video/services/studio_workspace.py
@@ -0,0 +1,187 @@
+"""Studio 工作区脚手架与 timeline.json 校验契约。
+
+职责（纯结构/时间合法性，无风格常量——颜色/字体/动效参数不在此模块）：
+  - create_workspace：outputs/studio/<film_id>/ 下 7 个子目录（幂等）
+  - validate_timeline：防御式校验，任何输入都不抛异常，返回中文错误列表
+    （错误信息内嵌字段路径，供创作 agent 自读自修）
+  - save_timeline / load_timeline：timeline.json 落 ws["analysis"]，utf-8 + ensure_ascii=False
+"""
+
+from __future__ import annotations
+
+import json
+import math
+from pathlib import Path
+
+_STUDIO_ROOT = Path(__file__).resolve().parent.parent.parent.parent / "outputs" / "studio"
+
+_SUBDIRS = ("inputs", "analysis", "scene", "assets", "frames", "review", "out")
+
+
+def create_workspace(film_id: str) -> dict[str, Path]:
+    """建 outputs/studio/<film_id>/{inputs,analysis,scene,assets,frames,review,out}。
+
+    幂等（exist_ok=True），返回各路径 dict（键 = 目录名）。
+    """
+    root = _STUDIO_ROOT / film_id
+    ws = {name: root / name for name in _SUBDIRS}
+    for p in ws.values():
+        p.mkdir(parents=True, exist_ok=True)
+    return ws
+
+
+def _is_num(v: object) -> bool:
+    """数字且非 bool、非 NaN/Inf（bool 是 int 子类；json.loads 默认放行 NaN/Infinity，
+    NaN>0 恒 False 会溜过 >0 检查。int 不走 isfinite——超大 int 转 float 会 OverflowError）。"""
+    if isinstance(v, bool) or not isinstance(v, (int, float)):
+        return False
+    if isinstance(v, float):
+        return math.isfinite(v)
+    return True
+
+
+def _type_err(field: str, v: object) -> str:
+    """数字字段类型错的消息：非有限 float 单独点名，避免「应为数字，实际为 float」误导自修。"""
+    if isinstance(v, float) and not math.isfinite(v):
+        return f"{field} 必须为有限数字（NaN/Inf 不可用）"
+    return f"{field} 应为数字，实际为 {type(v).__name__}"
+
+
+def validate_timeline(tl: dict) -> list[str]:
+    """校验 timeline 结构与时间合法性，返回错误列表（空 = 通过）。
+
+    防御式：tl 可能缺键、类型错、triggers 挂错位置——所有分支返回错误字符串，
+    绝不抛异常。规则：
+      必填 fps>0 / duration>0 / canvas{w,h}
+      speech[] 每项含 start/end/text 且 end>start
+      beats[] 每项含 id/t_start/t_end/title/visual_task 且 t_end>t_start，
+              beats 按 t_start 升序不重叠
+      triggers[].t 落在其所属 beat 的 [t_start,t_end] 内（trigger 挂在 beat 的 triggers 字段下）
+    """
+    if not isinstance(tl, dict):
+        return [f"timeline 应为对象(dict)，实际为 {type(tl).__name__}"]
+    errs: list[str] = []
+
+    if "fps" not in tl:
+        errs.append("缺少 fps")
+    elif not _is_num(tl["fps"]):
+        errs.append(_type_err("fps", tl["fps"]))
+    elif tl["fps"] <= 0:
+        errs.append(f"fps 必须 > 0，实际为 {tl['fps']}")
+
+    if "duration" not in tl:
+        errs.append("缺少 duration")
+    elif not _is_num(tl["duration"]):
+        errs.append(_type_err("duration", tl["duration"]))
+    elif tl["duration"] <= 0:
+        errs.append(f"duration 必须 > 0，实际为 {tl['duration']}")
+
+    if "canvas" not in tl:
+        errs.append("缺少 canvas")
+    elif not isinstance(tl["canvas"], dict):
+        errs.append(f"canvas 应为对象(dict)，实际为 {type(tl['canvas']).__name__}")
+    else:
+        canvas = tl["canvas"]
+        for dim in ("w", "h"):
+            if dim not in canvas:
+                errs.append(f"canvas.{dim} 缺失")
+            elif not _is_num(canvas[dim]):
+                errs.append(_type_err(f"canvas.{dim}", canvas[dim]))
+            elif canvas[dim] <= 0:
+                errs.append(f"canvas.{dim} 必须 > 0，实际为 {canvas[dim]}")
+
+    if "speech" not in tl:
+        errs.append("缺少 speech")
+    elif not isinstance(tl["speech"], list):
+        errs.append(f"speech 应为列表，实际为 {type(tl['speech']).__name__}")
+    else:
+        for i, seg in enumerate(tl["speech"]):
+            tag = f"speech[{i}]"
+            if not isinstance(seg, dict):
+                errs.append(f"{tag} 应为对象(dict)，实际为 {type(seg).__name__}")
+                continue
+            for field in ("start", "end", "text"):
+                if field not in seg:
+                    errs.append(f"{tag}.{field} 缺失")
+            for tfield in ("start", "end"):
+                if tfield in seg and not _is_num(seg[tfield]):
+                    errs.append(_type_err(f"{tag}.{tfield}", seg[tfield]))
+            if _is_num(seg.get("start")) and _is_num(seg.get("end")) and seg["end"] <= seg["start"]:
+                errs.append(f"{tag}.end 必须 > start（实际 end={seg['end']}, start={seg['start']}）")
+            if "text" in seg and not isinstance(seg["text"], str):
+                errs.append(f"{tag}.text 应为字符串，实际为 {type(seg['text']).__name__}")
+
+    if "beats" not in tl:
+        errs.append("缺少 beats")
+    elif not isinstance(tl["beats"], list):
+        errs.append(f"beats 应为列表，实际为 {type(tl['beats']).__name__}")
+    else:
+        prev: dict | None = None
+        for i, beat in enumerate(tl["beats"]):
+            tag = f"beats[{i}]"
+            if not isinstance(beat, dict):
+                errs.append(f"{tag} 应为对象(dict)，实际为 {type(beat).__name__}")
+                prev = None
+                continue
+            for field in ("id", "t_start", "t_end", "title", "visual_task"):
+                if field not in beat:
+                    errs.append(f"{tag}.{field} 缺失")
+            for sfield in ("id", "title", "visual_task"):
+                if sfield in beat and not isinstance(beat[sfield], str):
+                    errs.append(f"{tag}.{sfield} 应为字符串，实际为 {type(beat[sfield]).__name__}")
+            for tfield in ("t_start", "t_end"):
+                if tfield in beat and not _is_num(beat[tfield]):
+                    errs.append(_type_err(f"{tag}.{tfield}", beat[tfield]))
+            if _is_num(beat.get("t_start")) and _is_num(beat.get("t_end")) and beat["t_end"] <= beat["t_start"]:
+                errs.append(f"{tag}.t_end 必须 > t_start（实际 t_end={beat['t_end']}, t_start={beat['t_start']}）")
+            if (prev is not None and _is_num(prev.get("t_end")) and _is_num(beat.get("t_start"))
+                    and beat["t_start"] < prev["t_end"]):
+                errs.append(f"{tag}.t_start 与上一 beat 重叠（须按 t_start 升序且不重叠，上一 beat t_end={prev['t_end']}）")
+            _check_triggers(errs, i, beat)
+            prev = beat if _is_num(beat.get("t_start")) else None
+
+    if "triggers" in tl:
+        errs.append("顶层 triggers 挂错位置：trigger 应挂在各 beat 的 triggers 字段下")
+
+    return errs
+
+
+def _check_triggers(errs: list[str], beat_i: int, beat: dict) -> None:
+    """校验单个 beat 的 triggers（挂在 beat.triggers 下），追加错误到 errs。"""
+    if "triggers" not in beat:
+        return
+    tag = f"beats[{beat_i}].triggers"
+    triggers = beat["triggers"]
+    if not isinstance(triggers, list):
+        errs.append(f"{tag} 应为列表，实际为 {type(triggers).__name__}")
+        return
+    t_start = beat.get("t_start")
+    t_end = beat.get("t_end")
+    span_ok = _is_num(t_start) and _is_num(t_end)
+    for j, trig in enumerate(triggers):
+        ttag = f"{tag}[{j}]"
+        if not isinstance(trig, dict):
+            errs.append(f"{ttag} 应为对象(dict)，实际为 {type(trig).__name__}")
+            continue
+        if "t" not in trig:
+            errs.append(f"{ttag}.t 缺失")
+        elif not _is_num(trig["t"]):
+            errs.append(_type_err(f"{ttag}.t", trig["t"]))
+        elif span_ok and not (t_start <= trig["t"] <= t_end):
+            errs.append(f"{ttag}.t={trig['t']} 超出所属 beat [t_start,t_end]=[{t_start},{t_end}]")
+
+
+def _timeline_path(ws: dict) -> Path:
+    return Path(ws["analysis"]) / "timeline.json"
+
+
+def save_timeline(ws: dict, tl: dict) -> Path:
+    """timeline.json 落 ws["analysis"] 目录（utf-8 + ensure_ascii=False），返回文件路径。"""
+    p = _timeline_path(ws)
+    p.write_text(json.dumps(tl, ensure_ascii=False, indent=2), encoding="utf-8")
+    return p
+
+
+def load_timeline(ws: dict) -> dict:
+    """读回 ws["analysis"]/timeline.json。"""
+    return json.loads(_timeline_path(ws).read_text(encoding="utf-8"))
diff --git a/backend/tests/fixtures/studio_spike/scene/index.html b/backend/tests/fixtures/studio_spike/scene/index.html
new file mode 100644
index 0000000..3edfedf
--- /dev/null
+++ b/backend/tests/fixtures/studio_spike/scene/index.html
@@ -0,0 +1,46 @@
+<!doctype html>
+<!-- Studio P0 spike 分层场景（Task 6 fixture）：四层合成，DOM 顺序即层序——
+     底帧(#base) → 身后动效标题条(#title) → 人物(#person) → 前景触发卡片(#card)。
+     运行时由测试拷入工作区 scene/，并生成 assets_manifest.js
+     （帧目录/数量/尺寸/节拍标题/触发卡片）。seekTo(t) 纯函数驱动全部画面状态；
+     渲染模式（URL 无参数）只暴露 seekTo 供逐帧截图，?preview 才启动 rAF 实时循环。 -->
+<html lang="zh">
+<head>
+<meta charset="utf-8">
+<title>studio spike scene</title>
+<style>
+  html, body { margin: 0; padding: 0; background: #000; }
+  #stage { position: relative; width: 1080px; height: 1920px; overflow: hidden; background: #101018; }
+  /* world = 原始帧坐标系（底帧/人物同几何），由 play.js 按 cover 比例缩放居中 */
+  #world { position: absolute; left: 50%; top: 50%; }
+  #base, #person { position: absolute; left: 0; top: 0; display: block; }
+  /* 身后层：标题条（人物之下，顶部落纯背景区不被人物遮挡），滑入手写插值 */
+  #title {
+    position: absolute; left: 0; top: 24px; box-sizing: border-box; width: 100%;
+    padding: 22px 44px 22px 40px; border-left: 14px solid #ff2442;
+    background: rgba(13, 15, 24, 0.78); color: #fff;
+    font: 700 58px/1.28 "Microsoft YaHei", "PingFang SC", sans-serif;
+    opacity: 0; transform: translateX(-108%);
+  }
+  /* 前景层：触发卡片（最上层，落人物躯干上方），弹入手写插值 */
+  #card {
+    position: absolute; left: 50%; bottom: 190px; box-sizing: border-box; max-width: 86%;
+    padding: 26px 42px; border-radius: 26px; background: #ff2442; color: #fff;
+    font: 700 54px/1.3 "Microsoft YaHei", "PingFang SC", sans-serif;
+    opacity: 0; transform: translate(-50%, 0) scale(0.8);
+  }
+</style>
+</head>
+<body>
+  <div id="stage">
+    <div id="world">
+      <img id="base" alt="" draggable="false">
+      <div id="title"><span id="title_text"></span></div>
+      <img id="person" alt="" draggable="false">
+      <div id="card"><span id="card_text"></span></div>
+    </div>
+  </div>
+  <script src="assets_manifest.js"></script>
+  <script src="play.js"></script>
+</body>
+</html>
diff --git a/backend/tests/fixtures/studio_spike/scene/play.js b/backend/tests/fixtures/studio_spike/scene/play.js
new file mode 100644
index 0000000..65fc128
--- /dev/null
+++ b/backend/tests/fixtures/studio_spike/scene/play.js
@@ -0,0 +1,113 @@
+/* Studio P0 spike 场景驱动（Task 6 fixture）。
+ *
+ * 契约：
+ *   - window.seekTo(t) 把画面设到第 t 秒的确切状态。纯函数：禁 Math.random /
+ *     Date / performance.now 参与画面计算。返回 Promise 等底帧/人物两层素材
+ *     decode 完成（Playwright evaluate 会 await 返回的 Promise，保证渲染器
+ *     逐帧截图不会拍到半解码的旧图）。
+ *   - 动效全部按 t 手写数学插值（easeOutCubic），禁用 CSS transition/animation
+ *     —— 渲染态下 transition 依赖真实流逝时间，逐帧截图会产生不确定帧。
+ *   - 渲染模式（URL 无参数）：只暴露 seekTo，不启动任何循环。
+ *   - 预览模式（URL 带 ?preview）：rAF 实时循环调 seekTo（仅人工预览用，
+ *     performance.now 只出现在预览循环里，不进 seekTo）。
+ */
+(function () {
+  "use strict";
+  var M = window.STUDIO_SCENE_MANIFEST;
+  var world = document.getElementById("world");
+  var baseEl = document.getElementById("base");
+  var personEl = document.getElementById("person");
+  var titleEl = document.getElementById("title");
+  var titleText = document.getElementById("title_text");
+  var cardEl = document.getElementById("card");
+  var cardText = document.getElementById("card_text");
+
+  var TITLE_IN = 0.4;  // 标题条滑入时长（秒），动效参数来自任务书
+  var CARD_IN = 0.3;   // 卡片弹入时长（秒）
+  var CARD_OUT = 0.3;  // 卡片弹出时长（秒）
+
+  // cover 缩放：人物与底帧同容器同几何，天然对齐无重影
+  var scale = Math.max(M.canvas.w / M.base.w, M.canvas.h / M.base.h);
+  world.style.width = M.base.w + "px";
+  world.style.height = M.base.h + "px";
+  world.style.transform = "translate(-50%, -50%) scale(" + scale + ")";
+  baseEl.style.width = M.base.w + "px";
+  baseEl.style.height = M.base.h + "px";
+  personEl.style.width = M.person.w + "px";
+  personEl.style.height = M.person.h + "px";
+
+  function clamp01(x) { return x < 0 ? 0 : (x > 1 ? 1 : x); }
+  function easeOutCubic(p) { var q = 1 - p; return 1 - q * q * q; }
+  function pad6(i) { return ("00000" + i).slice(-6); }
+  function frameSrc(dir, pattern, i) { return dir + "/" + pattern.replace("%06d", pad6(i)); }
+
+  var curBase = -1;
+  var curPerson = -1;
+  var curTitleBeat = null;
+  var curCard = null;
+
+  window.seekTo = function (t) {
+    if (t < 0) t = 0;
+    if (t > M.duration) t = M.duration;
+
+    // 1) 底帧层：index = floor(t*fps)，clamp 到 [0, count-1]
+    var bi = Math.floor(t * M.base.fps);
+    bi = Math.max(0, Math.min(M.base.count - 1, bi));
+    if (bi !== curBase) { baseEl.src = frameSrc(M.base.dir, M.base.pattern, bi); curBase = bi; }
+
+    // 2) 人物层：取与底帧同一视频时刻的抠像帧（round(bi*person.fps/base.fps)），
+    //    两层像素同一瞬 → 人物层与底帧同步无重影
+    var pi = Math.round(bi * M.person.fps / M.base.fps);
+    pi = Math.max(0, Math.min(M.person.count - 1, pi));
+    if (pi !== curPerson) { personEl.src = frameSrc(M.person.dir, M.person.pattern, pi); curPerson = pi; }
+
+    // 3) 身后层：当前 beat 的标题条，按 t 手写 easeOutCubic 滑入（无 CSS transition）
+    var beat = null;
+    for (var k = 0; k < M.beats.length; k++) {
+      if (t >= M.beats[k].t_start) beat = M.beats[k];
+    }
+    if (beat) {
+      if (curTitleBeat !== beat) { titleText.textContent = beat.title; curTitleBeat = beat; }
+      var e = easeOutCubic(clamp01((t - beat.t_start) / TITLE_IN));
+      titleEl.style.transform = "translateX(" + (-108 * (1 - e)) + "%)";
+      titleEl.style.opacity = String(e);
+    } else {
+      titleEl.style.opacity = "0";
+      curTitleBeat = null;
+    }
+
+    // 4) 前景层：触发卡片（t≥trigger.t 弹入，所属 beat 结束前弹出），同手写插值
+    var trig = null;
+    for (var j = 0; j < M.triggers.length; j++) {
+      if (t >= M.triggers[j].t && t < M.triggers[j].until) trig = M.triggers[j];
+    }
+    if (trig) {
+      if (curCard !== trig) { cardText.textContent = trig.text; curCard = trig; }
+      var pin = easeOutCubic(clamp01((t - trig.t) / CARD_IN));
+      var pout = clamp01((t - (trig.until - CARD_OUT)) / CARD_OUT);
+      var dy = 26 * (1 - pin) - 30 * pout;
+      cardEl.style.transform = "translate(-50%, " + dy + "px) scale(" + (0.8 + 0.2 * pin) + ")";
+      cardEl.style.opacity = String(pin * (1 - pout));
+    } else {
+      cardEl.style.opacity = "0";
+      curCard = null;
+    }
+
+    // 等两层素材 decode 完成（渲染器逐帧截图前必须拿到完整像素）
+    var d1 = baseEl.decode ? baseEl.decode() : Promise.resolve();
+    var d2 = personEl.decode ? personEl.decode() : Promise.resolve();
+    return Promise.all([d1, d2]);
+  };
+
+  window.seekTo(0);
+
+  // 预览模式（?preview）：rAF 实时循环，循环播放
+  if (new URLSearchParams(window.location.search).has("preview")) {
+    var t0 = performance.now();
+    var loop = function (now) {
+      window.seekTo(((now - t0) / 1000) % M.duration);
+      requestAnimationFrame(loop);
+    };
+    requestAnimationFrame(loop);
+  }
+})();
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
diff --git a/backend/tests/test_studio_preview.py b/backend/tests/test_studio_preview.py
new file mode 100644
index 0000000..9939090
--- /dev/null
+++ b/backend/tests/test_studio_preview.py
@@ -0,0 +1,10 @@
+def test_preview_server_serves_scene(tmp_path):
+    from capabilities.video.services.studio_preview import StudioPreviewServer
+    (tmp_path / "index.html").write_text("<html><body>hi</body></html>", encoding="utf-8")
+    ws = {"scene": tmp_path}
+    srv = StudioPreviewServer(ws); url = srv.start()
+    import urllib.request
+    body = urllib.request.urlopen(url).read().decode("utf-8")
+    assert "hi" in body
+    assert srv.url_for("assets/x.png").startswith(url.rstrip("/"))
+    srv.stop()
diff --git a/backend/tests/test_studio_render.py b/backend/tests/test_studio_render.py
new file mode 100644
index 0000000..46698d0
--- /dev/null
+++ b/backend/tests/test_studio_render.py
@@ -0,0 +1,32 @@
+def test_render_scene_deterministic_and_mux(tmp_path):
+    from capabilities.video.adapters.studio_render import render_scene
+    scene = tmp_path / "scene"; scene.mkdir()
+    # 最小场景：seekTo 驱动方块位移（相对路径引用 assets/point.png）
+    (scene / "assets").mkdir()
+    from PIL import Image
+    Image.new("RGB", (40, 40), (255, 0, 0)).save(scene / "assets" / "point.png")
+    (scene / "index.html").write_text(
+        '<img id="p" src="assets/point.png" style="position:absolute;left:0;top:0">'
+        '<script>window.seekTo=(t)=>{document.getElementById("p").style.left=(t*100)+"px";};'
+        "window.seekTo(0);</script>", encoding="utf-8")
+    r1 = render_scene(scene / "index.html", tmp_path / "f1", duration=0.5, fps=8, width=320, height=568)
+    r2 = render_scene(scene / "index.html", tmp_path / "f2", duration=0.5, fps=8, width=320, height=568)
+    assert r1.count == 4
+    assert (r1.frames_dir / ("frame_%06d.png" % 0)).read_bytes() == (r2.frames_dir / ("frame_%06d.png" % 0)).read_bytes()  # 确定性
+    from PIL import Image as I2
+    # brief 原文探测帧 4（t=0.5s），但其自身约定 count=ceil(0.5*8)=4、帧号 0 起，
+    # 帧 4 必不存在（off-by-one 笔误，与括号笔误同类已裁决）→ 改探测末帧 3
+    # （t=0.375s，方块位移至 37.5px，(50,10) 落在方块内）
+    im = I2.open(r1.frames_dir / ("frame_%06d.png" % 3))
+    assert im.getpixel((50, 10))[0] > 200  # 方块已位移且资产非空白
+    # 混音：1s 静音音轨
+    from capabilities.video.adapters.html_to_video import resolve_ffmpeg
+    import subprocess
+    wav = tmp_path / "a.wav"
+    subprocess.run([resolve_ffmpeg(), "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
+        "-t", "0.5", str(wav)], check=True, capture_output=True)
+    r3 = render_scene(scene / "index.html", tmp_path / "f3", duration=0.5, fps=8,
+        width=320, height=568, audio_path=str(wav), out_video=tmp_path / "out.mp4")
+    assert r3.video_path.is_file()
+    from capabilities.video.adapters.studio_assets import probe_duration
+    assert abs(probe_duration(str(r3.video_path)) - 0.5) < 1 / 8 + 0.05  # 音画时长偏差 ≤1帧
diff --git a/backend/tests/test_studio_review.py b/backend/tests/test_studio_review.py
new file mode 100644
index 0000000..ac873f9
--- /dev/null
+++ b/backend/tests/test_studio_review.py
@@ -0,0 +1,86 @@
+def test_review_on_synthetic_frames(tmp_path):
+    from PIL import Image
+    from capabilities.video.services.studio_review import (
+        contact_sheet, completeness_check, loudness_report)
+    fd = tmp_path / "frames"; fd.mkdir()
+    for i in range(8):
+        Image.new("RGB", (320, 568), (i * 20 % 255, 0, 0)).save(fd / ("frame_%06d.png" % i))
+    assert completeness_check(fd, 8) == []
+    (fd / ("frame_%06d.png" % 3)).unlink()
+    assert any("3" in e for e in completeness_check(fd, 8))
+    sheet = contact_sheet(fd, [0.0, 0.5], fps=8, out_png=tmp_path / "sheet.png")
+    assert sheet.is_file() and Image.open(sheet).size[0] >= 640
+    from capabilities.video.adapters.html_to_video import resolve_ffmpeg
+    import subprocess
+    wav = tmp_path / "a.wav"
+    subprocess.run([resolve_ffmpeg(), "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
+        str(wav)], check=True, capture_output=True)
+    rep = loudness_report(str(wav))
+    assert rep["peak"] > -30 and "duration" in rep
+
+
+def _make_frames(tmp_path, n):
+    from PIL import Image
+    fd = tmp_path / "frames"
+    fd.mkdir(parents=True, exist_ok=True)
+    for i in range(n):
+        Image.new("RGB", (320, 568), (i * 20 % 255, 0, 0)).save(fd / ("frame_%06d.png" % i))
+    return fd
+
+
+def _make_video(tmp_path, name, with_audio, duration=1):
+    import subprocess
+    from capabilities.video.adapters.html_to_video import resolve_ffmpeg
+    src = tmp_path / name
+    cmd = [resolve_ffmpeg(), "-y", "-f", "lavfi",
+           "-i", f"testsrc=duration={duration}:size=320x568:rate=8"]
+    if with_audio:
+        cmd += ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-c:a", "aac"]
+    cmd += ["-t", str(duration), "-pix_fmt", "yuv420p", str(src)]
+    subprocess.run(cmd, check=True, capture_output=True)
+    return src
+
+
+_TL = {"fps": 8, "duration": 1.0, "canvas": {"w": 320, "h": 568}, "speech": [],
+       "beats": [{"id": "b1", "t_start": 0.0, "t_end": 1.0,
+                  "title": "开场", "visual_task": "画方块"}]}
+
+
+def test_review_workspace_happy_path(tmp_path):
+    """有静音音轨 + 帧齐全 + 时长吻合 → errors 为空，产物齐备。"""
+    from capabilities.video.services.studio_review import review_workspace
+    fd = _make_frames(tmp_path, 8)
+    src = _make_video(tmp_path, "withaudio.mp4", with_audio=True)
+    ws = {"frames": fd, "review": tmp_path / "review"}
+    res = review_workspace(ws, _TL, str(src))
+    assert res["errors"] == [], res["errors"]
+    assert len(res["contact_sheets"]) == 1
+    assert (tmp_path / "review" / "beat_b1_0.00.png").is_file()
+    assert res["loudness"]["peak"] <= 0 and "duration" in res["loudness"]
+
+
+def test_review_workspace_missing_audio_pin(tmp_path):
+    """静音轨回归钉（Task 2 补欠账）：无音轨视频必须报「无音轨」。"""
+    from capabilities.video.services.studio_review import review_workspace
+    fd = _make_frames(tmp_path, 8)
+    src = _make_video(tmp_path, "noaudio.mp4", with_audio=False)
+    ws = {"frames": fd, "review": tmp_path / "review"}
+    res = review_workspace(ws, _TL, str(src))
+    assert any("音轨" in e for e in res["errors"]), res["errors"]
+
+
+def test_review_workspace_duration_deviation(tmp_path):
+    """timeline.duration 与实测偏差 > 1/fps+0.05 → 报「时长偏差」。"""
+    from capabilities.video.services.studio_review import review_workspace
+    fd = _make_frames(tmp_path, 16)
+    src = _make_video(tmp_path, "v2s.mp4", with_audio=False, duration=2)
+    ws = {"frames": fd, "review": tmp_path / "review"}
+    res = review_workspace(ws, _TL, str(src))  # _TL.duration=1.0，实测≈2.0s
+    assert any("时长偏差" in e for e in res["errors"]), res["errors"]
+
+
+def test_review_workspace_defensive_garbage():
+    """防御式：ws/timeline/video 全是垃圾也不抛异常，错误进 errors。"""
+    from capabilities.video.services.studio_review import review_workspace
+    res = review_workspace({}, {}, "nonexistent.mp4")
+    assert isinstance(res["errors"], list) and res["errors"]
diff --git a/backend/tests/test_studio_spike.py b/backend/tests/test_studio_spike.py
new file mode 100644
index 0000000..18566f6
--- /dev/null
+++ b/backend/tests/test_studio_spike.py
@@ -0,0 +1,205 @@
+"""Task 6：P0 spike 集成验证（live，真实口播素材）。
+
+端到端链路：预切 15s 口播段 → 24fps 抽帧（底帧层）→ RVM 人物抠像（GPU，人物层）
+→ 分层场景（底帧/身后标题条/人物/前景卡片，seekTo 纯函数确定性渲染）→
+450 帧（15s×30fps）渲染 + 混音 → review_workspace 质检 → 本地预览服务打印 URL。
+
+门控：STUDIO_SPIKE=1 才运行（GPU 抠像 + 分钟级逐帧渲染，默认 skip）。
+skip 契约：matte_person 返回 None（GPU/torch/权重不可用）→ pytest.skip，
+不算 FAIL（抠像是增值步骤，降级契约见 person_matting）。
+
+红线遵守：
+  - whisper 禁止在本进程运行（ct2 会拉旧 cuDNN DLL，同进程后续 torch CUDA 必
+    fastfail，2026-10-29 实证，见 test_matting_live2.py）——speech 分段直接复用
+    既有转录产物 outputs/matting_live2/transcript.json（即本视频
+    live_person_talk.mp4 的 whisper 转录结果）。
+  - 动效确定性：场景 seekTo 按t手写插值（见 fixtures/studio_spike/scene/play.js），
+    渲染态无 CSS transition/animation。
+"""
+import asyncio
+import json
+import os
+import shutil
+import subprocess
+import sys
+import tempfile
+from pathlib import Path
+
+import pytest
+
+BACKEND = Path(__file__).resolve().parent.parent
+SRC_VIDEO = BACKEND / "uploads" / "videos" / "live_person_talk.mp4"
+TRANSCRIPT = BACKEND / "outputs" / "matting_live2" / "transcript.json"
+FIXTURE_SCENE = BACKEND / "tests" / "fixtures" / "studio_spike" / "scene"
+
+SEG_START = 22.0  # 22s 起为完整句首「第二大量除汗之后别立刻猛灌冰水」，人物正面清晰
+SEG_DUR = 15.0
+SEG_FILM_ID = "spike1"
+
+pytestmark = pytest.mark.skipif(
+    os.environ.get("STUDIO_SPIKE") != "1",
+    reason="P0 spike live 集成验证（GPU 抠像+分钟级逐帧渲染）：需 STUDIO_SPIKE=1 门控开启",
+)
+
+
+def _run_ffmpeg(cmd: list[str]) -> None:
+    """ffmpeg 执行（Windows 编码安全：str 参数列表禁 shell=True，输出落临时文件再读）。"""
+    from capabilities.video.adapters.html_to_video import resolve_ffmpeg
+
+    ffmpeg = resolve_ffmpeg()
+    assert ffmpeg, "ffmpeg not available (PATH / imageio-ffmpeg / FFMPEG_PATH)"
+    with tempfile.TemporaryDirectory() as td:
+        log = Path(td) / "ffmpeg.log"
+        with open(log, "wb") as f:
+            proc = subprocess.run(
+                [ffmpeg, *cmd], stdout=f, stderr=f, check=False,
+                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
+            )
+        text = log.read_text(encoding="utf-8", errors="replace")
+    assert proc.returncode == 0, f"ffmpeg failed (rc={proc.returncode}): {text[-600:]}"
+
+
+def _precut_segment(seg: Path) -> None:
+    """预切 15s 段：-c copy 优先（时长漂移 ≤0.3s 可接受），不行则重编码精确对齐。"""
+    copy_cmd = ["-y", "-loglevel", "error", "-ss", str(SEG_START), "-t", str(SEG_DUR),
+                "-i", str(SRC_VIDEO), "-c", "copy", str(seg)]
+    from capabilities.video.adapters.studio_assets import probe_duration
+
+    try:
+        _run_ffmpeg(copy_cmd)
+        if abs(probe_duration(str(seg)) - SEG_DUR) <= 0.3:
+            return
+    except AssertionError:
+        pass  # copy 失败/时长漂移超限 → 落重编码
+    _run_ffmpeg(["-y", "-loglevel", "error", "-ss", str(SEG_START), "-t", str(SEG_DUR),
+                 "-i", str(SRC_VIDEO), "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
+                 "-c:a", "aac", str(seg)])
+    assert abs(probe_duration(str(seg)) - SEG_DUR) <= 0.3, (
+        f"预切段时长偏差超限: {probe_duration(str(seg)):.3f}s")
+
+
+def _build_timeline() -> dict:
+    """手写 timeline（3 beat + 各 1 trigger）+ 真实 speech 分段（既有转录产物，段内偏移）。"""
+    segs = json.loads(TRANSCRIPT.read_text(encoding="utf-8"))
+    speech = []
+    for sgm in segs:
+        s0 = max(0.0, sgm["start"] - SEG_START)
+        s1 = min(SEG_DUR, sgm["end"] - SEG_START)
+        if s1 - s0 > 0.05:  # 越过切段边界的残段裁掉
+            speech.append({"start": round(s0, 3), "end": round(s1, 3), "text": sgm["text"]})
+
+    beats = [
+        {"id": "beat_cold", "t_start": 0.0, "t_end": 5.0,
+         "title": "第二件事：别猛灌冰水", "visual_task": "标题条滑入 + 讲解卡片弹入",
+         "triggers": [{"t": 1.6, "text": "大量出汗后，别立刻猛灌冰水"}]},
+        {"id": "beat_stomach", "t_start": 5.0, "t_end": 10.0,
+         "title": "肠胃最怕冷刺激", "visual_task": "标题条切换 + 提示卡片弹入",
+         "triggers": [{"t": 6.8, "text": "很容易腹痛、拉肚子"}]},
+        {"id": "beat_sun", "t_start": 10.0, "t_end": 15.0,
+         "title": "第三件事：做好防晒", "visual_task": "标题条切换 + 收尾卡片弹入",
+         "triggers": [{"t": 11.4, "text": "帽子、防晒衣、防晒霜都安排上"}]},
+    ]
+    return {"fps": 30, "duration": SEG_DUR, "canvas": {"w": 1080, "h": 1920},
+            "speech": speech, "beats": beats}
+
+
+def test_studio_spike_e2e():
+    from capabilities.video.adapters.person_matting import matte_person
+    from capabilities.video.adapters.studio_assets import extract_frames, probe_duration
+    from capabilities.video.adapters.studio_render import render_scene
+    from capabilities.video.services.studio_preview import StudioPreviewServer
+    from capabilities.video.services.studio_review import review_workspace
+    from capabilities.video.services.studio_workspace import (
+        create_workspace, save_timeline, validate_timeline,
+    )
+
+    assert SRC_VIDEO.is_file(), f"源素材缺失: {SRC_VIDEO}"
+    assert TRANSCRIPT.is_file(), f"既有转录产物缺失（禁止本进程跑 whisper）: {TRANSCRIPT}"
+
+    ws = create_workspace(SEG_FILM_ID)
+
+    # 1) 预切 15s 段
+    seg = ws["inputs"] / "seg.mp4"
+    _precut_segment(seg)
+
+    # 2) 底帧层素材：24fps 抽帧
+    base_info = extract_frames(str(seg), ws["assets"] / "base", fps=24.0)
+
+    # 3) 人物层素材：RVM 抠像（GPU；失败/None → skip 不算 FAIL）
+    person_raw = ws["assets"] / "person_raw"
+    if person_raw.exists():
+        shutil.rmtree(person_raw)  # 强制重抠：缓存只认目录，seg 变更会复用错帧
+    matte = asyncio.run(matte_person(str(seg), person_raw))
+    if matte is None:
+        pytest.skip("RVM 人物抠像不可用（torch/GPU/权重缺失，matte_person 返回 None）"
+                    "——人物层无法验证，P0 spike 按降级契约 skip 不算 FAIL")
+
+    # 4) 组织场景工程（fixture → 工作区，帧资产拷入 scene/assets 供 file:// 相对路径加载）
+    scene_dir = ws["scene"]
+    if scene_dir.exists():
+        shutil.rmtree(scene_dir)  # 清旧资产（重跑帧数量可能变化）
+    shutil.copytree(FIXTURE_SCENE, scene_dir)
+    base_dir = scene_dir / "assets" / "base"
+    person_dir = scene_dir / "assets" / "person"
+    base_dir.mkdir(parents=True)
+    person_dir.mkdir(parents=True)
+    for f in sorted((ws["assets"] / "base").glob("frame_*.jpg")):
+        shutil.copy2(f, base_dir / f.name)
+    for f in sorted(matte.frame_dir.glob("*.png")):
+        shutil.copy2(f, person_dir / f"person_{int(f.stem):06d}.png")
+
+    # 5) timeline：真实转录分段 + 手写 beats/triggers，先过 validate_timeline
+    tl = _build_timeline()
+    errs = validate_timeline(tl)
+    assert errs == [], f"timeline 校验失败: {errs}"
+    save_timeline(ws, tl)
+
+    # 6) 场景 manifest（测试侧生成，play.js 消费）
+    manifest = {
+        "duration": SEG_DUR, "canvas": {"w": 1080, "h": 1920},
+        "base": {"dir": "assets/base", "pattern": "frame_%06d.jpg",
+                 "fps": base_info.fps, "count": base_info.count,
+                 "w": base_info.width, "h": base_info.height},
+        "person": {"dir": "assets/person", "pattern": "person_%06d.png",
+                   "fps": matte.fps, "count": matte.count,
+                   "w": matte.width, "h": matte.height},
+        "beats": [{"t_start": b["t_start"], "title": b["title"]} for b in tl["beats"]],
+        "triggers": [{"t": g["t"], "until": b["t_end"], "text": g["text"]}
+                     for b in tl["beats"] for g in b["triggers"]],
+    }
+    (scene_dir / "assets_manifest.js").write_text(
+        "window.STUDIO_SCENE_MANIFEST = " + json.dumps(manifest, ensure_ascii=False) + ";",
+        encoding="utf-8")
+
+    # 7) 音频：seg → aac（渲染混音用）
+    audio = ws["out"] / "seg_audio.m4a"
+    _run_ffmpeg(["-y", "-loglevel", "error", "-i", str(seg), "-vn", "-c:a", "aac", str(audio)])
+
+    # 8) 确定性渲染：450 帧（15s×30fps）+ 混音 → final.mp4
+    final = ws["out"] / "final.mp4"
+    result = render_scene(scene_dir / "index.html", ws["frames"], duration=SEG_DUR, fps=30,
+                          width=1080, height=1920, audio_path=str(audio), out_video=final)
+    assert result.count == 450, f"渲染帧数不符: {result.count}"
+    assert final.is_file(), f"成片未生成: {final}"
+
+    # 9) 质检：errors 必须为空、每 beat 一张 contact sheet、时长 ≈15±0.05
+    review = review_workspace(ws, tl, str(final))
+    assert review["errors"] == [], f"质检 errors: {review['errors']}"
+    assert len(review["contact_sheets"]) == len(tl["beats"])
+    for p in review["contact_sheets"]:
+        assert Path(p).is_file(), f"contact sheet 缺失: {p}"
+    dur_final = probe_duration(str(final))
+    assert abs(dur_final - SEG_DUR) <= 0.05, f"成片时长偏差超 0.05s: {dur_final:.3f}s"
+
+    # 10) 本地预览服（人工观看用；测试结束即停，成片路径供直接播放）
+    server = StudioPreviewServer(ws)
+    url = server.start()
+    try:
+        print(f"\n[studio-spike] 预览 URL: {url}index.html?preview")
+        print(f"[studio-spike] 成片: {final}")
+        print(f"[studio-spike] 响度: {review['loudness']}")
+        print(f"[studio-spike] contact_sheets: {len(review['contact_sheets'])} 张")
+        print(f"[studio-spike] speech 来源: 既有转录产物 {TRANSCRIPT.name}"
+              f"（{len(tl['speech'])} 段真实时间，非占位）")
+    finally:
+        server.stop()
diff --git a/backend/tests/test_studio_workspace.py b/backend/tests/test_studio_workspace.py
new file mode 100644
index 0000000..994f22f
--- /dev/null
+++ b/backend/tests/test_studio_workspace.py
@@ -0,0 +1,119 @@
+def test_workspace_and_timeline_contract(tmp_path):
+    from capabilities.video.services.studio_workspace import (
+        create_workspace, validate_timeline, save_timeline, load_timeline)
+    ws = create_workspace("ws_test1")
+    assert ws["scene"].is_dir() and ws["review"].is_dir()
+    ok = {"fps": 30, "duration": 5.0, "canvas": {"w": 1080, "h": 1920},
+          "speech": [{"start": 0.0, "end": 1.0, "text": "你好", "words": []}],
+          "beats": [{"id": "b1", "type": "claim", "t_start": 0.0, "t_end": 5.0,
+                     "title": "开场", "visual_task": "建立悬念", "assets": [],
+                     "triggers": [{"text": "你好", "t": 0.2}]}]}
+    assert validate_timeline(ok) == []
+    bad = {**ok, "duration": -1, "beats": [{"id": "b1", "t_start": 5.0, "t_end": 4.0, "title": "", "visual_task": ""}]}
+    errs = validate_timeline(bad)
+    assert any("duration" in e for e in errs) and any("t_end" in e for e in errs)
+    p = save_timeline(ws, ok); assert p.is_file()
+    assert load_timeline(ws)["duration"] == 5.0
+
+
+def test_create_workspace_dirs_and_idempotent():
+    from capabilities.video.services.studio_workspace import create_workspace
+    ws = create_workspace("ws_test1")
+    assert set(ws.keys()) == {"inputs", "analysis", "scene", "assets", "frames", "review", "out"}
+    for name, p in ws.items():
+        assert p.is_dir(), name
+    ws2 = create_workspace("ws_test1")  # 幂等：目录已存在不报错
+    assert ws2 == ws
+
+
+def test_validate_missing_required_keys():
+    from capabilities.video.services.studio_workspace import validate_timeline
+    errs = validate_timeline({})
+    assert isinstance(errs, list) and errs
+    for field in ("fps", "duration", "canvas", "speech", "beats"):
+        assert any(field in e for e in errs), (field, errs)
+
+
+def test_validate_type_errors_and_bool():
+    from capabilities.video.services.studio_workspace import validate_timeline
+    tl = {"fps": True, "duration": "5", "canvas": {"w": "1080", "h": -1920},
+          "speech": "no", "beats": {"b1": {}}}
+    errs = validate_timeline(tl)
+    for field in ("fps", "duration", "canvas.w", "canvas.h", "speech", "beats"):
+        assert any(field in e for e in errs), (field, errs)
+
+
+def test_validate_rejects_nonfinite_numbers():
+    # json.loads 默认放行 NaN/Infinity；NaN>0 为 False 会溜过 >0 检查，必须显式拒绝
+    from capabilities.video.services.studio_workspace import validate_timeline
+    tl = {"fps": float("nan"), "duration": float("inf"),
+          "canvas": {"w": 1080, "h": 1920}, "speech": [], "beats": []}
+    errs = validate_timeline(tl)
+    assert any("fps" in e for e in errs), errs
+    assert any("duration" in e for e in errs), errs
+
+
+def test_validate_speech_item_errors():
+    from capabilities.video.services.studio_workspace import validate_timeline
+    tl = {"fps": 30, "duration": 5.0, "canvas": {"w": 1080, "h": 1920},
+          "speech": [{"start": 1.0, "end": 1.0}, 42],
+          "beats": [{"id": "b1", "t_start": 0.0, "t_end": 5.0, "title": "t", "visual_task": "v"}]}
+    errs = validate_timeline(tl)
+    assert any("end" in e and "start" in e for e in errs), errs
+    assert any("text" in e for e in errs), errs
+    assert any("speech[1]" in e for e in errs), errs
+
+
+def test_validate_beats_item_and_order_errors():
+    from capabilities.video.services.studio_workspace import validate_timeline
+    tl = {"fps": 30, "duration": 5.0, "canvas": {"w": 1080, "h": 1920},
+          "speech": [], "beats": [
+              {"id": "b1", "t_start": 0.0, "t_end": 3.0, "title": "a", "visual_task": "v"},
+              {"id": "b2", "t_start": 2.0, "t_end": 4.0, "title": "b", "visual_task": "v"},
+              {"id": 3, "t_start": "x", "t_end": None, "visual_task": "v"},
+          ]}
+    errs = validate_timeline(tl)
+    assert any("t_start" in e and "重叠" in e for e in errs), errs  # b2 与 b1 重叠
+    assert any("beats[2].id" in e for e in errs), errs
+    assert any("beats[2].t_start" in e for e in errs), errs
+    assert any("beats[2].t_end" in e for e in errs), errs
+    assert any("beats[2].title" in e for e in errs), errs
+
+
+def test_validate_trigger_rules():
+    from capabilities.video.services.studio_workspace import validate_timeline
+    beat_ok = {"id": "b1", "t_start": 0.0, "t_end": 5.0, "title": "t", "visual_task": "v"}
+    tl = {"fps": 30, "duration": 5.0, "canvas": {"w": 1080, "h": 1920},
+          "speech": [], "beats": [
+              {**beat_ok, "triggers": [{"t": 5.1}, {"text": "无t"}, "x"]},
+          ],
+          "triggers": [{"t": 0.2}]}  # 挂错位置：顶层 triggers
+    errs = validate_timeline(tl)
+    assert any("triggers[0].t" in e and "5.1" in e for e in errs), errs      # t 超出 beat 区间
+    assert any("triggers[1].t" in e and "缺失" in e for e in errs), errs      # 缺 t
+    assert any("triggers[2]" in e for e in errs), errs                        # 非对象
+    assert any("挂错" in e or "beats[" in e for e in errs), errs              # 顶层 triggers 报挂错位置
+
+
+def test_boundary_cases():
+    # 边界语义钉住：前 beat t_end == 后 beat t_start 不判重叠（开区间语义）；
+    # trigger t 恰在所属 beat 的 t_start/t_end 边界均合法（闭区间语义）
+    from capabilities.video.services.studio_workspace import validate_timeline
+    tl = {"fps": 30, "duration": 5.0, "canvas": {"w": 1080, "h": 1920},
+          "speech": [], "beats": [
+              {"id": "b1", "t_start": 0.0, "t_end": 2.5, "title": "a", "visual_task": "v",
+               "triggers": [{"text": "甲", "t": 0.0}]},          # t == b1.t_start
+              {"id": "b2", "t_start": 2.5, "t_end": 5.0, "title": "b", "visual_task": "v",
+               "triggers": [{"text": "乙", "t": 2.5},            # t == b2.t_start（相邻边界点）
+                            {"text": "丙", "t": 5.0}]},          # t == b2.t_end
+          ]}
+    assert validate_timeline(tl) == []
+
+
+def test_validate_never_raises_on_garbage():
+    from capabilities.video.services.studio_workspace import validate_timeline
+    for garbage in (None, [], "tl", 42, {"fps": None, "canvas": []},
+                    {"beats": [{"triggers": 7, "t_start": True}]},
+                    {"speech": [None], "beats": [None, {"triggers": [{"t": {}}]}]}):
+        errs = validate_timeline(garbage)
+        assert isinstance(errs, list) and all(isinstance(e, str) for e in errs), garbage
