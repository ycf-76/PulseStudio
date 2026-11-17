"""media_downloader 单元测试（mock yt-dlp，无真实网络）。

测试: yt-dlp 媒体下载能力
场景: 白名单路由 / 平台拒绝 / cookie 转换 / 错误路径
预期: 见 docs/FEATURE_yt-dlp媒体下载接入.md 验收标准
文档依据: docs/FEATURE_yt-dlp媒体下载接入.md
"""

from __future__ import annotations

from pathlib import Path

import pytest

from capabilities.scrapling.spiders.media_downloader import (
    MediaDownloadError,
    UnsupportedPlatformError,
    download_media,
    write_netscape_cookiefile,
)


class FakeYoutubeDL:
    """最小 YoutubeDL 替身：记录 opts，extract_info 时落盘一个假文件。"""

    last_opts: dict | None = None
    fail: bool = False

    def __init__(self, opts: dict):
        FakeYoutubeDL.last_opts = opts
        self._info: dict | None = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def extract_info(self, url: str, download: bool = True):
        if FakeYoutubeDL.fail:
            raise RuntimeError("mocked download failure")
        outtmpl = self.last_opts["outtmpl"]
        filepath = str(Path(outtmpl.replace("%(id)s", "BV1xx").replace("%(ext)s", "mp4")))
        Path(filepath).write_bytes(b"fake-video")
        self._info = {
            "id": "BV1xx",
            "title": "测试视频",
            "duration": 60,
            "extractor_key": "BiliBili",
            "requested_downloads": [{"filepath": filepath}],
        }
        return self._info

    def prepare_filename(self, info: dict) -> str:
        return info["requested_downloads"][0]["filepath"]


@pytest.fixture
def fake_ydl(monkeypatch):
    FakeYoutubeDL.last_opts = None
    FakeYoutubeDL.fail = False
    import yt_dlp

    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYoutubeDL)
    return FakeYoutubeDL


def test_download_media_bilibili_success_returns_filepath(tmp_path, fake_ydl):
    """测试: bilibili 白名单下载；场景: mock yt-dlp 正常返回；预期: 结果结构完整、文件存在。"""
    result = download_media(
        "https://www.bilibili.com/video/BV1xx", platform="bilibili", out_dir=tmp_path
    )
    assert result["platform"] == "bilibili"
    assert result["url"] == "https://www.bilibili.com/video/BV1xx"
    assert result["title"] == "测试视频"
    assert result["extractor"] == "BiliBili"
    assert Path(result["filepath"]).exists()
    assert Path(result["filepath"]).parent == tmp_path


def test_download_media_format_has_height_cap(tmp_path, fake_ydl):
    """测试: 画质上限参数；场景: max_height=720；预期: format 含高度限制。"""
    download_media(
        "https://www.bilibili.com/video/BV1xx", "bilibili", out_dir=tmp_path, max_height=720
    )
    assert "height<=720" in fake_ydl.last_opts["format"]


def test_download_media_no_height_cap(tmp_path, fake_ydl):
    """测试: 画质不限制；场景: max_height=None；预期: format 无高度过滤。"""
    download_media(
        "https://www.bilibili.com/video/BV1xx", "bilibili", out_dir=tmp_path, max_height=None
    )
    assert "height" not in fake_ydl.last_opts["format"]


def test_download_media_xiaohongshu_rejected(tmp_path):
    """测试: 平台白名单；场景: 小红书无 yt-dlp extractor；预期: 明确拒绝。"""
    with pytest.raises(UnsupportedPlatformError, match="xiaohongshu"):
        download_media("https://www.xiaohongshu.com/explore/abc", "xiaohongshu", out_dir=tmp_path)


def test_download_media_douyin_rejected(tmp_path):
    """测试: 平台白名单；场景: 抖音风控不稳不走 yt-dlp；预期: 明确拒绝。"""
    with pytest.raises(UnsupportedPlatformError, match="douyin"):
        download_media("https://www.douyin.com/video/123", "douyin", out_dir=tmp_path)


def test_download_media_empty_url_rejected(tmp_path):
    """测试: 参数校验；场景: 空 url；预期: 抛 MediaDownloadError。"""
    with pytest.raises(MediaDownloadError, match="url 不能为空"):
        download_media("", "bilibili", out_dir=tmp_path)


def test_download_media_ytdlp_failure_wrapped(tmp_path, fake_ydl):
    """测试: 错误路径；场景: yt-dlp 内部异常；预期: 包装为 MediaDownloadError 且带原因。"""
    fake_ydl.fail = True
    with pytest.raises(MediaDownloadError, match="下载失败"):
        download_media("https://www.bilibili.com/video/BV1xx", "bilibili", out_dir=tmp_path)


def test_download_media_missing_product_raises(tmp_path, fake_ydl, monkeypatch):
    """测试: 错误路径；场景: extract_info 成功但未产出文件；预期: 抛 MediaDownloadError。"""

    def fake_extract(self, url, download=True):
        return {
            "id": "BV1xx",
            "title": "t",
            "requested_downloads": [{"filepath": str(tmp_path / "ghost.mp4")}],
        }

    monkeypatch.setattr(FakeYoutubeDL, "extract_info", fake_extract)
    with pytest.raises(MediaDownloadError, match="未找到产物文件"):
        download_media("https://www.bilibili.com/video/BV1xx", "bilibili", out_dir=tmp_path)


def test_download_media_cookies_passed_as_netscape_file(tmp_path, fake_ydl):
    """测试: 登录态透传；场景: 传入 dict cookies；预期: 生成临时 Netscape cookiefile 且用后清理。"""
    cookies = {"SESSDATA": "abc123", "buvid3": {"value": "xyz", "domain": ".bilibili.com"}}
    download_media(
        "https://www.bilibili.com/video/BV1xx", "bilibili", out_dir=tmp_path, cookies=cookies
    )

    cookiefile = fake_ydl.last_opts.get("cookiefile")
    assert cookiefile, "cookies 传入时 opts 应包含 cookiefile"
    assert not Path(cookiefile).exists(), "临时 cookiefile 用后应删除"


def test_write_netscape_cookiefile_both_value_forms(tmp_path):
    """测试: cookie 转换；场景: {name: value} 与 {name: {value, domain}} 两种形态；预期: 行格式正确。"""
    cookies = {"SESSDATA": "abc", "buvid3": {"value": "xyz", "domain": ".custom.com"}}
    out = tmp_path / "cookies.txt"
    write_netscape_cookiefile(cookies, ".bilibili.com", out)

    rows = [
        line.split("\t")
        for line in out.read_text(encoding="utf-8").splitlines()
        if not line.startswith("#")
    ]
    by_name = {r[5]: r for r in rows}
    assert by_name["SESSDATA"][0] == ".bilibili.com"
    assert by_name["SESSDATA"][6] == "abc"
    assert by_name["buvid3"][0] == ".custom.com"
    assert by_name["buvid3"][6] == "xyz"
    assert all(len(r) == 7 for r in rows)


def test_download_media_ytdlp_not_installed(tmp_path, monkeypatch):
    """测试: 依赖缺失；场景: yt_dlp 导入失败；预期: 抛可读 MediaDownloadError（含安装提示）。"""
    import sys

    monkeypatch.setitem(sys.modules, "yt_dlp", None)
    with pytest.raises(MediaDownloadError, match="pip install yt-dlp"):
        download_media("https://www.bilibili.com/video/BV1xx", "bilibili", out_dir=tmp_path)


def test_download_media_ffmpeg_location_resolved(tmp_path, fake_ydl, monkeypatch):
    """测试: ffmpeg 探测；场景: PATH 无 ffmpeg 但 imageio_ffmpeg 可用；预期: opts 指向自带二进制。"""
    import shutil as _shutil

    import imageio_ffmpeg

    monkeypatch.setattr(_shutil, "which", lambda _: None)
    download_media("https://www.bilibili.com/video/BV1xx", "bilibili", out_dir=tmp_path)
    assert fake_ydl.last_opts.get("ffmpeg_location") == imageio_ffmpeg.get_ffmpeg_exe()


def test_base_spider_download_media_delegates(tmp_path, fake_ydl):
    """测试: 基类薄封装；场景: DummySpider 平台/cookies 自动透传；预期: download_media 收到正确参数。"""
    from capabilities.scrapling.spiders.base import BasePlatformSpider

    class DummySpider(BasePlatformSpider):
        platform = "bilibili"
        name = "dummy"

        def parse(self):
            return []

    # 只验证参数透传，绕开 Scrapling Spider ABC 的 session 初始化
    spider = DummySpider.__new__(DummySpider)
    spider.user_id = "u1"
    spider.platform_uid = "2"
    spider.cookies = {"SESSDATA": "abc"}
    result = spider.download_media("https://www.bilibili.com/video/BV1xx", out_dir=tmp_path)

    assert result["platform"] == "bilibili"
    assert "cookiefile" in fake_ydl.last_opts
