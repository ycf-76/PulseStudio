"""隔离验证：单段 caption 渲染 + 合成抽帧像素检查。

验证链路（不需要转录/LLM）：
  1. caption_pop 模板在设计画布（684x1174 → 1080x1854）渲染
  2. compose_with_overlays 合成到真实 684x1174 底片
  3. PyAV 抽帧：窗内底部区域应有白色字幕像素，窗外应无泄漏
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

VIDEO = Path(r"D:\My_Project\多智能体小红书发布平台\backend\uploads\videos\OwdN5Jwjo_9XA_iAvuht-Q\01M5VDAHJR83J07D32SN14FW0P.mp4")
OUT = Path(__file__).parent.parent / "outputs" / "render_e2e"

WIN_START, WIN_END = 1.0, 4.0


def frame_diff_region(video_a: Path, video_b: Path, t: float) -> float:
    """两视频在 t 时刻帧的底部1/3区域平均像素差（0-255）。"""
    import av
    import numpy as np

    def grab(v: Path) -> np.ndarray:
        with av.open(str(v)) as c:
            stream = c.streams.video[0]
            stream.thread_type = "AUTO"
            target = int(t / stream.time_base) if stream.time_base else None
            for frame in c.decode(stream):
                if target is not None and frame.pts < target:
                    continue
                return frame.to_ndarray(format="rgb24")
        raise RuntimeError(f"no frame at t={t} in {v}")

    a, b = grab(video_a), grab(video_b)
    if a.shape != b.shape:
        return 999.0
    h = a.shape[0]
    d = np.abs(a[int(h * 2 / 3):, :].astype(int) - b[int(h * 2 / 3):, :].astype(int))
    return float(d.mean())


async def main() -> int:
    from capabilities.video.adapters.html_to_video import (
        compose_with_overlays,
        render_html_to_video,
    )
    from capabilities.video.adapters.overlay_templates import caption_pop
    from capabilities.video.skills.talking_head_overlay import TalkingHeadOverlaySkill

    OUT.mkdir(parents=True, exist_ok=True)

    # 0. 设计画布
    cw, ch = TalkingHeadOverlaySkill._design_canvas(684, 1174)
    print(f"design canvas: {cw}x{ch} (expect 1080x1854)")
    assert (cw, ch) == (1080, 1854), "design canvas unexpected"

    # 1. 渲染 caption（词级均分由模板缺省处理）
    html = caption_pop(
        text="第一 不要长时间对着空调直吹",
        width=cw, height=ch,
        duration=WIN_END - WIN_START,
    )
    overlay = OUT / "diag_caption.mp4"
    result = await render_html_to_video(
        html_content=html, output_path=overlay,
        width=cw, height=ch, duration=WIN_END - WIN_START,
    )
    if not result:
        print("FAIL: render_html_to_video returned None")
        return 1
    print(f"render ok: {result}")

    # 2. 合成到真实底片
    final = OUT / "diag_composed.mp4"
    await compose_with_overlays(
        base_video=VIDEO,
        overlay_videos=[(WIN_START, WIN_END, result)],
        output_path=final,
    )
    print(f"compose ok: {final}")

    # 3. 与底片同帧差分：窗内底部应有字幕差异，窗外应≈0（无泄漏）
    diff_in = await asyncio.to_thread(frame_diff_region, final, VIDEO, 2.5)
    diff_out = await asyncio.to_thread(frame_diff_region, final, VIDEO, 5.5)
    print(f"bottom diff vs base: inside= {diff_in:.2f}, outside= {diff_out:.2f}")

    if diff_in < 3.0:
        print("FAIL: no visible caption inside window (diff too small)")
        return 1
    if diff_out > 3.0:
        print("FAIL: overlay leaks outside window")
        return 1
    print("PASS: caption visible in window, no leak outside")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
