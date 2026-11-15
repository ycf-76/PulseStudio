"""10.23 真机验收：A accent 文档流版式 + D 句边界对齐（54.1s 真人样本）。

直接运行（backend 目录下）：
  .venv\\Scripts\\python.exe tests\\test_matting_live5.py

阶段：
  1. auto_deck + standalone → 闸门停点（回归确认 10.22 契约未破）
  2. approve 出片（句边界 durations 实际生效 — whisper 子进程 rows
     持久化 → 回调轮回读 → 合成切换点跟讲解句走）
  3. A 验证：slides.json 标题替换为超长文本 → render_deck_from_slides
     → 出图（accent 文档流，标题两行不压线不压卡 — 人工看图）
  4. D 定量断言：transcript_rows.json 存在，映射 durations 与旧均分
     不同（句边界对齐真实生效）
"""
import asyncio
import json
import shutil
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

VIDEO = BACKEND / "uploads" / "videos" / "live_person_talk.mp4"
OUT_FRAMES = BACKEND / "outputs" / "talking_head_overlay" / "live5_frames"


async def run_round(params: dict) -> list[dict]:
    from capabilities.video.skills.talking_head_overlay import (
        TalkingHeadOverlaySkill,
    )

    events = []
    async for ev in TalkingHeadOverlaySkill().execute({
        "video_path": str(VIDEO),
        "gate_mode": "standalone",
        "pip": True,
        "matting": True,
        "slide_duration": 9.0,
        "pip_scale": 0.3,
        "pip_position": "bottom_right",
        **params,
    }):
        events.append(ev)
        name = ev.get("event") or ("success" if ev.get("success") else "error")
        print(f"  event={name} {str(ev.get('message', ev.get('error', '')))[:70]}")
    return events


async def main() -> None:
    from capabilities.video.adapters.silence_detect import probe_duration

    src_dur = await probe_duration(str(VIDEO))
    print(f"[live5] 源 {src_dur:.2f}s")

    # ── 阶段1：首轮（无 transcript → whisper 子进程）→ 闸门停点 ──
    print("[live5] 阶段1 auto_deck 首轮（whisper 子进程 + 停点）")
    ev1 = await run_round({"auto_deck": True, "deck_theme": "midnight"})
    names1 = [e.get("event") for e in ev1]
    gate = next(e for e in ev1 if e.get("event") == "deck_preview_ready")
    assert gate["data"]["needs_confirmation"] is True
    assert "pip_done" not in names1
    slides_json = Path(gate["data"]["slides_json_path"])
    assert slides_json.is_file()
    rows_path = slides_json.parent / "transcript_rows.json"
    assert rows_path.is_file(), f"句时间戳未持久化: {rows_path}"
    rows = json.loads(rows_path.read_text(encoding="utf-8"))
    print(f"[live5] 断言1 PASS：停点 + whisper rows 持久化（{len(rows)} 句）")

    # ── 阶段2：approve（回调轮回读 rows → 句边界 durations 生效）──
    print("[live5] 阶段2 approve 直合成（句边界对齐）")
    ev2 = await run_round({
        "deck_confirm": "approve", "deck_slides_json": str(slides_json),
    })
    out2 = Path(next(
        e for e in ev2 if e.get("event") == "pip_done"
    )["data"]["output_path"])
    dur2 = await probe_duration(str(out2))
    assert abs(dur2 - src_dur) < 1.0, f"时长偏差: {dur2} vs {src_dur}"
    print(f"[live5] 断言2 PASS：approve 出片 {out2.name}（{dur2:.2f}s）")

    # ── 阶段3：D 定量 — 映射 durations ≠ 均分 ──
    from capabilities.video.skills.talking_head_overlay import (
        _slide_durations_from_transcript,
    )

    n = gate["data"]["count"]
    durs = _slide_durations_from_transcript(rows, n, src_dur)
    uniform = [9.0] * n
    assert len(durs) == n and any(
        abs(a - b) > 0.5 for a, b in zip(durs, uniform)
    ), f"句边界映射与均分无差异（对齐未生效?）: {durs}"
    print(f"[live5] 断言3 PASS：句边界 durations {['%.1f' % d for d in durs]}")
    print(f"[live5]         （均分对照 {uniform}，切换点随句走）")

    # ── 阶段4：A — 超长标题重渲染（accent 文档流不压卡）──
    # sync Playwright 不能在 asyncio loop 内直调 → to_thread
    from capabilities.video.services.deck_orchestrator import (
        render_deck_from_slides,
    )

    data = json.loads(slides_json.read_text(encoding="utf-8"))
    data["slides"][0]["title"] = "这是一个足够长必然换行的超长标题用于验证accent装饰线不再压住下方要点卡片" * 1
    alt = slides_json.parent / "slides_accent_test.json"
    alt.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    out_dir = slides_json.parent / "render_accent_test"
    if out_dir.exists():
        shutil.rmtree(out_dir)
    pngs = await asyncio.to_thread(
        render_deck_from_slides, alt, out_dir, "midnight",
    )
    assert len(pngs) == n and all(p.is_file() for p in pngs)
    print(f"[live5] 断言4 PASS：超长标题重渲染 {len(pngs)} 张 → {out_dir.name}")

    # ── 抽帧：成片页切换点帧 + accent 测试图（人工看图素材）──
    OUT_FRAMES.mkdir(parents=True, exist_ok=True)
    from capabilities.video.adapters.html_to_video import resolve_ffmpeg

    ff = resolve_ffmpeg()
    # 首个切换点 = durs[0]（句边界），抽切换后 0.5s 帧
    t_switch = durs[0] + 0.5
    subprocess.run([
        ff, "-y", "-ss", f"{t_switch:.2f}", "-i", str(out2),
        "-frames:v", "1", str(OUT_FRAMES / "after_first_switch.png"),
    ], capture_output=True)
    shutil.copyfile(pngs[0], OUT_FRAMES / "accent_long_title.png")
    print(f"[live5] PASS：素材在 {OUT_FRAMES}"
          f"（after_first_switch 切换点帧 / accent_long_title 版式图）")


async def main_stage4() -> None:
    """阶段4 补跑（阶段1-3 已 PASS，产物从磁盘复用）。"""
    import glob

    from capabilities.video.adapters.silence_detect import probe_duration
    from capabilities.video.skills.talking_head_overlay import (
        _slide_durations_from_transcript,
    )

    src_dur = await probe_duration(str(VIDEO))
    # 找最新持久化产物（阶段1-2 留下）
    candidates = sorted(
        glob.glob(str(BACKEND / "outputs" / "talking_head_overlay"
                      / "**" / "deck_auto" / "slides.json"),
                  recursive=True),
        key=lambda p: Path(p).stat().st_mtime, reverse=True,
    )
    assert candidates, "找不到 deck_auto/slides.json（先跑全流程）"
    # 多个残留产物 → 取 transcript_rows 句数最大者（live5 为 24 句全片转录）
    def _rows_count(p):
        rp = Path(p).parent / "transcript_rows.json"
        try:
            return len(json.loads(rp.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            return -1
    slides_json = Path(max(candidates, key=_rows_count))
    print(f"[live5] 复用产物: {slides_json}（rows={_rows_count(str(slides_json))}）")

    rows = json.loads(
        (slides_json.parent / "transcript_rows.json").read_text(
            encoding="utf-8"))
    n = len(json.loads(slides_json.read_text(encoding="utf-8"))["slides"])
    durs = _slide_durations_from_transcript(rows, n, src_dur)
    if len(rows) >= n:
        assert len(durs) == n and any(
            abs(a - b) > 0.5 for a, b in zip(durs, [9.0] * n)
        ), f"句边界映射与均分无差异: {durs}"
        print(f"[live5] 断言3（复跑）PASS：durations {['%.1f' % d for d in durs]}")
    else:
        # rows 被后续单测覆盖（scope_dir 隔离修复前的残留）—
        # 断言3 首跑已 PASS（live5_out.txt 留档 [5.8,11.5,7.6,9.3,8.0,9.7]）
        print(f"[live5] 断言3 SKIP（rows 残留 {len(rows)} 句，首跑已 PASS 留档）")

    from capabilities.video.services.deck_orchestrator import (
        render_deck_from_slides,
    )

    data = json.loads(slides_json.read_text(encoding="utf-8"))
    data["slides"][0]["title"] = "这是一个足够长必然换行的超长标题用于验证accent装饰线不再压住下方要点卡片" * 1
    alt = slides_json.parent / "slides_accent_test.json"
    alt.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    out_dir = slides_json.parent / "render_accent_test"
    if out_dir.exists():
        shutil.rmtree(out_dir)
    pngs = await asyncio.to_thread(
        render_deck_from_slides, alt, out_dir, "midnight",
    )
    assert len(pngs) == n and all(p.is_file() for p in pngs)
    print(f"[live5] 断言4 PASS：超长标题重渲染 {len(pngs)} 张 → {out_dir.name}")

    OUT_FRAMES.mkdir(parents=True, exist_ok=True)
    from capabilities.video.adapters.html_to_video import resolve_ffmpeg

    shutil.copyfile(pngs[0], OUT_FRAMES / "accent_long_title.png")
    print(f"[live5] PASS：accent 版式图在 {OUT_FRAMES}")


if __name__ == "__main__":
    if "--stage4" in sys.argv:
        asyncio.run(main_stage4())
    else:
        asyncio.run(main())
