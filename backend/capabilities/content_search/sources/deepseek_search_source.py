"""DeepSeek 原生联网搜索内容源（服务端 web_search 工具）。

协议来源：DeepSeek-Harness 的 packages/web/web-search-deepseek/src/provider.ts
- 端点：POST https://api.deepseek.com/anthropic/v1/messages（Anthropic 兼容，非 chat-completions）
- 工具：tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": N}]
- 复用 DEEPSEEK_API_KEY，无需任何第三方搜索 API Key，国内直连

响应结构（content 块序列）：
- server_tool_use            → 服务端发起的搜索（query）
- web_search_tool_result     → content[] 内每条 web_search_result 有 title / url / page_age
                                （encrypted_content 是加密正文，无法解密，忽略）
- text                       → 答案正文 + citations[]（摘要片段藏在 citations 的 cited_text 里）

与 Tavily/Serper 的关键差异：
- 一次搜索 = 一个完整模型回合（token 计费），不是纯检索接口
- 严格模式：未触发原生搜索则返回空，绝不从模型正文里抓 URL（与 harness 一致）
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import replace
from typing import Any
from urllib.parse import urlparse

import httpx

from capabilities.content_search.sources.base import ContentSource, TrendingContent

logger = logging.getLogger(__name__)

_WEB_SEARCH_TOOL = "web_search_20250305"
_DEFAULT_QUERY = "今天 微博热搜 小红书 热门话题"
# 缓存条目上限，防止关键词无限增长导致内存膨胀
_CACHE_MAX_ENTRIES = 256


def _is_homepage(url: str) -> bool:
    """站点首页/入口页不算内容（如 https://news.ifeng.com/?mid=xxx）。"""
    path = urlparse(url).path
    return path in ("", "/")


def _content_id(url: str) -> str:
    """按 URL 生成稳定指纹（不能用内建 hash：跨进程不稳定）。"""
    digest = hashlib.md5(url.encode("utf-8")).hexdigest()[:12]
    return f"deepseek_{digest}"


def _iso_like(value: str) -> bool:
    """page_age 可能是 "3 days" 这类相对描述，只接受 ISO8601 形态。"""
    return len(value) >= 4 and value[:4].isdigit()


def _citation_snippets(blocks: list[dict[str, Any]]) -> dict[str, str]:
    """把 text 块 citations[] 里的 cited_text 按 url 建成片段表（首次出现优先）。"""
    snippets: dict[str, str] = {}
    for block in blocks:
        if block.get("type") != "text":
            continue
        for cite in block.get("citations") or []:
            url = cite.get("url") or ""
            text = cite.get("cited_text") or ""
            if url and text and url not in snippets:
                snippets[url] = text
    return snippets


def parse_search_response(data: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    """解析 Messages 响应，返回 (去重后的结果条目, 模型答案正文)。

    只认 web_search_tool_result 块里的 web_search_result，按 url 去重。
    """
    blocks = data.get("content") or []
    snippets = _citation_snippets(blocks)

    seen: set[str] = set()
    items: list[dict[str, Any]] = []
    for block in blocks:
        if block.get("type") != "web_search_tool_result":
            continue
        for item in block.get("content") or []:
            if item.get("type") != "web_search_result":
                continue
            url = item.get("url") or ""
            if not url or url in seen or _is_homepage(url):
                continue
            seen.add(url)
            items.append({
                "url": url,
                "title": (item.get("title") or "").strip(),
                "snippet": snippets.get(url, ""),
                "page_age": item.get("page_age") or "",
            })

    answer = "".join(
        block.get("text") or "" for block in blocks if block.get("type") == "text"
    )
    return items, answer


class DeepSeekSearchSource(ContentSource):
    """DeepSeek 原生联网搜索源。

    - search_trending: 按关键词触发服务端 web_search
    - get_trending: 用固定中文热点查询兜底（DeepSeek 无"榜单"概念）
    - 结果按 (time_range, query) 进程内缓存，TTL 内复用（每次搜索都是付费模型回合）
    """

    @property
    def name(self) -> str:
        return "deepseek"

    @property
    def billable(self) -> bool:
        return True

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.deepseek.com/anthropic/v1",
        model: str = "deepseek-v4-flash",
        max_uses: int = 1,
        max_tokens: int = 1024,
        cache_ttl: int = 300,
        timeout: float = 60.0,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.max_uses = max(1, max_uses)
        self.max_tokens = max(256, max_tokens)
        self.cache_ttl = max(0, cache_ttl)
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None
        # key=(time_range, query) → (到期 monotonic 时间戳, 全量结果)
        self._cache: dict[str, tuple[float, list[TrendingContent]]] = {}
        # 同 key 并发请求合并（定时监控与用户搜索可能同时打同一个关键词）
        self._locks: dict[str, asyncio.Lock] = {}

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            from capabilities.content_search.sources.base import (
                create_async_client,
                get_proxy_url,
            )

            proxy = get_proxy_url()
            self._client = await create_async_client(
                timeout=httpx.Timeout(self.timeout),
                proxy=proxy or None,
                trust_env=False,
            )
        return self._client

    def _build_payload(self, query: str) -> dict[str, Any]:
        return {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": [{
                "role": "user",
                "content": [{
                    "type": "text",
                    "text": f"Perform a web search for the query: {query}",
                }],
            }],
            "tools": [{
                "type": _WEB_SEARCH_TOOL,
                "name": "web_search",
                "max_uses": self.max_uses,
            }],
        }

    async def _call_messages(self, query: str) -> dict[str, Any]:
        """调 Anthropic 兼容 Messages 端点，429/5xx 重试 2 次。"""
        client = await self._ensure_client()
        headers = {
            "x-api-key": self.api_key,
            "authorization": f"Bearer {self.api_key}",
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        endpoint = f"{self.base_url}/messages"
        for attempt in range(3):
            try:
                resp = await client.post(endpoint, json=self._build_payload(query), headers=headers)
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPStatusError as e:
                status = e.response.status_code
                if status in (429, 500, 502, 503, 504) and attempt < 2:
                    wait = 2 ** attempt + 1
                    logger.warning(
                        f"[deepseek] HTTP {status} (attempt {attempt + 1}/3), retry in {wait}s"
                    )
                    await asyncio.sleep(wait)
                    continue
                logger.error(f"[deepseek] HTTP {status}: {e.response.text[:300]}")
                return {}
            except Exception as e:
                logger.error(f"[deepseek] search '{query}' failed: {e}")
                return {}
        return {}

    def _normalize(
        self, items: list[dict[str, Any]], answer: str
    ) -> list[TrendingContent]:
        results: list[TrendingContent] = []
        for idx, item in enumerate(items):
            url = item["url"]
            title = item["title"] or url
            snippet = item["snippet"]
            page_age = item["page_age"]
            results.append(TrendingContent(
                platform="deepseek",
                content_id=_content_id(url),
                title=title,
                summary=snippet[:200] if snippet else title[:200],
                content=snippet or title,
                author="",
                url=url,
                likes=max(0, 1000 - idx * 50),
                comments=0,
                shares=0,
                views=0,
                cover_img="",
                published_at=page_age if _iso_like(page_age) else "",
                tags=[],
                title_original=title,
                summary_original=snippet,
                content_original=snippet,
                raw={"rank": idx + 1, "page_age": page_age, "answer_len": len(answer)},
            ))
        return results

    async def _search_remote(self, query: str) -> list[TrendingContent]:
        """真实发起一次搜索（不打缓存），返回全量结果。"""
        data = await self._call_messages(query)
        if not data:
            return []

        items, answer = parse_search_response(data)
        if not items:
            logger.warning(
                f"[deepseek] '{query}': 响应无 web_search_tool_result 块，"
                f"原生搜索未触发（stop_reason={data.get('stop_reason')}）"
            )
            return []

        usage = data.get("usage") or {}
        logger.info(
            f"[deepseek] '{query}': {len(items)} sources, "
            f"in={usage.get('input_tokens')} out={usage.get('output_tokens')} "
            f"search_requests={(usage.get('server_tool_use') or {}).get('web_search_requests')}"
        )
        return self._normalize(items, answer)

    def _store(self, key: str, results: list[TrendingContent]) -> None:
        """写入缓存，超容量时淘汰最早插入的一条。"""
        if self.cache_ttl <= 0 or not results:
            return
        if len(self._cache) >= _CACHE_MAX_ENTRIES:
            oldest = next(iter(self._cache), None)
            if oldest is not None:
                self._cache.pop(oldest, None)
                self._locks.pop(oldest, None)
        self._cache[key] = (time.monotonic() + self.cache_ttl, results)

    async def _search_cached(self, query: str, time_range: str) -> list[TrendingContent]:
        """带 TTL 缓存 + 同 key 并发合并的搜索。

        空结果不写缓存：一次网络抖动不应让该关键词在 TTL 内持续无数据。
        """
        if self.cache_ttl <= 0:
            return await self._search_remote(query)

        key = f"{time_range}|{query}"
        now = time.monotonic()
        hit = self._cache.get(key)
        if hit is not None and now < hit[0]:
            logger.info(
                f"[deepseek] cache hit '{query}' "
                f"({len(hit[1])} items, {int(hit[0] - now)}s left)"
            )
            return [replace(r) for r in hit[1]]

        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock

        async with lock:
            # 双重检查：等锁期间可能已被同 key 的另一个请求填充
            now = time.monotonic()
            hit = self._cache.get(key)
            if hit is not None and now < hit[0]:
                logger.info(f"[deepseek] cache hit after wait '{query}'")
                return [replace(r) for r in hit[1]]

            results = await self._search_remote(query)
            self._store(key, results)
            return [replace(r) for r in results]

    async def search_trending(
        self,
        keyword: str,
        limit: int = 20,
        time_range: str = "week",
    ) -> list[TrendingContent]:
        query = keyword.strip() or _DEFAULT_QUERY
        results = await self._search_cached(query, time_range)
        return results[:limit]

    async def get_trending(
        self,
        category: str = "",
        limit: int = 20,
    ) -> list[TrendingContent]:
        return await self.search_trending(category or _DEFAULT_QUERY, limit)

    def clear_cache(self) -> None:
        """清空缓存（测试或运维手动失效用）。"""
        self._cache.clear()
        self._locks.clear()

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        self.clear_cache()