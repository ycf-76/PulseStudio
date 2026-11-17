"""cut_plan 确认闸门（设计文档 3.4 #2 缺口2：叠加方案渲染前用户看不到）。

覆盖：
  1. events.py 契约：EndedReason.CUT_PLAN_PAUSED / EventType.CUT_PLAN_CONFIRMATION_REQUIRED
  2. session.py：cut_plan 闸门分支（SSE 事件、loop_state 落盘、ended_reason、
     分支顺序必须在 guardian 判定命中前）
  3. dispatch_hooks.py：gate_mode 注入范围收窄（仅 pip/overlay_decor 场景）
  4. agents/chat_agent.py：awaiting_cut_plan_confirmation 状态映射
  5. routers/chat_agent.py：/confirm-cut-plan 确定性回放（gate_mode=auto_confirm）
"""

from __future__ import annotations

import inspect

import pytest

# ============================================================================
# 1. events.py 契约
# ============================================================================


class TestEventsContract:
    def test_ended_reason_cut_plan_paused(self):
        from app.engine.protocol.events import EndedReason

        assert EndedReason.CUT_PLAN_PAUSED.value == "cut_plan_paused"

    def test_event_type_cut_plan_confirmation_required(self):
        from app.engine.protocol.events import EventType

        assert EventType.CUT_PLAN_CONFIRMATION_REQUIRED.value == "cut_plan_confirmation_required"


# ============================================================================
# 2. session.py cut_plan 闸门分支
# ============================================================================


class TestSessionCutPlanGate:
    def _run_source(self) -> str:
        from app.engine.reactor.session import ReactorSession

        return inspect.getsource(ReactorSession.run)

    def test_emits_cut_plan_confirmation_required(self):
        source = self._run_source()
        assert (
            "cut_plan_confirmation_required" in source
        ), "session.run 应在 overlay_plan_ready 时发 cut_plan_confirmation_required SSE 事件"

    def test_loop_state_saves_cut_plan(self):
        source = self._run_source()
        assert (
            '"cut_plan"' in source and "_awaiting_cut_plan_confirmation" in source
        ), "loop_state 应落盘 cut_plan 数据并标记 _awaiting_cut_plan_confirmation"

    def test_ended_reason_cut_plan_paused(self):
        source = self._run_source()
        assert (
            "EndedReason.CUT_PLAN_PAUSED.value" in source
        ), "cut_plan 闸门分支应以 CUT_PLAN_PAUSED 终止（暂停而非完成）"

    def test_cut_plan_branch_before_storyboard_branch(self):
        """cut_plan 分支必须在 storyboard 分支之前。

        原因：overlay_plan_ready 信号 needs_confirmation=True 且无 shots，
        若 storyboard 分支在前也不会命中它，但 guardian 分支（needs_confirmation
        且无 shots）会命中——cut_plan 分支必须放在 guardian 之后、storyboard
        之前，且以 event=="overlay_plan_ready" 为判别条件。
        """
        source = self._run_source()
        cp_pos = source.find("cut_plan confirmation signal")
        sb_pos = source.find("storyboard confirmation signal")
        assert cp_pos != -1 and sb_pos != -1
        assert cp_pos < sb_pos, "cut_plan 分支应位于 storyboard 分支之前"

    def test_cut_plan_branch_uses_overlay_plan_ready_condition(self):
        source = self._run_source()
        assert 'obs.get("event") == "overlay_plan_ready"' in source, (
            "cut_plan 分支应以 event==overlay_plan_ready 判别，"
            "避免误吞 guardian / storyboard 的 needs_confirmation 信号"
        )

    def test_paused_reason_includes_cut_plan(self):
        from app.engine.protocol.events import EndedReason
        from app.engine.reactor.session import _paused_reason

        assert _paused_reason(EndedReason.CUT_PLAN_PAUSED.value) is True
        assert _paused_reason("workflow_completed") is False

    def test_is_paused_tuple_includes_cut_plan(self):
        source = self._run_source()
        # 取 run 源码尾部 _is_paused 判定
        pos = source.rfind("_is_paused = final_output")
        assert pos != -1
        assert (
            "CUT_PLAN_PAUSED" in source[pos : pos + 300]
        ), "_is_paused 元组应包含 CUT_PLAN_PAUSED，否则暂停时会误发 workflow_completed"


# ============================================================================
# 3. dispatch_hooks.py gate_mode 注入范围收窄
# ============================================================================


class TestGateModeInjectionScope:
    def _make_ctx(self):
        from app.engine.protocol.dispatch_hooks import DispatchContext

        return DispatchContext(
            run_context={"video_asset_ids": ["asset_x"]}, workflow_id="wf_test", turn_id=None
        )

    @pytest.mark.asyncio
    async def test_main_flow_no_gate_mode_injection(self):
        """主流程（无 pip/overlay_decor）不得注入 auto_confirm——缺口2 根因。"""
        from app.agents import dispatch_hooks

        hook = dispatch_hooks.CreativeVideoContextHook()

        async def _fake_resolve(ids, user_id):
            return []

        orig = dispatch_hooks._resolve_video_paths
        dispatch_hooks._resolve_video_paths = _fake_resolve
        try:
            args = {
                "skill": "talking_head_overlay",
                "arguments": {"video_asset_ids": ["asset_x"]},
            }
            _, out = await hook.pre_execute("skill_call", args, self._make_ctx())
            assert "gate_mode" not in out["arguments"], (
                "主流程 talking_head_overlay 不应被注入 gate_mode=auto_confirm，"
                "否则叠加方案渲染前用户看不到（缺口2）"
            )
        finally:
            dispatch_hooks._resolve_video_paths = orig

    @pytest.mark.asyncio
    async def test_pip_path_still_auto_confirm(self):
        """pip=True（用户显式要画中画）仍自动确认，走完流程。"""
        from app.agents import dispatch_hooks

        hook = dispatch_hooks.CreativeVideoContextHook()

        async def _fake_resolve(ids, user_id):
            return []

        orig = dispatch_hooks._resolve_video_paths
        dispatch_hooks._resolve_video_paths = _fake_resolve
        try:
            args = {
                "skill": "talking_head_overlay",
                "arguments": {"video_asset_ids": ["asset_x"], "pip": True},
            }
            _, out = await hook.pre_execute("skill_call", args, self._make_ctx())
            assert out["arguments"].get("gate_mode") == "auto_confirm"
        finally:
            dispatch_hooks._resolve_video_paths = orig


# ============================================================================
# 4. agents/chat_agent.py 状态映射
# ============================================================================


class TestChatAgentStatusMapping:
    def test_awaiting_cut_plan_status_mapped(self):
        from app.agents.chat_agent import ChatAgent

        source = inspect.getsource(ChatAgent)
        assert "awaiting_cut_plan_confirmation" in source, (
            "ChatAgent 应把 _awaiting_cut_plan_confirmation 映射为 "
            "status=awaiting_cut_plan_confirmation"
        )


# ============================================================================
# 5. routers/chat_agent.py /confirm-cut-plan
# ============================================================================


class TestConfirmCutPlanRoute:
    def test_route_exists_with_deterministic_replay(self):
        from app.api.routers.chat_agent import confirm_cut_plan_action

        source = inspect.getsource(confirm_cut_plan_action)
        assert (
            'call_args["gate_mode"] = "auto_confirm"' in source
        ), "confirm-cut-plan 回放必须显式 gate_mode=auto_confirm（确定性回放）"
        assert (
            "cut_plan_confirmation_resolved" in source
        ), "确认/取消后应广播 cut_plan_confirmation_resolved"
        assert "cleanup_loop_state" in source

    def test_confirm_resolved_payload_carries_version(self):
        """设计文档 3.4 #4：回放成功的 resolved 载荷应携带 version（版本号）。"""
        from app.api.routers.chat_agent import confirm_cut_plan_action

        source = inspect.getsource(confirm_cut_plan_action)
        assert (
            "version" in source
        ), "confirm-cut-plan 回放成功时应计算版本号并在 SSE 载荷中携带 version"

    def test_paused_lists_include_cut_plan(self):
        import app.api.routers.chat_agent as router_mod

        source = inspect.getsource(router_mod)
        assert (
            source.count('"cut_plan_paused"') >= 3
        ), "router 内三处续跑暂停列表都应包含 cut_plan_paused"
