"""outputs/ 产物目录索引。

把文件系统里的平台产物目录映射成「我的空间」可渲染的作品条目。

约束：
- 扫描只读，不写库、不删文件、不依赖数据库，便于单测与本地调试。
  唯一例外是 `delete_product`：用户显式删除产物时才动文件，且只删本目录直属文件。
- 一个产物 = 一个「直接含有图片/视频」的目录；祖先目录若自身无媒体则不重复产出。
- 媒体通过 `/outputs/<相对路径>` 静态路径对外暴露，前端零代理直接渲染。
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
SVG_EXTS = {".svg"}
VIDEO_EXTS = {".mp4", ".mov", ".webm", ".m4v"}
SKIP_DIRS = {"__pycache__", "node_modules", ".git", ".idea", ".vscode"}
URL_PREFIX = "/outputs"
ID_PREFIX = "output:"
EDIT_OVERLAY_FILE = "_content_edit.json"
MEDIA_EXTS = IMAGE_EXTS | SVG_EXTS | VIDEO_EXTS
# 视频坏文件下限：0/1 字节的 mp4 必然无法解封装（Chrome 报
# DEMUXER_ERROR_COULD_NOT_OPEN），扫描时直接跳过，避免「我的空间」里
# 出现一条永远播不动的视频卡片（2026-11-07 单测残留 cut_ghost_*.mp4 实证）。
MIN_VIDEO_BYTES = 1024

_PLATFORM_HINTS: tuple[tuple[str, str], ...] = (
    ("xiaohongshu", "xiaohongshu"),
    ("xhs", "xiaohongshu"),
    ("小红书", "xiaohongshu"),
    ("douyin", "douyin"),
    ("抖音", "douyin"),
    ("bilibili", "bilibili"),
    ("b站", "bilibili"),
)


def project_root() -> Path:
    """backend/app/services/output_index.py -> 项目根目录。"""
    return Path(__file__).resolve().parents[3]


def backend_root() -> Path:
    """backend/app/services/output_index.py -> backend 目录。"""
    return Path(__file__).resolve().parents[2]


def output_root() -> Path | None:
    """产物根目录：环境变量 > backend/outputs > 项目根 outputs，取第一个存在的目录。"""
    raw = os.environ.get("MY_WORKS_OUTPUT_ROOT", "").strip()
    if raw:
        root = Path(raw).expanduser()
        return root if root.is_dir() else None
    for candidate in (backend_root() / "outputs", project_root() / "outputs"):
        if candidate.is_dir():
            return candidate
    return None


def infer_platform(text: str) -> str:
    """按路径/标题里的关键词推断平台，缺省小红书（卡片产物默认小红书规格）。"""
    lowered = (text or "").lower()
    for keyword, platform in _PLATFORM_HINTS:
        if keyword in lowered:
            return platform
    return "xiaohongshu"


def _natural_key(name: str) -> list[Any]:
    """自然排序：page_2 排在 page_10 之前。"""
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", name)]


def _is_noise_token(token: str) -> bool:
    """单个词是否为机器编号：长数字串 / 长十六进制串 / 长字母数字混排哈希。"""
    t = token.lower()
    if not t:
        return True
    if len(t) >= 6 and t.isdigit():
        return True
    if len(t) >= 8 and re.fullmatch(r"[0-9a-f]+", t):
        return True
    if len(t) >= 16 and any(c.isdigit() for c in t) and any(c.isalpha() for c in t):
        return True
    return False


def _clean_segment(seg: str) -> str:
    """清洗一个路径片段：去掉哈希 / 时间戳 / ULID / 用户 ID 等机器编号，保留可读文字。

    整段撞上机器编号特征（去掉分隔符后仍是长字母数字混排）时整段丢弃，
    例如 `OwdN5Jwjo_9XA_iAvuht-Q`、`6a0d0d3457a3cd9896755cfa6d042596`。
    """
    collapsed = re.sub(r"[\W_]+", "", seg).lower()
    if (
        len(collapsed) >= 16
        and any(c.isdigit() for c in collapsed)
        and any(c.isalpha() for c in collapsed)
    ):
        return ""
    tokens = re.split(r"[^0-9A-Za-z\u4e00-\u9fff]+", seg)
    kept = [t for t in tokens if not _is_noise_token(t)]
    return " ".join(t for t in kept if t).strip()


def _humanize(rel_parts: tuple[str, ...]) -> str:
    cleaned = [_clean_segment(p) for p in rel_parts[-2:]]
    text = " · ".join(p for p in cleaned if p)
    return text or "未命名产物"


def _clean_rel_path(rel_path: str) -> str:
    """展示用的相对路径：逐段清洗机器编号，全部被清掉时保留原路径兜底。"""
    parts = [_clean_segment(p) for p in rel_path.split("/")]
    return "/".join(p for p in parts if p) or rel_path


def _media_url(root: Path, path: Path) -> str:
    rel = path.relative_to(root).as_posix()
    return f"{URL_PREFIX}/{rel}"


def _product_id(rel_path: str) -> str:
    digest = hashlib.sha1(rel_path.encode("utf-8")).hexdigest()[:16]
    return f"{ID_PREFIX}{digest}"


def _collect_media(directory: Path) -> tuple[list[Path], list[Path], list[Path]]:
    images: list[Path] = []
    videos: list[Path] = []
    svgs: list[Path] = []
    try:
        entries = sorted(directory.iterdir(), key=lambda p: _natural_key(p.name))
    except OSError as exc:
        logger.warning("读取产物目录失败 %s: %s", directory, exc)
        return [], [], []
    for entry in entries:
        if not entry.is_file():
            continue
        suffix = entry.suffix.lower()
        if suffix in SVG_EXTS:
            svgs.append(entry)
        elif suffix in IMAGE_EXTS:
            images.append(entry)
        elif suffix in VIDEO_EXTS:
            try:
                if entry.stat().st_size < MIN_VIDEO_BYTES:
                    logger.warning("跳过损坏的视频产物（%s 字节）: %s", entry.stat().st_size, entry)
                    continue
            except OSError:
                continue
            videos.append(entry)
    return images, videos, svgs


def _build_product(
    root: Path, directory: Path, rel_parts: tuple[str, ...]
) -> dict[str, Any] | None:
    images, videos, svgs = _collect_media(directory)
    if not images and not videos and not svgs:
        return None
    rel_path = "/".join(rel_parts)
    title = _humanize(rel_parts)
    image_urls = [_media_url(root, p) for p in images]
    svg_urls = [_media_url(root, p) for p in svgs]
    video_url = _media_url(root, videos[0]) if videos else ""
    try:
        mtime = directory.stat().st_mtime
    except OSError:
        mtime = 0.0
    created_at = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat() if mtime else None
    pages = [
        {"type": "image", "imageUrl": url, "pngUrls": [url], "id": f"page-{idx}"}
        for idx, url in enumerate(image_urls)
    ]
    return {
        "id": _product_id(rel_path),
        "origin": "generated",
        "origin_kind": "output",
        "title": title,
        "topic": _clean_rel_path(rel_path),
        "content_text": "",
        "tags": [],
        "platform": infer_platform(rel_path),
        "cover_img_url": image_urls[0] if image_urls else (svg_urls[0] if svg_urls else ""),
        "images": image_urls,
        "svg_url": svg_urls[0] if svg_urls else "",
        "svg_urls": svg_urls,
        "video_url": video_url,
        "page_count": len(image_urls) + len(videos) + len(svg_urls),
        "published_at": created_at,
        "collected_at": None,
        "content_status": "draft",
        "card_draft": {"title": title, "pages": pages} if pages else None,
        "first_page_html": None,
        "collected_likes": 0,
        "collected_collects": 0,
        "collected_comments": 0,
        "collected_shares": 0,
        "performance_score": None,
        "is_replicated": False,
        "predicted_viral_score": None,
        "actual_viral_score": None,
        "prediction_error": None,
        "published_note_id": None,
        "workflow_id": "",
        "title_pattern": None,
        "emotion_trigger": None,
        "content_structure": None,
        "rel_path": rel_path,
        "_sort_ts": mtime,
    }


def scan_products(limit: int | None = None) -> list[dict[str, Any]]:
    """扫描产物根目录，返回按时间倒序的产物列表。"""
    root = output_root()
    if root is None:
        return []
    products: list[dict[str, Any]] = []
    for dirpath, dirnames, _filenames in os.walk(root):
        dirnames[:] = sorted(
            (d for d in dirnames if d not in SKIP_DIRS and not d.startswith("_")),
            key=_natural_key,
        )
        directory = Path(dirpath)
        if directory == root:
            continue
        rel_parts = directory.relative_to(root).parts
        if any(part.startswith("_") for part in rel_parts):
            continue
        product = _build_product(root, directory, rel_parts)
        if product:
            products.append(product)
    products.sort(key=lambda item: item["_sort_ts"], reverse=True)
    return products if limit is None else products[:limit]


def get_product(product_id: str) -> dict[str, Any] | None:
    """按 id 取单个产物；找不到返回 None。"""
    if not product_id.startswith(ID_PREFIX):
        return None
    for product in scan_products():
        if product["id"] == product_id:
            return product
    return None


def product_dir(rel_path: str) -> Path | None:
    """产物相对路径 -> 目录绝对路径；越界或目录不存在返回 None。"""
    root = output_root()
    if root is None or not rel_path:
        return None
    base = root.resolve()
    directory = (base / rel_path).resolve()
    try:
        directory.relative_to(base)
    except ValueError:
        logger.warning("产物路径越界，已拒绝: %s", rel_path)
        return None
    return directory if directory.is_dir() else None


def delete_product(product: dict[str, Any]) -> dict[str, int]:
    """删除一个产物目录里的直属媒体文件与编辑覆盖层。

    一个产物 = 「直接含媒体的目录」，子目录各自也可能是独立产物，
    递归删除会连带删掉别人的作品，所以只清理本目录直属文件；
    清空后再尝试删掉空目录（非空则保留，不含媒体时扫描自然不再产出）。
    """
    directory = product_dir(str(product.get("rel_path") or ""))
    if directory is None:
        raise LookupError("产物目录不存在或已被清理")
    try:
        entries = list(directory.iterdir())
    except OSError as exc:
        logger.warning("读取产物目录失败 %s: %s", directory, exc)
        raise LookupError("产物目录不可读") from exc

    removed = 0
    for entry in entries:
        if not entry.is_file():
            continue
        if entry.suffix.lower() not in MEDIA_EXTS and entry.name != EDIT_OVERLAY_FILE:
            continue
        try:
            entry.unlink()
            removed += 1
        except OSError as exc:
            logger.warning("删除产物文件失败 %s: %s", entry, exc)
    if removed == 0:
        raise LookupError("产物没有可删除的文件")

    try:
        directory.rmdir()
    except OSError:
        logger.debug("产物目录非空，保留: %s", directory)

    root = output_root()
    if root is not None:
        _prune_empty_parents(directory.parent, root.resolve())
    return {"files": removed}


def _prune_empty_parents(start: Path, base: Path) -> None:
    """媒体删光后顺手清掉空的祖先目录，到产物根目录为止。"""
    current = start
    while current != base and current.is_dir():
        try:
            current.relative_to(base)
        except ValueError:
            return
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent


def public_product(product: dict[str, Any]) -> dict[str, Any]:
    """去掉内部排序字段，返回可直接响应给前端的字典。"""
    return {key: value for key, value in product.items() if not key.startswith("_")}
