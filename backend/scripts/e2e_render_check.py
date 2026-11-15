"""渲染器端到端真实验证（需 playwright + ffmpeg，非 mock）。

验证 ADR-001 渲染路线：逐帧透明截图是否真实产出带 alpha 的 PNG。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from capabilities.video.adapters.html_to_video import (
    build_frame_times,
    frames_dir_for,
    render_html_to_video,
)
from capabilities.video.adapters.overlay_templates import caption_pop


async def main() -> int:
    html = caption_pop(
        text="今天天气真不错",
        words=[
            {"text": "今天", "start": 0.0, "end": 0.5},
            {"text": "天气", "start": 0.5, "end": 1.0},
            {"text": "真不错", "start": 1.0, "end": 1.8},
        ],
        duration=1.8,
    )
    out = Path("outputs/render_e2e/caption_pop_test.mp4")
    result = await render_html_to_video(html, out, duration=1.8)
    if result is None:
        print("FAIL: render returned None")
        return 1

    fdir = frames_dir_for(out)
    frames = sorted(fdir.glob("frame_*.png"))
    n_expected = len(build_frame_times(1.8))
    print(f"frames: {len(frames)} (expect {n_expected})")
    assert len(frames) == n_expected, "帧数不符"

    # alpha 检查：PIL 验证角落像素透明 + 中部像素不透明
    from PIL import Image

    img = Image.open(frames[-1]).convert("RGBA")
    corner = img.getpixel((5, 5))
    print(f"corner alpha={corner[3]} (expect 0)")
    assert corner[3] == 0, "背景不透明 — omit_background 失效"

    # 中部应有非透明像素（字幕文字）
    w, h = img.size
    center_box = img.crop((w // 4, h // 2, w * 3 // 4, h - 100))
    alpha_nonzero = any(px[3] > 0 for px in center_box.getdata())
    print(f"center has content: {alpha_nonzero}")
    assert alpha_nonzero, "画面中部无内容 — 字幕未渲染"

    print(f"preview mp4: {result} exists={result.exists()}")
    assert result.exists()
    print("E2E RENDER OK")
    return 0


if __name__ == "__main__":
    import asyncio

    raise SystemExit(asyncio.run(main()))
