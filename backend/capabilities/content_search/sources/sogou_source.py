"""头条搜索内容源（免费，无需 API Key，国内直连，中文分词优秀）。

so.toutiao.com 是字节跳动旗下的搜索引擎，特点：
- 完全免费，无需 API Key
- 国内直连，无需代理
- 中文分词准确，搜索结果质量高
- 反爬宽松，服务器端请求即可正常获取结果
- 内容覆盖全网（包括头条号/微信公众号/知乎/B站等）
- 搜索结果自带封面图（toutiaoimg.com CDN）

与搜狗/Bing 的区别：
- 搜狗：反爬严格，连续搜索触发验证页面，不可靠
- Bing 中国版：服务器端请求时中文分词崩溃（拆成单字），不可用
- 头条搜索：反爬宽松 + 中文分词准确 + 国内直连，最可靠的免费方案

解析策略：
- 数据来源优先走页面自身使用的 /search/?format=json 接口（返回 dom 字段），
  命中率高于直接抓页面；接口失败时回退抓页面，并识别空壳页（无结果链接）后重试
- 用 /search/jump 链接的 jtoken 分组，每组 = 一条搜索结果
- 组内文本按块级标签先切行，再按「噪声 / 来源 / 断句残片」逐行分类
- 标题：优先取长度 6~50 的行（视频卡片的标题节点），否则取首个 6~120 的行
- 摘要：组内最长的正文行
- 链接：解出 jump 链接 url= 参数里的真实地址，并压到 1024 字符以内
- 封面图：从组附近 HTML 提取 img 标签
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import OrderedDict
from typing import Any
from urllib.parse import unquote, urlsplit, urlunsplit

import httpx

from capabilities.content_search.sources.base import ContentSource, TrendingContent
from capabilities.content_search.sources.translator import translate_batch

logger = logging.getLogger(__name__)

_TOUTIAO_SEARCH_URL = "https://so.toutiao.com/search"
_TOUTIAO_JSON_URL = "https://so.toutiao.com/search/"

_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)

_BROWSER_HEADERS = {
    "User-Agent": _BROWSER_UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Sec-Ch-Ua": '"Google Chrome";v="125", "Chromium";v="125", "Not.A/Brand";v="24"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

_RE_JUMP_LINK = re.compile(
    r'<a[^>]*href="(/search/jump\?[^"]*)"[^>]*>(.*?)</a>',
    re.DOTALL | re.IGNORECASE,
)
_RE_JUMP_TARGET = re.compile(r'[&?]url=([^&"]+)')
_RE_FRAGMENT_TEXT = re.compile(r"#:~:text=.*$")
_RE_JTOKEN = re.compile(r"jtoken=([a-f0-9]+)")
_RE_IMG_SRC = re.compile(r'<img[^>]+src="([^"]+)"', re.DOTALL | re.IGNORECASE)
_RE_TAG_STRIP = re.compile(r"<[^>]+>")
_RE_HTML_ENTITY = re.compile(r"&#(\d+);|&amp;|&lt;|&gt;|&quot;|&#x([0-9a-fA-F]+);")
_RE_NOISE = re.compile(
    r"^(\d+次播放|\d+:\d+$|\d{4}年\d+月\d+日$|.*网\d{4}年\d+月\d+日$|\d+月\d+日$)"
)
_RE_BLOCK_BREAK = re.compile(
    r"<(?:div|p|li|h[1-6]|br|tr|td|span)\b[^>]*>|</(?:div|p|li|h[1-6]|tr|td|span)>",
    re.IGNORECASE,
)
_RE_SOURCE_LINE = re.compile(r"^(?:\d{4}年\d+月\d+日|\d+月\d+日|\S{2,12}网)$")
_RE_BROKEN = re.compile(r"^(?:&|amp;|mp;|nbsp|lt;|gt;|quot;|#\d+;|。|，|、|\))")


def _fit_url_length(url: str, max_len: int = 1024) -> str:
    """把超长链接压到字段上限内。

    入库字段是 String(1024)，而头条部分结果给的是带一长串追踪参数的跳转链接
    （可超 3000 字符）。超限时保留 scheme://host/path，丢掉查询串：
    跳转域名本身仍可访问，好过整条记录写入失败。
    """
    if len(url) <= max_len:
        return url
    parts = urlsplit(url)
    short = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    return short[:max_len]


def _resolve_url(href: str) -> str:
    """从 /search/jump 链接里取出真实目标地址。

    jump 链接形如 /search/jump?aid=1455&jtoken=<超长>&url=<目标地址编码>&log=<超长>，
    整条链接可达 5000+ 字符，直接入库会超出 url 字段上限（1024）。
    真实目标地址就藏在 url= 参数里，解出来既短又可直接跳转。
    """
    m = _RE_JUMP_TARGET.search(_decode_html_entities(href))
    fallback = f"https://so.toutiao.com{_decode_html_entities(href)}"
    if not m:
        return _fit_url_length(fallback)
    target = unquote(m.group(1))
    target = _RE_FRAGMENT_TEXT.sub("", target)
    if target.startswith("//"):
        target = "https:" + target
    if not target.startswith("http"):
        return _fit_url_length(fallback)
    return _fit_url_length(target)


def _decode_html_entities(text: str) -> str:
    def _replace(m: re.Match) -> str:
        if m.group(1):
            return chr(int(m.group(1)))
        if m.group(2):
            return chr(int(m.group(2), 16))
        entity = m.group(0)
        return {"&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"'}.get(entity, entity)

    return _RE_HTML_ENTITY.sub(_replace, text)


def _strip_tag_lines(html: str) -> list[str]:
    """按块级标签切行后再去标签。

    视频卡片的 <a> 里，时长 / 标题 / 作者 / 日期 / 播放量是各自独立的节点，
    整体去标签会拼成「13:40露营装备太多了你知道咋选吗?南哥说车2023年5月11日6万次播放」，
    标题无法辨认。先按标签断行，再逐行判断，标题才能被正确挑出来。
    """
    text = _RE_BLOCK_BREAK.sub("\n", html)
    text = _decode_html_entities(_RE_TAG_STRIP.sub("", text))
    return [ln.strip() for ln in text.split("\n") if ln.strip()]


class SogouSource(ContentSource):
    """头条搜索内容源（免费，无需 API Key，国内直连）。

    - search_trending: 调 so.toutiao.com/search，按相关性排序
    - get_trending: 调 so.toutiao.com/search，热门话题搜索
    - 中文分词准确，搜索结果质量高
    - 反爬宽松，服务器端请求即可正常获取
    - 封面图：从搜索结果中提取 img 标签

    注意：虽然类名叫 SogouSource（历史原因），实际使用的是头条搜索。
    """

    @property
    def name(self) -> str:
        return "sogou"

    def __init__(
        self,
        timeout: float = 20.0,
        max_retries: int = 2,
        min_interval: float = 3.0,
    ) -> None:
        self.timeout = timeout
        self.max_retries = max_retries
        self.min_interval = min_interval
        self._client: httpx.AsyncClient | None = None
        self._last_request_time: float = 0

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            from capabilities.content_search.sources.base import (
                create_async_client,
                get_proxy_url,
            )
            proxy = get_proxy_url()
            self._client = await create_async_client(
                timeout=httpx.Timeout(self.timeout),
                headers=_BROWSER_HEADERS,
                follow_redirects=True,
                proxy=proxy or None,
                trust_env=False,
            )
        return self._client

    async def _translate_results(
        self, results: list[TrendingContent]
    ) -> list[TrendingContent]:
        if not results:
            return results
        client = await self._ensure_client()
        titles = [r.title for r in results]
        summaries = [r.summary for r in results]
        zh_titles, zh_summaries = await asyncio.gather(
            translate_batch(titles, target_lang="zh-CN", client=client),
            translate_batch(summaries, target_lang="zh-CN", client=client),
        )
        for r, zh_t, zh_s in zip(results, zh_titles, zh_summaries):
            r.title = zh_t
            r.summary = zh_s
        logger.info(f"[sogou] translated {len(results)} items (zh-CN)")
        return results

    def _parse_html(self, html: str) -> list[TrendingContent]:
        all_jumps = list(_RE_JUMP_LINK.finditer(html))
        if not all_jumps:
            return []

        groups: dict[str, list[dict[str, Any]]] = OrderedDict()
        for m in all_jumps:
            href = m.group(1)
            jt_match = _RE_JTOKEN.search(href)
            if not jt_match:
                continue
            jtoken = jt_match.group(1)
            if jtoken not in groups:
                groups[jtoken] = []
            lines = _strip_tag_lines(m.group(2))
            groups[jtoken].append({
                "lines": lines,
                "href": href,
                "pos": m.start(),
            })

        results: list[TrendingContent] = []
        idx = 0

        for items in groups.values():
            if not items:
                continue
            items.sort(key=lambda x: x["pos"])

            title = ""
            snippet = ""
            source = ""
            candidates: list[str] = []
            url = _resolve_url(items[0]['href'])

            for item in items:
                for text in item["lines"]:
                    if len(text) < 3:
                        continue

                    if _RE_NOISE.match(text):
                        if not source:
                            source = text
                        continue

                    if _RE_SOURCE_LINE.match(text):
                        if not source:
                            source = text
                        continue

                    if _RE_BROKEN.match(text):
                        continue

                    candidates.append(text)
                    if len(text) > 15 and len(text) > len(snippet):
                        snippet = text[:300]

            for cand in candidates:
                if 6 <= len(cand) <= 50:
                    title = cand
                    break
            if not title:
                for cand in candidates:
                    if 6 <= len(cand) < 120:
                        title = cand
                        break

            if not title:
                continue

            summary = snippet[:200] if snippet else title[:200]

            cover_img = ""
            pos_start = items[0]["pos"]
            pos_end = items[-1]["pos"] + 500
            block = html[max(0, pos_start - 500):min(len(html), pos_end)]
            img_match = _RE_IMG_SRC.search(block)
            if img_match:
                img_url = img_match.group(1).strip()
                if img_url.startswith("//"):
                    img_url = "https:" + img_url
                elif img_url.startswith("/"):
                    img_url = "https://so.toutiao.com" + img_url
                if img_url.startswith("http"):
                    cover_img = _fit_url_length(img_url)

            position = idx + 1
            likes = max(0, 1000 - position * 50)

            results.append(TrendingContent(
                platform="sogou",
                content_id=f"sogou_{idx}_{hash(url) & 0xFFFFFFFF}",
                title=title,
                summary=summary,
                content=snippet or summary,
                author=source,
                url=url,
                likes=likes,
                comments=0,
                shares=0,
                views=0,
                cover_img=cover_img,
                published_at="",
                tags=[],
                title_original=title,
                summary_original=summary,
                content_original=snippet or summary,
                raw={"position": position, "source": source},
            ))
            idx += 1

        return results

    async def _fetch_via_json(
        self,
        client: httpx.AsyncClient,
        query: str,
        count: int,
    ) -> str:
        """走页面自身用的 JSON 接口，直接拿结果片段的 HTML。

        头条搜索结果页有两种返回形态：
        - 完整页（约 2MB，内含结果 DOM）
        - 空壳页（约 1.4MB，DOM 全靠前端二次请求渲染，服务端拿不到任何结果）
        空壳页会随机出现，只解析完整页会导致抓取时通时不通。

        页面 JS 自己就是用 /search/?format=json 拉数据再渲染的，
        这个接口稳定返回 dom 字段，命中率远高于直接抓页面。
        """
        headers = dict(_BROWSER_HEADERS)
        headers.update({
            "Accept": "application/json, text/plain, */*",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
        })
        params: dict[str, Any] = {
            "keyword": query,
            "format": "json",
            "count": count,
            "offset": 0,
            "pd": "synthesis",
            "from": "search_tab",
            "source": "search_tab",
            "search_json": "{}",
        }
        try:
            resp = await client.get(_TOUTIAO_JSON_URL, params=params, headers=headers)
            resp.raise_for_status()
            payload = resp.json()
        except Exception as e:
            logger.warning(f"[sogou] JSON 接口不可用: {e}")
            return ""
        dom = payload.get("dom") or ""
        if _RE_JUMP_LINK.search(dom):
            return dom
        logger.warning(f"[sogou] JSON 接口返回的 dom 无结果链接（{len(dom)} 字符）")
        return ""

    async def _call_toutiao(
        self,
        query: str,
        limit: int = 20,
        page: int = 0,
    ) -> str:
        client = await self._ensure_client()

        if not query.strip():
            query = "今日热点"

        now = time.monotonic()
        elapsed = now - self._last_request_time
        if elapsed < self.min_interval:
            await asyncio.sleep(self.min_interval - elapsed)
        self._last_request_time = time.monotonic()

        count = max(10, min(limit, 30))
        dom = await self._fetch_via_json(client, query, count)
        if dom:
            return dom

        params: dict[str, Any] = {
            "keyword": query,
        }

        for attempt in range(self.max_retries + 1):
            try:
                resp = await client.get(_TOUTIAO_SEARCH_URL, params=params)
                resp.raise_for_status()
                text = resp.text
                if _RE_JUMP_LINK.search(text):
                    return text
                if attempt < self.max_retries:
                    wait = 2 ** attempt + 1
                    logger.warning(
                        f"[sogou] 页面未包含结果链接（疑似反爬空壳页，{len(text)} 字符），"
                        f"{wait}s 后重试 {attempt+1}/{self.max_retries}"
                    )
                    await asyncio.sleep(wait)
                    continue
                logger.error(
                    f"[sogou] 反爬空壳页持续返回（{len(text)} 字符），本次抓取放弃"
                )
                return ""
            except httpx.HTTPStatusError as e:
                status_code = e.response.status_code
                if status_code == 429 and attempt < self.max_retries:
                    wait = 2 ** attempt + 1
                    logger.warning(
                        f"[sogou] 429 rate limited (attempt {attempt+1}/{self.max_retries+1}), "
                        f"retrying in {wait}s"
                    )
                    await asyncio.sleep(wait)
                    continue
                logger.error(f"[sogou] HTTP {status_code}: {e.response.text[:300]}")
                return ""
            except Exception as e:
                logger.error(f"[sogou] search failed: {e}")
                return ""
        return ""

    async def search_trending(
        self,
        keyword: str,
        limit: int = 20,
        time_range: str = "week",
    ) -> list[TrendingContent]:
        html = await self._call_toutiao(keyword, limit)
        if not html:
            return []

        results = self._parse_html(html)
        logger.info(f"[sogou] search '{keyword}': got {len(results)} results")

        sliced = results[:limit]
        translated = await self._translate_results(sliced)
        return translated

    async def get_trending(
        self,
        category: str = "",
        limit: int = 20,
    ) -> list[TrendingContent]:
        query = category if category else "今日热点"
        return await self.search_trending(query, limit)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        logger.info("[sogou] closed")