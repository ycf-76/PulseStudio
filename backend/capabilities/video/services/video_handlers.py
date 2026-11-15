"""AI 视频工坊：Worker handler 注册（三类 job_type → Provider 调用 → 素材落库）。

设计依据：docs/VIDEO_ARCHITECTURE_MIGRATION_PLAN.md §3/§6（handler 是任务层与
Provider 之间的唯一耦合点；阶段 1-3 换真实 Provider 时本文件零改动）

三个 handler：
  shot_image  单镜分镜关键帧：Provider.generate_shot_image → save_asset → 回写 shot.frame_*
  shot_video  单镜视频片段：  Provider.generate_shot_video → save_video_asset → 回写 shot.clip_*
  compose     拼接成片：      汇总各 shot clip → compose_video_concat（真实 ffmpeg）→ save_video_asset → 分镜 completed
"""

from __future__ import annotations

import logging
from typing import Any

from capabilities.video.services.video_jobs import video_studio_service
from capabilities.video.services.video_worker import VideoWorker

logger = logging.getLogger(__name__)


async def _resolve_provider() -> Any:
    from capabilities.video.adapters.media_provider import resolve_media_provider

    return await resolve_media_provider()


async def _handle_shot_image(job: dict[str, Any], report: Any) -> dict[str, Any]:
    from app.services.asset_library import save_asset

    storyboard_id = job["storyboard_id"]
    shot_id = job["shot_id"]
    provider = await _resolve_provider()

    await report(10)
    await provider.estimate("shot_image", job.get("input") or {})
    image_bytes = await provider.generate_shot_image(job.get("input") or {})
    if not image_bytes:
        raise RuntimeError("Provider 返回空图片")

    await report(80)
    asset = await save_asset(
        user_id=job["user_id"],
        filename=f"frame_{shot_id[:8]}.png",
        content_type="image/png",
        data=image_bytes,
        source="ai",
    )
    await report(95)

    await video_studio_service.update_shot_field(
        storyboard_id, job["user_id"], shot_id,
        {"frame_url": asset["original_url"], "frame_asset_id": asset["id"], "frame_source": provider.name},
    )
    return {"result_asset_id": asset["id"], "result_url": asset["original_url"]}


async def _handle_shot_video(job: dict[str, Any], report: Any) -> dict[str, Any]:
    from app.services.asset_library import save_video_asset

    storyboard_id = job["storyboard_id"]
    shot_id = job["shot_id"]
    provider = await _resolve_provider()

    await report(5)
    await provider.estimate("shot_video", job.get("input") or {})
    video_bytes = await provider.generate_shot_video(job.get("input") or {})
    if not video_bytes:
        raise RuntimeError("Provider 返回空视频")

    await report(85)
    asset = await save_video_asset(
        user_id=job["user_id"],
        filename=f"clip_{shot_id[:8]}.mp4",
        content_type="video/mp4",
        data=video_bytes,
        source="ai",
        metadata={"source": "video_studio", "shot_id": shot_id, "provider": provider.name},
    )
    await report(95)

    await video_studio_service.update_shot_field(
        storyboard_id, job["user_id"], shot_id,
        {"clip_url": asset["original_url"], "clip_asset_id": asset["asset_id"], "clip_mode": "video", "clip_error": ""},
    )
    return {"result_asset_id": asset["asset_id"], "result_url": asset["original_url"]}


async def _handle_compose(job: dict[str, Any], report: Any) -> dict[str, Any]:
    """拼接成片：真实 ffmpeg 跑占位/真实片段（阶段 0 即验证拼接路径）。"""
    from capabilities.video.adapters.video_compose import compose_video_concat
    from app.services.asset_library import save_video_asset, url_to_local_path

    storyboard_id = job["storyboard_id"]
    sb = await video_studio_service.get_storyboard(storyboard_id, job["user_id"])
    if sb is None:
        raise LookupError(f"分镜 {storyboard_id} 不存在")

    shots = sb.get("shots") or []
    clip_paths: list[str] = []
    transitions: list[str] = []
    for shot in shots:
        clip_url = shot.get("clip_url")
        if not clip_url:
            raise RuntimeError(f"镜头 {shot.get('shot_index')} 缺少视频片段，无法拼接")
        local = url_to_local_path(clip_url)
        if not local:
            raise RuntimeError(f"片段文件不存在：{clip_url}")
        clip_paths.append(local)
        transitions.append(shot.get("transition", "cut"))

    await report(20)
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory(prefix="vs_compose_") as td:
        out = Path(td) / "final.mp4"
        result_path = await compose_video_concat(
            clip_urls=clip_paths,
            transitions=transitions,
            output_path=str(out),
        )
        final_bytes = result_path.read_bytes()

    await report(85)
    asset = await save_video_asset(
        user_id=job["user_id"],
        filename=f"final_{storyboard_id[:8]}.mp4",
        content_type="video/mp4",
        data=final_bytes,
        source="ai",
        metadata={"source": "video_studio_compose", "storyboard_id": storyboard_id},
    )
    await report(95)

    await video_studio_service.mark_storyboard_completed(
        storyboard_id, asset["asset_id"], asset["original_url"]
    )
    return {"result_asset_id": asset["asset_id"], "result_url": asset["original_url"]}


def register_default_handlers(worker: VideoWorker) -> None:
    """把三类 handler 注册到 Worker（幂等，重复调用安全）。"""
    if "shot_image" not in worker.registered_types:
        worker.register("shot_image", _handle_shot_image)
    if "shot_video" not in worker.registered_types:
        worker.register("shot_video", _handle_shot_video)
    if "compose" not in worker.registered_types:
        worker.register("compose", _handle_compose)
