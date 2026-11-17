"""BYOK 凭据审计日志。

安全铁律：审计只记录「谁、何时、对哪个厂商的凭据、做了什么、结果如何」，
绝不写入 API Key 明文/密文，也不写入任何可逆推 Key 的信息。

审计写入失败不得影响主流程（只告警），因为审计是观测手段而非业务依赖。
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import CredentialAuditLog, generate_ulid

logger = logging.getLogger(__name__)

ACTION_SAVE = "save"
ACTION_DELETE = "delete"
ACTION_VERIFY = "verify"
ACTION_USE = "use"

RESULT_OK = "ok"
RESULT_FAIL = "fail"
RESULT_DENIED = "denied"


def _clean(text: str, limit: int) -> str:
    """审计字段一律截断 + 去换行，防止日志注入与超长内容。"""
    return (text or "").replace("\r", " ").replace("\n", " ").strip()[:limit]


def client_ip(request) -> str:
    """从 Request 取客户端 IP：优先 X-Forwarded-For 首个地址（反代场景），否则 socket 地址。"""
    if request is None:
        return ""
    forwarded = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    if forwarded:
        return forwarded
    if getattr(request, "client", None) is not None:
        return getattr(request.client, "host", "") or ""
    return ""


async def record_audit(
    db: AsyncSession,
    *,
    user_id: str,
    provider: str,
    action: str,
    result: str = RESULT_OK,
    ip: str = "",
    user_agent: str = "",
    detail: str = "",
) -> None:
    """在当前事务里追加一条审计记录（flush，不 commit，由调用方提交）。"""
    try:
        db.add(
            CredentialAuditLog(
                id=generate_ulid(),
                user_id=_clean(user_id, 128),
                provider=_clean(provider, 32),
                action=_clean(action, 32),
                result=_clean(result, 16),
                ip=_clean(ip, 64),
                user_agent=_clean(user_agent, 256),
                detail=_clean(detail, 512),
            )
        )
        await db.flush()
    except Exception:
        logger.warning(
            "[BYOK-AUDIT] 写入审计失败 user=%s provider=%s action=%s",
            user_id,
            provider,
            action,
            exc_info=True,
        )


async def record_use_audit(user_id: str, provider: str, detail: str = "") -> None:
    """独立会话记录一次「凭据被用于调用模型」。

    chat 解析链路自带独立 Session，这里再开一个短事务，失败静默不影响对话。
    """
    from app.db.session import AsyncSessionLocal

    try:
        async with AsyncSessionLocal() as db:
            await record_audit(
                db,
                user_id=user_id,
                provider=provider,
                action=ACTION_USE,
                detail=detail,
            )
            await db.commit()
    except Exception:
        logger.debug("[BYOK-AUDIT] 记录调用审计跳过 user=%s provider=%s", user_id, provider)
