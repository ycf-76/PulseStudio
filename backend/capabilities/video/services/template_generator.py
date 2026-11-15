"""P1 模板生成器 — LLM 运行时生成新叠加模板 + 契约校验入库。

对标 Remotion Claude 的 "not template fill-ins"：视觉空间不被 16 个
预写模板封顶，需求匹配不到时 LLM 现场生成。

安全模型（红线）：
  LLM 产出**只是数据** — 参数化 HTML（{var} 占位符），Python 侧
  GeneratedTemplate 包装成 (width, height, duration, **kwargs) -> str。
  全模块无 exec/eval — LLM 永远不产出可执行代码。

契约校验（继承十轮 demo 全部防线，静态 + 可选渲染冒烟）：
  1. seekTo 契约：window.seekTo = function 必须存在（worker 逐帧渲染）
  2. 禁真实时钟：setInterval/setTimeout/rAF/performance.now/new Date
     （确定性渲染 — 动画只由 seekTo(t) 驱动）；同理禁 CSS
     animation/@keyframes/transition（真实时间驱动，破坏逐帧确定性）
  3. 选择器审计：JS 引用的 .class 必须在 DOM 中（o17 事故），
     null guard（if (x)）豁免条件渲染
  4. {color} 消费：palette 规划↔渲染契约闭合（run7 教训）
  5. 占位符白名单：防 LLM 编造变量名 → 渲染期留死占位符
  6. 文字/背景对比度：WCAG AA（≥48px 大字 3.0:1，其余 4.5:1）—
     2026-10-28 排版审计转化（VLM 反复报"白字压色块看不清"，根因
     _text_on 选色缺陷，生成入口直接拦下同类问题）

生成流程（复用 P0 闭环反馈模式）：
  prompt → LLM → 提取 HTML → 校验 → 失败把错误清单拼回 prompt 重试
  （上限 2 次）→ 通过则注册 GENERATED + 落盘 JSON（跨进程复用，
  "生成后零边际成本"）→ 全败返回 None（管线降级回内置 16 模板）。
"""

from __future__ import annotations

import json
import logging
import re
from html import escape
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 落盘目录（测试 monkeypatch 此符号）
_STORE_DIR = Path(__file__).resolve().parent.parent.parent.parent / "outputs" / "generated_templates"

# 进程内注册表：name -> GeneratedTemplate
GENERATED: dict[str, "GeneratedTemplate"] = {}

# 占位符白名单：渲染器注入 3 个 + params 常用键（键名别名在包装层归一）
_ALLOWED_VARS = {
    "width", "height", "duration",
    "text", "sub", "title", "keyword", "label", "number",
    "color", "x", "y", "size", "icon",
}

# 真实时钟黑名单（确定性渲染铁律）
# CSS animation/@keyframes/transition 同理：由真实时间驱动，
# 逐帧截图时状态取决于墙钟 → 每次渲染结果不同，审片闭环失效
_CLOCK_PATTERNS = [
    r"setInterval\s*\(", r"setTimeout\s*\(",
    r"requestAnimationFrame\s*\(",
    r"performance\.now\s*\(", r"new\s+Date\s*\(",
    r"@keyframes\b", r"animation\s*:", r"transition\s*:",
]

# 占位符形态：{identifier}（CSS 花括号含冒号/分号/空格，天然不匹配）
_PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")

# 键名别名 → 主键（内置模板 v4.1 同款兼容）
_TEXT_ALIASES = ("text", "title", "sub", "keyword", "label")

_DEFAULT_COLOR = "#FFE066"


def _extract_quoted(html: str) -> str:
    """从 LLM 响应提取 HTML — 允许裸 HTML 或 ```html fenced。"""
    m = re.search(r"```(?:html)?\s*\n(.*?)```", html, re.S)
    if m:
        return m.group(1).strip()
    return html.strip()


def validate_generated_html(html: str) -> list[str]:
    """静态契约校验 — 返回错误清单（空 = 通过）。

    全部规则来自本项目真实事故，见模块 docstring。
    """
    errors: list[str] = []

    if "<body" not in html.lower():
        errors.append("缺少 <body>（HTML 不完整）")
    if "<script" not in html.lower():
        errors.append("缺少 <script>（无 seekTo 动画逻辑）")

    # 1. seekTo 契约
    if not re.search(r"window\.seekTo\s*=", html):
        errors.append("缺少 window.seekTo = function(t) 契约（worker 逐帧渲染必需）")

    # 2. 真实时钟黑名单
    for pat in _CLOCK_PATTERNS:
        if re.search(pat, html):
            errors.append(
                f"使用真实时钟 {pat.replace(chr(92) + 's*', '')} — "
                "禁止：动画必须由 seekTo(t) 驱动（确定性渲染）",
            )

    # 3. 选择器审计（o17 事故防线 + null guard 豁免）
    body_m = re.search(r"<body[^>]*>(.*?)</body>", html, re.S | re.I)
    if body_m:
        body = body_m.group(1)
        js = "".join(re.findall(r"<script>(.*?)</script>", html, re.S | re.I))
        for sel_m in re.finditer(
            r"querySelector(?:All)?\(\s*['\"]([^'\"]+)['\"]", js,
        ):
            sel = sel_m.group(1)
            if not sel.startswith("."):
                continue
            # null guard 豁免：const v = querySelector(sel) 且有 if (v)
            var_m = re.search(
                rf"const (\w+) = document\.querySelector(?:All)?\(\s*['\"]"
                + re.escape(sel) + "['\"]",
                js,
            )
            if var_m and re.search(rf"if\s*\(\s*{var_m.group(1)}\s*\)", js):
                continue
            for seg in sel.split(".")[1:]:
                cls_m = re.match(r"\w+", seg)
                if not cls_m:
                    continue
                cls = cls_m.group(0)
                if not re.search(rf'class="[^"]*\b{re.escape(cls)}\b', body):
                    errors.append(
                        f"JS 引用 .{cls}（来自 {sel}）但 DOM 中不存在该 class，"
                        "且无 null guard → worker 逐帧渲染会崩",
                    )
                    break

    # 4. {color} 消费（palette 契约）
    if "{color}" not in html:
        errors.append("模板不消费 {color} 占位符 — palette 规划/渲染契约必须闭合")

    # 5. 占位符白名单
    for m in _PLACEHOLDER_RE.finditer(html):
        var = m.group(1)
        if var not in _ALLOWED_VARS:
            errors.append(f"未知占位符 {{{var}}} — 可用：{sorted(_ALLOWED_VARS)}")

    # 6. 文字/背景对比度（WCAG AA — 排版审计 2026-10-28 转化）
    errors.extend(_check_contrast(html))

    return errors


def _parse_css_color(v: str) -> tuple[int, int, int, float] | None:
    """解析 CSS 颜色字面量 → (r, g, b, a)。

    仅支持 #rgb/#rrggbb/#rrggbbaa/rgb()/rgba()；命名色、var()、
    currentColor、占位符掩码等无法静态判定 → None（调用方跳过）。
    """
    s = v.strip()
    if s.startswith("#"):
        h = s[1:]
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        if len(h) not in (6, 8) or not re.fullmatch(r"[0-9a-fA-F]+", h):
            return None
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        a = int(h[6:8], 16) / 255 if len(h) == 8 else 1.0
        return r, g, b, a
    m = re.match(r"rgba?\(([^)]+)\)", s)
    if m:
        parts = [p.strip() for p in m.group(1).split(",")]
        if len(parts) < 3:
            return None
        try:
            r, g, b = (int(float(p)) for p in parts[:3])
            a = float(parts[3]) if len(parts) > 3 else 1.0
        except ValueError:
            return None
        return r, g, b, a
    return None


def _wcag_contrast(c1: tuple[int, int, int], c2: tuple[int, int, int]) -> float:
    """WCAG 2.x 相对亮度对比度。"""

    def _lum(c: tuple[int, int, int]) -> float:
        def lin(v: float) -> float:
            v /= 255
            return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

        return 0.2126 * lin(c[0]) + 0.7152 * lin(c[1]) + 0.0722 * lin(c[2])

    l1, l2 = _lum(c1), _lum(c2)
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


def _check_contrast(html: str) -> list[str]:
    """契约第 6 条：静态 CSS 对比度检查。

    只检查 <style> 块里同规则内 color + 纯色 background 的配对；
    渐变 / 占位符 / 半透明（alpha<0.85）/ 命名色无法静态判定 → 跳过
    （VLM 审片兜底）。阈值：≥48px 大字 3.0:1，其余 4.5:1（WCAG AA）。
    """
    errors: list[str] = []
    styles = "".join(re.findall(r"<style[^>]*>(.*?)</style>", html, re.S | re.I))
    if not styles:
        return errors
    # 占位符 {color} 掩成无花括号 token，避免打断规则块解析
    css = _PLACEHOLDER_RE.sub(lambda m: f"__PH_{m.group(1)}__", styles)
    for rule in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        decls = rule.group(2)
        color = bg = None
        for d in decls.split(";"):
            if ":" not in d:
                continue
            prop, val = d.split(":", 1)
            prop = prop.strip().lower()
            if prop == "color" and color is None:
                color = val.strip()
            elif prop in ("background-color", "background") and bg is None:
                bg = val.strip()
        if not color or not bg:
            continue
        if any(k in bg for k in ("gradient", "url(", "none", "transparent")):
            continue
        cf, cb = _parse_css_color(color), _parse_css_color(bg)
        if not cf or not cb:
            continue  # 占位符/命名色 → 无法判定
        if cf[3] < 0.85 or cb[3] < 0.85:
            continue  # 半透明与下层混合色未知
        fs_m = re.search(r"font-size\s*:\s*(\d+(?:\.\d+)?)px", decls)
        size = float(fs_m.group(1)) if fs_m else 0.0
        need = 3.0 if size >= 48 else 4.5
        ratio = _wcag_contrast(cf[:3], cb[:3])
        if ratio < need:
            sel = re.sub(r"\s+", " ", rule.group(1).strip())[:40]
            errors.append(
                f"文字/背景对比度 {ratio:.2f}:1 不足 {need}:1"
                f"（{sel}：{color} on {bg}）— 请黑白取优或加深背景"
            )
    return errors


class GeneratedTemplate:
    """LLM 生成模板的运行时包装 — duck-typing 兼容 TEMPLATES 函数。

    调用形态与内置模板一致：(width, height, duration, **kwargs) -> HTML str。
    占位符只替换白名单变量；params 值经 HTML 转义（防注入）。
    """

    def __init__(self, name: str, html_template: str):
        self.name = name
        self._tpl = html_template

    def __call__(
        self,
        width: int = 1080,
        height: int = 1920,
        duration: float = 3.0,
        **kwargs: Any,
    ) -> str:
        # 键名别名归一：text < title < sub < keyword < label 首个非空
        text_val = ""
        for alias in _TEXT_ALIASES:
            v = kwargs.get(alias)
            if v:
                text_val = str(v)
                break

        color_val = str(kwargs.get("color") or _DEFAULT_COLOR)

        subs: dict[str, str] = {
            "width": str(int(width)),
            "height": str(int(height)),
            "duration": str(float(duration)),
            "text": escape(text_val),
            "sub": escape(str(kwargs.get("sub") or "")),
            "title": escape(str(kwargs.get("title") or "")),
            "keyword": escape(str(kwargs.get("keyword") or "")),
            "label": escape(str(kwargs.get("label") or "")),
            "number": escape(str(kwargs.get("number") or "")),
            "color": color_val,
            "x": str(kwargs.get("x") or 0.5),
            "y": str(kwargs.get("y") or 0.5),
            "size": str(kwargs.get("size") or 0.3),
            "icon": escape(str(kwargs.get("icon") or "✨")),
        }

        def _sub(m: re.Match) -> str:
            return subs.get(m.group(1), m.group(0))

        return _PLACEHOLDER_RE.sub(_sub, self._tpl)


_TEMPLATE_GEN_PROMPT = """你是短视频叠加动画模板工程师。生成一个口播视频叠加模板。

需求：{requirement}

输出一个完整 HTML 文件（单文件内联 CSS/JS），硬性契约：
1. 必须定义 window.seekTo = function(t) {{ ... }}：t 为当前秒数，
   函数内设置该时刻全部元素状态（入场进度、透明度、位移、缩放）
2. 禁止真实时钟：不得使用 setInterval / setTimeout /
   requestAnimationFrame / new Date / performance.now，也不得使用
   CSS animation / @keyframes / transition — 动画只能由
   seekTo(t) 驱动（渲染器逐帧调用求值）
3. querySelector 引用的每个 class 必须出现在 HTML 的 DOM 中；
   条件渲染的元素必须 if (x) 判空保护
4. 必须消费占位符 {{color}}（主题色，来自全片 3 色盘）—
   主视觉元素用它做底色/描边，不要写死颜色
5. 可用占位符（双花括号形式，仅限这些）：
   {{width}} {{height}} {{duration}} {{text}} {{sub}} {{title}}
   {{keyword}} {{label}} {{number}} {{color}} {{x}} {{y}} {{size}} {{icon}}
   其中 width/height/duration 由渲染器注入，其余来自规划参数
6. 视觉要求：大字号粗体、亮色记忆点、黑描边保证任何底色上可读、
   入场动画 0.3-0.5s、入场后保持轻微 sin 微动（呼吸感）；
   文字与所在色块对比度必须达标 —— 小字（<48px）≥4.5:1，
   大字（≥48px）≥3.0:1，拿不准就用黑字亮底或白字深底
7. 透明背景（background: transparent），叠加在视频上
8. CSS 花括号照常写（{{margin: 0}} 的大括号不是占位符 — 占位符
   只有大括号内是上述白名单单词时才生效）

只输出 HTML（可 ```html fenced 或裸 HTML），不要解释。"""


async def generate_template(
    llm: Any,
    name: str,
    requirement: str,
    max_attempts: int = 3,
) -> GeneratedTemplate | None:
    """LLM 生成新模板 — 校验失败带反馈重试，全败返回 None 降级。"""
    if not llm or not requirement:
        return None

    from app.adapters.llm_base import llm_prompt_to_text

    feedback = ""
    last_errors: list[str] = []
    for attempt in range(max_attempts):
        try:
            prompt = _TEMPLATE_GEN_PROMPT.format(requirement=requirement)
            if feedback:
                prompt = f"{feedback}\n\n{prompt}"
            resp = await llm_prompt_to_text(llm, prompt)
            html = _extract_quoted(resp)
        except Exception as e:
            logger.warning("[template_generator] LLM 调用失败 attempt=%d: %s", attempt, e)
            continue

        errors = validate_generated_html(html)
        if not errors:
            tpl = GeneratedTemplate(name, html)
            GENERATED[name] = tpl
            _persist(name, html, requirement)
            logger.info("[template_generator] 模板 %s 生成并通过契约校验", name)
            return tpl

        last_errors = errors
        feedback = (
            "你上一次的输出违反了以下契约，逐条修复后重新输出完整 HTML：\n- "
            + "\n- ".join(errors)
        )
        logger.warning(
            "[template_generator] %s 契约校验失败 attempt=%d: %s",
            name, attempt, errors,
        )

    logger.error(
        "[template_generator] %s 生成失败（%d 次），降级回内置模板。错误：%s",
        name, max_attempts, last_errors,
    )
    return None


def _persist(name: str, html: str, requirement: str) -> None:
    """落盘 JSON — 跨进程复用（生成后零边际成本）。失败不阻断。"""
    try:
        _STORE_DIR.mkdir(parents=True, exist_ok=True)
        (_STORE_DIR / f"{name}.json").write_text(
            json.dumps({
                "name": name,
                "html": html,
                "requirement": requirement,
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:  # 写盘失败不影响本次运行
        logger.warning("[template_generator] 落盘失败 %s: %s", name, e)


def load_generated_templates() -> dict[str, GeneratedTemplate]:
    """启动时加载落盘模板 — 损坏/契约不过的跳过（防旧模板绕过新契约）。"""
    GENERATED.clear()
    if not _STORE_DIR.is_dir():
        return GENERATED
    for f in sorted(_STORE_DIR.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            name, html = data["name"], data["html"]
            if validate_generated_html(html):
                logger.warning("[template_generator] 落盘模板 %s 契约不过，跳过", name)
                continue
            GENERATED[name] = GeneratedTemplate(name, html)
        except Exception as e:
            logger.warning("[template_generator] 落盘模板 %s 损坏，跳过：%s", f.name, e)
    if GENERATED:
        logger.info("[template_generator] 加载 %d 个生成模板: %s", len(GENERATED), list(GENERATED))
    return GENERATED
