"""作品视频下载服务：把 yt-dlp 能力接到作品记录上。

流程：POST /my-works/{id}/download-media → 校验 → 后台线程下载 →
落盘 outputs/media/{platform}/ → local_video_path 回写 → 通知总线推送。

设计文档: docs/FEATURE_yt-dlp媒体下载接入.md（前端接入章节）
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from sqlalchemy import select

from app.db.models import PublishedContentPerformance
from app.db.session import AsyncSessionLocal

logger = logging.getLogger(__name__)

# 下载根目录（backend/outputs），对应 main.py 的静态挂载前缀 /outputs
OUTPUTS_DIR = Path(__file__).resolve().parents[2] / "outputs"

# 进程内防重复下载守卫（单 worker 部署下足够；多 worker 时靠幂等校验兜底）
_downloading: set[str] = set()


def check_downloadable(platform: str | None, video_url: str | None) -> str | None:
    """校验作品是否可下载。可下载返回 None，否则返回面向用户的错误信息。"""
    if not video_url:
        return "该作品没有视频链接，无法下载"
    from capabilities.scrapling.spiders.media_downloader import (
        YTDLP_PLATFORM_WHITELIST,
        UnsupportedPlatformError,
    )
    if platform not in YTDLP_PLATFORM_WHITELIST:
        label = {"xiaohongshu": "小红书", "douyin": "抖音"}.get(platform or "", platform)
        return f"暂不支持{label}平台的视频下载（无稳定下载通道）"
    return None


def to_web_url(local_video_path: str | None) -> str | None:
    """本地落盘路径 → /outputs 静态挂载 Web 路径。"""
    if not local_video_path:
        return None
    try:
        rel = Path(local_video_path).resolve().relative_to(OUTPUTS_DIR.resolve())
        return f"/outputs/{rel.as_posix()}"
    except ValueError:
        return None


async def start_download(record_id: str, user_id: str) -> dict:
    """启动单个作品视频的后台下载。

    Returns:
        {status: "started" | "ready" | "blocked", message, local_video_url?}
    """
    async with AsyncSessionLocal() as db:
        record = await db.scalar(
            select(PublishedContentPerformance).where(
                PublishedContentPerformance.id == record_id,
                PublishedContentPerformance.user_id == user_id,
            )
        )
        if not record:
            return {"status": "blocked", "message": "作品不存在"}

        blocked = check_downloadable(record.platform, record.video_url)
        if blocked:
            return {"status": "blocked", "message": blocked}

        if record.local_video_path and Path(record.local_video_path).exists():
            return {
                "status": "ready",
                "message": "视频已下载",
                "local_video_url": to_web_url(record.local_video_path),
            }

    if record_id in _downloading:
        return {"status": "started", "message": "下载进行中"}

    _downloading.add(record_id)
    asyncio.create_task(_download_and_save(record_id, user_id))
    return {"status": "started", "message": "下载已启动，完成后自动更新"}


async def _download_and_save(record_id: str, user_id: str) -> None:
    """后台任务：下载视频并回写 local_video_path。"""
    from capabilities.scrapling.spiders.media_downloader import download_media

    try:
        async with AsyncSessionLocal() as db:
            record = await db.scalar(
                select(PublishedContentPerformance).where(
                    PublishedContentPerformance.id == record_id
                )
            )
            if not record or not record.video_url:
                return
            platform, url = record.platform, record.video_url

        await _notify("work_media_download_started", record_id, user_id, {"platform": platform})

        result = await asyncio.to_thread(
            download_media, url, platform
        )  # yt-dlp 阻塞，放线程池避免卡事件循环

        async with AsyncSessionLocal() as db:
            record = await db.scalar(
                select(PublishedContentPerformance).where(
                    PublishedContentPerformance.id == record_id
                )
            )
            if record:
                record.local_video_path = result["filepath"]
                await db.commit()

        await _notify("work_media_download_finished", record_id, user_id, {
            "platform": platform,
            "local_video_url": to_web_url(result["filepath"]),
        })
        logger.info(f"work media downloaded: record={record_id}, path={result['filepath']}")
    except Exception as e:
        logger.error(f"work media download failed: record={record_id}, error={e}", exc_info=True)
        await _notify("work_media_download_failed", record_id, user_id, {"error": str(e)[:200]})
    finally:
        _downloading.discard(record_id)


async def _notify(event: str, record_id: str, user_id: str, extra: dict) -> None:
    """通知总线推送（无订阅者时静默）。"""
    try:
        from app.services.notification_bus import notification_bus
        await notification_bus.publish(event, {"record_id": record_id, "user_id": user_id, **extra})
    except Exception:
        pass
