"""Real Media Provider：将 VideoGenAdapter 适配为 MediaProvider 契约。

设计依据：docs/VIDEO_ARCHITECTURE_ALIGNMENT_MIGRATION_PLAN.md §3（阶段 1-3）

职责：
  - 包装已有的 VideoGenAdapter（DashScope/Kling/Ark），
    将其 text2video/image2video 接口映射到 MediaProvider 的
    generate_shot_video/generate_shot_image 契约；
  - 分镜编排仍走 LLM（video_orchestrator.generate_storyboard），
    本类只负责"单镜素材生成"这一步的真实 API 调用；
  - 图片生成复用 WanxAdapter（与视频同生态 DashScope）。

与 MockMediaProvider 的关系：
  - 同一 MediaProvider 契约的不同实现；
  - handler 层（video_handlers.py）零改动即可切换 mock ↔ real。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class RealMediaProvider:
    """真实 API 的 MediaProvider 实现（阶段 1+）。"""

    name = "real"

    def __init__(self) -> None:
        self._video_adapter: Any = None
        self._image_adapter: Any = None
        self.fallback_note: str | None = None

    # ── 延迟初始化（避免 import 时就读配置）─────────────────

    def _get_video_adapter(self) -> Any:
        if self._video_adapter is None:
            from app.config import get_settings
            from capabilities.video.adapters.video_gen import create_video_adapter

            settings = get_settings()
            provider = getattr(settings, "video_provider", "dashscope") or "dashscope"
            kwargs: dict[str, str] = {"provider": provider}

            if provider == "dashscope":
                kwargs["api_key"] = getattr(settings, "dashscope_api_key", "") or ""
                kwargs["model"] = getattr(settings, "dashscope_video_model", "") or "wanx2.1-t2v-turbo"
            elif provider == "kling":
                kwargs["access_key"] = getattr(settings, "kling_access_key", "") or ""
                kwargs["secret_key"] = getattr(settings, "kling_secret_key", "") or ""
                kwargs["model"] = getattr(settings, "kling_model", "") or "kling-v1"
            elif provider == "ark":
                kwargs["api_key"] = getattr(settings, "ark_api_key", "") or ""
                kwargs["model"] = getattr(settings, "ark_model", "") or ""

            self._video_adapter = create_video_adapter(**kwargs)
            logger.info(f"[real_provider] 视频适配器已创建: {provider}")
        return self._video_adapter

    def _get_image_adapter(self) -> Any:
        if self._image_adapter is None:
            from app.config import get_settings
            from capabilities.image_gen.adapters.image_gen import WanxAdapter

            settings = get_settings()
            api_key = getattr(settings, "dashscope_api_key", "") or ""
            model = getattr(settings, "wanx_model", "") or "wanx2.1-t2i-turbo"
            self._image_adapter = WanxAdapter(api_key=api_key, model=model)
            logger.info("[real_provider] 图片适配器已创建: wanx")
        return self._image_adapter

    # ── MediaProvider 契约实现 ─────────────────────────────

    async def estimate(self, job_type: str, job_input: dict[str, Any]) -> dict[str, Any]:
        """成本预估（阶段 4 启用计费；当前返回占位）。"""
        return {"credits": 0.0}

    async def generate_storyboard(
        self,
        prompt: str,
        *,
        shot_count: int,
        shot_duration: float,
        style: str,
        ratio: str,
    ) -> dict[str, Any]:
        """分镜编排：委托给 video_orchestrator（LLM 编排）。"""
        from capabilities.video.services.video_orchestrator import video_orchestrator

        storyboard = await video_orchestrator.create_storyboard(
            prompt=prompt,
            target_duration=shot_count * shot_duration,
            shot_duration=shot_duration,
            style=style,
            ratio=ratio,
        )
        storyboard["degradation"] = []
        logger.info(f"[real_provider] 分镜编排完成: {len(storyboard.get('shots', []))} 镜")
        return storyboard

    async def generate_shot_image(self, job_input: dict[str, Any]) -> bytes:
        """单镜关键帧：调用 WanxAdapter 文生图。"""
        adapter = self._get_image_adapter()
        shot = job_input.get("shot", {})
        image_prompt = shot.get("image_prompt", "")
        ratio = job_input.get("ratio", "9:16")

        logger.info(f"[real_provider] 生成分镜图: {shot.get('shot_index', '?')}")
        # WanxAdapter.generate() 返回 list[bytes]，取第一张
        image_list = await adapter.generate(
            prompt=image_prompt,
            size=self._ratio_to_wanx_size(ratio),
            count=1,
        )
        if not image_list or not image_list[0]:
            raise RuntimeError("WanxAdapter 返回空图片")
        return image_list[0]

    async def generate_shot_video(self, job_input: dict[str, Any]) -> bytes:
        """单镜视频片段：调用 VideoGenAdapter（DashScope/Kling/Ark）。"""
        adapter = self._get_video_adapter()
        shot = job_input.get("shot", {})
        image_prompt = shot.get("image_prompt", "")
        ratio = job_input.get("ratio", "9:16")
        duration = float(job_input.get("duration") or 5.0)

        # 检查是否有参考图（frame_url）→ 图生视频；否则文生视频
        frame_url = shot.get("frame_url")

        logger.info(
            f"[real_provider] 生成视频片段: "
            f"shot={shot.get('shot_index', '?')} mode={'i2v' if frame_url else 't2v'}"
        )

        if frame_url:
            video_bytes = await adapter.image2video(
                image=frame_url,
                prompt=image_prompt,
                ratio=ratio,
                duration=duration,
            )
        else:
            video_bytes = await adapter.text2video(
                prompt=image_prompt,
                ratio=ratio,
                duration=duration,
            )

        if not video_bytes:
            raise RuntimeError("VideoGenAdapter 返回空视频")
        return video_bytes

    async def check(self) -> dict[str, bool]:
        """离线自检：检查 API Key 是否配置。"""
        from app.config import get_settings

        settings = get_settings()
        provider = getattr(settings, "video_provider", "dashscope") or "dashscope"

        checks: dict[str, bool] = {"configured": False}

        if provider == "dashscope":
            api_key = getattr(settings, "dashscope_api_key", "") or ""
            checks["configured"] = bool(api_key)
            checks["api_key_set"] = checks["configured"]
        elif provider == "kling":
            ak = getattr(settings, "kling_access_key", "") or ""
            sk = getattr(settings, "kling_secret_key", "") or ""
            checks["configured"] = bool(ak and sk)
            checks["access_key_set"] = bool(ak)
            checks["secret_key_set"] = bool(sk)
        elif provider == "ark":
            api_key = getattr(settings, "ark_api_key", "") or ""
            checks["configured"] = bool(api_key)
            checks["api_key_set"] = checks["configured"]

        checks["provider"] = provider
        return checks

    # ── 内部工具方法 ──────────────────────────────────────

    @staticmethod
    def _ratio_to_wanx_size(ratio: str) -> str:
        """比例 → WanxAdapter 的 size 参数。

        WanxAdapter 支持的 size（依据 image_gen.py）：
          - "1024*1024" (1:1)
          - "768*1344" (9:16)
          - "1344*768" (16:9)
          - "720*1280" (移动端竖屏)
        """
        mapping = {
            "1:1": "1024*1024",
            "9:16": "768*1344",
            "16:9": "1344*768",
        }
        return mapping.get(ratio, "768*1344")