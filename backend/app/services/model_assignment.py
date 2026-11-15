"""模型分配服务：把智能体 / 技能的 LLM 选择统一收拢到「模型配置」。

职责：
- 读写用户级模型分配（DB 为唯一事实来源）
- 维护进程内只读缓存，供技能执行时零成本查询（技能执行是高频路径）
- 启动时全量预热缓存，写入后同步刷新缓存

key 约定：
- default → 全局默认模型（target_key 为空）
- agent   → target_key 为 agent_id
- skill   → target_key 为 "node_type/name"
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import UserModelAssignment

logger = logging.getLogger(__name__)

DEFAULT_TARGET = "default"
AGENT_TARGET = "agent"
SKILL_TARGET = "skill"

# user_id → { ("default", "") | ("agent", agent_id) | ("skill", "node/name"): model }
_ASSIGNMENT_CACHE: dict[str, dict[tuple[str, str], str]] = {}


def _cache_put(user_id: str, target_type: str, target_key: str, model: str) -> None:
    bucket = _ASSIGNMENT_CACHE.setdefault(user_id, {})
    if model:
        bucket[(target_type, target_key)] = model
    else:
        bucket.pop((target_type, target_key), None)


def _cache_get(user_id: str, target_type: str, target_key: str) -> str:
    if not user_id:
        return ""
    return _ASSIGNMENT_CACHE.get(user_id, {}).get((target_type, target_key), "")


def get_cached_default_model(user_id: str) -> str:
    """全局默认模型（未单独指定时使用）。"""
    return _cache_get(user_id, DEFAULT_TARGET, "")


def get_cached_agent_model(user_id: str, agent_id: str) -> str:
    return _cache_get(user_id, AGENT_TARGET, agent_id)


def get_cached_skill_model(user_id: str, node_type: str, name: str) -> str:
    """技能专属模型；为空则调用方回落到默认模型。"""
    return _cache_get(user_id, SKILL_TARGET, f"{node_type}/{name}")


def get_cached_skill_model_with_default(user_id: str, node_type: str, name: str) -> str:
    return get_cached_skill_model(user_id, node_type, name) or get_cached_default_model(user_id)


async def load_all_assignments() -> None:
    """启动时全量预热缓存。DB 不可用时静默跳过（缓存保持空，不阻断启动）。"""
    from app.db.session import AsyncSessionLocal

    try:
        async with AsyncSessionLocal() as session:
            stmt = select(UserModelAssignment).where(UserModelAssignment.model != "")
            result = await session.execute(stmt)
            rows = list(result.scalars().all())
    except Exception as e:
        logger.warning(f"[model_assignment] warmup skipped: {e}")
        return

    _ASSIGNMENT_CACHE.clear()
    for row in rows:
        _cache_put(row.user_id, row.target_type, row.target_key, row.model)
    logger.info(f"[model_assignment] warmed {len(rows)} assignment(s)")


async def list_assignments(
    db: AsyncSession, user_id: str
) -> dict[tuple[str, str], str]:
    """读取某用户全部分配（DB 为准）。"""
    stmt = select(UserModelAssignment).where(UserModelAssignment.user_id == user_id)
    result = await db.execute(stmt)
    rows = list(result.scalars().all())
    return {(r.target_type, r.target_key): r.model for r in rows if r.model}


async def save_assignment(
    db: AsyncSession, user_id: str, target_type: str, target_key: str, model: str
) -> None:
    """写入/更新分配；model 为空时删除该条分配（回到未指定）。"""
    stmt = select(UserModelAssignment).where(
        UserModelAssignment.user_id == user_id,
        UserModelAssignment.target_type == target_type,
        UserModelAssignment.target_key == target_key,
    )
    result = await db.execute(stmt)
    existing = result.scalar_one_or_none()

    if not model:
        if existing is not None:
            await db.delete(existing)
            await db.commit()
        _cache_put(user_id, target_type, target_key, "")
        return

    if existing is None:
        from app.db.models import generate_ulid

        db.add(UserModelAssignment(
            id=generate_ulid(),
            user_id=user_id,
            target_type=target_type,
            target_key=target_key,
            model=model,
        ))
    else:
        existing.model = model
    await db.commit()
    _cache_put(user_id, target_type, target_key, model)
