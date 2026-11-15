"""10.19 对话式修改层 真机验收脚本（standalone，非 CI 测试）。

直接运行（backend 目录下）：
  .venv\\Scripts\\python.exe tests\\test_revision_live.py

验收内容（docs 10.19「TDD 与验收」标准）：
  1. 初次规划：22s 样本 + storyboard 转录 → LLM 真规划（两步链）
  2. local 路由：喂"第 2 个字幕的字号改大一点" → 解析出 local UPDATE 指令
     → patch 后仅该段 params 变化、其余段逐字节一致
  3. global 路由：喂"整体再紧凑点" → 解析归 global → feedback 全量重规划出有效 plan

说明：skill 层路由（revision 参数 → 修改路径 / 事件流 / 轮数护栏）由
tests/test_overlay_revision.py::TestSkillRevisionRoute 单测覆盖（mock LLM），
本脚本只验真实 LLM 的意图解析与 patch 语义。
"""
import asyncio
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

VIDEO = BACKEND / "uploads" / "videos" / "verify" / "01M60QX2QHZBV9VAY3ZABMV2MY.mp4"
STORYBOARD = BACKEND / "outputs" / "夏天出门前必做的三件小事" / "assets" / "storyboard.json"

LOCAL_RAW = "第 2 个字幕的字号改大一点"
GLOBAL_RAW = "整体再紧凑点"


def _plan_dump(plan) -> list[dict]:
    return [s.model_dump() for s in plan.segments]


def _diff_segments(orig: list[dict], new: list[dict]) -> dict[str, set[str]]:
    """逐段对比 → {segment_id: 变了的顶层键集合}。"""
    changed = {}
    for a, b in zip(orig, new):
        if a != b:
            changed[a["segment_id"]] = {k for k in a if a[k] != b[k]}
    return changed


async def main() -> None:
    from capabilities.video.adapters.silence_detect import probe_duration
    from capabilities.video.services.overlay_orchestrator import (
        apply_revision,
        generate_overlay_plan,
        parse_revision_intent,
    )

    sb = json.loads(STORYBOARD.read_text(encoding="utf-8"))
    transcript, t = [], 0.0
    for i, shot in enumerate(sb["shots"]):
        d = float(shot.get("duration", 3.0))
        transcript.append({
            "index": i, "start": t, "end": t + d,
            "text": shot["caption"], "words": [],
        })
        t += d

    duration = await probe_duration(str(VIDEO))
    print(f"[live] 源视频: {VIDEO.name}（{duration:.1f}s），转录 {len(transcript)} 段")

    from app.adapters.deepseek import DeepSeekAdapter
    from app.config import Settings
    llm = DeepSeekAdapter(api_key=Settings().deepseek_api_key)

    # ── Step 1: 初次规划（两步链）──
    plan, _ = await generate_overlay_plan(transcript, duration, llm, use_two_step=True)
    if not plan.segments:
        print("[live] FAIL：初次规划为空")
        sys.exit(1)
    print(f"[live] 初次规划 {len(plan.segments)} 段：")
    for s in plan.segments:
        print(f"  {s.segment_id}: {s.time_start:.1f}-{s.time_end:.1f}s "
              f"{s.template}（skipped={s.skipped}）")

    # ── Step 2: local 路由 — 改第 2 个字幕的字号 ──
    result, problems = await parse_revision_intent(llm, LOCAL_RAW, plan, duration)
    if problems or result is None or result.route != "local" or not result.instructions:
        route = getattr(result, "route", None)
        n_ins = len(result.instructions) if result else 0
        print(f"[live] FAIL：local 解析 route={route} instructions={n_ins} problems={problems}")
        sys.exit(1)
    for ins in result.instructions:
        print(f"[live] local 指令：{ins.action} → {ins.segment_ids}（依据：{ins.locate_hint}）"
              + (f" params={ins.params}" if ins.params else ""))

    patched, patch_problems = apply_revision(plan, result.instructions, duration)
    if patch_problems:
        print(f"[live] FAIL：patch 自检问题 {patch_problems}")
        sys.exit(1)

    orig, new = _plan_dump(plan), _plan_dump(patched)
    if len(orig) != len(new):
        print("[live] FAIL：段数变化（UPDATE 不应增删段）")
        sys.exit(1)
    changed = _diff_segments(orig, new)
    targets = {sid for ins in result.instructions for sid in ins.segment_ids}
    if set(changed) != targets:
        print(f"[live] FAIL：实际变化段 {set(changed)} ≠ 指令目标 {targets}")
        sys.exit(1)
    bad_keys = {k for keys in changed.values() for k in keys} - {"params"}
    if bad_keys:
        print(f"[live] FAIL：改了 params 之外的键 {bad_keys}")
        sys.exit(1)
    for sid in sorted(changed):
        seg = next(s for s in patched.segments if s.segment_id == sid)
        old = next(s for s in plan.segments if s.segment_id == sid)
        print(f"[live] 仅 {sid} params 变化：{old.params} → {seg.params}")
    print("[live] local 验收 OK：仅目标段 params 变化，其余段逐字节一致")

    # ── Step 3: global 路由 — 整体紧凑 ──
    result2, problems2 = await parse_revision_intent(llm, GLOBAL_RAW, plan, duration)
    if problems2 or result2 is None or result2.route != "global":
        route = getattr(result2, "route", None)
        print(f"[live] FAIL：global 解析 route={route} problems={problems2}")
        sys.exit(1)
    print(f"[live] global 解析 OK：{result2.reason}")

    replan, _ = await generate_overlay_plan(
        transcript, duration, llm, feedback=GLOBAL_RAW, use_two_step=True,
    )
    if not replan.segments:
        print("[live] FAIL：global 重规划为空")
        sys.exit(1)
    print(f"[live] global 重规划 {len(replan.segments)} 段（原 {len(plan.segments)} 段）：")
    for s in replan.segments:
        print(f"  {s.segment_id}: {s.time_start:.1f}-{s.time_end:.1f}s {s.template}")
    print("[live] PASS：10.19 真机验收完成")


if __name__ == "__main__":
    asyncio.run(main())
