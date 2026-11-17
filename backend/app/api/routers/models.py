"""Models API — BYOK 动态模型管理。

提供：
  - GET  /providers          — 列出所有支持的 Provider
  - GET  /available          — 列出当前用户可用的模型（已配 Key 的 Provider 才可用）
  - GET  /credentials        — 列出当前用户已配置的凭据（Key 脱敏）
  - PUT  /credentials/{provider}  — 保存/更新某 Provider 的凭据
  - DELETE /credentials/{provider} — 删除某 Provider 的凭据
  - POST /credentials/{provider}/verify — 验证 API Key 有效性
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.api.schemas.common import StandardResponse
from app.config import get_settings
from app.crypto.token_crypto import TokenCrypto, build_credential_aad
from app.db.models import UserProviderCredential
from app.db.session import get_db
from app.services.content_guard import redact
from app.services.credential_audit import (
    ACTION_DELETE,
    ACTION_SAVE,
    ACTION_VERIFY,
    RESULT_DENIED,
    RESULT_FAIL,
    RESULT_OK,
    client_ip,
    record_audit,
)
from app.services.provider_registry import (
    ProviderType,
    get_all_providers,
    get_global_fallback_key,
    get_provider,
)
from app.services.rate_limit import RateLimitExceeded, check_rate_limit
from app.services.url_guard import UnsafeUrlError, validate_outbound_url

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/models", tags=["models"])


# ===== Schemas =====


class ProviderInfo(BaseModel):
    provider: str
    provider_name: str
    icon: str
    type: str
    default_base_url: str
    allow_custom_base_url: bool
    allow_custom_models: bool
    docs_url: str = ""
    key_placeholder: str = ""
    has_api_key: bool = False
    source: str = ""


class ModelInfo(BaseModel):
    model_id: str
    api_model: str
    display_name: str
    description: str
    capabilities: list[str]
    max_tokens: int
    provider: str


class AvailableModel(ModelInfo):
    has_api_key: bool
    is_active: bool = True
    source: str = "user"


class CredentialStatus(BaseModel):
    provider: str
    provider_name: str
    icon: str
    has_api_key: bool
    custom_base_url: str
    custom_models: list[str]
    is_active: bool


class SaveCredentialRequest(BaseModel):
    api_key: str = Field(..., min_length=1, description="API Key（明文传入，服务端加密存储）")
    custom_base_url: str = ""
    custom_models: list[str] = Field(default_factory=list)
    is_active: bool = True


class VerifyResult(BaseModel):
    valid: bool
    error: str = ""
    models_count: int = 0


class AssignmentTarget(BaseModel):
    target_type: str
    target_key: str
    label: str
    subtitle: str = ""
    default_model: str = ""
    assigned_model: str = ""


class AssignmentList(BaseModel):
    default_model: str = ""
    targets: list[AssignmentTarget] = Field(default_factory=list)


class SaveAssignmentRequest(BaseModel):
    target_type: str = Field(..., description="default / agent / skill")
    target_key: str = ""
    model: str = ""


# ===== Helpers =====


def _get_crypto() -> TokenCrypto:
    return TokenCrypto()


async def _get_user_credential(
    db: AsyncSession, user_id: str, provider: str
) -> UserProviderCredential | None:
    stmt = select(UserProviderCredential).where(
        UserProviderCredential.user_id == user_id,
        UserProviderCredential.provider == provider,
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def _get_all_user_credentials(db: AsyncSession, user_id: str) -> list[UserProviderCredential]:
    stmt = select(UserProviderCredential).where(
        UserProviderCredential.user_id == user_id,
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


def _provider_source(provider: str, cred: UserProviderCredential | None) -> str:
    """该厂商的 Key 来源：user（用户在设置页配置）/ global（平台内置）/ ""（不可用）。"""
    if cred is not None and cred.is_active:
        return "user"
    if get_global_fallback_key(provider):
        return "global"
    return ""


def _audit_context(request: Request) -> tuple[str, str]:
    """返回 (客户端 IP, User-Agent)，供审计记录使用。"""
    return client_ip(request), request.headers.get("user-agent", "")


def _enforce_rate_limit(scope: str, user_id: str, provider: str, limit: int) -> None:
    """按 user+provider+动作做滑动窗口限流，超限抛 429。"""
    try:
        check_rate_limit(
            f"byok-{scope}:{user_id}:{provider}",
            limit=limit,
            window_seconds=60,
        )
    except RateLimitExceeded as exc:
        raise HTTPException(
            status_code=429,
            detail=str(exc),
            headers={"Retry-After": str(exc.retry_after)},
        ) from exc


def _safe_error(exc: Exception) -> str:
    """把第三方返回的错误脱敏后再回传给前端，避免带出 Key/内部地址。"""
    safe, _ = redact(str(exc))
    return safe


def _validate_base_url(base_url: str) -> None:
    """自定义 base_url 的 SSRF 校验；为空表示使用默认地址，直接放行。"""
    if not base_url:
        return
    try:
        validate_outbound_url(
            base_url,
            allow_private=get_settings().byok_allow_private_base_url,
        )
    except UnsafeUrlError as exc:
        raise HTTPException(status_code=400, detail=f"自定义地址不安全: {exc}") from exc


# ===== Routes =====


@router.get("/providers")
async def list_providers(
    user_id: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> StandardResponse[list[ProviderInfo]]:
    credentials = await _get_all_user_credentials(db, user_id)
    cred_map: dict[str, UserProviderCredential] = {c.provider: c for c in credentials}

    providers = get_all_providers()
    data = [
        ProviderInfo(
            provider=p.provider,
            provider_name=p.provider_name,
            icon=p.icon,
            type=p.provider_type.value,
            default_base_url=p.default_base_url,
            allow_custom_base_url=p.allow_custom_base_url,
            allow_custom_models=p.allow_custom_models,
            docs_url=p.docs_url,
            key_placeholder=p.key_placeholder,
            has_api_key=_provider_source(p.provider, cred_map.get(p.provider)) != "",
            source=_provider_source(p.provider, cred_map.get(p.provider)),
        )
        for p in providers.values()
    ]
    return StandardResponse(data=data)


@router.get("/available")
async def list_available_models(
    user_id: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> StandardResponse[list[AvailableModel]]:
    credentials = await _get_all_user_credentials(db, user_id)
    cred_map: dict[str, UserProviderCredential] = {c.provider: c for c in credentials}

    models: list[AvailableModel] = []
    for prov in get_all_providers().values():
        cred = cred_map.get(prov.provider)
        has_user_key = cred is not None and cred.is_active
        has_global_key = bool(get_global_fallback_key(prov.provider))
        has_api_key = has_user_key or has_global_key

        for m in prov.default_models:
            source = "user" if has_user_key else ("global" if has_global_key else "")
            models.append(
                AvailableModel(
                    model_id=m.model_id,
                    api_model=m.api_model,
                    display_name=m.display_name,
                    description=m.description,
                    capabilities=list(m.capabilities),
                    max_tokens=m.max_tokens,
                    provider=prov.provider,
                    has_api_key=has_api_key,
                    is_active=cred.is_active if cred else True,
                    source=source,
                )
            )

        if cred and cred.custom_models:
            try:
                custom_model_names = json.loads(cred.custom_models)
                for cm_name in custom_model_names:
                    existing_ids = {m.model_id for m in prov.default_models}
                    if cm_name not in existing_ids:
                        models.append(
                            AvailableModel(
                                model_id=cm_name,
                                api_model=cm_name,
                                display_name=cm_name,
                                description="自定义模型",
                                capabilities=["text", "chat"],
                                max_tokens=4096,
                                provider=prov.provider,
                                has_api_key=True,
                                is_active=cred.is_active,
                                source="user",
                            )
                        )
            except (json.JSONDecodeError, TypeError):
                pass

    return StandardResponse(data=models)


@router.get("/credentials")
async def list_credentials(
    user_id: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> StandardResponse[list[CredentialStatus]]:
    credentials = await _get_all_user_credentials(db, user_id)
    providers = get_all_providers()

    data: list[CredentialStatus] = []
    for c in credentials:
        prov = providers.get(c.provider)
        custom_models: list[str] = []
        try:
            custom_models = json.loads(c.custom_models)
        except (json.JSONDecodeError, TypeError):
            pass
        data.append(
            CredentialStatus(
                provider=c.provider,
                provider_name=prov.provider_name if prov else c.provider,
                icon=prov.icon if prov else "",
                has_api_key=True,
                custom_base_url=c.custom_base_url,
                custom_models=custom_models,
                is_active=c.is_active,
            )
        )
    return StandardResponse(data=data)


@router.put("/credentials/{provider}")
async def save_credential(
    provider: str,
    payload: SaveCredentialRequest,
    http_request: Request,
    user_id: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> StandardResponse[dict]:
    prov_def = get_provider(provider)
    if not prov_def:
        raise HTTPException(status_code=400, detail=f"不支持的 Provider: {provider}")

    settings = get_settings()
    ip, ua = _audit_context(http_request)

    # 1) 限流：防止把后端当成打第三方 API 的跳板
    try:
        _enforce_rate_limit("save", user_id, provider, settings.byok_save_rate_limit)
    except HTTPException:
        await record_audit(
            db,
            user_id=user_id,
            provider=provider,
            action=ACTION_SAVE,
            result=RESULT_DENIED,
            ip=ip,
            user_agent=ua,
            detail="触发保存限流",
        )
        await db.commit()
        raise

    # 2) SSRF：自定义 base_url 必须是可公开出站的 http(s) 地址
    custom_base_url = (payload.custom_base_url or "").strip()
    try:
        _validate_base_url(custom_base_url)
    except HTTPException as exc:
        await record_audit(
            db,
            user_id=user_id,
            provider=provider,
            action=ACTION_SAVE,
            result=RESULT_DENIED,
            ip=ip,
            user_agent=ua,
            detail=str(exc.detail),
        )
        await db.commit()
        raise

    # 3) 加密：AAD 绑定 user_id+provider，密文跨用户搬运会解密失败
    crypto = _get_crypto()
    encrypted_key = crypto.encrypt(payload.api_key, aad=build_credential_aad(user_id, provider))

    existing = await _get_user_credential(db, user_id, provider)
    if existing:
        existing.encrypted_api_key = encrypted_key
        existing.custom_base_url = custom_base_url
        existing.custom_models = json.dumps(payload.custom_models, ensure_ascii=False)
        existing.is_active = payload.is_active
    else:
        from app.db.models import generate_ulid

        cred = UserProviderCredential(
            id=generate_ulid(),
            user_id=user_id,
            provider=provider,
            encrypted_api_key=encrypted_key,
            custom_base_url=custom_base_url,
            custom_models=json.dumps(payload.custom_models, ensure_ascii=False),
            is_active=payload.is_active,
        )
        db.add(cred)

    # 4) 审计：只记录动作与结果，不写 Key
    await record_audit(
        db,
        user_id=user_id,
        provider=provider,
        action=ACTION_SAVE,
        result=RESULT_OK,
        ip=ip,
        user_agent=ua,
        detail="custom_base_url" if custom_base_url else "",
    )

    await db.commit()
    logger.info("[BYOK] Saved credential for user=%s provider=%s", user_id, provider)
    return StandardResponse(data={"provider": provider}, message="凭据已保存")


@router.delete("/credentials/{provider}")
async def delete_credential(
    provider: str,
    http_request: Request,
    user_id: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> StandardResponse[dict]:
    existing = await _get_user_credential(db, user_id, provider)
    if not existing:
        raise HTTPException(status_code=404, detail=f"未找到 {provider} 的凭据")
    await db.delete(existing)
    ip, ua = _audit_context(http_request)
    await record_audit(
        db,
        user_id=user_id,
        provider=provider,
        action=ACTION_DELETE,
        result=RESULT_OK,
        ip=ip,
        user_agent=ua,
    )
    await db.commit()
    logger.info("[BYOK] Deleted credential for user=%s provider=%s", user_id, provider)
    return StandardResponse(data={"provider": provider}, message="凭据已删除")


@router.post("/credentials/{provider}/verify")
async def verify_credential(
    provider: str,
    http_request: Request,
    user_id: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> StandardResponse[VerifyResult]:
    prov_def = get_provider(provider)
    if not prov_def:
        raise HTTPException(status_code=400, detail=f"不支持的 Provider: {provider}")

    settings = get_settings()
    ip, ua = _audit_context(http_request)

    existing = await _get_user_credential(db, user_id, provider)
    if not existing:
        return StandardResponse(data=VerifyResult(valid=False, error="未配置 API Key"))

    # 1) 限流：验证会真实请求第三方，避免被刷
    try:
        _enforce_rate_limit("verify", user_id, provider, settings.byok_verify_rate_limit)
    except HTTPException:
        await record_audit(
            db,
            user_id=user_id,
            provider=provider,
            action=ACTION_VERIFY,
            result=RESULT_DENIED,
            ip=ip,
            user_agent=ua,
            detail="触发验证限流",
        )
        await db.commit()
        raise

    # 2) 解密：兼容历史无 AAD 密文；跨用户搬运的密文会解密失败
    crypto = _get_crypto()
    try:
        api_key = crypto.decrypt_with_fallback(
            existing.encrypted_api_key,
            aad=build_credential_aad(user_id, provider),
        )
    except Exception as exc:
        safe_error = _safe_error(exc)
        await record_audit(
            db,
            user_id=user_id,
            provider=provider,
            action=ACTION_VERIFY,
            result=RESULT_FAIL,
            ip=ip,
            user_agent=ua,
            detail=safe_error,
        )
        await db.commit()
        return StandardResponse(data=VerifyResult(valid=False, error=f"Key 解密失败: {safe_error}"))

    # 3) 即便历史数据存了不安全地址，也在出站前再拦一次
    try:
        _validate_base_url(existing.custom_base_url)
    except HTTPException as exc:
        await record_audit(
            db,
            user_id=user_id,
            provider=provider,
            action=ACTION_VERIFY,
            result=RESULT_DENIED,
            ip=ip,
            user_agent=ua,
            detail=str(exc.detail),
        )
        await db.commit()
        return StandardResponse(data=VerifyResult(valid=False, error=str(exc.detail)))

    base_url = existing.custom_base_url or prov_def.default_base_url

    if prov_def.provider_type == ProviderType.ANTHROPIC:
        await record_audit(
            db,
            user_id=user_id,
            provider=provider,
            action=ACTION_VERIFY,
            result=RESULT_OK,
            ip=ip,
            user_agent=ua,
            detail="anthropic skip",
        )
        await db.commit()
        return StandardResponse(
            data=VerifyResult(valid=True, models_count=0),
            message="Anthropic SDK 暂不支持远程模型列表验证，Key 已保存",
        )

    try:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        models_resp = await client.models.list()
        models_count = len(models_resp.data) if models_resp.data else 0
        await record_audit(
            db,
            user_id=user_id,
            provider=provider,
            action=ACTION_VERIFY,
            result=RESULT_OK,
            ip=ip,
            user_agent=ua,
            detail=f"models={models_count}",
        )
        await db.commit()
        return StandardResponse(data=VerifyResult(valid=True, models_count=models_count))
    except Exception as e:
        safe_error = _safe_error(e)
        logger.warning(
            "[BYOK] Verify failed for user=%s provider=%s: %s", user_id, provider, safe_error
        )
        await record_audit(
            db,
            user_id=user_id,
            provider=provider,
            action=ACTION_VERIFY,
            result=RESULT_FAIL,
            ip=ip,
            user_agent=ua,
            detail=safe_error,
        )
        await db.commit()
        return StandardResponse(data=VerifyResult(valid=False, error=safe_error))


# ===== 模型分配（智能体 / 技能统一在这里配置） =====


@router.get("/assignments")
async def list_model_assignments(
    user_id: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> StandardResponse[AssignmentList]:
    """列出可配置模型的目标（智能体 + Prompt 型技能）及其当前分配。"""
    from app.services.model_assignment import (
        AGENT_TARGET,
        DEFAULT_TARGET,
        SKILL_TARGET,
        list_assignments,
    )

    assignments = await list_assignments(db, user_id)

    targets: list[AssignmentTarget] = []

    try:
        from app.agents.registry import get_agent_registry

        for agent in get_agent_registry().list_agents():
            targets.append(
                AssignmentTarget(
                    target_type=AGENT_TARGET,
                    target_key=agent.agent_id,
                    label=agent.role or agent.agent_id,
                    subtitle=agent.agent_id,
                    default_model=agent.llm_model or "",
                    assigned_model=assignments.get((AGENT_TARGET, agent.agent_id), ""),
                )
            )
    except Exception as e:
        logger.warning("[assignments] list agents failed: %s", e)

    try:
        from app.core.skill_registry import SkillRegistry, ensure_builtin_skills_registered

        ensure_builtin_skills_registered()
        registry = SkillRegistry.instance()
        for node_type, skills in registry.list_all(user_id).items():
            for meta in skills:
                if not meta.get("is_prompt_skill"):
                    continue
                key = f"{node_type}/{meta.get('name', '')}"
                targets.append(
                    AssignmentTarget(
                        target_type=SKILL_TARGET,
                        target_key=key,
                        label=meta.get("display_name") or meta.get("name", ""),
                        subtitle=node_type,
                        default_model="",
                        assigned_model=assignments.get((SKILL_TARGET, key), ""),
                    )
                )
    except Exception as e:
        logger.warning("[assignments] list skills failed: %s", e)

    return StandardResponse(
        data=AssignmentList(
            default_model=assignments.get((DEFAULT_TARGET, ""), ""),
            targets=targets,
        )
    )


@router.put("/assignments")
async def save_model_assignment(
    request: SaveAssignmentRequest,
    user_id: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> StandardResponse[dict]:
    """保存某个目标的模型选择；model 为空表示恢复「跟随默认」。"""
    from app.services.model_assignment import (
        AGENT_TARGET,
        DEFAULT_TARGET,
        SKILL_TARGET,
        save_assignment,
    )

    if request.target_type not in (DEFAULT_TARGET, AGENT_TARGET, SKILL_TARGET):
        raise HTTPException(status_code=400, detail=f"不支持的目标类型: {request.target_type}")

    if request.target_type != DEFAULT_TARGET and not request.target_key:
        raise HTTPException(status_code=400, detail="agent / skill 类型必须提供 target_key")

    await save_assignment(
        db,
        user_id,
        request.target_type,
        "" if request.target_type == DEFAULT_TARGET else request.target_key,
        request.model.strip(),
    )
    return StandardResponse(
        data={"target_type": request.target_type, "target_key": request.target_key},
        message="模型分配已保存",
    )
