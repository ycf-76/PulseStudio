"""通用网页抓取与平台路由测试。

文档依据: docs/FEATURE_平台账号管理与作品数据回收.md（采集降级链）
核心约束: 平台正则只做路由提示，不能成为「非这 6 个平台就不采集」的准入限制。
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services import web_page_fetcher, work_collector
from app.services.web_page_fetcher import (
    MIN_TEXT_CHARS,
    collect_generic_page,
    extract_web_content,
    fetch_web_page,
)

_ARTICLE_HTML = """
<html>
<head>
  <title>标题 - 站点名</title>
  <meta property="og:title" content="一篇公开博客文章" />
  <meta name="author" content="张三" />
  <meta property="og:image" content="https://cdn.example.com/cover.png" />
  <script>var noise = "这段脚本内容不该出现在正文里";</script>
  <style>.noise{color:red}</style>
</head>
<body>
  <nav><a href="/">首页</a><a href="/about">关于</a><a href="/login">登录</a></nav>
  <header><h1>站点大标题</h1></header>
  <main>
    <article>
      <h2>正文标题</h2>
      <p>第一段正文内容，讲的是如何在不限制平台的前提下采集公开网页数据，任何 http 链接都应该能走通通用路径。</p>
      <p>第二段正文内容，补充说明正文抽取会剔除导航、页脚、脚本等噪声，只保留真正可读的段落文本。</p>
      <p>第三段正文内容，验证段落之间保持换行，便于后续结构化处理以及交给模型做摘要或改写。</p>
      <p>第四段正文内容，用于保证正文长度超过最小阈值，避免被误判为登录墙或空白页面。</p>
      <img src="https://cdn.example.com/inline.png" />
    </article>
  </main>
  <footer><p>版权所有 2026</p></footer>
</body>
</html>
"""


def test_extract_web_content_uses_og_title_and_body():
    """
    测试: 通用网页正文抽取
    场景: 含 nav/header/footer/script 的标准文章页
    预期: 取 og:title，正文来自 article，导航与脚本噪声被剔除
    文档依据: docs/FEATURE_平台账号管理与作品数据回收.md#7-Spider采集层
    """
    result = extract_web_content(_ARTICLE_HTML, "https://example.com/post/1")

    assert result["title"] == "一篇公开博客文章"
    assert result["author"] == "张三"
    assert "第一段正文内容" in result["content_text"]
    assert "第三段正文内容" in result["content_text"]
    assert "这段脚本内容不该出现在正文里" not in result["content_text"]
    assert "首页" not in result["content_text"]
    assert "版权所有 2026" not in result["content_text"]
    assert result["image_urls"][0] == "https://cdn.example.com/cover.png"


def test_extract_web_content_falls_back_to_full_body():
    """
    测试: 正文容器兜底
    场景: 无 article 结构、正文散落在 div 里的简单页面
    预期: 仍能抽出正文，不返回空文本
    文档依据: docs/FEATURE_平台账号管理与作品数据回收.md#7-Spider采集层
    """
    html = (
        "<html><body><div>"
        + "<div>这是一段没有 article 标签的正文内容，长度足以通过最小正文阈值。</div>"
        + "<div>第二段同样是正文，用于验证兜底路径能取到全文。</div>"
        + "</div></body></html>"
    )
    result = extract_web_content(html, "https://example.com/plain")

    assert "没有 article 标签的正文内容" in result["content_text"]
    assert "第二段同样是正文" in result["content_text"]


@pytest.mark.asyncio
async def test_collect_generic_page_returns_collector_shape():
    """
    测试: 通用网页采集输出结构
    场景: 抓取到一篇正常公开文章
    预期: 输出与 work_collector 各平台采集一致的字段，platform=web
    文档依据: docs/FEATURE_平台账号管理与作品数据回收.md#2-核心衔接点
    """
    page = extract_web_content(_ARTICLE_HTML, "https://example.com/post/1")
    page["content_type"] = "text/html"
    page["status"] = 200

    with patch("app.services.web_page_fetcher.fetch_web_page", AsyncMock(return_value=page)):
        stats = await collect_generic_page("https://example.com/post/1", "unknown")

    assert stats is not None
    assert stats["platform"] == "web"
    assert stats["note_type"] == "article"
    assert stats["title"] == "一篇公开博客文章"
    assert "第一段正文内容" in stats["content_text"]
    for key in ("likes", "collects", "comments", "shares", "cover_img_url", "image_urls"):
        assert key in stats


@pytest.mark.asyncio
async def test_collect_generic_page_rejects_thin_content():
    """
    测试: 正文过短判定为无效页面
    场景: 页面只有登录墙/空白壳（正文 < MIN_TEXT_CHARS）
    预期: 返回 None，不把噪声当成正文
    文档依据: docs/FEATURE_平台账号管理与作品数据回收.md#7-Spider采集层
    """
    page = {
        "url": "https://example.com/wall",
        "title": "请登录",
        "content_text": "请先登录后查看",
        "text_length": len("请先登录后查看"),
        "author": "",
        "site_name": "",
        "published_at": "",
        "image_urls": [],
        "content_type": "text/html",
        "status": 200,
    }
    assert len(page["content_text"]) < MIN_TEXT_CHARS

    with patch("app.services.web_page_fetcher.fetch_web_page", AsyncMock(return_value=page)):
        stats = await collect_generic_page("https://example.com/wall")

    assert stats is None


@pytest.mark.asyncio
async def test_collect_from_url_unknown_platform_no_longer_rejected():
    """
    测试: 未知平台不再被正则拒绝
    场景: 任意普通公开网页（非 6 个已知平台）
    预期: 走通用网页抓取并返回数据，source=web_page_parse
    文档依据: docs/FEATURE_平台账号管理与作品数据回收.md#7-Spider采集层
    """
    stats = {
        "note_id": "https://example.com/post/1",
        "title": "一篇公开博客文章",
        "content_text": "正文" * 200,
        "tags": [],
        "cover_img_url": "",
        "image_urls": [],
        "likes": 0,
        "collects": 0,
        "comments": 0,
        "shares": 0,
        "author_fans": 1,
        "author_nickname": "张三",
        "note_type": "article",
        "platform": "unknown",
    }

    with patch.object(work_collector, "collect_generic_page", AsyncMock(return_value=stats)):
        result = await work_collector.collect_from_url("https://example.com/post/1")

    assert result is not None
    assert result["source"] == "web_page_parse"
    assert result["title"] == "一篇公开博客文章"
    assert not any("无法识别链接所属平台" in e for e in work_collector.get_last_collect_errors())


@pytest.mark.asyncio
async def test_collect_from_url_known_platform_without_content_id_falls_back():
    """
    测试: 已知平台但抽不出内容 ID 时仍降级采集
    场景: B站个人空间页（命中平台正则，但没有 bvid）
    预期: 降级到通用网页抓取，而不是直接返回 None
    文档依据: docs/FEATURE_平台账号管理与作品数据回收.md#7-Spider采集层
    """
    stats = {
        "note_id": "https://space.bilibili.com/12345",
        "title": "UP主主页",
        "content_text": "主页正文" * 100,
        "tags": [],
        "cover_img_url": "",
        "image_urls": [],
        "likes": 0,
        "collects": 0,
        "comments": 0,
        "shares": 0,
        "author_fans": 1,
        "author_nickname": "UP主",
        "note_type": "article",
        "platform": "bilibili",
    }

    with patch.object(work_collector, "collect_generic_page", AsyncMock(return_value=stats)):
        result = await work_collector.collect_from_url("https://space.bilibili.com/12345")

    assert result is not None
    assert result["source"] == "web_page_parse"
    assert result["platform"] == "bilibili"


@pytest.mark.asyncio
async def test_collect_from_url_generic_chain_falls_back_to_link_grabber():
    """
    测试: 通用链降级顺序
    场景: 通用网页抓取失败（需登录/反爬）
    预期: 继续尝试 link-grabber，两个都失败才返回 None，且记录失败原因
    文档依据: docs/FEATURE_平台账号管理与作品数据回收.md#8-work_collector降级链增强
    """
    grabber_stats = {"note_id": "x", "title": "抓取成功", "platform": "unknown"}

    with (
        patch.object(work_collector, "collect_generic_page", AsyncMock(return_value=None)),
        patch.object(
            work_collector, "_collect_via_link_grabber", AsyncMock(return_value=grabber_stats)
        ),
    ):
        result = await work_collector.collect_from_url("https://example.com/blocked")

    assert result is not None
    assert result["source"] == "link_grabber"
    assert any("通用网页" in e for e in work_collector.get_last_collect_errors())


@pytest.mark.asyncio
async def test_collect_from_url_generic_chain_all_failed_returns_none():
    """
    测试: 通用链全失败
    场景: 通用抓取与 link-grabber 都失败
    预期: 返回 None，失败原因可追溯（不再是无理由的静默 None）
    文档依据: docs/FEATURE_平台账号管理与作品数据回收.md#8-work_collector降级链增强
    """
    with (
        patch.object(work_collector, "collect_generic_page", AsyncMock(return_value=None)),
        patch.object(work_collector, "_collect_via_link_grabber", AsyncMock(return_value=None)),
    ):
        result = await work_collector.collect_from_url("https://example.com/blocked")

    assert result is None
    errors = work_collector.get_last_collect_errors()
    assert any("通用网页" in e for e in errors)
    assert any("link-grabber" in e for e in errors)


def test_detect_platform_still_routes_known_platforms():
    """
    测试: 已知平台识别保持可用
    场景: 小红书 / 抖音 / B站 链接
    预期: 仍能识别为对应平台（正则只做路由提示，功能未回退）
    文档依据: docs/FEATURE_平台账号管理与作品数据回收.md#7-Spider采集层
    """
    assert work_collector.detect_platform("https://www.xiaohongshu.com/explore/abc") == "xiaohongshu"
    assert work_collector.detect_platform("https://v.douyin.com/xyz/") == "douyin"
    assert work_collector.detect_platform("https://www.bilibili.com/video/BV1xx411c7mD") == "bilibili"
    assert work_collector.detect_platform("https://example.com/post/1") == "unknown"


class _FakeResponse:
    def __init__(self, status_code: int, body: str, content_type: str) -> None:
        self.status_code = status_code
        self.headers = {"content-type": content_type}
        self.text = body
        self.url = "https://example.com/post/1"


class _FakeClient:
    response: _FakeResponse

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *args) -> bool:
        return False

    async def get(self, url: str) -> _FakeResponse:
        return self.response


@pytest.mark.asyncio
async def test_fetch_web_page_parses_html_response(monkeypatch):
    """
    测试: HTTP 直取 + 正文解析（不依赖真实网络）
    场景: 200 + text/html 的正常页面
    预期: 返回标题与正文，状态码透传
    文档依据: docs/FEATURE_平台账号管理与作品数据回收.md#7-Spider采集层
    """
    _FakeClient.response = _FakeResponse(200, _ARTICLE_HTML, "text/html; charset=utf-8")
    monkeypatch.setattr(web_page_fetcher.httpx, "AsyncClient", _FakeClient)

    page = await fetch_web_page("https://example.com/post/1")

    assert page is not None
    assert page["status"] == 200
    assert page["title"] == "一篇公开博客文章"
    assert "第一段正文内容" in page["content_text"]


@pytest.mark.asyncio
async def test_fetch_web_page_rejects_error_status(monkeypatch):
    """
    测试: 非 2xx 直接放弃
    场景: 404 页面
    预期: 返回 None，不把错误页当正文
    文档依据: docs/FEATURE_平台账号管理与作品数据回收.md#7-Spider采集层
    """
    _FakeClient.response = _FakeResponse(404, _ARTICLE_HTML, "text/html")
    monkeypatch.setattr(web_page_fetcher.httpx, "AsyncClient", _FakeClient)

    assert await fetch_web_page("https://example.com/missing") is None


@pytest.mark.asyncio
async def test_fetch_web_page_rejects_non_html_content(monkeypatch):
    """
    测试: 非 HTML 响应不进入正文解析
    场景: content-type 为 application/pdf
    预期: 返回 None，避免把二进制当正文
    文档依据: docs/FEATURE_平台账号管理与作品数据回收.md#7-Spider采集层
    """
    _FakeClient.response = _FakeResponse(200, _ARTICLE_HTML, "application/pdf")
    monkeypatch.setattr(web_page_fetcher.httpx, "AsyncClient", _FakeClient)

    assert await fetch_web_page("https://example.com/file.pdf") is None


def test_rpa_bridge_treats_any_public_url_as_collectable():
    """
    测试: 微信/飞书桥不再用平台白名单拦截链接
    场景: 用户发送普通公开网页链接（非 6 个已知平台）
    预期: 判定为可采集，platform 返回空字符串（仅作路由提示）
    文档依据: docs/FEATURE_平台账号管理与作品数据回收.md#7-Spider采集层
    """
    from capabilities.rpa import feishu_agent_bridge, wechat_agent_bridge

    for bridge in (wechat_agent_bridge, feishu_agent_bridge):
        has_link, url, platform = bridge._has_collectable_link("看看这篇 https://example.com/post/1")
        assert has_link is True
        assert url == "https://example.com/post/1"
        assert platform == ""

        has_link, url, platform = bridge._has_collectable_link(
            "https://www.xiaohongshu.com/explore/abc"
        )
        assert has_link is True
        assert platform == "xiaohongshu"


def test_extract_web_content_tolerates_img_without_src():
    """
    测试: 图片标签缺 src 不崩解析
    场景: 真实站点常见的 <img> 只有 data-src 或干脆没有 src
    预期: 正常返回正文，不抛 NoneType 异常
    文档依据: docs/FEATURE_平台账号管理与作品数据回收.md#7-Spider采集层
    """
    html = (
        "<html><body><article>"
        "<p>没有 src 的图片标签在真实站点非常常见，解析器必须能跳过而不是整页解析失败。</p>"
        "<img><img data-src='https://cdn.example.com/a.png'>"
        "<p>第二段正文，确保正文长度超过兜底阈值，避免退回整页。</p>"
        "</article></body></html>"
    )
    result = extract_web_content(html, "https://example.com/img")

    assert "没有 src 的图片标签" in result["content_text"]
    assert "https://cdn.example.com/a.png" in result["image_urls"]


def test_http_client_kwargs_disables_system_proxy_by_default():
    """
    测试: 默认不探测系统代理
    场景: 未显式配置 HTTP_PROXY（Windows 注册表代理未运行时会导致全站 ConnectError）
    预期: trust_env=False，代理为 None，走直连
    文档依据: docs/FEATURE_平台账号管理与作品数据回收.md#7-Spider采集层
    """
    kwargs = web_page_fetcher.http_client_kwargs()

    assert kwargs["trust_env"] is False
    assert kwargs["proxy"] is None


@pytest.mark.asyncio
async def test_collect_generic_page_render_path_uses_browser():
    """
    测试: 浏览器渲染采集路径
    场景: JS 壳页面（HTTP 直取有响应但正文过短）
    预期: render=True 时自动升级为浏览器渲染，并标记 collect_source=web_browser_render
    文档依据: docs/FEATURE_平台账号管理与作品数据回收.md#7-Spider采集层
    """
    thin_page = {
        "url": "https://news.example.com",
        "title": "新闻首页",
        "content_text": "",
        "text_length": 0,
        "author": "",
        "site_name": "",
        "published_at": "",
        "image_urls": [],
        "content_type": "text/html",
        "status": 200,
    }
    rendered_page = extract_web_content(_ARTICLE_HTML, "https://news.example.com")
    rendered_page["content_type"] = "text/html"
    rendered_page["status"] = 200

    with (
        patch("app.services.web_page_fetcher.fetch_web_page", AsyncMock(return_value=thin_page)),
        patch(
            "app.services.web_page_fetcher.fetch_web_page_rendered",
            AsyncMock(return_value=rendered_page),
        ) as rendered,
    ):
        stats = await collect_generic_page("https://news.example.com", "unknown", render=True)

    assert rendered.await_count == 1
    assert stats is not None
    assert stats["platform"] == "web"
    assert stats["collect_source"] == "web_browser_render"
    assert "第一段正文内容" in stats["content_text"]


@pytest.mark.asyncio
async def test_collect_generic_page_skips_render_when_http_failed():
    """
    测试: HTTP 直取失败时不启用浏览器渲染
    场景: 网络不可达/4xx（fetch_web_page 返回 None）
    预期: 不做渲染（浏览器同样连不上，只会白等），直接返回 None
    文档依据: docs/FEATURE_平台账号管理与作品数据回收.md#7-Spider采集层
    """
    with (
        patch("app.services.web_page_fetcher.fetch_web_page", AsyncMock(return_value=None)),
        patch(
            "app.services.web_page_fetcher.fetch_web_page_rendered", AsyncMock(return_value=None)
        ) as rendered,
    ):
        assert await collect_generic_page("https://unreachable.example.com", render=True) is None

    assert rendered.await_count == 0


@pytest.mark.asyncio
async def test_collect_from_url_generic_chain_passes_render_and_keeps_source():
    """
    测试: 通用链渲染升级后的 source 透传
    场景: collect_generic_page 走浏览器渲染成功
    预期: source 透传为 web_browser_render，且只调用一次（不重复抓取）
    文档依据: docs/FEATURE_平台账号管理与作品数据回收.md#8-work_collector降级链增强
    """
    stats = {
        "note_id": "https://news.example.com",
        "title": "新闻首页",
        "content_text": "正文" * 200,
        "tags": [],
        "cover_img_url": "",
        "image_urls": [],
        "likes": 0,
        "collects": 0,
        "comments": 0,
        "shares": 0,
        "author_fans": 1,
        "author_nickname": "站点",
        "note_type": "article",
        "platform": "web",
        "collect_source": "web_browser_render",
    }

    with patch.object(
        work_collector, "collect_generic_page", AsyncMock(return_value=stats)
    ) as generic:
        result = await work_collector.collect_from_url("https://news.example.com")

    assert generic.await_count == 1
    assert generic.await_args.kwargs.get("render") is True
    assert result is not None
    assert result["source"] == "web_browser_render"
    assert "collect_source" not in result


@pytest.mark.asyncio
async def test_trending_search_routes_url_input_to_collector():
    """
    测试: 搜索入口收到链接时直接采集，不去搜关键词
    场景: 用户把任意公开网页链接贴进搜索框（platform 选了搜索引擎源）
    预期: 走 collect_from_url，返回 1 条该页面正文，不调用任何平台的 search_trending
    文档依据: docs/FEATURE_平台账号管理与作品数据回收.md#7-Spider采集层
    """
    from capabilities.content_search.skills.trending_search import TrendingSearchSkill

    stats = {
        "note_id": "https://example.com/post/1",
        "title": "一篇公开博客文章",
        "content_text": "正文" * 100,
        "tags": [],
        "cover_img_url": "",
        "image_urls": [],
        "likes": 0,
        "collects": 0,
        "comments": 0,
        "shares": 0,
        "author_fans": 1,
        "author_nickname": "张三",
        "platform": "web",
        "source_url": "https://example.com/post/1",
        "source": "web_page_parse",
    }

    with patch.object(
        work_collector, "collect_from_url", AsyncMock(return_value=stats)
    ) as collector:
        out = await TrendingSearchSkill().execute({
            "keyword": "看看这篇 https://example.com/post/1",
            "platform": "tavily",
            "limit": 10,
            "min_interactions": 0,
        })

    assert collector.await_count == 1
    assert collector.await_args.args[0] == "https://example.com/post/1"
    assert out["count"] == 1
    assert out["results"][0]["platform"] == "web"
    assert out["results"][0]["url"] == "https://example.com/post/1"
    assert out["results"][0]["collect_source"] == "web_page_parse"
    assert "正文" in out["results"][0]["content"]


@pytest.mark.asyncio
async def test_trending_search_url_failure_returns_error_not_keyword_search():
    """
    测试: 链接采集失败时不静默退回关键词搜索
    场景: 链接不可达
    预期: 返回空结果 + error，避免用链接串去搜索引擎搜出一堆无关内容
    """
    from capabilities.content_search.skills.trending_search import TrendingSearchSkill

    with patch.object(
        work_collector, "collect_from_url", AsyncMock(return_value=None)
    ):
        out = await TrendingSearchSkill().execute({
            "keyword": "https://unreachable.example.com/post",
            "platform": "",
            "limit": 10,
            "min_interactions": 0,
        })

    assert out["count"] == 0
    assert out["results"] == []
    assert out["error"]


def test_extract_url_strips_trailing_punctuation():
    """
    测试: 链接识别只做「是不是链接」判断，不做平台判定
    场景: 中文句子里夹带链接，链接末尾跟标点
    预期: 抽出干净的 URL；纯关键词输入返回空串
    """
    from app.services.topic_pool import _extract_url

    assert _extract_url("帮我看看 https://example.com/post/1。") == "https://example.com/post/1"
    assert _extract_url("（https://example.com/a?b=1）") == "https://example.com/a?b=1"
    assert _extract_url("小红书爆款选题") == ""