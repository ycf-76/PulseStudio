"""User video asset API."""

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile

from app.api.deps import get_current_user
from app.api.schemas.common import StandardResponse
from app.services.asset_library import delete_video_asset, list_video_assets, save_video_asset

MAX_VIDEO_UPLOAD_BYTES = 500 * 1024 * 1024

router = APIRouter(prefix="/api/video-assets", tags=["video-assets"])


@router.post("")
async def upload_video_assets(
    files: list[UploadFile] = File(...),
    user_id: str = Depends(get_current_user),
) -> StandardResponse[list[dict]]:
    if not files:
        raise HTTPException(status_code=400, detail="请至少上传一个视频")
    if len(files) > 5:
        raise HTTPException(status_code=400, detail="单次最多上传 5 个视频")

    saved: list[dict] = []
    for file in files:
        data = await file.read()
        if len(data) > MAX_VIDEO_UPLOAD_BYTES:
            raise HTTPException(status_code=400, detail=f"{file.filename} 不能超过 500MB")
        try:
            asset = await save_video_asset(
                user_id=user_id,
                filename=file.filename or "",
                content_type=file.content_type or "video/mp4",
                data=data,
                source="upload",
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        saved.append(asset)

    return StandardResponse(data=saved, message=f"已上传 {len(saved)} 个视频")


@router.get("")
async def get_video_assets(
    user_id: str = Depends(get_current_user),
    limit: int | None = Query(default=None, ge=1, le=1000),
) -> StandardResponse[list[dict]]:
    return StandardResponse(data=await list_video_assets(user_id, limit=limit))


@router.delete("/{asset_id}")
async def remove_video_asset(
    asset_id: str,
    user_id: str = Depends(get_current_user),
) -> StandardResponse[dict]:
    deleted = await delete_video_asset(user_id, asset_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="视频资产不存在")
    return StandardResponse(data={"deleted": asset_id})
