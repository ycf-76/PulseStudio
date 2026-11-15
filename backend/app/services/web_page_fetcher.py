"""通用网页抓取与正文抽取。

平台正则只用于「识别已知平台」的路由提示，不是数据获取的准入条件。
任何 http(s) 链接都能走通这条通用路径，返回标题/正文/图片，
不依赖 bs4/lxml 等额外依赖（只用标准库 html.parser 做轻量正文定位）。
"""
from __future__ import annotations

import asyncio
import logging
import re
from html.parser import HTMLParser
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 20.0
RENDER_HARD_TIMEOUT = 60.0
MAX_TEXT_CHARS = 8000
MAX_COLLECT_CHARS = 5000
MAX_IMAGES = 9
MIN_TEXT_CHARS = 120
MIN_BODY_FALLBACK_CHARS = 40
MAX_BODY_CHARS = 3 * 1024 * 1024

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

_VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img",
    "input", "link", "meta", "param", "source", "track", "wbr",
}

_SKIP_TAGS = {
    "script", "style", "noscript", "svg", "iframe", "canvas", "template",
    "head", "nav", "header", "footer", "aside", "form",
    "button", "select", "textarea", "label", "input",
}

_BLOCK_TAGS = {
    "p", "div", "section", "article", "main", "body", "blockquote",
    "h1", "h2", "h3", "h4", "h5", "h6", "li", "ul", "ol", "tr", "td", "br",
}

_TITLE_META_KEYS = ("og:title", "twitter:title", "title")
_AUTHOR_META_KEYS = ("author", "article:author", "og:article:author")
_TIME_META_KEYS = ("article:published_time", "datepublished", "pubdate", "og:updated_time")


class _Node:
    """极简节点：只记录标签、属性和有序子项，用于正文定位。"""

    __slots__ = ("tag", "attrs", "items", "parent", "total", "link")

    def __init__(self, tag: str, attrs: dict[str, str], parent: "_Node | None") -> None:
        self.tag = tag
        self.attrs = attrs
        self.items: list[tuple[str, Any]] = []
        self.parent = parent
        self.total = 0
        self.link = 0


class _TreeBuilder(HTMLParser):
    """把 HTML 解析成一棵轻量树，同时收集 meta / 图片 / title 文本。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("#root", {}, None)
        self._stack: list[_Node] = [self.root]
        self.meta: dict[str, str] = {}
        self.images: list[str] = []
        self.title_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k.lower(): (v or "") for k, v in attrs}
        node = _Node(tag, a, self._stack[-1])
        self._stack[-1].items.append(("node", node))

        if tag == "meta":
            key = a.get("property") or a.get("name") or a.get("itemprop")
            content = a.get("content", "").strip()
            if key and content:
                self.meta.setdefault(key.lower(), content)
        elif tag == "img":
            src = a.get("src") or a.get("data-src") or a.get("data-original") or ""
            if src.startswith(("http://", "https://")):
                self.images.append(src)

        if tag not in _VOID_TAGS:
            self._stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in _VOID_TAGS:
            self._stack.pop()

    def handle_endtag(self, tag: str) -> None:
        for i in range(len(self._stack) - 1, 0, -1):
            if self._stack[i].tag == tag:
                del self._stack[i:]
                return

    def handle_data(self, data: str) -> None:
        if not data.strip():
            return
        self._stack[-1].items.append(("text", data.replace("\xa0", " ")))
        if self._stack[-1].tag == "title":
            self.title_parts.append(data)


def _measure(node: _Node) -> tuple[int, int]:
    """自底向上计算 (全文字符数, 链接内字符数)。"""
    if node.tag in _SKIP_TAGS:
        node.total, node.link = 0, 0
        return 0, 0

    total = 0
    link = 0
    for kind, value in node.items:
        if kind == "text":
            total += len(value)
        else:
            c_total, c_link = _measure(value)
            total += c_total
            link += c_link
    if node.tag == "a":
        link = total
    node.total, node.link = total, link
    return total, link


def _depth(node: _Node) -> int:
    depth = 0
    cur = node.parent
    while cur is not None:
        depth += 1
        cur = cur.parent
    return depth


def _select_body(root: _Node) -> _Node:
    """选正文容器：正文多、链接文字少的节点优先；得分接近时取更深的那层。"""
    best = root
    stack = [root]
    while stack:
        node = stack.pop()
        if node.tag in _SKIP_TAGS:
            continue
        score = node.total - node.link
        best_score = best.total - best.link
        if score > best_score or (score > 0 and score >= best_score * 0.95 and _depth(node) > _depth(best)):
            best = node
        for kind, value in node.items:
            if kind == "node":
                stack.append(value)
    return best


def _render(node: _Node, parts: list[str]) -> None:
    if node.tag in _SKIP_TAGS:
        return
    for kind, value in node.items:
        if kind == "text":
            parts.append(value)
        else:
            _render(value, parts)
            if value.tag in _BLOCK_TAGS:
                parts.append("\n")


def _normalize_text(raw: str) -> str:
    raw = re.sub(r"[ \t\u3000]+", " ", raw)
    lines = [ln.strip() for ln in raw.split("\n")]
    return "\n".join(ln for ln in lines if ln)


def extract_web_content(html: str, url: str = "") -> dict[str, Any]:
    """从 HTML 中抽取标题/正文/作者/图片。纯函数，便于单测。"""
    builder = _TreeBuilder()
    try:
        builder.feed(html)
        builder.close()
    except Exception as e:
        logger.warning(f"[web_page_fetcher] HTML 解析异常 url={url}: {e}")

    _measure(builder.root)
    body = _select_body(builder.root)
    parts: list[str] = []
    _render(body, parts)
    text = _normalize_text("".join(parts))

    if len(text) < MIN_BODY_FALLBACK_CHARS and body is not builder.root:
        # 正文容器几乎抽不出东西时才退回整页，避免误把导航/页脚当成正文
        parts = []
        _render(builder.root, parts)
        text = _normalize_text("".join(parts))

    title = ""
    for key in _TITLE_META_KEYS:
        if builder.meta.get(key):
            title = builder.meta[key]
            break
    if not title:
        title = _normalize_text("".join(builder.title_parts))

    author = ""
    for key in _AUTHOR_META_KEYS:
        if builder.meta.get(key):
            author = builder.meta[key]
            break

    published_at = ""
    for key in _TIME_META_KEYS:
        if builder.meta.get(key):
            published_at = builder.meta[key]
            break

    images: list[str] = []
    og_image = builder.meta.get("og:image", "")
    if og_image.startswith(("http://", "https://")):
        images.append(og_image)
    images.extend(builder.images)
    images = list(dict.fromkeys(images))[:MAX_IMAGES]

    return {
        "url": url,
        "title": title.strip()[:200],
        "content_text": text[:MAX_TEXT_CHARS],
        "text_length": len(text),
        "author": author.strip()[:100],
        "site_name": builder.meta.get("og:site_name", "").strip()[:100],
        "published_at": published_at.strip()[:40],
        "image_urls": images,
    }


def http_client_kwargs() -> dict[str, Any]:
    """返回 httpx 客户端的代理参数（全站抓取统一使用）。

    默认关闭 trust_env：Windows 上 trust_env=True 会让 httpx 通过注册表拿到系统代理
    （如 127.0.0.1:7897），该代理未运行时所有公网请求都抛 ConnectError，
    表现为「公开网页一条都抓不到」。只有显式配置 HTTP_PROXY=system 时才交回 httpx 探测。
    """
    try:
        from app.config import get_settings
        configured = (get_settings().http_proxy or "").strip()
    except Exception:
        configured = ""

    if configured == "system":
        return {"proxy": None, "trust_env": True}
    return {"proxy": configured or None, "trust_env": False}


async def _get(url: str, *, timeout: float, headers: dict[str, str]) -> httpx.Response:
    """发起 GET：显式代理失败时自动直连重试，避免代理没开就整体采集中断。

    SSL 上下文复用全局共享实例：httpx 默认会在事件循环线程里同步调用
    ssl.create_default_context()，在 Windows 上要读整本系统证书库（实测 3~11s），
    每抓一个页面都新建 client 会把整个事件循环卡住同样久。
    """
    from capabilities.content_search.sources.base import get_ssl_context

    verify = await get_ssl_context()
    kwargs = http_client_kwargs()
    try:
        async with httpx.AsyncClient(
            timeout=timeout, follow_redirects=True, headers=headers, verify=verify, **kwargs
        ) as client:
            return await client.get(url)
    except httpx.ConnectError:
        if not kwargs["proxy"]:
            raise
        logger.warning(f"[web_page_fetcher] 代理不可用，改为直连 url={url}")
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers=headers,
            verify=verify,
            proxy=None,
            trust_env=False,
        ) as client:
            return await client.get(url)


async def fetch_web_page(url: str, *, timeout: float = DEFAULT_TIMEOUT) -> dict[str, Any] | None:
    """抓取任意公开网页并抽取正文。失败返回 None。"""
    headers = {
        "User-Agent": _UA,
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "application/json;q=0.9,text/plain;q=0.8,*/*;q=0.7"
        ),
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    try:
        resp = await _get(url, timeout=timeout, headers=headers)
    except Exception as e:
        logger.warning(f"[web_page_fetcher] 抓取失败 url={url}: {type(e).__name__} {e}")
        return None

    if resp.status_code >= 400:
        logger.warning(f"[web_page_fetcher] HTTP {resp.status_code} url={url}")
        return None

    content_type = (resp.headers.get("content-type") or "").lower()
    body = resp.text
    if len(body) > MAX_BODY_CHARS:
        logger.info(f"[web_page_fetcher] 页面过大，截断处理 url={url} size={len(body)}")
        body = body[:MAX_BODY_CHARS]

    final_url = str(resp.url)

    is_text_like = any(k in content_type for k in ("json", "text", "xml"))
    # 仅在服务端没给 content-type 时才靠首字符嗅探，避免把 PDF/图片当 HTML
    sniff_html = not content_type and body.lstrip()[:1] == "<"

    if "html" in content_type or sniff_html:
        result = extract_web_content(body, final_url)
    elif is_text_like:
        result = {
            "url": final_url,
            "title": "",
            "content_text": _normalize_text(body)[:MAX_TEXT_CHARS],
            "text_length": len(body),
            "author": "",
            "site_name": "",
            "published_at": "",
            "image_urls": [],
        }
    else:
        logger.info(f"[web_page_fetcher] 非文本内容，跳过 url={url} content_type={content_type}")
        return None

    result["content_type"] = content_type
    result["status"] = resp.status_code
    return result


async def fetch_web_page_rendered(url: str, *, timeout_ms: int = 30000) -> dict[str, Any] | None:
    """用无头浏览器渲染后再抽取正文，覆盖 JS 动态渲染的页面。

    Scrapling 未安装或渲染失败时返回 None，由调用方继续降级。
    整体带硬超时：浏览器启动本身可能卡住数分钟，不能拖住整条采集链。
    """
    try:
        from scrapling.fetchers import StealthyFetcher
    except ImportError:
        logger.debug("[web_page_fetcher] 未安装 scrapling，跳过浏览器渲染")
        return None

    def _render() -> Any:
        return StealthyFetcher.fetch(url, headless=True, network_idle=True, timeout=timeout_ms)

    try:
        page = await asyncio.wait_for(
            asyncio.get_event_loop().run_in_executor(None, _render),
            timeout=RENDER_HARD_TIMEOUT,
        )
    except asyncio.TimeoutError:
        logger.warning(f"[web_page_fetcher] 浏览器渲染超时({RENDER_HARD_TIMEOUT}s) url={url}")
        return None
    except Exception as e:
        logger.warning(f"[web_page_fetcher] 浏览器渲染失败 url={url}: {type(e).__name__} {e}")
        return None

    html = getattr(page, "html_content", None) or ""
    if not html:
        try:
            html = str(page)
        except Exception:
            html = ""
    if len(html) < 50:
        logger.warning(f"[web_page_fetcher] 渲染结果为空 url={url}")
        return None

    result = extract_web_content(html, url)
    result["content_type"] = "text/html"
    result["status"] = 200
    return result


async def collect_generic_page(url: str, platform: str = "web", *, render: bool = False) -> dict[str, Any] | None:
    """通用网页采集，输出与 work_collector 各平台采集一致的结构。

    render=True 时：HTTP 直取拿到的正文过短（JS 壳页面）才升级为浏览器渲染；
    连 HTTP 都拿不到页面（网络不可达/4xx/非文本）时不渲染——浏览器同样连不上，只会白等。
    返回结构的 collect_source 标记实际生效的采集方式。
    """
    page = await fetch_web_page(url)
    source = "web_page_parse"

    if render and page is not None and len(page["content_text"]) < MIN_TEXT_CHARS:
        logger.info(f"[web_page_fetcher] 正文过短，尝试浏览器渲染 url={url}")
        rendered = await fetch_web_page_rendered(url)
        if rendered:
            page, source = rendered, "web_browser_render"

    if not page:
        return None

    text = page["content_text"]
    if len(text) < MIN_TEXT_CHARS:
        logger.info(f"[web_page_fetcher] 正文过短({len(text)}字符)，判定为无效页面 url={url}")
        return None

    images = page["image_urls"]
    # unknown 只是路由信号，落库/返回时统一标记为 web，避免下游出现无意义的平台值
    platform_label = "web" if platform in ("", "auto", "unknown") else platform
    return {
        "note_id": page["url"],
        "title": page["title"] or page["url"],
        "content_text": text[:MAX_COLLECT_CHARS],
        "tags": [],
        "cover_img_url": images[0] if images else "",
        "image_urls": images,
        "likes": 0,
        "collects": 0,
        "comments": 0,
        "shares": 0,
        "author_fans": 1,
        "author_nickname": page["author"] or page["site_name"],
        "note_type": "article",
        "platform": platform_label,
        "source_url": page["url"],
        "collect_source": source,
    }