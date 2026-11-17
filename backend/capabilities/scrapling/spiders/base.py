"""平台 Spider 基类。

基于 Scrapling Spider ABC，提供通用配置和入库回调。
子类只需实现 parse() 和平台特定选择器。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from scrapling.spiders import Spider

logger = logging.getLogger(__name__)


class BasePlatformSpider(Spider):
    """平台 Spider 基类，继承 Scrapling Spider ABC。

    子类需实现：
      - configure_sessions(): 配置 StealthySession / FetcherSession
      - parse(): 解析列表页，yield Request 或 dict
      - parse_note_detail(): 解析详情页（可选）

    通用能力：
      - adaptive=True: 自适应选择器，网站改版自动容错
      - autothrottle: 自动限速
      - on_scraped_item(): 每条结果实时入库 + 推送进度
      - on_close(): 爬取完成后更新同步状态
    """

    platform: str = ""
    user_id: str = ""
    platform_uid: str = ""
    cookies: dict | None = None
    # 已实时入库的条目数（on_scraped_item 累加），供子进程回报真实入库量
    saved_count: int = 0

    adaptive = True
    autothrottle_enabled = True
    autothrottle_start_delay = 2.0
    autothrottle_max_delay = 10.0
    concurrent_requests = 2
    max_blocked_retries = 3

    def __init__(
        self, user_id: str = "", platform_uid: str = "", cookies: dict | None = None, **kwargs
    ):
        self.user_id = user_id
        self.platform_uid = platform_uid
        self.cookies = cookies
        self.saved_count = 0
        super().__init__(**kwargs)

    async def on_scraped_item(self, item: dict) -> dict | None:
        """每条结果实时入库 + 推送进度。"""
        from capabilities.scrapling.spiders.db_sink import save_scraped_item

        saved = await save_scraped_item(item, self.platform, self.user_id)
        if saved:
            self.saved_count += 1
            try:
                from app.services.notification_bus import notification_bus

                await notification_bus.publish(
                    "spider_item_scraped",
                    {
                        "platform": self.platform,
                        "title": item.get("title", "")[:50],
                        "spider_name": self.name,
                    },
                )
            except Exception:
                pass
        return item

    async def on_close(self) -> None:
        """爬取完成后更新 PlatformAccount 同步状态。"""
        from capabilities.scrapling.spiders.db_sink import update_sync_status

        await update_sync_status(self.user_id, self.platform, "idle")

    def download_media(
        self,
        url: str,
        out_dir: str | Path | None = None,
        max_height: int | None = 1080,
        timeout: int | None = None,
    ) -> dict[str, Any]:
        """下载当前平台的视频文件（yt-dlp，白名单路由）。

        元数据爬取仍走各 Spider 自己的 API 链路，本方法只负责文件落盘。
        非白名单平台（xiaohongshu / douyin）抛 UnsupportedPlatformError。
        详见 capabilities.scrapling.spiders.media_downloader。
        """
        from capabilities.scrapling.spiders.media_downloader import download_media

        return download_media(
            url,
            platform=self.platform,
            out_dir=out_dir,
            cookies=self.cookies,
            max_height=max_height,
            timeout=timeout,
        )

    @staticmethod
    def _parse_count(text: str) -> int:
        """解析 '1.2w' '3.5k' '1.2万' 等中文计数格式。"""
        text = text.strip().replace(",", "")
        if not text:
            return 0
        try:
            if "w" in text or "万" in text:
                return int(float(text.replace("w", "").replace("万", "")) * 10000)
            if "k" in text:
                return int(float(text.replace("k", "")) * 1000)
            return int(float(text))
        except (ValueError, TypeError):
            return 0

    @staticmethod
    def _safe_text(el: Any, selectors: str, attr: str = "text") -> str:
        """Scrapling 自适应选择器提取文本或属性，支持逗号分隔的多 selector。"""
        for sel in selectors.split(", "):
            try:
                sel = sel.strip()
                if not sel:
                    found = el
                else:
                    els = el.css(sel)
                    found = els[0] if els else None
                if not found:
                    continue
                if attr == "text":
                    return found.text.strip() if hasattr(found, "text") else ""
                return found.attrib.get(attr, "") if hasattr(found, "attrib") else ""
            except Exception:
                continue
        return ""
