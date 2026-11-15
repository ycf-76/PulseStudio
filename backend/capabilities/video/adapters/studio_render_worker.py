"""Video Studio 场景渲染 worker — 独立子进程跑 Playwright 确定性逐帧截图。

与 _render_worker.py（原片叠加渲染）同构，差异点（Task 2 契约）：
  - page.goto(file://...) 打开磁盘场景文件：场景内相对路径资产才可用
  - screenshot(omit_background=False)：场景负责完整合成，帧不抠 alpha
  - 状态文件字段：ok / count / error

本机 browser.close() 会挂起 → 帧全部落盘后 os._exit(0) 硬退出
（见 _render_worker.py 文档）；父进程负责 taskkill /T 清理进程树。

用法（命令行）:
  python studio_render_worker.py <scene_html> <frames_dir> <width> <height> <duration> <fps>
"""

from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path


def main() -> int:
    scene_html, frames_dir, width, height, duration, fps = sys.argv[1:7]
    width, height, duration, fps = int(width), int(height), float(duration), int(fps)

    frames_path = Path(frames_dir)
    frames_path.mkdir(parents=True, exist_ok=True)

    # 与父进程 studio_render / studio_review 的公式一致（math.ceil），
    # int(x+0.999) 在 duration*fps 略高于整数时少 1 帧 → 帧数不符误报
    n_frames = max(1, math.ceil(duration * fps))

    from playwright.sync_api import sync_playwright

    status_file = frames_path / "_worker_status.json"
    t0 = time.time()

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--force-device-scale-factor=1"])
            page = browser.new_page(
                viewport={"width": width, "height": height},
                device_scale_factor=1,
            )
            # 磁盘场景文件走 file:// 打开，场景内相对路径资产才可用
            page.goto(Path(scene_html).resolve().as_uri(), wait_until="load")
            page.wait_for_timeout(300)  # 字体/资产渲染余量

            for i in range(n_frames):
                t = i / fps
                page.evaluate("(t) => window.seekTo(t)", t)
                page.screenshot(
                    path=str(frames_path / ("frame_%06d.png" % i)),
                    omit_background=False,
                    type="png",
                )

            status_file.write_text(json.dumps({
                "ok": True, "count": n_frames,
                "elapsed": round(time.time() - t0, 2),
            }), encoding="utf-8")
            # 帧已全部落盘即达成目标；不调 browser.close()（本机会挂起）
            sys.stdout.flush()
            import os
            os._exit(0)
    except Exception as e:
        status_file.write_text(json.dumps({
            "ok": False, "count": 0, "error": f"{type(e).__name__}: {e}",
        }), encoding="utf-8")
        import os
        os._exit(1)


if __name__ == "__main__":
    raise SystemExit(main())
