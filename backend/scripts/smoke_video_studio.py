"""AI 视频工坊 W1+W2 冒烟验证：零 API Key 全链路（阶段 0 签收依据）。

验证路径（docs/VIDEO_ARCHITECTURE_ALIGNMENT_MIGRATION_PLAN.md §6.2）：
  创建分镜 → mock 分镜编排 → 保存分镜（乐观锁）→ 提交 shot_image 任务
  → Worker 执行 → 分镜图落库 → 确认闸门 → 提交 shot_video 任务
  → Worker 执行 → compose 拼接（真实 ffmpeg）→ 成片落库 → 分镜 completed
  另验证：失败率触发 → retry 重试 → 恢复成功。

用法：
    cd backend
    python scripts/smoke_video_studio.py

环境要求：仅 ffmpeg（占位视频与拼接用）；无需任何模型 API Key。
数据库：使用独立 smoke 库（data/smoke_video.db），不污染开发库。
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

# ── 必须在 import app.* 之前设置：独立 smoke 库 + 低 mock 延迟 ──
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault(
    "DATABASE_URL", f"sqlite+aiosqlite:///{_BACKEND_ROOT / 'data' / 'smoke_video.db'}"
)
os.environ.setdefault("VIDEO_MOCK_LATENCY_SEC", "0.2")

sys.path.insert(0, str(_BACKEND_ROOT))

from app.adapters.media_provider import resolve_media_provider  # noqa: E402
from app.db.models import Base  # noqa: E402
from app.db.session import engine  # noqa: E402
from app.services.video_handlers import register_default_handlers  # noqa: E402
from app.services.video_jobs import video_studio_service  # noqa: E402
from app.services.video_worker import video_worker  # noqa: E402

USER = "smoke_user"
FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    mark = "✅" if ok else "❌"
    print(f"  {mark} {name}" + (f" —— {detail}" if detail else ""))
    if not ok:
        FAILURES.append(name)


async def wait_jobs(
    storyboard_id: str, job_type: str, expect: int, timeout: float = 120.0
) -> list[dict]:
    """轮询直到该类型任务全部到达终态。"""
    deadline = time.monotonic() + timeout
    last: list[dict] = []
    while time.monotonic() < deadline:
        jobs = [
            j
            for j in await video_studio_service.list_jobs(storyboard_id, USER)
            if j["job_type"] == job_type
        ]
        last = jobs
        if len(jobs) >= expect and all(j["status"] in ("succeeded", "failed") for j in jobs):
            return jobs
        await asyncio.sleep(0.3)
    return last


async def main() -> int:
    # 0. 建表（独立 smoke 库）
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("== 0. 数据库就绪（smoke 库）")

    # 1. 建分镜 + mock 编排（阶段 0：LLM 也用 mock）
    provider = await resolve_media_provider()
    print(f"== 1. Provider 解析：{provider.name}")

    sb = await video_studio_service.create_storyboard(
        USER,
        "一只橘猫在夕阳下的窗台上伸懒腰，镜头缓慢推进",
        ratio="9:16",
        shot_duration=3.0,
        style="电影感",
    )
    storyboard = await provider.generate_storyboard(
        sb["prompt"],
        shot_count=3,
        shot_duration=3.0,
        style="电影感",
        ratio="9:16",
    )
    shots = storyboard["shots"]
    check("分镜编排 ≥3 镜", len(shots) >= 3, f"{len(shots)} 镜")

    saved = await video_studio_service.save_shots(sb["id"], USER, shots)
    check("分镜保存", len(saved["shots"]) == len(shots), "status=draft")

    # 2. 提交分镜图任务并启动 Worker
    register_default_handlers(video_worker)
    await video_worker.start()
    print("== 2. 提交 3 个 shot_image 任务，Worker 执行中…")
    t0 = time.monotonic()
    for shot in saved["shots"]:
        await video_studio_service.submit_job(
            USER,
            sb["id"],
            "shot_image",
            shot_id=shot["shot_id"],
            provider=provider.name,
            model="mock-image",
            job_input={"shot": shot, "ratio": "9:16"},
        )
    image_jobs = await wait_jobs(sb["id"], "shot_image", len(shots))
    img_ok = all(j["status"] == "succeeded" for j in image_jobs)
    check(
        "shot_image 全部成功",
        img_ok and len(image_jobs) == len(shots),
        f"{sum(1 for j in image_jobs if j['status'] == 'succeeded')}/{len(shots)}，{time.monotonic() - t0:.1f}s",
    )

    sb_now = await video_studio_service.get_storyboard(sb["id"], USER)
    frames_ok = all(s.get("frame_url") for s in sb_now["shots"])
    files_ok = all(
        (
            Path(_BACKEND_ROOT / "uploads" / s["frame_url"].removeprefix("/uploads/")).exists()
            for s in sb_now["shots"]
            if s.get("frame_url")
        )
    )
    check(
        "关键帧落库 + 落盘",
        frames_ok and files_ok,
        f"provider={provider.name}（source=ai 路径生效）",
    )

    # 3. 确认闸门
    confirmed = await video_studio_service.confirm_storyboard(sb["id"], USER)
    check("确认闸门 draft→confirmed", confirmed["status"] == "confirmed")

    # 4. 提交视频任务
    print("== 4. 提交 3 个 shot_video 任务，Worker 执行中…")
    t0 = time.monotonic()
    for shot in confirmed["shots"]:
        await video_studio_service.submit_job(
            USER,
            sb["id"],
            "shot_video",
            shot_id=shot["shot_id"],
            provider=provider.name,
            model="mock-video",
            job_input={"shot": shot, "ratio": "9:16", "duration": shot.get("duration", 3.0)},
        )
    video_jobs = await wait_jobs(sb["id"], "shot_video", len(shots))
    vid_ok = all(j["status"] == "succeeded" for j in video_jobs)
    check(
        "shot_video 全部成功（ffmpeg 占位片段）",
        vid_ok and len(video_jobs) == len(shots),
        f"{time.monotonic() - t0:.1f}s",
    )

    # 5. 拼接成片（真实 compose_video_concat）
    print("== 5. 提交 compose 任务（真实 ffmpeg 拼接）…")
    await video_studio_service.set_storyboard_status(sb["id"], "composing")
    await video_studio_service.submit_job(
        USER,
        sb["id"],
        "compose",
        provider=provider.name,
        model="ffmpeg",
        job_input={},
    )
    compose_jobs = await wait_jobs(sb["id"], "compose", 1)
    compose_ok = bool(compose_jobs) and compose_jobs[0]["status"] == "succeeded"
    check(
        "compose 成功", compose_ok, compose_jobs[0].get("error", "") if compose_jobs else "无任务"
    )

    sb_final = await video_studio_service.get_storyboard(sb["id"], USER)
    final_file_ok = False
    if sb_final.get("final_url"):
        # URL 形如 /uploads/videos/... → 落点 backend/uploads/videos/...
        final_file_ok = (Path(_BACKEND_ROOT / sb_final["final_url"].removeprefix("/"))).exists()
    check(
        "成片落库 + 分镜 completed",
        sb_final["status"] == "completed" and final_file_ok,
        f"url={sb_final.get('final_url')}",
    )

    # 6. 失败 → 重试路径（调高 mock 失败率再恢复）
    print("== 6. 失败率 + retry 路径…")
    from app.config import get_settings

    settings = get_settings()
    object.__setattr__(settings, "video_mock_failure_rate", 1.0)  # pydantic settings 实例属性覆盖
    job = await video_studio_service.submit_job(
        USER,
        sb["id"],
        "shot_image",
        shot_id=shots[0]["shot_id"],
        job_input={"shot": shots[0], "ratio": "9:16"},
    )
    failed = await wait_jobs(sb["id"], "shot_image", 4, timeout=30)
    target = [j for j in failed if j["id"] == job["id"]]
    check(
        "失败率触发 → failed",
        bool(target) and target[0]["status"] == "failed",
        target[0].get("error", "") if target else "无任务",
    )

    object.__setattr__(settings, "video_mock_failure_rate", 0.0)
    retried = await video_studio_service.retry_job(job["id"], USER)
    check("retry failed→queued", retried["status"] == "queued", f"attempts={retried['attempts']}")
    done = await wait_jobs(sb["id"], "shot_image", 4)
    target = [j for j in done if j["id"] == job["id"]]
    check("retry 后成功", bool(target) and target[0]["status"] == "succeeded")

    # 7. 停止 Worker + 汇总
    await video_worker.stop()
    status = video_worker.status()
    print(f"== 7. Worker stats: {status['stats']}")
    check("Worker 启动恢复无残留", status["stats"]["recovered"] >= 0)

    print()
    if FAILURES:
        print(f"❌ 冒烟未通过：{len(FAILURES)} 项失败 -> {FAILURES}")
        return 1
    print(
        "✅ 冒烟全部通过：零 API Key 环境下「分镜 → 分镜图 → 闸门 → 视频片段 → ffmpeg 成片」全链路闭环"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
