"""Spider 结果入库：写入 PublishedContentPerformance 表。

入库格式严格对齐 my_works.py 的 collect_note_data 端点逻辑，
确保前端 workStore.mapApiToWorkItem() 零改动即可展示到"已采集"列表。

关键：content_status='collected' → 前端 worksByStatus.collected 自动展示
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy import update as sa_update

from app.db.models import PublishedContentPerformance
from app.db.session import AsyncSessionLocal

logger = logging.getLogger(__name__)


async def save_scraped_item(item: dict[str, Any], platform: str, user_id: str) -> bool:
    """Spider yield item → PublishedContentPerformance 入库。

    入库格式对齐 my_works.py:collect_note_data 的逻辑，
    确保前端 workStore.mapApiToWorkItem() 零改动即可展示到"已采集"列表。

    关键：content_status='collected' → worksByStatus.collected 自动展示
    """
    try:
        from app.services.performance_collector import compute_performance_score
        score = compute_performance_score({
            "likes": item.get("likes", 0),
            "collects": item.get("collects", 0),
            "comments": item.get("comments", 0),
            "shares": item.get("shares", 0),
        })
    except Exception:
        score = 0.0

    cover_img_url = item.get("cover_img_url", "")
    image_urls = item.get("image_urls") or []
    video_url = item.get("video_url", "")

    note_id = item.get("note_id", "")
    try:
        cover_img_url = await _cache_cover_image(note_id, cover_img_url)
    except Exception:
        pass
    try:
        image_urls = await _cache_detail_images(note_id, image_urls)
    except Exception:
        pass

    if not note_id:
        note_id = item.get("title", "")[:64] or f"unknown_{datetime.now(UTC).timestamp()}"
        logger.warning(f"save_scraped_item: note_id 为空，使用 fallback id: {note_id}")
    now = datetime.now(UTC)

    async with AsyncSessionLocal() as db:
        existing = await db.scalar(
            select(PublishedContentPerformance).where(
                PublishedContentPerformance.user_id == user_id,
                PublishedContentPerformance.platform == platform,
                PublishedContentPerformance.published_note_id == note_id,
            )
        )

        if existing:
            def pick(new_value: Any, old_value: Any) -> Any:
                if new_value is None or new_value == "" or new_value == 0 or new_value == []:
                    return old_value
                return new_value

            # 只有主页卡片（from_card=True）带来的单张封面是"低质量兜底"，
            # 详情页的 imageList 是完整图集（单图笔记本身就是一张），可信。
            # 该笔记详情页已重试到上限时也放宽，宁可留一张本地封面也别留 403 外链。
            allow_single = not item.get("from_card") or int(
                existing.detail_refresh_attempts or 0
            ) >= _max_detail_attempts()
            merged_images = _pick_images(
                image_urls,
                existing.images,
                allow_single_downgrade=allow_single,
            )
            values: dict[str, Any] = {
                "title": pick((item.get("title", "") or "")[:512], existing.title),
                "content_text": pick(item.get("content_text"), existing.content_text),
                "tags": pick(item.get("tags"), existing.tags),
                "cover_img_url": _pick_cover(cover_img_url, existing.cover_img_url),
                "images": merged_images,
                "video_url": pick(video_url, existing.video_url),
                "collected_likes": pick(item.get("likes", 0), existing.collected_likes),
                "collected_collects": pick(item.get("collects", 0), existing.collected_collects),
                "collected_comments": pick(item.get("comments", 0), existing.collected_comments),
                "collected_shares": pick(item.get("shares", 0), existing.collected_shares),
                "performance_score": pick(score, existing.performance_score),
                "collected_at": now,
                "content_status": "collected",
            }
            # 图集已全部落本地 → 清零详情页重试计数，
            # 这样将来 CDN 链接再次过期时还能被重新刷新（否则会被重试上限永久锁死）
            if _all_images_local(merged_images):
                values["detail_refresh_attempts"] = 0

            await db.execute(
                sa_update(PublishedContentPerformance)
                .where(PublishedContentPerformance.id == existing.id)
                .values(**values)
            )
            await db.commit()
            return True

        record = PublishedContentPerformance(
            user_id=user_id,
            workflow_id=None,
            published_note_id=note_id,
            title=(item.get("title", "") or "")[:512],
            content_text=item.get("content_text"),
            tags=item.get("tags"),
            cover_img_url=cover_img_url,
            images=image_urls if image_urls else None,
            video_url=video_url or None,
            platform=platform,
            collected_likes=item.get("likes", 0),
            collected_collects=item.get("collects", 0),
            collected_comments=item.get("comments", 0),
            collected_shares=item.get("shares", 0),
            performance_score=score,
            collected_at=now,
            published_at=now,
            content_status="collected",
        )
        db.add(record)
        await db.commit()

    return True


async def update_sync_status(
    user_id: str, platform: str, status: str, error: str | None = None
) -> None:
    """更新 PlatformAccount 的同步状态。"""
    from app.db.models import PlatformAccount

    async with AsyncSessionLocal() as db:
        await db.execute(
            sa_update(PlatformAccount)
            .where(
                PlatformAccount.user_id == user_id,
                PlatformAccount.platform == platform,
            )
            .values(
                sync_status=status,
                sync_error=error,
                updated_at=datetime.now(UTC),
            )
        )
        if status == "idle":
            await db.execute(
                sa_update(PlatformAccount)
                .where(
                    PlatformAccount.user_id == user_id,
                    PlatformAccount.platform == platform,
                )
                .values(last_synced_at=datetime.now(UTC))
            )
        await db.commit()


async def count_collected_works(user_id: str, platform: str) -> int:
    """统计该平台已采集作品总数（口径与 my_works._list_collected 一致）。"""
    from sqlalchemy import func, or_

    async with AsyncSessionLocal() as db:
        total = await db.scalar(
            select(func.count())
            .select_from(PublishedContentPerformance)
            .where(
                PublishedContentPerformance.user_id == user_id,
                PublishedContentPerformance.platform == platform,
                or_(
                    PublishedContentPerformance.workflow_id == "",
                    PublishedContentPerformance.workflow_id.is_(None),
                ),
            )
        )
        return int(total or 0)


async def load_cached_images(user_id: str, platform: str) -> dict[str, list[str]]:
    """读取已入库笔记的图集，供爬虫判断哪些笔记还需要重新拉详情页。

    图片一旦落到本地 /uploads 就不会再过期，重复拉详情页只是浪费同步时间
    （每条详情页都要开一次浏览器页面，笔记多时会直接超时）。
    """
    if not user_id:
        return {}

    async with AsyncSessionLocal() as db:
        rows = await db.execute(
            select(
                PublishedContentPerformance.published_note_id,
                PublishedContentPerformance.images,
            ).where(
                PublishedContentPerformance.user_id == user_id,
                PublishedContentPerformance.platform == platform,
            )
        )
        cached: dict[str, list[str]] = {}
        for note_id, images in rows:
            if not note_id or not isinstance(images, list):
                continue
            urls = [u for u in images if isinstance(u, str) and u]
            if urls:
                cached[note_id] = urls
        return cached


async def load_detail_refresh_state(user_id: str, platform: str) -> dict[str, Any]:
    """读取每条笔记的详情页刷新历史（最近尝试时间 + 累计次数）。

    供爬虫做冷却判定与优先级排序，避免"图集仍是外链 → 每轮全量重抓"的死循环。
    返回 {note_id: RefreshState}。
    """
    from capabilities.scrapling.spiders.detail_refresh import RefreshState

    if not user_id:
        return {}

    async with AsyncSessionLocal() as db:
        rows = await db.execute(
            select(
                PublishedContentPerformance.published_note_id,
                PublishedContentPerformance.detail_refresh_at,
                PublishedContentPerformance.detail_refresh_attempts,
            ).where(
                PublishedContentPerformance.user_id == user_id,
                PublishedContentPerformance.platform == platform,
            )
        )
        states: dict[str, Any] = {}
        for note_id, last_at, attempts in rows:
            if not note_id:
                continue
            states[note_id] = RefreshState(
                last_attempt_at=last_at, attempts=int(attempts or 0)
            )
        return states


async def record_detail_attempts(
    user_id: str, platform: str, note_ids: list[str]
) -> None:
    """登记本轮即将抓取的详情页（次数 +1、时间置为当前）。

    在发请求之前登记而不是抓完之后，即使子进程被超时强杀，
    冷却与重试计数依然生效，不会出现"每次同步都从零重抓"。
    """
    if not user_id or not note_ids:
        return

    now = datetime.now(UTC)
    async with AsyncSessionLocal() as db:
        await db.execute(
            sa_update(PublishedContentPerformance)
            .where(
                PublishedContentPerformance.user_id == user_id,
                PublishedContentPerformance.platform == platform,
                PublishedContentPerformance.published_note_id.in_(note_ids),
            )
            .values(
                detail_refresh_at=now,
                detail_refresh_attempts=PublishedContentPerformance.detail_refresh_attempts + 1,
            )
        )
        await db.commit()


async def update_works_count(user_id: str, platform: str, count: int) -> None:
    """更新 PlatformAccount 的作品数。"""
    from app.db.models import PlatformAccount

    async with AsyncSessionLocal() as db:
        await db.execute(
            sa_update(PlatformAccount)
            .where(
                PlatformAccount.user_id == user_id,
                PlatformAccount.platform == platform,
            )
            .values(works_count=count, updated_at=datetime.now(UTC))
        )
        await db.commit()


async def update_platform_uid(user_id: str, platform: str, uid: str) -> None:
    """更新 PlatformAccount 的 platform_uid。"""
    from app.db.models import PlatformAccount

    async with AsyncSessionLocal() as db:
        await db.execute(
            sa_update(PlatformAccount)
            .where(
                PlatformAccount.user_id == user_id,
                PlatformAccount.platform == platform,
            )
            .values(platform_uid=uid, updated_at=datetime.now(UTC))
        )
        await db.commit()


async def _cache_cover_image(note_id: str, url: str) -> str:
    """缓存封面图到本地（对齐 my_works.py 逻辑）。"""
    if not url or not url.startswith(("http://", "https://")):
        return url

    try:
        from app.services.image_store import save_topic_cover
        local_url = await save_topic_cover(note_id, url)
        if local_url:
            return local_url
    except Exception:
        pass

    return url


async def _cache_detail_images(note_id: str, urls: list[str]) -> list[str]:
    """详情图本地化：外链下载到 uploads，避免小红书 CDN 签名过期后 403。

    对齐 my_works.py 的采集逻辑；已是本地路径的图片直接跳过。
    """
    if not urls:
        return urls

    from app.services.image_store import cache_detail_images
    cached = await cache_detail_images(note_id, urls)
    return cached or urls


def _max_detail_attempts() -> int:
    """详情页自动重试上限（与爬虫侧策略保持一致）。"""
    try:
        from app.config import get_settings
        return int(get_settings().spider_detail_max_attempts)
    except Exception:
        from capabilities.scrapling.spiders.detail_refresh import DEFAULT_MAX_ATTEMPTS
        return DEFAULT_MAX_ATTEMPTS


def _all_images_local(images: Any) -> bool:
    """图集是否已全部落本地（本地文件不会过期）。"""
    if not isinstance(images, (list, tuple)) or not images:
        return False
    from app.services.image_store import is_local_url

    urls = [u for u in images if isinstance(u, str) and u]
    return bool(urls) and all(is_local_url(u) for u in urls)


def _pick_cover(new_cover: str, old_cover: Any) -> Any:
    """封面合并：本地文件优先，避免外链把已缓存的本地封面覆盖掉。"""
    if not new_cover:
        return old_cover

    from app.services.image_store import is_local_url

    if is_local_url(new_cover):
        return new_cover
    if isinstance(old_cover, str) and is_local_url(old_cover):
        return old_cover
    return new_cover


def _pick_images(
    new_images: list[str], old_images: Any, allow_single_downgrade: bool = False
) -> Any:
    """图片列表专用合并：本地文件优先，且单张封面不得覆盖已有图集。

    小红书 CDN 签名约 2 天过期，外链随时变 403。所以合并规则按「可用性」排序：
      1. 旧值全是本地文件、新值含外链 → 保留旧值（外链不可用）
      2. 旧值全是本地文件、新值张数变少 → 保留旧值（详情页抓取失败时
         只会带回封面一张，降级成单图会让用户以为同步没生效）
      3. 新值只有一张且来自主页卡片兜底（allow_single_downgrade=False），
         而旧值是多图图集或仍是外链 → 保留旧值，让这条笔记继续留在
         "详情页刷新候选"里，等详情页把图集补齐
      4. 其余情况一律用新值（新值是完整图集或已本地化）

    allow_single_downgrade=False 仅用于"新值只是主页卡片封面"这种低质量兜底：
    详情页 imageList 即使是单图也是真实图集，不受此限制。
    该笔记详情页已重试到上限（放弃自动修复）时同样放宽，
    宁可保留一张本地封面也不要留一堆 403 外链。
    """
    if not new_images:
        return old_images

    from app.services.image_store import is_local_url

    old = old_images if isinstance(old_images, list) else []
    old_vals = [u for u in old if u]
    new_vals = [u for u in new_images if u]
    if not old_vals:
        return new_images
    if not new_vals:
        return old

    new_local = all(is_local_url(u) for u in new_vals)
    old_local = all(is_local_url(u) for u in old_vals)

    if old_local and not new_local:
        return old
    if old_local and len(old_vals) > len(new_vals):
        return old
    if not allow_single_downgrade and len(new_vals) <= 1:
        # 新值只有一张（主页卡片的封面兜底）：旧值要么是多图图集、要么还是
        # 过期外链，都不能被这一张覆盖。否则这条笔记会被误判"图集已本地化"
        # 而退出详情页刷新候选，图集就再也补不齐了（库里 32 条单图外链就是这么来的）。
        if len(old_vals) > 1 or not old_local:
            return old
    return new_images
