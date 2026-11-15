"""管理后台鉴权依赖 require_admin 的单元测试。

对应第七个安全问题：管理后台接口不得存在"无 token 即放行"的兜底路径，
非管理员身份必须被拒绝，且白名单为空时必须 fail-closed。
"""

from __future__ import annotations

import httpx
import pytest
from fastapi import Depends, FastAPI

from app.api.deps import require_admin
from app.config import get_settings
from app.security import create_jwt


def _build_app() -> FastAPI:
    app = FastAPI()

    @app.get("/admin/ping")
    async def ping(user_id: str = Depends(require_admin)) -> dict:
        return {"user_id": user_id}

    return app


async def _request(app: FastAPI, headers: dict | None = None) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.get("/admin/ping", headers=headers or {})


@pytest.fixture(autouse=True)
def secure_settings(monkeypatch):
    """为每个用例注入可用的 JWT 密钥与默认空白名单，不依赖真实 .env。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "jwt_secret_key", "test-secret-key-0123456789abcdef")
    monkeypatch.setattr(settings, "jwt_algorithm", "HS256")
    monkeypatch.setattr(settings, "admin_user_ids", "")
    return settings


async def test_require_admin_without_token_returns_401(secure_settings):
    """无 token 必须直接 401，不能落到任何兜底用户。"""
    response = await _request(_build_app())
    assert response.status_code == 401


async def test_require_admin_with_invalid_token_returns_401(secure_settings):
    """伪造 token 必须 401。"""
    response = await _request(_build_app(), {"Authorization": "Bearer not-a-jwt"})
    assert response.status_code == 401


async def test_require_admin_non_admin_returns_403(secure_settings, monkeypatch):
    """已登录但不在白名单必须 403。"""
    monkeypatch.setattr(secure_settings, "admin_user_ids", "admin_1")
    token = create_jwt("normal_user")
    response = await _request(_build_app(), {"Authorization": f"Bearer {token}"})
    assert response.status_code == 403


async def test_require_admin_admin_returns_200(secure_settings, monkeypatch):
    """白名单内的管理员可以正常访问。"""
    monkeypatch.setattr(secure_settings, "admin_user_ids", "admin_1,admin_2")
    token = create_jwt("admin_1")
    response = await _request(_build_app(), {"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json()["user_id"] == "admin_1"


async def test_require_admin_empty_allowlist_fails_closed(secure_settings):
    """白名单为空时，即使 token 合法也必须拒绝。"""
    token = create_jwt("admin_1")
    response = await _request(_build_app(), {"Authorization": f"Bearer {token}"})
    assert response.status_code == 403
