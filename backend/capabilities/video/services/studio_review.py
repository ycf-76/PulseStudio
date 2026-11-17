"""Studio 质检工具（Task 4）— P2 创作 agent 的「眼睛」。

四件套（不含任何风格常量，颜色/字体归 overlay_templates / themes 层）：
  - contact_sheet：按时间点取最近存在帧拼网格图（列数 = ceil(sqrt(n))，
    每格等比缩放到 cell 宽，下标注时间点）
  - loudness_report：`ffmpeg -af astats=metadata=1 -f null -` 解析 stderr
    Overall 段的 RMS level dB / Peak level dB（数字静音的 -inf 钉到 -120，
    保证 JSON 可序列化）
  - completeness_check：帧序完整（无缺号、无 0 字节、无越界多余帧），错误带帧号
  - review_workspace：防御式汇总（每 beat contact sheet + 响度 + 帧完整性 +
    时长偏差容差 1/fps+0.05 + 静音轨回归钉）。任何环节失败进 errors 列表，
    绝不抛异常——它是 agent 自查通道，崩溃 = 自查失效。

Windows 编码安全：subprocess 一律 str 参数列表 + CREATE_NO_WINDOW（禁
shell=True）；ffmpeg 输出重定向到临时文件再读（errors="replace"）。
"""

from __future__ import annotations

import bisect
import logging
import math
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from capabilities.video.adapters.html_to_video import resolve_ffmpeg
from capabilities.video.adapters.studio_assets import probe_duration

logger = logging.getLogger(__name__)

_LABEL_H = 24  # contact sheet 单格下方时间标注条高度
_DB_FLOOR = -120.0  # astats -inf（数字静音）钉到的 dB 下限，保证 JSON 安全

_FRAME_FILE_RE = re.compile(r"^frame_(\d+)\.(png|jpg)$")
_SCENE_TEXT_SUFFIXES = {".html", ".js", ".css"}  # 素材使用检查扫描的 scene 文本后缀
_REF_TOKEN_RE = re.compile(r"\.\./(?:\.cache/(?:frames|render)|frames|assets)/[A-Za-z0-9_%\-\.\/]+")
_PATTERN_FIELD_RE = re.compile(r"%0(\d+)d")
_DB_NUM = r"(-?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?|-?inf|nan)"
_RMS_RE = re.compile(r"RMS level dB:\s*" + _DB_NUM)  # 勿改成 "RMS.*dB"，会误匹配 RMS peak dB
_PEAK_RE = re.compile(r"Peak level dB:\s*" + _DB_NUM)
_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):([\d.]+)")


def _is_num(v: object) -> bool:
    """数字且非 bool、非 NaN/Inf（与 studio_workspace.validate_timeline 同语义）。"""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return False
    if isinstance(v, float):
        return math.isfinite(v)
    return True


def _run_ffmpeg(cmd: list[str]) -> tuple[int, str]:
    """执行 ffmpeg；stdout/stderr 落临时文件后读回（Windows 编码安全）。"""
    with tempfile.TemporaryDirectory() as td:
        log = Path(td) / "ffmpeg.log"
        with open(log, "wb") as f:
            proc = subprocess.run(
                cmd,
                stdout=f,
                stderr=f,
                check=False,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
        return proc.returncode, log.read_text(encoding="utf-8", errors="replace")


def _ffmpeg_i_stderr(media_path: str) -> str:
    """`ffmpeg -i`（无输出文件，rc=1 属预期）的 stderr 文本。"""
    ffmpeg = resolve_ffmpeg()
    if not ffmpeg:
        return ""
    _, text = _run_ffmpeg([ffmpeg, "-hide_banner", "-i", str(media_path)])
    return text


def _frame_index(frames_dir: Path) -> dict[int, Path]:
    """帧目录 → {帧号: 路径}（frame_%06d.png/.jpg 兼容 Task1/Task2 产物）。"""
    idx: dict[int, Path] = {}
    for p in Path(frames_dir).iterdir():
        m = _FRAME_FILE_RE.match(p.name)
        if m:
            idx[int(m.group(1))] = p
    return idx


def _nearest_frame(indices: list[int], target: int) -> int:
    """离 target 最近的已存在帧号；距离并列取小号（确定性）。"""
    pos = bisect.bisect_left(indices, target)
    cands: list[int] = []
    if pos < len(indices):
        cands.append(indices[pos])
    if pos > 0:
        cands.append(indices[pos - 1])
    return min(cands, key=lambda i: (abs(i - target), i))


def contact_sheet(
    frames_dir: Path,
    times: list[float],
    fps: float,
    out_png: Path,
    cell: int = 360,
) -> Path:
    """按时间点取帧拼网格图：round(t*fps) 最近帧号，戳到存在文件。

    列数 = ceil(sqrt(n))，每格等比缩放到 cell 宽，下标注时间点。
    帧目录为空 / times 为空 / fps 非法时抛异常（由 review_workspace 兜底捕获）。
    """
    if not _is_num(fps) or fps <= 0:
        raise ValueError(f"contact_sheet: fps 必须 > 0，实际为 {fps!r}")
    if not times:
        raise ValueError("contact_sheet: times 为空")
    idx_map = _frame_index(frames_dir)
    if not idx_map:
        raise RuntimeError(f"contact_sheet: 帧目录无 frame_*.png/jpg: {frames_dir}")

    indices = sorted(idx_map)
    cells: list[tuple[float, Image.Image]] = []
    for t in times:
        target = round(float(t) * float(fps))
        im = Image.open(idx_map[_nearest_frame(indices, target)]).convert("RGB")
        h = max(1, round(im.height * cell / im.width))
        cells.append((float(t), im.resize((cell, h), Image.Resampling.LANCZOS)))

    cols = math.ceil(math.sqrt(len(cells)))
    rows = math.ceil(len(cells) / cols)
    try:
        font = ImageFont.load_default(size=14)
    except TypeError:  # Pillow < 10.1 无 size 参数
        font = ImageFont.load_default()

    row_h = [
        max(im.height for _, im in cells[r * cols : (r + 1) * cols]) + _LABEL_H for r in range(rows)
    ]
    canvas = Image.new("RGB", (cols * cell, sum(row_h)), (24, 24, 24))
    draw = ImageDraw.Draw(canvas)
    y = 0
    for r in range(rows):
        for c, (t, im) in enumerate(cells[r * cols : (r + 1) * cols]):
            x = c * cell
            canvas.paste(im, (x, y))
            label = f"t={t:.2f}s"
            tw = draw.textlength(label, font=font)
            draw.text(
                (x + (cell - tw) / 2, y + im.height + 4), label, fill=(235, 235, 235), font=font
            )
        y += row_h[r]

    out_png = Path(out_png)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_png)
    return out_png


def loudness_report(video_path: str) -> dict:
    """响度（dB）：ffmpeg astats 解析 Overall 段 RMS/Peak level dB + 时长。

    返回 {"rms": float, "peak": float, "duration": float}；rms/peak 为负 dB 值，
    数字静音的 -inf 钉到 -120 保证 JSON 安全。duration 复用 Task 1 probe_duration。

    Raises:
        RuntimeError: ffmpeg 缺失、stderr 无 astats 数值（典型：无音轨）或探测失败。
    """
    ffmpeg = resolve_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("ffmpeg not available (PATH / imageio-ffmpeg / FFMPEG_PATH)")
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-i",
        str(video_path),
        "-af",
        "astats=metadata=1",
        "-f",
        "null",
        "-",
    ]
    rc, text = _run_ffmpeg(cmd)

    seg = text[text.rfind("Overall") :] if "Overall" in text else text
    rms = _parse_db(_RMS_RE, seg)
    peak = _parse_db(_PEAK_RE, seg)
    if rms is None or peak is None:
        raise RuntimeError(f"loudness astats parse failed (rc={rc}, no dB values): {text[-400:]}")
    return {"rms": rms, "peak": peak, "duration": probe_duration(str(video_path))}


def _parse_db(pattern: re.Pattern[str], text: str) -> float | None:
    """提取单个 dB 数值；-inf/nan 钉到 _DB_FLOOR，+inf 钉到 0（JSON 安全）。"""
    m = pattern.search(text)
    if not m:
        return None
    try:
        v = float(m.group(1))
    except ValueError:
        return None
    if math.isnan(v):
        return _DB_FLOOR
    if math.isinf(v):
        return _DB_FLOOR if v < 0 else 0.0
    return v


def completeness_check(frames_dir: Path, expected: int) -> list[str]:
    """帧序完整性：[0, expected) 无缺号、无 0 字节、无越界多余帧，错误带帧号。"""
    if isinstance(expected, bool) or not isinstance(expected, int) or expected <= 0:
        return [f"expected 应为正整数，实际为 {expected!r}"]
    frames_dir = Path(frames_dir)
    if not frames_dir.is_dir():
        return [f"帧目录不存在: {frames_dir}"]
    idx_map = _frame_index(frames_dir)
    errs: list[str] = []
    for i in range(expected):
        if i not in idx_map:
            errs.append(f"缺帧: frame_{i:06d}（编号 {i}，期望 0..{expected - 1}）")
    for i in sorted(set(idx_map) - set(range(expected))):
        errs.append(f"多余帧: {idx_map[i].name}（编号 {i} 超出期望范围 0..{expected - 1}）")
    for i, p in sorted(idx_map.items()):
        try:
            size = p.stat().st_size
        except OSError as exc:
            errs.append(f"帧不可读: {p.name}（编号 {i}）: {exc}")
            continue
        if size == 0:
            errs.append(f"0 字节帧: {p.name}（编号 {i}）")
    return errs


def review_workspace(ws: dict, timeline: dict, video_path: str) -> dict:
    """质检汇总（防御式，绝不抛异常）：{"contact_sheets", "loudness", "errors"}。

    errors 为中文错误列表（嵌字段路径/实测值，供创作 agent 自读自修）：
      - 每 beat 的 t_start/t_end 抽帧 contact sheet 落 ws["review"]/beat_<id>_<t>.png
      - 响度 loudness_report(video_path)
      - 帧完整性 completeness_check(frames, expected=math.ceil(duration*fps))
      - 时长偏差：probe_duration vs timeline.duration，> 1/fps + 0.05 报错
      - 静音轨回归钉：无声轨或音轨时长≈0 报错（Task 2 补欠账）
    """
    errors: list[str] = []
    result: dict = {"contact_sheets": [], "loudness": {}, "errors": errors}
    try:
        _review_inner(ws, timeline, video_path, result, errors)
    except Exception as exc:  # 自查通道崩溃 = 自查失效，一切异常都进 errors
        logger.exception("review_workspace unexpected failure")
        errors.append(f"质检流程异常: {exc}")
    return result


def _review_inner(
    ws: dict,
    timeline: dict,
    video_path: str,
    result: dict,
    errors: list[str],
) -> None:
    if not isinstance(ws, dict):
        errors.append(f"ws 应为对象(dict)，实际为 {type(ws).__name__}")
        return
    frames_dir = ws.get("frames")
    review_dir = ws.get("review")
    if frames_dir is None or review_dir is None:
        errors.append(
            "ws 缺少 frames/review 目录键（应为 studio_workspace.create_workspace 产物），无法质检"
        )
        return
    frames_dir = Path(frames_dir)
    review_dir = Path(review_dir)

    if not isinstance(timeline, dict):
        errors.append(f"timeline 应为对象(dict)，实际为 {type(timeline).__name__}")
        timeline = {}
    fps = timeline.get("fps")
    duration = timeline.get("duration")
    beats = timeline.get("beats")
    fps_ok = _is_num(fps) and fps > 0

    # 1) 每 beat contact sheet
    if not fps_ok:
        errors.append(f"timeline.fps 无效（{fps!r}），跳过抽帧 contact sheet")
    else:
        if not isinstance(beats, list) or not beats:
            errors.append("timeline.beats 缺失或为空，无 beat 可抽帧")
        for i, beat in enumerate(beats if isinstance(beats, list) else []):
            try:
                if not isinstance(beat, dict):
                    errors.append(f"beats[{i}] 应为对象(dict)，跳过抽帧")
                    continue
                bid = beat.get("id")
                ts, te = beat.get("t_start"), beat.get("t_end")
                if not isinstance(bid, str) or not bid:
                    errors.append(f"beats[{i}].id 应为非空字符串，跳过抽帧")
                    continue
                if not (_is_num(ts) and _is_num(te)):
                    errors.append(f"beats[{i}].t_start/t_end 应为数字，跳过抽帧")
                    continue
                safe_id = re.sub(r"[^0-9A-Za-z_-]+", "_", bid)
                out = review_dir / f"beat_{safe_id}_{float(ts):.2f}.png"
                result["contact_sheets"].append(
                    str(contact_sheet(frames_dir, [ts, te], float(fps), out))
                )
            except Exception as exc:
                errors.append(f"beats[{i}] contact sheet 失败: {exc}")

    # 2) 帧完整性（expected = ceil(duration*fps)，与渲染侧 studio_render 的
    #    n_frames 公式一致：ceil 保证覆盖到最后一帧；round 在非整除时长
    #    （如 7.35s×30fps）会比渲染少 1 帧，误报「多余帧」打断自查闭环）
    if fps_ok and _is_num(duration) and duration > 0:
        expected = math.ceil(duration * fps)
        if expected <= 0:
            errors.append(f"期望帧数 ≤ 0（duration={duration}×fps={fps}），帧完整性检查跳过")
        else:
            errors.extend(completeness_check(frames_dir, expected))
    else:
        errors.append(
            f"timeline duration/fps 无效（duration={duration!r}, fps={fps!r}），帧完整性检查跳过"
        )

    # 3) 响度
    try:
        result["loudness"] = loudness_report(str(video_path))
    except Exception as exc:
        errors.append(f"响度检测失败: {exc}")

    # 4) 时长偏差（容差 1/fps + 0.05）
    if fps_ok and _is_num(duration) and duration > 0:
        try:
            actual = probe_duration(str(video_path))
            tol = 1.0 / float(fps) + 0.05
            if abs(actual - float(duration)) > tol:
                errors.append(
                    f"时长偏差超限: timeline.duration={duration}, 实测={actual:.3f}s, "
                    f"容差={tol:.3f}s（1/fps+0.05）"
                )
        except Exception as exc:
            errors.append(f"时长探测失败: {exc}")

    # 5) 静音轨回归钉（Task 2 补欠账）
    _audio_pin_errors(str(video_path), errors)

    # 6) 素材使用检查（P2 验收实验回灌）
    _asset_usage_errors(ws, errors)


def _scene_text(scene_dir: Path) -> str:
    """scene 目录全部文本文件内容拼接（素材引用扫描输入）。"""
    text = ""
    for p in sorted(Path(scene_dir).rglob("*")):
        if p.is_file() and p.suffix.lower() in _SCENE_TEXT_SUFFIXES:
            try:
                text += p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
    return text


def _verify_ref_token(scene_dir: Path, token: str) -> str:
    """单条素材引用验证 → "ok" / "broken" / "unverifiable"。

    token 形如 ../frames/frame_%06d.jpg 或 ../assets/frames/000000.png。
    含 %0Nd 模式的按实际目录内匹配该形状的文件数采样展开（i=0 与 i=count-1）；
    固定文件名（含扩展名）直接验证存在；JS 拼接前缀等静态解析不了的放过
    （prepare_assets 的 hint 前置拦截）。
    """
    dirpart, _, filepart = token.rpartition("/")
    if not filepart:
        return "unverifiable"  # ../frames/ 后无文件名
    target = (scene_dir / dirpart).resolve()
    if not target.is_dir():
        return "broken"
    m = _PATTERN_FIELD_RE.search(filepart)
    if m:
        pat_re = re.compile(
            _PATTERN_FIELD_RE.sub(lambda g: "[0-9]{%d}" % int(g.group(1)), re.escape(filepart))
            + r"\Z"
        )
        count = sum(1 for p in target.iterdir() if p.is_file() and pat_re.match(p.name))
        if count == 0:
            return "broken"
        for i in {0, count - 1}:
            name = _PATTERN_FIELD_RE.sub(lambda g, i=i: f"{i:0{int(g.group(1))}d}", filepart)
            if not (target / name).is_file():
                return "broken"
        return "ok"
    if "." in filepart:  # 固定文件名 → 直接存在性验证
        return "ok" if (target / filepart).is_file() else "broken"
    return "unverifiable"  # JS 拼接等 → 放过


def _asset_usage_errors(ws: dict, errors: list[str]) -> None:
    """素材使用检查（风格中立）：场景必须引用真实素材，frames/assets 任一即合法。

    三种失败模式（文案区分，P2 验收实验发现「真人全程没上屏」的成片）：
      - 抽帧/抠像目录均不存在 → agent 未调 prepare_assets，报「未准备任何素材」
      - scene 文本零引用 ../.cache/frames/ 与 ../assets/ → 素材躺着不用，
        报「未使用任何真实素材」
      - 有引用但全部断链（命名猜测错误/文件缺失）→ 报「素材引用全部断链」
    scene 键缺失或 scene/index.html 未写时跳过（渲染侧前置校验会另行拦截，
    不在此重复报）；至少一条引用验证通过即放行。
    """
    scene_dir = ws.get("scene")
    if scene_dir is None:
        return
    index_html = Path(scene_dir) / "index.html"
    if not index_html.is_file():
        return
    frames_src = Path(ws[".cache"]) / "frames" if ws.get(".cache") else None
    frames_ok = (bool(frames_src) and frames_src.is_dir()) or (
        bool(ws.get("frames")) and Path(ws["frames"]).is_dir()
    )
    assets_ok = bool(ws.get("assets")) and Path(ws["assets"]).is_dir()
    if not frames_ok and not assets_ok:
        errors.append(
            "未准备任何素材（抽帧/抠像目录均不存在）：口播源视频的真人画面"
            "或抠像轨必须入画（先 prepare_assets(frames) 或 prepare_assets(matting)）"
        )
        return
    tokens = _REF_TOKEN_RE.findall(_scene_text(Path(scene_dir)))
    if not tokens:
        errors.append(
            "场景未使用任何真实素材（frames/assets 均未引用）：口播源视频的"
            "真人画面或抠像轨必须入画"
        )
        return
    verdicts = [_verify_ref_token(Path(scene_dir), tok) for tok in tokens]
    if "ok" in verdicts:
        return
    if "broken" in verdicts:
        bad = [t for t, v in zip(tokens, verdicts) if v == "broken"][:3]
        errors.append(
            "素材引用全部断链（可能命名不匹配）："
            + "；".join(bad)
            + " — 请使用 prepare_assets 返回的确切 pattern/example 重新引用"
        )


def _audio_pin_errors(video_path: str, errors: list[str]) -> None:
    """静音轨回归钉：无声轨或音轨时长≈0 报错；探测链全失败也报错。"""
    info = _audio_track_info(video_path)
    if info is None:
        errors.append("音轨探测失败（PyAV + ffmpeg 两条链都不可用）")
        return
    has_audio, audio_dur = info
    if not has_audio:
        errors.append(
            "视频无音轨（静音轨混流缺失？对照 studio_render._encode_video 的 -shortest 混音链路）"
        )
    elif audio_dur < 0.05:
        errors.append(f"音轨时长异常（≈0，实测 {audio_dur:.3f}s，疑似音轨被截断）")


def _audio_track_info(video_path: str) -> tuple[bool, float] | None:
    """探测 (有无音轨, 音轨时长秒)：PyAV 优先，`ffmpeg -i` stderr 兜底；全败返回 None。"""
    try:
        import av

        with av.open(video_path) as container:
            audio = next((s for s in container.streams if s.type == "audio"), None)
            if audio is None:
                return False, 0.0
            dur = 0.0
            if audio.duration is not None and audio.time_base is not None:
                dur = float(audio.duration * audio.time_base)
            if dur <= 0 and container.duration:
                dur = container.duration / 1_000_000.0
            return True, dur
    except Exception as exc:  # 探测链必须走完兜底，吞掉一切 PyAV 异常
        logger.debug("av audio probe failed for %s: %s", video_path, exc)

    text = _ffmpeg_i_stderr(video_path)
    if not text:
        return None
    if "Audio:" not in text:
        return False, 0.0
    m = _DURATION_RE.search(text)
    dur = (int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))) if m else 0.0
    return True, dur
