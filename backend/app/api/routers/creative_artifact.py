"""CreativeArtifact API：统一创作对象的读写与构建。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import get_current_user
from app.api.schemas.common import StandardResponse
from app.services.creative_artifact import (
    CreativeArtifact,
    build_artifact,
    build_storyboard,
    delete_artifact,
    list_artifacts_for_user,
    load_artifact,
    save_artifact,
    to_card_draft,
    verify_owner,
)

router = APIRouter(prefix="/api/creative-artifacts", tags=["creative-artifacts"])


def _forbidden(exc: PermissionError) -> HTTPException:
    return HTTPException(status_code=403, detail=str(exc) or "无权访问该作品")


async def _owned_artifact(artifact_id: str, user_id: str) -> CreativeArtifact:
    """读+鉴权合一：不存在 404，不属于当前用户 403。"""
    artifact = load_artifact(artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="artifact not found")
    try:
        await verify_owner(artifact, user_id)
    except PermissionError as exc:
        raise _forbidden(exc) from exc
    return artifact


class BuildRequest(BaseModel):
    artifact_id: str = ""
    card_draft: dict[str, Any] = Field(default_factory=dict)
    brief: dict[str, Any] = Field(default_factory=dict)
    analysis: dict[str, Any] = Field(default_factory=dict)
    source: dict[str, Any] = Field(default_factory=dict)
    persist: bool = True


class StoryboardRequest(BaseModel):
    card_draft: dict[str, Any] = Field(default_factory=dict)


@router.get("")
async def list_items(
    limit: int = 50,
    work_id: str = "",
    session_id: str = "",
    user_id: str = Depends(get_current_user),
) -> StandardResponse[list[dict[str, Any]]]:
    """只返回当前用户的产物；传 work_id / session_id 可进一步收窄到单个作品或会话。"""
    return StandardResponse(
        data=await list_artifacts_for_user(
            user_id, limit=limit, work_id=work_id, session_id=session_id
        )
    )


@router.post("/build")
async def build(
    payload: BuildRequest,
    user_id: str = Depends(get_current_user),
) -> StandardResponse[dict[str, Any]]:
    """从 card_draft / brief / analysis 构建统一创作对象。"""
    if payload.artifact_id:
        existing = load_artifact(payload.artifact_id)
        if existing is not None:
            try:
                await verify_owner(existing, user_id)
            except PermissionError as exc:
                raise _forbidden(exc) from exc
    artifact = build_artifact(
        artifact_id=payload.artifact_id,
        owner_user_id=user_id,
        card_draft=payload.card_draft,
        brief=payload.brief,
        analysis=payload.analysis,
        source=payload.source,
    )
    if payload.persist:
        save_artifact(artifact)
    return StandardResponse(data=artifact.model_dump())


@router.post("/storyboard")
async def storyboard(
    payload: StoryboardRequest,
    _user_id: str = Depends(get_current_user),
) -> StandardResponse[list[dict[str, Any]]]:
    """只做分镜提升，不持久化，便于前端预览。"""
    draft = payload.card_draft or {}
    pages = build_storyboard(
        draft.get("pages") or [],
        template_id=draft.get("suggested_template") or "",
    )
    return StandardResponse(data=[p.model_dump() for p in pages])


@router.get("/{artifact_id}")
async def get_item(
    artifact_id: str,
    user_id: str = Depends(get_current_user),
) -> StandardResponse[dict[str, Any]]:
    artifact = await _owned_artifact(artifact_id, user_id)
    return StandardResponse(data=artifact.model_dump())


@router.get("/{artifact_id}/card-draft")
async def get_card_draft(
    artifact_id: str,
    user_id: str = Depends(get_current_user),
) -> StandardResponse[dict[str, Any]]:
    artifact = await _owned_artifact(artifact_id, user_id)
    return StandardResponse(data=to_card_draft(artifact))


@router.put("/{artifact_id}")
async def put_item(
    artifact_id: str,
    artifact: CreativeArtifact,
    user_id: str = Depends(get_current_user),
) -> StandardResponse[dict[str, Any]]:
    artifact.artifact_id = artifact_id
    existing = load_artifact(artifact_id)
    if existing is not None:
        try:
            await verify_owner(existing, user_id)
        except PermissionError as exc:
            raise _forbidden(exc) from exc
    # 归属永远由服务端身份决定，不接受客户端自称
    artifact.owner_user_id = user_id
    saved = save_artifact(artifact)
    return StandardResponse(data=saved.model_dump())


@router.patch("/{artifact_id}")
async def patch_item(
    artifact_id: str,
    updates: dict[str, Any],
    user_id: str = Depends(get_current_user),
) -> StandardResponse[dict[str, Any]]:
    existing = load_artifact(artifact_id)
    if existing is not None:
        try:
            await verify_owner(existing, user_id)
        except PermissionError as exc:
            raise _forbidden(exc) from exc
    current = existing or CreativeArtifact(artifact_id=artifact_id)
    data = current.model_dump()
    for key, value in updates.items():
        if key in ("artifact_id", "version", "owner_user_id"):
            continue
        if isinstance(value, dict) and isinstance(data.get(key), dict):
            data[key].update(value)
        else:
            data[key] = value
    data["owner_user_id"] = user_id

    updated = CreativeArtifact(**data)
    saved = save_artifact(updated)
    return StandardResponse(data=saved.model_dump())


@router.delete("/{artifact_id}")
async def delete_item(
    artifact_id: str,
    user_id: str = Depends(get_current_user),
) -> StandardResponse[dict[str, Any]]:
    await _owned_artifact(artifact_id, user_id)
    ok = delete_artifact(artifact_id)
    if not ok:
        raise HTTPException(status_code=404, detail="artifact not found")
    return StandardResponse(data={"deleted": True})