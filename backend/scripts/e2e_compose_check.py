"""compose_with_overlays 端到端验证 — 真实视频 + rgba 时间窗叠加 + 抽帧检查。

验收标准：
  A2 无绿边：叠加文字边缘干净（alpha 合成，非 chromakey）
  A3 叠加真实出现：叠加时间窗内抽帧必须有内容，窗外必须无叠加
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from capabilities.video.adapters.html_to_video import (
    compose_with_overlays,
    frames_dir_for,
    render_html_to_video,
)
from capabilities.video.adapters.overlay_templates import caption_pop, stat_card

BASE = Path(
    r"D:\My_Project\多智能体小红书发布平台\backend\uploads\assets\OwdN5Jwjo_9XA_iAvuht-Q\01M4R4Z8GMKKBY6AFA809QYW97.mp4"
)
OUT_DIR = Path(__file__).parent.parent / "outputs" / "render_e2e"


async def main() -> int:
    import asyncio

    ffmpeg = None
    from capabilities.video.adapters.html_to_video import resolve_ffmpeg

    ffmpeg = resolve_ffmpeg()

    # 1. 渲染两个叠加（1.8s 字幕 + 2.2s 数据卡）
    cap_html = caption_pop(
        text="今天天气真不错",
        words=[
            {"text": "今天", "start": 0.0, "end": 0.5},
            {"text": "天气", "start": 0.5, "end": 1.0},
            {"text": "真不错", "start": 1.0, "end": 1.8},
        ],
        duration=1.8,
    )
    cap_out = OUT_DIR / "ov_caption.mp4"
    r1 = await render_html_to_video(cap_html, cap_out, duration=1.8)
    assert r1, "caption render failed"

    stat_html = stat_card(label="测试指标", value="3000+", icon="📊", duration=2.2)
    stat_out = OUT_DIR / "ov_stat.mp4"
    r2 = await render_html_to_video(stat_html, stat_out, duration=2.2)
    assert r2, "stat render failed"

    # 2. 合成：字幕 [0.3, 2.1)，数据卡 [2.6, 4.8)（测试视频全长 5.04s）
    final = OUT_DIR / "composed_final.mp4"
    result = await compose_with_overlays(
        base_video=BASE,
        overlay_videos=[(0.3, 2.1, cap_out), (2.6, 4.8, stat_out)],
        output_path=final,
    )
    print(f"composed: {result} exists={result.exists()}")

    # 3. 抽帧检查：窗内 3.0s（字幕）、6.0s（数据卡）有叠加；窗外 1.0s 无叠加
    import subprocess

    def grab(src: Path, ts: float, name: str) -> Path:
        p = OUT_DIR / name
        subprocess.run(
            [
                ffmpeg,
                "-y",
                "-loglevel",
                "error",
                "-ss",
                str(ts),
                "-i",
                str(src),
                "-vframes",
                "1",
                str(p),
            ],
            capture_output=True,
            check=True,
        )
        return p

    from PIL import Image, ImageChops

    def diff_pixels(a: Path, b: Path) -> int:
        """两帧显著差异像素数（通道差>30），剔除编码噪声。"""
        ia = Image.open(a).convert("RGB")
        ib = Image.open(b).convert("RGB")
        if ia.size != ib.size:
            ib = ib.resize(ia.size)
        diff = ImageChops.difference(ia, ib)
        return sum(1 for r, g, b in diff.getdata() if max(r, g, b) > 30)

    THRESHOLD = 3000  # 显著差异像素下限（1080x1920 帧）
    before = diff_pixels(grab(final, 0.1, "chk_before.png"), grab(BASE, 0.1, "chk_base01.png"))
    cap_on = diff_pixels(grab(final, 1.0, "chk_caption.png"), grab(BASE, 1.0, "chk_base10.png"))
    stat_on = diff_pixels(grab(final, 3.5, "chk_stat.png"), grab(BASE, 3.5, "chk_base35.png"))
    print(f"t=0.1 diff pixels: {before} (expect < {THRESHOLD})")
    print(f"t=1.0 diff pixels: {cap_on} (expect >= {THRESHOLD})")
    print(f"t=3.5 diff pixels: {stat_on} (expect >= {THRESHOLD})")

    assert before < THRESHOLD, f"叠加在时间窗外泄漏 (diff={before})"
    assert cap_on >= THRESHOLD, f"字幕叠加未出现 (diff={cap_on})"
    assert stat_on >= THRESHOLD, f"数据卡叠加未出现 (diff={stat_on})"

    print("COMPOSE E2E OK")
    return 0


if __name__ == "__main__":
    import asyncio

    raise SystemExit(asyncio.run(main()))
