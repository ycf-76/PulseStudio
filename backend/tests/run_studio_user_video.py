"""用户真实视频全流水线试跑驱动（真 LLM + 真渲染）。

与 test_studio_p2_acceptance.py 的关系：复用其真跑要件（backend/.env 注入
DEEPSEEK_API_KEY + get_settings 缓存清理 → resolve_llm；whisper 子进程隔离
防 ct2 cuDNN DLL 污染同进程 torch CUDA 的 RVM 抠像；SSE publish 打桩收集
事件），但独立成脚本而非 pytest——单视频、单打法（builtin general_talk）、
无 brief，验收脚本的双支对比 / git 冻结 / 切片逻辑均不适用。

用法（仓库根）：
  backend\\.venv\\Scripts\\python backend\\tests\\run_studio_user_video.py
可选环境变量：
  STUDIO_VIDEO  源视频路径（默认 backend/outputs/studio/source_uploads/user_test_36s.mp4）

结果摘要写 outputs/studio/_user_video_run.json 供取证；失败时 exit 1 并
打印 chunk 的 error/review 摘要（任一环节失败都是有效数据，如实记录）。
"""

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
# 主进程必须显式入 path：系统 Python 的 site-packages 里有无关的第三方
# `app` 包（app.module_one），不插首位会让 `import app.config` 解析到它
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
ENV_FILE = BACKEND / ".env"
STUDIO_ROOT = BACKEND / "outputs" / "studio"
DEFAULT_VIDEO = STUDIO_ROOT / "source_uploads" / "user_test_36s.mp4"

# 子进程 whisper 转录器（与验收脚本同一模板）：stdout 打 @@TRANSCRIPT@@<json> 哨兵行
_WHISPER_RUNNER = (
    "import json, sys\n"
    "sys.path.insert(0, r'%s')\n"
    "from capabilities.video.adapters.whisper_transcribe import transcribe\n"
    "segs = transcribe(r'%s')\n"
    "out = []\n"
    "for s in segs:\n"
    "    out.append({'text': s.text, 'start': s.start, 'end': s.end,\n"
    "                'words': [{'text': w.text, 'start': w.start, 'end': w.end}\n"
    "                         for w in (s.words or [])]})\n"
    "print('@@TRANSCRIPT@@' + json.dumps(out, ensure_ascii=False))\n"
)


def _subprocess_transcribe(audio_path, model_size="small", language=None):
    """whisper 子进程隔离：防 ct2 cuDNN DLL 污染同进程 torch CUDA（RVM）。"""
    code = _WHISPER_RUNNER % (BACKEND, Path(audio_path))
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(BACKEND),
        capture_output=True,
        text=True,
        timeout=900,
    )
    for line in (proc.stdout or "").splitlines():
        if line.startswith("@@TRANSCRIPT@@"):
            raw = json.loads(line[len("@@TRANSCRIPT@@") :])
            from capabilities.video.adapters.whisper_transcribe import (
                TranscribeSegment,
                TranscribeWord,
            )

            return [
                TranscribeSegment(
                    text=s["text"],
                    start=s["start"],
                    end=s["end"],
                    words=[
                        TranscribeWord(text=w["text"], start=w["start"], end=w["end"])
                        for w in s.get("words") or []
                    ],
                )
                for s in raw
            ]
    raise RuntimeError(
        f"子进程 whisper 转录失败 rc={proc.returncode}：{(proc.stderr or '')[-500:]}"
    )


async def main() -> int:
    # 真 LLM / 真生图就绪：backend/.env 的缺失 key 全量注入环境（cwd 无关，
    # 不覆盖已有变量）+ 清 get_settings 缓存；只挑 DEEPSEEK_API_KEY 会让
    # DASHSCOPE_API_KEY 缺失 → generate_image 报不可用（第七次跑片教训）
    if ENV_FILE.is_file():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            k, _, v = s.partition("=")
            k = k.strip()
            if k and not os.environ.get(k):
                os.environ[k] = v.strip().strip('"').strip("'")
    from app.config import get_settings

    get_settings.cache_clear()
    from app.adapters.model_router import get_model_router

    llm = get_model_router().resolve_llm()
    if llm is None:
        print("FATAL: DEEPSEEK_API_KEY 未配置（backend/.env）", file=sys.stderr)
        return 2

    # whisper 子进程隔离（脚本进程级替换，无需 pytest monkeypatch）
    from capabilities.video.services import studio_agent as sa

    sa.transcribe = _subprocess_transcribe

    # SSE publish 打桩收集（进度监控 + 事件计数）
    import app.services.sse_bus as sse_mod

    events: list[dict] = []

    async def fake_publish(workflow_id, event_type, payload, turn_id=None):
        events.append(dict(payload))
        ph = payload.get("phase") or event_type
        print(f"[sse] {ph} {payload.get('tool') or ''}", flush=True)

    sse_mod.sse_bus.publish = fake_publish

    video = Path(os.environ.get("STUDIO_VIDEO") or DEFAULT_VIDEO)
    if not video.is_file():
        print(f"FATAL: 源视频不存在: {video}", file=sys.stderr)
        return 2
    print(f"[driver] video={video} size={video.stat().st_size}", flush=True)
    # 打法覆盖：STUDIO_METHOD_PATH 指向外部打法文本或目录（load_method 的 path
    # 通道：.md 文件直接读；目录读其下 method.md）；缺省 builtin general_talk。
    # 目录形态支持多文件打法包：install 打桩把目录内 method.md 以外的全部文件
    # 原样拷入 ws 的 inputs/rules/（list_rules 递归可见，agent 分阶段 read_rules_file）
    method_path = os.environ.get("STUDIO_METHOD_PATH")
    method_dir: Path | None = None
    if method_path:
        mp = Path(method_path)
        if not mp.is_file() and not mp.is_dir():
            print(f"FATAL: 打法路径不存在: {method_path}", file=sys.stderr)
            return 2
        method = {"path": method_path}
        if mp.is_dir():
            if not (mp / "method.md").is_file():
                print(f"FATAL: 打法目录下缺 method.md: {method_path}", file=sys.stderr)
                return 2
            method_dir = mp
        print(f"[driver] method_path={method_path} (dir={method_dir is not None})", flush=True)
    else:
        method = {"builtin": "general_talk"}
        print("[driver] method=builtin general_talk", flush=True)

    from capabilities.video.skills import video_studio as vs_mod

    if method_dir is not None:
        _orig_install = vs_mod.install_into_workspace

        def _install_with_bundle(ws, mtd, _orig=_orig_install, _src=method_dir):
            p = _orig(ws, mtd)
            n = 0
            for f in sorted(_src.rglob("*")):
                if f.is_file() and f.name != "method.md":
                    dst = Path(ws["inputs"]) / "rules" / f.relative_to(_src)
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    dst.write_bytes(f.read_bytes())
                    n += 1
            print(f"[driver] bundle extras -> rules/: {n} files", flush=True)
            return p

        vs_mod.install_into_workspace = _install_with_bundle

    skill = vs_mod.VideoStudioSkill()
    brief = os.environ.get("STUDIO_BRIEF", "")
    if brief:
        print(f"[driver] brief={brief[:80]}...", flush=True)
    canvas = os.environ.get("STUDIO_CANVAS", "") or None
    if canvas:
        print(f"[driver] canvas 硬约束={canvas}", flush=True)
    require_fx = os.environ.get("STUDIO_REQUIRE_FX", "").strip().lower() in ("1", "true", "yes")
    if require_fx:
        print("[driver] required_fx 硬校验=开（effects 申报 dx≥40px |rot|≥2.5°）", flush=True)
    t0 = time.perf_counter()
    chunks = []
    async for c in skill.execute(
        {
            "video_path": str(video),
            "method": method,
            "brief": brief,
            "canvas": canvas,
            "require_fx": require_fx,
            "llm": llm,
            "_user_id": "user_video_test",
            "_workflow_id": "user-video-36s",
        }
    ):
        chunks.append(c)
    elapsed = time.perf_counter() - t0

    if len(chunks) != 1:
        print(f"FATAL: execute 应 yield 1 次，实际 {len(chunks)}", file=sys.stderr)
        return 1
    chunk = chunks[0]
    phases = [e.get("phase") for e in events if e.get("phase")]
    preview_events = [e for e in events if e.get("preview_url")]
    print(
        f"\n[driver] elapsed={elapsed:.1f}s turns={chunk.get('turns')} "
        f"success={chunk.get('success')}",
        flush=True,
    )
    print(
        f"[driver] sse_events={len(events)} preview_events={len(preview_events)} "
        f"phases={phases}",
        flush=True,
    )
    if not chunk.get("success"):
        print(f"[driver] ERROR={chunk.get('error')!r}", file=sys.stderr)
        print(f"[driver] review={chunk.get('review')!r}", file=sys.stderr)

    # 摘要落盘供取证
    summary = {
        "video": str(video),
        "success": bool(chunk.get("success")),
        "film_id": Path(chunk["workspace"]).name if chunk.get("workspace") else None,
        "workspace": chunk.get("workspace"),
        "video_path": chunk.get("video_path"),
        "turns": chunk.get("turns"),
        "elapsed_s": round(elapsed, 1),
        "error": chunk.get("error"),
        "review": chunk.get("review_summary"),
        "preview_url": chunk.get("preview_url"),
        "preview_events": len(preview_events),
        "sse_events": len(events),
        "phases": phases,
    }
    STUDIO_ROOT.mkdir(parents=True, exist_ok=True)
    (STUDIO_ROOT / "_user_video_run.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    # 全量 SSE 事件落盘：失败取证不再靠猜（第八次同签名×4 止损无法定位教训）
    (STUDIO_ROOT / "_user_video_run_events.json").write_text(
        json.dumps(events, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[driver] summary -> {STUDIO_ROOT / '_user_video_run.json'}", flush=True)
    return 0 if chunk.get("success") else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
