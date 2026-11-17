"""DeepSeek Search API 内容源（原生检索接口，不走模型回合）。

协议来源：DeepSeek-Harness 的 packages/web/web-deepseek-api（0.2.2-alpha.3）
- 检索端点：POST {base_url}/search，base_url 含 /v1/tools 前缀（默认 https://api.deepseek.com/v1/tools）
  请求：{"query": ..., "count": 1..50, "need_content": false}
  响应：{"results": [{"url", "title", "snippet", "summary"?, "date"?}]}，空数组也算成功
- 抓取端点：POST {base_url}/fetch，请求 {"url": ...}，响应 {"content": "<markdown>"}
  该接口不回报目标站状态码与最终 URL，HTTP 200 即视为抓取成功
- 鉴权：Authorization: Bearer $DEEPSEEK_API_KEY（账号登录态的 x-dsh-auth-token 是另一套，本项目用 API Key）

与 deepseek（Anthropic Messages + web_search 工具）的关键差异：
- 一次搜索 = 一次纯检索 HTTP 请求，不消耗模型 token（无需为降本做缓存，缓存只为省往返）
- date 是真实 ISO8601 时间戳，snippet/summary 每条都有（不依赖模型是否引用）
- count 是结果条数（1-50），不是"允许触发搜索的次数"

正文补全：检索结果的 summary 是服务端给的正文摘要，短条目（< fetch_min_chars）用 Fetch API
抓页面 Markdown 覆盖正文，让下游分析有足够材料；补全失败只记日志，不影响检索结果返回。

严格模式：results 不是数组、或任一条目缺 url/title/snippet，整包视为无效响应（与 harness 一致）。
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

_DEFAULT_QUERY = "今天 微博热搜 小红书 热门话题"
_MIN_COUNT = 1
_MAX_COUNT = 50
_RETRY_STATUS = (429, 500, 502, 503, 504)
_CACHE_MAX_ENTRIES = 128


def _is_homepage(url: str) -> bool:
    """站点首页/入口页不算内容（如 https://news.ifeng.com/?mid=xxx）。"""
    path = urlparse(url).path
    return path in ("", "/")


def _content_id(url: str) -> str:
    """按 URL 生成稳定指纹（不能用内建 hash：跨进程不稳定）。"""
    digest = hashlib.md5(url.encode("utf-8")).hexdigest()[:12]
    return f"deepseek_api_{digest}"


def _opt_str(value: Any) -> str:
    return value if isinstance(value, str) else ""


def parse_results(payload: Any) -> list[dict[str, str]] | None:
    """校验并归一化 Search API 响应。

    Args:
        payload: Search 端点返回的 JSON（不可信）

    Returns:
        归一化条目列表（url/title/snippet/summary/date/site_name/authority/rerank_score）；
        响应结构不合法时返回 None（由调用方记日志并返回空）
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        return None

    items: list[dict[str, str]] = []
    for raw in payload["results"]:
        if not isinstance(raw, dict):
            return None
        url = raw.get("url")
        title = raw.get("title")
        snippet = raw.get("snippet")
        if not isinstance(url, str) or not url:
            return None
        if not isinstance(title, str) or not isinstance(snippet, str):
            return None
        summary = raw.get("summary")
        date = raw.get("date")
        if summary is not None and not isinstance(summary, str):
            return None
        if date is not None and not isinstance(date, str):
            return None

        if _is_homepage(url):
            continue
        items.append(
            {
                "url": url,
                "title": title.strip(),
                "snippet": snippet,
                "summary": summary or "",
                "date": date or "",
                "site_name": _opt_str(raw.get("site_name")),
                "authority": str(raw.get("authority") or ""),
                "rerank_score": str(raw.get("rerank_score") or ""),
            }
        )
    return items


def parse_fetch(payload: Any) -> str | None:
    """校验并提取 Fetch 端点响应里的页面 Markdown。

    Args:
        payload: Fetch 端点返回的 JSON（不可信）

    Returns:
        页面 Markdown 文本；响应结构不合法或内容为空时返回 None
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("content"), str):
        return None
    content = payload["content"].strip()
    return content or None


class DeepSeekApiSource(ContentSource):
    """DeepSeek Search API 内容源。

    - search_trending: 按关键词直接检索（零模型 token），短正文条目用 Fetch API 补全
    - get_trending: 用固定中文热点查询兜底（该接口无"榜单"概念）
    - 进程内 TTL 缓存：单次检索只是一次 HTTP 请求，缓存只为省掉重复网络往返
    """

    @property
    def name(self) -> str:
        return "deepseek_api"

    @property
    def billable(self) -> bool:
        return True

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.deepseek.com/v1/tools",
        count: int = 20,
        timeout: float = 30.0,
        max_retries: int = 2,
        cache_ttl: int = 600,
        fetch_enabled: bool = True,
        fetch_min_chars: int = 200,
        fetch_max_urls: int = 5,
        fetch_max_chars: int = 4000,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.count = min(max(count, _MIN_COUNT), _MAX_COUNT)
        self.timeout = timeout
        self.max_retries = max(0, max_retries)
        self.cache_ttl = max(0, cache_ttl)
        self.fetch_enabled = fetch_enabled
        self.fetch_min_chars = max(0, fetch_min_chars)
        self.fetch_max_urls = max(0, fetch_max_urls)
        self.fetch_max_chars = max(1, fetch_max_chars)
        self._client: httpx.AsyncClient | None = None
        self._cache: dict[str, tuple[float, list[TrendingContent]]] = {}
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

    def _headers(self) -> dict[str, str]:
        return {
            "authorization": f"Bearer {self.api_key}",
            "content-type": "application/json",
            "accept": "application/json",
        }

    def _build_payload(self, query: str, count: int) -> dict[str, Any]:
        return {"query": query, "count": count, "need_content": False}

    async def _post_json(
        self,
        endpoint: str,
        payload: dict[str, Any],
        what: str,
    ) -> dict[str, Any] | None:
        """POST 到指定端点，429/5xx 指数退避重试；401 不重试（Key 无效重试无意义）。"""
        client = await self._ensure_client()
        headers = self._headers()

        for attempt in range(self.max_retries + 1):
            try:
                resp = await client.post(endpoint, json=payload, headers=headers)
                if resp.status_code == 200:
                    return resp.json()
                if resp.status_code in _RETRY_STATUS and attempt < self.max_retries:
                    wait = 2**attempt + 1
                    logger.warning(
                        f"[deepseek_api] HTTP {resp.status_code} "
                        f"(attempt {attempt + 1}/{self.max_retries + 1}), retry in {wait}s"
                    )
                    await asyncio.sleep(wait)
                    continue
                logger.error(
                    f"[deepseek_api] HTTP {resp.status_code} from {endpoint}: " f"{resp.text[:300]}"
                )
                return None
            except Exception as e:
                logger.error(f"[deepseek_api] {what} failed: {e}")
                return None
        return None

    async def _call_search(self, query: str, count: int) -> dict[str, Any] | None:
        return await self._post_json(
            f"{self.base_url}/search",
            self._build_payload(query, count),
            f"search '{query}'",
        )

    async def _call_fetch(self, url: str) -> str | None:
        """调 Fetch 端点抓页面 Markdown；失败返回 None（不阻断检索结果）。"""
        payload = await self._post_json(
            f"{self.base_url}/fetch",
            {"url": url},
            f"fetch '{url}'",
        )
        if payload is None:
            return None
        content = parse_fetch(payload)
        if content is None:
            logger.warning(f"[deepseek_api] fetch '{url}': 响应无可用 content，已丢弃")
        return content

    async def _enrich(self, results: list[TrendingContent]) -> list[TrendingContent]:
        """对正文过短的条目用 Fetch API 抓页面 Markdown 补全。

        - 只补前 fetch_max_urls 条（服务端 rerank 顺序），并发上限 3，单条失败不影响其他
        - 抓到的正文覆盖 content / content_original，raw["fetched"] 标记来源
        """
        if not self.fetch_enabled or self.fetch_max_urls <= 0:
            return results

        targets = [r for r in results if len(r.content) < self.fetch_min_chars][
            : self.fetch_max_urls
        ]
        if not targets:
            return results

        sem = asyncio.Semaphore(3)

        async def _one(item: TrendingContent) -> None:
            async with sem:
                markdown = await self._call_fetch(item.url)
            if not markdown:
                return
            body = markdown[: self.fetch_max_chars]
            # 只接受"比原正文更长"的抓取结果。Fetch 只回报 HTTP 200，不回报目标站真实状态，
            # 抓到的可能是反爬验证页/登录墙（如微信「环境异常」几十字），
            # 用它覆盖服务端上千字的摘要会让正文质量断崖式下降，故短于原文时保留原摘要
            if len(body) <= len(item.content):
                logger.debug(
                    f"[deepseek_api] fetch '{item.url}': 抓取正文 {len(body)} 字 "
                    f"不优于原摘要 {len(item.content)} 字，保留原摘要"
                )
                return
            item.content = body
            item.content_original = body
            item.raw["fetched"] = True

        await asyncio.gather(*(_one(item) for item in targets), return_exceptions=True)
        filled = sum(1 for item in targets if item.raw.get("fetched"))
        logger.info(f"[deepseek_api] fetch enrichment: {filled}/{len(targets)} pages filled")
        return results

    def _normalize(self, items: list[dict[str, str]]) -> list[TrendingContent]:
        """Search API 字段 → TrendingContent。

        summary 是 1000-2300 字的正文摘要（面向模型），snippet 是短摘录（面向展示），
        因此 summary 字段用 snippet、content 字段用 summary——正文比摘录更有分析价值。
        """
        results: list[TrendingContent] = []
        for idx, item in enumerate(items):
            url = item["url"]
            title = item["title"] or url
            snippet = item["snippet"]
            body = item["summary"]
            results.append(
                TrendingContent(
                    platform="deepseek_api",
                    content_id=_content_id(url),
                    title=title,
                    summary=(snippet or body or title)[:200],
                    content=body or snippet or title,
                    author=item["site_name"],
                    url=url,
                    likes=0,
                    comments=0,
                    shares=0,
                    views=0,
                    cover_img="",
                    published_at=item["date"],
                    tags=[],
                    title_original=title,
                    summary_original=snippet,
                    content_original=body,
                    raw={
                        "rank": idx + 1,
                        "site_name": item["site_name"],
                        "authority": item["authority"],
                        "rerank_score": item["rerank_score"],
                        "date": item["date"],
                    },
                )
            )
        return results

    async def _search_remote(self, query: str, count: int) -> list[TrendingContent]:
        """真实检索一次：Search API → 校验 → 归一化 → 短正文补全。"""
        data = await self._call_search(query, count)
        if data is None:
            return []

        items = parse_results(data)
        if items is None:
            logger.error(f"[deepseek_api] '{query}': 响应结构不合法，已丢弃")
            return []

        logger.info(f"[deepseek_api] '{query}': {len(items)} sources (count={count})")
        return await self._enrich(self._normalize(items))

    def _store(self, key: str, results: list[TrendingContent]) -> None:
        """写入缓存，超容量时淘汰最早插入的一条。空结果不缓存。"""
        if self.cache_ttl <= 0 or not results:
            return
        if len(self._cache) >= _CACHE_MAX_ENTRIES:
            oldest = next(iter(self._cache), None)
            if oldest is not None:
                self._cache.pop(oldest, None)
                self._locks.pop(oldest, None)
        self._cache[key] = (time.monotonic() + self.cache_ttl, results)

    async def _search_cached(self, query: str, count: int) -> list[TrendingContent]:
        """带 TTL 缓存 + 同 key 并发合并的检索。

        空结果不写缓存：一次网络抖动不应让该关键词在 TTL 内持续无数据。
        """
        if self.cache_ttl <= 0:
            return await self._search_remote(query, count)

        key = f"{count}|{query}"
        now = time.monotonic()
        hit = self._cache.get(key)
        if hit is not None and now < hit[0]:
            logger.info(
                f"[deepseek_api] cache hit '{query}' "
                f"({len(hit[1])} items, {int(hit[0] - now)}s left)"
            )
            return hit[1]

        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock

        async with lock:
            now = time.monotonic()
            hit = self._cache.get(key)
            if hit is not None and now < hit[0]:
                logger.info(f"[deepseek_api] cache hit after wait '{query}'")
                return hit[1]

            results = await self._search_remote(query, count)
            self._store(key, results)
            return results

    async def search_trending(
        self,
        keyword: str,
        limit: int = 20,
        time_range: str = "week",
    ) -> list[TrendingContent]:
        """按关键词检索。

        time_range 不参与请求：Search API 无时间过滤参数（与 Messages 版一致），
        真实发布时间已放在 published_at 里，由下游决定怎么用。
        """
        query = keyword.strip() or _DEFAULT_QUERY
        want = min(self.count, max(limit, _MIN_COUNT))
        results = await self._search_cached(query, want)
        return [replace(r) for r in results[:limit]]

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
