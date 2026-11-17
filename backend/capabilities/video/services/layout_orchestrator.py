"""G2 布局编排层服务 — LLM 布局规划 + 契约校验 + 确定性编译 + VLM 审查。

设计依据 docs/smart-cut-and-overlay.md 10.18（已批准方案）：

  三重锁（G1 模式平移）：
    1. LLM 只产 LayoutTimeline JSON（纯数据，不产滤镜不产命令）
    2. 白名单/时间轴完整性/引用存在性校验 — 不过带错误清单回喂重试 ≤2
    3. 确定性编译：分段渲染 + concat demuxer，零执行自由度

  降级链：布局规划失败 → fallback 单段静态 pip → 调用方再失败回退原片。

  布局切点落在语流中间（与 smart_cut 的静音切口不同）→ 段间不做
  crossfade/fade，硬切（转场属后续转场层，编译选项 B 是其前置）。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import subprocess
import tempfile
from enum import Enum
from pathlib import Path
from typing import Any, NamedTuple

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

DEFAULT_BG_COLOR = "#1a1a2e"
_PIP_MARGIN = 20  # pip 窗口距边缘像素（对齐 talking_head_overlay 默认）
_MAX_SEGMENTS = 10  # 构图护栏：防 LLM 过度碎片化
_MIN_SEGMENT_SEC = 1.5  # 构图护栏：单段最短时长（防构图抖动）
_MAX_PLAN_ATTEMPTS = 3  # 规划 + 反馈重试 ≤2（对齐 P0 审片闭环）
_TOL = 0.05  # 时间轴浮点容差（铺满/相邻判定）
_HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
_SHOT_RE = re.compile(r"^shot:(\d+)$")


class LayoutPrimitive(str, Enum):
    """布局原语白名单 — LLM 只能三选一。"""

    FULL = "full"  # 人像全屏
    PIP = "pip"  # 人像缩角，主画面 = 配图 / 纯色背景
    BROLL = "broll"  # 配图全屏，人像隐藏


class PipCorner(str, Enum):
    BOTTOM_RIGHT = "bottom_right"
    BOTTOM_LEFT = "bottom_left"
    TOP_RIGHT = "top_right"
    TOP_LEFT = "top_left"


class LayoutSegment(BaseModel):
    """一个布局段 — 对齐 CutCandidate 的可审计设计（reason 必填可选）。"""

    start: float
    end: float
    primitive: LayoutPrimitive
    pip_position: PipCorner | None = None
    pip_scale: float = 0.3
    main_source: str | None = None  # "shot:N" | "color"（pip/broll 必填）
    reason: str = ""


class LayoutTimeline(BaseModel):
    """布局时间轴 — LLM 的唯一产出物。"""

    segments: list[LayoutSegment] = Field(default_factory=list)
    bg_color: str = DEFAULT_BG_COLOR


class RenderStep(NamedTuple):
    """一段的确定性编译产物 — cmd 为抽象命令（cmd[0] 恒 "ffmpeg"，
    执行器负责解析真实二进制路径 → 编译保持纯函数、不依赖环境）。"""

    cmd: list[str]
    out_path: Path


# ── 校验（三重锁第 2 层） ─────────────────────────────────────────


def validate_layout_timeline(
    timeline: LayoutTimeline,
    duration: float,
    broll_count: int = 0,
) -> list[str]:
    """契约校验 — 返回问题清单（空 = 通过）。

    检查项：时间轴完整性（从 0 铺满、无重叠、无留缝、end>start）、
    构图护栏（段数上限、单段最短时长）、原语约束（pip 必填
    main_source、scale 范围、broll 必须配图）、引用存在性（shot:N
    必须命中真实配图）、bg_color 合法性。
    """
    problems: list[str] = []
    segs = timeline.segments

    if not segs:
        return ["布局为空（segments 至少 1 段）"]
    if not _HEX_RE.match(timeline.bg_color or ""):
        problems.append(f"bg_color 非法（须 #rrggbb）：{timeline.bg_color!r}")
    if len(segs) > _MAX_SEGMENTS:
        problems.append(f"段数 {len(segs)} 超上限 {_MAX_SEGMENTS}（构图碎片化，合并相邻同原语段）")

    if abs(segs[0].start) > _TOL:
        problems.append(f"时间轴必须从 0 开始（首段 start={segs[0].start}）")
    if abs(segs[-1].end - duration) > _TOL:
        problems.append(f"时间轴必须铺满至片尾（末段 end={segs[-1].end}，片长 {duration}）")

    for i, seg in enumerate(segs, 1):
        if seg.end <= seg.start:
            problems.append(f"段 {i} end <= start（{seg.start} → {seg.end}）")
        if seg.end - seg.start < _MIN_SEGMENT_SEC:
            problems.append(
                f"段 {i} 过短（{seg.end - seg.start:.1f}s < {_MIN_SEGMENT_SEC}s"
                " 最小时长，构图抖动）"
            )
        if i > 1:
            gap = seg.start - segs[i - 2].end
            if gap > _TOL:
                problems.append(f"段 {i - 1} 与段 {i} 之间留缝（{gap:.2f}s）")
            elif -gap > _TOL:
                problems.append(f"段 {i - 1} 与段 {i} 时间重叠（{-gap:.2f}s）")

        prim = seg.primitive
        if prim == LayoutPrimitive.FULL:
            continue  # full 段的 main_source 无意义，忽略不校验

        src = (seg.main_source or "").strip()
        if not src:
            problems.append(f"{prim.value} 段 {i} 缺 main_source（shot:N 或 color）")
            continue
        m = _SHOT_RE.match(src)
        if m:
            n = int(m.group(1))
            if broll_count <= 0:
                problems.append(f"段 {i} 引用配图 {src} 但没有可用配图")
            elif n >= broll_count:
                problems.append(f"段 {i} main_source 引用不存在的配图 {src}（共 {broll_count} 张）")
        elif src != "color":
            problems.append(f"段 {i} main_source 非法：{src!r}（shot:N 或 color）")
        elif prim == LayoutPrimitive.BROLL:
            problems.append(f"broll 段 {i} 的 main_source 必须是配图引用（shot:N），不能是 color")

        if prim == LayoutPrimitive.PIP and not (0.1 <= seg.pip_scale <= 0.5):
            problems.append(f"pip 段 {i} pip_scale={seg.pip_scale} 越界（0.1~0.5）")

    return problems


# ── 防御式解析（仿 overlay_orchestrator._parse_plan_output） ─────


def _strip_fences(raw: str) -> str:
    """剥掉 LLM 爱加的 ```json 围栏。"""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def parse_layout_output(
    raw: str,
    duration: float,
    broll_count: int = 0,
) -> tuple[LayoutTimeline, list[str]]:
    """LLM 原始输出 → (timeline, problems)。

    防御式：坏段跳过不炸、时间越界 clamp、乱序自动排序、
    pip 缺 position 补默认右下角。返回的 problems 来自最终校验。
    """
    timeline = LayoutTimeline()
    try:
        data = json.loads(_strip_fences(raw))
    except (json.JSONDecodeError, TypeError):
        return timeline, [f"布局输出不是合法 JSON：{str(raw)[:120]!r}"]

    if isinstance(data, dict):
        segs_raw = data.get("segments") or []
        timeline.bg_color = str(data.get("bg_color") or DEFAULT_BG_COLOR)
    elif isinstance(data, list):
        segs_raw = data
    else:
        return timeline, ["布局 JSON 顶层必须是对象或数组"]

    parsed: list[LayoutSegment] = []
    for i, item in enumerate(segs_raw):
        if not isinstance(item, dict):
            continue
        try:
            seg = LayoutSegment(**item)
        except Exception as e:
            logger.debug(f"[layout] 跳过坏段 #{i}: {e}")
            continue
        seg.start = max(0.0, float(seg.start))
        seg.end = min(float(duration), float(seg.end))
        if seg.end <= seg.start:
            continue
        if seg.primitive == LayoutPrimitive.PIP and seg.pip_position is None:
            seg.pip_position = PipCorner.BOTTOM_RIGHT
        parsed.append(seg)

    parsed.sort(key=lambda s: s.start)
    timeline.segments = parsed
    return timeline, validate_layout_timeline(timeline, duration, broll_count)


def fallback_timeline(duration: float, broll_count: int = 0) -> LayoutTimeline:
    """降级布局 — 单段静态 pip（对齐 _execute_pip 默认参数）。"""
    src = "shot:0" if broll_count > 0 else "color"
    return LayoutTimeline(
        segments=[
            LayoutSegment(
                start=0.0,
                end=duration,
                primitive=LayoutPrimitive.PIP,
                pip_position=PipCorner.BOTTOM_RIGHT,
                pip_scale=0.3,
                main_source=src,
                reason="降级：规划失败回退静态画中画",
            ),
        ]
    )


# ── 确定性编译（三重锁第 3 层 — 纯函数，不执行） ─────────────────


def _pip_geometry(
    corner: PipCorner,
    scale: float,
    w: int,
    h: int,
) -> tuple[int, int, int, int]:
    """pip 窗口几何：宽高 + 四角摆放坐标。"""
    pw, ph = int(w * scale), int(h * scale)
    right, bottom = w - pw - _PIP_MARGIN, h - ph - _PIP_MARGIN
    x = _PIP_MARGIN if corner in (PipCorner.BOTTOM_LEFT, PipCorner.TOP_LEFT) else right
    y = _PIP_MARGIN if corner in (PipCorner.TOP_LEFT, PipCorner.TOP_RIGHT) else bottom
    return pw, ph, x, y


def _letterbox_expr(w: int, h: int, out_label: str = "[bg]") -> str:
    """配图 letterbox：等比缩小 + pad 居中（复用 10.15 短边归一思路）。"""
    return (
        f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2{out_label}"
    )


_VCODEC = [
    "-c:v",
    "libx264",
    "-preset",
    "fast",
    "-crf",
    "23",
    "-pix_fmt",
    "yuv420p",
    "-c:a",
    "aac",
    "-b:a",
    "128k",
]


def build_render_plan(
    video_path: str,
    timeline: LayoutTimeline,
    brolls: list[str],
    video_wh: tuple[int, int],
    tmp_dir: Path,
) -> list[RenderStep]:
    """LayoutTimeline → 每段一条 ffmpeg 抽象命令 + concat 列表素材。

    纯函数（不执行、不依赖环境）：cmd[0] 恒为 "ffmpeg" 字面量，
    真实二进制路径由执行器（compile_layout）解析替换。
    """
    w, h = video_wh
    steps: list[RenderStep] = []

    for i, seg in enumerate(timeline.segments):
        dur = seg.end - seg.start
        out = Path(tmp_dir) / f"seg_{i:03d}.mp4"
        ss, t = f"{seg.start:.3f}", f"{dur:.3f}"

        if seg.primitive == LayoutPrimitive.FULL:
            # 全屏段：剪切 + 重编码（必重编码共识：不用 -c copy）
            cmd = [
                "ffmpeg",
                "-y",
                "-ss",
                ss,
                "-i",
                str(video_path),
                "-t",
                t,
                *_VCODEC,
                str(out),
            ]
        elif seg.primitive == LayoutPrimitive.PIP:
            pw, ph, x, y = _pip_geometry(
                seg.pip_position or PipCorner.BOTTOM_RIGHT,
                seg.pip_scale,
                w,
                h,
            )
            src = (seg.main_source or "color").strip()
            m = _SHOT_RE.match(src)
            if m and int(m.group(1)) < len(brolls):
                # 主画面 = 配图（输入 0），人像 = 原视频（输入 1）
                fc = (
                    f"[0:v]scale={pw}:{ph}[pip];"
                    f"[1:v]{_letterbox_expr(w, h)};"
                    f"[bg][pip]overlay={x}:{y}[v]"
                )
                cmd = [
                    "ffmpeg",
                    "-y",
                    "-loop",
                    "1",
                    "-framerate",
                    "30",
                    "-i",
                    str(brolls[int(m.group(1))]),
                    "-ss",
                    ss,
                    "-i",
                    str(video_path),
                    "-t",
                    t,
                    "-filter_complex",
                    fc,
                    "-map",
                    "[v]",
                    "-map",
                    "1:a?",
                    *_VCODEC,
                    "-shortest",
                    str(out),
                ]
            else:
                # 主画面 = 纯色背景（lavfi color 源，输入 0 是原视频）
                bg_hex = (timeline.bg_color or DEFAULT_BG_COLOR).lstrip("#")
                fc = (
                    f"[0:v]scale={pw}:{ph}[pip];"
                    f"color=c=0x{bg_hex}:s={w}x{h}:r=30:d={t}[bg];"
                    f"[bg][pip]overlay={x}:{y}[v]"
                )
                cmd = [
                    "ffmpeg",
                    "-y",
                    "-ss",
                    ss,
                    "-i",
                    str(video_path),
                    "-t",
                    t,
                    "-filter_complex",
                    fc,
                    "-map",
                    "[v]",
                    "-map",
                    "0:a?",
                    *_VCODEC,
                    "-shortest",
                    str(out),
                ]
        else:  # BROLL：配图全屏，无人像窗口
            n = 0
            m = _SHOT_RE.match((seg.main_source or "").strip())
            if m and int(m.group(1)) < len(brolls):
                n = int(m.group(1))
            fc = f"[1:v]{_letterbox_expr(w, h, '[v]')}"
            cmd = [
                "ffmpeg",
                "-y",
                "-loop",
                "1",
                "-framerate",
                "30",
                "-i",
                str(brolls[n]),
                "-ss",
                ss,
                "-i",
                str(video_path),
                "-t",
                t,
                "-filter_complex",
                fc,
                "-map",
                "[v]",
                "-map",
                "1:a?",
                *_VCODEC,
                "-shortest",
                str(out),
            ]
        steps.append(RenderStep(cmd=cmd, out_path=out))

    return steps


async def _run_ffmpeg(cmd: list[str]) -> str:
    """异步执行 ffmpeg（对齐 smart_cut._run_ffmpeg 模式，NotImplementedError 兜底）。"""
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        returncode = process.returncode
    except NotImplementedError:
        result = await subprocess.run(cmd, capture_output=True)  # type: ignore[misc]
        stdout, stderr, returncode = result.stdout, result.stderr, result.returncode

    if returncode != 0:
        tail = "\n".join((stderr.decode(errors="replace") or "").splitlines()[-12:])
        raise RuntimeError(f"ffmpeg 失败（exit {returncode}）：\n{tail}")
    return stdout.decode(errors="replace")


async def compile_layout(
    video_path: str,
    timeline: LayoutTimeline,
    brolls: list[str],
    tmp_dir: Path,
    video_wh: tuple[int, int],
) -> Path:
    """执行渲染计划：逐段渲染 → concat demuxer 拼接。

    段内参数恒定 + 每段独立重编码（参数统一）→ concat -c copy 安全。
    布局切点在语流中 → 不做 fade（区别于 smart_cut 静音切口）。
    """
    from capabilities.video.adapters.html_to_video import resolve_ffmpeg

    steps = build_render_plan(video_path, timeline, brolls, video_wh, Path(tmp_dir))
    if not steps:
        raise ValueError("空布局，无可编译段")

    ff = resolve_ffmpeg()
    if ff is None:
        raise RuntimeError("ffmpeg 不可用，无法编译布局")

    for step in steps:
        await _run_ffmpeg([ff, *step.cmd[1:]])

    # concat 列表（Windows 路径反斜杠会被 demuxer 当转义 → 统一正斜杠）
    list_path = Path(tmp_dir) / "concat.txt"
    list_path.write_text(
        "\n".join(f"file '{str(step.out_path.resolve()).replace(chr(92), '/')}'" for step in steps),
        encoding="utf-8",
    )
    out_path = Path(tmp_dir) / f"{Path(video_path).stem}_layout.mp4"
    await _run_ffmpeg(
        [
            ff,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_path),
            "-c",
            "copy",
            str(out_path),
        ]
    )
    return out_path


# ── LLM 布局规划（三重锁第 1 层 + 反馈重试） ─────────────────────


def _build_layout_prompt(
    duration: float,
    transcript_segments: list[dict[str, Any]] | None,
    brolls: list[str],
) -> str:
    if brolls:
        shot_lines = "\n".join(f"  shot:{i} → {p}" for i, p in enumerate(brolls))
        avail = f"可用配图（main_source 用 shot:N 引用）：\n{shot_lines}"
    else:
        avail = '可用配图：无可用配图（pip 的 main_source 只能填 "color"，且不可使用 broll 原语）'

    if transcript_segments:
        lines = []
        for seg in transcript_segments[:80]:
            s, e, t = seg.get("start", 0), seg.get("end", 0), seg.get("text", "")
            lines.append(f"  {float(s):.1f}-{float(e):.1f} {t}")
        transcript_text = "口播转录（时间轴，秒）：\n" + "\n".join(lines)
    else:
        transcript_text = (
            f"（未提供转录文本。口播总长 {duration:.1f}s，按开场/核心/收束的常规叙事节奏编排）"
        )

    return f"""你是口播视频的镜头布局编排师。根据口播内容规划画面构图时间轴：
人物什么时候全屏、什么时候缩到角落把主画面让给配图、什么时候全屏配图。

口播总长：{duration:.1f} 秒。
{transcript_text}
{avail}

布局原语（primitive，三选一）：
  full   人像全屏（开场问候、观点收束、情绪段）
  pip    人像缩到角落，主画面给配图或纯色背景（讲核心内容/数据/步骤时）
  broll  配图全屏，人像隐藏（强叙事、案例展示段；仅在有可用配图时）

硬性约束：
  - segments 必须从 0.0 铺满到 {duration:.1f}，相邻段无缝无重叠
  - 段数 ≤ {_MAX_SEGMENTS}，单段 ≥ {_MIN_SEGMENT_SEC}s（防构图抖动）
  - pip 必填 main_source（"shot:N" 或 "color"）；pip_scale 0.1~0.5；pip_position ∈ bottom_right/bottom_left/top_right/top_left
  - broll 的 main_source 必须是 "shot:N"
  - bg_color 为 #rrggbb 格式（pip 用 color 背景时的画布底色）

输出格式（只输出 JSON，不要任何解释文字、不要代码块围栏）：
{{
  "segments": [
    {{"start": 0, "end": 4, "primitive": "full", "reason": "开场人像全屏"}},
    {{"start": 4, "end": 16, "primitive": "pip", "pip_position": "bottom_right", "pip_scale": 0.3, "main_source": "shot:0", "reason": "讲核心内容，配图作主画面"}},
    {{"start": 16, "end": {duration:.1f}, "primitive": "full", "reason": "收束回全屏"}}
  ],
  "bg_color": "{DEFAULT_BG_COLOR}"
}}"""


async def generate_layout_plan(
    llm: Any,
    duration: float,
    transcript_segments: list[dict[str, Any]] | None = None,
    brolls: list[str] | None = None,
) -> tuple[LayoutTimeline | None, str]:
    """LLM 布局规划 + 校验反馈重试（≤ _MAX_PLAN_ATTEMPTS 次）。

    返回 (timeline, 最后一次原始输出)；全部失败返回 (None, raw) —
    调用方走 fallback_timeline 降级，绝不阻断出片。
    """
    from app.adapters.llm_base import llm_prompt_to_text

    brolls = brolls or []
    if llm is None:
        return None, ""

    prompt = _build_layout_prompt(duration, transcript_segments, brolls)
    raw = ""
    for _attempt in range(_MAX_PLAN_ATTEMPTS):
        try:
            raw = await llm_prompt_to_text(llm, prompt)
        except Exception as e:
            logger.warning(f"[layout] LLM 调用失败：{e}")
            return None, raw
        timeline, problems = parse_layout_output(raw, duration, len(brolls))
        if not problems:
            return timeline, raw
        logger.info(f"[layout] 布局校验未过（attempt {_attempt + 1}）：{problems}")
        prompt = (
            "你上一次输出的布局未通过校验：\n- "
            + "\n- ".join(problems)
            + "\n请修正以上问题，重新输出完整布局 JSON（只输出 JSON）。原要求：\n"
            + prompt
        )
    return None, raw


# ── VLM 布局审查（P0 审片平移） ──────────────────────────────────

_LAYOUT_REVIEW_PROMPT = """你是口播视频布局编排的视觉审查员。下面是成片在各布局段中点抽出的帧。

布局时间轴（帧按此顺序给出）：
{timeline_desc}

逐帧检查是否存在以下真实可见的问题：
1. 黑帧或纯色空白帧（渲染失败）
2. pip 人像窗口遮挡配图中的关键内容（文字/主体）
3. 构图与该段声明的原语不符（full=人物全屏；pip=主画面+角落人像小窗；broll=配图全屏无人像）
4. 画面明显变形、拉伸或letterbox裁切错误

输出 JSON（只输出 JSON，不要解释）：
{{"verdict": "pass" 或 "fail", "problems": ["第N帧：问题描述", ...]}}"""


async def _extract_frames(
    video_path: str | Path,
    timestamps: list[float],
    out_dir: str | Path,
) -> list[Path]:
    """定点抽帧 — 复用 overlay_review.extract_review_frames（防御式）。"""
    from capabilities.video.services.overlay_review import extract_review_frames

    return await extract_review_frames(video_path, timestamps, out_dir, "layout")


def _encode_frame_b64(path: str | Path) -> str | None:
    """帧 → JPEG data URI — 复用 overlay_review._frame_to_data_uri（含 110KB 压缩）。"""
    from capabilities.video.services.overlay_review import _frame_to_data_uri

    return _frame_to_data_uri(path)


def _flatten_vlm_content(resp: dict[str, Any]) -> str:
    """DashScope 多图输入 content 可能是 list[{"text":...}] — 拉平取文本。"""
    content = resp.get("content") if isinstance(resp, dict) else resp
    if isinstance(content, list):
        return "".join(str(p.get("text", "")) for p in content if isinstance(p, dict))
    return str(content or "")


async def review_layout(
    final_path: str | Path,
    timeline: LayoutTimeline,
    adapter: Any,
) -> str | None:
    """VLM 看图审查成片布局 — 每段中点一帧。

    降级契约（对齐 P0）：VLM 不可用/抽帧失败/坏 JSON → 返回 None，
    审查绝不阻断出片。发现问题返回反馈文本（供事件流上报/重规划）。
    """
    if adapter is None or not timeline.segments:
        return None
    try:
        timestamps: list[float] = []
        for seg in timeline.segments:
            t = (seg.start + seg.end) / 2
            if t not in timestamps:
                timestamps.append(t)

        with tempfile.TemporaryDirectory(prefix="layout_review_") as td:
            frames = await _extract_frames(final_path, timestamps, td)
            if not frames:
                logger.warning("[layout] 抽帧失败，跳过布局审查")
                return None

            uris = [u for u in (_encode_frame_b64(p) for p in frames) if u]
            if not uris:
                return None

            # 请求体预算分块（复用 overlay_review 实测阈值，防中间层掐断）
            from capabilities.video.services.overlay_review import (
                _ChunkItem,
                _pack_chunks,
            )

            items = [
                _ChunkItem(meta=seg, uri=uri, tag="layout")
                for seg, uri in zip(timeline.segments, uris)
            ]
            problems: list[str] = []
            for chunk in _pack_chunks(items):
                timeline_desc = "\n".join(
                    f"帧{i + 1}（{seg.primitive.value} {seg.start:.1f}-{seg.end:.1f}s）：{seg.reason or '无说明'}"
                    for i, seg in enumerate([it.meta for it in chunk if it.meta is not None])
                )
                prompt = _LAYOUT_REVIEW_PROMPT.format(timeline_desc=timeline_desc)
                content: list[dict[str, Any]] = [{"text": prompt}]
                content.extend({"image": it.uri} for it in chunk)
                try:
                    resp = await adapter.chat([{"role": "user", "content": content}])
                except Exception as e:
                    logger.warning(f"[layout] VLM 调用失败，跳过该块：{e}")
                    continue
                text = _flatten_vlm_content(resp)
                try:
                    data = json.loads(_strip_fences(text))
                except (json.JSONDecodeError, TypeError):
                    logger.warning(f"[layout] 审查输出非 JSON，降级通过：{text[:80]!r}")
                    continue
                if isinstance(data, dict) and data.get("verdict") == "fail":
                    problems.extend(str(p) for p in (data.get("problems") or []) if p)
            if problems:
                return "；".join(problems)
            return None
    except Exception as e:
        logger.warning(f"[layout] 布局审查异常，降级通过：{e}")
        return None


def plan_key(timeline: LayoutTimeline) -> str:
    """产物隔离 key — 不同布局时间轴不互相覆盖（对齐 talking_head_overlay）。"""
    return hashlib.md5(
        json.dumps(timeline.model_dump(), ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:8]
