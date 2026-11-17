"""离线环境隔离：假下载器 + 临时 uploads + 临时 sqlite。

同步链路有两个必须隔离的副作用，否则无法离线重放：
  1. 图片本地化会真的去请求小红书 CDN，并写到真实 uploads/ 目录
  2. 入库会连真实 MySQL

这里把它们替换成确定性假实现，让整条链路可重放、可断言、零网络、零真实账号。

用法（脚本）：
    with offline_image_store(tmp):
        engine, sf = await create_temp_db()
        with patch_session_local(sf):
            ...
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any, Iterator

# 1x1 透明 PNG，用作假下载结果
TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)

# 模拟"签名已过期"的 CDN 外链：假下载器对这类 URL 返回 None（等价于 403）
EXPIRED_MARKERS = ("expired", "403", "xhscdn.com/notes_pre_post")


def is_expired_url(url: str) -> bool:
    return any(marker in url for marker in EXPIRED_MARKERS)


async def fake_download_image(url: str, timeout: float = 30.0) -> tuple[bytes, str] | None:
    """假下载器：本地/正常外链返回 1x1 PNG，过期外链返回 None。"""
    if is_expired_url(url):
        return None
    return TINY_PNG, "image/png"


@contextlib.contextmanager
def offline_image_store(root: Path) -> Iterator[Path]:
    """把 image_store 指向临时目录，并换掉真实下载器。"""
    from app.services import image_store

    saved = {
        "download_image": image_store.download_image,
        "covers": image_store._TOPIC_COVERS_DIR,
        "images": image_store._TOPIC_IMAGES_DIR,
        "init": image_store._DIRS_INITIALIZED,
    }
    image_store.download_image = fake_download_image
    image_store._TOPIC_COVERS_DIR = root / "topic_covers"
    image_store._TOPIC_IMAGES_DIR = root / "topic_images"
    image_store._DIRS_INITIALIZED = False
    try:
        yield root
    finally:
        image_store.download_image = saved["download_image"]
        image_store._TOPIC_COVERS_DIR = saved["covers"]
        image_store._TOPIC_IMAGES_DIR = saved["images"]
        image_store._DIRS_INITIALIZED = saved["init"]


async def create_temp_db() -> tuple[Any, Any]:
    """建一个只含 published_content_performance 的内存 SQLite。

    返回 (engine, sessionmaker)。调用方负责 engine.dispose()。
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db.models import PublishedContentPerformance

    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(PublishedContentPerformance.__table__.create, checkfirst=True)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    return engine, session_factory


@contextlib.contextmanager
def patch_session_local(session_factory: Any) -> Iterator[None]:
    """把 db_sink 的会话工厂换成临时库，避免连真实 MySQL。"""
    from capabilities.scrapling.spiders import db_sink

    saved = db_sink.AsyncSessionLocal
    db_sink.AsyncSessionLocal = session_factory
    try:
        yield
    finally:
        db_sink.AsyncSessionLocal = saved
