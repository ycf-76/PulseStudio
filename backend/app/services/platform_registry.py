"""平台分类单一来源。

设置页账号管理、我的空间平台筛选、产物平台推断共用同一份清单，
避免各模块各自维护 PLATFORM_LABELS 导致分类不一致。
"""
from __future__ import annotations

SUPPORTED_PLATFORMS: tuple[str, ...] = ("xiaohongshu", "douyin", "bilibili")

PLATFORM_LABELS: dict[str, str] = {
    "xiaohongshu": "小红书",
    "douyin": "抖音",
    "bilibili": "B站",
    "wechat_mp": "微信",
    "instagram": "Instagram",
    "threads": "Threads",
    "kuaishou": "快手",
}


def platform_label(platform: str) -> str:
    """平台标识 → 展示名，未知标识原样返回。"""
    return PLATFORM_LABELS.get(platform, platform)


def order_platforms(platforms: list[str], counts: dict[str, int] | None = None) -> list[str]:
    """排序：已绑定/已支持平台优先，其次按作品数量降序，最后按标识字母序。"""
    counts = counts or {}
    supported_index = {p: i for i, p in enumerate(SUPPORTED_PLATFORMS)}
    return sorted(
        set(platforms),
        key=lambda p: (
            0 if p in supported_index else 1,
            supported_index.get(p, len(supported_index)),
            -counts.get(p, 0),
            p,
        ),
    )