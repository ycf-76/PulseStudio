# Task 1: 原片抽帧 adapter（studio_assets）

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
