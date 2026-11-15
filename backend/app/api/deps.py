"""FastAPI 公共依赖。

提供基于 JWT 的用户身份解析依赖 get_current_user，供所有需要登录的路由使用。

用法：
    from app.api.deps import get_current_user

    @router.get(...)
    async def handler(user_id: str = Depends(get_current_user)):
        ...
"""
from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import get_settings
from app.security import decode_user_id

# Bearer token scheme，auto_error=False 让我们在依赖里返回统一错误格式
_bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> str:
    """从 Authorization: Bearer <jwt> 解析当前用户 ID。

    仅接受 Authorization header。出于安全考虑（token 会落入浏览器历史、
    代理日志、Referer 头），不再支持通过 ?token= 查询参数传递 JWT。

    Returns:
        user_id（JWT sub claim）

    Raises:
        HTTPException 401: 未提供 token / scheme 非 Bearer / token 无效或过期
    """
    if credentials is None or (credentials.scheme or "").lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="缺少访问令牌",
            headers={"WWW-Authenticate": "Bearer"},
        )

    jwt_token = credentials.credentials
    if not jwt_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="缺少访问令牌",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        return decode_user_id(jwt_token)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="令牌无效或已过期",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


async def require_admin(user_id: str = Depends(get_current_user)) -> str:
    """管理后台专用依赖：在标准鉴权之上追加管理员身份校验。

    与 get_current_user 的关键差异：
      - 无 token 时同样返回 401，不存在任何"免登录兜底"
      - 通过 token 校验后，还必须命中管理员白名单，否则返回 403

    管理员来源为 settings.admin_user_ids（逗号分隔）。白名单为空时一律拒绝，
    确保管理后台默认关闭（fail-closed），不会因为漏配而对外裸奔。

    Returns:
        user_id（已确认是管理员）

    Raises:
        HTTPException 401: 未提供 token / token 无效或过期
        HTTPException 403: 已登录但不在管理员白名单（含白名单为空的情况）
    """
    admin_ids = set(get_settings().admin_user_id_list)
    if user_id not in admin_ids:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要管理员权限",
        )
    return user_id
