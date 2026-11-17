"""10.22 真机验收：deck 确认闸门全流程（首轮停点 → approve → retheme）。

直接运行（backend 目录下）：
  .venv\\Scripts\\python.exe tests\\test_matting_live4.py

流程（54.1s 真人口播样本，DeepSeek 真调 + RVM 真抠）：
  阶段1 首轮：auto_deck + standalone → deck_preview_ready 停点
        （needs_confirmation=True，无 pip_done — 必须停）
  阶段2 approve：deck_confirm=approve 回传 slides.json → 零 LLM 直合成
  阶段3 retheme：deck_confirm=retheme + deck_theme=cream → 零 LLM
        重渲染 → cream 版成片（三主题版式对比素材）
每阶段独立 execute（真实跨轮回调契约），RVM 每轮真跑（无跨轮帧缓存）。
"""

import asyncio
import json
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

VIDEO = BACKEND / "uploads" / "videos" / "live_person_talk.mp4"
TRANSCRIPT = BACKEND / "outputs" / "matting_live2" / "transcript.json"
OUT_FRAMES = BACKEND / "outputs" / "talking_head_overlay" / "live4_frames"


async def run_round(params: dict) -> list[dict]:
    from capabilities.video.skills.talking_head_overlay import (
        TalkingHeadOverlaySkill,
    )

    events = []
    async for ev in TalkingHeadOverlaySkill().execute(
        {
            "video_path": str(VIDEO),
            "gate_mode": "standalone",
            "pip": True,
            "matting": True,
            "transcript": json.loads(TRANSCRIPT.read_text(encoding="utf-8")),
            "slide_duration": 9.0,
            "pip_scale": 0.3,
            "pip_position": "bottom_right",
            **params,
        }
    ):
        events.append(ev)
        name = ev.get("event") or ("success" if ev.get("success") else "error")
        print(f"  event={name} {ev.get('message', ev.get('error', ''))[:70]}")
    return events


async def main() -> None:
    from capabilities.video.adapters.silence_detect import probe_duration

    src_dur = await probe_duration(str(VIDEO))
    print(f"[live4] 源 {src_dur:.2f}s，闸门三阶段验收")

    # ── 阶段1：首轮生成 → 必须停在闸门 ──
    print("[live4] 阶段1 首轮 auto_deck（standalone，应停）")
    ev1 = await run_round({"auto_deck": True, "deck_theme": "midnight"})
    names1 = [e.get("event") for e in ev1]
    assert "deck_preview_ready" in names1, f"首轮应停闸门: {names1}"
    gate = next(e for e in ev1 if e.get("event") == "deck_preview_ready")
    assert gate["data"]["needs_confirmation"] is True
    assert "pip_done" not in names1, "闸门停点后不得合成"
    slides_json = gate["data"]["slides_json_path"]
    assert Path(slides_json).is_file(), f"slides.json 未落盘: {slides_json}"
    print(
        f"[live4] 断言1 PASS：停点 {gate['data']['count']} 张"
        f"（{gate['data']['theme']}），slides.json 已持久化"
    )

    # ── 阶段2：approve → 零 LLM 直合成 ──
    print("[live4] 阶段2 approve 直合成")
    ev2 = await run_round(
        {
            "deck_confirm": "approve",
            "deck_slides_json": slides_json,
        }
    )
    names2 = [e.get("event") for e in ev2]
    assert "pip_done" in names2, f"approve 应出片: {names2}"
    out2 = Path(next(e for e in ev2 if e.get("event") == "pip_done")["data"]["output_path"])
    dur2 = await probe_duration(str(out2))
    assert abs(dur2 - src_dur) < 1.0, f"时长偏差: {dur2} vs {src_dur}"
    print(f"[live4] 断言2 PASS：approve 出片 {out2.name}（{dur2:.2f}s）")

    # ── 阶段3：retheme midnight → cream（零 LLM 重渲染）──
    print("[live4] 阶段3 retheme → cream")
    ev3 = await run_round(
        {
            "deck_confirm": "retheme",
            "deck_theme": "cream",
            "deck_slides_json": slides_json,
        }
    )
    names3 = [e.get("event") for e in ev3]
    assert "pip_done" in names3, f"retheme 应出片: {names3}"
    ready3 = next(e for e in ev3 if e.get("event") == "deck_ready")
    assert ready3["data"]["theme"] == "cream"
    out3 = Path(next(e for e in ev3 if e.get("event") == "pip_done")["data"]["output_path"])
    dur3 = await probe_duration(str(out3))
    print(f"[live4] 断言3 PASS：cream 出片 {out3.name}（{dur3:.2f}s）")

    # ── 抽帧：midnight vs cream 首帧对比（三主题版式素材）──
    OUT_FRAMES.mkdir(parents=True, exist_ok=True)
    from capabilities.video.adapters.html_to_video import resolve_ffmpeg

    ff = resolve_ffmpeg()
    for tag, vid in (("approve_midnight", out2), ("retheme_cream", out3)):
        subprocess.run(
            [
                ff,
                "-y",
                "-ss",
                "1.5",
                "-i",
                str(vid),
                "-frames:v",
                "1",
                str(OUT_FRAMES / f"{tag}.png"),
            ],
            capture_output=True,
        )
    print(f"[live4] PASS：闸门全流程完成，首帧对比在 {OUT_FRAMES}")


if __name__ == "__main__":
    asyncio.run(main())
