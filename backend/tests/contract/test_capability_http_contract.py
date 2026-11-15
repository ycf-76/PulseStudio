"""三期契约测试：能力服务 HTTP 协议 + Transport 路由（docs/内核-能力分层架构方案.md §4 三期）。

锁定内容：
1. 能力服务协议三方法（health / tools / call）——直接 ASGI 直连，免起端口
2. 双跑等价：grid_detect/grid_split 同一断言在 InProcess（函数包装）与
   Http（ASGI）两种 transport 下结果一致——内核零改动切换的机制保证
3. CompositeTransport 按名路由：remote 工具走 HTTP，其余进程内
4. grid_capability remote 模式端到端（monkeypatch grid_service_url）

全部离线，不起真端口。
"""
from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

pytest.importorskip("numpy", reason="grid 能力需要 numpy/Pillow")

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # backend/ 根，保证 capability_service 可导入

from capability_service.main import app as capability_app  # noqa: E402

from app.engine.protocol.transport import (  # noqa: E402
    CompositeTransport,
    HttpTransport,
    InProcessTransport,
)
from app.services.grid_types import GridDetection  # noqa: E402


def _make_test_image(tmp_path: Path) -> Path:
    """造一张 900x900 的 3x3 拼图样图（九宫格色块，接缝明显）。"""
    from PIL import Image

    im = Image.new("RGB", (900, 900), (255, 255, 255))
    colors = [(30, 30, 30), (200, 200, 200), (90, 140, 220)]
    for c in range(3):
        for r in range(3):
            im.paste(colors[(c + r) % 3], (c * 300 + 2, r * 300 + 2, (c + 1) * 300 - 2, (r + 1) * 300 - 2))
    p = tmp_path / "grid3x3.png"
    im.save(p)
    return p


# ---------- 1. 能力服务协议 ----------

@pytest.mark.asyncio
async def test_health_endpoint():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=capability_app), base_url="http://cap") as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


@pytest.mark.asyncio
async def test_tools_list_endpoint():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=capability_app), base_url="http://cap") as client:
        resp = await client.get("/tools")
    names = {t["name"] for t in resp.json()["tools"]}
    assert {"grid_detect", "grid_split", "render_htmls"} <= names


@pytest.mark.asyncio
async def test_call_unknown_tool_404():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=capability_app), base_url="http://cap") as client:
        resp = await client.post("/tools/nope/call", json={"arguments": {}})
    assert resp.status_code == 404


# ---------- 2. 双跑等价：InProcess vs Http ----------

@pytest.mark.asyncio
async def test_grid_detect_dual_run_equivalent(tmp_path):
    image = _make_test_image(tmp_path)

    # in-process：直接包同步函数
    from app.services.grid_detect import detect_grid as local_detect

    local_det = await asyncio_to_thread(local_detect, image)

    # http：ASGI 直连 capability app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=capability_app), base_url="http://cap") as client:
        resp = await client.post(
            "/tools/grid_detect/call", json={"arguments": {"image_path": str(image)}}
        )
    remote_det = GridDetection.from_dict(resp.json()["result"])

    assert local_det.is_grid == remote_det.is_grid
    if remote_det.is_grid:
        assert (local_det.rows, local_det.cols) == (remote_det.rows, remote_det.cols)


async def asyncio_to_thread(fn, *args):
    import asyncio

    return await asyncio.to_thread(fn, *args)


@pytest.mark.asyncio
async def test_grid_split_dual_run_equivalent(tmp_path):
    image = _make_test_image(tmp_path)
    out_local = tmp_path / "tiles_local"
    out_remote = tmp_path / "tiles_remote"

    from app.services.grid_detect import detect_grid as local_detect
    from app.services.grid_detect import split_grid as local_split

    det = await asyncio_to_thread(local_detect, image)
    assert det.is_grid, "样图应被识别为拼图"

    tiles_local = await asyncio_to_thread(local_split, image, out_local, det)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=capability_app), base_url="http://cap") as client:
        resp = await client.post(
            "/tools/grid_split/call",
            json={
                "arguments": {
                    "image_path": str(image),
                    "out_dir": str(out_remote),
                    "detection": det.to_dict(),
                }
            },
        )
    result = resp.json()["result"]
    assert result["ok"] is True
    assert len(result["tiles"]) == len(tiles_local) > 0


# ---------- 3. CompositeTransport 路由 ----------

@pytest.mark.asyncio
async def test_composite_routes_remote_tools(tmp_path):
    image = _make_test_image(tmp_path)

    class _MarkerPrimitive:
        name = "bash"
        required_permissions = frozenset()

        async def execute(self, **kwargs):
            return {"ok": True, "source": "in_process"}

    composite = CompositeTransport(
        in_process=InProcessTransport({"bash": _MarkerPrimitive()}),
        remote_services={},
    )
    http = HttpTransport(
        "http://cap",
        client=httpx.AsyncClient(transport=httpx.ASGITransport(app=capability_app), base_url="http://cap"),
    )
    await http.refresh_tools()  # HttpTransport.get_tool 依赖工具清单缓存
    composite.register_remote({"grid_detect"}, http)

    # bash 走进程内
    r1 = await composite.call("bash", {"command": "ls"})
    assert r1["source"] == "in_process"
    # grid_detect 走 HTTP
    r2 = await composite.call("grid_detect", {"image_path": str(image)})
    assert r2.get("ok") is True or "rows" in r2, f"grid_detect 返回异常: {r2}"
    # 清单合并（bash 进程内 + grid_detect 远端）
    names = {h.name for h in composite.list_tools()}
    assert {"bash", "grid_detect"} <= names
    # get_tool 路由
    assert composite.get_tool("bash").name == "bash"
    assert composite.get_tool("grid_detect").name == "grid_detect"


# ---------- 4. grid_capability remote 端到端 ----------

@pytest.mark.asyncio
async def test_grid_capability_remote_mode(tmp_path, monkeypatch):
    from app.services import grid_capability

    image = _make_test_image(tmp_path)

    class _FakeSettings:
        grid_service_url = "http://cap"

    monkeypatch.setattr(
        grid_capability, "_service_url", lambda: "http://cap"
    )

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=capability_app), base_url="http://cap") as client:
        monkeypatch.setattr(HttpTransport, "_ensure_client", lambda self: client)
        det = await grid_capability.detect_grid(image)
        assert isinstance(det, GridDetection)
        assert det.is_grid

        out_dir = tmp_path / "cap_tiles"
        tiles = await grid_capability.split_grid(image, out_dir, det)
        assert len(tiles) == det.rows * det.cols
        assert all(t.exists() for t in tiles)


# ---------- 5. browser_capability 双跑契约（五期-A） ----------

@pytest.mark.asyncio
async def test_render_htmls_registered_in_capability_service():
    """render_htmls 工具已在 capability-service 注册。"""
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=capability_app), base_url="http://cap") as client:
        resp = await client.get("/tools")
    names = {t["name"] for t in resp.json()["tools"]}
    assert "render_htmls" in names


@pytest.mark.asyncio
async def test_browser_capability_local_mode():
    """browser_capability.render_card_pngs() local 模式：toml runtime=inprocess 时走进程内。"""
    from app.services import browser_capability

    assert browser_capability._service_url() == ""


# ---------- 6. 五期-B：声明即拓扑（runtime 字段生效） ----------


def test_get_capability_runtime_default_inprocess():
    """toml 中 runtime=inprocess（默认）→ get_capability_runtime 返回 inprocess。"""
    from app.core.capability_loader import get_capability_runtime

    assert get_capability_runtime("grid") == "inprocess"
    assert get_capability_runtime("browser") == "inprocess"
    assert get_capability_runtime("media") == "inprocess"
    assert get_capability_runtime("spider") == "inprocess"


def test_get_capability_runtime_unknown_capability():
    """toml 中不存在的 capability → 默认 inprocess。"""
    from app.core.capability_loader import get_capability_runtime

    assert get_capability_runtime("nonexistent") == "inprocess"


def test_get_capability_service_url_inprocess_returns_empty():
    """runtime=inprocess → get_capability_service_url 返回空串（进程内模式）。"""
    from app.core.capability_loader import get_capability_service_url

    assert get_capability_service_url("grid") == ""
    assert get_capability_service_url("browser") == ""
    assert get_capability_service_url("media") == ""
    assert get_capability_service_url("spider") == ""


def test_get_capability_routing_table_snapshot():
    """路由表快照：当前 toml 声明所有可外置能力均为 inprocess。"""
    from app.core.capability_loader import get_capability_routing_table

    table = get_capability_routing_table()
    assert set(table.keys()) == {"grid", "browser", "media", "spider"}
    for cap_name, info in table.items():
        assert info["runtime"] == "inprocess", f"{cap_name} should be inprocess"
        assert info["service_url"] == "", f"{cap_name} service_url should be empty"
        assert info["status"] == "inprocess", f"{cap_name} status should be inprocess"


def test_get_capability_service_url_service_mode_with_url(monkeypatch):
    """runtime=service + URL 非空 → 返回 URL。

    注意：Settings 是 lru_cache 单例，monkeypatch.setenv 之后必须 cache_clear，
    否则会读到前面用例缓存下来的空 URL（单独跑绿、整目录跑红 = 顺序依赖）。
    """
    from app.core.capability_loader import get_capability_service_url, _read_toml_data
    from app.config import get_settings

    original_data = _read_toml_data()

    class _FakeData:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass

    import app.core.capability_loader as loader_mod

    fake_data = dict(original_data)
    fake_grid = dict(fake_data.get("grid", {}))
    fake_grid["runtime"] = "service"
    fake_data["grid"] = fake_grid

    monkeypatch.setattr(loader_mod, "_read_toml_data", lambda: fake_data)
    monkeypatch.setenv("GRID_SERVICE_URL", "http://cap:8100")
    get_settings.cache_clear()

    try:
        url = get_capability_service_url("grid")
    finally:
        get_settings.cache_clear()

    assert url == "http://cap:8100"


def test_get_capability_service_url_service_mode_no_url_warns(monkeypatch):
    """runtime=service + URL 为空 → 告警 + 返回空串（回退 inprocess）。"""
    from app.core.capability_loader import get_capability_service_url

    import app.core.capability_loader as loader_mod

    original_data = loader_mod._read_toml_data()
    fake_data = dict(original_data)
    fake_browser = dict(fake_data.get("browser", {}))
    fake_browser["runtime"] = "service"
    fake_data["browser"] = fake_browser

    monkeypatch.setattr(loader_mod, "_read_toml_data", lambda: fake_data)
    monkeypatch.delenv("BROWSER_SERVICE_URL", raising=False)

    url = get_capability_service_url("browser")
    assert url == ""


def test_grid_capability_uses_toml_runtime(monkeypatch):
    """grid_capability._service_url() 现在走 capability_loader 声明。"""
    from app.services import grid_capability
    from app.core.capability_loader import get_capability_service_url

    assert grid_capability._service_url() == get_capability_service_url("grid")


def test_browser_capability_uses_toml_runtime():
    """browser_capability._service_url() 现在走 capability_loader 声明。"""
    from app.services import browser_capability
    from app.core.capability_loader import get_capability_service_url

    assert browser_capability._service_url() == get_capability_service_url("browser")


# ---------- 7. 五期-C：长任务进度通道 ----------


@pytest.mark.asyncio
async def test_internal_events_endpoint_receives_and_forwards():
    """POST /internal/events 接收进度事件并转发进 sse_bus。"""
    from app.api.routers.internal import router
    from fastapi import FastAPI

    internal_app = FastAPI()
    internal_app.include_router(router)

    workflow_id = "test_wf_progress"

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=internal_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/internal/events",
            json={
                "workflow_id": workflow_id,
                "event_type": "multi_image_video_progress",
                "payload": {"step": "rendering", "progress": 0.5},
                "turn_id": "turn_001",
            },
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert "event_id" in body

    from app.services.sse_bus import sse_bus

    history = sse_bus._event_history.get(workflow_id, [])
    assert len(history) >= 1
    last_event = history[-1]
    assert last_event.event_type == "multi_image_video_progress"
    assert last_event.payload["step"] == "rendering"
    assert last_event.turn_id == "turn_001"

    sse_bus.cleanup_workflow(workflow_id)


@pytest.mark.asyncio
async def test_internal_events_without_turn_id():
    """POST /internal/events 不传 turn_id 也能正常工作。"""
    from app.api.routers.internal import router
    from fastapi import FastAPI

    internal_app = FastAPI()
    internal_app.include_router(router)

    workflow_id = "test_wf_no_turn"

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=internal_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/internal/events",
            json={
                "workflow_id": workflow_id,
                "event_type": "progress_update",
                "payload": {"percent": 80},
            },
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True

    from app.services.sse_bus import sse_bus

    history = sse_bus._event_history.get(workflow_id, [])
    assert len(history) >= 1
    assert history[-1].turn_id is None

    sse_bus.cleanup_workflow(workflow_id)


@pytest.mark.asyncio
async def test_capability_service_emit_progress_no_callback():
    """emit_progress() 无回调时不报错，静默丢弃。"""
    from capability_service.main import emit_progress, set_progress_callback

    set_progress_callback(None)
    await emit_progress({"workflow_id": "x", "event_type": "test", "payload": {}})


@pytest.mark.asyncio
async def test_capability_service_emit_progress_with_callback():
    """emit_progress() 有回调时正确调用。"""
    from capability_service.main import emit_progress, set_progress_callback

    received = []

    async def _cb(event):
        received.append(event)

    set_progress_callback(_cb)
    await emit_progress({"workflow_id": "x", "event_type": "test", "payload": {"a": 1}})
    assert len(received) == 1
    assert received[0]["event_type"] == "test"
    assert received[0]["payload"]["a"] == 1

    set_progress_callback(None)


def test_event_bridge_install_without_env():
    """CAPABILITY_EVENTS_URL 未设置时，桥不安装。"""
    import os

    old = os.environ.pop("CAPABILITY_EVENTS_URL", None)
    try:
        from app.services.capability_event_bridge import install_progress_bridge

        result = install_progress_bridge()
        assert result is False
    finally:
        if old is not None:
            os.environ["CAPABILITY_EVENTS_URL"] = old


@pytest.mark.asyncio
async def test_progress_dual_run_equivalent():
    """进度事件双跑等价：进程内直发 vs 经 /internal/events 转发，SSE 总线收到同一结构。"""
    from app.services.sse_bus import sse_bus
    from app.api.routers.internal import router
    from fastapi import FastAPI

    workflow_id = "test_dual_progress"

    # 路径1：进程内直发
    eid1 = await sse_bus.publish(
        workflow_id,
        "multi_image_video_progress",
        {"step": "composing", "progress": 0.3},
        turn_id="t1",
    )

    # 路径2：经 /internal/events 转发
    internal_app = FastAPI()
    internal_app.include_router(router)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=internal_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/internal/events",
            json={
                "workflow_id": workflow_id,
                "event_type": "multi_image_video_progress",
                "payload": {"step": "composing", "progress": 0.3},
                "turn_id": "t1",
            },
        )
    eid2 = resp.json()["event_id"]

    # 两条路径产生的事件结构一致（event_id 不同但其余字段相同）
    history = sse_bus._event_history.get(workflow_id, [])
    assert len(history) == 2
    e1, e2 = history[0], history[1]
    assert e1.event_type == e2.event_type == "multi_image_video_progress"
    assert e1.payload == e2.payload == {"step": "composing", "progress": 0.3}
    assert e1.turn_id == e2.turn_id == "t1"
    assert eid1 != eid2

    sse_bus.cleanup_workflow(workflow_id)


# ---------- 8. 五期-D：media-service（onnx/ffmpeg 隔离） ----------


@pytest.mark.asyncio
async def test_tts_piper_registered_in_capability_service():
    """tts_piper 工具已在 capability-service 注册。"""
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=capability_app), base_url="http://cap") as client:
        resp = await client.get("/tools")
    names = {t["name"] for t in resp.json()["tools"]}
    assert "tts_piper" in names


@pytest.mark.asyncio
async def test_video_compose_registered_in_capability_service():
    """video_compose 工具已在 capability-service 注册。"""
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=capability_app), base_url="http://cap") as client:
        resp = await client.get("/tools")
    names = {t["name"] for t in resp.json()["tools"]}
    assert "video_compose" in names


def test_media_capability_local_mode():
    """media_capability._service_url() 在 toml runtime=inprocess 时返回空串。"""
    from app.services import media_capability

    assert media_capability._service_url() == ""


def test_media_capability_uses_toml_runtime():
    """media_capability._service_url() 走 capability_loader 声明。"""
    from app.services import media_capability
    from app.core.capability_loader import get_capability_service_url

    assert media_capability._service_url() == get_capability_service_url("media")


def test_media_service_url_in_settings():
    """Settings.media_service_url 字段存在且默认为空。"""
    from app.config import get_settings

    s = get_settings()
    assert hasattr(s, "media_service_url")
    assert s.media_service_url == ""


def test_capability_tools_count_includes_media():
    """capability-service 工具列表包含全部 6 个工具（grid*2 + render_htmls + tts_piper + video_compose + spider_sync）。"""
    from capability_service.main import CAPABILITY_TOOLS

    expected = {"grid_detect", "grid_split", "render_htmls", "tts_piper", "video_compose", "spider_sync"}
    assert set(CAPABILITY_TOOLS.keys()) == expected


# ---------- 9. 五期-E：spider-service（Scrapling+Playwright 隔离） ----------


@pytest.mark.asyncio
async def test_spider_sync_registered_in_capability_service():
    """spider_sync 工具已在 capability-service 注册。"""
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=capability_app), base_url="http://cap") as client:
        resp = await client.get("/tools")
    names = {t["name"] for t in resp.json()["tools"]}
    assert "spider_sync" in names


def test_spider_capability_local_mode():
    """spider_capability._service_url() 在 toml runtime=inprocess 时返回空串。"""
    from app.services import spider_capability

    assert spider_capability._service_url() == ""


def test_spider_capability_uses_toml_runtime():
    """spider_capability._service_url() 走 capability_loader 声明。"""
    from app.services import spider_capability
    from app.core.capability_loader import get_capability_service_url

    assert spider_capability._service_url() == get_capability_service_url("spider")


def test_spider_service_url_in_settings():
    """Settings.spider_service_url 字段存在且默认为空。"""
    from app.config import get_settings

    s = get_settings()
    assert hasattr(s, "spider_service_url")
    assert s.spider_service_url == ""


def test_spider_capability_has_start_sync():
    """spider_capability 访问层暴露 start_sync 函数。"""
    from app.services import spider_capability

    assert hasattr(spider_capability, "start_sync")
    import inspect
    assert inspect.iscoroutinefunction(spider_capability.start_sync)