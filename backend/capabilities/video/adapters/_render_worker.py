"""渲染 worker — 独立子进程跑 Playwright 帧截图。

为什么是独立子进程（ADR-001 补充，环境适配）：
  本机环境下 Playwright 的 browser.close()/上下文清理会挂起（Chromium 启动、
  set_content、screenshot 均正常，已探针验证）。worker 在帧全部落盘后
  os._exit(0) 硬退出跳过清理；父进程负责 taskkill /T 清理进程树。

用法（命令行）:
  python _render_worker.py <html_file> <frames_dir> <width> <height> <duration> <fps>
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path


def main() -> int:
    html_file, frames_dir, width, height, duration, fps = sys.argv[1:7]
    width, height, duration, fps = int(width), int(height), float(duration), int(fps)

    frames_path = Path(frames_dir)
    frames_path.mkdir(parents=True, exist_ok=True)
    html = Path(html_file).read_text(encoding="utf-8")

    n_frames = max(1, int(duration * fps + 0.999))

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
            page.set_content(html, wait_until="load")
            page.wait_for_timeout(120)  # 字体渲染余量

            for i in range(n_frames):
                t = i / fps
                page.evaluate("(t) => window.seekTo && window.seekTo(t)", t)
                page.screenshot(
                    path=str(frames_path / ("frame_%06d.png" % i)),
                    omit_background=True,
                    type="png",
                )

            status_file.write_text(
                json.dumps(
                    {
                        "ok": True,
                        "frames": n_frames,
                        "elapsed": round(time.time() - t0, 2),
                    }
                ),
                encoding="utf-8",
            )
            # 帧已全部落盘即达成目标；不调 browser.close()（本机会挂起）
            sys.stdout.flush()
            import os

            os._exit(0)
    except Exception as e:
        status_file.write_text(
            json.dumps(
                {
                    "ok": False,
                    "error": f"{type(e).__name__}: {e}",
                }
            ),
            encoding="utf-8",
        )
        import os

        os._exit(1)


if __name__ == "__main__":
    raise SystemExit(main())
