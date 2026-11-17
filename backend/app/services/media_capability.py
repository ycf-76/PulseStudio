"""media 能力访问层（五期-D）：local(进程内) / remote(HTTP) 按配置切换。

- 调用方（auto_short_video / video_compose skill）只 import 这里
  ——remote 模式下主进程完全不加载 piper onnx / ffmpeg。
- 配置（五期-B）：capability.toml [media] runtime 字段为唯一控制源。
  runtime="inprocess" → 进程内；runtime="service" → 读 Settings.media_service_url 走 HTTP。
- 向后兼容：toml 无 [media] 段时仍读 Settings.media_service_url。
- 复用 grid_capability.py 的范式（核心下沉→服务注册→配置切换→双跑契约）。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _service_url() -> str:
    from app.core.capability_loader import get_capability_service_url

    return get_capability_service_url("media")


async def tts_piper(
    text: str,
    output_path: str | Path,
    *,
    binary: str | None = None,
    model_path: str | None = None,
) -> str:
    """piper 离线 TTS：local / remote 按配置切换。

    local: 直接调 synthesize_piper()（进程内 piper + onnx）
    remote: HTTP 调 capability-service tts_piper（隔离进程 piper + onnx）
    """
    url = _service_url()
    out_str = str(output_path)

    if url:
        from app.engine.protocol.transport import HttpTransport

        transport = HttpTransport(url, timeout_seconds=120)
        try:
            result = await transport.call(
                "tts_piper",
                {
                    "text": text,
                    "output_path": out_str,
                    "binary": binary or "",
                    "model_path": model_path or "",
                },
            )
        finally:
            await transport.aclose()
        if not result.get("ok", True):
            raise RuntimeError(f"remote tts_piper failed: {result.get('error')}")
        return result.get("output_path", out_str)

    from app.skills.produce.tts_piper import synthesize_piper

    return await synthesize_piper(text, out_str, binary=binary, model_path=model_path)


async def video_compose(
    storyboard: dict[str, Any],
    output_path: str | Path | None = None,
    *,
    pad_mode: str = "auto",
    sub_font: str | None = None,
) -> dict[str, Any]:
    """视频合成：local / remote 按配置切换。

    local: 直接调 compose_video()（进程内 ffmpeg）
    remote: HTTP 调 capability-service video_compose（隔离进程 ffmpeg）
    """
    url = _service_url()

    if url:
        from app.engine.protocol.transport import HttpTransport

        transport = HttpTransport(url, timeout_seconds=600)
        try:
            result = await transport.call(
                "video_compose",
                {
                    "storyboard_json": json.dumps(storyboard),
                    "output_path": str(output_path) if output_path else "",
                    "pad_mode": pad_mode,
                    "sub_font": sub_font or "",
                },
            )
        finally:
            await transport.aclose()
        return result

    from capabilities.video.adapters.video_compose import compose_video

    return await compose_video(
        storyboard=storyboard,
        output_path=str(output_path) if output_path else None,
        pad_mode=pad_mode,
        sub_font=sub_font,
    )
