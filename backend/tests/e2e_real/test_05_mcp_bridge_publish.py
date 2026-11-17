"""E2E 真实 API：MCP 桥接 / 平台发布适配器。

覆盖此前「前端调了、后端没有」的两组端点：
- /api/mcp/bridge/{register,stream,result}
- /api/workflows/publish{,/weibo,/wechat_mp}

说明：发布端点受 settings.automation_enabled 总开关保护（默认 false），
因此真实发文无法在默认配置下验证；本文件验证的是「鉴权 + 总开关 + 参数校验」
这三层契约，真实发文需显式开启 automation_enabled 后人工验证。
"""

from __future__ import annotations

import json
import uuid

import pytest

pytestmark = pytest.mark.integration


def test_bridge_register_returns_hub_status(anon_client):
    """测试：桥接注册端点可用且返回集线器状态。

    场景：不带 JWT 调用 POST /api/mcp/bridge/register（桥接页面就是这么调的）。
    预期：200，返回 connected / pending / last_register。
    文档依据：backend/app/api/routers/mcp_bridge.py register_bridge。
    """
    resp = anon_client.post("/api/mcp/bridge/register", json={"page": "e2e-bridge"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["page"] == "e2e-bridge"
    assert isinstance(body["connected"], int)
    assert body["last_register"], "注册后应记录心跳时间"


def test_bridge_stream_opens_with_hello_event(anon_client):
    """测试：桥接 SSE 首帧是 hello，且可正常断开。

    场景：GET /api/mcp/bridge/stream，读取首个事件后关闭。
    预期：媒体类型为 text/event-stream，首帧 data 含 type=hello。
    文档依据：backend/app/services/mcp_bridge.py McpBridgeHub.stream。
    """
    with anon_client.stream("GET", "/api/mcp/bridge/stream") as resp:
        assert resp.status_code == 200, resp.read()
        assert "text/event-stream" in resp.headers.get("content-type", "")
        first = ""
        for line in resp.iter_lines():
            if line.startswith("data:"):
                first = line
                break
        assert first, "未收到任何 SSE 数据帧"
        payload = json.loads(first[len("data:") :].strip())
        assert payload["type"] == "hello"


def test_bridge_result_unknown_call_id_returns_404(client):
    """测试：回传未知 call_id 明确失败，不静默吞掉。

    场景：POST /api/mcp/bridge/result/{随机call_id}。
    预期：404（call_id 不存在或已超时）。
    文档依据：backend/app/api/routers/mcp_bridge.py post_result。
    """
    resp = client.post(
        f"/api/mcp/bridge/result/{uuid.uuid4().hex}",
        json={"success": True, "data": {}},
    )
    assert resp.status_code == 404, resp.text
    assert "call_id" in resp.text


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("/api/workflows/publish", {"title": "番茄工作法", "content": "25 分钟专注，5 分钟休息。"}),
        ("/api/workflows/publish/weibo", {"content": "今天试了番茄工作法，专注度明显提升。"}),
        (
            "/api/workflows/publish/wechat_mp",
            {"title": "番茄工作法实操", "content": "正文内容。"},
        ),
    ],
)
def test_publish_endpoints_require_auth(anon_client, path, payload):
    """测试：三个发布端点都必须鉴权。

    场景：不带 JWT 直接发布。
    预期：401/403，且不会触发任何浏览器动作。
    文档依据：backend/app/api/routers/workflows.py（Depends(get_current_user)）。
    """
    resp = anon_client.post(path, json=payload)
    assert resp.status_code in (401, 403), f"{path} 未鉴权: {resp.status_code} {resp.text}"


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("/api/workflows/publish", {"title": "番茄工作法", "content": "25 分钟专注。"}),
        ("/api/workflows/publish/weibo", {"content": "番茄工作法真香。"}),
        ("/api/workflows/publish/wechat_mp", {"title": "实操", "content": "正文。"}),
    ],
)
def test_publish_endpoints_blocked_by_automation_switch(client, path, payload):
    """测试：自动化总开关关闭时发布端点返回 403（安全闸门生效）。

    场景：默认配置（automation_enabled=false）调用发布。
    预期：403 且提示信息包含 automation_enabled。
    文档依据：backend/app/api/routers/workflows.py _publish 的总开关校验。
    """
    resp = client.post(path, json=payload)
    assert resp.status_code == 403, f"{path} 应被总开关拦截: {resp.status_code} {resp.text}"
    assert "automation_enabled" in resp.text


def test_publish_wechat_mp_requires_title(client):
    """测试：公众号发布缺少标题时明确报错（而非静默用空标题发布）。

    场景：automation_enabled 打开时提交空标题。
    预期：400「公众号发布必须提供标题」。
    文档依据：backend/app/api/routers/workflows.py publish_wechat_mp。
    """
    resp = client.post(
        "/api/workflows/publish/wechat_mp",
        json={"title": "", "content": "正文内容。"},
    )
    if resp.status_code == 403:
        pytest.skip("automation_enabled=false，总开关先于参数校验拦截")
    assert resp.status_code == 400, resp.text
    assert "标题" in resp.text


def test_publish_rejects_foreign_account_id(client, auth):
    """测试：account_id 不属于当前用户时拒绝发布（IDOR 防护）。

    场景：用一个不存在的 account_id 发布小红书。
    预期：404「账号不存在」。
    文档依据：backend/app/api/routers/workflows.py _resolve_account。
    """
    resp = client.post(
        "/api/workflows/publish",
        json={
            "title": "番茄工作法",
            "content": "25 分钟专注。",
            "account_id": "01JZZZZZZZZZZZZZZZZZZZZZZZ",
        },
    )
    if resp.status_code == 403:
        pytest.skip("automation_enabled=false，总开关先于账号校验拦截")
    assert resp.status_code == 404, resp.text
    assert "账号不存在" in resp.text
