"""一次性校验：归档卡片是否已进入指定账号的「我的空间」。"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import output_index as oi
from app.services.creative_artifact import list_artifacts_for_user, load_artifact

UID = "OwdN5Jwjo_9XA_iAvuht-Q"
WORK_PREFIX = "archive:e2e-card-verify:"


async def main() -> int:
    all_items = await list_artifacts_for_user(UID, limit=200)
    items = [
        it for it in all_items
        if str((it.get("source") or {}).get("work_id") or "").startswith(WORK_PREFIX)
    ]
    print("=== 1. 归属可见性 ===")
    print("该账号可见产物总数 =", len(all_items), "| 其中归档卡片 =", len(items))
    owner_bad = 0
    for it in items:
        if it["owner_user_id"] != UID:
            owner_bad += 1
        print("  {} | {:16s} | pages={} | {}".format(
            it["artifact_id"], it["title"], it["page_count"], it["cover_url"]))
    print("归属错误条数 =", owner_bad)

    print()
    print("=== 2. 是否与 outputs 扫描重复 ===")
    dup = [p for p in oi.scan_products() if "_archived" in p["rel_path"]]
    print("scan_products 中 _archived 条目数 =", len(dup), "(应为 0)")

    print()
    print("=== 3. 图片文件与 URL 一致性 ===")
    root = oi.backend_root() / "outputs"
    total = 0
    bad = []
    for it in items:
        art = load_artifact(it["artifact_id"])
        if art is None:
            bad.append(("产物无法加载", it["artifact_id"]))
            continue
        for page in art.storyboard:
            total += 1
            url = page.image_url
            if not url.startswith("/outputs/"):
                bad.append(("URL 前缀异常", url))
                continue
            if not (root / url[len("/outputs/"):]).is_file():
                bad.append(("文件缺失", url))
    print("校验页数 =", total, "异常 =", len(bad))
    for b in bad[:8]:
        print("  ", b)

    print()
    print("=== 4. 静态访问路径抽查 ===")
    if items:
        art = load_artifact(items[0]["artifact_id"])
        if art and art.storyboard:
            print("  封面 URL =", art.storyboard[0].image_url)

    return 0 if not owner_bad and not dup and not bad else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))