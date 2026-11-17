"""口播演示一键（2026-11-08）— 确定性编排 skill。

背景：前端启动口播任务靠 LLM 组装参数，注入示例只传 video_path →
overlay 默认走字幕增强链，pip/matting/auto_deck/handoff 全没开，
成片只剩原片（用户实机反馈「幻灯片是空的，小窗也是，只有原视频」）。

修复：同 auto_short_video 先例 —— 后端确定性串链，LLM 只传意图：
  smart_cut 裁剪串联（plan → refine 两轮，失败降级原片）
  → TalkingHeadOverlaySkill PIP 演示链全参数直通
    （pip + matting + auto_deck + handoff + auto_confirm 无人值守）
  → 透传 skill_call 注入的 _user_id/_workflow_id（落盘分层/SSE 归属）

deck 确认停点一期不做（auto_confirm 直通）；确认交互复用既有
cut_plan 闸门模式，待前端接 deck_preview_ready 后升级。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from app.core.skill_registry import Skill, register
from capabilities.video.skills import resolve_owner
from capabilities.video.skills.smart_cut import SmartCutSkill
from capabilities.video.skills.talking_head_overlay import TalkingHeadOverlaySkill

logger = logging.getLogger(__name__)


class TalkingHeadDemoInput(BaseModel):
    """口播演示一键输入 — LLM 只需传 video_path，其余全内置。"""

    video_path: str
    deck_theme: str = "auto"  # midnight/cream/sunset/auto（透传 overlay）
    pip_position: str = "top_right"
    pip_scale: float = 0.28
    cut_enabled: bool = True  # smart_cut 裁剪串联开关（默认开）


@register
class TalkingHeadDemoSkill(Skill):
    """口播演示一键：原片 → 成品演示视频。

    确定性串链：smart_cut 裁剪 → matting 角落人像 → AI 演示幻灯片
    （转录自动生成）→ 说到才翻页 → 文字流式 → 开场全屏让位。
    """

    node_type = "produce"
    name = "talking_head_demo"
    display_name = "口播演示一键"

    description = (
        "口播视频一键出成品演示片：智能裁剪→人物抠像角落小窗→"
        "AI 自动生成演示幻灯片→口播说到才翻页→文字流式→"
        "开场全屏缩小让位。只传 video_path 即可。"
    )
    skill_category = "produce"
    skill_priority = "hot"
    trigger_words = ["口播演示", "口播视频", "演示视频", "口播出片", "讲解视频"]
    usage_hint = (
        "用法说明（必须遵守）：\n"
        "- 用户给口播视频要出演示片 → 只传 "
        "arguments={'video_path': '<视频路径>}，"
        "pip/抠像/幻灯片/翻页等已内置，不要自行补传 overlay 参数\n"
        "- 不要用 talking_head_overlay 直接组参数（那是底层技能，"
        "缺参数只会出字幕增强片）\n"
    )
    input_schema = TalkingHeadDemoInput

    async def execute(
        self,
        params: dict[str, Any],
    ) -> Any:  # async generator（框架按最后一个 yield 取结果）
        parsed = TalkingHeadDemoInput(**params)
        user_id, workflow_id = resolve_owner(params)
        video_path = Path(parsed.video_path)

        if not video_path.is_file():
            yield {"success": False, "error": f"video not found: {parsed.video_path}"}
            return

        # ── 1) smart_cut 裁剪串联（plan → refine；失败降级原片不阻断）──
        cut_path, kept_segments = None, None
        if parsed.cut_enabled:
            try:
                sc = SmartCutSkill()
                plan = await sc.execute(
                    {
                        "video_path": parsed.video_path,
                        "detect_fluff": True,
                        "chain_with_overlay": True,
                    }
                )
                if plan.get("mode") == "plan":
                    cut = await sc.execute(
                        {
                            "video_path": parsed.video_path,
                            "refine_candidates": plan["plan"]["candidates"],
                            "chain_with_overlay": True,
                        }
                    )
                    cut_path = cut.get("output_path")
                    kept_segments = cut.get("kept_segments")
            except Exception as e:
                logger.warning(f"[talking_head_demo] smart_cut 失败，降级原片：{e}")

        # ── 2) overlay PIP 演示链全参数直通（auto_confirm 无人值守）──
        overlay_params: dict[str, Any] = {
            "video_path": parsed.video_path,
            "gate_mode": "auto_confirm",
            "pip": True,
            "pip_position": parsed.pip_position,
            "pip_scale": parsed.pip_scale,
            "matting": True,
            "auto_deck": True,
            "deck_theme": parsed.deck_theme,
            "handoff": True,
            # 归属透传：落盘目录/SSE 按 user/workflow 分层
            "_user_id": user_id,
        }
        if workflow_id:
            overlay_params["_workflow_id"] = workflow_id
        if cut_path:
            overlay_params["cut_video_path"] = cut_path
            overlay_params["kept_segments"] = kept_segments

        output_path = None
        last_error = None

        async for chunk in TalkingHeadOverlaySkill().execute(overlay_params):
            if not isinstance(chunk, dict):
                continue
            if chunk.get("event") == "pip_done":
                output_path = (chunk.get("data") or {}).get("output_path")
            if chunk.get("success") is False:
                last_error = chunk.get("error")
            elif chunk.get("event", "").endswith("_error"):
                last_error = (chunk.get("data") or {}).get("error") or chunk.get("error")

        # ── 3) 终态（框架取最后一个 yield）──
        if output_path:
            yield {
                "success": True,
                "output_path": output_path,
                "cut_video_path": cut_path,
                "message": "口播演示成片完成",
            }
        else:
            yield {
                "success": False,
                "error": last_error or "演示合成未产出成片（overlay 无 pip_done）",
            }
