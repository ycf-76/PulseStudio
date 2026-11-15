"""叠加增强 Skill — 口播视频 + 字幕/图标/数据卡片。

设计依据：
  - ossclip 的 analyze/produce 分离 + Zod-typed scene library
  - edit-greek-reel 的 3 阶段流水线：规划 → 渲染 → 合成
  - HyperFrames 的 source-time → cut-time 映射

闸门机制（3 级）：
  闸门 1：叠加规划确认（LLM 规划 + 模板选择）
  闸门 2：动画预览确认（Playwright 渲染预览）
  闸门 3：合成预览确认（ffmpeg overlay 合成）

串联模式（smart_cut → talking_head_overlay）：
  闸门合并为 3 级（删减规划+叠加规划 → 动画预览 → 最终合成）
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import shutil
import tempfile
from contextlib import asynccontextmanager
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from app.core.skill_base import Skill
from app.core.skill_registry import register
from capabilities.video.adapters.html_to_video import resolve_ffmpeg
from capabilities.video.skills import resolve_owner, scoped_dir
from capabilities.video.services.overlay_orchestrator import (
    OverlayPlan,
    apply_revision,
    generate_overlay_plan,
    parse_revision_intent,
)

logger = logging.getLogger(__name__)


def _simplify_cn(text: str) -> str:
    """繁→简归一化 — whisper 词流偶发繁体（重建字幕直接展示给观众，
    必须简体；同时让词级对齐的字符匹配与 LLM 简体文案一致）。
    缺 zhconv 时原样返回（零件可缺，对齐 _normalize_simplified）。"""
    try:
        from zhconv import convert
    except ImportError:
        return text
    return convert(text, "zh-cn")

def _deck_top_clear_px(parsed: Any) -> int:
    """N2：deck 主画面顶部右侧让位宽 — 顶部小窗防压标题。

    小窗宽 = 主画布 1080 × pip_scale，双边距 40 → 上取整到 40px
    网格；位置非 top_* → 0（底部小窗压不到标题，不让位）。
    """
    import math

    pos = getattr(parsed, "pip_position", None)
    scale = float(getattr(parsed, "pip_scale", 0) or 0)
    if pos is None or not str(pos.value).startswith("top") or scale <= 0:
        return 0
    return int(math.ceil((1080 * scale + 40) / 40) * 40)


_OUTPUT_DIR = Path(__file__).resolve().parent.parent.parent.parent / "outputs"

# P1 生成预算（G1 放开后保留的成本护栏）：按"不同 gen_ 模板数"计，
# 同名模板已入库则零边际成本不占预算。8 个定制模板远超真实单片需要 —
# 这是防 LLM 失控刷模板的保险丝，不是表达上限。
_GEN_BUDGET = 8

# 10.19 对话式修改层：单会话修改轮数护栏（用户裁决 ≤5）—
# 防无限改，超限提示换全局重规划。轮数由调用方维护回传。
_MAX_REVISION_ROUNDS = 5


def _transcribe_cpu_subprocess(
    video_path: str, timeout: float = 600.0,
) -> list[dict[str, Any]]:
    """whisper 转录跑在独立子进程（auto_deck 无 transcript 时的兜底通道）。

    10.20 对照实验实锤：ct2 构造 WhisperModel（即使 device=cpu）即把
    系统旧 cuDNN DLL 拉进进程，同进程后续 torch CUDA conv native
    fastfail（0xC0000409）不可捕获 — matting 的 RVM 同进程必炸。
    一键产品化不能依赖调用方分进程编排，故转录自带子进程隔离，
    GPU 只留给 RVM。转录失败/引擎缺失 → []（不阻断，走 deck_fallback）。
    """
    import os
    import subprocess
    import sys

    backend_root = Path(__file__).resolve().parents[3]
    code = (
        "import json,sys;sys.path.insert(0, sys.argv[2]);"
        "from capabilities.video.adapters.whisper_transcribe import transcribe;"
        "segs = transcribe(sys.argv[1], 'small', 'zh');"
        "print(json.dumps([{'start': s.start, 'end': s.end, 'text': s.text,"
        " 'words': [{'text': w.text, 'start': w.start, 'end': w.end}"
        " for w in (s.words or [])]} for s in segs], ensure_ascii=False))"
    )
    env = {**os.environ, "WHISPER_DEVICE": "cpu"}
    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        proc = subprocess.run(
            [sys.executable, "-c", code, video_path, str(backend_root)],
            capture_output=True, timeout=timeout, env=env,
            creationflags=creationflags,
        )
    except subprocess.TimeoutExpired:
        logger.warning("auto_deck whisper subprocess timed out (%ss)", timeout)
        return []
    if proc.returncode != 0:
        logger.warning(
            "auto_deck whisper subprocess failed: %s",
            proc.stderr.decode(errors="replace")[-300:],
        )
        return []
    try:
        # 取最后一行非空输出（引擎日志走 stderr，stdout 理论上只有 JSON）
        line = proc.stdout.decode("utf-8", errors="replace").strip()
        rows = json.loads(line.splitlines()[-1]) if line else []
    except (json.JSONDecodeError, IndexError):
        logger.warning("auto_deck whisper subprocess output unparsable")
        return []
    return rows if isinstance(rows, list) else []



# 10.25-F xfade 转场样式表（确定性轮换）与转场时长
_XFADE_STYLES = [
    "fade", "slideleft", "slideup",
    "wipeleft", "circleopen", "smoothleft",
]
_XFADE_TD = 0.5


# M3-P2-7 motion tokens 单点定义（方案 P2-7）：所有动效时长同源引用。
# pop 三档步长 0.06（M2 已验收视觉冻结）不硬套 token，见 _build_decor_cmd。
_MOTION = {"fast": 0.2, "normal": 0.4, "stagger": 0.13}
_DECOR_FADE = _MOTION["normal"]

# M3-P1-5 zoom 量化参数（方案：1.08-1.10x / 0.35-0.45s / ≤5 次）
_ZOOM_AMP = 0.08      # 峰值增量 → 峰值 scale 1.08
_ZOOM_PEAK_AT = 0.3   # 峰值时刻（窗内进度比）
_ZOOM_MAX = 0.45      # 单窗时长上限
_ZOOM_NGRAM = 4       # relaxed 滑窗长度（n<4 短语歧义高不降窗）

# M3-P2-6 安全区：80px @1080 宽折算，下限 24
_SAFE_MARGIN_REF = 80


def _safe_margin(width: int) -> int:
    return max(24, round(_SAFE_MARGIN_REF * width / 1080))

# M2-P1-4 SFX 音效库（免版权 — 本仓库 ffmpeg 合成，零外部素材）；
# 音量 0.18 落在方案量化区间 0.15-0.2（不抢人声）
_SFX_LIBRARY = ["pop", "whoosh", "ding", "switch", "riser", "impact"]
_SFX_VOLUME = 0.18
_SFX_DIR = Path(__file__).resolve().parent.parent / "assets" / "sfx"

# M2-P0-2 触发式卡窗：停留上限与可读下限
_TRIGGER_DWELL = 2.5
_TRIGGER_PUNCT = set("，。！？、；：“”‘’（）【】《》…—,.!?;: \t\n\"'")

# 10.31 M1-P0-1：品牌强调色 — 与 deck_orchestrator._HIGHLIGHT 同源，
# hex 值必须一致（karaoke 当前词 / decor keyword 同一强调色）
_KARAOKE_ACCENT = "#39E508"
_KARAOKE_BAND_H = 260

_KARAOKE_PAGE_HTML = """<!doctype html><html><head><meta charset="utf-8"><style>
* { margin:0; padding:0; box-sizing:border-box; }
body { background:transparent; width:%(w)dpx; height:%(h)dpx;
  font-family:"Microsoft YaHei","PingFang SC",sans-serif; }
#band { width:100%%; height:100%%; display:flex; align-items:center;
  justify-content:center; padding:0 28px; }
#line { color:#fff; font-weight:900; text-align:center; line-height:1.4; }
#line span { text-shadow:3px 0 0 #000,-3px 0 0 #000,0 3px 0 #000,
  0 -3px 0 #000,2px 2px 0 #000,-2px 2px 0 #000,2px -2px 0 #000,
  -2px -2px 0 #000,0 6px 18px rgba(0,0,0,.55); }
#line span.on { color:%(accent)s; display:inline-block;
  transform:scale(1.08); }
</style></head><body>
<div id="band"><div id="line"></div></div>
<script>
const DATA = %(data)s;
function setFrame(si, wi) {
  const seg = DATA[si];
  const el = document.getElementById("line");
  el.style.fontSize = seg.px + "px";
  el.innerHTML = "";
  seg.words.forEach(function(w, i) {
    const s = document.createElement("span");
    s.textContent = w.text;
    if (i === wi) s.className = "on";
    el.appendChild(s);
  });
}
</script></body></html>"""


def _fit_font_px(text: str, width: int, cap: int = 46, floor: int = 24) -> int:
    """M3-P2-6 fitText：行宽预算反解字号（纯函数，两行制）。

    全角当量 1.0、半角 0.6；预算 = width - 2×28 内边距；最多两行 →
    px = min(cap, 预算×2/当量)，下限 floor 保可读。替代 M1 三档
    （_karaoke_font_px）— 连续自适应，长句不再靠固定档近似。
    """
    budget = max(200.0, float(width) - 56.0)
    units = sum(1.0 if ord(c) > 0x2E80 else 0.6 for c in str(text))
    if units <= 0:
        return cap
    return max(floor, min(cap, int(budget * 2.0 / units)))


def _karaoke_frame_plan(
    rows: list[dict[str, Any]],
    duration: float,
) -> list[dict[str, Any]]:
    """10.31 M1-P0-1：词级 karaoke 帧计划（纯函数）。

    每词一帧：帧显示整句文本 + 当前词高亮；帧窗 = [词 start,
    下一词 start)；句末词帧延伸到下一句首词 start（字幕常驻到
    换句）；全片末帧延伸到 duration。词戳缺失的句跳过；全缺返回
    [] — 调用方整体跳过字幕层（降级无字幕，绝不阻断出片）。
    """
    frames: list[dict[str, Any]] = []
    for si, r in enumerate(rows):
        words = r.get("words") or []
        if not words:
            continue
        nxt_first = None
        for r2 in rows[si + 1:]:
            if r2.get("words"):
                nxt_first = float(r2["words"][0]["start"])
                break
        for wi, w in enumerate(words):
            st = max(0.0, float(w.get("start", 0)))
            if wi + 1 < len(words):
                en = float(words[wi + 1]["start"])
            elif nxt_first is not None:
                en = nxt_first
            else:
                en = float(duration)
            st = min(st, duration)
            en = min(en, duration)
            if en <= st:
                continue
            frames.append({"si": si, "wi": wi, "start": st, "end": en})
    return frames


def _karaoke_display_data(
    rows: list[dict[str, Any]], width: int = 720,
) -> list[dict[str, Any]]:
    """karaoke 展示数据 — 词文本繁→简。

    live13 实证 whisper 词流偶发繁体（時候/沒有），字幕直出观众
    必须简体，对齐 _simplify_cn 契约（缺 zhconv 原样返回）。
    """
    return [
        {
            "px": _fit_font_px(str(r.get("text") or ""), width),
            "words": [
                {"text": _simplify_cn(str(w.get("text", "")))}
                for w in (r.get("words") or [])
            ],
        }
        for r in rows
    ]


def _render_karaoke_frames(
    frames: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    out_dir: Path,
    width: int = 720,
) -> list[Path]:
    """10.31 M1-P0-1：karaoke 帧渲染 — 单页 evaluate 逐帧切换 + 透明截图。

    成本：每词 1 张截图（约 50-100ms/帧，30s 口播 ≈ 150-250 帧，
    live13 实测；超限降级逐句 karaoke — 方案文档 P0-1 预案）。帧间
    只变 DOM 不重载页面。frames 空 → 返回 []（不渲染）。
    """
    if not frames:
        return []
    from playwright.sync_api import sync_playwright

    out_dir.mkdir(parents=True, exist_ok=True)
    data = _karaoke_display_data(rows, width)
    html = _KARAOKE_PAGE_HTML % {
        "w": width, "h": _KARAOKE_BAND_H,
        "accent": _KARAOKE_ACCENT,
        "data": json.dumps(data, ensure_ascii=False).replace("</", "<\\/"),
    }
    paths: list[Path] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(
            viewport={"width": width, "height": _KARAOKE_BAND_H},
        )
        try:
            page.set_content(html, wait_until="networkidle")
            for i, f in enumerate(frames):
                page.evaluate(
                    "([s, w]) => setFrame(s, w)", [f["si"], f["wi"]],
                )
                shot = out_dir / f"kao_{i:04d}.png"
                page.screenshot(path=str(shot), omit_background=True)
                paths.append(shot)
        finally:
            browser.close()
    return paths


def _write_ffconcat(
    frames: list[dict[str, Any]],
    pngs: list[Path],
    out_dir: Path,
) -> Path:
    """10.31 M1-P0-1：帧计划 → ffconcat 清单（不均匀帧时间轴）。

    末帧重复一次无 duration 行 — concat demuxer 对最后一条目的
    duration 常被吞（官方 FAQ），重复条目兜底保证末帧可见到片尾。
    """
    lines = ["ffconcat version 1.0"]
    for f, p in zip(frames, pngs):
        lines.append(f"file '{p.name}'")
        lines.append(f"duration {f['end'] - f['start']:.3f}")
    if pngs:
        lines.append(f"file '{pngs[-1].name}'")
    lst = out_dir / "karaoke_concat.txt"
    lst.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return lst


def _find_trigger_span(
    rows: list[dict[str, Any]], trigger: str,
) -> tuple[float, float] | None:
    """M3-P1-5：词流搜文本首次出现 → (首词 start, 末词 end)。

    全部词文本（简体化、去标点空白）拼成干草堆并记录 每字符→词序号
    映射；命中 → 首字符词 start 与尾字符词 end（跨词拼接也能命中，
    如"老板"+"推单"）。词戳缺失/搜不到/空文本 → None（绝不阻断）。
    """
    t = _simplify_cn(str(trigger or "")).strip()
    if not t:
        return None
    words = [w for r in rows for w in (r.get("words") or [])]
    chars: list[str] = []
    idx_of: list[int] = []
    for wi, w in enumerate(words):
        clean = "".join(
            c for c in _simplify_cn(str(w.get("text", "")))
            if c not in _TRIGGER_PUNCT
        )
        for c in clean:
            chars.append(c)
            idx_of.append(wi)
    pos = "".join(chars).find(t)
    if pos < 0:
        return None
    try:
        return (
            float(words[idx_of[pos]].get("start", 0) or 0),
            float(words[idx_of[pos + len(t) - 1]].get("end", 0) or 0),
        )
    except (IndexError, TypeError, ValueError):
        return None


def _find_trigger_start(
    rows: list[dict[str, Any]], trigger: str,
) -> float | None:
    """M2-P0-2：词流搜触发词首次出现（span 薄壳，契约不变）。"""
    span = _find_trigger_span(rows, trigger)
    return None if span is None else span[0]


def _zoom_curve(p: float) -> float:
    """M3-P1-5：zoom 增量曲线（纯函数）— p 为窗内进度 [0,1]。

    p ≤ 0.3：amp·sqrt(p/0.3)（sqrt ease-in 推至峰值）；p > 0.3：
    amp·(1-(p-0.3)/0.7)²（quadratic 回落）；边界归零（窗首尾无跳变）。
    """
    if p <= 0.0 or p >= 1.0:
        return 0.0
    if p <= _ZOOM_PEAK_AT:
        return _ZOOM_AMP * (p / _ZOOM_PEAK_AT) ** 0.5
    return _ZOOM_AMP * (
        1.0 - (p - _ZOOM_PEAK_AT) / (1.0 - _ZOOM_PEAK_AT)
    ) ** 2


def _zoompan_expr(
    windows: list[tuple[float, float]],
    fps: int,
    width: int,
    height: int,
) -> str | None:
    """M3-P1-5：zoom 窗列表 → zoompan 滤镜参数串（纯函数）。

    z = 1 + amp·Σ between(in,S,E)·curve(帧内进度)（窗不重叠 → 每帧至多
    一项，与 _zoom_curve 同一数学）；S/E = round(t·fps) 帧号（zoompan
    无 t 变量，用 in）；x/y 全程居中跟随。fps 必须显式传输入帧率 —
    zoompan 缺省 25 会重定时（帧复制/丢帧 → 音画漂移）。d=1 视频输入
    逐帧求值、s=输出尺寸。空窗/全退化窗 → None（不加 zoompan，降级）。
    """
    terms: list[str] = []
    for ts, te in windows:
        s_ = int(round(float(ts) * fps))
        e_ = int(round(float(te) * fps))
        if e_ <= s_:
            continue
        d = e_ - s_
        inv = 1.0 / d
        inv_peak = 1.0 / (_ZOOM_PEAK_AT * d)
        inv_fall = 1.0 / ((1.0 - _ZOOM_PEAK_AT) * d)
        terms.append(
            f"between(in,{s_},{e_})"
            f"*if(lte((in-{s_})*{inv:.10g},{_ZOOM_PEAK_AT}),"
            f"sqrt((in-{s_})*{inv_peak:.10g}),"
            f"pow(1-((in-{s_})*{inv:.10g}-{_ZOOM_PEAK_AT})"
            f"*{inv_fall:.10g},2))"
        )
    if not terms:
        return None
    z = f"1+{_ZOOM_AMP:.10g}*(" + "+".join(terms) + ")"
    return (
        f"zoompan=z='{z}'"
        ":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
        f":d=1:s={width}x{height}:fps={int(round(fps))}"
    )


def _relaxed_span(
    rows: list[dict[str, Any]], text: str,
) -> tuple[float, float] | None:
    """M3-P1-5（live15 实证）：提炼语措辞漂移的两级容错定位。

    bullets 是 LLM 提炼语（换尾/换词），整句 substring 命中率低 →
    ① 整句 _find_trigger_span；② 去标点后 4 字滑窗（步长 1，首个
    命中）。n<4 短语歧义高（泛命中错位）不降窗；插词场景滑窗无法
    覆盖 → 该候选放弃（绝不阻断）。
    """
    if not str(text or "").strip():
        return None
    hit = _find_trigger_span(rows, text)
    if hit is not None:
        return hit
    flat = "".join(
        c for c in _simplify_cn(str(text)) if c not in _TRIGGER_PUNCT
    )
    for i in range(0, len(flat) - _ZOOM_NGRAM + 1):
        hit = _find_trigger_span(rows, flat[i:i + _ZOOM_NGRAM])
        if hit is not None:
            return hit
    return None


def _zoom_windows(
    slides: list[dict[str, Any]],
    transcript_rows: list[dict[str, Any]],
    duration: float,
    max_zooms: int = 5,
) -> list[tuple[float, float]]:
    """M3-P1-5：emphasis 契约 → zoom 强调窗（纯函数，≤5 处）。

    number 优先 keyword 次之（edit-greek-reel：数字/转折触发）；词流
    _find_trigger_span 定位；同起点去重；窗长 = max(词跨度+0.15,
    _MOTION["normal"]) clamp ≤ _ZOOM_MAX 与 duration；窗间隔 <0.5s 保
    首个（杜绝连环推近）；按时间排序。全部搜不到 → []（不加 zoompan，
    绝不阻断）。
    """
    if not transcript_rows or not slides:
        return []
    picked: list[tuple[float, float]] = []
    starts_seen: set[float] = set()
    for em in ("number", "keyword"):
        for sl in slides:
            if len(picked) >= max_zooms:
                break
            for b in (sl.get("bullets") or []):
                if len(picked) >= max_zooms:
                    break
                if not isinstance(b, dict) or b.get("emphasis") != em:
                    continue
                span = _relaxed_span(
                    transcript_rows, str(b.get("text") or ""),
                )
                if span is None or span[0] in starts_seen:
                    continue
                starts_seen.add(span[0])
                picked.append(span)
        if len(picked) >= max_zooms:
            break
    wins: list[tuple[float, float]] = []
    for ts, te in sorted(picked):
        end = min(
            ts + max(te - ts + 0.15, _MOTION["normal"]),
            ts + _ZOOM_MAX,
            float(duration),
        )
        if end - ts >= 0.25:
            wins.append((ts, end))
    out: list[tuple[float, float]] = []
    for ts, te in wins:
        if out and ts - out[-1][1] < 0.5:
            continue
        out.append((ts, te))
    return out[:max_zooms]


async def _probe_video_fps(video_path: str) -> float:
    """M3-P1-5：探测视频帧率（PyAV 优先）— zoompan fps 必须显式。

    zoompan 缺省 fps=25 会重定时（帧复制/丢帧 → 音画漂移）；探测失败
    回退 30（decor 链输出 -r 30 主流竖版帧率，绝不阻断）。
    """
    try:
        import av

        def _p() -> float:
            with av.open(video_path) as container:
                for stream in container.streams:
                    if stream.type == "video":
                        r = float(stream.average_rate or 0.0)
                        return r if r > 0 else 30.0
                return 30.0

        return await asyncio.to_thread(_p)
    except Exception:
        return 30.0


def _trigger_bounds(
    slides: list[dict[str, Any]],
    transcript_rows: list[dict[str, Any]],
    page_bounds: list[tuple[float, float]],
    duration: float | None = None,
) -> list[tuple[float, float]]:
    """M2-P0-2：内容触发式卡窗（纯函数）。

    每页在词流搜 trigger.text 首次出现 → 卡从说到那一刻弹入
    （live14 实证：LLM 触发词命中点常落在比例切分的页区间外 ~0.5s，
    页区间限制会 5/5 全回退 — 方案设计原意是"说到才出现"，页区间
    只做回退兜底）。停留 min(页时长, 2.5s)；end 封顶下一个触发
    时刻与 duration（防同位叠卡）；停留 <0.8s / 无 trigger 字段
    （旧 slides.json）/词戳缺失/搜不到 → 回退句边界页区间（绝不
    阻断，M1 行为兜底）。
    """
    n_pages = len(page_bounds)
    hits: list[float | None] = [None] * n_pages
    for i in range(n_pages):
        tg = ""
        if i < len(slides):
            tv = slides[i].get("trigger")
            if isinstance(tv, dict):
                tg = str(tv.get("text") or "").strip()
            elif isinstance(tv, str):
                tg = tv.strip()
        if tg and transcript_rows:
            hits[i] = _find_trigger_start(transcript_rows, tg)
    out: list[tuple[float, float]] = []
    seen: set[float] = set()
    for i, (b0, b1) in enumerate(page_bounds):
        ts = hits[i]
        if ts is None or ts in seen:
            out.append((b0, b1))
            continue
        end = ts + min(b1 - b0, _TRIGGER_DWELL)
        # 封顶于所有其它更晚的触发时刻（live14 实证：卡4/卡5 触发
        # 顺序与页序颠倒，只封顶"列表靠后"会留 0.12s 叠窗）
        nxt = min(
            (h for j, h in enumerate(hits)
             if j != i and h is not None and h > ts),
            default=None,
        )
        if nxt is not None:
            end = min(end, nxt)
        if duration is not None:
            end = min(end, duration)
        if end - ts < 0.8:
            out.append((b0, b1))
            continue
        seen.add(ts)
        out.append((ts, end))
    return out


def _plan_sfx(
    bounds: list[tuple[float, float]], duration: float,
) -> list[dict[str, Any]]:
    """M2-P1-4：卡入场时刻 → SFX 事件（纯函数）。

    库内 6 音效确定性轮换、全片不重复、≤6 处；t<0.15（片头首卡与
    人声起点相撞）或 t≥duration-0.2（贴片尾）跳过。
    """
    evs: list[dict[str, Any]] = []
    for b0, _b1 in bounds:
        if len(evs) >= len(_SFX_LIBRARY):
            break
        t = float(b0)
        if t < 0.15 or t >= duration - 0.2:
            continue
        evs.append({"name": _SFX_LIBRARY[len(evs)], "t": t})
    return evs


def _build_sfx_mix(
    events: list[dict[str, Any]],
    sfx_dir: Path,
    input_base: int = 1,
    main_label: str = "0:a",
) -> tuple[list[Path], list[str], str | None, dict[str, Any]]:
    """M2-P1-4：SFX 混音命令片段（纯函数）。

    每事件：aformat 归一 48k 立体声 → adelay=ms（全通道）→
    volume=0.18 → 与主音频 amix(inputs=N+1, duration=first,
    normalize=0) — normalize=0 保主音量不被 N 路均分衰减。
    文件缺失 → 该事件跳过；全部缺失/无事件 → ([], [], None)
    （调用方音频直通 0:a?，降级不阻断）。
    Returns (sfx_input_paths, filter_parts, audio_label, meta)。
    """
    paths: list[Path] = []
    parts: list[str] = []
    labels: list[str] = []
    names: list[str] = []
    for ev in events:
        name = str(ev.get("name") or "")
        p = Path(sfx_dir) / f"{name}.wav"
        if not name or not p.is_file():
            continue
        idx = input_base + len(paths)
        paths.append(p)
        names.append(name)
        ms = max(0, int(round(float(ev.get("t", 0)) * 1000)))
        lbl = f"fx{idx}"
        parts.append(
            f"[{idx}:a]aformat=sample_rates=48000:channel_layouts=stereo,"
            f"adelay={ms}:all=1,volume={_SFX_VOLUME}[{lbl}]"
        )
        labels.append(lbl)
    if not paths:
        return [], [], None, {"planned": len(events), "mixed": 0}
    parts.append(
        f"[{main_label}]aformat=sample_rates=48000:"
        f"channel_layouts=stereo[mainx]"
    )
    parts.append(
        "[mainx]" + "".join(f"[{l}]" for l in labels)
        + f"amix=inputs={len(labels) + 1}:duration=first:"
        f"normalize=0[aout]"
    )
    return paths, parts, "[aout]", {
        "planned": len(events), "mixed": len(paths), "names": names,
    }


def _build_decor_cmd(
    video_path: Path,
    cards: list[Path],
    bounds: list[tuple[float, float]],
    duration: float,
    width: int,
    height: int,
    scale: float = 1.0,
    effect: str = "fade",
    fade: float = _DECOR_FADE,
    subtitle_ffconcat: Path | None = None,
    out_path: Path | None = None,
    sfx_events: list[dict[str, Any]] | None = None,
    sfx_dir: Path | None = None,
    zoom_windows: list[tuple[float, float]] | None = None,
    fps: int = 30,
) -> tuple[list[str], dict[str, Any]]:
    """10.30：原片 + 素材卡叠加命令构造（纯函数，便于测试）。

    width = 原片宽（卡目标宽 = width*scale，scale 相对主画面）。
    每卡：-loop 1（静态图转连续流，否则单帧 t=0 → fade alpha 恒 0，
    live12 实证卡不可见）→ scale 到目标宽 → rgba；
    10.31 M1：卡 y 从 24 移到安全区（max(48, 高度 5%)）。
    10.31 M1-P0-1：subtitle_ffconcat（karaoke 字幕清单）→ concat
    demuxer 输入 → fps=30 归一 → overlay 70% 高度带，恒在链条最外层
    （字幕不被素材卡遮盖）。
    M2-P0-2：effect="pop" — 入场三档快切（0.5x/1.08x/1.0x 各 0.06s，
    enable 窗切换，0.18s 弹入）替代 fade in；末档 fade out 退场。
    M2-P1-4：sfx_events（卡入场时刻）→ _build_sfx_mix 混入短音效；
    无事件/缺文件 → 音频直通 0:a?（降级不阻断）。
    M3-P1-5：zoom_windows → 原片链最前端 [0:v]zoompan 强调推近
    （sqrt ease-in 30% 峰值 + quadratic 回落，峰值 scale 1.08）— 卡/
    字幕 overlay 全在其后不被拉动；无窗不加（M2 行为不变）。
    M3-P2-6：card_y 安全区 _safe_margin(width) 折算（80px @1080）。
    """
    cmd = [str(resolve_ffmpeg()), "-y", "-i", str(video_path)]
    for c in cards:
        cmd += ["-loop", "1", "-i", str(c)]
    sub_idx = 1 + len(cards)
    if subtitle_ffconcat:
        cmd += ["-f", "concat", "-safe", "0", "-i", str(subtitle_ffconcat)]
    parts: list[str] = []
    prev = "0:v"
    if zoom_windows:
        zp = _zoompan_expr(zoom_windows, fps, width, height)
        if zp:
            parts.append(f"[0:v]{zp}[vz]")
            prev = "vz"
    target_w = max(120, int(width * scale))
    card_y = max(_safe_margin(width), int(height * 0.05))
    pop = effect == "pop"
    for i, (b0, b1) in enumerate(bounds, 1):
        if pop:
            # 三档尺寸快切入场：0.5x → 1.08x → 1.0x（各 0.06s）
            w_half = max(60, int(target_w * 0.5))
            w_up = int(target_w * 1.08)
            t1, t2 = b0 + 0.06, b0 + 0.12
            parts.append(f"[{i}:v]split=3[pa{i}][pb{i}][pc{i}]")
            parts.append(f"[pa{i}]scale={w_half}:-1,format=rgba[ga{i}]")
            parts.append(f"[pb{i}]scale={w_up}:-1,format=rgba[gb{i}]")
            parts.append(
                f"[pc{i}]scale={target_w}:-1,format=rgba,"
                f"fade=t=out:st={max(0.0, b1 - fade):.1f}:d={fade}:alpha=1"
                f"[gc{i}]"
            )
            parts.append(
                f"[{prev}][ga{i}]overlay=(W-w)/2:{card_y}:"
                f"enable='between(t,{b0:.2f},{t1:.2f})'[oa{i}]"
            )
            parts.append(
                f"[oa{i}][gb{i}]overlay=(W-w)/2:{card_y}:"
                f"enable='between(t,{t1:.2f},{t2:.2f})'[ob{i}]"
            )
            label = (
                f"v{i}" if i < len(bounds)
                else ("vdeck" if subtitle_ffconcat else "vout")
            )
            parts.append(
                f"[ob{i}][gc{i}]overlay=(W-w)/2:{card_y}:"
                f"enable='gte(t,{t2:.2f})'[{label}]"
            )
            prev = label
            continue
        out_st = f"fade=t=in:st={b0:.1f}:d={fade}:alpha=1"
        out_end = f"fade=t=out:st={max(0.0, b1 - fade):.1f}:d={fade}:alpha=1"
        chain = (
            f"[{i}:v]scale={target_w}:-1,format=rgba,"
            f"{out_st},{out_end}[s{i}]"
        )
        parts.append(chain)
        label = f"v{i}" if i < len(bounds) else (
            "vdeck" if subtitle_ffconcat else "vout"
        )
        parts.append(
            f"[{prev}][s{i}]overlay=(W-w)/2:{card_y}[{label}]"
        )
        prev = label
    if subtitle_ffconcat:
        if prev == "0:v":
            parts.append("[0:v]null[vbase]")
            prev = "vbase"
        parts.append(f"[{sub_idx}:v]fps={fps},format=rgba[sub]")
        parts.append(
            f"[{prev}][sub]overlay=(W-w)/2:{int(height * 0.70)}[vout]"
        )
    elif not parts:
        parts.append("[0:v]null[vout]")
    audio_map = "0:a?"
    sfx_meta: dict[str, Any] | None = None
    if sfx_events:
        sfx_paths, sfx_parts, alabel, sfx_meta = _build_sfx_mix(
            sfx_events,
            sfx_dir or _SFX_DIR,
            input_base=1 + len(cards) + (1 if subtitle_ffconcat else 0),
        )
        if sfx_paths:
            for p in sfx_paths:
                cmd += ["-i", str(p)]
            parts += sfx_parts
            audio_map = alabel
    fc = ";".join(parts)
    audio_codec = ["-c:a", "aac"] if sfx_meta else ["-c:a", "copy"]
    cmd += [
        "-filter_complex", fc,
        "-map", "[vout]", "-map", audio_map,
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        *audio_codec, "-r", str(fps),
        "-t", f"{duration:.2f}",
        str(out_path or video_path.parent / "decor_out.mp4"),
    ]
    return cmd, {
        "windows": bounds, "effect": effect, "fade": fade,
        "subtitle": bool(subtitle_ffconcat), "sfx": sfx_meta,
        "zoom": list(zoom_windows or []),
    }


async def _execute_decor(
    opts: dict[str, Any],
    body_video: Path,
    duration: float,
    scope_dir: Path,
    cards: list[Path],
    bounds: list[tuple[float, float]],
):
    """10.30：decor 合成 — 渲染卡已在传入，构造命令 + 跑 ffmpeg。"""
    yield {"event": "decor_start",
           "message": f"原片叠加合成：{len(cards)} 张素材卡"}
    cmd, meta = _build_decor_cmd(
        body_video, cards, bounds, duration,
        int((opts or {}).get("main_width", 720)),
        int((opts or {}).get("main_height", 1280)),
        scale=float((opts or {}).get("decor_scale", 0.6)),
        effect=str((opts or {}).get("decor_effect", "fade")),
    )
    from capabilities.video.skills.smart_cut import _run_ffmpeg
    await _run_ffmpeg(cmd)
    out = body_video.parent / "decor_out.mp4"
    if not out.is_file():
        yield {"success": False, "error": "decor synthesis failed"}
        return
    yield {"event": "decor_done", "data": {"output_path": str(out)}}



_KB_AMP = 0.06


def _kenburns_filter(
    duration: float, fps: int, width: int, height: int,
    zoom_in: bool = True,
) -> str:
    """N4：Ken Burns 缓推 filter（纯函数）— 消幻灯片静止僵硬感。

    2x upscale 防 zoompan 整像素抖动；z 按输出帧号 on 线性
    （1.00→1.06 推近 / 1.06→1.00 拉远）；x/y 居中表达式防 zoom
    漂移；输出回主画布尺寸。振幅 6%（_KB_AMP）— 页时长 5-12s
    只走 6%，缓到只察觉"在呼吸"不察觉在动。
    """
    frames = max(1, int(round(duration * fps)))
    if zoom_in:
        z = f"1+{_KB_AMP}*on/{frames}"
    else:
        z = f"{1 + _KB_AMP}-{_KB_AMP}*on/{frames}"
    return (
        f"scale=iw*2:ih*2,"
        f"zoompan=z='{z}':"
        f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"d=1:s={width}x{height}:fps={fps},"
        f"setsar=1,format=yuv420p"
    )


def _build_layers_cmd(
    pages: list[list[dict[str, Any]]], output_path: Path,
    durations: list[float], fps: int = 30,
    width: int = 1080, height: int = 1920, td: float = _XFADE_TD,
) -> tuple[list[str], dict[str, Any]]:
    """N5：元素分层动画合成命令（纯函数）。

    每层一输入（-loop 1 -t 页时长 e）；bg 层（t0=0/dur=0/y0=0）
    完整 pre 打底，文字层 format=rgba + fade alpha 入场，overlay
    y 表达式上滑落定（t<t0 时 alpha=0 不可见）；页产物进 xfade
    链（转场样式轮换 / 触发翻页 offset 契约不变）。单页无 xfade。
    Returns (cmd, meta) — meta 含 e/offsets/styles/layers_per_page。
    """
    n = len(pages)
    if len(durations) != n:
        raise ValueError(
            f"layers durations mismatch: {len(durations)} for {n} pages"
        )
    e = [d + td for d in durations]
    offsets: list[float] = []
    acc = 0.0
    for i in range(n - 1):
        acc += durations[i]
        offsets.append(acc)
    styles = [_XFADE_STYLES[i % len(_XFADE_STYLES)] for i in range(n - 1)]
    pre = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=#1a1a2e,"
        f"setsar=1,fps={fps},format=yuv420p"
    )
    parts: list[str] = []
    g = 0
    page_tags: list[str] = []
    for i, layers in enumerate(pages):
        base: str | None = None
        for j, ly in enumerate(layers):
            t0v, dur, y0 = float(ly["t0"]), float(ly["dur"]), int(ly["y0"])
            # 层 t0 = 页内相对时刻 — xfade 把第二输入重基到 offset，
            # 输入 PTS 0-based 即页内时间，st=t0v 即页内入场（live20
            # 探针实证：锚定 page_start 反而迟到 page_start，且短页
            # e_i < tg 时 fade 永不触发 → 整页无字）。
            tg = t0v
            is_bg = t0v == 0.0 and dur == 0.0 and y0 == 0
            if is_bg:
                parts.append(f"[{g}:v]{pre}[ly{g}]")
            else:
                parts.append(
                    f"[{g}:v]format=rgba,setsar=1,fps={fps},"
                    f"fade=t=in:st={tg:.2f}:d={dur:.2f}:alpha=1[ly{g}]"
                )
            if base is None:
                base = f"ly{g}"
            else:
                out = f"pg{i}" if j == len(layers) - 1 else f"ov{i}_{j}"
                parts.append(
                    f"[{base}][ly{g}]overlay=0:"
                    f"'{y0}*max(0,1-(t-{tg:.2f})/{dur:.2f})'[{out}]"
                )
                base = out
            g += 1
        if base is None:
            raise ValueError(f"page {i} has no layers")
        page_tags.append(base)
    if n > 1:
        cur = page_tags[0]
        for i in range(n - 1):
            out = f"x{i + 1}" if i + 1 < n - 1 else "vout"
            parts.append(
                f"[{cur}][{page_tags[i + 1]}]xfade=transition={styles[i]}:"
                f"duration={td:.2f}:offset={offsets[i]:.2f}[{out}]"
            )
            cur = out
        fc = ";".join(parts)
        final = "[vout]"
    else:
        fc = ";".join(parts)
        final = page_tags[0]

    cmd = ["ffmpeg", "-y"]
    for layers, ei in zip(pages, e):
        for ly in layers:
            cmd += ["-loop", "1", "-t", f"{ei:.2f}", "-i", str(ly["png"])]
    cmd += [
        "-filter_complex", fc, "-map", final,
        "-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p",
        str(output_path),
    ]
    return cmd, {
        "e": e, "offsets": offsets, "styles": styles,
        "layers_per_page": [len(p) for p in pages],
    }


def _build_typewriter_cmd(
    frame_dirs: list[Path], output_path: Path,
    durations: list[float], fps: int = 30,
    width: int = 1080, height: int = 1920, td: float = _XFADE_TD,
) -> tuple[list[str], dict[str, Any]]:
    """N6：打字机帧序列合成命令（纯函数）。

    每页帧目录一 image2 输入（-framerate + -t e_i 硬截 — offset
    契约不因帧数取整漂移），页产物 scale/pad 归一后进 xfade 链
    （转场样式轮换 / 触发翻页 offset 契约与层链一致）。动画已在
    帧里，无层 fade。单页无 xfade。
    Returns (cmd, meta) — meta 含 e/offsets/styles。
    """
    n = len(frame_dirs)
    if len(durations) != n:
        raise ValueError(
            f"typewriter durations mismatch: {len(durations)} for {n} pages"
        )
    e = [d + td for d in durations]
    offsets: list[float] = []
    acc = 0.0
    for i in range(n - 1):
        acc += durations[i]
        offsets.append(acc)
    styles = [_XFADE_STYLES[i % len(_XFADE_STYLES)] for i in range(n - 1)]
    pre = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=#1a1a2e,"
        f"setsar=1,fps={fps},format=yuv420p"
    )
    parts = [f"[{i}:v]{pre}[pg{i}]" for i in range(n)]
    if n > 1:
        cur = "pg0"
        for i in range(n - 1):
            out = f"x{i + 1}" if i + 1 < n - 1 else "vout"
            parts.append(
                f"[{cur}][pg{i + 1}]xfade=transition={styles[i]}:"
                f"duration={td:.2f}:offset={offsets[i]:.2f}[{out}]"
            )
            cur = out
        fc = ";".join(parts)
        final = "[vout]"
    else:
        fc = ";".join(parts)
        final = "pg0"
    cmd = ["ffmpeg", "-y"]
    for d_path, ei in zip(frame_dirs, e):
        cmd += [
            "-framerate", str(fps), "-t", f"{ei:.2f}",
            "-i", str(d_path / "%06d.png"),
        ]
    cmd += [
        "-filter_complex", fc, "-map", final,
        "-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p",
        str(output_path),
    ]
    return cmd, {"e": e, "offsets": offsets, "styles": styles}


def _build_xfade_cmd(
    screenshots: list[Path], output_path: Path, durations: list[float],
    fps: int = 30, width: int = 1920, height: int = 1080,
    td: float = _XFADE_TD, kenburns: bool = False,
) -> tuple[list[str], dict[str, Any]]:
    """10.25-F：句边界模式 xfade 转场链命令构造（纯函数，便于测试）。

    10.29 live11 实证：durations 数 < 页数时 offset 畸形 → ffmpeg 挂，
    前置 fail fast 断言。

    数学：e_i = d_i + td（全页；末页多给 td 保完整显示，尾部多出的
    td 由下游合成 -t 硬截，句边界不漂移）。第 i 次转场
    offset_i = sum(e_0..e_i) − (i+1)·td = 前缀和(d)[i]，转场区间
    [B_i, B_i+td]（B_i=句边界累计点）→ 切换点精确对齐讲解句边界。
    样式按 _XFADE_STYLES 确定性轮换。kenburns（N4）：每输入 pre
    后追加 Ken Burns 缓推，奇偶页推近/拉远交替。
    Returns (cmd, meta) — meta 含 e/offsets/styles/kb（测试断言用）。
    """
    n = len(screenshots)
    if len(durations) != n:
        raise ValueError(
            f"xfade durations mismatch: {len(durations)} for {n} slides "
            "(10.29 fail fast — 句边界映射错位请检查 _remap_transcript_rows)"
        )
    e = [d + td for d in durations]
    offsets: list[float] = []
    acc = 0.0
    for i in range(n - 1):
        acc += durations[i]
        offsets.append(acc)
    styles = [_XFADE_STYLES[i % len(_XFADE_STYLES)] for i in range(n - 1)]

    pre = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=#1a1a2e,"
        f"setsar=1,fps={fps},format=yuv420p"
    )
    kb_dirs: list[bool] = []
    parts = []
    for i in range(n):
        f_pre = pre
        if kenburns:
            zi = i % 2 == 0
            kb_dirs.append(zi)
            f_pre = pre + "," + _kenburns_filter(
                durations[i], fps, width, height, zoom_in=zi,
            )
        parts.append(f"[{i}:v]{f_pre}[v{i}]")
    cur = "v0"
    for i in range(n - 1):
        out = f"x{i + 1}" if i + 1 < n - 1 else "vout"
        parts.append(
            f"[{cur}][v{i + 1}]xfade=transition={styles[i]}:"
            f"duration={td:.2f}:offset={offsets[i]:.2f}[{out}]"
        )
        cur = out
    fc = ";".join(parts)

    cmd = ["ffmpeg", "-y"]
    for ss, ei in zip(screenshots, e):
        cmd += ["-loop", "1", "-t", f"{ei:.2f}", "-i", str(ss)]
    cmd += [
        "-filter_complex", fc, "-map", "[vout]",
        "-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p",
        str(output_path),
    ]
    return cmd, {"e": e, "offsets": offsets, "styles": styles, "kb": kb_dirs}


def _remap_transcript_rows(
    rows: list[dict[str, Any]],
    kept_segments: list[tuple[float, float]] | None,
) -> list[dict[str, Any]]:
    """10.27：句时间戳从原视频轴映射到裁后轴（smart_cut 串联）。

    删除区 = 相邻保留段之间的 gap；句中点落在 gap 内 → 该句被裁掉
    （丢弃）；其余句 start/end 平移（减去其之前所有 gap 长度）。
    kept_segments 缺失/单段（无删除区）→ 原样返回。
    """
    if not kept_segments or len(kept_segments) < 2:
        return rows
    gaps = [
        (kept_segments[i][1], kept_segments[i + 1][0])
        for i in range(len(kept_segments) - 1)
        if kept_segments[i + 1][0] > kept_segments[i][1]
    ]
    if not gaps:
        return rows

    def shift(t: float) -> float:
        return t - sum(min(g1, t) - g0 for g0, g1 in gaps if t > g0)

    out: list[dict[str, Any]] = []
    for r in rows:
        s, e = float(r.get("start", 0)), float(r.get("end", 0))
        # 10.29 live11 实证：句任一端落删除区 → 丢弃（中点判定会放过
        # start 在 gap 内的跨切口碎词，映射后边界错位 → 5 页只出 4 durs
        # → xfade 命令畸形 ffmpeg 挂）
        if any(
            (g0 <= s <= g1) or (g0 <= e <= g1) for g0, g1 in gaps
        ):
            continue
        # 10.31 M1-P0-1：词戳随句平移（删除区词丢弃）— 否则串联裁剪后
        # karaoke 用原轴词戳 → 字幕错位
        rw = r.get("words") or []
        if rw:
            kept_w = []
            for w in rw:
                ws, we = float(w.get("start", 0)), float(w.get("end", 0))
                if any(
                    (g0 < ws < g1) or (g0 < we < g1)
                    for g0, g1 in gaps
                ):
                    continue
                kept_w.append({**w, "start": shift(ws), "end": shift(we)})
            r = {**r, "words": kept_w}
        out.append({**r, "start": shift(s), "end": shift(e)})
    return out


def _slide_durations_from_transcript(
    transcript_rows: list[dict[str, Any]],
    n_slides: int,
    total_duration: float,
) -> list[float]:
    """句时间戳 → 每页幻灯片时长（10.23-D 句边界对齐）。

    M 句按位置比例切 N 页（每页 spans[L*k/N : L*(k+1)/N]），页时长 =
    块末句 end − 块首句 start；首页 start 夹 0、末页 end 夹
    total_duration — 得到**恰 n_slides 个**连续覆盖 [0, total] 的
    durations，切换点与讲解句边界同步。
    10.29 live11 实证：旧 ceil(M/N) 分块在 M=11/N=5 时只产 4 块 ≠ 页数
    → xfade mismatch ffmpeg 挂；改为按位置切分保证块数==页数。
    transcript 空 / n_slides<=0 → []（调用方走旧均分，向后兼容）。
    """
    spans = []
    for r in transcript_rows or []:
        s, e = float(r.get("start") or 0.0), float(r.get("end") or 0.0)
        if e > s:
            spans.append((s, e))
    if not spans or n_slides <= 0:
        return []
    L = len(spans)
    durs: list[float] = []
    last_block_start = 0.0
    for k in range(n_slides):
        i0 = (L * k) // n_slides
        i1 = (L * (k + 1)) // n_slides
        if i1 > i0:
            chunk = spans[i0:i1]
            block_start = chunk[0][0] if k > 0 else 0.0
            block_end = chunk[-1][1]
        else:
            # M < N：该页分不到句，延用上一块起点（末页夹片尾会覆盖）
            block_start, block_end = last_block_start, last_block_start
        durs.append(max(0.5, block_end - block_start))
        last_block_start = block_start
    # 末页夹到片尾（含片尾静音/收尾段）
    durs[-1] = max(0.5, total_duration - last_block_start)
    return durs


def _sentence_block_starts(
    transcript_rows: list[dict[str, Any]],
    n_slides: int,
) -> list[float] | None:
    """N1：句边界块起点（与 _slide_durations_from_transcript 同源
    切分，纯函数）。页 0 = 首句 start（对齐调用方 t0 降级链路，
    非句边界版本的 0.0 — 触发翻页的页 0 起点即 handoff 让位时刻）；
    M<N 页延用上一块。transcript 空 → None（调用方自行兜底）。"""
    spans = []
    for r in transcript_rows or []:
        s, e = float(r.get("start") or 0.0), float(r.get("end") or 0.0)
        if e > s:
            spans.append((s, e))
    if not spans or n_slides <= 0:
        return None
    L = len(spans)
    starts: list[float] = []
    last = spans[0][0]
    for k in range(n_slides):
        i0 = (L * k) // n_slides
        i1 = (L * (k + 1)) // n_slides
        if k == 0:
            st = spans[0][0]
        elif i1 > i0:
            st = spans[i0][0]
        else:
            # M < N：该页分不到句，延用上一块起点
            st = last
        starts.append(st)
        last = st
    return starts


# N1：让位转场时长（xfade/acrossfade 共用）
_HANDOFF_TD = 0.4
# N1：触发翻页起点最小间隙（更近视为误命中，回退句边界）
_TRIGGER_MIN_GAP = 0.5


def _trigger_slide_starts(
    rows: list[dict[str, Any]],
    slides: list[dict[str, Any]],
    duration: float,
) -> tuple[float, list[float]] | None:
    """N1：触发翻页起点（纯函数，M2 词级时钟同源）。

    顺序扫描：每页 trigger 从上一页命中之后找（触发词按讲解顺序
    消耗 — 10.31 live16 实证：片头总结句里出现过的短语会让末页
    「首次出现」命中落在首页之前 → 乱序整链回退）。命中 → 词
    start；miss → 该页句边界块起点单页降级（live16 实证：末页
    触发词可能只在前文出现过，all-or-nothing 让触发翻页整链失效）。
    起点单调化（间隙 ≥0.5s）+ 末页夹 duration-0.5（末页至少 0.5s，
    与句边界「夹片尾」同源）。全 miss / 无页 → None（调用方回退
    句边界，绝不阻断）。
    """
    words = [w for r in rows for w in (r.get("words") or [])]
    chars: list[str] = []
    idx_of: list[int] = []
    for wi, w in enumerate(words):
        clean = "".join(
            c for c in _simplify_cn(str(w.get("text", "")))
            if c not in _TRIGGER_PUNCT
        )
        for c in clean:
            chars.append(c)
            idx_of.append(wi)
    hay = "".join(chars)
    raw: list[float | None] = []
    cursor = 0
    for s in slides or []:
        tv = s.get("trigger")
        text = (
            str(tv.get("text") or "") if isinstance(tv, dict)
            else (tv if isinstance(tv, str) else "")
        )
        t = _simplify_cn(text).strip()
        st: float | None = None
        if t:
            pos = hay.find(t, cursor)
            if pos >= 0:
                try:
                    st = float(words[idx_of[pos]].get("start", 0) or 0)
                    cursor = pos + len(t)
                except (IndexError, TypeError, ValueError):
                    st = None
        raw.append(st)
    n = len(raw)
    if n == 0 or all(x is None for x in raw):
        return None
    sb = _sentence_block_starts(rows, n) or [0.0] * n
    out = [
        float(x) if x is not None else float(sb[k])
        for k, x in enumerate(raw)
    ]
    # 单调化：间隙 <0.5s 的页回退句边界兜底（仍冲突 → 前点+gap）
    for k in range(1, n):
        if out[k] < out[k - 1] + _TRIGGER_MIN_GAP:
            out[k] = max(float(sb[k]), out[k - 1] + _TRIGGER_MIN_GAP)
    # 末页夹 duration-0.5（末页至少 0.5s）
    if out[-1] > float(duration) - _TRIGGER_MIN_GAP:
        out[-1] = max(0.5, float(duration) - _TRIGGER_MIN_GAP)
    bounds = out + [float(duration)]
    return out[0], [b - a for a, b in zip(bounds, bounds[1:])]


def _load_deck_slides(path: Path) -> list[dict[str, Any]] | None:
    """N1：回读 slides.json 的 slides 数组（触发翻页数据源，
    含 M2 trigger 字段；防御式，读不到 → None）。"""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    slides = data.get("slides") if isinstance(data, dict) else data
    if not isinstance(slides, list) or not slides:
        return None
    out = [s for s in slides if isinstance(s, dict)]
    return out or None


def _handoff_plan(
    t0: float, duration: float, td: float = _HANDOFF_TD,
) -> dict[str, Any] | None:
    """N1：开场全屏 → t0 缩小让位的分段计划（纯函数）。

    t0<=0 / t0 > duration-2（段B 至少 2s）→ None（走现行为单段）。
    segA 截 [0, t0+td+0.5]（多 0.5s 余量防 xfade 边界末帧差）；
    segB = [t0, duration]（PIP 合成从 t0 截段，页序列原轴对齐）。
    """
    t0 = float(t0 or 0)
    if t0 <= 0 or t0 > duration - 2.0:
        return None
    return {
        "t0": t0, "td": td,
        "segA": (0.0, t0 + td + 0.5),
        "segB": (t0, float(duration)),
    }


def _handoff_cmd(
    seg_a: Path, seg_b: Path, t0: float, td: float, out_path: Path,
) -> list[str]:
    """N1：段A（全屏人）+ 段B（PIP 让位）→ xfade 溶解合并（纯函数）。

    视频在 [t0, t0+td] 交叉溶解；音频两段天然重叠同源区间
    （segA=[0,t0+td]、segB=[t0,dur]）→ acrossfade 交叉淡化。
    """
    return [
        "ffmpeg", "-y",
        "-i", str(seg_a), "-i", str(seg_b),
        "-filter_complex", (
            f"[0:v][1:v]xfade=transition=fade:duration={td:.3f}"
            f":offset={t0:.3f}[v];"
            f"[0:a][1:a]acrossfade=d={td:.3f}[a]"
        ),
        "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "fast",
        "-c:a", "aac", "-b:a", "128k",
        "-pix_fmt", "yuv420p",
        str(out_path),
    ]


def _pip_full_cmd(
    main_video_path: Path | None,
    matte_fps: float, matte_frame_dir: Path, video_path: Path,
    main_width: int, main_height: int,
    pip_w: int, pip_h: int,
    overlay_x: int, overlay_y: int,
    duration: float, bg_hex: str,
    handoff_t0: float | None,
    output_path: Path,
) -> list[str]:
    """matte 路径 pip_full 合成命令（纯函数，便于测试）。

    handoff_t0 给定（N1）→ 主画面 = 纯色底(t0) + slides concat：
    页链时长按裁后轴 [t0, duration] 规划（chain τ0 ↔ handoff t0），
    slides 必须前置 t0 底对齐口播（live20 实证：从 0 直放 → 整链
    提前 t0 — 页 0 被段A盖住、翻页全提前、末页后冻结）。无 handoff
    → slides 从 0 直放（现行为）。
    """
    if main_video_path and handoff_t0 is not None:
        inputs = [
            "-f", "lavfi", "-i",
            f"color=c=0x{bg_hex}:s={main_width}x{main_height}"
            f":d={handoff_t0:.3f}:r=30",
            "-i", str(main_video_path),
        ]
        compose = (
            "[0:v][1:v]concat=n=2:v=1:a=0[main_v];"
            f"[2:v]scale={pip_w}:{pip_h}:flags=lanczos[pip];"
            f"[main_v][pip]overlay={overlay_x}:{overlay_y}:format=auto[out_v]"
        )
        audio_idx = 3
    else:
        if main_video_path:
            inputs = ["-i", str(main_video_path)]
        else:
            inputs = [
                "-f", "lavfi", "-i",
                f"color=c=0x{bg_hex}:s={main_width}x{main_height}"
                f":d={duration:.3f}:r=30",
            ]
        compose = (
            f"[1:v]scale={pip_w}:{pip_h}:flags=lanczos[pip];"
            f"[0:v][pip]overlay={overlay_x}:{overlay_y}:format=auto[out_v]"
        )
        audio_idx = 2
    return [
        "ffmpeg", "-y", *inputs,
        "-framerate", f"{matte_fps}", "-i", str(matte_frame_dir / "%06d.png"),
        "-i", str(video_path),
        "-filter_complex", compose,
        "-map", "[out_v]", "-map", f"{audio_idx}:a?",
        "-c:v", "libx264", "-preset", "fast",
        "-c:a", "aac", "-b:a", "128k",
        "-pix_fmt", "yuv420p",
        # -t 硬限输出时长：-shortest 在 muxer 层软掐，x264
        # lookahead 会多带 1-2s（真机实证 56.07 vs 源 54.1）
        "-t", f"{duration:.3f}",
        "-shortest",
        str(output_path),
    ]


class GateMode(str, Enum):
    STANDALONE = "standalone"
    CHAINED = "chained"
    AUTO_CONFIRM = "auto_confirm"


class PipPosition(str, Enum):
    BOTTOM_RIGHT = "bottom_right"
    BOTTOM_LEFT = "bottom_left"
    TOP_RIGHT = "top_right"
    TOP_LEFT = "top_left"


class RevisionInput(BaseModel):
    """10.19 对话式修改输入 — 用户对已确认规划的修改意见。"""
    raw_text: str = Field(description="用户修改意见原话（如'第2个卡片删掉'）")
    current_plan: dict[str, Any] = Field(
        description="当前规划（overlay_plan_ready 事件里的 plan 原样回传）",
    )
    revision_round: int = Field(default=1, description="修改轮数（调用方维护，≤5）")


class OverlayInput(BaseModel):
    """叠加增强输入。"""
    video_path: str = Field(description="视频文件路径")
    gate_mode: GateMode = Field(default=GateMode.STANDALONE, description="闸门模式")
    auto_confirm_low_risk: bool = Field(default=False, description="自动确认低风险闸门")
    templates: list[str] | None = Field(default=None, description="指定模板白名单")
    kept_segments: list[tuple[float, float]] | None = Field(
        default=None,
        description="串联模式：smart_cut 的保留片段（用于锚点重映射）",
    )
    cut_video_path: str | None = Field(
        default=None,
        description="串联模式：smart_cut 裁后视频路径（10.27）— 提供后"
                    "视频本体/时长切裁后轴，转录仍跑原视频",
    )
    pip: bool = Field(default=False, description="画中画模式：将视频人物缩小放在角落，主画面留给演示内容")
    pip_position: PipPosition = Field(default=PipPosition.BOTTOM_RIGHT, description="画中画位置")
    pip_scale: float = Field(default=0.3, description="画中画缩放比例（0.1~0.5）")
    overlay_decor: bool = Field(
        False,
        description="10.30 装饰叠加：原片全屏不抠人，素材卡按句边界叠加（与 pip 互斥，优先级更高）",
    )
    decor_scale: float = Field(
        0.6, ge=0.3, le=1.0,
        description="10.31 M1-P0-3：素材卡宽占原片宽比例（旧横幅 85% 降为轻装饰）",
    )
    decor_effect: str = Field(
        "pop", description="10.30 素材卡入场特效（fade 渐显 / "
        "10.31 M2 pop 三档快切弹入，默认 pop）",
    )
    pip_margin: int = Field(default=20, description="画中画距边缘像素")
    handoff: bool = Field(
        default=False,
        description="N1 开场全屏 → t0 缩小让位（matting 路径；"
                    "t0=开始讲解时刻，主画面让位给演示内容）",
    )
    handoff_t0: float | None = Field(
        default=None,
        description="让位时刻显式覆盖（缺省：首页触发词 > 句边界 > 3.0s）",
    )
    deck_triggers: list[str] | None = Field(
        default=None,
        description="手动 deck 翻页触发词（与页序对齐，说到才翻；"
                    "auto_deck 用 slides.json 自带 trigger）",
    )
    bg_color: str = Field(default="#1a1a2e", description="主画面背景色（画中画模式）")
    deck_html: str | None = Field(default=None, description="PPT/演示文稿 HTML 文件路径（画中画主画面）")
    deck_images: list[str] | None = Field(
        default=None,
        description="10.20：幻灯片配图路径列表（全屏主画面）— 直接喂图，"
                    "跳过 deck_html 截图",
    )
    auto_deck: bool = Field(
        default=False,
        description="10.21 一键：无 deck_images 时自动转录→LLM 生成要点"
                    "幻灯片作主画面；LLM 失败/为空降级纯色底（deck_fallback），"
                    "绝不阻断出片",
    )
    deck_theme: str = Field(
        default="midnight",
        description="auto_deck 幻灯片主题（midnight/cream/sunset），"
                    "'auto'=LLM 按内容气质自选（10.25-E），"
                    "非法值回退 midnight",
    )
    deck_max_slides: int = Field(
        default=6, description="auto_deck 幻灯片张数上限",
    )
    deck_confirm: str | None = Field(
        default=None,
        description="10.22 deck 确认闸门回调轮：None=首轮生成；"
                    "'approve'=满意直合成（零 LLM）；'retheme'=换主题重渲染"
                    "（零 LLM，配 deck_theme）；'regenerate'=按反馈重新生成"
                    "（配 deck_revision）",
    )
    deck_slides_json: str | None = Field(
        default=None,
        description="10.22 回调轮回传：首轮 deck_preview_ready 事件给的 "
                    "slides.json 路径（deck 产物持久化目录）",
    )
    deck_revision: str | None = Field(
        default=None,
        description="10.22 regenerate 的自然语言修改意见（原话拼进 LLM "
                    "prompt 反馈段重新提炼）",
    )
    deck_revision_round: int = Field(
        default=0,
        description="10.22 regenerate 已进行轮数（>5 拒绝，护栏对齐 "
                    "_MAX_REVISION_ROUNDS）",
    )
    matting: bool = Field(
        default=False,
        description="10.20 人物抠像：RVM 真抠像（alpha 融合，无边框）；"
                    "失败自动回退矩形 PIP",
    )
    slide_duration: float = Field(default=5.0, description="每页幻灯片停留秒数")
    transcript: list[dict[str, Any]] | None = Field(
        default=None,
        description="已知文本时间轴（跳过 whisper）。内嵌路径由上游提供原文"
                    "（如 auto_short_video 的 script captions）— whisper 对 TTS "
                    "合成音频误听率高（90%→九十辈子），已知文本时直接喂原文更准更快。"
                    "元素格式：{index, start, end, text, words:[{text,start,end}]}",
    )
    revision: RevisionInput | None = Field(
        default=None,
        description="10.19 对话式修改：提供时走修改路径（local patch / global "
                    "feedback 重规划），不提供走常规规划",
    )


@register
class TalkingHeadOverlaySkill(Skill):
    """叠加增强 Skill。"""

    node_type: str = "edit"
    name: str = "talking_head_overlay"
    display_name: str = "AI口播增强"
    description: str = (
        "AI口播剪辑增强：Whisper转录→LLM规划叠加动画→Playwright渲染→ffmpeg合成。"
        "根据口播内容自动生成动态字幕（逐词高亮/弹出）、关键词图标、数据卡片、标题卡等叠加动画。"
        "也支持画中画模式（pip=True）：将人像缩小放角落，主画面留给演示内容。"
    )
    skill_category: str = "edit"
    skill_priority: str = "hot"
    trigger_words: list[str] = ["口播", "口播演示", "画中画", "叠加增强", "加字幕"]
    needs_confirmation: bool = True

    # ── video_pipeline_progress 发射器（docs/口播前端交互设计方案.md#3.4）──
    # 仿 auto_short_video._emit_step；载荷补 phase 枚举（transcribe/plan/
    # render/review），前端据此渲染阶段清单 + 总百分比。workflow_id 缺失
    # （脚本直调/单测）时为空操作，行为与改造前一致。
    _PROGRESS_TOTAL = 6

    def _make_progress_emitter(
        self, workflow_id: str, turn_id: str,
    ) -> Any:
        async def _emit(
            step: int, phase: str, label: str, detail: str = "",
        ) -> None:
            if not workflow_id:
                return
            from app.services.sse_bus import sse_bus

            await sse_bus.publish(
                workflow_id,
                "video_pipeline_progress",
                {
                    "skill": "talking_head_overlay",
                    "step": step,
                    "total": self._PROGRESS_TOTAL,
                    "phase": phase,
                    "label": label,
                    "detail": detail,
                    "percent": int(step / self._PROGRESS_TOTAL * 100),
                },
                turn_id=turn_id or None,
            )

        return _emit

    async def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        parsed = OverlayInput(**params)
        video_path = Path(parsed.video_path)

        # ── 10.27 串联修复：cut_video_path 提供 → 本体/时长切裁后轴 ──
        # 此前 kept_segments 只映射词戳，视频本体从不被裁（live9-2 实证
        # 成片 54.08s ≈ 源 54.10s 而非裁后 53.8s）→ 串联端到端断链。
        # 转录仍跑原视频（词戳原轴 → TimeMapper 映射），句边界 rows 同步映射。
        body_video: Path = video_path
        if parsed.cut_video_path:
            _cv = Path(parsed.cut_video_path)
            if not _cv.is_file():
                yield {"success": False,
                       "error": f"cut video not found: {parsed.cut_video_path}"}
                return
            body_video = _cv

        # 产物隔离：落盘目录按「用户 → 本次创作任务」分层。
        # 此前只有全局 outputs/talking_head_overlay/，文件名仅含
        # 「源视频 stem + 规划指纹」—— 两个用户上传同名 video.mp4 且规划一致
        # 时路径完全重合，后者直接覆盖前者。user_id/workflow_id 由 reactor 的
        # skill_call 注入（_context / _user_id / _workflow_id），
        # 独立调用缺失时回退旧路径，行为不变。
        user_id, workflow_id = resolve_owner(params)
        scope_dir = scoped_dir(_OUTPUT_DIR / "talking_head_overlay", user_id, workflow_id)

        # 进度发射器：供本方法与 _execute_pip 共用（实例每次 skill_call 新建，
        # 实例属性无跨工作流串扰；getattr 兜底保证直调 _execute_pip 的旧测试兼容）
        _emit_progress = self._make_progress_emitter(
            workflow_id, str(params.get("_turn_id") or ""),
        )
        self._progress_emit = _emit_progress

        if not video_path.is_file():
            yield {"success": False, "error": f"video not found: {parsed.video_path}"}
            return

        try:
            from capabilities.video.adapters.silence_detect import probe_duration
            total_duration = await probe_duration(str(body_video))
        except Exception:
            total_duration = 0.0

        if parsed.pip or parsed.overlay_decor:
            # 10.30：overlay_decor 复用 pip 的 deck 闸门链（转录/规划/
            # 停点确认），deck 就绪后在合成段分叉到 _finalize_decor
            # （原片全屏 + 素材卡叠加，不抠像缩窗）
            async for chunk in self._execute_pip(parsed, body_video, total_duration, scope_dir):
                yield chunk
            return

        try:
            base_w, base_h = await self._probe_resolution(str(video_path))
        except Exception:
            base_w, base_h = 1080, 1920

        async with managed_temp_dir() as tmp_dir:
            try:
                await _emit_progress(
                    1, "transcribe", "转写与词级对齐",
                    f"时长 {total_duration:.1f}s" if total_duration else "",
                )
                # ── 10.19 对话式修改路由：有 revision 走修改路径 ──
                revision_meta = None
                if parsed.revision is not None:
                    if parsed.revision.revision_round > _MAX_REVISION_ROUNDS:
                        yield {
                            "event": "revision_rejected",
                            "data": {
                                "error": f"修改轮数超限（≤{_MAX_REVISION_ROUNDS}），"
                                         "请换用整体重规划或新建会话",
                            },
                            "needs_confirmation": False,
                        }
                        return
                    revision_meta, plan, timed_words, seg_dicts = (
                        await self._revision_plan(parsed, total_duration)
                    )
                    if plan is None:
                        # 解析失败/定位失败/patch 自检不过 → 反问不猜
                        yield {
                            "event": "revision_clarify",
                            "data": {"problems": revision_meta},
                            "needs_confirmation": True,
                            "message": "没听懂要改哪里，请说得更具体一点",
                        }
                        return
                else:
                    plan, timed_words, seg_dicts = await self._generate_plan(
                        parsed, total_duration, tmp_dir,
                    )
                if not plan.segments:
                    yield {
                        "success": True,
                        "message": "no overlays planned",
                        "output_path": str(video_path),
                    }
                    return

                await _emit_progress(
                    2, "plan", "叠加规划完成",
                    f"{len(plan.segments)} 个叠加片段",
                )

                revision_note = (
                    ""
                    if revision_meta is None
                    else (
                        f"（修改 round {parsed.revision.revision_round}·"
                        f"{revision_meta.get('route')}）"
                    )
                )
                if parsed.gate_mode == GateMode.AUTO_CONFIRM:
                    # 自动确认也广播规划（免确认事件），供下游日志/抽帧对照
                    yield {
                        "event": "overlay_plan_ready",
                        "data": {**plan.model_dump(), "revision": revision_meta},
                        "needs_confirmation": False,
                        "message": f"自动确认：规划了 {len(plan.segments)} 个叠加片段{revision_note}",
                    }
                else:
                    yield {
                        "event": "overlay_plan_ready",
                        "data": {**plan.model_dump(), "revision": revision_meta},
                        "needs_confirmation": True,
                        "message": f"规划了 {len(plan.segments)} 个叠加片段，请确认{revision_note}",
                    }
                    return

                await _emit_progress(3, "render", "渲染叠加动画预览")
                preview_pairs = await self._render_previews(
                    plan, timed_words, tmp_dir, base_w, base_h,
                    llm=self._resolve_llm(),
                )
                if parsed.gate_mode != GateMode.AUTO_CONFIRM:
                    yield {
                        "event": "overlay_preview_ready",
                        "data": {"previews": [str(p) for _, p in preview_pairs]},
                        "needs_confirmation": True,
                        "message": "动画预览已生成，请确认",
                    }
                    return

                # ── P0 视觉自审闭环：VLM 看图审查 → 带反馈重规划重渲 ──
                # 降级契约：VLM 不可用 → 0 事件直接合成，审查绝不阻断出片
                state = {
                    "plan": plan,
                    "preview_pairs": preview_pairs,
                    "review": None,
                }
                await _emit_progress(4, "review", "VLM 视觉自审")
                async for review_event in self._review_loop(
                    state, seg_dicts, timed_words, parsed,
                    total_duration, tmp_dir, base_w, base_h,
                ):
                    yield review_event
                plan, preview_pairs = state["plan"], state["preview_pairs"]

                await _emit_progress(5, "render", "ffmpeg 合成成片")
                output_path = tmp_dir / f"{video_path.stem}_overlay{video_path.suffix}"
                final_path = await self._compose_final(
                    str(video_path), preview_pairs, output_path,
                )

                perm_dir = scope_dir
                perm_dir.mkdir(parents=True, exist_ok=True)
                # 产物隔离：同名源视频 + 不同叠加规划属于不同作品，落盘不能同名覆盖
                _plan_key = hashlib.md5(
                    json.dumps(plan.model_dump(), ensure_ascii=False, sort_keys=True).encode("utf-8")
                ).hexdigest()[:8]
                perm_path = perm_dir / f"{final_path.stem}_{_plan_key}{final_path.suffix}"
                shutil.copy2(str(final_path), str(perm_path))

                await _emit_progress(6, "review", "口播增强完成", str(perm_path))

                yield {
                    "event": "overlay_compose_done",
                    "data": {
                        "output_path": str(perm_path),
                        "overlay_count": len(plan.segments),
                        # 终版规划快照（P0 重规划后与初规划可能不同，
                        # report.json 需要审计"渲染用的到底是哪版规划"）
                        "final_plan": plan.model_dump(),
                    },
                    "needs_confirmation": False,
                    "message": f"叠加合成完成：{len(plan.segments)} 个片段",
                }

            except Exception as e:
                logger.error(f"talking_head_overlay failed: {e}", exc_info=True)
                yield {
                    "event": "overlay_error",
                    "data": {"error": str(e)},
                    "needs_confirmation": False,
                }

    async def _execute_pip(
        self,
        parsed: OverlayInput,
        video_path: Path,
        duration: float,
        scope_dir: Path,
    ) -> Any:
        """画中画模式：将视频人物缩小放在角落，主画面留给演示内容。

        两种主画面模式：
          A) deck_html 指定 PPT → Playwright 逐页截图 → 幻灯片视频
          B) 无 deck_html → 纯色背景主画面

        最后用 ffmpeg 将原视频缩小叠加到指定角落位置。
        """
        yield {
            "event": "pip_start",
            "data": {
                "video_path": str(video_path),
                "position": parsed.pip_position.value,
                "scale": parsed.pip_scale,
                "deck_html": parsed.deck_html,
            },
            "message": (
                f"开始口播演示合成：PPT={'有' if parsed.deck_html else '无'}，"
                f"位置={parsed.pip_position.value}，缩放={parsed.pip_scale}"
            ),
        }
        _emit = getattr(self, "_progress_emit", None)
        if _emit:
            await _emit(
                1, "transcribe", "转写与演示素材准备",
                f"时长 {duration:.1f}s" if duration else "",
            )

        perm_dir = scope_dir
        perm_dir.mkdir(parents=True, exist_ok=True)

        async with managed_temp_dir() as tmp_dir:
            main_video_path: Path | None = None
            main_width = 1920
            main_height = 1080

            # ── 10.22 deck 确认闸门：回调轮路由（approve/retheme/
            # regenerate）。产物持久化在 scope_dir（跨 execute 可达 —
            # 会话 tmp 不跨轮，live3b 实证），approve 消费首轮已渲染
            # PNG（缺失时同版确定性重渲染兜底，零 LLM）。
            auto_deck_images: list[str] = []
            t0_eff: float | None = None  # N1 让位时刻（方案降级链解析）
            # 10.23-D：句边界对齐的句时间戳来源（transcript 已知优先，
            # whisper 子进程 rows 兜底）
            _deck_transcript_rows: list[dict[str, Any]] = list(
                parsed.transcript or []
            )
            # 10.27：串联裁剪 — 首轮 rows 是原轴，映射到裁后轴
            # （回调轮回读 transcript_rows.json 的已映射，不会走到这里）
            if _deck_transcript_rows and parsed.cut_video_path:
                _deck_transcript_rows = _remap_transcript_rows(
                    _deck_transcript_rows, parsed.kept_segments,
                )
            deck_ready_theme = parsed.deck_theme
            if (
                parsed.deck_confirm
                and parsed.deck_slides_json
                and not parsed.deck_images
            ):
                from capabilities.video.services.deck_orchestrator import (
                    generate_deck_slides,
                    render_deck_from_slides,
                )

                # 10.23-D：回调轮回读首轮持久化的句时间戳（保持句边界对齐）
                if not _deck_transcript_rows:
                    try:
                        _rp = Path(parsed.deck_slides_json).parent / (
                            "transcript_rows.json"
                        )
                        _deck_transcript_rows = json.loads(
                            _rp.read_text(encoding="utf-8")
                        )
                    except (OSError, json.JSONDecodeError, ValueError):
                        pass

                json_path = Path(parsed.deck_slides_json)
                if (
                    parsed.deck_confirm == "regenerate"
                    and parsed.deck_revision_round > _MAX_REVISION_ROUNDS
                ):
                    yield {
                        "event": "deck_rejected",
                        "data": {
                            "error": f"幻灯片修改轮数超限（≤{_MAX_REVISION_ROUNDS}），"
                                     "请 approve 当前版或换主题重试",
                        },
                        "needs_confirmation": False,
                        "message": "deck 修改轮数超限",
                    }
                    return

                deck_pngs: list[Path] = []
                if parsed.deck_confirm == "approve":
                    existing = sorted(json_path.parent.glob("slide_*.png"))
                    # 10.25 live7 实证：deck_auto 目录可能残留上一轮旧
                    # PNG（页数变更时 glob 会吞进多页）→ 张数与 slides.json
                    # 权威页数不符时弃 glob，确定性重渲染（零 LLM）
                    want = 0
                    try:
                        want = len(json.loads(
                            json_path.read_text(encoding="utf-8")
                        ).get("slides") or [])
                    except (OSError, json.JSONDecodeError):
                        pass
                    if existing and want and len(existing) == want:
                        deck_pngs = existing
                    if not deck_pngs:
                        deck_pngs = await asyncio.to_thread(
                            render_deck_from_slides, json_path,
                            json_path.parent,
                            top_right_clear_px=_deck_top_clear_px(parsed),
                        )
                    try:
                        deck_ready_theme = json.loads(
                            json_path.read_text(encoding="utf-8")
                        ).get("theme") or "midnight"
                    except (OSError, json.JSONDecodeError):
                        deck_ready_theme = "midnight"
                elif parsed.deck_confirm == "retheme":
                    deck_pngs = await asyncio.to_thread(
                        render_deck_from_slides, json_path,
                        json_path.parent / f"render_{parsed.deck_theme}",
                        parsed.deck_theme,
                        top_right_clear_px=_deck_top_clear_px(parsed),
                    )
                    deck_ready_theme = parsed.deck_theme
                elif parsed.deck_confirm == "regenerate":
                    try:
                        first = json.loads(
                            json_path.read_text(encoding="utf-8")
                        )
                        prev_transcript = str(first.get("transcript") or "")
                    except (OSError, json.JSONDecodeError):
                        prev_transcript = ""
                    deck_pngs = await generate_deck_slides(
                        prev_transcript,
                        self._resolve_llm(),
                        json_path.parent,
                        max_slides=parsed.deck_max_slides,
                        width=1080, height=1920,
                        person_clearance_px=640 if parsed.matting else 0,
                        theme=parsed.deck_theme,
                        feedback=parsed.deck_revision,
                        top_right_clear_px=_deck_top_clear_px(parsed),
                    )
                    deck_ready_theme = parsed.deck_theme

                auto_deck_images = [str(p) for p in deck_pngs]
                if auto_deck_images:
                    yield {
                        "event": "deck_ready",
                        "data": {
                            "count": len(auto_deck_images),
                            "theme": deck_ready_theme,
                            "confirm": parsed.deck_confirm,
                        },
                        "needs_confirmation": False,
                        "message": (
                            f"deck 回调（{parsed.deck_confirm}）完成 "
                            f"{len(auto_deck_images)} 张（{deck_ready_theme}）"
                        ),
                    }
                else:
                    yield {
                        "event": "deck_fallback",
                        "data": {
                            "reason": f"deck_confirm={parsed.deck_confirm} "
                                      "未产出幻灯片，主画面回退纯色底",
                        },
                        "needs_confirmation": False,
                        "message": "deck 回调未产出幻灯片，主画面回退纯色底",
                    }

            # ── 10.21 auto_deck 一键：无 deck_images 时自动产幻灯片 ──
            # 转录文本：已知 transcript 优先（上游提供原文，whisper 对
            # TTS 音频误听率高）→ whisper 兜底跑独立子进程（ct2 构造即
            # 污染进程 DLL，同进程 RVM torch conv 必崩，10.20 实证）。
            # LLM 失败/空 → deck_fallback 事件 → 纯色底主画面，不阻断。
            # 10.22：产物落 scope_dir/deck_auto（持久化 — 闸门回调轮
            # 与 approve 跨 execute 消费）。
            elif parsed.auto_deck and not parsed.deck_images:
                transcript_text = ""
                if parsed.transcript:
                    transcript_text = "\n".join(
                        str(s.get("text") or "") for s in parsed.transcript
                    ).strip()
                else:
                    rows = await asyncio.to_thread(
                        _transcribe_cpu_subprocess, parsed.video_path,
                    )
                    _deck_transcript_rows = list(rows)
                    transcript_text = "\n".join(
                        str(r.get("text") or "") for r in rows
                    ).strip()

                deck_pngs: list[Path] = []
                if transcript_text:
                    from capabilities.video.services.deck_orchestrator import (
                        generate_deck_slides,
                    )

                    deck_pngs = await generate_deck_slides(
                        transcript_text,
                        self._resolve_llm(),
                        perm_dir / "deck_auto",
                        max_slides=parsed.deck_max_slides,
                        width=1080, height=1920,
                        # matting 时人物窗在右下角，deck 底部让位（10.20 同款）
                        person_clearance_px=640 if parsed.matting else 0,
                        theme=parsed.deck_theme,
                        top_right_clear_px=_deck_top_clear_px(parsed),
                    )

                if deck_pngs:
                    auto_deck_images = [str(p) for p in deck_pngs]
                    # 10.25-E：theme=auto 时回读 LLM 实际选中主题（事件报真值）
                    try:
                        deck_ready_theme = json.loads(
                            (perm_dir / "deck_auto" / "slides.json")
                            .read_text(encoding="utf-8")
                        ).get("theme") or deck_ready_theme
                    except (OSError, json.JSONDecodeError):
                        pass
                    # 10.23-D：句时间戳持久化 — 闸门回调轮（approve/
                    # retheme/regenerate）不传 transcript，回读此文件
                    # 才能保持句边界对齐（否则停点确认把对齐废掉）
                    if _deck_transcript_rows:
                        try:
                            _d = perm_dir / "deck_auto"
                            _d.mkdir(parents=True, exist_ok=True)
                            (_d / "transcript_rows.json").write_text(
                                json.dumps(
                                    _deck_transcript_rows,
                                    ensure_ascii=False,
                                ),
                                encoding="utf-8",
                            )
                        except OSError:
                            pass
                else:
                    yield {
                        "event": "deck_fallback",
                        "data": {
                            "reason": "auto_deck 未产出幻灯片（转录/LLM 不可用），"
                                      "主画面回退纯色底",
                        },
                        "needs_confirmation": False,
                        "message": "自动幻灯片生成失败，主画面回退纯色底",
                    }

            # ── 10.22 deck_ready 统一发 + 闸门停点（仅首轮；回调轮已在
            # 上面的分支内发过 deck_ready）──
            if auto_deck_images and not parsed.deck_confirm:
                yield {
                    "event": "deck_ready",
                    "data": {
                        "count": len(auto_deck_images),
                        "theme": deck_ready_theme,
                    },
                    "needs_confirmation": False,
                    "message": (
                        f"auto_deck 生成 {len(auto_deck_images)} 张幻灯片"
                        f"（{deck_ready_theme}）"
                    ),
                }
                # 闸门：deck_preview_ready 事件总是广播（对齐 overlay
                # 主线惯例 — auto_confirm 下事件照发不停）；仅首轮 +
                # 非 AUTO_CONFIRM 才真停等确认。回调轮不再进闸门。
                if not parsed.deck_confirm:
                    needs_gate = (
                        parsed.gate_mode != GateMode.AUTO_CONFIRM
                    )
                    yield {
                        "event": "deck_preview_ready",
                        "data": {
                            "needs_confirmation": needs_gate,
                            "slides_paths": auto_deck_images,
                            "slides_json_path": str(
                                perm_dir / "deck_auto" / "slides.json"
                            ),
                            "theme": deck_ready_theme,
                            "count": len(auto_deck_images),
                        },
                        "needs_confirmation": needs_gate,
                        "message": (
                            f"幻灯片初稿就绪（{len(auto_deck_images)} 张，"
                            f"{deck_ready_theme}）— 满意回复 approve 继续，"
                            "换主题回 retheme+deck_theme，改内容回 "
                            "regenerate+deck_revision"
                        ) if needs_gate else (
                            f"幻灯片就绪（{len(auto_deck_images)} 张，"
                            f"{deck_ready_theme}），无人值守直通合成"
                        ),
                    }
                    if needs_gate:
                        return

            if _emit:
                await _emit(2, "plan", "演示素材规划就绪")
            if parsed.deck_images or auto_deck_images:
                # 10.20：配图直进幻灯片管线（跳过 deck_html 截图）。
                # 主画面画布取首张配图分辨率（配图多为竖版 9:16，
                # 沿用横版 1920x1080 会把竖版图压成黑边小条）。
                # 10.21：图源 = 显式 deck_images 或 auto_deck 产物。
                deck_source = parsed.deck_images or auto_deck_images
                images = [p for p in deck_source if Path(p).is_file()]
                if not images:
                    yield {
                        "success": False,
                        "error": f"deck_images 全部缺失: {deck_source}",
                    }
                    return
                if parsed.overlay_decor:
                    # 10.30：装饰叠加分叉 — 原片全屏 + 素材卡叠加，
                    # 不生成幻灯片主画面、不抠像缩窗
                    _sj = (
                        Path(parsed.deck_slides_json)
                        if parsed.deck_slides_json
                        else perm_dir / "deck_auto" / "slides.json"
                    )
                    async for _c in self._finalize_decor(
                        parsed, video_path, duration, scope_dir, tmp_dir,
                        images=images, slides_json=_sj,
                        transcript_rows=_deck_transcript_rows,
                    ):
                        yield _c
                    return
                if _emit:
                    await _emit(3, "render", "采集幻灯片主画面")
                yield {
                    "event": "pip_capturing_slides",
                    "data": {"count": len(images), "source": "deck_images"},
                    "message": f"使用 {len(images)} 张配图作幻灯片主画面",
                }
                try:
                    from capabilities.video.adapters.person_matting import (
                        _probe_image_size,
                    )
                    main_width, main_height = await asyncio.to_thread(
                        _probe_image_size, images[0],
                    )
                except Exception:
                    pass  # 探测失败回退默认画布

                try:
                    # 10.23-D：auto_deck 产物走句边界对齐（切换点跟讲解
                    # 句走）；手动 deck_images 无此约定 → 均分
                    # N1：触发翻页优先（说到才翻，M2 同源）→ miss 回退句边界
                    boundary_durs = None
                    _slide_defs = None
                    if auto_deck_images:
                        _slide_defs = _load_deck_slides(
                            perm_dir / "deck_auto" / "slides.json",
                        )
                    elif parsed.deck_triggers:
                        _slide_defs = [
                            {"trigger": {"text": t}}
                            for t in parsed.deck_triggers
                        ]
                    if _slide_defs and _deck_transcript_rows:
                        _hit = _trigger_slide_starts(
                            _deck_transcript_rows, _slide_defs, duration,
                        )
                        if _hit is not None:
                            t0_eff, boundary_durs = _hit
                    if boundary_durs is None:
                        boundary_durs = (
                            _slide_durations_from_transcript(
                                _deck_transcript_rows, len(images), duration,
                            ) or None
                        ) if auto_deck_images and not parsed.deck_images else None
                        if t0_eff is None and _deck_transcript_rows:
                            # 让位时刻降级：首句起点（找不到 → 3.0s 缺省）
                            try:
                                _s0 = float(
                                    _deck_transcript_rows[0].get("start") or 0
                                )
                                if 0 < _s0 < duration:
                                    t0_eff = _s0
                            except (TypeError, ValueError, IndexError):
                                pass
                    if t0_eff is None:
                        t0_eff = 3.0
                    tw_dirs: list[Path] | None = None
                    layers_pages: list[list[dict[str, Any]]] | None = None
                    if boundary_durs is not None:
                        # N6/N5：触发翻页/句边界链 → 打字机流式 → 元素
                        # 分层 → 整页（三级降级，渲染失败不阻断出片）
                        _sl_data: dict[str, Any] | None = None
                        try:
                            _sl_data = json.loads(
                                Path(parsed.deck_slides_json).read_text(
                                    encoding="utf-8"
                                )
                            )
                        except Exception:
                            _sl_data = None
                        _slides_list = (
                            (_sl_data or {}).get("slides") or []
                        )
                        if _slides_list and len(_slides_list) == len(images):
                            try:
                                from capabilities.video.services.deck_orchestrator import (
                                    render_deck_typewriter,
                                )

                                tw_dirs = await asyncio.to_thread(
                                    render_deck_typewriter,
                                    _slides_list, tmp_dir / "deck_tw",
                                    boundary_durs,
                                    width=main_width, height=main_height,
                                    person_clearance_px=(
                                        640 if parsed.matting else 0
                                    ),
                                    theme=(
                                        (_sl_data or {}).get("theme")
                                        or "midnight"
                                    ),
                                    top_right_clear_px=_deck_top_clear_px(
                                        parsed
                                    ),
                                )
                            except Exception as _te:
                                logger.warning(
                                    "deck typewriter render failed, "
                                    "fallback layers: %s", _te,
                                )
                                tw_dirs = None
                        if tw_dirs is None:
                            try:
                                from capabilities.video.services.deck_orchestrator import (
                                    render_deck_layers,
                                )

                                layers_pages = await asyncio.to_thread(
                                    render_deck_layers,
                                    _slides_list,
                                    tmp_dir / "deck_layers",
                                    width=main_width, height=main_height,
                                    person_clearance_px=(
                                        640 if parsed.matting else 0
                                    ),
                                    theme=(
                                        (_sl_data or {}).get("theme")
                                        or "midnight"
                                    ),
                                    top_right_clear_px=_deck_top_clear_px(
                                        parsed
                                    ),
                                )
                            except Exception as _le:
                                logger.warning(
                                    "deck layers render failed, "
                                    "fallback whole-page: %s", _le,
                                )
                                layers_pages = None
                    await self._slides_to_video(
                        [Path(p) for p in images], tmp_dir / "slides.mp4",
                        slide_duration=parsed.slide_duration,
                        width=main_width, height=main_height,
                        durations=boundary_durs,
                        frames=tw_dirs,
                        layers=layers_pages,
                    )
                except Exception as e:
                    yield {"success": False, "error": f"slides video failed: {e}"}
                    return
                main_video_path = tmp_dir / "slides.mp4"
            elif parsed.deck_html:
                deck_path = Path(parsed.deck_html)
                if not deck_path.is_file():
                    yield {"success": False, "error": f"deck_html not found: {parsed.deck_html}"}
                    return

                if _emit:
                    await _emit(3, "render", "截取 PPT 幻灯片")
                yield {
                    "event": "pip_capturing_slides",
                    "data": {"deck_html": str(deck_path)},
                    "message": "正在截取 PPT 幻灯片...",
                }

                try:
                    screenshots = await self._capture_slides(
                        deck_path, tmp_dir, main_width, main_height,
                    )
                except Exception as e:
                    yield {"success": False, "error": f"slide capture failed: {e}"}
                    return

                if not screenshots:
                    yield {"success": False, "error": "no slides captured from deck_html"}
                    return

                yield {
                    "event": "pip_slides_captured",
                    "data": {"count": len(screenshots)},
                    "message": f"截取了 {len(screenshots)} 页幻灯片",
                }

                slides_video = tmp_dir / "slides.mp4"
                try:
                    await self._slides_to_video(
                        screenshots, slides_video,
                        slide_duration=parsed.slide_duration,
                        width=main_width, height=main_height,
                    )
                except Exception as e:
                    yield {"success": False, "error": f"slides video failed: {e}"}
                    return

                main_video_path = slides_video
            else:
                try:
                    main_width, main_height = await self._probe_resolution(str(video_path))
                except Exception as e:
                    yield {"success": False, "error": f"cannot probe video resolution: {e}"}
                    return

            pip_info = await self._probe_resolution(str(video_path))
            pip_orig_w, pip_orig_h = pip_info
            pip_w = int(main_width * parsed.pip_scale)
            pip_h = int(pip_orig_h * pip_w / pip_orig_w) if pip_orig_w else int(main_height * parsed.pip_scale)
            margin = parsed.pip_margin

            pos_map = {
                PipPosition.BOTTOM_RIGHT: (main_width - pip_w - margin, main_height - pip_h - margin),
                PipPosition.BOTTOM_LEFT: (margin, main_height - pip_h - margin),
                PipPosition.TOP_RIGHT: (main_width - pip_w - margin, margin),
                PipPosition.TOP_LEFT: (margin, margin),
            }
            overlay_x, overlay_y = pos_map.get(parsed.pip_position, pos_map[PipPosition.BOTTOM_RIGHT])

            # 产物隔离：口播演示成片按「PPT + 画中画参数 + 画布形态」取指纹，
            # 避免不同作品的成片同名覆盖。
            # 10.23-C：形态段进指纹 — live3b（fallback 纯色底）曾以相同
            # 输入指纹覆盖 live3（deck 全链）同路径产物。
            if auto_deck_images:
                _deck_shape = f"deckauto{deck_ready_theme}{len(auto_deck_images)}"
            elif parsed.deck_images:
                _deck_shape = "deckmanual"
            elif parsed.auto_deck:
                _deck_shape = "deckfallback"
            else:
                _deck_shape = "plainpip"
            _pip_key = hashlib.md5(
                f"{parsed.deck_html or ''}|{parsed.deck_images or ''}|{parsed.pip_position.value}|{parsed.pip_scale}|{parsed.pip_margin}|{parsed.matting}|{parsed.auto_deck}|{parsed.deck_theme}|{_deck_shape}".encode("utf-8")
            ).hexdigest()[:8]
            output_path = perm_dir / f"{video_path.stem}_pip_presentation_{_pip_key}{video_path.suffix}"

            # ── 10.20 人物抠像：RVM alpha 融合（失败降级矩形小窗）──
            matte = None
            if parsed.matting:
                from capabilities.video.adapters.person_matting import matte_person
                matte = await matte_person(str(video_path), tmp_dir / "matte")
                if matte is None:
                    yield {
                        "event": "pip_matting_fallback",
                        "data": {"reason": "抠像不可用（torch/权重/GPU），回退矩形小窗"},
                        "needs_confirmation": False,
                        "message": "人物抠像不可用，已回退矩形小窗",
                    }

            bg_color_hex = parsed.bg_color.lstrip("#")
            if len(bg_color_hex) != 6:
                bg_color_hex = "1a1a2e"

            handoff_plan = None
            if parsed.handoff and t0_eff:
                handoff_plan = _handoff_plan(t0_eff, duration)

            if matte is not None:
                # alpha 融合：主画面（幻灯片/纯色底）+ 人物 rgba 帧序列 +
                # 原视频作音频源。人物尺寸按主画面宽度 × pip_scale 缩放。
                pip_w = int(main_width * parsed.pip_scale)
                pip_h = (
                    int(matte.height * pip_w / matte.width)
                    if matte.width else int(main_height * parsed.pip_scale)
                )
                matte_pos = {
                    PipPosition.BOTTOM_RIGHT: (main_width - pip_w - margin, main_height - pip_h - margin),
                    PipPosition.BOTTOM_LEFT: (margin, main_height - pip_h - margin),
                    PipPosition.TOP_RIGHT: (main_width - pip_w - margin, margin),
                    PipPosition.TOP_LEFT: (margin, margin),
                }
                overlay_x, overlay_y = matte_pos.get(
                    parsed.pip_position, matte_pos[PipPosition.BOTTOM_RIGHT],
                )
                cmd = _pip_full_cmd(
                    main_video_path,
                    matte.fps, matte.frame_dir, video_path,
                    main_width, main_height, pip_w, pip_h,
                    overlay_x, overlay_y, duration, bg_color_hex,
                    handoff_plan["t0"] if handoff_plan else None,
                    output_path,
                )
            elif main_video_path:
                filter_complex = (
                    f"[1:v]scale={pip_w}:{pip_h}:flags=lanczos[pip_scaled];"
                    f"[0:v][pip_scaled]overlay={overlay_x}:{overlay_y}:format=auto[out_v]"
                )
                cmd = [
                    "ffmpeg", "-y",
                    "-i", str(main_video_path),
                    "-i", str(video_path),
                    "-filter_complex", filter_complex,
                    "-map", "[out_v]",
                    "-map", "0:a?", "-map", "1:a?",
                    "-c:v", "libx264", "-preset", "fast",
                    "-c:a", "aac", "-b:a", "128k",
                    "-pix_fmt", "yuv420p",
                    "-shortest",
                    str(output_path),
                ]
            else:
                filter_complex = (
                    f"color=c=0x{bg_color_hex}:s={main_width}x{main_height}:d={duration}:r=30[bg];"
                    f"[0:v]scale={pip_w}:{pip_h}:flags=lanczos[pip];"
                    f"[bg][pip]overlay={overlay_x}:{overlay_y}:format=auto"
                )
                cmd = [
                    "ffmpeg", "-y",
                    "-i", str(video_path),
                    "-filter_complex", filter_complex,
                    "-map", "0:a?",
                    "-c:v", "libx264", "-preset", "fast",
                    "-c:a", "aac", "-b:a", "128k",
                    "-pix_fmt", "yuv420p",
                    "-shortest",
                    str(output_path),
                ]

            try:
                from capabilities.video.skills.smart_cut import _run_ffmpeg
                if handoff_plan is not None and matte is not None:
                    # N1：segB 全时长 PIP → 段A（原片全屏）/段B（t0 截段）
                    # → xfade 溶解（人让位给内容）+ acrossfade
                    full_out = tmp_dir / "pip_full.mp4"
                    await _run_ffmpeg([*cmd[:-1], str(full_out)])
                    seg_a = tmp_dir / "handoff_a.mp4"
                    seg_b = tmp_dir / "handoff_b.mp4"
                    await _run_ffmpeg([
                        "ffmpeg", "-y", "-i", str(video_path),
                        "-t", f"{handoff_plan['segA'][1]:.3f}",
                        "-vf", (
                            f"scale={main_width}:{main_height}:"
                            "force_original_aspect_ratio=decrease,"
                            f"pad={main_width}:{main_height}:(ow-iw)/2:"
                            f"(oh-ih)/2:color=0x{bg_color_hex}"
                        ),
                        "-c:v", "libx264", "-preset", "fast",
                        "-c:a", "aac", "-b:a", "128k",
                        "-pix_fmt", "yuv420p", str(seg_a),
                    ])
                    await _run_ffmpeg([
                        "ffmpeg", "-y", "-ss", f"{handoff_plan['t0']:.3f}",
                        "-i", str(full_out),
                        "-c:v", "libx264", "-preset", "fast",
                        "-c:a", "aac", "-b:a", "128k",
                        "-pix_fmt", "yuv420p", str(seg_b),
                    ])
                    await _run_ffmpeg(_handoff_cmd(
                        seg_a, seg_b, handoff_plan["t0"],
                        handoff_plan["td"], output_path,
                    ))
                else:
                    await _run_ffmpeg(cmd)
            except Exception as e:
                yield {"success": False, "error": f"ffmpeg pip compose failed: {e}"}
                return

        if _emit:
            await _emit(6, "render", "口播演示合成完成", str(output_path))
        if output_path.is_file():
            yield {
                "event": "pip_done",
                "data": {
                    "output_path": str(output_path),
                    "pip_size": f"{pip_w}x{pip_h}",
                    "pip_position": f"({overlay_x},{overlay_y})",
                    "canvas_size": f"{main_width}x{main_height}",
                    "has_deck": bool(parsed.deck_html or parsed.deck_images or auto_deck_images),
                    "matting": matte is not None,
                    "handoff": (
                        {"t0": round(handoff_plan["t0"], 3)}
                        if handoff_plan else None
                    ),
                },
                "needs_confirmation": False,
                "message": (
                    f"口播演示合成完成：{pip_w}x{pip_h} 小窗在 {parsed.pip_position.value}"
                    + ("，主画面=PPT幻灯片" if parsed.deck_html else "")
                ),
            }
        else:
            yield {"success": False, "error": "output file not created"}

    async def _finalize_decor(
        self,
        parsed: OverlayInput,
        body_video: Path,
        duration: float,
        scope_dir: Path,
        tmp_dir: Path,
        *,
        images: list[str],
        slides_json: Path,
        transcript_rows: list[dict[str, Any]],
    ) -> Any:
        """10.30 装饰叠加合成：原片全屏 + 素材卡按句边界进出。

        与 pip 合成分叉：不生成幻灯片主画面、不抠像缩窗——原片音画
        不动；素材卡（slides.json 内容 + image 配图横幅重排）在句
        边界页区间内淡入淡出，顶部居中不遮人物头部。
        """
        from capabilities.video.services.deck_orchestrator import (
            render_decor_cards,
        )
        from capabilities.video.skills.smart_cut import _run_ffmpeg

        yield {
            "event": "decor_start",
            "data": {"count": len(images), "slides_json": str(slides_json)},
            "message": (
                f"装饰叠加模式：原片全屏，{len(images)} 张素材卡按口播节奏进出"
            ),
        }
        try:
            deck_data = json.loads(slides_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            yield {"success": False,
                   "error": f"decor slides.json 不可读: {e}"}
            return
        slides = deck_data.get("slides") or []
        n = min(len(slides), len(images))
        if n == 0:
            yield {"success": False, "error": "decor: 无可用素材页"}
            return
        slides = slides[:n]
        theme = str(deck_data.get("theme") or "midnight")

        # 时间窗：句边界页区间优先（切卡跟讲解句走），否则均分
        durs = (
            _slide_durations_from_transcript(transcript_rows, n, duration)
            if transcript_rows else None
        )
        if not durs or len(durs) != n:
            durs = [duration / n] * n
        page_bounds: list[tuple[float, float]] = []
        acc = 0.0
        for d in durs:
            page_bounds.append((acc, min(duration, acc + float(d))))
            acc += float(d)
        # M2-P0-2：触发式卡窗 — 词流搜 trigger.text 首次出现，卡从说到
        # 那一刻弹入；无 trigger/搜不到/停留过短 → 回退句边界（绝不阻断）
        bounds = _trigger_bounds(slides, transcript_rows, page_bounds, duration)
        # M3-P1-5：强调 zoom — emphasis 契约词流定位（≤5 处，搜不到不加）
        zoom_windows = _zoom_windows(slides, transcript_rows, duration)

        cards = await asyncio.to_thread(
            render_decor_cards, slides, tmp_dir / "decor", theme,
        )
        if len(cards) != n:
            yield {"success": False,
                   "error": f"decor: 卡渲染数 {len(cards)} != 页数 {n}"}
            return

        # 10.31 M1-P0-1：词级 karaoke 字幕 — 词级数据缺失 → 跳过字幕层
        # （降级不阻断）；渲染/清单均 to_thread（Playwright 同步 API）。
        # 字幕需在 stamp 前定案：缓存 key 含有无字幕，避免结果键冲突；
        # 带宽=主片宽（源片可能非 720，overlay 居中不裁字）。
        try:
            main_w, main_h = await self._probe_resolution(str(body_video))
        except Exception:
            main_w, main_h = 720, 1280
        kao_frames = _karaoke_frame_plan(transcript_rows, duration)
        subtitle: Path | None = None
        if kao_frames:
            kao_dir = tmp_dir / "karaoke"
            kao_pngs = await asyncio.to_thread(
                _render_karaoke_frames,
                kao_frames, transcript_rows, kao_dir, main_w,
            )
            if kao_pngs:
                subtitle = await asyncio.to_thread(
                    _write_ffconcat, kao_frames, kao_pngs, kao_dir,
                )
        # M2-P1-4：SFX — 卡入场时刻混短音效（无音轨/缺文件降级直通）
        sfx_events: list[dict[str, Any]] = []
        try:
            has_audio = await self._probe_has_audio(str(body_video))
        except Exception:
            has_audio = False
        if has_audio:
            sfx_events = _plan_sfx(bounds, duration)
        stamp = hashlib.md5(
            (
                str(slides_json) + str(bounds) + str(subtitle is not None)
                + str(parsed.decor_effect)
                + str([(e["name"], e["t"]) for e in sfx_events])
                + str(zoom_windows)
            ).encode()
        ).hexdigest()[:8]
        out = scope_dir / f"decor_{stamp}.mp4"
        try:
            fps = int(round(await _probe_video_fps(str(body_video))))
        except Exception:
            fps = 0
        fps = fps if 12 <= fps <= 60 else 30
        cmd, _meta = _build_decor_cmd(
            body_video, cards, bounds, duration, main_w, main_h,
            scale=float(parsed.decor_scale),
            effect=str(parsed.decor_effect),
            subtitle_ffconcat=subtitle,
            sfx_events=sfx_events,
            zoom_windows=zoom_windows,
            fps=fps,
            out_path=out,
        )
        try:
            await _run_ffmpeg(cmd)
        except Exception as e:
            yield {"success": False, "error": f"ffmpeg decor failed: {e}"}
            return
        if not out.is_file():
            yield {"success": False, "error": "decor output not created"}
            return
        sfx_names = [e["name"] for e in sfx_events]
        yield {
            "event": "decor_done",
            "data": {
                "output_path": str(out),
                "cards": n,
                "windows": [[a, b] for a, b in bounds],
                "effect": str(parsed.decor_effect),
                "karaoke": bool(subtitle),
                "sfx": sfx_names,
                "zoom": zoom_windows,
            },
            "needs_confirmation": False,
            "message": (
                f"装饰叠加完成：{n} 张素材卡触发式弹入"
                + (
                    f"，词级 karaoke {len(kao_frames)} 帧"
                    if subtitle else ""
                )
                + (f"，SFX {len(sfx_names)} 处" if sfx_names else "")
                + (
                    f"，强调 zoom {len(zoom_windows)} 处"
                    if zoom_windows else ""
                )
                + f"，成片 {out.name}"
            ),
        }

    async def _probe_resolution(self, video_path: str) -> tuple[int, int]:
        """获取视频分辨率 — PyAV 优先（本机 ffprobe 不在 PATH），ffprobe 兜底。"""
        try:
            import av

            def _av_probe() -> tuple[int, int]:
                with av.open(video_path) as container:
                    for stream in container.streams:
                        if stream.type == "video":
                            return (
                                stream.codec_context.width,
                                stream.codec_context.height,
                            )
                raise ValueError("no video stream found")

            return await asyncio.to_thread(_av_probe)
        except Exception:
            pass

        import json as _json
        proc = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_streams", video_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        info = _json.loads(stdout)
        for stream in info.get("streams", []):
            if stream.get("codec_type") == "video":
                return int(stream["width"]), int(stream["height"])
        raise ValueError("no video stream found")

    async def _probe_has_audio(self, video_path: str) -> bool:
        """探测音轨 — PyAV 优先，ffprobe 兜底；探测失败 False
        （SFX 层整体降级直通，绝不阻断）。"""
        try:
            import av

            def _av_check() -> bool:
                with av.open(video_path) as container:
                    return any(
                        s.type == "audio" for s in container.streams
                    )

            return await asyncio.to_thread(_av_check)
        except Exception:
            pass
        import json as _json
        try:
            proc = await asyncio.create_subprocess_exec(
                "ffprobe", "-v", "quiet", "-print_format", "json",
                "-show_streams", video_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            info = _json.loads(stdout)
            return any(
                s.get("codec_type") == "audio"
                for s in info.get("streams", [])
            )
        except Exception:
            return False

    async def _capture_slides(
        self,
        deck_html: Path,
        out_dir: Path,
        width: int = 1920,
        height: int = 1080,
    ) -> list[Path]:
        """用 Playwright 逐页截图 PPT 幻灯片。"""
        from playwright.async_api import async_playwright

        screenshots: list[Path] = []
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page(viewport={"width": width, "height": height})
            await page.goto(deck_html.resolve().as_uri(), wait_until="networkidle")
            await page.wait_for_timeout(1500)

            deck = await page.query_selector("deck-stage")
            if not deck:
                await browser.close()
                return []

            slides = await deck.query_selector_all(".slide")
            for i, slide in enumerate(slides):
                await page.evaluate(
                    """([deckEl, idx]) => {
                        const slides = deckEl.querySelectorAll('.slide');
                        slides.forEach((s, j) => {
                            s.classList.toggle('active', j === idx);
                            s.style.display = j === idx ? '' : 'none';
                            s.style.opacity = j === idx ? '1' : '0';
                        });
                    }""",
                    [deck, i],
                )
                await page.wait_for_timeout(600)
                out_path = out_dir / f"slide_{i:03d}.png"
                await page.screenshot(path=str(out_path), full_page=False)
                screenshots.append(out_path)

            await browser.close()

        return screenshots

    async def _slides_to_video(
        self,
        screenshots: list[Path],
        output_path: Path,
        slide_duration: float = 5.0,
        fps: int = 30,
        width: int = 1920,
        height: int = 1080,
        durations: list[float] | None = None,
        frames: list[Path] | None = None,
        layers: list[list[dict[str, Any]]] | None = None,
    ) -> Path:
        """用 ffmpeg 把截图序列合成幻灯片视频。

        durations（10.23-D）：句边界模式逐页时长（连续覆盖全片，切换点
        跟讲解句边界）；None → 旧行为 slide_duration 均分。句边界模式
        不加 0.8 交叉余量（每页累计漂移会吃掉对齐精度）。
        frames（N6）：打字机帧序列目录 — 逐字流式，优先于层/整页。
        """
        if not screenshots:
            raise ValueError("No screenshots")

        # N6：打字机帧序列优先（文字逐字流式，动画已在帧里）
        if frames:
            cmd, _meta = _build_typewriter_cmd(
                frames, output_path,
                durations or [slide_duration] * len(frames),
                fps=fps, width=width, height=height,
            )
            from capabilities.video.skills.smart_cut import _run_ffmpeg
            await _run_ffmpeg(cmd)
            return output_path

        # N5：分层动画优先（层定义给定 → 元素级入场动画）
        if layers:
            cmd, _meta = _build_layers_cmd(
                layers, output_path,
                durations or [slide_duration] * len(layers),
                fps=fps, width=width, height=height,
            )
            from capabilities.video.skills.smart_cut import _run_ffmpeg
            await _run_ffmpeg(cmd)
            return output_path

        # 10.25-F：句边界模式（durations 给定）多页 → xfade 转场链；
        # 单页 / durations=None（手动 deck_images 均分）保持旧 concat 硬切
        if durations and len(screenshots) > 1:
            cmd, _meta = _build_xfade_cmd(
                screenshots, output_path, durations,
                fps=fps, width=width, height=height,
            )
            from capabilities.video.skills.smart_cut import _run_ffmpeg
            await _run_ffmpeg(cmd)
            return output_path

        concat_file = output_path.parent / "concat.txt"
        with open(concat_file, "w", encoding="utf-8") as f:
            for i, ss in enumerate(screenshots):
                if durations and i < len(durations):
                    dur = durations[i]
                else:
                    dur = slide_duration + (0.8 if i > 0 else 0)
                f.write(f"file '{ss.as_posix()}'\n")
                f.write(f"duration {dur}\n")
            f.write(f"file '{screenshots[-1].as_posix()}'\n")
            f.write("duration 0.1\n")

        cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0", "-i", str(concat_file),
            "-vf", f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=#1a1a2e,format=yuv420p",
            "-c:v", "libx264", "-preset", "fast",
            "-r", str(fps),
            "-pix_fmt", "yuv420p",
            str(output_path),
        ]

        # 走 _run_ffmpeg（内含 ffmpeg 路径解析）— 裸 "ffmpeg" 在本机
        # 不在 PATH（WinError 2，与 _probe_resolution 注释同源）
        from capabilities.video.skills.smart_cut import _run_ffmpeg
        await _run_ffmpeg(cmd)

        concat_file.unlink(missing_ok=True)
        return output_path

    async def _build_timed_words(
        self,
        parsed: OverlayInput,
        duration: float,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """转录通道：已知文本时间轴优先（跳过 whisper），whisper 兜底。

        Returns:
            (seg_dicts, timed_words) — timed_words 为全片词级时间戳
            （源时间轴），供渲染阶段做"图形落在词上"对齐（OVE 规则 1）；
            seg_dicts 为转录片段，P0 视觉自审闭环带反馈重规划时复用
            （不重复跑 whisper）。
        """
        import asyncio

        from capabilities.video.services.overlay_orchestrator import TimeMapper

        # 已知文本时间轴（内嵌路径上游提供原文）→ 跳过 whisper：
        # TTS 合成音频 whisper 误听率高（"90%"→"九十辈子"），原文更准更快
        if parsed.transcript:
            seg_dicts = [
                {
                    "index": i,
                    "start": float(s["start"]),
                    "end": float(s["end"]),
                    "text": str(s.get("text") or ""),
                    "words": [
                        {"text": str(w["text"]), "start": float(w["start"]), "end": float(w["end"])}
                        for w in (s.get("words") or [])
                    ],
                }
                for i, s in enumerate(parsed.transcript)
                if s.get("end", 0) > s.get("start", 0)
            ]
            logger.info(
                f"使用上游已知文本时间轴（{len(seg_dicts)} 段），跳过 whisper 转录"
            )
        else:
            from capabilities.video.adapters.whisper_transcribe import transcribe

            # to_thread：whisper 转录是重 CPU/GPU 同步调用，直跑会阻塞事件循环
            segments = await asyncio.to_thread(
                transcribe, parsed.video_path, model_size="small", language="zh",
            )

            if not segments:
                return [], []

            # 词级时间戳进规划输入（LLM 按词对齐叠加起点，OVE 规则 1）
            seg_dicts = [
                {
                    "index": i,
                    "start": s.start,
                    "end": s.end,
                    "text": s.text,
                    "words": [
                        {"text": w.text, "start": w.start, "end": w.end}
                        for w in s.words
                    ],
                }
                for i, s in enumerate(segments)
            ]

        # 全片词级流（源时间轴）→ 渲染阶段使用
        timed_words = [w for s in seg_dicts for w in s["words"]]

        if parsed.kept_segments:
            # 词时间戳映射到剪辑时间轴（被删时刻的词丢弃）；
            # plan 的锚点 remap 统一由 _apply_constraints 做（避免双重映射）
            mapper = TimeMapper(parsed.kept_segments)
            mapped = []
            for w in timed_words:
                m = mapper.map(w["start"])
                if m is not None:
                    mapped.append({
                        "text": w["text"], "start": m,
                        "end": m + (w["end"] - w["start"]),
                    })
            timed_words = mapped

        return seg_dicts, timed_words

    async def _generate_plan(
        self,
        parsed: OverlayInput,
        duration: float,
        tmp_dir: Path,
    ) -> tuple[Any, list[dict[str, Any]], list[dict[str, Any]]]:
        """闸门 1：生成叠加规划。

        Returns:
            (plan, timed_words, seg_dicts)
        """
        seg_dicts, timed_words = await self._build_timed_words(parsed, duration)
        if not seg_dicts:
            return OverlayPlan(video_id=""), [], []

        plan, _ = await generate_overlay_plan(
            seg_dicts, duration, self._resolve_llm(),
            use_two_step=True,  # P2 两步链：先叙事结构分析再落时间轴
        )
        plan = self._apply_constraints(parsed, plan)
        return plan, timed_words, seg_dicts

    async def _revision_plan(
        self,
        parsed: OverlayInput,
        duration: float,
    ) -> tuple[Any, Any, list[dict[str, Any]], list[dict[str, Any]]]:
        """10.19 对话式修改：意图路由 → local 代码级 patch / global 重规划。

        Returns:
            (revision_meta, plan, timed_words, seg_dicts) — plan=None 表示
            解析失败/定位失败/自检不过，meta 为问题清单（上层反问用户）。
        """
        raw = parsed.revision.raw_text
        current_plan = OverlayPlan(**parsed.revision.current_plan)
        seg_dicts, timed_words = await self._build_timed_words(parsed, duration)

        result, problems = await parse_revision_intent(
            self._resolve_llm(), raw, current_plan, duration,
        )
        if result is None:
            return problems, None, [], []

        if result.route == "global":
            # 全局意见 → 走 P0/P2 同款 feedback 重规划，产出过同样约束
            plan, _ = await generate_overlay_plan(
                seg_dicts, duration, self._resolve_llm(),
                feedback=raw, use_two_step=True,
            )
            plan = self._apply_constraints(parsed, plan)
            return (
                {"route": "global", "reason": result.reason},
                plan, timed_words, seg_dicts,
            )

        patched, patch_problems = apply_revision(
            current_plan, result.instructions, duration,
        )
        if patch_problems:
            return patch_problems, None, [], []
        return (
            {
                "route": "local",
                "instructions": [i.model_dump() for i in result.instructions],
            },
            patched, timed_words, seg_dicts,
        )

    @staticmethod
    def _resolve_llm() -> Any:
        """规划用 LLM — 配置缺失/导入失败返回 None（generate_overlay_plan 降级兜底）。"""
        try:
            from app.adapters.deepseek import DeepSeekAdapter
            from app.config import Settings
            return DeepSeekAdapter(api_key=Settings().deepseek_api_key)
        except Exception:
            return None

    @staticmethod
    def _apply_constraints(parsed: OverlayInput, plan: Any) -> Any:
        """规划约束后处理：锚点重映射（串联模式）+ 模板白名单过滤 + 空间冲突消解。

        初次规划与 P0 闭环带反馈重规划共用 — 重规划产出必须过同样的约束。
        """
        from capabilities.video.services.overlay_orchestrator import (
            remap_overlay_anchors,
            resolve_overlay_conflicts,
        )

        if parsed.kept_segments and plan.segments:
            plan = remap_overlay_anchors(plan, parsed.kept_segments)
        if parsed.templates:
            # 显式白名单：gen_* 生成模板一并放行（P1），其余按名单过滤
            from capabilities.video.services.template_generator import GENERATED
            allowed = set(parsed.templates) | set(GENERATED)
            from capabilities.video.services.overlay_orchestrator import template_name
            plan.segments = [
                s for s in plan.segments if template_name(s.template) in allowed
            ]
        return resolve_overlay_conflicts(plan)

    async def _replan_with_feedback(
        self,
        parsed: OverlayInput,
        seg_dicts: list[dict[str, Any]],
        duration: float,
        feedback: str,
    ) -> Any:
        """P0 闭环：带视觉审查反馈重新规划（复用已转录 seg_dicts，不重跑 whisper）。

        Returns: 新 OverlayPlan；失败/空规划返回 None（调用方保留当前渲染）。
        """
        from capabilities.video.services.overlay_orchestrator import generate_overlay_plan

        llm = self._resolve_llm()
        if llm is None:
            return None
        try:
            plan, _ = await generate_overlay_plan(
                seg_dicts, duration, llm, feedback=feedback,
                use_two_step=True,  # P2：反馈也进叙事步做结构层反思
            )
        except Exception as e:
            logger.warning(f"replan with feedback failed: {e}")
            return None
        if not plan.segments:
            return None
        return self._apply_constraints(parsed, plan)

    # P0 视觉自审闭环轮次上限：初审 + 1 次带反馈重渲复审
    _REVIEW_MAX_ROUNDS = 2

    async def _review_loop(
        self,
        state: dict[str, Any],
        seg_dicts: list[dict[str, Any]],
        timed_words: list[dict[str, Any]],
        parsed: OverlayInput,
        duration: float,
        tmp_dir: Path,
        base_w: int,
        base_h: int,
    ):
        """P0 视觉自审闭环 — 渲染预览 → VLM 看图审查 → 带反馈重规划重渲。

        异步生成器：yield overlay_review_done 事件；最终 plan/preview_pairs
        写回 state（异步生成器无法 return 值，state dict 是约定出口）。

        决策先于事件：事件的 will_retry 必须反映真实走向（重规划失败
        不会谎报"将重试"）。降级契约：review_overlays 返回 None（VLM/
        ffmpeg 不可用）→ 0 事件直接返回，绝不阻断出片。
        """
        from capabilities.video.services.overlay_review import (
            build_replan_feedback,
            review_overlays,
        )

        if not state["preview_pairs"]:
            return

        for round_idx in range(1, self._REVIEW_MAX_ROUNDS + 1):
            try:
                result = await review_overlays(
                    state["plan"], state["preview_pairs"], tmp_dir,
                    base_video=parsed.video_path,
                )
            except Exception as e:
                # 双保险：审查本身崩溃也不能拦住出片
                logger.warning(f"overlay review crashed (round {round_idx}): {e}")
                return
            if result is None:
                return

            state["review"] = result
            critical = result.critical_issues

            # 末轮或无 problem 级问题 → 定论出片
            if not critical or round_idx >= self._REVIEW_MAX_ROUNDS:
                yield {
                    "event": "overlay_review_done",
                    "data": {
                        "round": round_idx,
                        "passed": result.passed,
                        "issue_count": len(result.issues),
                        "critical_count": len(critical),
                        "issues": [i.model_dump() for i in result.issues],
                        "will_retry": False,
                    },
                    "needs_confirmation": False,
                    "message": (
                        f"视觉审查通过（{len(result.issues)} 条建议）"
                        if result.passed
                        else f"视觉审查发现 {len(critical)} 个问题，已达复审上限，按当前效果出片"
                    ),
                }
                return

            # 有 problem 且还有预算 → 带反馈重规划；失败则按当前渲染出片
            new_plan = None
            try:
                new_plan = await self._replan_with_feedback(
                    parsed, seg_dicts, duration,
                    build_replan_feedback(result),
                )
            except Exception as e:
                logger.warning(f"replan after review failed: {e}")
            if new_plan is None:
                yield {
                    "event": "overlay_review_done",
                    "data": {
                        "round": round_idx,
                        "passed": result.passed,
                        "issue_count": len(result.issues),
                        "critical_count": len(critical),
                        "issues": [i.model_dump() for i in result.issues],
                        "will_retry": False,
                    },
                    "needs_confirmation": False,
                    "message": "视觉审查发现问题但重规划失败，按当前效果出片",
                }
                return

            new_pairs = await self._render_previews(
                new_plan, timed_words, tmp_dir, base_w, base_h,
                llm=self._resolve_llm(),
            )
            if not new_pairs:
                logger.warning("re-render after review produced nothing, keep current render")
                return

            yield {
                "event": "overlay_review_done",
                "data": {
                    "round": round_idx,
                    "passed": result.passed,
                    "issue_count": len(result.issues),
                    "critical_count": len(critical),
                    "issues": [i.model_dump() for i in result.issues],
                    "will_retry": True,
                },
                "needs_confirmation": False,
                "message": (
                    f"视觉审查发现 {len(critical)} 个问题，"
                    f"带反馈重新规划并重渲（{len(new_pairs)} 个片段）"
                ),
            }
            state["plan"], state["preview_pairs"] = new_plan, new_pairs

    _CAPTION_TEMPLATES = {"caption_pop", "caption_karaoke", "caption_typewriter"}

    # 设计画布短边：模板字号/间距按 1080 宽设计，合成时 scale 回底片尺寸
    _DESIGN_MIN_SIDE = 1080

    @staticmethod
    def _design_canvas(base_w: int, base_h: int) -> tuple[int, int]:
        """渲染画布：与底片同比例、短边归一到 1080（上下双向）。

        模板字号（54-120px）按短边 1080 设计：底片小分辨率（684×1174）
        直接用底片尺寸渲染会让文字比例失衡 → 放大；底片 4K（2160×3840）
        原样渲染则字号只有设计比例的一半 → 缩小。**双向归一**后，任何
        9:16 素材都在同一设计基准上渲染，比例恒定（合成阶段统一 scale
        回底片尺寸，缩小=超采样更锐）。

        横屏/非 9:16 比例：短边已 1080（如 1920×1080）时原样交给模板，
        由 _base_css 按设计基准等比缩放居中兜底（宁小勿溢出）。
        """
        def _even(x: int) -> int:
            return x if x % 2 == 0 else x + 1

        m = min(base_w, base_h)
        if m == TalkingHeadOverlaySkill._DESIGN_MIN_SIDE:
            return _even(base_w), _even(base_h)
        scale = TalkingHeadOverlaySkill._DESIGN_MIN_SIDE / m
        return _even(round(base_w * scale)), _even(round(base_h * scale))

    @staticmethod
    def _words_for_segment(
        timed_words: list[dict[str, Any]], seg: Any,
    ) -> list[dict[str, Any]]:
        """取叠加窗口内的词（相对 seg.time_start 的时间），clip 到窗口内。"""
        dur = seg.time_end - seg.time_start
        out = []
        for w in timed_words:
            rel_s = max(0.0, w["start"] - seg.time_start)
            rel_e = min(dur, w["end"] - seg.time_start)
            if rel_e > rel_s:
                out.append({"text": w["text"], "start": rel_s, "end": rel_e})
        return out

    async def _render_previews(
        self,
        plan: Any,
        timed_words: list[dict[str, Any]],
        tmp_dir: Path,
        base_w: int = 1080,
        base_h: int = 1920,
        llm: Any = None,
    ) -> list[tuple[Any, Path]]:
        """闸门 2：渲染叠加动画预览。

        返回 [(segment, 渲染产物路径)] 配对 — compose 按 seg 自带的时间窗
        配对自己的视频，避免渲染失败时 zip 串位。

        渲染画布：_design_canvas（与底片同比例、短边 1080），
        caption 类模板注入词级时间戳（align_word_timing 对齐，OVE 规则 1：
        图形落在词上 — 逐词弹出时机与语音精确同步）。

        P1 模板生成器：gen_* 未知模板在此现场生成（契约校验失败重试 ≤2，
        每片上限 2 个生成 — 生成 30-60s/个，防 LLM 滥提名拖垮管线），
        全败该段降级 skip。
        """
        from capabilities.video.adapters.html_to_video import (
    render_html_to_video,
    resolve_ffmpeg,
)
        from capabilities.video.adapters.overlay_templates import TEMPLATES
        from capabilities.video.adapters.word_align import (
            TimedWord,
            align_word_timing,
        )
        from capabilities.video.services.overlay_orchestrator import template_name
        from capabilities.video.services.template_generator import (
            GENERATED,
            generate_template,
            load_generated_templates,
        )

        # 跨进程复用：加载历史生成模板（幂等；"生成后零边际成本"）
        load_generated_templates()

        canvas_w, canvas_h = self._design_canvas(base_w, base_h)
        gen_budget = _GEN_BUDGET

        preview_pairs: list[tuple[Any, Path]] = []
        for seg in plan.segments:
            if seg.skipped:
                continue

            tname = template_name(seg.template)
            template_fn = TEMPLATES.get(tname) or GENERATED.get(tname)
            if not template_fn and tname not in TEMPLATES:
                # P1：未知模板 → 现场生成（llm 缺失/预算尽/生成失败 → skip）
                requirement = str(
                    seg.params.get("requirement")
                    or f"{tname} 模板：{json.dumps(seg.params, ensure_ascii=False)[:200]}",
                )
                if llm is not None and gen_budget > 0:
                    gen_budget -= 1
                    logger.info(
                        "[P1 template_generator] 生成模板 %s：%s", tname, requirement[:80],
                    )
                    tpl = await generate_template(llm, tname, requirement)
                    template_fn = tpl or GENERATED.get(tname)
                if not template_fn:
                    logger.warning(f"template not found (gen failed): {tname}")
                    continue

            render_kwargs = dict(seg.params)
            dur = seg.time_end - seg.time_start

            # v4 主题色盘透传 — LLM 未显式给色时模板可从盘内取（参数缺失不报错）
            if getattr(plan, "palette", None):
                render_kwargs.setdefault("palette", list(plan.palette))

            # 词级对齐：窗口词 + params 文本 → TimedWord 列表
            if tname in self._CAPTION_TEMPLATES:
                window_words = [
                    {**w, "text": _simplify_cn(str(w["text"]))}
                    for w in self._words_for_segment(timed_words, seg)
                ]
                text = str(render_kwargs.get("text", "")).strip()
                if not text:
                    # LLM 缺 text 时从转录窗口词重建（字面化视觉：字幕=口播原话），
                    # 而非丢弃整段 — LLM 常假设字幕文本由系统自动从转录取
                    text = "".join(w["text"] for w in window_words)
                    if text:
                        render_kwargs["text"] = text
                if not text:
                    logger.warning("skip overlay %s: caption without text (no window words)", seg.segment_id)
                    continue
                timed = align_word_timing(
                    text,
                    [
                        TimedWord(text=w["text"], start=w["start"], end=w["end"])
                        for w in window_words
                    ],
                    dur,
                )
                render_kwargs["words"] = [
                    {"text": t.text, "start": t.start, "end": t.end} for t in timed
                ]

            try:
                html_content = template_fn(
                    width=canvas_w, height=canvas_h,
                    duration=dur,
                    **render_kwargs,
                )
            except TypeError as e:
                # 单个段参数缺失/不合法不否决整个规划（anti-hallucination：
                # 不信任 LLM 输出，坏段降级跳过）
                logger.warning(
                    "skip overlay %s (%s): bad params %s",
                    seg.segment_id, tname, e,
                )
                continue

            output = tmp_dir / f"overlay_{seg.segment_id}.mp4"
            result = await render_html_to_video(
                html_content=html_content,
                output_path=output,
                width=canvas_w, height=canvas_h,
                duration=dur,
            )
            if result:
                preview_pairs.append((seg, result))
            else:
                logger.warning(f"overlay render failed: {seg.segment_id} ({seg.template})")

        return preview_pairs

    async def _compose_final(
        self,
        video_path: str,
        overlay_pairs: list[tuple[Any, Path]],
        output_path: Path,
    ) -> Path:
        """闸门 3：合成最终视频。seg↔video 按 _render_previews 的配对走，不 zip。"""
        from capabilities.video.adapters.html_to_video import compose_with_overlays

        overlay_tuples = [
            (seg.time_start, seg.time_end, vid)
            for seg, vid in overlay_pairs
            if not seg.skipped
        ]

        return await compose_with_overlays(
            base_video=video_path,
            overlay_videos=overlay_tuples,
            output_path=output_path,
        )


@asynccontextmanager
async def managed_temp_dir():
    """临时目录上下文管理器 — 确保清理。"""
    tmp = tempfile.mkdtemp(prefix="overlay_")
    tmp_path = Path(tmp)
    try:
        yield tmp_path
    finally:
        import shutil
        try:
            shutil.rmtree(tmp, ignore_errors=True)
        except Exception:
            pass