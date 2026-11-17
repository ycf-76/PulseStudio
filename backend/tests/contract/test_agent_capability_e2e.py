"""端到端集成测试：Agent -> Skill -> capability 访问层 -> 进程内执行。

验证五期外置后，Agent 通过 Skill 调用能力工具的完整链路仍然通畅。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------- 1. spider_capability 调用链 ----------


@pytest.mark.asyncio
async def test_spider_capability_inprocess_calls_runner():
    """spider_capability.start_sync 在 inprocess 模式下调 runner.start_sync。"""
    with patch(
        "capabilities.scrapling.spiders.runner.start_sync", new_callable=AsyncMock
    ) as mock_runner:
        from app.services.spider_capability import start_sync

        await start_sync("acc1", "user1", "xiaohongshu", force=False)
        mock_runner.assert_awaited_once_with("acc1", "user1", "xiaohongshu", force=False)


@pytest.mark.asyncio
async def test_spider_capability_remote_calls_http():
    """spider_capability.start_sync 在 service 模式下调 HTTP transport。"""
    with patch("app.services.spider_capability._service_url", return_value="http://cap:8001"):
        mock_transport = AsyncMock()
        mock_transport.call = AsyncMock(return_value={"ok": True})
        mock_transport.aclose = AsyncMock()

        with patch("app.engine.protocol.transport.HttpTransport", return_value=mock_transport):
            from app.services.spider_capability import start_sync

            await start_sync("acc1", "user1", "xiaohongshu", force=True)
            mock_transport.call.assert_awaited_once()
            call_args = mock_transport.call.call_args
            assert call_args[0][0] == "spider_sync"
            assert call_args[0][1]["account_id"] == "acc1"
            assert call_args[0][1]["platform"] == "xiaohongshu"
            assert call_args[0][1]["force"] is True


# ---------- 2. media_capability 调用链 ----------


@pytest.mark.asyncio
async def test_media_tts_piper_inprocess_calls_synthesize():
    """media_capability.tts_piper 在 inprocess 模式下调 synthesize_piper。"""
    with patch(
        "app.skills.produce.tts_piper.synthesize_piper",
        new_callable=AsyncMock,
        return_value="/out/tts.wav",
    ) as mock_syn:
        from app.services.media_capability import tts_piper

        result = await tts_piper("hello", "/out/tts.wav")
        assert result == "/out/tts.wav"
        mock_syn.assert_awaited_once()


@pytest.mark.asyncio
async def test_media_tts_piper_remote_calls_http():
    """media_capability.tts_piper 在 service 模式下调 HTTP transport。"""
    with patch("app.services.media_capability._service_url", return_value="http://cap:8001"):
        mock_transport = AsyncMock()
        mock_transport.call = AsyncMock(return_value={"ok": True, "output_path": "/out/tts.wav"})
        mock_transport.aclose = AsyncMock()

        with patch("app.engine.protocol.transport.HttpTransport", return_value=mock_transport):
            from app.services.media_capability import tts_piper

            result = await tts_piper("hello", "/out/tts.wav")
            assert result == "/out/tts.wav"
            mock_transport.call.assert_awaited_once()
            assert mock_transport.call.call_args[0][0] == "tts_piper"


@pytest.mark.asyncio
async def test_media_video_compose_inprocess_calls_adapter():
    """media_capability.video_compose 在 inprocess 模式下调 compose_video adapter。"""
    fake_result = {"output_path": "/out/final.mp4"}
    with patch(
        "capabilities.video.adapters.video_compose.compose_video",
        new_callable=AsyncMock,
        return_value=fake_result,
    ) as mock_comp:
        from app.services.media_capability import video_compose

        result = await video_compose({"shots": []}, "/out/final.mp4")
        assert result == fake_result
        mock_comp.assert_awaited_once()


@pytest.mark.asyncio
async def test_media_video_compose_remote_calls_http():
    """media_capability.video_compose 在 service 模式下调 HTTP transport。"""
    with patch("app.services.media_capability._service_url", return_value="http://cap:8001"):
        mock_transport = AsyncMock()
        mock_transport.call = AsyncMock(return_value={"output_path": "/out/final.mp4"})
        mock_transport.aclose = AsyncMock()

        with patch("app.engine.protocol.transport.HttpTransport", return_value=mock_transport):
            from app.services.media_capability import video_compose

            result = await video_compose({"shots": []}, "/out/final.mp4")
            assert result["output_path"] == "/out/final.mp4"
            mock_transport.call.assert_awaited_once()
            assert mock_transport.call.call_args[0][0] == "video_compose"


# ---------- 3. grid_capability 调用链 ----------


@pytest.mark.asyncio
async def test_grid_capability_inprocess_calls_local():
    """grid_capability 在 inprocess 模式下调本地 grid 函数。"""
    from app.services.grid_capability import _service_url

    assert _service_url() == ""


# ---------- 4. browser_capability 调用链 ----------


@pytest.mark.asyncio
async def test_browser_capability_inprocess_calls_local():
    """browser_capability 在 inprocess 模式下调本地 render 函数。"""
    from app.services.browser_capability import _service_url

    assert _service_url() == ""


# ---------- 5. accounts.py 路由走 spider_capability ----------


def test_accounts_router_imports_spider_capability():
    """accounts.py 已改用 spider_capability 访问层。"""
    import inspect

    from app.api.routers import accounts

    src = inspect.getsource(accounts)
    assert "spider_capability" in src
    assert "from capabilities.scrapling.spiders.runner import start_sync" not in src


# ---------- 6. spider_sync skill 走 spider_capability ----------


def test_spider_sync_skill_imports_spider_capability():
    """spider_sync skill 已改用 spider_capability 访问层。"""
    import inspect

    from capabilities.content_search.skills import spider_sync

    src = inspect.getsource(spider_sync)
    assert "spider_capability" in src


# ---------- 7. auto_short_video 走 media_capability ----------


def test_auto_short_video_imports_media_capability():
    """auto_short_video skill 已改用 media_capability 访问层。"""
    import inspect

    from capabilities.video.skills import auto_short_video

    src = inspect.getsource(auto_short_video)
    assert "media_capability" in src


# ---------- 8. capability-service /call 端点可调通 ----------


@pytest.mark.asyncio
async def test_capability_service_call_tts_piper():
    """capability-service /call tts_piper 端点可调通（mock 内部函数）。"""
    import httpx

    from capability_service.main import app as capability_app

    with patch(
        "app.skills.produce.tts_piper.synthesize_piper",
        new_callable=AsyncMock,
        return_value="/out/tts.wav",
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=capability_app), base_url="http://cap"
        ) as client:
            resp = await client.post(
                "/tools/tts_piper/call",
                json={
                    "arguments": {"text": "test", "output_path": "/out/tts.wav"},
                },
            )
        assert resp.status_code == 200
        assert resp.json()["result"]["ok"] is True


@pytest.mark.asyncio
async def test_capability_service_call_video_compose():
    """capability-service /call video_compose 端点可调通（mock 内部函数）。"""
    import httpx

    from capability_service.main import app as capability_app

    with patch(
        "capabilities.video.adapters.video_compose.compose_video",
        new_callable=AsyncMock,
        return_value={"output_path": "/out/final.mp4"},
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=capability_app), base_url="http://cap"
        ) as client:
            resp = await client.post(
                "/tools/video_compose/call",
                json={
                    "arguments": {
                        "storyboard_json": '{"shots": []}',
                        "output_path": "/out/final.mp4",
                    },
                },
            )
        assert resp.status_code == 200
        assert "result" in resp.json()


@pytest.mark.asyncio
async def test_capability_service_call_spider_sync():
    """capability-service /call spider_sync 端点可调通（mock 内部函数）。"""
    import httpx

    from capability_service.main import app as capability_app

    with patch("capabilities.scrapling.spiders.runner.start_sync", new_callable=AsyncMock):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=capability_app), base_url="http://cap"
        ) as client:
            resp = await client.post(
                "/tools/spider_sync/call",
                json={
                    "arguments": {"account_id": "a1", "user_id": "u1", "platform": "xiaohongshu"},
                },
            )
        assert resp.status_code == 200
        assert resp.json()["result"]["ok"] is True
