"""三层澄清体系集成测试——模拟真实 ReactorSession 澄清→续跑流程。

不依赖 LLM API，用 mock LLM 验证端到端信号流。
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agents.clarification_schema import (
    ClarifyFieldMeta,
    ClarifyOption,
    ClarifyWhen,
)
from app.engine.governance.guardian import GuardianPolicy
from app.engine.reactor.session import ReactorSession
from app.engine.schemas import Permission


def _make_fake_skill():
    skill = MagicMock()
    skill.name = "compose_card_deck"
    skill.node_type = "produce"
    skill.trigger_words = ["做图文卡片", "知识卡片"]
    # MagicMock 会自动补齐任意属性并返回 MagicMock，会让 build_skill_doc 的
    # "\n".join 拿到非字符串；显式声明这两个字段，保持与真实技能同型。
    skill.usage_hint = ""
    skill.input_schema = None
    skill.clarify_meta = {
        "recipe_id": ClarifyFieldMeta(
            hint="选择骨架类型",
            when=ClarifyWhen.missing,
            options=[
                ClarifyOption(label="干货清单", value="bullet_list"),
                ClarifyOption(label="步骤教程", value="step_by_step"),
            ],
        ),
        "theme_id": ClarifyFieldMeta(
            hint="选择视觉风格",
            when=ClarifyWhen.missing,
            options=[
                ClarifyOption(label="简约白", value="minimal_white"),
                ClarifyOption(label="暗色科技", value="dark_tech"),
            ],
        ),
    }
    return skill


class TestReactorSessionClarificationFlow:
    """模拟 ReactorSession.run() 的澄清流程。"""

    @pytest.mark.asyncio
    async def test_pre_flight_layer3_triggers_clarification(self):
        """Pre-flight 阶段 Layer 3 触发 → 返回 _awaiting_clarification 信号。"""
        skill = _make_fake_skill()

        mock_llm = AsyncMock()
        sse_bus = AsyncMock()
        sse_bus.publish = AsyncMock()

        session = ReactorSession(
            llm=mock_llm,
            skills=[skill],
            allowed_permissions=set(Permission),
            sse_bus=sse_bus,
            workflow_id="test_wf",
        )
        session._guardian._policies["compose_card_deck"] = GuardianPolicy(
            tool_name="compose_card_deck",
            auto_allow=True,
            risk_level="low",
            needs_clarification=True,
        )

        result = await session.run("帮我做图文卡片")

        assert result.get("_awaiting_clarification") is True
        assert "_loop_state_path" in result
        assert result.get("_clarification_skill") == "compose_card_deck"
        assert "_clarification_batches" in result
        assert len(result["_clarification_batches"]) > 0

        assert sse_bus.publish.call_count > 0
        event_types = [call.args[1] for call in sse_bus.publish.call_args_list]
        assert "agent_clarification_required" in event_types

    @pytest.mark.asyncio
    async def test_pre_flight_no_match_no_clarification(self):
        """Pre-flight 阶段意图不匹配任何技能 → 不弹澄清，走正常循环。"""
        skill = _make_fake_skill()

        mock_llm = AsyncMock()
        mock_llm.stream_chat = AsyncMock(return_value=AsyncMock())
        mock_llm.stream_chat.return_value.__aiter__ = lambda self: iter([
            {"content": json.dumps({"thought": "done", "final": True, "output": {"summary": "ok"}}), "is_final": True, "token_usage": 10}
        ])

        sse_bus = AsyncMock()
        sse_bus.publish = AsyncMock()

        session = ReactorSession(
            llm=mock_llm,
            skills=[skill],
            allowed_permissions=set(Permission),
            sse_bus=sse_bus,
            workflow_id="test_wf",
        )

        async def _fake_stream(messages):
            yield {"content": json.dumps({"thought": "done", "final": True, "output": {"summary": "ok"}}), "is_final": True, "token_usage": 10}

        mock_llm.stream_chat = _fake_stream

        result = await session.run("今天天气怎么样")

        assert result.get("_awaiting_clarification") is not True

    @pytest.mark.asyncio
    async def test_ask_user_question_primitive_in_dispatch(self):
        """LLM 调 ask_user_question → _dispatch 返回澄清信号 → 循环 break。"""
        skill = _make_fake_skill()

        call_count = 0

        async def _fake_stream(messages):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield {
                    "content": json.dumps({
                        "thought": "用户没指定骨架，我先问一下",
                        "tool_calls": [{
                            "name": "ask_user_question",
                            "arguments": {
                                "title": "创作偏好确认",
                                "skill": "compose_card_deck",
                                "questions": [{
                                    "id": "q_0",
                                    "question": "选择骨架类型",
                                    "type": "single_choice",
                                    "options": [
                                        {"label": "干货清单", "value": "bullet_list"},
                                        {"label": "步骤教程", "value": "step_by_step"},
                                    ],
                                }],
                            },
                        }],
                        "final": False,
                    }),
                    "is_final": True,
                    "token_usage": 50,
                }
            else:
                yield {
                    "content": json.dumps({"thought": "done", "final": True, "output": {"summary": "ok"}}),
                    "is_final": True,
                    "token_usage": 10,
                }

        mock_llm = AsyncMock()
        mock_llm.stream_chat = _fake_stream

        sse_bus = AsyncMock()
        sse_bus.publish = AsyncMock()

        session = ReactorSession(
            llm=mock_llm,
            skills=[skill],
            allowed_permissions=set(Permission),
            sse_bus=sse_bus,
            workflow_id="test_wf_layer1",
        )

        result = await session.run("做图文卡片")

        assert result.get("_awaiting_clarification") is True
        assert result.get("_clarification_prompt") == "创作偏好确认"
        assert "_loop_state_path" in result

    @pytest.mark.asyncio
    async def test_resume_with_clarification_result_no_re_clarify(self):
        """续跑时 context 含 clarification_result → Layer 2 不重复弹。"""
        skill = _make_fake_skill()

        async def _fake_stream(messages):
            yield {
                "content": json.dumps({
                    "thought": "用户已确认偏好，直接执行",
                    "tool_calls": [{
                        "name": "skill_call",
                        "arguments": {
                            "skill": "compose_card_deck",
                            "arguments": {
                                "recipe_id": "bullet_list",
                                "theme_id": "minimal_white",
                            },
                        },
                    }],
                    "final": False,
                }),
                "is_final": True,
                "token_usage": 50,
            }

        mock_llm = AsyncMock()
        mock_llm.stream_chat = _fake_stream

        sse_bus = AsyncMock()
        sse_bus.publish = AsyncMock()

        session = ReactorSession(
            llm=mock_llm,
            skills=[skill],
            allowed_permissions=set(Permission),
            sse_bus=sse_bus,
            workflow_id="test_wf_resume",
        )

        context_with_answers = {
            "clarification_result": {
                "recipe_id": "bullet_list",
                "theme_id": "minimal_white",
            },
            "recipe_id": "bullet_list",
            "theme_id": "minimal_white",
        }

        result = await session.run(
            "[observation] 用户确认了创作偏好: recipe_id=bullet_list, theme_id=minimal_white\n\n请继续执行原始请求：做图文卡片",
            context=context_with_answers,
        )

        assert result.get("_awaiting_clarification") is not True


class TestLoopStateRoundTrip:
    """验证 loop_state save → load → resume 完整流程。"""

    def test_save_load_preserves_clarified_answers(self):
        from app.engine.reactor.loop_state import (
            cleanup_loop_state,
            load_loop_state,
            save_loop_state,
        )

        state = {
            "messages": [
                {"role": "system", "content": "system prompt"},
                {"role": "user", "content": "做图文卡片"},
                {"role": "assistant", "content": json.dumps({"thought": "需要澄清", "tool_calls": [], "final": False})},
                {"role": "user", "content": "[observation] 用户确认了创作偏好: recipe_id=bullet_list"},
            ],
            "iteration": 1,
            "context_snapshot": {
                "workflow_id": "test_wf",
                "clarification_result": {"recipe_id": "bullet_list"},
            },
            "pending_skill_call": {
                "name": "compose_card_deck",
                "arguments": {"recipe_id": "bullet_list"},
            },
            "clarified_answers": {"recipe_id": "bullet_list"},
        }

        path = save_loop_state(state)
        loaded = load_loop_state(path)

        assert loaded["iteration"] == 1
        assert loaded["clarified_answers"]["recipe_id"] == "bullet_list"
        assert loaded["context_snapshot"]["clarification_result"]["recipe_id"] == "bullet_list"
        assert loaded["pending_skill_call"]["name"] == "compose_card_deck"

        cleanup_loop_state(path)

    def test_clarification_timeout_check(self):
        import time

        from app.engine.reactor.loop_state import check_clarification_timeout

        state_fresh = {"_saved_at": time.time()}
        assert check_clarification_timeout(state_fresh) == "ok"

        state_expired = {"_saved_at": time.time() - 7200}
        assert check_clarification_timeout(state_expired) == "expired"

        state_already = {"_clarification_expired": True}
        assert check_clarification_timeout(state_already) == "expired"
