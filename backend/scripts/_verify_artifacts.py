#!/usr/bin/env python3
"""紧凑列出指定用户的创作产物，用于识别重复。"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

USER_ID = "OwdN5Jwjo_9XA_iAvuht-Q"


async def main() -> int:
    import time

    from app.services.creative_artifact import list_artifacts_for_user

    rows = await list_artifacts_for_user(USER_ID, limit=60)
    rows.sort(key=lambda r: float(r.get("updated_at") or 0), reverse=True)
    print(f"total={len(rows)}")
    print(f"{'artifact_id':16s} {'updated':20s} {'pg':>3s}  title")
    for r in rows:
        ts = float(r.get("updated_at") or 0)
        stamp = time.strftime("%H:%M:%S", time.localtime(ts))
        print(
            f"{r.get('artifact_id'):16s} {stamp:20s} {int(r.get('page_count') or 0):3d}  "
            f"{str(r.get('title'))[:44]}"
        )
    return 0


sys.exit(asyncio.run(main()))