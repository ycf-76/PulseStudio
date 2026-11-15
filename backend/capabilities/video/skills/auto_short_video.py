"""一键短视频编排 Skill：主题 → 成品短视频。

设计依据：
  - Pulse auto-short-video SKILL.md 的编排流程（L28-58）
  - 编排原则：零件可缺（缺 API key 的环节自动降级，不阻断整体）
  - 先出 Plan：涉及多个付费 API 时先向用户说明将调用哪些
  - 中间产物留档：分镜图/配音/字幕/storyboard 都存 asset_library

编排流程（依据 Pulse auto-short-video SKILL.md L28-58）：
  1. 写脚本分镜：LLM 把主题写成口播文案，拆成 N 句
  2. 生成画面：逐句 text2img（有图像 API key）或降级
  3. 配音：TTS 把文案合成口播（edge-tts 在线 → piper 离线降级）或跳过
  4. 合成成片：调 compose_video（ffmpeg 确定性合成）
  5. 交付：产出 final.mp4 + 制作说明

确认闸门（2026-10-17，对齐小加同学 3 阶段流水线）：
  本技能与 multi_image_video 一样，禁止"一条路走完直接出片"。
  第一次调用只走到 Step 1（脚本分镜）就停下，返回 needs_confirmation=True 的草稿；
  用户确认/编辑后，调用方把最终 shots 通过 refine_shots 回传，才执行 Step 2-4。
  这样付费的 text2img / TTS 不会在用户还没看过脚本时就被消耗掉。

  三种调用组合：
    - 只给 topic              → 返回分镜草稿 + needs_confirmation=True（不生成任何画面）
    - refine_shots=[...]      → 视为"用户已确认"，从 Step 2 开始生成
    - refine_shots=[]         → 报错（空分镜无法生成）
"""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.core.skill_base import Skill
from app.core.skill_registry import register

logger = logging.getLogger(__name__)

_OUTPUT_DIR = Path(__file__).resolve().parent.parent.parent.parent / "outputs"


class AutoShortVideoInput(BaseModel):
    """一键短视频输入参数。

    字段设计依据：
      topic: Pulse auto-short-video SKILL.md 输入"主题/文案（必填）"
      target_duration: Pulse auto-short-video SKILL.md "可选：目标时长"
      ratio: 画幅比（Pulse 要求"画幅确认硬门"，默认 9:16 竖屏）
      style: Pulse auto-short-video SKILL.md "可选：风格"
      need_narration: 是否需要配音（Pulse "零件可缺"原则）
      need_bgm: 是否需要 BGM（Pulse "零件可缺"原则）
      image_motion: Pulse assemble.py image_motion（ken-burns / static）
    """

    topic: str = Field(min_length=2, max_length=500, description="视频主题或文案")
    target_duration: float = Field(default=30.0, ge=5.0, le=300.0, description="目标时长（秒），5-300s")
    ratio: Literal["9:16", "16:9"] = Field(
        default="9:16", description="画幅比：9:16 竖屏（小红书/抖音）/ 16:9 横屏（B站）"
    )
    style: str = Field(default="口播", description="风格：口播/科普/种草/教程")
    need_narration: bool = Field(default=True, description="是否需要配音（缺 TTS 时自动降级跳过）")
    need_bgm: bool = Field(default=False, description="是否需要 BGM")
    image_motion: Literal["ken-burns", "static"] = Field(
        default="ken-burns", description="图片默认运动：ken-burns（照片缓动）/ static（文字卡/图表）"
    )
    refine_shots: list[dict[str, Any]] | None = Field(
        default=None,
        description="用户确认/修改后的分镜列表（确认闸门的唯一信号）。"
                    "为 None 表示首次编排，只返回草稿；非空表示已确认，从生成画面开始执行。",
    )
    refine_title: str | None = Field(
        default=None,
        description="用户确认时的视频标题。refine 模式会跳过 LLM 重编脚本，"
                    "靠它保留用户看到的那个标题（不给则用 topic）。",
    )
    enhance_overlay: bool = Field(
        default=False,
        description="成片后自动叠加增强（动态字幕/图标/卡片动画，talking_head_overlay，"
                    "叙事两步链+VLM 视觉自审）。缺 Whisper/LLM/Playwright 零件时"
                    "自动降级返回原成片，绝不阻断主管线。",
    )


_SIZE_MAP = {"9:16": "1080x1920", "16:9": "1920x1080"}


def _normalize_shot(shot: dict) -> dict:
    """统一运镜字段：LLM 脚本写的是 motion，前端分镜卡片编辑的是 motion_prompt。

    两边互认，否则用户在卡片里改的运镜在回放时被静默丢掉。
    """
    out = dict(shot)
    if not out.get("motion_prompt") and out.get("motion"):
        out["motion_prompt"] = out["motion"]
    if not out.get("motion") and out.get("motion_prompt"):
        out["motion"] = out["motion_prompt"]
    return out


def _build_transcript(
    shots: list[dict], total_duration: float,
) -> list[dict[str, Any]]:
    """由分镜文案构造词级时间轴（内嵌 overlay 跳过 whisper 用）。

    实机依据（2026-10-28 全链验收）：TTS 合成音频 whisper 误听率高 —
    "90%的人都漏了"听成"九十辈子的人都漏了"，规划 LLM 基于错文本提取
    关键词导致叠加内容与画面语义错位。上游手里就有 script 原文，
    直接喂原文既根除误听又省掉一次 whisper 全片转录。

    时间轴：按镜时长比例缩放到成片实际时长（合成可能有截断/pad）；
    镜内词按字符数比例分配 — 中文口播语速近似线性，规划只要词边界。
    """
    items = [
        ((s.get("caption") or "").strip(), max(float(s.get("duration") or 0), 0.1))
        for s in shots
    ]
    items = [(t, d) for t, d in items if t]
    if not items or total_duration <= 0:
        return []

    scale = total_duration / sum(d for _, d in items)
    spans = [d * scale for _, d in items]
    # 末段吃掉累积误差，保证与成片时长精确对齐
    spans[-1] = max(total_duration - sum(spans[:-1]), 0.1)

    try:
        import jieba

        def _tokens(text: str) -> list[str]:
            return [t for t in jieba.cut(text) if t.strip()]
    except ImportError:
        def _tokens(text: str) -> list[str]:
            return [text]

    out: list[dict[str, Any]] = []
    cursor = 0.0
    for (text, _), span in zip(items, spans):
        start, end = cursor, cursor + span
        cursor = end
        toks = _tokens(text)
        total_chars = sum(len(t) for t in toks) or 1
        words: list[dict[str, Any]] = []
        acc = 0
        for tok in toks:
            ws = start + span * acc / total_chars
            acc += len(tok)
            we = start + span * acc / total_chars
            words.append({"text": tok, "start": round(ws, 3), "end": round(we, 3)})
        out.append({
            "index": len(out),
            "start": round(start, 3),
            "end": round(end, 3),
            "text": text,
            "words": words,
        })
    return out

_SCRIPT_PROMPT = """你是一个短视频脚本编剧。请根据以下要求写出一条短视频的分镜脚本。

主题：{topic}
目标时长：{duration}秒
风格：{style}
画幅：{ratio}

请输出 JSON 格式（不要 markdown 代码块，直接输出 JSON）：
{{
  "title": "视频标题",
  "hook": "开头 hook（3秒内抓住注意力）",
  "shots": [
    {{
      "caption": "这一句的口播文案",
      "image_prompt": "这一句配图的画面描述（英文，用于 AI 生图）",
      "duration": 3.0,
      "motion": "ken-burns"
    }}
  ],
  "closing": "结尾号召语"
}}

要求：
- 每句 15-30 字，口语化，适合口播
- 分镜数 = 目标时长 / 每镜时长（每镜 3-5 秒）
- hook 要有好奇缺口或模式打断
- image_prompt 要具体、有画面感
- 总时长尽量接近目标时长
"""


async def _generate_script(topic: str, duration: float, style: str, ratio: str) -> dict:
    """用 LLM 生成视频脚本分镜。

    依据：Pulse auto-short-video SKILL.md Step 1 "写脚本分镜"，
    用 video-script 把主题写成口播文案，拆成 N 句。
    本实现直接用项目 LLM 调用能力生成 JSON 脚本。
    """
    from app.config import get_settings

    settings = get_settings()
    prompt = _SCRIPT_PROMPT.format(topic=topic, duration=int(duration), style=style, ratio=ratio)

    try:
        # DeepSeekAdapter — 与 talking_head_overlay._resolve_llm 同源
        # （app.engine.llm_client 是不存在的旧路径，真机全链验收暴露）
        from app.adapters.deepseek import DeepSeekAdapter
        llm = DeepSeekAdapter(api_key=settings.deepseek_api_key)
        from app.adapters.llm_base import llm_prompt_to_text
        text = await llm_prompt_to_text(llm, prompt)
        json_start = text.find("{")
        json_end = text.rfind("}") + 1
        if json_start >= 0 and json_end > json_start:
            script = json.loads(text[json_start:json_end])
        else:
            script = json.loads(text)
        if "shots" not in script or not script["shots"]:
            raise ValueError("脚本缺少 shots 字段")
        return script
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning(f"LLM 脚本解析失败，使用默认脚本：{e}")
        num_shots = max(2, int(duration / 4))
        per_dur = duration / num_shots
        return {
            "title": topic,
            "hook": f"关于{topic}，你可能不知道这些",
            "shots": [
                {
                    "caption": f"第{i + 1}点：{topic}的相关内容",
                    "image_prompt": f"illustration of {topic}, scene {i + 1}, high quality",
                    "duration": per_dur,
                    "motion": "ken-burns",
                }
                for i in range(num_shots)
            ],
            "closing": "关注我，了解更多",
        }


async def _generate_shot_images(shots: list[dict], ratio: str, user_id: str) -> list[dict]:
    """逐句生成配图。

    依据：Pulse auto-short-video SKILL.md Step 2 "生成画面"，
    有图像 API key → WanxAdapter 逐句 text2img。
    缺 key → 降级（返回空 image 字段，compose 会用纯色占位）。
    """
    from app.config import get_settings

    settings = get_settings()
    api_key = (settings.dashscope_api_key or "").strip()
    if not api_key:
        logger.info("未配置图像 API key，跳过配图生成（降级）")
        return shots

    try:
        from capabilities.image_gen.adapters.image_gen import WanxAdapter

        adapter = WanxAdapter(api_key=api_key)
        size_map = {"9:16": "720x1280", "16:9": "1280x720"}
        size = size_map.get(ratio, "720x1280")

        for shot in shots:
            prompt = shot.get("image_prompt", "")
            if not prompt:
                continue
            try:
                result = await adapter.generate(prompt=prompt, size=size, count=1)
                # 归一化：WanxAdapter 可能返回 list[bytes]/list[str]/bytes
                if isinstance(result, list):
                    result = result[0] if result else None
                img_bytes: bytes | None = None
                if isinstance(result, bytes):
                    img_bytes = result
                elif isinstance(result, str) and result.startswith("http"):
                    import urllib.request

                    with urllib.request.urlopen(result, timeout=60) as resp:
                        img_bytes = resp.read()
                if not img_bytes:
                    continue
                from app.services.asset_library import save_asset

                asset = await save_asset(
                    user_id=user_id,
                    filename=f"shot_{hash(prompt) % 10000:04d}.png",
                    content_type="image/png",
                    data=img_bytes,
                )
                from app.services.asset_library import url_to_local_path

                local_path = url_to_local_path(asset["original_url"])
                shot["image"] = local_path or asset["original_url"]
                logger.info(f"配图生成成功：{shot.get('caption', '')[:20]}")
            except Exception as e:
                logger.warning(f"配图生成失败，跳过：{e}")
        return shots
    except Exception as e:
        logger.warning(f"图像适配器初始化失败，跳过配图：{e}")
        return shots


async def _generate_narration(
    script: dict, user_id: str, output_dir: Path | None = None, run_id: str = "",
) -> tuple[str | None, str]:
    """配音：edge-tts 在线优先，失败降级 piper 离线。

    依据：Pulse auto-short-video SKILL.md Step 3 "配音"。
    返回 (配音文件路径, 来源标签)；都失败返回 (None, "none")。
    """
    shots = script.get("shots", [])
    full_text = "。".join(s.get("caption", "") for s in shots if s.get("caption"))
    if not full_text.strip():
        return (None, "none")

    # 产物隔离：配音落在「本次运行」的专属目录。此前回退到用户级公共
    # outputs/assets/<user_id>/narration.mp3 —— 同一用户两个作品并发配音
    # 会互相覆盖（后写的盖掉先写的，成片配上别人的音）。
    output_dir = output_dir or (
        _OUTPUT_DIR / user_id / "auto_short_video"
        / (run_id or uuid.uuid4().hex[:16]) / "assets"
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        from app.agents.prompts.scripts.tts_edge import synthesize

        voice_path = output_dir / "narration.mp3"
        result = await synthesize(text=full_text, output_path=voice_path, voice="zh-CN-XiaoxiaoNeural")
        return (str(result), "edge-tts")
    except Exception as e:
        logger.warning(f"edge-tts 配音失败，尝试 piper 离线降级：{e}")

    try:
        from app.config import get_settings
        from app.services.media_capability import tts_piper

        settings = get_settings()
        voice_path = output_dir / "narration_piper.wav"
        result = await tts_piper(
            text=full_text,
            output_path=voice_path,
            binary=settings.piper_binary,
            model_path=settings.piper_model_path,
        )
        return (str(result), "piper")
    except Exception as e:
        logger.warning(f"piper 离线配音也不可用：{e}")
        return (None, "none")


@register
class AutoShortVideoSkill(Skill):
    """一键短视频：主题 → 成品短视频。

    依据：Pulse auto-short-video SKILL.md 的编排流程，
    自动串联 文案→配图→配音→合成，把制作层零件编排成一条"一键出片"流水线。
    """

    node_type = "produce"
    name = "auto_short_video"

    # 能力侧自带用法指引（prompt.build_skill_catalog 聚合进系统提示）
    usage_hint = (
        "用法说明（必须遵守）：\n"
        "- 用户只给主题/文案、要一键出片 → arguments 含 topic\n"
        "- 必须分两步走，禁止一步出片：第一步拿分镜草稿（系统自动返回 "
        "needs_confirmation），整理分镜表给用户确认；第二步用户确认后才带 "
        "refine_shots（按用户意见改过的 shots）和原始参数重调，视频才真正生成\n"
        "- 未经用户确认，绝对不要带 refine_shots 调用\n"
    )
    display_name = "一键短视频"

    description = (
        "一句话主题直接出成品短视频：自动串联 文案→配图→配音→字幕→合成，"
        "输出可播放的视频。缺 API key 的环节自动降级，不阻断整体。"
    )
    skill_category = "create"
    skill_priority = "hot"
    trigger_words = [
        "一键生成视频",
        "自动做短视频",
        "主题生成视频",
        "帮我做条视频",
        "口播视频一条龙",
        "自动出片",
        "短视频一键生成",
    ]

    input_schema = AutoShortVideoInput

    @staticmethod
    async def _enhance_with_overlay(
        video_path: str, parsed: AutoShortVideoInput, user_id: str,
        transcript: list[dict[str, Any]] | None = None,
        workflow_id: str = "",
    ) -> dict[str, Any] | None:
        """Step 4.5：成片叠加增强 — 内嵌 talking_head_overlay（AUTO_CONFIRM）。

        产出增强片另存 asset_library（不覆盖原片）；任何失败（转录/规划/
        渲染/合成零件缺失）→ 返回 None，调用方降级返回原成片 — 与全管线
        "增强是增值步骤绝非闸门"的降级契约一致。

        transcript：上游已知文本时间轴（script captions）— 传入则 overlay
        跳过 whisper，根除 TTS 合成音频误听（实机："90%"→"九十辈子"）。
        """
        try:
            from capabilities.video.skills.talking_head_overlay import (
                TalkingHeadOverlaySkill,
            )

            overlay_skill = TalkingHeadOverlaySkill()
            overlay_params: dict[str, Any] = {
                "video_path": video_path,
                "gate_mode": "auto_confirm",
            }
            # 归属透传：overlay 的落盘目录按 user/workflow 分层，
            # 内嵌调用不带上身份就会退回全局目录，跨用户互相覆盖。
            overlay_params["_user_id"] = user_id
            if workflow_id:
                overlay_params["_workflow_id"] = workflow_id
            if transcript:
                overlay_params["transcript"] = transcript
            output_path: str | None = None
            async for chunk in overlay_skill.execute(overlay_params):
                if chunk.get("event") == "overlay_compose_done":
                    output_path = chunk["data"]["output_path"]
                elif chunk.get("event") == "overlay_error":
                    logger.warning(
                        f"overlay enhance failed: {chunk['data'].get('error')}"
                    )
            if not output_path:
                return None

            from app.services.asset_library import save_video_asset

            data = Path(output_path).read_bytes()
            return await save_video_asset(
                user_id=user_id,
                filename=f"{parsed.topic[:20]}_enhanced.mp4",
                content_type="video/mp4",
                data=data,
                source="ai",
                metadata={
                    "source": "talking_head_overlay",
                    "chained_from": "auto_short_video",
                    "topic": parsed.topic,
                },
            )
        except Exception as e:
            logger.warning(f"overlay enhance degraded: {e}")
            return None

    async def execute(self, inputs: dict[str, Any]) -> dict[str, Any]:
        parsed = AutoShortVideoInput(
            **{k: v for k, v in inputs.items() if k in AutoShortVideoInput.model_fields}
        )

        user_id = inputs.get("_user_id", "default")

        degradation_notes: list[str] = []

        workflow_id = inputs.get("_workflow_id")
        _turn_id = inputs.get("_turn_id") or ""

        async def _emit_step(step: int, total: int, label: str, detail: str) -> None:
            if not workflow_id:
                return None
            from app.services.sse_bus import sse_bus

            pct = int(step / total * 100)
            await sse_bus.publish(
                workflow_id,
                "video_pipeline_progress",
                {
                    "skill": "auto_short_video",
                    "step": step,
                    "total": total,
                    "label": label,
                    "detail": detail,
                    "percent": pct,
                },
                turn_id=_turn_id or None,
            )

        size = _SIZE_MAP.get(parsed.ratio, "1080x1920")
        # 产物隔离：中间产物（分镜 json / 成片 / 配音）按「用户 + 本次运行」分目录。
        # 此前用 _OUTPUT_DIR/<topic>/，同用户两个会话做同主题会互相覆盖中间文件，
        # 并发时甚至会读到对方刚写了一半的 storyboard。workflow_id 已能唯一标识一次
        # 创作任务，缺失时退化为随机 id。
        _run_dir = _OUTPUT_DIR / user_id / "auto_short_video" / (workflow_id or uuid.uuid4().hex[:16])
        output_dir = _run_dir / "assets"
        output_dir.mkdir(parents=True, exist_ok=True)

        async def _publish_preview_card(title: str, preview_shots: list[dict]) -> None:
            """把分镜草稿推给作品面板，用户在别处也能看到待确认的那一版。"""
            if not workflow_id:
                return
            from app.services.sse_bus import sse_bus

            await sse_bus.publish(workflow_id, "card_draft_ready", {
                "card_draft": {
                    "title": f"分镜预览 - {title}",
                    "template": "storyboard_preview",
                    "pages": [{
                        "id": f"shot_{i + 1}",
                        "type": "content",
                        "mediaType": "image",
                        "title": s.get("caption", f"第{i + 1}镜"),
                        "imagePrompt": s.get("image_prompt", ""),
                        "motionPrompt": s.get("motion", parsed.image_motion),
                        "duration": s.get("duration", 4.0),
                        "transition": "cut",
                    } for i, s in enumerate(preview_shots)],
                    "pngUrls": [],
                    "htmlUrls": [],
                },
                "png_urls": [],
                "html_urls": [],
            }, turn_id=_turn_id or None)

        def _build_preview(title: str, preview_shots: list[dict], script_extra: dict) -> dict[str, Any]:
            est = sum(float(s.get("duration") or 0) for s in preview_shots) or parsed.target_duration
            return {
                "mode": "preview",
                "title": title,
                "shots": preview_shots,
                "hook": script_extra.get("hook", ""),
                "closing": script_extra.get("closing", ""),
                "total_shots": len(preview_shots),
                "estimated_duration": round(est, 1),
                "needs_confirmation": True,
                "confirm_hint": (
                    "分镜草稿已生成，视频尚未生成。请把草稿展示给用户并等待确认或修改，"
                    "再把最终 shots 通过 refine_shots 参数回传（标题可用 refine_title 一起带回）。"
                    "未经用户确认不要自行生成。"
                ),
                "hint": "编辑 shots 中任意镜的 caption/image_prompt/duration 后，"
                        "通过 refine_shots 参数传回即可生成视频。也可增删镜头。",
            }

        # ── 分支：用户已确认（refine） vs 首次编排 ──
        if parsed.refine_shots is not None:
            shots = [_normalize_shot(s) for s in parsed.refine_shots]
            if not shots:
                return {"error": "refine_shots 为空，无镜头可生成"}
            title = parsed.refine_title or parsed.topic
            script = {"title": title, "shots": shots}
            degradation_notes.append("微调模式：跳过 LLM 重编脚本，使用用户已确认的分镜")
            logger.info(f"[refine] 已确认分镜 {len(shots)} 镜，从 Step 2 开始")
        else:
            logger.info(f"[1/4] 生成脚本分镜：{parsed.topic}")
            await _emit_step(1, 4, "生成脚本分镜", parsed.topic)
            script = await _generate_script(
                topic=parsed.topic,
                duration=parsed.target_duration,
                style=parsed.style,
                ratio=parsed.ratio,
            )
            shots = [_normalize_shot(s) for s in script.get("shots", [])]
            script["shots"] = shots
            title = script.get("title", parsed.topic)

            if not shots:
                return {"error": "分镜脚本为空，无法生成视频"}

            # ── 确认闸门（2026-10-17）：未确认前不消耗任何付费 API ──
            # 与 multi_image_video 同一条硬约束：refine_shots 是"用户已确认"的唯一信号。
            logger.info("[gate] 未提供 refine_shots，返回分镜草稿等待用户确认")
            await _emit_step(1, 4, "等待用户确认", "分镜草稿已生成，确认后再生成视频")
            await _publish_preview_card(title, shots)
            return _build_preview(title, shots, script)

        logger.info(f"[2/4] 生成配图（{len(shots)} 镜）")
        await _emit_step(2, 4, "生成配图", f"{len(shots)} 镜")
        shots = await _generate_shot_images(shots, parsed.ratio, user_id)
        has_images = any(s.get("image") for s in shots)
        if not has_images:
            degradation_notes.append(
                "配图：未生成任何图片（API key 未配置或全部失败），将用纯色占位"
            )

        narration_path = None
        if parsed.need_narration:
            logger.info("[3/4] 生成配音")
            await _emit_step(3, 4, "生成配音", "TTS 合成口播（edge-tts → piper 降级）")
            narration_path, narration_source = await _generate_narration(
                script, user_id, output_dir, run_id=workflow_id or "",
            )
            if not narration_path:
                degradation_notes.append("配音：edge-tts 与 piper 均不可用，跳过配音")
            elif narration_source == "edge-tts":
                degradation_notes.append("配音：edge-tts 在线合成")
            else:
                degradation_notes.append(f"配音：edge-tts 不可用，降级 {narration_source} 离线合成")
        else:
            degradation_notes.append("配音：用户未要求")

        logger.info("[4/4] 合成成片")
        await _emit_step(4, 4, "合成成片", "ffmpeg 确定性合成")

        storyboard = {
            "size": size,
            "image_motion": parsed.image_motion,
            "shots": shots,
        }
        if narration_path:
            storyboard["narration"] = narration_path

        storyboard_path = output_dir / "storyboard.json"
        storyboard_path.write_text(
            json.dumps(storyboard, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        final_path = _run_dir / "final.mp4"

        try:
            from app.services.media_capability import video_compose

            result = await video_compose(
                storyboard=storyboard,
                output_path=str(final_path),
                pad_mode="auto",
            )
            # local 分支 compose_video 返回 Path；remote 分支（HTTP）返回 dict
            result_path = (
                result if isinstance(result, Path)
                else Path(result.get("output_path", str(final_path)))
            )
            video_bytes = result_path.read_bytes()

            from app.services.asset_library import save_video_asset

            asset = await save_video_asset(
                user_id=user_id,
                filename=f"{parsed.topic[:20]}_final.mp4",
                content_type="video/mp4",
                data=video_bytes,
                source="ai",
                metadata={
                    "source": "auto_short_video",
                    "topic": parsed.topic,
                    "ratio": parsed.ratio,
                    "target_duration": parsed.target_duration,
                    "style": parsed.style,
                    "shots_count": len(shots),
                    "has_narration": bool(narration_path),
                    "degradation": degradation_notes,
                },
            )
            result = {
                "success": True,
                "asset_id": asset["id"],
                "url": asset["original_url"],
                "file_size": asset["file_size"],
                "title": script.get("title", parsed.topic),
                "shots_count": len(shots),
                "has_narration": bool(narration_path),
                "degradation": degradation_notes,
                "storyboard_path": str(storyboard_path),
            }

            # Step 4.5：管线内嵌叠加增强（文档 3.7 集成方式 3）
            # 降级契约：overlay 任何失败 → note + 原成片照常返回
            if parsed.enhance_overlay:
                await _emit_step(5, 5, "叠加增强", "talking_head_overlay（两步链+视觉自审）")
                # 已知原文时间轴 → overlay 跳过 whisper（根除合成音频误听）
                transcript: list[dict[str, Any]] = []
                try:
                    from capabilities.video.adapters.silence_detect import probe_duration
                    transcript = _build_transcript(
                        shots, await probe_duration(str(result_path)),
                    )
                except Exception as e:
                    logger.warning(f"transcript 构造失败，回退 whisper 转录：{e}")
                enhanced = await self._enhance_with_overlay(
                    str(result_path), parsed, user_id, transcript=transcript or None,
                    workflow_id=workflow_id or "",
                )
                if enhanced:
                    result["enhanced_asset_id"] = enhanced["id"]
                    result["enhanced_url"] = enhanced["original_url"]
                else:
                    result["degradation"] = list(result["degradation"]) + [
                        "叠加增强：talking_head_overlay 不可用或失败，已降级返回原成片"
                    ]
            return result
        except Exception as e:
            error_type = getattr(e, "error_type", "compose_failed")
            logger.error(f"视频合成失败：{error_type} - {e}")
            return {
                "success": False,
                "error": str(e),
                "error_type": error_type,
                "degradation": degradation_notes,
                "storyboard_path": str(storyboard_path),
            }