"""工作区媒体产物自动发布。

问题（2026-11-08 实证）：口播成片由 bash/ffmpeg 直接写在工作区根目录
（`output_ai_koubo_final.mp4` 4.96MB、36.9s、h264+aac 完好），
但 `outputs/` 才是「我的空间」的扫描根，成片既不在那里、也没有作为
产物字段挂到 final_output，前端没有任何可渲染的地址——用户只能看到
一句「文件在 xxx.mp4」的文字，所以「生成了但看不见」。

对策：回合结束时收割本轮新增的媒体文件，复制到
`outputs/<user_id>/agent_media/<文件名>/`，返回 `/outputs/...` 可直连
渲染的地址。发布幂等：同名同大小的目标已存在就复用，不重复复制。
"""
from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path
from typing import Any

from app.services.output_index import output_root

logger = logging.getLogger(__name__)

VIDEO_EXTS = {".mp4", ".mov", ".webm", ".m4v"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".flac"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
MEDIA_EXTS = VIDEO_EXTS | AUDIO_EXTS | IMAGE_EXTS

# 小于 1KB 的媒体文件必然是坏文件（0/1 字节的 mp4 无法解封装），不发布
MIN_BYTES = 1024
SKIP_DIRS = {"__pycache__", "node_modules", ".git", ".idea", ".vscode", "outputs"}
PUBLISH_SUBDIR = "agent_media"
# 排序优先级：成片/音频优先于零散截图，保证 limit 截断时先保住主产物
_KIND_PRIORITY = {"video": 0, "audio": 1, "image": 2}


def _safe_name(name: str, fallback: str = "media") -> str:
    cleaned = re.sub(r"[^\w\u4e00-\u9fff\-]+", "_", name).strip("_")
    return cleaned[:60] or fallback


def _kind(suffix: str) -> str:
    suffix = suffix.lower()
    if suffix in VIDEO_EXTS:
        return "video"
    if suffix in AUDIO_EXTS:
        return "audio"
    return "image"


def _humanize(stem: str) -> str:
    return re.sub(r"[_\-\s]+", " ", stem).strip() or "媒体产物"


def _iter_workspace_media(root: Path, since: float) -> list[Path]:
    """列出工作区里本轮新增的媒体文件（跳过隐藏目录/产物目录）。

    用户可以把任意项目文件夹绑定成会话工作区，所以遍历必须设上界：
    最多 3 层目录、最多检查 3000 个条目——宁可漏掉深层产物，也不能让
    一次对话收尾卡在几十万文件的遍历上。
    """
    found: list[Path] = []
    if not root.is_dir():
        return found
    max_depth = 3
    max_entries = 3000
    checked = 0
    for path in root.rglob("*"):
        checked += 1
        if checked > max_entries:
            logger.debug("[agent_media] 工作区条目超过上限 %d，停止扫描", max_entries)
            break
        try:
            if not path.is_file():
                continue
        except OSError:
            continue
        rel_parts = path.relative_to(root).parts
        if len(rel_parts) - 1 > max_depth:
            continue
        if any(part.startswith(".") or part in SKIP_DIRS for part in rel_parts[:-1]):
            continue
        if path.suffix.lower() not in MEDIA_EXTS:
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        if stat.st_size < MIN_BYTES or stat.st_mtime < since:
            continue
        found.append(path)
    found.sort(
        key=lambda p: (
            _KIND_PRIORITY.get(_kind(p.suffix), 3),
            -p.stat().st_mtime,
        )
    )
    return found


def _publish_one(source: Path, root: Path, user_dir: str) -> dict[str, Any] | None:
    """把单个媒体文件发布到 outputs/<user>/agent_media/<stem>/，返回可渲染条目。"""
    stem = _safe_name(source.stem)
    target_dir = root / user_dir / PUBLISH_SUBDIR / stem
    target = target_dir / source.name
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        if not (target.is_file() and target.stat().st_size == source.stat().st_size):
            shutil.copy2(source, target)
    except OSError as exc:
        logger.warning("[agent_media] 发布失败 %s: %s", source, exc)
        return None
    rel = target.relative_to(root).as_posix()
    return {
        "title": _humanize(source.stem),
        "kind": _kind(source.suffix),
        "file_name": source.name,
        "source_path": str(source),
        "output_path": str(target),
        "url": f"/outputs/{rel}",
        "size": target.stat().st_size,
    }


async def publish_workspace_media(
    since: float,
    user_id: str,
    limit: int = 3,
) -> list[dict[str, Any]]:
    """发布本轮新增的工作区媒体产物（新→旧，最多 limit 条）。

    失败一律降级为空列表：产物发布不能连累这一轮对话本身。
    """
    root = output_root()
    if root is None:
        return []
    ws_roots = _workspace_roots()

    user_dir = _safe_name(user_id or "default", fallback="default")
    published: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    for ws in ws_roots:
        if root == ws or root in ws.parents:
            continue
        for source in _iter_workspace_media(ws, since):
            if source.name in seen_names:
                continue
            item = _publish_one(source, root, user_dir)
            if item:
                seen_names.add(source.name)
                published.append(item)
            if len(published) >= limit:
                break
        if len(published) >= limit:
            break
    if published:
        logger.info(
            "[agent_media] 已发布 %d 个工作区媒体产物: %s",
            len(published),
            [item["url"] for item in published],
        )
    return published


def _workspace_roots() -> list[Path]:
    """可能的工作区根（去重、保序）。

    平台里存在两套约定：bash/glob/grep 走 `backend/data/workspace`，
    file_read/file_write 走 `app/data/workspace`（2026-11-08 实证：成片落在
    前者、发布逻辑扫后者 → 收割为空）。这里把会话绑定根、环境变量根与两套
    默认根全部纳入，任一处写了成片都能收割到。
    """
    roots: list[Path] = []

    def _add(candidate: Path | str | None) -> None:
        if not candidate:
            return
        try:
            resolved = Path(candidate).resolve()
        except OSError:
            return
        if resolved not in roots:
            roots.append(resolved)

    try:
        from app.core.context_vars import current_workspace_root

        _add(current_workspace_root.get())
    except Exception:  # noqa: BLE001 - contextvar 不可用不影响兜底根
        pass
    try:
        from app.sandbox.win_native import default_workspace_root

        _add(default_workspace_root())
    except Exception:  # noqa: BLE001
        pass
    try:
        from app.engine.reactor.file_ops_core import workspace_root

        _add(workspace_root())
    except Exception:  # noqa: BLE001
        pass
    return roots