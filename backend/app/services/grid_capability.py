"""grid 能力访问层（三期 + 五期-B 声明即拓扑）：local(进程内) / remote(HTTP) 按配置切换。

- 调用方（multi_image_video）只 import 这里，不直接 import grid_detect
  ——remote 模式下主进程完全不加载 numpy（grid_detect 模块级拉 numpy）。
- 配置（五期-B）：capability.toml [grid] runtime 字段为唯一控制源。
  runtime="inprocess" → 进程内；runtime="service" → 读 Settings.grid_service_url 走 HTTP。
- 向后兼容：toml 无 [grid] 段时仍读 Settings.grid_service_url。
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from app.engine.protocol.transport import HttpTransport
from app.services.grid_types import GridDetection

logger = logging.getLogger(__name__)


def _service_url() -> str:
    from app.core.capability_loader import get_capability_service_url

    return get_capability_service_url("grid")


async def detect_grid(image_path: str | Path) -> GridDetection:
    url = _service_url()
    if url:
        transport = HttpTransport(url, timeout_seconds=60)
        try:
            result = await transport.call("grid_detect", {"image_path": str(image_path)})
        finally:
            await transport.aclose()
        if not result.get("ok", True):
            logger.warning(f"[grid-cap] remote detect failed: {result.get('error')}")
            return GridDetection(is_grid=False, reason="remote 检测失败，按单图处理")
        return GridDetection.from_dict(result)

    # local：numpy 计算丢线程池，避免阻塞事件循环
    from app.services.grid_detect import detect_grid as _local_detect

    det = await asyncio.to_thread(_local_detect, image_path)
    return det


async def split_grid(
    image_path: str | Path,
    out_dir: str | Path,
    detection: GridDetection,
) -> list[Path]:
    url = _service_url()
    if url:
        transport = HttpTransport(url, timeout_seconds=120)
        try:
            result = await transport.call(
                "grid_split",
                {
                    "image_path": str(image_path),
                    "out_dir": str(out_dir),
                    "detection": detection.to_dict(),
                },
            )
        finally:
            await transport.aclose()
        if not result.get("ok", True):
            raise RuntimeError(f"remote grid_split failed: {result.get('error')}")
        return [Path(t) for t in result.get("tiles", [])]

    from app.services.grid_detect import split_grid as _local_split

    return await asyncio.to_thread(_local_split, image_path, out_dir, detection)