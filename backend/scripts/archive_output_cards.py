"""把项目根 outputs/ 下的卡片产物归档到指定账号的「我的空间」。

背景：
- `output_index.output_root()` 只认 backend/outputs（存在即优先），项目根 outputs/
  下的产物既不会被扫描，也无法通过 /outputs 静态路径访问。
- 本脚本把卡片图片复制进 backend/outputs/_archived/<user_id>/...（目录以下划线
  开头，`scan_products` 会跳过，因此不会与创作产物条目重复展示；但 StaticFiles
  挂载的是整个 backend/outputs，图片仍可通过 /outputs/... 访问），
  再调 `save_card_draft_artifact` 落成带 owner_user_id 的创作产物。

用法：
    python scripts/archive_output_cards.py --user-id <uid> [--source <dir>] [--dry-run]
"""
from __future__ import annotations

import argparse
import asyncio
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
ARCHIVE_PREFIX = "_archived"
URL_PREFIX = "/outputs"


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _backend_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _template_of(group: str) -> str:
    """组名首段即卡片模板风格，例如 luxe-e2e-v3 -> luxe。"""
    return group.split("-")[0] or "free_form"


def _collect_groups(source: Path) -> list[tuple[str, list[Path]]]:
    """一个直接含图片的子目录 = 一组卡片。"""
    groups: list[tuple[str, list[Path]]] = []
    if not source.is_dir():
        return groups
    for directory in sorted(p for p in source.iterdir() if p.is_dir()):
        if directory.name.startswith("_"):
            continue
        images = sorted(
            (p for p in directory.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS),
            key=lambda p: p.name,
        )
        if images:
            groups.append((directory.name, images))
    return groups


def _copy_images(group: str, images: list[Path], target_dir: Path) -> list[str]:
    """复制图片并返回对应的 /outputs 访问路径（顺序与 images 一致）。"""
    target_dir.mkdir(parents=True, exist_ok=True)
    urls: list[str] = []
    for image in images:
        destination = target_dir / image.name
        if not destination.exists() or destination.stat().st_size != image.stat().st_size:
            shutil.copy2(image, destination)
        rel = destination.relative_to(_backend_root() / "outputs").as_posix()
        urls.append(f"{URL_PREFIX}/{rel}")
    return urls


def _build_card_draft(group: str, urls: list[str]) -> dict:
    pages = [
        {
            "id": f"page-{index + 1}",
            "type": "image",
            "imageUrl": url,
            "pngUrls": [url],
        }
        for index, url in enumerate(urls)
    ]
    return {
        "title": group,
        "suggested_template": _template_of(group),
        "pages": pages,
    }


async def _archive(source: Path, user_id: str, dry_run: bool) -> int:
    from app.services.creative_artifact import save_card_draft_artifact

    groups = _collect_groups(source)
    if not groups:
        print(f"[archive] 未找到可归档的卡片目录: {source}")
        return 1

    archive_root = _backend_root() / "outputs" / ARCHIVE_PREFIX / user_id / source.name
    print(f"[archive] 源目录: {source}")
    print(f"[archive] 目标账号: {user_id}")
    print(f"[archive] 图片落地: {archive_root}")
    print(f"[archive] 共 {len(groups)} 组卡片")

    failed = 0
    for group, images in groups:
        urls = _copy_images(group, images, archive_root / group) if not dry_run else []
        if dry_run:
            print(f"  - {group}: {len(images)} 页（dry-run，未复制未落盘）")
            continue
        artifact_id = await save_card_draft_artifact(
            user_id=user_id,
            card_draft=_build_card_draft(group, urls),
            work_id=f"archive:{source.name}:{group}",
            platform="xiaohongshu",
        )
        if artifact_id:
            print(f"  - {group}: {len(urls)} 页 -> artifact {artifact_id}")
        else:
            failed += 1
            print(f"  - {group}: 落盘失败（save_card_draft_artifact 返回空）")
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="把 outputs/ 下的卡片归档到指定账号")
    parser.add_argument("--user-id", required=True, help="目标账号 user_id")
    parser.add_argument(
        "--source",
        default=str(_project_root() / "outputs" / "e2e-card-verify"),
        help="卡片源目录（默认项目根 outputs/e2e-card-verify）",
    )
    parser.add_argument("--dry-run", action="store_true", help="只打印计划，不复制不落盘")
    args = parser.parse_args()
    return asyncio.run(_archive(Path(args.source).expanduser(), args.user_id, args.dry_run))


if __name__ == "__main__":
    raise SystemExit(main())