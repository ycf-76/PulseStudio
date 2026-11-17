#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""card_audit.py — 卡片成图的确定性质检（专治「死空白」+ 密度不足）。

小红书卡片最常见的廉价感来源：内容没填满 3:4 画幅、底部一大片死空白。
本脚本对渲染出的 PNG 做**逐行内容密度分析**，量化：整体填充率、最大无理由空白带、
尤其是**底部死空白占比**，据设计系统的「≥75% 画高、任何空白带 >15% 判失败」自动判定。

来自 Pulse card-design/scripts/card_audit.py。

子命令：
    audit      分析单张卡片 PNG（--file），报填充率/底部死空白/逐带密度 + 判定
    selftest   自检（合成图验证检测逻辑）

用法：
    card_audit.py audit -f outputs/x/card_01.png
    card_audit.py audit -f outputs/x/card_01.png --json
    for f in outputs/x/card_*.png; do python card_audit.py audit -f "$f"; done
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path

SPAN_MIN = 0.80
DEAD_BAND_MAX = 0.15
EDGE_TOL = 12
ROW_EDGE_FRAC = 0.006


def _die(msg: str, code: int = 1) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def _row_has_content(arr):
    import numpy as np

    gray = arr.mean(axis=2)
    gx = np.abs(np.diff(gray, axis=1))
    edge = gx > EDGE_TOL
    frac = edge.mean(axis=1)
    return frac > ROW_EDGE_FRAC, frac


def analyze(path: str, tol: int = EDGE_TOL) -> dict:
    try:
        import numpy as np
        from PIL import Image
    except Exception as e:
        _die(f"需要 Pillow + numpy：{e}")
    p = Path(path)
    if not p.is_file():
        _die(f"图片不存在：{path}")
    img = Image.open(p).convert("RGB")
    w, h = img.size
    arr = np.asarray(img)
    corners = np.array([arr[0, 0], arr[0, -1], arr[-1, 0], arr[-1, -1]])
    bg = tuple(int(x) for x in np.median(corners, axis=0))

    has, frac = _row_has_content(arr)
    content_rows = int(has.sum())
    fill = content_rows / h

    first = int(np.argmax(has)) if content_rows else 0
    last = h - 1 - int(np.argmax(has[::-1])) if content_rows else 0
    span = (last - first + 1) / h if content_rows else 0.0

    bottom_empty = 0
    for i in range(h - 1, -1, -1):
        if has[i]:
            break
        bottom_empty += 1
    bottom_dead = bottom_empty / h

    max_gap = 0
    cur = 0
    for i in range(first, last + 1):
        if not has[i]:
            cur += 1
            max_gap = max(max_gap, cur)
        else:
            cur = 0
    max_gap_frac = max_gap / h

    bands = []
    for b in range(4):
        s, e = int(b * h / 4), int((b + 1) * h / 4)
        bands.append(round(float(has[s:e].mean()), 2))

    problems = []
    if bottom_dead > DEAD_BAND_MAX:
        problems.append(f"底部死空白 {bottom_dead*100:.0f}% > {DEAD_BAND_MAX*100:.0f}%")
    if max_gap_frac > DEAD_BAND_MAX:
        problems.append(f"中段最大空白带 {max_gap_frac*100:.0f}% > {DEAD_BAND_MAX*100:.0f}%")
    if span < SPAN_MIN:
        problems.append(f"内容纵向跨度 {span*100:.0f}% < {SPAN_MIN*100:.0f}%")
    if bands[3] < 0.10 and bands[0] > 0.3:
        problems.append("头重脚轻：内容堆在顶部、底部近空")

    return {
        "file": str(p),
        "size": f"{w}x{h}",
        "bg": bg,
        "span": round(span, 3),
        "bottom_dead": round(bottom_dead, 3),
        "max_gap": round(max_gap_frac, 3),
        "fill": round(fill, 3),
        "bands": bands,
        "pass": not problems,
        "problems": problems,
    }


def _print(r: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return
    tag = "PASS" if r["pass"] else "FAIL"
    print(f"{tag}  {Path(r['file']).name}  ({r['size']})")
    print(
        f"  纵向跨度 {r['span']*100:.0f}% | 底部死空白 {r['bottom_dead']*100:.0f}% | "
        f"最大空白带 {r['max_gap']*100:.0f}% | 4带密度 {r['bands']}"
    )
    for p in r["problems"]:
        print(f"  !! {p}")


def _selftest() -> int:
    import tempfile

    import numpy as np
    from PIL import Image

    ok = True

    def chk(name, cond):
        nonlocal ok
        print(f"[{'PASS' if cond else 'FAIL'}] {name}")
        ok = ok and cond

    with tempfile.TemporaryDirectory() as td:

        def stripes(region, base, h=1440, w=1080):
            a = np.full((h, w, 3), base, np.uint8)
            y0, y1 = region
            block = np.full((y1 - y0, w, 3), base, np.uint8)
            block[:, ::6] = 255 - base
            a[y0:y1] = block
            return a

        f1 = Path(td) / "top.png"
        Image.fromarray(stripes((80, 600), 245)).save(f1)
        r1 = analyze(str(f1))
        chk("检出底部死空白", (not r1["pass"]) and r1["bottom_dead"] > 0.15)

        f2 = Path(td) / "full.png"
        Image.fromarray(stripes((80, 1370), 245)).save(f2)
        r2 = analyze(str(f2))
        chk("填满的卡片 PASS", r2["pass"] and r2["span"] >= 0.80)

        f3 = Path(td) / "dark.png"
        Image.fromarray(stripes((80, 1370), 14)).save(f3)
        r3 = analyze(str(f3))
        chk("深底也能测跨度", r3["span"] >= 0.80 and r3["bg"][0] < 40)

        grad = np.zeros((1440, 1080, 3), np.uint8)
        for y in range(1440):
            grad[y, :] = (10 + y // 20, 20 + y // 30, 60 + y // 12)
        f4 = Path(td) / "grad.png"
        Image.fromarray(grad).save(f4)
        r4 = analyze(str(f4))
        chk("平滑渐变判为空", (not r4["pass"]) and r4["span"] < 0.2)

        mid = np.full((1440, 1080, 3), 245, np.uint8)
        blk = np.full((200, 1080, 3), 245, np.uint8)
        blk[:, ::6] = 20
        mid[80:280] = blk
        mid[1160:1360] = blk
        f5 = Path(td) / "gap.png"
        Image.fromarray(mid).save(f5)
        r5 = analyze(str(f5))
        chk("检出中段大空洞", (not r5["pass"]) and r5["max_gap"] > 0.15)

    print("selftest 通过" if ok else "selftest 失败")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(
        description="卡片成图确定性质检（死空白/密度）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    pa = sub.add_parser("audit", help="分析卡片 PNG")
    pa.add_argument("-f", "--file", required=True, help="卡片 PNG（支持 glob）")
    pa.add_argument("--json", action="store_true")
    pa.add_argument("--tol", type=int, default=18, help="偏离背景阈值(默认18)")
    sub.add_parser("selftest", help="自检")
    a = ap.parse_args()
    if a.cmd == "selftest":
        return _selftest()
    if a.cmd == "audit":
        files = sorted(glob.glob(a.file)) or [a.file]
        allpass = True
        for f in files:
            r = analyze(f, a.tol)
            _print(r, a.json)
            allpass = allpass and r["pass"]
        return 0 if allpass else 1
    return 1


if __name__ == "__main__":
    sys.exit(main())
