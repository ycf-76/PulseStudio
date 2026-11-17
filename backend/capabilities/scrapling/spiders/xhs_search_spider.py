"""小红书搜索 Spider：爬取指定关键词的爆款笔记。

复用 XhsSpider 的登录态注入、__INITIAL_STATE__ 解析、反检测等能力，
但 start_requests 指向搜索页而非用户主页。

使用方式：
  1. Spider ABC 调度（子进程运行）：
     spider = XhsSearchSpider(search_keyword="穿搭", search_limit=20)
     items = spider.start()

  2. 直接调用（供 XhsSpiderSource 异步桥接）：
     items = await XhsSearchSpider.run_search(keyword="穿搭", limit=20)

风控红线：
  - 搜索页风控比主页更严，autothrottle_start_delay=8s
  - 每次搜索最多 20 条（小红书搜索页最多翻 2 页）
  - 检测到验证码/登录页时立即停止
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote

from scrapling.spiders.request import Request
from scrapling.spiders.session import SessionManager

from capabilities.scrapling.spiders.base import BasePlatformSpider

logger = logging.getLogger(__name__)

XHS_SEARCH_URL = "https://www.xiaohongshu.com/search_result?keyword={keyword}&source=web_search"
XHS_HOMEPAGE = "https://www.xiaohongshu.com"

STORAGE_DIR = Path(__file__).resolve().parent.parent.parent.parent / "app" / "storage_states"


class XhsSearchSpider(BasePlatformSpider):
    """小红书搜索页 Spider。

    继承 BasePlatformSpider，使用：
      - AsyncStealthySession: 反检测浏览器
      - Adaptive: 自适应选择器
      - autothrottle: 自动限速（搜索页更保守）

    与 XhsSpider 的区别：
      - start_requests → 搜索页 URL（非用户主页）
      - parse → 解析搜索结果卡片（非用户笔记列表）
      - 不需要 platform_uid
      - 需要 search_keyword + search_limit
    """

    name = "xhs_search"
    platform = "xiaohongshu"
    allowed_domains = {"xiaohongshu.com", "www.xiaohongshu.com"}

    concurrent_requests = 1
    autothrottle_start_delay = 8.0
    autothrottle_max_delay = 25.0

    async def on_close(self) -> None:
        """搜索 spider 不需要更新同步状态（与用户主页同步不同）。"""
        pass

    async def on_scraped_item(self, item: dict) -> dict | None:
        """搜索 spider 不实时入库（结果由子进程 stdout 返回给调用方处理）。"""
        return item

    def __init__(
        self,
        search_keyword: str = "",
        search_limit: int = 20,
        **kwargs,
    ):
        self.search_keyword = search_keyword
        self.search_limit = min(search_limit, 20)
        super().__init__(**kwargs)

    def configure_sessions(self, manager: SessionManager) -> None:
        """配置 AsyncStealthySession，加载登录态。

        完全复用 XhsSpider 的登录态注入逻辑。
        """
        from scrapling.engines._browsers._stealth import AsyncStealthySession

        STORAGE_DIR.mkdir(parents=True, exist_ok=True)
        storage_path = STORAGE_DIR / "xiaohongshu.json"

        cookie_list: list[dict] = []

        if storage_path.exists():
            cookie_list = self._load_cookies_from_storage_state(storage_path)
            if cookie_list:
                cleaned: list[dict] = []
                for c in cookie_list:
                    if c.get("expires", 0) < 0:
                        del c["expires"]
                    if not c.get("domain"):
                        continue
                    c.pop("url", None)
                    if not c.get("path"):
                        c["path"] = "/"
                    cleaned.append(c)
                cookie_list = cleaned
                self.logger.info(
                    f"XhsSearchSpider: {len(cookie_list)} cookies loaded from storageState"
                )
            else:
                self.logger.warning("XhsSearchSpider: storageState exists but no cookies found")
        else:
            self.logger.warning("XhsSearchSpider: no storageState file, trying DB cookies")
            if self.cookies:
                cookie_list = self._build_cookies_from_db()
                if cookie_list:
                    self.logger.info(f"XhsSearchSpider: {len(cookie_list)} cookies loaded from DB")

        kwargs_sess: dict[str, Any] = {
            "headless": True,
            "network_idle": True,
            "google_search": False,
            "wait": 5000,
        }

        if cookie_list:
            _cookies = cookie_list

            async def _inject_cookies(page: Any) -> None:
                try:
                    await page.context.add_cookies(_cookies)
                except Exception as e:
                    import logging

                    logging.getLogger("xhs_search").warning(f"page_setup add_cookies failed: {e}")

            kwargs_sess["page_setup"] = _inject_cookies
            kwargs_sess["cookies"] = cookie_list

        manager.add("stealthy", AsyncStealthySession(**kwargs_sess), default=True)

    async def start_requests(self):
        """生成初始请求：小红书搜索页。"""
        if not self.search_keyword:
            self.logger.error("XhsSearchSpider: search_keyword 为空，无法搜索")
            return

        encoded_kw = quote(self.search_keyword, safe="")
        url = XHS_SEARCH_URL.format(keyword=encoded_kw)
        self.logger.info(f"XhsSearchSpider: start_requests -> {url}")
        yield Request(
            url=url,
            callback=self.parse,
            selector_config={"adaptive": True},
        )

    async def parse(self, response: Any):
        """解析搜索结果页，提取爆款笔记卡片。

        策略1（优先）：从 __INITIAL_STATE__ 提取搜索结果笔记列表
        策略2（兜底）：CSS 选择器匹配笔记卡片

        即使检测到登录弹窗，仍先尝试提取 __INITIAL_STATE__，
        因为 SPA 可能在弹窗出现前已渲染数据。
        """
        if self._is_captcha_page(response):
            self.logger.error("XhsSearchSpider: 检测到验证码页面！搜索频率过高，被风控拦截。")
            return

        is_login = self._is_login_page(response)
        if is_login:
            self.logger.warning("XhsSearchSpider: 检测到登录弹窗，仍尝试提取 __INITIAL_STATE__...")

        seen_ids: set[str] = set()
        notes_from_state = self._extract_search_notes_from_initial_state(response)
        if notes_from_state:
            self.logger.info(
                f"XhsSearchSpider: found {len(notes_from_state)} notes from __INITIAL_STATE__"
            )
            for item in notes_from_state:
                note_id = item.get("note_id", "")
                if not note_id or note_id in seen_ids:
                    continue
                seen_ids.add(note_id)
                if len(seen_ids) > self.search_limit:
                    break

                yield {
                    "note_id": note_id,
                    "title": item.get("title", ""),
                    "content_text": item.get("content_text", ""),
                    "cover_img_url": item.get("cover_img_url", ""),
                    "image_urls": [item["cover_img_url"]] if item.get("cover_img_url") else [],
                    "tags": item.get("tags", []),
                    "platform": "xiaohongshu",
                    "likes": item.get("likes", 0),
                    "collects": item.get("collects", 0),
                    "comments": item.get("comments", 0),
                    "shares": 0,
                    "video_url": "",
                    "url": item.get("note_url", ""),
                    "author": item.get("author", ""),
                }

        if not notes_from_state:
            cards = self._parse_search_cards_from_dom(response)
            self.logger.info(f"XhsSearchSpider: found {len(cards)} note cards from DOM")
            for item in cards:
                note_id = item.get("note_id", "")
                if not note_id or note_id in seen_ids:
                    continue
                seen_ids.add(note_id)
                if len(seen_ids) > self.search_limit:
                    break

                yield {
                    "note_id": note_id,
                    "title": item.get("title", ""),
                    "content_text": "",
                    "cover_img_url": item.get("cover_img_url", ""),
                    "image_urls": [item["cover_img_url"]] if item.get("cover_img_url") else [],
                    "tags": [],
                    "platform": "xiaohongshu",
                    "likes": item.get("likes", 0),
                    "collects": 0,
                    "comments": 0,
                    "shares": 0,
                    "video_url": "",
                    "url": item.get("note_url", ""),
                    "author": item.get("author", ""),
                }

        if not seen_ids:
            if is_login:
                self.logger.error(
                    "XhsSearchSpider: 登录态已失效，搜索页弹出登录弹窗且无数据。"
                    "请重新扫码登录小红书以刷新 cookies。"
                )
            else:
                self.logger.warning(
                    "XhsSearchSpider: 搜索结果页未找到任何笔记！"
                    "可能原因：1) cookies过期 2) 风控验证码 3) 关键词无结果 4) 页面结构变更"
                )

    def _extract_search_notes_from_initial_state(self, response: Any) -> list[dict]:
        """从 __INITIAL_STATE__ 提取搜索结果笔记列表。

        搜索页 __INITIAL_STATE__ 结构：
          - search.notes: 搜索结果笔记列表（主要）
          - feed.notes: 备选路径
        每条笔记包含：id/noteId, title/displayTitle, cover, interactInfo, noteCard 等
        """
        state = self._extract_initial_state(response)
        if not state:
            self.logger.debug("XhsSearchSpider: __INITIAL_STATE__ not found or empty")
            return []

        state_keys = list(state.keys())[:10]
        self.logger.debug(f"XhsSearchSpider: __INITIAL_STATE__ keys: {state_keys}")

        notes: list[dict] = []
        try:
            raw_notes: list[dict] = []

            search_data = state.get("search", {})
            if isinstance(search_data, dict):
                search_notes = search_data.get("notes", [])
                if isinstance(search_notes, list):
                    if search_notes and isinstance(search_notes[0], list):
                        for sub in search_notes:
                            if isinstance(sub, list):
                                raw_notes.extend(sub)
                    else:
                        raw_notes.extend(search_notes)

            if not raw_notes:
                feed_data = state.get("feed", {})
                if isinstance(feed_data, dict):
                    feed_notes = feed_data.get("notes", [])
                    if isinstance(feed_notes, list):
                        if feed_notes and isinstance(feed_notes[0], list):
                            for sub in feed_notes:
                                if isinstance(sub, list):
                                    raw_notes.extend(sub)
                        else:
                            raw_notes.extend(feed_notes)

            for n in raw_notes:
                if not isinstance(n, dict):
                    continue

                card = n.get("noteCard", {})
                source = card if card else n

                nid = source.get("id", source.get("noteId", ""))
                if not nid:
                    nid = n.get("id", n.get("noteId", ""))
                if not nid:
                    continue

                interact = source.get("interactInfo", {})
                if not interact:
                    interact = n.get("interactInfo", {})
                likes = self._parse_count(str(interact.get("likedCount", "0")))
                collects = self._parse_count(str(interact.get("collectedCount", "0")))
                comments = self._parse_count(str(interact.get("commentCount", "0")))

                title = source.get("title", "") or source.get("displayTitle", "")
                if not title:
                    title = n.get("title", "") or n.get("displayTitle", "")

                cover_img_url = ""
                cover = source.get("cover", {})
                if not cover or not isinstance(cover, dict):
                    cover = n.get("cover", {})
                if isinstance(cover, dict):
                    cover_img_url = cover.get("url", "") or cover.get("urlDefault", "")
                    if not cover_img_url:
                        info_list = cover.get("infoList", [])
                        if info_list:
                            best = max(
                                info_list, key=lambda x: x.get("width", 0) * x.get("height", 0)
                            )
                            cover_img_url = best.get("url", "")
                elif isinstance(cover, str) and cover:
                    cover_img_url = cover

                if cover_img_url and cover_img_url.startswith("//"):
                    cover_img_url = f"https:{cover_img_url}"
                if cover_img_url and not cover_img_url.startswith(("http://", "https://")):
                    cover_img_url = f"https://{cover_img_url}"

                desc = source.get("desc", "") or n.get("desc", "")

                author = ""
                user_info = source.get("user", {}) or n.get("user", {})
                if isinstance(user_info, dict):
                    author = user_info.get("nickname", "") or user_info.get("nickName", "")

                tags: list[str] = []
                tag_list = source.get("tagList", []) or n.get("tagList", [])
                if isinstance(tag_list, list):
                    for t in tag_list:
                        name = t.get("name", "") if isinstance(t, dict) else str(t)
                        if name:
                            tags.append(name)

                note_item = {
                    "note_id": nid,
                    "note_url": f"https://www.xiaohongshu.com/explore/{nid}",
                    "title": title,
                    "cover_img_url": cover_img_url,
                    "content_text": desc,
                    "likes": likes,
                    "collects": collects,
                    "comments": comments,
                    "author": author,
                    "tags": tags,
                }
                notes.append(note_item)
        except Exception as e:
            self.logger.debug(
                f"XhsSearchSpider: _extract_search_notes_from_initial_state error: {e}"
            )
        return notes

    def _parse_search_cards_from_dom(self, response: Any) -> list[dict]:
        """CSS 选择器兜底：从 DOM 解析搜索结果卡片。

        搜索页卡片结构（2024-2025）：
          .note-item / [class*='note-item'] / section.note-item
            a[href*='/explore/'] 或 a[href*='/search_result/']
            img (封面)
            [class*='like'] / [class*='count'] (点赞)
        """
        cards: list[dict] = []
        try:
            note_els = response.css(
                "[class*='note-item'], section.note-item, [class*='feeds-item'], [class*='search-item']",
                auto_save=True,
            )
            if not note_els:
                note_els = response.css("a[href*='/explore/'], a[href*='/note/']", auto_save=True)
                seen_parents: set[int] = set()
                unique_parents = []
                for el in note_els:
                    parent = getattr(el, "parent", None)
                    pid = id(parent) if parent else 0
                    if pid not in seen_parents:
                        seen_parents.add(pid)
                        unique_parents.append(el)
                note_els = unique_parents

            for el in note_els:
                try:
                    link_els = el.css("a[href*='/explore/'], a[href*='/note/']")
                    if not link_els:
                        if hasattr(el, "attrib") and el.attrib.get("href", ""):
                            link_els = [el]
                    if not link_els:
                        continue

                    link = link_els[0]
                    href = link.attrib.get("href", "") if hasattr(link, "attrib") else ""
                    if not href:
                        continue
                    if href.startswith("/"):
                        href = f"https://www.xiaohongshu.com{href}"

                    note_id = self._extract_note_id(href)
                    if not note_id:
                        continue

                    title = ""
                    title_els = el.css("[class*='title'], [class*='name'], [class*='desc']")
                    if title_els:
                        title = (title_els[0].text or "").strip()

                    cover_img_url = ""
                    img_els = el.css("img")
                    if img_els:
                        img = img_els[0]
                        if hasattr(img, "attrib"):
                            cover_img_url = img.attrib.get("src", "") or img.attrib.get(
                                "data-src", ""
                            )
                    if cover_img_url and cover_img_url.startswith("//"):
                        cover_img_url = f"https:{cover_img_url}"

                    like_text = self._safe_text(
                        el, "[class*='like'] span, [class*='count'], [class*='likeCount']"
                    )
                    likes = self._parse_count(like_text)

                    author = ""
                    author_els = el.css("[class*='author'], [class*='nickname'], [class*='name'] a")
                    if author_els:
                        author = (author_els[0].text or "").strip()

                    cards.append(
                        {
                            "note_id": note_id,
                            "note_url": href,
                            "title": title,
                            "cover_img_url": cover_img_url,
                            "likes": likes,
                            "author": author,
                        }
                    )
                except Exception as e:
                    self.logger.debug(f"XhsSearchSpider: error parsing card: {e}")
                    continue
        except Exception as e:
            self.logger.debug(f"XhsSearchSpider: _parse_search_cards_from_dom error: {e}")
        return cards

    def _is_captcha_page(self, response: Any) -> bool:
        """检测是否为验证码/风控页面。"""
        try:
            url = ""
            if hasattr(response, "url"):
                url = str(response.url)
            if "captcha" in url.lower() or "verify" in url.lower():
                return True

            captcha_els = response.css(
                "[class*='captcha'], [class*='verify'], [class*='slider'], "
                "[class*='geetest'], [id*='captcha'], [id*='verify']"
            )
            if captcha_els and len(captcha_els) >= 1:
                return True

            title_els = response.css("title")
            if title_els:
                title_text = (
                    (title_els[0].text or "").lower() if hasattr(title_els[0], "text") else ""
                )
                if "验证" in title_text or "verify" in title_text or "captcha" in title_text:
                    return True
        except Exception:
            pass
        return False

    def _extract_initial_state(self, response: Any) -> dict | None:
        """从 <script> 标签中提取 __INITIAL_STATE__。

        复用 XhsSpider 的完整解析逻辑（含 JS 构造函数清理）。
        """
        try:
            scripts = response.css("script")
            for s in scripts:
                txt = s.text if hasattr(s, "text") else ""
                if not txt or "__INITIAL_STATE__" not in txt:
                    continue
                idx = txt.find("__INITIAL_STATE__")
                if idx < 0:
                    continue
                eq_idx = txt.find("=", idx)
                if eq_idx < 0:
                    continue
                json_start = eq_idx + 1
                while json_start < len(txt) and txt[json_start] in " \t":
                    json_start += 1
                if json_start >= len(txt) or txt[json_start] != "{":
                    continue
                depth = 0
                json_end = json_start
                in_str = False
                escape = False
                for i in range(json_start, len(txt)):
                    ch = txt[i]
                    if escape:
                        escape = False
                        continue
                    if ch == "\\":
                        escape = True
                        continue
                    if ch == '"' and not in_str:
                        in_str = True
                        continue
                    if ch == '"' and in_str:
                        in_str = False
                        continue
                    if in_str:
                        continue
                    if ch == "{":
                        depth += 1
                    elif ch == "}":
                        depth -= 1
                        if depth == 0:
                            json_end = i + 1
                            break
                if depth != 0:
                    continue
                raw = txt[json_start:json_end]
                raw = re.sub(r"\bundefined\b", "null", raw)
                raw = re.sub(r"\bNaN\b", "null", raw)
                raw = re.sub(r"\bInfinity\b", '"Infinity"', raw)
                raw = re.sub(r"\b-Infinity\b", '"-Infinity"', raw)
                raw = self._strip_js_constructors(raw)
                try:
                    state = json.loads(raw)
                    if isinstance(state, dict):
                        return state
                except json.JSONDecodeError:
                    continue
        except Exception as e:
            self.logger.debug(f"XhsSearchSpider: _extract_initial_state error: {e}")
        return None

    @staticmethod
    def _strip_js_constructors(raw: str) -> str:
        """移除 JS 构造函数表达式（new Set/Map/RegExp/Date 等）。"""

        def _find_matching_paren(s: str, start: int) -> int:
            depth = 0
            in_str = False
            esc = False
            for i in range(start, len(s)):
                ch = s[i]
                if esc:
                    esc = False
                    continue
                if ch == "\\":
                    esc = True
                    continue
                if ch == '"' and not in_str:
                    in_str = True
                    continue
                if ch == '"' and in_str:
                    in_str = False
                    continue
                if in_str:
                    continue
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                    if depth == 0:
                        return i
            return -1

        patterns = [
            (r"new\s+Set\(", "[]"),
            (r"new\s+Map\(", "{}"),
            (r"new\s+WeakSet\(", "[]"),
            (r"new\s+WeakMap\(", "{}"),
            (r"new\s+RegExp\(", "null"),
            (r"new\s+Date\(", "null"),
        ]
        for pat, replacement in patterns:
            offset = 0
            result = raw
            while True:
                m = re.search(pat, result[offset:])
                if not m:
                    break
                abs_start = offset + m.start()
                paren_start = abs_start + m.end() - m.start() - 1
                paren_end = _find_matching_paren(result, paren_start)
                if paren_end < 0:
                    offset = abs_start + m.end()
                    continue
                result = result[:abs_start] + replacement + result[paren_end + 1 :]
                offset = abs_start + len(replacement)
            raw = result
        return raw

    def _load_cookies_from_storage_state(self, storage_path: Path) -> list[dict]:
        """从 Playwright storageState JSON 提取 cookies。"""
        if not storage_path.exists():
            return []
        try:
            state = json.loads(storage_path.read_text(encoding="utf-8"))
            cookie_list = []
            for c in state.get("cookies", []):
                sc: dict[str, Any] = {
                    "name": c.get("name", ""),
                    "value": c.get("value", ""),
                }
                for key in ("domain", "path", "url", "expires", "httpOnly", "secure", "sameSite"):
                    if c.get(key) is not None:
                        sc[key] = c[key]
                cookie_list.append(sc)
            return cookie_list
        except Exception as e:
            logger.warning(f"XhsSearchSpider: failed to parse storageState: {e}")
            return []

    def _build_cookies_from_db(self) -> list[dict]:
        """从数据库 cookies_json 构建 cookie 列表。"""
        cookie_list: list[dict] = []
        if isinstance(self.cookies, dict):
            for name, val in self.cookies.items():
                if isinstance(val, dict):
                    c: dict[str, Any] = {"name": name, "value": val.get("value", "")}
                    c["domain"] = val.get("domain", ".xiaohongshu.com")
                    c["path"] = val.get("path", "/")
                    cookie_list.append(c)
                else:
                    cookie_list.append(
                        {"name": name, "value": str(val), "domain": ".xiaohongshu.com", "path": "/"}
                    )
        elif isinstance(self.cookies, list):
            for c in self.cookies:
                if isinstance(c, dict) and c.get("name"):
                    item = {
                        "name": c["name"],
                        "value": c.get("value", ""),
                        "domain": ".xiaohongshu.com",
                        "path": "/",
                    }
                    if c.get("domain"):
                        item["domain"] = c["domain"]
                    if c.get("path"):
                        item["path"] = c["path"]
                    cookie_list.append(item)
        return cookie_list

    @staticmethod
    def _parse_count(text: str) -> int:
        """解析 '1.2万' → 12000 等。"""
        if not text:
            return 0
        text = text.strip()
        try:
            if "万" in text:
                return int(float(text.replace("万", "")) * 10000)
            if "w" in text.lower():
                return int(float(text.lower().replace("w", "")) * 10000)
            if "k" in text.lower():
                return int(float(text.lower().replace("k", "")) * 1000)
            return int(text)
        except (ValueError, TypeError):
            return 0

    @staticmethod
    def _extract_note_id(url: str) -> str:
        """从 URL 中提取 note_id。"""
        patterns = [
            r"xiaohongshu\.com/explore/([0-9a-fA-F]{24})",
            r"xiaohongshu\.com/discovery/item/([0-9a-fA-F]{24})",
            r"xiaohongshu\.com/note/([0-9a-fA-F]{24})",
            r"/note/([0-9a-fA-F]{24})",
        ]
        for pat in patterns:
            m = re.search(pat, url)
            if m:
                return m.group(1)
        return ""

    def _is_login_page(self, response: Any) -> bool:
        """检测当前页面是否为登录页。

        搜索页可能包含登录入口元素（如顶栏登录按钮），
        但这不代表页面是登录页。只有当页面主体是登录表单时才判定为登录页。
        """
        try:
            url = ""
            if hasattr(response, "url"):
                url = str(response.url)
            if "/login" in url or "/sign-in" in url:
                self.logger.info(f"XhsSearchSpider: login page detected by URL: {url}")
                return True
            title_text = ""
            title_els = response.css("title")
            if title_els:
                title_text = (
                    (title_els[0].text or "").lower() if hasattr(title_els[0], "text") else ""
                )
            if "登录" in title_text or "login" in title_text or "signin" in title_text:
                self.logger.info(f"XhsSearchSpider: login page detected by title: {title_text}")
                return True
            login_modal_els = response.css(
                "[class*='login-modal'], [class*='qrcode-container'], "
                "[class*='login-container'], [id*='login-modal']"
            )
            if login_modal_els and len(login_modal_els) >= 1:
                self.logger.info("XhsSearchSpider: login page detected by modal element")
                return True
        except Exception:
            pass
        return False

    @staticmethod
    def _safe_text(parent: Any, selectors: str) -> str:
        """Scrapling 自适应选择器提取文本。"""
        for sel in selectors.split(", "):
            try:
                els = parent.css(sel.strip())
                el = els[0] if els else None
                if el:
                    text = (el.text or "").strip()
                    if text:
                        return text
            except Exception:
                continue
        return ""

    @classmethod
    async def run_search(cls, keyword: str, limit: int = 20) -> list[dict]:
        """异步运行搜索（供 XhsSpiderSource 桥接调用）。

        在子进程中运行 Spider，通过 stdout JSON 行返回结果。
        """
        import asyncio
        import os
        import subprocess
        import sys

        spider_script = Path(__file__).resolve()
        backend_dir = str(spider_script.parents[3])

        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        pythonpath = env.get("PYTHONPATH", "")
        if backend_dir not in pythonpath.split(os.pathsep):
            env["PYTHONPATH"] = (
                f"{backend_dir}{os.pathsep}{pythonpath}" if pythonpath else backend_dir
            )

        cmd = [
            sys.executable,
            str(spider_script),
            keyword,
            str(limit),
        ]

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
            )
        except Exception as e:
            logger.error(f"XhsSearchSpider: failed to start subprocess: {e}")
            return []

        try:
            result_line = await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(None, proc.stdout.readline),
                timeout=120,
            )
        except asyncio.TimeoutError:
            if proc.poll() is None:
                proc.terminate()
            logger.warning("XhsSearchSpider: subprocess timed out")
            return []

        try:
            proc.wait(timeout=10)
        except Exception:
            pass

        if not result_line:
            return []

        raw = (
            result_line.decode("utf-8", errors="replace")
            if isinstance(result_line, bytes)
            else result_line
        ).strip()
        if not raw:
            return []

        try:
            result = json.loads(raw)
        except json.JSONDecodeError as e:
            logger.error(f"XhsSearchSpider: result JSON decode error: {e}")
            return []

        if result.get("error"):
            logger.error(f"XhsSearchSpider: subprocess error: {result['error']}")
            return []

        return result.get("items", [])


if __name__ == "__main__":
    """子进程入口：接收 keyword + limit 参数，运行搜索并输出 JSON 结果。"""
    import sys as _sys

    try:
        _sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    if len(_sys.argv) < 3:
        output = json.dumps(
            {"error": "用法: xhs_search_spider.py <keyword> <limit>"}, ensure_ascii=False
        )
        _sys.stdout.write(output + "\n")
        _sys.stdout.flush()
        _sys.exit(1)

    _keyword = _sys.argv[1]
    _limit = int(_sys.argv[2])

    try:
        spider = XhsSearchSpider(search_keyword=_keyword, search_limit=_limit)
        items = spider.start()

        serializable = []
        for item in items:
            d = {}
            for k, v in item.items():
                try:
                    json.dumps(v)
                    d[k] = v
                except Exception:
                    d[k] = str(v)
            serializable.append(d)

        _sys.stdout.write(
            json.dumps({"ok": True, "items": serializable}, ensure_ascii=False) + "\n"
        )
        _sys.stdout.flush()
    except Exception as e:
        _sys.stdout.write(json.dumps({"error": str(e)[:500]}, ensure_ascii=False) + "\n")
        _sys.stdout.flush()
