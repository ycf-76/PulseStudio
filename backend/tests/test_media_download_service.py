"""media_download_service 单元测试（mock DB 与 yt-dlp，无真实网络）。

测试: 作品视频下载服务
场景: 可下载校验 / Web 路径转换 / start_download 三态
预期: docs/FEATURE_yt-dlp媒体下载接入.md 前端接入章节
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import media_download_service as svc


def _record(**kw) -> SimpleNamespace:
    base = dict(id="rec1", user_id="u1", platform="bilibili",
                video_url="https://www.bilibili.com/video/BV1xx", local_video_path=None)
    base.update(kw)
    return SimpleNamespace(**base)


class _FakeDB:
    def __init__(self, record):
        self._record = record

    async def scalar(self, stmt):
        return self._record

    async def commit(self):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


def _patch_db(monkeypatch, record):
    monkeypatch.setattr(svc, "AsyncSessionLocal", lambda: _FakeDB(record))


# ===== check_downloadable =====

def test_check_downloadable_bilibili_ok():
    """测试: 白名单校验；场景: bilibili 有视频链接；预期: 可下载。"""
    assert svc.check_downloadable("bilibili", "https://www.bilibili.com/video/BV1xx") is None


def test_check_downloadable_no_video_url():
    """测试: 白名单校验；场景: 无视频链接；预期: 拒绝并提示。"""
    msg = svc.check_downloadable("bilibili", None)
    assert msg and "没有视频链接" in msg


def test_check_downloadable_xiaohongshu_rejected():
    """测试: 白名单校验；场景: 小红书；预期: 明确不支持提示。"""
    msg = svc.check_downloadable("xiaohongshu", "https://xhslink.com/abc")
    assert msg and "小红书" in msg


def test_check_downloadable_douyin_rejected():
    """测试: 白名单校验；场景: 抖音；预期: 明确不支持提示。"""
    msg = svc.check_downloadable("douyin", "https://www.douyin.com/video/1")
    assert msg and "抖音" in msg


# ===== to_web_url =====

def test_to_web_url_outputs_relative():
    """测试: Web 路径转换；场景: 文件在 outputs 目录内；预期: /outputs/ 前缀路径。"""
    p = svc.OUTPUTS_DIR / "media" / "bilibili" / "BV1xx.mp4"
    assert svc.to_web_url(str(p)) == "/outputs/media/bilibili/BV1xx.mp4"


def test_to_web_url_outside_outputs_none():
    """测试: Web 路径转换；场景: 文件在 outputs 外；预期: 返回 None（不暴露本地路径）。"""
    assert svc.to_web_url(str(Path("C:/Windows/notepad.exe"))) is None
    assert svc.to_web_url(None) is None


# ===== start_download =====

async def test_start_download_record_not_found(monkeypatch):
    """测试: 三态返回；场景: 记录不存在；预期: blocked。"""
    _patch_db(monkeypatch, None)
    result = await svc.start_download("missing", "u1")
    assert result["status"] == "blocked"


async def test_start_download_blocked_platform(monkeypatch):
    """测试: 三态返回；场景: 小红书记录；预期: blocked + 可读原因。"""
    _patch_db(monkeypatch, _record(platform="xiaohongshu", video_url="https://xhslink.com/a"))
    result = await svc.start_download("rec1", "u1")
    assert result["status"] == "blocked"
    assert "小红书" in result["message"]


async def test_start_download_already_ready(monkeypatch, tmp_path):
    """测试: 幂等；场景: local_video_path 文件已存在（outputs 内）；预期: 直接 ready + web url。"""
    fixture_dir = svc.OUTPUTS_DIR / "media" / "_test_fixtures"
    fixture_dir.mkdir(parents=True, exist_ok=True)
    video = fixture_dir / "pytest_BV1xx.mp4"
    video.write_bytes(b"x")
    try:
        _patch_db(monkeypatch, _record(local_video_path=str(video)))
        result = await svc.start_download("rec1", "u1")
        assert result["status"] == "ready"
        assert result["local_video_url"] == "/outputs/media/_test_fixtures/pytest_BV1xx.mp4"
    finally:
        video.unlink(missing_ok=True)


async def test_start_download_started(monkeypatch):
    """测试: 三态返回；场景: 校验通过；预期: started 且后台任务被调度（此处 stub）。"""
    _patch_db(monkeypatch, _record())

    async def fake_download_and_save(record_id, user_id):
        return None

    monkeypatch.setattr(svc, "_download_and_save", fake_download_and_save)
    result = await svc.start_download("rec1", "u1")
    assert result["status"] == "started"


async def test_download_and_save_writes_back(monkeypatch, tmp_path):
    """测试: 回写；场景: download_media 成功返回；预期: local_video_path 写入记录。"""
    video = tmp_path / "BV1xx.mp4"
    video.write_bytes(b"video")
    record = _record()
    _patch_db(monkeypatch, record)

    def fake_download_media(url, platform):
        return {"filepath": str(video), "title": "t", "duration": 1, "extractor": "BiliBili", "url": url, "platform": platform}

    import capabilities.scrapling.spiders.media_downloader as md
    monkeypatch.setattr(md, "download_media", fake_download_media)
    # _download_and_save 内部延迟导入 download_media，monkeypatch 模块属性即可生效

    await svc._download_and_save("rec1", "u1")
    assert record.local_video_path == str(video)
