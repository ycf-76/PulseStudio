"""动态布局编排 Skill — 口播镜头随内容动态切换构图（G2）。

设计依据 docs/smart-cut-and-overlay.md 10.18（已批准方案）：
  开场人像全屏 → 讲核心内容时缩小 + storyboard 配图当主画面 → 收束回全屏。

  与 talking_head_overlay 静态 pip 的区别：_execute_pip 全程一个位置一个
  缩放；本 skill 由 LLM 按叙事节奏产出 LayoutTimeline（FULL/PIP/BROLL
  三原语白名单 + 时间轴），确定性编译成分段 ffmpeg + concat。

  三重锁：LLM 只产 JSON → 契约校验（不过带反馈重试）→ 零执行编译。
  降级链：布局规划失败 → 单段静态 pip → 调用方回退原片，绝不阻断。

闸门（对齐 talking_head_overlay 模式）：
  standalone  首调只出 layout_plan_ready 事件，用户确认后带 layout_plan 回调
  auto_confirm 广播规划后直接编译出片（供 auto_short_video 内嵌）
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from app.core.skill_base import Skill
from app.core.skill_registry import register

logger = logging.getLogger(__name__)

# 测试与降级可替换的模块级依赖（monkeypatch 挂点）
from capabilities.video.services.layout_orchestrator import (  # noqa: E402
    LayoutTimeline,
    compile_layout,
    fallback_timeline,
    generate_layout_plan,
    plan_key,
    review_layout,
    validate_layout_timeline,
)
from capabilities.video.skills import resolve_owner, scoped_dir
from capabilities.video.skills.talking_head_overlay import (  # noqa: E402
    GateMode,
    managed_temp_dir,
)

_OUTPUT_DIR = Path(__file__).resolve().parent.parent.parent.parent / "outputs"


def _probe_duration(video_path: str) -> Any:
    """视频时长探测 — 复用 silence_detect.probe_duration。"""
    from capabilities.video.adapters.silence_detect import probe_duration

    return probe_duration(video_path)


async def _probe_resolution(video_path: str) -> tuple[int, int]:
    """获取视频分辨率 — PyAV 优先，ffprobe 兜底，再失败 (1080, 1920)。

    对齐 talking_head_overlay._probe_resolution 实现（本机 ffprobe 不在 PATH）。
    """
    try:
        import av

        def _av_probe() -> tuple[int, int]:
            with av.open(video_path) as container:
                for stream in container.streams:
                    if stream.type == "video":
                        return (
                            stream.codec_context.width,
                            stream.codec_context.height,
                        )
            raise ValueError("no video stream found")

        import asyncio

        return await asyncio.to_thread(_av_probe)
    except Exception:
        pass

    import asyncio
    import json as _json

    proc = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_streams", video_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    info = _json.loads(stdout)
    for stream in info.get("streams", []):
        if stream.get("codec_type") == "video":
            return int(stream["width"]), int(stream["height"])
    raise ValueError(f"no video stream in {video_path}")


def _resolve_llm() -> Any:
    """规划用 LLM — 配置缺失/导入失败返回 None（走 fallback 降级）。"""
    try:
        from app.adapters.deepseek import DeepSeekAdapter
        from app.config import Settings
        return DeepSeekAdapter(api_key=Settings().deepseek_api_key)
    except Exception:
        return None


def _resolve_vlm() -> Any:
    """审片用 VLM — 模型名走 tier router 解析（对齐 overlay_review；
    真机教训：不走 router 的默认模型名 404 Model not exist）。
    配置缺失/导入失败返回 None（跳过审查，不阻断）。"""
    try:
        from capabilities.image_gen.adapters.qwen_vl import QwenVLAdapter
        from app.config import Settings
        from app.services.model_tier import PipelineStage, get_tier_router

        vl_model = get_tier_router().resolve(PipelineStage.VL_UNDERSTAND, "vl")
        return QwenVLAdapter(api_key=Settings().dashscope_api_key, model=vl_model)
    except Exception:
        return None


class LayoutInput(BaseModel):
    """动态布局编排输入。"""
    video_path: str = Field(description="视频文件路径")
    gate_mode: GateMode = Field(default=GateMode.STANDALONE, description="闸门模式")
    brolls: list[str] | None = Field(
        default=None,
        description="可用配图路径列表（storyboard 分镜等），布局 main_source 按 shot:N 引用",
    )
    transcript: list[dict[str, Any]] | None = Field(
        default=None,
        description="口播转录时间轴（提高布局规划质量）：[{start, end, text}]",
    )
    layout_plan: dict[str, Any] | None = Field(
        default=None,
        description="确认回调：layout_plan_ready 事件里的 timeline 原样回传执行",
    )


@register
class DynamicLayoutSkill(Skill):
    """动态布局编排 Skill。"""

    node_type: str = "edit"
    name: str = "dynamic_layout"
    display_name: str = "动态布局编排"
    description: str = (
        "口播镜头随内容动态切换构图：LLM 按叙事节奏产出布局时间轴"
        "（开场人像全屏 → 讲核心内容时缩小+配图当主画面 → 收束回全屏），"
        "确定性编译成分段 ffmpeg 渲染。三原语：full/pip/broll。"
        "规划失败自动降级为静态画中画，绝不阻断出片。"
    )
    skill_category: str = "edit"
    skill_priority: str = "hot"
    trigger_words: list[str] = ["动态布局", "布局编排", "镜头切换", "动态画中画"]
    needs_confirmation: bool = True

    async def execute(self, params: dict[str, Any]) -> Any:
        parsed = LayoutInput(**params)
        video_path = Path(parsed.video_path)

        # 产物隔离：落盘目录按「用户 → 本次创作任务」分层。
        # 此前全局 outputs/dynamic_layout/ + 文件名仅含规划指纹（plan_key 是
        # timeline 的纯内容哈希，不含归属），两个用户对同名视频产出相同布局
        # 时路径重合，后者覆盖前者。
        user_id, workflow_id = resolve_owner(params)
        scope_dir = scoped_dir(_OUTPUT_DIR / "dynamic_layout", user_id, workflow_id)

        if not video_path.is_file():
            yield {"success": False, "error": f"video not found: {parsed.video_path}"}
            return

        try:
            duration = float(await _probe_duration(str(video_path)))
        except Exception:
            duration = 0.0
        if duration <= 0:
            yield {"success": False, "error": "无法探测视频时长，布局编排需要有效时长"}
            return

        try:
            video_wh = await _probe_resolution(str(video_path))
        except Exception:
            video_wh = (1080, 1920)

        brolls = [str(p) for p in (parsed.brolls or [])]

        try:
            # ── 确认回调路径：用户已确认的计划 → 校验后直接编译 ──
            if parsed.layout_plan is not None:
                async for chunk in self._execute_confirmed(
                    parsed, video_path, duration, video_wh, brolls, scope_dir,
                ):
                    yield chunk
                return

            # ── 规划路径 ──
            llm = _resolve_llm()
            timeline, raw = await generate_layout_plan(
                llm, duration,
                transcript_segments=parsed.transcript, brolls=brolls,
            )
            degraded = False
            if timeline is None:
                timeline = fallback_timeline(duration, broll_count=len(brolls))
                degraded = True
            else:
                # 规划通过校验才返回；fallback 必然合法 — 双保险复验
                problems = validate_layout_timeline(timeline, duration, len(brolls))
                if problems:
                    timeline = fallback_timeline(duration, broll_count=len(brolls))
                    degraded = True

            plan_message = (
                f"布局规划完成：{len(timeline.segments)} 段"
                + ("（规划失败，已降级为静态画中画布局）" if degraded else "，请确认")
            )
            plan_event = {
                "event": "layout_plan_ready",
                "data": {
                    "timeline": timeline.model_dump(),
                    "duration": duration,
                    "brolls": brolls,
                    "raw_plan": raw,
                    "degraded": degraded,
                },
                "needs_confirmation": parsed.gate_mode != GateMode.AUTO_CONFIRM,
                "message": plan_message,
            }
            if parsed.gate_mode != GateMode.AUTO_CONFIRM:
                yield plan_event
                return
            yield plan_event

            async for chunk in self._compose_and_report(
                video_path, timeline, brolls, video_wh, scope_dir,
            ):
                yield chunk

        except Exception as e:
            logger.error(f"dynamic_layout failed: {e}", exc_info=True)
            yield {
                "event": "layout_error",
                "data": {"error": str(e)},
                "needs_confirmation": False,
            }

    async def _execute_confirmed(
        self,
        parsed: LayoutInput,
        video_path: Path,
        duration: float,
        video_wh: tuple[int, int],
        brolls: list[str],
        scope_dir: Path,
    ) -> Any:
        """确认回调：layout_plan 原样执行（不重新规划）。校验不过拒绝执行。"""
        try:
            timeline = LayoutTimeline(**parsed.layout_plan)  # type: ignore[arg-type]
        except Exception as e:
            yield {"success": False, "error": f"布局计划无法解析：{e}"}
            return
        problems = validate_layout_timeline(timeline, duration, len(brolls))
        if problems:
            yield {
                "success": False,
                "error": "布局计划未通过校验：" + "；".join(problems),
            }
            return

        async for chunk in self._compose_and_report(
            video_path, timeline, brolls, video_wh, scope_dir,
        ):
            yield chunk

    async def _compose_and_report(
        self,
        video_path: Path,
        timeline: LayoutTimeline,
        brolls: list[str],
        video_wh: tuple[int, int],
        scope_dir: Path,
    ) -> Any:
        """编译 + 落盘 + VLM 审查 + 完成事件。"""
        async with managed_temp_dir() as tmp_dir:
            final_path = await compile_layout(
                str(video_path), timeline, brolls, tmp_dir, video_wh,
            )

            perm_dir = scope_dir
            perm_dir.mkdir(parents=True, exist_ok=True)
            # 产物隔离：同源视频 + 不同布局时间轴 = 不同作品（对齐 talking_head_overlay）
            perm_path = perm_dir / (
                f"{Path(video_path).stem}_layout_{plan_key(timeline)}.mp4"
            )
            shutil.copy2(str(final_path), str(perm_path))

        message = f"布局合成完成：{len(timeline.segments)} 段，{timeline.segments[0].primitive.value} 开场"
        vlm = _resolve_vlm()
        if vlm is not None:
            feedback = await review_layout(str(perm_path), timeline, vlm)
            if feedback:
                # 布局审查问题如实上报（auto_confirm 供上游日志，不自动重规划）
                message += f"；VLM 审查发现问题：{feedback}"

        yield {
            "event": "layout_compose_done",
            "data": {
                "output_path": str(perm_path),
                "segment_count": len(timeline.segments),
                "timeline": timeline.model_dump(),
            },
            "needs_confirmation": False,
            "message": message,
        }