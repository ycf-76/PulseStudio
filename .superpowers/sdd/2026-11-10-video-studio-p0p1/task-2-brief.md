# Task 2: 确定性渲染 adapter（studio_render）

**Files:**
- Create: `backend/capabilities/video/adapters/studio_render_worker.py`（子进程入口，仿 _render_worker.py 结构）
- Create: `backend/capabilities/video/adapters/studio_render.py`（父进程封装）
- Test: `backend/tests/test_studio_render.py`

**Interfaces:**
- Consumes: `resolve_ffmpeg()`；Task 1 `probe_duration`（已实现于 `capabilities.video.adapters.studio_assets`，PyAV 实现，签名 `probe_duration(media_path: str) -> float`）
- Produces:
  - `@dataclass RenderResult { frames_dir: Path, count: int, fps: float, elapsed: float, video_path: Path | None }`
  - `def render_scene(scene_html: Path, frames_dir: Path, duration: float, fps: int = 30, width: int = 1080, height: int = 1920, audio_path: str | None = None, out_video: Path | None = None) -> RenderResult`
  - 约定：worker 以 `page.goto(file://<scene_html 绝对路径>)` 打开（相对资产可用），逐帧 `page.evaluate("(t) => window.seekTo(t)", t)` + `screenshot(frame_%06d.png, omit_background=False)`；末尾写 `_worker_status.json` 后 `os._exit(0)`
  - 无 audio → 静音轨合成；`-r <fps> -i frame_%06d.png` 编码 h264 yuv420p + `-shortest` 混音

- [ ] **Step 1: Write the failing test**

```python
def test_render_scene_deterministic_and_mux(tmp_path):
    from capabilities.video.adapters.studio_render import render_scene
    scene = tmp_path / "scene"; scene.mkdir()
    # 最小场景：seekTo 驱动方块位移（相对路径引用 assets/point.png）
    (scene / "assets").mkdir()
    from PIL import Image
    Image.new("RGB", (40, 40), (255, 0, 0)).save(scene / "assets" / "point.png")
    (scene / "index.html").write_text(
        '<img id="p" src="assets/point.png" style="position:absolute;left:0;top:0">'
        '<script>window.seekTo=(t)=>{document.getElementById("p").style.left=(t*100)+"px";};'
        "window.seekTo(0);</script>", encoding="utf-8")
    r1 = render_scene(scene / "index.html", tmp_path / "f1", duration=0.5, fps=8, width=320, height=568)
    r2 = render_scene(scene / "index.html", tmp_path / "f2", duration=0.5, fps=8, width=320, height=568)
    assert r1.count == 4
    assert (r1.frames_dir / "frame_%06d.png" % 0).read_bytes() == (r2.frames_dir / "frame_%06d.png" % 0).read_bytes()  # 确定性
    from PIL import Image as I2
    im = I2.open(r1.frames_dir / "frame_%06d.png" % 4)
    assert im.getpixel((50, 10))[0] > 200  # 0.5s 时方块已位移且资产非空白
    # 混音：1s 静音音轨
    from capabilities.video.adapters.html_to_video import resolve_ffmpeg
    import subprocess
    wav = tmp_path / "a.wav"
    subprocess.run([resolve_ffmpeg(), "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
        "-t", "0.5", str(wav)], check=True, capture_output=True)
    r3 = render_scene(scene / "index.html", tmp_path / "f3", duration=0.5, fps=8,
        width=320, height=568, audio_path=str(wav), out_video=tmp_path / "out.mp4")
    assert r3.video_path.is_file()
    from capabilities.video.adapters.studio_assets import probe_duration
    assert abs(probe_duration(str(r3.video_path)) - 0.5) < 1 / 8 + 0.05  # 音画时长偏差 ≤1帧
```

（注：`(r1.frames_dir / "frame_%06d.png" % 0)` 与 Task 1 同样的运算符优先级笔误，正确写法为 `(r1.frames_dir / ("frame_%06d.png" % 0))` 与 `(r2.frames_dir / ("frame_%06d.png" % 0))`——实现测试时直接写成正确形式，此为已裁决的 brief 笔误修正，其余值逐字使用。）

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_studio_render.py -v`
Expected: FAIL（ModuleNotFoundError: studio_render）

- [ ] **Step 3: Implement** `studio_render_worker.py` + `render_scene`

worker 仿 _render_worker.py：argv 接 `<scene_html> <frames_dir> <w> <h> <duration> <fps>`，`page.goto(Path(scene_html).resolve().as_uri())`，等 `load` + 300ms 字体/资产余量后循环截图；结束写 status 后 `os._exit(0)`。父进程 `render_scene` 用 `subprocess.Popen([sys.executable, worker_path, ...], creationflags=CREATE_NO_WINDOW)`，`communicate(timeout=...)` 超时则 `taskkill /T /F` 进程树（Windows）；读 `_worker_status.json` 校验 ok；有 out_video 时 ffmpeg 帧序编码 + `-i audio -c:a aac -shortest`。

- [ ] **Step 4: Run test to verify it passes**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_studio_render.py -v`
Expected: PASS（Chromium 启动 ~3-6s，可接受）

- [ ] **Step 5: Commit**

```bash
git add backend/capabilities/video/adapters/studio_render_worker.py backend/capabilities/video/adapters/studio_render.py backend/tests/test_studio_render.py
git commit -m "feat(studio): 确定性场景渲染 adapter（逐帧截图+编码混音）"
```
