"""图片生成桥接：供 reactor primitives / agent 工具调用的统一入口。"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


async def generate_image_with_wanx(
    prompt: str,
    style: str = "default",
    count: int = 3,
    size: str = "1080x1440",
) -> list[str]:
    """用通义万相生成图片，保存到 uploads 并返回 URL 列表。

    Args:
        prompt: 图片描述
        style: 风格（default / 电影感 / ins风 等，会拼入 prompt）
        count: 生成数量（1-4）
        size: 尺寸，如 "1080x1440"

    Returns:
        图片 URL 列表（/uploads/assets/... 格式）
    """
    from capabilities.image_gen.adapters.image_gen import get_default_adapter
    from app.services.asset_library import save_asset
    from app.config import get_settings

    settings = get_settings()
    adapter = get_default_adapter()
    if adapter is None:
        raise RuntimeError("无可用的图片生成 adapter（需配置 DASHSCOPE_API_KEY）")

    style_prefix = ""
    if style and style != "default":
        style_prefix = f"{style}风格，"

    full_prompt = f"{style_prefix}{prompt}"

    images = await adapter.generate(prompt=full_prompt, size=size, count=count)
    if not images:
        # adapter 失败时会把真实原因记在类变量上（欠费/限流/Key 无效/超时），
        # 必须透出——否则 ReAct 循环只看到"adapter 返回空列表"，
        # 无法判断是环境问题（欠费）还是参数问题，反复撞同一堵墙。
        err_type = getattr(adapter, "last_error_type", None) or ""
        err_msg = getattr(adapter, "last_error_message", "") or ""
        if err_msg:
            raise RuntimeError(f"图片生成失败（{err_type or 'unknown'}）：{err_msg}")
        raise RuntimeError("图片生成失败：adapter 返回空列表")

    urls: list[str] = []
    for idx, img_bytes in enumerate(images):
        try:
            url = await save_asset(
                user_id="system",
                filename=f"wanx_{idx}.png",
                content=img_bytes,
                content_type="image/png",
            )
            urls.append(url)
        except Exception as e:
            logger.warning(f"[write_helper] 保存第 {idx} 张图片失败：{e}")

    if not urls:
        raise RuntimeError("图片生成后保存全部失败")

    return urls