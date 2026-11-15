"""小红书 Spider 内容源：基于 Scrapling 爬虫框架搜索小红书爆款内容。

使用 XhsSearchSpider + AsyncStealthySession 直接爬取搜索页。

优势：
  - 真正的小红书站内数据
  - 复用登录态（storageState）
  - 反检测（AsyncStealthySession）
  - 自适应选择器（网站改版容错）

风控：
  - 搜索页风控比主页更严，每次搜索限 20 条
  - 需要有效登录态（storageState），过期时返回空并提示重新登录
  - 检测到验证码时自动停止
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from capabilities.content_search.sources.base import ContentSource, TrendingContent

logger = logging.getLogger(__name__)

STORAGE_DIR = Path(__file__).resolve().parent.parent.parent.parent / "app" / "storage_states"


class XhsSpiderSource(ContentSource):
    """小红书 Spider 内容源（基于 Scrapling 爬虫）。

    用 XhsSearchSpider 在子进程中运行搜索，
    结果归一化为 TrendingContent 供选题池使用。

    注册为 platform="xiaohongshu"，
    由 init_sources() 自动检测 storageState 决定是否注册。
    """

    @property
    def name(self) -> str:
        return "xiaohongshu"

    def __init__(self) -> None:
        self._storage_available: bool | None = None

    def _check_storage_state(self) -> bool:
        """检查登录态是否可用。"""
        if self._storage_available is not None:
            return self._storage_available
        storage_path = STORAGE_DIR / "xiaohongshu.json"
        self._storage_available = storage_path.exists()
        if not self._storage_available:
            logger.warning(
                "[xiaohongshu_spider] storageState 不存在，"
                "请先扫码登录小红书（设置 → 平台账号 → 扫码登录）"
            )
        return self._storage_available

    def _normalize(self, raw: dict[str, Any]) -> TrendingContent | None:
        """把 XhsSearchSpider yield 的 dict 归一化为 TrendingContent。"""
        try:
            note_id = str(raw.get("note_id") or "")
            if not note_id:
                return None

            title = (raw.get("title") or "").strip()
            desc = (raw.get("content_text") or "").strip()
            summary = (desc[:200]) if desc else ""
            content = desc
            author = raw.get("author") or ""
            url = raw.get("url") or raw.get("note_url") or ""
            cover_img = raw.get("cover_img_url") or ""
            likes = int(raw.get("likes") or 0)
            comments = int(raw.get("comments") or 0)
            shares = int(raw.get("shares") or 0)
            collects = int(raw.get("collects") or 0)
            tags_raw = raw.get("tags") or []
            tags = tags_raw if isinstance(tags_raw, list) else []

            return TrendingContent(
                platform="xiaohongshu",
                content_id=note_id,
                title=title,
                summary=summary,
                content=content,
                author=author,
                url=url,
                likes=likes,
                comments=comments,
                shares=shares,
                collects=collects,
                views=0,
                cover_img=cover_img,
                published_at="",
                tags=tags,
                raw=raw,
            )
        except Exception as e:
            logger.warning(f"[xiaohongshu_spider] normalize failed: {e}")
            return None

    async def search_trending(
        self,
        keyword: str,
        limit: int = 20,
        time_range: str = "week",
    ) -> list[TrendingContent]:
        """用 XhsSearchSpider 搜索小红书爆款内容。

        在子进程中运行 Spider（Scrapling 内部用 Playwright sync API），
        通过 stdout JSON 行返回结果。
        """
        if not self._check_storage_state():
            logger.error(
                "[xiaohongshu_spider] 登录态不可用，无法搜索。"
                "请先扫码登录小红书。"
            )
            return []

        search_limit = min(limit, 20)

        try:
            from capabilities.scrapling.spiders.xhs_search_spider import XhsSearchSpider
            raw_items = await XhsSearchSpider.run_search(keyword, search_limit)
        except Exception as e:
            logger.error(f"[xiaohongshu_spider] search_trending failed: {e}")
            return []

        results: list[TrendingContent] = []
        for raw in raw_items:
            content = self._normalize(raw)
            if content:
                results.append(content)

        logger.info(
            f"[xiaohongshu_spider] search '{keyword}': "
            f"got {len(raw_items)} raw, {len(results)} normalized"
        )
        return results[:limit]

    async def get_trending(
        self,
        category: str = "",
        limit: int = 20,
    ) -> list[TrendingContent]:
        """小红书没有全站热门 API，需通过关键词搜索。

        如果不提供 category，返回空列表。
        """
        if not category:
            logger.info("[xiaohongshu_spider] get_trending requires category (keyword), got empty")
            return []
        return await self.search_trending(category, limit)

    async def close(self) -> None:
        logger.info("[xiaohongshu_spider] close (no-op)")