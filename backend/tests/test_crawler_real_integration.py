"""Real integration tests: call external services and verify real data returns.

These tests make REAL network calls — no mocks.
Marked with @pytest.mark.integration so they can be skipped in CI without keys.

Run:
    pytest tests/test_crawler_real_integration.py -v          # all
    pytest tests/test_crawler_real_integration.py -v -m "not slow"  # skip slow ones
    pytest tests/test_crawler_real_integration.py -v -k "tavily"   # only tavily
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Any

import pytest

# Ensure backend is importable
BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# Ensure .env is loaded
try:
    from dotenv import load_dotenv
    load_dotenv(BACKEND_DIR / ".env", override=False)
except ImportError:
    pass

logger = logging.getLogger(__name__)


def _has_env(key: str) -> bool:
    return bool(os.environ.get(key, "").strip())


# ═══════════════════════════════════════════════════════════════════
# Helper: init SourceManager with all available sources
# ═══════════════════════════════════════════════════════════════════

async def _init_all_sources():
    """Initialize SourceManager with all available sources (same as app lifespan)."""
    from capabilities.content_search.sources.manager import source_manager, init_sources
    if not source_manager.list_platforms():
        await init_sources()
    return source_manager


@pytest.fixture(autouse=True)
async def _reset_sources_after_test():
    """每个用例结束后关闭内容源单例。

    source_manager 是模块级单例，其内部 httpx.AsyncClient 在首次请求时绑定
    当时的事件循环；pytest-asyncio 每个用例用独立事件循环，不清理就会把上一个
    循环的客户端带进下一个用例，报 "Event loop is closed" 让真实请求全部失败。
    """
    yield
    from capabilities.content_search.sources.manager import source_manager
    await source_manager.close_all()


# ═══════════════════════════════════════════════════════════════════
# 1. TAVILY: 全网搜索（需 TAVILY_API_KEY）
# ═══════════════════════════════════════════════════════════════════

@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not _has_env("TAVILY_API_KEY"), reason="TAVILY_API_KEY not set")
async def test_tavily_search_returns_real_data():
    """Tavily 全网搜索应返回真实搜索结果。"""
    from capabilities.content_search.sources.tavily_source import TavilySource

    api_key = os.environ["TAVILY_API_KEY"]
    source = TavilySource(api_key=api_key)

    try:
        results = await source.search_trending("AI agent framework", limit=5)
        assert len(results) > 0, "Tavily should return results for 'AI agent framework'"

        first = results[0]
        assert first.title, "Result should have a title"
        assert first.url, "Result should have a URL"
        assert first.platform == "tavily", f"Platform should be 'tavily', got '{first.platform}'"

        logger.info(f"[Tavily] got {len(results)} results, first: {first.title[:60]}")
    finally:
        await source.close()


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not _has_env("TAVILY_API_KEY"), reason="TAVILY_API_KEY not set")
async def test_tavily_chinese_keyword():
    """Tavily 应支持中文关键词搜索。"""
    from capabilities.content_search.sources.tavily_source import TavilySource

    api_key = os.environ["TAVILY_API_KEY"]
    source = TavilySource(api_key=api_key)

    try:
        results = await source.search_trending("夏日穿搭", limit=5)
        assert len(results) > 0, "Tavily should return results for Chinese keyword"

        first = results[0]
        assert first.title, "Result should have a title"
        logger.info(f"[Tavily CN] got {len(results)} results, first: {first.title[:60]}")
    finally:
        await source.close()


# ═══════════════════════════════════════════════════════════════════
# 2. DEEPSEEK: 原生联网搜索（服务端 web_search 工具，复用 DEEPSEEK_API_KEY）
# ═══════════════════════════════════════════════════════════════════

@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not _has_env("DEEPSEEK_API_KEY"), reason="DEEPSEEK_API_KEY not set")
async def test_deepseek_search_returns_real_data():
    """DeepSeek 原生搜索应返回真实网页结果（国内直连，无需代理）。"""
    from capabilities.content_search.sources.deepseek_search_source import (
        DeepSeekSearchSource,
    )

    source = DeepSeekSearchSource(api_key=os.environ["DEEPSEEK_API_KEY"])

    try:
        results = await source.search_trending("小红书 秋冬穿搭", limit=5)
        assert len(results) > 0, "DeepSeek should return results for Chinese keyword"

        first = results[0]
        assert first.platform == "deepseek", f"Platform should be 'deepseek', got '{first.platform}'"
        assert first.title, "Result should have a title"
        assert first.url.startswith("http"), f"Result should have a real URL, got '{first.url}'"

        logger.info(f"[DeepSeek] got {len(results)} results, first: {first.title[:60]}")
    finally:
        await source.close()


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not _has_env("DEEPSEEK_API_KEY"), reason="DEEPSEEK_API_KEY not set")
async def test_deepseek_get_trending():
    """DeepSeek get_trending 应返回真实热点页面（无关键词场景）。"""
    from capabilities.content_search.sources.deepseek_search_source import (
        DeepSeekSearchSource,
    )

    source = DeepSeekSearchSource(api_key=os.environ["DEEPSEEK_API_KEY"])

    try:
        results = await source.get_trending(limit=5)
        assert len(results) > 0, "DeepSeek get_trending should return results"
        for r in results:
            assert r.url and r.title
        logger.info(f"[DeepSeek trending] got {len(results)} results")
    finally:
        await source.close()


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not _has_env("DEEPSEEK_API_KEY"), reason="DEEPSEEK_API_KEY not set")
async def test_deepseek_api_search_returns_real_data():
    """DeepSeek Search API 应返回真实结果，且带正文摘要与发布时间（零模型 token）。"""
    from capabilities.content_search.sources.deepseek_api_source import DeepSeekApiSource

    source = DeepSeekApiSource(api_key=os.environ["DEEPSEEK_API_KEY"])

    try:
        results = await source.search_trending("小红书 秋冬穿搭", limit=5)
        assert len(results) > 0, "Search API should return results for Chinese keyword"

        first = results[0]
        assert first.platform == "deepseek_api", f"Platform should be 'deepseek_api', got '{first.platform}'"
        assert first.title, "Result should have a title"
        assert first.url.startswith("http"), f"Result should have a real URL, got '{first.url}'"
        assert first.content, "Result should carry the body summary as content"
        assert any(r.published_at for r in results), "At least one result should carry a real date"

        logger.info(
            f"[DeepSeek API] got {len(results)} results, first: {first.title[:60]} "
            f"| date={first.published_at} | content_len={len(first.content)}"
        )
    finally:
        await source.close()


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not _has_env("DEEPSEEK_API_KEY"), reason="DEEPSEEK_API_KEY not set")
async def test_deepseek_api_get_trending():
    """DeepSeek Search API get_trending 应返回真实热点页面（无关键词场景）。"""
    from capabilities.content_search.sources.deepseek_api_source import DeepSeekApiSource

    source = DeepSeekApiSource(api_key=os.environ["DEEPSEEK_API_KEY"])

    try:
        results = await source.get_trending(limit=5)
        assert len(results) > 0, "Search API get_trending should return results"
        for r in results:
            assert r.url and r.title
        logger.info(f"[DeepSeek API trending] got {len(results)} results")
    finally:
        await source.close()


# ═══════════════════════════════════════════════════════════════════
# 3. HACKERNEWS: 零认证，免费 API
# ═══════════════════════════════════════════════════════════════════

@pytest.mark.integration
@pytest.mark.asyncio
async def test_hackernews_search_returns_real_data():
    """HackerNews 搜索应返回真实结果（零认证）。"""
    from capabilities.content_search.sources.hackernews_source import HackerNewsSource

    source = HackerNewsSource()
    try:
        results = await source.search_trending("python", limit=5)
        assert len(results) > 0, "HackerNews should return results for 'python'"

        first = results[0]
        assert first.title, "Result should have a title"
        assert first.url, "Result should have a URL"
        assert first.platform == "hackernews"

        logger.info(f"[HN] got {len(results)} results, first: {first.title[:60]}")
    finally:
        await source.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_hackernews_get_trending():
    """HackerNews get_trending 应返回热门帖子。"""
    from capabilities.content_search.sources.hackernews_source import HackerNewsSource

    source = HackerNewsSource()
    try:
        results = await source.get_trending(limit=10)
        assert len(results) > 0, "HackerNews should have trending posts"

        first = results[0]
        assert first.title, "Trending post should have a title"
        assert first.likes >= 0, "Should have likes count"

        logger.info(f"[HN trending] got {len(results)} results, top: {first.title[:60]} (likes={first.likes})")
    finally:
        await source.close()


# ═══════════════════════════════════════════════════════════════════
# 3. GITHUB: 零认证可用（限速60/h），有 token 更好
# ═══════════════════════════════════════════════════════════════════

@pytest.mark.integration
@pytest.mark.asyncio
async def test_github_search_returns_real_data():
    """GitHub 搜索应返回真实仓库结果。"""
    from capabilities.content_search.sources.github_source import GitHubSource

    token = os.environ.get("GITHUB_TOKEN", "")
    source = GitHubSource(token=token)
    try:
        results = await source.search_trending("llm agent", limit=5)
        assert len(results) > 0, "GitHub should return repos for 'llm agent'"

        first = results[0]
        assert first.title, "Repo should have a title"
        assert first.url, "Repo should have a URL"
        assert first.platform == "github"

        logger.info(f"[GitHub] got {len(results)} results, first: {first.title[:60]}")
    finally:
        await source.close()


# ═══════════════════════════════════════════════════════════════════
# 4. DEV.TO: 零认证，免费 API
# ═══════════════════════════════════════════════════════════════════

@pytest.mark.integration
@pytest.mark.asyncio
async def test_devto_search_returns_real_data():
    """Dev.to 应返回真实文章。"""
    from capabilities.content_search.sources.devto_source import DevToSource

    source = DevToSource()
    try:
        results = await source.get_trending(limit=5)
        assert len(results) > 0, "Dev.to should return trending articles"

        first = results[0]
        assert first.title, "Article should have a title"
        assert first.url, "Article should have a URL"
        assert first.platform == "devto"

        logger.info(f"[Dev.to] got {len(results)} results, first: {first.title[:60]}")
    finally:
        await source.close()


# ═══════════════════════════════════════════════════════════════════
# 5. BUILTIN: 零网络依赖，纯本地
# ═══════════════════════════════════════════════════════════════════

@pytest.mark.integration
@pytest.mark.asyncio
async def test_builtin_search_returns_data():
    """内置话题库应返回匹配的中文内容。"""
    from capabilities.content_search.sources.builtin_source import BuiltinSource

    source = BuiltinSource()
    try:
        results = await source.search_trending("穿搭", limit=5)
        assert len(results) > 0, "Builtin should return topics for '穿搭'"

        first = results[0]
        assert first.title, "Topic should have a title"
        assert first.platform == "builtin"

        logger.info(f"[Builtin] got {len(results)} results, first: {first.title[:60]}")
    finally:
        await source.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_builtin_get_trending():
    """内置话题库 get_trending 应返回热门话题。"""
    from capabilities.content_search.sources.builtin_source import BuiltinSource

    source = BuiltinSource()
    try:
        results = await source.get_trending(limit=10)
        assert len(results) > 0, "Builtin should have trending topics"

        first = results[0]
        assert first.title

        logger.info(f"[Builtin trending] got {len(results)} results, top: {first.title[:60]}")
    finally:
        await source.close()


# ═══════════════════════════════════════════════════════════════════
# 6. HOTBOARD: 公益 API，国内直连
# ═══════════════════════════════════════════════════════════════════

@pytest.mark.integration
@pytest.mark.asyncio
async def test_hotboard_weibo_returns_real_data():
    """微博热搜热榜应返回真实热搜数据。"""
    from capabilities.content_search.sources.hotboard_source import HotboardSource, load_hotboard_config

    cfg = load_hotboard_config()
    if "hb-weibo" not in cfg:
        pytest.skip("hb-weibo not in hotboard config")

    weibo_cfg = cfg["hb-weibo"]
    source = HotboardSource(
        platform_key="hb-weibo",
        label=weibo_cfg.get("label", "微博热搜"),
        primary_url=weibo_cfg.get("primary", ""),
        backup_url=weibo_cfg.get("backup", ""),
    )
    try:
        results = await source.get_trending(limit=10)
        assert len(results) > 0, "Weibo hotboard should return trending items"

        first = results[0]
        assert first.title, "Hot item should have a title"

        logger.info(f"[Hotboard weibo] got {len(results)} items, top: {first.title[:60]}")
    finally:
        await source.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_hotboard_bilibili_returns_real_data():
    """B站热搜热榜应返回真实热搜数据。"""
    from capabilities.content_search.sources.hotboard_source import HotboardSource, load_hotboard_config

    cfg = load_hotboard_config()
    if "hb-bilibili" not in cfg:
        pytest.skip("hb-bilibili not in hotboard config")

    bili_cfg = cfg["hb-bilibili"]
    source = HotboardSource(
        platform_key="hb-bilibili",
        label=bili_cfg.get("label", "B站热搜"),
        primary_url=bili_cfg.get("primary", ""),
        backup_url=bili_cfg.get("backup", ""),
    )
    try:
        results = await source.get_trending(limit=10)
        assert len(results) > 0, "Bilibili hotboard should return trending items"

        first = results[0]
        assert first.title, "Hot item should have a title"

        logger.info(f"[Hotboard bilibili] got {len(results)} items, top: {first.title[:60]}")
    finally:
        await source.close()


# ═══════════════════════════════════════════════════════════════════
# 7. SOGOU (头条搜索): 免费，国内直连
# ═══════════════════════════════════════════════════════════════════

@pytest.mark.integration
@pytest.mark.asyncio
async def test_sogou_search_returns_real_data():
    """搜狗/头条搜索应返回真实搜索结果。"""
    from capabilities.content_search.sources.sogou_source import SogouSource

    source = SogouSource()
    try:
        results = await source.search_trending("AI 大模型", limit=5)
        assert len(results) > 0, "Sogou should return results for 'AI 大模型'"

        first = results[0]
        assert first.title, "Result should have a title"
        assert first.platform == "sogou"

        logger.info(f"[Sogou] got {len(results)} results, first: {first.title[:60]}")
    finally:
        await source.close()


# ═══════════════════════════════════════════════════════════════════
# 8. XHS SPIDER: 小红书站内搜索（需登录态）
# ═══════════════════════════════════════════════════════════════════

def _xhs_storage_exists() -> bool:
    storage_path = BACKEND_DIR / "app" / "storage_states" / "xiaohongshu.json"
    return storage_path.exists()


@pytest.mark.integration
@pytest.mark.slow
@pytest.mark.asyncio
@pytest.mark.skipif(not _xhs_storage_exists(), reason="xiaohongshu.json not found (not logged in)")
async def test_xhs_spider_search_returns_real_data():
    """小红书 Spider 搜索应返回真实笔记数据（需登录态）。

    这是端到端测试：启动 Playwright 子进程 → 加载登录态 → 搜索 → 解析结果。
    超时 120 秒（Playwright 启动 + 页面加载 + 反检测等待）。
    """
    from capabilities.content_search.sources.xhs_spider_source import XhsSpiderSource

    source = XhsSpiderSource()
    assert source._check_storage_state(), "Storage state should be available"

    try:
        results = await source.search_trending("穿搭", limit=5)
        # Note: 可能因风控返回空，不算失败
        if results:
            first = results[0]
            assert first.title, "Note should have a title"
            assert first.platform == "xiaohongshu"
            assert first.content_id, "Note should have a note_id"

            logger.info(
                f"[XHS Spider] got {len(results)} notes, first: "
                f"{first.title[:60]} (likes={first.likes})"
            )
        else:
            logger.warning("[XHS Spider] search returned 0 results (possibly rate-limited)")
            pytest.skip("XHS search returned empty (rate-limited or captcha)")
    finally:
        await source.close()


# ═══════════════════════════════════════════════════════════════════
# 9. SOURCE MANAGER: 多平台综合搜索
# ═══════════════════════════════════════════════════════════════════

@pytest.mark.integration
@pytest.mark.asyncio
async def test_source_manager_init_and_list():
    """SourceManager 应成功初始化并列出可用平台。"""
    sm = await _init_all_sources()
    platforms = sm.list_platforms()

    assert len(platforms) > 0, "At least one source should be registered"
    logger.info(f"[SourceManager] registered platforms: {platforms}")

    assert "hackernews" in platforms, "HackerNews (zero-auth) should always be registered"
    assert "builtin" in platforms, "Builtin source should always be registered"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_source_manager_multi_platform_search():
    """SourceManager 多平台综合搜索应返回合并结果。"""
    sm = await _init_all_sources()

    results = await sm.search_trending("AI", limit=10, platform="hackernews")
    assert len(results) > 0, "HackerNews search should return results"

    logger.info(f"[SourceManager multi] got {len(results)} results from hackernews")


# ═══════════════════════════════════════════════════════════════════
# 10. TRENDING SEARCH SKILL: 真实调用
# ═══════════════════════════════════════════════════════════════════

@pytest.mark.integration
@pytest.mark.asyncio
async def test_trending_search_skill_real_call():
    """trending_search skill 应能真实调用并返回数据。"""
    sm = await _init_all_sources()

    from app.core.skill_registry import get_skill_class_by_name
    from unittest.mock import MagicMock

    cls = get_skill_class_by_name("trending_search")
    skill = cls()

    mock_ctx = MagicMock()
    mock_ctx.user_id = "test_user"

    result = await skill.execute({
        "keyword": "AI agent",
        "limit": 5,
        "platform": "hackernews",
        "min_interactions": 0,
        "_context": mock_ctx,
    })

    assert result.get("count", 0) > 0, "trending_search should return results"
    assert len(result.get("results", [])) > 0, "Should have result items"

    first = result["results"][0]
    assert "title" in first, "Result item should have title"
    assert "url" in first, "Result item should have url"

    logger.info(
        f"[trending_search skill] got {result['count']} results, "
        f"first: {first['title'][:60]}"
    )


# ═══════════════════════════════════════════════════════════════════
# 11. XHS SEARCH SKILL: 真实调用（需登录态）
# ═══════════════════════════════════════════════════════════════════

@pytest.mark.integration
@pytest.mark.slow
@pytest.mark.asyncio
@pytest.mark.skipif(not _xhs_storage_exists(), reason="xiaohongshu.json not found")
async def test_xhs_search_skill_real_call():
    """xhs_search skill 应能真实调用并返回小红书笔记。"""
    from app.core.skill_registry import get_skill_class_by_name
    from unittest.mock import MagicMock

    cls = get_skill_class_by_name("xhs_search")
    skill = cls()

    mock_ctx = MagicMock()
    mock_ctx.user_id = "test_user"

    result = await skill.execute({
        "keyword": "穿搭",
        "limit": 5,
        "min_likes": 0,
        "require_cover": False,
        "_context": mock_ctx,
    })

    if result.get("count", 0) > 0:
        first = result["results"][0]
        assert "note_id" in first
        assert "title" in first

        logger.info(
            f"[xhs_search skill] got {result['count']} notes, "
            f"first: {first['title'][:60]} (likes={first.get('likes', 0)})"
        )
    else:
        logger.warning("[xhs_search skill] returned 0 results (rate-limited)")
        pytest.skip("XHS search returned empty")


# ═══════════════════════════════════════════════════════════════════
# 12. TAVILY SITE SEARCH: 站内搜索
# ═══════════════════════════════════════════════════════════════════

@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not _has_env("TAVILY_API_KEY"), reason="TAVILY_API_KEY not set")
async def test_tavily_site_search_zhihu():
    """Tavily Site Search 应能搜索知乎内容。"""
    from capabilities.content_search.sources.tavily_source import TavilySource
    from capabilities.content_search.sources.tavily_site_source import TavilySiteSource

    api_key = os.environ["TAVILY_API_KEY"]
    tavily = TavilySource(api_key=api_key)
    zhihu = TavilySiteSource("zhihu", "zhihu.com", tavily)

    try:
        results = await zhihu.search_trending("AI 大模型", limit=5)
        if results:
            first = results[0]
            assert first.title
            logger.info(f"[Tavily zhihu] got {len(results)} results, first: {first.title[:60]}")
        else:
            logger.warning("[Tavily zhihu] returned 0 results (zhihu may block site: search)")
    finally:
        await tavily.close()


# ═══════════════════════════════════════════════════════════════════
# 13. AGENT → SUB-AGENT → CRAWLER: 完整真实链路
# ═══════════════════════════════════════════════════════════════════

@pytest.mark.integration
@pytest.mark.asyncio
async def test_agent_spawn_crawler_real_chain():
    """主 Agent spawn 爬虫子 Agent → 调 trending_search → 返回真实数据。"""
    from app.engine.collab.agent_manager import AgentManager
    from app.engine.collab.message_bus import InterAgentBus
    from app.engine.collab.tools import CollaborationTools
    from app.engine.collab.concurrency_pool import ConcurrencyPool
    from capabilities.content_search.sources import manager as sm_mod

    bus = InterAgentBus()
    pool = ConcurrencyPool(max_slots=3)
    manager = AgentManager(max_concurrent=3, max_depth=3)
    manager.register_root("root")
    collab_tools = CollaborationTools(
        agent_manager=manager,
        message_bus=bus,
        concurrency_pool=pool,
    )

    await sm_mod.source_manager.close_all()
    sm_mod.source_manager = sm_mod.SourceManager()
    await sm_mod.init_sources()

    from app.core.skill_registry import get_skill_class_by_name
    from unittest.mock import MagicMock

    cls = get_skill_class_by_name("trending_search")
    skill = cls()

    mock_ctx = MagicMock()
    mock_ctx.user_id = "test_user"

    params = {
        "keyword": "python",
        "limit": 5,
        "platform": "hackernews",
        "min_interactions": 0,
        "_context": mock_ctx,
    }
    collab_tools.inject_params(params, "root")

    result = await skill.execute(params)

    assert result.get("count", 0) > 0, "Sub-agent crawler should return real data"

    logger.info(
        f"[Agent→Crawler chain] got {result['count']} real results, "
        f"platform=hackernews, keyword=python"
    )

    await manager.shutdown()
    await sm_mod.source_manager.close_all()


# ═══════════════════════════════════════════════════════════════════
# 14. DATA QUALITY: 验证返回数据的完整性和一致性
# ═══════════════════════════════════════════════════════════════════

@pytest.mark.integration
@pytest.mark.asyncio
async def test_hackernews_data_quality():
    """验证 HackerNews 返回数据的字段完整性和类型正确性。"""
    from capabilities.content_search.sources.hackernews_source import HackerNewsSource

    source = HackerNewsSource()
    try:
        results = await source.search_trending("rust", limit=3)
        assert len(results) > 0

        for i, r in enumerate(results):
            assert isinstance(r.platform, str) and r.platform == "hackernews", f"[{i}] platform"
            assert isinstance(r.content_id, str) and r.content_id, f"[{i}] content_id"
            assert isinstance(r.title, str) and r.title, f"[{i}] title"
            assert isinstance(r.url, str), f"[{i}] url type"
            assert isinstance(r.likes, int) and r.likes >= 0, f"[{i}] likes"
            assert isinstance(r.comments, int) and r.comments >= 0, f"[{i}] comments"
            assert isinstance(r.tags, list), f"[{i}] tags type"

            d = r.to_dict()
            assert "platform" in d
            assert "title" in d
            assert "url" in d
            assert "likes" in d

        logger.info(f"[HN quality] {len(results)} items all passed quality checks")
    finally:
        await source.close()


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not _has_env("TAVILY_API_KEY"), reason="TAVILY_API_KEY not set")
async def test_tavily_data_quality():
    """验证 Tavily 返回数据的字段完整性和类型正确性。"""
    from capabilities.content_search.sources.tavily_source import TavilySource

    source = TavilySource(api_key=os.environ["TAVILY_API_KEY"])
    try:
        results = await source.search_trending("machine learning", limit=3)
        assert len(results) > 0

        for i, r in enumerate(results):
            assert r.platform == "tavily", f"[{i}] platform"
            assert r.title, f"[{i}] title"
            assert r.url, f"[{i}] url"
            assert isinstance(r.likes, int), f"[{i}] likes type"
            assert isinstance(r.summary, str), f"[{i}] summary type"

        logger.info(f"[Tavily quality] {len(results)} items all passed quality checks")
    finally:
        await source.close()


# ═══════════════════════════════════════════════════════════════════
# 15. CROSS-PLATFORM: 同一关键词多平台对比
# ═══════════════════════════════════════════════════════════════════

@pytest.mark.integration
@pytest.mark.asyncio
async def test_cross_platform_same_keyword():
    """同一关键词在多个平台搜索，验证各平台独立返回数据。"""
    sm = await _init_all_sources()

    keyword = "AI"
    platform_results: dict[str, int] = {}

    for platform in sm.list_platforms():
        source = sm.get_source(platform)
        if source is None:
            continue
        try:
            results = await source.search_trending(keyword, limit=3)
            platform_results[platform] = len(results)
        except Exception as e:
            logger.warning(f"[cross-platform] {platform} failed: {e}")
            platform_results[platform] = -1

    working_platforms = [p for p, c in platform_results.items() if c > 0]
    assert len(working_platforms) >= 2, (
        f"At least 2 platforms should return data for '{keyword}', "
        f"got: {platform_results}"
    )

    logger.info(f"[cross-platform] keyword='{keyword}', results: {platform_results}")