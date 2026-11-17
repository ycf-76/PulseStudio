"""DeepSeekApiSource 单元测试（不打真实网络，不产生费用）。

协议依据：DeepSeek-Harness packages/web/web-deepseek-api（0.2.2-alpha.3）
- 检索请求体：query / count(1-50) / need_content:false
- 检索响应：{"results": [{url, title, snippet, summary?, date?}]}，空数组也算成功
- 抓取请求体：{"url": ...}；抓取响应：{"content": "<markdown>"}，HTTP 200 即成功
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from capabilities.content_search.sources import deepseek_api_source as das  # noqa: E402
from capabilities.content_search.sources.deepseek_api_source import (  # noqa: E402
    DeepSeekApiSource,
    parse_fetch,
    parse_results,
)


def _item(url: str = "https://example.com/a/1", **extra: Any) -> dict[str, Any]:
    base = {
        "url": url,
        "title": "标题",
        "snippet": "短摘录",
        "summary": "正文摘要",
        "date": "2026-10-31T16:00:00+00:00",
        "site_name": "什么值得买",
        "authority": 1,
        "rerank_score": 1.009,
    }
    base.update(extra)
    return base


def _payload(*items: dict[str, Any]) -> dict[str, Any]:
    return {"results": list(items)}


def _items(n: int) -> list[dict[str, Any]]:
    return [_item(url=f"https://example.com/a/{i}") for i in range(n)]


class _FakeResponse:
    def __init__(self, status_code: int, payload: Any = None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self) -> Any:
        return self._payload


class _FakeClient:
    """按顺序吐出预置响应，并记录每次请求。"""

    def __init__(self, responses: list[_FakeResponse]) -> None:
        self._responses = responses
        self.requests: list[dict[str, Any]] = []

    async def post(self, url: str, json: Any = None, headers: Any = None) -> _FakeResponse:
        self.requests.append({"url": url, "json": json, "headers": headers})
        if len(self._responses) > 1:
            return self._responses.pop(0)
        return self._responses[0]

    async def aclose(self) -> None:
        pass


async def _no_sleep(_seconds: float) -> None:
    return None


@pytest.fixture
def source() -> DeepSeekApiSource:
    """默认关闭 Fetch 补全与缓存：本文件主体测检索协议，补全/缓存另有专门用例。"""
    return DeepSeekApiSource(api_key="fake-key", count=20, cache_ttl=0, fetch_enabled=False)


@pytest.fixture
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """把重试等待变成零等待，避免测试真的等 3 秒。"""
    monkeypatch.setattr(das, "asyncio", types.SimpleNamespace(sleep=_no_sleep))


def test_platform_name(source: DeepSeekApiSource):
    """
    测试: 平台标识
    场景: 实例化后读取 name
    预期: "deepseek_api"（与旧路线 "deepseek" 区分，便于并存对比）
    文档依据: base.py ContentSource.name 契约
    """
    assert source.name == "deepseek_api"


def test_parse_results_maps_all_fields():
    """
    测试: 响应解析
    场景: 合法响应含 url/title/snippet/summary/date/site_name
    预期: 归一化后字段齐全
    文档依据: dsh-web-deepseek-api README「结果与失败」
    """
    items = parse_results(_payload(_item()))

    assert items is not None
    assert len(items) == 1
    assert items[0]["url"] == "https://example.com/a/1"
    assert items[0]["title"] == "标题"
    assert items[0]["snippet"] == "短摘录"
    assert items[0]["summary"] == "正文摘要"
    assert items[0]["date"] == "2026-10-31T16:00:00+00:00"
    assert items[0]["site_name"] == "什么值得买"


def test_parse_results_empty_is_success():
    """
    测试: 空结果
    场景: 服务端返回 results: []
    预期: 返回空列表而非 None（文档明示空数组表示成功）
    文档依据: dsh-web-deepseek-api README「空结果数组表示成功」
    """
    assert parse_results({"results": []}) == []


def test_parse_results_date_null_becomes_empty():
    """
    测试: 可选字段缺失
    场景: date 为 null、summary 缺失（文档说明两者均可选）
    预期: 归一化成空串
    文档依据: dsh-web-deepseek-api README「可为 null 的 date」
    """
    items = parse_results(_payload(_item(date=None, summary=None)))

    assert items is not None
    assert items[0]["date"] == ""
    assert items[0]["summary"] == ""


@pytest.mark.parametrize(
    "bad_payload",
    [
        None,
        [],
        {"results": "not-a-list"},
        {"items": []},
        {"results": [_item(url="")]},
        {"results": [{"title": "无 url", "snippet": "x"}]},
        {"results": [{"url": "https://a.com/1", "title": "x"}]},
        {"results": [{"url": "https://a.com/1", "title": "x", "snippet": 1}]},
        {"results": [_item(summary=123)]},
        {"results": [_item(date=123)]},
        {"results": ["not-a-dict"]},
    ],
)
def test_parse_results_invalid_payload_returns_none(bad_payload):
    """
    测试: 严格校验
    场景: 响应不是对象 / results 不是数组 / 条目缺必填字段或类型错
    预期: 整包判定无效返回 None（与 harness invalidResponse 一致）
    文档依据: dsh-web-deepseek-api lib/index.js mapSearchResponse
    """
    assert parse_results(bad_payload) is None


def test_parse_results_drops_homepage():
    """
    测试: 站点首页过滤
    场景: results 中混入 path 为 "/" 的站点首页
    预期: 首页被剔除，只留真实文章
    文档依据: 旧路线 deepseek_search_source._is_homepage 同类策略
    """
    items = parse_results(
        _payload(
            _item(url="https://news.ifeng.com/?mid=1"),
            _item(url="https://example.com/article/1"),
        )
    )

    assert items is not None
    assert [i["url"] for i in items] == ["https://example.com/article/1"]


async def test_search_trending_maps_to_trending_content(source: DeepSeekApiSource):
    """
    测试: 搜索结果映射
    场景: 服务端返回两条合法结果
    预期: summary 用短摘录、content 用正文摘要、published_at 用真实时间、author 用站点名
    文档依据: base.py TrendingContent 契约 + dsh-web-deepseek-api README
    """
    source._client = _FakeClient([_FakeResponse(200, _payload(*_items(2)))])

    results = await source.search_trending("露营装备", limit=10)

    assert len(results) == 2
    first = results[0]
    assert first.platform == "deepseek_api"
    assert first.url == "https://example.com/a/0"
    assert first.summary == "短摘录"
    assert first.content == "正文摘要"
    assert first.published_at == "2026-10-31T16:00:00+00:00"
    assert first.author == "什么值得买"
    assert first.content_id.startswith("deepseek_api_")
    assert first.raw["rank"] == 1


async def test_count_is_min_of_config_and_limit(source: DeepSeekApiSource):
    """
    测试: 结果条数请求
    场景: 配置 count=20，调用方 limit=5
    预期: 请求体 count=5（更小的请求上限优先），且带 need_content=false
    文档依据: dsh-web-deepseek-api README「请求中更小的 maxResults 优先」
    """
    client = _FakeClient([_FakeResponse(200, _payload(*_items(5)))])
    source._client = client

    await source.search_trending("露营装备", limit=5)

    body = client.requests[0]["json"]
    assert body["count"] == 5
    assert body["need_content"] is False
    assert body["query"] == "露营装备"
    assert "time_range" not in body


def test_count_clamped_to_service_bounds():
    """
    测试: count 边界
    场景: 配置 count=100（超服务端上限 50）与 count=0（低于下限 1）
    预期: 分别夹到 50 与 1
    文档依据: dsh-web-deepseek-api README「count 为 1 至 50 的整数」
    """
    assert DeepSeekApiSource(api_key="k", count=100).count == 50
    assert DeepSeekApiSource(api_key="k", count=0).count == 1


async def test_limit_slices_results(source: DeepSeekApiSource):
    """
    测试: 返回条数
    场景: 服务端返回 6 条，limit=3
    预期: 只返回前 3 条，保持服务端 rerank 顺序
    文档依据: base.py search_trending 契约
    """
    source._client = _FakeClient([_FakeResponse(200, _payload(*_items(6)))])

    results = await source.search_trending("露营装备", limit=3)

    assert len(results) == 3
    assert [r.url for r in results] == [f"https://example.com/a/{i}" for i in range(3)]


async def test_invalid_response_returns_empty(source: DeepSeekApiSource):
    """
    测试: 无效响应兜底
    场景: 服务端 200 但响应结构不合法
    预期: 返回空列表，不抛异常（search 节点可继续走降级链）
    文档依据: docs/开发指南.md 高可用性要求（关键路径有降级）
    """
    source._client = _FakeClient([_FakeResponse(200, {"unexpected": True})])

    assert await source.search_trending("露营装备", limit=5) == []


async def test_retry_on_5xx_then_success(source: DeepSeekApiSource, no_sleep: None):
    """
    测试: 5xx 重试
    场景: 第一次 503，第二次 200
    预期: 自动重试并最终返回结果
    文档依据: 旧路线 deepseek_search_source 同类重试策略（429/5xx 退避重试）
    """
    client = _FakeClient(
        [
            _FakeResponse(503, None, "service unavailable"),
            _FakeResponse(200, _payload(*_items(1))),
        ]
    )
    source._client = client

    results = await source.search_trending("露营装备", limit=5)

    assert len(client.requests) == 2
    assert len(results) == 1


async def test_401_not_retried(source: DeepSeekApiSource, no_sleep: None):
    """
    测试: 鉴权失败
    场景: 服务端返回 401
    预期: 不重试（Key 无效时重试无意义），直接返回空
    文档依据: dsh-web-deepseek-api README「不会自动重试或回退至其他提供者」
    """
    client = _FakeClient([_FakeResponse(401, {"error": "invalid key"}, "invalid key")])
    source._client = client

    assert await source.search_trending("露营装备", limit=5) == []
    assert len(client.requests) == 1


async def test_empty_keyword_uses_default_query(source: DeepSeekApiSource):
    """
    测试: 空关键词兜底
    场景: keyword 为空白
    预期: 用内置热点查询，避免向服务端发空 query
    文档依据: 旧路线 deepseek_search_source 同类兜底
    """
    client = _FakeClient([_FakeResponse(200, _payload(*_items(1)))])
    source._client = client

    await source.search_trending("   ", limit=5)

    assert client.requests[0]["json"]["query"] == das._DEFAULT_QUERY


async def test_get_trending_uses_category_as_query(source: DeepSeekApiSource):
    """
    测试: get_trending
    场景: category="美妆"
    预期: category 作为 query 检索（该接口无榜单概念）
    文档依据: base.py get_trending 契约
    """
    client = _FakeClient([_FakeResponse(200, _payload(*_items(1)))])
    source._client = client

    results = await source.get_trending(category="美妆", limit=5)

    assert client.requests[0]["json"]["query"] == "美妆"
    assert len(results) == 1


async def test_auth_header_uses_bearer(source: DeepSeekApiSource):
    """
    测试: 鉴权头
    场景: 发起检索
    预期: Authorization: Bearer <key>（API Key 路线，不发 x-dsh-auth-token）
    文档依据: dsh-web-deepseek-api README「API 密钥使用 Authorization: Bearer」
    """
    client = _FakeClient([_FakeResponse(200, _payload(*_items(1)))])
    source._client = client

    await source.search_trending("露营装备", limit=5)

    headers = client.requests[0]["headers"]
    assert headers["authorization"] == "Bearer fake-key"
    assert "x-dsh-auth-token" not in headers


async def test_request_url_appends_search_to_base(source: DeepSeekApiSource):
    """
    测试: 端点拼接
    场景: base_url 带 /v1/tools 前缀
    预期: 追加 /search，不重复拼前缀
    文档依据: dsh-web-deepseek-api README「追加 /search 或 /fetch」
    """
    client = _FakeClient([_FakeResponse(200, _payload(*_items(1)))])
    source._client = client

    await source.search_trending("露营装备", limit=5)

    assert client.requests[0]["url"] == "https://api.deepseek.com/v1/tools/search"


# ═══════════════════════════════════════════════════════════════════
# Fetch API：短正文补全
# ═══════════════════════════════════════════════════════════════════


class _RouteClient:
    """按端点分流：/search 固定返回检索响应，/fetch 按目标 URL 返回预置响应。"""

    def __init__(
        self,
        search_payload: Any,
        fetch_map: dict[str, _FakeResponse] | None = None,
        fetch_default: _FakeResponse | None = None,
    ) -> None:
        self.search_payload = search_payload
        self.fetch_map = fetch_map or {}
        self.fetch_default = fetch_default
        self.requests: list[dict[str, Any]] = []

    async def post(self, url: str, json: Any = None, headers: Any = None) -> _FakeResponse:
        self.requests.append({"url": url, "json": json, "headers": headers})
        if url.endswith("/search"):
            return _FakeResponse(200, self.search_payload)
        target = (json or {}).get("url")
        if target in self.fetch_map:
            return self.fetch_map[target]
        if self.fetch_default is not None:
            return self.fetch_default
        return _FakeResponse(200, {"content": f"# 正文 for {target}"})

    def fetch_requests(self) -> list[dict[str, Any]]:
        return [r for r in self.requests if r["url"].endswith("/fetch")]

    async def aclose(self) -> None:
        pass


def _short_item(url: str, chars: int = 5) -> dict[str, Any]:
    return _item(url=url, summary="短" * chars)


def test_parse_fetch_extracts_markdown():
    """
    测试: Fetch 响应解析
    场景: 合法响应 {"content": "..."}
    预期: 返回 Markdown 文本
    文档依据: dsh-web-deepseek-api lib/index.js decode（payload.content 为字符串即成功）
    """
    assert parse_fetch({"content": "# 标题\n\n正文"}) == "# 标题\n\n正文"


@pytest.mark.parametrize(
    "bad_payload",
    [
        None,
        [],
        {"content": 123},
        {"body": "x"},
        {"content": "   "},
    ],
)
def test_parse_fetch_invalid_returns_none(bad_payload):
    """
    测试: Fetch 严格校验
    场景: 响应缺 content / 类型错 / 内容只有空白
    预期: 返回 None（由调用方保留原正文）
    文档依据: dsh-web-deepseek-api lib/index.js decode（非字符串即 invalidResponse）
    """
    assert parse_fetch(bad_payload) is None


async def test_fetch_enriches_short_content():
    """
    测试: 短正文补全
    场景: 检索结果正文 5 字（低于阈值），Fetch 返回 Markdown
    预期: content 被 Markdown 覆盖，raw["fetched"] 标记为 True
    文档依据: dsh-web-deepseek-api README「Fetch 返回页面 Markdown」
    """
    src = DeepSeekApiSource(api_key="k", count=20, cache_ttl=0, fetch_min_chars=200)
    client = _RouteClient(
        _payload(_short_item("https://example.com/a/1")),
        fetch_map={"https://example.com/a/1": _FakeResponse(200, {"content": "# 页面正文"})},
    )
    src._client = client

    results = await src.search_trending("露营装备", limit=5)

    assert results[0].content == "# 页面正文"
    assert results[0].content_original == "# 页面正文"
    assert results[0].raw["fetched"] is True
    fetch_reqs = client.fetch_requests()
    assert len(fetch_reqs) == 1
    assert fetch_reqs[0]["url"] == "https://api.deepseek.com/v1/tools/fetch"
    assert fetch_reqs[0]["json"] == {"url": "https://example.com/a/1"}


async def test_fetch_skips_long_content():
    """
    测试: 长正文不补全
    场景: 检索结果正文 2000 字（高于阈值 200）
    预期: 不发 Fetch 请求，正文保持服务端摘要
    文档依据: 成本控制——已有足够正文时不额外抓取
    """
    src = DeepSeekApiSource(api_key="k", count=20, cache_ttl=0, fetch_min_chars=200)
    client = _RouteClient(_payload(_short_item("https://example.com/a/1", chars=2000)))
    src._client = client

    results = await src.search_trending("露营装备", limit=5)

    assert client.fetch_requests() == []
    assert results[0].content == "短" * 2000


async def test_fetch_failure_keeps_original_content():
    """
    测试: 补全失败兜底
    场景: Fetch 返回 404（该接口失败时无内容可用）
    预期: 保留原正文，不抛异常，不影响检索结果返回
    文档依据: dsh-web-deepseek-api README「抓取失败即失败，不回报目标站状态码」
    """
    src = DeepSeekApiSource(api_key="k", count=20, cache_ttl=0, fetch_min_chars=200)
    client = _RouteClient(
        _payload(_short_item("https://example.com/a/1")),
        fetch_default=_FakeResponse(404, {"error": "not found"}, "not found"),
    )
    src._client = client

    results = await src.search_trending("露营装备", limit=5)

    assert results[0].content == "短" * 5
    assert "fetched" not in results[0].raw


async def test_fetch_rejects_shorter_anti_bot_page():
    """
    测试: 抓取结果劣于原摘要时拒绝覆盖
    场景: 原摘要 500 字（低于阈值 1000 触发补全），Fetch 抓到 60 字反爬验证页
    预期: 保留 500 字原摘要，不标记 fetched
    文档依据: dsh-web-deepseek-api「Fetch 不回报目标站状态码与最终 URL，HTTP 200 即视为成功」，
    故 200 也可能是反爬/登录墙，用它覆盖更长的服务端摘要会让正文质量断崖式下降
    """
    src = DeepSeekApiSource(
        api_key="k",
        count=20,
        cache_ttl=0,
        fetch_min_chars=1000,
    )
    client = _RouteClient(
        _payload(_short_item("https://example.com/a/1", chars=500)),
        fetch_map={
            "https://example.com/a/1": _FakeResponse(
                200, {"content": "环境异常\n当前环境异常，完成验证后即可继续访问。"}
            )
        },
    )
    src._client = client

    results = await src.search_trending("露营装备", limit=5)

    assert results[0].content == "短" * 500
    assert results[0].content_original == "短" * 500
    assert "fetched" not in results[0].raw
    assert len(client.fetch_requests()) == 1


async def test_fetch_max_urls_limits_requests():
    """
    测试: 补全条数上限
    场景: 3 条短正文，fetch_max_urls=2
    预期: 只对前 2 条发 Fetch（按服务端 rerank 顺序）
    文档依据: 成本控制——补全条数由配置决定
    """
    src = DeepSeekApiSource(
        api_key="k",
        count=20,
        cache_ttl=0,
        fetch_min_chars=200,
        fetch_max_urls=2,
    )
    client = _RouteClient(_payload(*[_short_item(f"https://example.com/a/{i}") for i in range(3)]))
    src._client = client

    results = await src.search_trending("露营装备", limit=5)

    fetch_reqs = client.fetch_requests()
    assert len(fetch_reqs) == 2
    assert [r["json"]["url"] for r in fetch_reqs] == [
        "https://example.com/a/0",
        "https://example.com/a/1",
    ]
    assert results[2].raw.get("fetched") is None


async def test_fetch_truncates_to_max_chars():
    """
    测试: 补全正文截断
    场景: Fetch 返回 6000 字 Markdown，fetch_max_chars=4000
    预期: 写入正文时截断到 4000 字，避免下游 token 爆炸
    文档依据: 成本控制——单条正文上限由配置决定
    """
    src = DeepSeekApiSource(
        api_key="k",
        count=20,
        cache_ttl=0,
        fetch_min_chars=200,
        fetch_max_chars=4000,
    )
    long_md = "字" * 6000
    client = _RouteClient(
        _payload(_short_item("https://example.com/a/1")),
        fetch_map={"https://example.com/a/1": _FakeResponse(200, {"content": long_md})},
    )
    src._client = client

    results = await src.search_trending("露营装备", limit=5)

    assert len(results[0].content) == 4000


async def test_fetch_disabled_makes_no_fetch_request():
    """
    测试: 关闭补全
    场景: fetch_enabled=False
    预期: 完全不发 Fetch 请求（纯检索，最省延迟）
    文档依据: 配置项 DEEPSEEK_SEARCH_API_FETCH_ENABLED
    """
    src = DeepSeekApiSource(api_key="k", count=20, cache_ttl=0, fetch_enabled=False)
    client = _RouteClient(_payload(_short_item("https://example.com/a/1")))
    src._client = client

    await src.search_trending("露营装备", limit=5)

    assert client.fetch_requests() == []


# ═══════════════════════════════════════════════════════════════════
# 缓存：同关键词 TTL 内复用
# ═══════════════════════════════════════════════════════════════════


async def test_cache_hit_skips_second_request():
    """
    测试: 缓存命中
    场景: 同关键词连续搜索两次
    预期: 第二次不再发网络请求，结果内容一致
    文档依据: 延迟优化——重复关键词复用上次检索
    """
    src = DeepSeekApiSource(api_key="k", count=20, cache_ttl=600, fetch_enabled=False)
    client = _RouteClient(_payload(*_items(2)))
    src._client = client

    first = await src.search_trending("露营装备", limit=2)
    second = await src.search_trending("露营装备", limit=2)

    assert len(client.requests) == 1
    assert [r.url for r in second] == [r.url for r in first]


async def test_cache_disabled_always_requests():
    """
    测试: 关闭缓存
    场景: cache_ttl=0，同关键词搜两次
    预期: 每次都发网络请求
    文档依据: 配置项 DEEPSEEK_SEARCH_API_CACHE_TTL（0=禁用）
    """
    src = DeepSeekApiSource(api_key="k", count=20, cache_ttl=0, fetch_enabled=False)
    client = _RouteClient(_payload(*_items(2)))
    src._client = client

    await src.search_trending("露营装备", limit=2)
    await src.search_trending("露营装备", limit=2)

    assert len(client.requests) == 2


async def test_cache_does_not_store_empty_results():
    """
    测试: 空结果不缓存
    场景: 第一次返回空数组（服务端成功但无结果），第二次返回有结果
    预期: 第二次仍然重新请求并拿到数据（一次空响应不应污染 TTL）
    文档依据: 与旧路线 deepseek_search_source 同策略（空结果不写缓存）
    """
    src = DeepSeekApiSource(api_key="k", count=20, cache_ttl=600, fetch_enabled=False)

    class _SequenceClient:
        def __init__(self) -> None:
            self.calls = 0

        async def post(self, url: str, json: Any = None, headers: Any = None) -> _FakeResponse:
            self.calls += 1
            if self.calls == 1:
                return _FakeResponse(200, {"results": []})
            return _FakeResponse(200, _payload(*_items(1)))

        async def aclose(self) -> None:
            pass

    client = _SequenceClient()
    src._client = client

    assert await src.search_trending("露营装备", limit=2) == []
    assert len(await src.search_trending("露营装备", limit=2)) == 1
    assert client.calls == 2


async def test_clear_cache_forces_refetch():
    """
    测试: 手动失效
    场景: 缓存命中后调用 clear_cache() 再搜
    预期: 重新发网络请求
    文档依据: 运维/测试手动失效接口
    """
    src = DeepSeekApiSource(api_key="k", count=20, cache_ttl=600, fetch_enabled=False)
    client = _RouteClient(_payload(*_items(1)))
    src._client = client

    await src.search_trending("露营装备", limit=2)
    src.clear_cache()
    await src.search_trending("露营装备", limit=2)

    assert len(client.requests) == 2
