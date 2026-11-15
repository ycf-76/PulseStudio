"""PromptDrivenSkill DSML 残片防护单测（2026-11-03）。

背景：PromptDrivenSkill 是单次裸 LLM 调用、无内嵌工具执行器。当 SKILL.md
指示内层模型"调工具"时，DeepSeek 会吐出原生 <｜｜DSML｜｜ invoke> token，
无人解析执行。防护要求：检测到 DSML 残片时返回 ok=False，绝不把碎片当
report 流给上层 Agent。

零网络、零真机 LLM：LLM 用脚本替身。
"""

from __future__ import annotations

import pytest

from app.core.skill_base import PromptDrivenSkill

_DSML_RESIDUE = (
    '<｜｜DSML｜｜ calls>\n'
    '<｜｜DSML｜｜ invoke name="web_fetch">\n'
    '<｜｜DSML｜｜ parameter name="url" string="true">'
    "https://60s.crystelf.top/v2/weibo</｜｜DSML｜｜ parameter>\n"
    "</｜｜DSML｜｜ invoke>\n"
    "</｜｜DSML｜｜ calls>"
)

_NORMAL_REPORT = "# 热点速报\n\n一切正常，这是正文内容。"


class _ScriptedLLM:
    def __init__(self, content: str):
        self._content = content

    async def chat(self, **kwargs):
        return {"content": self._content}


class _DummyPromptSkill(PromptDrivenSkill):
    node_type = "test"
    name = "dummy_dsml_probe"
    description = "测试用"
    skill_md_path = ""


def _make_skill(tmp_path, monkeypatch, content: str) -> _DummyPromptSkill:
    md = tmp_path / "dummy_skill.md"
    md.write_text("# 测试技能\n\n正文", encoding="utf-8")
    # _load_skill_md 是 classmethod：monkeypatch 类属性，避免读到真实 skills 目录
    monkeypatch.setattr(_DummyPromptSkill, "skill_md_path", str(md), raising=False)
    monkeypatch.setattr(_DummyPromptSkill, "_md_cache", md.read_text(encoding="utf-8"), raising=False)
    monkeypatch.setattr(_DummyPromptSkill, "reference_paths", [], raising=False)
    skill = _DummyPromptSkill()
    skill._llm = _ScriptedLLM(content)
    return skill


@pytest.mark.asyncio
async def test_dsml_residue_rejected(tmp_path, monkeypatch):
    """LLM 输出 DSML 残片 → ok=False，不产出 report。"""
    skill = _make_skill(tmp_path, monkeypatch, _DSML_RESIDUE)

    # 绕过模型配置解析（模型分配失败时自动 fallback 到 inputs["llm"]）
    from app.services import model_assignment

    monkeypatch.setattr(
        model_assignment,
        "get_cached_skill_model_with_default",
        lambda *a, **kw: None,
        raising=False,
    )

    result = await skill.execute({"llm": skill._llm})
    assert result.get("ok") is False
    assert "report" not in result
    assert "DSML" in result.get("error", "") or "碎片" in result.get("error", "")


@pytest.mark.asyncio
async def test_normal_output_passthrough(tmp_path, monkeypatch):
    """正常输出不受防护影响，report 照常返回。"""
    skill = _make_skill(tmp_path, monkeypatch, _NORMAL_REPORT)

    from app.services import model_assignment

    monkeypatch.setattr(
        model_assignment,
        "get_cached_skill_model_with_default",
        lambda *a, **kw: None,
        raising=False,
    )

    result = await skill.execute({"llm": skill._llm})
    assert result.get("report") == _NORMAL_REPORT
    assert result.get("ok") is None or result.get("ok") is not False
