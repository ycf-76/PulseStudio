"""ChatFolder 持久化服务（素材区文件夹 / 对话分组）。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import desc, select, update

from app.db.models import ChatFile, ChatFolder, ChatSession, User
from app.db.session import AsyncSessionLocal


def _folder_to_dict(folder: ChatFolder) -> dict[str, Any]:
    return {
        "id": folder.id,
        "name": folder.name,
        "path": folder.path or "",
        "position": folder.position,
        "created_at": folder.created_at.isoformat() if folder.created_at else None,
        "updated_at": folder.updated_at.isoformat() if folder.updated_at else None,
    }


async def list_folders(user_id: str, limit: int = 200) -> list[dict[str, Any]]:
    async with AsyncSessionLocal() as db:
        stmt = (
            select(ChatFolder)
            .where(ChatFolder.user_id == user_id)
            .order_by(ChatFolder.position, desc(ChatFolder.created_at))
            .limit(limit)
        )
        result = await db.scalars(stmt)
        return [_folder_to_dict(f) for f in result.all()]


async def create_folder(user_id: str, name: str, path: str = "") -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        user = await db.get(User, user_id)
        if user is None:
            user = User(id=user_id, nickname=f"user_{user_id[:8]}")
            db.add(user)
            await db.flush()
        folder = ChatFolder(user_id=user_id, name=name or "新文件夹", path=path or "")
        db.add(folder)
        await db.commit()
        await db.refresh(folder)
        return _folder_to_dict(folder)


async def update_folder(
    folder_id: str,
    user_id: str,
    name: str | None = None,
    path: str | None = None,
) -> dict[str, Any] | None:
    async with AsyncSessionLocal() as db:
        folder = await db.get(ChatFolder, folder_id)
        if folder is None or folder.user_id != user_id:
            return None
        if name is not None and name.strip():
            folder.name = name.strip()
        if path is not None:
            folder.path = path
        await db.commit()
        await db.refresh(folder)
        return _folder_to_dict(folder)


async def delete_folder(folder_id: str, user_id: str) -> bool:
    """删除文件夹，并把其下会话/文件回落到未分组状态（不删数据）。"""
    async with AsyncSessionLocal() as db:
        folder = await db.get(ChatFolder, folder_id)
        if folder is None or folder.user_id != user_id:
            return False
        await db.execute(
            update(ChatSession)
            .where(ChatSession.user_id == user_id, ChatSession.folder_id == folder_id)
            .values(folder_id="")
        )
        await db.execute(
            update(ChatFile).where(ChatFile.folder_id == folder_id).values(folder_id="")
        )
        await db.delete(folder)
        await db.commit()
        return True
