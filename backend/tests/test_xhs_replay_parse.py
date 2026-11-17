"""小红书主页 / 详情页解析的离线重放测试。

覆盖问题：解析层（__INITIAL_STATE__ 提取、CSS 兜底选择器、from_card /
detail_page 两条产出路径）此前没有任何离线验证手段，每次改动只能拿真实
账号跑一次同步来试错——真跑一次暴露一层，改完再跑暴露下一层。

本测试用「真实 HTML → 真实 parse()」，不启动浏览器、不联网、不碰账号。
前半部分用合成页面验证重放harness本身；后半部分用录制的真实页面夹具，
夹具缺失时自动 skip 并给出补录指引。

文档依据：docs/FEATURE_平台账号管理与作品数据回收.md#7.2（BasePlatformSpider）
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from capabilities.scrapling.spiders import replay  # noqa: E402
from capabilities.scrapling.spiders.xhs_spider import XhsSpider  # noqa: E402


def _spider(budget: int = 10) -> XhsSpider:
    """user_id 留空 → 跳过所有 DB 读写，纯解析。"""
    spider = XhsSpider(user_id="", platform_uid="replay_uid")
    spider.detail_budget = budget
    return spider


def _profile_html(note_ids: list[str]) -> str:
    cards = [
        {
            "noteCard": {
                "id": nid,
                "type": "normal",
                "displayTitle": f"标题-{nid}",
                "cover": {"urlDefault": f"https://sns-img.xhscdn.com/{nid}.webp"},
                "interactInfo": {
                    "likedCount": "1.2w",
                    "collectedCount": "34",
                    "commentCount": "5",
                },
                "xsecToken": f"tok-{nid}",
            }
        }
        for nid in note_ids
    ]
    state = {"user": {"notes": [cards]}}
    links = "".join(f"<a href='/explore/{nid}'>卡片</a>" for nid in note_ids)
    return (
        "<html><head><title>小红书 - 你的生活指南</title></head><body>"
        f"<div class='user-notes'>{links}</div>"
        f"<script>window.__INITIAL_STATE__={json.dumps(state, ensure_ascii=False)}</script>"
        "</body></html>"
    )


def _detail_html(note_id: str, image_count: int = 9, video_url: str = "") -> str:
    note = {
        "id": note_id,
        "title": f"详情-{note_id}",
        "desc": "正文内容 #标签",
        "imageList": [
            {"urlDefault": f"https://sns-img.xhscdn.com/{note_id}_{i}.webp"}
            for i in range(image_count)
        ],
        "interactInfo": {
            "likedCount": "120",
            "collectedCount": "30",
            "commentCount": "7",
            "shareCount": "2",
        },
        "tagList": [{"name": "标签A"}, {"name": "标签B"}],
    }
    if video_url:
        note["video"] = {
            "media": {"stream": [{"format": "mp4", "height": 720, "masterUrl": video_url}]}
        }
    state = {"noteDetailMap": {note_id: {"note": note}}}
    return (
        "<html><head><title>详情</title></head><body>"
        f"<script>window.__INITIAL_STATE__={json.dumps(state, ensure_ascii=False)}</script>"
        "</body></html>"
    )


# ═══════════════════════════════════════════════════════════════════
# 1. 重放 harness 自检（不依赖任何录制夹具）
# ═══════════════════════════════════════════════════════════════════


def test_replay_response_wraps_real_selector():
    """测试: ReplayResponse
    场景: 用真实 HTML 构造离线 Response
    预期: css 选择器与 text 都走真实的 Scrapling Selector，不依赖网络
    """
    resp = replay.ReplayResponse("<html><body><p class='t'>你好</p></body></html>")
    assert resp.status == 200
    assert resp.css("p")[0].text == "你好"
    assert resp.meta == {}


@pytest.mark.asyncio
async def test_parse_yields_card_items_and_detail_requests():
    """测试: run_parse
    场景: 主页 3 条笔记，图集都还没落本地
    预期: 产出 3 条卡片 item（from_card=True）+ 3 个详情页 Request
    """
    spider = _spider()
    result = await replay.run_parse(spider, _profile_html(["n1", "n2", "n3"]))

    assert {item["note_id"] for item in result.items} == {"n1", "n2", "n3"}
    assert all(item["from_card"] is True for item in result.items)
    assert result.detail_note_ids == ["n1", "n2", "n3"]
    assert spider.login_failed is False


@pytest.mark.asyncio
async def test_parse_card_item_carries_only_cover_image():
    """测试: run_parse
    场景: 主页卡片只有封面一张图
    预期: image_urls 只有封面，且标记 from_card=True（入库时不得降级已有图集）
    """
    result = await replay.run_parse(_spider(), _profile_html(["n1"]))
    item = result.items[0]

    assert item["image_urls"] == ["https://sns-img.xhscdn.com/n1.webp"]
    assert item["cover_img_url"] == "https://sns-img.xhscdn.com/n1.webp"
    assert item["video_url"] == ""
    assert item["from_card"] is True


@pytest.mark.asyncio
async def test_parse_detail_returns_full_gallery():
    """测试: run_parse_note
    场景: 多图笔记详情页，imageList 9 张
    预期: image_urls 9 张（图集唯一来源），tags 解析出来
    """
    spider = _spider()
    result = await replay.run_parse_note(spider, _detail_html("n1", image_count=9), note_id="n1")
    item = result.items[0]

    assert len(item["image_urls"]) == 9
    assert item["image_urls"][0] == "https://sns-img.xhscdn.com/n1_0.webp"
    assert item["tags"] == ["标签A", "标签B"]
    assert not item.get("from_card"), "详情页 item 不能带 from_card（否则入库会被当低质量兜底）"


@pytest.mark.asyncio
async def test_parse_detail_extracts_video_url():
    """测试: run_parse_note
    场景: 视频笔记详情页，stream 里有 mp4 变体
    预期: video_url 取到直链，图集仍完整
    """
    spider = _spider()
    result = await replay.run_parse_note(
        spider,
        _detail_html("v1", image_count=2, video_url="https://cdn/v1.mp4"),
        note_id="v1",
    )
    item = result.items[0]

    assert item["video_url"] == "https://cdn/v1.mp4"
    assert len(item["image_urls"]) == 2


@pytest.mark.asyncio
async def test_parse_detail_single_image_note_keeps_one_image():
    """测试: run_parse_note
    场景: 单图笔记详情页
    预期: 只有 1 张图也算完整图集（不能因为"只有一张"被判为卡片兜底）
    """
    spider = _spider()
    result = await replay.run_parse_note(spider, _detail_html("s1", image_count=1), note_id="s1")
    assert len(result.items[0]["image_urls"]) == 1
    assert result.items[0]["from_card"] is False


@pytest.mark.asyncio
async def test_parse_detects_login_page_without_network():
    """测试: run_parse
    场景: cookies 过期被重定向到登录页
    预期: login_failed 置 True 且不产出任何 item（避免误报同步成功）
    """
    spider = _spider()
    html = (
        "<html><head><title>登录 - 小红书</title></head>"
        "<body><div class='login-container'>扫码登录</div></body></html>"
    )
    result = await replay.run_parse(spider, html)

    assert spider.login_failed is True
    assert result.items == []


# ═══════════════════════════════════════════════════════════════════
# 2. 真实录制页面重放（夹具缺失时 skip，不静默通过）
# ═══════════════════════════════════════════════════════════════════


def _require(name: str) -> None:
    if not replay.has_fixture(name):
        pytest.skip(
            f"缺少录制页面 {replay.fixture_path(name)} —— "
            "浏览器打开该页面后 F12 Console 执行 "
            "copy(document.documentElement.outerHTML) 保存即可（与爬虫无关，零风险）"
        )


@pytest.mark.asyncio
async def test_fixture_profile_page_yields_notes():
    """测试: run_parse（真实主页夹具）
    场景: 用户主页快照
    预期: 至少解析出 1 条笔记；不是登录页
    """
    _require(replay.PROFILE_PAGE)
    spider = _spider()
    result = await replay.run_parse(spider, replay.load_html(replay.PROFILE_PAGE))

    assert spider.login_failed is False, "主页被判定为登录页 → cookies 或夹具有问题"
    assert result.items, "主页解析不出任何笔记 → __INITIAL_STATE__ 结构可能已变"
    assert all(item["note_id"] for item in result.items)


@pytest.mark.asyncio
async def test_fixture_detail_image_note_yields_gallery():
    """测试: run_parse_note（真实多图笔记夹具）
    场景: 多图笔记详情页快照
    预期: image_urls 至少 2 张（图集是"过期外链能修回来"的前提）
    """
    _require(replay.DETAIL_IMAGE)
    spider = _spider()
    result = await replay.run_parse_note(
        spider, replay.load_html(replay.DETAIL_IMAGE), note_id="fixture"
    )

    assert result.items, "详情页解析不出 item → noteDetailMap 结构可能已变"
    images = result.items[0]["image_urls"]
    assert len(images) >= 2, f"多图笔记只解析到 {len(images)} 张图"


@pytest.mark.asyncio
async def test_fixture_detail_video_note_yields_video_url():
    """测试: run_parse_note（真实视频笔记夹具）
    场景: 视频笔记详情页快照
    预期: video_url 是 http(s) 直链
    """
    _require(replay.DETAIL_VIDEO)
    spider = _spider()
    result = await replay.run_parse_note(
        spider, replay.load_html(replay.DETAIL_VIDEO), note_id="fixture"
    )

    assert result.items, "视频笔记详情页解析不出 item"
    assert result.items[0]["video_url"].startswith("http"), "视频直链没解析出来"


@pytest.mark.asyncio
async def test_fixture_detail_single_image_note_yields_one_image():
    """测试: run_parse_note（真实单图笔记夹具）
    场景: 单图笔记详情页快照
    预期: 图集恰好 1 张，且 from_card=False（详情页单图不算降级）
    """
    _require(replay.DETAIL_SINGLE)
    spider = _spider()
    result = await replay.run_parse_note(
        spider, replay.load_html(replay.DETAIL_SINGLE), note_id="fixture"
    )

    assert result.items, "单图笔记详情页解析不出 item"
    assert len(result.items[0]["image_urls"]) == 1
    assert result.items[0]["from_card"] is False
