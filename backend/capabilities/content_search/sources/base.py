"""ContentSource 抽象基类 + 统一数据结构。

与 MCPClient 的关系：
- MCPClient 是小红书专用（含 publish/account_info 等发布能力）
- ContentSource 是只读数据源（只搜索，不发布）
"""

from __future__ import annotations

import asyncio
import logging
import os
import ssl
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_SSL_CONTEXT: ssl.SSLContext | None = None
_SSL_LOCK = threading.Lock()


async def get_ssl_context() -> ssl.SSLContext:
    """全局共享的 SSL 上下文（在后台线程构建一次）。

    httpx.AsyncClient 在 verify=True 时会同步调用 ssl.create_default_context()，
    该方法在 Windows 上要读整本系统证书库，实测单次耗时 3~11 秒。由于它是在
    事件循环线程里同步执行的，每新建一个 client 就会把整个事件循环卡住同样久，
    这是多平台搜索尾延迟真正的根因（13 处 client 创建 → 累计几十秒）。
    这里只构建一次并复用，且放到线程池里跑，不阻塞事件循环。
    用 threading.Lock 而非 asyncio.Lock，避免跨事件循环（测试场景）绑定报错。
    """
    if _SSL_CONTEXT is not None:
        return _SSL_CONTEXT

    def _build() -> ssl.SSLContext:
        global _SSL_CONTEXT
        with _SSL_LOCK:
            if _SSL_CONTEXT is None:
                _SSL_CONTEXT = ssl.create_default_context()
            return _SSL_CONTEXT

    return await asyncio.to_thread(_build)


async def create_async_client(**kwargs: Any) -> httpx.AsyncClient:
    """创建带共享 SSL 上下文的 httpx 异步客户端。

    所有 ContentSource 都应通过它建 client，避免每个源都重复付证书库加载成本。
    """
    kwargs.setdefault("verify", await get_ssl_context())
    return httpx.AsyncClient(**kwargs)


def get_proxy_url() -> str:
    """获取 httpx 代理 URL（供所有 ContentSource 共享）。

    优先级：
    1. settings.http_proxy 显式配置（如 http://127.0.0.1:7897）
    2. settings.http_proxy == "system" → 读环境变量 HTTP_PROXY / HTTPS_PROXY
    3. 空字符串 → 不走代理

    Returns:
        代理 URL 字符串，或空字符串（不走代理）
    """
    try:
        from app.config import get_settings

        settings = get_settings()
        proxy = settings.http_proxy
    except Exception:
        proxy = ""

    if not proxy:
        return ""

    if proxy == "system":
        env_proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or ""
        if env_proxy:
            logger.info(f"[sources] using system proxy: {env_proxy}")
        return env_proxy

    logger.info(f"[sources] using configured proxy: {proxy}")
    return proxy


@dataclass
class TrendingContent:
    """跨平台统一的爆款内容数据结构。

    所有平台的搜索结果都会归一化成这个结构，
    下游 analyze 节点不关心数据来自哪个平台。
    """

    platform: str  # "reddit" | "hackernews" | "xiaohongshu" | ...
    content_id: str  # 平台内容 ID
    title: str  # 标题（中文译文；原文在 title_original）
    summary: str  # 摘要（中文译文；原文在 summary_original）
    content: str  # 正文（中文译文；原文在 content_original）
    author: str  # 作者
    url: str  # 原文链接
    likes: int = 0  # 点赞/Upvote
    comments: int = 0  # 评论数
    shares: int = 0  # 转发/分享数
    views: int = 0  # 浏览量（部分平台有）
    collects: int = 0  # 收藏数（小红书有，其他平台默认0）
    author_fans: int = 0  # 作者粉丝数（小红书/GitHub有，其他平台默认0）
    cover_img: str = ""  # 封面图 URL（可选）
    published_at: str = ""  # ISO8601 发布时间
    tags: list[str] = field(default_factory=list)  # 标签/版块
    title_original: str = ""  # 标题原文（英文）
    summary_original: str = ""  # 摘要原文（英文）
    content_original: str = ""  # 正文原文（英文）
    raw: dict[str, Any] = field(default_factory=dict)  # 原始数据（诊断用）

    def to_dict(self) -> dict[str, Any]:
        """转 dict（供 SSE 传输 / LLM 输入）。"""
        d = {
            "platform": self.platform,
            "content_id": self.content_id,
            "title": self.title,
            "summary": self.summary,
            "content": self.content,
            "author": self.author,
            "url": self.url,
            "likes": self.likes,
            "comments": self.comments,
            "shares": self.shares,
            "views": self.views,
            "collects": self.collects,
            "author_fans": self.author_fans,
            "cover_img": self.cover_img,
            "published_at": self.published_at,
            "tags": self.tags,
            "title_original": self.title_original,
            "summary_original": self.summary_original,
        }
        return d


class ContentSource(ABC):
    """多平台内容数据源抽象接口。

    实现类需提供：
    - name: 平台标识（"reddit" / "hackernews" / ...）
    - search_trending: 按关键词搜索热门内容
    - get_trending: 获取平台热门内容（无关键词，按热度）
    - close: 释放资源
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """平台唯一标识，如 "reddit" / "hackernews" / "xiaohongshu"。"""
        raise NotImplementedError

    @property
    def billable(self) -> bool:
        """调用本源是否消耗付费额度。

        True 表示每次调用都会产生真实费用（如 DeepSeek 检索 API、付费模型回合）。
        定时任务（选题池 MonitorAgent 等）必须跳过 billable 源，
        避免后台无人值守的轮询把额度烧光；只有用户主动搜索或 Agent
        明确调用时才允许使用这些源。
        """
        return False

    @property
    def concurrency_group(self) -> str:
        """并发分组标识（供多平台并发调度器使用）。

        共享同一上游配额的源（如同一 API Key 的 Tavily 站内搜索群）
        必须返回相同的组名，调度器据此在组内限流，
        避免多个源同时打同一配额导致 429 重试把单次搜索拖成数十秒。
        默认每个源各自一组（互不影响）。
        """
        return self.name

    @property
    def concurrency_limit(self) -> int | None:
        """组内并发上限。None 表示沿用调度器的全局并发数。"""
        return None

    @abstractmethod
    async def search_trending(
        self,
        keyword: str,
        limit: int = 20,
        time_range: str = "week",
    ) -> list[TrendingContent]:
        """按关键词搜索热门内容。

        Args:
            keyword: 搜索关键词
            limit: 返回条数上限
            time_range: 时间范围 "day" / "week" / "month" / "year" / "all"

        Returns:
            按热度排序的爆款内容列表
        """
        raise NotImplementedError

    @abstractmethod
    async def get_trending(
        self,
        category: str = "",
        limit: int = 20,
    ) -> list[TrendingContent]:
        """获取平台热门内容（无关键词，按全局热度）。

        Args:
            category: 分类/subreddit（可选，如 "technology"）
            limit: 返回条数上限

        Returns:
            平台全局热门内容列表
        """
        raise NotImplementedError

    @abstractmethod
    async def close(self) -> None:
        """释放资源。"""
        raise NotImplementedError
