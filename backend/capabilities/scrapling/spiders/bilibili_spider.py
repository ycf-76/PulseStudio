"""B站 Spider：爬取用户主页全部投稿数据。

B站 API 相对公开，用轻量 HTTP（httpx）即可，无需浏览器。
投稿列表接口 x/space/wbi/arc/search 需要 WBI 签名，否则返回 -352 风控校验失败。
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
import urllib.parse
from collections.abc import AsyncIterator
from typing import Any

import httpx

from capabilities.scrapling.spiders.base import BasePlatformSpider

logger = logging.getLogger(__name__)

BILIBILI_HOME = "https://www.bilibili.com/"
BILIBILI_API_NAV = "https://api.bilibili.com/x/web-interface/nav"
BILIBILI_API_FINGER_SPI = "https://api.bilibili.com/x/frontend/finger/spi"
BILIBILI_API_SPACE = "https://api.bilibili.com/x/space/wbi/arc/search"
BILIBILI_API_VIDEO_INFO = "https://api.bilibili.com/x/web-interface/view"
BILIBILI_USER_HOME = "https://space.bilibili.com/{uid}"

UA_BILIBILI = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# WBI 签名用的重排表（bilibili-API-collect 公开算法，取 img_key+sub_key 后按此表重排取前 32 位）
WBI_MIXIN_KEY_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
]

# WBI 签名要求剔除的特殊字符
_WBI_ILLEGAL_CHARS = "!'()*"


class BilibiliSpider(BasePlatformSpider):
    """B站用户投稿 Spider。

    优先使用 B站公开 API（无需登录），API 失败时降级为页面爬取。
    """

    name = "bilibili_user_videos"
    platform = "bilibili"

    concurrent_requests = 4
    request_delay = 1.0

    _blocked_reason: str = ""

    def start(self) -> list[dict[str, Any]]:
        """爬取B站用户主页全部投稿。"""
        if not self.platform_uid:
            logger.warning("BilibiliSpider: platform_uid is empty, cannot crawl")
            return []

        items = self._crawl_via_api()
        if items:
            return items

        logger.info("BilibiliSpider: API crawl returned empty, falling back to page crawl")
        items = self._crawl_via_page()
        if not items and self._blocked_reason:
            raise RuntimeError(self._blocked_reason)
        return items

    async def parse(self, response: Any) -> AsyncIterator[dict[str, Any]]:
        """Scrapling Spider ABC 要求的回调实现。

        Spider 基类把 parse() 声明为抽象方法，子类不实现则无法实例化。
        本 Spider 由 spider_worker 直接调用 start()（同步直连 API 链路），
        不经过 CrawlerEngine 的 start_requests/parse 调度，
        因此这里不产出任何 item，仅为满足抽象基类契约。
        """
        return
        yield {}

    def _crawl_via_api(self) -> list[dict[str, Any]]:
        """通过 B站公开 API 爬取（投稿列表接口需要 WBI 签名）。"""
        items: list[dict[str, Any]] = []

        headers = {
            "User-Agent": UA_BILIBILI,
            "Referer": f"https://space.bilibili.com/{self.platform_uid}",
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Origin": "https://space.bilibili.com",
        }

        with httpx.Client(headers=headers, timeout=30.0, follow_redirects=True) as client:
            for ck_name, ck_val in (self.cookies or {}).items():
                value = ck_val.get("value", "") if isinstance(ck_val, dict) else str(ck_val)
                if not value:
                    continue
                client.cookies.set(ck_name, value, domain=".bilibili.com", path="/")

            try:
                client.get(BILIBILI_HOME)
            except Exception as e:
                logger.warning(f"BilibiliSpider: warm-up request failed: {e}")

            if not self._ensure_buvid(client):
                logger.warning("BilibiliSpider: buvid 指纹 cookie 获取失败，投稿接口可能被风控")

            items: list[dict[str, Any]] = []
            for attempt in range(4):
                mixin_key = self._fetch_wbi_mixin_key(client)
                if not mixin_key:
                    logger.warning("BilibiliSpider: WBI 签名密钥获取失败，无法调用投稿列表接口")
                    break

                items = self._fetch_all_pages(client, mixin_key)
                if items or not self._blocked_reason:
                    break
                if attempt == 3:
                    break

                delay = 2.0 + attempt * 2.0
                logger.info(
                    f"BilibiliSpider: 第 {attempt + 1} 轮被风控，{delay:.0f}s 后刷新指纹重试投稿接口"
                )
                time.sleep(delay)
                self._ensure_buvid(client, force=True)

            if not items:
                return []

            for i, item in enumerate(items):
                if i > 0 and i % 10 == 0:
                    time.sleep(self.request_delay)
                try:
                    detail = self._fetch_video_detail_api(item["note_id"], client)
                    if detail:
                        item.update(detail)
                except Exception as e:
                    logger.warning(f"BilibiliSpider: detail fetch failed for {item['note_id']}: {e}")

        return items

    def _fetch_all_pages(self, client: httpx.Client, mixin_key: str) -> list[dict[str, Any]]:
        """翻页拉取用户投稿列表。"""
        items: list[dict[str, Any]] = []
        pn = 1
        ps = 30
        self._blocked_reason = ""

        while True:
            params = {
                "mid": self.platform_uid,
                "pn": pn,
                "ps": ps,
                "order": "pubdate",
                "platform": "web",
                "web_location": "1550101",
            }

            try:
                resp = client.get(BILIBILI_API_SPACE, params=self._sign_wbi(params, mixin_key))
            except Exception as e:
                logger.warning(f"BilibiliSpider: API request failed (pn={pn}): {e}")
                break

            if resp.status_code == 412:
                self._blocked_reason = (
                    "B站接口风控拦截(412)，请稍后重试；若持续失败请重新扫码绑定B站账号"
                )
                logger.warning(f"BilibiliSpider: {self._blocked_reason}")
                break

            try:
                data = resp.json()
            except Exception as e:
                logger.warning(
                    f"BilibiliSpider: 响应解析失败 (pn={pn}) "
                    f"status={resp.status_code}: {e}"
                )
                break

            if data.get("code") != 0:
                logger.warning(
                    f"BilibiliSpider: API 异常 (pn={pn}) "
                    f"code={data.get('code')} message={data.get('message')}"
                )
                if data.get("code") == -352:
                    self._blocked_reason = (
                        "B站接口风控校验失败(-352)，请稍后重试；若持续失败请重新扫码绑定B站账号"
                    )
                    logger.warning(f"BilibiliSpider: {self._blocked_reason}")
                break

            vlist = data.get("data", {}).get("list", {}).get("vlist", [])
            if not vlist:
                break

            for v in vlist:
                item = {
                    "note_id": v.get("bvid", ""),
                    "title": v.get("title", ""),
                    "cover_img_url": self._normalize_url(v.get("pic", "")),
                    "platform": "bilibili",
                    "likes": v.get("like", 0),
                    "collects": v.get("favorite", 0),
                    "comments": v.get("comment", 0),
                    "shares": 0,
                    "tags": [],
                    "content_text": v.get("description", ""),
                    "image_urls": [],
                    "published_at": v.get("created", 0),
                }

                tag_str = v.get("tag", "")
                if tag_str:
                    item["tags"] = [t.strip() for t in tag_str.split(",") if t.strip()]

                items.append(item)

            if len(vlist) < ps:
                break

            pn += 1
            time.sleep(self.request_delay)

        logger.info(f"BilibiliSpider: API crawled {len(items)} videos")
        return items

    @staticmethod
    def _normalize_url(url: str) -> str:
        """B站图片地址常为协议相对形式（//i0.hdslb.com/...），补全 https 前缀。"""
        if not url:
            return ""
        if url.startswith("//"):
            return f"https:{url}"
        return url

    def _ensure_buvid(self, client: httpx.Client, force: bool = False) -> bool:
        """注入 buvid3/buvid4 设备指纹 cookie。

        B站投稿接口未登录时的 412/-352 风控主要依赖设备指纹校验，
        没有 buvid3 时即便 WBI 签名正确也会被拦截。
        指纹由 /x/frontend/finger/spi 公开下发，无需登录即可获取。

        已登录账号沿用原设备指纹（避免触发异地登录风控）；
        未登录账号每次同步重新申请指纹——B站会对反复使用的匿名 buvid
        累积风控，复用旧指纹会持续 412。
        """
        if not force and client.cookies.get("SESSDATA"):
            return True

        try:
            resp = client.get(BILIBILI_API_FINGER_SPI)
            data = resp.json()
        except Exception as e:
            logger.warning(f"BilibiliSpider: buvid 指纹获取失败: {e}")
            return False

        payload = data.get("data") or {}
        buvid3 = payload.get("b_3") or ""
        buvid4 = payload.get("b_4") or ""
        if not buvid3:
            logger.warning(f"BilibiliSpider: buvid 接口未返回指纹 (code={data.get('code')})")
            return False

        for name, value in (("buvid3", buvid3), ("buvid4", buvid4)):
            if value:
                client.cookies.set(name, value, domain=".bilibili.com", path="/")
        client.cookies.set("b_nut", str(int(time.time())), domain=".bilibili.com", path="/")
        return True

    def _fetch_wbi_mixin_key(self, client: httpx.Client) -> str:
        """取 nav 接口的 img_key/sub_key，按重排表生成 WBI mixin_key。"""
        try:
            resp = client.get(BILIBILI_API_NAV)
            data = resp.json()
        except Exception as e:
            logger.warning(f"BilibiliSpider: nav request failed: {e}")
            return ""

        wbi_img = (data.get("data") or {}).get("wbi_img") or {}
        img_key = wbi_img.get("img_url", "").rsplit("/", 1)[-1].split(".")[0]
        sub_key = wbi_img.get("sub_url", "").rsplit("/", 1)[-1].split(".")[0]
        if not img_key or not sub_key:
            logger.warning(f"BilibiliSpider: nav 未返回 wbi_img (code={data.get('code')})")
            return ""

        raw = img_key + sub_key
        return "".join(raw[i] for i in WBI_MIXIN_KEY_TAB if i < len(raw))[:32]

    @staticmethod
    def _sign_wbi(params: dict[str, Any], mixin_key: str) -> dict[str, Any]:
        """WBI 签名：参数按 key 排序后拼接 query，md5(query + mixin_key) 即 w_rid。"""
        signed: dict[str, Any] = {k: v for k, v in params.items() if v is not None}
        signed["wts"] = int(time.time())
        query = urllib.parse.urlencode(
            [(k, re.sub(f"[{_WBI_ILLEGAL_CHARS}]", "", str(v))) for k, v in sorted(signed.items())]
        )
        signed["w_rid"] = hashlib.md5((query + mixin_key).encode()).hexdigest()
        return signed

    def _fetch_video_detail_api(
        self, bvid: str, client: httpx.Client
    ) -> dict[str, Any] | None:
        """通过 API 获取视频详情。"""
        try:
            resp = client.get(
                BILIBILI_API_VIDEO_INFO,
                params={"bvid": bvid},
                timeout=15.0,
            )
            data = resp.json()
        except Exception:
            return None

        stat = data.get("data", {}).get("stat", {})
        if not stat:
            return None

        return {
            "likes": stat.get("like", 0),
            "collects": stat.get("favorite", 0),
            "comments": stat.get("reply", 0),
            "shares": stat.get("share", 0),
        }

    def _fetch_rendered_page(self, url: str) -> Any:
        """用浏览器渲染主页（B站投稿列表由前端 JS 渲染，纯 HTTP 只能拿到空壳）。

        浏览器不可用时回落为普通 HTTP 请求，至少不中断降级链路。
        """
        cookie_list: list[dict[str, Any]] = []
        for ck_name, ck_val in (self.cookies or {}).items():
            value = ck_val.get("value", "") if isinstance(ck_val, dict) else str(ck_val)
            if not value:
                continue
            domain = ck_val.get("domain") if isinstance(ck_val, dict) else None
            cookie_list.append({
                "name": ck_name,
                "value": value,
                "domain": domain or ".bilibili.com",
                "path": "/",
            })

        try:
            from scrapling.fetchers import StealthyFetcher
        except ImportError:
            logger.error("Scrapling not installed")
            return None

        try:
            return StealthyFetcher.fetch(
                url,
                headless=True,
                network_idle=True,
                timeout=60000,
                load_dom=True,
                cookies=cookie_list or None,
            )
        except Exception as e:
            logger.warning(f"BilibiliSpider: 浏览器渲染失败，回落 HTTP 请求: {e}")

        try:
            from scrapling.fetchers import Fetcher
            return Fetcher.get(url, headers={"User-Agent": UA_BILIBILI}, timeout=30)
        except Exception as e:
            logger.error(f"BilibiliSpider: page fetch failed: {e}")
            return None

    def _crawl_via_page(self) -> list[dict[str, Any]]:
        """降级：通过浏览器渲染用户主页后爬取。"""
        home_url = BILIBILI_USER_HOME.format(uid=self.platform_uid)
        logger.info(f"BilibiliSpider: page crawl {home_url}")

        page = self._fetch_rendered_page(home_url)
        if page is None:
            return []

        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        links = page.css("a[href*='/video/BV']")

        for link in links:
            try:
                href = link.attrib.get("href", "") if hasattr(link, "attrib") else ""
                m = re.search(r"/video/(BV[\w]+)", href)
                if not m:
                    continue
                bvid = m.group(1)
                if bvid in seen:
                    continue
                seen.add(bvid)

                card = link
                for _ in range(3):
                    parent = getattr(card, "parent", None)
                    if parent is None or getattr(parent, "tag", "") in ("body", "html"):
                        break
                    card = parent

                title = (
                    self._safe_text(link, "a[title]", attr="title")
                    or self._safe_text(card, "[class*='title']")
                    or link.attrib.get("title", "")
                    or self._safe_text(link, "", attr="title")
                )

                cover = (
                    self._safe_text(card, "img", attr="src")
                    or self._safe_text(link, "img", attr="src")
                )

                items.append({
                    "note_id": bvid,
                    "title": title.strip(),
                    "cover_img_url": self._normalize_url(cover),
                    "platform": "bilibili",
                    "likes": 0,
                    "collects": 0,
                    "comments": 0,
                    "shares": 0,
                    "tags": [],
                    "content_text": "",
                    "image_urls": [],
                })
            except Exception as e:
                logger.warning(f"BilibiliSpider: card parse error: {e}")

        logger.info(f"BilibiliSpider: page crawl found {len(items)} videos")
        return items
