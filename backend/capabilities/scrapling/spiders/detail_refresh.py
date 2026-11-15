"""详情页刷新策略：单轮预算、冷却期与重试上限。

背景（2026-11 实测）：同步时每条详情页都要开一次反爬浏览器页面并做图集本地化，
单条 10~25s；而小红书 CDN 签名约 2 天过期，图集一旦是外链就会永远被判定"需要刷新"。
旧逻辑既不限条数也不限重试，32 条过期图集每轮都要全量重抓 → 跑满 900s 被 terminate
→ 子进程结果行丢失 → 同步"总是出错"。

这里把刷新做成有预算、有冷却、有上限的渐进过程：
  - 每轮最多抓 budget 条，未轮到的留到下次同步（不会整批失败）
  - 同一笔记在冷却期内不重复抓
  - 同一笔记累计 attempts 超过上限后放弃自动重试（force 可绕过）
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

DEFAULT_COOLDOWN_HOURS = 12.0
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BUDGET = 12


@dataclass(frozen=True)
class RefreshState:
    """某条笔记的详情页刷新历史。"""

    last_attempt_at: datetime | None = None
    attempts: int = 0


@dataclass(frozen=True)
class DetailCandidate:
    """一个待抓取的详情页候选。"""

    note_id: str
    url: str
    source: str = "state"  # state=主页 __INITIAL_STATE__ 卡片；dom=滚动后 DOM 链接
    attempts: int = 0
    last_attempt_at: datetime | None = None
    likes_hint: int = 0
    from_card: bool = True


def _as_utc(value: datetime | None) -> datetime | None:
    """统一成带时区的 UTC 时间（MySQL 取回的时间可能是 naive）。"""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _all_local(images: Any) -> bool:
    """图集是否已全部落本地（本地文件不会过期，无需再拉详情页）。"""
    if not images or not isinstance(images, (list, tuple)):
        return False
    from app.services.image_store import is_local_url

    urls = [u for u in images if isinstance(u, str) and u]
    if not urls:
        return False
    return all(is_local_url(u) for u in urls)


def needs_detail_refresh(
    cached_images: Any,
    state: RefreshState | None = None,
    *,
    now: datetime | None = None,
    cooldown_hours: float = DEFAULT_COOLDOWN_HOURS,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    force: bool = False,
) -> bool:
    """是否需要再拉一次详情页。

    规则（按顺序）：
      1. 图集已全部本地 → 不需要（本地文件不会过期）
      2. force（用户点了全量刷新）→ 需要，忽略冷却与重试上限
      3. 累计尝试次数已达上限 → 不需要（避免永久重抓）
      4. 上次尝试仍在冷却期内 → 不需要
      5. 其余 → 需要
    """
    if _all_local(cached_images):
        return False
    if force:
        return True
    if state is None:
        return True
    if state.attempts >= max_attempts:
        return False
    last = _as_utc(state.last_attempt_at)
    if last is None:
        return True
    reference = _as_utc(now) or datetime.now(UTC)
    return reference - last >= timedelta(hours=max(0.0, cooldown_hours))


def select_detail_targets(
    candidates: list[DetailCandidate], budget: int = DEFAULT_BUDGET
) -> tuple[list[DetailCandidate], int]:
    """按优先级排序并截断到单轮预算。

    排序（升序）：尝试次数少 → 上次尝试更早 → DOM 链接优先（href 自带新的
    xsec_token，详情页才打得开）→ note_id 稳定排序。

    budget <= 0 表示不限制（仅手工修复使用）。
    返回 (本轮要抓的候选, 被推迟到下轮的条数)。
    """
    if not candidates:
        return [], 0

    def _epoch(value: datetime | None) -> float:
        dt = _as_utc(value)
        return dt.timestamp() if dt else 0.0

    ordered = sorted(
        candidates,
        key=lambda c: (
            c.attempts,
            _epoch(c.last_attempt_at),
            0 if c.source == "dom" else 1,
            c.note_id,
        ),
    )
    if budget is None or budget <= 0:
        return ordered, 0
    return ordered[:budget], max(0, len(ordered) - budget)
