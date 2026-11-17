"""ReAct Loop 六轮诊断 — P0 双重锁死锁 + Guardian 信号吞没 + 熔断器零调用 + SSE 终态。

Step 26: loop_state._get_lock 使用 RLock（防止双重获取死锁）
Step 27: Guardian needs_confirm → needs_confirmation 字段名匹配
Step 28: _call_llm 接入熔断器（allow_request / record_success / record_failure）
Step 29: agent_loop_done 加入 SSE _TERMINAL_TYPES
Step 30: 后台任务 add_done_callback 发 workflow_error SSE 事件
"""

from __future__ import annotations

import inspect
import threading

import pytest

# ============================================================================
# Step 26: 双重锁死锁 — _get_lock 应返回 RLock
# ============================================================================


class TestStep26DoubleLockDeadlock:
    """loop_state._get_lock 返回的锁必须是可重入的（RLock）。

    根因：/clarify 和 /confirm-storyboard 在 API 层 acquire 锁，
    然后调用 load_loop_state / save_loop_state，这些函数内部
    也 acquire 同一把锁。threading.Lock 不可重入 → 100% 死锁。
    """

    def test_get_lock_returns_rlock(self):
        """_get_lock 返回的锁实例必须是 threading.RLock。"""
        from app.engine.reactor.loop_state import _get_lock

        lock = _get_lock("__test_rlock__")
        # RLock 实例的类名是 _RLock，且允许同一线程多次 acquire
        assert type(lock).__name__ in ("RLock", "_RLock"), (
            f"BUG：_get_lock 返回 {type(lock).__name__}，不可重入。"
            "/clarify 和 /confirm-storyboard 在 API 层 acquire 后，"
            "load_loop_state / save_loop_state 内部再次 acquire 同一把锁 → 死锁。"
        )

    def test_rlock_allows_reentrant_acquire(self):
        """验证 RLock 允许同一线程多次 acquire。"""
        from app.engine.reactor.loop_state import _get_lock

        lock = _get_lock("__test_reentrant__")
        lock.acquire()
        try:
            lock.acquire()  # 同一线程第二次 acquire — RLock 不会死锁
            lock.release()
        finally:
            lock.release()

    def test_lock_type_annotation_is_rlock(self):
        """_get_lock 的返回类型注解应为 threading.RLock。"""
        from app.engine.reactor.loop_state import _get_lock

        source = inspect.getsource(_get_lock)
        has_rlock_annotation = "RLock" in source
        assert has_rlock_annotation, (
            "_get_lock 返回类型注解应标注为 threading.RLock，" "便于静态类型检查和代码阅读。"
        )


# ============================================================================
# Step 27: Guardian needs_confirm 信号字段名
# ============================================================================


class TestStep27GuardianNeedsConfirmField:
    """_dispatch 返回 needs_confirmation（不是 needs_confirm）。

    根因：_dispatch 中 Guardian NEEDS_CONFIRM 分支返回 needs_confirm=True，
    但主循环检查 obs.get("needs_confirmation")。字段名不匹配 →
    Guardian 安全闸门形同虚设。
    """

    def test_dispatch_returns_needs_confirmation(self):
        """_dispatch 的 Guardian NEEDS_CONFIRM 分支应返回 needs_confirmation。"""
        from app.engine.reactor.session import ReactorSession

        source = inspect.getsource(ReactorSession._dispatch)

        # 找 Guardian NEEDS_CONFIRM 分支
        if "needs_confirm" not in source and "needs_confirmation" not in source:
            pytest.skip("Guardian NEEDS_CONFIRM 分支不存在")

        # 检查返回值中用 needs_confirmation（不是 needs_confirm）
        has_needs_confirmation = (
            '"needs_confirmation"' in source or "'needs_confirmation'" in source
        )
        assert has_needs_confirmation, (
            "BUG：_dispatch 返回 needs_confirm=True，但主循环检查 "
            "obs.get('needs_confirmation')。字段名不匹配 → "
            "Guardian 安全闸门形同虚设，高危操作无法暂停循环。"
        )

    def test_main_loop_checks_needs_confirmation(self):
        """主循环应检查 needs_confirmation 字段。"""
        from app.engine.reactor.session import ReactorSession

        source = inspect.getsource(ReactorSession.run)

        has_check = '"needs_confirmation"' in source or "'needs_confirmation'" in source
        assert has_check, (
            "主循环应检查 obs.get('needs_confirmation') 来响应 " "Guardian 的确认请求。"
        )

    def test_no_needs_confirm_in_dispatch_return(self):
        """_dispatch 中不应有 needs_confirm（旧字段名）。"""
        from app.engine.reactor.session import ReactorSession

        source = inspect.getsource(ReactorSession._dispatch)

        # 检查是否有旧的 needs_confirm 字段（排除注释）
        code_lines = [line for line in source.split("\n") if not line.strip().startswith("#")]
        code = "\n".join(code_lines)
        has_old_field = '"needs_confirm"' in code or "'needs_confirm'" in code
        assert not has_old_field, (
            "代码中仍有 needs_confirm 字段。应统一为 needs_confirmation " "以匹配主循环的检查。"
        )


# ============================================================================
# Step 28: _call_llm 接入熔断器
# ============================================================================


class TestStep28CircuitBreakerIntegration:
    """_call_llm 应接入 LLM 熔断器。

    根因：llm_circuit.py 定义了完整的状态机（CLOSED/OPEN/HALF_OPEN），
    但 _call_llm 从未调用 allow_request / record_success / record_failure。
    熔断器永远 CLOSED，连续失败时每个请求都重试。
    """

    def test_call_llm_checks_circuit(self):
        """_call_llm 入口应检查熔断器 allow_request。"""
        from app.engine.reactor.session import ReactorSession

        source = inspect.getsource(ReactorSession._call_llm)

        has_allow_request = "allow_request" in source
        assert has_allow_request, (
            "BUG：_call_llm 未检查熔断器 allow_request()。"
            "LLM 连续失败时，每个新请求都通过入口检查 → 反复报错。"
        )

    def test_call_llm_records_success(self):
        """_call_llm 成功时应调 record_success。"""
        from app.engine.reactor.session import ReactorSession

        source = inspect.getsource(ReactorSession._call_llm)

        has_record_success = "record_success" in source
        assert has_record_success, (
            "BUG：_call_llm 成功时未调 record_success()。" "熔断器永远不知道 LLM 恢复正常。"
        )

    def test_call_llm_records_failure(self):
        """_call_llm GIVE_UP 时应调 record_failure。"""
        from app.engine.reactor.session import ReactorSession

        source = inspect.getsource(ReactorSession._call_llm)

        has_record_failure = "record_failure" in source
        assert has_record_failure, (
            "BUG：_call_llm GIVE_UP 时未调 record_failure()。"
            "熔断器永远不知道 LLM 持续失败，不会转为 OPEN。"
        )

    def test_circuit_open_returns_final_json(self):
        """熔断器 OPEN 时 _call_llm 应记录警告并尝试探测（而非直接拒绝）。"""
        from app.engine.reactor.session import ReactorSession

        source = inspect.getsource(ReactorSession._call_llm)

        allow_pos = source.find("allow_request")
        if allow_pos == -1:
            pytest.fail("allow_request 不存在")
        after_allow = source[allow_pos:]
        has_warning = "circuit breaker OPEN" in after_allow[:500]
        assert has_warning, "熔断器 OPEN 时应记录警告日志，便于运维排查。"


# ============================================================================
# Step 29: agent_loop_done 加入 SSE _TERMINAL_TYPES
# ============================================================================


class TestStep29AgentLoopDoneTerminal:
    """agent_loop_done 应在 SSE _TERMINAL_TYPES 中。

    根因：/confirm 和 /clarify 路径发布 agent_loop_done 后，
    SSE 流不会自动关闭 → 连接泄漏直到心跳超时（~120s）。
    """

    def test_agent_loop_done_is_terminal(self):
        """agent_loop_done 应在 _TERMINAL_TYPES 中。"""
        from app.services.sse_bus import _TERMINAL_TYPES

        assert "agent_loop_done" in _TERMINAL_TYPES, (
            "BUG：agent_loop_done 不在 _TERMINAL_TYPES 中。"
            "SSE 流收到 agent_loop_done 后不会自动关闭 → 连接泄漏。"
        )

    def test_workflow_error_is_terminal(self):
        """workflow_error 也应在 _TERMINAL_TYPES 中（后台任务异常时发）。"""
        from app.services.sse_bus import _TERMINAL_TYPES

        assert "workflow_error" in _TERMINAL_TYPES, (
            "workflow_error 应在 _TERMINAL_TYPES 中，" "确保后台任务异常时 SSE 流能正确关闭。"
        )


# ============================================================================
# Step 30: 后台任务 add_done_callback 发 workflow_error SSE
# ============================================================================


class TestStep30BackgroundTaskSseOnError:
    """后台任务异常时 add_done_callback 应发 workflow_error SSE 事件。

    根因：之前的 add_done_callback 只调 t.exception() 防止警告，
    但不发 SSE 事件 → 前端永远不知道任务失败。
    """

    def test_confirm_callback_publishes_workflow_error(self):
        """/confirm 的 add_done_callback 应发 workflow_error SSE。"""
        from app.api.routers.chat_agent import confirm_action

        source = inspect.getsource(confirm_action)

        has_workflow_error = "workflow_error" in source
        assert has_workflow_error, (
            "BUG：/confirm 的 add_done_callback 不发 workflow_error SSE。"
            "后台任务异常时前端永远看不到终态事件。"
        )

    def test_clarify_callback_publishes_workflow_error(self):
        """/clarify 的 add_done_callback 应发 workflow_error SSE。"""
        from app.api.routers.chat_agent import clarify_action

        source = inspect.getsource(clarify_action)

        has_workflow_error = "workflow_error" in source
        assert has_workflow_error, (
            "BUG：/clarify 的 add_done_callback 不发 workflow_error SSE。"
            "后台任务异常时前端永远看不到终态事件。"
        )

    def test_storyboard_callback_publishes_workflow_error(self):
        """/confirm-storyboard 的 add_done_callback 应发 workflow_error SSE。"""
        from app.api.routers.chat_agent import confirm_storyboard_action

        source = inspect.getsource(confirm_storyboard_action)

        has_workflow_error = "workflow_error" in source
        assert has_workflow_error, (
            "BUG：/confirm-storyboard 的 add_done_callback 不发 workflow_error SSE。"
            "后台任务异常时前端永远看不到终态事件。"
        )

    def test_callbacks_use_sse_bus_publish(self):
        """callback 中应使用 sse_bus.publish 发 SSE 事件。"""
        from app.api.routers.chat_agent import confirm_action

        source = inspect.getsource(confirm_action)

        has_sse_publish = "sse_bus.publish" in source
        assert has_sse_publish, (
            "callback 中应使用 sse_bus.publish 发 SSE 事件，" "确保前端能收到后台任务失败的通知。"
        )
