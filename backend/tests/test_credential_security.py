"""BYOK 凭据安全加固测试。

覆盖六项安全缺口：
  1. AES-GCM 密文用 user_id:provider 作为 AAD 绑定，跨用户搬运会解密失败
  2. 凭据 save / delete / verify / use 写入审计日志
  3. 自定义 base_url 的 SSRF 防护（内网 / 元数据 / 非 http(s)）
  4. 错误信息脱敏，不把 Key 回传给前端
  5. 保存 / 验证接口限流
  6. JWT 不再接受 ?token= 查询参数
"""

from __future__ import annotations

import httpx
import pytest
from fastapi import Depends, FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api.deps import get_current_user
from app.api.routers import models as models_router_module
from app.config import get_settings
from app.crypto.token_crypto import TokenCrypto, build_credential_aad
from app.db.models import CredentialAuditLog, UserProviderCredential
from app.db.session import Base, get_db
from app.security import create_jwt
from app.services.rate_limit import RateLimitExceeded, check_rate_limit, reset_rate_limits
from app.services.url_guard import UnsafeUrlError, validate_outbound_url

# ===== 公共 fixture =====

@pytest.fixture(autouse=True)
def secure_settings(monkeypatch):
    """注入测试用加密/JWT 密钥，并清空限流计数，避免依赖真实 .env。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "aes_secret_key", TokenCrypto.generate_key())
    monkeypatch.setattr(settings, "jwt_secret_key", "test-secret-key-0123456789abcdef")
    monkeypatch.setattr(settings, "jwt_algorithm", "HS256")
    reset_rate_limits()
    return settings


@pytest.fixture
async def session_factory():
    """内存 SQLite，只建凭据与审计两张表，隔离真实数据库。"""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    tables = [UserProviderCredential.__table__, CredentialAuditLog.__table__]
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: Base.metadata.create_all(sync_conn, tables=tables))
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.fixture
async def api_client(session_factory):
    """挂载 models 路由，固定当前用户为 user_a。"""
    app = FastAPI()
    app.include_router(models_router_module.router)

    async def _override_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: "user_a"

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


# ===== 1. AAD 绑定 =====

def test_aad_binds_ciphertext_to_user_and_provider():
    crypto = TokenCrypto()
    aad_a = build_credential_aad("user_a", "deepseek")
    ciphertext = crypto.encrypt("sk-secret-123", aad=aad_a)

    assert crypto.decrypt(ciphertext, aad=aad_a) == "sk-secret-123"

    # 换成别的用户 / 别的厂商都无法解密
    with pytest.raises(Exception):
        crypto.decrypt(ciphertext, aad=build_credential_aad("user_b", "deepseek"))
    with pytest.raises(Exception):
        crypto.decrypt(ciphertext, aad=build_credential_aad("user_a", "openai"))
    with pytest.raises(Exception):
        crypto.decrypt_with_fallback(ciphertext, aad=build_credential_aad("user_b", "deepseek"))


def test_decrypt_with_fallback_reads_legacy_ciphertext():
    """历史无 AAD 密文仍可读取，且无需迁移。"""
    crypto = TokenCrypto()
    legacy = crypto.encrypt("sk-legacy-123")
    assert crypto.decrypt_with_fallback(
        legacy, aad=build_credential_aad("user_a", "deepseek")
    ) == "sk-legacy-123"


# ===== 2. 审计日志 =====

async def test_save_writes_audit_and_encrypts_with_aad(api_client, session_factory):
    plaintext_key = "sk-secret-key-abcdef123456"
    response = await api_client.put(
        "/api/models/credentials/deepseek",
        json={
            "api_key": plaintext_key,
            "custom_base_url": "https://8.8.8.8/v1",
            "custom_models": ["my-custom-model"],
        },
    )
    assert response.status_code == 200

    async with session_factory() as db:
        cred = (await db.execute(select(UserProviderCredential))).scalar_one()
        audit = (await db.execute(select(CredentialAuditLog))).scalars().all()

    # 密文里不含明文
    assert plaintext_key not in cred.encrypted_api_key
    # 正确 AAD 可解密，错误 AAD 不可解密
    assert TokenCrypto().decrypt(
        cred.encrypted_api_key, aad=build_credential_aad("user_a", "deepseek")
    ) == plaintext_key
    with pytest.raises(Exception):
        TokenCrypto().decrypt(
            cred.encrypted_api_key, aad=build_credential_aad("user_b", "deepseek")
        )

    assert len(audit) == 1
    assert audit[0].action == "save"
    assert audit[0].result == "ok"
    assert plaintext_key not in (audit[0].detail or "")


async def test_delete_writes_audit(api_client, session_factory):
    await api_client.put(
        "/api/models/credentials/deepseek",
        json={"api_key": "sk-secret-key-abcdef123456"},
    )
    response = await api_client.delete("/api/models/credentials/deepseek")
    assert response.status_code == 200

    async with session_factory() as db:
        audit = (await db.execute(select(CredentialAuditLog))).scalars().all()

    actions = [a.action for a in audit]
    assert actions.count("save") == 1
    assert actions.count("delete") == 1


# ===== 3. SSRF 防护 =====

@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/file",
        "http://localhost/v1",
        "http://127.0.0.1:8080/v1",
        "http://10.0.0.1/v1",
        "http://169.254.169.254/latest/meta-data/",
        "http://metadata.google.internal/computeMetadata/v1/",
        "http://internal-service.internal/v1",
    ],
)
def test_url_guard_blocks_unsafe_targets(url):
    with pytest.raises(UnsafeUrlError):
        validate_outbound_url(url, resolve_hostnames=False)


def test_url_guard_allows_public_https():
    assert validate_outbound_url("https://8.8.8.8/v1", resolve_hostnames=False) == "https://8.8.8.8/v1"
    assert validate_outbound_url("") == ""


async def test_save_rejects_private_base_url_and_audits_denial(api_client, session_factory):
    response = await api_client.put(
        "/api/models/credentials/deepseek",
        json={"api_key": "sk-secret-key-abcdef123456", "custom_base_url": "http://127.0.0.1:9000/v1"},
    )
    assert response.status_code == 400

    async with session_factory() as db:
        audit = (await db.execute(select(CredentialAuditLog))).scalars().all()

    assert len(audit) == 1
    assert audit[0].action == "save"
    assert audit[0].result == "denied"


# ===== 4. 错误信息脱敏 =====

def test_safe_error_hides_api_key():
    from app.api.routers.models import _safe_error

    exc = Exception("401 Unauthorized: incorrect key sk-live-abcdefghijklmnop")
    safe = _safe_error(exc)
    assert "sk-live-abcdefghijklmnop" not in safe
    assert "[已隐藏]" in safe


# ===== 5. 限流 =====

def test_rate_limit_sliding_window():
    reset_rate_limits()
    key = "test-window"
    check_rate_limit(key, limit=2, window_seconds=60, now=1000.0)
    check_rate_limit(key, limit=2, window_seconds=60, now=1001.0)
    with pytest.raises(RateLimitExceeded) as exc_info:
        check_rate_limit(key, limit=2, window_seconds=60, now=1002.0)
    assert exc_info.value.retry_after >= 1

    # 窗口滑过后旧记录被清理，可再次通过
    check_rate_limit(key, limit=2, window_seconds=60, now=1062.0)


async def test_save_rate_limit_returns_429(api_client, monkeypatch):
    monkeypatch.setattr(get_settings(), "byok_save_rate_limit", 2)
    payload = {"api_key": "sk-secret-key-abcdef123456"}
    assert (await api_client.put("/api/models/credentials/deepseek", json=payload)).status_code == 200
    assert (await api_client.put("/api/models/credentials/deepseek", json=payload)).status_code == 200
    third = await api_client.put("/api/models/credentials/deepseek", json=payload)
    assert third.status_code == 429
    assert third.headers.get("retry-after")


# ===== 6. 移除 query token =====

async def test_jwt_query_token_is_rejected():
    token = create_jwt("user_a")

    app = FastAPI()

    @app.get("/whoami")
    async def whoami(user_id: str = Depends(get_current_user)) -> dict:
        return {"user_id": user_id}

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        # ?token= 不再被接受
        query_response = await client.get("/whoami", params={"token": token})
        assert query_response.status_code == 401

        # Authorization header 正常通过
        header_response = await client.get(
            "/whoami", headers={"Authorization": f"Bearer {token}"}
        )
        assert header_response.status_code == 200
        assert header_response.json()["user_id"] == "user_a"
