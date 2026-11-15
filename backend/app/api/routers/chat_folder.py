"""Chat Folder API — 素材区文件夹（对话分组）持久化。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.deps import get_current_user
from app.api.schemas.common import StandardResponse
from app.services import chat_folder

router = APIRouter(prefix="/api/chat/folders", tags=["chat_folder"])


class CreateFolderRequest(BaseModel):
    name: str = "新文件夹"
    path: str = ""


class UpdateFolderRequest(BaseModel):
    name: str | None = None
    path: str | None = None


@router.get("")
async def list_folders(
    user_id: str = Depends(get_current_user),
) -> StandardResponse[list]:
    data = await chat_folder.list_folders(user_id)
    return StandardResponse(data=data)


@router.post("")
async def create_folder(
    payload: CreateFolderRequest,
    user_id: str = Depends(get_current_user),
) -> StandardResponse[dict]:
    data = await chat_folder.create_folder(user_id, payload.name, payload.path)
    return StandardResponse(data=data)


@router.patch("/{folder_id}")
async def update_folder(
    folder_id: str,
    payload: UpdateFolderRequest,
    user_id: str = Depends(get_current_user),
) -> StandardResponse[dict]:
    data = await chat_folder.update_folder(
        folder_id, user_id, name=payload.name, path=payload.path
    )
    if data is None:
        raise HTTPException(status_code=404, detail="Folder not found")
    return StandardResponse(data=data)


@router.delete("/{folder_id}")
async def delete_folder(
    folder_id: str,
    user_id: str = Depends(get_current_user),
) -> StandardResponse[bool]:
    ok = await chat_folder.delete_folder(folder_id, user_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Folder not found")
    return StandardResponse(data=True)