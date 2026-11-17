"""Unit tests for Receipt Ledger + Output Gate + Execution Gate (hallucination hard guard).

Tests the core behaviors:
1. Receipt Ledger: every tool execution creates an immutable receipt
2. Output Gate: unproven artifact keys are removed from final_output
3. Output Gate: proven artifact keys are preserved and supplemented
4. Execution Gate: zero-execution zero-artifact termination marks _fake_final
"""

from __future__ import annotations

import sys
from pathlib import Path

BENCH_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(BENCH_ROOT.parent))


def test_receipt_ledger_basic():
    """Receipt ledger records each tool call with ok status and artifacts."""
    ledger: list[dict] = []

    obs_ok = True
    obs = {"ok": True, "card_draft": {"schema_version": 1}, "title": "test"}
    tool_name = "compose_card_deck"
    iteration = 1
    step_tool_count = 0

    receipt_artifacts: dict = {}
    if isinstance(obs, dict) and obs_ok:
        for rk in (
            "card_draft",
            "asset_ids",
            "file_path",
            "html_path",
            "image_url",
            "title",
            "pages",
            "deck",
            "schema_version",
        ):
            rv = obs.get(rk)
            if rv is not None:
                receipt_artifacts[rk] = rv

    ledger.append(
        {
            "call_id": f"call_{iteration}_{step_tool_count}",
            "tool": tool_name,
            "ok": obs_ok,
            "artifacts": receipt_artifacts,
            "iter": iteration,
        }
    )

    assert len(ledger) == 1
    assert ledger[0]["ok"] is True
    assert "card_draft" in ledger[0]["artifacts"]
    assert "title" in ledger[0]["artifacts"]
    assert ledger[0]["call_id"] == "call_1_0"
    print("✅ Receipt Ledger basic: records ok=True with artifacts")


def test_receipt_ledger_failed():
    """Failed tool execution creates receipt with ok=False and empty artifacts."""
    ledger: list[dict] = []

    obs_ok = False
    obs = {"ok": False, "error": "missing _user_id"}
    tool_name = "compose_card_deck"
    iteration = 2

    receipt_artifacts: dict = {}
    if isinstance(obs, dict) and obs_ok:
        pass

    ledger.append(
        {
            "call_id": f"call_{iteration}_0",
            "tool": tool_name,
            "ok": obs_ok,
            "artifacts": receipt_artifacts,
            "iter": iteration,
        }
    )

    assert len(ledger) == 1
    assert ledger[0]["ok"] is False
    assert ledger[0]["artifacts"] == {}
    print("✅ Receipt Ledger failed: records ok=False with empty artifacts")


def test_output_gate_removes_unproven():
    """Output Gate removes artifact keys that have no ok=True receipt."""
    ledger = [
        {
            "call_id": "call_1_0",
            "tool": "compose_card_deck",
            "ok": False,
            "artifacts": {},
            "iter": 1,
        },
    ]

    final_output = {
        "title": "test",
        "card_draft": {"schema_version": 1},
        "pages": 3,
        "ended_reason": "completed",
    }

    PRODUCT_ARTIFACT_KEYS = frozenset(
        {
            "card_draft",
            "asset_ids",
            "file_path",
            "html_path",
            "image_url",
            "title",
            "pages",
            "deck",
        }
    )

    proven_artifacts: set = set()
    for rcpt in ledger:
        if rcpt["ok"]:
            proven_artifacts.update(rcpt["artifacts"].keys())

    unproven_keys: set = set()
    for gk in list(final_output.keys()):
        if gk in PRODUCT_ARTIFACT_KEYS and gk not in proven_artifacts:
            unproven_keys.add(gk)
            del final_output[gk]

    assert "card_draft" not in final_output
    assert "pages" not in final_output
    assert "title" not in final_output
    assert "ended_reason" in final_output
    assert unproven_keys == {"card_draft", "pages", "title"}
    print(f"✅ Output Gate: removed unproven keys {unproven_keys}")


def test_output_gate_preserves_proven():
    """Output Gate preserves artifact keys that have ok=True receipt."""
    ledger = [
        {
            "call_id": "call_1_0",
            "tool": "compose_card_deck",
            "ok": True,
            "artifacts": {"card_draft": {"v": 1}, "title": "real"},
            "iter": 1,
        },
    ]

    final_output = {
        "title": "real",
        "card_draft": {"v": 1},
        "ended_reason": "completed",
    }

    PRODUCT_ARTIFACT_KEYS = frozenset(
        {
            "card_draft",
            "asset_ids",
            "file_path",
            "html_path",
            "image_url",
            "title",
            "pages",
            "deck",
        }
    )

    proven_artifacts: set = set()
    for rcpt in ledger:
        if rcpt["ok"]:
            proven_artifacts.update(rcpt["artifacts"].keys())

    unproven_keys: set = set()
    for gk in list(final_output.keys()):
        if gk in PRODUCT_ARTIFACT_KEYS and gk not in proven_artifacts:
            unproven_keys.add(gk)
            del final_output[gk]

    assert "card_draft" in final_output
    assert "title" in final_output
    assert unproven_keys == set()
    print("✅ Output Gate: preserved proven keys {card_draft, title}")


def test_output_gate_type2_hallucination():
    """Type-2 hallucination: LLM calls wrong tool then fabricates card_draft.

    Scenario: LLM calls llm_generate (text only), then claims card_draft
    in final output. Output Gate should remove card_draft because
    llm_generate's receipt has no card_draft artifact.
    """
    ledger = [
        {
            "call_id": "call_1_0",
            "tool": "llm_generate",
            "ok": True,
            "artifacts": {"title": "generated title"},
            "iter": 1,
        },
    ]

    final_output = {
        "title": "generated title",
        "card_draft": {"schema_version": 1, "pages": 3},
        "ended_reason": "completed",
    }

    PRODUCT_ARTIFACT_KEYS = frozenset(
        {
            "card_draft",
            "asset_ids",
            "file_path",
            "html_path",
            "image_url",
            "title",
            "pages",
            "deck",
        }
    )

    proven_artifacts: set = set()
    for rcpt in ledger:
        if rcpt["ok"]:
            proven_artifacts.update(rcpt["artifacts"].keys())

    unproven_keys: set = set()
    for gk in list(final_output.keys()):
        if gk in PRODUCT_ARTIFACT_KEYS and gk not in proven_artifacts:
            unproven_keys.add(gk)
            del final_output[gk]

    assert "card_draft" not in final_output, "Type-2 hallucinated card_draft should be removed"
    assert "title" in final_output, "Real title should be preserved"
    assert "card_draft" in unproven_keys
    print("✅ Output Gate: Type-2 hallucination blocked (card_draft removed, title preserved)")


def test_output_gate_supplement_missing():
    """Output Gate supplements artifact keys that are in ledger but missing from output."""
    ledger = [
        {
            "call_id": "call_1_0",
            "tool": "compose_card_deck",
            "ok": True,
            "artifacts": {"card_draft": {"v": 1}, "title": "real"},
            "iter": 1,
        },
    ]

    observed_artifacts = {"card_draft": {"v": 1}, "title": "real"}

    final_output = {
        "ended_reason": "completed",
    }

    PRODUCT_ARTIFACT_KEYS = frozenset(
        {
            "card_draft",
            "asset_ids",
            "file_path",
            "html_path",
            "image_url",
            "title",
            "pages",
            "deck",
        }
    )

    proven_artifacts: set = set()
    for rcpt in ledger:
        if rcpt["ok"]:
            proven_artifacts.update(rcpt["artifacts"].keys())

    for a_key, a_val in observed_artifacts.items():
        if a_key in proven_artifacts:
            final_output.setdefault(a_key, a_val)

    assert "card_draft" in final_output
    assert "title" in final_output
    print("✅ Output Gate: supplemented missing proven artifacts {card_draft, title}")


def test_output_gate_mixed_scenario():
    """Mixed: some tools succeed, some fail, LLM fabricates extra keys."""
    ledger = [
        {
            "call_id": "call_1_0",
            "tool": "llm_generate",
            "ok": True,
            "artifacts": {"title": "real title"},
            "iter": 1,
        },
        {
            "call_id": "call_2_0",
            "tool": "compose_card_deck",
            "ok": False,
            "artifacts": {},
            "iter": 2,
        },
    ]

    final_output = {
        "title": "real title",
        "card_draft": {"fabricated": True},
        "pages": 3,
        "ended_reason": "completed",
    }

    PRODUCT_ARTIFACT_KEYS = frozenset(
        {
            "card_draft",
            "asset_ids",
            "file_path",
            "html_path",
            "image_url",
            "title",
            "pages",
            "deck",
        }
    )

    proven_artifacts: set = set()
    for rcpt in ledger:
        if rcpt["ok"]:
            proven_artifacts.update(rcpt["artifacts"].keys())

    unproven_keys: set = set()
    for gk in list(final_output.keys()):
        if gk in PRODUCT_ARTIFACT_KEYS and gk not in proven_artifacts:
            unproven_keys.add(gk)
            del final_output[gk]

    assert "title" in final_output
    assert "card_draft" not in final_output
    assert "pages" not in final_output
    assert unproven_keys == {"card_draft", "pages"}
    print(f"✅ Output Gate mixed: title preserved, {{card_draft, pages}} removed (compose failed)")


def test_execution_gate_nudge_exhausted():
    """Execution Gate: nudge_exhausted with zero receipts → _fake_final=True."""
    ledger: list[dict] = []
    observed_artifacts: dict = {}
    ended_reason = "nudge_exhausted"

    has_any_receipt = bool(ledger)
    has_any_artifact = bool(observed_artifacts)

    final_output: dict = {"ended_reason": ended_reason}

    if not has_any_receipt and not has_any_artifact:
        final_output["_fake_final"] = True
        final_output["_execution_gate"] = {
            "trigger": ended_reason,
            "receipt_count": len(ledger),
            "artifact_count": len(observed_artifacts),
        }
        if ended_reason not in ("max_iterations", "token_budget"):
            final_output["ended_reason"] = "fake_final"

    assert final_output["_fake_final"] is True
    assert final_output["_execution_gate"]["trigger"] == "nudge_exhausted"
    assert final_output["ended_reason"] == "fake_final"
    print("✅ Execution Gate: nudge_exhausted → _fake_final=True, ended_reason=fake_final")


def test_execution_gate_duplicate_calls():
    """Execution Gate: duplicate_calls with zero receipts → _fake_final=True."""
    ledger: list[dict] = []
    observed_artifacts: dict = {}
    ended_reason = "duplicate_calls"

    final_output: dict = {"ended_reason": ended_reason}

    has_any_receipt = bool(ledger)
    has_any_artifact = bool(observed_artifacts)

    if not has_any_receipt and not has_any_artifact:
        final_output["_fake_final"] = True
        final_output["_execution_gate"] = {
            "trigger": ended_reason,
            "receipt_count": len(ledger),
            "artifact_count": len(observed_artifacts),
        }
        if ended_reason not in ("max_iterations", "token_budget"):
            final_output["ended_reason"] = "fake_final"

    assert final_output["_fake_final"] is True
    assert final_output["ended_reason"] == "fake_final"
    print("✅ Execution Gate: duplicate_calls → _fake_final=True, ended_reason=fake_final")


def test_execution_gate_max_iterations_no_rewrite():
    """Execution Gate: max_iterations is resource truncation, not hallucination.

    Should still mark _fake_final=True but NOT rewrite ended_reason.
    """
    ledger: list[dict] = []
    observed_artifacts: dict = {}
    ended_reason = "max_iterations"

    final_output: dict = {"ended_reason": ended_reason}

    has_any_receipt = bool(ledger)
    has_any_artifact = bool(observed_artifacts)

    if not has_any_receipt and not has_any_artifact:
        final_output["_fake_final"] = True
        final_output["_execution_gate"] = {
            "trigger": ended_reason,
            "receipt_count": len(ledger),
            "artifact_count": len(observed_artifacts),
        }
        if ended_reason not in ("max_iterations", "token_budget"):
            final_output["ended_reason"] = "fake_final"

    assert final_output["_fake_final"] is True
    assert final_output["ended_reason"] == "max_iterations"
    print("✅ Execution Gate: max_iterations → _fake_final=True, ended_reason unchanged")


def test_execution_gate_normal_completion_skipped():
    """Execution Gate: normal completion with receipts → no _fake_final."""
    ledger = [
        {
            "call_id": "call_1_0",
            "tool": "compose_card_deck",
            "ok": True,
            "artifacts": {"card_draft": {"v": 1}},
            "iter": 1,
        },
    ]
    observed_artifacts = {"card_draft": {"v": 1}}
    ended_reason = "completed"

    final_output: dict = {"ended_reason": ended_reason}

    has_any_receipt = bool(ledger)
    has_any_artifact = bool(observed_artifacts)

    if not has_any_receipt and not has_any_artifact:
        final_output["_fake_final"] = True

    assert "_fake_final" not in final_output
    assert final_output["ended_reason"] == "completed"
    print("✅ Execution Gate: normal completion with receipts → no _fake_final")


def test_execution_gate_paused_skipped():
    """Execution Gate: paused sessions are not checked (not in scope)."""
    ended_reason = "clarification_paused"

    final_output: dict = {"ended_reason": ended_reason}

    is_paused = ended_reason in (
        "clarification_paused",
        "guardian_paused",
        "storyboard_paused",
    )

    if not is_paused:
        final_output["_fake_final"] = True

    assert "_fake_final" not in final_output
    assert final_output["ended_reason"] == "clarification_paused"
    print("✅ Execution Gate: paused session → skipped, no _fake_final")


def test_pure_qa_bypass_nudge():
    """pure_qa: thought contains '不需要调用工具' → nudge not triggered."""
    thought = "用户只是让我自我介绍，这是纯问答，不需要调用工具"
    _is_pure_qa = False
    _qa_signals = (
        "纯问答",
        "纯聊天",
        "闲聊",
        "不需要调用工具",
        "不需要工具",
        "无需调用",
        "无需工具",
        "直接回答",
        "直接回复",
        "不需要调用任何工具",
        "不涉及创作",
        "问答场景",
        "聊天场景",
        "只是聊天",
        "只是问答",
        "不涉及工具",
        "无需执行",
        "无需创作",
    )
    _thought_lower = thought.lower()
    _is_pure_qa = any(s in _thought_lower for s in _qa_signals)

    assert _is_pure_qa is True
    _any_tool_executed = False
    _has_artifacts = False
    _llm_call_failed = False
    _fake_final_nudge = 0
    _MAX_FAKE_FINAL_NUDGE = 2
    should_nudge = (
        not _any_tool_executed
        and not _has_artifacts
        and not _llm_call_failed
        and _fake_final_nudge < _MAX_FAKE_FINAL_NUDGE
        and not _is_pure_qa
    )
    assert should_nudge is False
    print("✅ pure_qa bypass: thought with '不需要调用工具' → nudge skipped")


def test_pure_qa_bypass_execution_gate():
    """pure_qa: Execution Gate should not mark _fake_final for pure QA."""
    ledger: list[dict] = []
    observed_artifacts: dict = {}
    ended_reason = "completed"
    _is_pure_qa = True

    final_output: dict = {"ended_reason": ended_reason}

    has_any_receipt = bool(ledger)
    has_any_artifact = bool(observed_artifacts)

    if not has_any_receipt and not has_any_artifact and not _is_pure_qa:
        final_output["_fake_final"] = True
        final_output["_execution_gate"] = {
            "trigger": ended_reason,
            "receipt_count": 0,
            "artifact_count": 0,
        }
        final_output["ended_reason"] = "fake_final"

    assert "_fake_final" not in final_output
    assert final_output["ended_reason"] == "completed"
    print("✅ pure_qa bypass: Execution Gate → no _fake_final, ended_reason unchanged")


def test_pure_qa_no_signal_triggers_gate():
    """Non-pure-QA: thought without QA signals → Execution Gate still triggers."""
    thought = "我已经完成了卡片制作"
    _is_pure_qa = False
    _qa_signals = (
        "纯问答",
        "纯聊天",
        "闲聊",
        "不需要调用工具",
        "不需要工具",
        "无需调用",
        "无需工具",
        "直接回答",
        "直接回复",
        "不需要调用任何工具",
        "不涉及创作",
        "问答场景",
        "聊天场景",
        "只是聊天",
        "只是问答",
        "不涉及工具",
        "无需执行",
        "无需创作",
    )
    _thought_lower = thought.lower()
    _is_pure_qa = any(s in _thought_lower for s in _qa_signals)

    assert _is_pure_qa is False

    ledger: list[dict] = []
    observed_artifacts: dict = {}
    ended_reason = "nudge_exhausted"

    final_output: dict = {"ended_reason": ended_reason}

    has_any_receipt = bool(ledger)
    has_any_artifact = bool(observed_artifacts)

    if not has_any_receipt and not has_any_artifact and not _is_pure_qa:
        final_output["_fake_final"] = True
        final_output["_execution_gate"] = {
            "trigger": ended_reason,
            "receipt_count": 0,
            "artifact_count": 0,
        }
        if ended_reason not in ("max_iterations", "token_budget"):
            final_output["ended_reason"] = "fake_final"

    assert final_output["_fake_final"] is True
    assert final_output["ended_reason"] == "fake_final"
    print("✅ Non-pure-QA: thought without QA signals → Execution Gate triggers normally")


def test_format_nudge_natural_language_qa_accepted():
    """Format nudge: natural language output with no creation signals → accepted as pure QA."""
    content = "你好呀！我是你的全能内容创作助手，能帮你做卡片、写文案、剪视频。"
    user_intent = "你好，自我介绍一下自己"
    _any_tool_executed = False

    _is_natural_language_qa = False
    if content and len(content.strip()) > 20 and not _any_tool_executed:
        _creation_signals = (
            "做卡片",
            "做封面",
            "做视频",
            "写文案",
            "写笔记",
            "生成",
            "创作",
            "排版",
            "出图",
            "出片",
            "做图",
            "做海报",
            "做长图",
            "compose",
            "render",
            "帮我做",
            "帮我写",
            "帮我生成",
            "帮我创",
            "制作",
            "导出",
            "保存为",
            "发布",
        )
        _msg_lower = (user_intent or "").lower()
        _msg_has_creation = any(s in _msg_lower for s in _creation_signals)
        if not _msg_has_creation:
            _is_natural_language_qa = True

    assert _is_natural_language_qa is True
    print("✅ Format nudge: natural language + no creation signals → accepted as pure QA")


def test_format_nudge_creation_signal_triggers_nudge():
    """Format nudge: natural language output but user has creation signals → nudge."""
    content = "好的，我来帮你做一张卡片"
    user_intent = "帮我做一张卡片"
    _any_tool_executed = False

    _is_natural_language_qa = False
    if content and len(content.strip()) > 20 and not _any_tool_executed:
        _creation_signals = (
            "做卡片",
            "做封面",
            "做视频",
            "写文案",
            "写笔记",
            "生成",
            "创作",
            "排版",
            "出图",
            "出片",
            "做图",
            "做海报",
            "做长图",
            "compose",
            "render",
            "帮我做",
            "帮我写",
            "帮我生成",
            "帮我创",
            "制作",
            "导出",
            "保存为",
            "发布",
        )
        _msg_lower = (user_intent or "").lower()
        _msg_has_creation = any(s in _msg_lower for s in _creation_signals)
        if not _msg_has_creation:
            _is_natural_language_qa = True

    assert _is_natural_language_qa is False
    print("✅ Format nudge: user has creation signals → nudge triggers normally")


def test_format_nudge_short_output_triggers_nudge():
    """Format nudge: very short output (<20 chars) → nudge triggers (could be error)."""
    content = "好的"
    _any_tool_executed = False

    _is_natural_language_qa = False
    if content and len(content.strip()) > 20 and not _any_tool_executed:
        _is_natural_language_qa = True

    assert _is_natural_language_qa is False
    print("✅ Format nudge: short output → nudge triggers (not accepted as QA)")


def test_format_nudge_after_tool_executed():
    """Format nudge: tool already executed → natural language not accepted as QA."""
    content = "工具执行失败了，我来直接回答"
    _any_tool_executed = True

    _is_natural_language_qa = False
    if content and len(content.strip()) > 20 and not _any_tool_executed:
        _is_natural_language_qa = True

    assert _is_natural_language_qa is False
    print("✅ Format nudge: tool already executed → not accepted as QA")


def main():
    print("=" * 60)
    print("Receipt Ledger + Output Gate + Execution Gate — Unit Tests")
    print("=" * 60)
    print()

    test_receipt_ledger_basic()
    test_receipt_ledger_failed()
    test_output_gate_removes_unproven()
    test_output_gate_preserves_proven()
    test_output_gate_type2_hallucination()
    test_output_gate_supplement_missing()
    test_output_gate_mixed_scenario()
    test_execution_gate_nudge_exhausted()
    test_execution_gate_duplicate_calls()
    test_execution_gate_max_iterations_no_rewrite()
    test_execution_gate_normal_completion_skipped()
    test_execution_gate_paused_skipped()
    test_pure_qa_bypass_nudge()
    test_pure_qa_bypass_execution_gate()
    test_pure_qa_no_signal_triggers_gate()
    test_format_nudge_natural_language_qa_accepted()
    test_format_nudge_creation_signal_triggers_nudge()
    test_format_nudge_short_output_triggers_nudge()
    test_format_nudge_after_tool_executed()

    print()
    print("=" * 60)
    print("All Receipt Ledger + Output Gate + Execution Gate tests passed ✅")
    print("=" * 60)


if __name__ == "__main__":
    main()
