"""详情页刷新策略回归测试（单轮预算 / 冷却期 / 重试上限）。

覆盖问题：图集仍是小红书 CDN 外链的笔记每轮同步都会被判定"需要拉详情页"，
旧逻辑既不限条数也不限重试，32 条过期图集每轮全量重抓 → 跑满 900s 被 terminate
→ 结果行丢失 → 同步"总是出错"。策略模块把刷新改成有预算、有冷却、有上限的渐进过程。

文档依据：docs/FEATURE_平台账号管理与作品数据回收.md#7.2
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from capabilities.scrapling.spiders import detail_refresh as dr  # noqa: E402

CDN = "https://sns-img.xhscdn.com/{}"
LOCAL = "/uploads/topic_images/{}"


# ═══════════════════════════════════════════════════════════════════
# 1. needs_detail_refresh：本地化即完成，冷却与重试上限生效
# ═══════════════════════════════════════════════════════════════════


def test_all_local_images_need_no_refresh():
    """测试: needs_detail_refresh
    场景: 图集已全部落本地（本地文件不会过期）
    预期: 不需要再拉详情页，即使重试次数还没到上限
    """
    images = [LOCAL.format("n1_0.jpg"), LOCAL.format("n1_1.jpg")]
    assert dr.needs_detail_refresh(images, dr.RefreshState(attempts=0)) is False


def test_missing_images_need_refresh():
    """测试: needs_detail_refresh
    场景: 库里还没有任何图集（首次采集或历史数据缺失）
    预期: 需要拉详情页
    """
    assert dr.needs_detail_refresh(None) is True
    assert dr.needs_detail_refresh([]) is True


def test_expired_cdn_images_need_refresh_without_history():
    """测试: needs_detail_refresh
    场景: 图集全是 CDN 外链，且从未尝试过刷新
    预期: 需要拉详情页
    """
    assert dr.needs_detail_refresh([CDN.format("a.jpg"), CDN.format("b.jpg")]) is True


def test_cooldown_blocks_recent_attempt():
    """测试: needs_detail_refresh
    场景: 1 小时前刚抓过，冷却期 12 小时
    预期: 本轮不再重复抓，避免每轮都重抓同一条
    """
    now = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
    state = dr.RefreshState(last_attempt_at=now - timedelta(hours=1), attempts=1)
    assert dr.needs_detail_refresh([CDN.format("a.jpg")], state, now=now) is False


def test_cooldown_expired_allows_retry():
    """测试: needs_detail_refresh
    场景: 上次抓取已过去 13 小时（超过 12 小时冷却）
    预期: 允许再抓一次
    """
    now = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
    state = dr.RefreshState(last_attempt_at=now - timedelta(hours=13), attempts=1)
    assert dr.needs_detail_refresh([CDN.format("a.jpg")], state, now=now) is True


def test_attempt_limit_stops_endless_refresh():
    """测试: needs_detail_refresh
    场景: 已自动重试 3 次（达到上限），图集仍是外链
    预期: 不再自动重抓，避免永久重抓；需要 force 才继续
    """
    now = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
    state = dr.RefreshState(last_attempt_at=now - timedelta(days=3), attempts=3)
    assert dr.needs_detail_refresh([CDN.format("a.jpg")], state, now=now) is False


def test_force_overrides_cooldown_and_attempt_limit():
    """测试: needs_detail_refresh
    场景: 用户点了全量刷新（force），该笔记已达到重试上限且在冷却期内
    预期: 仍然抓——这是用户主动要求把过期图文重新更新
    """
    now = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
    state = dr.RefreshState(last_attempt_at=now - timedelta(minutes=10), attempts=5)
    assert dr.needs_detail_refresh([CDN.format("a.jpg")], state, now=now, force=True) is True


def test_force_still_skips_localized_images():
    """测试: needs_detail_refresh
    场景: 图集已本地化，用户点全量刷新
    预期: 仍然跳过——本地文件不会过期，重抓只是白花时间
    """
    images = [LOCAL.format("n1_0.jpg"), LOCAL.format("n1_1.jpg")]
    assert dr.needs_detail_refresh(images, force=True) is False


def test_naive_datetime_is_treated_as_utc():
    """测试: needs_detail_refresh
    场景: MySQL 取回的 detail_refresh_at 是不带时区的 naive datetime
    预期: 按 UTC 处理，不抛 TypeError（naive 与 aware 不能直接相减）
    """
    now = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
    state = dr.RefreshState(last_attempt_at=datetime(2026, 11, 5, 1, 0), attempts=1)
    assert dr.needs_detail_refresh([CDN.format("a.jpg")], state, now=now) is False


# ═══════════════════════════════════════════════════════════════════
# 2. select_detail_targets：单轮预算 + 优先级排序
# ═══════════════════════════════════════════════════════════════════


def _candidate(
    note_id: str, attempts: int = 0, last: datetime | None = None, source: str = "state"
) -> dr.DetailCandidate:
    return dr.DetailCandidate(
        note_id=note_id,
        url=f"https://www.xiaohongshu.com/explore/{note_id}",
        source=source,
        attempts=attempts,
        last_attempt_at=last,
    )


def test_budget_truncates_candidates():
    """测试: select_detail_targets
    场景: 32 条过期图集都进入候选，单轮预算 12
    预期: 只返回 12 条，其余 20 条推迟到下一轮（单轮耗时稳定在超时线以内）
    """
    candidates = [_candidate(f"n{i}") for i in range(32)]
    targets, deferred = dr.select_detail_targets(candidates, budget=12)
    assert len(targets) == 12
    assert deferred == 20


def test_budget_less_than_candidates_returns_all():
    """测试: select_detail_targets
    场景: 候选数少于预算
    预期: 全部返回，没有推迟
    """
    candidates = [_candidate(f"n{i}") for i in range(5)]
    targets, deferred = dr.select_detail_targets(candidates, budget=12)
    assert len(targets) == 5
    assert deferred == 0


def test_zero_budget_means_unlimited():
    """测试: select_detail_targets
    场景: budget<=0（全量刷新 / 手工修复）
    预期: 不截断，全部返回
    """
    candidates = [_candidate(f"n{i}") for i in range(32)]
    targets, deferred = dr.select_detail_targets(candidates, budget=0)
    assert len(targets) == 32
    assert deferred == 0


def test_targets_prefer_never_attempted_notes():
    """测试: select_detail_targets
    场景: 部分笔记已重试过，部分是全新候选
    预期: 尝试次数少的优先，保证每轮都在推进未被修过的笔记
    """
    now = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
    candidates = [
        _candidate("retried", attempts=2, last=now - timedelta(days=2)),
        _candidate("fresh", attempts=0),
    ]
    targets, _ = dr.select_detail_targets(candidates, budget=1)
    assert [t.note_id for t in targets] == ["fresh"]


def test_targets_prefer_stale_attempt_then_dom_links():
    """测试: select_detail_targets
    场景: 两条同样重试过的笔记，一条上次更早，且存在 DOM 链接候选
    预期: 上次更早的优先；同等条件下 DOM 链接优先（href 自带新 xsec_token）
    """
    now = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
    candidates = [
        _candidate("recent", attempts=1, last=now - timedelta(hours=2)),
        _candidate("stale", attempts=1, last=now - timedelta(hours=20)),
        _candidate("dom", attempts=1, last=now - timedelta(hours=20), source="dom"),
    ]
    targets, _ = dr.select_detail_targets(candidates, budget=2)
    assert [t.note_id for t in targets] == ["dom", "stale"]


def test_empty_candidates():
    """测试: select_detail_targets
    场景: 没有需要刷新的笔记（图集都已本地化）
    预期: 返回空列表且不报错
    """
    assert dr.select_detail_targets([], budget=12) == ([], 0)
