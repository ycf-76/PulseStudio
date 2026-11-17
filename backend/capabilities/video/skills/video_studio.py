"""video_studio 入口 skill — agent 工作室（Video Studio P2 Task 4）。

定位：接 Reactor skill_call + SSE 进度的入口。内部驱动 StudioToolLoop
（轻量工具循环：LLM 多轮 JSON-action + 工具白名单），而非调用固定编排函数；
与旧流水线 talking_head_overlay 并列，互不影响。

流程（execute 为 async generator，仿 talking_head_overlay）：
  resolve_owner → create_workspace → emit prepare → load_method + install
  （brief 非空时一并落为 rules/brief.md，agent 经 list_rules/read_rules_file
  读取——复用「打法=文本输入」装载机制，不改 studio_agent）→ emit method →
  StudioToolLoop.run()（工具级事件经包装回调映射为 phase 枚举转发 SSE，
  preview_url 一经产出随每次 emit 透传）→ 按结果 emit deliver / fail 并 yield。

平台红线自证：本文件零风格内容——无任何颜色/字体/动效常量；风格只存在于
studio_methods 文本与 agent 写出的场景代码。
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from app.core.skill_base import Skill
from app.core.skill_registry import register
from capabilities.video.services.studio_agent import StudioToolLoop
from capabilities.video.services.studio_workspace import create_workspace
from capabilities.video.skills import resolve_owner
from capabilities.video.skills.studio_methods import install_into_workspace, load_method

logger = logging.getLogger(__name__)

# 进度 phase 枚举（仿 talking_head_overlay._make_progress_emitter；
# 事件走 video_pipeline_progress 通道，前端渲染阶段清单 + 总百分比）
_PHASES = ("prepare", "method", "plan", "scene", "render", "review", "fix", "deliver")
_PROGRESS_TOTAL = len(_PHASES)

_PHASE_LABEL = {
    "prepare": "准备工作区",
    "method": "装载打法",
    "plan": "内容规划",
    "scene": "场景工程",
    "render": "渲染成片",
    "review": "质检",
    "fix": "修复重渲",
    "deliver": "交付成片",
}

# 工具级 action → 进度 phase 映射（StudioToolLoop 已按工具 emit，skill 层
# 不重复逐工具 emit，只做映射转发；render 修复轮单独映射为 fix）
_TOOL_PHASE_MAP = {
    "list_rules": "method",
    "read_rules_file": "method",
    "transcribe": "prepare",
    "prepare_assets": "prepare",
    "generate_image": "prepare",
    "save_timeline": "plan",
    "write_scene_file": "scene",
    "render": "render",  # attempt ≥ 2 时映射为 fix
    "review": "review",
    "start_preview": "scene",
}

# 终态事件（deliver/fail）由 skill 层按 run() 结果统一发射，转发层丢弃，
# 避免与 skill 层终态事件重复
_TERMINAL_TOOL_PHASES = ("deliver", "fail")


class VideoStudioInput(BaseModel):
    video_path: str
    method: dict = Field(default_factory=lambda: {"builtin": "general_talk"})
    brief: str = ""
    # 画幅硬约束（如 "1920x1080"）：save_timeline 校验 canvas 必须匹配。
    # brief 是软提示（LLM 遵从不稳定），画幅属确定性验收项走硬校验。
    canvas: str | None = None
    # 视觉效果强度硬校验：save_timeline 校验 effects.tears/shakes 申报参数
    # （位移 ≥40px、|rot| ≥2.5° 等）。同理属确定性验收项，prompt 恳求无效。
    require_fx: bool = False


@register
class VideoStudioSkill(Skill):
    """AI 口播工作室：agent 全自动创作循环入口。"""

    node_type: str = "edit"
    name: str = "video_studio"
    display_name: str = "AI 口播工作室"
    description: str = (
        "AI口播工作室：给定口播原片与打法（method），创作 agent 自动完成"
        "读打法→转录→内容规划→写场景→渲染→质检→修复→交付全流程，"
        "产出 out/final.mp4 与自审查报告，支持实时预览。"
    )
    skill_category: str = "edit"
    skill_priority: str = "hot"
    trigger_words: list[str] = ["口播工作室", "AI口播", "视频工作室", "自动成片"]

    # ── video_pipeline_progress 发射器（仿 talking_head_overlay）────────
    _PROGRESS_TOTAL = _PROGRESS_TOTAL

    def _make_progress_emitter(self, workflow_id: str, turn_id: str) -> Any:
        async def _emit(
            step: int, phase: str, label: str, detail: str = "", extra: dict | None = None
        ) -> None:
            if not workflow_id:
                return
            payload = {
                "skill": "video_studio",
                "step": step,
                "total": self._PROGRESS_TOTAL,
                "phase": phase,
                "label": label,
                "detail": detail,
                "percent": int(step / self._PROGRESS_TOTAL * 100),
            }
            if extra:
                payload.update(extra)
            from app.services.sse_bus import sse_bus

            await sse_bus.publish(
                workflow_id,
                "video_pipeline_progress",
                payload,
                turn_id=turn_id or None,
            )

        return _emit

    def _make_tool_forwarder(self, workflow_id: str, turn_id: str):
        """把 StudioToolLoop 的同步 emit_progress 回调映射为 SSE 进度事件。

        返回 (forward, tasks, state)：
        - forward(phase, payload)：同步回调（studio_agent 事件循环内调用），
          映射 phase 后用 create_task 调度异步发射；
        - tasks：待 gather 的发射任务（execute 结束前统一 await）；
        - state：{"preview_url": ...}——一经产出随后续每次事件透传。
        """
        emit = self._make_progress_emitter(workflow_id, turn_id)
        tasks: list[asyncio.Task] = []
        state: dict[str, str | None] = {"preview_url": None}

        def forward(phase: str, payload: dict) -> None:
            try:
                preview_url = payload.get("preview_url")
                if preview_url:
                    state["preview_url"] = str(preview_url)
                if phase in _TERMINAL_TOOL_PHASES:
                    return
                if phase == "render" and int(payload.get("attempt") or 1) >= 2:
                    mapped = "fix"
                else:
                    mapped = _TOOL_PHASE_MAP.get(phase)
                if mapped is None:
                    return
                step = _PHASES.index(mapped) + 1
                extra = {"tool": phase, "ok": bool(payload.get("ok", True))}
                if state["preview_url"]:
                    extra["preview_url"] = state["preview_url"]
                detail = json.dumps(payload, ensure_ascii=False, default=str)[:300]
                tasks.append(
                    asyncio.get_running_loop().create_task(
                        emit(step, mapped, _PHASE_LABEL[mapped], detail, extra)
                    )
                )
            except Exception:
                logger.warning("forward progress failed for phase %s", phase, exc_info=True)

        return forward, tasks, state

    async def execute(self, params: dict[str, Any]):
        parsed = VideoStudioInput(**params)
        video_path = Path(parsed.video_path)
        user_id, workflow_id = resolve_owner(params)
        turn_id = str(params.get("_turn_id") or "")

        if not video_path.is_file():
            yield {"success": False, "error": f"video not found: {parsed.video_path}"}
            return

        # LLM：inputs["llm"] 优先（测试/编排注入），fallback model_router
        llm = params.get("llm")
        if llm is None:
            try:
                from app.adapters.model_router import get_model_router

                llm = get_model_router().resolve_llm()
            except Exception as exc:
                logger.warning("[video_studio] resolve_llm failed: %s", exc)
                llm = None
        if llm is None:
            yield {
                "success": False,
                "error": "LLM 不可用：inputs 未注入 llm 且 model_router 未配置",
            }
            return

        film_id = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}"
        ws = create_workspace(film_id)
        workspace_root = Path(ws["out"]).parent

        emit = self._make_progress_emitter(workflow_id, turn_id)
        forward, tasks, state = self._make_tool_forwarder(workflow_id, turn_id)

        try:
            await emit(
                1, "prepare", _PHASE_LABEL["prepare"], f"film={film_id} video={video_path.name}"
            )
            method = load_method(parsed.method)
            install_into_workspace(ws, method)
            if parsed.brief.strip():
                # 用户补充要求：以文本形态随打法装入 rules/（同一装载机制，
                # agent 经 list_rules / read_rules_file 读取），不改写不解释
                (Path(ws["inputs"]) / "rules" / "brief.md").write_text(
                    parsed.brief, encoding="utf-8"
                )
            await emit(2, "method", _PHASE_LABEL["method"], method["name"])

            required_canvas = None
            if parsed.canvas:
                try:
                    w, h = parsed.canvas.lower().split("x")
                    required_canvas = (int(w), int(h))
                except ValueError:
                    yield {
                        "success": False,
                        "error": f"canvas 参数非法（期望 WxH，如 1920x1080）：{parsed.canvas}",
                    }
                    return
            loop = StudioToolLoop(
                ws,
                llm,
                method["text"],
                str(video_path),
                forward,
                required_canvas=required_canvas,
                required_fx=parsed.require_fx,
            )
            result = await loop.run()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

            if result.get("ok"):
                review = result.get("review") or {}
                out: dict[str, Any] = {
                    "success": True,
                    "video_path": result.get("video_path"),
                    "workspace": str(workspace_root),
                    "review_summary": {
                        "error_count": len(review.get("errors") or []),
                        "errors": review.get("errors") or [],
                        "loudness": review.get("loudness") or {},
                    },
                    "turns": result.get("turns"),
                }
                if state["preview_url"]:
                    out["preview_url"] = state["preview_url"]
                await emit(
                    _PROGRESS_TOTAL,
                    "deliver",
                    _PHASE_LABEL["deliver"],
                    str(out.get("video_path") or ""),
                    {"video_path": out.get("video_path")},
                )
                yield out
            else:
                error = result.get("error") or "创作循环失败（无错误详情）"
                await emit(_PROGRESS_TOTAL, "fail", "创作失败", error[:300])
                failed: dict[str, Any] = {"success": False, "error": error}
                if result.get("review"):
                    failed["review"] = result["review"]
                yield failed
        except Exception as exc:
            logger.exception("[video_studio] execute failed")
            try:
                await emit(_PROGRESS_TOTAL, "fail", "创作失败", str(exc)[:300])
            except Exception:
                pass
            yield {"success": False, "error": str(exc)}
