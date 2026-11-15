"""Analyze hallucination benchmark results in depth."""
from __future__ import annotations
import json
import sys
from pathlib import Path
from collections import Counter

BENCH_ROOT = Path(__file__).resolve().parent

def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "results/hallucination_20261023_192939.json"
    p = BENCH_ROOT / path
    with open(p, encoding="utf-8") as f:
        data = json.load(f)

    details = data["details"]
    reasons = Counter(d["ended_reason"] for d in details)
    steps = [d["total_steps"] for d in details]
    tools = [d["steps_with_tools"] for d in details]
    durations = [d["duration_ms"] for d in details]

    print("=" * 60)
    print("深度分析: hallucination benchmark results")
    print("=" * 60)

    print("\n=== ended_reason 分布 ===")
    for r, c in reasons.most_common():
        print(f"  {r}: {c}")

    print(f"\n=== 步数统计 ===")
    print(f"  平均步数: {sum(steps)/len(steps):.1f}")
    print(f"  平均有工具步数: {sum(tools)/len(tools):.1f}")
    print(f"  平均耗时: {sum(durations)/len(durations)/1000:.1f}s")
    print(f"  总耗时: {sum(durations)/1000:.1f}s")

    clarified = [d for d in details if d["ended_reason"] == "clarification_paused"]
    completed = [d for d in details if d["ended_reason"] == "completed"]
    guardian = [d for d in details if d["ended_reason"] == "guardian_paused"]

    print(f"\n=== 关键发现 ===")
    print(f"  clarification_paused: {len(clarified)} -> LLM调了ask_user_question后暂停等用户")
    print(f"  completed: {len(completed)} -> 正常完成")
    print(f"  guardian_paused: {len(guardian)} -> 守护者介入暂停")

    print(f"\n=== 幻觉检测盲区分析 ===")
    no_artifact_completed = [d for d in details if not d["actual_artifacts"] and d["ended_reason"] == "completed"]
    print(f"  completed但无产物: {len(no_artifact_completed)} 个")
    for d in no_artifact_completed:
        print(f"    - {d['seed_id']}: {d['user_message'][:40]}... steps={d['total_steps']} tools={d['steps_with_tools']}")

    print(f"\n=== clarification_paused 详情 ===")
    for d in clarified:
        print(f"  {d['seed_id']}: {d['user_message'][:40]}... steps={d['total_steps']} tools={d['steps_with_tools']}")

    print(f"\n=== guardian_paused 详情 ===")
    for d in guardian:
        print(f"  {d['seed_id']}: {d['user_message'][:40]}... steps={d['total_steps']} tools={d['steps_with_tools']}")

    print(f"\n=== 结论 ===")
    if data["summary"]["hallucination_count"] == 0:
        print("  幻觉率 = 0%，但这不意味着没有幻觉风险。")
        print("  原因分析：")
        print(f"  1. {len(clarified)}/{len(details)} 的种子被 clarification 暂停——")
        print("     LLM调了ask_user_question而非直接final，这避开了Type-1幻觉。")
        print("     但在真实用户场景中，用户回答后LLM续跑，幻觉可能在续跑中发生。")
        print(f"  2. {len(guardian)}/{len(details)} 的种子被 guardian 暂停——")
        print("     守护者拦截了部分风险，但这也意味着LLM的输出有被拦截的问题。")
        print(f"  3. {len(no_artifact_completed)} 个completed但无产物的种子——")
        print("     LLM调了工具但工具失败（如缺少_user_id），最终无产物完成。")
        print("     这不是幻觉，但也不是正常产出。")
        print("")
        print("  核心问题：当前测试集无法触发续跑幻觉。")
        print("  因为 clarification_paused 后session就结束了，")
        print("  没有模拟'用户回答后LLM续跑'这个最危险的阶段。")
        print("  真实幻觉（如用户最初报告的'封面做好了'）发生在续跑中。")

if __name__ == "__main__":
    main()