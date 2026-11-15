"""选题池自动抓取不得消耗付费搜索源（DeepSeek）的契约测试。"""
from __future__ import annotations

from capabilities.content_search.sources.base import ContentSource, TrendingContent
from capabilities.pool_monitor.monitor_agent import auto_crawl_platforms


class _FreeSource(ContentSource):
    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    async def search_trending(self, keyword: str, limit: int = 20, time_range: str = "week") -> list[TrendingContent]:
        return []

    async def get_trending(self, category: str = "", limit: int = 20) -> list[TrendingContent]:
        return []

    async def close(self) -> None:
        return None


class _BillableSource(_FreeSource):
    @property
    def billable(self) -> bool:
        return True


class _FakeManager:
    def __init__(self, sources: list[ContentSource]) -> None:
        self._sources = {s.name: s for s in sources}

    def list_platforms(self) -> list[str]:
        return list(self._sources)

    def get_source(self, platform: str) -> ContentSource | None:
        return self._sources.get(platform)


def test_billable_defaults_to_false():
    assert _FreeSource("free").billable is False


def test_deepseek_sources_are_billable():
    from capabilities.content_search.sources.deepseek_api_source import DeepSeekApiSource
    from capabilities.content_search.sources.deepseek_search_source import DeepSeekSearchSource

    assert object.__new__(DeepSeekApiSource).billable is True
    assert object.__new__(DeepSeekSearchSource).billable is True


def test_auto_crawl_skips_billable_sources():
    mgr = _FakeManager([
        _FreeSource("sogou"),
        _FreeSource("builtin"),
        _BillableSource("deepseek"),
        _BillableSource("deepseek_api"),
    ])
    platforms, skipped = auto_crawl_platforms(mgr)
    assert platforms == ["sogou", "builtin"]
    assert sorted(skipped) == ["deepseek", "deepseek_api"]


def test_auto_crawl_skips_xiaohongshu():
    mgr = _FakeManager([_FreeSource("xiaohongshu"), _FreeSource("sogou")])
    platforms, skipped = auto_crawl_platforms(mgr)
    assert platforms == ["sogou"]
    assert skipped == []


def test_auto_crawl_returns_empty_when_only_billable():
    mgr = _FakeManager([_BillableSource("deepseek_api")])
    platforms, skipped = auto_crawl_platforms(mgr)
    assert platforms == []
    assert skipped == ["deepseek_api"]
