"""End-to-end chain tests: Agent → sub-Agent → crawler skill → external service.

Covers the full dispatch chain:
  ReactorSession → spawn_agent → sub-Agent ReAct loop → crawler skill → source

Test categories:
  1. Happy path: spawn sub-agent that calls xhs_search / trending_search / spider_sync
  2. Skill inheritance: sub-agent inherits all crawler skills from parent
  3. Crawler skill → source routing: XhsSpiderSource / SourceManager / spider_capability
  4. External service mode: runtime="service" → HttpTransport
  5. Boundary: no login state / no bound account / rate limit / permission denied
  6. Chain integrity: inject_params → skill execute → source call
  7. Error propagation: source error → skill error → sub-agent result → parent
  8. Concurrency: multiple sub-agents calling crawler simultaneously
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.engine.collab import (
    AgentHandle,
    AgentManager,
    AgentMessage,
    AgentStatus,
    CollaborationTools,
    ConcurrencyPool,
    InterAgentBus,
)
from app.engine.collab.message_bus import MessageType

# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
async def collab():
    """Fresh CollaborationTools instance per test."""
    manager = AgentManager(max_concurrent=3, max_depth=3)
    bus = InterAgentBus()
    pool = ConcurrencyPool(max_slots=3)
    root = manager.register_root("root")
    tools = CollaborationTools(
        agent_manager=manager,
        message_bus=bus,
        concurrency_pool=pool,
    )
    yield tools, manager, bus, pool, root
    await manager.shutdown()


def _inject(collab_tools: CollaborationTools, params: dict, caller: str = "root") -> dict:
    collab_tools.inject_params(params, caller)
    return params


def _make_db_mock(accounts_list):
    """创建 mock DB session factory，返回指定 accounts 列表。"""
    mock_scalars_result = MagicMock()
    mock_scalars_result.all.return_value = accounts_list

    mock_db_session = AsyncMock()
    mock_db_session.scalars = AsyncMock(return_value=mock_scalars_result)
    mock_db_session.__aenter__ = AsyncMock(return_value=mock_db_session)
    mock_db_session.__aexit__ = AsyncMock(return_value=None)

    mock_session_factory = MagicMock(return_value=mock_db_session)
    return mock_session_factory


class _DBMock:
    """Context manager: monkey-patch app.db.session.AsyncSessionLocal."""

    def __init__(self, accounts_list):
        self.mock_factory = _make_db_mock(accounts_list)

    def __enter__(self):
        import app.db.session as db_mod

        self._original = db_mod.AsyncSessionLocal
        db_mod.AsyncSessionLocal = self.mock_factory
        return self

    def __exit__(self, *args):
        import app.db.session as db_mod

        db_mod.AsyncSessionLocal = self._original


# ═══════════════════════════════════════════════════════════════════
# 1. SKILL REGISTRY: crawler skills are registered and discoverable
# ═══════════════════════════════════════════════════════════════════


class TestCrawlerSkillRegistration:
    """验证爬虫 Skill 在 SkillRegistry 中正确注册。"""

    def test_xhs_search_registered(self):
        from app.core.skill_registry import (
            ensure_builtin_skills_registered,
            get_skill_class_by_name,
        )

        ensure_builtin_skills_registered()
        cls = get_skill_class_by_name("xhs_search")
        assert cls is not None
        assert cls.node_type == "search"
        assert cls.name == "xhs_search"

    def test_trending_search_registered(self):
        from app.core.skill_registry import (
            ensure_builtin_skills_registered,
            get_skill_class_by_name,
        )

        ensure_builtin_skills_registered()
        cls = get_skill_class_by_name("trending_search")
        assert cls is not None
        assert cls.node_type == "search"
        assert cls.name == "trending_search"

    def test_spider_sync_registered(self):
        from app.core.skill_registry import (
            ensure_builtin_skills_registered,
            get_skill_class_by_name,
        )

        ensure_builtin_skills_registered()
        cls = get_skill_class_by_name("spider_sync")
        assert cls is not None
        assert cls.node_type == "search"
        assert cls.name == "spider_sync"

    def test_url_collect_registered(self):
        from app.core.skill_registry import (
            ensure_builtin_skills_registered,
            get_skill_class_by_name,
        )

        ensure_builtin_skills_registered()
        cls = get_skill_class_by_name("url_collect")
        assert cls is not None
        assert cls.node_type == "search"
        assert cls.name == "url_collect"

    def test_all_crawler_skills_have_xhs_search_permission(self):
        from app.core.skill_registry import (
            ensure_builtin_skills_registered,
            get_skill_class_by_name,
        )
        from app.engine.schemas import Permission

        ensure_builtin_skills_registered()

        for name in ("xhs_search", "trending_search", "spider_sync", "url_collect"):
            cls = get_skill_class_by_name(name)
            assert cls is not None, f"{name} should be registered"
            assert (
                Permission.XHS_SEARCH in cls.required_permissions
            ), f"{name} should require XHS_SEARCH permission"

    def test_crawler_skills_have_trigger_words(self):
        from app.core.skill_registry import (
            ensure_builtin_skills_registered,
            get_skill_class_by_name,
        )

        ensure_builtin_skills_registered()

        for name in ("spider_sync", "url_collect"):
            cls = get_skill_class_by_name(name)
            assert cls is not None
            assert len(cls.trigger_words) > 0, f"{name} should have trigger_words for LLM routing"

    def test_crawler_skills_have_prompt_guidance(self):
        from app.core.skill_registry import (
            ensure_builtin_skills_registered,
            get_skill_class_by_name,
        )

        ensure_builtin_skills_registered()

        for name in ("spider_sync", "url_collect"):
            cls = get_skill_class_by_name(name)
            assert cls is not None
            assert len(cls.prompt_guidance) > 0, f"{name} should have prompt_guidance"

    def test_crawler_skills_have_description_for_llm(self):
        from app.core.skill_registry import (
            ensure_builtin_skills_registered,
            get_skill_class_by_name,
        )

        ensure_builtin_skills_registered()

        for name in ("xhs_search", "trending_search", "spider_sync", "url_collect"):
            cls = get_skill_class_by_name(name)
            assert cls is not None
            assert len(cls.description) > 0, f"{name} should have description for LLM routing"


# ═══════════════════════════════════════════════════════════════════
# 2. SKILL INHERITANCE: sub-agent inherits crawler skills
# ═══════════════════════════════════════════════════════════════════


class TestSubAgentSkillInheritance:
    """验证子 Agent 继承父 Agent 的全量爬虫 skill。"""

    def test_sub_agent_executor_inherits_parent_skills(self):
        """_ensure_collab 创建的 _sub_agent_executor 应继承父 session 的 skills。"""
        from app.core.skill_registry import (
            ensure_builtin_skills_registered,
            get_skill_class_by_name,
        )
        from app.engine.reactor.session import ReactorSession

        ensure_builtin_skills_registered()

        crawler_skill_names = ["xhs_search", "trending_search", "spider_sync", "url_collect"]
        all_skills = []
        for name in crawler_skill_names:
            cls = get_skill_class_by_name(name)
            if cls:
                all_skills.append(cls())

        session = ReactorSession(skills=all_skills, workflow_id="test_inherit")
        collab = session._ensure_collab()
        assert collab is not None

        executor = collab.agent_manager._agent_executor
        assert executor is not None, "sub-agent executor should be set"

    def test_spawn_agent_description_mentions_crawler_context(self):
        """spawn_agent 的 description 应提及 Context Gathering 信号。"""
        from app.core.skill_registry import (
            ensure_builtin_skills_registered,
            get_skill_class_by_name,
        )

        ensure_builtin_skills_registered()

        cls = get_skill_class_by_name("spawn_agent")
        skill = cls()
        assert (
            "Context Gathering" in skill.description
            or "Information" in skill.description
            or "搜索" in skill.description
        )

    def test_reactor_system_prompt_includes_crawler_skills(self):
        """ReactorSession 的 system_prompt 应包含爬虫 skill 描述。"""
        from app.engine.reactor.session import ReactorSession, collect_reactor_skills

        skills = collect_reactor_skills()
        session = ReactorSession(skills=skills)

        for name in ("xhs_search", "trending_search", "spider_sync", "url_collect"):
            assert (
                name in session.system_prompt
            ), f"{name} should appear in system_prompt for LLM to discover"


# ═══════════════════════════════════════════════════════════════════
# 3. XHS SEARCH: skill → XhsSpiderSource → XhsSearchSpider
# ═══════════════════════════════════════════════════════════════════


class TestXhsSearchChain:
    """xhs_search skill → XhsSpiderSource → XhsSearchSpider 链路。"""

    @pytest.mark.asyncio
    async def test_xhs_search_calls_spider_source(self):
        """xhs_search execute 应调用 XhsSpiderSource.search_trending。"""
        from app.core.skill_registry import get_skill_class_by_name

        cls = get_skill_class_by_name("xhs_search")
        skill = cls()

        fake_results = [
            MagicMock(
                content_id="note_1",
                title="Test Note",
                summary="Summary",
                likes=500,
                comments=30,
                url="https://xhs.com/note_1",
                cover_img="https://img.xhs.com/1.jpg",
                author="test_author",
                collects=100,
                shares=10,
                tags=["穿搭"],
            ),
        ]

        with patch.object(skill, "_get_xhs_client") as mock_client:
            mock_source = AsyncMock()
            mock_source.search_trending = AsyncMock(return_value=fake_results)
            mock_client.return_value = mock_source

            result = await skill.execute(
                {
                    "keyword": "穿搭",
                    "limit": 10,
                    "min_likes": 0,
                    "require_cover": False,
                }
            )

            mock_source.search_trending.assert_awaited_once()
            assert result["count"] >= 1
            assert result["results"][0]["note_id"] == "note_1"

    @pytest.mark.asyncio
    async def test_xhs_search_filters_low_likes(self):
        """xhs_search 应过滤低于 min_likes 的结果。"""
        from app.core.skill_registry import get_skill_class_by_name

        cls = get_skill_class_by_name("xhs_search")
        skill = cls()

        fake_results = [
            MagicMock(
                content_id="hot",
                title="Hot",
                summary="",
                likes=500,
                comments=10,
                url="",
                cover_img="",
                author="",
                collects=0,
                shares=0,
                tags=[],
            ),
            MagicMock(
                content_id="cold",
                title="Cold",
                summary="",
                likes=5,
                comments=0,
                url="",
                cover_img="",
                author="",
                collects=0,
                shares=0,
                tags=[],
            ),
        ]

        with patch.object(skill, "_get_xhs_client") as mock_client:
            mock_source = AsyncMock()
            mock_source.search_trending = AsyncMock(return_value=fake_results)
            mock_client.return_value = mock_source

            result = await skill.execute(
                {
                    "keyword": "test",
                    "limit": 10,
                    "min_likes": 100,
                    "require_cover": False,
                }
            )

            ids = [r["note_id"] for r in result["results"]]
            assert "hot" in ids
            assert "cold" not in ids

    @pytest.mark.asyncio
    async def test_xhs_search_filters_invalid_items(self):
        """xhs_search 应过滤无 note_id 或无标题的无效项。"""
        from app.core.skill_registry import get_skill_class_by_name

        cls = get_skill_class_by_name("xhs_search")
        skill = cls()

        fake_results = [
            MagicMock(
                content_id="",
                title="No ID",
                summary="",
                likes=100,
                comments=0,
                url="",
                cover_img="",
                author="",
                collects=0,
                shares=0,
                tags=[],
            ),
            MagicMock(
                content_id="no_title",
                title="",
                summary="",
                likes=100,
                comments=0,
                url="",
                cover_img="",
                author="",
                collects=0,
                shares=0,
                tags=[],
            ),
            MagicMock(
                content_id="valid",
                title="Valid",
                summary="",
                likes=100,
                comments=0,
                url="",
                cover_img="",
                author="",
                collects=0,
                shares=0,
                tags=[],
            ),
        ]

        with patch.object(skill, "_get_xhs_client") as mock_client:
            mock_source = AsyncMock()
            mock_source.search_trending = AsyncMock(return_value=fake_results)
            mock_client.return_value = mock_source

            result = await skill.execute(
                {
                    "keyword": "test",
                    "limit": 10,
                    "min_likes": 0,
                    "require_cover": False,
                }
            )

            ids = [r["note_id"] for r in result["results"]]
            assert "valid" in ids
            assert "no_title" not in ids

    @pytest.mark.asyncio
    async def test_xhs_search_no_login_state_returns_empty(self):
        """无登录态时 XhsSpiderSource.search_trending 应返回空列表。"""
        from capabilities.content_search.sources.xhs_spider_source import XhsSpiderSource

        with patch.object(XhsSpiderSource, "_check_storage_state", return_value=False):
            source = XhsSpiderSource()
            source._storage_available = False
            results = await source.search_trending("穿搭", limit=10)
            assert results == []

    @pytest.mark.asyncio
    async def test_xhs_search_spider_source_normalizes_results(self):
        """XhsSpiderSource._normalize 应正确归一化 raw dict。"""
        from capabilities.content_search.sources.xhs_spider_source import XhsSpiderSource

        source = XhsSpiderSource()
        raw = {
            "note_id": "abc123",
            "title": "Test Title",
            "content_text": "Some content text here",
            "cover_img_url": "https://img.xhs.com/cover.jpg",
            "likes": 1000,
            "comments": 50,
            "shares": 20,
            "collects": 200,
            "author": "fashion_girl",
            "tags": ["穿搭", "日常"],
        }

        content = source._normalize(raw)
        assert content is not None
        assert content.content_id == "abc123"
        assert content.title == "Test Title"
        assert content.likes == 1000
        assert content.platform == "xiaohongshu"
        assert content.tags == ["穿搭", "日常"]

    @pytest.mark.asyncio
    async def test_xhs_search_spider_source_normalize_drops_invalid(self):
        """_normalize 对无 note_id 的 raw 应返回 None。"""
        from capabilities.content_search.sources.xhs_spider_source import XhsSpiderSource

        source = XhsSpiderSource()
        assert source._normalize({"title": "no id"}) is None
        assert source._normalize({}) is None


# ═══════════════════════════════════════════════════════════════════
# 4. TRENDING SEARCH: skill → SourceManager → multi-platform
# ═══════════════════════════════════════════════════════════════════


class TestTrendingSearchChain:
    """trending_search skill → SourceManager → 多平台搜索链路。"""

    @pytest.mark.asyncio
    async def test_trending_search_single_platform(self):
        """trending_search 指定 platform 时应只搜该平台。"""
        from app.core.skill_registry import get_skill_class_by_name
        from capabilities.content_search.sources.base import TrendingContent

        cls = get_skill_class_by_name("trending_search")
        skill = cls()

        fake_content = TrendingContent(
            platform="reddit",
            content_id="r1",
            title="AI Trend",
            summary="Summary",
            content="",
            author="user1",
            url="https://reddit.com/r1",
            likes=100,
            comments=20,
        )

        with patch.object(skill, "_get_source") as mock_get:
            mock_source = AsyncMock()
            mock_source.name = "reddit"
            mock_source.search_trending = AsyncMock(return_value=[fake_content])
            mock_get.return_value = mock_source

            result = await skill.execute(
                {
                    "keyword": "AI",
                    "limit": 10,
                    "platform": "reddit",
                    "min_interactions": 0,
                }
            )

            assert result["count"] >= 1
            assert result["platform"] == "reddit"

    @pytest.mark.asyncio
    async def test_trending_search_multi_platform(self):
        """trending_search 不指定 platform 时应搜所有平台。"""
        from app.core.skill_registry import get_skill_class_by_name
        from capabilities.content_search.sources.base import TrendingContent

        cls = get_skill_class_by_name("trending_search")
        skill = cls()

        reddit_content = TrendingContent(
            platform="reddit",
            content_id="r1",
            title="Reddit AI",
            summary="",
            content="",
            author="",
            url="",
            likes=50,
            comments=10,
        )
        hn_content = TrendingContent(
            platform="hackernews",
            content_id="h1",
            title="HN AI",
            summary="",
            content="",
            author="",
            url="",
            likes=200,
            comments=30,
        )

        with patch.object(skill, "_search_all_platforms") as mock_multi:
            mock_multi.return_value = ([reddit_content, hn_content], ["reddit", "hackernews"])

            result = await skill.execute(
                {
                    "keyword": "AI",
                    "limit": 10,
                    "min_interactions": 0,
                }
            )

            assert result["count"] == 2
            assert "reddit" in result["platform"]
            assert "hackernews" in result["platform"]

    @pytest.mark.asyncio
    async def test_trending_search_filters_low_interactions(self):
        """trending_search 应过滤互动量低于 min_interactions 的结果。"""
        from app.core.skill_registry import get_skill_class_by_name
        from capabilities.content_search.sources.base import TrendingContent

        cls = get_skill_class_by_name("trending_search")
        skill = cls()

        low_content = TrendingContent(
            platform="reddit",
            content_id="low",
            title="Low",
            summary="",
            content="",
            author="",
            url="",
            likes=2,
            comments=0,
        )
        high_content = TrendingContent(
            platform="reddit",
            content_id="high",
            title="High",
            summary="",
            content="",
            author="",
            url="",
            likes=100,
            comments=50,
        )

        with patch.object(skill, "_search_all_platforms") as mock_multi:
            mock_multi.return_value = ([low_content, high_content], ["reddit"])

            result = await skill.execute(
                {
                    "keyword": "AI",
                    "limit": 10,
                    "min_interactions": 50,
                }
            )

            ids = [r["content_id"] for r in result["results"]]
            assert "high" in ids
            assert "low" not in ids

    @pytest.mark.asyncio
    async def test_trending_search_unavailable_platform_returns_empty(self):
        """trending_search 对不可用平台应返回空结果而非抛异常。"""
        from app.core.skill_registry import get_skill_class_by_name

        cls = get_skill_class_by_name("trending_search")
        skill = cls()

        with patch.object(skill, "_get_source", return_value=None):
            with patch.object(skill, "_list_platforms", return_value=["reddit"]):
                result = await skill.execute(
                    {
                        "keyword": "AI",
                        "limit": 10,
                        "platform": "nonexistent",
                        "min_interactions": 0,
                    }
                )

                assert result["count"] == 0


# ═══════════════════════════════════════════════════════════════════
# 5. SPIDER SYNC: skill → spider_capability → runner / HTTP
# ═══════════════════════════════════════════════════════════════════


class TestSpiderSyncChain:
    """spider_sync skill → spider_capability → runner / HTTP 链路。"""

    @pytest.mark.asyncio
    async def test_spider_sync_no_user_id_returns_error(self):
        """无 user_id 时 spider_sync 应返回 error。"""
        from app.core.skill_registry import get_skill_class_by_name

        cls = get_skill_class_by_name("spider_sync")
        skill = cls()

        result = await skill.execute(
            {
                "platform": "xiaohongshu",
                "_context": None,
            }
        )
        assert result.get("ok") is False
        assert "用户身份" in result.get("error", "") or "登录" in result.get("error", "")

    @pytest.mark.asyncio
    async def test_spider_sync_unsupported_platform_returns_error(self):
        """不支持的平台应返回 error。"""
        from app.core.skill_registry import get_skill_class_by_name

        cls = get_skill_class_by_name("spider_sync")
        skill = cls()

        mock_ctx = MagicMock()
        mock_ctx.user_id = "user1"

        result = await skill.execute(
            {
                "platform": "tiktok",
                "_context": mock_ctx,
            }
        )
        assert result.get("ok") is False
        assert "不支持" in result.get("error", "")

    @pytest.mark.asyncio
    async def test_spider_sync_no_bound_account_returns_error(self):
        """无绑定账号时应返回 error。"""
        from app.core.skill_registry import get_skill_class_by_name

        cls = get_skill_class_by_name("spider_sync")
        skill = cls()

        mock_ctx = MagicMock()
        mock_ctx.user_id = "user1"

        with _DBMock(accounts_list=[]):
            result = await skill.execute(
                {
                    "platform": "xiaohongshu",
                    "_context": mock_ctx,
                }
            )
            assert result.get("ok") is False
            assert "绑定" in result.get("error", "")

    @pytest.mark.asyncio
    async def test_spider_capability_inprocess_calls_runner(self):
        """spider_capability.start_sync 在 inprocess 模式下调 runner.start_sync。"""
        with patch(
            "capabilities.scrapling.spiders.runner.start_sync", new_callable=AsyncMock
        ) as mock_runner:
            from app.services.spider_capability import start_sync

            await start_sync("acc1", "user1", "xiaohongshu", force=False)
            mock_runner.assert_awaited_once_with("acc1", "user1", "xiaohongshu", force=False)

    @pytest.mark.asyncio
    async def test_spider_capability_remote_calls_http(self):
        """spider_capability.start_sync 在 service 模式下调 HTTP transport。"""
        with patch(
            "app.services.spider_capability._service_url", return_value="http://spider-svc:8001"
        ):
            mock_transport = AsyncMock()
            mock_transport.call = AsyncMock(return_value={"ok": True})
            mock_transport.aclose = AsyncMock()

            with patch("app.engine.protocol.transport.HttpTransport", return_value=mock_transport):
                from app.services.spider_capability import start_sync

                await start_sync("acc1", "user1", "xiaohongshu", force=True)
                mock_transport.call.assert_awaited_once()
                call_args = mock_transport.call.call_args
                assert call_args[0][0] == "spider_sync"
                assert call_args[0][1]["account_id"] == "acc1"
                assert call_args[0][1]["platform"] == "xiaohongshu"
                assert call_args[0][1]["force"] is True


# ═══════════════════════════════════════════════════════════════════
# 6. CAPABILITY LOADER: runtime routing (inprocess / service)
# ═══════════════════════════════════════════════════════════════════


class TestCapabilityRuntimeRouting:
    """capability_loader 的 runtime 路由逻辑。"""

    def test_default_runtime_is_inprocess(self):
        """未声明 runtime 时默认为 inprocess。"""
        from app.core.capability_loader import get_capability_runtime

        runtime = get_capability_runtime("nonexistent_cap")
        assert runtime == "inprocess"

    def test_service_url_empty_for_inprocess(self):
        """inprocess 模式下 service_url 应为空。"""
        from app.core.capability_loader import get_capability_service_url

        with patch(
            "app.core.capability_loader._read_toml_data",
            return_value={
                "scrapling": {"enabled": True, "runtime": "inprocess"},
            },
        ):
            url = get_capability_service_url("scrapling")
            assert url == ""

    def test_service_url_returns_configured_url(self):
        """service 模式下应返回 Settings 中配置的 URL。"""
        from app.core.capability_loader import get_capability_service_url

        mock_settings = MagicMock()
        mock_settings.spider_service_url = "http://spider-svc:8001"

        with (
            patch(
                "app.core.capability_loader._read_toml_data",
                return_value={
                    "spider": {"enabled": True, "runtime": "service"},
                },
            ),
            patch("app.config.get_settings", return_value=mock_settings),
        ):
            url = get_capability_service_url("spider")
            assert url == "http://spider-svc:8001"

    def test_service_mode_with_empty_url_falls_back(self):
        """service 模式但 URL 为空时应回退（返回空字符串）。"""
        from app.core.capability_loader import get_capability_service_url

        mock_settings = MagicMock()
        mock_settings.spider_service_url = ""

        with (
            patch(
                "app.core.capability_loader._read_toml_data",
                return_value={
                    "scrapling": {"enabled": True, "runtime": "service"},
                },
            ),
            patch("app.config.get_settings", return_value=mock_settings),
        ):
            url = get_capability_service_url("scrapling")
            assert url == ""

    def test_unknown_runtime_falls_back_to_inprocess(self):
        """未知 runtime 值应回退到 inprocess。"""
        from app.core.capability_loader import get_capability_runtime

        with patch(
            "app.core.capability_loader._read_toml_data",
            return_value={
                "scrapling": {"enabled": True, "runtime": "kubernetes"},
            },
        ):
            runtime = get_capability_runtime("scrapling")
            assert runtime == "inprocess"


# ═══════════════════════════════════════════════════════════════════
# 7. SOURCE MANAGER: multi-platform source registration
# ═══════════════════════════════════════════════════════════════════


class TestSourceManagerChain:
    """SourceManager 多平台源注册和分发。"""

    def test_register_and_get_source(self):
        from capabilities.content_search.sources.base import ContentSource
        from capabilities.content_search.sources.manager import SourceManager

        mgr = SourceManager()
        mock_source = MagicMock(spec=ContentSource)
        mock_source.name = "test_platform"
        mgr.register(mock_source)

        assert mgr.get_source("test_platform") is mock_source
        assert mgr.is_available("test_platform")
        assert "test_platform" in mgr.list_platforms()

    def test_get_nonexistent_source_returns_none(self):
        from capabilities.content_search.sources.manager import SourceManager

        mgr = SourceManager()
        assert mgr.get_source("ghost") is None
        assert not mgr.is_available("ghost")

    @pytest.mark.asyncio
    async def test_search_trending_delegates_to_source(self):
        from capabilities.content_search.sources.base import ContentSource, TrendingContent
        from capabilities.content_search.sources.manager import SourceManager

        mgr = SourceManager()
        mock_source = AsyncMock(spec=ContentSource)
        mock_source.name = "mock_platform"
        fake_content = TrendingContent(
            platform="mock_platform",
            content_id="1",
            title="T",
            summary="",
            content="",
            author="",
            url="",
            likes=10,
            comments=5,
        )
        mock_source.search_trending = AsyncMock(return_value=[fake_content])
        mgr.register(mock_source)
        mgr.set_default("mock_platform")

        results = await mgr.search_trending("test", limit=10)
        assert len(results) == 1
        assert results[0].content_id == "1"

    @pytest.mark.asyncio
    async def test_close_all_cleans_up(self):
        from capabilities.content_search.sources.base import ContentSource
        from capabilities.content_search.sources.manager import SourceManager

        mgr = SourceManager()
        mock_source = AsyncMock(spec=ContentSource)
        mock_source.name = "p1"
        mock_source.close = AsyncMock()
        mgr.register(mock_source)

        await mgr.close_all()
        mock_source.close.assert_awaited_once()
        assert mgr.list_platforms() == []


# ═══════════════════════════════════════════════════════════════════
# 8. SPAWN → CRAWLER: full chain via collab infrastructure
# ═══════════════════════════════════════════════════════════════════


class TestSpawnToCrawlerChain:
    """spawn sub-agent → sub-agent 调用爬虫 skill 的完整链路。"""

    @pytest.mark.asyncio
    async def test_spawn_with_crawler_task_description(self, collab):
        """spawn 一个爬虫子任务应成功，task_description 通过 bus 传递。"""
        tools, mgr, bus, pool, root = collab
        from app.core.skill_registry import get_skill_class_by_name

        spawn_cls = get_skill_class_by_name("spawn_agent")
        result = await spawn_cls().execute(
            _inject(
                tools,
                {
                    "task_name": "xhs_research",
                    "task_description": "搜索小红书'穿搭'关键词，返回前20条热门笔记",
                },
            )
        )

        assert result.get("success") is True
        agent_id = result["agent_id"]

        assert bus.pending_count(agent_id) >= 1
        msg = await bus.receive(agent_id)
        assert msg is not None
        assert "穿搭" in msg.content
        assert msg.message_type == MessageType.TASK

    @pytest.mark.asyncio
    async def test_spawn_crawler_with_role(self, collab):
        """spawn 爬虫子任务带 role 参数应正确设置。"""
        tools, mgr, bus, pool, root = collab
        from app.core.skill_registry import get_skill_class_by_name

        spawn_cls = get_skill_class_by_name("spawn_agent")
        result = await spawn_cls().execute(
            _inject(
                tools,
                {
                    "task_name": "researcher",
                    "task_description": "Research trending topics",
                    "role": "researcher",
                },
            )
        )

        assert result.get("success") is True
        assert result.get("role") == "researcher"

    @pytest.mark.asyncio
    async def test_spawn_crawler_with_model_override(self, collab):
        """spawn 爬虫子任务带 model override 应正确设置。"""
        tools, mgr, bus, pool, root = collab
        from app.core.skill_registry import get_skill_class_by_name

        spawn_cls = get_skill_class_by_name("spawn_agent")
        result = await spawn_cls().execute(
            _inject(
                tools,
                {
                    "task_name": "deep_research",
                    "task_description": "Deep research with reasoning model",
                    "model": "deepseek-r1",
                },
            )
        )

        assert result.get("success") is True
        assert result.get("model") == "deepseek-r1"

    @pytest.mark.asyncio
    async def test_spawn_crawler_fork_mode(self, collab):
        """spawn 爬虫子任务带 fork_mode=fork 应继承父上下文。"""
        tools, mgr, bus, pool, root = collab
        from app.core.skill_registry import get_skill_class_by_name

        spawn_cls = get_skill_class_by_name("spawn_agent")
        result = await spawn_cls().execute(
            _inject(
                tools,
                {
                    "task_name": "context_aware_search",
                    "task_description": "Search based on what we discussed",
                    "fork_mode": "fork",
                },
            )
        )

        assert result.get("success") is True
        assert result.get("fork_mode") == "fork"

    @pytest.mark.asyncio
    async def test_spawn_and_send_crawler_instruction(self, collab):
        """spawn 子 agent 后通过 send_message 发送爬取指令。"""
        tools, mgr, bus, pool, root = collab
        from app.core.skill_registry import get_skill_class_by_name

        spawn_cls = get_skill_class_by_name("spawn_agent")
        spawn_result = await spawn_cls().execute(
            _inject(
                tools,
                {
                    "task_name": "crawler",
                    "task_description": "Ready to crawl",
                },
            )
        )
        agent_id = spawn_result["agent_id"]

        send_cls = get_skill_class_by_name("send_message")
        send_result = await send_cls().execute(
            _inject(
                tools,
                {
                    "target_agent_id": agent_id,
                    "content": "请搜索小红书'咖啡'关键词，limit=10",
                },
            )
        )

        assert send_result.get("success") is True
        assert send_result["delivered"] is True

    @pytest.mark.asyncio
    async def test_multiple_crawler_sub_agents_parallel(self):
        """多个爬虫子 agent 并行 spawn 应各自独立。"""
        manager = AgentManager(max_concurrent=5, max_depth=3)
        bus = InterAgentBus()
        pool = ConcurrencyPool(max_slots=5)
        root = manager.register_root("root")
        tools = CollaborationTools(
            agent_manager=manager,
            message_bus=bus,
            concurrency_pool=pool,
        )

        from app.core.skill_registry import get_skill_class_by_name

        spawn_cls = get_skill_class_by_name("spawn_agent")

        tasks = [
            ("xhs_crawler", "搜索小红书穿搭趋势"),
            ("reddit_crawler", "搜索Reddit AI热点"),
            ("hn_crawler", "搜索HackerNews技术文章"),
        ]

        agent_ids = []
        for name, desc in tasks:
            result = await spawn_cls().execute(
                _inject(
                    tools,
                    {
                        "task_name": name,
                        "task_description": desc,
                    },
                )
            )
            assert result.get("success") is True
            agent_ids.append(result["agent_id"])

        assert len(set(agent_ids)) == 3, "each spawn should produce unique agent_id"

        list_cls = get_skill_class_by_name("list_agents")
        list_result = await list_cls().execute(_inject(tools, {}))
        assert list_result["total"] == 4  # root + 3 sub-agents

        await manager.shutdown()


# ═══════════════════════════════════════════════════════════════════
# 9. BOUNDARY: crawler-specific edge cases
# ═══════════════════════════════════════════════════════════════════


class TestCrawlerBoundaryConditions:
    """爬虫链路的边界条件。"""

    @pytest.mark.asyncio
    async def test_xhs_search_empty_keyword(self):
        """xhs_search 空关键词应正常处理（由 source 层决定行为）。"""
        from app.core.skill_registry import get_skill_class_by_name

        cls = get_skill_class_by_name("xhs_search")
        skill = cls()

        with patch.object(skill, "_get_xhs_client") as mock_client:
            mock_source = AsyncMock()
            mock_source.search_trending = AsyncMock(return_value=[])
            mock_client.return_value = mock_source

            result = await skill.execute(
                {
                    "keyword": "",
                    "limit": 10,
                    "min_likes": 0,
                    "require_cover": False,
                }
            )
            assert result["count"] == 0

    @pytest.mark.asyncio
    async def test_xhs_search_limit_capped_at_100(self):
        """xhs_search limit 超过 100 应被 input_schema 约束。"""
        from app.core.skill_registry import get_skill_class_by_name

        cls = get_skill_class_by_name("xhs_search")
        skill = cls()

        with pytest.raises(Exception):
            await skill.execute(
                {
                    "keyword": "test",
                    "limit": 999,
                    "min_likes": 0,
                }
            )

    @pytest.mark.asyncio
    async def test_trending_search_zero_results_triggers_fallback(self):
        """trending_search 零结果时应触发 fallback（热门榜兜底）。"""
        from app.core.skill_registry import get_skill_class_by_name

        cls = get_skill_class_by_name("trending_search")
        skill = cls()

        fallback_dict = {
            "content_id": "fb1",
            "title": "Fallback Trend",
            "summary": "",
            "content": "",
            "author": "",
            "url": "",
            "likes": 500,
            "comments": 100,
            "shares": 0,
            "platform": "reddit",
        }

        with (
            patch.object(skill, "_search_all_platforms", new_callable=AsyncMock) as mock_multi,
            patch.object(skill, "_fallback_to_trending", new_callable=AsyncMock) as mock_fallback,
        ):
            mock_multi.return_value = ([], [])
            mock_fallback.return_value = ([fallback_dict], "reddit")

            result = await skill.execute(
                {
                    "keyword": "obscure_topic_xyz",
                    "limit": 10,
                    "min_interactions": 0,
                }
            )

            mock_fallback.assert_awaited_once()
            assert result["count"] >= 1

    @pytest.mark.asyncio
    async def test_trending_search_disable_fallback_skips_fallback(self):
        """disable_fallback=True 时零结果不触发 fallback。"""
        from app.core.skill_registry import get_skill_class_by_name

        cls = get_skill_class_by_name("trending_search")
        skill = cls()

        with patch.object(skill, "_search_all_platforms") as mock_multi:
            mock_multi.return_value = ([], [])

            result = await skill.execute(
                {
                    "keyword": "obscure_topic_xyz",
                    "limit": 10,
                    "min_interactions": 0,
                    "disable_fallback": True,
                }
            )

            assert result["count"] == 0
            assert result["filter_stats"]["fallback_used"] is False

    @pytest.mark.asyncio
    async def test_spider_sync_frequency_protection(self):
        """距上次同步不足1小时时应拒绝（除非 force=true）。"""
        from datetime import UTC, datetime, timedelta

        from app.core.skill_registry import get_skill_class_by_name

        cls = get_skill_class_by_name("spider_sync")
        skill = cls()

        mock_ctx = MagicMock()
        mock_ctx.user_id = "user1"

        mock_account = MagicMock()
        mock_account.platform = "xiaohongshu"
        mock_account.sync_status = "idle"
        mock_account.last_synced_at = datetime.now(UTC) - timedelta(minutes=30)

        with _DBMock(accounts_list=[mock_account]):
            result = await skill.execute(
                {
                    "platform": "xiaohongshu",
                    "_context": mock_ctx,
                    "force": False,
                }
            )
            assert result.get("ok") is False
            assert "频繁" in result.get("error", "") or "分钟前" in result.get("error", "")

    @pytest.mark.asyncio
    async def test_spider_sync_force_bypasses_frequency(self):
        """force=true 时应绕过频率保护。"""
        from datetime import UTC, datetime, timedelta

        from app.core.skill_registry import get_skill_class_by_name

        cls = get_skill_class_by_name("spider_sync")
        skill = cls()

        mock_ctx = MagicMock()
        mock_ctx.user_id = "user1"

        mock_account = MagicMock()
        mock_account.id = "acc1"
        mock_account.platform = "xiaohongshu"
        mock_account.sync_status = "idle"
        mock_account.last_synced_at = datetime.now(UTC) - timedelta(minutes=30)

        with (
            _DBMock(accounts_list=[mock_account]),
            patch(
                "capabilities.scrapling.spiders.db_sink.update_sync_status", new_callable=AsyncMock
            ),
            patch("app.services.spider_capability.start_sync", new_callable=AsyncMock),
        ):

            result = await skill.execute(
                {
                    "platform": "xiaohongshu",
                    "_context": mock_ctx,
                    "force": True,
                }
            )
            assert result.get("ok") is True
            assert "小红书" in result.get("message", "")

    @pytest.mark.asyncio
    async def test_spider_sync_already_running_skips(self):
        """正在同步中的平台应跳过。"""
        from app.core.skill_registry import get_skill_class_by_name

        cls = get_skill_class_by_name("spider_sync")
        skill = cls()

        mock_ctx = MagicMock()
        mock_ctx.user_id = "user1"

        mock_account = MagicMock()
        mock_account.platform = "xiaohongshu"
        mock_account.sync_status = "running"
        mock_account.last_synced_at = None

        with _DBMock(accounts_list=[mock_account]):
            result = await skill.execute(
                {
                    "platform": "xiaohongshu",
                    "_context": mock_ctx,
                }
            )
            assert result.get("ok") is False
            assert "同步中" in result.get("error", "") or "正在" in result.get("error", "")


# ═══════════════════════════════════════════════════════════════════
# 10. ERROR PROPAGATION: source error → skill → sub-agent → parent
# ═══════════════════════════════════════════════════════════════════


class TestErrorPropagation:
    """错误从 source 层传播到 parent agent 的链路。"""

    @pytest.mark.asyncio
    async def test_xhs_search_source_error_returns_empty(self):
        """XhsSpiderSource 搜索异常时 xhs_search 应返回空结果而非抛异常。"""
        from app.core.skill_registry import get_skill_class_by_name

        cls = get_skill_class_by_name("xhs_search")
        skill = cls()

        with patch.object(skill, "_get_xhs_client") as mock_client:
            mock_source = AsyncMock()
            mock_source.search_trending = AsyncMock(side_effect=RuntimeError("Spider crashed"))
            mock_client.return_value = mock_source

            with pytest.raises(RuntimeError, match="Spider crashed"):
                await skill.execute(
                    {
                        "keyword": "test",
                        "limit": 10,
                        "min_likes": 0,
                    }
                )

    @pytest.mark.asyncio
    async def test_xhs_search_no_login_state_raises(self):
        """无登录态时 _get_xhs_client 应抛 RuntimeError。"""
        from app.core.skill_registry import get_skill_class_by_name

        cls = get_skill_class_by_name("xhs_search")
        skill = cls()

        with patch(
            "capabilities.content_search.sources.xhs_spider_source.XhsSpiderSource"
        ) as mock_cls:
            mock_instance = MagicMock()
            mock_instance._check_storage_state.return_value = False
            mock_cls.return_value = mock_instance

            with pytest.raises(RuntimeError, match="登录态"):
                await skill.execute(
                    {
                        "keyword": "test",
                        "limit": 10,
                        "min_likes": 0,
                    }
                )

    @pytest.mark.asyncio
    async def test_sub_agent_error_propagates_via_bus(self, collab):
        """子 agent 执行失败时，错误应通过 bus 传回 parent。"""
        tools, mgr, bus, pool, root = collab

        handle = await mgr.spawn("failing_crawler", parent_id="root", task_description="Will fail")
        bus.register(handle.agent_id)

        handle.mark_error("Spider login expired")

        from app.core.skill_registry import get_skill_class_by_name

        wait_cls = get_skill_class_by_name("wait_agent")
        wait_skill = wait_cls()

        result = await wait_skill.execute(
            _inject(
                tools,
                {
                    "agent_id": handle.agent_id,
                    "timeout_seconds": 5,
                },
            )
        )

        assert result["status"] == "error"
        assert result.get("error") is not None

    @pytest.mark.asyncio
    async def test_trending_search_source_runtime_error_returns_empty(self):
        """trending_search source 抛 'local client not connected' RuntimeError 时应返回空结果（不 raise）。"""
        from app.core.skill_registry import get_skill_class_by_name

        cls = get_skill_class_by_name("trending_search")
        skill = cls()

        with patch.object(skill, "_get_source") as mock_get:
            mock_source = AsyncMock()
            mock_source.name = "broken_platform"
            mock_source.search_trending = AsyncMock(
                side_effect=RuntimeError("local client not connected")
            )
            mock_get.return_value = mock_source

            result = await skill.execute(
                {
                    "keyword": "test",
                    "limit": 10,
                    "platform": "broken_platform",
                    "min_interactions": 0,
                }
            )

            assert result["count"] == 0
            assert "broken_platform" in result["platform"]

    @pytest.mark.asyncio
    async def test_trending_search_source_unexpected_error_raises(self):
        """trending_search source 抛非 'local client' RuntimeError 时应 raise。"""
        from app.core.skill_registry import get_skill_class_by_name

        cls = get_skill_class_by_name("trending_search")
        skill = cls()

        with patch.object(skill, "_get_source") as mock_get:
            mock_source = AsyncMock()
            mock_source.name = "broken_platform"
            mock_source.search_trending = AsyncMock(
                side_effect=RuntimeError("database connection lost")
            )
            mock_get.return_value = mock_source

            with pytest.raises(RuntimeError, match="database connection lost"):
                await skill.execute(
                    {
                        "keyword": "test",
                        "limit": 10,
                        "platform": "broken_platform",
                        "min_interactions": 0,
                    }
                )


# ═══════════════════════════════════════════════════════════════════
# 11. CONCURRENT CRAWLER: multiple sub-agents calling crawler
# ═══════════════════════════════════════════════════════════════════


class TestConcurrentCrawlerAccess:
    """多个子 agent 并行调用爬虫的并发场景。"""

    @pytest.mark.asyncio
    async def test_concurrent_xhs_searches_dont_interfere(self):
        """多个并发的 xhs_search 调用应互不干扰。"""
        from app.core.skill_registry import get_skill_class_by_name
        from capabilities.content_search.sources.base import TrendingContent

        cls = get_skill_class_by_name("xhs_search")
        results_all = []

        async def _search(keyword: str):
            skill = cls()
            with patch.object(skill, "_get_xhs_client") as mock_client:
                mock_source = AsyncMock()
                fake = MagicMock(
                    content_id=f"note_{keyword}",
                    title=f"Title {keyword}",
                    summary="",
                    likes=100,
                    comments=10,
                    url="",
                    cover_img="",
                    author="",
                    collects=0,
                    shares=0,
                    tags=[],
                )
                mock_source.search_trending = AsyncMock(return_value=[fake])
                mock_client.return_value = mock_source

                result = await skill.execute(
                    {
                        "keyword": keyword,
                        "limit": 10,
                        "min_likes": 0,
                        "require_cover": False,
                    }
                )
                results_all.append((keyword, result))

        await asyncio.gather(
            _search("穿搭"),
            _search("咖啡"),
            _search("手冲"),
        )

        assert len(results_all) == 3
        keywords = {kw for kw, _ in results_all}
        assert keywords == {"穿搭", "咖啡", "手冲"}

    @pytest.mark.asyncio
    async def test_pool_limits_concurrent_crawler_agents(self):
        """并发池应限制同时运行的爬虫子 agent 数量。"""
        manager = AgentManager(max_concurrent=2, max_depth=3)
        bus = InterAgentBus()
        pool = ConcurrencyPool(max_slots=2)
        root = manager.register_root("root")
        tools = CollaborationTools(
            agent_manager=manager,
            message_bus=bus,
            concurrency_pool=pool,
        )

        from app.core.skill_registry import get_skill_class_by_name

        spawn_cls = get_skill_class_by_name("spawn_agent")

        r1 = await spawn_cls().execute(
            _inject(
                tools,
                {
                    "task_name": "crawler_1",
                    "task_description": "Search topic 1",
                },
            )
        )
        assert r1.get("success") is True

        r2 = await spawn_cls().execute(
            _inject(
                tools,
                {
                    "task_name": "overflow",
                    "task_description": "Should fail - pool full",
                },
            )
        )
        assert "error" in r2, "max_concurrent=2, root+crawler_1 already active"

        await manager.shutdown()


# ═══════════════════════════════════════════════════════════════════
# 12. REACTOR SESSION: _ensure_collab → spawn → crawler
# ═══════════════════════════════════════════════════════════════════


class TestReactorToCrawlerChain:
    """ReactorSession → _ensure_collab → spawn → crawler skill 完整链路。"""

    def test_ensure_collab_with_crawler_skills(self):
        """_ensure_collab 初始化后，爬虫 skill 应在 session 的 skill 列表中。"""
        from app.core.skill_registry import (
            ensure_builtin_skills_registered,
            get_skill_class_by_name,
        )
        from app.engine.reactor.session import ReactorSession

        ensure_builtin_skills_registered()

        crawler_names = ["xhs_search", "trending_search", "spider_sync", "url_collect"]
        all_skills = []
        for name in crawler_names:
            cls = get_skill_class_by_name(name)
            if cls:
                all_skills.append(cls())

        session = ReactorSession(skills=all_skills, workflow_id="test_crawler_chain")
        collab = session._ensure_collab()

        assert collab is not None
        skill_names_in_session = {s.name for s in session._skills}
        for name in crawler_names:
            assert name in skill_names_in_session, f"{name} should be in session skills"

    def test_collab_disabled_hides_crawler_tools(self):
        """collab_mode=disabled 时 LLM 看不到 spawn_agent，无法调度爬虫子 agent。"""
        from app.engine.reactor.session import ReactorSession

        session = ReactorSession(skills=[], workflow_id="test_no_collab", collab_mode="disabled")

        collab = session._ensure_collab()
        assert collab is None

    def test_inject_params_carries_all_infra(self):
        """inject_params 应注入完整的协作基础设施。"""
        manager = AgentManager(max_concurrent=3, max_depth=3)
        bus = InterAgentBus()
        pool = ConcurrencyPool(max_slots=3)
        root = manager.register_root("root")
        tools = CollaborationTools(
            agent_manager=manager,
            message_bus=bus,
            concurrency_pool=pool,
        )

        params = {}
        tools.inject_params(params, "root")

        assert params["_agent_manager"] is manager
        assert params["_message_bus"] is bus
        assert params["_concurrency_pool"] is pool
        assert params["_caller_agent_id"] == "root"

        asyncio.run(manager.shutdown())
