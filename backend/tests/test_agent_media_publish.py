"""工作区媒体产物自动发布单测。

场景：口播成片由 bash/ffmpeg 直接写在工作区根目录，outputs/ 才是「我的空间」
扫描根 → 成片生成了但前端无任何可渲染地址（2026-11-08 19:03 实证）。
预期：回合结束时收割本轮新增媒体，复制进 outputs/<user>/agent_media/，
      返回 /outputs/... 可直连地址；坏文件（<1KB）与旧文件不发布；重复发布幂等。
"""
from __future__ import annotations

import asyncio
import os
import time

import pytest

from app.services import agent_media_publish


@pytest.fixture
def roots(tmp_path, monkeypatch):
    ws = tmp_path / "workspace"
    out = tmp_path / "outputs"
    ws.mkdir()
    out.mkdir()
    monkeypatch.setenv("WORKSPACE_ROOT", str(ws))
    monkeypatch.setenv("MY_WORKS_OUTPUT_ROOT", str(out))
    return ws, out


def _publish(since: float, user_id: str = "u1"):
    return asyncio.run(agent_media_publish.publish_workspace_media(since, user_id))


def test_publishes_new_video_into_outputs(roots):
    """本轮新增的成片被复制进 outputs 并返回可直连 url。"""
    ws, out = roots
    started = time.time()
    video = ws / "output_ai_koubo_final.mp4"
    video.write_bytes(b"x" * 4096)
    os.utime(video, (started + 1, started + 1))

    items = _publish(started)

    assert len(items) == 1
    item = items[0]
    assert item["kind"] == "video"
    assert item["url"] == "/outputs/u1/agent_media/output_ai_koubo_final/output_ai_koubo_final.mp4"
    assert item["size"] == 4096
    assert (out / "u1" / "agent_media" / "output_ai_koubo_final" / video.name).is_file()


def test_skips_broken_and_stale_files(roots):
    """0/1 字节坏文件与上一轮的旧文件都不发布。"""
    ws, out = roots
    started = time.time()
    bad = ws / "cut_ghost.mp4"
    bad.write_bytes(b"x")
    os.utime(bad, (started + 1, started + 1))
    stale = ws / "old_take.mp4"
    stale.write_bytes(b"x" * 4096)
    os.utime(stale, (started - 3600, started - 3600))

    assert _publish(started) == []
    assert not (out / "u1").exists()


def test_repeat_publish_is_idempotent(roots):
    """同名同大小目标已存在则复用，不重复复制、不产生重复条目。"""
    ws, out = roots
    started = time.time()
    video = ws / "clip.mp4"
    video.write_bytes(b"x" * 2048)
    os.utime(video, (started + 1, started + 1))

    first = _publish(started)
    second = _publish(started)

    assert len(first) == 1 and len(second) == 1
    assert first[0]["url"] == second[0]["url"]
    files = list((out / "u1" / "agent_media" / "clip").iterdir())
    assert len(files) == 1


def test_limit_caps_published_items(roots):
    """多文件时按新→旧取前 limit 个（默认 3）。"""
    ws, _out = roots
    started = time.time()
    for idx in range(5):
        f = ws / f"take_{idx}.mp4"
        f.write_bytes(b"x" * 4096)
        os.utime(f, (started + idx + 1, started + idx + 1))

    items = _publish(started)

    assert len(items) == 3
    assert items[0]["file_name"] == "take_4.mp4"
    assert items[-1]["file_name"] == "take_2.mp4"


def test_skips_outputs_dir_inside_workspace(roots):
    """产物目录若嵌在工作区内，不重复收割自己发布的副本。"""
    ws, out = roots
    started = time.time()
    nested_out = ws / "outputs"
    nested_out.mkdir()
    video = ws / "clip.mp4"
    video.write_bytes(b"x" * 4096)
    os.utime(video, (started + 1, started + 1))
    copy = nested_out / "clip.mp4"
    copy.write_bytes(b"x" * 4096)
    os.utime(copy, (started + 1, started + 1))

    items = _publish(started)

    assert [item["file_name"] for item in items] == ["clip.mp4"]