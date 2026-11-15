"""DeepSeekSearchSource 缓存行为单元测试（不打真实网络，不产生费用）。

覆盖：
1. 同关键词二次调用命中缓存，不再请求上游
2. limit 只影响切片，不影响缓存内容
3. TTL 过期后重新请求
4. 空结果不写缓存（避免一次抖动锁死该关键词）
5. 同 key 并发请求合并（single-flight）
6. 返回副本，调用方修改不污染缓存
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from capabilities.content_search.sources.deepseek_search_source import (  # noqa: E402
    DeepSeekSearchSource,
)


def _fake_response(n: int = 10, tag: str = "x") -> dict:
    """构造与真实 Messages 响应同构的假数据（含 web_search_tool_result 块）。"""
    results = [
        {
            "type": "web_search_result",
            "title": f"结果 {tag}-{i}",
            "url": f"https://example.com/article/{tag}-{i}",
            "page_age": "",
            "encrypted_content": "ignored",
        }
        for i in range(n)
    ]
    return {
        "content": [
            {"type": "server_tool_use", "name": "web_search", "input": {"query": tag}},
            {"type": "web_search_tool_result", "content": results},
            {"type": "text", "text": "答案正文", "citations": []},
        ],
        "usage": {"input_tokens": 100, "output_tokens": 50},
        "stop_reason": "end_turn",
    }


@pytest.fixture
def source() -> DeepSeekSearchSource:
    return DeepSeekSearchSource(api_key="fake-key", cache_ttl=300)


def _install_counter(source: DeepSeekSearchSource, n: int = 10, delay: float = 0.0):
    """把 _call_messages 换成计数版假实现，返回调用次数计数器。"""
    calls = {"n": 0}

    async def fake_call(query: str) -> dict:
        calls["n"] += 1
        if delay:
            await asyncio.sleep(delay)
        return _fake_response(n=n, tag=str(calls["n"]))

    source._call_messages = fake_call  # type: ignore[method-assign]
    return calls


@pytest.mark.asyncio
async def test_cache_hit_skips_upstream(source: DeepSeekSearchSource):
    """同一关键词二次调用应命中缓存，上游只被打一次。"""
    calls = _install_counter(source)

    first = await source.search_trending("露营装备", limit=5)
    second = await source.search_trending("露营装备", limit=5)

    assert calls["n"] == 1, f"上游应只调用 1 次，实际 {calls['n']}"
    assert len(first) == 5 and len(second) == 5
    assert [r.url for r in first] == [r.url for r in second]


@pytest.mark.asyncio
async def test_limit_slices_without_refetch(source: DeepSeekSearchSource):
    """limit 只做切片，不影响缓存：先小后大不触发重新请求。"""
    calls = _install_counter(source, n=10)

    small = await source.search_trending("露营装备", limit=3)
    large = await source.search_trending("露营装备", limit=8)

    assert calls["n"] == 1
    assert len(small) == 3
    assert len(large) == 8
    assert [r.url for r in small] == [r.url for r in large[:3]]


@pytest.mark.asyncio
async def test_ttl_expiry_refetches(source: DeepSeekSearchSource):
    """缓存到期后应重新请求上游。"""
    calls = _install_counter(source)
    await source.search_trending("露营装备", limit=5)
    assert calls["n"] == 1

    # 手动把过期时间推到过去，避免测试里真的等 300 秒
    key = "week|露营装备"
    expired_at, results = source._cache[key]
    source._cache[key] = (time.monotonic() - 1, results)

    await source.search_trending("露营装备", limit=5)
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_empty_result_not_cached(source: DeepSeekSearchSource):
    """空结果不写缓存，下次调用仍会重试上游。"""

    async def empty_call(query: str) -> dict:
        return {"content": [{"type": "text", "text": "没有搜索结果"}], "stop_reason": "end_turn"}

    source._call_messages = empty_call  # type: ignore[method-assign]

    first = await source.search_trending("冷门关键词", limit=5)
    assert first == []
    assert "week|冷门关键词" not in source._cache

    second = await source.search_trending("冷门关键词", limit=5)
    assert second == []


@pytest.mark.asyncio
async def test_concurrent_same_key_single_flight(source: DeepSeekSearchSource):
    """同 key 并发请求应合并成一次上游调用。"""
    calls = _install_counter(source, delay=0.05)

    results = await asyncio.gather(
        *[source.search_trending("露营装备", limit=5) for _ in range(5)]
    )

    assert calls["n"] == 1, f"并发 5 次应只打 1 次上游，实际 {calls['n']}"
    assert all(len(r) == 5 for r in results)


@pytest.mark.asyncio
async def test_returned_objects_are_copies(source: DeepSeekSearchSource):
    """返回的是副本，调用方修改字段不会污染缓存。"""
    _install_counter(source)
    await source.search_trending("露营装备", limit=5)

    hit = await source.search_trending("露营装备", limit=5)
    hit[0].title = "被调用方改掉了"

    again = await source.search_trending("露营装备", limit=5)
    assert again[0].title != "被调用方改掉了", "缓存被调用方污染"


@pytest.mark.asyncio
async def test_cache_disabled_when_ttl_zero():
    """cache_ttl=0 时每次调用都打上游。"""
    src = DeepSeekSearchSource(api_key="fake-key", cache_ttl=0)
    calls = _install_counter(src)

    await src.search_trending("露营装备", limit=5)
    await src.search_trending("露营装备", limit=5)

    assert calls["n"] == 2
    assert src._cache == {}


@pytest.mark.asyncio
async def test_different_keyword_not_shared(source: DeepSeekSearchSource):
    """不同关键词互不共享缓存。"""
    calls = _install_counter(source)

    await source.search_trending("露营装备", limit=5)
    await source.search_trending("美妆教程", limit=5)

    assert calls["n"] == 2