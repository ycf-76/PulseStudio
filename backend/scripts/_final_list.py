#!/usr/bin/env python3
"""最终清单：确认 7 组卡片在用户空间各有一条。"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

USER_ID = "OwdN5Jwjo_9XA_iAvuht-Q"


async def main() -> int:
    from app.services.creative_artifact import list_artifacts_for_user

    rows = await list_artifacts_for_user(USER_ID, limit=60)
    rows.sort(key=lambda r: float(r.get("updated_at") or 0), reverse=True)

    lines = [f"user={USER_ID}  total_artifacts={len(rows)}", ""]
    titles: dict[str, int] = {}
    for r in rows:
        t = str(r.get("title") or "")
        titles[t] = titles.get(t, 0) + 1
        lines.append(
            f"{r.get('artifact_id'):16s} pages={int(r.get('page_count') or 0):2d}  {t}"
        )

    dup = {t: c for t, c in titles.items() if c > 1}
    lines.append("")
    lines.append(f"重复标题: {dup if dup else '无'}")

    out = ROOT.parent / "ppt" / "_save_probe" / "final_list.txt"
    out.write_text("\n".join(lines), encoding="utf-8")
    return 0


sys.exit(asyncio.run(main()))