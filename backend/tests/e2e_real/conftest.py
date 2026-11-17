"""真实 API E2E 测试夹具。

设计约束：
- 真实后端（默认 http://127.0.0.1:8000），真实第三方 API，不 mock。
- 后端未启动时整目录 skip，保证全量 pytest 不会因环境缺失变红。
- 会话级共享 dev-login 得到的 JWT，避免每次重复登录。
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx
import pytest

BASE_URL = os.getenv("E2E_BASE_URL", "http://127.0.0.1:8000")
DEV_LOGIN_EMAIL = os.getenv("E2E_EMAIL", "1693241521@qq.com")
REPO_ROOT = Path(__file__).resolve().parents[3]


def _health() -> tuple[bool, str]:
    try:
        resp = httpx.get(f"{BASE_URL}/health", timeout=5.0, trust_env=False)
        return resp.status_code == 200, resp.text[:200]
    except Exception as exc:  # noqa: BLE001 - 探测失败即视为未启动
        return False, str(exc)


@pytest.fixture(scope="session")
def base_url() -> str:
    ok, detail = _health()
    if not ok:
        pytest.skip(f"后端未就绪（{BASE_URL}）：{detail}")
    return BASE_URL


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def auth(base_url: str) -> dict:
    resp = httpx.post(
        f"{base_url}/api/auth/dev-login",
        params={"email": DEV_LOGIN_EMAIL},
        timeout=30.0,
        trust_env=False,
    )
    assert resp.status_code == 200, f"dev-login 失败: {resp.status_code} {resp.text}"
    payload = resp.json()
    assert payload.get("success") is True, payload
    return payload["data"]


@pytest.fixture(scope="session")
def client(base_url: str, auth: dict):
    with httpx.Client(
        base_url=base_url,
        headers={"Authorization": f"Bearer {auth['token']}"},
        timeout=httpx.Timeout(connect=15.0, read=600.0, write=120.0, pool=30.0),
        trust_env=False,
    ) as http:
        yield http


@pytest.fixture(scope="session")
def anon_client(base_url: str):
    with httpx.Client(base_url=base_url, timeout=30.0, trust_env=False) as http:
        yield http


@pytest.fixture(scope="session")
def qa_session_id(client) -> str:
    """会话级共享：一个带一条消息的普通会话，供反馈等模块关联使用。"""
    created = client.post("/api/chat/sessions", json={"title": "E2E 共享会话"})
    assert created.status_code == 200, created.text
    session_id = created.json()["data"]["id"]
    client.post(
        f"/api/chat/sessions/{session_id}/messages",
        json={"role": "user", "content": "E2E 共享会话初始化消息"},
    )
    yield session_id
    client.delete(f"/api/chat/sessions/{session_id}")


@pytest.fixture(scope="session")
def second_user_client(base_url: str):
    """第二个账号，用于越权访问校验。"""
    resp = httpx.post(
        f"{base_url}/api/auth/dev-login",
        params={"email": "e2e-probe-b@example.com"},
        timeout=30.0,
        trust_env=False,
    )
    if resp.status_code != 200 or resp.json().get("success") is not True:
        pytest.skip(f"第二账号 dev-login 不可用: {resp.status_code} {resp.text[:200]}")
    token = resp.json()["data"]["token"]
    with httpx.Client(
        base_url=base_url,
        headers={"Authorization": f"Bearer {token}"},
        timeout=httpx.Timeout(connect=15.0, read=300.0, write=60.0, pool=30.0),
        trust_env=False,
    ) as http:
        yield http
