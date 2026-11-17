"""媒体生成 Provider 统一契约（AI 视频工坊）。

设计依据：docs/VIDEO_ARCHITECTURE_ALIGNMENT_MIGRATION_PLAN.md §3/§6

三类媒体能力（LLM 编排 / 图片 / 视频）共用一套接口形状；差异只在输入输出类型。
阶段 0 全部由 MockMediaProvider 实现；阶段 1-3 按 settings.video_provider_mode
切换真实实现时，handler 层（app/services/video_handlers.py）代码零改动。

与旧 VideoGenAdapter 的关系：
  - 旧适配器（app/adapters/video_gen.py）内部本就是"提交→轮询→下载"异步任务模式，
    阶段 3 将其翻转为 submit/poll/download 三段即满足本契约，不是重写；
  - 本契约是任务层（JobService/Worker）与模型层之间的唯一耦合点。
"""

from __future__ import annotations

import logging
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@runtime_checkable
class MediaProvider(Protocol):
    """媒体生成提供方统一接口。

    name: 提供方标识（mock / dashscope / kling / ark）；
    estimate: 生成前预估成本（credits，首版占位，阶段 4 启用计费）；
    其余方法由各能力方法签名自描述（见 MockMediaProvider 实现）。
    """

    name: str

    async def estimate(self, job_type: str, job_input: dict[str, Any]) -> dict[str, float]:
        """预估成本：{"credits": float}。Mock 返回 0。"""
        ...

    async def generate_storyboard(
        self,
        prompt: str,
        *,
        shot_count: int,
        shot_duration: float,
        style: str,
        ratio: str,
    ) -> dict[str, Any]:
        """编排分镜脚本（LLM 能力）。返回 {"title": ..., "shots": [...]}。"""
        ...

    async def generate_shot_image(self, job_input: dict[str, Any]) -> bytes:
        """生成单镜分镜关键帧图片，返回 PNG bytes。"""
        ...

    async def generate_shot_video(self, job_input: dict[str, Any]) -> bytes:
        """生成单镜视频片段，返回 MP4 bytes。"""
        ...

    async def check(self) -> dict[str, bool]:
        """离线自检（不发请求）：{"configured": bool}。"""
        ...


async def resolve_media_provider() -> MediaProvider:
    """按配置解析 Provider。

    video_provider_mode=mock（默认，阶段 0）→ MockMediaProvider；
    video_provider_mode=real（阶段 1 起）→ RealMediaProvider（包装真实 API 适配器）。
    """
    from app.config import get_settings

    settings = get_settings()
    mode = (getattr(settings, "video_provider_mode", "mock") or "mock").strip()

    if mode == "real":
        # 阶段 1+：使用真实 API（DashScope/Kling/Ark）
        from capabilities.video.adapters.real_media_provider import RealMediaProvider

        provider = RealMediaProvider()
        check = await provider.check()
        if not check.get("configured"):
            provider.fallback_note = (
                f"真实 Provider ({check.get('provider', 'unknown')}) "
                f"API Key 未配置，已回落 mock"
            )
            logger.warning(f"[media_provider] {provider.fallback_note}")
            from capabilities.video.adapters.mock_media_provider import MockMediaProvider

            return MockMediaProvider()

        logger.info(f"[media_provider] 使用真实 Provider: {check.get('provider')}")
        return provider

    from capabilities.video.adapters.mock_media_provider import MockMediaProvider

    return MockMediaProvider()
