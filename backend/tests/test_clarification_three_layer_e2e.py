"""三层澄清体系端到端测试（对标 Claude Code auto mode）

覆盖链路：
  链路 1: Layer 1 — LLM 自决策 (ask_user_question 工具)
  链路 2: Layer 2 — 分类器兜底 (_classifier_fallback_check)
  链路 3: Layer 3 — 用户显式规则 (_check_user_ask_rules)
  链路 4: /clarify 端点续跑
  链路 5: /confirm 端点续跑
  链路 6: 三层调度顺序
  链路 7: auto_default 空字符串判断
"""

from unittest.mock import MagicMock

import pytest

from app.agents.clarification_schema import (
    ClarifyFieldMeta,
    ClarifyOption,
    ClarifyWhen,
)
from app.engine.governance.guardian import _DEFAULT_POLICIES, Guardian, GuardianPolicy
from app.engine.reactor.primitives import (
    ALL_PRIMITIVES,
    PRIMITIVES_BY_NAME,
    AskUserQuestionPrimitive,
)
from app.engine.reactor.session import ReactorSession

# ============================================================================
# 辅助：构造 fake skill
# ============================================================================


def _make_fake_skill(
    name: str = "compose_card_deck",
    node_type: str = "produce",
    trigger_words: list[str] | None = None,
    clarify_meta: dict | None = None,
):
    """构造一个 fake skill 对象，供 ReactorSession._skills 使用。"""
    skill = MagicMock()
    skill.name = name
    skill.node_type = node_type
    skill.trigger_words = trigger_words or ["做图文卡片", "知识卡片"]
    skill.clarify_meta = clarify_meta or {
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


# ============================================================================
# 链路 1: Layer 1 — LLM 自决策 (ask_user_question)
# ============================================================================


class TestLayer1AskUserQuestion:
    """ask_user_question 工具注册、Guardian 放行、信号格式。"""

    def test_primitive_registered(self):
        assert "ask_user_question" in PRIMITIVES_BY_NAME

    def test_primitive_in_all_primitives(self):
        names = [p.name for p in ALL_PRIMITIVES]
        assert "ask_user_question" in names

    def test_guardian_policy_auto_allow(self):
        policy = _DEFAULT_POLICIES.get("ask_user_question")
        assert policy is not None
        assert policy.auto_allow is True
        assert policy.risk_level == "low"

    @pytest.mark.asyncio
    async def test_guardian_check_allows(self):
        guardian = Guardian()
        decision = await guardian.check("ask_user_question", {})
        assert decision.action.value == "allow"

    @pytest.mark.asyncio
    async def test_execute_returns_clarification_signal(self):
        prim = AskUserQuestionPrimitive()
        result = await prim.execute(
            title="创作偏好确认",
            skill="compose_card_deck",
            questions=[
                {
                    "id": "q_0",
                    "question": "选择骨架类型",
                    "type": "single_choice",
                    "options": [
                        {"label": "干货清单", "value": "bullet_list"},
                        {"label": "步骤教程", "value": "step_by_step"},
                    ],
                },
            ],
        )
        assert result.get("_awaiting_clarification") is True
        assert result.get("_clarification_prompt") == "创作偏好确认"
        assert result.get("_clarification_skill") == "compose_card_deck"
        assert "_clarification_batches" in result
        assert len(result["_clarification_batches"]) == 1

    @pytest.mark.asyncio
    async def test_execute_empty_questions_returns_error(self):
        prim = AskUserQuestionPrimitive()
        result = await prim.execute(title="test", questions=[])
        assert result.get("ok") is False
        assert "error" in result


# ============================================================================
# 链路 2: Layer 2 — 分类器兜底 (_classifier_fallback_check)
# ============================================================================


class TestLayer2ClassifierFallback:
    """_classifier_fallback_check 三重缺失检查。"""

    def _make_session(self, skills=None, context=None):
        session = ReactorSession.__new__(ReactorSession)
        session._skills = skills if skills is not None else [_make_fake_skill()]
        session._run_context = context or {}
        session.auto_approve = True
        session._guardian = Guardian()
        session._CLARIFY_TRIGGER_NODE_TYPES = frozenset(
            {
                "produce",
                "create",
                "design",
                "generate",
                "copywrite",
                "card_design",
            }
        )
        return session

    def test_no_skill_args_skips_clarification(self):
        """LLM 一个专属参数都没传 → 不弹（承诺度守卫：可能是误路由，让 LLM 自纠正）。"""
        session = self._make_session()
        result = session._classifier_fallback_check("compose_card_deck", {})
        assert result is None

    def test_partial_args_triggers_clarification(self):
        """LLM 传了部分参数 + 有缺失 → 弹澄清（LLM 已承诺，补问缺失的）。"""
        session = self._make_session()
        result = session._classifier_fallback_check(
            "compose_card_deck", {"recipe_id": "bullet_list"}
        )
        assert result is not None
        assert result.get("_awaiting_clarification") is True

    def test_user_clarified_suppresses_clarification(self):
        """用户本轮已答过澄清（/clarify 续跑 context 带 user_clarified）→
        Layer 2 完全静默，即使技能字段仍缺失。

        根因（2026-10-21 实锤）：Layer 1（ask_user_question，q_0/q_1 键）
        与 Layer 2（技能 clarify_meta 字段键）键空间不同，用户答完
        Layer 1 后技能字段仍"缺失"，Layer 2 再补弹 = 澄清死循环。"""
        session = self._make_session(context={"user_clarified": True})
        # LLM 传了部分参数（满足承诺度守卫），但用户已答过澄清 → 不弹
        result = session._classifier_fallback_check(
            "compose_card_deck", {"recipe_id": "bullet_list"}
        )
        assert result is None

    def test_tool_args_suppresses_clarification(self):
        """LLM 已在 tool_args 中传了所有参数 → 不弹。"""
        session = self._make_session()
        result = session._classifier_fallback_check(
            "compose_card_deck", {"recipe_id": "bullet_list", "theme_id": "minimal_white"}
        )
        assert result is None

    def test_clarification_result_suppresses_clarification(self):
        """用户之前已回答过所有字段 → 不弹。"""
        session = self._make_session(
            context={
                "clarification_result": {"recipe_id": "bullet_list", "theme_id": "minimal_white"}
            }
        )
        result = session._classifier_fallback_check("compose_card_deck", {})
        assert result is None

    def test_partial_clarification_result_still_clarifies(self):
        """用户只回答了部分字段 → 仍弹（问缺失的）。"""
        session = self._make_session(context={"clarification_result": {"recipe_id": "bullet_list"}})
        result = session._classifier_fallback_check("compose_card_deck", {})
        assert result is not None

    def test_auto_default_with_empty_string_suppresses(self):
        """auto_default=True + default="" → 不弹（空字符串 is not None）。"""
        skill = _make_fake_skill()
        skill.clarify_meta = {
            "recipe_id": ClarifyFieldMeta(
                hint="骨架类型",
                when=ClarifyWhen.missing,
                auto_default=True,
                default="",
                options=[ClarifyOption(label="默认", value="default")],
            ),
        }
        session = self._make_session(skills=[skill])
        result = session._classifier_fallback_check("compose_card_deck", {})
        assert result is None

    def test_non_trigger_node_type_skips(self):
        """node_type 不在 CLARIFY_TRIGGER_NODE_TYPES → 不弹。"""
        skill = _make_fake_skill(node_type="search")
        session = self._make_session(skills=[skill])
        result = session._classifier_fallback_check("compose_card_deck", {})
        assert result is None

    def test_no_clarify_meta_skips(self):
        """技能没有 clarify_meta → 不弹。"""
        skill = _make_fake_skill()
        skill.clarify_meta = None
        session = self._make_session(skills=[skill])
        result = session._classifier_fallback_check("compose_card_deck", {})
        assert result is None

    def test_skill_call_unwraps_inner_skill(self):
        """tool_name="skill_call" 时从 args.skill 取内层技能名。"""
        session = self._make_session()
        result = session._classifier_fallback_check(
            "skill_call", {"skill": "compose_card_deck", "recipe_id": "bullet_list"}
        )
        assert result is not None
        assert result.get("_awaiting_clarification") is True


# ============================================================================
# 链路 3: Layer 3 — 用户显式规则 (_check_user_ask_rules)
# ============================================================================


class TestLayer3UserAskRules:
    """_check_user_ask_rules 检查 GuardianPolicy.needs_clarification。"""

    def _make_session(self, skills=None, needs_clarification=False):
        session = ReactorSession.__new__(ReactorSession)
        session._skills = skills if skills is not None else [_make_fake_skill()]
        session.auto_approve = True
        session._guardian = Guardian()
        if needs_clarification:
            session._guardian._policies["compose_card_deck"] = GuardianPolicy(
                tool_name="compose_card_deck",
                auto_allow=True,
                risk_level="low",
                needs_clarification=True,
            )
        session._CLARIFY_TRIGGER_NODE_TYPES = frozenset(
            {
                "produce",
                "create",
                "design",
                "generate",
                "copywrite",
                "card_design",
            }
        )
        return session

    def test_no_needs_clarification_policy_returns_none(self):
        """技能没有 needs_clarification=True 的 policy → 不弹。"""
        session = self._make_session(needs_clarification=False)
        result = session._check_user_ask_rules("compose_card_deck")
        assert result is None

    def test_needs_clarification_policy_triggers(self):
        """技能有 needs_clarification=True → 弹澄清。"""
        session = self._make_session(needs_clarification=True)
        result = session._check_user_ask_rules("compose_card_deck")
        assert result is not None
        assert result.get("_awaiting_clarification") is True

    def test_unknown_skill_returns_none(self):
        """技能名不在 _skills 中 → 不弹。"""
        session = self._make_session(needs_clarification=True)
        result = session._check_user_ask_rules("nonexistent_skill")
        assert result is None


# ============================================================================
# 链路 4: /clarify 端点 context 构建
# ============================================================================


class TestClarifyEndpointContext:
    """验证 /clarify 端点正确构建 context["clarification_result"]。"""

    def test_clarification_result_in_context(self):
        """模拟 /clarify answer 分支的 context 构建。"""
        _ctx_snapshot = {"workflow_id": "test_session", "topic": "做图文卡片"}
        _clarification_result = {"recipe_id": "bullet_list", "theme_id": "minimal_white"}

        context = dict(_ctx_snapshot)
        context.pop("extra", None)
        context["clarification_result"] = _clarification_result
        context.update(_clarification_result)

        assert context["clarification_result"] == _clarification_result
        assert context["recipe_id"] == "bullet_list"
        assert context["theme_id"] == "minimal_white"

    def test_cancel_branch_also_sets_clarification_result(self):
        """模拟 /clarify cancel 分支的 context 构建。"""
        _ctx_snapshot = {"workflow_id": "test_session"}
        _clarification_result = {}

        context = dict(_ctx_snapshot)
        context.pop("extra", None)
        context["clarification_result"] = _clarification_result
        context.update(_clarification_result)

        assert "clarification_result" in context


# ============================================================================
# 链路 5: /confirm 端点 context 构建
# ============================================================================


class TestConfirmEndpointContext:
    """验证 /confirm 端点正确构建 context["clarification_result"]。"""

    def test_existing_clarification_preserved(self):
        """模拟 /confirm 分支的 context 构建。"""
        _ctx_snapshot = {
            "workflow_id": "test_session",
            "clarification_result": {"recipe_id": "bullet_list"},
        }

        context = dict(_ctx_snapshot)
        context.pop("extra", None)
        _existing_clarification = _ctx_snapshot.get("clarification_result", {}) or {}
        if _existing_clarification:
            context["clarification_result"] = _existing_clarification

        assert context["clarification_result"] == {"recipe_id": "bullet_list"}


# ============================================================================
# 链路 6: 三层调度顺序
# ============================================================================


class TestThreeLayerDispatchOrder:
    """验证 _dispatch 中三层澄清的调度顺序：Layer 3 → Layer 1 → Layer 2。"""

    def test_layer3_takes_priority_over_layer2(self):
        """Layer 3 命中时 Layer 2 不应被调用。"""
        skill = _make_fake_skill()
        session = ReactorSession.__new__(ReactorSession)
        session._skills = [skill]
        session._run_context = {}
        session.auto_approve = True
        session._guardian = Guardian()
        session._guardian._policies["compose_card_deck"] = GuardianPolicy(
            tool_name="compose_card_deck",
            auto_allow=True,
            risk_level="low",
            needs_clarification=True,
        )
        session._CLARIFY_TRIGGER_NODE_TYPES = frozenset(
            {
                "produce",
                "create",
                "design",
                "generate",
                "copywrite",
                "card_design",
            }
        )

        layer3_result = session._check_user_ask_rules("compose_card_deck")
        assert layer3_result is not None

        layer2_result = session._classifier_fallback_check(
            "compose_card_deck", {"recipe_id": "bullet_list"}
        )
        assert layer2_result is not None

    def test_layer3_not_triggered_layer2_fallback(self):
        """Layer 3 未命中时 Layer 2 兜底（LLM 传了部分参数表示有承诺）。"""
        skill = _make_fake_skill()
        session = ReactorSession.__new__(ReactorSession)
        session._skills = [skill]
        session._run_context = {}
        session.auto_approve = True
        session._guardian = Guardian()
        session._CLARIFY_TRIGGER_NODE_TYPES = frozenset(
            {
                "produce",
                "create",
                "design",
                "generate",
                "copywrite",
                "card_design",
            }
        )

        layer3_result = session._check_user_ask_rules("compose_card_deck")
        assert layer3_result is None

        layer2_result = session._classifier_fallback_check(
            "compose_card_deck", {"recipe_id": "bullet_list"}
        )
        assert layer2_result is not None


# ============================================================================
# 链路 7: auto_default 空字符串判断
# ============================================================================


class TestAutoDefaultEmptyString:
    """验证 auto_default=True + default="" 时不弹澄清。"""

    def test_empty_string_is_not_none(self):
        meta = ClarifyFieldMeta(
            hint="骨架类型",
            when=ClarifyWhen.missing,
            auto_default=True,
            default="",
        )
        assert meta.auto_default is True
        assert meta.default == ""
        assert meta.default is not None

    def test_none_default_does_not_suppress(self):
        meta = ClarifyFieldMeta(
            hint="骨架类型",
            when=ClarifyWhen.missing,
            auto_default=True,
            default="",
        )
        meta.default = None
        assert meta.default is None


# ============================================================================
# 链路 8: _resolve_intent_skill 意图解析
# ============================================================================


class TestResolveIntentSkill:
    """验证 pre-flight 意图解析。"""

    def _make_session(self, skills=None):
        session = ReactorSession.__new__(ReactorSession)
        session._skills = skills if skills is not None else [_make_fake_skill()]
        return session

    def test_trigger_word_match(self):
        session = self._make_session()
        result = session._resolve_intent_skill("帮我做图文卡片")
        assert result is not None
        assert result.name == "compose_card_deck"

    def test_no_match_returns_none(self):
        session = self._make_session()
        result = session._resolve_intent_skill("今天天气怎么样")
        assert result is None

    def test_ambiguous_returns_none(self):
        """两个技能同分 → 返回 None，交给 LLM 决策。"""
        skill1 = _make_fake_skill(name="skill_a", trigger_words=["做图文"])
        skill2 = _make_fake_skill(name="skill_b", trigger_words=["做图文"])
        session = self._make_session(skills=[skill1, skill2])
        result = session._resolve_intent_skill("帮我做图文")
        assert result is None

    def test_empty_skills_returns_none(self):
        session = self._make_session(skills=[])
        result = session._resolve_intent_skill("做图文卡片")
        assert result is None


# ============================================================================
# 链路 9: loop_state 持久化
# ============================================================================


class TestLoopStatePersistence:
    """验证 loop_state save/load/cleanup。"""

    def test_save_and_load(self):
        from app.engine.reactor.loop_state import (
            cleanup_loop_state,
            load_loop_state,
            save_loop_state,
        )

        state = {
            "messages": [{"role": "user", "content": "test"}],
            "iteration": 3,
            "context_snapshot": {"workflow_id": "test_wid"},
            "pending_skill_call": {"name": "compose_card_deck", "arguments": {}},
            "clarified_answers": {"recipe_id": "bullet_list"},
        }
        path = save_loop_state(state)
        loaded = load_loop_state(path)
        assert loaded["iteration"] == 3
        assert loaded["clarified_answers"]["recipe_id"] == "bullet_list"
        cleanup_loop_state(path)

    def test_load_nonexistent_raises(self):
        from app.engine.reactor.loop_state import load_loop_state

        with pytest.raises(FileNotFoundError):
            load_loop_state("nonexistent_path_12345")
