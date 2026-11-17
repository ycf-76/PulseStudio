"""A/B Comparison Report — 计算幻觉抑制系数 (HSC)

用法:
    python -m bench.compare_reports baseline.json treatment.json

输出:
    - HSC (Hallucination Suppression Coefficient) per category
    - 跨trigger对比
    - Type-1 vs Type-2 分解
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

BENCH_ROOT = Path(__file__).resolve().parent
RESULTS_DIR = BENCH_ROOT / "results"


def load_report(path: str) -> dict:
    p = Path(path)
    if not p.is_absolute():
        p = RESULTS_DIR / p
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def compute_hsc(h_raw: float, h_sys: float) -> float:
    if h_raw == 0:
        return 1.0 if h_sys == 0 else float("-inf")
    return 1.0 - (h_sys / h_raw)


def compare(baseline: dict, treatment: dict) -> dict:
    b_meta = baseline["meta"]
    t_meta = treatment["meta"]
    b_sum = baseline["summary"]
    t_sum = treatment["summary"]

    overall_hsc = compute_hsc(b_sum["hallucination_rate"], t_sum["hallucination_rate"])

    type1_hsc = (
        compute_hsc(
            b_sum["type1_fake_final_count"] / b_sum["total_runs"],
            t_sum["type1_fake_final_count"] / t_sum["total_runs"],
        )
        if b_sum["total_runs"] > 0
        else 0.0
    )

    type2_hsc = (
        compute_hsc(
            b_sum["type2_fabricated_artifact_count"] / b_sum["total_runs"],
            t_sum["type2_fabricated_artifact_count"] / t_sum["total_runs"],
        )
        if b_sum["total_runs"] > 0
        else 0.0
    )

    category_hsc = {}
    for cat in baseline["by_category"]:
        b_rate = baseline["by_category"][cat]["rate"]
        t_rate = treatment["by_category"].get(cat, {}).get("rate", 0.0)
        category_hsc[cat] = {
            "baseline_rate": b_rate,
            "treatment_rate": t_rate,
            "hsc": compute_hsc(b_rate, t_rate),
        }

    trigger_hsc = {}
    all_triggers = set(baseline["by_trigger"].keys()) | set(treatment["by_trigger"].keys())
    for trigger in all_triggers:
        b_rate = baseline["by_trigger"].get(trigger, {}).get("rate", 0.0)
        t_rate = treatment["by_trigger"].get(trigger, {}).get("rate", 0.0)
        trigger_hsc[trigger] = {
            "baseline_rate": b_rate,
            "treatment_rate": t_rate,
            "hsc": compute_hsc(b_rate, t_rate),
        }

    nudge_successes = 0
    nudge_total = 0
    for d in treatment["details"]:
        if d["hallucination_type"] == "fake_final":
            nudge_total += 1
        elif d.get("ended_reason") and d["steps_with_tools"] > 0:
            pass

    return {
        "baseline": {
            "model": b_meta["model"],
            "version": b_meta["version_label"],
            "timestamp": b_meta["timestamp"],
            "total_seeds": b_meta["total_seeds"],
        },
        "treatment": {
            "model": t_meta["model"],
            "version": t_meta["version_label"],
            "timestamp": t_meta["timestamp"],
            "total_seeds": t_meta["total_seeds"],
        },
        "overall": {
            "baseline_hallucination_rate": b_sum["hallucination_rate"],
            "treatment_hallucination_rate": t_sum["hallucination_rate"],
            "hsc": round(overall_hsc, 4),
            "hsc_interpretation": interpret_hsc(overall_hsc),
        },
        "by_type": {
            "type1_fake_final": {
                "baseline_rate": (
                    round(b_sum["type1_fake_final_count"] / b_sum["total_runs"], 4)
                    if b_sum["total_runs"]
                    else 0
                ),
                "treatment_rate": (
                    round(t_sum["type1_fake_final_count"] / t_sum["total_runs"], 4)
                    if t_sum["total_runs"]
                    else 0
                ),
                "hsc": round(type1_hsc, 4),
            },
            "type2_fabricated_artifact": {
                "baseline_rate": (
                    round(b_sum["type2_fabricated_artifact_count"] / b_sum["total_runs"], 4)
                    if b_sum["total_runs"]
                    else 0
                ),
                "treatment_rate": (
                    round(t_sum["type2_fabricated_artifact_count"] / t_sum["total_runs"], 4)
                    if t_sum["total_runs"]
                    else 0
                ),
                "hsc": round(type2_hsc, 4),
            },
        },
        "by_category": category_hsc,
        "by_trigger": trigger_hsc,
        "false_positive": {
            "baseline": b_sum["false_positive_rate"],
            "treatment": t_sum["false_positive_rate"],
        },
    }


def interpret_hsc(hsc: float) -> str:
    if hsc < 0:
        return "❌ 系统在放大幻觉（比无防护更糟）"
    if hsc == 0:
        return "⚠️ 系统对幻觉无影响"
    if hsc < 0.3:
        return "⚠️ 弱抑制（不到30%）"
    if hsc < 0.5:
        return "🟡 中等抑制（30%-50%）"
    if hsc < 0.8:
        return "🟢 较强抑制（50%-80%）"
    if hsc < 1.0:
        return "✅ 强抑制（80%以上）"
    return "✅ 完全抑制（幻觉归零）"


def render_comparison_md(comp: dict) -> str:
    lines = [
        "# 幻觉抑制对比报告 (A/B)",
        "",
        f"- **基线版本**: {comp['baseline']['version']} ({comp['baseline']['model']})",
        f"- **修复版本**: {comp['treatment']['version']} ({comp['treatment']['model']})",
        "",
        "## 总体幻觉抑制系数 (HSC)",
        "",
        f"| 指标 | 基线 | 修复 | HSC |",
        f"|------|------|------|-----|",
        f"| 幻觉率 | {comp['overall']['baseline_hallucination_rate']:.1%} | {comp['overall']['treatment_hallucination_rate']:.1%} | **{comp['overall']['hsc']:.2f}** |",
        "",
        f"**判定**: {comp['overall']['hsc_interpretation']}",
        "",
        "## 按幻觉类型分解",
        "",
        f"| 类型 | 基线率 | 修复率 | HSC |",
        f"|------|--------|--------|-----|",
    ]
    for t, data in comp["by_type"].items():
        lines.append(
            f"| {t} | {data['baseline_rate']:.1%} | {data['treatment_rate']:.1%} | {data['hsc']:.2f} |"
        )

    lines += [
        "",
        "## 按场景分类",
        "",
        f"| 分类 | 基线率 | 修复率 | HSC |",
        f"|------|--------|--------|-----|",
    ]
    for cat, data in comp["by_category"].items():
        lines.append(
            f"| {cat} | {data['baseline_rate']:.1%} | {data['treatment_rate']:.1%} | {data['hsc']:.2f} |"
        )

    lines += [
        "",
        "## 按触发条件",
        "",
        f"| 触发条件 | 基线率 | 修复率 | HSC |",
        f"|----------|--------|--------|-----|",
    ]
    for trigger, data in sorted(comp["by_trigger"].items()):
        lines.append(
            f"| {trigger} | {data['baseline_rate']:.1%} | {data['treatment_rate']:.1%} | {data['hsc']:.2f} |"
        )

    lines += [
        "",
        "## 假阳性率",
        "",
        f"- 基线: {comp['false_positive']['baseline']:.1%}",
        f"- 修复: {comp['false_positive']['treatment']:.1%}",
        "",
        "## HSC 判定标准",
        "",
        "| HSC 范围 | 含义 |",
        "|----------|------|",
        "| < 0 | ❌ 系统在放大幻觉 |",
        "| = 0 | ⚠️ 无影响 |",
        "| 0–0.3 | ⚠️ 弱抑制 |",
        "| 0.3–0.5 | 🟡 中等抑制 |",
        "| 0.5–0.8 | 🟢 较强抑制 |",
        "| 0.8–1.0 | ✅ 强抑制 |",
        "| = 1.0 | ✅ 完全抑制 |",
    ]

    return "\n".join(lines)


def main():
    if len(sys.argv) < 3:
        print("Usage: python -m bench.compare_reports <baseline.json> <treatment.json>")
        print("  Paths can be relative to bench/results/")
        sys.exit(1)

    baseline = load_report(sys.argv[1])
    treatment = load_report(sys.argv[2])

    comp = compare(baseline, treatment)
    md = render_comparison_md(comp)

    print(md)

    ts = __import__("datetime").datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = RESULTS_DIR / f"comparison_{ts}.md"
    out_path.write_text(md, encoding="utf-8")
    print(f"\nSaved to: {out_path}")


if __name__ == "__main__":
    main()
