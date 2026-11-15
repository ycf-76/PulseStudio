"""进程内滑动窗口限流（BYOK 敏感接口专用）。

为什么需要：`PUT /credentials` 与 `verify` 会让后端「拿着 Key 去打第三方 API」，
无限流时单用户可把服务端变成放大攻击的跳板，也会烧掉自己的额度。

实现取舍：单进程内存计数，不引入 Redis 依赖。多副本部署时应换成
`settings.redis_url` 上的共享计数（接口保持不变，替换 `check_rate_limit` 内部实现即可）。
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque


class RateLimitExceeded(Exception):
    """触发限流。retry_after 为建议等待秒数。"""

    def __init__(self, retry_after: int) -> None:
        super().__init__(f"请求过于频繁，请 {retry_after} 秒后重试")
        self.retry_after = retry_after


_LOCK = threading.Lock()
_BUCKETS: dict[str, deque[float]] = defaultdict(deque)


def check_rate_limit(
    key: str,
    *,
    limit: int,
    window_seconds: int,
    now: float | None = None,
) -> None:
    """滑动窗口限流：窗口内第 limit+1 次调用抛 RateLimitExceeded。

    Args:
        key: 限流维度（如 `cred-save:{user_id}:{provider}`）。
        limit: 窗口内允许的最大次数；<=0 表示不限制。
        window_seconds: 窗口长度（秒）。
        now: 注入时间戳，仅供测试使用。
    """
    if limit <= 0:
        return

    ts = time.monotonic() if now is None else now
    with _LOCK:
        bucket = _BUCKETS[key]
        while bucket and ts - bucket[0] >= window_seconds:
            bucket.popleft()
        if len(bucket) >= limit:
            retry_after = max(1, int(window_seconds - (ts - bucket[0])))
            raise RateLimitExceeded(retry_after)
        bucket.append(ts)


def reset_rate_limits() -> None:
    """清空所有计数（测试或运维手动解封时使用）。"""
    with _LOCK:
        _BUCKETS.clear()
