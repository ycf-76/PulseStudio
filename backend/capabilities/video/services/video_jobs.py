"""AI 视频工坊：分镜会话态 + 异步任务层服务。

设计依据：docs/VIDEO_ARCHITECTURE_ALIGNMENT_MIGRATION_PLAN.md §4

职责边界：
  - 本服务只管理"状态"（storyboard 生命周期 + job 四态状态机），不执行任何模型调用；
  - 执行由 VideoWorker（app/services/video_worker.py）完成，handler 注册机制解耦；
  - 认领语义：PostgreSQL 用 FOR UPDATE SKIP LOCKED；SQLite（dev 默认）退化为此防并发
    更新 + 行数校验，单进程开发场景等价。

状态机：
  storyboard: draft → confirmed → composing → completed
  job: queued → running → succeeded | failed（retry 回 queued，attempts ≤ 3）
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, update

from app.db.models import VideoJob, VideoStoryboard
from app.db.session import AsyncSessionLocal, is_sqlite

logger = logging.getLogger(__name__)

# job_type → 并发上限（Worker 按类型取最小值调度）
JOB_TYPE_CONCURRENCY = {
    "shot_image": 2,  # 复用 multi_image_video_max_concurrent_frames 语义
    "shot_video": 1,  # 串行，保护 API 配额
    "compose": 1,  # ffmpeg 拼接独立跑
}
MAX_JOB_ATTEMPTS = 3


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _sb_to_dict(sb: VideoStoryboard) -> dict[str, Any]:
    return {
        "id": sb.id,
        "user_id": sb.user_id,
        "session_id": sb.session_id,
        "title": sb.title,
        "prompt": sb.prompt,
        "status": sb.status,
        "ratio": sb.ratio,
        "shot_duration": sb.shot_duration,
        "style": sb.style,
        "scene_description": sb.scene_description,
        "shots": sb.shots or [],
        "reference_asset_ids": sb.reference_asset_ids or [],
        "final_asset_id": sb.final_asset_id,
        "final_url": sb.final_url,
        "degradation": sb.degradation or [],
        "created_at": sb.created_at.isoformat() if sb.created_at else None,
        "updated_at": sb.updated_at.isoformat() if sb.updated_at else None,
    }


def _job_to_dict(job: VideoJob) -> dict[str, Any]:
    return {
        "id": job.id,
        "user_id": job.user_id,
        "storyboard_id": job.storyboard_id,
        "shot_id": job.shot_id,
        "job_type": job.job_type,
        "status": job.status,
        "progress": job.progress,
        "provider": job.provider,
        "model": job.model,
        "input": job.input or {},
        "result_asset_id": job.result_asset_id,
        "result_url": job.result_url,
        "error": job.error,
        "attempts": job.attempts,
        "credits_est": job.credits_est,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    }


class VideoStudioService:
    """分镜会话 + 任务状态机（无模型调用，纯状态管理）。"""

    # ── 分镜会话态 ────────────────────────────────────────

    async def create_storyboard(
        self,
        user_id: str,
        prompt: str,
        *,
        title: str = "",
        session_id: str | None = None,
        ratio: str = "9:16",
        shot_duration: float = 5.0,
        style: str = "",
        reference_asset_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        async with AsyncSessionLocal() as session:
            sb = VideoStoryboard(
                user_id=user_id,
                session_id=session_id,
                title=title or prompt[:30],
                prompt=prompt,
                status="draft",
                ratio=ratio,
                shot_duration=shot_duration,
                style=style,
                shots=[],
                reference_asset_ids=reference_asset_ids or [],
            )
            session.add(sb)
            await session.commit()
            await session.refresh(sb)
            return _sb_to_dict(sb)

    async def get_storyboard(self, storyboard_id: str, user_id: str) -> dict[str, Any] | None:
        async with AsyncSessionLocal() as session:
            sb = await session.get(VideoStoryboard, storyboard_id)
            if sb is None or sb.user_id != user_id:
                return None
            return _sb_to_dict(sb)

    async def list_storyboards(self, user_id: str, limit: int = 50) -> list[dict[str, Any]]:
        async with AsyncSessionLocal() as session:
            stmt = (
                select(VideoStoryboard)
                .where(VideoStoryboard.user_id == user_id)
                .order_by(VideoStoryboard.created_at.desc())
                .limit(limit)
            )
            rows = (await session.execute(stmt)).scalars().all()
            return [_sb_to_dict(sb) for sb in rows]

    async def save_scene_description(
        self, storyboard_id: str, user_id: str, scene: dict[str, Any]
    ) -> None:
        async with AsyncSessionLocal() as session:
            sb = await session.get(VideoStoryboard, storyboard_id)
            if sb is None or sb.user_id != user_id:
                return
            sb.scene_description = scene
            await session.commit()

    async def save_shots(
        self,
        storyboard_id: str,
        user_id: str,
        shots: list[dict[str, Any]],
        *,
        expected_updated_at: str | None = None,
    ) -> dict[str, Any]:
        """整份回写分镜（编辑器保存）。

        乐观锁：expected_updated_at 不匹配返回 conflict 错误（409）。
        每镜自动补 shot_id（ULID），缺失 caption/prompt 的镜头拒绝。
        """
        from app.db.models import generate_ulid

        # 深拷贝为全新对象：保证 JSON 列脏跟踪生效（见 update_shot_field 注释）
        shots = [dict(s) for s in shots]
        for shot in shots:
            if not shot.get("image_prompt"):
                raise ValueError(f"第 {shot.get('shot_index', '?')} 镜缺少 image_prompt")
            if not shot.get("shot_id"):
                shot["shot_id"] = generate_ulid()

        async with AsyncSessionLocal() as session:
            sb = await session.get(VideoStoryboard, storyboard_id)
            if sb is None or sb.user_id != user_id:
                raise LookupError("分镜不存在")
            if sb.status not in ("draft",):
                raise PermissionError(f"分镜已 {sb.status}，不可再编辑（需先重置为 draft）")
            if expected_updated_at and sb.updated_at:
                if sb.updated_at.isoformat() != expected_updated_at:
                    raise ConflictError("分镜已被其他会话修改，请刷新后重试")
            sb.shots = shots
            await session.commit()
            await session.refresh(sb)
            return _sb_to_dict(sb)

    async def confirm_storyboard(self, storyboard_id: str, user_id: str) -> dict[str, Any]:
        """确认闸门：draft → confirmed。未确认不允许提交视频任务。"""
        async with AsyncSessionLocal() as session:
            sb = await session.get(VideoStoryboard, storyboard_id)
            if sb is None or sb.user_id != user_id:
                raise LookupError("分镜不存在")
            if sb.status != "draft":
                raise PermissionError(f"当前状态 {sb.status} 不可确认")
            if not sb.shots:
                raise ValueError("分镜为空，无法确认")
            sb.status = "confirmed"
            await session.commit()
            await session.refresh(sb)
            return _sb_to_dict(sb)

    async def reopen_storyboard(self, storyboard_id: str, user_id: str) -> dict[str, Any]:
        """闸门回退：confirmed → draft（用户改主意，允许再编辑）。"""
        async with AsyncSessionLocal() as session:
            sb = await session.get(VideoStoryboard, storyboard_id)
            if sb is None or sb.user_id != user_id:
                raise LookupError("分镜不存在")
            if sb.status != "confirmed":
                raise PermissionError(f"当前状态 {sb.status} 不可回退")
            sb.status = "draft"
            await session.commit()
            await session.refresh(sb)
            return _sb_to_dict(sb)

    async def set_storyboard_status(self, storyboard_id: str, status: str) -> None:
        async with AsyncSessionLocal() as session:
            sb = await session.get(VideoStoryboard, storyboard_id)
            if sb is None:
                return
            sb.status = status
            await session.commit()

    async def mark_storyboard_completed(
        self, storyboard_id: str, final_asset_id: str, final_url: str
    ) -> None:
        async with AsyncSessionLocal() as session:
            sb = await session.get(VideoStoryboard, storyboard_id)
            if sb is None:
                return
            sb.final_asset_id = final_asset_id
            sb.final_url = final_url
            sb.status = "completed"
            await session.commit()

    async def append_degradation(self, storyboard_id: str, note: str) -> None:
        async with AsyncSessionLocal() as session:
            sb = await session.get(VideoStoryboard, storyboard_id)
            if sb is None:
                return
            notes = list(sb.degradation or [])
            notes.append(note)
            sb.degradation = notes
            await session.commit()

    async def update_shot_field(
        self, storyboard_id: str, user_id: str, shot_id: str, fields: dict[str, Any]
    ) -> dict[str, Any]:
        """回写单镜执行产物（frame_url/clip_url/clip_mode 等）。

        注意：JSON 列必须整体换新对象（新 list + 新 dict）才能被 SQLAlchemy
        脏跟踪——原地 shot.update() 后赋回同一个 list 对象会被判定"无变化"，
        commit 静默跳过 UPDATE（2026-10-16 冒烟实测踩坑）。
        """
        async with AsyncSessionLocal() as session:
            sb = await session.get(VideoStoryboard, storyboard_id)
            if sb is None or sb.user_id != user_id:
                raise LookupError("分镜不存在")
            shots = [dict(s) for s in (sb.shots or [])]
            hit = False
            for i, shot in enumerate(shots):
                if shot.get("shot_id") == shot_id:
                    shots[i] = {**shot, **fields}
                    hit = True
                    break
            if not hit:
                raise LookupError(f"镜头 {shot_id} 不存在")
            sb.shots = shots
            await session.commit()
            await session.refresh(sb)
            return _sb_to_dict(sb)

    # ── 任务状态机 ────────────────────────────────────────

    async def submit_job(
        self,
        user_id: str,
        storyboard_id: str,
        job_type: str,
        *,
        shot_id: str | None = None,
        provider: str = "mock",
        model: str = "",
        job_input: dict[str, Any] | None = None,
        credits_est: float = 0.0,
    ) -> dict[str, Any]:
        if job_type not in JOB_TYPE_CONCURRENCY:
            raise ValueError(f"未知 job_type: {job_type}（支持 {list(JOB_TYPE_CONCURRENCY)}）")
        async with AsyncSessionLocal() as session:
            job = VideoJob(
                user_id=user_id,
                storyboard_id=storyboard_id,
                shot_id=shot_id,
                job_type=job_type,
                status="queued",
                provider=provider,
                model=model,
                input=job_input or {},
                credits_est=credits_est,
            )
            session.add(job)
            await session.commit()
            await session.refresh(job)
            return _job_to_dict(job)

    async def claim_jobs(self, limits: dict[str, int] | None = None) -> list[dict[str, Any]]:
        """Worker 认领：按 job_type 各取不超过并发上限的 queued 任务置为 running。

        PostgreSQL: SELECT ... FOR UPDATE SKIP LOCKED；SQLite: 逐条防并发更新。
        返回认领成功的 job dict 列表。
        """
        limits = limits or JOB_TYPE_CONCURRENCY
        claimed: list[dict[str, Any]] = []

        for job_type, limit in limits.items():
            if limit <= 0:
                continue
            async with AsyncSessionLocal() as session:
                if is_sqlite:
                    stmt = (
                        select(VideoJob)
                        .where(VideoJob.status == "queued", VideoJob.job_type == job_type)
                        .order_by(VideoJob.created_at.asc())
                        .limit(limit)
                    )
                    rows = list((await session.execute(stmt)).scalars().all())
                    took: list[VideoJob] = []
                    for row in rows:
                        result = await session.execute(
                            update(VideoJob)
                            .where(VideoJob.id == row.id, VideoJob.status == "queued")
                            .values(
                                status="running", started_at=_now(), attempts=VideoJob.attempts + 1
                            )
                        )
                        if result.rowcount == 1:
                            took.append(row)
                        await session.commit()
                    for row in took:
                        await session.refresh(row)
                    claimed.extend(_job_to_dict(r) for r in took)
                else:
                    stmt = (
                        select(VideoJob)
                        .where(VideoJob.status == "queued", VideoJob.job_type == job_type)
                        .order_by(VideoJob.created_at.asc())
                        .limit(limit)
                        .with_for_update(skip_locked=True)
                    )
                    rows = list((await session.execute(stmt)).scalars().all())
                    for row in rows:
                        row.status = "running"
                        row.started_at = _now()
                        row.attempts = row.attempts + 1
                    await session.commit()
                    for row in rows:
                        await session.refresh(row)
                    claimed.extend(_job_to_dict(r) for r in rows)
        return claimed

    async def get_job(self, job_id: str, user_id: str | None = None) -> dict[str, Any] | None:
        async with AsyncSessionLocal() as session:
            job = await session.get(VideoJob, job_id)
            if job is None:
                return None
            if user_id is not None and job.user_id != user_id:
                return None
            return _job_to_dict(job)

    async def list_jobs(self, storyboard_id: str, user_id: str) -> list[dict[str, Any]]:
        async with AsyncSessionLocal() as session:
            stmt = (
                select(VideoJob)
                .where(VideoJob.storyboard_id == storyboard_id, VideoJob.user_id == user_id)
                .order_by(VideoJob.created_at.asc())
            )
            rows = (await session.execute(stmt)).scalars().all()
            return [_job_to_dict(j) for j in rows]

    async def update_job_progress(self, job_id: str, progress: int) -> None:
        async with AsyncSessionLocal() as session:
            job = await session.get(VideoJob, job_id)
            if job is None or job.status != "running":
                return
            job.progress = max(0, min(100, int(progress)))
            await session.commit()

    async def complete_job(
        self, job_id: str, *, result_asset_id: str = "", result_url: str = ""
    ) -> None:
        async with AsyncSessionLocal() as session:
            job = await session.get(VideoJob, job_id)
            if job is None:
                return
            job.status = "succeeded"
            job.progress = 100
            job.result_asset_id = result_asset_id or None
            job.result_url = result_url or None
            job.finished_at = _now()
            job.error = ""
            await session.commit()

    async def fail_job(self, job_id: str, error: str) -> str:
        """标记失败。返回最终状态（failed；attempts 超限时同样 failed，由 retry 接口兜底）。"""
        async with AsyncSessionLocal() as session:
            job = await session.get(VideoJob, job_id)
            if job is None:
                return "missing"
            job.status = "failed"
            job.error = str(error)[:2000]
            job.finished_at = _now()
            await session.commit()
            return "failed"

    async def retry_job(self, job_id: str, user_id: str) -> dict[str, Any]:
        """单任务重试：failed → queued。attempts 已满则拒绝。"""
        async with AsyncSessionLocal() as session:
            job = await session.get(VideoJob, job_id)
            if job is None or job.user_id != user_id:
                raise LookupError("任务不存在")
            if job.status != "failed":
                raise PermissionError(f"任务状态 {job.status} 不可重试")
            if job.attempts >= MAX_JOB_ATTEMPTS:
                raise PermissionError(f"任务已重试 {job.attempts} 次达上限，请调整参数后重新提交")
            job.status = "queued"
            job.error = ""
            job.progress = 0
            job.finished_at = None
            await session.commit()
            await session.refresh(job)
            return _job_to_dict(job)

    async def recover_stale_running(self) -> int:
        """启动恢复：残留 running 重置为 queued（attempts+1）；attempts 已满标 failed。

        解决进程崩溃/重启后任务卡死。返回重置条数。
        """
        async with AsyncSessionLocal() as session:
            stmt = select(VideoJob).where(VideoJob.status == "running")
            rows = list((await session.execute(stmt)).scalars().all())
            recovered = 0
            for job in rows:
                job.attempts += 1
                if job.attempts >= MAX_JOB_ATTEMPTS:
                    job.status = "failed"
                    job.error = f"进程重启恢复失败：已执行 {job.attempts} 次仍未完成"
                    job.finished_at = _now()
                else:
                    job.status = "queued"
                    job.started_at = None
                    job.error = f"进程重启，第 {job.attempts} 次恢复执行"
                recovered += 1
            await session.commit()
            if recovered:
                logger.warning(f"[video_jobs] 启动恢复：重置 {recovered} 个残留 running 任务")
            return recovered

    async def count_pending(self, storyboard_id: str) -> dict[str, int]:
        """统计分镜下各状态任务数（compose 前置判断用）。"""
        async with AsyncSessionLocal() as session:
            stmt = select(VideoJob).where(VideoJob.storyboard_id == storyboard_id)
            rows = (await session.execute(stmt)).scalars().all()
            counts: dict[str, int] = {}
            for j in rows:
                counts[j.status] = counts.get(j.status, 0) + 1
            return counts

    async def jobs_of_type(self, storyboard_id: str, job_type: str) -> list[dict[str, Any]]:
        async with AsyncSessionLocal() as session:
            stmt = (
                select(VideoJob)
                .where(VideoJob.storyboard_id == storyboard_id, VideoJob.job_type == job_type)
                .order_by(VideoJob.created_at.asc())
            )
            rows = (await session.execute(stmt)).scalars().all()
            return [_job_to_dict(j) for j in rows]


class ConflictError(Exception):
    """乐观锁冲突（409）。"""


# 进程级单例（服务无状态，复用一个实例即可）
video_studio_service = VideoStudioService()
