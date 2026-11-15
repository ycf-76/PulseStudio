#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""backfill_artifact_owner.py — 给存量 CreativeArtifact 补 owner_user_id。

产物隔离上线后，归属校验优先看 `owner_user_id`，为空时才回落到会话归属。
存量 JSON 大多是空值，每次访问都要多查一次会话表；会话被删后就彻底不可验证。
本脚本把归属一次性固化下来，让后续访问走快路径且不依赖会话是否还存在。

归属推导顺序：
  1. source.session_id  → chat_sessions.user_id
  2. source.work_id     → 该作品下所有会话若同属一个用户，取该用户
  3. 都推不出来 → 跳过并计入 unresolved（脚本不猜、不写空值）

用法：
    python scripts/backfill_artifact_owner.py --dry-run
    python scripts/backfill_artifact_owner.py
    python scripts/backfill_artifact_owner.py --quarantine   # 顺带隔离无法解析的损坏产物
    python scripts/backfill_artifact_owner.py --store data/creative_artifacts
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app.db.models import ChatSession  # noqa: E402
from app.db.session import AsyncSessionLocal, engine  # noqa: E402

DEFAULT_STORE = Path(__file__).resolve().parents[1] / "data" / "creative_artifacts"


async def _owner_by_session(session_id: str) -> str:
    if not session_id:
        return ""
    async with AsyncSessionLocal() as db:
        row = await db.get(ChatSession, session_id)
        return str(row.user_id) if row else ""


async def _owner_by_work(work_id: str) -> str:
    if not work_id:
        return ""
    async with AsyncSessionLocal() as db:
        rows = (await db.scalars(select(ChatSession).where(ChatSession.work_id == work_id))).all()
    owners = {str(r.user_id) for r in rows}
    # 同一作品出现多个归属用户时不猜，留给人工确认
    return owners.pop() if len(owners) == 1 else ""


async def _resolve(data: dict[str, Any]) -> tuple[str, str]:
    source = data.get("source") or {}
    owner = await _owner_by_session(str(source.get("session_id") or ""))
    if owner:
        return owner, "session"
    owner = await _owner_by_work(str(source.get("work_id") or ""))
    if owner:
        return owner, "work"
    return "", ""


def _write_atomic(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_name(f"{path.name}.tmp-{uuid.uuid4().hex[:8]}")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


async def run(store: Path, dry_run: bool, quarantine: bool) -> dict[str, int]:
    stats = {"scanned": 0, "already_owned": 0, "filled": 0, "unresolved": 0, "broken": 0}
    if not store.exists():
        print(f"产物目录不存在：{store}")
        return stats

    quarantine_dir = store / "corrupted"

    for path in sorted(store.glob("*.json")):
        stats["scanned"] += 1
        try:
            # 与服务层同款宽松读取：BOM + 字符串内裸控制字符都不算损坏，
            # 否则这些本来可用的产物会被误判成 corrupted 而被隔离。
            data = json.loads(path.read_text(encoding="utf-8-sig"), strict=False)
        except (OSError, ValueError) as exc:
            stats["broken"] += 1
            print(f"[broken] {path.name}: {exc}")
            if quarantine and not dry_run:
                quarantine_dir.mkdir(parents=True, exist_ok=True)
                os.replace(path, quarantine_dir / path.name)
                print(f"[quarantine] {path.name} -> corrupted/")
            continue

        if data.get("owner_user_id"):
            stats["already_owned"] += 1
            continue

        owner, via = await _resolve(data)
        if not owner:
            stats["unresolved"] += 1
            source = data.get("source") or {}
            print(
                f"[unresolved] {path.name}: session={source.get('session_id', '')!r} "
                f"work={source.get('work_id', '')!r}"
            )
            continue

        stats["filled"] += 1
        print(f"[fill] {path.name}: owner={owner} via={via}{' (dry-run)' if dry_run else ''}")
        if dry_run:
            continue
        data["owner_user_id"] = owner
        _write_atomic(path, data)

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="补全存量创作产物的 owner_user_id")
    parser.add_argument("--store", default=str(DEFAULT_STORE), help="creative_artifacts 目录")
    parser.add_argument("--dry-run", action="store_true", help="只打印将要补全的项，不落盘")
    parser.add_argument(
        "--quarantine",
        action="store_true",
        help="把无法解析的损坏产物移到 corrupted/ 子目录（不删除，可人工恢复）",
    )
    args = parser.parse_args()

    async def _run() -> dict[str, int]:
        try:
            return await run(Path(args.store), args.dry_run, args.quarantine)
        finally:
            await engine.dispose()

    stats = asyncio.run(_run())
    print(
        "扫描 {scanned} 个产物：已归属 {already_owned}，补全 {filled}，"
        "无法判定 {unresolved}，损坏 {broken}".format(**stats)
    )


if __name__ == "__main__":
    main()