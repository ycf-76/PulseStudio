"""Provider 凭据解析（BYOK）——技能 / 适配器工厂的统一入口。

对话链路（api/routers/chat.py）自行解析凭据；媒体类能力（生图 / 生视频）
由本模块解析，保证「设置页配置的用户 Key」在脚本层同样生效。

解析顺序：
  1. 用户凭据（user_provider_credentials 表，加密存储）
  2. 平台级 Key（backend/.env，见 provider_registry.get_global_fallback_key）
"""

from __future__ import annotations

import logging

from sqlalchemy import select

from app.services.provider_registry import get_global_fallback_key

logger = logging.getLogger(__name__)

# 媒体 provider 名 → 凭据 provider（后者为历史别名，用户可能只在「通义」通道配过 Key）
_MEDIA_PROVIDER_ALIASES: dict[str, tuple[str, ...]] = {
    "ark": ("ark",),
    "volcengine": ("ark",),
    "dashscope": ("wanx", "qwen"),
    "wanx": ("wanx", "qwen"),
}


async def get_user_provider_key(user_id: str | None, provider: str) -> str:
    """读取并解密某用户在某 Provider 下配置的 Key；未配置返回空串。"""
    if not user_id or user_id in ("anonymous", "default", "system"):
        return ""
    try:
        from app.crypto.token_crypto import TokenCrypto, build_credential_aad
        from app.db.models import UserProviderCredential
        from app.db.session import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            stmt = select(UserProviderCredential).where(
                UserProviderCredential.user_id == user_id,
                UserProviderCredential.provider == provider,
                UserProviderCredential.is_active.is_(True),
            )
            result = await db.execute(stmt)
            cred = result.scalar_one_or_none()
            if cred is None:
                return ""
            return TokenCrypto().decrypt_with_fallback(
                cred.encrypted_api_key,
                aad=build_credential_aad(user_id, provider),
            )
    except Exception as e:
        logger.debug(
            "[provider_credentials] 读取用户凭据失败 user=%s provider=%s: %s",
            user_id,
            provider,
            e,
        )
        return ""


async def resolve_provider_key(
    provider: str,
    user_id: str | None = None,
    *,
    fallback_providers: tuple[str, ...] = (),
) -> str:
    """解析某 Provider 的可用 Key：用户凭据优先，平台级 Key 兜底。"""
    for candidate in (provider, *fallback_providers):
        key = await get_user_provider_key(user_id, candidate)
        if key:
            return key
    for candidate in (provider, *fallback_providers):
        key = get_global_fallback_key(candidate)
        if key:
            return key
    return ""


async def resolve_media_provider_key(provider: str, user_id: str | None = None) -> str:
    """媒体生成 provider（dashscope / wanx / ark）→ 可用 Key。"""
    candidates = _MEDIA_PROVIDER_ALIASES.get(provider, (provider,))
    return await resolve_provider_key(candidates[0], user_id, fallback_providers=candidates[1:])