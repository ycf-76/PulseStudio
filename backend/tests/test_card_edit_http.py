"""卡片读/编辑 HTTP 契约。

详情页「左编辑右预览」的前提是：产物里已有的 CardDeck 必须能被前端读到，
且编辑写入要带乐观锁、异常要映射成可被前端识别的状态码。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI

from app.api.deps import get_current_user
from app.api.routers import cards as cards_router_module


def _client() -> httpx.AsyncClient:
    app = FastAPI()
    app.include_router(cards_router_module.router)
    app.dependency_overrides[get_current_user] = lambda: "u1"
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


_DRAFT = {
    "artifact_id": "ca_one",
    "version": 3,
    "title": "旅行清单",
    "pages": [{"id": "page_01", "type": "cover"}],
    "deck": {
        "deck_id": "d1",
        "pages": [{"id": "page_01", "layout_id": "cover_hero", "blocks": []}],
    },
}


@pytest.mark.asyncio
async def test_get_card_returns_deck_and_version():
    with patch.object(
        cards_router_module, "read_card", new_callable=AsyncMock, return_value=_DRAFT
    ):
        async with _client() as client:
            resp = await client.get("/api/cards/ca_one")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["version"] == 3
    assert data["deck"]["deck_id"] == "d1"


@pytest.mark.asyncio
async def test_edit_route_passes_expected_version_and_returns_new_draft():
    fake = AsyncMock(return_value={**_DRAFT, "version": 4})
    with patch.object(cards_router_module, "apply_card_edit", fake):
        async with _client() as client:
            resp = await client.post(
                "/api/cards/ca_one/edit",
                json={
                    "expected_version": 3,
                    "page_id": "page_01",
                    "block_id": "h1",
                    "op": "set_text",
                    "value": "新标题",
                },
            )

    assert resp.status_code == 200
    assert resp.json()["data"]["version"] == 4
    args = fake.await_args.args
    assert args[0] == "ca_one" and args[1] == "u1" and args[2] == 3
    assert args[3].op == "set_text" and args[3].value == "新标题"


@pytest.mark.asyncio
async def test_edit_route_maps_stale_version_to_409():
    with patch.object(
        cards_router_module,
        "apply_card_edit",
        new_callable=AsyncMock,
        side_effect=RuntimeError("卡片版本已更新，请刷新后重试"),
    ):
        async with _client() as client:
            resp = await client.post(
                "/api/cards/ca_one/edit",
                json={"expected_version": 1, "page_id": "p", "op": "set_theme", "value": "warm"},
            )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_edit_route_maps_permission_and_missing_to_403_404():
    for exc, status in (
        (PermissionError("无权访问该作品"), 403),
        (LookupError("创作作品不存在"), 404),
    ):
        with patch.object(
            cards_router_module, "apply_card_edit", new_callable=AsyncMock, side_effect=exc
        ):
            async with _client() as client:
                resp = await client.post(
                    "/api/cards/ca_one/edit",
                    json={
                        "expected_version": 1,
                        "page_id": "p",
                        "op": "set_theme",
                        "value": "warm",
                    },
                )
        assert resp.status_code == status


@pytest.mark.asyncio
async def test_get_card_maps_non_structured_to_400():
    with patch.object(
        cards_router_module,
        "read_card",
        new_callable=AsyncMock,
        side_effect=ValueError("该作品不是结构化卡片"),
    ):
        async with _client() as client:
            resp = await client.get("/api/cards/ca_one")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_edit_requires_expected_version():
    async with _client() as client:
        resp = await client.post(
            "/api/cards/ca_one/edit", json={"page_id": "p", "op": "set_theme", "value": "warm"}
        )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_meta_route_is_not_shadowed_by_card_id_route():
    """/{artifact_id} 必须排在 /meta 之后，否则组件清单会被当成作品 id 吞掉。"""
    async with _client() as client:
        resp = await client.get("/api/cards/meta")
    assert resp.status_code == 200
    assert "layouts" in resp.json()["data"]


def test_summarize_exposes_deck_into_card_draft():
    from pathlib import Path

    from app.services.creative_artifact import _summarize

    deck = {"deck_id": "d1", "theme_id": "ink", "pages": []}
    summary = _summarize(
        {"artifact_id": "ca_one", "brief": {"title": "T"}, "card_deck": deck},
        Path("ca_one.json"),
    )
    assert summary["card_draft"]["deck"] == deck


def test_summarize_keeps_deck_empty_when_artifact_has_none():
    from pathlib import Path

    from app.services.creative_artifact import _summarize

    summary = _summarize({"artifact_id": "ca_one", "brief": {"title": "T"}}, Path("ca_one.json"))
    assert summary["card_draft"]["deck"] is None


def test_to_card_draft_exposes_deck_for_editor():
    from app.services.creative_artifact import CreativeArtifact, to_card_draft

    deck = {"deck_id": "d1", "theme_id": "ink", "pages": []}
    artifact = CreativeArtifact(artifact_id="ca_one", version=7, card_deck=deck)
    draft = to_card_draft(artifact)
    assert draft["deck"] == deck
    assert draft["version"] == 7
    assert draft["artifact_id"] == "ca_one"
