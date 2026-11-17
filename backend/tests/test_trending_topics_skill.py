"""trending_topics Python 技能单测（2026-11-03）。

背景：原 trending_topics 是 prompt skill（裸 LLM 调用无内嵌工具），内层模型
吐 DSML 碎片导致热点链路全失效。重构为 Python 直抓技能后，本文件验证：
1. 注册无歧义（同名 prompt skill 已下线）
2. 平台别名解析
3. 合并/排序/相关度/跨平台重合逻辑（mock 数据，零网络）
4. 全部平台失败 → ok=False（不伪造数据）
5. report 生成
"""

from __future__ import annotations

from typing import Any

import pytest

import capabilities.content_search.skills.trending_topics as tt_mod
from capabilities.content_search.skills.trending_topics import (
    TrendingTopicsInput,
    TrendingTopicsSkill,
)

# ─── 注册无歧义 ───────────────────────────────────────


def test_skill_registered_without_ambiguity():
    """同名 prompt skill 已下线，裸名解析必须唯一命中。"""
    from app.core.skill_registry import ensure_builtin_skills_registered, get_skill_class_by_name

    ensure_builtin_skills_registered()
    cls = get_skill_class_by_name("trending_topics")
    assert cls is not None
    assert cls.name == "trending_topics"
    assert cls.node_type == "discover"
    assert not cls.__module__.startswith(
        "app.core.prompt_skills"
    ), "trending_topics 不应再解析到 prompt skill"


def test_model_tier_contract():
    """沿用原 prompt skill 的模型档位（test_skill_model_tier 契约）。"""
    assert TrendingTopicsSkill.model_tier == "standard"
    assert TrendingTopicsSkill.task_type == "analyze"
    assert TrendingTopicsSkill.temperature_override == 0.5


# ─── 平台别名解析 ─────────────────────────────────────


def test_resolve_platforms_default_all():
    keys = TrendingTopicsSkill._resolve_platforms("")
    assert keys, "hotboard_sources.json 应至少有一个 enabled 平台"
    assert all(k.startswith("hb-") for k in keys)


def test_resolve_platforms_aliases():
    zh = TrendingTopicsSkill._resolve_platforms("微博,抖音")
    assert zh == ["hb-weibo", "hb-douyin"]
    en = TrendingTopicsSkill._resolve_platforms("weibo, B站")
    assert en == ["hb-weibo", "hb-bilibili"]
    unknown = TrendingTopicsSkill._resolve_platforms("twitter")
    assert unknown == []


# ─── mock 抓取执行 ────────────────────────────────────


class _FakeSource:
    """替身 HotboardSource：按 key 返回预置数据 / 抛错（返回真实 TrendingContent）。"""

    data: dict[str, list[dict[str, Any]]] = {}
    fail: set[str] = set()

    def __init__(
        self, platform_key: str, label: str = "", primary_url: str = "", backup_url: str = ""
    ):
        self._key = platform_key
        self._label = label or platform_key

    @property
    def name(self) -> str:
        return self._key

    async def get_trending(self, category: str = "", limit: int = 20):
        from capabilities.content_search.sources.base import TrendingContent

        if self._key in self.fail:
            raise RuntimeError("mock network error")
        results = []
        for i, it in enumerate(self.data.get(self._key, [])[:limit]):
            results.append(
                TrendingContent(
                    platform=self._key,
                    content_id=f"mock_{self._key}_{i}",
                    title=it["title"],
                    summary=it["title"],
                    content=it["title"],
                    author=self._label,
                    url=it.get("url", ""),
                    likes=it["hot"],
                    raw={"rank": i + 1},
                )
            )
        return results

    async def close(self):
        pass


@pytest.fixture
def patched_source(monkeypatch):
    """替换 execute 内部实际用到的 HotboardSource（本地 import 路径）。"""
    import capabilities.content_search.sources.hotboard_source as hb_mod

    _FakeSource.data = {
        "hb-weibo": [
            {"title": "AI 大模型发布", "hot": 900, "url": "u1"},
            {"title": "某明星官宣结婚", "hot": 800, "url": "u2"},
            {"title": "编程入门指南", "hot": 100, "url": "u3"},
        ],
        "hb-douyin": [
            {"title": "AI 大模型发布", "hot": 700, "url": "u4"},
            {"title": "美食探店", "hot": 300, "url": "u5"},
        ],
        "hb-zhihu": [
            {"title": "AI 大模型发布", "hot": 500, "url": "u6"},
        ],
    }
    _FakeSource.fail = set()
    monkeypatch.setattr(hb_mod, "HotboardSource", _FakeSource)
    return _FakeSource


@pytest.mark.asyncio
async def test_execute_merges_and_marks_cross_platform(patched_source):
    skill = TrendingTopicsSkill()
    result = await skill.execute({})
    assert result["ok"] is True
    assert result["count"] == 6

    top = result["results"][0]
    assert top["title"] == "AI 大模型发布"
    assert top["cross_platform"] is True  # 三平台重合
    # 跨平台热度应取各自值，第一条应是微博 900
    assert top["platform"] == "微博"
    assert top["hot"] == 900

    titles = [it["title"] for it in result["results"]]
    assert titles.count("AI 大模型发布") == 3


@pytest.mark.asyncio
async def test_execute_track_relevance(patched_source):
    skill = TrendingTopicsSkill()
    result = await skill.execute({"track": "AI"})
    by_relevance = {}
    for it in result["results"]:
        by_relevance.setdefault(it["relevance"], []).append(it["title"])
    assert "AI 大模型发布" in by_relevance.get("高", [])
    assert "美食探店" in by_relevance.get("低", [])


@pytest.mark.asyncio
async def test_execute_partial_failure(patched_source):
    patched_source.fail = {"hb-douyin", "hb-zhihu"}
    skill = TrendingTopicsSkill()
    result = await skill.execute({"platforms": "微博,抖音,知乎"})
    assert result["ok"] is True
    assert result["by_platform"] == {"hb-weibo": 3}
    assert set(result["failed_platforms"]) == {"hb-douyin", "hb-zhihu"}


@pytest.mark.asyncio
async def test_execute_all_failed_returns_error(patched_source):
    patched_source.fail = {"hb-weibo", "hb-douyin", "hb-zhihu"}
    skill = TrendingTopicsSkill()
    result = await skill.execute({})
    assert result["ok"] is False
    assert "失败" in result["error"]
    # 绝不伪造数据：失败时不得带 results
    assert "results" not in result


@pytest.mark.asyncio
async def test_execute_report_contains_table(patched_source):
    skill = TrendingTopicsSkill()
    result = await skill.execute({})
    report = result["report"]
    assert "# 热点速报" in report
    assert "| # | 平台 | 话题 | 热度 | 相关度 |" in report
    assert "AI 大模型发布" in report


def test_input_schema_defaults():
    in_ = TrendingTopicsInput.model_validate({})
    assert in_.platforms == ""
    assert in_.limit_per_platform == 10
    assert in_.track == ""
