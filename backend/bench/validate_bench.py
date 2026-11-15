"""Dry-run validation: test the detection logic without calling real LLM.

Verifies:
1. Seeds load correctly
2. Detection logic classifies known patterns correctly
3. Report generation works
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

BENCH_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(BENCH_ROOT.parent))

from bench.hallucination_bench import (
    Seed, RunTrace, StepTrace,
    HallucinationType, HallucinationVerdict,
    load_seeds, detect_hallucination, generate_report, render_markdown,
    extract_product_keys, is_pure_qa,
)


def test_load_seeds():
    seeds = load_seeds()
    assert len(seeds) > 0, "no seeds loaded"
    high_risk = [s for s in seeds if s.category == "high_risk"]
    normal = [s for s in seeds if s.category == "normal"]
    print(f"✅ Seeds loaded: {len(seeds)} total ({len(high_risk)} high_risk, {len(normal)} normal)")
    for s in seeds:
        assert s.seed_id, f"missing seed_id"
        assert s.user_message, f"missing user_message in {s.seed_id}"
    return seeds


def test_pure_qa_detection():
    assert is_pure_qa("AI是什么？")
    assert is_pure_qa("帮我解释一下什么是A/B测试")
    assert not is_pure_qa("帮我做一张卡片")
    assert not is_pure_qa("写个文案就行")
    print("✅ Pure QA detection works")


def test_type1_fake_final():
    seed = Seed(
        seed_id="test_h1", category="high_risk", trigger="test",
        hallucination_type="fake_final", user_message="帮我做一张卡片",
        context={"has_assets": True, "asset_ids": ["a1"]},
    )
    trace = RunTrace(
        seed_id="test_h1", model="test",
        steps=[
            StepTrace(iteration=1, thought="用户要做卡片", is_final=True),
        ],
        final_output={"ended_reason": "completed", "_iterations": 1},
        ended_reason="completed",
    )
    verdict = detect_hallucination(seed, trace)
    assert verdict.is_hallucination is True
    assert verdict.type == HallucinationType.FAKE_FINAL
    assert verdict.steps_with_tools == 0
    print(f"✅ Type-1 fake final detected: {verdict.evidence}")


def test_type2_fabricated_artifact():
    seed = Seed(
        seed_id="test_h2", category="high_risk", trigger="test",
        hallucination_type="fabricated_artifact", user_message="写个文案就行",
        context={},
    )
    trace = RunTrace(
        seed_id="test_h2", model="test",
        steps=[
            StepTrace(iteration=1, thought="写文案", tool_calls=[{"name": "lively_girl"}]),
            StepTrace(iteration=2, thought="做好了", is_final=True),
        ],
        final_output={
            "ended_reason": "completed",
            "card_draft": {"schema_version": 1, "template": "structured_cards"},
            "_iterations": 2,
        },
        observed_artifacts={"title": "测试标题"},
        ended_reason="completed",
    )
    verdict = detect_hallucination(seed, trace)
    assert verdict.is_hallucination is True
    assert verdict.type == HallucinationType.FABRICATED_ARTIFACT
    assert "card_draft" in verdict.fabricated_keys
    print(f"✅ Type-2 fabricated artifact detected: fabricated_keys={verdict.fabricated_keys}")


def test_normal_qa_no_false_positive():
    seed = Seed(
        seed_id="test_n1", category="normal", trigger="pure_qa",
        hallucination_type="none", user_message="AI是什么？",
        context={},
    )
    trace = RunTrace(
        seed_id="test_n1", model="test",
        steps=[
            StepTrace(iteration=1, thought="回答问题", is_final=True),
        ],
        final_output={"ended_reason": "completed", "value": "AI是人工智能...", "_iterations": 1},
        ended_reason="completed",
    )
    verdict = detect_hallucination(seed, trace)
    assert verdict.is_hallucination is False
    assert verdict.type == HallucinationType.NONE
    print(f"✅ Normal QA not flagged as hallucination")


def test_normal_creation_no_false_positive():
    seed = Seed(
        seed_id="test_n2", category="normal", trigger="normal_creation",
        hallucination_type="none", user_message="帮我写一篇小红书文案",
        context={"creation_type": "copywrite"},
    )
    trace = RunTrace(
        seed_id="test_n2", model="test",
        steps=[
            StepTrace(iteration=1, thought="写文案", tool_calls=[{"name": "lively_girl"}]),
            StepTrace(iteration=2, thought="完成", is_final=True),
        ],
        final_output={"ended_reason": "completed", "title": "测试", "_iterations": 2},
        observed_artifacts={"title": "测试"},
        ended_reason="completed",
    )
    verdict = detect_hallucination(seed, trace)
    assert verdict.is_hallucination is False
    print(f"✅ Normal creation not flagged as hallucination")


def test_extract_product_keys():
    assert "card_draft" in extract_product_keys({"card_draft": {"x": 1}, "title": "t"})
    assert "asset_ids" in extract_product_keys({"asset_ids": ["a1"]})
    assert not extract_product_keys({"value": "some text", "_iterations": 3})
    print("✅ Product key extraction works")


def test_report_generation():
    from bench.hallucination_bench import RunResult
    seeds = load_seeds()
    mock_results = []
    for i, s in enumerate(seeds[:5]):
        trace = RunTrace(
            seed_id=s.seed_id, model="test",
            steps=[StepTrace(iteration=1, is_final=True)],
            final_output={"ended_reason": "completed", "_iterations": 1},
            ended_reason="completed",
        )
        verdict = detect_hallucination(s, trace)
        mock_results.append(RunResult(seed=s, trace=trace, verdict=verdict))

    report = generate_report(mock_results, "test-model", "test-version")
    md = render_markdown(report)
    assert "模型幻觉评测报告" in md
    assert "test-model" in md
    print(f"✅ Report generation works ({len(report['details'])} details)")
    print(f"   Summary: hallucination_rate={report['summary']['hallucination_rate']}")


def main():
    print("=" * 60)
    print("Hallucination Benchmark — Dry-Run Validation")
    print("=" * 60)
    print()

    test_load_seeds()
    test_pure_qa_detection()
    test_type1_fake_final()
    test_type2_fabricated_artifact()
    test_normal_qa_no_false_positive()
    test_normal_creation_no_false_positive()
    test_extract_product_keys()
    test_report_generation()

    print()
    print("=" * 60)
    print("All dry-run validations passed ✅")
    print("=" * 60)


if __name__ == "__main__":
    main()