# Video Studio P0+P1（确定性渲染地基）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建成 agent 驱动口播影片的制作地基：场景工程逐帧确定性渲染 → 编码混音 → 质检产物，并用 spike 样段验证音画对齐与动效真实可见。

**Architecture:** 场景页（HTML+CSS，暴露 `window.seekTo(t)`）负责完整合成（原片抽帧序列 + 人物透明帧序列为素材，页内分层绘制）；独立子进程 Playwright 逐帧截图（复用 _render_worker 的硬退出模式）；ffmpeg 帧序编码 + 混音；质检工具产出抽帧连播/响度/完整性报告。工作区目录与 timeline.json 契约固定下来，供 P2 创作 agent 消费。

**Tech Stack:** Python 3.12 / FastAPI 后端（backend/.venv）、Playwright sync API（子进程）、ffmpeg（imageio_ffmpeg 内置二进制，经 resolve_ffmpeg）、Pillow（拼图）、pytest。

**Spec:** docs/superpowers/specs/2026-11-10-agent-video-studio-design.md（本计划只覆盖设计 §6 的 P0+P1；P2 创作 agent 循环与 P3 泛化验证依赖本阶段的实测结论，P1 验收后另立计划）

## Global Constraints

- ffmpeg 一律经 `capabilities.video.adapters.html_to_video.resolve_ffmpeg` 获取路径，禁止硬编码 ffmpeg 路径
- Playwright 必须跑在独立子进程，帧落盘后 `os._exit(0)` 硬退出（本机 browser.close() 挂起，ADR-001 补充）；Windows 用 `CREATE_NO_WINDOW`
- 场景时钟契约：`window.seekTo(t)` 确定性设态——场景代码禁随机数、禁依赖真实流逝时间（Date/performance.now 驱动画面）
- 画布 1080×1920 竖屏；渲染 fps 默认 30，原片底帧抽帧 fps 默认 24（磁盘预算内）
- 平台层禁止出现任何风格常量（颜色/字体/动效参数不得写死进 adapter/service——它们属于场景代码或方法论规则）
- whisper 转录与 RVM 抠像不得同进程（ct2 拉旧 cuDNN DLL 导致 native fastfail）；本阶段不新增转录调用，只消费已有产物
- 全部输出落 `backend/outputs/studio/<film_id>/`；产品代码不复制 agent-motion 仓库的代码与规则文本

## Review Focus

1. **seekTo 非确定性**：场景页用了 `performance.now()`/随机数 → 渲染帧与预览画面不一致。测试：Task 2 的场景夹具含确定性断言（同一 t 渲染两次帧字节一致）。
2. **音画时长漂移**：编码后 MP4 时长与 timeline.duration 偏差 > 1 帧。测试：Task 2 断言 ffprobe 时长误差 ≤ 1/fps + 0.05s。
3. **音频盖过人声**：SFX 混音后人声被掩没。测试：Task 4 响度报告输出 SFX 段与全程 RMS 对比字段，断言可读出差异。
4. **抽帧 fps 与人物轨 fps 不一致导致底/人错位**：抽取器必须显式记录 fps 且允许与渲染 fps 不同。测试：Task 1 断言 FrameSeqInfo.fps 回读正确。
5. **scene 相对路径资产在 file:// 下 404**：渲染必须以 `page.goto(file://...)` 打开磁盘场景文件而非 set_content。测试：Task 2 夹具引用相对路径图片，断言渲染出的帧含该图片像素（非空白）。

---

### Task 1: 原片抽帧 adapter（studio_assets）

**Files:**
- Create: `backend/capabilities/video/adapters/studio_assets.py`
- Test: `backend/tests/test_studio_assets.py`

**Interfaces:**
- Consumes: `resolve_ffmpeg()`（html_to_video）；subprocess
- Produces:
  - `@dataclass FrameSeqInfo { frame_dir: Path, count: int, fps: float, width: int, height: int }`
  - `def extract_frames(video_path: str, out_dir: Path, fps: float = 24.0) -> FrameSeqInfo` — 产出 `frame_%06d.jpg`
  - `def probe_duration(media_path: str) -> float` — ffprobe 秒数

- [ ] **Step 1: Write the failing test**

```python
def test_extract_frames_counts_and_meta(tmp_path):
    # 用 ffmpeg lavfi 造 2s 30fps 测试视频
    from capabilities.video.adapters.studio_assets import extract_frames, probe_duration
    import subprocess
    from capabilities.video.adapters.html_to_video import resolve_ffmpeg
    src = tmp_path / "src.mp4"
    subprocess.run([resolve_ffmpeg(), "-y", "-f", "lavfi",
        "-i", "testsrc=duration=2:size=320x568:rate=30",
        "-pix_fmt", "yuv420p", str(src)], check=True, capture_output=True)
    info = extract_frames(str(src), tmp_path / "frames", fps=24.0)
    assert info.count == 48 and abs(info.fps - 24.0) < 0.01
    assert info.width == 320 and info.height == 568
    assert (info.frame_dir / "frame_%06d.jpg" % 0).is_file()
    assert abs(probe_duration(str(src)) - 2.0) < 0.1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_studio_assets.py -v`
Expected: FAIL（ModuleNotFoundError: studio_assets）

- [ ] **Step 3: Implement** `extract_frames` / `probe_duration` / `FrameSeqInfo`

ffmpeg `-vf fps=24 -qscale:v 2` 输出 jpg 序列；meta 用 ffprobe json 解析（宽度/高度/时长）。注意中文路径：subprocess 传 str 参数即可，输出重定向到文件再读（Windows 编码安全）。

- [ ] **Step 4: Run test to verify it passes**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_studio_assets.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/capabilities/video/adapters/studio_assets.py backend/tests/test_studio_assets.py
git commit -m "feat(studio): 原片抽帧 adapter，产出帧序列与元信息"
```

### Task 2: 确定性渲染 adapter（studio_render）

**Files:**
- Create: `backend/capabilities/video/adapters/studio_render_worker.py`（子进程入口，仿 _render_worker.py 结构）
- Create: `backend/capabilities/video/adapters/studio_render.py`（父进程封装）
- Test: `backend/tests/test_studio_render.py`

**Interfaces:**
- Consumes: `resolve_ffmpeg()`；Task 1 `probe_duration`
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

### Task 3: 工作区与 timeline 契约（studio_workspace）

**Files:**
- Create: `backend/capabilities/video/services/studio_workspace.py`
- Test: `backend/tests/test_studio_workspace.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `def create_workspace(film_id: str) -> dict[str, Path]` — 建 `outputs/studio/<film_id>/{inputs,analysis,scene,assets,frames,review,out}`，返回各路径 dict
  - `def validate_timeline(tl: dict) -> list[str]` — 返回错误列表（空=通过）：必填 `fps>0/duration>0/canvas{w,h}`；`speech[]` 每项含 start/end/text 且 end>start；`beats[]` 每项含 `id/t_start/t_end/title/visual_task` 且 `t_end>t_start`、beats 按 t_start 升序不重叠；`triggers[].t` 落在其 beat 的 [t_start,t_end] 内
  - `def save_timeline(ws: dict, tl: dict) -> Path` / `def load_timeline(ws: dict) -> dict`

- [ ] **Step 1: Write the failing test**

```python
def test_workspace_and_timeline_contract(tmp_path):
    from capabilities.video.services.studio_workspace import (
        create_workspace, validate_timeline, save_timeline, load_timeline)
    ws = create_workspace("ws_test1")
    assert ws["scene"].is_dir() and ws["review"].is_dir()
    ok = {"fps": 30, "duration": 5.0, "canvas": {"w": 1080, "h": 1920},
          "speech": [{"start": 0.0, "end": 1.0, "text": "你好", "words": []}],
          "beats": [{"id": "b1", "type": "claim", "t_start": 0.0, "t_end": 5.0,
                     "title": "开场", "visual_task": "建立悬念", "assets": [],
                     "triggers": [{"text": "你好", "t": 0.2}]}]}
    assert validate_timeline(ok) == []
    bad = {**ok, "duration": -1, "beats": [{"id": "b1", "t_start": 5.0, "t_end": 4.0, "title": "", "visual_task": ""}]}
    errs = validate_timeline(bad)
    assert any("duration" in e for e in errs) and any("t_end" in e for e in errs)
    p = save_timeline(ws, ok); assert p.is_file()
    assert load_timeline(ws)["duration"] == 5.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_studio_workspace.py -v`
Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 3: Implement** 三个函数 + dataclass-free 纯校验

错误信息带字段名（duration/t_end/…）便于创作 agent 自读自修。workspace 根由 `_OUTPUT_DIR` 惯例推导（`Path(__file__).…parents[3]/"outputs"/"studio"`）。

- [ ] **Step 4: Run test to verify it passes**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_studio_workspace.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/capabilities/video/services/studio_workspace.py backend/tests/test_studio_workspace.py
git commit -m "feat(studio): 工作区脚手架与 timeline.json 校验契约"
```

### Task 4: 质检工具（studio_review）

**Files:**
- Create: `backend/capabilities/video/services/studio_review.py`
- Test: `backend/tests/test_studio_review.py`

**Interfaces:**
- Consumes: Task 1 `probe_duration`；resolve_ffmpeg；Pillow
- Produces:
  - `def contact_sheet(frames_dir: Path, times: list[float], fps: float, out_png: Path, cell: int = 360) -> Path` — 按时间点取帧拼网格图
  - `def loudness_report(video_path: str) -> dict` — ffprobe/astats：`{"rms": float, "peak": float, "duration": float}`
  - `def completeness_check(frames_dir: Path, expected: int) -> list[str]` — 帧序完整（无缺号、无 0 字节）
  - `def review_workspace(ws: dict, timeline: dict, video_path: str) -> dict` — 汇总：每 beat 的 t_start/t_end 抽帧 contact_sheet 到 review/ + 响度 + 完整性 + 时长偏差（vs timeline.duration，>1 帧报错），返回 `{"contact_sheets": [...], "loudness": {...}, "errors": [...]}`

- [ ] **Step 1: Write the failing test**

```python
def test_review_on_synthetic_frames(tmp_path):
    from PIL import Image
    from capabilities.video.services.studio_review import (
        contact_sheet, completeness_check, loudness_report)
    fd = tmp_path / "frames"; fd.mkdir()
    for i in range(8):
        Image.new("RGB", (320, 568), (i * 20 % 255, 0, 0)).save(fd / ("frame_%06d.png" % i))
    assert completeness_check(fd, 8) == []
    (fd / "frame_%06d.png" % 3).unlink()
    assert any("3" in e for e in completeness_check(fd, 8))
    sheet = contact_sheet(fd, [0.0, 0.5], fps=8, out_png=tmp_path / "sheet.png")
    assert sheet.is_file() and Image.open(sheet).size[0] >= 640
    from capabilities.video.adapters.html_to_video import resolve_ffmpeg
    import subprocess
    wav = tmp_path / "a.wav"
    subprocess.run([resolve_ffmpeg(), "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
        str(wav)], check=True, capture_output=True)
    rep = loudness_report(str(wav))
    assert rep["peak"] > -30 and "duration" in rep
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_studio_review.py -v`
Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 3: Implement** 四个函数

loudness 用 `ffmpeg -i x -af astats=metadata=1 -f null -` 解析 stderr 的 RMS level/Peak level（dB）；contact_sheet 取帧按最近帧号（`round(t*fps)` 戳到存在文件）。

- [ ] **Step 4: Run test to verify it passes**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_studio_review.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/capabilities/video/services/studio_review.py backend/tests/test_studio_review.py
git commit -m "feat(studio): 质检工具（contact sheet/响度/帧完整性/时长偏差）"
```

### Task 5: 本地预览服务（studio_preview）

**Files:**
- Create: `backend/capabilities/video/services/studio_preview.py`
- Test: `backend/tests/test_studio_preview.py`

**Interfaces:**
- Consumes: studio_workspace 的 ws dict
- Produces:
  - `class StudioPreviewServer` — `__init__(ws: dict, port: int = 0)`；`def start(self) -> str`（返回 `http://127.0.0.1:<port>/`，port=0 时系统分配）；`def stop(self) -> None`；`def url_for(self, rel: str) -> str`
  - 用 `http.server.ThreadingHTTPServer` + SimpleHTTPRequestHandler（directory=ws["scene"]），后台线程；`/` 默认 index.html
  - 场景页实时预览的播放循环属于场景代码职责（rAF 调 seekTo），服务只做静态托管——契约写入 Task 6 spike 场景的 play.js

- [ ] **Step 1: Write the failing test**

```python
def test_preview_server_serves_scene(tmp_path):
    from capabilities.video.services.studio_preview import StudioPreviewServer
    (tmp_path / "index.html").write_text("<html><body>hi</body></html>", encoding="utf-8")
    ws = {"scene": tmp_path}
    srv = StudioPreviewServer(ws); url = srv.start()
    import urllib.request
    body = urllib.request.urlopen(url).read().decode("utf-8")
    assert "hi" in body
    srv.stop()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_studio_preview.py -v`
Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 3: Implement** StudioPreviewServer

threading.daemon 跑 serve_forever；stop 用 shutdown()+server_close()。

- [ ] **Step 4: Run test to verify it passes**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_studio_preview.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/capabilities/video/services/studio_preview.py backend/tests/test_studio_preview.py
git commit -m "feat(studio): 场景本地预览静态服务"
```

### Task 6: P0 spike 集成验证（live，真实口播素材）

**Files:**
- Create: `backend/tests/test_studio_spike.py`（`*_live` 风格：真 GPU 抠像缓存复用 + 真渲染，pytest 默认跳过，`STUDIO_SPIKE=1` 环境变量开启）
- Create: `backend/outputs/studio/spike1/scene/`（手工最小场景工程：index.html + play.js + styles.css）

**Interfaces:**
- Consumes: Task 1 `extract_frames`、Task 2 `render_scene`、Task 3 `create_workspace/save_timeline`、Task 4 `review_workspace`、Task 5 `StudioPreviewServer`、既有 `matte_person(video_path, out_dir) -> MatteResult`
- Produces: spike 成片 `backend/outputs/studio/spike1/out/final.mp4` + review 产物（contact sheets/响度报告）——交付用户肉眼验收动效与音画

- [ ] **Step 1: 准备 spike 场景工程**

spike1/scene/index.html：`seekTo(t)` 驱动四层——底帧 `<img id="base">`（src 按 t 换 `../assets/base/frame_%06d.jpg`）、身后层动效标题条（CSS transition 由 seekTo 置类名，t 进入 beat 后 0.4s 滑入）、人物层 `<img id="person">`（`../assets/person/frame_%06d.png`，RGBA）、前景层触发卡片（trigger.t 后弹入）；play.js：`?preview` 时 rAF 循环调 seekTo（实时预览模式）。样式从简——spike 验管线不验美观。

- [ ] **Step 2: Write the live test**

```python
import os, pytest
pytestmark = pytest.mark.skipif(os.environ.get("STUDIO_SPIKE") != "1",
    reason="真机 spike：需 GPU 抠像与真实口播素材，STUDIO_SPIKE=1 开启")

def test_spike_end_to_end():
    # 素材：live_person_talk.mp4 取 15s 段（ffmpeg -ss 20 -t 15 预切到 inputs/）
    # 1) extract_frames(段视频, assets/base, fps=24) → FrameSeqInfo
    # 2) matte_person(段视频, assets/person) → rgba 帧序列（复用缓存则跳过 GPU）
    # 3) 手写 timeline.json（3 个 beat + 1 个 trigger，词级时间来自既有转录产物，
    #    无则用段内均匀占位并在 review 报告标注"占位时间轴"）
    # 4) render_scene(scene/index.html, frames, duration=15, fps=30, audio_path=段音频, out_video=out/final.mp4)
    # 5) review_workspace：errors==[]（时长偏差/完整性）；contact sheets 落 review/
    # 6) StudioPreviewServer 起服，打印预览 URL 到 stdout（人工观看用）
    # 断言：final.mp4 存在、probe_duration≈15±0.05、review errors 空
```

- [ ] **Step 3: Run live test**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_studio_spike.py -v -s`（环境变量 `STUDIO_SPIKE=1`）
Expected: PASS；stdout 打印预览 URL 与成片路径

- [ ] **Step 4: 人工验收（设计文档 P0 出口）**

用户看 final.mp4（或开预览 URL）确认：动效真实可见（标题滑入/卡片弹入）、音画对齐、人物层与底帧同步无重影。验收通过即 P0+P1 完成。

- [ ] **Step 5: Commit**

```bash
git add backend/tests/test_studio_spike.py backend/outputs/studio/spike1/scene
git commit -m "feat(studio): P0 spike 集成验证（分层场景+确定性渲染+质检）"
```

## P2 / P3（本计划不含，P1 验收后另立计划）

- **P2 创作循环**：video_studio.py 入口 skill + 轻量工具循环（读规则→内容计划→素材→写场景→渲染→自查→修→交付）+ 内置方法论文本（studio_methods/）+ 前端预览接入 + video_jobs 进度事件
- **P3 泛化验证**：第二份不同打法 skill 输入，平台层 diff=0 产出风格迥异成片
- 依赖本阶段的实测输入：单帧渲染耗时（决定 P2 的重试预算）、timeline 契约的 agent 实操摩擦、review 产物对 agent 自查的可读性
