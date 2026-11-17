"""Tests for TieredModelRouter — model tier allocation across pipeline stages.

W3c：路由器已迁至 app/services.model_tier。
2026-10-16 修正过期期望：2026-10-13 真机探活后模型名已从旧命名
（qwen2.5-vl-7b-instruct/qwen2.5-vl-72b-instruct/wan2.1-i2v-*）切换为 qwen2.5-vl-*/wanx2.1-i2v-*，
本文件的期望值未同步更新（存量失败），本次一并修正。
2026-10-27 二次修正：真机探活当前百炼账号唯一可用 VL = qwen3-vl-plus
（qwen2.5-vl-* 与 qwen-vl-max* 均 403/404），档位语义测试改为动态引用
_DEFAULT_TIER_MODELS，今后换模型名只改 model_tier.py 一处。
"""

from __future__ import annotations

import pytest

from app.services.model_tier import (
    _DEFAULT_STAGE_TIERS,
    _DEFAULT_TIER_MODELS,
    ModelTier,
    PipelineStage,
    TieredModelRouter,
)


class TestModelTier:

    def test_economy_value(self):
        assert ModelTier.ECONOMY.value == "economy"

    def test_standard_value(self):
        assert ModelTier.STANDARD.value == "standard"

    def test_quality_value(self):
        assert ModelTier.QUALITY.value == "quality"


class TestPipelineStage:

    def test_all_stages_defined(self):
        assert PipelineStage.VL_UNDERSTAND.value == "vl_understand"
        assert PipelineStage.LLM_STORYBOARD.value == "llm_storyboard"
        assert PipelineStage.TEXT2IMAGE.value == "text2image"
        assert PipelineStage.IMAGE2VIDEO.value == "image2video"
        assert PipelineStage.AUDIO_ENHANCE.value == "audio_enhance"


class TestDefaultStageTiers:

    def test_planning_uses_economy(self):
        assert _DEFAULT_STAGE_TIERS[PipelineStage.VL_UNDERSTAND] == ModelTier.ECONOMY
        assert _DEFAULT_STAGE_TIERS[PipelineStage.LLM_STORYBOARD] == ModelTier.ECONOMY

    def test_generation_uses_quality(self):
        assert _DEFAULT_STAGE_TIERS[PipelineStage.TEXT2IMAGE] == ModelTier.QUALITY
        assert _DEFAULT_STAGE_TIERS[PipelineStage.IMAGE2VIDEO] == ModelTier.QUALITY

    def test_postprocess_uses_standard(self):
        assert _DEFAULT_STAGE_TIERS[PipelineStage.AUDIO_ENHANCE] == ModelTier.STANDARD


class TestDefaultTierModels:

    def test_economy_uses_cheap_models(self):
        eco = _DEFAULT_TIER_MODELS[ModelTier.ECONOMY]
        # 2026-10-27 真机探活：当前账号唯一可用 VL = qwen3-vl-plus
        assert eco["vl"] == "qwen3-vl-plus"
        assert eco["t2i"] == "wanx2.1-t2i-turbo"
        assert eco["i2v"] == "wanx2.1-i2v-turbo"

    def test_quality_uses_strong_models(self):
        qual = _DEFAULT_TIER_MODELS[ModelTier.QUALITY]
        assert qual["t2i"] == "wanx2.1-t2i-plus"
        assert qual["i2v"] == "wanx2.1-i2v-plus"

    def test_standard_uses_turbo_models(self):
        std = _DEFAULT_TIER_MODELS[ModelTier.STANDARD]
        assert std["t2i"] == "wanx2.1-t2i-turbo"
        assert std["i2v"] == "wanx2.1-i2v-turbo"


class TestTieredModelRouterResolve:

    def setup_method(self):
        self.router = TieredModelRouter()

    def test_vl_understand_uses_economy_vl(self):
        model = self.router.resolve(PipelineStage.VL_UNDERSTAND, "vl")
        assert model == "qwen3-vl-plus"

    def test_llm_storyboard_uses_economy_llm(self):
        model = self.router.resolve(PipelineStage.LLM_STORYBOARD, "llm")
        assert model == "deepseek-chat"

    def test_text2image_uses_quality_t2i(self):
        model = self.router.resolve(PipelineStage.TEXT2IMAGE, "t2i")
        assert model == "wanx2.1-t2i-plus"

    def test_image2video_uses_quality_i2v(self):
        model = self.router.resolve(PipelineStage.IMAGE2VIDEO, "i2v")
        assert model == "wanx2.1-i2v-plus"

    def test_audio_enhance_uses_standard(self):
        model = self.router.resolve(PipelineStage.AUDIO_ENHANCE, "t2i")
        assert model == "wanx2.1-t2i-turbo"

    def test_unknown_capability_returns_empty(self):
        model = self.router.resolve(PipelineStage.VL_UNDERSTAND, "unknown_cap")
        assert model == ""

    def test_unknown_stage_falls_to_standard(self):
        from app.services.model_tier import PipelineStage as PS

        model = self.router.resolve(PS.AUDIO_ENHANCE, "vl")
        assert model == _DEFAULT_TIER_MODELS[ModelTier.STANDARD]["vl"]


class TestTieredModelRouterDynamicAdjust:

    def setup_method(self):
        self.router = TieredModelRouter()

    def test_set_stage_tier(self):
        self.router.set_stage_tier(PipelineStage.VL_UNDERSTAND, ModelTier.QUALITY)
        model = self.router.resolve(PipelineStage.VL_UNDERSTAND, "vl")
        assert model == _DEFAULT_TIER_MODELS[ModelTier.QUALITY]["vl"]

    def test_set_tier_model(self):
        self.router.set_tier_model(ModelTier.ECONOMY, "vl", "qwen-vl-max-latest")
        model = self.router.resolve(PipelineStage.VL_UNDERSTAND, "vl")
        assert model == "qwen-vl-max-latest"

    def test_tier_for_stage(self):
        tier = self.router.tier_for_stage(PipelineStage.VL_UNDERSTAND)
        assert tier == ModelTier.ECONOMY

        tier = self.router.tier_for_stage(PipelineStage.TEXT2IMAGE)
        assert tier == ModelTier.QUALITY


class TestTieredModelRouterSummary:

    def test_summary_structure(self):
        router = TieredModelRouter()
        summary = router.summary()

        assert "stage_tiers" in summary
        assert "tier_definitions" in summary
        assert "vl_understand" in summary["stage_tiers"]
        assert summary["stage_tiers"]["vl_understand"]["tier"] == "economy"
        assert summary["stage_tiers"]["text2image"]["tier"] == "quality"

    def test_summary_shows_actual_models(self):
        router = TieredModelRouter()
        summary = router.summary()

        vl_stage = summary["stage_tiers"]["vl_understand"]
        assert "models" in vl_stage
        assert vl_stage["models"]["vl"] == _DEFAULT_TIER_MODELS[ModelTier.ECONOMY]["vl"]

        t2i_stage = summary["stage_tiers"]["text2image"]
        assert t2i_stage["models"]["t2i"] == "wanx2.1-t2i-plus"


class TestTieredModelRouterCustomConfig:

    def test_custom_stage_tiers(self):
        custom_tiers = dict(_DEFAULT_STAGE_TIERS)
        custom_tiers[PipelineStage.VL_UNDERSTAND] = ModelTier.QUALITY
        router = TieredModelRouter(stage_tiers=custom_tiers)

        model = router.resolve(PipelineStage.VL_UNDERSTAND, "vl")
        assert model == _DEFAULT_TIER_MODELS[ModelTier.QUALITY]["vl"]

    def test_custom_tier_models(self):
        custom_models = dict(_DEFAULT_TIER_MODELS)
        custom_models[ModelTier.QUALITY] = {
            "vl": "qwen2.5-vl-72b-instruct",
            "llm": "deepseek-chat",
            "t2i": "wanx2.1-t2i-plus",
            "i2v": "wanx2.1-i2v-plus",
        }
        router = TieredModelRouter(tier_models=custom_models)

        model = router.resolve(PipelineStage.TEXT2IMAGE, "t2i")
        assert model == "wanx2.1-t2i-plus"

    def test_all_quality_for_high_budget(self):
        custom_tiers = {stage: ModelTier.QUALITY for stage in PipelineStage}
        router = TieredModelRouter(stage_tiers=custom_tiers)

        assert (
            router.resolve(PipelineStage.VL_UNDERSTAND, "vl")
            == _DEFAULT_TIER_MODELS[ModelTier.QUALITY]["vl"]
        )
        assert router.resolve(PipelineStage.TEXT2IMAGE, "t2i") == "wanx2.1-t2i-plus"

    def test_all_economy_for_low_budget(self):
        custom_tiers = {stage: ModelTier.ECONOMY for stage in PipelineStage}
        router = TieredModelRouter(stage_tiers=custom_tiers)

        assert (
            router.resolve(PipelineStage.VL_UNDERSTAND, "vl")
            == _DEFAULT_TIER_MODELS[ModelTier.ECONOMY]["vl"]
        )
        assert router.resolve(PipelineStage.TEXT2IMAGE, "t2i") == "wanx2.1-t2i-turbo"


class TestTieredModelRouterFallback:

    def test_missing_capability_falls_to_standard(self):
        tier_models = dict(_DEFAULT_TIER_MODELS)
        tier_models[ModelTier.ECONOMY] = {"llm": "deepseek-chat"}
        router = TieredModelRouter(tier_models=tier_models)

        model = router.resolve(PipelineStage.VL_UNDERSTAND, "vl")
        assert model == _DEFAULT_TIER_MODELS[ModelTier.STANDARD]["vl"]

    def test_missing_capability_falls_to_economy(self):
        tier_models = dict(_DEFAULT_TIER_MODELS)
        tier_models[ModelTier.ECONOMY] = {"llm": "deepseek-chat"}
        tier_models[ModelTier.STANDARD] = {"llm": "deepseek-chat"}
        router = TieredModelRouter(tier_models=tier_models)

        model = router.resolve(PipelineStage.VL_UNDERSTAND, "llm")
        assert model == "deepseek-chat"
