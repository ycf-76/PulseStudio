"""ReAct Loop 七轮诊断 — P0-2 Guardian 路由安全漏洞 + P1-5/P1-7/P1-10。

  Step 31: Guardian needs_confirmation 与 storyboard needs_confirmation 区分
  Step 32: 主入口 agent_chat 异常时发 workflow_error SSE
  Step 33: /clarify cancel 路径 cleanup_loop_state 在 finally 中
  Step 34: _build_initial_messages 截断大 context
"""

from __future__ import annotations

import inspect

import pytest

# ============================================================================
# Step 31: Guardian 确认 vs 分镜确认路由区分
# ============================================================================


class TestStep31GuardianVsStoryboardRouting:
    """Guardian needs_confirmation 和技能分镜 needs_confirmation 必须区分。

    根因：主循环 L430 检查 obs.get("needs_confirmation") 后直接走分镜确认路径，
    不区分来源。Guardian 拦截 bash 返回 needs_confirmation=True（无 shots 字段），
    被路由到分镜确认 → pending_skill_call 设为 {name: "bash", arguments: ...} →
    用户点"确认分镜"时 bash 被执行 → 安全漏洞。
    """

    def test_guardian_confirmation_has_dedicated_sse_event(self):
        """Guardian 确认应发 guardian_confirmation_required SSE 事件（不是 storyboard）。"""
        from app.engine.reactor.session import ReactorSession
        source = inspect.getsource(ReactorSession.run)

        has_guardian_event = "guardian_confirmation_required" in source
        assert has_guardian_event, (
            "BUG：Guardian 确认没有专用 SSE 事件。Guardian 拦截的高危操作被路由到"
            "分镜确认路径 → 用户点'确认分镜'时高危操作被执行 → 安全漏洞。"
        )

    def test_storyboard_check_requires_shots_field(self):
        """分镜确认检查应要求 obs 中有 shots 字段。"""
        from app.engine.reactor.session import ReactorSession
        source = inspect.getsource(ReactorSession.run)

        # 找 storyboard confirmation 分支
        sb_pos = source.find("storyboard confirmation signal")
        if sb_pos == -1:
            pytest.skip("storyboard confirmation 分支不存在")
        # 在此之前应有 shots 检查
        before_sb = source[:sb_pos]
        has_shots_check = '"shots"' in before_sb[-500:] or "'shots'" in before_sb[-500:]
        assert has_shots_check, (
            "分镜确认分支应检查 obs 中有 shots 字段，"
            "以区分 Guardian 确认（无 shots）和技能分镜确认（有 shots）。"
        )

    def test_guardian_confirmation_saves_awaiting_guardian(self):
        """Guardian 确认应保存 _awaiting_guardian_confirmation 到 loop_state。"""
        from app.engine.reactor.session import ReactorSession
        source = inspect.getsource(ReactorSession.run)

        has_guardian_awaiting = "_awaiting_guardian_confirmation" in source
        assert has_guardian_awaiting, (
            "Guardian 确认应在 loop_state 中设置 _awaiting_guardian_confirmation，"
            "以便 /confirm-storyboard 端点区分确认类型。"
        )

    def test_guardian_confirmation_not_routed_to_storyboard(self):
        """Guardian 确认不应设置 _awaiting_storyboard_confirmation。"""
        from app.engine.reactor.session import ReactorSession
        source = inspect.getsource(ReactorSession.run)

        # 找 Guardian 确认分支
        guardian_pos = source.find("guardian_confirmation_required")
        if guardian_pos == -1:
            pytest.fail("guardian_confirmation_required 不存在")
        # Guardian 分支内不应有 _awaiting_storyboard_confirmation
        guardian_block = source[guardian_pos:guardian_pos + 800]
        has_storyboard_in_guardian = "_awaiting_storyboard_confirmation" in guardian_block
        assert not has_storyboard_in_guardian, (
            "Guardian 确认分支内不应设置 _awaiting_storyboard_confirmation，"
            "否则高危操作仍会被路由到分镜确认回放路径。"
        )


# ============================================================================
# Step 32: 主入口 agent_chat 异常时发 workflow_error SSE
# ============================================================================


class TestStep32AgentChatExceptionSse:
    """agent.process() 抛异常时应发 workflow_error SSE 事件。

    根因：agent.process() 调用没有 try/except，异常时
    workflow_completed / workflow_error 都不发 → SSE 流不关闭。
    """

    def test_agent_process_in_try_except(self):
        """agent.process() 应在 try/except 中调用。"""
        from app.api.routers.chat_agent import agent_chat
        source = inspect.getsource(agent_chat)

        process_pos = source.find("agent.process(")
        if process_pos == -1:
            pytest.skip("agent.process() 调用不存在")
        before_process = source[:process_pos]
        # 向前找最近的 try:（可能在几行之前）
        last_try_pos = before_process.rfind("try:")
        assert last_try_pos != -1, (
            "BUG：agent.process() 不在 try/except 中。"
            "异常时 workflow_error SSE 事件不发 → SSE 流不关闭。"
        )
        # try: 和 agent.process 之间不应有 except（即 try 仍有效）
        between = before_process[last_try_pos:]
        assert "except" not in between, (
            "agent.process() 应在最近的 try 块内。"
        )

    def test_exception_handler_publishes_workflow_error(self):
        """异常处理应发 workflow_error SSE 事件。"""
        from app.api.routers.chat_agent import agent_chat
        source = inspect.getsource(agent_chat)

        # 找 agent.process 后的 except 块
        process_pos = source.find("agent.process(")
        after_process = source[process_pos:]
        has_workflow_error = "workflow_error" in after_process[:2000]
        assert has_workflow_error, (
            "BUG：agent.process() 异常时不发 workflow_error SSE。"
            "前端 SSE 连接泄漏，直到心跳超时。"
        )


# ============================================================================
# Step 33: /clarify cancel 路径 cleanup_loop_state 在 finally 中
# ============================================================================


class TestStep33ClarifyCancelCleanupInFinally:
    """/clarify cancel 路径中 cleanup_loop_state 应在 finally 中。

    根因：session.run() 抛异常时，cleanup_loop_state 在之后 → 不执行 → 临时文件残留。
    """

    def test_cancel_path_cleanup_in_finally(self):
        """cancel 路径的 cleanup_loop_state 应在 finally 块中。"""
        from app.api.routers.chat_agent import clarify_action
        source = inspect.getsource(clarify_action)

        # 找 cancel 分支
        cancel_pos = source.find('"cancel"')
        if cancel_pos == -1:
            pytest.skip("cancel 分支不存在")

        # 在 cancel 分支中找 cleanup_loop_state
        after_cancel = source[cancel_pos:]
        cleanup_pos = after_cancel.find("cleanup_loop_state")
        if cleanup_pos == -1:
            pytest.fail("cancel 分支中应有 cleanup_loop_state")

        # 检查 cleanup 在 finally 中（向前找 finally）
        before_cleanup = after_cancel[:cleanup_pos]
        has_finally = "finally:" in before_cleanup[-300:]
        assert has_finally, (
            "BUG：cancel 路径的 cleanup_loop_state 不在 finally 中。"
            "session.run() 异常时临时文件残留。"
        )


# ============================================================================
# Step 34: _build_initial_messages 截断大 context
# ============================================================================


class TestStep34BuildContextTruncation:
    """_build_initial_messages 应截断大 context，避免浪费 context window。

    根因：json.dumps(context) 把整个 context 序列化进 system message。
    当 context 含 work_context 长文本时，可能数千 token → 浪费预算。
    """

    def test_has_truncation_logic(self):
        """_build_initial_messages 应有截断逻辑。"""
        from app.engine.reactor.session import ReactorSession
        source = inspect.getsource(ReactorSession._build_initial_messages)

        has_truncation = "truncated" in source.lower()
        assert has_truncation, (
            "BUG：_build_initial_messages 无截断逻辑。"
            "大 context 整体序列化进 system message → 浪费 context window 预算。"
        )

    def test_has_max_chars_constant(self):
        """应有最大字符数常量。"""
        from app.engine.reactor.session import ReactorSession

        has_max = hasattr(ReactorSession, "_CONTEXT_SUMMARY_MAX_CHARS")
        assert has_max, (
            "ReactorSession 应定义 _CONTEXT_SUMMARY_MAX_CHARS 常量，"
            "控制 context system message 的最大长度。"
        )

    @pytest.mark.asyncio
    async def test_large_context_gets_truncated(self):
        """大 context 值应被截断而非完整序列化。"""
        from app.engine.reactor.session import ReactorSession

        session = ReactorSession.__new__(ReactorSession)
        session.system_prompt = "test"
        # __init__ 才会设置的内部状态，__new__ 构造时需手动补齐
        session._prompt_routed = True
        session._system_prompt_explicit = True
        session._skills = []
        session._creation_type = None

        large_context = {
            "work_context": "x" * 5000,
            "small_field": "ok",
        }
        messages = session._build_initial_messages("test intent", large_context)
        # 动态设计块会以独立 system 消息插在 system_prompt 之后，
        # 故不能按固定下标取 context 消息，按前缀查找。
        context_msg = next(
            m["content"] for m in messages if m["content"].startswith("[context]")
        )

        # work_context 应被截断（不会出现 5000 个 x）
        assert "x" * 5000 not in context_msg, (
            "大字段应被截断，不应完整序列化进 system message。"
        )
        assert "truncated" in context_msg, (
            "截断后应有 truncated 标记。"
        )
        # 小字段应完整保留
        assert "small_field" in context_msg, (
            "小字段应完整保留，不应被截断。"
        )
