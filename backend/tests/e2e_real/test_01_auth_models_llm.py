"""E2E 真实 API：认证 / 模型配置 / LLM 直连 / 前后端路径契约。

测试环境：后端 8000 端口真实运行，DeepSeek 走真实云端调用。
"""

from __future__ import annotations

import re

import pytest

from helpers import (
    collect_openai_stream,
    extract_frontend_api_paths,
    normalize_api_path,
    stream_text,
)

pytestmark = pytest.mark.integration

SECRET_PATTERN = re.compile(r"sk-[A-Za-z0-9_\-]{16,}")


def _walk_strings(node):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from _walk_strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk_strings(value)


def test_dev_login_returns_full_identity(auth: dict):
    """测试：dev-login 返回可用的 JWT 与用户身份。

    场景：后端 debug 模式下用固定邮箱一键登录。
    预期：token / refresh_token 非空，user_id 与 email 一致，昵称头像已解析。
    文档依据：backend/app/api/routers/auth.py dev_login。
    """
    for key in ("token", "refresh_token", "user_id", "nickname", "avatar_url"):
        assert auth.get(key), f"dev-login 缺少字段 {key}: {auth}"
    assert auth["token"].count(".") == 2, "JWT 结构异常"


def test_me_matches_login_identity(client, auth):
    """测试：/api/auth/me 与登录身份一致。

    场景：携带 Bearer token 查询当前用户。
    预期：返回同一 user_id；nickname 与登录返回一致；/me 不返回明文邮箱（隐私设计）。
    文档依据：backend/app/api/routers/auth.py get_me L61-76。
    """
    resp = client.get("/api/auth/me")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["user_id"] == auth["user_id"]
    assert data["nickname"] == auth["nickname"]
    assert "email" not in data, f"/me 意外返回了邮箱字段: {data}"


@pytest.mark.parametrize(
    "path",
    [
        "/api/chat/sessions",
        "/api/assets",
        "/api/models/available",
        "/api/topic-pool",
        "/api/my-works",
        "/api/skills",
        "/api/dashboard/overview",
    ],
)
def test_protected_endpoints_reject_anonymous(anon_client, path: str):
    """测试：核心业务端点拒绝匿名访问。

    场景：不带 Authorization 直接请求各业务接口。
    预期：401 或 403，且不返回业务数据。
    文档依据：backend/app/api/deps.py get_current_user。
    """
    resp = anon_client.get(path)
    assert resp.status_code in (401, 403), f"{path} 未鉴权即可访问: {resp.status_code} {resp.text[:200]}"


def test_refresh_token_issues_working_token(base_url, auth, anon_client):
    """测试：refresh token 可换发新访问令牌。

    场景：用 dev-login 的 refresh_token 调 /api/auth/refresh。
    预期：返回新 token，且新 token 能通过 /api/auth/me。
    文档依据：backend/app/api/routers/auth.py refresh。
    """
    resp = anon_client.post("/api/auth/refresh", params={"refresh_token": auth["refresh_token"]})
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    new_token = data.get("token") or data.get("access_token")
    assert new_token, f"refresh 未返回新 token: {data}"
    me = anon_client.get("/api/auth/me", headers={"Authorization": f"Bearer {new_token}"})
    assert me.status_code == 200, me.text


def test_available_models_contract(client):
    """测试：模型列表契约完整且至少有一个可直接调用的模型。

    场景：读取 /api/models/available。
    预期：每个模型含 model_id/api_model/provider/capabilities/has_api_key；
         至少一个 text 能力模型 has_api_key=True。
    文档依据：backend/app/api/routers/models.py list_available_models。
    """
    resp = client.get("/api/models/available")
    assert resp.status_code == 200, resp.text
    models = resp.json()["data"]
    assert isinstance(models, list) and models, "模型列表为空"

    for model in models:
        for key in ("model_id", "api_model", "provider", "capabilities", "has_api_key"):
            assert key in model, f"模型 {model.get('model_id')} 缺字段 {key}"
        assert isinstance(model["capabilities"], list)

    usable = [m for m in models if m["has_api_key"] and "text" in m["capabilities"]]
    assert usable, "没有任何配置了 Key 的文本模型，核心链路无法运行"


def test_providers_and_assignments_cover_core_agents(client):
    """测试：厂商清单与模型分配覆盖核心智能体。

    场景：读 /api/models/providers 与 /api/models/assignments。
    预期：厂商含 deepseek/dashscope 等；分配目标含 search/analyze/copywrite/
          image_plan/image_gen/image_review/audit/final_review 等核心角色。
    文档依据：backend/app/api/routers/models.py providers / assignments。
    """
    providers = client.get("/api/models/providers")
    assert providers.status_code == 200, providers.text
    plist = providers.json()["data"]
    assert plist and all("provider" in p for p in plist), plist
    provider_ids = {p["provider"] for p in plist}
    assert {"deepseek", "dashscope"} & provider_ids, f"缺少核心厂商: {provider_ids}"

    assignments = client.get("/api/models/assignments")
    assert assignments.status_code == 200, assignments.text
    data = assignments.json()["data"]
    keys = {t["target_key"] for t in data["targets"]}
    expected = {"search", "analyze", "copywrite", "image_plan", "image_gen", "image_review", "audit", "final_review"}
    missing = expected - keys
    assert not missing, f"模型分配缺少核心智能体: {missing}；现有: {sorted(keys)}"


def test_credentials_never_leak_plaintext_secret(client):
    """测试：凭证接口不泄露明文 API Key。

    场景：读 /api/models/credentials。
    预期：响应中不存在形如 sk-xxxx 的明文密钥。
    文档依据：backend/app/api/routers/models.py list_credentials（应只回掩码）。
    """
    resp = client.get("/api/models/credentials")
    assert resp.status_code == 200, resp.text
    leaked = [s for s in _walk_strings(resp.json()) if SECRET_PATTERN.search(s)]
    assert not leaked, f"凭证接口泄露明文密钥: {leaked[:2]}"


def test_chat_completions_real_llm_stream(client):
    """测试：OpenAI 兼容端点真实流式返回内容。

    场景：model=DeepSeek-V4-Flash，stream=True，真实调用 DeepSeek。
    预期：SSE 分块含非空 content，最后一帧为 [DONE]。
    文档依据：backend/app/api/routers/chat.py chat_completions。
    """
    chunks = collect_openai_stream(
        client,
        {
            "model": "DeepSeek-V4-Flash",
            "stream": True,
            "messages": [{"role": "user", "content": "只回复两个字：你好"}],
        },
        timeout=120.0,
    )
    text = stream_text(chunks).strip()
    assert text, f"真实 LLM 未返回内容: {chunks[:3]}"
    assert chunks[-1] == "[DONE]", "流未以 [DONE] 收尾"


def test_chat_completions_unknown_model_reports_friendly_error(client):
    """测试：未知模型给出明确错误而不是静默回退。

    场景：model=Not-A-Real-Model 调 /api/v1/chat/completions。
    预期：200 + 错误分块，文案包含「未知模型」。
    文档依据：backend/app/api/routers/chat.py L312-317。
    """
    chunks = collect_openai_stream(
        client,
        {"model": "Not-A-Real-Model", "stream": True, "messages": [{"role": "user", "content": "hi"}]},
        timeout=60.0,
    )
    blob = str(chunks)
    assert "未知模型" in blob, f"未知模型未给出明确提示: {blob[:300]}"


def test_highlight_endpoint_marks_key_sentences(client):
    """测试：划重点接口返回带 ==..== 标记的文本。

    场景：提交一段中文说明，density=medium。
    预期：highlighted_text 含至少一处 == 标记（真实 LLM）。
    文档依据：backend/app/api/routers/chat.py highlight_text。
    """
    resp = client.post(
        "/api/v1/chat/highlight",
        json={
            "text": "番茄工作法是每工作25分钟休息5分钟。它能降低拖延，提高专注度。连续四个番茄钟后休息15分钟。",
            "density": "medium",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "==" in body["highlighted_text"], f"未标记重点: {body['highlighted_text']}"


def test_frontend_api_paths_all_exist_in_backend(client, repo_root):
    """测试：前端引用的每个 /api 路径在后端 openapi 中都存在。

    场景：扫描 frontend/src 的 .ts/.vue 字面量路径，与后端 openapi.json 比对。
    预期：无任何缺失路径（此前的 MCP 桥接与平台发布契约缺口已补齐）。
    文档依据：frontend/src/api/*.ts + backend openapi。
    """
    openapi = client.get("/openapi.json").json()
    backend_paths = {normalize_api_path(p) for p in openapi["paths"]}

    calls = extract_frontend_api_paths(repo_root / "frontend" / "src")
    assert calls, "未扫描到任何前端 API 路径，检查 frontend/src 是否存在"

    missing: dict[str, set[str]] = {}
    for source, raw in calls:
        path = normalize_api_path(raw)
        if path in backend_paths:
            continue
        if any(bp.startswith(path) for bp in backend_paths):
            continue
        missing.setdefault(path, set()).add(source)

    assert not missing, (
        "以下前端路径在后端 openapi 中不存在（新增契约漂移）：\n"
        + "\n".join(f"  {p}  <- {sorted(s)[:2]}" for p, s in sorted(missing.items()))
    )