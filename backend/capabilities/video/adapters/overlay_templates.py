"""叠加动画 HTML 模板库 v3 — 彩色贴纸式视觉冲击 + OVE 确定性逐帧渲染。

v3 核心变化（用户反馈：v2 只有白字黑边，"一点亮色的地方都没有"）：
  - caption_pop 新增 accent_words：命中词变亮色圆角色块（黄/红交替）弹出
  - caption_karaoke 当前词改为亮黄底色块 + 黑字（CapCut 风）
  - stat_card 亮色渐变卡 + 过冲弹跳入场
  - keyword_icon 白底气泡卡 + 过冲弹跳
  - keyword_highlight 亮黄标记笔从左扫出，扫满后文字翻黑
  - title_card 亮色下划线扫出
  - number_roll 数值渐变字 + 亮色描边卡
  - bar_chart_grow 每柱独立亮色
  - lower_third 亮色渐变横幅
  - 新增 section_card：段落编号巨型色块（"第一/第二/第三"列举段首）
  - 新增 emoji_sticker：大 emoji 弹跳微旋转（全片最强戏剧点）

v4 核心变化（用户反馈：v3 仍"太单一"——视觉词汇窄/无画布互动/无微动无节奏无主题色）：
  - 新增 arrow_point / circle_mark：画面空间互动（箭头戳向 / 手绘圈注）
  - 新增 full_card / split_compare：整屏色块冲击卡（开场轰炸 / 结尾收束 / ❌✅对比）
  - 全模板持续微动 __idle：入场后呼吸/浮动/摆动，由 seekTo 的 t 相位驱动（确定性）
  - 主题色 palette 参数透传 + _text_on 亮度自适应文字对比色

继承 v2 契约（ADR-001/003）：
  - 透明背景替代绿幕（compose 走 rgba，文字不再有绿边）
  - 所有动画由 window.seekTo(t) 驱动：渲染器逐帧调用求值元素状态，
    100% 确定性（对齐 OVE/Remotion 的确定性时间轴模型）
  - 字幕模板接受 words 参数（词级时间戳，来自 word_align.align_word_timing），
    实现"图形落在词上"（OVE 规则 1）
  - 大字号 + 双层描边（text-stroke + text-shadow）+ max-width 防越界

模板契约：
  - 必须定义 window.seekTo(t: number)：设置 t 秒时刻全部元素状态
  - body background: transparent（rgba 合成的前提）
  - bg_color 参数接受但忽略（LLM 旧输出兼容）
"""

from __future__ import annotations

import json
from typing import Any, Callable

_FONT = '"PingFang SC", "Microsoft YaHei", "Source Han Sans SC", sans-serif'

# 模板内部坐标基准：所有字号/边距/位移的绝对 px 都按 1080×1920 设计
# （2026-10-28 样本多样性审计：此前只在 1080×1920 素材上验证过，
#  720p 竖屏/横屏源视频会按原尺寸渲染 → 字号相对放大、布局溢出）
_DESIGN_W = 1080
_DESIGN_H = 1920

# 亮色盘（v3 视觉冲击的"记忆点"色）
_YELLOW = "#FFE066"   # 亮黄（主强调）
_YELLOW_SHADOW = "#E0B400"
_RED = "#FF4D4D"      # 亮红（次强调）
_RED_SHADOW = "#C22F2F"
_ORANGE = "#FF9F43"   # 橙（渐变副色）
_GREEN = "#22C55E"    # 亮绿（v4 split_compare 正确侧）
_TEAL = "#4ECDC4"     # 青绿（数据类）
_PURPLE = "#A78BFA"   # 紫（柱状图）
_BAR_COLORS = [_RED, _YELLOW, _TEAL, _PURPLE, _ORANGE]


def _base_css(width: int, height: int) -> str:
    """共享基础样式：reset + 透明背景 + 分辨率自适应画布。

    自适应做法：body 固定为设计基准 1080×1920（模板内部 px 原样有效），
    整体按 k = min(w/1080, h/1920) 等比缩放并居中 — 16 个模板内部
    一行不用改。横屏时 k 由高度决定、内容偏小属有意保守（横屏正确
    形态是重排而非缩放，暂不覆盖；宁小勿溢出）。
    """
    w = int(width or _DESIGN_W)
    h = int(height or _DESIGN_H)
    if w <= 0:
        w = _DESIGN_W
    if h <= 0:
        h = _DESIGN_H
    k = min(w / _DESIGN_W, h / _DESIGN_H)
    ox = (w - _DESIGN_W * k) / 2
    oy = (h - _DESIGN_H * k) / 2
    return f"""
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
html {{
    width: {w}px; height: {h}px;
    background: transparent; overflow: hidden;
}}
body {{
    width: {_DESIGN_W}px; height: {_DESIGN_H}px;
    background: transparent;
    font-family: {_FONT};
    overflow: hidden;
    transform-origin: top left;
    transform: translate({ox:.3f}px, {oy:.3f}px) scale({k:.6f});
}}"""


def _clamp01_js() -> str:
    """共享缓动函数：clamp01 + easeOutCubic + easeOutBack（过冲弹跳）。"""
    return """
function clamp01(x) { return Math.max(0, Math.min(1, x)); }
function easeOutCubic(x) { return 1 - Math.pow(1 - x, 3); }
function easeOutBack(x) {
    const c1 = 1.70158, c3 = c1 + 1;
    return 1 + c3 * Math.pow(x - 1, 3) + c1 * Math.pow(x - 1, 2);
}"""


def _words_js(words: list[dict[str, Any]] | None) -> str:
    """词级时间数据注入为 JS 常量。"""
    data = words or []
    return f"const WORDS = {json.dumps(data, ensure_ascii=False)};"


def _doc(html_body: str, css: str, js: str) -> str:
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>{css}</style></head><body>
{html_body}
<script>{js}</script>
</body></html>"""


def _word_spans(
    text: str,
    words: list[dict[str, Any]] | None,
    duration: float,
    classify: Callable[[str], str] | None = None,
) -> tuple[list[dict[str, Any]], str]:
    """构建词级 span。words 缺省时按 split_words 均分。

    classify: 可选回调，返回词的附加 class（如 accent 色块标记）。
    返回 (words, spans_html)。
    """
    from capabilities.video.adapters.word_align import split_words

    if not words:
        ws = split_words(text)
        step = duration / max(len(ws), 1)
        words = [
            {"text": w, "start": i * step, "end": (i + 1) * step}
            for i, w in enumerate(ws)
        ]
    parts = []
    for i, w in enumerate(words):
        cls = "word"
        if classify:
            extra = classify(w["text"])
            if extra:
                cls += f" {extra}"
        parts.append(f'<span class="{cls}" data-i="{i}">{w["text"]}</span>')
    return words, "".join(parts)


def _as_float(v: Any, default: float) -> float:
    """LLM 数值参数兜底（字符串/None 均安全）。"""
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _wcag_lum(color: Any) -> float | None:
    """#rrggbb → WCAG 2.x 相对亮度；非法输入返回 None（调用方自行兜底）。"""
    try:
        h = str(color).lstrip("#")
        if len(h) != 6:
            return None
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)

        def _lin(v: float) -> float:
            v /= 255
            return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

        return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)
    except (ValueError, TypeError):
        return None


def _text_on(bg: str, fallback: str = "#FFFFFF") -> str:
    """按背景色选最具可读性的黑/白文字色（WCAG 对比度保障）。

    2026-10-28 模板排版审计修正：原实现用感知亮度 0.299/0.587/0.114
    加 0.6 阈值二选一 — #FF4D4D 的近似亮度 0.51 被误判为"浅色"选白字，
    实际对比仅 3.27:1，正文（<48px 需 4.5:1）不达标。这正是十轮 demo 里
    VLM 反复报的"白字压橙色块对比度 2.8:1"同一类问题。

    改为 WCAG 相对亮度 + 黑白对比度取优：数学上
    max(c_black, c_white) ≥ √21 ≈ 4.58，恒 ≥ 4.5，任何背景都达标。
    """
    lum = _wcag_lum(bg)
    if lum is None:
        return fallback
    c_white = 1.05 / (lum + 0.05)
    c_black = (lum + 0.05) / 0.05
    return "#FFFFFF" if c_white >= c_black else "#111111"


def _safe_text_color(text_color: Any, bg: str, need: float) -> str:
    """显式文字色对比度校验 — 不足 need:1 则回退 _text_on 自动选色。

    修 2026-10-28 G1 真机验证发现的防线旁路：full_card 显式传 text_color
    直接生效，绕过 WCAG 自动选色（对比度防线的唯一漏洞）。调用方按
    "本卡最严适用字号档"传 need（≥48px 大字 3.0，其余 4.5）。
    非法/无法判定的显式色同样回退，不盲信 LLM 输入。
    """
    explicit = str(text_color or "").strip()
    if not explicit:
        return _text_on(bg)
    lum_fg, lum_bg = _wcag_lum(explicit), _wcag_lum(bg)
    if lum_fg is None or lum_bg is None:
        return _text_on(bg)
    hi, lo = max(lum_fg, lum_bg), min(lum_fg, lum_bg)
    return explicit if (hi + 0.05) / (lo + 0.05) >= need else _text_on(bg)


def _idle_js() -> str:
    """v4 微动辅助 — 入场结束后持续的"呼吸感"微动。

    确定性：全部由 seekTo 传入的 t 计算相位（禁 CSS animation / rAF）。
    dy=浮动px rot=摆动deg freq=频率 sc=呼吸幅度 ph=相位偏移
    """
    return """
function __idle(el, t, t0, dy, rot, freq, sc, ph) {
    if (!el || t < t0) return;
    const f = (t - t0) * freq;
    el.style.transform =
        'translateY(' + (Math.sin(f + ph) * dy).toFixed(2) + 'px)' +
        ' rotate(' + (Math.sin(f * 0.77 + ph) * rot).toFixed(2) + 'deg)' +
        ' scale(' + (1 + Math.sin(f * 0.6 + ph) * sc).toFixed(4) + ')';
}"""


# ── 字幕类 ────────────────────────────────────────────────

def caption_pop(
    text: str,
    width: int = 1080,
    height: int = 1920,
    duration: float = 3.0,
    font_size: int = 72,
    color: str = "#FFFFFF",
    stroke_color: str = "#000000",
    highlight_color: str = _YELLOW,
    **kwargs: Any,
) -> str:
    """逐词弹出大字幕 + 重音词亮色色块（OVE 规则 1：图形落在词上）。

    words 参数（可选）: [{"text": "今天", "start": 0.0, "end": 0.4}, ...]
    accent_words 参数（可选）: ["三秒", "防晒"] — 命中的词变成亮色圆角色块
    （黄/红交替）弹出，其余白字黑边。这是字幕的"亮色记忆点"。
    缺省 words 时按字符均分。
    """
    accent_words = [a for a in (kwargs.get("accent_words") or []) if a]
    words, _ = _word_spans(text, kwargs.get("words"), duration)

    # 盘内色消费（2026-10-27 o6/o10 实证：palette 规则要求每段 color
    # 来自色盘，accent 块写死黄红无视 color → 规划与渲染脱节）：
    # LLM 传非默认 color → accent 块统一用它，字色按亮度自动黑白；
    # 不传 → 黄红交替兜底（CapCut 贴纸感设计保留）。
    accent_custom = color if color and color != "#FFFFFF" else ""

    # 重音词色块：同一 accent 词命中的多个 word 同色（语义单元不拆散
    # — 2026-10-27 o23 实证：'特别多'分词后黄红拆两块）；不同 accent
    # 词按其索引交替（无自定义色时黄红循环）。
    spans_parts = []
    for i, w in enumerate(words):
        wt = w["text"]
        cls = "word"
        if accent_words:
            hit = next((j for j, a in enumerate(accent_words)
                        if a in wt or wt in a), -1)
            if hit >= 0:
                cls += f" accent c{hit % 2 + 1}"
        spans_parts.append(f'<span class="{cls}" data-i="{i}">{wt}</span>')
    spans = "".join(spans_parts)

    css = _base_css(width, height) + f"""
body {{ display: flex; align-items: flex-end; justify-content: center; padding-bottom: 260px; }}
.caption {{
    display: flex; flex-wrap: wrap; gap: 10px; justify-content: center;
    max-width: 92%; padding: 24px 36px; line-height: 1.35;
}}
.word {{
    font-size: {font_size}px; font-weight: 900; color: {color};
    -webkit-text-stroke: 3px {stroke_color}; paint-order: stroke fill;
    text-shadow: 0 4px 12px rgba(0,0,0,0.55);
    padding: 4px 8px; border-radius: 16px;
    opacity: 0; transform: translateY(30px) scale(0.7);
}}
.word.accent {{
    -webkit-text-stroke: 0; color: #1A1A1A;
    padding: 4px 18px;
}}"""

    if accent_custom:
        # 盘内色模式：单一 accent 色（不再输出黄红循环规则）
        tc = _text_on(accent_custom)
        css += f"""
.word.accent.c1, .word.accent.c2 {{ background: {accent_custom}; color: {tc};
    box-shadow: 0 6px 0 rgba(0,0,0,0.28); }}"""
    else:
        css += f"""
.word.accent.c1 {{ background: {_YELLOW}; box-shadow: 0 6px 0 {_YELLOW_SHADOW}; }}
.word.accent.c2 {{ background: {_RED}; color: #FFFFFF; box-shadow: 0 6px 0 {_RED_SHADOW}; }}"""

    js = _clamp01_js() + _words_js(words) + _idle_js() + """
window.seekTo = function(t) {
    document.querySelectorAll('.word').forEach(el => {
        const i = +el.dataset.i, w = WORDS[i];
        const isAccent = el.classList.contains('accent');
        const p = clamp01((t - w.start) / (isAccent ? 0.35 : 0.28));
        const e = isAccent ? easeOutBack(p) : easeOutCubic(p);
        el.style.opacity = Math.min(1, p * 2);
        el.style.transform = `translateY(${(1 - Math.min(1, p)) * 30}px) scale(${0.7 + 0.3 * e})`;
    });
    __idle(document.querySelector('.caption'), t, 0.5, 5, 0.5, 1.6, 0, 0);
};"""
    return _doc(f'<div class="caption">{spans}</div>', css, js)


def caption_karaoke(
    text: str,
    width: int = 1080,
    height: int = 1920,
    duration: float = 3.0,
    font_size: int = 68,
    color: str = "#FFFFFF",
    highlight_color: str = _YELLOW,
    stroke_color: str = "#000000",
    **kwargs: Any,
) -> str:
    """逐词卡拉OK字幕 — 当前词亮黄底色块 + 黑字（CapCut 风）。"""
    words, spans = _word_spans(text, kwargs.get("words"), duration)

    css = _base_css(width, height) + f"""
body {{ display: flex; align-items: flex-end; justify-content: center; padding-bottom: 260px; }}
.caption {{
    display: flex; flex-wrap: wrap; gap: 8px; justify-content: center;
    max-width: 92%; padding: 24px 36px; line-height: 1.35;
}}
.word {{
    font-size: {font_size}px; font-weight: 800; color: {color};
    -webkit-text-stroke: 3px {stroke_color}; paint-order: stroke fill;
    text-shadow: 0 4px 12px rgba(0,0,0,0.55);
    padding: 4px 14px; border-radius: 14px;
    opacity: 0.5; transition: none;
}}"""

    js = _clamp01_js() + _words_js(words) + _idle_js() + f"""
window.seekTo = function(t) {{
    document.querySelectorAll('.word').forEach(el => {{
        const i = +el.dataset.i, w = WORDS[i];
        if (t >= w.start && t < w.end) {{
            el.style.opacity = 1;
            el.style.background = '{highlight_color}';
            el.style.color = '#111111';
            el.style.webkitTextStroke = '0px #000';
            el.style.boxShadow = '0 5px 0 {_YELLOW_SHADOW}';
            el.style.transform = 'scale(1.08)';
        }} else {{
            el.style.opacity = t >= w.end ? 1 : 0.5;
            el.style.background = 'transparent';
            el.style.color = '{color}';
            el.style.webkitTextStroke = '3px {stroke_color}';
            el.style.boxShadow = 'none';
            el.style.transform = 'scale(1)';
        }}
   }});
    __idle(document.querySelector('.caption'), t, 0.5, 5, 0.5, 1.6, 0, 0);
}};"""
    return _doc(f'<div class="caption">{spans}</div>', css, js)


def caption_typewriter(
    text: str,
    width: int = 1080,
    height: int = 1920,
    duration: float = 3.0,
    font_size: int = 64,
    color: str = "#FFFFFF",
    stroke_color: str = "#000000",
    **kwargs: Any,
) -> str:
    """打字机字幕 — 按 words 时间依次弹出（带轻微缩放）。"""
    words, spans = _word_spans(text, kwargs.get("words"), duration)

    css = _base_css(width, height) + f"""
body {{ display: flex; align-items: flex-end; justify-content: center; padding-bottom: 260px; }}
.caption {{
    display: flex; flex-wrap: wrap; gap: 6px; justify-content: center;
    max-width: 92%; padding: 24px 36px; line-height: 1.35;
}}
.word {{
    font-size: {font_size}px; font-weight: 800; color: {color};
    -webkit-text-stroke: 3px {stroke_color}; paint-order: stroke fill;
    text-shadow: 0 4px 12px rgba(0,0,0,0.55);
    opacity: 0; transform: scale(0.6);
}}"""

    js = _clamp01_js() + _words_js(words) + _idle_js() + """
window.seekTo = function(t) {
    document.querySelectorAll('.word').forEach(el => {
        const i = +el.dataset.i, w = WORDS[i];
        const p = easeOutBack(clamp01((t - w.start) / 0.25));
        el.style.opacity = t >= w.start ? 1 : 0;
        el.style.transform = t >= w.start ? `scale(${0.6 + 0.4 * Math.min(1, p)})` : 'scale(0.6)';
    });
    __idle(document.querySelector('.caption'), t, 0.5, 5, 0.5, 1.6, 0, 0);
};"""
    return _doc(f'<div class="caption">{spans}</div>', css, js)


# ── 关键词/图标类 ─────────────────────────────────────────

def keyword_icon(
    keyword: str = "",
    icon: str = "📌",
    animation: str = "pop",
    width: int = 1080,
    height: int = 1920,
    duration: float = 2.0,
    font_size: int = 44,
    color: str = "#111111",
    **kwargs: Any,
) -> str:
    """关键词白底气泡卡 + emoji 图标过冲弹跳入场。

    白底气泡 + 底部实体投影（贴纸感），黑字保证可读。
    兼容 LLM 参数习惯：emoji 键名（优先于 icon）、subtitle 副标题
    （渲染为关键词下方的小字说明行）。
    color 参数仅兼容旧输出（气泡内固定黑字）。
    """
    icon = str(kwargs.get("emoji") or icon)
    keyword = str(keyword or kwargs.get("text") or kwargs.get("title") or "")
    subtitle = str(kwargs.get("subtitle") or "")
    subtitle_html = f'<span class="subtitle">{subtitle}</span>' if subtitle else ""
    css = _base_css(width, height) + f"""
body {{ display: flex; align-items: center; justify-content: flex-end; padding-right: 56px; }}
.keyword-box {{
    display: flex; align-items: center; gap: 16px;
    padding: 18px 34px; max-width: 80%;
    background: #FFFFFF; border-radius: 999px;
    box-shadow: 0 7px 0 rgba(0,0,0,0.25), 0 16px 34px rgba(0,0,0,0.30);
    opacity: 0; transform-origin: right center;
}}
.icon {{ font-size: 72px; }}
.txt-col {{ display: flex; flex-direction: column; align-items: flex-start; gap: 2px; }}
.keyword {{ font-size: {font_size}px; color: #111111; font-weight: 900; line-height: 1.2; }}
.subtitle {{ font-size: 22px; color: #666666; font-weight: 600; }}"""

    js = _clamp01_js() + _idle_js() + """
window.seekTo = function(t) {
    const el = document.querySelector('.keyword-box');
    const p = clamp01((t - 0.15) / 0.40);
    const pb = easeOutBack(p);
    el.style.opacity = Math.min(1, p * 3);
    el.style.transform = `translateX(${(1 - Math.min(1, p)) * 90}px) scale(${0.6 + 0.4 * pb})`;
    __idle(el, t, 0.6, 4, 1.2, 1.7, 0.006, 0);
};"""
    return _doc(
        f'<div class="keyword-box"><span class="icon">{icon}</span>'
        f'<span class="txt-col"><span class="keyword">{keyword}</span>{subtitle_html}</span></div>',
        css, js,
    )


def keyword_highlight(
    keyword: str = "",
    width: int = 1080,
    height: int = 1920,
    duration: float = 2.0,
    font_size: int = 56,
    color: str = "#FFFFFF",
    highlight_color: str = _YELLOW,
    **kwargs: Any,
) -> str:
    """关键词亮黄标记笔 — 文字弹入 + marker 从左扫出，扫满后文字翻黑。"""
    keyword = str(keyword or kwargs.get("text") or kwargs.get("keyword_text") or "")
    if color != "#FFFFFF":  # LLM color=盘内主题色 → 标记笔色（文字保持白）
        highlight_color = color
    css = _base_css(width, height) + f"""
body {{ display: flex; align-items: center; justify-content: center; }}
.highlight-box {{ position: relative; padding: 16px 30px; opacity: 0; }}
.keyword {{
    font-size: {font_size}px; color: {color}; font-weight: 900;
    -webkit-text-stroke: 3px #000; paint-order: stroke fill;
    text-shadow: 0 4px 12px rgba(0,0,0,0.5);
    position: relative; z-index: 2;
}}
.marker {{
    position: absolute; left: 6px; top: 14%; height: 72%; width: calc(100% - 12px);
    background: linear-gradient(90deg, {highlight_color}, #FFD43B);
    border-radius: 12px; transform: scaleX(0); transform-origin: left center;
    z-index: 1; box-shadow: 0 5px 0 rgba(0,0,0,0.18);
}}"""

    js = _clamp01_js() + _idle_js() + """
window.seekTo = function(t) {
    const box = document.querySelector('.highlight-box');
    const line = document.querySelector('.marker');
    const kw = document.querySelector('.keyword');
    const p1 = clamp01((t - 0.15) / 0.35);
    const p2 = clamp01((t - 0.50) / 0.30);
    box.style.opacity = Math.min(1, p1);
    box.style.transform = `scale(${0.8 + 0.2 * Math.min(1, easeOutBack(p1))})`;
    line.style.transform = `scaleX(${p2})`;
    kw.style.color = p2 >= 1 ? '#111111' : '#FFFFFF';
    __idle(box, t, 0.55, 4, 1.0, 1.7, 0.005, 0);
};"""
    return _doc(
        f'<div class="highlight-box"><div class="marker"></div>'
        f'<span class="keyword">{keyword}</span></div>', css, js,
    )


# ── 卡片类 ────────────────────────────────────────────────

def stat_card(
    label: str = "",
    value: str = "",
    icon: str = "📊",
    width: int = 1080,
    height: int = 1920,
    duration: float = 3.0,
    color: str = "#FFFFFF",
    accent_color: str = _RED,
    **kwargs: Any,
) -> str:
    """数据卡片 — 亮色渐变卡 + 过冲弹跳入场（右侧滑入）。"""
    label = str(label or kwargs.get("text") or kwargs.get("title") or "")
    value = str(value or kwargs.get("sub") or kwargs.get("subtitle") or "")
    if color != "#FFFFFF":  # LLM color=盘内主题色 → 卡片主色（文字保持白）
        accent_color = color
    accent2 = kwargs.get("accent_color_2", _ORANGE)
    css = _base_css(width, height) + f"""
body {{ display: flex; align-items: center; justify-content: flex-end; padding-right: 48px; }}
.card {{
    padding: 30px 42px; max-width: 78%;
    background: linear-gradient(135deg, {accent_color}, {accent2});
    border-radius: 24px;
    box-shadow: 0 9px 0 rgba(0,0,0,0.28), 0 20px 44px rgba(0,0,0,0.35);
    opacity: 0; transform: translateX(70px) scale(0.9);
}}
.icon {{ font-size: 52px; margin-bottom: 8px; }}
.value {{ font-size: 84px; color: #FFFFFF; font-weight: 900;
    -webkit-text-stroke: 2.5px rgba(0,0,0,0.35); paint-order: stroke fill; }}
.label {{ font-size: 28px; color: #FFFFFF; opacity: 0.95; font-weight: 600; }}"""

    js = _clamp01_js() + _idle_js() + """
window.seekTo = function(t) {
    const el = document.querySelector('.card');
    const p = clamp01((t - 0.2) / 0.45);
    const pb = easeOutBack(p);
    el.style.opacity = Math.min(1, p * 3);
    el.style.transform = `translateX(${(1 - Math.min(1, p)) * 70}px) scale(${0.9 + 0.1 * pb})`;
    __idle(el, t, 0.7, 5, 1.2, 1.6, 0.006, 0);
};"""
    return _doc(
        f'<div class="card"><div class="icon">{icon}</div>'
        f'<div class="value">{value}</div><div class="label">{label}</div></div>',
        css, js,
    )


def title_card(
    title: str = "",
    subtitle: str = "",
    width: int = 1080,
    height: int = 1920,
    duration: float = 3.0,
    color: str = "#FFFFFF",
    accent_color: str = _YELLOW,
    **kwargs: Any,
) -> str:
    """标题卡 — 标题过冲弹入 + 亮色下划线扫出 + 副标题跟进。"""
    title = str(title or kwargs.get("text") or "")
    subtitle = str(subtitle or kwargs.get("sub") or "")
    if color != "#FFFFFF":  # LLM color=盘内主题色 → 下划线主色（标题保持白）
        accent_color = color
    subtitle_html = f'<div class="subtitle">{subtitle}</div>' if subtitle else ""
    # 长标题缩字号防孤字行（"…健康第一位"的"位"孤行事故 → 分档自适应）
    title_size = _fit_font_size(title, 80, [(12, 48), (9, 60)])
    css = _base_css(width, height) + f"""
body {{ display: flex; align-items: center; justify-content: center; }}
.title-box {{
    text-align: center; padding: 44px 64px; max-width: 88%;
    opacity: 0;
}}
.title {{
    font-size: {title_size}px; color: {color}; font-weight: 900; line-height: 1.25;
    -webkit-text-stroke: 4px #000; paint-order: stroke fill;
    text-shadow: 0 6px 16px rgba(0,0,0,0.4);
    transform: scale(0.8);
}}
.accent-line {{
    height: 14px; margin: 20px auto 0; width: 62%;
    background: linear-gradient(90deg, {accent_color}, {_ORANGE});
    border-radius: 7px; transform: scaleX(0); transform-origin: center;
    box-shadow: 0 5px 0 rgba(0,0,0,0.2);
}}
.subtitle {{ font-size: 34px; color: {accent_color}; margin-top: 22px; font-weight: 800;
    -webkit-text-stroke: 1.5px rgba(0,0,0,0.5); paint-order: stroke fill;
    opacity: 0; }}"""

    js = _clamp01_js() + _idle_js() + """
window.seekTo = function(t) {
    const box = document.querySelector('.title-box');
    const title = document.querySelector('.title');
    const line = document.querySelector('.accent-line');
    const sub = document.querySelector('.subtitle');
    const p1 = clamp01((t - 0.1) / 0.45);
    const p2 = clamp01((t - 0.45) / 0.30);
    const p3 = clamp01((t - 0.75) / 0.30);
    box.style.opacity = Math.min(1, p1 * 2);
    title.style.transform = `scale(${0.8 + 0.2 * Math.min(1, easeOutBack(p1))})`;
    line.style.transform = `scaleX(${p2})`;
    if (sub) sub.style.opacity = p3;
    __idle(box, t, 1.1, 6, 0.8, 1.4, 0, 0);
};"""
    return _doc(
        f'<div class="title-box"><div class="title">{title}</div>'
        f'<div class="accent-line"></div>{subtitle_html}</div>',
        css, js,
    )


def number_roll(
    label: str = "",
    value: str = "0",
    target: str = "100",
    width: int = 1080,
    height: int = 1920,
    duration: float = 3.0,
    color: str = "#FFFFFF",
    accent_color: str = _TEAL,
    **kwargs: Any,
) -> str:
    """数字滚动 — 亮色渐变数字 + 描边亮框卡，seekTo(t) 驱动插值（确定性）。"""
    label = str(label or kwargs.get("text") or kwargs.get("title") or "")
    css = _base_css(width, height) + f"""
body {{ display: flex; align-items: center; justify-content: flex-end; padding-right: 48px; }}
.card {{
    padding: 30px 42px; min-width: 260px;
    background: rgba(17,17,17,0.82); border-radius: 22px;
    border: 3px solid {accent_color};
    box-shadow: 0 0 30px {accent_color}66, 0 9px 0 rgba(0,0,0,0.28);
    backdrop-filter: blur(8px);
    opacity: 0; transform: translateX(60px);
}}
.label {{ font-size: 26px; color: {color}; opacity: 0.9; margin-bottom: 10px; }}
.value {{
    font-size: 76px; font-weight: 900;
    background: linear-gradient(90deg, {accent_color}, #FFD43B);
    -webkit-background-clip: text; background-clip: text; color: transparent;
}}"""

    js = _clamp01_js() + _idle_js() + f"""
window.seekTo = function(t) {{
    const card = document.querySelector('.card');
    const el = document.getElementById('counter');
    const pIn = clamp01((t - 0.2) / 0.4);
    card.style.opacity = Math.min(1, pIn * 2);
    card.style.transform = `translateX(${{(1 - Math.min(1, pIn)) * 60}}px)`;
    const from = {value}, to = {target};
    const p = easeOutCubic(clamp01((t - 0.35) / 1.2));
    el.textContent = Math.round(from + (to - from) * p).toLocaleString();
    __idle(card, t, 0.65, 4, 1.0, 1.6, 0.005, 0);
}};"""
    return _doc(
        f'<div class="card"><div class="label">{label}</div>'
        f'<div class="value" id="counter">{value}</div></div>', css, js,
    )


def bar_chart_grow(
    labels: str = "A,B,C",
    values: str = "30,60,90",
    width: int = 1080,
    height: int = 1920,
    duration: float = 3.0,
    color: str = "#FFFFFF",
    accent_color: str = _TEAL,
    **kwargs: Any,
) -> str:
    """柱状图生长 — 每柱独立亮色级联入场。"""
    label_list = [x for x in labels.split(",") if x.strip()]
    value_list = [float(v) for v in values.split(",") if v.strip()]
    max_val = max(value_list) if value_list else 1
    bars = ""
    for i, (lbl, val) in enumerate(zip(label_list, value_list)):
        c = _BAR_COLORS[i % len(_BAR_COLORS)]
        bars += (
            f'<div class="bar-row"><span class="bar-label">{lbl}</span>'
            f'<div class="bar-track"><div class="bar-fill" data-i="{i}" '
            f'data-w="{val / max_val * 100:.1f}" style="background:{c}"></div></div>'
            f'<span class="bar-value" style="color:{c}">{val:.0f}</span></div>'
        )

    css = _base_css(width, height) + f"""
body {{ display: flex; align-items: center; justify-content: flex-end; padding-right: 48px; }}
.chart {{
    padding: 30px 40px; min-width: 340px; max-width: 78%;
    background: rgba(17,17,17,0.82); border-radius: 22px;
    border: 3px solid {accent_color};
    box-shadow: 0 0 30px {accent_color}55, 0 9px 0 rgba(0,0,0,0.28);
    backdrop-filter: blur(8px);
    opacity: 0; transform: translateX(60px);
}}
.bar-row {{ display: flex; align-items: center; gap: 10px; margin-bottom: 14px; }}
.bar-label {{ font-size: 22px; color: {color}; min-width: 56px; text-align: right; }}
.bar-track {{ flex: 1; height: 28px; background: rgba(255,255,255,0.12); border-radius: 8px; overflow: hidden; }}
.bar-fill {{ height: 100%; width: 0%; border-radius: 8px; }}
.bar-value {{ font-size: 24px; min-width: 52px; font-weight: 800; }}"""

    js = _clamp01_js() + _idle_js() + """
window.seekTo = function(t) {
    const chart = document.querySelector('.chart');
    const pIn = clamp01((t - 0.15) / 0.4);
    chart.style.opacity = Math.min(1, pIn * 2);
    chart.style.transform = `translateX(${(1 - Math.min(1, pIn)) * 60}px)`;
    document.querySelectorAll('.bar-fill').forEach(el => {
        const i = +el.dataset.i;
        const p = easeOutBack(clamp01((t - 0.45 - i * 0.18) / 0.55));
        el.style.width = (Math.min(1, p) * +el.dataset.w) + '%';
    });
    __idle(chart, t, 0.6, 4, 1.0, 1.6, 0.005, 0);
};"""
    return _doc(f'<div class="chart">{bars}</div>', css, js)


def lower_third(
    title: str = "",
    subtitle: str = "",
    width: int = 1080,
    height: int = 1920,
    duration: float = 3.0,
    color: str = "#FFFFFF",
    accent_color: str = _RED,
    **kwargs: Any,
) -> str:
    """下三分之一字幕条 — 亮色渐变横幅 + 白字，左下滑入。"""
    title = str(title or kwargs.get("text") or "")
    subtitle = str(subtitle or kwargs.get("sub") or "")
    if color != "#FFFFFF":  # LLM color=盘内主题色 → 横幅主色（文字保持白）
        accent_color = color
    accent2 = kwargs.get("accent_color_2", _ORANGE)
    css = _base_css(width, height) + f"""
body {{ display: flex; align-items: flex-end; justify-content: flex-start; padding: 0 0 180px 48px; }}
.lower-third {{
    padding: 22px 40px; max-width: 84%;
    background: linear-gradient(90deg, {accent_color}, {accent2});
    border-radius: 16px 30px 30px 16px;
    box-shadow: 0 8px 0 rgba(0,0,0,0.22), 0 16px 34px rgba(0,0,0,0.30);
    opacity: 0; transform: translateX(-64px);
}}
.title {{ font-size: 40px; color: {color}; font-weight: 900;
    text-shadow: 0 2px 6px rgba(0,0,0,0.35); }}
.subtitle {{ font-size: 26px; color: {color}; opacity: 0.95; margin-top: 6px; }}"""

    js = _clamp01_js() + _idle_js() + """
window.seekTo = function(t) {
    const p = easeOutBack(clamp01((t - 0.2) / 0.4));
    const el = document.querySelector('.lower-third');
    el.style.opacity = Math.min(1, clamp01((t - 0.2) / 0.2));
    el.style.transform = `translateX(${(1 - Math.min(1, p)) * -64}px)`;
    __idle(el, t, 0.65, 5, 0.8, 1.5, 0.005, 0);
};"""
    return _doc(
        f'<div class="lower-third"><div class="title">{title}</div>'
        f'<div class="subtitle">{subtitle}</div></div>', css, js,
    )


# ── v3 新增：强视觉结构类 ─────────────────────────────────

def _fit_font_size(text: str, base: int, steps: list[tuple[int, int]]) -> int:
    """按字数分档缩字号 — 防长标题折行产生孤字（2026-10-27 o10/o23 实证）。

    steps: [(最少字数, 字号)] 降序，命中第一个 len(text) >= n 的档；
    都不命中用 base。"夏天舒服过 健康第一位"这类空格也算 1 字位。
    """
    n = len(text or "")
    for min_len, size in steps:
        if n >= min_len:
            return size
    return base


def section_card(
    number: str = "01",
    title: str = "",
    width: int = 1080,
    height: int = 1920,
    duration: float = 3.0,
    accent_color: str = _YELLOW,
    color: str = "",
    **kwargs: Any,
) -> str:
    """段落编号卡 — 巨型数字色块旋转扫入 + 段落标题跟进。

    用于"第一/第二/第三"列举段首：params.number 传 "01"/"02"/"03"，
    params.title 传该段主题。accent_color 可在黄/红/青间轮换制造节奏；
    LLM 按 palette 规则传的 params.color（color 参数）非空时优先用作
    编号块色（2026-10-27 o6 实证修复：此前写死默认黄无视盘内色）。
    """
    accent_color = color or accent_color
    title = str(title or kwargs.get("text") or "")
    # 长标题缩字号保单行（"出汗别猛灌冰"+"水"孤字断义事故 → 分档自适应）
    title_size = _fit_font_size(title, 54, [(9, 36), (7, 44)])
    css = _base_css(width, height) + f"""
body {{ display: flex; align-items: center; justify-content: flex-start; padding-left: 64px; }}
.section {{ display: flex; align-items: center; gap: 26px; opacity: 0; max-width: 90%; }}
.num {{
    display: inline-block; font-size: 120px; font-weight: 900; color: #1A1A1A;
    background: {accent_color}; padding: 10px 30px; border-radius: 26px;
    box-shadow: 0 10px 0 rgba(0,0,0,0.28), 0 18px 40px rgba(0,0,0,0.30);
    transform: translateX(-140px) rotate(-20deg);
}}
.txt {{
    font-size: {title_size}px; color: #FFFFFF; font-weight: 900; line-height: 1.3;
    -webkit-text-stroke: 3px #000; paint-order: stroke fill;
    text-shadow: 0 4px 12px rgba(0,0,0,0.5);
    max-width: 56%; opacity: 0; white-space: nowrap;
}}"""

    js = _clamp01_js() + _idle_js() + """
window.seekTo = function(t) {
    const sec = document.querySelector('.section');
    const num = document.querySelector('.num');
    const txt = document.querySelector('.txt');
    const p1 = easeOutBack(clamp01(t / 0.45));
    const q1 = Math.min(1, clamp01(t / 0.45));
    const p2 = clamp01((t - 0.30) / 0.35);
    sec.style.opacity = Math.min(1, clamp01(t / 0.2));
    num.style.transform = `translateX(${(1 - q1) * -140}px) rotate(${-6 - (1 - q1) * 14}deg) scale(${0.7 + 0.3 * Math.min(1.06, p1)})`;
    txt.style.opacity = p2;
    txt.style.transform = `translateX(${(1 - p2) * 40}px)`;
    __idle(sec, t, 0.7, 5, 0.8, 1.5, 0.006, 0);
};"""
    return _doc(
        f'<div class="section"><div class="num">{number}</div>'
        f'<div class="txt">{title}</div></div>', css, js,
    )


def emoji_sticker(
    emoji: str = "🤯",
    width: int = 1080,
    height: int = 1920,
    duration: float = 2.0,
    size: int = 190,
    **kwargs: Any,
) -> str:
    """大 emoji 贴纸 — 过冲弹跳入场 + 常驻微旋转（全片最强戏剧点专用）。"""
    css = _base_css(width, height) + f"""
body {{ display: flex; align-items: center; justify-content: center; }}
.sticker {{
    font-size: {size}px; line-height: 1; opacity: 0;
    filter: drop-shadow(0 14px 20px rgba(0,0,0,0.35));
}}"""

    js = _clamp01_js() + """
window.seekTo = function(t) {
    const el = document.querySelector('.sticker');
    const p = easeOutBack(clamp01((t - 0.1) / 0.5));
    const q = Math.min(1, clamp01((t - 0.1) / 0.5));
    const wob = Math.sin(Math.max(0, t - 0.1) * 3.2) * 4;
    el.style.opacity = q;
    el.style.transform = `scale(${0.3 + 0.7 * p}) rotate(${wob}deg)`;
};"""
    return _doc(f'<div class="sticker">{emoji}</div>', css, js)


# ── v4 新增：画面互动 + 整屏冲击类 ─────────────────────────

def arrow_point(
    x: float = 0.5,
    y: float = 0.40,
    label: str = "",
    direction: str = "up",
    color: str = _RED,
    width: int = 1080,
    height: int = 1920,
    duration: float = 2.0,
    **kwargs: Any,
) -> str:
    """箭头指向 + 标签色块 — 画面空间互动（箭头持续"戳"向画面中的位置）。

    params: x/y (0-1 相对位置), direction (up/down/left/right), color（盘内色）
    兼容 LLM 键名：text/keyword → label。
    """
    label = str(label or kwargs.get("text") or kwargs.get("keyword") or "看这里")
    direction = str(direction).lower()
    if direction not in ("up", "down", "left", "right"):
        direction = "up"
    x, y = _as_float(x, 0.5), _as_float(y, 0.4)
    x, y = min(max(x, 0.08), 0.92), min(max(y, 0.10), 0.90)
    rot = {"up": 0, "down": 180, "left": -90, "right": 90}[direction]
    axis = "Y" if direction in ("up", "down") else "X"
    sign = -1 if direction in ("up", "left") else 1
    flex_dir = "column" if direction in ("up", "down") else "row"
    arrow_first = direction in ("up", "left")
    arrow_html = (
        f'<svg class="arrow" width="92" height="128" viewBox="0 0 100 140">'
        f'<polygon points="50,0 96,58 68,58 68,140 32,140 32,58 4,58" fill="{color}"/></svg>'
    )
    chip_html = f'<div class="chip">{label}</div>'
    body_inner = arrow_html + chip_html if arrow_first else chip_html + arrow_html

    css = _base_css(width, height) + f"""
body {{ display: block; position: relative; }}
.arrow-wrap {{
    position: absolute; left: {x * 100:.1f}%; top: {y * 100:.1f}%;
    transform: translate(-50%, -50%);
    display: flex; flex-direction: {flex_dir}; align-items: center; gap: 12px;
    opacity: 0; max-width: 88%;
}}
.arrow {{
    flex-shrink: 0;
    filter: drop-shadow(0 10px 14px rgba(0,0,0,0.35));
}}
.chip {{
    background: {color}; color: {_text_on(color)};
    font-size: 36px; font-weight: 900; line-height: 1.25;
    padding: 12px 26px; border-radius: 16px;
    box-shadow: 0 6px 0 rgba(0,0,0,0.25), 0 14px 30px rgba(0,0,0,0.30);
    opacity: 0;
}}"""

    js = _clamp01_js() + _idle_js() + f"""
window.seekTo = function(t) {{
    const wrap = document.querySelector('.arrow-wrap');
    const arrow = document.querySelector('.arrow');
    const chip = document.querySelector('.chip');
    const ROT = {rot};
    const pA = easeOutBack(clamp01((t - 0.05) / 0.40));
    const pC = easeOutBack(clamp01((t - 0.25) / 0.30));
    wrap.style.opacity = Math.min(1, clamp01(t / 0.2) * 1.5);
    arrow.style.transform = 'translate{axis}(' + ({sign} * (1 - Math.min(1, pA)) * 60).toFixed(1)
        + 'px) rotate(' + ROT + 'deg) scale(' + (0.6 + 0.4 * Math.min(1, pA)).toFixed(3) + ')';
    chip.style.opacity = Math.min(1, pC * 2);
    chip.style.transform = 'scale(' + (0.6 + 0.4 * Math.min(1, pC)).toFixed(3) + ')';
    if (t >= 0.6) {{
        const poke = Math.sin((t - 0.6) * 3.4) * 7;
        arrow.style.transform = 'translate{axis}(' + ({sign} * poke).toFixed(2)
            + 'px) rotate(' + ROT + 'deg)';
        __idle(chip, t, 0.6, 4, 1.0, 1.6, 0.005, 0);
    }}
}};"""
    return _doc(f'<div class="arrow-wrap">{body_inner}</div>', css, js)


def circle_mark(
    x: float = 0.5,
    y: float = 0.45,
    label: str = "",
    color: str = _RED,
    size: float = 0.30,
    width: int = 1080,
    height: int = 1920,
    duration: float = 2.0,
    **kwargs: Any,
) -> str:
    """手绘感圈注 — SVG 椭圆"画"出来圈住画面中的位置（stroke-dashoffset 描画）。

    params: x/y (0-1 圆心相对位置), color（盘内色）, size (0-1 相对画布宽的直径比例)
    label 可选：圈下方的注释色块。兼容 text/keyword/caption 键名。
    """
    label = str(label or kwargs.get("text") or kwargs.get("keyword") or kwargs.get("caption") or "")
    x, y = min(max(_as_float(x, 0.5), 0.15), 0.85), min(max(_as_float(y, 0.45), 0.15), 0.85)
    import math

    d = max(200.0, _as_float(size, 0.30) * width)
    rx = d / 2 - 12
    ry = d * 0.72 / 2 - 12
    peri = math.pi * (3 * (rx + ry) - math.sqrt((3 * rx + ry) * (rx + 3 * ry)))
    dash = round(peri + 30)
    chip_html = f'<div class="chip">{label}</div>' if label else ""

    css = _base_css(width, height) + f"""
body {{ display: block; position: relative; }}
.ring-wrap {{
    position: absolute;
    left: calc({x * 100:.1f}% - {d / 2:.0f}px);
    top: calc({y * 100:.1f}% - {d * 0.36:.0f}px);
    width: {d:.0f}px;
    display: flex; flex-direction: column; align-items: center; gap: 10px;
    opacity: 0;
}}
.ring {{ filter: drop-shadow(0 8px 12px rgba(0,0,0,0.30)); }}
.ring ellipse {{ stroke-dasharray: {dash}; stroke-dashoffset: {dash}; }}
.chip {{
    background: {color}; color: {_text_on(color)};
    font-size: 34px; font-weight: 900; line-height: 1.25;
    padding: 10px 24px; border-radius: 14px;
    box-shadow: 0 5px 0 rgba(0,0,0,0.25);
    opacity: 0;
}}"""

    js = _clamp01_js() + _idle_js() + f"""
window.seekTo = function(t) {{
    const wrap = document.querySelector('.ring-wrap');
    const el = document.querySelector('.ring ellipse');
    const chip = document.querySelector('.chip');
    wrap.style.opacity = Math.min(1, clamp01(t / 0.15) * 2);
    const p = easeOutCubic(clamp01((t - 0.2) / 0.75));
    el.style.strokeDashoffset = String({dash} * (1 - p));
    if (chip) {{
        const pc = easeOutBack(clamp01((t - 0.85) / 0.30));
        chip.style.opacity = Math.min(1, pc * 2);
        chip.style.transform = 'scale(' + (0.6 + 0.4 * Math.min(1, pc)).toFixed(3) + ')';
    }}
    __idle(wrap, t, 1.0, 4, 1.6, 1.4, 0.004, 0);
}};"""
    return _doc(
        f'<div class="ring-wrap">'
        f'<svg class="ring" width="{d:.0f}" height="{d * 0.72:.0f}" viewBox="0 0 {d:.0f} {d * 0.72:.0f}">'
        f'<ellipse cx="{d / 2:.0f}" cy="{d * 0.36:.0f}" rx="{rx:.0f}" ry="{ry:.0f}" fill="none" '
        f'stroke="{color}" stroke-width="12" stroke-linecap="round" '
        f'transform="rotate(-8 {d / 2:.0f} {d * 0.36:.0f})"/></svg>{chip_html}</div>',
        css, js,
    )


def full_card(
    text: str = "",
    sub: str = "",
    color: str = _RED,
    text_color: str = "",
    width: int = 1080,
    height: int = 1920,
    duration: float = 1.6,
    **kwargs: Any,
) -> str:
    """整屏色块闪卡 — 大字砸入 + 短驻留 + 收束淡出（开场轰炸/结尾收束专用）。

    params: text（大字，必配）, sub（小字可选）, color（盘内色）
    text_color 显式传入须过 WCAG 对比度（不达标自动回退黑/白取优）。
    兼容键名：title/message/keyword → text。
    """
    text = str(text or kwargs.get("title") or kwargs.get("message") or kwargs.get("keyword") or "")
    sub = str(sub or kwargs.get("subtitle") or "")
    # tc 同时用于 110px 大字（≥48px 档 3.0:1）与 38px 副标题（<48px 档
    # 4.5:1）→ 按本卡最严适用档校验；显式色不达标回退自动选色
    # （G1 旁路收口：此前显式 text_color 直接生效绕过对比度防线）
    need = 4.5 if sub else 3.0
    tc = _safe_text_color(text_color, color, need)
    fade_at = max(0.5, float(duration) - 0.35)
    sub_html = f'<div class="sub">{sub}</div>' if sub else ""

    css = _base_css(width, height) + f"""
body {{ display: block; }}
.flash {{
    position: fixed; inset: 0;
    background: {color};
    display: flex; align-items: center; justify-content: center;
    opacity: 0; transform: scale(1.25);
}}
.stripe {{
    position: absolute; bottom: 14%; left: 10%; width: 80%; height: 8px;
    background: {tc}; opacity: 0.30; border-radius: 4px;
    transform: scaleX(0); transform-origin: left center;
}}
.core {{ text-align: center; padding: 0 8%; max-width: 100%; }}
.big {{ font-size: 110px; font-weight: 900; color: {tc}; line-height: 1.25; letter-spacing: 2px; }}
.sub {{ font-size: 38px; color: {tc}; opacity: 0.85; margin-top: 26px; font-weight: 700; }}"""

    js = _clamp01_js() + _idle_js() + f"""
window.seekTo = function(t) {{
    const flash = document.querySelector('.flash');
    const big = document.querySelector('.big');
    const stripe = document.querySelector('.stripe');
    const sub = document.querySelector('.sub');
    const FADE_AT = {fade_at:.2f};
    const pIn = easeOutCubic(clamp01(t / 0.30));
    flash.style.opacity = String(pIn);
    flash.style.transform = 'scale(' + (1.25 - 0.25 * pIn).toFixed(4) + ')';
    const pBig = easeOutBack(clamp01((t - 0.15) / 0.35));
    big.style.transform = 'scale(' + (0.6 + 0.4 * Math.min(1, pBig)).toFixed(3) + ')';
    stripe.style.transform = 'scaleX(' + clamp01((t - 0.35) / 0.30).toFixed(3) + ')';
    if (sub) sub.style.opacity = String(clamp01((t - 0.5) / 0.25) * 0.85);
    if (t >= 0.6) __idle(big, t, 0.6, 0, 0, 2.2, 0.010, 0);
    const fade = clamp01((t - FADE_AT) / 0.30);
    if (fade > 0) {{
        flash.style.opacity = String(pIn * (1 - fade));
        flash.style.transform = 'scale(' + (1 + 0.06 * fade).toFixed(4) + ')';
    }}
}};"""
    return _doc(
        f'<div class="flash"><div class="stripe"></div>'
        f'<div class="core"><div class="big">{text}</div>{sub_html}</div></div>',
        css, js,
    )


def split_compare(
    bad_text: str = "",
    good_text: str = "",
    bad_icon: str = "❌",
    good_icon: str = "✅",
    bad_color: str = _RED,
    good_color: str = _GREEN,
    width: int = 1080,
    height: int = 1920,
    duration: float = 2.5,
    **kwargs: Any,
) -> str:
    """左右分屏 ❌/✅ 对比卡 — 两半色块错拍滑入 + 反相微动（错误做法 vs 正确做法）。

    兼容键名：wrong_text/left_text/bad → bad_text；right_text/good → good_text；
    bad_emoji/good_emoji → 图标。
    """
    bad_text = str(bad_text or kwargs.get("wrong_text") or kwargs.get("left_text") or kwargs.get("bad") or "常见误区")
    good_text = str(good_text or kwargs.get("right_text") or kwargs.get("good") or "正确做法")
    bad_icon = str(kwargs.get("bad_emoji") or bad_icon)
    good_icon = str(kwargs.get("good_emoji") or good_icon)

    css = _base_css(width, height) + f"""
body {{ display: block; }}
.split {{
    position: fixed; inset: 0;
    display: flex; align-items: center; gap: 22px; padding: 0 4%;
}}
.half {{
    flex: 1; height: 44%;
    border-radius: 30px;
    display: flex; flex-direction: column; align-items: center; justify-content: center;
    gap: 16px; padding: 20px;
    box-shadow: 0 10px 0 rgba(0,0,0,0.22), 0 18px 40px rgba(0,0,0,0.30);
}}
.half.bad {{ background: {bad_color}; opacity: 0; transform: translateX(-90px); }}
.half.good {{ background: {good_color}; opacity: 0; transform: translateX(90px); }}
.mk {{ font-size: 120px; line-height: 1; }}
.tx {{
    font-size: 46px; font-weight: 900; line-height: 1.3; text-align: center;
    color: {_text_on(bad_color)};
}}
.half.good .tx {{ color: {_text_on(good_color)}; }}"""

    js = _clamp01_js() + _idle_js() + """
window.seekTo = function(t) {
    const bad = document.querySelector('.half.bad');
    const good = document.querySelector('.half.good');
    const pB = easeOutBack(clamp01((t - 0.05) / 0.40));
    const pG = easeOutBack(clamp01((t - 0.25) / 0.40));
    bad.style.opacity = Math.min(1, pB * 2);
    bad.style.transform = 'translateX(' + ((1 - Math.min(1, pB)) * -90).toFixed(1) + 'px)';
    good.style.opacity = Math.min(1, pG * 2);
    good.style.transform = 'translateX(' + ((1 - Math.min(1, pG)) * 90).toFixed(1) + 'px)';
    __idle(bad, t, 0.8, 5, 1.0, 1.5, 0.006, 0);
    __idle(good, t, 1.0, 5, 1.0, 1.5, 0.006, Math.PI);
};"""
    return _doc(
        f'<div class="split">'
        f'<div class="half bad"><div class="mk">{bad_icon}</div><div class="tx">{bad_text}</div></div>'
        f'<div class="half good"><div class="mk">{good_icon}</div><div class="tx">{good_text}</div></div>'
        f'</div>',
        css, js,
    )


TEMPLATES = {
    "caption_pop": caption_pop,
    "caption_karaoke": caption_karaoke,
    "caption_typewriter": caption_typewriter,
    "keyword_icon": keyword_icon,
    "keyword_highlight": keyword_highlight,
    "stat_card": stat_card,
    "title_card": title_card,
    "number_roll": number_roll,
    "bar_chart_grow": bar_chart_grow,
    "lower_third": lower_third,
    "section_card": section_card,
    "emoji_sticker": emoji_sticker,
    "arrow_point": arrow_point,
    "circle_mark": circle_mark,
    "full_card": full_card,
    "split_compare": split_compare,
}
