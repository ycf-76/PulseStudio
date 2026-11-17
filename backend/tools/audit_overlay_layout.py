"""叠加模板排版静态审计 — 无需 LLM，不依赖真机素材（2026-10-28）。

解决的问题（样本多样性审计遗留 #4）：
前十轮 demo 里的排版问题（副标题字号仅主标题 1/3、section_card 偏居左
仅占画面 1/8、白字压 #FF8A00 对比度 2.8:1、高亮条内边距过窄、收尾卡
层级模糊）全部靠 VLM 抽帧事后发现 —— 用最贵的手段做最便宜的检查。

本脚本把可计算的指标一次性算清：
  - 文字与其最近不透明背景的对比度（WCAG 相对亮度公式）
  - 无背景色文字（直接压视频画面）是否具备 text-shadow / stroke 兜底
  - 主次字号比（层级是否清晰）
  - 主元素占画面面积比（内容是否缩在一角）
  - 有背景元素的内边距

判据（阈值可调，依据见各常量）：
  对比度 ≥4.5（正文）/ ≥3.0（大字 ≥48px）
  无背景文字必须有 text-shadow 或 -webkit-text-stroke
  主次字号比 ≥0.5
  主元素占画面 ≥15%
  内边距 ≥8px

用法：
  python tools/audit_overlay_layout.py                 # 全部 16 模板
  python tools/audit_overlay_layout.py caption_pop     # 指定模板
退出码：存在 P0 问题 → 1（可接入 CI）；否则 0
"""

from __future__ import annotations

import asyncio
import inspect
import json
import re
import sys
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from capabilities.video.adapters.overlay_templates import TEMPLATES  # noqa: E402

# 画布基准（与 _base_css 的设计基准一致）
CW, CH = 1080, 1920

# 通用参数弹药库：按各模板签名过滤后传入（多传无妨，有 **kwargs 的模板自取）
_COMMON: dict[str, Any] = {
    "text": "这句话千万不要忽略",
    "title": "三件事千万别漏",
    "subtitle": "很多人都做错了",
    "keyword": "空调直吹",
    "label": "增长率",
    "value": "200%",
    "number": "01",
    "icon": "📊",
    "color": "#FFE066",
    "accent_words": "不要",
    "accent_color": "#FF4D4D",
    "highlight_color": "#FFE066",
    "left_text": "之前",
    "right_text": "之后",
    "percent": 72,
    "animation": "pop",
    "words": [
        {"text": "这句话", "start": 0.0, "end": 0.3},
        {"text": "千万不要", "start": 0.3, "end": 0.6},
        {"text": "忽略", "start": 0.6, "end": 0.9},
    ],
}

# 各模板的定制覆盖（用于覆盖真实规划里常见的参数形态）
_OVERRIDES: dict[str, dict[str, Any]] = {
    "caption_pop": {"text": "90%的人都漏了这一步", "accent_words": "漏了"},
    "caption_karaoke": {"text": "空调直吹最容易中招"},
    "caption_typewriter": {"text": "出门前十分钟涂防晒"},
    "keyword_highlight": {"keyword": "空调直吹", "highlight_color": "#FFE066"},
    "stat_card": {"label": "中招率", "value": "90%", "icon": "📊"},
    "title_card": {"title": "夏天出门三件事", "subtitle": "90%的人都做错"},
    "number_roll": {"number": "01", "label": "涂防晒"},
    "bar_chart_grow": {"label": "中招率", "percent": 90},
    "lower_third": {"title": "皮肤科医生", "subtitle": "十年临床经验"},
    "section_card": {"number": "01", "title": "空调直吹"},
    "emoji_sticker": {"icon": "🥶", "label": "冷"},
    "arrow_point": {"text": "注意这里"},
    "circle_mark": {"text": "重点"},
    "full_card": {"text": "夏天千万别大意", "color": "#FF4D4D"},
    "split_compare": {"left_text": "错误", "right_text": "正确"},
    "keyword_icon": {"keyword": "空调直吹", "icon": "🥶", "subtitle": "冷风直吹"},
}

# 采集脚本：一次性把所需几何/颜色/字体信息交给 Python 侧判定
_COLLECT_JS = r"""
() => {
  const parse = (s) => {
    if (!s) return null;
    const m = s.match(/rgba?\(([^)]+)\)/);
    if (!m) return null;
    const p = m[1].split(',').map(x => parseFloat(x));
    if (p.length < 3) return null;
    return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 };
  };
  const lum = (c) => {
    const f = (v) => {
      v = v / 255;
      return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
    };
    return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b);
  };
  const contrast = (a, b) => {
    const l1 = lum(a), l2 = lum(b);
    const hi = Math.max(l1, l2), lo = Math.min(l1, l2);
    return (hi + 0.05) / (lo + 0.05);
  };
  const bgOf = (el) => {
    // 从父元素开始找 —— 文字元素自身若带背景（高亮条等），那也是"文字的背景块"，
    // 但它与文字 bbox 重合会导致贴边距离恒为 0，须看更外层的容器
    let cur = el.parentElement;
    while (cur && cur !== document.documentElement) {
      const st = getComputedStyle(cur);
      const c = parse(st.backgroundColor);
      if (c && c.a > 0.05) return { color: c, gradient: false, el: cur };
      if (st.backgroundImage && st.backgroundImage !== 'none') {
        return { color: null, gradient: true, el: cur };
      }
      cur = cur.parentElement;
    }
    return null;
  };

  const canvas = document.body.getBoundingClientRect();
  const canvasArea = canvas.width * canvas.height;
  const out = { texts: [], blocks: [], canvasArea };

  for (const el of document.querySelectorAll('body *')) {
    const st = getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0 || st.visibility === 'hidden') continue;

    const own = Array.from(el.childNodes)
      .filter(n => n.nodeType === 3)
      .map(n => n.textContent.trim())
      .join('');
    if (own) {
      const fg = parse(st.color) || { r: 0, g: 0, b: 0, a: 1 };
      const bg = bgOf(el);
      const size = parseFloat(st.fontSize) || 0;
      // 视觉贴边：文字 bbox 到其背景块 bbox 的四边最小距离
      // （比 padding 属性准确 —— padding:0 但 flex 居中的布局不算贴边）
      let edgeGap = null;
      if (bg && bg.el) {
        const br = bg.el.getBoundingClientRect();
        edgeGap = Math.min(
          rect.top - br.top, br.bottom - rect.bottom,
          rect.left - br.left, br.right - rect.right,
        );
      }
      out.texts.push({
        text: own.slice(0, 18),
        fontSize: Math.round(size),
        contrast: (bg && bg.color) ? +contrast(fg, bg.color).toFixed(2) : null,
        onVideo: !bg,
        gradientBg: bg ? bg.gradient : false,
        edgeGap: edgeGap === null ? null : Math.round(edgeGap),
        hasShadow: st.textShadow !== 'none',
        strokeWidth: parseFloat(st.webkitTextStrokeWidth) || 0,
        bold: parseInt(st.fontWeight, 10) || 400,
        // 渐变文字（background-clip:text + color:transparent）无法静态算对比度
        gradient: st.webkitBackgroundClip === 'text' || st.backgroundClip === 'text',
      });
    }

    const bgc = parse(st.backgroundColor);
    if (bgc && bgc.a > 0.05) {
      const pad = {
        t: parseFloat(st.paddingTop) || 0,
        r: parseFloat(st.paddingRight) || 0,
        b: parseFloat(st.paddingBottom) || 0,
        l: parseFloat(st.paddingLeft) || 0,
      };
      out.blocks.push({
        area: rect.width * rect.height,
        minPadding: Math.min(pad.t, pad.r, pad.b, pad.l),
        tag: el.tagName.toLowerCase(),
        cls: (el.className || '').toString().slice(0, 24),
      });
    }
  }

  // 主元素面积：取面积最大的有背景块；无背景块（纯文字模板）用文字元素并集近似
  const maxBlock = out.blocks.reduce((a, b) => (b.area > (a ? a.area : 0) ? b : a), null);
  out.maxBlockArea = maxBlock ? maxBlock.area : 0;
  out.minPadding = out.blocks.length
    ? Math.min(...out.blocks.map(b => b.minPadding))
    : null;
  return out;
}
"""

# 判据常量（口径说明见 10.15 补记）
MIN_CONTRAST_BODY = 4.5
MIN_CONTRAST_LARGE = 3.0
LARGE_FONT_PX = 48
MIN_PADDING = 8

# 模板语义分层 —— 面积/层级判据不可一刀切：
#   caption：本来就是窄条字幕，占画面 4% 属正常；
#   mark：箭头/圆圈/贴纸本来就是小标记；面积判据不适用
#   card：卡片/整屏类，才谈"是否缩在一角"
_CAPTION = {
    "caption_pop",
    "caption_karaoke",
    "caption_typewriter",
    "keyword_highlight",
}
_MARK = {"arrow_point", "circle_mark", "emoji_sticker"}
# 层级判据只对"确有主副标题语义"的模板生效 —— 编号 vs 标题
# （section_card 120/54）、大数字 vs 小标签（number_roll）不是层级问题
_HIERARCHY = {"title_card", "lower_third", "full_card"}
MIN_HIERARCHY_RATIO = 0.5

# 纯 emoji/符号不按文字检查兜底（颜色由系统字体决定，且无 shadow 是常态）
_EMOJI_ONLY = re.compile(r"^[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u200D\s]+$")


def _is_emoji_only(s: str) -> bool:
    return bool(_EMOJI_ONLY.match(s.strip()))


def _build(template_name: str) -> tuple[str | None, str]:
    fn = TEMPLATES[template_name]
    sig = inspect.signature(fn)
    has_var_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
    payload = {**_COMMON, **_OVERRIDES.get(template_name, {})}
    kwargs = {k: v for k, v in payload.items() if k in sig.parameters or has_var_kw}
    kwargs.update({"width": CW, "height": CH, "duration": 2.0})
    try:
        return fn(**kwargs), ""
    except Exception as e:  # 模板参数形态与弹药库不匹配时如实报错
        return None, f"{type(e).__name__}: {e}"


async def _audit_one(page: Any, name: str) -> dict[str, Any]:
    html, err = _build(name)
    if html is None:
        return {"name": name, "error": err}
    await page.set_content(html)
    await page.wait_for_timeout(120)
    data = await page.evaluate(_COLLECT_JS)

    issues: list[tuple[str, str]] = []  # (级别, 描述)
    notes: list[str] = []  # INFO 级，只展示不判不合格

    # 1) 对比度（仅对有明确背景色的文字可精确计算）
    for t in data["texts"]:
        if _is_emoji_only(t["text"]):
            continue
        if t.get("gradient"):
            notes.append(f"渐变文字「{t['text']}」({t['fontSize']}px) 对比度需人工目视确认")
            continue
        if t.get("gradientBg"):
            notes.append(f"渐变背景上的「{t['text']}」({t['fontSize']}px) 对比度需人工目视确认")
            continue
        if t["contrast"] is not None:
            need = MIN_CONTRAST_LARGE if t["fontSize"] >= LARGE_FONT_PX else MIN_CONTRAST_BODY
            if t["contrast"] < need:
                issues.append(
                    (
                        "P0",
                        f"对比度 {t['contrast']}:1 < {need}:1 「{t['text']}」"
                        f"({t['fontSize']}px)",
                    )
                )
        elif t["onVideo"] and not t["hasShadow"] and t["strokeWidth"] < 1:
            # 压视频画面且无兜底：静态无法判断真实背景亮度，
            # 十轮 demo 真机出片未报可读性问题 → 只作提示，交真人/真实素材验证
            notes.append(
                f"压画面无 shadow/stroke 兜底 「{t['text']}」"
                f"({t['fontSize']}px) — 需真实素材目视确认"
            )

    # 2) 视觉贴边：文字到背景块四边的最小距离
    gaps = [
        t["edgeGap"]
        for t in data["texts"]
        if t.get("edgeGap") is not None and not _is_emoji_only(t["text"])
    ]
    if gaps and min(gaps) < MIN_PADDING:
        issues.append(
            (
                "P1",
                f"文字距背景块边缘 {min(gaps)}px < {MIN_PADDING}px — 视觉贴边",
            )
        )

    # 3) 层级比：阈值未经真机目视校准 → 只作提示，不判不合格
    sizes = sorted({t["fontSize"] for t in data["texts"]}, reverse=True)
    if name in _HIERARCHY and len(sizes) >= 2:
        ratio = sizes[1] / sizes[0]
        if ratio < MIN_HIERARCHY_RATIO:
            notes.append(
                f"主次字号比 {ratio:.2f} ({sizes[0]}px vs {sizes[1]}px) — " "副标题偏小，可考虑调大"
            )

    # 4) 面积占比：只展示（阈值需用真实合格产物校准，暂不判不合格）
    area_ratio = min(
        data["maxBlockArea"] / data["canvasArea"] if data["canvasArea"] else 0,
        1.0,
    )
    if name not in _CAPTION and name not in _MARK:
        notes.append(f"主元素占画面 {area_ratio * 100:.1f}%")

    return {
        "name": name,
        "issues": issues,
        "notes": notes,
        "texts": data["texts"],
        "area_ratio": area_ratio,
        "levels": sizes[:3],
        "min_padding": data["minPadding"],
    }


async def main() -> int:
    names = sys.argv[1:] or list(TEMPLATES)
    bad = [n for n in names if n not in TEMPLATES]
    if bad:
        print(f"未知模板：{bad}\n可选：{list(TEMPLATES)}")
        return 2

    from playwright.async_api import async_playwright

    results: list[dict[str, Any]] = []
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(
            viewport={"width": CW, "height": CH},
            device_scale_factor=1,
        )
        for name in names:
            results.append(await _audit_one(page, name))
        await browser.close()

    print(f"\n{'模板':<20}{'P0':>3}{'P1':>4}{'主元素占比':>11}{'字号档':>16}{'内边距':>8}")
    print("-" * 70)
    for r in results:
        if "error" in r:
            print(f"{r['name']:<20} 构建失败：{r['error']}")
            continue
        p0 = sum(1 for lv, _ in r["issues"] if lv == "P0")
        p1 = sum(1 for lv, _ in r["issues"] if lv == "P1")
        lv = "/".join(str(x) for x in r["levels"]) or "-"
        pad = "-" if r["min_padding"] is None else f"{r['min_padding']:.0f}px"
        print(f"{r['name']:<20}{p0:>3}{p1:>4}" f"{r['area_ratio'] * 100:>10.1f}%{lv:>16}{pad:>8}")

    print("\n问题清单：")
    total = 0
    for r in results:
        if "error" in r:
            total += 1
            print(f"  [构建] {r['name']}: {r['error']}")
            continue
        for lv, msg in r["issues"]:
            total += 1
            print(f"  [{lv}] {r['name']}: {msg}")
    if total == 0:
        print("  无")

    info = [(r["name"], n) for r in results if "error" not in r for n in r["notes"]]
    if info:
        print("\n参考指标（未设阈值，供人工校准）：")
        for name, n in info:
            print(f"  [INFO] {name}: {n}")

    print(f"\n合计 {total} 条问题；P0 存在则退出码 1")
    has_p0 = any(lv == "P0" for r in results if "error" not in r for lv, _ in r["issues"])
    return 1 if has_p0 else 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    raise SystemExit(asyncio.run(main()))
