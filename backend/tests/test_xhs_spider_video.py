"""小红书 Spider 视频地址提取回归测试。

覆盖问题：视频笔记采集回来只有封面，video_url 为空。

实测依据（2026-11 用登录态 Playwright 抓取真实页面）：
  - 主页 noteCard 只有 cover / type / xsecToken，没有任何视频流字段
  - 详情页 note.video.media.stream 是 {画质档位: [变体...]} 字典，不是列表
  - 详情页不带 xsec_token 时 noteDetailMap 为空
  - format="mp4" 的变体浏览器可播；format="fmp4"（.m4s）报 MEDIA_ERR_SRC_NOT_SUPPORTED
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from capabilities.scrapling.spiders.xhs_spider import XhsSpider  # noqa: E402


class _FakeElement:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeLink:
    """主页笔记卡片的 <a> 替身，带 href 属性。"""

    def __init__(self, href: str) -> None:
        self.attrib = {"href": href}
        self.parent = self
        self.text = ""

    def css(self, *_: Any, **__: Any) -> list:
        return []


class _FakeResponse:
    """极简 Response 替身：只实现 spider 实际用到的 css() 与 url/meta。"""

    def __init__(
        self,
        html: str,
        url: str = "https://www.xiaohongshu.com/user/profile/x",
        meta: dict | None = None,
    ) -> None:
        self.url = url
        self.meta = meta or {}
        self._html = html
        self._links: list[_FakeLink] = []

    def with_links(self, hrefs: list[str]) -> "_FakeResponse":
        self._links = [_FakeLink(h) for h in hrefs]
        return self

    def css(self, selector: str, **_: Any) -> list:
        if selector == "script":
            return [_FakeElement(f"window.__INITIAL_STATE__={self._html}")]
        if selector == "title":
            return [_FakeElement("小红书")]
        if selector == "body":
            return [_FakeElement("")]
        if "a[href" in selector:
            return list(self._links)
        return []


def _spider() -> XhsSpider:
    return XhsSpider(user_id="u1", platform_uid="5b61cb9ee8ac2b6454ba7b71")


# ═══════════════════════════════════════════════════════════════════
# 1. _extract_video_url：新版字典结构 + mp4 优先
# ═══════════════════════════════════════════════════════════════════


def test_extract_video_url_prefers_mp4_over_higher_res_fmp4():
    """测试: _extract_video_url
    场景: 新版 stream 为画质档位字典，1080p 是 fmp4，720p 是 mp4
    预期: 选 720p 的 mp4（fmp4 浏览器不可播）
    """
    video = {
        "media": {
            "stream": {
                "EF4": [
                    {
                        "height": 720,
                        "size": 3948062,
                        "format": "mp4",
                        "masterUrl": "https://cdn/720p.mp4",
                    }
                ],
                "EF5": [
                    {
                        "height": 720,
                        "size": 2763608,
                        "format": "fmp4",
                        "masterUrl": "https://cdn/720p.m4s",
                    },
                    {
                        "height": 1080,
                        "size": 2721419,
                        "format": "fmp4",
                        "masterUrl": "https://cdn/1080p.m4s",
                    },
                ],
                "EF6": [],
                "EF7": [],
            }
        }
    }
    assert XhsSpider._extract_video_url(video) == "https://cdn/720p.mp4"


def test_extract_video_url_picks_highest_mp4_when_multiple():
    """测试: _extract_video_url
    场景: 多个 mp4 变体，分辨率不同
    预期: 取分辨率最高的那个
    """
    video = {
        "media": {
            "stream": [
                {"height": 540, "size": 100, "format": "mp4", "masterUrl": "https://cdn/540p.mp4"},
                {
                    "height": 1080,
                    "size": 100,
                    "format": "mp4",
                    "masterUrl": "https://cdn/1080p.mp4",
                },
            ]
        }
    }
    assert XhsSpider._extract_video_url(video) == "https://cdn/1080p.mp4"


# ═══════════════════════════════════════════════════════════════════
# 2. _extract_video_url：旧版列表结构
# ═══════════════════════════════════════════════════════════════════


def test_extract_video_url_supports_legacy_list_stream():
    """测试: _extract_video_url
    场景: 旧版 media.stream 直接是列表
    预期: 正常取到 masterUrl
    """
    video = {
        "media": {
            "stream": [{"height": 720, "format": "mp4", "masterUrl": "https://cdn/legacy.mp4"}]
        }
    }
    assert XhsSpider._extract_video_url(video) == "https://cdn/legacy.mp4"


def test_extract_video_url_uses_backup_urls_when_master_missing():
    """测试: _extract_video_url
    场景: 变体没有 masterUrl，只有 backupUrls
    预期: 退到 backupUrls[0]
    """
    video = {
        "media": {
            "stream": {
                "EF4": [{"height": 720, "format": "mp4", "backupUrls": ["https://cdn/backup.mp4"]}]
            }
        }
    }
    assert XhsSpider._extract_video_url(video) == "https://cdn/backup.mp4"


# ═══════════════════════════════════════════════════════════════════
# 3. _extract_video_url：mediaV2 兜底与空值
# ═══════════════════════════════════════════════════════════════════


def test_extract_video_url_falls_back_to_media_v2_json():
    """测试: _extract_video_url
    场景: media.stream 为空，mediaV2 是 JSON 字符串且其 stream 带地址
    预期: 解析 mediaV2 后取到地址
    """
    media_v2 = json.dumps(
        {
            "video": {"width": "1842", "height": "1080"},
            "stream": {
                "EF4": [{"height": 720, "format": "mp4", "masterUrl": "https://cdn/v2.mp4"}]
            },
        }
    )
    video = {"media": {"stream": {}}, "mediaV2": media_v2}
    assert XhsSpider._extract_video_url(video) == "https://cdn/v2.mp4"


def test_extract_video_url_returns_empty_for_text_note():
    """测试: _extract_video_url
    场景: 图文笔记，没有 video 字段
    预期: 返回空串，不影响图文采集
    """
    assert XhsSpider._extract_video_url({}) == ""
    assert XhsSpider._extract_video_url(None) == ""


def test_extract_video_url_ignores_broken_media_v2():
    """测试: _extract_video_url
    场景: mediaV2 不是合法 JSON
    预期: 不抛异常，返回空串
    """
    assert XhsSpider._extract_video_url({"mediaV2": "{not json"}) == ""


# ═══════════════════════════════════════════════════════════════════
# 4. _build_note_detail_url：必须带 xsec_token
# ═══════════════════════════════════════════════════════════════════


def test_build_note_detail_url_includes_xsec_token():
    """测试: _build_note_detail_url
    场景: 主页卡片带 xsecToken
    预期: 详情页 URL 带 xsec_token，否则 noteDetailMap 为空拿不到视频
    """
    item = {"note_id": "6ae6e0f1000000001802936e", "xsec_token": "AB5vm0TTj38o"}
    url = XhsSpider._build_note_detail_url(item)
    assert url.startswith("https://www.xiaohongshu.com/explore/6ae6e0f1000000001802936e?")
    assert "xsec_token=AB5vm0TTj38o" in url


def test_build_note_detail_url_without_token():
    """测试: _build_note_detail_url
    场景: 卡片没有 xsecToken
    预期: 仍返回可用的基础详情页 URL，不拼出空参数
    """
    url = XhsSpider._build_note_detail_url({"note_id": "abc123"})
    assert url == "https://www.xiaohongshu.com/explore/abc123"


# ═══════════════════════════════════════════════════════════════════
# 5. 主页卡片解析保留 type 与 xsecToken
# ═══════════════════════════════════════════════════════════════════


def test_extract_notes_from_initial_state_keeps_type_and_token():
    """测试: _extract_notes_from_initial_state
    场景: 主页 __INITIAL_STATE__ 含一条视频卡片
    预期: 带出 note_type="video" 与 xsec_token，供 parse 决定是否请求详情页
    """
    state = {
        "user": {
            "notes": [
                [
                    {
                        "noteCard": {
                            "noteId": "6ae6e0f1000000001802936e",
                            "type": "video",
                            "xsecToken": "AB5vm0TTj38oDkrQCCrbswFaRFu1ikOfeXb26sHCUvK8M=",
                            "displayTitle": "人生终究是一个人走完的",
                            "cover": {"url": "https://cdn/cover.jpg"},
                            "interactInfo": {
                                "likedCount": "1200",
                                "collectedCount": "300",
                                "commentCount": "45",
                            },
                        }
                    }
                ]
            ]
        }
    }
    notes = _spider()._extract_notes_from_initial_state(_FakeResponse(json.dumps(state)))
    assert len(notes) == 1
    note = notes[0]
    assert note["note_id"] == "6ae6e0f1000000001802936e"
    assert note["note_type"] == "video"
    assert note["xsec_token"].startswith("AB5vm0TTj38o")
    assert note["cover_img_url"] == "https://cdn/cover.jpg"
    assert note["likes"] == 1200


@pytest.mark.asyncio
async def test_parse_yields_detail_request_for_every_note():
    """测试: parse
    场景: 主页同时含视频与图文笔记
    预期: 两条笔记都额外产出带 xsec_token 的详情页 Request

    依据: 主页卡片只有封面一张，图集与视频直链都只在详情页 noteDetailMap 里。
    只给视频笔记拉详情，图文笔记的 imageList 永远只剩封面，且小红书 CDN 签名
    约 2 天过期，导致"图文过期后同步也不更新"。
    """
    state = {
        "user": {
            "notes": [
                [
                    {
                        "noteCard": {
                            "noteId": "vid1",
                            "type": "video",
                            "xsecToken": "tok1",
                            "displayTitle": "视频笔记",
                            "cover": {"url": "https://cdn/v.jpg"},
                        }
                    },
                    {
                        "noteCard": {
                            "noteId": "img1",
                            "type": "normal",
                            "xsecToken": "tok2",
                            "displayTitle": "图文笔记",
                            "cover": {"url": "https://cdn/i.jpg"},
                        }
                    },
                ]
            ]
        }
    }
    spider = _spider()
    results = []
    async for out in spider.parse(_FakeResponse(json.dumps(state))):
        results.append(out)

    items = [r for r in results if isinstance(r, dict)]
    requests = [r for r in results if not isinstance(r, dict)]

    assert {i["note_id"] for i in items} == {"vid1", "img1"}
    assert all(i["video_url"] == "" for i in items)
    assert len(requests) == 2
    urls = sorted(r.url for r in requests)
    assert urls[0].startswith("https://www.xiaohongshu.com/explore/img1?xsec_token=tok2")
    assert urls[1].startswith("https://www.xiaohongshu.com/explore/vid1?xsec_token=tok1")
    assert all(r.meta["from_card"] is True for r in requests)


@pytest.mark.asyncio
async def test_parse_marks_login_failed_when_redirected_to_login_page():
    """测试: parse
    场景: cookies 过期，主页被重定向到登录页
    预期: login_failed 置 True 且不产出任何 item

    依据: 旧实现只打日志就返回，子进程仍上报 ok=True + 空列表，
    前端因此误报"同步完成"，用户以为同步过了其实一条都没写。
    """
    spider = _spider()
    login_response = _FakeResponse(
        "<html><body>请登录</body></html>", url="https://www.xiaohongshu.com/login"
    )
    results = [out async for out in spider.parse(login_response)]
    assert results == []
    assert spider.login_failed is True


@pytest.mark.asyncio
async def test_parse_note_keeps_card_data_when_detail_has_no_state():
    """测试: parse_note
    场景: 详情页请求失败（无 __INITIAL_STATE__），且该请求来自主页卡片
    预期: 不产出低质量 item，保留已入库的卡片数据
    """
    spider = _spider()
    response = _FakeResponse("undefined", meta={"note_id": "vid1", "from_card": True})
    results = [out async for out in spider.parse_note(response)]
    assert results == []


# ═══════════════════════════════════════════════════════════════════
# 6. 主页滚动翻页：首屏之外的笔记也要重新同步
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_parse_merges_dom_notes_outside_first_screen():
    """测试: parse
    场景: __INITIAL_STATE__ 只有首屏 1 条，滚动后 DOM 里多出 1 条更早的笔记
    预期: 两条都发详情页请求，且首屏那条不重复

    依据: 不滚动时，库里更早的笔记永远拿不到新的 xsec_token，
    已过期的图文图集也就永远刷不回来。
    """
    state = {
        "user": {
            "notes": [
                [
                    {
                        "noteCard": {
                            "noteId": "6ae6e0f1000000001802936e",
                            "type": "normal",
                            "xsecToken": "tokA",
                            "displayTitle": "首屏笔记",
                            "cover": {"url": "https://cdn/a.jpg"},
                        }
                    },
                ]
            ]
        }
    }
    response = _FakeResponse(json.dumps(state)).with_links(
        [
            "/explore/6ae6e0f1000000001802936e?xsec_token=tokA&xsec_source=pc_user",
            "/explore/5f1a2b3c0000000001000001?xsec_token=tokB&xsec_source=pc_user",
        ]
    )

    results = [out async for out in _spider().parse(response)]
    requests = [r for r in results if not isinstance(r, dict)]
    urls = [r.url for r in requests]

    assert sum("6ae6e0f1000000001802936e" in u for u in urls) == 1
    assert any("5f1a2b3c0000000001000001" in u and "tokB" in u for u in urls)


@pytest.mark.asyncio
async def test_start_requests_attaches_home_scroll_action(monkeypatch):
    """测试: start_requests
    场景: 正常启动爬虫
    预期: 主页 Request 带 page_action，否则瀑布流不会加载首屏之外的笔记
    """
    spider = _spider()
    monkeypatch.setattr(type(spider), "_try_update_uid_in_db", lambda self: None)

    requests = [r async for r in spider.start_requests()]
    assert len(requests) == 1
    assert requests[0]._session_kwargs.get("page_action") == spider._scroll_home


@pytest.mark.asyncio
async def test_scroll_home_scrolls_configured_times():
    """测试: _scroll_home
    场景: 传入 playwright page
    预期: 按 scroll_times 滚动并等待
    """

    class _FakeMouse:
        def __init__(self) -> None:
            self.calls = 0

        async def wheel(self, dx: int, dy: int) -> None:
            self.calls += 1

    class _FakePage:
        def __init__(self) -> None:
            self.mouse = _FakeMouse()
            self.waits = 0

        async def wait_for_timeout(self, ms: int) -> None:
            self.waits += 1

    spider = _spider()
    spider.scroll_times = 3
    page = _FakePage()
    await spider._scroll_home(page)
    assert page.mouse.calls == 3
    assert page.waits == 3


# ═══════════════════════════════════════════════════════════════════
# 7. 详情页按需拉取：已落本地图集的笔记不再重复拉
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    ("cached", "expected"),
    [
        (None, True),
        ([], True),
        (["https://sns-webpic-qc.xhscdn.com/20260901/abc.jpg"], True),
        (["/uploads/topic_covers/6ae6e0f1.jpg"], False),
        (["/uploads/detail/6ae6e0f1_1.jpg", "/uploads/detail/6ae6e0f1_2.jpg"], False),
        (["/uploads/detail/6ae6e0f1_1.jpg", "https://sns-webpic-qc.xhscdn.com/abc.jpg"], True),
    ],
)
def test_needs_detail_page_only_when_images_not_local(cached, expected):
    """测试: _needs_detail_page
    场景: 库里已有该笔记的图集，可能是本地文件或过期外链
    预期: 只有全部落本地时才跳过详情页；含外链（会过期）或没有图集时都要拉
    """
    assert XhsSpider._needs_detail_page(cached) is expected


@pytest.mark.asyncio
async def test_parse_skips_detail_request_for_cached_notes(monkeypatch):
    """测试: parse
    场景: 库里该笔记图集已全部落本地（同步过的旧笔记）
    预期: 仍然产出卡片 item 更新点赞数，但不再发详情页请求

    依据: 同步时每条详情页都要开一次浏览器页面，笔记多时会直接撞超时；
    本地文件不会过期，重复拉详情页纯属浪费。
    """
    state = {
        "user": {
            "notes": [
                [
                    {
                        "noteCard": {
                            "noteId": "6ae6e0f1000000001802936e",
                            "type": "normal",
                            "xsecToken": "tokA",
                            "displayTitle": "已同步笔记",
                            "cover": {"url": "https://cdn/a.jpg"},
                        }
                    },
                ]
            ]
        }
    }
    response = _FakeResponse(json.dumps(state)).with_links(
        [
            "/explore/6ae6e0f1000000001802936e?xsec_token=tokA&xsec_source=pc_user",
        ]
    )

    spider = _spider()

    async def _fake_cached() -> dict[str, list[str]]:
        return {"6ae6e0f1000000001802936e": ["/uploads/detail/6ae6e0f1_1.jpg"]}

    monkeypatch.setattr(type(spider), "_load_cached_images", lambda self: _fake_cached())

    results = [out async for out in spider.parse(response)]
    requests = [r for r in results if not isinstance(r, dict)]
    items = [r for r in results if isinstance(r, dict)]

    assert requests == []
    assert [i["note_id"] for i in items] == ["6ae6e0f1000000001802936e"]
