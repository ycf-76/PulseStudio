"""hotboard_source._parse_hot_response 解析单测（2026-11-03）。

用真实接口形状做离线解析验证（数据形状来自 2026-11-03 宿主网络实测）：
- 知乎 v2：数组混 null 占位、条目用 link 字段、热度是 "2992 万热度" 字符串
- 头条 v2：data 为纯字符串数组
- xxapi 失效：HTTP 200 但 {"code": 980401, ...} → 解析 0 条
"""

from __future__ import annotations

from capabilities.content_search.sources.base import TrendingContent
from capabilities.content_search.sources.hotboard_source import _parse_hot_response


def test_zhihu_v2_null_mixed_and_hot_value_desc():
    """知乎真实响应：null 占位 + link 字段 + 中文热度描述。"""
    data = {
        "code": 200,
        "message": "获取成功",
        "data": [
            {
                "title": "如何看待10元快剪正暴打全国的「设计总监」？",
                "hot_value_desc": "2992 万热度",
                "link": "https://www.zhihu.com/question/123",
            },
            None, None, None,  # 真实响应中的 null 占位
            {
                "title": "怎么看 OpenAI CEO 说最好的内存应该留给数据中心？",
                "hot_value_desc": "122 万热度",
                "link": "https://www.zhihu.com/question/456",
            },
            None, None,
        ],
    }
    items = _parse_hot_response(data)
    assert len(items) == 2  # null 全部跳过
    assert items[0]["title"].startswith("如何看待10元快剪")
    assert items[0]["hot"] == 2992 * 10_000  # "2992 万热度" → 数值
    assert items[0]["url"] == "https://www.zhihu.com/question/123"
    assert items[1]["hot"] == 122 * 10_000


def test_yi_unit_parsed():
    assert _parse_hot_response({"data": [{"title": "x", "hot_value_desc": "1.2亿热度"}]})[0]["hot"] == 120_000_000


def test_toutiao_v2_string_array():
    data = {"code": 200, "data": ["刘延东同志遗体在京火化", "世界互联网大会乌镇峰会今天开幕"]}
    items = _parse_hot_response(data)
    assert len(items) == 2
    assert items[0]["title"] == "刘延东同志遗体在京火化"
    assert items[0]["hot"] == 0


def test_xxapi_error_code_yields_empty():
    """xxapi 失效返回 HTTP 200 + 业务错误码 → 0 条，不抛异常。"""
    data = {"code": 980401, "msg": "token error"}
    assert _parse_hot_response(data) == []


def test_weibo_shape_unchanged():
    """原有微博形状（数值 hot）不受解析改动影响。"""
    data = {"code": 200, "data": [{"title": "话题", "hot": 3456789, "url": "https://s.weibo.com/x"}]}
    items = _parse_hot_response(data)
    assert items[0]["hot"] == 3456789
    assert items[0]["url"] == "https://s.weibo.com/x"


def _make_hotboard(titles: list[str]):
    """构造一个不联网的 HotboardSource，直接注入榜单数据。"""
    from capabilities.content_search.sources.hotboard_source import HotboardSource, _normalize

    src = HotboardSource("hb-test", "测试热榜", "https://example.com/hot")
    src._get_all_cached = lambda: _async_value([
        TrendingContent(
            platform="hb-test",
            content_id=f"hotboard_hb-test_{i}",
            title=t,
            summary=t,
            content=t,
            author="测试热榜",
            url="",
            likes=0,
            comments=0,
            shares=0,
            views=0,
            cover_img="",
            published_at="",
            tags=[],
            title_original=t,
            summary_original=t,
            content_original=t,
            raw={},
        )
        for i, t in enumerate(titles)
    ])
    assert _normalize("AI 大模型") == "ai大模型"
    return src


async def _async_value(v):
    return v


async def test_search_trending_no_match_returns_empty():
    """关键词未命中热榜时必须返回空，不能回落成全量热榜（假阳性）。"""
    src = _make_hotboard(["秋日穿搭灵感", "国庆出行高峰"])
    assert await src.search_trending("露营装备", limit=10) == []


async def test_search_trending_matches_ignoring_whitespace():
    """「AI 大模型」与榜单里的「AI大模型」应视为同一关键词。"""
    src = _make_hotboard(["AI大模型迎来新突破", "秋日穿搭灵感"])
    hits = await src.search_trending("AI 大模型", limit=10)
    assert len(hits) == 1
    assert hits[0].title == "AI大模型迎来新突破"


async def test_search_trending_empty_keyword_returns_hotlist():
    """关键词为空时保留原语义：返回全量热榜。"""
    src = _make_hotboard(["秋日穿搭灵感", "国庆出行高峰"])
    assert len(await src.search_trending("", limit=10)) == 2
