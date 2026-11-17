"""Task 6：P2 验收实验（live，真 LLM + 真渲染；STUDIO_ACCEPTANCE=1 门控）。

对照计划判据（docs/superpowers/plans/2026-11-10-video-studio-p2.md Task 6）：
  - live_person_talk.mp4 前 15s，分别以 general_talk / minimal_cards 各跑一支
  - 两支都 success=True、成片存在、review errors 为空
  - 两支差异真实：timeline beats 内容不同（非 film_id/时间戳元数据）、
    scene/index.html 内容不同
  - 产出在 backend/outputs/studio/<film_id>/（video_studio skill 保证，此处复核）

真跑语义与进程隔离：
  - LLM：get_model_router().resolve_llm()（真 DeepSeek）。get_settings 的
    env_file=".env" 相对 cwd（仓库根跑 pytest 时读不到 backend/.env）且
    lru_cache 单例——故先解析 backend/.env 把 DEEPSEEK_API_KEY 注入
    os.environ（pydantic-settings 中环境变量优先于 .env 文件），再清缓存重建。
  - whisper：**子进程内真转录**。whisper_transcribe.transcribe 无进程隔离，
    而 ct2 会拉旧 cuDNN DLL——同进程后续 RVM（torch CUDA）必 fastfail
    （2026-10-29 实证，见 test_studio_spike.py 模块文档）。平台代码不改，
    本测试把 studio_agent.transcribe 替换为「subprocess 跑真 whisper →
    回填 TranscribeSegment」：真跑语义不变，隔离达标。
  - 抠像（RVM）/渲染（Playwright）/质检：真实执行（本机已具备，
    test_studio_spike / test_studio_render 已验证）。

skip 契约：STUDIO_ACCEPTANCE 未设置或原片缺失 → skip（不算 FAIL）。
单跑其中一支时，差异对比自动降级为 skip（需两支同进程/同机产物可比）。
每支成功后把摘要写入 outputs/studio/_acceptance_<method>.json，
跨进程复跑也能比对。
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

import app.services.sse_bus as sse_mod

BACKEND = Path(__file__).resolve().parents[1]
SRC_VIDEO = BACKEND / "uploads" / "videos" / "live_person_talk.mp4"
ENV_FILE = BACKEND / ".env"
STUDIO_ROOT = BACKEND / "outputs" / "studio"

# git 冻结检查：验收期间允许变动的路径（产出目录 + 本地缓存 + 本脚本自身——
# 脚本提交可能落在两次快照之间，属工作流自身动作而非平台代码改动）
_GIT_ALLOWED_PREFIXES = ("backend/outputs/",)
_GIT_ALLOWED_PATHS = ("backend/tests/test_studio_p2_acceptance.py",)
_GIT_ALLOWED_SUBSTR = ("__pycache__", ".pytest_cache", ".venv")

pytestmark = [
    pytest.mark.skipif(
        not os.environ.get("STUDIO_ACCEPTANCE"),
        reason="STUDIO_ACCEPTANCE 未设置：验收实验默认跳过（消耗真 LLM tokens + 分钟级渲染）",
    ),
    pytest.mark.skipif(
        not SRC_VIDEO.is_file(),
        reason=f"live 原片不存在：{SRC_VIDEO}",
    ),
]

# method 名 → run 摘要（模块级，供同进程的第二支做差异对比）
_RESULTS: dict[str, dict] = {}

# 子进程 whisper 转录器：stdout 打 @@TRANSCRIPT@@<json> 哨兵行
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


def _ensure_llm_env(monkeypatch):
    """真 LLM 就绪：backend/.env 的 key 注入环境（cwd 无关）+ 清 get_settings 缓存。"""
    if not os.environ.get("DEEPSEEK_API_KEY") and ENV_FILE.is_file():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s.startswith("DEEPSEEK_API_KEY") and "=" in s:
                k, _, v = s.partition("=")
                monkeypatch.setenv(k.strip(), v.strip().strip('"').strip("'"))
                break
    from app.config import get_settings

    get_settings.cache_clear()
    from app.adapters.model_router import get_model_router

    llm = get_model_router().resolve_llm()
    if llm is None:
        pytest.skip("DEEPSEEK_API_KEY 未配置（backend/.env），无法真跑 LLM")
    return llm


def _git_snapshot() -> list[str]:
    """git status --porcelain 快照（过滤允许变动的路径）。"""
    proc = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=str(BACKEND.parent),
        capture_output=True,
        text=True,
        timeout=60,
    )
    out = []
    for line in (proc.stdout or "").splitlines():
        p = line[3:].strip().strip('"').replace("\\", "/")
        if p.startswith(_GIT_ALLOWED_PREFIXES) or p in _GIT_ALLOWED_PATHS:
            continue
        if any(s in p for s in _GIT_ALLOWED_SUBSTR):
            continue
        out.append(line)
    return out


@pytest.fixture(scope="module", autouse=True)
def git_freeze():
    """Task 6 判据：验收期间平台层代码零改动（除 outputs 产出与缓存）。"""
    before = set(_git_snapshot())
    yield
    after = set(_git_snapshot())
    drift = sorted(before ^ after)
    assert (
        not drift
    ), "验收期间平台代码出现非预期改动（应只有 outputs/studio 产出）：\n" + "\n".join(drift[:30])


def _make_clip(tmp_path: Path) -> Path:
    """切前 15s 段落到临时目录（任务规定；spike 经验另提示 22s 起句首更完整，
    见报告）。重编码保留音轨供转录。"""
    from capabilities.video.adapters.html_to_video import resolve_ffmpeg

    clip = tmp_path / "clip15s.mp4"
    subprocess.run(
        [
            resolve_ffmpeg(),
            "-y",
            "-i",
            str(SRC_VIDEO),
            "-t",
            "15",
            "-pix_fmt",
            "yuv420p",
            str(clip),
        ],
        check=True,
        capture_output=True,
    )
    assert clip.is_file() and clip.stat().st_size > 0, "15s 切片失败"
    return clip


@pytest.fixture
def isolated_whisper(monkeypatch):
    """whisper 子进程隔离：防 ct2 cuDNN DLL 污染同进程 torch CUDA（RVM 抠像）。"""
    from capabilities.video.adapters.whisper_transcribe import (
        TranscribeSegment,
        TranscribeWord,
    )
    from capabilities.video.services import studio_agent as sa

    def subprocess_transcribe(audio_path, model_size="small", language=None):
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

    monkeypatch.setattr(sa, "transcribe", subprocess_transcribe)


def _load_summary(method: str) -> dict | None:
    f = STUDIO_ROOT / f"_acceptance_{method}.json"
    if f.is_file():
        return json.loads(f.read_text(encoding="utf-8"))
    return None


def _dump_summary(method: str, summary: dict) -> None:
    STUDIO_ROOT.mkdir(parents=True, exist_ok=True)
    f = STUDIO_ROOT / f"_acceptance_{method}.json"
    f.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


async def _run_one(monkeypatch, tmp_path: Path, method_ref: dict, method: str, llm) -> dict:
    from capabilities.video.skills.video_studio import VideoStudioSkill

    clip = _make_clip(tmp_path)
    skill = VideoStudioSkill()
    chunks = []
    # SSE 事件记录：注入 _workflow_id 使 emitter 生效，publish 打桩收集
    # （进度打印：preview events 计数等，方便肉眼验收定位输出目录）
    events: list[dict] = []

    async def fake_publish(workflow_id, event_type, payload, turn_id=None):
        events.append(dict(payload))

    monkeypatch.setattr(sse_mod.sse_bus, "publish", fake_publish)
    t0 = time.perf_counter()
    async for c in skill.execute(
        {
            "video_path": str(clip),
            "method": method_ref,
            "llm": llm,
            "_user_id": "acceptance",
            "_workflow_id": f"acceptance-{method}",
        }
    ):
        chunks.append(c)
    elapsed = time.perf_counter() - t0
    assert len(chunks) == 1, f"{method}: 应恰好 yield 一次，实际 {len(chunks)} 次"

    chunk = chunks[0]
    assert (
        chunk["success"] is True
    ), f"{method} 失败：error={chunk.get('error')!r} review={chunk.get('review')!r}"
    video_path = Path(chunk["video_path"])
    assert video_path.is_file(), f"{method}: 成片不存在 {video_path}"
    assert STUDIO_ROOT in video_path.parents, f"{method}: 产出不在 {STUDIO_ROOT} 下：{video_path}"
    assert (
        chunk["review_summary"]["errors"] == []
    ), f"{method} review errors 非空：{chunk['review_summary']['errors']}"

    film_dir = Path(chunk["workspace"])
    tl_path = film_dir / "analysis" / "timeline.json"
    scene_path = film_dir / "scene" / "index.html"
    assert tl_path.is_file(), f"{method}: timeline.json 未落盘 {tl_path}"
    assert scene_path.is_file(), f"{method}: scene/index.html 未落盘 {scene_path}"
    phases = [e.get("phase") for e in events if e.get("phase")]
    preview_events = [e for e in events if e.get("preview_url")]
    print(
        f"\n[acceptance:{method}] film_id={film_dir.name} turns={chunk.get('turns')} "
        f"elapsed={elapsed:.1f}s"
    )
    print(f"[acceptance:{method}] workspace={film_dir}")
    print(f"[acceptance:{method}] final={video_path}")
    print(
        f"[acceptance:{method}] sse_events={len(events)} "
        f"preview_events={len(preview_events)} preview_url={chunk.get('preview_url')}"
    )
    print(f"[acceptance:{method}] phases={phases}")
    summary = {
        "film_id": film_dir.name,
        "video_path": str(video_path),
        "turns": chunk.get("turns"),
        "elapsed_s": round(elapsed, 1),
        "sse_events": len(events),
        "preview_events": len(preview_events),
        "preview_url": chunk.get("preview_url"),
        "beats": json.loads(tl_path.read_text(encoding="utf-8"))["beats"],
        "scene_html": scene_path.read_text(encoding="utf-8"),
        "review": chunk["review_summary"],
    }
    _RESULTS[method] = summary
    _dump_summary(method, summary)
    return summary


async def test_acceptance_general_talk(monkeypatch, tmp_path, isolated_whisper):
    llm = _ensure_llm_env(monkeypatch)
    s = await _run_one(monkeypatch, tmp_path, {"builtin": "general_talk"}, "general_talk", llm)
    assert s["beats"], "general_talk：timeline 无 beats"


async def test_acceptance_minimal_cards_and_divergence(monkeypatch, tmp_path, isolated_whisper):
    llm = _ensure_llm_env(monkeypatch)
    s = await _run_one(monkeypatch, tmp_path, {"builtin": "minimal_cards"}, "minimal_cards", llm)
    assert s["beats"], "minimal_cards：timeline 无 beats"

    other = _RESULTS.get("general_talk") or _load_summary("general_talk")
    if other is None:
        pytest.skip("单跑 minimal_cards：无 general_talk 结果可比（差异判据需两支）")

    a, b = other, s
    beats_a = json.dumps(a["beats"], ensure_ascii=False, sort_keys=True)
    beats_b = json.dumps(b["beats"], ensure_ascii=False, sort_keys=True)
    assert beats_a != beats_b, (
        f"两套打法的 timeline beats 完全雷同（差异判据不成立）：\n"
        f"general_talk({a['film_id']}) vs minimal_cards({b['film_id']})"
    )
    assert a["scene_html"] != b["scene_html"], (
        f"两套打法的 scene/index.html 完全雷同（差异判据不成立）："
        f"{a['film_id']} vs {b['film_id']}"
    )
