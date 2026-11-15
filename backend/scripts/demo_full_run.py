"""全链路演示：对测试视频跑 talking_head_overlay skill（AUTO_CONFIRM）。

产出：
  1. outputs/talking_head_overlay/<stem>_overlay.mp4 — 最终成片
  2. outputs/demo_run/report.json — 规划统计 + 事件日志
  3. outputs/demo_run/cmp_*.png — 原始帧 vs 成片帧（同时间点，肉眼对比用）
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

VIDEO = Path(r"D:\My_Project\多智能体小红书发布平台\backend\uploads\videos\OwdN5Jwjo_9XA_iAvuht-Q\01M5VDAHJR83J07D32SN14FW0P.mp4")
OUT_DIR = Path(__file__).parent.parent / "outputs" / "demo_run"


async def main() -> int:
    from capabilities.video.skills.talking_head_overlay import (
        GateMode,
        TalkingHeadOverlaySkill,
    )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    skill = TalkingHeadOverlaySkill()

    events: list[dict] = []
    t0 = time.time()
    async for chunk in skill.execute({
        "video_path": str(VIDEO),
        "gate_mode": GateMode.AUTO_CONFIRM,
    }):
        events.append(chunk)
        event = chunk.get("event", "?")
        if event == "?" :
            print(f"CHUNK: {json.dumps(chunk, ensure_ascii=False, default=str)[:500]}")
        elif event == "overlay_compose_done":
            print(f"[{time.time()-t0:.0f}s] DONE: {chunk['data']['output_path']}")
            print(f"       overlays: {chunk['data']['overlay_count']}")
        elif event == "overlay_review_done":
            d = chunk["data"]
            retry = " → 带反馈重规划重渲" if d["will_retry"] else ""
            print(
                f"[{time.time()-t0:.0f}s] REVIEW round={d['round']} "
                f"passed={d['passed']} issues={d['issue_count']} "
                f"critical={d['critical_count']}{retry}"
            )
            print(f"       {chunk.get('message', '')}")
        elif event == "overlay_error":
            print(f"ERROR: {chunk['data']}")
            return 1
        else:
            print(f"[{time.time()-t0:.0f}s] event={event}")

    # 事件摘要落盘
    plan_event = next((e for e in events if e.get("event") == "overlay_plan_ready"), None)
    done_event = next((e for e in events if e.get("event") == "overlay_compose_done"), None)
    review_events = [e["data"] for e in events if e.get("event") == "overlay_review_done"]
    # 规划优先取终版（P0 重规划后 compose 事件带 final_plan）；
    # initial_plan 保留初版，用于对比"审查反馈改了什么"
    result_data = dict(done_event["data"]) if done_event else None
    final_plan = (result_data or {}).pop("final_plan", None)
    report = {
        "elapsed_s": round(time.time() - t0, 1),
        "plan": final_plan or (plan_event["data"] if plan_event else None),
        "initial_plan": plan_event["data"] if plan_event else None,
        "result": result_data,
        # P0 视觉自审闭环事件（轮次/问题数/issue 明细/是否重试）
        "reviews": review_events,
    }
    (OUT_DIR / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if not done_event:
        print(f"未完成：events={[e.get('event') for e in events]}")
        return 1

    # 抽帧对比：原始 vs 成片（均匀采样叠加段，覆盖全片）
    from capabilities.video.adapters.html_to_video import resolve_ffmpeg

    ffmpeg = resolve_ffmpeg()
    final = Path(done_event["data"]["output_path"])
    segments = (report["plan"] or {}).get("segments", [])
    if len(segments) > 6:
        step = len(segments) / 6
        segments = [segments[int(i * step)] for i in range(6)]
    shots = []
    for i, seg in enumerate(segments):
        mid = (seg["time_start"] + seg["time_end"]) / 2
        for tag, src in (("orig", VIDEO), ("out", final)):
            p = OUT_DIR / f"cmp_{tag}_{i}_{mid:.1f}s.png"
            subprocess_run(ffmpeg, src, mid, p)
            shots.append(str(p))
    print(f"compare frames: {len(shots)}")
    return 0


def subprocess_run(ffmpeg, src: Path, ts: float, out: Path) -> None:
    import subprocess

    subprocess.run(
        [ffmpeg, "-y", "-loglevel", "error", "-ss", str(ts), "-i", str(src),
         "-vframes", "1", str(out)],
        capture_output=True, check=False,
    )


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
