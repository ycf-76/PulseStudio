# Task 4: 质检工具（studio_review）

**Files:**
- Create: `backend/capabilities/video/services/studio_review.py`
- Test: `backend/tests/test_studio_review.py`

**Interfaces:**
- Consumes: Task 1 `probe_duration`（`capabilities.video.adapters.studio_assets`，PyAV 实现，签名 `probe_duration(media_path: str) -> float`）；`resolve_ffmpeg()`；Pillow
- Produces:
  - `def contact_sheet(frames_dir: Path, times: list[float], fps: float, out_png: Path, cell: int = 360) -> Path` — 按时间点取帧（`round(t*fps)` 最近帧号，戳到存在文件）拼网格图（列数 = ceil(sqrt(n))，每格等比缩放到 cell 宽，下标注时间点）
  - `def loudness_report(video_path: str) -> dict` — `{"rms": float, "peak": float, "duration": float}`（dB，rms/peak 为负 dB 值）
  - `def completeness_check(frames_dir: Path, expected: int) -> list[str]` — 帧序完整（无缺号、无 0 字节），错误信息带帧号
  - `def review_workspace(ws: dict, timeline: dict, video_path: str) -> dict` — 汇总：每 beat 的 t_start/t_end 抽帧 contact_sheet 到 ws["review"]/（文件名 beat_<id>_<t>.png）+ 响度 + 完整性（expected = round(duration*fps)）+ 时长偏差（probe_duration vs timeline.duration，> 1/fps + 0.05 报错入 errors）+ 静音轨回归钉（无声轨或 duration≈0 报错），返回 `{"contact_sheets": [...], "loudness": {...}, "errors": [...]}`

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
    (fd / ("frame_%06d.png" % 3)).unlink()
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

loudness 用 `ffmpeg -i x -af astats=metadata=1 -f null -` 解析 stderr 的 RMS level/Peak level（dB）；中文/编码安全（errors="replace" 或输出重定向文件再读）。contact_sheet 用 Pillow（文件内已 import 的方式对齐）。帧号戳到存在文件：若 round(t*fps) 越界/缺号，取最近的存在的帧并在 sheet 内继续。

- [ ] **Step 4: Run test to verify it passes**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_studio_review.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/capabilities/video/services/studio_review.py backend/tests/test_studio_review.py
git commit -m "feat(studio): 质检工具（contact sheet/响度/帧完整性/时长偏差）"
```
