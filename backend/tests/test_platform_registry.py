from app.services.platform_registry import (
    PLATFORM_LABELS,
    SUPPORTED_PLATFORMS,
    order_platforms,
    platform_label,
)


def test_supported_platforms_match_labels():
    for platform in SUPPORTED_PLATFORMS:
        assert platform in PLATFORM_LABELS


def test_platform_label_known_and_unknown():
    assert platform_label("xiaohongshu") == "小红书"
    assert platform_label("douyin") == "抖音"
    assert platform_label("bilibili") == "B站"
    assert platform_label("unknown_platform") == "unknown_platform"


def test_order_platforms_puts_supported_first():
    result = order_platforms(["kuaishou", "bilibili", "xiaohongshu", "douyin"], {})
    assert result[:3] == ["xiaohongshu", "douyin", "bilibili"]
    assert result[3] == "kuaishou"


def test_order_platforms_dedupes_and_keeps_all():
    result = order_platforms(["xiaohongshu", "xiaohongshu", "douyin"], {})
    assert result == ["xiaohongshu", "douyin"]


def test_order_platforms_unsupported_sorted_by_count_desc():
    counts = {"kuaishou": 3, "wechat_mp": 9, "instagram": 0}
    result = order_platforms(["kuaishou", "wechat_mp", "instagram"], counts)
    assert result == ["wechat_mp", "kuaishou", "instagram"]
