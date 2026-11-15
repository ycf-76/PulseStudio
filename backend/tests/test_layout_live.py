"""G2 dynamic_layout 真机验收脚本（standalone，非 CI 测试）。

直接运行（backend 目录下）：
  .venv\\Scripts\\python.exe tests\\test_layout_live.py

验收内容（docs 10.18 真机验收标准）：
  1. LLM 真规划：22s 样本 + storyboard 配图 → 三段式 timeline
     （FULL 开场 → PIP/BROLL 讲核心 → FULL 收束）
  2. 确定性编译：分段渲染 + concat，成片时长 ≈ 源片
  3. VLM 布局审查：每段中点抽帧看构图
  4. 降级链不在本脚本范围（单测已覆盖）
"""
import asyncio
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

VIDEO = BACKEND / "uploads" / "videos" / "verify" / "01M60QX2QHZBV9VAY3ZABMV2MY.mp4"
STORYBOARD = BACKEND / "outputs" / "夏天出门前必做的三件小事" / "assets" / "storyboard.json"


async def main() -> None:
    from capabilities.video.skills.dynamic_layout import DynamicLayoutSkill

    sb = json.loads(STORYBOARD.read_text(encoding="utf-8"))
    brolls = [s["image"] for s in sb["shots"] if Path(s["image"]).is_file()]
    missing = [s["image"] for s in sb["shots"] if not Path(s["image"]).is_file()]
    print(f"[live] 源视频: {VIDEO.name}")
    print(f"[live] 配图: {len(brolls)} 张可用" + (f"，缺失 {len(missing)}" if missing else ""))

    # captions → 转录时间轴（storyboard durations 累计）
    transcript, t = [], 0.0
    for shot in sb["shots"]:
        d = float(shot.get("duration", 3.0))
        transcript.append({"start": t, "end": t + d, "text": shot["caption"]})
        t += d
    print(f"[live] 转录: {len(transcript)} 段，累计 {t:.1f}s")

    skill = DynamicLayoutSkill()
    events = []
    async for ev in skill.execute({
        "video_path": str(VIDEO),
        "gate_mode": "auto_confirm",
        "brolls": brolls,
        "transcript": transcript,
    }):
        print(f"[event] {json.dumps(ev, ensure_ascii=False)[:400]}")
        events.append(ev)

    done = [e for e in events if e.get("event") == "layout_compose_done"]
    errors = [e for e in events if e.get("event") == "layout_error"]
    if errors:
        print(f"[live] FAIL：layout_error = {errors[0]}")
        sys.exit(1)
    if not done:
        print("[live] FAIL：没有 layout_compose_done 事件")
        sys.exit(1)

    out = Path(done[0]["data"]["output_path"])
    timeline = done[0]["data"]["timeline"]
    print("\n[live] 布局时间轴：")
    for i, seg in enumerate(timeline["segments"], 1):
        print(
            f"  段{i}: {seg['start']:.1f}-{seg['end']:.1f}s "
            f"{seg['primitive']}"
            + (f" @{seg.get('pip_position')} x{seg.get('pip_scale')}" if seg["primitive"] == "pip" else "")
            + f" main={seg.get('main_source')} — {seg.get('reason', '')}"
        )
    print(f"\n[live] 成片: {out}")
    print(f"[live] 存在: {out.is_file()}，大小: {out.stat().st_size / 1024:.0f} KB")

    if not out.is_file():
        print("[live] FAIL：成片不存在")
        sys.exit(1)

    # 成片时长校验 ≈ 源片
    from capabilities.video.adapters.silence_detect import probe_duration
    src_d, out_d = await probe_duration(str(VIDEO)), await probe_duration(str(out))
    print(f"[live] 时长: 源 {src_d:.2f}s → 成片 {out_d:.2f}s（差 {abs(src_d - out_d):.2f}s）")
    if abs(src_d - out_d) > 0.5:
        print("[live] FAIL：成片时长偏差 > 0.5s")
        sys.exit(1)

    primitives = [s["primitive"] for s in timeline["segments"]]
    n_kinds = len(set(primitives))
    print(f"\n[live] 原语分布: {primitives}（{n_kinds} 种）")
    if n_kinds < 2:
        print("[live] WARN：单原语布局（LLM 没做构图切换）— 如非内容所致需排查 prompt")
    print("[live] PASS：真机验收完成")


if __name__ == "__main__":
    asyncio.run(main())
