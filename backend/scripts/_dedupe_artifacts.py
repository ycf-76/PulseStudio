#!/usr/bin/env python3
"""清理本次落盘产生的重复产物（19:07-19:09 那一批旧文件），保留最新版。"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STORE = ROOT / "data" / "creative_artifacts"

STALE = [
    "ca_d235a06130a6",
    "ca_721cd1e2da0d",
    "ca_443e76954c8a",
    "ca_74d90149af62",
    "ca_c728c448096c",
]


def main() -> int:
    print("删除前核对：")
    for aid in STALE:
        p = STORE / f"{aid}.json"
        if not p.exists():
            print(f"  {aid}  已不存在，跳过")
            continue
        data = json.loads(p.read_text(encoding="utf-8-sig"), strict=False)
        print(
            f"  {aid}  title={data.get('brief', {}).get('title')}  "
            f"pages={len(data.get('storyboard') or [])}  "
            f"owner={data.get('owner_user_id')}"
        )

    print("\n执行删除：")
    for aid in STALE:
        p = STORE / f"{aid}.json"
        if p.exists():
            p.unlink()
            print(f"  removed {aid}.json")
    print(f"\n剩余产物文件数 = {len(list(STORE.glob('*.json')))}")
    return 0


sys.exit(main())