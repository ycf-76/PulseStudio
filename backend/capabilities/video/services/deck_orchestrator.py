"""讲解内容 → 要点幻灯片 deck 服务（10.20 二期，2026-10-29）。

用户指令："根据讲解内容进行一个内容渲染" — 转录全文 → LLM 抽要点 →
HTML 幻灯片渲染 → Playwright 截图 → PNG 列表（直接喂
talking_head_overlay 的 deck_images 通道，复用 10.20 matting 全链）。

三重锁对齐：
  - LLM 只产纯数据 JSON（{title, bullets[]} 白名单契约，禁自由 HTML）
  - 坏 JSON/越权字段 → 防御式解析剔除 → 空列表降级（调用方回退纯色底）
  - 渲染是确定性模板填充，零执行自由度
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DECK_PROMPT = """你是幻灯片策划。根据口播讲解的转录全文，提炼 4-{n} 张要点幻灯片
（按内容密度自定张数，宁缺毋滥，不要凑数）。

转录全文（含时间）：
{transcript}
{feedback}
要求：
1. 第一张为开场主张（最抓人的结论/悬念），最后一张为总结/行动号召
2. 每张：title ≤14 字（一句话观点，不是名词）；slide_type 按内容选：
   points 要点卡（默认）/ quote 全屏金句（核心结论，至少 1 张）/
   stat 大数字（有数据时）/ compare 左右对比（有对比内容时）
3. bullets 2-4 条（每条 ≤24 字，具体信息量优先，禁空话）；元素格式
   {{"text": "...", "emphasis": "none"|"number"|"keyword"}} —
   数字类信息标 number、核心词标 keyword（渲染为强调色）
4. 每页配一个 image_prompt：英文画面描述（≤30 词，具象场景+光影氛围，
   竖构图，与该页内容直接相关；不加文字/水印要求）
5. 每页配一个 trigger：{{"text": "老板推单"}} — 从转录原文里挑 4-6 字
   连续字串作该页触发词（口播说到它的瞬间卡片弹出）：必须是转录中
   逐字出现的原话片段，禁止改写/概括/夹标点；各页触发词不重复
6. 只输出 JSON 数组，元素格式 {{"title": "...", "slide_type": "...",
   "image_prompt": "...", "trigger": {{"text": "..."}},
   "bullets": [{{"text": "...", "emphasis": "..."}}]}}
7. 禁止输出 HTML/代码/时间戳/上述之外任何额外字段"""

# 10.24-D 生成后自检：审空话/信息密度/覆盖度/页型混用，达标回复 OK。
_REVIEW_PROMPT = """你是幻灯片审稿人。审查这组幻灯片要点是否达标：
1. 空话套话（"干货满满""赋能"类无信息量表述）
2. 信息密度：bullets 是否具体（数字/事实/动作，而非口号）
3. 覆盖度：是否漏掉转录里的关键内容
4. 页型：slide_type 混用是否合理（该用 quote/stat/compare 而没用）
达标只回复 OK；有问题则输出一句具体可执行的修改意见。

幻灯片 JSON：
{slides_json}

转录摘要：
{transcript}
"""

_FEEDBACK_SECTION = """
用户对上一版幻灯片的修改意见（必须落实）：
{revision}
"""

# 10.21 多主题：样式字段全部由主题 dict 注入 _slide_html（确定性模板
# 填充，零执行自由度）。midnight 为 10.20 原版深色渐变（默认/回退）。
# 10.26：配图罩层渐变（与主题色调联动，保证浮层文字可读）
_DECK_IMAGE_SHADES: dict[str, str] = {
    "midnight": (
        "linear-gradient(160deg, rgba(20,20,40,.82) 0%,"
        " rgba(30,30,63,.68) 55%, rgba(37,37,72,.86) 100%)"
    ),
    "cream": (
        "linear-gradient(160deg, rgba(250,246,238,.85) 0%,"
        " rgba(245,238,226,.74) 55%, rgba(238,228,210,.90) 100%)"
    ),
    "sunset": (
        "linear-gradient(160deg, rgba(59,29,15,.82) 0%,"
        " rgba(138,61,28,.68) 55%, rgba(217,108,44,.86) 100%)"
    ),
}

_DECK_THEMES: dict[str, dict[str, str]] = {
    "midnight": {
        "body_bg": "linear-gradient(160deg,#141428 0%,#1e1e3f 55%,#252548 100%)",
        "text_color": "#f5f5fa",
        "bullet_bg": "rgba(255,255,255,.06)",
        "bullet_border": "rgba(255,255,255,.10)",
        "dot_color": "#ffb84d",
        "accent_bg": "linear-gradient(90deg,#ff4d4d,#ffb84d)",
        "muted_color": "rgba(245,245,250,.35)",
    },
    "cream": {
        "body_bg": "linear-gradient(160deg,#faf6ee 0%,#f4eddd 55%,#efe5d0 100%)",
        "text_color": "#2b2a26",
        "bullet_bg": "rgba(43,42,38,.05)",
        "bullet_border": "rgba(43,42,38,.12)",
        "dot_color": "#c2571b",
        "accent_bg": "linear-gradient(90deg,#c2571b,#e8a23d)",
        "muted_color": "rgba(43,42,38,.40)",
    },
    "sunset": {
        "body_bg": "linear-gradient(160deg,#3b1d0f 0%,#8a3d1c 55%,#d96c2c 100%)",
        "text_color": "#fff8f0",
        "bullet_bg": "rgba(255,255,255,.10)",
        "bullet_border": "rgba(255,255,255,.18)",
        "dot_color": "#ffd166",
        "accent_bg": "linear-gradient(90deg,#ffd166,#ff8c42)",
        "muted_color": "rgba(255,248,240,.40)",
    },
}


# 10.25-E 主题自动选：theme="auto" 时拼进生成 prompt，LLM 按内容
# 气质自选主题；输出改包裹对象 {"theme": "<选中>", "slides": [...]}。
_THEME_AUTO_SECTION = """

【主题自动选】先根据上述讲解内容的气质，从三个主题中选定最匹配的一个：
- midnight：深色蓝紫科技感，适合商务、科技、知识干货类内容
- cream：暖米纸感文艺调，适合生活、情感、读书、慢节奏类内容
- sunset：橙红渐变暖调，适合美食、旅行、热情安利、促销类内容
输出格式改为包裹对象（slides 数组内元素契约不变）：
{"theme": "<midnight|cream|sunset>", "slides": [...]}
"""


_EMPHASIS = {"none", "number", "keyword"}
_SLIDE_TYPES = {"points", "quote", "stat", "compare"}


def _simplify_cn(text: str) -> str:
    """繁→简归一化 — live14 实证 whisper 转录偶发繁体，LLM 照抄后
    标题/要点/触发词带回繁体上屏；观众端必须简体。与
    talking_head_overlay._simplify_cn 同源契约（缺 zhconv 原样返回）。"""
    try:
        from zhconv import convert
    except ImportError:
        return text
    return convert(text, "zh-cn")


def _parse_bullet(b: Any) -> dict[str, str] | None:
    """单条要点归一化 — str（旧格式）→ {text, emphasis:none}；
    dict 校验 text/emphasis 白名单（非法回退 none）；无效 → None。"""
    if isinstance(b, str):
        t = _simplify_cn(b.strip())
        return {"text": t, "emphasis": "none"} if t else None
    if isinstance(b, dict):
        t = b.get("text")
        if not isinstance(t, str) or not t.strip():
            return None
        em = b.get("emphasis")
        return {
            "text": _simplify_cn(t.strip()),
            "emphasis": em if isinstance(em, str) and em in _EMPHASIS else "none",
        }
    return None


def parse_deck_json(raw: str) -> list[dict[str, Any]]:
    """防御式解析 → 白名单 [{title, slide_type, bullets[{text,emphasis}]}]。

    10.24 富要点契约：emphasis ∈ none/number/keyword 白名单（非法回退
    none）；slide_type ∈ points/quote/stat/compare（非法回退 points）。
    旧格式 bullets[str] 自动映射。坏 JSON / 非数组 / 字段越权或类型
    不符 → 剔除该张；全部无效 → []。
    """
    m = re.search(r"\[.*\]", raw, re.S)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    slides = []
    for item in data:
        if not isinstance(item, dict):
            continue
        title = item.get("title")
        bullets = item.get("bullets")
        if not isinstance(title, str) or not title.strip():
            continue
        if not isinstance(bullets, list) or not bullets:
            continue
        clean = [p for b in bullets if (p := _parse_bullet(b))]
        if not clean:
            continue
        stype = item.get("slide_type")
        # 10.26：image_prompt 可选画面描述（生图用）；非 str 丢弃、超长截 200
        ip = item.get("image_prompt")
        ip = ip.strip()[:200] if isinstance(ip, str) and ip.strip() else None
        d: dict[str, Any] = {
            "title": _simplify_cn(title.strip()[:20]),
            "slide_type": (
                stype if isinstance(stype, str) and stype in _SLIDE_TYPES
                else "points"
            ),
            "bullets": clean[:4],
        }
        if ip:
            d["image_prompt"] = ip
        # M2-P0-2 触发词：dict.text 白名单收录（裸 str 容错），
        # 无效/缺失丢弃 → 调用方回退句边界（绝不阻断）
        tg = item.get("trigger")
        tt = (
            tg.get("text") if isinstance(tg, dict)
            else (tg if isinstance(tg, str) else None)
        )
        if isinstance(tt, str) and tt.strip():
            d["trigger"] = {"text": _simplify_cn(tt.strip()[:12])}
        slides.append(d)
    return slides


def parse_deck_theme(raw: str) -> str | None:
    """10.25-E：从 LLM 包裹对象提取自选主题（白名单校验）。

    裸数组 / 包裹缺失 / 坏 JSON / 主题越权 → None（调用方降级 midnight）。
    """
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    th = data.get("theme")
    return th if isinstance(th, str) and th in _DECK_THEMES else None


def _bullet_pair(b: Any) -> tuple[str, str]:
    """渲染侧要点归一化 — 兼容 str（旧持久化 JSON）与 dict 富要点。"""
    if isinstance(b, dict):
        return str(b.get("text", "")), str(b.get("emphasis", "none"))
    return str(b), "none"


def _bullet_html(b: Any, show: bool = True, tw_k: int | None = None) -> str:
    """单条要点卡 — emphasis: number 渐变大字 / keyword 主题强调色。
    show（N5 层渲染）：False → visibility:hidden（占位保布局）。
    tw_k（N6 打字机）：卡片/文字加 id 供 JS 驱动，文字逐字 span 化
    （number 渐变字除外，整块淡入，见 _typewriter_plan）。"""
    text, em = _bullet_pair(b)
    cls = {"number": ' class="em-num"', "keyword": ' class="em-kw"'}.get(em, "")
    if tw_k is None:
        return (
            f'<div class="bullet" style="visibility:'
            f"{'visible' if show else 'hidden'}\">"
            f'<span class="dot"></span>'
            f"<p{cls}>{_esc(text)}</p></div>"
        )
    # N6 打字机：卡片 opacity:0 起步由 JS 淡入；文字逐字 span 化
    # （number 渐变字除外 — background-clip:text 逐字透明度失效，
    # 整块淡入）
    if em == "number":
        body_html = _esc(text)
    else:
        body_html = "".join(
            f'<span class="tw">{_esc(c)}</span>' for c in text
        )
    return (
        f'<div class="bullet" id="twc-b{tw_k}" style="opacity:0">'
        f'<span class="dot"></span>'
        f'<p{cls} id="tw-b{tw_k}">{body_html}</p></div>'
    )


_LAYER_FADE = 0.30
_LAYER_STAGGER = 0.35
_LAYER_SHIFT = 36

# N6 打字机：字速快于口播语速（中文 4-5 字/秒），流式感明显不拖沓；
# 单字透明度渐显由 JS 逐步计算（无 CSS 过渡 → 帧确定性截图）
_TYPEWRITER_CPS = 9.0
_TYPEWRITER_FADE = 0.08

# 末页尾漂移：handoff 合成链中让位 xfade（td 0.5）+ acrossfade（0.6）
# 重叠吃掉视频流尾段 — live24 实测成片视频流止于 sum(d)-1.06
# （音频到 32.20s，视频帧止 31.56s，与层动画版一致为链路固有）。
# 末页按 dur-_TAIL_DRAIN 计算可用时长 → 文字在此前流完，
# 避免最后一 bullet 在结尾被截（N6 交付实测问题）。
_TAIL_DRAIN = 1.2


def _slide_layers_plan(slide: dict[str, Any]) -> list[dict[str, Any]]:
    """N5：页内元素分层计划（纯函数）。

    points 页 → [bg, head, b0..bn]：bg 常驻打底；head 标题层
    t0=0.10 先落；要点层 stagger 0.35s 逐条入场；文字层统一
    fade _LAYER_FADE 秒 + 上滑 _LAYER_SHIFT px 落定。
    非 points 页（quote/stat/compare）→ 整页单层 ["page"]
    （层动画 v1 只做要点页）。
    """
    if slide.get("slide_type") not in (None, "points"):
        return [{"name": "page", "t0": 0.0, "dur": 0.0, "y0": 0}]
    layers = [
        {"name": "bg", "t0": 0.0, "dur": 0.0, "y0": 0},
        {"name": "head", "t0": 0.10, "dur": _LAYER_FADE, "y0": _LAYER_SHIFT},
    ]
    for k in range(len(slide.get("bullets") or [])):
        layers.append({
            "name": f"b{k}", "t0": 0.45 + k * _LAYER_STAGGER,
            "dur": _LAYER_FADE, "y0": _LAYER_SHIFT,
        })
    return layers


def _typewriter_plan(slide: dict[str, Any]) -> dict[str, dict[str, Any]] | None:
    """N6：打字机调度（纯函数）。

    points 页 → {"head": {t0, chars}, "b{k}": {...}}，t0 与
    _slide_layers_plan 层入场同源同值（条间错峰节奏不变，只把整条
    fade 细化为条内逐字浮现）。emphasis=number 的要点整块淡入
    （chars=False）— 渐变字 background-clip:text 作用在 p 上，逐字
    span 透明度会让文字整体不可见。非 points 页 → None（整页淡入，
    短页金句流式会截断）。
    """
    if slide.get("slide_type") not in (None, "points"):
        return None
    plan: dict[str, dict[str, Any]] = {"head": {"t0": 0.10, "chars": True}}
    for k, b in enumerate(slide.get("bullets") or []):
        _, em = _bullet_pair(b)
        plan[f"b{k}"] = {
            "t0": 0.45 + k * _LAYER_STAGGER,
            "chars": em != "number",
        }
    return plan


def _typewriter_t_done(
    plan: dict[str, dict[str, Any]], slide: dict[str, Any], cps: float,
) -> float:
    """页内文字全部完成时刻（纯函数）— 各层 t0 + 字数/cps
    （+单字 fade / 卡片 0.15）取最大。"""
    bullets = slide.get("bullets") or []
    ends = [0.15]
    for k, meta in plan.items():
        if meta["chars"]:
            n_chars = (
                len(str(slide.get("title") or "")) if k == "head"
                else len(_bullet_pair(bullets[int(k[1:])])[0])
            )
            ends.append(meta["t0"] + n_chars / cps + _TYPEWRITER_FADE)
        else:
            ends.append(meta["t0"] + _TYPEWRITER_FADE)
        ends.append(meta["t0"] + 0.15)
    return max(ends)


def _typewriter_cps(
    plan: dict[str, dict[str, Any]] | None, slide: dict[str, Any],
    avail: float,
) -> float:
    """N6：页级有效字速（纯函数）— 最紧文字层在 avail 内流不完时
    按其字数/余量提速，保证短页/片尾文字流完不截断（live20 短页
    fade 截断同源教训）。长页保持基准字速。页短到无流式余地
    （avail ≤ t0+fade）→ 保持基准速尽力而为。"""
    if not plan:
        return _TYPEWRITER_CPS
    bullets = slide.get("bullets") or []
    need = _TYPEWRITER_CPS
    for k, meta in plan.items():
        if not meta["chars"]:
            continue
        n_chars = (
            len(str(slide.get("title") or "")) if k == "head"
            else len(_bullet_pair(bullets[int(k[1:])])[0])
        )
        slack = avail - meta["t0"] - _TYPEWRITER_FADE
        if slack <= 0:
            continue
        need = max(need, n_chars / slack)
    return need


def _typewriter_avail(dur: float, is_last: bool, td: float) -> float:
    """N6：页可用时长（纯函数）— 非末页 dur+td-0.3（原公式）；
    末页扣 _TAIL_DRAIN（链路尾漂移，见常量注释）。"""
    return (dur - _TAIL_DRAIN) if is_last else (dur + td - 0.3)


def _slide_html(
    title: str, bullets: list[Any], idx: int, total: int,
    person_clearance_px: int = 0,
    theme: str = "midnight",
    slide_type: str = "points",
    image: str | None = None,
    top_right_clear_px: int = 0,
    only_layer: str | None = None,
    typewriter: bool = False,
    tw_cps: float | None = None,
) -> str:
    """确定性幻灯片模板 — 竖版 9:16，四页型（10.24-B 内容丰富度）。

    points 要点卡（默认/回退）/ quote 全屏金句 / stat 大数字 /
    compare 左右两栏。emphasis（10.24-A）：number 渐变大字、
    keyword 主题强调色。person_clearance_px > 0 时底部预留该高度
    （人物小窗让位，10.20 优化）。top_right_clear_px > 0 时标题区右侧让位 + 页码移左上（deck 作主画面配顶部小窗防遮挡，N2）。only_layer（N5）：层渲染
    选择器 — None 全显（现行为）；"bg" 纯背景+页脚；"head" 标题层；
    "b{k}" 第 k 条要点层（其余 visibility:hidden 占位保布局一致，
    文字层 body 透明配 omit_background 截图）。theme 非法回退 midnight（10.21）。
    10.26：image（图片路径）有值且可读 → 全屏背景 + 主题罩层 +
    文字浮层；缺失/读失败静默回退纯文字。typewriter（N6）：True 且
    points 页 → 文字逐字 span 化 + JS 调度（__twSet）打字机流式；
    非 points 页 / 缺省不流式。
    """
    t = dict(_DECK_THEMES.get(theme) or _DECK_THEMES["midnight"])
    t["image_shade"] = _DECK_IMAGE_SHADES.get(
        theme, _DECK_IMAGE_SHADES["midnight"]
    )
    st = slide_type if slide_type in _SLIDE_TYPES else "points"
    # N6 打字机调度：points 页文字逐字浮现（None → 整页淡入）
    tw_plan = (
        _typewriter_plan(
            {"slide_type": st, "title": title, "bullets": bullets},
        )
        if typewriter else None
    )
    # N5 层选择器：各元素可见性（hidden 占位保布局）；文字层透明底
    vis_idx = only_layer is None or only_layer == "head"
    vis_title = vis_idx
    vis_total = only_layer is None or only_layer == "bg"
    b_sel = -1
    if only_layer and only_layer.startswith("b") and only_layer[1:].isdigit():
        b_sel = int(only_layer[1:])
    transparent = only_layer == "head" or b_sel >= 0
    body_bg_css = "transparent" if transparent else t["body_bg"]

    # 10.26：配图 → 全屏背景（base64 内嵌，set_content 可加载）+
    # 主题罩层 + 文字浮层；文件缺失/无图 → 纯文字版式（CSS 一并去除）
    bg = ""
    bg_css = ""
    if image and not transparent:
        try:
            from base64 import b64encode

            data = Path(image).read_bytes()
            bg = (
                f'<img class="bg-img" src="data:image/jpeg;base64,'
                f'{b64encode(data).decode()}">\n'
                f'<div class="bg-shade"></div>\n'
            )
            bg_css = (
                ".bg-img { position:absolute; inset:0; width:100%;"
                " height:100%; object-fit:cover; }\n"
                f'.bg-shade {{ position:absolute; inset:0;'
                f' background:{t["image_shade"]}; }}'
            )
        except OSError:
            bg = ""
            bg_css = ""
    # N2：顶部右侧让位 — 小窗占位时标题右缩、页码移左上
    idx_pos = "left:110px" if top_right_clear_px > 0 else "right:110px"
    title_clear = (
        f" margin-right:{top_right_clear_px}px;"
        if top_right_clear_px > 0 else ""
    )
    if tw_plan:
        _tw_title = "".join(
            f'<span class="tw">{_esc(c)}</span>' for c in title
        )
        _h1 = f'<h1 id="tw-head">{_tw_title}</h1>'
    else:
        _h1 = (
            f"<h1 style=\"visibility:"
            f"{'visible' if vis_title else 'hidden'}\">{_esc(title)}</h1>"
        )
    head = (
        f'<div class="idx" style="visibility:'
        f"{'visible' if vis_idx else 'hidden'}\">{idx:02d}</div>\n"
        f"{_h1}"
        f'<div class="accent" style="visibility:'
        f"{'visible' if vis_idx else 'hidden'}\"></div>"
    )
    if st == "quote":
        note = ""
        if bullets:
            note_text, _ = _bullet_pair(bullets[0])
            if note_text:
                note = f'<p class="quote-note">{_esc(note_text)}</p>'
        head = (
            f'<div class="idx" style="visibility:'
            f"{'visible' if vis_idx else 'hidden'}\">{idx:02d}</div>\n"
            f'<div class="accent" style="visibility:'
            f"{'visible' if vis_idx else 'hidden'}\"></div>"
        )
        body = f'<h1 class="quote-title">{_esc(title)}</h1>\n{note}'
    elif st == "stat":
        num_text = ""
        rest: list[Any] = []
        for b in bullets:
            bt, em = _bullet_pair(b)
            if not num_text and (em == "number" or bt[:1].isdigit()):
                num_text = bt
            else:
                rest.append(b)
        items = "\n".join(_bullet_html(b) for b in rest)
        body = f'<div class="stat-num">{_esc(num_text or "—")}</div>\n{items}'
    elif st == "compare":
        left = bullets[0::2]
        right = bullets[1::2]
        body = (
            '<div class="compare-grid">\n'
            f'<div class="compare-col">{"".join(_bullet_html(b) for b in left)}</div>\n'
            f'<div class="compare-col">{"".join(_bullet_html(b) for b in right)}</div>\n'
            "</div>"
        )
    else:
        if tw_plan:
            body = "\n".join(
                _bullet_html(b, tw_k=i) for i, b in enumerate(bullets)
            )
        else:
            body = "\n".join(
                _bullet_html(
                    b,
                    show=(
                        only_layer is None
                        or (b_sel >= 0 and i == b_sel)
                    ),
                )
                for i, b in enumerate(bullets)
            )
    # N6：打字机 JS — 逐字透明度由 JS 按调度计算（无 CSS 过渡，
    # 截图前 set 态 → 帧确定性）；卡片 0.15s 淡入，单字 0.08s 渐显
    tw_js = ""
    if tw_plan:
        sched = json.dumps(tw_plan, ensure_ascii=False)
        tw_js = (
            "<script>window.__twCps=" + repr(tw_cps or _TYPEWRITER_CPS)
            + ";window.__twFade=" + repr(_TYPEWRITER_FADE)
            + ";window.__tw=" + sched + ";window.__twSet=function(t){"
            "var S=window.__tw;for(var k in S){var s=S[k];"
            'var el=document.getElementById("tw-"+k);'
            "if(el){if(s.chars){var ch=el.children;"
            "for(var i=0;i<ch.length;i++){"
            "var a=(t-s.t0-i/window.__twCps)/window.__twFade;"
            "ch[i].style.opacity=a<0?0:(a>1?1:a);}}"
            "else{var a2=(t-s.t0)/window.__twFade;"
            "el.style.opacity=a2<0?0:(a2>1?1:a2);}}"
            'var card=document.getElementById("twc-"+k);'
            "if(card){var b=(t-s.t0)/0.15;"
            "card.style.opacity=b<0?0:(b>1?1:b);}}};</script>"
        )
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ width:1080px; height:1920px; overflow:hidden;
  font-family:"Microsoft YaHei","PingFang SC",sans-serif;
  background:{body_bg_css};
  color:{t["text_color"]}; position:relative; }}
.fg {{ position:absolute; inset:0; display:flex; flex-direction:column;
  justify-content:center; padding:0 110px {person_clearance_px}px; }}
{bg_css}
.accent {{ width:96px; height:14px; border-radius:7px;
  background:{t["accent_bg"]}; margin:0 0 56px; }}
.idx {{ position:absolute; {idx_pos}; top:64px; font-size:44px;
  color:{t["muted_color"]}; font-weight:700; letter-spacing:2px; }}
h1 {{ font-size:96px; line-height:1.28; font-weight:900;
  margin:70px 0 48px; letter-spacing:1px;{title_clear} }}
.bullet {{ display:flex; align-items:flex-start; gap:28px;
  background:{t["bullet_bg"]}; border:1px solid {t["bullet_border"]};
  border-radius:24px; padding:40px 44px; margin-bottom:36px; }}
.dot {{ flex:none; width:16px; height:16px; border-radius:50%;
  background:{t["dot_color"]}; margin-top:22px; }}
.bullet p {{ font-size:52px; line-height:1.5; font-weight:500; }}
.em-num {{ font-size:66px; font-weight:900; line-height:1.4;
  background:{t["accent_bg"]}; -webkit-background-clip:text;
  -webkit-text-fill-color:transparent; }}
.em-kw {{ color:{t["dot_color"]}; font-weight:800; }}
.quote-title {{ font-size:112px; line-height:1.42; font-weight:900;
  margin:80px 0 56px; letter-spacing:2px;{title_clear} }}
.quote-note {{ font-size:46px; color:{t["muted_color"]}; line-height:1.6; }}
.stat-num {{ font-size:230px; font-weight:900; line-height:1.15;
  background:{t["accent_bg"]}; -webkit-background-clip:text;
  -webkit-text-fill-color:transparent; margin:48px 0; }}
.compare-grid {{ display:grid; grid-template-columns:1fr 1fr;
  gap:32px; align-items:start; }}
.compare-col .bullet {{ padding:30px 32px; margin-bottom:28px; }}
.compare-col .bullet p {{ font-size:42px; }}
.total {{ position:absolute; left:110px; bottom:96px; font-size:40px;
  color:{t["muted_color"]}; letter-spacing:4px; }}
</style></head><body>
{bg}<div class="fg">
{head}
{body}
<div class="total" style="visibility:{'visible' if vis_total else 'hidden'}">{idx} / {total}</div>
</div>
{tw_js}</body></html>"""


def _esc(s: str) -> str:
    return (
        s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def render_deck_slides(
    slides: list[dict[str, Any]],
    out_dir: Path,
    width: int = 1080,
    height: int = 1920,
    person_clearance_px: int = 0,
    theme: str = "midnight",
    top_right_clear_px: int = 0,
) -> list[Path]:
    """幻灯片 JSON → HTML → Playwright 逐页截图 PNG（同步，可 to_thread）。"""
    from playwright.sync_api import sync_playwright

    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": width, "height": height})
        try:
            for i, s in enumerate(slides, 1):
                html = _slide_html(
                    s["title"], s["bullets"], i, len(slides),
                    person_clearance_px=person_clearance_px,
                    theme=theme,
                    slide_type=s.get("slide_type", "points"),
                    image=s.get("image"),
                    top_right_clear_px=top_right_clear_px,
                )
                page.set_content(html, wait_until="networkidle")
                shot = out_dir / f"slide_{i:02d}.png"
                page.screenshot(path=str(shot))
                paths.append(shot)
        finally:
            browser.close()
    return paths


def render_deck_from_slides(
    slides_json_path: str | Path,
    out_dir: Path,
    theme: str | None = None,
    width: int = 1080,
    height: int = 1920,
    person_clearance_px: int = 0,
    top_right_clear_px: int = 0,
) -> list[Path]:
    """从已落盘 slides.json 重渲染（10.22 确认闸门 retheme/approve 用）。

    纯确定性：零 LLM 调用。theme 显式指定时覆盖 JSON 内记录的首轮主题
    （retheme）；缺省用 JSON 内 theme（approve 同版重放）。
    JSON 不可读/缺 slides → []（调用方降级）。
    """
    try:
        data = json.loads(Path(slides_json_path).read_text(encoding="utf-8"))
        slides = data.get("slides")
        if not isinstance(slides, list) or not slides:
            return []
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f"[deck] slides.json 不可读（降级 []）：{e}")
        return []
    return render_deck_slides(
        slides, out_dir, width, height,
        person_clearance_px=person_clearance_px,
        top_right_clear_px=top_right_clear_px,
        theme=theme or data.get("theme") or "midnight",
    )


def render_deck_layers(
    slides: list[dict[str, Any]],
    out_dir: Path,
    width: int = 1080,
    height: int = 1920,
    person_clearance_px: int = 0,
    theme: str = "midnight",
    top_right_clear_px: int = 0,
) -> list[list[dict[str, Any]]]:
    """N5：deck 元素分层渲染 — 每层全屏 PNG（文字层透明底）。

    points 页按 _slide_layers_plan 拆 bg/head/逐条要点层
    （visibility 占位保布局层间一致）；非 points 页整页单层。
    返回 pages[i][j] = {name, t0, dur, y0, png}（合成动效契约）。
    渲染异常向上抛（调用方降级走整页链，不阻断出片）。
    """
    from playwright.sync_api import sync_playwright

    out_dir.mkdir(parents=True, exist_ok=True)
    pages: list[list[dict[str, Any]]] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": width, "height": height})
        try:
            for i, sl in enumerate(slides, 1):
                plan = _slide_layers_plan(sl)
                layers: list[dict[str, Any]] = []
                for j, ly in enumerate(plan):
                    name = ly["name"]
                    html = _slide_html(
                        sl["title"], sl["bullets"], i, len(slides),
                        person_clearance_px=person_clearance_px,
                        theme=theme,
                        slide_type=sl.get("slide_type", "points"),
                        image=sl.get("image"),
                        top_right_clear_px=top_right_clear_px,
                        only_layer=None if name == "page" else name,
                    )
                    page.set_content(html, wait_until="networkidle")
                    shot = out_dir / f"L{i:02d}_{j}_{name}.png"
                    page.screenshot(path=str(shot), omit_background=True)
                    layers.append({**ly, "png": shot})
                pages.append(layers)
        finally:
            browser.close()
    return pages


def render_deck_typewriter(
    slides: list[dict[str, Any]],
    out_dir: Path,
    durations: list[float],
    td: float = 0.5,
    fps: int = 30,
    width: int = 1080,
    height: int = 1920,
    person_clearance_px: int = 0,
    theme: str = "midnight",
    top_right_clear_px: int = 0,
) -> list[Path]:
    """N6：打字机逐帧渲染 — 每页 PNG 帧序列（JS 确定性驱动）。

    每页帧数 = ceil((d_i + td) * fps)；帧 t 由 window.__twSet 精确
    设态后截图（逐字透明度由 JS 计算，无 CSS 过渡 → 帧确定性）。
    页内文字全部完成后（t_done）的帧直接复制末帧，不重复截图
    （长页大头是静止帧，复制 ~1ms/帧 vs 截图 ~100ms/帧）。
    非 points 页（调度 None）截首帧后全复制。
    返回 pages[i] = 帧目录（%06d.png）。渲染异常向上抛
    （调用方降级走层动画链，不阻断出片）。
    """
    import math
    import shutil

    from playwright.sync_api import sync_playwright

    if len(durations) != len(slides):
        raise ValueError(
            f"typewriter durations mismatch: {len(durations)} "
            f"for {len(slides)} slides"
        )
    out_dir.mkdir(parents=True, exist_ok=True)
    dirs: list[Path] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": width, "height": height})
        try:
            for i, sl in enumerate(slides):
                plan = _typewriter_plan(sl)
                # 短页/片尾提速：流完需时超页可用时长 → 按比例提字速
                # （末页扣尾漂移 _TAIL_DRAIN，保证结尾不截字）
                cps_eff = _typewriter_cps(
                    plan, sl,
                    _typewriter_avail(durations[i], i == len(slides) - 1, td),
                )
                html = _slide_html(
                    sl["title"], sl["bullets"], i + 1, len(slides),
                    person_clearance_px=person_clearance_px,
                    theme=theme,
                    slide_type=sl.get("slide_type", "points"),
                    image=sl.get("image"),
                    top_right_clear_px=top_right_clear_px,
                    typewriter=True,
                    tw_cps=cps_eff,
                )
                n = math.ceil((durations[i] + td) * fps)
                page_dir = out_dir / f"page_{i:02d}"
                page_dir.mkdir(parents=True, exist_ok=True)
                page.set_content(html, wait_until="networkidle")
                # 文字完成时刻（提速字速下复算）
                t_done = (
                    _typewriter_t_done(plan, sl, cps_eff)
                    if plan else 0.0
                )
                last: Path | None = None
                for j in range(n):
                    t = j / fps
                    shot = page_dir / f"{j:06d}.png"
                    if last is not None and t >= t_done:
                        shutil.copyfile(last, shot)
                        continue
                    if plan is not None:
                        page.evaluate(f"window.__twSet({t:.4f})")
                    page.screenshot(path=str(shot))
                    last = shot
                dirs.append(page_dir)
        finally:
            browser.close()
    return dirs


# 10.31 M1-P0-3：品牌强调色 — 与 talking_head_overlay._KARAOKE_ACCENT
# 同源，hex 值必须一致（karaoke 当前词 / decor keyword 同一强调色）
_HIGHLIGHT = "#39E508"

_DECOR_BAND_H = 340

_TEXT_STROKE = (
    "text-shadow:3px 0 0 #000,-3px 0 0 #000,0 3px 0 #000,0 -3px 0 #000,"
    "2px 2px 0 #000,-2px 2px 0 #000,2px -2px 0 #000,-2px -2px 0 #000,"
    "0 6px 18px rgba(0,0,0,.55);"
)


def _decor_card_html(slide: dict[str, Any], theme: str = "midnight") -> str:
    """10.31 M1-P0-3：轻装饰卡 — 无底框，白字黑描边直接上画面。

    contrast, not decoration（RemotionUI）：退役旧实色圆角卡底框，
    改顶部黑色线性渐变压暗保可读；keyword 要点品牌高亮色；金句页
    海报大字，配图降为小圆图（无则省）。body background transparent
    — Playwright omit_background 截图保 alpha。theme 参数保留兼容
    旧调用方，颜色不再依赖主题卡面。
    """
    st = slide.get("slide_type") if slide.get("slide_type") in _SLIDE_TYPES else "points"
    is_quote = st == "quote"
    # live13 实证：quote 64px 海报字 + 3 要点超出 340px 高度带 →
    # 带要点一律 46px；金句页要点封顶 2 条（海报让位内容适配）
    bullets = list(slide.get("bullets") or [])
    if is_quote:
        bullets = bullets[:2]
    title_px = 64 if (is_quote and not bullets) else 46

    thumb_html = ""
    if slide.get("image"):
        try:
            from base64 import b64encode

            data = Path(slide["image"]).read_bytes()
            thumb_html = (
                f'<img class="thumb" src="data:image/jpeg;base64,'
                f'{b64encode(data).decode()}">'
            )
        except Exception:
            thumb_html = ""

    bullets_html = ""
    for b in bullets:
        text, emph = _bullet_pair(b)
        body = (
            f'<em class="kw">{_esc(text)}</em>'
            if emph == "keyword" else _esc(text)
        )
        bullets_html += f'<div class="bl">{body}</div>'

    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ background:transparent; width:720px; height:{_DECOR_BAND_H}px;
  font-family:"Microsoft YaHei","PingFang SC",sans-serif; }}
.band {{ position:relative; width:720px; height:{_DECOR_BAND_H}px;
  overflow:hidden; }}
.grad {{ position:absolute; left:0; top:0; right:0; bottom:0;
  background:linear-gradient(180deg,rgba(0,0,0,.62),rgba(0,0,0,0)); }}
.wrap {{ position:relative; display:flex; align-items:center; gap:20px;
  padding:40px 44px 0; }}
.thumb {{ width:116px; height:116px; object-fit:cover; border-radius:50%;
  flex-shrink:0; opacity:.92; }}
.txt {{ flex:1; display:flex; flex-direction:column; gap:12px; }}
.title {{ font-size:{title_px}px; font-weight:900; color:#fff;
  line-height:1.25; {_TEXT_STROKE} }}
.bl {{ font-size:24px; font-weight:700; color:#fff; line-height:1.35;
  {_TEXT_STROKE} }}
.kw {{ font-style:normal; color:{_HIGHLIGHT}; }}
</style></head><body>
<div class="band"><div class="grad"></div><div class="wrap">
{thumb_html}<div class="txt">
<div class="title">{_esc(slide.get("title", ""))}</div>
{bullets_html}
</div></div></div>
</body></html>"""


def render_decor_cards(
    slides: list[dict[str, Any]],
    out_dir: Path,
    theme: str | None = None,
) -> list[Path]:
    """10.31 M1-P0-3：slides → 轻装饰卡 PNG（720×340 透明底高度带）。"""
    from playwright.sync_api import sync_playwright

    out_dir.mkdir(parents=True, exist_ok=True)
    first_theme = theme or (
        "midnight"  # theme=None 时由调用方传 JSON 内主题
    )
    paths: list[Path] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(
            viewport={"width": 720, "height": _DECOR_BAND_H},
        )
        try:
            for i, s in enumerate(slides, 1):
                html = _decor_card_html(s, theme=first_theme)
                page.set_content(html, wait_until="networkidle")
                shot = out_dir / f"decor_{i:02d}.png"
                page.screenshot(path=str(shot), omit_background=True)
                paths.append(shot)
        finally:
            browser.close()
    return paths


async def _generate_slide_images(
    slides: list[dict[str, Any]], out_dir: Path,
) -> int:
    """10.26：逐页万相生图（image_prompt → img_NN.jpg → slides[i]["image"]）。

    key 缺失 → 0（零调用）；单页失败 → 跳过该页其余继续（绝不阻断）；
    无 image_prompt 的页跳过。返回成功张数。
    """
    from app.config import get_settings

    key = get_settings().dashscope_api_key
    if not key:
        return 0
    from capabilities.image_gen.adapters.image_gen import WanxAdapter

    adapter = WanxAdapter(api_key=key)
    ok = 0
    for i, s in enumerate(slides, 1):
        ip = s.get("image_prompt")
        if not ip:
            continue
        try:
            data = (await adapter.generate(ip, size="720x1280"))[0]
            p = out_dir / f"img_{i:02d}.jpg"
            p.write_bytes(data)
            s["image"] = str(p)
            ok += 1
            logger.info(f"[deck] 配图 {i}/{len(slides)} -> {p.name}")
        except Exception as e:  # noqa: BLE001 — 单页失败降级纯文字
            logger.warning(f"[deck] 配图 {i} 失败（降级纯文字）：{e}")
    return ok


async def _review_deck(
    llm: Any, slides: list[dict[str, Any]], transcript_text: str,
) -> str:
    """10.24-D 自检审查 — 返回修改意见；达标/异常返回 ""（不阻断）。"""
    try:
        from app.adapters.llm_base import llm_prompt_to_text

        raw = await llm_prompt_to_text(llm, _REVIEW_PROMPT.format(
            slides_json=json.dumps(slides, ensure_ascii=False),
            transcript=transcript_text[:1500],
        ))
        fb = (raw or "").strip()
        if not fb or fb.rstrip(".。！!").upper() == "OK":
            return ""
        return fb
    except Exception as e:
        logger.warning(f"[deck] 自检审查失败（跳过重试）：{e}")
        return ""


async def generate_deck_slides(
    transcript_text: str,
    llm: Any,
    out_dir: Path,
    max_slides: int = 6,
    width: int = 1080,
    height: int = 1920,
    person_clearance_px: int = 0,
    theme: str = "midnight",
    feedback: str | None = None,
    top_right_clear_px: int = 0,
) -> list[Path]:
    """转录全文 → LLM 要点契约 → 幻灯片 PNG 列表。

    降级契约：LLM 失败/解析空/渲染异常 → []（调用方回退纯色底主画面）。
    解析成功后落盘 slides.json（theme/transcript/slides — 10.22 确认
    闸门 retheme 零 LLM 重渲染 + regenerate 反馈的数据源）。
    """
    import asyncio

    if not transcript_text.strip():
        return []
    try:
        from app.adapters.llm_base import llm_prompt_to_text

        fb = (
            _FEEDBACK_SECTION.format(revision=feedback.strip())
            if feedback and feedback.strip() else ""
        )
        auto = theme == "auto"
        prompt = _DECK_PROMPT.format(
            n=max_slides, transcript=transcript_text[:8000], feedback=fb,
        ) + (_THEME_AUTO_SECTION if auto else "")
        raw = await llm_prompt_to_text(llm, prompt)
        # 10.25-E：auto → LLM 包裹对象自选主题；越权/裸数组降级 midnight
        actual = (parse_deck_theme(raw) or "midnight") if auto else theme
        slides = parse_deck_json(raw)
        if not slides:
            logger.warning("[deck] LLM 输出解析为空，降级 []")
            return []
        # 10.24-D 生成后自检重试 ≤2 轮：审查意见拼 feedback 重生成；
        # 重试解析空 / 审查异常保留上一版（绝不阻断，降级契约）
        review_fb = await _review_deck(llm, slides, transcript_text)
        for _ in range(2):
            if not review_fb:
                break
            retry_prompt = _DECK_PROMPT.format(
                n=max_slides, transcript=transcript_text[:8000],
                feedback=_FEEDBACK_SECTION.format(revision=review_fb),
            )
            raw = await llm_prompt_to_text(llm, retry_prompt)
            retry_slides = parse_deck_json(raw)
            if retry_slides:
                slides = retry_slides
                if auto:
                    actual = parse_deck_theme(raw) or actual
            review_fb = await _review_deck(llm, slides, transcript_text)
        n = min(len(slides), max_slides)
        slides = slides[:n]
        out_dir.mkdir(parents=True, exist_ok=True)
        # 10.26：自动配图（image_prompt → 万相生图 → image 路径进 JSON）
        await _generate_slide_images(slides, out_dir)
        (out_dir / "slides.json").write_text(json.dumps({
            "theme": actual, "transcript": transcript_text,
            "slides": slides,
        }, ensure_ascii=False, indent=1), encoding="utf-8")
        logger.info(f"[deck] LLM 产出 {len(slides)} 张幻灯片（theme={actual}）")
        return await asyncio.to_thread(
            render_deck_slides, slides, out_dir, width, height,
            person_clearance_px, theme=actual,
            top_right_clear_px=top_right_clear_px,
        )
    except Exception as e:
        logger.warning(f"[deck] 生成失败（降级 []）：{e}")
        return []
