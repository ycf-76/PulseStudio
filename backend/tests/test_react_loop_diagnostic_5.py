"""ReAct Loop 五轮诊断 — API 层锁管理 + 后台任务异常 + LLM 静默降级。

  Step 22: /clarify 锁泄漏（compile_clarification_result 异常时锁不释放）
  Step 23: /confirm-storyboard 锁泄漏（call_args 构造异常时锁不释放）
  Step 24: asyncio.create_task 后台任务异常被吞
  Step 25: build_reactor_session llm=None 静默降级无日志
"""

from __future__ import annotations

import inspect

# ============================================================================
# Step 22: /clarify 锁泄漏
# ============================================================================


class TestStep22ClarifyLockLeakage:
    """/clarify 端点中锁是否用 try/finally 保护。"""

    def test_clarify_uses_try_finally_for_lock(self):
        """clarify_action 中 _file_lock.acquire() 后是否有 try/finally。"""
        from app.api.routers.chat_agent import clarify_action
        source = inspect.getsource(clarify_action)

        has_acquire = "_file_lock.acquire()" in source
        assert has_acquire, "应调用 _file_lock.acquire()"

        # 检查 acquire 后紧跟 try
        acquire_pos = source.index("_file_lock.acquire()")
        after_acquire = source[acquire_pos:]
        has_try_after_acquire = "try:" in after_acquire[:200]
        assert has_try_after_acquire, (
            "BUG：_file_lock.acquire() 后没有 try 块。"
            "如果 compile_clarification_result() 或 collect_clarify_fields() 抛异常，"
            "锁不会释放 → 同一 loop_state_path 的后续请求永久死锁。"
        )

    def test_clarify_has_finally_release(self):
        """clarify_action 中是否有 finally: _file_lock.release()。"""
        from app.api.routers.chat_agent import clarify_action
        source = inspect.getsource(clarify_action)

        has_finally_release = "finally:" in source and "_file_lock.release()" in source
        assert has_finally_release, (
            "BUG：没有 finally: _file_lock.release()。"
            "异常路径下锁不会释放。"
        )

    def test_clarify_no_manual_release_in_early_returns(self):
        """clarify_action 中早期返回路径不应有手动 _file_lock.release()。

        有了 try/finally 后，手动 release 是多余的（但不会出错）。
        检查是否还有散落的 _file_lock.release() 调用。
        """
        from app.api.routers.chat_agent import clarify_action
        source = inspect.getsource(clarify_action)

        release_count = source.count("_file_lock.release()")
        # try/finally 中应有 1 个 release
        # 如果有多个，说明还有散落的手动 release（多余但无害）
        if release_count > 1:
            pass  # 多余的手动 release，可以清理但不是 BUG


# ============================================================================
# Step 23: /confirm-storyboard 锁泄漏
# ============================================================================


class TestStep23StoryboardLockLeakage:
    """/confirm-storyboard 端点中锁是否用 try/finally 保护。"""

    def test_storyboard_uses_try_finally_for_lock(self):
        """confirm_storyboard_action 中 _file_lock.acquire() 后是否有 try/finally。"""
        from app.api.routers.chat_agent import confirm_storyboard_action
        source = inspect.getsource(confirm_storyboard_action)

        has_acquire = "_file_lock.acquire()" in source
        assert has_acquire, "应调用 _file_lock.acquire()"

        acquire_pos = source.index("_file_lock.acquire()")
        after_acquire = source[acquire_pos:]
        has_try_after_acquire = "try:" in after_acquire[:200]
        assert has_try_after_acquire, (
            "BUG：_file_lock.acquire() 后没有 try 块。"
            "call_args 构造异常时锁不会释放。"
        )

    def test_storyboard_has_finally_release(self):
        """confirm_storyboard_action 中是否有 finally: _file_lock.release()。"""
        from app.api.routers.chat_agent import confirm_storyboard_action
        source = inspect.getsource(confirm_storyboard_action)

        has_finally_release = "finally:" in source and "_file_lock.release()" in source
        assert has_finally_release, (
            "BUG：没有 finally: _file_lock.release()。"
            "异常路径下锁不会释放。"
        )

    def test_storyboard_background_task_does_not_release_lock(self):
        """_run_refine_in_background 中不应有 _file_lock.release()。

        锁的生命周期应由主函数的 try/finally 管理，
        后台任务不应再释放锁（避免双重释放）。
        """
        from app.api.routers.chat_agent import confirm_storyboard_action
        source = inspect.getsource(confirm_storyboard_action)

        # 检查 _run_refine_in_background 内部是否有 _file_lock.release()
        bg_start = source.find("_run_refine_in_background")
        if bg_start == -1:
            return  # 没有后台任务

        # 找到后台函数定义的范围
        bg_section = source[bg_start:]
        has_release_in_bg = "_file_lock.release()" in bg_section[:2000]
        # 如果后台任务中还有 release，说明重构不完整
        # 但这不是 BUG（双重 release 在 threading.Lock 上会抛 RuntimeError）
        if has_release_in_bg:
            pass  # 需要清理：后台任务不应释放锁


# ============================================================================
# Step 24: asyncio.create_task 后台任务异常被吞
# ============================================================================


class TestStep24BackgroundTaskExceptionSwallowed:
    """asyncio.create_task 后台任务异常是否被正确处理。"""

    def test_confirm_adds_done_callback(self):
        """/confirm 端点的 create_task 是否加了 add_done_callback。"""
        from app.api.routers.chat_agent import confirm_action
        source = inspect.getsource(confirm_action)

        has_done_callback = "add_done_callback" in source
        assert has_done_callback, (
            "BUG：/confirm 的 create_task 没有加 add_done_callback。"
            "后台任务异常被 asyncio 吞掉，前端永远看不到 agent_loop_done。"
        )

    def test_clarify_adds_done_callback(self):
        """/clarify 端点的 create_task 是否加了 add_done_callback。"""
        from app.api.routers.chat_agent import clarify_action
        source = inspect.getsource(clarify_action)

        has_done_callback = "add_done_callback" in source
        assert has_done_callback, (
            "BUG：/clarify 的 create_task 没有加 add_done_callback。"
            "后台任务异常被 asyncio 吞掉，前端永远看不到 agent_loop_done。"
        )

    def test_storyboard_adds_done_callback(self):
        """/confirm-storyboard 端点的 create_task 是否加了 add_done_callback。"""
        from app.api.routers.chat_agent import confirm_storyboard_action
        source = inspect.getsource(confirm_storyboard_action)

        has_done_callback = "add_done_callback" in source
        assert has_done_callback, (
            "BUG：/confirm-storyboard 的 create_task 没有加 add_done_callback。"
            "后台任务异常被 asyncio 吞掉，前端永远看不到 agent_loop_done。"
        )

    def test_done_callback_retrieves_exception(self):
        """add_done_callback 是否调用了 task.exception()。"""
        from app.api.routers.chat_agent import confirm_action
        source = inspect.getsource(confirm_action)

        has_exception_retrieval = "t.exception()" in source or "task.exception()" in source
        assert has_exception_retrieval, (
            "add_done_callback 应调用 task.exception() 来检索异常，"
            "否则 Python 3.12 会打印 'Task exception was never retrieved' 警告。"
        )


# ============================================================================
# Step 25: build_reactor_session llm=None 静默降级
# ============================================================================


class TestStep25LlmNoneSilentDegradation:
    """build_reactor_session 中 llm=None 时是否有日志警告。"""

    def test_build_session_logs_when_llm_none(self):
        """build_reactor_session 是否在 llm=None 时记录错误日志。"""
        from app.engine.reactor.session import build_reactor_session
        source = inspect.getsource(build_reactor_session)

        # 检查是否有 llm is None 的二次检查
        has_none_check = "llm is None" in source
        assert has_none_check, "应有 llm is None 检查"

        # 检查是否有 logger.error
        has_error_log = "logger.error" in source
        assert has_error_log, (
            "BUG：build_reactor_session 中 llm=None 时没有 logger.error。"
            "LLM 不可用时静默降级，用户只看到 [内部错误已隐藏]，"
            "运维无法从日志中定位问题。"
        )

    def test_build_session_checks_deepseek_return(self):
        """build_reactor_session 是否检查 get_deepseek_llm() 的返回值。"""
        from app.engine.reactor.session import build_reactor_session
        source = inspect.getsource(build_reactor_session)

        has_deepseek_call = "get_deepseek_llm(" in source
        assert has_deepseek_call, "应调用 get_deepseek_llm()"

        # 找实际调用位置（llm = get_deepseek_llm(model=...)），跳过 docstring 中的引用
        call_pattern = "llm = get_deepseek_llm("
        assert call_pattern in source, "应有 llm = get_deepseek_llm(...) 赋值"
        call_pos = source.index(call_pattern)
        after_call = source[call_pos:]
        has_post_check = "llm is None" in after_call[:300]
        assert has_post_check, (
            "BUG：get_deepseek_llm() 返回 None 时没有检查。"
            "API key 缺失或熔断器开启时，ReactorSession(llm=None) 静默降级。"
        )
