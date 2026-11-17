"""「平台生成」作品聚合服务。

把三类「不是采集来的」产物统一成 `MyWorkItem` 结构，供 /api/my-works 直接返回：

1. 创作产物（`backend/data/creative_artifacts/*.json`，按 owner_user_id 过滤）
2. 产物目录（`outputs/` 下的图片/视频目录，见 output_index）
3. 工作流发布记录（`PublishedContentPerformance.workflow_id` 非空的记录）

字段结构刻意与采集条目保持一致，前端复用同一套卡片与详情渲染链路。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import desc, select

from app.db.models import PublishedContentPerformance
from app.db.session import AsyncSessionLocal
from app.services import output_index, work_content_edit
from app.services.creative_artifact import list_artifacts_for_user

logger = logging.getLogger(__name__)

ARTIFACT_PREFIX = "artifact:"


def _iso(ts: float | int | None) -> str | None:
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
    except (ValueError, OSError, OverflowError):
        return None


def _sort_ts(value: Any) -> float:
    """把 ISO 时间字符串/时间戳统一成可排序的秒级浮点。"""
    if isinstance(value, (int, float)):
        return float(value)
    if not value:
        return 0.0
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except ValueError:
        return 0.0


def _base_item(
    *,
    item_id: str,
    origin_kind: str,
    title: str,
    topic: str,
    platform: str,
    cover_url: str,
    images: list[str],
    video_url: str,
    published_at: str | None,
    page_count: int,
    card_draft: dict[str, Any] | None,
    first_page_html: str | None,
) -> dict[str, Any]:
    return {
        "id": item_id,
        "origin": "generated",
        "origin_kind": origin_kind,
        "workflow_id": "",
        "published_note_id": None,
        "topic": topic,
        "title": title,
        "content_text": "",
        "tags": [],
        "cover_img_url": cover_url,
        "images": images,
        "video_url": video_url,
        "platform": platform,
        "published_at": published_at,
        "collected_at": None,
        "collected_likes": 0,
        "collected_collects": 0,
        "collected_comments": 0,
        "collected_shares": 0,
        "performance_score": None,
        "is_replicated": False,
        "predicted_viral_score": None,
        "actual_viral_score": None,
        "prediction_error": None,
        "content_status": "draft",
        "title_pattern": None,
        "emotion_trigger": None,
        "content_structure": None,
        "card_draft": card_draft,
        "first_page_html": first_page_html,
        "page_count": page_count,
    }


def artifact_to_item(summary: dict[str, Any]) -> dict[str, Any]:
    """创作产物摘要 -> 统一作品条目。"""
    artifact_id = str(summary.get("artifact_id") or "")
    title = summary.get("title") or summary.get("topic") or "未命名作品"
    images = [url for url in (summary.get("page_images") or []) if url]
    card_draft = summary.get("card_draft") or None
    source = summary.get("source") or {}
    item = _base_item(
        item_id=f"{ARTIFACT_PREFIX}{artifact_id}",
        origin_kind="artifact",
        title=title,
        topic=summary.get("topic") or "",
        platform=str(source.get("platform") or "xiaohongshu"),
        cover_url=str(summary.get("cover_url") or (images[0] if images else "")),
        images=images,
        video_url="",
        published_at=_iso(summary.get("updated_at")),
        page_count=int(summary.get("page_count") or len(images)),
        card_draft=card_draft,
        first_page_html=summary.get("first_page_html") or None,
    )
    item["_artifact_id"] = artifact_id
    return item


async def _workflow_items(user_id: str, limit: int) -> list[dict[str, Any]]:
    """工作流发布记录（PCP 中 workflow_id 非空的记录）归入「平台生成」。"""
    async with AsyncSessionLocal() as db:
        stmt = (
            select(PublishedContentPerformance)
            .where(PublishedContentPerformance.user_id == user_id)
            .where(PublishedContentPerformance.workflow_id != "")
            .order_by(desc(PublishedContentPerformance.published_at))
            .limit(limit)
        )
        records = list((await db.scalars(stmt)).all())

    items: list[dict[str, Any]] = []
    for record in records:
        images = record.images if isinstance(record.images, list) else []
        urls = [entry for entry in images if isinstance(entry, str) and entry]
        item = _base_item(
            item_id=record.id,
            origin_kind="workflow",
            title=record.title or record.topic or "未命名作品",
            topic=record.topic or "",
            platform=record.platform or "xiaohongshu",
            cover_url=record.cover_img_url or (urls[0] if urls else ""),
            images=urls,
            video_url=record.video_url or "",
            published_at=record.published_at.isoformat() if record.published_at else None,
            page_count=len(urls),
            card_draft=getattr(record, "card_draft", None),
            first_page_html=getattr(record, "first_page_html", None),
        )
        item["workflow_id"] = record.workflow_id
        items.append(
            work_content_edit.apply_content_edit(item, work_content_edit.read_record_edit(record))
        )
    return items


async def list_generated_works(
    user_id: str,
    *,
    platform: str | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """汇总「平台生成」作品，按时间倒序返回。"""
    if not user_id:
        return []

    items: list[dict[str, Any]] = []
    try:
        summaries = await list_artifacts_for_user(user_id, limit=limit)
        items.extend(artifact_to_item(summary) for summary in summaries)
    except Exception as exc:  # noqa: BLE001 - 产物读取失败不能拖垮整个列表
        logger.warning("读取创作产物失败 user=%s: %s", user_id, exc)

    items.extend(
        work_content_edit.apply_output_edit(
            output_index.public_product(product), work_content_edit.read_output_edit(product)
        )
        for product in output_index.scan_products(limit=limit)
    )

    try:
        items.extend(await _workflow_items(user_id, limit))
    except Exception as exc:  # noqa: BLE001 - 数据库异常降级为「只有文件产物」
        logger.warning("读取工作流发布记录失败 user=%s: %s", user_id, exc)

    if platform:
        items = [item for item in items if item.get("platform") == platform]

    items.sort(key=lambda item: _sort_ts(item.get("published_at")), reverse=True)
    for item in items:
        item.pop("_artifact_id", None)
    return items


async def get_generated_work(user_id: str, work_id: str) -> dict[str, Any] | None:
    """按 id 取单个「平台生成」作品；不存在或不属于该用户返回 None。"""
    if not work_id:
        return None
    if work_id.startswith(output_index.ID_PREFIX):
        product = output_index.get_product(work_id)
        if not product:
            return None
        return work_content_edit.apply_output_edit(
            output_index.public_product(product), work_content_edit.read_output_edit(product)
        )
    if not work_id.startswith(ARTIFACT_PREFIX):
        return None

    artifact_id = work_id[len(ARTIFACT_PREFIX) :]
    for summary in await list_artifacts_for_user(user_id, limit=10_000):
        if str(summary.get("artifact_id")) == artifact_id:
            item = artifact_to_item(summary)
            item.pop("_artifact_id", None)
            return item
    return None
