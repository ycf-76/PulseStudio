"""AI 视频工坊：进程内异步 Worker。

设计依据：docs/VIDEO_ARCHITECTURE_ALIGNMENT_MIGRATION_PLAN.md §4（D1/D6）

职责：
  - 启动时恢复残留 running 任务（进程崩溃/重启不丢）；
  - 循环认领 queued 任务（JobService.claim_jobs，PG SKIP LOCKED / SQLite 防并发更新）；
  - 按 job_type 并发执行 handler（shot_image ≤ 2 / shot_video 串行 / compose 独立）；
  - 单任务等待超时兜底（默认 900s，覆盖"最坏 total_timeout + 300s 轮询"）；
  - 成功/失败回调 on_event（由接入方 wiring SSE，Worker 本身不感知传输层）。

handler 契约：
    async def handler(job: dict, report: ReportProgress) -> dict:
        # 返回 {"result_asset_id": "...", "result_url": "..."}
        # 失败直接抛异常，Worker 负责 fail_job
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from capabilities.video.services.video_jobs import (
    JOB_TYPE_CONCURRENCY,
    video_studio_service,
)

logger = logging.getLogger(__name__)

ReportProgress = Callable[[int], Awaitable[None]]
JobHandler = Callable[[dict[str, Any], ReportProgress], Awaitable[dict[str, Any]]]

# 单任务最大执行时长（秒）。视频生成最坏约 total_timeout(600)+轮询(300)，900 覆盖。
JOB_EXEC_TIMEOUT = 900.0


class VideoWorker:
    """进程内 Worker 单例。start() 进 lifespan，stop() 退 lifespan。"""

    def __init__(self, poll_idle: float = 1.5, poll_busy: float = 0.3) -> None:
        self._handlers: dict[str, JobHandler] = {}
        self._semaphores: dict[str, asyncio.Semaphore] = {}
        self._task: asyncio.Task | None = None
        self._stopping = False
        self._poll_idle = poll_idle
        self._poll_busy = poll_busy
        self._running_jobs: set[str] = set()
        self.on_event: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None
        self.stats = {"claimed": 0, "succeeded": 0, "failed": 0, "recovered": 0}

    # ── handler 注册 ──────────────────────────────────────

    def register(self, job_type: str, handler: JobHandler) -> None:
        if job_type not in JOB_TYPE_CONCURRENCY:
            raise ValueError(f"未知 job_type: {job_type}")
        self._handlers[job_type] = handler
        self._semaphores[job_type] = asyncio.Semaphore(JOB_TYPE_CONCURRENCY[job_type])
        logger.info(f"[video_worker] handler 注册：{job_type}")

    @property
    def registered_types(self) -> list[str]:
        return sorted(self._handlers)

    # ── 生命周期 ──────────────────────────────────────────

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stopping = False
        # 启动恢复：残留 running → queued（attempts+1，超限标 failed）
        recovered = await video_studio_service.recover_stale_running()
        self.stats["recovered"] = recovered
        self._task = asyncio.create_task(self._run(), name="video-worker")
        logger.info(
            f"[video_worker] 启动（poll_idle={self._poll_idle}s, "
            f"handlers={self.registered_types}）"
        )

    async def stop(self) -> None:
        self._stopping = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info(f"[video_worker] 已停止（stats={self.stats}）")

    # ── 主循环 ────────────────────────────────────────────

    async def _run(self) -> None:
        while not self._stopping:
            try:
                claimed = await video_studio_service.claim_jobs()
            except Exception as e:
                logger.warning(f"[video_worker] 认领失败：{e}")
                await asyncio.sleep(self._poll_idle)
                continue

            if not claimed:
                await asyncio.sleep(self._poll_idle)
                continue

            self.stats["claimed"] += len(claimed)
            # 本批并发执行（每类型信号量在 handler 外层控制）
            results = await asyncio.gather(
                *[self._execute_one(job) for job in claimed],
                return_exceptions=True,
            )
            for job, result in zip(claimed, results):
                if isinstance(result, Exception):
                    logger.error(f"[video_worker] 任务 {job['id']} 未捕获异常：{result}")
            await asyncio.sleep(self._poll_busy)

    async def _execute_one(self, job: dict[str, Any]) -> None:
        job_type = job["job_type"]
        handler = self._handlers.get(job_type)
        sem = self._semaphores.get(job_type)
        if handler is None:
            await video_studio_service.fail_job(job["id"], f"未注册的 job_type: {job_type}")
            await self._emit("failed", job)
            return

        async with sem:
            self._running_jobs.add(job["id"])
            started = time.monotonic()
            try:
                async def _report(pct: int) -> None:
                    await video_studio_service.update_job_progress(job["id"], pct)

                result = await asyncio.wait_for(handler(job, _report), timeout=JOB_EXEC_TIMEOUT)
                await video_studio_service.complete_job(
                    job["id"],
                    result_asset_id=str(result.get("result_asset_id", "")),
                    result_url=str(result.get("result_url", "")),
                )
                self.stats["succeeded"] += 1
                logger.info(
                    f"[video_worker] 任务 {job['id']}（{job_type}）成功，"
                    f"耗时 {time.monotonic() - started:.1f}s"
                )
                await self._emit("succeeded", job)
            except asyncio.TimeoutError:
                await video_studio_service.fail_job(
                    job["id"], f"执行超时（>{JOB_EXEC_TIMEOUT:.0f}s）"
                )
                self.stats["failed"] += 1
                logger.warning(f"[video_worker] 任务 {job['id']} 执行超时")
                await self._emit("failed", job)
            except Exception as e:
                await video_studio_service.fail_job(job["id"], str(e))
                self.stats["failed"] += 1
                logger.warning(f"[video_worker] 任务 {job['id']} 失败：{e}")
                await self._emit("failed", job)
            finally:
                self._running_jobs.discard(job["id"])

    async def _emit(self, outcome: str, job: dict[str, Any]) -> None:
        if self.on_event is None:
            return
        try:
            await self.on_event(outcome, job)
        except Exception as e:
            logger.debug(f"[video_worker] on_event 回调失败（忽略）：{e}")

    def status(self) -> dict[str, Any]:
        return {
            "running": self._task is not None and not self._task.done(),
            "handlers": self.registered_types,
            "active_jobs": sorted(self._running_jobs),
            "stats": dict(self.stats),
            "concurrency": dict(JOB_TYPE_CONCURRENCY),
        }


# 进程级单例
video_worker = VideoWorker()
