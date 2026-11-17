"""作品内容编辑（内容层覆盖）。

采集记录与平台生成产物没有 CardDeck 块结构，只能做「内容层」编辑：
标题 / 正文 / 话题标签 / 图片顺序 / 封面。

设计约束：
1. 原始采集快照（title / content_text / images …）是分析归因的数据源，不允许被覆盖。
   编辑结果写入独立覆盖层，读取时再叠加到对外字段，随时可回溯。
2. PCP 记录：覆盖层存在 `card_draft[CONTENT_EDIT_KEY]`，不新增表、不做迁移。
3. outputs/ 产物没有数据库记录，覆盖层写在产物目录下的 `_content_edit.json`。
4. 覆盖层只认白名单字段，避免前端把任意键写进库。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.db.models import PublishedContentPerformance
from app.db.session import AsyncSessionLocal
from app.services import output_index

logger = logging.getLogger(__name__)

CONTENT_EDIT_KEY = "content_edit"
OUTPUT_EDIT_FILE = output_index.EDIT_OVERLAY_FILE

EDITABLE_FIELDS: tuple[str, ...] = ("title", "content_text", "tags", "images", "cover_img_url")


class ContentEditRejected(Exception):
    """状态闸门拒绝写入（作品不是草稿，或已被发布/归档）。"""


_TITLE_MAX = 200
_TEXT_MAX = 20000
_TAGS_MAX = 40
_TAG_LEN_MAX = 30
_IMAGES_MAX = 60


class ContentEditError(ValueError):
    """编辑内容不合法（空、超长、URL 非法）。"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_url(value: str) -> str:
    """只接受站内绝对路径与 http(s) 外链，挡掉 javascript:/data: 等注入面。"""
    text = value.strip()
    if text.startswith(("/", "http://", "https://")):
        return text
    return ""


def _clean_tags(value: Any) -> list[str]:
    if not isinstance(value, list):
        raise ContentEditError("tags 必须是数组")
    tags: list[str] = []
    for raw in value:
        if not isinstance(raw, str):
            continue
        tag = raw.strip().lstrip("#").strip()[:_TAG_LEN_MAX]
        if tag and tag not in tags:
            tags.append(tag)
        if len(tags) >= _TAGS_MAX:
            break
    return tags


def _clean_images(value: Any) -> list[str]:
    if not isinstance(value, list):
        raise ContentEditError("images 必须是数组")
    images: list[str] = []
    for raw in value:
        if not isinstance(raw, str):
            continue
        url = _safe_url(raw)
        if url and url not in images:
            images.append(url)
        if len(images) >= _IMAGES_MAX:
            break
    return images


def normalize_edit(payload: dict[str, Any]) -> dict[str, Any]:
    """把前端提交的字段裁剪成覆盖层可存的最小集合。只保留显式给出的字段。"""
    if not isinstance(payload, dict):
        raise ContentEditError("请求体必须是对象")

    edit: dict[str, Any] = {}
    if payload.get("title") is not None:
        edit["title"] = str(payload["title"]).strip()[:_TITLE_MAX]
    if payload.get("content_text") is not None:
        edit["content_text"] = str(payload["content_text"])[:_TEXT_MAX]
    if payload.get("tags") is not None:
        edit["tags"] = _clean_tags(payload["tags"])
    if payload.get("images") is not None:
        edit["images"] = _clean_images(payload["images"])
    if payload.get("cover_img_url") is not None:
        cover = _safe_url(str(payload["cover_img_url"]))
        if cover:
            edit["cover_img_url"] = cover

    unknown = set(payload) - set(EDITABLE_FIELDS)
    if unknown:
        logger.debug("忽略不可编辑字段: %s", sorted(unknown))
    return edit


def read_record_edit(record: PublishedContentPerformance) -> dict[str, Any]:
    """读取 PCP 记录上的内容覆盖层。"""
    draft = getattr(record, "card_draft", None)
    if not isinstance(draft, dict):
        return {}
    edit = draft.get(CONTENT_EDIT_KEY)
    return edit if isinstance(edit, dict) else {}


def apply_content_edit(item: dict[str, Any], edit: dict[str, Any]) -> dict[str, Any]:
    """把覆盖层叠加到对外条目；没有覆盖层时原样返回，零成本。"""
    if not edit:
        return item
    merged = dict(item)
    for field in EDITABLE_FIELDS:
        if field in edit:
            merged[field] = edit[field]
    merged["content_edit"] = edit
    merged["content_edited"] = True
    return merged


def read_output_edit(product: dict[str, Any]) -> dict[str, Any]:
    """读取产物目录下的内容覆盖层。"""
    directory = output_index.product_dir(str(product.get("rel_path") or ""))
    if directory is None:
        return {}
    path = directory / OUTPUT_EDIT_FILE
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("读取产物编辑覆盖失败 %s: %s", path, exc)
        return {}
    return data if isinstance(data, dict) else {}


def apply_output_edit(product: dict[str, Any], edit: dict[str, Any]) -> dict[str, Any]:
    """产物覆盖层：图片列表变了，页面顺序必须跟着走，否则详情页与预览各说一套。"""
    merged = apply_content_edit(product, edit)
    if "images" not in edit:
        return merged
    images = merged.get("images") if isinstance(merged.get("images"), list) else []
    draft = merged.get("card_draft") if isinstance(merged.get("card_draft"), dict) else {}
    merged["card_draft"] = {
        **draft,
        "pages": [
            {"type": "image", "imageUrl": url, "pngUrls": [url], "id": f"page-{idx}"}
            for idx, url in enumerate(images)
        ],
    }
    merged["page_count"] = len(images)
    return merged


def save_output_edit(record_id: str, edit: dict[str, Any]) -> dict[str, Any]:
    """写入产物目录覆盖层，返回叠加后的条目。"""
    product = output_index.get_product(record_id)
    if not product:
        raise LookupError("产物不存在或已被清理")
    directory = output_index.product_dir(str(product.get("rel_path") or ""))
    if directory is None:
        raise LookupError("产物目录不可写")
    payload = {**edit, "updated_at": _now_iso()}
    try:
        (directory / OUTPUT_EDIT_FILE).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError as exc:
        logger.warning("写入产物编辑覆盖失败 %s: %s", directory, exc)
        raise LookupError("产物目录不可写") from exc
    return apply_output_edit(output_index.public_product(product), edit)


async def save_record_edit(
    user_id: str,
    record_id: str,
    edit: dict[str, Any],
    require_status: str | None = None,
) -> PublishedContentPerformance:
    """把覆盖层写进 PCP 记录的 card_draft，保留原快照与已有 deck。

    require_status：写入前的状态闸门。传 "draft" 时只允许改草稿，
    采集/已发布作品（只读分析对象）直接抛 ContentEditRejected，不动原记录。
    """
    async with AsyncSessionLocal() as db:
        record = await db.scalar(
            select(PublishedContentPerformance).where(
                PublishedContentPerformance.id == record_id,
                PublishedContentPerformance.user_id == user_id,
            )
        )
        if not record:
            raise LookupError("作品记录不存在")
        if require_status and (record.content_status or "") != require_status:
            raise ContentEditRejected(
                f"当前作品状态为 {record.content_status or 'unknown'}，只有草稿可以修改"
            )
        draft = dict(record.card_draft) if isinstance(record.card_draft, dict) else {}
        draft[CONTENT_EDIT_KEY] = {**edit, "updated_at": _now_iso()}
        # JSONB 变更追踪靠对象替换，不能原地改
        record.card_draft = draft
        await db.commit()
        await db.refresh(record)
        return record
