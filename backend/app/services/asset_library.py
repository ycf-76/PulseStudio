"""User image asset library.

Phase 4 backend foundation:
- Validate uploaded image content with Pillow.
- Strip EXIF metadata.
- Save original and thumbnail files.
- Persist asset metadata per user.
"""

from __future__ import annotations

import io
import logging
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

from app.db.models import ImageAsset, VideoAsset, generate_ulid
from app.db.session import AsyncSessionLocal

logger = logging.getLogger(__name__)


ALLOWED_CONTENT_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/gif",
}

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
THUMBNAIL_MAX_SIZE = (400, 400)

_BASE_DIR = Path(__file__).resolve().parent.parent.parent / "uploads"


def _ext_for_format(image_format: str) -> str:
    return {
        "JPEG": ".jpg",
        "PNG": ".png",
        "WEBP": ".webp",
        "GIF": ".gif",
    }.get((image_format or "").upper(), ".png")


def _process_image(content_type: str, data: bytes) -> tuple[bytes, str, int, int]:
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise ValueError("不支持的图片类型")
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError("图片不能超过 10MB")

    try:
        image = Image.open(io.BytesIO(data))
        image = ImageOps.exif_transpose(image)
        # Explicitly drop EXIF before re-encoding; do not rely on save() alone.
        image.info.pop("exif", None)
        if image.mode not in ("RGB", "RGBA"):
            image = image.convert("RGBA")

        output = io.BytesIO()
        save_kwargs: dict[str, Any] = {}
        if image.format == "JPEG":
            save_kwargs["quality"] = 90
        elif image.format == "WEBP":
            save_kwargs["quality"] = 88
        image.save(output, format=image.format or "PNG", **save_kwargs)
        processed = output.getvalue()
        return processed, image.format or "PNG", image.width, image.height
    except Exception as exc:
        raise ValueError(f"图片内容无效: {exc}") from exc


def _make_thumbnail(data: bytes, image_format: str) -> bytes:
    image = Image.open(io.BytesIO(data))
    image.thumbnail(THUMBNAIL_MAX_SIZE)
    if image.mode not in ("RGB", "RGBA"):
        image = image.convert("RGBA")
    output = io.BytesIO()
    save_kwargs: dict[str, Any] = {}
    if image_format == "JPEG":
        save_kwargs["quality"] = 82
    elif image_format == "WEBP":
        save_kwargs["quality"] = 82
    image.save(output, format=image_format or "PNG", **save_kwargs)
    return output.getvalue()


def _asset_to_dict(asset: ImageAsset) -> dict[str, Any]:
    return {
        "asset_id": asset.id,
        "id": asset.id,  # 兼容旧调用方约定（multi_image_video 等用 asset["id"]）
        "filename": asset.filename,
        "content_type": asset.content_type,
        "file_size": asset.file_size,
        "width": asset.width,
        "height": asset.height,
        "original_url": asset.original_url,
        "thumbnail_url": asset.thumbnail_url,
        "source": asset.source,
    }


def _path_from_url(url: str) -> Path | None:
    if not url or not url.startswith("/uploads/"):
        return None
    return _BASE_DIR / url.removeprefix("/uploads/")


def path_from_url(url: str) -> Path | None:
    """将 /uploads/... URL 解析为本地绝对路径（公开 API）。

    供 multi_image_video / video_pipeline 等跨模块调用，
    替代直接 import 私有的 _path_from_url。
    返回 None 表示 URL 不是 uploads 路径。
    """
    return _path_from_url(url)


def url_to_local_path(url: str) -> str | None:
    """将 /uploads/... URL 解析为本地绝对路径字符串。

    比 path_from_url 多一步：路径不存在时返回 None，
    避免调用方每次都要 .exists() 判断。
    """
    p = _path_from_url(url)
    if p is not None and p.exists():
        return str(p)
    return None


def url_to_base64(url: str) -> str | None:
    """将 /uploads/... URL 或本地路径转为 data:image/...;base64,... 字符串。

    供 QwenVL / DashScope 等需要 base64 图片输入的 API 使用。
    URL 必须是 /uploads/... 格式（由 save_asset / save_video_asset 生成）。
    返回 None 表示文件不存在或读取失败。
    """
    import base64

    p = _path_from_url(url)
    if p is None:
        if Path(url).is_file():
            p = Path(url)
        else:
            return None
    if not p.exists():
        return None

    suffix = p.suffix.lower()
    mime_map = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png", ".gif": "image/gif",
        ".webp": "image/webp", ".bmp": "image/bmp",
        ".mp4": "video/mp4",
    }
    mime = mime_map.get(suffix, "application/octet-stream")
    try:
        raw = p.read_bytes()
        b64 = base64.b64encode(raw).decode("ascii")
        return f"data:{mime};base64,{b64}"
    except Exception:
        return None


async def save_asset(
    user_id: str,
    filename: str,
    content_type: str,
    data: bytes,
    source: str = "upload",
) -> dict[str, Any]:
    """Validate, process, store, and persist one user image asset.

    source: upload=用户上传 / ai=AI 生成（分镜关键帧等，区分素材来源统计）。
    """
    processed, image_format, width, height = _process_image(content_type, data)
    thumbnail = _make_thumbnail(processed, image_format)
    asset_id = generate_ulid()
    ext = _ext_for_format(image_format)
    user_dir = _BASE_DIR / "assets" / user_id
    user_dir.mkdir(parents=True, exist_ok=True)

    original_name = f"{asset_id}{ext}"
    thumbnail_name = f"{asset_id}_thumb{ext}"
    (user_dir / original_name).write_bytes(processed)
    (user_dir / thumbnail_name).write_bytes(thumbnail)

    async with AsyncSessionLocal() as session:
        asset = ImageAsset(
            id=asset_id,
            user_id=user_id,
            filename=filename,
            content_type=content_type,
            file_size=len(processed),
            width=width,
            height=height,
            original_url=f"/uploads/assets/{user_id}/{original_name}",
            thumbnail_url=f"/uploads/assets/{user_id}/{thumbnail_name}",
            source=source,
        )
        session.add(asset)
        await session.commit()
        await session.refresh(asset)
        return _asset_to_dict(asset)


_VIDEO_BASE_DIR = _BASE_DIR / "videos"


def _video_asset_to_dict(asset: VideoAsset) -> dict[str, Any]:
    return {
        "asset_id": asset.id,
        "id": asset.id,
        "filename": asset.filename,
        "content_type": asset.content_type,
        "file_size": asset.file_size,
        "original_url": asset.original_url,
        "thumbnail_url": asset.thumbnail_url,
        "source": asset.source,
        "metadata": asset.extra or {},
    }


async def save_video_asset(
    user_id: str,
    filename: str,
    content_type: str,
    data: bytes,
    source: str = "ai",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """保存 AI 生成的视频素材：落盘 uploads/videos/ + 落库 VideoAsset。

    返回结构同时带 asset_id 与 id 两个键，兼容两种调用方约定
    （video_pipeline 用 asset["original_url"]，auto_short_video 用 asset["id"]）。
    """
    asset_id = generate_ulid()
    ext = Path(filename).suffix or ".mp4"
    user_dir = _VIDEO_BASE_DIR / user_id
    user_dir.mkdir(parents=True, exist_ok=True)

    original_name = f"{asset_id}{ext}"
    (user_dir / original_name).write_bytes(data)

    async with AsyncSessionLocal() as session:
        asset = VideoAsset(
            id=asset_id,
            user_id=user_id,
            filename=filename,
            content_type=content_type,
            file_size=len(data),
            original_url=f"/uploads/videos/{user_id}/{original_name}",
            thumbnail_url="",
            source=source,
            extra=metadata or {},
        )
        session.add(asset)
        await session.commit()
        await session.refresh(asset)
        return _video_asset_to_dict(asset)


async def list_assets(user_id: str, limit: int | None = None) -> list[dict[str, Any]]:
    """列出用户的图片素材，按创建时间倒序。

    limit 为 None 时返回全部（保持历史行为，卡片素材库等依赖全量）。
    传值时只取最近 N 张——聊天历史回填缩略图这类场景用得上，避免整表回传。
    """
    from sqlalchemy import select

    async with AsyncSessionLocal() as session:
        stmt = (
            select(ImageAsset)
            .where(ImageAsset.user_id == user_id)
            .order_by(ImageAsset.created_at.desc())
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        result = await session.execute(stmt)
        return [_asset_to_dict(asset) for asset in result.scalars().all()]


async def get_asset(user_id: str, asset_id: str) -> dict[str, Any] | None:
    """按 asset_id 取单张图片素材元数据（同时校验归属）。

    供 vl_analyze 等技能把聊天里透出的 asset_id 还原成真实图片——
    LLM 只能看到 id，看不到像素，必须由技能侧查库拿到 original_url。
    返回 None 表示不存在或不属于该用户（不区分两者，避免探测他人素材）。
    """
    from sqlalchemy import select

    if not user_id or not asset_id:
        return None
    async with AsyncSessionLocal() as session:
        stmt = select(ImageAsset).where(
            ImageAsset.user_id == user_id,
            ImageAsset.id == asset_id,
        )
        result = await session.execute(stmt)
        asset = result.scalar_one_or_none()
        return _asset_to_dict(asset) if asset is not None else None


async def list_video_assets(user_id: str, limit: int | None = None) -> list[dict[str, Any]]:
    from sqlalchemy import select

    async with AsyncSessionLocal() as session:
        stmt = (
            select(VideoAsset)
            .where(VideoAsset.user_id == user_id)
            .order_by(VideoAsset.created_at.desc())
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        result = await session.execute(stmt)
        return [_video_asset_to_dict(asset) for asset in result.scalars().all()]


async def delete_video_asset(user_id: str, asset_id: str) -> bool:
    from sqlalchemy import select

    async with AsyncSessionLocal() as session:
        stmt = select(VideoAsset).where(
            VideoAsset.user_id == user_id,
            VideoAsset.id == asset_id,
        )
        result = await session.execute(stmt)
        asset = result.scalar_one_or_none()
        if asset is None:
            return False
        original_path = _path_from_url(asset.original_url)
        await session.delete(asset)
        await session.commit()

    if original_path is not None:
        original_path.unlink(missing_ok=True)
    return True


async def delete_asset(user_id: str, asset_id: str) -> bool:
    from sqlalchemy import select

    async with AsyncSessionLocal() as session:
        stmt = select(ImageAsset).where(
            ImageAsset.user_id == user_id,
            ImageAsset.id == asset_id,
        )
        result = await session.execute(stmt)
        asset = result.scalar_one_or_none()
        if asset is None:
            return False
        original_path = _path_from_url(asset.original_url)
        thumbnail_path = _path_from_url(asset.thumbnail_url)
        await session.delete(asset)
        await session.commit()

    for path in (original_path, thumbnail_path):
        if path is not None:
            path.unlink(missing_ok=True)
    return True