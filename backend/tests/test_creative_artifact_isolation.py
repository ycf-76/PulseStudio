"""产物隔离：归属校验 + 按用户/作品/会话维度的列表过滤。"""

from __future__ import annotations

import pytest

from app.api.routers import creative_artifact as api
from app.services import creative_artifact as svc


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "_STORE_DIR", tmp_path)
    return tmp_path


def _seed(artifact_id: str, *, owner: str = "", session_id: str = "", work_id: str = "") -> None:
    artifact = svc.build_artifact(
        artifact_id=artifact_id,
        owner_user_id=owner,
        card_draft={"title": artifact_id, "pages": []},
        source={"session_id": session_id, "work_id": work_id},
    )
    svc.save_artifact(artifact)


async def test_verify_owner_accepts_matching_owner(store):
    _seed("a1", owner="u1")
    artifact = svc.load_artifact("a1")
    assert artifact is not None
    await svc.verify_owner(artifact, "u1")


async def test_verify_owner_rejects_other_user(store):
    _seed("a1", owner="u1")
    artifact = svc.load_artifact("a1")
    assert artifact is not None
    with pytest.raises(PermissionError):
        await svc.verify_owner(artifact, "u2")


async def test_verify_owner_falls_back_to_session_for_legacy(store, monkeypatch):
    _seed("legacy", session_id="s1")

    async def fake_owner(session_id: str) -> str:
        return "u1" if session_id == "s1" else ""

    monkeypatch.setattr(svc, "session_owner", fake_owner)
    artifact = svc.load_artifact("legacy")
    assert artifact is not None
    await svc.verify_owner(artifact, "u1")
    with pytest.raises(PermissionError):
        await svc.verify_owner(artifact, "u2")


async def test_verify_owner_rejects_legacy_without_session(store):
    _seed("orphan")
    artifact = svc.load_artifact("orphan")
    assert artifact is not None
    with pytest.raises(PermissionError):
        await svc.verify_owner(artifact, "u1")


async def test_list_only_returns_current_user_artifacts(store):
    _seed("a1", owner="u1", session_id="s1", work_id="w1")
    _seed("a2", owner="u2", session_id="s2", work_id="w2")

    items = await svc.list_artifacts_for_user("u1")
    assert [i["artifact_id"] for i in items] == ["a1"]
    assert items[0]["owner_user_id"] == "u1"
    assert items[0]["source"]["work_id"] == "w1"


async def test_list_filters_by_work_and_session(store):
    _seed("a1", owner="u1", session_id="s1", work_id="w1")
    _seed("a2", owner="u1", session_id="s1", work_id="w2")

    by_work = await svc.list_artifacts_for_user("u1", work_id="w1")
    assert [i["artifact_id"] for i in by_work] == ["a1"]

    by_session = await svc.list_artifacts_for_user("u1", session_id="s1")
    assert {i["artifact_id"] for i in by_session} == {"a1", "a2"}

    missing = await svc.list_artifacts_for_user("u1", work_id="w9")
    assert missing == []


async def test_list_legacy_fallback_and_anonymous_denied(store, monkeypatch):
    _seed("legacy", session_id="s1")
    _seed("orphan")

    async def fake_owner(session_id: str) -> str:
        return "u1" if session_id == "s1" else ""

    monkeypatch.setattr(svc, "session_owner", fake_owner)
    items = await svc.list_artifacts_for_user("u1")
    assert [i["artifact_id"] for i in items] == ["legacy"]
    assert await svc.list_artifacts_for_user("") == []


async def test_api_list_scopes_to_user(store):
    _seed("a1", owner="u1")
    _seed("a2", owner="u2")

    resp = await api.list_items(limit=50, work_id="", session_id="", user_id="u1")
    assert [i["artifact_id"] for i in resp.data] == ["a1"]


async def test_api_get_and_delete_are_owner_scoped(store):
    _seed("a1", owner="u1")

    await api.get_item("a1", user_id="u1")

    with pytest.raises(api.HTTPException) as forbidden:
        await api.get_item("a1", user_id="u2")
    assert forbidden.value.status_code == 403

    with pytest.raises(api.HTTPException) as missing:
        await api.get_item("nope", user_id="u1")
    assert missing.value.status_code == 404

    with pytest.raises(api.HTTPException) as denied_delete:
        await api.delete_item("a1", user_id="u2")
    assert denied_delete.value.status_code == 403
    assert svc.load_artifact("a1") is not None


async def test_api_patch_cannot_steal_ownership(store):
    _seed("a1", owner="u1")

    with pytest.raises(api.HTTPException) as denied:
        await api.patch_item("a1", {"brief": {"title": "hack"}}, user_id="u2")
    assert denied.value.status_code == 403

    saved = await api.patch_item(
        "a1", {"owner_user_id": "u2", "brief": {"title": "mine"}}, user_id="u1"
    )
    assert saved.data["owner_user_id"] == "u1"
    assert saved.data["brief"]["title"] == "mine"


async def test_api_build_sets_owner_and_blocks_overwrite(store):
    built = await api.build(
        api.BuildRequest(artifact_id="a1", card_draft={"title": "t", "pages": []}),
        user_id="u1",
    )
    assert built.data["owner_user_id"] == "u1"

    with pytest.raises(api.HTTPException) as denied:
        await api.build(
            api.BuildRequest(artifact_id="a1", card_draft={"title": "x", "pages": []}),
            user_id="u2",
        )
    assert denied.value.status_code == 403


async def test_load_and_list_tolerate_utf8_bom(store):
    """存量产物曾被带 BOM 的编辑器改写过，带 BOM 的必须仍能读出来。"""
    _seed("a1", owner="u1", session_id="s1")
    path = store / "a1.json"
    path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())

    artifact = svc.load_artifact("a1")
    assert artifact is not None
    assert artifact.owner_user_id == "u1"
    assert [i["artifact_id"] for i in await svc.list_artifacts_for_user("u1")] == ["a1"]