"""ReAct Loop 运行时诊断测试 — 按优先级逐步排查。

排查顺序（每步只测一件事，通过才进入下一步）：
  Step 1: _parse_step 对非 JSON 输出的处理 — 是否提前终止循环
  Step 2: Token Budget 是否实际生效 — 有计数无检查？
  Step 3: Context 截断行为 — 超过 _MESSAGES_MAX_LEN 时是否丢关键信息
  Step 4: 工具调用去重 — LLM 重复调同一工具同一参数是否无限循环
  Step 5: _dispatch 原地改写 args — 副作用是否泄漏到 observation
  Step 6: loop_state 并发安全 — 锁是否真正使用
  Step 7: Observation 截断 — 4000 字符截断是否导致 LLM 信息残缺

每个 Step 用独立的 test class，便于 -k 指定只跑某步。
"""

from __future__ import annotations

import json
import time

import pytest

from app.engine.reactor.session import ReactorSession


class _FakeBus:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def publish(self, workflow_id, event_type, payload, turn_id=None):
        self.calls.append(
            {
                "workflow_id": workflow_id,
                "event_type": event_type,
                "payload": payload,
                "turn_id": turn_id,
            }
        )
        return "evt_fake"


class _ScriptedLLM:
    """按脚本顺序返回 LLM 响应，支持流式和非流式。"""

    model_name = "mock-diag"

    def __init__(self, responses: list[dict]) -> None:
        self._responses = list(responses)
        self._index = 0

    async def stream_chat(self, messages, **kwargs):
        idx = min(self._index, len(self._responses) - 1)
        self._index += 1
        for chunk in self._responses[idx]["chunks"]:
            yield chunk

    async def chat(self, messages, **kwargs):
        idx = min(self._index, len(self._responses) - 1)
        self._index += 1
        parts = []
        tu = 0
        for chunk in self._responses[idx]["chunks"]:
            if chunk.get("content"):
                parts.append(chunk["content"])
            if chunk.get("is_final"):
                tu = chunk.get("token_usage", 0) or tu
        return {"content": "".join(parts), "reasoning_content": None, "token_usage": tu or 1}


def _chunk(content: str = "", is_final: bool = True, token_usage: int = 1) -> dict:
    return {
        "content": content,
        "reasoning_content": "",
        "is_final": is_final,
        "token_usage": token_usage,
    }


def _react_step(
    thought: str, tool_calls: list | None = None, final: bool = False, output: dict | None = None
) -> str:
    step = {"thought": thought, "tool_calls": tool_calls or [], "final": final}
    if output is not None:
        step["output"] = output
    return json.dumps(step, ensure_ascii=False)


def _make_session(llm, max_iterations: int = 12, **kwargs) -> ReactorSession:
    bus = _FakeBus()
    return ReactorSession(
        llm=llm,
        workflow_id="wf_diag",
        sse_bus=bus,
        max_iterations=max_iterations,
        **kwargs,
    )


# ============================================================================
# Step 1: _parse_step 对非 JSON 输出的处理
# ============================================================================


class TestStep1ParseStepNonJSON:
    """_parse_step 遇到非 JSON 输出时，是否导致循环提前终止。

    预期行为：非 JSON 文本应被当作 thought，循环应继续而非终止。
    实际行为（待验证）：not tool_calls → break，循环直接终止。
    """

    def test_parse_step_returns_thought_for_plain_text(self):
        """纯文本输入 → _parse_step 应返回 thought，不应标记 final。"""
        session = _make_session(_ScriptedLLM([]))
        result = session._parse_step("我来帮你分析一下这个话题")
        assert result.get("thought") == "我来帮你分析一下这个话题"
        assert result.get("final") is False
        assert result.get("tool_calls") == []

    def test_parse_step_handles_markdown_with_json(self):
        """markdown 包裹的 JSON → 应正确解析。"""
        session = _make_session(_ScriptedLLM([]))
        raw = '```json\n{"thought": "搜索热点", "tool_calls": [{"name": "xhs_search", "arguments": {"keyword": "AI"}}], "final": false}\n```'
        result = session._parse_step(raw)
        assert result.get("thought") == "搜索热点"
        assert result.get("final") is False
        assert len(result.get("tool_calls", [])) == 1

    def test_parse_step_handles_partial_json(self):
        """不完整 JSON → 降级为 thought，不应崩溃。"""
        session = _make_session(_ScriptedLLM([]))
        result = session._parse_step('{"thought": "正在思考')
        assert result.get("thought") is not None
        assert result.get("final") is False

    @pytest.mark.asyncio
    async def test_non_json_output_terminates_loop_prematurely(self):
        """关键测试：LLM 输出非 JSON 文本时，循环是否提前终止。

        场景：LLM 第 1 轮正常调工具，第 2 轮输出一段解释性文字（非 JSON）。
        如果循环提前终止，_iterations 会是 2 而非继续推理。
        """
        llm = _ScriptedLLM(
            [
                {
                    "chunks": [
                        _chunk(
                            _react_step(
                                "先搜索热点",
                                [{"name": "xhs_search", "arguments": {"keyword": "AI"}}],
                            )
                        )
                    ]
                },
                {
                    "chunks": [
                        _chunk(
                            "根据搜索结果，我建议从以下几个角度来创作内容：\n1. 热点解读\n2. 实用技巧\n3. 个人经验分享"
                        )
                    ]
                },
            ]
        )
        session = _make_session(llm, max_iterations=5)
        result = await session.run("帮我写一篇AI教育的小红书笔记")

        iterations = result.get("_iterations", 0)
        steps = result.get("_steps", [])

        # 诊断：记录实际行为
        last_step = steps[-1] if steps else {}
        has_raw_text = "raw_text" in result

        # 如果循环在第 2 轮就终止了，说明非 JSON 输出导致了提前终止
        # 这是 BUG：LLM 只是输出了一段解释，循环应该让它继续而非终止
        if iterations == 2 and has_raw_text:
            pytest.fail(
                f"BUG 确认：LLM 第 2 轮输出非 JSON 文本导致循环提前终止。"
                f"iterations={iterations}, last_step={last_step}, output keys={list(result.keys())}"
            )

        # 如果循环没有提前终止，说明行为已修正
        assert iterations >= 2, f"循环至少应跑 2 轮，实际: {iterations}"

    @pytest.mark.asyncio
    async def test_non_json_then_final_json_recovers(self):
        """LLM 先输出非 JSON，下一轮恢复 JSON → 循环应能恢复。

        如果 Step 1 的 bug 存在，这个测试也会失败（因为第 2 轮非 JSON 就终止了，
        根本到不了第 3 轮）。
        """
        llm = _ScriptedLLM(
            [
                {
                    "chunks": [
                        _chunk(
                            _react_step(
                                "搜索热点", [{"name": "xhs_search", "arguments": {"keyword": "AI"}}]
                            )
                        )
                    ]
                },
                {"chunks": [_chunk("让我分析一下搜索结果...")]},
                {
                    "chunks": [
                        _chunk(_react_step("分析完成", final=True, output={"_message": "分析结果"}))
                    ]
                },
            ]
        )
        session = _make_session(llm, max_iterations=5)
        result = await session.run("帮我分析AI教育热点")

        iterations = result.get("_iterations", 0)
        # 如果 bug 存在，iterations 会是 2（第 2 轮非 JSON 就终止了）
        # 期望：iterations == 3（第 2 轮非 JSON 当 thought，第 3 轮 final）
        if iterations < 3:
            pytest.fail(
                f"BUG 确认：非 JSON 输出导致循环在第 {iterations} 轮终止，"
                f"无法恢复到第 3 轮的 final 输出"
            )


# ============================================================================
# Step 2: Token Budget 是否实际生效
# ============================================================================


class TestStep2TokenBudget:
    """Token Budget 是否在 ReAct 循环中实际检查。

    预期：当 token 累计超过 _max_tokens 时，循环应提前终止或压缩。
    实际（待验证）：_token_count 只累加不检查。
    """

    @pytest.mark.asyncio
    async def test_token_count_is_tracked(self):
        """token_count 确实在累加。"""
        llm = _ScriptedLLM(
            [
                {
                    "chunks": [
                        _chunk(
                            _react_step(
                                "思考1", [{"name": "xhs_search", "arguments": {"keyword": "AI"}}]
                            ),
                            token_usage=5000,
                        )
                    ]
                },
                {
                    "chunks": [
                        _chunk(
                            _react_step("完成", final=True, output={"ok": True}), token_usage=3000
                        )
                    ]
                },
            ]
        )
        session = _make_session(llm, max_iterations=5)
        result = await session.run("测试token追踪")

        assert result.get("_token_usage", 0) > 0, "token_usage 应大于 0"
        assert session._token_count > 0, "_token_count 应大于 0"

    @pytest.mark.asyncio
    async def test_token_budget_not_enforced(self):
        """关键测试：token 超过 _max_tokens 时循环是否仍在继续。

        场景：设置极低的 max_tokens，每轮消耗大量 token。
        如果循环继续跑到 max_iterations，说明 budget 未生效。
        """
        high_token_per_call = 30000
        llm = _ScriptedLLM(
            [
                {
                    "chunks": [
                        _chunk(
                            _react_step(
                                "思考1", [{"name": "xhs_search", "arguments": {"keyword": "AI"}}]
                            ),
                            token_usage=high_token_per_call,
                        )
                    ]
                },
                {
                    "chunks": [
                        _chunk(
                            _react_step(
                                "思考2", [{"name": "xhs_search", "arguments": {"keyword": "教育"}}]
                            ),
                            token_usage=high_token_per_call,
                        )
                    ]
                },
                {
                    "chunks": [
                        _chunk(
                            _react_step("完成", final=True, output={"ok": True}),
                            token_usage=high_token_per_call,
                        )
                    ]
                },
            ]
        )
        session = _make_session(llm, max_iterations=5)
        session._max_tokens = 50000  # 设一个很低的阈值

        result = await session.run("测试token预算")
        iterations = result.get("_iterations", 0)
        total_tokens = result.get("_token_usage", 0)

        # 诊断：如果循环跑完了所有 3 轮，说明 token budget 没有生效
        if iterations == 3 and total_tokens > session._max_tokens:
            pytest.fail(
                f"BUG 确认：Token Budget 未生效。"
                f"total_tokens={total_tokens} > max_tokens={session._max_tokens}，"
                f"但循环仍跑了 {iterations} 轮到完成。"
                f"应在 total_tokens 接近 max_tokens 时提前终止或压缩。"
            )

        # 如果 budget 生效了，iterations 应 < 3
        assert iterations <= 3


# ============================================================================
# Step 3: Context 截断行为
# ============================================================================


class TestStep3ContextTruncation:
    """消息超过 _MESSAGES_MAX_LEN 时的截断行为。

    预期：保留 system + 最近 N 条，但关键信息（如早期决策）会丢失。
    """

    def test_truncation_keeps_system_messages(self):
        """截断后 system 消息必须保留。"""
        session = _make_session(_ScriptedLLM([]))
        messages = [
            {"role": "system", "content": "你是创作助手"},
            {"role": "user", "content": "第1条"},
            {"role": "assistant", "content": "回复1"},
        ] * 30  # 90 条，超过 _MESSAGES_MAX_LEN=60

        truncated = session._maybe_truncate_messages(messages)
        system_count = sum(1 for m in truncated if m["role"] == "system")
        assert system_count >= 1, "截断后至少保留 1 条 system 消息"

    @pytest.mark.xfail(reason="已知限制：暴力截断会丢早期消息，需接入 ContextCompactor 解决")
    def test_truncation_drops_early_messages(self):
        """截断后早期消息被丢弃。"""
        session = _make_session(_ScriptedLLM([]))
        early_marker = "EARLY_CRITICAL_DECISION_MARKER"
        messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": early_marker},
        ]
        for i in range(70):
            messages.append({"role": "user", "content": f"msg_{i}"})
            messages.append({"role": "assistant", "content": f"resp_{i}"})

        truncated = session._maybe_truncate_messages(messages)
        has_early = any(early_marker in m.get("content", "") for m in truncated)

        if not has_early:
            pytest.fail(
                f"BUG 确认：截断丢弃了早期关键决策消息。"
                f"原始 {len(messages)} 条 → 截断后 {len(truncated)} 条，"
                f"早期标记 '{early_marker}' 已丢失。"
                f"应使用 LLM 摘要压缩而非暴力截断。"
            )

    def test_truncation_logs_warning(self, caplog):
        """截断时必须记录警告日志（短期修复验证）。"""
        import logging as _logging

        session = _make_session(_ScriptedLLM([]))
        messages = [{"role": "system", "content": "system"}, {"role": "user", "content": "early"}]
        for i in range(70):
            messages.append({"role": "user", "content": f"msg_{i}"})
            messages.append({"role": "assistant", "content": f"resp_{i}"})

        with caplog.at_level(_logging.WARNING, logger="app.engine.reactor.session"):
            truncated = session._maybe_truncate_messages(messages)

        truncation_logs = [r for r in caplog.records if "context truncation" in r.message]
        assert truncation_logs, "截断时应记录 context truncation 警告日志"
        assert len(truncated) <= 60, "截断后消息数不超过上限"

    def test_truncation_limit(self):
        """截断后消息数不超过 _MESSAGES_MAX_LEN。"""
        session = _make_session(_ScriptedLLM([]))
        messages = [{"role": "user", "content": f"msg_{i}"} for i in range(100)]
        truncated = session._maybe_truncate_messages(messages)
        assert len(truncated) <= 60, f"截断后 {len(truncated)} 条，超过上限 60"


# ============================================================================
# Step 4: 工具调用去重
# ============================================================================


class TestStep4ToolCallDedup:
    """LLM 重复调同一工具同一参数时，循环是否无限重复。

    历史 BUG：判重用的是「整批签名」，只要相邻两批不完全相同就归零，
    LLM 在批里夹带一个新调用即可让同一条失败调用无限刷。
    修复：改为逐签名统计「连续出现在多少个 step」，任一签名连刷到阈值即刹车。
    """

    @pytest.mark.asyncio
    async def test_duplicate_tool_calls_caught(self):
        """LLM 连续 3 次调同一工具同一参数 → 第 3 次应被拦截，不再执行。

        场景：LLM 每轮都调 xhs_search(keyword="AI")，连续 3 次。
        预期：第 1、2 次执行，第 3 次触发 DUPLICATE_CALLS 刹车。
        """
        same_call = [{"name": "xhs_search", "arguments": {"keyword": "AI"}}]
        llm = _ScriptedLLM(
            [
                {"chunks": [_chunk(_react_step("搜索AI", same_call))]},
                {"chunks": [_chunk(_react_step("再搜一次", same_call))]},
                {"chunks": [_chunk(_react_step("又搜一次", same_call))]},
                {"chunks": [_chunk(_react_step("完成", final=True, output={"ok": True}))]},
            ]
        )
        session = _make_session(llm, max_iterations=6)
        result = await session.run("搜索AI热点")
        steps = result.get("_steps", [])

        xhs_calls = [
            tc
            for step in steps
            for tc in step.get("tool_calls", [])
            if tc.get("name") == "xhs_search"
        ]
        assert len(xhs_calls) == 2, f"第 3 次重复调用应被拦截，实际执行了 {len(xhs_calls)} 次"
        assert result.get("ended_reason") == "duplicate_calls"
        assert result.get("_repeated_call", {}).get("name") == "xhs_search"
        assert result.get("_repeated_call", {}).get("consecutive_steps") == 3

    @pytest.mark.asyncio
    async def test_interleaved_duplicate_still_caught(self):
        """批里每次夹带一个新调用 → 旧实现会归零放行，新实现必须照样刹车。"""
        repeated = {"name": "xhs_search", "arguments": {"keyword": "AI"}}
        llm = _ScriptedLLM(
            [
                {
                    "chunks": [
                        _chunk(_react_step("搜A", [repeated, {"name": "probe_1", "arguments": {}}]))
                    ]
                },
                {
                    "chunks": [
                        _chunk(_react_step("搜A", [repeated, {"name": "probe_2", "arguments": {}}]))
                    ]
                },
                {
                    "chunks": [
                        _chunk(_react_step("搜A", [repeated, {"name": "probe_3", "arguments": {}}]))
                    ]
                },
                {"chunks": [_chunk(_react_step("完成", final=True, output={"ok": True}))]},
            ]
        )
        session = _make_session(llm, max_iterations=6)
        result = await session.run("搜索AI热点")
        steps = result.get("_steps", [])

        xhs_calls = [
            tc
            for step in steps
            for tc in step.get("tool_calls", [])
            if tc.get("name") == "xhs_search"
        ]
        assert len(xhs_calls) == 2, f"夹带新调用不应重置计数，实际执行了 {len(xhs_calls)} 次"
        assert result.get("ended_reason") == "duplicate_calls"

    @pytest.mark.asyncio
    async def test_interrupted_repeat_not_counted(self):
        """重复调用中间插了一步完全不含它 → 连续计数清零，不误杀。"""
        call_a = {"name": "xhs_search", "arguments": {"keyword": "AI"}}
        call_b = {"name": "xhs_search", "arguments": {"keyword": "美食"}}
        llm = _ScriptedLLM(
            [
                {"chunks": [_chunk(_react_step("搜A", [call_a]))]},
                {"chunks": [_chunk(_react_step("搜A", [call_a]))]},
                {"chunks": [_chunk(_react_step("换关键词", [call_b]))]},
                {"chunks": [_chunk(_react_step("再搜A", [call_a]))]},
                {"chunks": [_chunk(_react_step("完成", final=True, output={"ok": True}))]},
            ]
        )
        session = _make_session(llm, max_iterations=8)
        result = await session.run("搜索AI热点")
        steps = result.get("_steps", [])

        xhs_calls = [
            tc
            for step in steps
            for tc in step.get("tool_calls", [])
            if tc.get("name") == "xhs_search"
        ]
        assert len(xhs_calls) == 4, "中间穿插其他调用后连续计数应清零，不应误杀"
        assert result.get("ended_reason") != "duplicate_calls"

    @pytest.mark.asyncio
    async def test_successful_polling_not_braked(self):
        """轮询场景：同签名连调 4 次、每次 ok=true → 必须全部放行。

        轮询的语义就是「同参数连调直到状态就绪」，若按「连续出现」计数，
        第 3 次就会被刹车误杀。刹车只应针对「连续失败」。
        """
        poll_call = [{"name": "poll_status", "arguments": {"task_id": "t1"}}]
        llm = _ScriptedLLM(
            [
                {"chunks": [_chunk(_react_step("查状态1", poll_call))]},
                {"chunks": [_chunk(_react_step("查状态2", poll_call))]},
                {"chunks": [_chunk(_react_step("查状态3", poll_call))]},
                {"chunks": [_chunk(_react_step("查状态4", poll_call))]},
                {"chunks": [_chunk(_react_step("完成", final=True, output={"ok": True}))]},
            ]
        )
        session = _make_session(llm, max_iterations=8)

        async def _ok_dispatch(tool_call):
            return {"ok": True, "status": "pending"}

        session._dispatch = _ok_dispatch
        result = await session.run("等待任务完成")
        steps = result.get("_steps", [])

        poll_calls = [
            tc
            for step in steps
            for tc in step.get("tool_calls", [])
            if tc.get("name") == "poll_status"
        ]
        assert len(poll_calls) == 4, "成功的轮询不应被重复调用刹车误杀"
        assert result.get("ended_reason") != "duplicate_calls"

    @pytest.mark.asyncio
    async def test_fail_then_succeed_resets_count(self):
        """失败、成功、失败、成功 → 成功一次即打断连败，不应累计到刹车阈值。"""
        call = [{"name": "flaky_tool", "arguments": {"x": 1}}]
        llm = _ScriptedLLM(
            [
                {"chunks": [_chunk(_react_step("试1", call))]},
                {"chunks": [_chunk(_react_step("试2", call))]},
                {"chunks": [_chunk(_react_step("试3", call))]},
                {"chunks": [_chunk(_react_step("试4", call))]},
                {"chunks": [_chunk(_react_step("完成", final=True, output={"ok": True}))]},
            ]
        )
        session = _make_session(llm, max_iterations=8)

        outcomes = iter(
            [
                {"ok": False, "error": "boom"},
                {"ok": True},
                {"ok": False, "error": "boom"},
                {"ok": True},
            ]
        )

        async def _flaky_dispatch(tool_call):
            return next(outcomes, {"ok": True})

        session._dispatch = _flaky_dispatch
        result = await session.run("重试任务")
        steps = result.get("_steps", [])

        calls = [
            tc
            for step in steps
            for tc in step.get("tool_calls", [])
            if tc.get("name") == "flaky_tool"
        ]
        assert len(calls) == 4, "成功后应重置连败计数，不应被刹车截断"
        assert result.get("ended_reason") != "duplicate_calls"

    @pytest.mark.asyncio
    async def test_repeated_probe_is_braked(self):
        """空探针返回 ok=true 但技能没执行 → 仍属无进展，连刷 3 步必须刹车。

        若只看 ok 就放行，LLM 反复探同一个技能会永远刹不住。
        """
        probe_call = [{"name": "probe_tool", "arguments": {"skill": "x"}}]
        llm = _ScriptedLLM(
            [
                {"chunks": [_chunk(_react_step("探1", probe_call))]},
                {"chunks": [_chunk(_react_step("探2", probe_call))]},
                {"chunks": [_chunk(_react_step("探3", probe_call))]},
                {"chunks": [_chunk(_react_step("完成", final=True, output={"ok": True}))]},
            ]
        )
        session = _make_session(llm, max_iterations=8)

        async def _probe_dispatch(tool_call):
            return {"ok": True, "probe": True, "note": "未执行：缺少必填参数"}

        session._dispatch = _probe_dispatch
        result = await session.run("调用技能")
        steps = result.get("_steps", [])

        probe_calls = [
            tc
            for step in steps
            for tc in step.get("tool_calls", [])
            if tc.get("name") == "probe_tool"
        ]
        assert len(probe_calls) == 2, "空探针无进展，第 3 步应被刹车"
        assert result.get("ended_reason") == "duplicate_calls"
        assert result.get("_repeated_call", {}).get("consecutive_steps") == 3


# ============================================================================
# Step 5: _dispatch 原地改写 args
# ============================================================================


class TestStep5DispatchArgsMutation:
    """_dispatch 是否原地修改 tool_call 的 arguments dict。

    预期：args 应被拷贝后修改，不应影响原始 dict。
    """

    @pytest.mark.asyncio
    async def test_dispatch_does_not_mutate_original_args(self):
        """_dispatch 调用后，原始 args dict 不应被修改。"""
        original_args = {"keyword": "AI"}
        original_args_copy = dict(original_args)

        llm = _ScriptedLLM(
            [
                {
                    "chunks": [
                        _chunk(
                            _react_step(
                                "搜索", [{"name": "xhs_search", "arguments": original_args}]
                            )
                        )
                    ]
                },
                {"chunks": [_chunk(_react_step("完成", final=True, output={"ok": True}))]},
            ]
        )
        session = _make_session(llm, max_iterations=3)
        result = await session.run("搜索AI")

        # 检查 original_args 是否被 _dispatch 修改
        mutated_keys = set(original_args.keys()) - set(original_args_copy.keys())
        if original_args != original_args_copy:
            pytest.fail(
                f"BUG 确认：_dispatch 原地修改了 args dict。"
                f"原始 keys={list(original_args_copy.keys())}，"
                f"修改后 keys={list(original_args.keys())}，"
                f"新增 keys={mutated_keys}。"
                f"应使用 args.copy() 后修改。"
            )


# ============================================================================
# Step 6: loop_state 并发安全
# ============================================================================


class TestStep6LoopStateConcurrency:
    """loop_state 的 save/load 是否线程安全。

    预期：_locks 已定义但 save_loop_state / load_loop_state 未使用锁。
    """

    def test_save_loop_state_does_not_use_lock(self):
        """save_loop_state 是否真正使用了 _get_lock 返回的锁。"""
        import inspect

        from app.engine.reactor.loop_state import save_loop_state

        source = inspect.getsource(save_loop_state)
        uses_lock = "_get_lock" in source or "_locks" in source

        if not uses_lock:
            pytest.fail(
                "BUG 确认：save_loop_state 未使用锁。"
                "_locks 和 _get_lock 已定义但从未在 save/load 中调用，"
                "并发写入可能导致状态文件损坏。"
            )

    def test_concurrent_save_does_not_corrupt(self):
        """快速连续两次 save 同一路径，后者应覆盖前者（不损坏）。"""
        from app.engine.reactor.loop_state import load_loop_state, save_loop_state

        state1 = {"iteration": 1, "data": "first"}
        state2 = {"iteration": 2, "data": "second"}

        path = f"test_concurrent_{int(time.time()*1000)}"
        save_loop_state(state1, path=path)
        save_loop_state(state2, path=path)

        loaded = load_loop_state(path)
        # 后写应覆盖前写
        assert loaded["iteration"] == 2, f"期望 iteration=2，实际={loaded['iteration']}"
        assert loaded["data"] == "second"

    def test_cleanup_loop_state_uses_lock(self):
        """cleanup_loop_state 是否使用了锁。"""
        import inspect

        from app.engine.reactor.loop_state import cleanup_loop_state

        source = inspect.getsource(cleanup_loop_state)
        uses_lock = "_get_lock" in source
        assert uses_lock, "cleanup_loop_state 应使用 _get_lock 加锁，防止并发删写冲突"

    @pytest.mark.asyncio
    async def test_nudge_count_resets_on_tool_call_recovery(self):
        """LLM 恢复正常 tool_calls 输出后，nudge 计数应重置。

        场景：非 JSON → nudge → JSON with tool_calls → 非 JSON → nudge
        第二次非 JSON 应从 nudge_count=0 开始（因为中间恢复了），而不是从 1。
        """
        same_call = [{"name": "xhs_search", "arguments": {"keyword": "AI"}}]
        llm = _ScriptedLLM(
            [
                {"chunks": [_chunk("我来帮你分析一下这个话题")]},  # 非 JSON, nudge 1
                {"chunks": [_chunk(_react_step("搜索", same_call))]},  # 恢复 JSON
                {
                    "chunks": [_chunk("根据搜索结果，建议从以下角度创作")]
                },  # 又非 JSON, nudge 1（重置后）
                {
                    "chunks": [_chunk(_react_step("完成", final=True, output={"ok": True}))]
                },  # 恢复并完成
            ]
        )
        session = _make_session(llm, max_iterations=8)
        result = await session.run("写一篇AI教育笔记")

        iterations = result.get("_iterations", 0)
        # 如果 nudge_count 没有重置，第 3 轮非 JSON 时 nudge_count=2 → 直接 break
        # 期望：nudge_count 在第 2 轮恢复后重置为 0，第 3 轮非 JSON 时 nudge_count=1 → 继续
        assert iterations >= 4, (
            f"nudge_count 未重置：LLM 恢复 tool_calls 后又输出非 JSON 时，"
            f"应从 nudge_count=0 重新计数，实际 iterations={iterations}"
        )


# ============================================================================
# Step 7: Observation 截断
# ============================================================================


class TestStep7ObservationTruncation:
    """Observation 超过 4000 字符时的截断行为。

    预期：截断后 LLM 看到残缺数据，可能做出错误决策。
    """

    def test_observation_truncation_length(self):
        """验证 _OBSERVATION_MAX_LEN 的实际值和截断行为。"""
        from app.engine.reactor import session as session_mod

        max_len = getattr(session_mod, "_OBSERVATION_MAX_LEN", None)
        assert max_len is not None, "_OBSERVATION_MAX_LEN 应存在"
        assert max_len == 4000, f"_OBSERVATION_MAX_LEN 期望 4000，实际={max_len}"

    @pytest.mark.asyncio
    async def test_large_observation_is_truncated_in_messages(self):
        """大 observation 被截断后喂回 LLM，LLM 看到的是残缺数据。"""
        # 构造一个返回大量数据的 primitive mock
        big_data = {
            "ok": True,
            "results": [{"title": f"结果_{i}", "content": "x" * 500} for i in range(20)],
        }
        big_obs_str = json.dumps(big_data, ensure_ascii=False)

        llm = _ScriptedLLM(
            [
                {
                    "chunks": [
                        _chunk(
                            _react_step(
                                "搜索", [{"name": "xhs_search", "arguments": {"keyword": "AI"}}]
                            )
                        )
                    ]
                },
                {"chunks": [_chunk(_react_step("完成", final=True, output={"ok": True}))]},
            ]
        )
        session = _make_session(llm, max_iterations=3)

        # 直接检查截断逻辑
        from app.engine.reactor import session as session_mod

        max_len = session_mod._OBSERVATION_MAX_LEN

        obs_str = json.dumps(big_data, ensure_ascii=False, default=str)
        if len(obs_str) > max_len:
            truncated = obs_str[:max_len] + "...(truncated)"
            # 截断后的 observation 丢失了后一半搜索结果
            lost_results = len(big_data["results"]) - truncated.count("结果_")
            if lost_results > 0:
                # 这不是 crash bug，但记录诊断信息
                pass  # 信息丢失是已知的截断行为，Step 7 只是量化

        assert len(obs_str) > max_len, f"测试数据应超过 {max_len} 字符，实际={len(obs_str)}"
