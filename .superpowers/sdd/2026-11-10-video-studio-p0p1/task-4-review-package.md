d1d7b24 feat(studio): 质检工具（contact sheet/响度/帧完整性/时长偏差）
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
