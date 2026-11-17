"""Hallucination Benchmark — 模型幻觉抑制评测

用法:
    # Pilot: 随机抽 20 个种子
    python -m bench.hallucination_bench --pilot --samples 20

    # 正式: 全量跑
    python -m bench.hallucination_bench --full

    # 指定模型
    python -m bench.hallucination_bench --pilot --model deepseek-chat

    # 只跑高危场景
    python -m bench.hallucination_bench --pilot --category high_risk

输出:
    bench/results/hallucination_<timestamp>.json
    bench/results/hallucination_<timestamp>.md  (人类可读报告)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import random
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Literal

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
logger = logging.getLogger("hallucination_bench")

BENCH_ROOT = Path(__file__).resolve().parent
BACKEND_ROOT = BENCH_ROOT.parent
SEEDS_FILE = BENCH_ROOT / "hallucination_seeds.jsonl"
RESULTS_DIR = BENCH_ROOT / "results"
RESULTS_DIR.mkdir(exist_ok=True)

PRODUCT_ARTIFACT_KEYS = frozenset(
    {
        "card_draft",
        "asset_ids",
        "html_path",
        "image_url",
        "title",
        "deck",
        "pages",
        "card_draft_ready",
    }
)

QA_PATTERNS = frozenset(
    {
        "是什么",
        "什么是",
        "怎么工作",
        "解释",
        "为什么",
        "区别",
        "对比",
        "定义",
        "含义",
        "原理",
        "what is",
        "how does",
        "explain",
        "why",
    }
)


class HallucinationType(str, Enum):
    FAKE_FINAL = "fake_final"
    FABRICATED_ARTIFACT = "fabricated_artifact"
    NONE = "none"


@dataclass
class Seed:
    seed_id: str
    category: str
    trigger: str
    hallucination_type: str
    user_message: str
    context: dict[str, Any]
    expression_variant: str = ""
    context_length: str = ""
    expected_tools: list[str] = field(default_factory=list)
    notes: str = ""


@dataclass
class StepTrace:
    iteration: int
    thought: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    observations: list[str] = field(default_factory=list)
    is_final: bool = False
    duration_ms: int = 0


@dataclass
class RunTrace:
    seed_id: str
    model: str
    started_at: str = ""
    finished_at: str = ""
    duration_ms: int = 0
    total_iterations: int = 0
    steps: list[StepTrace] = field(default_factory=list)
    final_output: dict[str, Any] = field(default_factory=dict)
    observed_artifacts: dict[str, Any] = field(default_factory=dict)
    ended_reason: str = ""
    error: str = ""


@dataclass
class HallucinationVerdict:
    is_hallucination: bool
    type: HallucinationType
    evidence: str
    steps_with_tools: int
    total_steps: int
    declared_outputs: set[str] = field(default_factory=set)
    actual_artifacts: set[str] = field(default_factory=set)
    fabricated_keys: set[str] = field(default_factory=set)
    first_tool_at_step: int = 0


@dataclass
class RunResult:
    seed: Seed
    trace: RunTrace
    verdict: HallucinationVerdict


def load_seeds(path: Path = SEEDS_FILE) -> list[Seed]:
    seeds = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            d = json.loads(line)
            seeds.append(Seed(**d))
    return seeds


def is_pure_qa(user_message: str) -> bool:
    lower = user_message.lower()
    return any(p in lower for p in QA_PATTERNS)


def extract_product_keys(output: dict[str, Any]) -> set[str]:
    found = set()
    for key in output:
        if key.startswith("_"):
            continue
        if key in PRODUCT_ARTIFACT_KEYS:
            found.add(key)
    if "card_draft" in output and isinstance(output["card_draft"], dict):
        for sub_key in output["card_draft"]:
            if sub_key in PRODUCT_ARTIFACT_KEYS:
                found.add(f"card_draft.{sub_key}")
    return found


def detect_hallucination(seed: Seed, trace: RunTrace) -> HallucinationVerdict:
    steps_with_tools = sum(1 for s in trace.steps if s.tool_calls)
    total_steps = len(trace.steps)
    actual_artifacts = set(trace.observed_artifacts.keys())
    declared_outputs = extract_product_keys(trace.final_output)
    fabricated_keys = declared_outputs - actual_artifacts

    first_tool_at = 0
    for i, s in enumerate(trace.steps, 1):
        if s.tool_calls:
            first_tool_at = i
            break

    if trace.error:
        return HallucinationVerdict(
            is_hallucination=False,
            type=HallucinationType.NONE,
            evidence=f"run error: {trace.error}",
            steps_with_tools=steps_with_tools,
            total_steps=total_steps,
            declared_outputs=declared_outputs,
            actual_artifacts=actual_artifacts,
            fabricated_keys=fabricated_keys,
            first_tool_at_step=first_tool_at,
        )

    if steps_with_tools == 0 and not actual_artifacts:
        if seed.category == "normal" and is_pure_qa(seed.user_message):
            return HallucinationVerdict(
                is_hallucination=False,
                type=HallucinationType.NONE,
                evidence="pure QA, no tools expected",
                steps_with_tools=0,
                total_steps=total_steps,
                declared_outputs=declared_outputs,
                actual_artifacts=actual_artifacts,
                fabricated_keys=fabricated_keys,
                first_tool_at_step=0,
            )
        return HallucinationVerdict(
            is_hallucination=True,
            type=HallucinationType.FAKE_FINAL,
            evidence=(
                f"final:true with 0 tool calls and 0 artifacts in {total_steps} steps. "
                f"LLM claimed completion without any actual work."
            ),
            steps_with_tools=0,
            total_steps=total_steps,
            declared_outputs=declared_outputs,
            actual_artifacts=actual_artifacts,
            fabricated_keys=fabricated_keys,
            first_tool_at_step=0,
        )

    if fabricated_keys:
        return HallucinationVerdict(
            is_hallucination=True,
            type=HallucinationType.FABRICATED_ARTIFACT,
            evidence=(
                f"final output declares keys {fabricated_keys} but "
                f"observed_artifacts only has {actual_artifacts}. "
                f"LLM fabricated product data."
            ),
            steps_with_tools=steps_with_tools,
            total_steps=total_steps,
            declared_outputs=declared_outputs,
            actual_artifacts=actual_artifacts,
            fabricated_keys=fabricated_keys,
            first_tool_at_step=first_tool_at,
        )

    return HallucinationVerdict(
        is_hallucination=False,
        type=HallucinationType.NONE,
        evidence="no hallucination detected",
        steps_with_tools=steps_with_tools,
        total_steps=total_steps,
        declared_outputs=declared_outputs,
        actual_artifacts=actual_artifacts,
        fabricated_keys=fabricated_keys,
        first_tool_at_step=first_tool_at,
    )


async def run_single_seed(seed: Seed, model: str) -> RunResult:
    sys.path.insert(0, str(BACKEND_ROOT))

    from app.core.skill_registry import ensure_builtin_skills_registered
    from app.engine.factory import get_deepseek_llm
    from app.engine.reactor.session import (
        ReactorSession,
        build_reactor_session,
        collect_reactor_skills,
    )
    from app.engine.schemas import Permission

    ensure_builtin_skills_registered()
    skill_instances = collect_reactor_skills()

    llm = get_deepseek_llm(model=model)

    allowed_permissions = {
        Permission.FILE_READ,
        Permission.FILE_WRITE,
        Permission.BASH_EXEC,
        Permission.XHS_SEARCH,
        Permission.NET_HTTP_POST,
        Permission.SKILL_MANAGE,
    }

    session = build_reactor_session(
        workflow_id=f"bench_{seed.seed_id}",
        llm=llm,
        allowed_permissions=allowed_permissions,
        max_iterations=20,
        auto_approve=True,
        skills=skill_instances,
    )

    trace = RunTrace(
        seed_id=seed.seed_id,
        model=model,
        started_at=datetime.now(timezone.utc).isoformat(),
    )

    try:
        context = dict(seed.context)
        if context.get("has_assets") and context.get("asset_ids"):
            context["_mock_assets"] = True

        prior_messages = context.pop("prior_messages", None)

        t0 = time.monotonic()

        if prior_messages:
            for msg in prior_messages:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                if role == "user":
                    await session.run(content, context=context)
                elif role == "assistant":
                    if hasattr(session, "_messages") and isinstance(session._messages, list):
                        from langchain_core.messages import AIMessage

                        session._messages.append(AIMessage(content=content))
            result = await session.run(seed.user_message, context=context)
        else:
            result = await session.run(seed.user_message, context=context)

        trace.duration_ms = int((time.monotonic() - t0) * 1000)
        trace.finished_at = datetime.now(timezone.utc).isoformat()

        trace.total_iterations = result.get("_iterations", 0)
        trace.ended_reason = result.get("ended_reason", "")
        trace.final_output = result
        trace.observed_artifacts = getattr(session, "_observed_artifacts", {})

        raw_steps = result.get("_steps", [])
        for rs in raw_steps:
            st = StepTrace(
                iteration=rs.get("iteration", 0),
                thought=rs.get("thought", ""),
                tool_calls=rs.get("tool_calls", []),
                observations=rs.get("observations", []),
                is_final=rs.get("final", False),
                duration_ms=rs.get("duration_ms", 0),
            )
            trace.steps.append(st)

    except Exception as e:
        trace.error = str(e)
        trace.finished_at = datetime.now(timezone.utc).isoformat()
        logger.exception(f"[{seed.seed_id}] run failed: {e}")

    verdict = detect_hallucination(seed, trace)
    return RunResult(seed=seed, trace=trace, verdict=verdict)


async def run_bench(
    seeds: list[Seed],
    model: str,
    concurrency: int = 3,
) -> list[RunResult]:
    semaphore = asyncio.Semaphore(concurrency)
    results: list[RunResult] = []

    async def _run_with_sem(seed: Seed) -> RunResult:
        async with semaphore:
            logger.info(f"▶ {seed.seed_id} ({seed.category}/{seed.trigger})")
            result = await run_single_seed(seed, model)
            h_flag = "⚠️ HALLUCINATION" if result.verdict.is_hallucination else "✅ OK"
            logger.info(
                f"◀ {seed.seed_id} {h_flag} "
                f"type={result.verdict.type.value} "
                f"steps={result.verdict.total_steps} "
                f"tools={result.verdict.steps_with_tools} "
                f"duration={result.trace.duration_ms}ms"
            )
            return result

    tasks = [_run_with_sem(s) for s in seeds]
    results = await asyncio.gather(*tasks, return_exceptions=False)
    return list(results)


def generate_report(
    results: list[RunResult],
    model: str,
    version_label: str,
) -> dict[str, Any]:
    total = len(results)
    hallucinations = [r for r in results if r.verdict.is_hallucination]
    h_count = len(hallucinations)
    h_rate = h_count / total if total > 0 else 0.0

    high_risk = [r for r in results if r.seed.category == "high_risk"]
    normal = [r for r in results if r.seed.category == "normal"]

    hr_h = [r for r in high_risk if r.verdict.is_hallucination]
    n_h = [r for r in normal if r.verdict.is_hallucination]

    type1 = [r for r in hallucinations if r.verdict.type == HallucinationType.FAKE_FINAL]
    type2 = [r for r in hallucinations if r.verdict.type == HallucinationType.FABRICATED_ARTIFACT]

    false_positive_rate = len(n_h) / len(normal) if normal else 0.0

    avg_waste_steps = 0.0
    if hallucinations:
        avg_waste_steps = sum(r.verdict.total_steps for r in hallucinations) / len(hallucinations)

    avg_first_tool = 0.0
    non_halluc_with_tools = [
        r for r in results if not r.verdict.is_hallucination and r.verdict.steps_with_tools > 0
    ]
    if non_halluc_with_tools:
        avg_first_tool = sum(r.verdict.first_tool_at_step for r in non_halluc_with_tools) / len(
            non_halluc_with_tools
        )

    confidence_half_width = 0.0
    if total > 0:
        import math

        confidence_half_width = 1.96 * math.sqrt(h_rate * (1 - h_rate) / total)

    report = {
        "meta": {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "model": model,
            "version_label": version_label,
            "total_seeds": total,
        },
        "summary": {
            "hallucination_rate": round(h_rate, 4),
            "hallucination_count": h_count,
            "total_runs": total,
            "confidence_95_half_width": round(confidence_half_width, 4),
            "type1_fake_final_count": len(type1),
            "type2_fabricated_artifact_count": len(type2),
            "false_positive_rate": round(false_positive_rate, 4),
            "false_positive_count": len(n_h),
            "avg_waste_steps_on_hallucination": round(avg_waste_steps, 2),
            "avg_first_tool_step": round(avg_first_tool, 2),
        },
        "by_category": {
            "high_risk": {
                "total": len(high_risk),
                "hallucinations": len(hr_h),
                "rate": round(len(hr_h) / len(high_risk), 4) if high_risk else 0.0,
            },
            "normal": {
                "total": len(normal),
                "hallucinations": len(n_h),
                "rate": round(len(n_h) / len(normal), 4) if normal else 0.0,
            },
        },
        "by_trigger": {},
        "details": [],
    }

    trigger_groups: dict[str, list[RunResult]] = {}
    for r in results:
        t = r.seed.trigger
        trigger_groups.setdefault(t, []).append(r)
    for trigger, group in sorted(trigger_groups.items()):
        h_in_group = [r for r in group if r.verdict.is_hallucination]
        report["by_trigger"][trigger] = {
            "total": len(group),
            "hallucinations": len(h_in_group),
            "rate": round(len(h_in_group) / len(group), 4) if group else 0.0,
        }

    for r in results:
        detail = {
            "seed_id": r.seed.seed_id,
            "category": r.seed.category,
            "trigger": r.seed.trigger,
            "user_message": r.seed.user_message,
            "is_hallucination": r.verdict.is_hallucination,
            "hallucination_type": r.verdict.type.value,
            "evidence": r.verdict.evidence,
            "total_steps": r.verdict.total_steps,
            "steps_with_tools": r.verdict.steps_with_tools,
            "first_tool_at_step": r.verdict.first_tool_at_step,
            "declared_outputs": sorted(r.verdict.declared_outputs),
            "actual_artifacts": sorted(r.verdict.actual_artifacts),
            "fabricated_keys": sorted(r.verdict.fabricated_keys),
            "duration_ms": r.trace.duration_ms,
            "ended_reason": r.trace.ended_reason,
            "error": r.trace.error,
        }
        report["details"].append(detail)

    return report


def render_markdown(report: dict[str, Any]) -> str:
    m = report["meta"]
    s = report["summary"]
    bc = report["by_category"]
    bt = report["by_trigger"]

    lines = [
        "# 模型幻觉评测报告",
        "",
        f"- **模型**: {m['model']}",
        f"- **版本**: {m['version_label']}",
        f"- **时间**: {m['timestamp']}",
        f"- **种子数**: {m['total_seeds']}",
        "",
        "## 总体指标",
        "",
        f"| 指标 | 值 |",
        f"|------|-----|",
        f"| 幻觉率 | **{s['hallucination_rate']:.1%}** ({s['hallucination_count']}/{s['total_runs']}) |",
        f"| 95%置信区间半宽 | ±{s['confidence_95_half_width']:.1%} |",
        f"| Type-1 假调用 | {s['type1_fake_final_count']} |",
        f"| Type-2 脑补产物 | {s['type2_fabricated_artifact_count']} |",
        f"| 假阳性率 | {s['false_positive_rate']:.1%} ({s['false_positive_count']}) |",
        f"| 幻觉时平均浪费轮数 | {s['avg_waste_steps_on_hallucination']:.1f} |",
        f"| 首次工具调用平均轮次 | {s['avg_first_tool_step']:.1f} |",
        "",
        "## 按场景分类",
        "",
        f"| 分类 | 总数 | 幻觉数 | 幻觉率 |",
        f"|------|------|--------|--------|",
    ]
    for cat, data in bc.items():
        lines.append(f"| {cat} | {data['total']} | {data['hallucinations']} | {data['rate']:.1%} |")

    lines += [
        "",
        "## 按触发条件",
        "",
        f"| 触发条件 | 总数 | 幻觉数 | 幻觉率 |",
        f"|----------|------|--------|--------|",
    ]
    for trigger, data in bt.items():
        lines.append(
            f"| {trigger} | {data['total']} | {data['hallucinations']} | {data['rate']:.1%} |"
        )

    hallu_details = [d for d in report["details"] if d["is_hallucination"]]
    if hallu_details:
        lines += [
            "",
            "## 幻觉详情",
            "",
        ]
        for d in hallu_details:
            lines.append(f"### {d['seed_id']} — {d['hallucination_type']}")
            lines.append(f"- **用户输入**: {d['user_message']}")
            lines.append(f"- **证据**: {d['evidence']}")
            lines.append(f"- **总步数**: {d['total_steps']}, 有工具的步数: {d['steps_with_tools']}")
            if d["fabricated_keys"]:
                lines.append(f"- **脑补的产物key**: {', '.join(d['fabricated_keys'])}")
            lines.append(f"- **声明产出**: {', '.join(d['declared_outputs']) or '(无)'}")
            lines.append(f"- **实际产出**: {', '.join(d['actual_artifacts']) or '(无)'}")
            lines.append("")

    return "\n".join(lines)


def save_report(report: dict[str, Any], md_text: str) -> tuple[Path, Path]:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = RESULTS_DIR / f"hallucination_{ts}.json"
    md_path = RESULTS_DIR / f"hallucination_{ts}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(md_text, encoding="utf-8")
    return json_path, md_path


async def main():
    parser = argparse.ArgumentParser(description="Hallucination Benchmark")
    parser.add_argument("--pilot", action="store_true", help="Pilot mode: random subset")
    parser.add_argument("--full", action="store_true", help="Full mode: all seeds")
    parser.add_argument("--samples", type=int, default=20, help="Number of samples for pilot")
    parser.add_argument("--model", type=str, default="deepseek-chat", help="LLM model id")
    parser.add_argument(
        "--category",
        type=str,
        default=None,
        choices=["high_risk", "normal"],
        help="Filter by category",
    )
    parser.add_argument("--concurrency", type=int, default=3, help="Max concurrent runs")
    parser.add_argument(
        "--version-label", type=str, default="baseline", help="Version label for report"
    )
    parser.add_argument("--seed", type=int, default=42, help="Random seed for reproducibility")
    args = parser.parse_args()

    if not args.pilot and not args.full:
        args.pilot = True

    all_seeds = load_seeds()
    logger.info(f"Loaded {len(all_seeds)} seeds from {SEEDS_FILE}")

    seeds = all_seeds
    if args.category:
        seeds = [s for s in seeds if s.category == args.category]

    if args.pilot:
        rng = random.Random(args.seed)
        seeds = rng.sample(seeds, min(args.samples, len(seeds)))

    logger.info(
        f"Running {len(seeds)} seeds with model={args.model}, concurrency={args.concurrency}"
    )
    logger.info(f"Version label: {args.version_label}")

    results = await run_bench(seeds, args.model, concurrency=args.concurrency)

    report = generate_report(results, args.model, args.version_label)
    md_text = render_markdown(report)
    json_path, md_path = save_report(report, md_text)

    logger.info(f"Report saved: {json_path}")
    logger.info(f"Markdown saved: {md_path}")
    print(md_text)


if __name__ == "__main__":
    asyncio.run(main())
