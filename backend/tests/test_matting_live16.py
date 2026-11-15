# -*- coding: utf-8 -*-
"""10.31 live16 真机验收：N1 = 开场全屏 → t0 缩小让位 + 触发翻页。

直接运行（backend 目录下）：
  .venv\\Scripts\\python.exe tests\\test_matting_live16.py

链路：smart_cut → pip 首轮（auto_deck 规划停点）→ approve 回调轮
（触发翻页 slides → segB 全时长 PIP（matte 抠像右上角小窗）→
段A 原片全屏 → xfade 溶解让位 → acrossfade 音频）。

机器检查（终判仍由用户连播实看：让位是否自然、翻页是否跟口播）：
  - pip_done.handoff.t0 = 首页触发词命中时刻
  - 触发翻页起点计划可得（_trigger_slide_starts 非 None，
    触发优先 + miss 页句边界兜底）
  - 成片时长 ≈ 裁后时长（xfade/acrossfade 零漂移）
  - t0 前帧 = 原片全屏（顶带亮像素多）；t0 后帧 = 暗色 deck 主画面
  - t0 后帧右上角含 PIP 人像纹理（区域非平坦）
"""
import asyncio
import json
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

VIDEO = BACKEND / "uploads" / "videos" / "live12_src.mp4"
OUT_FRAMES = BACKEND / "outputs" / "talking_head_overlay" / "live16_frames"


async def main() -> None:
    # cuDNN 预热：主进程 cuDNN 首载若推迟到 chromium/whisper 子进程
    # 活动之后 → 系统旧 cuDNN 抢载（cudnnGetLibConfig error 127 →
    # 0xC0000409 fastfail，live16 两轮复现）；进程早期先加载 torch
    # 自带 cuDNN 则全程稳定（10.20 转录子进程隔离的同源问题，
    # 预热解法 — 诊断链 torch 探针全程未崩即此机理的反证）。
    import torch

    _ = (
        torch.nn.Conv2d(3, 3, 3).cuda()(
            torch.randn(1, 3, 8, 8, device="cuda")
        ).sum().item()
    )
    from PIL import Image

    from capabilities.video.adapters.silence_detect import probe_duration
    from capabilities.video.adapters.html_to_video import resolve_ffmpeg
    from capabilities.video.skills.smart_cut import SmartCutSkill
    from capabilities.video.skills.talking_head_overlay import (
        TalkingHeadOverlaySkill,
        _trigger_slide_starts,
    )

    ff = resolve_ffmpeg()
    src_dur = await probe_duration(str(VIDEO))
    print(f"[live16] 源 {src_dur:.2f}s  {VIDEO.name}")

    # ── 阶段0：智能裁剪 ──
    print("[live16] 阶段0 smart_cut（plan 轮）")
    sc = SmartCutSkill()
    plan = await sc.execute({
        "video_path": str(VIDEO),
        "detect_fluff": True,
        "chain_with_overlay": True,
    })
    assert plan.get("mode") == "plan", plan
    cands = plan["plan"]["candidates"]
    cut = await sc.execute({
        "video_path": str(VIDEO),
        "refine_candidates": cands,
        "chain_with_overlay": True,
    })
    cut_path, kept = cut["output_path"], cut["kept_segments"]
    cut_dur = await probe_duration(cut_path)
    print(f"    裁剪 {cut['original_duration']:.2f} → {cut_dur:.2f}s")

    # ── 阶段1：pip 首轮（auto_deck 规划 → 停点） ──
    base = {
        "video_path": str(VIDEO),
        "gate_mode": "standalone",
        "pip": True,
        "pip_position": "top_right",
        "pip_scale": 0.28,
        "matting": True,
        "auto_deck": True,
        "deck_theme": "auto",
        "cut_video_path": cut_path,
        "kept_segments": kept,
        "handoff": True,
    }

    async def run_round(extra: dict) -> list[dict]:
        events = []
        async for ev in TalkingHeadOverlaySkill().execute({**base, **extra}):
            events.append(ev)
            name = ev.get("event") or ("success" if ev.get("success") else "error")
            print(f"    event={name} {str(ev.get('message', ev.get('error', '')))[:70]}")
        return events

    print("[live16] 阶段1 pip 首轮（deck 规划 + 停点）")
    ev1 = await run_round({})
    gate = next(e for e in ev1 if e.get("event") == "deck_preview_ready")
    slides_json = Path(gate["data"]["slides_json_path"])
    data = json.loads(slides_json.read_text(encoding="utf-8"))
    slides = data["slides"]
    rows = json.loads(
        (slides_json.parent / "transcript_rows.json").read_text("utf-8")
    )
    print(f"[live16] 断言1：{len(slides)} 页 triggers="
          f"{[(s.get('trigger') or {}).get('text', '') if isinstance(s.get('trigger'), dict) else str(s.get('trigger') or '') for s in slides]}")

    # ── 断言1b：触发翻页起点计划可得（N1 纯函数真机数据验证，
    #    触发优先 + miss 页句边界兜底）──
    hit = _trigger_slide_starts(rows, slides, cut_dur)
    assert hit is not None, "触发翻页起点计划不可得 — 翻页将回退句边界"
    t0_exp, durs_exp = hit
    print(f"[live16] 断言1b：t0={t0_exp:.2f}s durations={[round(d, 2) for d in durs_exp]}")

    # ── 阶段2：approve 回调轮（触发翻页 + 让位合成） ──
    print("[live16] 阶段2 approve（触发翻页 + 让位合成）")
    ev2 = await run_round({
        "deck_confirm": "approve", "deck_slides_json": str(slides_json),
    })
    done = next(e for e in ev2 if e.get("event") == "pip_done")
    out = Path(done["data"]["output_path"])
    dur = await probe_duration(str(out))
    # 断言2：handoff 生效 + 时长零漂移
    ho = done["data"].get("handoff")
    assert ho, f"handoff 未生效：{done['data']}"
    assert abs(ho["t0"] - t0_exp) < 0.05, f"t0 不一致：{ho} vs {t0_exp}"
    assert abs(dur - cut_dur) < 0.6, f"时长漂移：{dur:.2f} vs {cut_dur:.2f}"
    assert done["data"].get("matting") is True, done["data"]
    print(f"[live16] 断言2：handoff t0={ho['t0']}s，成片 {dur:.2f}s ≈ 裁后 "
          f"{cut_dur:.2f}s，位置={done['data']['pip_position']}")

    # ── 断言3：t0 前后画面形态（全屏人 → 暗色 deck + 角落人） ──
    OUT_FRAMES.mkdir(parents=True, exist_ok=True)

    def grab(t: float, name: str) -> Path:
        fp = OUT_FRAMES / name
        subprocess.run(
            [ff, "-y", "-ss", f"{max(0.0, t):.2f}", "-i", str(out),
             "-frames:v", "1", str(fp)],
            capture_output=True,
        )
        return fp

    def top_white(fp: Path) -> int:
        img = Image.open(fp).convert("RGB")
        W, H = img.size
        band = img.crop((0, int(H * 0.05), W, int(H * 0.32)))
        return sum(1 for r, g, b in band.getdata()
                   if r > 215 and g > 215 and b > 215)

    t0 = ho["t0"]
    if t0 >= 1.5:
        full_f = grab(t0 - 0.3, f"before_{t0 - 0.3:.2f}s.png")
        deck_f = grab(t0 + 1.2, f"after_{t0 + 1.2:.2f}s.png")
        wf, wd = top_white(full_f), top_white(deck_f)
        print(f"[live16] 断言3：t0 前（全屏人）顶带白 {wf} vs t0 后（deck）{wd}")
        assert wf > wd * 1.2, f"t0 前后画面形态未切换（{wf} vs {wd}）"
    else:
        # 触发词在开头即命中（说到才翻语义下 t0≈0 合法）→ 无全屏段，
        # 亮度对比代理失效（原片开头可能整体偏暗），跳过形态对比
        deck_f = grab(t0 + 1.2, f"after_{t0 + 1.2:.2f}s.png")
        print(f"[live16] 断言3：t0={t0:.2f}s 过早（触发词即出口）— "
              f"无全屏段，跳过形态对比")

    # ── 断言4：t0 后右上角 PIP 人像纹理存在 ──
    img = Image.open(deck_f).convert("L")
    W, H = img.size
    region = img.crop((int(W * 0.72), int(H * 0.02), W, int(H * 0.24)))
    px = list(region.getdata())
    mean = sum(px) / len(px)
    textured = sum(1 for v in px if abs(v - mean) > 15)
    print(f"[live16] 断言4：右上 PIP 区纹理像素 {textured}（均值 {mean:.0f}）")
    assert textured > 500, "右上角未检出 PIP 人像纹理"

    print(f"[live16] PASS：{src_dur:.2f}s → {dur:.2f}s，{len(slides)} 页触发翻页，"
          f"t0={t0:.2f}s 让位，PIP={done['data']['pip_position']}")
    print(f"[live16] 成片：{out}")
    print(f"[live16] 抽帧：{OUT_FRAMES}（请连播实看：开场全屏→让位是否自然、"
          f"翻页是否说到才翻、小窗只露头效果）")


if __name__ == "__main__":
    asyncio.run(main())
