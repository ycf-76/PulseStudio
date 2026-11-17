"""飞书云文档网页组件（Doc Component SDK）鉴权 API。

前端在右侧栏用飞书官方「云文档组件」渲染飞书文档时，
组件要求每次渲染前用 jsapi_ticket 对页面 URL 做 sha1 签名。
签名有效期仅 10 分钟且单次有效，因此由本端点按需实时生成。

鉴权链路（官方：open.feishu.cn/document/uYjL24iN/uUDO3YjL1gzN24SN4cjN）：
  access_token(user 优先，app 降级)
    -> POST /open-apis/jssdk/ticket/get 获取 jsapi_ticket（7200s，本端缓存）
    -> sha1("jsapi_ticket=...&noncestr=...&timestamp=...&url=...")

路由前缀: /api/feishu/docs
"""

from __future__ import annotations

import hashlib
import logging
import re
import secrets
import time
from datetime import datetime
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.config import get_settings
from app.db.models import FeishuOAuthConnection
from app.db.session import get_db
from capabilities.messaging.adapters.feishu import FeishuAPIError, FeishuClient
from capabilities.messaging.services.feishu_oauth import get_valid_user_access_token

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/feishu/docs", tags=["Feishu Docs"])

FEISHU_TICKET_URL = "https://open.feishu.cn/open-apis/jssdk/ticket/get"
FEISHU_APP_TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/app_access_token/internal"

# ticket 官方有效期 7200s，提前 5 分钟刷新，避免边界过期
_TICKET_TTL_SECONDS = 7200 - 300
_ticket_cache: dict[str, tuple[str, float]] = {}  # key -> (ticket, expires_at_monotonic)


def _normalize_page_url(page_url: str) -> str:
    """官方要求签名 url 不含 # 和 ? 后面的参数。"""
    for sep in ("#", "?"):
        idx = page_url.find(sep)
        if idx != -1:
            page_url = page_url[:idx]
    return page_url.rstrip("/")


async def _get_ticket(bearer_token: str, cache_key: str) -> str:
    """用 access_token 换 jsapi_ticket，带进程内缓存。"""
    cached = _ticket_cache.get(cache_key)
    if cached and cached[1] > time.monotonic():
        return cached[0]

    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            FEISHU_TICKET_URL,
            headers={"Authorization": f"Bearer {bearer_token}"},
        )
    data = resp.json()
    if resp.status_code >= 400 or data.get("code", 0) not in (0, None):
        raise FeishuAPIError(
            code=int(data.get("code", resp.status_code)),
            msg=str(data.get("msg") or "获取飞书 jsapi_ticket 失败"),
            api_name="jssdk.ticket.get",
        )
    ticket = str((data.get("data") or {}).get("ticket") or "")
    if not ticket:
        raise FeishuAPIError(-1, "飞书 jsapi_ticket 返回为空", "jssdk.ticket.get")

    _ticket_cache[cache_key] = (ticket, time.monotonic() + _TICKET_TTL_SECONDS)
    return ticket


async def _get_app_access_token() -> str:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            FEISHU_APP_TOKEN_URL,
            json={"app_id": settings.feishu_app_id, "app_secret": settings.feishu_app_secret},
        )
    data = resp.json()
    token = str(data.get("app_access_token") or "")
    if resp.status_code >= 400 or not token:
        raise FeishuAPIError(
            code=int(data.get("code", resp.status_code)),
            msg=str(data.get("msg") or "获取飞书 app_access_token 失败"),
            api_name="auth.v3.app_access_token.internal",
        )
    return token


@router.get("/sdk-signature")
async def sdk_signature(
    page_url: str = Query(..., description="调用组件的页面 URL（不含 #/? 后参数由服务端裁剪）"),
    user_id: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """生成云文档组件 SDK 鉴权参数。

    优先用户身份（已绑定飞书 OAuth，可编辑）；未绑定时降级应用身份
    （官方限制：100 次/分钟，组件不支持编辑/评论/点赞）。
    """
    settings = get_settings()
    if not settings.feishu_app_id or not settings.feishu_app_secret:
        raise HTTPException(status_code=503, detail="尚未配置 FEISHU_APP_ID / FEISHU_APP_SECRET")

    url = _normalize_page_url(page_url.strip())
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=422, detail="page_url 必须是合法的 http(s) URL")

    identity = "app"
    open_id: str | None = None
    try:
        user_token = await get_valid_user_access_token(db, user_id)
    except FeishuAPIError as exc:
        logger.warning(f"[FeishuDocs] user token refresh failed, fallback to app identity: {exc}")
        user_token = None

    if user_token:
        result = await db.execute(
            select(FeishuOAuthConnection.open_id).where(FeishuOAuthConnection.user_id == user_id)
        )
        open_id = result.scalar_one_or_none()
        bearer = user_token
        cache_key = f"user:{user_id}"
        identity = "user"
    else:
        bearer = await _get_app_access_token()
        cache_key = f"app:{settings.feishu_app_id}"

    ticket = await _get_ticket(bearer, cache_key)

    timestamp = int(time.time() * 1000)
    nonce_str = secrets.token_urlsafe(16)
    string1 = f"jsapi_ticket={ticket}&noncestr={nonce_str}&timestamp={timestamp}&url={url}"
    signature = hashlib.sha1(string1.encode("utf-8")).hexdigest()

    return {
        "success": True,
        "data": {
            "appId": settings.feishu_app_id,
            "signature": signature,
            "timestamp": timestamp,
            "nonceStr": nonce_str,
            "url": url,
            "openId": open_id,
            "identity": identity,
        },
    }


# ==================== 多维表格数据 ====================
# 官方 DocComponentSdk 不支持 /base/ 链接（错误码 -100），
# 因此由后端经 OpenAPI 拉取字段+记录，前端自渲染表格视图。

_BITABLE_URL_RE = re.compile(
    r"https://[a-z0-9.-]*(?:feishu\.cn|larksuite\.com)/base/([A-Za-z0-9]+)", re.IGNORECASE
)

# 字段类型码（官方 bitable field type）
_FIELD_TYPE_DATE = 5
_FIELD_TYPE_DATE_RANGE = 1005


def _parse_bitable_url(url: str) -> tuple[str, str | None]:
    """从 base 链接提取 (app_token, table_id?)，链接形如
    https://xxx.feishu.cn/base/<app_token>?table=<tblXXX>&view=<vewXXX>"""
    match = _BITABLE_URL_RE.search(url.strip())
    if not match:
        raise HTTPException(status_code=422, detail="不是有效的飞书多维表格链接（/base/）")
    app_token = match.group(1)

    table_id: str | None = None
    query = url.split("?", 1)[1] if "?" in url else ""
    for pair in query.split("&"):
        if pair.startswith("table="):
            table_id = pair[6:].strip() or None
            break
    return app_token, table_id


def _cell_to_text(value: Any, field_type: int) -> str:
    """把飞书类型化单元格值扁平化为展示文本。

    常见形态：text 字段为 [{"text":..}]、人员为 [{"name":..}]、
    单选为 str、数字为 float、日期为毫秒时间戳、复选框为 bool、
    超链接为 {"text":..,"link":..}。
    """
    if value is None or value == "":
        return ""
    if isinstance(value, bool):
        return "是" if value else "否"
    if field_type in (_FIELD_TYPE_DATE, _FIELD_TYPE_DATE_RANGE) and isinstance(value, (int, float)):
        if not value:
            return ""
        try:
            return datetime.fromtimestamp(value / 1000).strftime("%Y-%m-%d %H:%M")
        except (ValueError, OSError, OverflowError):
            return str(value)
    if isinstance(value, (int, float)):
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value)
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return ", ".join(t for t in (_cell_to_text(v, field_type) for v in value) if t)
    if isinstance(value, dict):
        text = value.get("text")
        if isinstance(text, str):
            link = value.get("link")
            return f"{text}（{link}）" if link else text
        if isinstance(text, list):
            return _cell_to_text(text, field_type)
        if "name" in value:
            return str(value["name"])
        return _cell_to_text(value.get("value") or value.get("en_name") or "", field_type)
    return str(value)


@router.get("/bitable-data")
async def bitable_data(
    url: str = Query(..., description="飞书多维表格链接（/base/<app_token>?table=<table_id>）"),
    user_id: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """拉取多维表格字段与记录（扁平化后返回，供前端自渲染表格）。

    鉴权与 sdk-signature 一致：用户 OAuth token 优先，未绑定时降级应用身份。
    """
    settings = get_settings()
    if not settings.feishu_app_id or not settings.feishu_app_secret:
        raise HTTPException(status_code=503, detail="尚未配置 FEISHU_APP_ID / FEISHU_APP_SECRET")

    app_token, table_id = _parse_bitable_url(url)

    try:
        user_token = await get_valid_user_access_token(db, user_id)
    except FeishuAPIError as exc:
        logger.warning(f"[FeishuDocs] user token refresh failed, fallback to app identity: {exc}")
        user_token = None

    client = FeishuClient(app_id=settings.feishu_app_id, app_secret=settings.feishu_app_secret)
    bearer = user_token  # None 时适配器自动用应用身份

    try:
        if table_id:
            tables = [{"table_id": table_id, "name": ""}]
        else:
            tables = await client.list_bitable_tables(app_token, access_token=bearer)
            if not tables:
                raise HTTPException(status_code=404, detail="该多维表格中没有任何数据表")
        table = tables[0]

        fields = await client.list_bitable_fields(app_token, table["table_id"], access_token=bearer)
        raw_records = await client.search_bitable_records(
            app_token, table["table_id"], access_token=bearer
        )
    except FeishuAPIError as exc:
        logger.error(f"[FeishuDocs] bitable fetch failed: {exc}")
        hint = (
            "请确认你对该多维表格有阅读权限，或应用已开通 bitable 相关权限"
            if exc.code in (91402, 91403, 1254302, 1254045)
            else exc.msg
        )
        raise HTTPException(status_code=502, detail=f"读取多维表格失败：{hint}")

    field_meta = [
        {
            "fieldId": f.get("field_id") or "",
            "fieldName": str(f.get("field_name") or ""),
            "type": int(f.get("type") or 0),
            "uiType": f.get("ui_type") or "",
            "isPrimary": bool(f.get("is_primary")),
        }
        for f in fields
    ]
    type_by_name = {f["fieldName"]: f["type"] for f in field_meta}

    records = []
    for r in raw_records:
        flat: dict[str, str] = {}
        for name, raw_value in (r.get("fields") or {}).items():
            text = _cell_to_text(raw_value, type_by_name.get(name, 0))
            if text:
                flat[name] = text
        records.append(flat)

    return {
        "success": True,
        "data": {
            "appToken": app_token,
            "tableId": table["table_id"],
            "tableName": table.get("name") or "",
            "fields": field_meta,
            "records": records,
            "truncated": len(records) >= 2000,
        },
    }
