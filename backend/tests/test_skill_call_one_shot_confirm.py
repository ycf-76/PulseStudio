"""skill_call 一次性确认（2026-11-05）单测。

背景：需确认模式（auto_approve=False）下，每个 skill_call 都弹 Guardian 卡，
一轮视频任务连撞 N 次闸门（15:47/15:51 日志实锤）。修复后：用户批准过一次
（confirm-guardian 续跑路径置 session.skill_call_once_confirmed=True），
同 turn 内后续 low/medium 风险的 skill_call 直接放行；high 风险仍弹卡。

离线 mock，不碰 LLM / 计费层。
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.engine.governance.guardian import GuardianAction
from app.engine.reactor.session import ReactorSession
from app.engine.schemas import Permission


def _make_session(auto_approve: bool = False) -> ReactorSession:
    """需确认模式 + 全权限的最小 session，guardian/transport 由用例注入。"""
    session = ReactorSession(
        llm=AsyncMock(),
        skills=[],
        allowed_permissions=set(Permission),
        sse_bus=AsyncMock(),
        workflow_id="test_one_shot",
        auto_approve=auto_approve,
    )
    # 伪造 transport：skill_call 原语存在且可执行
    fake_tool = MagicMock()
    fake_tool.required_permissions = ()
    session._transport = MagicMock()
    session._transport.get_tool = MagicMock(return_value=fake_tool)
    session._transport.call = AsyncMock(return_value={"ok": True, "data": "executed"})
    return session


def _stub_guardian(session: ReactorSession, risk_level: str) -> None:
    decision = MagicMock()
    decision.action = GuardianAction.NEEDS_CONFIRM
    decision.risk_level = risk_level
    decision.reason = "test"
    decision.hint = "test hint"
    decision.user_hint = ""
    decision.confirmation_prompt = "是否执行？"
    session._guardian = MagicMock()
    session._guardian.check = AsyncMock(return_value=decision)


_SKILL_CALL = {
    "name": "skill_call",
    "arguments": {"skill": "video_transcribe", "arguments": {"video_path": "x.mp4"}},
}


@pytest.mark.asyncio
async def test_first_skill_call_still_asks_confirmation():
    """未批准过 → 需确认模式下首次 skill_call 仍弹确认。"""
    session = _make_session()
    _stub_guardian(session, "medium")

    result = await session._dispatch(dict(_SKILL_CALL))

    assert result.get("needs_confirmation") is True
    session._transport.call.assert_not_awaited()


@pytest.mark.asyncio
async def test_once_confirmed_skips_repeat_confirmation():
    """批准过一次（skill_call_once_confirmed=True）→ 同类 low/medium 直接放行。"""
    session = _make_session()
    session.skill_call_once_confirmed = True
    _stub_guardian(session, "medium")

    result = await session._dispatch(dict(_SKILL_CALL))

    assert result.get("ok") is True
    assert result.get("needs_confirmation") is None
    session._transport.call.assert_awaited_once()


@pytest.mark.asyncio
async def test_once_confirmed_high_risk_still_asks():
    """high 风险是安全底线：即使批准过一次也必须再弹卡。"""
    session = _make_session()
    session.skill_call_once_confirmed = True
    _stub_guardian(session, "high")

    result = await session._dispatch(dict(_SKILL_CALL))

    assert result.get("needs_confirmation") is True
    session._transport.call.assert_not_awaited()


@pytest.mark.asyncio
async def test_once_confirmed_does_not_affect_other_tools():
    """一次性确认只针对 skill_call：其它工具（如 bash）仍走原确认逻辑。"""
    session = _make_session()
    session.skill_call_once_confirmed = True
    _stub_guardian(session, "medium")

    result = await session._dispatch({"name": "bash", "arguments": {"command": "ls"}})

    assert result.get("needs_confirmation") is True
    session._transport.call.assert_not_awaited()


@pytest.mark.asyncio
async def test_auto_approve_covers_high_risk_tool():
    """自动放行开启时 high 风险工具（file_write / bash）不再弹卡。

    此前只放行 low/medium，high 风险仍返回 needs_confirmation，
    用户开了自动放行却反复被拦（BUG-008）。
    """
    session = _make_session(auto_approve=True)
    _stub_guardian(session, "high")

    result = await session._dispatch({"name": "file_write", "arguments": {"path": "a.txt"}})

    assert result.get("needs_confirmation") is None
    session._transport.call.assert_awaited_once()


@pytest.mark.asyncio
async def test_auto_approve_covers_high_risk_skill_call():
    """自动放行开启时 high 风险 skill_call 也直接放行。"""
    session = _make_session(auto_approve=True)
    _stub_guardian(session, "high")

    result = await session._dispatch(dict(_SKILL_CALL))

    assert result.get("needs_confirmation") is None
    session._transport.call.assert_awaited_once()