"""Integration tests for execution layer scripts and code-type Skills."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path

import pytest
from PIL import Image


SKILLS_DIR = Path(__file__).resolve().parent.parent / "app" / "agents" / "prompts" / "skills"
OUTPUTS_DIR = Path(__file__).resolve().parent.parent / "outputs"


@pytest.fixture
def base_image(tmp_path: Path) -> Path:
    img = Image.new("RGB", (600, 400), color=(100, 150, 200))
    p = tmp_path / "base.png"
    img.save(p)
    return p


class TestMemeOps:
    def test_classic_meme_top_bottom(self, base_image: Path, tmp_path: Path):
        from app.agents.prompts.scripts.meme_ops import render_classic

        out = render_classic(base_image, top_text="上方文字", bottom_text="下方文字", output_path=tmp_path / "classic.png")
        assert out.exists()
        assert out.stat().st_size > 0
        with Image.open(out) as result:
            assert result.size == (600, 400)

    def test_classic_meme_top_only(self, base_image: Path, tmp_path: Path):
        from app.agents.prompts.scripts.meme_ops import render_classic

        out = render_classic(base_image, top_text="只有上方", output_path=tmp_path / "top.png")
        assert out.exists()
        assert out.stat().st_size > 0

    def test_reaction_meme(self, base_image: Path, tmp_path: Path):
        from app.agents.prompts.scripts.meme_ops import render_reaction

        out = render_reaction(base_image, caption="反应图配文", output_path=tmp_path / "reaction.png")
        assert out.exists()
        with Image.open(out) as result:
            assert result.height > 400

    def test_classic_meme_long_text(self, base_image: Path, tmp_path: Path):
        from app.agents.prompts.scripts.meme_ops import render_classic

        out = render_classic(base_image, top_text="这是一段很长的文字用来测试自动换行功能是否正常工作", output_path=tmp_path / "long.png")
        assert out.exists()
        assert out.stat().st_size > 0


class TestMindmap:
    def test_render_mindmap(self, tmp_path: Path):
        from app.agents.prompts.scripts.mindmap import render_mindmap

        md = "# 中心\n## 分支一\n- A\n## 分支二\n- B\n"
        out = render_mindmap(md, tmp_path / "mm.html")
        assert out.exists()
        content = out.read_text(encoding="utf-8")
        assert "markmap" in content
        assert "中心" in content

    def test_render_mindmap_multilevel(self, tmp_path: Path):
        from app.agents.prompts.scripts.mindmap import render_mindmap

        md = "# Root\n## A\n### A1\n### A2\n## B\n### B1\n"
        out = render_mindmap(md, tmp_path / "multi.html")
        assert out.exists()
        content = out.read_text(encoding="utf-8")
        assert "Root" in content


class TestChartRender:
    def test_bar_chart(self, tmp_path: Path):
        from app.agents.prompts.scripts.chart_render import render_chart

        option = {
            "xAxis": {"type": "category", "data": ["A", "B", "C"]},
            "yAxis": {"type": "value"},
            "series": [{"type": "bar", "data": [10, 20, 30]}],
        }
        out = render_chart(option, tmp_path / "bar.html")
        assert out.exists()
        content = out.read_text(encoding="utf-8")
        assert "echarts" in content
        assert "bar" in content

    def test_line_chart_with_title(self, tmp_path: Path):
        from app.agents.prompts.scripts.chart_render import render_chart

        option = {
            "title": {"text": "趋势图"},
            "xAxis": {"type": "category", "data": ["1月", "2月", "3月"]},
            "yAxis": {"type": "value"},
            "series": [{"type": "line", "data": [100, 200, 150]}],
        }
        out = render_chart(option, tmp_path / "line.html")
        assert out.exists()
        content = out.read_text(encoding="utf-8")
        assert "趋势图" in content


class TestDocConvert:
    def test_md_to_html(self):
        from app.agents.prompts.scripts.doc_convert import md_to_html

        md = "# 标题\n\n段落 **粗体** 和 `代码`。\n\n| A | B |\n|---|---|\n| 1 | 2 |"
        html = md_to_html(md)
        assert "<h1" in html
        assert "<strong>" in html
        assert "<code>" in html
        assert "<table>" in html

    def test_md_to_html_empty(self):
        from app.agents.prompts.scripts.doc_convert import md_to_html

        html = md_to_html("")
        assert "<!DOCTYPE html>" in html


class TestTTSEdge:
    def test_resolve_voice_zh(self):
        from app.agents.prompts.scripts.tts_edge import resolve_voice

        assert resolve_voice("xiaoxiao") == "zh-CN-XiaoxiaoNeural"
        assert resolve_voice("yunxi") == "zh-CN-YunxiNeural"

    def test_resolve_voice_en(self):
        from app.agents.prompts.scripts.tts_edge import resolve_voice

        assert resolve_voice("aria") == "en-US-AriaNeural"

    def test_resolve_voice_full_id(self):
        from app.agents.prompts.scripts.tts_edge import resolve_voice

        assert resolve_voice("zh-CN-YunyangNeural") == "zh-CN-YunyangNeural"

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="真调 Edge TTS 网络服务（wss://speech.platform.bing.com）——硬规矩：测试不许打外部服务，网络合成验证走真机冒烟")
    async def test_synthesize(self, tmp_path: Path):
        from app.agents.prompts.scripts.tts_edge import synthesize

        out = await synthesize("测试语音", tmp_path / "tts.mp3", voice="zh-CN-XiaoxiaoNeural")
        assert out.exists()
        assert out.stat().st_size > 0


class TestCodeTypeSkills:
    def test_meme_render_skill_import(self):
        from capabilities.video.skills.meme_render import MemeRenderSkill

        assert MemeRenderSkill.name == "meme_render"
        assert MemeRenderSkill.capability_tier == "image_render"

    def test_tts_skill_import(self):
        from capabilities.video.skills.tts2 import TTSSkill

        assert TTSSkill.name == "tts_edge"
        assert TTSSkill.capability_tier == "external_service"

    def test_meme_render_skill_input_schema(self):
        from capabilities.video.skills.meme_render import MemeRenderInput

        data = MemeRenderInput(image_path="/tmp/test.png", top_text="上", bottom_text="下")
        assert data.mode == "auto"

    def test_tts_skill_input_schema(self):
        from capabilities.video.skills.tts2 import TTSInput

        data = TTSInput(text="你好", voice="xiaoxiao")
        assert data.voice == "xiaoxiao"


class TestCapabilityTierDistribution:
    def test_tier_counts(self):
        import app.core.prompt_skills as _ps
        _ps._scanned = False
        from app.core.prompt_skills import scan_and_register_prompt_skills
        from app.core.skill_registry import SkillRegistry

        scan_and_register_prompt_skills()
        registry = SkillRegistry.instance()
        all_skills = registry.all_skill_classes()

        from collections import Counter
        tiers = Counter(getattr(s, "capability_tier", "NOT_SET") for s in all_skills)

        # 2026-10 技能改版：html_card 档被重分类（并入 text_only / image_render），
        # 保留主干断言；external_service 上限约束仍在
        assert tiers["text_only"] >= 50
        assert tiers.get("image_render", 0) >= 1
        assert "external_service" not in tiers or tiers["external_service"] <= 2

    def test_key_skills_have_correct_tier(self):
        import app.core.prompt_skills as _ps
        _ps._scanned = False
        from app.core.prompt_skills import scan_and_register_prompt_skills
        from app.core.skill_registry import SkillRegistry

        scan_and_register_prompt_skills()
        registry = SkillRegistry.instance()
        all_skills = registry.all_skill_classes()
        by_name = {getattr(s, "name", ""): s for s in all_skills}

        # meme_generator 是 prompt 生成类（text_only）；meme_render 才是渲染类（image_render）
        assert getattr(by_name.get("video_script"), "capability_tier", "") == "text_only"
        assert getattr(by_name.get("meme_generator"), "capability_tier", "") == "text_only"
        assert getattr(by_name.get("meme_render"), "capability_tier", "") == "image_render"
        # 2026-10 改版：卡片/图表类技能的 capability_tier 统一为 text_only
        # （渲染由 meme_render 等专用 image_render 技能承担，html_card 档退役）
        assert getattr(by_name.get("chart_visualization"), "capability_tier", "") == "text_only"
        assert getattr(by_name.get("data_report"), "capability_tier", "") == "text_only"
        assert getattr(by_name.get("copywriting"), "capability_tier", "") == "text_only"
        assert getattr(by_name.get("card_xiaohongshu"), "capability_tier", "") == "text_only"