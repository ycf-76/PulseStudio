"""ReAct Loop 八轮诊断 — R1 Guardian 确认全链路 + R2 loop_state 泄漏 + R3 终态 SSE + R4 熔断器 + R5 turn_id。

Step 35: ChatAgent._agentic_loop 有 _awaiting_guardian_confirmation 检查
Step 36: agent_chat 有 awaiting_guardian_confirmation 状态处理
Step 37: /confirm-guardian 端点存在且完整
Step 38: /confirm + /clarify 后台任务 cleanup_loop_state 在 finally 中
Step 39: /confirm-storyboard 内部异常发 workflow_error SSE
Step 40: 熔断器 record_failure 区分不可恢复错误
Step 41: workflow_error 回调带 turn_id
"""

from __future__ import annotations

import inspect

import pytest

# ============================================================================
# Step 35: ChatAgent._agentic_loop Guardian 确认状态
# ============================================================================


class TestStep35GuardianChatAgentStatus:
    """ChatAgent._agentic_loop 应识别 _awaiting_guardian_confirmation。"""

    def test_agentic_loop_checks_guardian_confirmation(self):
        """_agentic_loop 应有 _awaiting_guardian_confirmation 检查。"""
        from app.agents.chat_agent import ChatAgent

        source = inspect.getsource(ChatAgent._agentic_loop)

        has_check = "_awaiting_guardian_confirmation" in source
        assert has_check, (
            "BUG：ChatAgent._agentic_loop 不检查 _awaiting_guardian_confirmation。"
            "Guardian 确认信号从 ReactorSession 传到 ChatAgent 后被忽略 → "
            "返回 agent_output 而非 awaiting_guardian_confirmation。"
        )

    def test_returns_awaiting_guardian_confirmation_status(self):
        """应返回 awaiting_guardian_confirmation 状态。"""
        from app.agents.chat_agent import ChatAgent

        source = inspect.getsource(ChatAgent._agentic_loop)

        has_status = "awaiting_guardian_confirmation" in source
        assert has_status, (
            "ChatAgent 应返回 status='awaiting_guardian_confirmation'，"
            "让 API 层能识别并返回正确的响应格式。"
        )


# ============================================================================
# Step 36: agent_chat Guardian 确认状态处理
# ============================================================================


class TestStep36AgentChatGuardianStatus:
    """agent_chat 应处理 awaiting_guardian_confirmation 状态。"""

    def test_agent_chat_handles_guardian_status(self):
        """agent_chat 应有 awaiting_guardian_confirmation 分支。"""
        from app.api.routers.chat_agent import agent_chat

        source = inspect.getsource(agent_chat)

        has_guardian = "awaiting_guardian_confirmation" in source
        assert has_guardian, (
            "BUG：agent_chat 不处理 awaiting_guardian_confirmation 状态。"
            "Guardian 确认信号传到 API 层后被忽略。"
        )

    def test_returns_loop_state_path(self):
        """应返回 loop_state_path 供前端调用 /confirm-guardian。"""
        from app.api.routers.chat_agent import agent_chat

        source = inspect.getsource(agent_chat)

        # 找 guardian 分支
        guardian_pos = source.find("awaiting_guardian_confirmation")
        if guardian_pos == -1:
            pytest.fail("awaiting_guardian_confirmation 分支不存在")
        after_guardian = source[guardian_pos:]
        has_loop_state = "loop_state_path" in after_guardian[:500]
        assert has_loop_state, (
            "awaiting_guardian_confirmation 响应应包含 loop_state_path，"
            "前端需要它来调用 /confirm-guardian 端点。"
        )


# ============================================================================
# Step 37: /confirm-guardian 端点
# ============================================================================


class TestStep37ConfirmGuardianEndpoint:
    """/confirm-guardian 端点应存在且完整。"""

    def test_endpoint_exists(self):
        """/confirm-guardian 端点应存在。"""
        from app.api.routers.chat_agent import router

        routes = [r.path for r in router.routes]
        has_endpoint = any("confirm-guardian" in r for r in routes)
        assert has_endpoint, (
            "BUG：/confirm-guardian 端点不存在。"
            "前端收到 guardian_confirmation_required 事件后无法确认/拒绝。"
        )

    def test_validates_guardian_confirmation_type(self):
        """端点应验证 loop_state 是 Guardian 确认类型。"""
        from app.api.routers.chat_agent import confirm_guardian_action

        source = inspect.getsource(confirm_guardian_action)

        has_type_check = "_awaiting_guardian_confirmation" in source
        assert has_type_check, (
            "/confirm-guardian 应验证 loop_state 中 _awaiting_guardian_confirmation=True，"
            "防止误用其他类型的 loop_state。"
        )

    def test_reject_path_sends_terminal_sse(self):
        """reject 路径应发 agent_loop_done 终态 SSE。"""
        from app.api.routers.chat_agent import confirm_guardian_action

        source = inspect.getsource(confirm_guardian_action)

        has_agent_loop_done = "agent_loop_done" in source
        assert has_agent_loop_done, (
            "/confirm-guardian reject 路径应发 agent_loop_done SSE，" "确保 SSE 流正确关闭。"
        )

    def test_cleanup_in_finally(self):
        """approve 路径的 cleanup_loop_state 应在 finally 中。"""
        from app.api.routers.chat_agent import confirm_guardian_action

        source = inspect.getsource(confirm_guardian_action)

        # 找后台任务中的 cleanup（第二个出现，第一个是 reject 路径）
        cleanup_positions = []
        start = 0
        while True:
            pos = source.find("cleanup_loop_state", start)
            if pos == -1:
                break
            cleanup_positions.append(pos)
            start = pos + 1

        if not cleanup_positions:
            pytest.fail("confirm_guardian_action 应有 cleanup_loop_state")

        # 至少有一个 cleanup 在 finally 中
        has_finally_cleanup = False
        for pos in cleanup_positions:
            before = source[:pos]
            if "finally:" in before[-500:]:
                has_finally_cleanup = True
                break
        assert has_finally_cleanup, (
            "cleanup_loop_state 应在 finally 块中，" "防止 session.run() 异常时临时文件残留。"
        )


# ============================================================================
# Step 38: /confirm + /clarify 后台任务 cleanup 在 finally 中
# ============================================================================


class TestStep38BackgroundCleanupInFinally:
    """后台任务的 cleanup_loop_state 应在 finally 中。"""

    def test_confirm_cleanup_in_finally(self):
        """/confirm 后台任务 cleanup_loop_state 应在 finally 中。"""
        from app.api.routers.chat_agent import confirm_action

        source = inspect.getsource(confirm_action)

        # 找 _run_resume_background 中的 cleanup
        bg_pos = source.find("_run_resume_background")
        if bg_pos == -1:
            pytest.skip("_run_resume_background 不存在")
        after_bg = source[bg_pos:]
        cleanup_pos = after_bg.find("cleanup_loop_state")
        if cleanup_pos == -1:
            pytest.fail("/confirm 后台任务应有 cleanup_loop_state")
        before_cleanup = after_bg[:cleanup_pos]
        has_finally = "finally:" in before_cleanup[-300:]
        assert has_finally, (
            "BUG：/confirm 后台任务 cleanup_loop_state 不在 finally 中。"
            "session.run() 异常时临时文件残留。"
        )

    def test_clarify_cleanup_in_finally(self):
        """/clarify 后台任务 cleanup_loop_state 应在 finally 中。"""
        from app.api.routers.chat_agent import clarify_action

        source = inspect.getsource(clarify_action)

        bg_pos = source.find("_run_clarify_resume_background")
        if bg_pos == -1:
            pytest.skip("_run_clarify_resume_background 不存在")
        after_bg = source[bg_pos:]
        cleanup_pos = after_bg.find("cleanup_loop_state")
        if cleanup_pos == -1:
            pytest.fail("/clarify 后台任务应有 cleanup_loop_state")
        before_cleanup = after_bg[:cleanup_pos]
        has_finally = "finally:" in before_cleanup[-300:]
        assert has_finally, (
            "BUG：/clarify 后台任务 cleanup_loop_state 不在 finally 中。"
            "session.run() 异常时临时文件残留。"
        )


# ============================================================================
# Step 39: /confirm-storyboard 内部异常发 workflow_error SSE
# ============================================================================


class TestStep39StoryboardExceptionTerminalSse:
    """/confirm-storyboard 内部异常消化后应发终态 SSE。"""

    def test_refine_exception_publishes_workflow_error(self):
        """_run_refine_in_background 的 except 块应发 workflow_error SSE。"""
        from app.api.routers.chat_agent import confirm_storyboard_action

        source = inspect.getsource(confirm_storyboard_action)

        # 找 _run_refine_in_background
        bg_pos = source.find("_run_refine_in_background")
        if bg_pos == -1:
            pytest.skip("_run_refine_in_background 不存在")
        after_bg = source[bg_pos:]

        # 找 except Exception 块
        except_pos = after_bg.find("except Exception as exc")
        if except_pos == -1:
            pytest.skip("except Exception 块不存在")
        after_except = after_bg[except_pos:]

        # 检查 except 块中有 workflow_error
        has_workflow_error = "workflow_error" in after_except[:1000]
        assert has_workflow_error, (
            "BUG：_run_refine_in_background 的 except 块不发 workflow_error SSE。"
            "异常被消化后 SSE 流不关闭。"
        )


# ============================================================================
# Step 40: 熔断器 record_failure 区分不可恢复错误
# ============================================================================


class TestStep40CircuitBreakerRecoverableFailure:
    """record_failure 应区分不可恢复错误（401/402/403）和暂时性错误（5xx）。"""

    def test_record_failure_not_always_unrecoverable(self):
        """record_failure 不应总是 is_unrecoverable=True。"""
        from app.engine.reactor.session import ReactorSession

        source = inspect.getsource(ReactorSession._call_llm)

        # 找 record_failure 调用
        rf_pos = source.find("record_failure")
        if rf_pos == -1:
            pytest.skip("record_failure 不存在")
        after_rf = source[rf_pos:]
        # 不应硬编码 True
        has_hardcoded_true = "is_unrecoverable=True" in after_rf[:100]
        assert not has_hardcoded_true, (
            "BUG：record_failure 硬编码 is_unrecoverable=True。"
            "5xx 暂时性错误也会立即熔断，应只对 401/402/403 立即熔断。"
        )

    def test_checks_error_type(self):
        """应根据错误内容判断是否不可恢复。"""
        from app.engine.reactor.session import ReactorSession

        source = inspect.getsource(ReactorSession._call_llm)

        rf_pos = source.find("record_failure")
        if rf_pos == -1:
            pytest.skip("record_failure 不存在")
        before_rf = source[:rf_pos]
        # 应有错误类型判断逻辑
        has_error_check = (
            "402" in before_rf[-300:]
            or "401" in before_rf[-300:]
            or "unauthorized" in before_rf[-300:]
        )
        assert has_error_check, (
            "record_failure 前应有错误类型判断逻辑，"
            "区分不可恢复错误（401/402/403）和暂时性错误（5xx）。"
        )


# ============================================================================
# Step 41: workflow_error 回调带 turn_id
# ============================================================================


class TestStep41WorkflowErrorTurnId:
    """workflow_error SSE 事件应带 turn_id。"""

    def test_confirm_callback_has_turn_id(self):
        """/confirm 的 _on_confirm_done 回调应传 turn_id。"""
        from app.api.routers.chat_agent import confirm_action

        source = inspect.getsource(confirm_action)

        # 找 _on_confirm_done
        cb_pos = source.find("_on_confirm_done")
        if cb_pos == -1:
            pytest.skip("_on_confirm_done 不存在")
        after_cb = source[cb_pos:]
        # 找 sse_bus.publish 调用
        publish_pos = after_cb.find("sse_bus.publish")
        if publish_pos == -1:
            pytest.skip("sse_bus.publish 不存在 in callback")
        after_publish = after_cb[publish_pos:]
        has_turn_id = "turn_id" in after_publish[:300]
        assert has_turn_id, (
            "BUG：/confirm 的 workflow_error 回调不传 turn_id。"
            "前端按 turn_id 过滤时可能丢弃错误事件。"
        )

    def test_clarify_callback_has_turn_id(self):
        """/clarify 的 _on_clarify_done 回调应传 turn_id。"""
        from app.api.routers.chat_agent import clarify_action

        source = inspect.getsource(clarify_action)

        cb_pos = source.find("_on_clarify_done")
        if cb_pos == -1:
            pytest.skip("_on_clarify_done 不存在")
        after_cb = source[cb_pos:]
        publish_pos = after_cb.find("sse_bus.publish")
        if publish_pos == -1:
            pytest.skip("sse_bus.publish 不存在 in callback")
        after_publish = after_cb[publish_pos:]
        has_turn_id = "turn_id" in after_publish[:300]
        assert has_turn_id, "BUG：/clarify 的 workflow_error 回调不传 turn_id。"

    def test_storyboard_callback_has_turn_id(self):
        """/confirm-storyboard 的 _on_storyboard_done 回调应传 turn_id。"""
        from app.api.routers.chat_agent import confirm_storyboard_action

        source = inspect.getsource(confirm_storyboard_action)

        cb_pos = source.find("_on_storyboard_done")
        if cb_pos == -1:
            pytest.skip("_on_storyboard_done 不存在")
        after_cb = source[cb_pos:]
        publish_pos = after_cb.find("sse_bus.publish")
        if publish_pos == -1:
            pytest.skip("sse_bus.publish 不存在 in callback")
        after_publish = after_cb[publish_pos:]
        has_turn_id = "turn_id" in after_publish[:300]
        assert has_turn_id, "BUG：/confirm-storyboard 的 workflow_error 回调不传 turn_id。"


class TestStep42ResumeSeedsFullHistory:
    """暂停续跑必须回灌 loop_state["messages"] 的完整历史。

    背景（实锤死锁）：git clone 撞 Guardian 暂停 → 用户批准 → /confirm-guardian
    只喂一句 observation 就重新 run()，模型看不到自己上一轮发过什么命令、
    收到过什么报错，只能重新决策 → 重发同一条命令 → 再次撞 Guardian →
    二次暂停 → 循环。修法：run(seed_messages=...) 原样沿用暂停前的对话。
    """

    def test_run_accepts_seed_messages(self):
        from app.engine.reactor.session import ReactorSession

        sig = inspect.signature(ReactorSession.run)
        assert (
            "seed_messages" in sig.parameters
        ), "BUG：ReactorSession.run 不接受 seed_messages，续跑无法回灌历史。"

    def test_seed_messages_short_circuits_initial_messages(self):
        from app.engine.reactor.session import ReactorSession

        source = inspect.getsource(ReactorSession.run)
        seed_pos = source.find("if seed_messages:")
        assert seed_pos != -1, "BUG：run() 未对 seed_messages 做分支处理。"
        # seed 分支必须排在 context_manager / 全新构建之前，否则历史被覆盖
        ctx_pos = source.find("elif context_manager is not None:")
        assert ctx_pos != -1 and seed_pos < ctx_pos, (
            "BUG：seed_messages 分支必须在 context_manager 分支之前，"
            "否则暂停前的历史会被重新构建的 messages 覆盖。"
        )

    def test_confirm_guardian_passes_seed_messages(self):
        """/confirm-guardian 续跑必须把落盘历史传回 session.run。"""
        from app.api.routers.chat_agent import confirm_guardian_action

        source = inspect.getsource(confirm_guardian_action)
        assert 'seed_messages=state.get("messages")' in source.replace("'", '"'), (
            "BUG：/confirm-guardian 续跑未回灌 loop_state['messages']，"
            "模型会丢失上一轮的工具调用记录并重复调用同一条命令。"
        )

    def test_all_resume_endpoints_pass_seed_messages(self):
        """四个续跑出口（confirm / confirm-guardian / clarify 两分支）都要回灌。"""
        from app.api.routers.chat_agent import (
            clarify_action,
            confirm_action,
            confirm_guardian_action,
        )

        for fn in (confirm_action, confirm_guardian_action, clarify_action):
            src = inspect.getsource(fn).replace("'", '"')
            assert (
                'seed_messages=state.get("messages")' in src
            ), f"BUG：{fn.__name__} 的续跑未回灌 loop_state['messages']。"
