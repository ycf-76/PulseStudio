"""诊断：转录 + LLM 规划两步分开验证，定位 no overlays planned 的原因。"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

VIDEO = Path(r"D:\My_Project\多智能体小红书发布平台\backend\uploads\videos\OwdN5Jwjo_9XA_iAvuht-Q\01M5VDAHJR83J07D32SN14FW0P.mp4")


async def main() -> int:
    from capabilities.video.adapters.whisper_transcribe import transcribe

    print("== step 1: transcribe ==")
    try:
        segments = await asyncio.to_thread(
            transcribe, str(VIDEO), model_size="small", language="zh",
        )
        print(f"segments: {len(segments)}")
        for s in segments[:3]:
            print(f"  [{s.start:.1f}-{s.end:.1f}] {s.text[:40]} words={len(s.words)}")
    except Exception as e:
        print(f"TRANSCRIBE FAILED: {type(e).__name__}: {e}")
        return 1

    if not segments:
        print("NO SEGMENTS — transcription empty")
        return 1

    print("== step 2: llm ==")
    try:
        from app.adapters.deepseek import DeepSeekAdapter
        from app.config import Settings
        st = Settings()
        key = st.deepseek_api_key
        print(f"api_key present: {bool(key)}")
        llm = DeepSeekAdapter(api_key=key)
    except Exception as e:
        print(f"LLM INIT FAILED: {type(e).__name__}: {e}")
        llm = None

    seg_dicts = [
        {
            "index": i, "start": s.start, "end": s.end, "text": s.text,
            "words": [{"text": w.text, "start": w.start, "end": w.end} for w in s.words],
        }
        for i, s in enumerate(segments)
    ]
    from capabilities.video.services.overlay_orchestrator import generate_overlay_plan

    try:
        plan, raw = await generate_overlay_plan(seg_dicts, segments[-1].end, llm)
        print(f"plan segments: {len(plan.segments)}")
        for s in plan.segments[:8]:
            params_brief = {k: (str(v)[:30]) for k, v in s.params.items()}
            print(f"  [{s.time_start:.1f}-{s.time_end:.1f}] {s.template.value} params={params_brief}")
        return 0
    except Exception as e:
        print(f"PLAN FAILED: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
