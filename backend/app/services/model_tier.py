"""模型分级调度（TieredModelRouter）：流水线阶段 → 算力分级 → 具体模型名。

W3c 从 app/adapters/video_pipeline.py 迁出（原文件 1200 行死代码随 W3c 删除）。
这里是路由器的唯一家；旧的 `from app.adapters.video_pipeline import
PipelineStage, get_tier_router` 在删除日前由 re-export 兼容层兜底。

核心思想：
  - 规划阶段（VL理解 + LLM编排）→ economy 级别，省算力
  - 生成阶段（生图 + 生视频）→ quality 级别，保质量
  - 后处理阶段（音频/字幕）→ standard 级别，平衡

用法：
    from app.services.model_tier import PipelineStage, get_tier_router
    router = get_tier_router()
    vl_model = router.resolve(PipelineStage.VL_UNDERSTAND, "vl")
    i2v_model = router.resolve(PipelineStage.IMAGE2VIDEO, "i2v")

注意（P1-1）：解析出的模型名是 dashscope 系命名（wanx/qwen 系列），
只应传给 dashscope 适配器；kling/ark 等 provider 应使用自身默认模型，
跨 provider 透传模型名是 bug（见 multi_image_video._generate_shot_clips）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class ModelTier(Enum):
    """模型算力分级。

    economy  → 便宜/快速，用于规划阶段（VL理解、LLM编排）
    standard → 标准性价比，用于一般生成
    quality  → 强模型/高质，用于核心生成阶段（生图、生视频）
    """

    ECONOMY = "economy"
    STANDARD = "standard"
    QUALITY = "quality"


class PipelineStage(Enum):
    """流水线阶段标识。"""

    VL_UNDERSTAND = "vl_understand"
    LLM_STORYBOARD = "llm_storyboard"
    TEXT2IMAGE = "text2image"
    IMAGE2VIDEO = "image2video"
    AUDIO_ENHANCE = "audio_enhance"


_DEFAULT_STAGE_TIERS: dict[PipelineStage, ModelTier] = {
    PipelineStage.VL_UNDERSTAND: ModelTier.ECONOMY,
    PipelineStage.LLM_STORYBOARD: ModelTier.ECONOMY,
    PipelineStage.TEXT2IMAGE: ModelTier.QUALITY,
    PipelineStage.IMAGE2VIDEO: ModelTier.QUALITY,
    PipelineStage.AUDIO_ENHANCE: ModelTier.STANDARD,
}

_DEFAULT_TIER_MODELS: dict[ModelTier, dict[str, str]] = {
    ModelTier.ECONOMY: {
        # 2026-10-27 真机探活：当前百炼账号 qwen2.5-vl-*（7b/72b）与
        # qwen-vl-max* 全部 403/404，唯一可用 VL = qwen3-vl-plus
        # （原生 multimodal-generation 与 compatible-mode 双端点验证通过）
        "vl": "qwen3-vl-plus",
        "llm": "deepseek-chat",
        "t2i": "wanx2.1-t2i-turbo",
        # 2026-10-13 真机探活：wan2.1-i2v-*（新命名）本账号 Model not exist，
        # 旧命名 wanx2.1-i2v-* 可用（当时账户欠费，模型名已过校验层）
        "i2v": "wanx2.1-i2v-turbo",
    },
    ModelTier.STANDARD: {
        "vl": "qwen3-vl-plus",
        "llm": "deepseek-chat",
        "t2i": "wanx2.1-t2i-turbo",
        "i2v": "wanx2.1-i2v-turbo",
    },
    ModelTier.QUALITY: {
        "vl": "qwen3-vl-plus",
        "llm": "deepseek-chat",
        "t2i": "wanx2.1-t2i-plus",
        "i2v": "wanx2.1-i2v-plus",
    },
}


@dataclass
class TieredModelRouter:
    """模型分级路由器：流水线阶段 → 算力分级 → 具体模型名。

    用法：
      router = TieredModelRouter()
      vl_model = router.resolve(PipelineStage.VL_UNDERSTAND, "vl")
      # → "qwen2.5-vl-7b-instruct"（economy 级别的 VL 模型）

      i2v_model = router.resolve(PipelineStage.IMAGE2VIDEO, "i2v")
      # → "wanx2.1-i2v-plus"（quality 级别的 i2v 模型）
    """

    stage_tiers: dict[PipelineStage, ModelTier] = field(default_factory=lambda: dict(_DEFAULT_STAGE_TIERS))
    # 深拷贝：浅拷贝会让 set_tier_model 的写入污染模块级常量 _DEFAULT_TIER_MODELS
    # （曾致测试间/运行时的 tier 调整永久泄漏，2026-10 修复）
    tier_models: dict[ModelTier, dict[str, str]] = field(
        default_factory=lambda: {k: dict(v) for k, v in _DEFAULT_TIER_MODELS.items()}
    )

    def resolve(self, stage: PipelineStage, capability: str) -> str:
        """根据流水线阶段和能力类型，解析出具体模型名。

        Args:
            stage: 流水线阶段（如 PipelineStage.VL_UNDERSTAND）
            capability: 能力类型键（"vl" / "llm" / "t2i" / "i2v"）

        Returns:
            具体模型名（如 "wanx2.1-i2v-plus"）
        """
        tier = self.stage_tiers.get(stage, ModelTier.STANDARD)
        models = self.tier_models.get(tier, {})
        model_name = models.get(capability, "")
        if not model_name:
            for fallback_tier in [ModelTier.STANDARD, ModelTier.ECONOMY]:
                fallback = self.tier_models.get(fallback_tier, {}).get(capability, "")
                if fallback:
                    model_name = fallback
                    break
        logger.debug(f"[tier_router] {stage.value} → {tier.value} → {capability} = {model_name}")
        return model_name

    def tier_for_stage(self, stage: PipelineStage) -> ModelTier:
        """查询某阶段使用的算力分级。"""
        return self.stage_tiers.get(stage, ModelTier.STANDARD)

    def set_stage_tier(self, stage: PipelineStage, tier: ModelTier) -> None:
        """动态调整某阶段的算力分级。"""
        self.stage_tiers[stage] = tier
        logger.info(f"[tier_router] {stage.value} 算力分级调整为 {tier.value}")

    def set_tier_model(self, tier: ModelTier, capability: str, model_name: str) -> None:
        """动态调整某分级下某能力的具体模型。"""
        if tier not in self.tier_models:
            self.tier_models[tier] = {}
        self.tier_models[tier][capability] = model_name
        logger.info(f"[tier_router] {tier.value}.{capability} 模型调整为 {model_name}")

    def summary(self) -> dict[str, Any]:
        """返回分级调度摘要。"""
        stages = {}
        for stage, tier in self.stage_tiers.items():
            models = self.tier_models.get(tier, {})
            stages[stage.value] = {
                "tier": tier.value,
                "models": dict(models),
            }
        return {
            "stage_tiers": stages,
            "tier_definitions": {
                tier.value: models for tier, models in self.tier_models.items()
            },
        }


_DEFAULT_TIER_ROUTER: TieredModelRouter | None = None


def get_tier_router() -> TieredModelRouter:
    """获取全局模型分级路由器（懒初始化，从配置加载）。"""
    global _DEFAULT_TIER_ROUTER
    if _DEFAULT_TIER_ROUTER is None:
        from app.config import get_settings
        settings = get_settings()

        stage_tiers = dict(_DEFAULT_STAGE_TIERS)
        # 深拷贝，避免写 settings 覆盖值时污染模块常量
        tier_models = {k: dict(v) for k, v in _DEFAULT_TIER_MODELS.items()}

        # getattr 兜底必须与 config.py 的 tier_*_vl 默认值保持一致
        # （2026-10-27 教训：两者不一致 → 测试绿但生产解析出已下线模型 404）
        tier_models[ModelTier.ECONOMY]["vl"] = getattr(settings, "tier_economy_vl", "qwen3-vl-plus")
        tier_models[ModelTier.ECONOMY]["llm"] = getattr(settings, "tier_economy_llm", "deepseek-chat")
        tier_models[ModelTier.ECONOMY]["t2i"] = getattr(settings, "tier_economy_t2i", "wanx2.1-t2i-turbo")
        tier_models[ModelTier.ECONOMY]["i2v"] = getattr(settings, "tier_economy_i2v", "wanx2.1-i2v-turbo")

        tier_models[ModelTier.STANDARD]["vl"] = getattr(settings, "tier_standard_vl", "qwen3-vl-plus")
        tier_models[ModelTier.STANDARD]["llm"] = getattr(settings, "tier_standard_llm", "deepseek-chat")
        tier_models[ModelTier.STANDARD]["t2i"] = getattr(settings, "tier_standard_t2i", "wanx2.1-t2i-turbo")
        tier_models[ModelTier.STANDARD]["i2v"] = getattr(settings, "tier_standard_i2v", "wanx2.1-i2v-turbo")

        tier_models[ModelTier.QUALITY]["vl"] = getattr(settings, "tier_quality_vl", "qwen3-vl-plus")
        tier_models[ModelTier.QUALITY]["llm"] = getattr(settings, "tier_quality_llm", "deepseek-chat")
        tier_models[ModelTier.QUALITY]["t2i"] = getattr(settings, "tier_quality_t2i", "wanx2.1-t2i-plus")
        tier_models[ModelTier.QUALITY]["i2v"] = getattr(settings, "tier_quality_i2v", "wanx2.1-i2v-plus")

        _DEFAULT_TIER_ROUTER = TieredModelRouter(stage_tiers=stage_tiers, tier_models=tier_models)
    return _DEFAULT_TIER_ROUTER
