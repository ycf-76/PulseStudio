"""分镜确认回放链路测试（2026-10-17 接入）。

背景：小加同学"手持跟拍日常vlog"技能的 3 阶段流水线要求
「VL 看图 → 分镜脚本 → 用户确认 → 再生成视频」。确认闸门在技能层
（multi_image_video.TestConfirmGate）和 Reactor 层（needs_confirmation
→ 落盘 loop_state）都已接上；本文件覆盖最后一环：
POST /api/v1/chat/confirm-storyboard 的确定性回放。

设计取舍：回放走「直接用 refine_shots 重放落盘的 pending_skill_call」，
不经 LLM。因为整份分镜 JSON 若靠 LLM 从对话历史重新吐出，镜头一多就
容易字段丢失/串镜，而且失败了还查不出原因。
"""

from __future__ import annotations

import asyncio

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.api.routers.chat_agent import (
    StoryboardConfirmRequest,
    confirm_storyboard_action,
)
from app.engine.reactor.loop_state import cleanup_loop_state, load_loop_state, save_loop_state

PENDING_ARGS = {
    "image_asset_ids": ["asset_a", "asset_b"],
    "prompt": "深圳大鹏山也度假酒店探店 vlog",
    "ratio": "9:16",
    "shot_duration": 5.0,
}

STORED_SHOTS = [
    {
        "shot_index": 1, "caption": "到达·拖箱进门", "image_prompt": "arrival",
        "motion_prompt": "推进", "reference_images": [1], "duration": 5.0,
        "transition": "cut",
    },
    {
        "shot_index": 2, "caption": "房间·落地窗山景", "image_prompt": "room",
        "motion_prompt": "由床推向窗", "reference_images": [2], "duration": 5.0,
        "transition": "dissolve",
    },
]


def _seed_state(tmp_path, *, shots=None, pending_args=None, with_pending=True):
    state = {
        "messages": [],
        "iteration": 2,
        "context_snapshot": {"workflow_id": "wf_confirm", "extra": {}},
        "pending_skill_call": (
            {"name": "multi_image_video", "arguments": dict(pending_args or PENDING_ARGS)}
            if with_pending else {}
        ),
        "storyboard": {
            "title": "山也度假酒店 vlog",
            "shots": shots if shots is not None else STORED_SHOTS,
            "estimated_duration": 10.0,
        },
        "clarified_answers": {},
        "_awaiting_storyboard_confirmation": True,
    }
    return save_loop_state(state)


def _mock_primitive(result):
    primitive = MagicMock()
    primitive.execute = AsyncMock(return_value=result)
    return primitive


@pytest.fixture(autouse=True)
def _isolate_state_dir(tmp_path, monkeypatch):
    """把 loop_state 落盘目录指到临时目录，避免污染真实状态目录/用例间串味。"""
    from app.engine.reactor import loop_state as loop_state_module

    monkeypatch.setattr(loop_state_module, "_STATE_DIR", tmp_path, raising=False)
    yield


@pytest.mark.asyncio
async def test_confirm_replays_with_edited_shots(tmp_path, monkeypatch):
    """确认（含用户改过的分镜）→ 用 refine_shots 重放，原参数不丢"""
    path = _seed_state(tmp_path)
    primitive = _mock_primitive({"success": True, "asset_url": "/uploads/final.mp4"})

    edited = [dict(STORED_SHOTS[0], caption="改过的第一镜"), STORED_SHOTS[1]]
    with patch("app.engine.reactor.primitives.SkillCallPrimitive", return_value=primitive):
        with patch("app.api.routers.chat_agent.sse_bus.publish", new=AsyncMock()):
            res = await confirm_storyboard_action(
                StoryboardConfirmRequest(
                    session_id="s1", loop_state_path=path, shots=edited,
                ),
                user_id="u1",
                db=MagicMock(),
            )

    # 2026-10 异步化改造：确认接口立即返回 processing，生成转后台任务、终态走 SSE
    assert res["status"] == "processing"
    await asyncio.sleep(0.05)  # 让 create_task 的后台回放任务跑完

    kwargs = primitive.execute.call_args.kwargs
    assert kwargs["skill"] == "multi_image_video"
    assert kwargs["arguments"]["refine_shots"] == edited
    # 原始调用参数必须原样带回，否则回放时参考图/prompt 就丢了
    assert kwargs["arguments"]["image_asset_ids"] == ["asset_a", "asset_b"]
    assert kwargs["arguments"]["prompt"] == PENDING_ARGS["prompt"]
    assert kwargs["arguments"]["shot_duration"] == 5.0
    # refine 模式跳过 VL+LLM，标题要靠落盘那份带回来（否则退化成 prompt 前 30 字）
    assert kwargs["arguments"]["refine_title"] == "山也度假酒店 vlog"
    # refine_shots / refine_title 之外不该混进用户没要求的键
    assert set(kwargs["arguments"]) - {"refine_shots", "refine_title"} == set(PENDING_ARGS)

    # 回放成功后状态必须清理，不能留着被重复触发
    with pytest.raises(FileNotFoundError):
        load_loop_state(path)


@pytest.mark.asyncio
async def test_confirm_without_shots_falls_back_to_stored(tmp_path):
    """前端只发"确认"没带 shots → 用落盘那份"""
    path = _seed_state(tmp_path)
    primitive = _mock_primitive({"success": True})

    with patch("app.engine.reactor.primitives.SkillCallPrimitive", return_value=primitive):
        with patch("app.api.routers.chat_agent.sse_bus.publish", new=AsyncMock()):
            await confirm_storyboard_action(
                StoryboardConfirmRequest(session_id="s1", loop_state_path=path, shots=None),
                user_id="u1",
                db=MagicMock(),
            )

    await asyncio.sleep(0.05)
    kwargs = primitive.execute.call_args.kwargs
    assert kwargs["arguments"]["refine_shots"] == STORED_SHOTS


@pytest.mark.asyncio
async def test_confirm_with_empty_shots_is_rejected(tmp_path):
    """用户清空了所有镜头 → 明确 400，不静默出一版空视频"""
    path = _seed_state(tmp_path)
    primitive = _mock_primitive({"success": True})

    with patch("app.engine.reactor.primitives.SkillCallPrimitive", return_value=primitive):
        with patch("app.api.routers.chat_agent.sse_bus.publish", new=AsyncMock()):
            with pytest.raises(HTTPException) as exc:
                await confirm_storyboard_action(
                    StoryboardConfirmRequest(
                        session_id="s1", loop_state_path=path, shots=[],
                    ),
                    user_id="u1",
                    db=MagicMock(),
                )

    assert exc.value.status_code == 400
    primitive.execute.assert_not_called()
    assert load_loop_state(path)["storyboard"]["shots"] == STORED_SHOTS


@pytest.mark.asyncio
async def test_cancel_never_generates(tmp_path):
    """取消 → 不调技能，状态清理，返回 cancelled"""
    path = _seed_state(tmp_path)
    primitive = _mock_primitive({"success": True})

    with patch("app.engine.reactor.primitives.SkillCallPrimitive", return_value=primitive):
        with patch("app.api.routers.chat_agent.sse_bus.publish", new=AsyncMock()):
            res = await confirm_storyboard_action(
                StoryboardConfirmRequest(
                    session_id="s1", loop_state_path=path, action="cancel",
                ),
                user_id="u1",
                db=MagicMock(),
            )

    assert res["status"] == "cancelled"
    primitive.execute.assert_not_called()
    with pytest.raises(FileNotFoundError):
        load_loop_state(path)


@pytest.mark.asyncio
async def test_missing_state_returns_404(tmp_path):
    """状态文件不存在/已过期 → 404，不抛出内部错误"""
    primitive = _mock_primitive({"success": True})

    with patch("app.engine.reactor.primitives.SkillCallPrimitive", return_value=primitive):
        with patch("app.api.routers.chat_agent.sse_bus.publish", new=AsyncMock()):
            with pytest.raises(HTTPException) as exc:
                await confirm_storyboard_action(
                    StoryboardConfirmRequest(
                        session_id="s1", loop_state_path="loop_does_not_exist",
                    ),
                    user_id="u1",
                    db=MagicMock(),
                )

    assert exc.value.status_code == 404
    primitive.execute.assert_not_called()


@pytest.mark.asyncio
async def test_state_without_pending_call_is_rejected(tmp_path):
    """落盘状态里没有待回放调用 → 400 且状态清理（不留僵尸状态）"""
    path = _seed_state(tmp_path, with_pending=False)
    primitive = _mock_primitive({"success": True})

    with patch("app.engine.reactor.primitives.SkillCallPrimitive", return_value=primitive):
        with patch("app.api.routers.chat_agent.sse_bus.publish", new=AsyncMock()):
            with pytest.raises(HTTPException) as exc:
                await confirm_storyboard_action(
                    StoryboardConfirmRequest(session_id="s1", loop_state_path=path),
                    user_id="u1",
                    db=MagicMock(),
                )

    assert exc.value.status_code == 400
    primitive.execute.assert_not_called()
    with pytest.raises(FileNotFoundError):
        load_loop_state(path)


@pytest.mark.asyncio
async def test_failed_generation_reports_failure_and_cleans_up(tmp_path):
    """回放后生成失败 → 如实返回 failed，不假装成功；状态同样清理"""
    path = _seed_state(tmp_path)
    primitive = _mock_primitive({
        "success": False, "error": "成片拼接失败", "clip_urls": ["/uploads/c1.mp4"],
    })
    publish = AsyncMock()

    with patch("app.engine.reactor.primitives.SkillCallPrimitive", return_value=primitive):
        with patch("app.api.routers.chat_agent.sse_bus.publish", new=publish):
            res = await confirm_storyboard_action(
                StoryboardConfirmRequest(session_id="s1", loop_state_path=path),
                user_id="u1",
                db=MagicMock(),
            )
            # sleep 必须在 patch 作用域内：后台任务执行时 mock 还要生效
            await asyncio.sleep(0.05)

    # 异步化改造：失败发生在后台任务里，接口同步返回 processing；
    # 失败也要发 resolved 事件，否则前端按钮会一直转圈
    assert res["status"] == "processing"

    assert publish.await_count > 0, (
        f"publish 从未被调用：后台任务未执行或未发事件。res={res}, "
        f"execute_called={primitive.execute.await_count}"
    )
    resolved = [c for c in publish.await_args_list if len(c.args) > 1 and c.args[1] == "storyboard_confirmation_resolved"]
    assert resolved, f"未发 resolved 事件: {[c.args for c in publish.await_args_list]}"
    assert resolved[-1].args[2]["ok"] is False
    with pytest.raises(FileNotFoundError):
        load_loop_state(path)


def test_stored_shots_round_trip_through_json(tmp_path):
    """落盘/读回的分镜不能被 JSON 序列化改写（refine_shots 回放的命脉）"""
    path = _seed_state(tmp_path)
    try:
        loaded = load_loop_state(path)
        assert loaded["storyboard"]["shots"] == STORED_SHOTS
        assert loaded["pending_skill_call"]["arguments"] == PENDING_ARGS
    finally:
        cleanup_loop_state(path)
