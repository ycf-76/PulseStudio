"""卡片渲染 HTTP 服务：把 app.cards DSL 暴露给前端图文编辑壳。

设计原则：
- 模型永远不写 markup（见 app/cards/schema.py 顶部注释）。本路由只是把
  已有的 renderer / composer / audit 包成 HTTP，让前端（独立进程）能消费。
- preview 端点两条路径：①前端回传完整 deck（编辑后）→ 直接 to_card_draft；
  ②topic+content 退化生成（compose_deck(llm=None)，不发起任何真机 LLM 请求）。
"""

from __future__ import annotations

import logging
import typing
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import get_current_user
from app.api.schemas.common import StandardResponse
from app.cards.operations import CardEdit, apply_card_edit, read_card
from app.cards.renderer import to_card_draft
from app.cards.schema import CardDeck, ThemeId

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/cards", tags=["cards"])


class PreviewRequest(BaseModel):
    """预览请求：deck 与 (topic+content) 二选一。"""

    deck: Optional[CardDeck] = None
    topic: Optional[str] = Field(default=None, max_length=80)
    content: Optional[str] = Field(default=None, max_length=6000)
    theme_id: ThemeId = "inkwash"
    image_layout: Literal["top", "side", "overlay"] = "top"


class CardEditRequest(CardEdit):
    """编辑请求：在块级指令之上带乐观锁版本，服务端据此拒绝过期写入。"""

    expected_version: int = Field(ge=1)


def _edit_error(exc: Exception) -> HTTPException:
    """把编辑链路的领域异常映射成 HTTP 状态码。

    404 作品不存在 / 403 越权 / 409 版本过期（前端应刷新后重试）/ 400 指令非法。
    """
    if isinstance(exc, LookupError):
        return HTTPException(status_code=404, detail=str(exc) or "创作作品不存在")
    if isinstance(exc, PermissionError):
        return HTTPException(status_code=403, detail=str(exc) or "无权访问该作品")
    if isinstance(exc, RuntimeError):
        return HTTPException(status_code=409, detail=str(exc) or "卡片版本已更新，请刷新后重试")
    return HTTPException(status_code=400, detail=str(exc) or "编辑指令不合法")


def _block_type_names() -> list[str]:
    """从 Block 联合类型里抽取所有 type 字面量，给前端做「组件清单」。"""
    try:
        union = typing.get_args(CardDeck.__fields__["pages"].annotation)  # noqa: 兜底
    except Exception:
        union = None
    # 直接从 Block 判别联合里取类型名，避免对 CardDeck 内部结构的假设
    try:
        from app.cards.schema import Block

        args = typing.get_args(Block)
        # Block 是 Annotated[Union[...], Field(...)]，get_args 返回 (Union, FieldInfo)
        union_type = next((a for a in args if getattr(a, "__origin__", None) is typing.Union), None)
        if union_type is not None:
            return sorted(
                {t.model_fields["type"].default for t in typing.get_args(union_type)}
            )
    except Exception as exc:  # pragma: no cover - 反射仅用于元数据
        logger.warning("提取 block 类型名失败: %s", exc)
    return []


@router.post("/preview")
async def preview(
    req: PreviewRequest, user_id: str = Depends(get_current_user)
) -> StandardResponse[dict]:
    if req.deck is not None:
        draft = to_card_draft(req.deck)
        return StandardResponse(data=draft)

    if req.topic and req.content:
        from app.cards.composer import compose_deck

        try:
            # llm=None：走退化路径（按行拆分），不发起任何真机 LLM 请求。
            draft = await compose_deck(
                topic=req.topic,
                content=req.content,
                user_id=user_id,
                llm=None,
                theme_id=req.theme_id,
                image_layout=req.image_layout,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return StandardResponse(data=draft)

    raise HTTPException(status_code=400, detail="preview 需要 deck 或 (topic+content)")


@router.post("/audit")
async def audit_endpoint(
    req: PreviewRequest, user_id: str = Depends(get_current_user)
) -> StandardResponse[dict]:
    if req.deck is None:
        raise HTTPException(status_code=400, detail="audit 需要 deck")
    from app.cards import audit as card_audit

    reports = card_audit.audit_deck(req.deck)
    return StandardResponse(
        data={
            "reports": reports,
            "min_fill": card_audit.deck_density(req.deck)["min_fill"],
        }
    )


@router.get("/meta")
async def meta() -> StandardResponse[dict]:
    """返回可用版式 / 主题 / 可选 Block，供前端放开组合（根治固定死）。"""
    from app.cards.schema import LAYOUTS, THEMES, optional_blocks

    return StandardResponse(
        data={
            "layouts": {lid: list(required) for lid, required in LAYOUTS.items()},
            "themes": {
                name: {"label": spec.get("label", name), "hint": spec.get("hint", "")}
                for name, spec in THEMES.items()
            },
            "block_types": _block_type_names(),
            "optional_blocks": {
                lid: sorted(optional_blocks(lid)) for lid in LAYOUTS
            },
        }
    )


@router.get("/{artifact_id}")
async def get_card(
    artifact_id: str,
    user_id: str = Depends(get_current_user),
) -> StandardResponse[dict]:
    """读取作品当前的结构化卡片（deck + 版本号），供详情页编辑区装载。"""
    try:
        data = await read_card(artifact_id, user_id)
    except Exception as exc:  # noqa: BLE001 - 统一走状态码映射
        raise _edit_error(exc) from exc
    return StandardResponse(data=data)


@router.post("/{artifact_id}/edit")
async def edit_card(
    artifact_id: str,
    req: CardEditRequest,
    user_id: str = Depends(get_current_user),
) -> StandardResponse[dict]:
    """执行一次块级编辑并落盘，返回新版本 draft（含重渲染后的 HTML）。"""
    edit = CardEdit.model_validate(req.model_dump(exclude={"expected_version"}))
    try:
        data = await apply_card_edit(artifact_id, user_id, req.expected_version, edit)
    except Exception as exc:  # noqa: BLE001 - 统一走状态码映射
        raise _edit_error(exc) from exc
    return StandardResponse(data=data)