"""小红书 Spider：爬取用户主页全部笔记数据。

使用 Scrapling Spider ABC + AsyncStealthySession（反检测浏览器）+ Adaptive 自适应选择器。
登录态通过 storageState 文件传递（由 platform_login 扫码登录时保存）。
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from scrapling.spiders.request import Request
from scrapling.spiders.session import SessionManager

from capabilities.scrapling.spiders.base import BasePlatformSpider
from capabilities.scrapling.spiders.detail_refresh import (
    DetailCandidate,
    select_detail_targets,
)

logger = logging.getLogger(__name__)

XHS_USER_HOME = "https://www.xiaohongshu.com/user/profile/{uid}"
XHS_HOMEPAGE = "https://www.xiaohongshu.com"

STORAGE_DIR = Path(__file__).resolve().parent.parent.parent.parent / "app" / "storage_states"


class XhsSpider(BasePlatformSpider):
    """小红书用户笔记 Spider。

    继承 Scrapling Spider ABC，使用：
      - AsyncStealthySession: 异步反检测浏览器
      - Adaptive: 自适应选择器，网站改版自动容错
      - autothrottle: 自动限速

    Scrapling Spider ABC 调度流程：
      start() → __run() → CrawlerEngine.crawl()
        → async with session_manager:
            → configure_sessions(manager)  ← 配置 AsyncStealthySession
            → async for req in start_requests()  ← 必须是 async generator
            → await parse(response)  ← 解析页面，yield item 或 Request
            → await on_scraped_item(item)  ← 自动入库
    """

    name = "xhs_user_notes"
    platform = "xiaohongshu"
    allowed_domains = {"xiaohongshu.com", "www.xiaohongshu.com"}

    concurrent_requests = 1
    autothrottle_start_delay = 5.0
    autothrottle_max_delay = 20.0

    # 登录态失效标记：parse 检测到登录页时置 True，
    # spider_worker 据此把"静默成功"改成真实失败，避免同步后误报已完成。
    login_failed = False

    # 主页瀑布流滚动次数：首屏只渲染约 20 条笔记，且 __INITIAL_STATE__ 是首屏快照。
    # 不滚动的话，更早的笔记拿不到新的 xsec_token，也就无法刷新已过期（CDN 签名
    # 2 天失效）的图文图集。每次滚动约加载 20 条，6 次可覆盖 120 条左右。
    scroll_times = 6

    # 单轮详情页抓取预算（None → 读配置 spider_detail_budget）。
    # 详见 detail_refresh.py：不设上限时，图集仍是外链的笔记每轮都会全量重抓，
    # 跑满超时被强杀，整批结果丢失。
    detail_budget: int | None = None
    # True = 全量刷新（忽略冷却期与重试上限，用户点"全量同步"时由 worker 注入）
    force: bool = False
    # 本轮因预算被推迟到下轮的详情页条数（回报给父进程）
    detail_deferred: int = 0

    def configure_sessions(self, manager: SessionManager) -> None:
        """配置 AsyncStealthySession，加载登录态。

        策略：用 page_setup 在每次导航前通过 context.add_cookies 注入 cookies，
        比 session 级别的 cookies 参数更可靠。
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
                self.logger.info(f"XhsSpider: {len(cookie_list)} cookies loaded from storageState")
            else:
                self.logger.warning("XhsSpider: storageState exists but no cookies found")
        else:
            self.logger.warning("XhsSpider: no storageState file, trying DB cookies")
            if self.cookies:
                cookie_list = self._build_cookies_from_db()
                if cookie_list:
                    self.logger.info(f"XhsSpider: {len(cookie_list)} cookies loaded from DB")

        kwargs: dict[str, Any] = {
            "headless": True,
            "network_idle": True,
            "google_search": False,
            "wait": 3000,
        }

        if cookie_list:
            _cookies = cookie_list

            async def _inject_cookies(page: Any) -> None:
                try:
                    await page.context.add_cookies(_cookies)
                except Exception as e:
                    import logging
                    logging.getLogger("xhs_user_notes").warning(f"page_setup add_cookies failed: {e}")

            kwargs["page_setup"] = _inject_cookies
            kwargs["cookies"] = cookie_list

        manager.add("stealthy", AsyncStealthySession(**kwargs), default=True)

    async def start_requests(self):
        """生成初始请求：用户主页。

        Scrapling engine 用 `async for request in start_requests()` 调度，
        所以必须是 async generator（async def + yield）。
        """
        if not self.platform_uid:
            self.platform_uid = self._extract_uid_from_cookies()
            if self.platform_uid:
                logger.info(f"XhsSpider: extracted uid={self.platform_uid} from cookies")

        if not self.platform_uid:
            uid = await self._extract_uid_from_homepage_async()
            if uid:
                self.platform_uid = uid
                logger.info(f"XhsSpider: extracted uid={self.platform_uid} from homepage")

        if self.platform_uid and self.user_id:
            self._try_update_uid_in_db()

        if not self.platform_uid:
            logger.warning("XhsSpider: cannot get uid, cannot crawl")
            return

        home_url = XHS_USER_HOME.format(uid=self.platform_uid)
        logger.info(f"XhsSpider: start_requests -> {home_url}")
        yield Request(
            url=home_url,
            callback=self.parse,
            selector_config={"adaptive": True},
            page_action=self._scroll_home,
        )

    async def _scroll_home(self, page: Any) -> None:
        """滚动主页触发瀑布流加载，让首屏之外的笔记出现在 DOM 中。

        Scrapling 的 page_action 在导航完成后执行，拿到的是 playwright 的 page。
        __INITIAL_STATE__ 只是首屏快照，滚动后新增的卡片只存在于 DOM，
        所以后续 parse 必须同时从 DOM 采集链接（href 自带 xsec_token）。
        """
        for i in range(self.scroll_times):
            try:
                await page.mouse.wheel(0, 1600)
            except Exception:
                try:
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                except Exception:
                    return
            await page.wait_for_timeout(1500)
            self.logger.info(f"XhsSpider: scroll home {i + 1}/{self.scroll_times}")

    async def _load_cached_images(self) -> dict[str, list[str]]:
        """读取已入库笔记的图集，用于判断哪些笔记还需要重新拉详情页。"""
        if not self.user_id:
            return {}
        try:
            from capabilities.scrapling.spiders.db_sink import load_cached_images

            return await load_cached_images(self.user_id, self.platform)
        except Exception as e:
            self.logger.warning(f"XhsSpider: load cached images failed: {e}")
            return {}

    async def _load_detail_refresh_state(self) -> dict[str, Any]:
        """读取每条笔记的详情页刷新历史（冷却与重试上限用）。"""
        if not self.user_id:
            return {}
        try:
            from capabilities.scrapling.spiders.db_sink import load_detail_refresh_state

            return await load_detail_refresh_state(self.user_id, self.platform)
        except Exception as e:
            self.logger.warning(f"XhsSpider: load detail refresh state failed: {e}")
            return {}

    async def _record_detail_attempts(self, note_ids: list[str]) -> None:
        """发请求前登记本轮详情页抓取，保证冷却与重试计数生效。"""
        if not note_ids or not self.user_id:
            return
        try:
            from capabilities.scrapling.spiders.db_sink import record_detail_attempts

            await record_detail_attempts(self.user_id, self.platform, note_ids)
        except Exception as e:
            self.logger.warning(f"XhsSpider: record detail attempts failed: {e}")

    def _refresh_policy(self) -> tuple[float, int, int]:
        """返回 (冷却小时数, 重试上限, 单轮详情页预算)。"""
        from capabilities.scrapling.spiders.detail_refresh import (
            DEFAULT_BUDGET,
            DEFAULT_COOLDOWN_HOURS,
            DEFAULT_MAX_ATTEMPTS,
        )

        cooldown = DEFAULT_COOLDOWN_HOURS
        max_attempts = DEFAULT_MAX_ATTEMPTS
        budget = self.detail_budget if self.detail_budget is not None else DEFAULT_BUDGET
        try:
            from app.config import get_settings

            cfg = get_settings()
            cooldown = float(cfg.spider_detail_cooldown_hours)
            max_attempts = int(cfg.spider_detail_max_attempts)
            if self.detail_budget is None:
                budget = int(cfg.spider_detail_budget)
        except Exception as e:
            self.logger.warning(f"XhsSpider: load refresh policy failed, use defaults: {e}")
        return cooldown, max_attempts, int(budget)

    @staticmethod
    def _needs_detail_page(
        cached_images: list[str] | None,
        state: Any = None,
        *,
        cooldown_hours: float = 12.0,
        max_attempts: int = 3,
        force: bool = False,
    ) -> bool:
        """是否需要拉详情页。

        图集已全部落本地时无需再拉（本地文件不会过期）；图集是外链（CDN 签名过期）
        或还没有图集时才需要。冷却期与重试上限见 detail_refresh.needs_detail_refresh。
        """
        from capabilities.scrapling.spiders.detail_refresh import needs_detail_refresh

        return needs_detail_refresh(
            cached_images,
            state,
            cooldown_hours=cooldown_hours,
            max_attempts=max_attempts,
            force=force,
        )

    async def parse(self, response: Any):
        """解析用户主页，提取笔记列表。

        策略1（优先）：从 <script> 标签中提取 __INITIAL_STATE__ 的笔记列表，
          直接产出 item（卡片级数据：标题、封面、点赞等）。
          卡片里没有详情图集也没有视频直链，因此图集尚未落本地的笔记会额外
          yield 一个详情页 Request，由 parse_note 取回完整数据（含 imageList /
          video_url）后按 note_id 覆盖入库。详情页数据是图文笔记图集唯一来源：
          卡片只有封面一张，不抓详情页图集永远只剩一张封面（且 2 天后签名过期）。
          图集已全部落本地的笔记跳过详情页：本地文件不会过期，重复拉只会拖长同步时间。
        策略2（兜底）：CSS 选择器匹配 <a href="/explore/{note_id}">，
          请求详情页获取完整数据。
        """
        if self._is_login_page(response):
            self.login_failed = True
            self.logger.error(
                "XhsSpider: 检测到登录页！cookies 已过期，请重新扫码登录。"
                " 不会爬取任何数据。"
            )
            return

        seen_ids: set[str] = set()
        cached_images = await self._load_cached_images()
        refresh_states = await self._load_detail_refresh_state()
        cooldown_hours, max_attempts, budget = self._refresh_policy()
        candidates: list[DetailCandidate] = []

        notes_from_state = self._extract_notes_from_initial_state(response)
        if notes_from_state:
            self.logger.info(f"XhsSpider: found {len(notes_from_state)} notes from __INITIAL_STATE__")
            for item in notes_from_state:
                note_id = item.get("note_id", "")
                if not note_id or note_id in seen_ids:
                    continue
                seen_ids.add(note_id)

                yield {
                    "note_id": note_id,
                    "title": item.get("title", ""),
                    "content_text": item.get("content_text", ""),
                    "cover_img_url": item.get("cover_img_url", ""),
                    "image_urls": [item["cover_img_url"]] if item.get("cover_img_url") else [],
                    "tags": [],
                    "platform": "xiaohongshu",
                    "likes": item.get("likes", 0),
                    "collects": item.get("collects", 0),
                    "comments": item.get("comments", 0),
                    "shares": 0,
                    "video_url": "",
                    # 主页卡片只有封面一张，不是完整图集：入库时不能拿这一张
                    # 去覆盖库里已有的图集或过期外链，否则图集会被降级且再也补不齐
                    "from_card": True,
                }

                state = refresh_states.get(note_id)
                if not self._needs_detail_page(
                    cached_images.get(note_id),
                    state,
                    cooldown_hours=cooldown_hours,
                    max_attempts=max_attempts,
                    force=self.force,
                ):
                    continue
                candidates.append(
                    DetailCandidate(
                        note_id=note_id,
                        url=self._build_note_detail_url(item),
                        source="state",
                        attempts=state.attempts if state else 0,
                        last_attempt_at=state.last_attempt_at if state else None,
                        likes_hint=item.get("likes", 0),
                        from_card=True,
                    )
                )

        # DOM 兜底必须始终执行，不能只在 __INITIAL_STATE__ 为空时才跑：
        # 主页滚动后新增的卡片只存在于 DOM，__INITIAL_STATE__ 仍是首屏快照。
        # 两者按 note_id 合并，state 里的已在 seen_ids 中会被跳过，DOM 只补充
        # 首屏之外的笔记（其 href 自带新的 xsec_token，详情页才打得开）。
        note_links = response.css("a[href*='/explore/'], a[href*='/note/']", auto_save=True)
        self.logger.info(f"XhsSpider: found {len(note_links)} note links from DOM")

        for link in note_links:
            try:
                note_url = link.attrib.get("href", "") if hasattr(link, "attrib") else ""
                if not note_url:
                    continue
                if note_url.startswith("/"):
                    note_url = f"https://www.xiaohongshu.com{note_url}"

                note_id = self._extract_note_id(note_url)
                if not note_id or note_id in seen_ids:
                    continue
                seen_ids.add(note_id)

                state = refresh_states.get(note_id)
                if not self._needs_detail_page(
                    cached_images.get(note_id),
                    state,
                    cooldown_hours=cooldown_hours,
                    max_attempts=max_attempts,
                    force=self.force,
                ):
                    continue

                card = getattr(link, "parent", link)
                like_text = self._safe_text(card, "[class*='like'] span, [class*='count'], [class*='like']")
                likes = self._parse_count(like_text)

                candidates.append(
                    DetailCandidate(
                        note_id=note_id,
                        url=note_url,
                        source="dom",
                        attempts=state.attempts if state else 0,
                        last_attempt_at=state.last_attempt_at if state else None,
                        likes_hint=likes,
                        from_card=False,
                    )
                )

            except Exception as e:
                self.logger.warning(f"XhsSpider: error parsing note card: {e}")
                continue

        # 单轮只抓 budget 条：图集仍是外链的笔记会一直进入候选，
        # 不截断的话每轮都要全量重抓，跑满超时被强杀 → 整批结果丢失。
        targets, deferred = select_detail_targets(candidates, budget)
        # 未轮到的条数回报给父进程，用于告知"还要再同步几次才能刷完"
        self.detail_deferred = deferred
        if candidates:
            self.logger.info(
                f"XhsSpider: detail candidates={len(candidates)}, "
                f"budget={budget}, targets={len(targets)}, deferred={deferred}"
            )

        for target in targets:
            self.logger.info(
                f"XhsSpider: note {target.note_id} -> request detail page "
                f"(source={target.source}, attempts={target.attempts})"
            )
            yield Request(
                url=target.url,
                callback=self.parse_note,
                meta={
                    "note_id": target.note_id,
                    "from_card": target.from_card,
                    "likes_hint": target.likes_hint,
                },
                selector_config={"adaptive": True},
            )

        if not seen_ids:
            self.logger.warning(
                "XhsSpider: 用户主页未找到任何笔记！"
                "可能原因：1) cookies过期被重定向到登录页 2) 用户无笔记 3) 页面结构变更"
            )

    async def parse_note(self, response: Any):
        """解析笔记详情页，提取完整内容。

        策略1（优先）：从 <script> 标签提取 __INITIAL_STATE__.noteDetailMap
          - 包含完整数据：title, desc, imageList, interactInfo, tagList, video
        策略2（兜底）：CSS 选择器匹配 DOM 元素
        """
        meta = response.meta if hasattr(response, "meta") else {}
        note_id = meta.get("note_id", "")
        likes_hint = meta.get("likes_hint", 0)
        from_card = bool(meta.get("from_card"))

        if self._is_login_page(response):
            self.login_failed = True
            self.logger.error(f"XhsSpider: note {note_id} 检测到登录页，cookies 已过期，跳过")
            return

        # 在详情页真正被处理时登记尝试（次数 +1、时间=当前），
        # 成功/失败都算一次；子进程被超时强杀时这条不会被登记，
        # 下次同步会重试，避免"没轮到抓就被记成失败"。
        if note_id:
            await self._record_detail_attempts([note_id])

        state_data = self._extract_note_detail_from_initial_state(response, note_id)
        if not state_data and from_card:
            self.logger.warning(
                f"XhsSpider: note {note_id} 详情页未取到 __INITIAL_STATE__，"
                f"保留主页卡片数据，不产出低质量 item"
            )
            return
        if state_data:
            self.logger.info(
                f"XhsSpider: note {note_id} from __INITIAL_STATE__ -> "
                f"title={state_data.get('title', '')[:20]!r}, "
                f"imgs={len(state_data.get('image_urls', []))}, "
                f"likes={state_data.get('likes', 0)}"
            )
            yield {
                "note_id": note_id or state_data.get("note_id", ""),
                "title": state_data.get("title", ""),
                "content_text": state_data.get("content_text", ""),
                "cover_img_url": state_data.get("cover_img_url", ""),
                "image_urls": state_data.get("image_urls", []),
                "tags": state_data.get("tags", []),
                "platform": "xiaohongshu",
                "likes": state_data.get("likes", 0) or likes_hint,
                "collects": state_data.get("collects", 0),
                "comments": state_data.get("comments", 0),
                "shares": state_data.get("shares", 0),
                "video_url": state_data.get("video_url", ""),
                # 详情页的 imageList 是完整图集（单图笔记就是一张），可信
                "from_card": False,
                "detail_page": True,
            }
            return

        title = self._safe_text(response, "[class*='title'], .title, h1, [class*='note-title']")
        content_text = self._safe_text(response, "[class*='desc'], [class*='content'], .desc, [class*='note-content']")

        image_urls: list[str] = []
        try:
            img_selectors = [
                "[class*='swiper'] img",
                "[class*='slide'] img",
                "[class*='image'] img",
                "[class*='carousel'] img",
            ]
            for sel in img_selectors:
                imgs = response.css(sel, auto_save=True)
                for img in imgs:
                    src = ""
                    if hasattr(img, "attrib"):
                        src = img.attrib.get("src", "") or img.attrib.get("data-src", "")
                    if src and src.startswith("//"):
                        src = f"https:{src}"
                    if src and src.startswith("http") and src not in image_urls:
                        image_urls.append(src)
                if image_urls:
                    break
        except Exception as e:
            self.logger.debug(f"XhsSpider: error extracting images: {e}")

        tags: list[str] = []
        try:
            tag_els = response.css("[class*='tag'] a, [class*='tag'] span, a[href*='/search_result/']", auto_save=True)
            for t in tag_els:
                txt = (t.text or "").strip().lstrip("#")
                if txt and txt not in tags and len(txt) < 30:
                    tags.append(txt)
        except Exception:
            pass

        likes = self._parse_count(
            self._safe_text(response, "[class*='like-count'], [class*='like'] span, [class*='likeCount']")
        ) or likes_hint
        collects = self._parse_count(
            self._safe_text(response, "[class*='collect-count'], [class*='collect'] span, [class*='collectCount']")
        )
        comments = self._parse_count(
            self._safe_text(response, "[class*='comment-count'], [class*='comment'] span, [class*='commentCount']")
        )
        shares = self._parse_count(
            self._safe_text(response, "[class*='share-count'], [class*='share'] span, [class*='shareCount']")
        )

        cover_img_url = image_urls[0] if image_urls else ""

        if not title and not content_text and not image_urls:
            self.logger.warning(
                f"XhsSpider: note {note_id} __INITIAL_STATE__ 和 CSS 选择器均未提取到数据，"
                f"页面可能未正确加载或结构已变更"
            )
        else:
            self.logger.info(
                f"XhsSpider: note {note_id} (CSS fallback) -> title={title[:20]!r}, "
                f"imgs={len(image_urls)}, likes={likes}, collects={collects}, "
                f"comments={comments}, shares={shares}"
            )

        yield {
            "note_id": note_id,
            "title": title,
            "content_text": content_text,
            "cover_img_url": cover_img_url,
            "image_urls": image_urls,
            "tags": tags,
            "platform": "xiaohongshu",
            "likes": likes,
            "collects": collects,
            "comments": comments,
            "shares": shares,
            "video_url": "",
            "from_card": False,
            "detail_page": True,
        }

    @staticmethod
    def _strip_js_constructors(raw: str) -> str:
        """移除 JS 构造函数表达式，将其替换为 JSON 兼容值。

        处理 new Set([...]), new Map([...]), new Set(), new Map(),
        new WeakSet(), new WeakMap(), new RegExp(...), new Date(...) 等。
        使用括号匹配确保正确处理嵌套。
        """
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
                result = result[:abs_start] + replacement + result[paren_end + 1:]
                offset = abs_start + len(replacement)
            raw = result
        return raw

    def _extract_initial_state(self, response: Any) -> dict | None:
        """从 <script> 标签中提取 window.__INITIAL_STATE__ 的 JSON 数据。

        小红书 SPA 在 <script> 中内联了 __INITIAL_STATE__ 赋值语句，
        格式如：window.__INITIAL_STATE__={"noteDetailMap":{...},...}
        """
        try:
            scripts = response.css("script")
            self.logger.debug(f"XhsSpider: found {len(scripts)} script tags")
            found_state_in_text = 0
            for s in scripts:
                txt = s.text if hasattr(s, "text") else ""
                if not txt:
                    continue
                if "__INITIAL_STATE__" in txt:
                    found_state_in_text += 1
            if found_state_in_text == 0:
                self.logger.warning(
                    "XhsSpider: __INITIAL_STATE__ not found in any script.text! "
                    f"Total scripts: {len(scripts)}, "
                    f"scripts with text: {sum(1 for s in scripts if hasattr(s, 'text') and s.text)}"
                )
                page_text = str(response.css("body")[0].text)[:200] if response.css("body") else ""
                if "login" in page_text.lower():
                    self.logger.warning("XhsSpider: page body contains 'login' - likely redirected")
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
                    if json_start < len(txt) and txt[json_start:json_start+9] == "undefined":
                        try:
                            self.logger.debug("XhsSpider: __INITIAL_STATE__=undefined，页面数据未加载")
                        except Exception:
                            pass
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
                raw = re.sub(r'\bundefined\b', 'null', raw)
                raw = re.sub(r'\bNaN\b', 'null', raw)
                raw = re.sub(r'\bInfinity\b', '"Infinity"', raw)
                raw = re.sub(r'\b-Infinity\b', '"-Infinity"', raw)
                raw = self._strip_js_constructors(raw)
                try:
                    state = json.loads(raw)
                    if isinstance(state, dict):
                        return state
                except json.JSONDecodeError as je:
                    try:
                        self.logger.debug(f"XhsSpider: JSON parse failed after cleanup: {je}")
                    except Exception:
                        pass
                    continue
                try:
                    for m in re.finditer(r"JSON\.parse\(['\"](.+)['\"]\)", txt[idx:]):
                        raw_json = m.group(1)
                        if raw_json.endswith(")") :
                            raw_json = raw_json[:raw_json.rfind(")")]
                        raw_json = raw_json.replace('\\"', '"').replace("\\'", "'")
                        decoded = json.loads(raw_json)
                        if isinstance(decoded, dict):
                            return decoded
                except Exception:
                    continue
        except Exception as e:
            self.logger.debug(f"XhsSpider: _extract_initial_state error: {e}")
        return None

    def _extract_notes_from_initial_state(self, response: Any) -> list[dict]:
        """从 __INITIAL_STATE__ 提取用户主页的笔记列表。

        返回丰富数据：note_id, note_url, title, cover_img_url, likes, collects, comments, note_type 等。
        主页 __INITIAL_STATE__ 中 user.notes 已包含卡片级数据，
        足够在详情页被拦截时直接产出 item。
        """
        state = self._extract_initial_state(response)
        if not state:
            return []
        notes: list[dict] = []
        try:
            raw_notes = state.get("user", {}).get("notes", [])
            if not raw_notes:
                raw_notes = state.get("feed", {}).get("notes", [])

            user_notes: list[dict] = []
            if isinstance(raw_notes, list):
                if raw_notes and isinstance(raw_notes[0], list):
                    for sub in raw_notes:
                        if isinstance(sub, list):
                            user_notes.extend(sub)
                else:
                    user_notes = raw_notes

            for n in user_notes:
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
                note_type = source.get("type", "") or n.get("type", "")

                cover_img_url = ""
                cover = source.get("cover", {})
                if not cover or not isinstance(cover, dict):
                    cover = n.get("cover", {})
                if isinstance(cover, dict):
                    cover_img_url = cover.get("url", "") or cover.get("urlDefault", "")
                    if not cover_img_url:
                        info_list = cover.get("infoList", [])
                        if info_list:
                            best = max(info_list, key=lambda x: x.get("width", 0) * x.get("height", 0))
                            cover_img_url = best.get("url", "")
                elif isinstance(cover, str) and cover:
                    cover_img_url = cover

                if cover_img_url and cover_img_url.startswith("//"):
                    cover_img_url = f"https:{cover_img_url}"
                if cover_img_url and not cover_img_url.startswith(("http://", "https://")):
                    cover_img_url = f"https://{cover_img_url}"

                desc = source.get("desc", "") or n.get("desc", "")

                note_item = {
                    "note_id": nid,
                    "note_url": f"https://www.xiaohongshu.com/explore/{nid}",
                    "title": title,
                    "cover_img_url": cover_img_url,
                    "content_text": desc,
                    "likes": likes,
                    "collects": collects,
                    "comments": comments,
                    "note_type": note_type,
                    "xsec_token": source.get("xsecToken", "") or n.get("xsecToken", ""),
                }
                notes.append(note_item)
        except Exception as e:
            self.logger.debug(f"XhsSpider: _extract_notes_from_initial_state error: {e}")
        return notes

    def _extract_note_detail_from_initial_state(self, response: Any, note_id: str) -> dict | None:
        """从 __INITIAL_STATE__ 提取笔记详情页的完整数据。

        返回包含 title, content_text, image_urls, tags, likes, collects, comments, shares, video_url 的字典。
        """
        state = self._extract_initial_state(response)
        if not state:
            return None
        try:
            note_detail_map = state.get("noteDetailMap", {})
            if not note_detail_map:
                note_detail_map = state.get("note", {}).get("noteDetailMap", {})
            if not note_detail_map:
                return None
            detail_key = note_id if note_id in note_detail_map else next(iter(note_detail_map), None)
            if not detail_key:
                return None
            note_obj = note_detail_map[detail_key]
            if isinstance(note_obj, dict):
                note = note_obj.get("note", note_obj)
            else:
                return None
            if not isinstance(note, dict):
                return None

            title = note.get("title", "")
            content_text = note.get("desc", "")

            image_urls: list[str] = []
            for img in note.get("imageList", []):
                url = img.get("url", "") or img.get("urlDefault", "")
                if not url:
                    info_list = img.get("infoList", [])
                    if info_list:
                        best = max(info_list, key=lambda x: x.get("width", 0) * x.get("height", 0))
                        url = best.get("url", "")
                if url and url.startswith("//"):
                    url = f"https:{url}"
                if url and not url.startswith(("http://", "https://")):
                    url = f"https://{url}"
                if url and url not in image_urls:
                    image_urls.append(url)

            video_url = self._extract_video_url(note.get("video", {}))

            interact = note.get("interactInfo", {})
            likes = self._parse_count(str(interact.get("likedCount", "0")))
            collects = self._parse_count(str(interact.get("collectedCount", "0")))
            comments = self._parse_count(str(interact.get("commentCount", "0")))
            shares = self._parse_count(str(interact.get("shareCount", "0")))

            tags: list[str] = []
            for t in note.get("tagList", []):
                name = t.get("name", "") if isinstance(t, dict) else str(t)
                if name:
                    tags.append(name)

            cover_img_url = image_urls[0] if image_urls else ""
            actual_note_id = note.get("id", note_id)

            return {
                "note_id": actual_note_id,
                "title": title,
                "content_text": content_text,
                "cover_img_url": cover_img_url,
                "image_urls": image_urls,
                "tags": tags,
                "likes": likes,
                "collects": collects,
                "comments": comments,
                "shares": shares,
                "video_url": video_url,
            }
        except Exception as e:
            self.logger.debug(f"XhsSpider: _extract_note_detail_from_initial_state error: {e}")
            return None

    @staticmethod
    def _build_note_detail_url(item: dict) -> str:
        """拼笔记详情页 URL。

        实测：不带 xsec_token 访问 /explore/{id} 时 noteDetailMap 为空，
        拿不到视频数据，所以必须带上主页卡片里的 xsecToken。
        """
        note_id = item.get("note_id", "")
        url = f"{XHS_HOMEPAGE}/explore/{note_id}"
        token = item.get("xsec_token", "")
        if token:
            url = f"{url}?xsec_token={token}&xsec_source=pc_user"
        return url

    @staticmethod
    def _iter_stream_variants(stream: Any) -> list[dict[str, Any]]:
        """把 stream 摊平成变体列表。

        新版小红书 stream 是 {画质档位: [变体...]} 字典（EF4/EF5/EF6/EF7），
        旧版是 [变体...] 列表，两种都要兼容。
        """
        variants: list[dict[str, Any]] = []
        if isinstance(stream, dict):
            for group in stream.values():
                if isinstance(group, list):
                    variants.extend(v for v in group if isinstance(v, dict))
        elif isinstance(stream, list):
            variants.extend(v for v in stream if isinstance(v, dict))
        return variants

    @classmethod
    def _extract_video_url(cls, video: Any) -> str:
        """从 note.video 提取可播放的视频直链。

        数据来源（按优先级）：
          1. video.media.stream —— 新版为画质档位字典，旧版为列表
          2. video.mediaV2      —— JSON 字符串，其 stream 字段同构（实测 masterUrl 为空，仅作兜底）

        选择规则（实测依据）：
          - format="mp4" 的变体能被浏览器直接播放；
          - format="fmp4"（.m4s DASH 分段）在浏览器中报 MEDIA_ERR_SRC_NOT_SUPPORTED，
            即使分辨率更高也不能选。
          因此先筛 mp4，再按 height、size 降序取最佳。
        """
        candidates: list[dict[str, Any]] = []

        if isinstance(video, dict):
            media = video.get("media") or {}
            if isinstance(media, dict):
                candidates.extend(cls._iter_stream_variants(media.get("stream")))

            media_v2 = video.get("mediaV2")
            if isinstance(media_v2, str) and media_v2.strip():
                try:
                    parsed = json.loads(media_v2)
                except (TypeError, ValueError):
                    parsed = None
                if isinstance(parsed, dict):
                    candidates.extend(cls._iter_stream_variants(parsed.get("stream")))

        if not candidates:
            return ""

        playable = [v for v in candidates if str(v.get("format", "")).lower() == "mp4"]
        pool = playable or candidates
        best = max(pool, key=lambda v: (v.get("height") or 0, v.get("size") or 0))

        url = best.get("masterUrl") or best.get("master_url") or ""
        if not url:
            backup = best.get("backupUrls") or []
            if isinstance(backup, list) and backup:
                url = backup[0]
        if not isinstance(url, str) or not url:
            return ""
        if url.startswith("//"):
            url = f"https:{url}"
        if not url.startswith(("http://", "https://")):
            url = f"https://{url}"
        return url

    async def _extract_uid_from_homepage_async(self) -> str:
        """用 SessionManager 异步访问小红书首页，提取用户 ID。

        在 Spider ABC 内部，session_manager 已经启动，
        可以用 self._session_manager.fetch(request) 异步请求页面。
        """
        try:
            req = Request(url=XHS_HOMEPAGE)
            page = await self._session_manager.fetch(req)
        except Exception as e:
            logger.warning(f"XhsSpider: async homepage fetch failed: {e}")
            return ""

        return self._extract_uid_from_page(page)

    def _extract_uid_from_page(self, page: Any) -> str:
        """从页面内容中提取 uid（多种策略）。"""
        try:
            uid_els = page.css("[class*='user-id'], [data-uid]")
            uid_el = uid_els[0] if uid_els else None
            if uid_el and hasattr(uid_el, "attrib"):
                uid = uid_el.attrib.get("data-uid", "") or uid_el.attrib.get("data-v-userid", "")
                if uid and len(uid) > 5:
                    logger.info(f"XhsSpider: got uid from DOM attribute: {uid}")
                    return uid
        except Exception:
            pass

        try:
            profile_links = page.css("a[href*='/user/profile/']")
            profile_link = profile_links[0] if profile_links else None
            if profile_link and hasattr(profile_link, "attrib"):
                href = profile_link.attrib.get("href", "")
                m = re.search(r'/user/profile/([0-9a-fA-F]{16,32})', href)
                if m:
                    logger.info(f"XhsSpider: got uid from profile link: {m.group(1)}")
                    return m.group(1)
        except Exception:
            pass

        try:
            all_links = page.css("a[href*='profile']")
            for link in all_links[:10]:
                href = link.attrib.get("href", "") if hasattr(link, "attrib") else ""
                m = re.search(r'/user/profile/(\w{10,30})', href)
                if m:
                    logger.info(f"XhsSpider: got uid from link: {m.group(1)}")
                    return m.group(1)
        except Exception:
            pass

        try:
            script_els = page.css("script")
            for s in script_els:
                txt = s.text if hasattr(s, "text") else ""
                m = re.search(r'"id"\s*:\s*"(\w{16,30})"', txt)
                if m:
                    logger.info(f"XhsSpider: got uid from inline script: {m.group(1)}")
                    return m.group(1)
        except Exception:
            pass

        logger.warning("XhsSpider: could not extract uid from page")
        return ""

    def _load_cookies_from_storage_state(self, storage_path: Path) -> list[dict]:
        """从 Playwright storageState JSON 提取 cookies（保留完整属性）。

        Scrapling SetCookieParam 格式：
          name, value, url?, domain?, path?, expires?,
          httpOnly?, secure?, sameSite?, partitionKey?
        """
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
            logger.info(f"XhsSpider: loaded {len(cookie_list)} cookies from storageState")
            return cookie_list
        except Exception as e:
            logger.warning(f"XhsSpider: failed to parse storageState: {e}")
            return []

    def _build_cookies_from_db(self) -> list[dict]:
        """从数据库 cookies_json 构建 cookie 列表。"""
        cookie_list: list[dict] = []
        if isinstance(self.cookies, dict):
            for name, val in self.cookies.items():
                if isinstance(val, dict):
                    c: dict[str, Any] = {"name": name, "value": val.get("value", "")}
                    if val.get("domain"):
                        c["domain"] = val["domain"]
                    else:
                        c["domain"] = ".xiaohongshu.com"
                    if val.get("path"):
                        c["path"] = val["path"]
                    else:
                        c["path"] = "/"
                    cookie_list.append(c)
                else:
                    cookie_list.append({"name": name, "value": str(val), "domain": ".xiaohongshu.com", "path": "/"})
        elif isinstance(self.cookies, list):
            for c in self.cookies:
                if isinstance(c, dict) and c.get("name"):
                    item = {"name": c["name"], "value": c.get("value", ""), "domain": ".xiaohongshu.com", "path": "/"}
                    if c.get("domain"):
                        item["domain"] = c["domain"]
                    if c.get("path"):
                        item["path"] = c["path"]
                    cookie_list.append(item)
        return cookie_list

    def _extract_uid_from_cookies(self) -> str:
        """从数据库 cookies 中直接提取 uid，无需访问网页。"""
        if not self.cookies:
            return ""

        uid_cookie_names = [
            "galaxy_creator_user_id",
            "uid",
            "customerClientId",
            "xhsuid",
        ]

        if isinstance(self.cookies, dict):
            for name in uid_cookie_names:
                val = self.cookies.get(name)
                if val is None:
                    continue
                if isinstance(val, dict):
                    v = val.get("value", "")
                else:
                    v = str(val)
                if v and len(v) > 5:
                    logger.info(f"XhsSpider: found uid '{v}' from cookie '{name}'")
                    return v

        elif isinstance(self.cookies, list):
            for c in self.cookies:
                if not isinstance(c, dict):
                    continue
                name = c.get("name", "")
                if name in uid_cookie_names:
                    v = c.get("value", "")
                    if v and len(v) > 5:
                        logger.info(f"XhsSpider: found uid '{v}' from cookie '{name}'")
                        return v

        return ""

    def _try_update_uid_in_db(self) -> None:
        """尝试将提取到的 uid 回写到数据库。"""
        if not self.platform_uid or not self.user_id:
            return
        try:
            import asyncio

            from capabilities.scrapling.spiders.db_sink import update_platform_uid
            try:
                loop = asyncio.get_event_loop()
                if not loop.is_running():
                    loop.run_until_complete(
                        update_platform_uid(self.user_id, "xiaohongshu", self.platform_uid)
                    )
                    logger.info(f"XhsSpider: updated uid={self.platform_uid} in db")
            except RuntimeError:
                pass
        except Exception as e:
            logger.warning(f"XhsSpider: failed to update uid in db: {e}")

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

    @staticmethod
    def _safe_attr(parent: Any, selectors: str, attr_name: str) -> str:
        """Scrapling 自适应选择器提取属性值。"""
        for sel in selectors.split(", "):
            try:
                els = parent.css(sel.strip())
                el = els[0] if els else None
                if el and hasattr(el, "attrib"):
                    val = el.attrib.get(attr_name, "")
                    if val:
                        return val
            except Exception:
                continue
        return ""

    @staticmethod
    def _parse_count(text: str) -> int:
        """解析互动数字符串，如 '1.2万' -> 12000, '345' -> 345。"""
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
            r'xiaohongshu\.com/explore/([0-9a-fA-F]{24})',
            r'xiaohongshu\.com/discovery/item/([0-9a-fA-F]{24})',
            r'xiaohongshu\.com/note/([0-9a-fA-F]{24})',
            r'/note/([0-9a-fA-F]{24})',
        ]
        for pat in patterns:
            m = re.search(pat, url)
            if m:
                return m.group(1)
        return ""

    def _is_login_page(self, response: Any) -> bool:
        """检测当前页面是否为登录页（cookies 过期被重定向）。

        使用轻量检测（URL + title + DOM），不调用 _extract_initial_state 避免重复解析。
        """
        try:
            url = ""
            if hasattr(response, "url"):
                url = str(response.url)
            if "/login" in url or "/sign-in" in url:
                return True
            title_text = ""
            title_els = response.css("title")
            if title_els:
                title_text = (title_els[0].text or "").lower() if hasattr(title_els[0], "text") else ""
            if "登录" in title_text or "login" in title_text or "signin" in title_text:
                return True
            login_els = response.css("[class*='login-modal'], [class*='qrcode'], [class*='sign-in']")
            if login_els and len(login_els) >= 1:
                return True
        except Exception:
            pass
        return False