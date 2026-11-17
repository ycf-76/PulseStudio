"""平台发布适配器 API —— 前端 adapters/*.ts 的发布出口。

对应调用方：
- POST /api/workflows/publish             小红书（frontend/src/adapters/xiaohongshu.ts）
- POST /api/workflows/publish/weibo       微博（frontend/src/adapters/weibo.ts）
- POST /api/workflows/publish/wechat_mp   微信公众号（frontend/src/adapters/wechat-mp.ts）

统一约定：
- 鉴权：必须带 JWT（发布是写操作，按用户维度隔离）。
- 总开关：受 settings.automation_enabled 管控，默认关闭；关闭时返回 403，
  因为自动发文存在平台风控风险。
- 返回体为扁平结构 {status, post_id, message, platform, url, screenshot_base64}，
  前端按 data.status === 'success' 判定成功。
- 半自动（默认 auto_submit=false）只填写不提交，status=awaiting_manual。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import get_current_user
from app.db.models import PlatformAccount
from app.db.session import AsyncSessionLocal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/workflows", tags=["workflows"])

_PLATFORM_LABELS = {
    "xiaohongshu": "小红书",
    "weibo": "微博",
    "wechat_mp": "微信公众号",
}


class PublishRequest(BaseModel):
    title: str = Field(default="", description="标题（公众号必填，小红书建议 ≤20 字）")
    content: str = Field(..., description="正文（含话题标签文本）")
    images_base64: list[str] = Field(
        default_factory=list, description="图片 base64 列表（不含 data: 前缀）"
    )
    cover_image_base64: str = Field(default="", description="封面图 base64（公众号用）")
    account_id: str | None = Field(
        default=None, description="平台账号 ID；不传则使用该平台默认登录态"
    )
    auto_submit: bool = Field(
        default=False, description="true=填写后自动点发布；false=半自动只填写"
    )


async def _resolve_account(
    account_id: str | None, platform: str, user_id: str
) -> PlatformAccount | None:
    """校验 account_id 归属与平台匹配，避免用别人的账号发布（IDOR）。"""
    if not account_id:
        return None
    async with AsyncSessionLocal() as db:
        acc = await db.scalar(select(PlatformAccount).where(PlatformAccount.id == account_id))
    if acc is None or acc.user_id != user_id:
        raise HTTPException(404, "账号不存在")
    if acc.platform != platform:
        raise HTTPException(400, f"账号平台不匹配：期望 {platform}，实际 {acc.platform}")
    return acc


async def _publish(platform: str, body: PublishRequest, user_id: str) -> dict[str, Any]:
    from app.config import get_settings

    if not get_settings().automation_enabled:
        raise HTTPException(
            403,
            "平台自动化已全局关闭（automation_enabled=false）。"
            "自动发文存在平台风控风险，如需人工操作请显式开启后再试。",
        )

    label = _PLATFORM_LABELS.get(platform, platform)
    await _resolve_account(body.account_id, platform, user_id)

    if not body.content.strip():
        raise HTTPException(400, "正文不能为空")

    images = list(body.images_base64)
    if body.cover_image_base64:
        images.insert(0, body.cover_image_base64)

    from capabilities.messaging.services.platform_publisher import publish_to_platform

    result = await publish_to_platform(
        platform=platform,
        title=body.title,
        content=body.content,
        tags=[],
        images_base64=images,
        auto_submit=body.auto_submit,
    )

    status = result.get("status", "failed")
    if status == "submitted":
        status = "success"

    logger.info(
        "[workflows:publish] platform=%s user=%s status=%s msg=%s",
        platform,
        user_id,
        status,
        result.get("message", "")[:80],
    )

    return {
        "status": status,
        "post_id": "",
        "message": result.get("message", "")
        or (f"{label}发布完成" if status == "success" else f"{label}发布失败"),
        "platform": platform,
        "url": result.get("url", ""),
        "screenshot_base64": result.get("screenshot_base64", ""),
    }


@router.post("/publish")
async def publish_xiaohongshu(
    body: PublishRequest,
    user_id: str = Depends(get_current_user),
) -> dict[str, Any]:
    """小红书图文发布：填写标题/正文/图片，可选自动提交。"""
    return await _publish("xiaohongshu", body, user_id)


@router.post("/publish/weibo")
async def publish_weibo(
    body: PublishRequest,
    user_id: str = Depends(get_current_user),
) -> dict[str, Any]:
    """微博发布：正文（≤140 字）+ 图片。"""
    return await _publish("weibo", body, user_id)


@router.post("/publish/wechat_mp")
async def publish_wechat_mp(
    body: PublishRequest,
    user_id: str = Depends(get_current_user),
) -> dict[str, Any]:
    """微信公众号图文发布：标题 + 正文 + 封面图。"""
    if not body.title.strip():
        raise HTTPException(400, "公众号发布必须提供标题")
    return await _publish("wechat_mp", body, user_id)
