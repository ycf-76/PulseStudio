"""多图参考视频生成流水线 Skill：多张参考图 + 描述 → 逐镜头编排 → 分镜关键帧 → image2video → 拼接成片。

设计依据：
  - 小加同学"手持跟拍日常vlog"技能的 3 阶段流水线：
    LLM 看图编排 → text2image 分镜关键帧 → image2video 逐镜生成 → 拼接
  - 与 auto_short_video 同构，但核心差异：
    1. 输入含多张参考图（非纯文字主题）
    2. Step 1 用 VL 理解参考图语义
    3. Step 4 用 image2video 真视频（非 ken-burns 图片缓动）
    4. Step 5 用 video_concat 拼接视频片段（非图片序列）

编排流程（6 步）：
1. VL 理解参考图：QwenVL 多图 → 场景/角色/道具/色彩结构化描述
2. LLM 编排分镜脚本：场景描述 + 用户 prompt → 逐镜头脚本
   ── 闸门：此处一律停下，把分镜草稿返回给用户确认（见下方「确认闸门」）
3. text2image 生成分镜关键帧：逐镜 image_prompt → WanxAdapter.generate()
4. image2video 逐镜生视频：关键帧 + motion_prompt → DashScopeVideoAdapter.image2video()
5. ffmpeg 拼接成片：视频片段 xfade concat → final.mp4
6. 存 asset_library + 发 SSE card_draft_ready

确认闸门（2026-10-17 接入，源自小加同学 3 阶段流水线的要求）：
  用户的需求形态是「VL 看图 → 生成分镜脚本 → 用户确认脚本没问题 → 再生成视频」。
  `refine_shots`（用户确认/修改后回传的分镜列表）是"已确认"的唯一信号。
  - refine_shots 非空 → 走 Step 3-6 生成
  - preview_only=True → 只走 VL+LLM，返回草稿
  - 两者都缺 → 同样只返回草稿，并带 needs_confirmation=True
  任何调用方（ReAct Agent / 直调脚本 / REST）都不可能跳过确认直接出片。

降级策略（零件可缺）：
  - VL 不可用 → 跳过 Step 1，直接用 prompt 编排
  - LLM 不可用 → 默认分镜（每图一镜 + prompt）
  - text2image 失败 → 用参考图原片作为关键帧
  - image2video 失败 → 该镜退化为 ken-burns 图片缓动
  - compose 失败 → 返回各片段 URL 列表
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.core.skill_base import Skill
from app.core.skill_registry import register

# 网格拼图检测（可选能力）。在**模块级**导入而不是用到时再 import：
# grid_detect 会在模块级加载 numpy/Pillow，若等到 _expand_grid_images 里再导，
# 就可能落在某个 patch.dict(sys.modules) 上下文内 —— 补丁退出时 clear() 还原
# 会把 numpy 从 sys.modules 抹掉，后续 import 触发 C 扩展二次加载直接报错。
# 导入失败（环境缺 numpy/Pillow）时降级为不可用，绝不让本技能整个起不来。
try:
    from app.services import grid_detect as _grid_detect
except Exception as _e:  # pragma: no cover - 缺依赖时的降级分支
    _grid_detect = None
    logging.getLogger(__name__).warning(
        f"[multi_image_video] 网格拼图检测不可用（缺 numpy/Pillow）：{_e}"
    )

logger = logging.getLogger(__name__)

_OUTPUT_DIR = Path(__file__).resolve().parent.parent.parent.parent / "outputs"


class MultiImageVideoInput(BaseModel):
    image_asset_ids: list[str] = Field(
        default_factory=list,
        max_length=10,
        description="参考图 asset_id 列表（1-10 张）。"
                    "refine_shots 模式（用户已确认分镜）下可省略，由系统沿用首次那份",
    )
    prompt: str = Field(
        min_length=5,
        max_length=2000,
        description="视频画面描述（镜头运动、角色动作、场景转换等）",
    )
    target_duration: float = Field(
        default=15.0,
        ge=5.0,
        le=60.0,
        description="目标时长（秒），5-60s",
    )
    ratio: Literal["9:16", "16:9", "1:1"] = Field(
        default="9:16",
        description="画幅比：9:16 竖屏 / 16:9 横屏 / 1:1 方形",
    )
    style: str = Field(
        default="电影感",
        description="风格：电影感/纪实/动画/广告",
    )
    shot_duration: float = Field(
        default=5.0,
        ge=2.0,
        le=10.0,
        description="每镜头时长（秒），2-10s",
    )
    provider: Literal["dashscope", "kling", "ark"] | None = Field(
        default=None,
        description="视频生成 provider（None 时用系统默认）",
    )
    preview_only: bool = Field(
        default=False,
        description="仅预览模式：只执行 VL+LLM 编排，返回分镜草稿供用户编辑，不生成视频",
    )
    refine_shots: list[dict[str, Any]] | None = Field(
        default=None,
        description="微调模式：传入用户确认（或编辑）后的分镜列表，"
                    "这是触发视频生成的唯一入口，缺省时一律只返回分镜草稿",
    )
    refine_image_asset_ids: list[str] | None = Field(
        default=None,
        description="微调模式的参考图 ID（若与首次不同则更新）",
    )
    refine_title: str | None = Field(
        default=None,
        description="微调模式的视频标题。refine 会跳过 VL+LLM，"
                    "靠它保留用户确认时看到的那个标题（不给则退回 prompt 前 30 字）",
    )

    @model_validator(mode="after")
    def _require_images_unless_refining(self) -> "MultiImageVideoInput":
        """首次编排必须有参考图；refine_shots（用户已确认分镜）模式可省略。"""
        if self.refine_shots is None and not self.image_asset_ids:
            raise ValueError(
                "image_asset_ids 至少需要 1 张；仅 refine_shots 模式下可省略"
            )
        return self


# ── W3a（2026-10-16）：编排逻辑已迁至 app/services/video_orchestrator.py ──
# VL 理解 / 分镜编排 / 参考图解析现在是可被 REST 接口（W4）与 ReAct 入口
# 双向调用的纯 service。以下同名再导出保持旧调用点与既有测试的
# patch 靶点（capabilities.video.skills.multi_image_video.*）不变。
from capabilities.video.services.video_orchestrator import (  # noqa: E402
    _STORYBOARD_PROMPT,
    _VL_SCENE_PROMPT,
    generate_storyboard as _generate_storyboard,
    resolve_reference_images as _resolve_image_urls,
    vl_understand_images as _vl_understand_images,
)



async def _generate_shot_frames(
    shots: list[dict[str, Any]],
    ratio: str,
    user_id: str,
    image_urls: list[str],
    character_lock: str = "",
) -> list[dict[str, Any]]:
    """Step 3: text2image 生成分镜关键帧。

    角色锚定注入（2026-10-18）：
      - character_lock 非空时，自动注入到每个 shot 的 image_prompt 开头
      - 确保所有镜头基于统一的角色描述生成，解决"每镜换脸"问题
      - 注入格式："{character_lock}, {原prompt}"

    降级：生图失败时用参考图原片作为关键帧。
    """
    from app.config import get_settings

    settings = get_settings()
    api_key = (settings.dashscope_api_key or "").strip()
    if not api_key:
        logger.info("[multi_image_video] 未配置图像 API key，用参考图作为关键帧")
        for i, shot in enumerate(shots):
            ref_idx = min(i, len(image_urls) - 1)
            shot["frame_url"] = image_urls[ref_idx]
            shot["frame_source"] = "reference"
        return shots

    try:
        from capabilities.image_gen.adapters.image_gen import WanxAdapter
        from app.services.model_tier import PipelineStage, get_tier_router
        from app.services.asset_library import save_asset

        router = get_tier_router()
        t2i_model = router.resolve(PipelineStage.TEXT2IMAGE, "t2i")
        logger.info(f"[multi_image_video] 文生图使用分级模型: {t2i_model}")
        adapter = WanxAdapter(api_key=api_key, model=t2i_model)
        size_map = {"9:16": "720x1280", "16:9": "1280x720", "1:1": "1024x1024"}
        size = size_map.get(ratio, "720x1280")
        max_concurrent = settings.multi_image_video_max_concurrent_frames

        sem = asyncio.Semaphore(max_concurrent)

        async def _gen_one(shot: dict, idx: int) -> None:
            prompt_text = shot.get("image_prompt", "")
            if not prompt_text:
                ref_idx = min(idx, len(image_urls) - 1)
                shot["frame_url"] = image_urls[ref_idx]
                shot["frame_source"] = "reference"
                return

            # 角色锚定注入（2026-10-18）：确保所有镜头基于统一角色描述
            original_prompt = prompt_text
            if character_lock and character_lock.strip():
                if not prompt_text.lower().startswith(character_lock.lower()[:50]):
                    prompt_text = f"{character_lock}, {prompt_text}"
                    logger.info(
                        f"[multi_image_video] 镜头{idx+1} 角色锚定已注入 "
                        f"(character_lock长度={len(character_lock)}, "
                        f"原prompt长度={len(original_prompt)}, "
                        f"新prompt长度={len(prompt_text)})"
                    )
                else:
                    logger.info(f"[multi_image_video] 镜头{idx+1} 已包含角色锚定前缀，跳过注入")

            async with sem:
                try:
                    img_bytes = await adapter.generate(prompt=prompt_text, size=size, count=1)
                    if img_bytes:
                        asset = await save_asset(
                            user_id=user_id,
                            filename=f"frame_{idx:03d}.png",
                            content_type="image/png",
                            data=img_bytes[0] if isinstance(img_bytes, list) else img_bytes,
                            source="ai",
                        )
                        shot["frame_url"] = asset["original_url"]
                        shot["frame_asset_id"] = asset["id"]
                        shot["frame_source"] = "ai_generated"
                        logger.info(f"[multi_image_video] 关键帧 {idx + 1} 生成成功")
                        return
                except Exception as e:
                    logger.warning(f"[multi_image_video] 关键帧 {idx + 1} 生成失败：{e}")

            ref_idx = min(idx, len(image_urls) - 1)
            shot["frame_url"] = image_urls[ref_idx]
            shot["frame_source"] = "reference_fallback"

        await asyncio.gather(*[_gen_one(shot, i) for i, shot in enumerate(shots)])
        return shots

    except Exception as e:
        logger.warning(f"[multi_image_video] 图像适配器初始化失败，用参考图：{e}")
        for i, shot in enumerate(shots):
            ref_idx = min(i, len(image_urls) - 1)
            shot["frame_url"] = image_urls[ref_idx]
            shot["frame_source"] = "reference_fallback"
        return shots


def _resolve_i2v_model(provider_name: str) -> str:
    """按 provider 解析图生视频模型名（P1-1 修复，2026-10-16）。

    分级路由器解析出的是 dashscope 系模型名（wanx2.1-i2v-*），只对
    dashscope 适配器有效。旧代码无论哪个 provider 都透传该模型名，
    kling/ark 会直接收到不存在的模型 → 400 / Model not exist，
    表现为"某镜 image2video 失败"实则全程不可用。

    现在：
      - dashscope → 走分级路由（economy/quality 档位可调）；
      - 其他 provider → 返回空串，由 adapter.image2video(model=None)
        回落自身默认模型（_resolve_adapter 已按 provider 注入 settings 默认值）。
    """
    if provider_name == "dashscope":
        from app.services.model_tier import PipelineStage, get_tier_router

        return get_tier_router().resolve(PipelineStage.IMAGE2VIDEO, "i2v")
    return ""


async def _generate_shot_clips(
    shots: list[dict[str, Any]],
    ratio: str,
    shot_duration: float,
    user_id: str,
    provider: str | None = None,
    on_progress: Any = None,
    total_timeout: float | None = None,
) -> list[dict[str, Any]]:
    """Step 4: image2video 逐镜生成视频片段。

    首尾帧连贯策略（Seedance i2v 独有）：
      - provider=ark 时，下一镜的首帧 = 上一镜的关键帧（frame_url）
      - 即 shot[i+1] 的 last_frame_url = shot[i].frame_url
      - 这样 Seedance 会以首尾帧约束生成，跨镜视觉连贯

    降级：某镜 image2video 失败时标记为 ken-burns 退回。
    串行执行（避免同时占多个 API 配额）。

    总体超时（P1-14 修复，2026-10-13）：total_timeout>0 时在镜头边界
    检查 deadline，超时后剩余镜头全部标 ken_burns 兜底——旧版 3 镜
    最坏 15 分钟无任何中断机制，用户侧表现为永久转圈。
    注意单镜内部轮询上限另有 300s，最坏耗时约为 total_timeout + 300s。
    """
    import time as _time

    from capabilities.video.skills.video_gen import _resolve_adapter, VideoGenError

    try:
        adapter, provider_name = _resolve_adapter(provider)
    except VideoGenError as e:
        logger.warning(f"[multi_image_video] 视频适配器不可用：{e.message}")
        for shot in shots:
            shot["clip_mode"] = "ken_burns"
            shot["clip_error"] = e.message
        return shots

    # P1-1 修复（2026-10-16）：模型名按 provider 解析，不再跨 provider
    # 透传 dashscope 模型名（详见 _resolve_i2v_model docstring）。
    i2v_model = _resolve_i2v_model(provider_name)
    logger.info(
        f"[multi_image_video] 图生视频模型: {i2v_model or '(provider 默认)'} "
        f"(provider={provider_name})"
    )

    deadline = _time.monotonic() + total_timeout if total_timeout and total_timeout > 0 else None
    if deadline:
        logger.info(f"[multi_image_video] 逐镜生成总体超时预算：{total_timeout:.0f}s")

    use_last_frame = provider_name == "ark"
    if use_last_frame:
        logger.info("[multi_image_video] Seedance 首尾帧连贯模式：启用")

    from app.services.asset_library import save_video_asset

    for i, shot in enumerate(shots):
        # P1-14：镜头边界检查总体超时。超时后剩余镜头全部 ken-burns 兜底，
        # 保证流程能走到拼接出片，而不是无限卡在轮询上。
        if deadline is not None and _time.monotonic() > deadline:
            remaining = len(shots) - i
            logger.warning(
                f"[multi_image_video] 总体超时 {total_timeout:.0f}s 已到，"
                f"剩余 {remaining} 镜退化为 ken-burns"
            )
            for s in shots[i:]:
                s["clip_mode"] = "ken_burns"
                s["clip_error"] = f"总体超时熔断（预算 {total_timeout:.0f}s）"
            break

        frame_url = shot.get("frame_url", "")
        if not frame_url:
            shot["clip_mode"] = "ken_burns"
            shot["clip_error"] = "无关键帧"
            continue

        from app.services.asset_library import url_to_base64, url_to_local_path

        if frame_url.startswith("http") or frame_url.startswith("data:"):
            full_url = frame_url
        else:
            full_url = url_to_base64(frame_url)
            if not full_url:
                local = url_to_local_path(frame_url)
                if local:
                    full_url = url_to_base64(local)
            if not full_url:
                shot["clip_mode"] = "ken_burns"
                shot["clip_error"] = f"关键帧无法读取：{frame_url[:80]}"
                continue
        motion_prompt = shot.get("motion_prompt", "")

        last_frame_url = None
        if use_last_frame and i > 0:
            prev_frame = shots[i - 1].get("frame_url", "")
            if prev_frame:
                if prev_frame.startswith("http") or prev_frame.startswith("data:"):
                    last_frame_url = prev_frame
                else:
                    last_frame_url = url_to_base64(prev_frame)
                    if not last_frame_url:
                        local = url_to_local_path(prev_frame)
                        if local:
                            last_frame_url = url_to_base64(local)

        try:
            video_bytes = await adapter.image2video(
                image=full_url,
                prompt=motion_prompt,
                ratio=ratio,
                duration=shot_duration,
                model=i2v_model or None,
                last_frame_url=last_frame_url,
                on_progress=on_progress,
            )

            if video_bytes:
                clip_asset = await save_video_asset(
                    user_id=user_id,
                    filename=f"clip_{i:03d}.mp4",
                    content_type="video/mp4",
                    data=video_bytes,
                    source="ai",
                    metadata={
                        "source": "multi_image_video",
                        "shot_index": i + 1,
                        "motion_prompt": motion_prompt,
                        "last_frame_used": bool(last_frame_url),
                    },
                )
                shot["clip_url"] = clip_asset["original_url"]
                shot["clip_asset_id"] = clip_asset["asset_id"]
                shot["clip_mode"] = "video"
                if last_frame_url:
                    shot["clip_coherence"] = "first_last_frame"
                logger.info(f"[multi_image_video] 第 {i + 1}/{len(shots)} 镜视频生成成功")
                continue

        except Exception as e:
            logger.warning(f"[multi_image_video] 第 {i + 1} 镜 image2video 失败：{e}")

        shot["clip_mode"] = "ken_burns"
        shot["clip_error"] = "image2video 失败"

    return shots


def _log_compose_failure(stage: str, exc: BaseException, **ctx: Any) -> None:
    """把拼接失败的真因落到独立诊断日志。

    D2 修复（2026-10-17）：本项目 app/ 下没有任何 logging 文件配置
    （无 basicConfig / FileHandler / dictConfig），logger 只走 stdout，
    链路一失败 backend/logs 一片空白，事后完全无法定性。这里开一条
    独立落盘通道，只写失败、不接全局 logging，零副作用。
    """
    try:
        import datetime as _dt

        log_dir = Path(__file__).resolve().parent.parent.parent.parent / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        ts = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(log_dir / "video_pipeline.log", "a", encoding="utf-8") as f:
            f.write(f"[{ts}] stage={stage} error={type(exc).__name__}: {exc}\n")
            for k, v in ctx.items():
                f.write(f"    {k}={v}\n")
    except Exception:
        pass


def _resolve_clip_urls(
    video_clips: list[dict[str, Any]],
) -> tuple[list[str], list[str], list[str]]:
    """把 shot 的 clip_url 解析成可喂 ffmpeg 的路径。

    D3 修复：旧代码在 concat 主路径与 simple 降级路径各写了一遍解析逻辑，
    两处已经出现行为分叉（降级路径丢了 transition）。这里统一一处。

    返回 (clip_urls, transitions, 跳过原因)。
    """
    from app.services.asset_library import url_to_local_path

    clip_urls: list[str] = []
    transitions: list[str] = []
    skipped: list[str] = []
    for s in video_clips:
        url = s["clip_url"]
        local = url_to_local_path(url)
        if local:
            clip_urls.append(local)
        elif url.startswith("http"):
            clip_urls.append(url)
        else:
            skipped.append(f"视频片段无法解析本地路径，跳过：{url[:60]}")
            continue
        transitions.append(s.get("transition", "cut"))
    return clip_urls, transitions, skipped


async def _compose_kenburns(
    shots: list[dict[str, Any]],
    ratio: str,
    user_id: str,
    title: str,
    degradation: list[str],
) -> tuple[bytes | None, list[str]]:
    """图片 ken-burns 合成（兜底路径）。

    D1 修复（2026-10-17）：旧代码只在「无视频片段」时才走 ken-burns，
    concat 失败后直接 return None —— 手里明明有 2-3 个可用关键帧，
    成片却归零。现在 concat 主路径与降级路径都失败后同样回退到这里。

    注意取的是全部 shots 的 frame_url（不只 ken_burns 镜），这样
    即使有 1 条视频片段也能和其余关键帧拼出完整时长。
    """
    from app.services.asset_library import url_to_local_path

    try:
        size_map = {"9:16": "1080x1920", "16:9": "1920x1080", "1:1": "1080x1080"}
        kb_shots: list[dict[str, Any]] = []
        for s in shots:
            frame_url = s.get("frame_url", "")
            if not frame_url:
                continue
            local = url_to_local_path(frame_url)
            if not local:
                degradation.append(f"关键帧无法转本地路径，跳过：{frame_url[:60]}")
                continue
            kb_shots.append({
                "caption": s.get("caption", ""),
                "image": local,
                "duration": s.get("duration", 5.0),
                "motion": "ken-burns",
            })
        if not kb_shots:
            degradation.append("无可用的本地关键帧，ken-burns 合成取消")
            return None, degradation

        storyboard = {
            "size": size_map.get(ratio, "1080x1920"),
            "image_motion": "ken-burns",
            "shots": kb_shots,
        }
        from capabilities.video.adapters.video_compose import compose_video

        output_dir = _OUTPUT_DIR / user_id / "multi_image_video"
        output_dir.mkdir(parents=True, exist_ok=True)
        # 产物隔离：兜底片同样带镜头指纹，防止同标题的不同作品互相覆盖
        _kb_key = hashlib.md5(
            "|".join(
                f"{s.get('image', '')}@{s.get('duration', 5.0)}" for s in kb_shots
            ).encode("utf-8")
        ).hexdigest()[:8]
        final_path = output_dir / f"{title[:20]}_{_kb_key}_kenburns.mp4"
        result_path = await compose_video(storyboard=storyboard, output_path=str(final_path), pad_mode="auto")
        degradation.append(f"已回退 ken-burns 图片合成（{len(kb_shots)} 镜）")
        return result_path.read_bytes(), degradation
    except Exception as e:
        logger.exception("[multi_image_video] ken-burns 兜底合成失败")
        _log_compose_failure("kenburns", e, user_id=user_id, shots=len(shots))
        degradation.append(f"ken-burns 合成也失败：{e}")
        return None, degradation


async def _expand_grid_images(
    image_urls: list[str],
    image_local_paths: list[str],
    user_id: str,
    degradation_notes: list[str],
) -> tuple[list[str], list[str]]:
    """把参考图里的九宫格拼图拆成单图，返回展开后的 (urls, local_paths)。

    为什么必须拆：拼图一个画面塞了 9 个场景、多个人物分身，整张喂给
    image2video 会被当成**单一动态画面**，9 格各自独立扭动 → "人物变形"。
    这是 2026-10-17 成片质量问题最上游的根因。

    为什么这里只拆"真拼图"：把单图误判成拼图并切开，会得到「半张脸 + 大片空白」
    的残片，比不拆更糟（那正是上一次事故的做法）。检测器已在 7 张真实素材上
    与人工 ground truth 逐张比对通过（只有 1 张真 3x3）。

    顺序稳定：同一批输入每次展开都得到同样的顺序，
    这样 refine 模式里用户 shots 的 reference_images 下标仍然对得上。
    """
    from app.services.grid_capability import detect_grid as _cap_detect
    from app.services.grid_capability import split_grid as _cap_split

    # 三期：grid 能力走访问层（local/remote 按配置切换，见 grid_capability）
    if _cap_detect is None or _cap_split is None:
        return list(image_urls), list(image_local_paths)

    async def _detect(image_path):
        try:
            return await _cap_detect(image_path)
        except Exception as e:
            logger.warning(f"[grid] 检测失败，按单图处理：{e}")
            return None

    out_urls: list[str] = []
    out_locals: list[str] = []
    expanded_any = False
    # 检测本身做不了（缺依赖 / 读不了图）时不能静默——否则拼图会原样进 image2video，
    # 又回到"人物变形"那个观感，而日志里看不出为什么没拆
    unchecked: list[str] = []

    for url, local in zip(image_urls, image_local_paths):
        if not local:
            out_urls.append(url)
            out_locals.append(local)
            continue

        try:
            det = await _detect(local)
        except Exception as e:
            logger.warning(f"[grid] 检测失败，按单图处理 {local}：{e}")
            unchecked.append(Path(local).name)
            out_urls.append(url)
            out_locals.append(local)
            continue

        if det is None or det.reason.startswith("依赖缺失"):
            unchecked.append(Path(local).name)
        if det is None or not det.is_grid:
            out_urls.append(url)
            out_locals.append(local)
            continue

        try:
            from app.services.asset_library import save_asset, url_to_local_path

            # 产物隔离：切图中间目录按「源图路径指纹」命名。只用文件名 stem 时，
            # 两个作品里同名的图（如 img_0）会落到同一目录，后一次切图覆盖前一次的瓦片。
            _tile_key = hashlib.md5(str(Path(local)).encode("utf-8")).hexdigest()[:12]
            tile_dir = _OUTPUT_DIR / user_id / "_grid_tiles" / _tile_key
            tiles = await _cap_split(local, tile_dir, det)
            if not tiles:
                raise RuntimeError("拆分结果为空")

            for t in tiles:
                asset = await save_asset(
                    user_id=user_id,
                    filename=t.name,
                    content_type="image/png",
                    data=t.read_bytes(),
                )
                out_urls.append(asset["original_url"])
                out_locals.append(url_to_local_path(asset["original_url"]) or str(t))

            expanded_any = True
            logger.info(f"[grid] {Path(local).name} 判定为 {det.rows}x{det.cols} 拼图，已拆成 {len(tiles)} 张")
        except Exception as e:
            # 拆不动就退回原图：素材绝不能因为拆分失败而丢失
            logger.warning(f"[grid] 拆分失败，保留原图 {local}：{e}")
            degradation_notes.append(f"拼图拆分失败，已保留原图：{Path(local).name}")
            out_urls.append(url)
            out_locals.append(local)

    if expanded_any:
        degradation_notes.append(
            f"拼图已拆分：参考图 {len(image_urls)} 张 → {len(out_urls)} 张（避免整张拼图被当成单一画面）"
        )
    if unchecked:
        degradation_notes.append(
            f"拼图检测未覆盖 {len(unchecked)} 张（{', '.join(unchecked[:3])}）："
            "这些图整张使用，若为九宫格可能出现画面内多场景同时晃动"
        )
    return out_urls, out_locals


async def _render_kenburns_clips(
    shots: list[dict[str, Any]],
    ratio: str,
    user_id: str,
    size_map: dict[str, str],
) -> dict[int, str]:
    """把 ken-burns 镜头逐段渲染成 mp4 片段，返回 {镜头下标: 片段路径}。

    D5 修复（2026-10-17）：实测成片只有 5 秒——6 镜里仅 1 镜出真视频，
    `_compose_final` 见只有一条视频片段就走单片段短路直接输出，其余 5 个
    镜头（含它们的关键帧）根本没进成片。这里让每个 ken-burns 镜头先各自
    产出一段轻量 mp4（本地 ffmpeg，不花钱），再和真视频片段一起 concat，
    保证成片时长覆盖全部镜头。

    纯本地 ffmpeg，无外部依赖；已有片段复用，重跑不重复渲染。
    """
    from capabilities.video.adapters.video_compose import compose_video
    from app.services.asset_library import url_to_local_path

    out_dir = _OUTPUT_DIR / user_id / "multi_image_video" / "_kb_clips"
    out_dir.mkdir(parents=True, exist_ok=True)

    result: dict[int, str] = {}
    for idx, s in enumerate(shots):
        # 只处理 ken-burns 镜头；下标用原始 shots 的下标，调用方据此定位
        if s.get("clip_mode") == "video" and s.get("clip_url"):
            continue
        frame_url = s.get("frame_url", "")
        if not frame_url:
            continue
        local = url_to_local_path(frame_url)
        if not local:
            continue
        # 产物隔离：缓存键带上「这一镜的关键帧」，不能只按镜号。
        # 此前同用户两个会话做不同视频时，镜号相同就命中对方留下的片段，
        # 成片里混进别的作品的画面。同帧重跑仍能命中缓存，隔离与复用兼得。
        _key = hashlib.md5(
            f"{frame_url}|{s.get('duration', 5.0)}".encode("utf-8")
        ).hexdigest()[:8]
        out = out_dir / f"kb_{idx:03d}_{_key}.mp4"
        if not out.exists() or out.stat().st_size == 0:
            # caption 必须留空：compose_video 见 caption 就会自动烧一层字幕，
            # 而这里产出的是**中间片段**，后面还要进成片。留空则字幕只在
            # _compose_final 里统一烧一次，避免"字幕叠字幕"（两行错位）。
            storyboard = {
                "size": size_map.get(ratio, "1080x1920"),
                "image_motion": "ken-burns",
                "shots": [{
                    "caption": "",
                    "image": local,
                    "duration": s.get("duration", 5.0),
                    "motion": "ken-burns",
                }],
            }
            await compose_video(
                storyboard=storyboard, output_path=str(out), pad_mode="auto"
            )
        if out.exists() and out.stat().st_size > 0:
            result[idx] = str(out)
    return result


async def _compose_final(
    shots: list[dict[str, Any]],
    ratio: str,
    user_id: str,
    title: str,
) -> tuple[bytes | None, list[str]]:
    """Step 5: 拼接成片。

    优先用 video_concat 拼接视频片段；无片段或拼接失败一律回退
    图片 ken-burns 合成，避免手里有关键帧却成片归零。
    返回 (final_bytes, degradation_notes)。
    """
    degradation: list[str] = []

    video_clips = [s for s in shots if s.get("clip_mode") == "video" and s.get("clip_url")]
    ken_burns_shots = [s for s in shots if s.get("clip_mode") != "video"]

    if ken_burns_shots:
        degradation.append(f"{len(ken_burns_shots)} 镜退化为 ken-burns 图片缓动")

    if not video_clips:
        degradation.append("无视频片段，尝试图片 ken-burns 合成")
        return await _compose_kenburns(shots, ratio, user_id, title, degradation)

    # D5：先把 ken-burns 镜头渲染成片段，让它们参与 concat，而不是被丢弃
    size_map = {"9:16": "1080x1920", "16:9": "1920x1080", "1:1": "1080x1080"}
    kb_clip_by_idx: dict[int, str] = {}
    if ken_burns_shots:
        try:
            kb_clip_by_idx = await _render_kenburns_clips(shots, ratio, user_id, size_map)
            degradation.append(
                f"{len(kb_clip_by_idx)} 个 ken-burns 镜头已转为视频片段参与拼接"
            )
        except Exception as e:
            logger.warning(f"[multi_image_video] ken-burns 镜头转片段失败：{e}")
            _log_compose_failure("kenburns_clips", e, user_id=user_id, shots=len(shots))
            degradation.append(f"ken-burns 镜头转片段失败：{e}")

    if not video_clips and not kb_clip_by_idx:
        degradation.append("无视频片段，尝试图片 ken-burns 合成")
        return await _compose_kenburns(shots, ratio, user_id, title, degradation)

    # 按原始镜头顺序组装全部片段，保证成片时长覆盖每一个镜头
    from app.services.asset_library import url_to_local_path

    clip_urls: list[str] = []
    transitions: list[str] = []
    # 只记录**真正进了成片**的镜头：被跳过的镜头不能占字幕时间轴，
    # 否则后面的字幕会整体提前（越到片尾偏移越大）
    used_shots: list[dict[str, Any]] = []
    for idx, s in enumerate(shots):
        if s.get("clip_mode") == "video" and s.get("clip_url"):
            url = s["clip_url"]
            local = url_to_local_path(url)
            if local:
                clip_urls.append(local)
            elif url.startswith("http"):
                clip_urls.append(url)
            else:
                degradation.append(f"第 {idx + 1} 镜视频片段无法解析本地路径，跳过")
                continue
        else:
            kb = kb_clip_by_idx.get(idx)
            if not kb:
                degradation.append(f"第 {idx + 1} 镜关键帧不可用，未进成片")
                continue
            clip_urls.append(kb)
        transitions.append(s.get("transition", "cut"))
        used_shots.append(s)

    if not clip_urls:
        degradation.append("无任何可用片段，尝试整段 ken-burns 合成")
        return await _compose_kenburns(shots, ratio, user_id, title, degradation)

    output_dir = _OUTPUT_DIR / user_id / "multi_image_video"
    output_dir.mkdir(parents=True, exist_ok=True)
    # 产物隔离：文件名带上本片镜头指纹，避免同用户两个会话用同一标题时互相覆盖成片
    _run_key = hashlib.md5(
        "|".join(
            f"{s.get('frame_url', '')}@{s.get('clip_url', '')}@{s.get('duration', 5.0)}"
            for s in shots
        ).encode("utf-8")
    ).hexdigest()[:8]
    final_path = output_dir / f"{title[:20]}_{_run_key}_final.mp4"

    async def _burn_subtitles_once(path: Path) -> Path:
        """成片阶段唯一一次字幕烧录。

        ken-burns 中间片段已改为不带 caption（见 _render_kenburns_clips），
        真视频片段本来就没有字幕，所以全片字幕在这里按 used_shots 统一烧。
        """
        from capabilities.video.adapters.video_compose import burn_subtitles

        durations = [float(s.get("duration", 5.0)) for s in used_shots]
        try:
            return await burn_subtitles(
                path, used_shots, durations, path,
                size=size_map.get(ratio, "1080x1920"),
            )
        except Exception as e:
            # 字幕是软需求：烧不上去也要交付能看的成片，不能让整条链断在这里
            logger.warning(f"[multi_image_video] 成片字幕烧录失败，交付无字幕版本：{e}")
            degradation.append(f"字幕烧录失败，已交付无字幕成片：{e}")
            return path

    try:
        from capabilities.video.adapters.video_compose import compose_video_concat

        result_path = await compose_video_concat(
            clip_urls=clip_urls,
            transitions=transitions,
            output_path=str(final_path),
        )
        result_path = await _burn_subtitles_once(result_path)
        return result_path.read_bytes(), degradation

    except Exception as e:
        logger.warning(f"[multi_image_video] video_concat 失败，尝试简单拼接：{e}")
        _log_compose_failure(
            "concat", e,
            user_id=user_id,
            video_clips=len(video_clips),
            resolved_clip_urls=len(clip_urls),
            clip_urls=clip_urls,
        )
        degradation.append(f"xfade 拼接失败：{e}，尝试简单 concat")

        try:
            from capabilities.video.adapters.video_compose import compose_video_concat_simple

            result_path = await compose_video_concat_simple(
                clip_urls=clip_urls,
                output_path=str(final_path),
            )
            result_path = await _burn_subtitles_once(result_path)
            return result_path.read_bytes(), degradation

        except Exception as e2:
            logger.warning(f"[multi_image_video] 简单拼接也失败，回退 ken-burns：{e2}")
            _log_compose_failure(
                "concat_simple", e2,
                user_id=user_id,
                video_clips=len(video_clips),
                resolved_clip_urls=len(clip_urls),
                clip_urls=clip_urls,
            )
            degradation.append(f"简单拼接也失败：{e2}")
            return await _compose_kenburns(shots, ratio, user_id, title, degradation)


@register
class MultiImageVideoSkill(Skill):
    """多图参考视频生成：多张参考图 + 描述 → 逐镜头编排 → 分镜关键帧 → image2video → 拼接成片。"""

    node_type = "produce"
    name = "multi_image_video"

    # 能力侧自带用法指引（prompt.build_skill_catalog 聚合进系统提示）——
    # 2026-10 纯净化：从内核 prompt.py 下沉至此，技能纪律归技能
    usage_hint = (
        "用法说明（必须遵守）：\n"
        "- 用户上传了图片并要求做视频/用这些图做视频 → skill=multi_image_video，"
        "arguments 含 image_asset_ids（用户刚上传的图片 ID，已注入上下文）和 prompt\n"
        "- 必须分两步走，禁止一步出片：\n"
        "  第一步：调技能拿分镜草稿。不需要额外传 preview_only，"
        "系统会自动停下并返回 needs_confirmation。拿到后把 title 和每个镜头"
        "（caption / image_prompt / motion_prompt / duration / transition）整理成"
        "易懂的分镜表给用户看，并问一句「这样拍可以吗，想改哪里」\n"
        "  第二步：只有用户明确表示可以/确认/生成（或给出具体修改）之后，才再调一次，"
        "这次必须带 refine_shots（= 上一步返回的 shots，按用户意见改过的版本）"
        "和同样的原始参数（同样的 image_asset_ids）一起传回，视频才会真正开始生成\n"
        "- 未经用户确认，绝对不要带 refine_shots 调用，也不要找别的旁路直接出片\n"
    )
    display_name = "多图参考视频生成"
    description = (
        "根据多张参考图片和文字描述，自动编排并生成一条完整短视频。"
        "流程：VL看图理解场景 → LLM编排分镜脚本 → AI生成分镜关键帧 → 逐镜图生视频 → 拼接成片。"
        "每个环节都有降级策略，不会因单个 API 不可用而中断。"
    )
    skill_category = "create"
    skill_priority = "hot"
    trigger_words = [
        "多图生视频", "用这些图做视频", "根据图片生成视频",
        "参考图做视频", "图片场景生成视频", "多图视频",
        "做视频", "生成视频", "视频", "图片做视频", "照片做视频",
    ]
    input_schema = MultiImageVideoInput

    async def execute(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.config import get_settings

        context = inputs.get("_context")
        user_id = getattr(context, "user_id", None)
        if not user_id:
            return {"error": "未能确认用户身份"}

        params = MultiImageVideoInput.model_validate({
            key: value for key, value in inputs.items() if key in MultiImageVideoInput.model_fields
        })

        # asset_id 守卫（2026-10-14）：拦截 LLM 幻觉的假 ID（img_1 这类），
        # 失败时回带本会话 available_asset_ids 让它用真值自纠，
        # 替代原来"假 ID 硬查库 → 404 → Agent 谎报素材库失效"的链路。
        _session_id = (getattr(context, "extra", None) or {}).get("session_id")
        from app.services.asset_ref import guard_asset_ids

        _guard = await guard_asset_ids(params.image_asset_ids, str(user_id), _session_id)
        if not _guard["ok"]:
            return {
                "error": _guard["error"],
                "hint": _guard["hint"],
                "available_asset_ids": _guard["available_asset_ids"],
            }

        workflow_id = inputs.get("_workflow_id")
        _turn_id = inputs.get("_turn_id") or ""
        degradation_notes: list[str] = []

        async def _emit_step(step: int, total: int, label: str, detail: str = "") -> None:
            if not workflow_id:
                return
            from app.services.sse_bus import sse_bus
            pct = int(step / total * 100)
            await sse_bus.publish(workflow_id, "multi_image_video_progress", {
                "skill": "multi_image_video",
                "step": step,
                "total": total,
                "label": label,
                "detail": detail,
                "percent": pct,
            }, turn_id=_turn_id or None)

        # ── 微调模式：跳过 VL+LLM，从 Step 3 开始 ──
        if params.refine_shots is not None:
            # 提取 _character_lock（confirm-storyboard 接口注入的角色锚定）
            _refine_kwargs = {}
            _char_lock_from_input = inputs.get("_character_lock", "") or ""
            if _char_lock_from_input:
                _refine_kwargs["_character_lock"] = _char_lock_from_input

            return await self._execute_refine(
                params, str(user_id), workflow_id, _emit_step,
                session_id=_session_id, turn_id=_turn_id,
                **_refine_kwargs
            )

        # Step 1: VL 理解参考图
        logger.info(f"[1/6] VL 理解参考图（{len(params.image_asset_ids)} 张）")
        await _emit_step(1, 6, "VL理解参考图", f"分析{len(params.image_asset_ids)}张参考图...")

        try:
            image_urls, image_local_paths = await _resolve_image_urls(params.image_asset_ids, str(user_id))
        except ValueError as e:
            return {"error": str(e)}

        # 九宫格拼图拆分（2026-10-17 P0）：拼图整张喂给 image2video 会得到
        # "9 格各自独立扭动"的人物变形观感。检测为真拼图就切成单图再参与编排，
        # 拆失败一律保留原图 —— 宁可不拆，也不能把素材弄丢。
        image_urls, image_local_paths = await _expand_grid_images(
            image_urls, image_local_paths, str(user_id), degradation_notes
        )

        scene_description = await _vl_understand_images(image_urls)
        if scene_description is None:
            degradation_notes.append("VL理解：不可用，跳过，直接用用户描述编排")

        # Step 2: LLM 编排分镜脚本
        logger.info("[2/6] 编排分镜脚本")
        await _emit_step(2, 6, "编排分镜脚本", "LLM 生成逐镜头脚本...")

        storyboard = await _generate_storyboard(
            scene_description=scene_description,
            user_prompt=params.prompt,
            target_duration=params.target_duration,
            shot_duration=params.shot_duration,
            style=params.style,
            num_reference_images=len(image_urls),
        )
        shots = storyboard.get("shots", [])
        title = storyboard.get("title", params.prompt[:30])

        if not shots:
            return {"error": "分镜脚本为空，无法生成视频"}

        # ── 分镜草稿构造 ──────────────────────────────────────────
        # 2026-10-17：小加同学"手持跟拍日常vlog"技能的 3 阶段流水线要求
        # 「VL 看图 → 分镜脚本 → 用户确认 → 才生成」。原来 preview_only 默认 False，
        # 调用方不显式传就会 6 步一路走完，用户完全没有介入点（product gap：
        # preview_only / refine_shots 此前除技能自身和 tests 外零引用）。
        # 现将草稿构造抽出来，供 preview_only 与下方确认闸门共用。
        async def _publish_preview_card() -> None:
            if not workflow_id:
                return
            from app.services.sse_bus import sse_bus

            await sse_bus.publish(workflow_id, "card_draft_ready", {
                "card_draft": {
                    "title": f"分镜预览 - {title}",
                    "template": "storyboard_preview",
                    "pages": [{
                        "id": f"shot_{s.get('shot_index', i+1)}",
                        "type": "content",
                        "mediaType": "image",
                        "title": s.get("caption", f"第{i+1}镜"),
                        "imagePrompt": s.get("image_prompt", ""),
                        "motionPrompt": s.get("motion_prompt", ""),
                        "duration": s.get("duration", params.shot_duration),
                        "transition": s.get("transition", "cut"),
                        "referenceImages": s.get("reference_images", []),
                    } for i, s in enumerate(shots)],
                    "pngUrls": [],
                    "htmlUrls": [],
                },
                "png_urls": [],
                "html_urls": [],
            }, turn_id=_turn_id or None)

        def _build_preview(needs_confirmation: bool) -> dict[str, Any]:
            result: dict[str, Any] = {
                "mode": "preview",
                "title": title,
                "shots": shots,
                "scene_description": scene_description,
                "image_urls": image_urls,
                "total_shots": len(shots),
                "estimated_duration": len(shots) * params.shot_duration,
                "degradation": degradation_notes,
                "hint": "编辑 shots 中任意镜的 image_prompt/motion_prompt/duration/transition 后，"
                        "通过 refine_shots 参数传回即可生成视频。"
                        "也可增删镜头（shots 列表长度可变）。",
            }
            if needs_confirmation:
                result["needs_confirmation"] = True
                result["confirm_hint"] = (
                    "分镜草稿已生成，视频尚未生成。请把草稿展示给用户并等待确认或修改，"
                    "再把最终 shots 通过 refine_shots 参数回传。未经用户确认不要自行生成。"
                )
            return result

        # ── 仅预览模式：返回分镜草稿，不生成视频 ──
        if params.preview_only:
            logger.info("[preview_only] 分镜草稿已生成，返回给用户编辑")
            await _emit_step(2, 2, "分镜预览就绪", "请编辑分镜后确认生成")
            await _publish_preview_card()
            return _build_preview(False)

        # ── 确认闸门（2026-10-17）：只有用户确认过的分镜才能生成 ──
        # refine_shots 是"用户已确认"的唯一信号。缺它时一律退回草稿，
        # 不让任何调用方（ReAct Agent / 直调脚本 / REST）跳过确认直接出片。
        if params.refine_shots is None:
            logger.info("[gate] 未提供 refine_shots，返回分镜草稿等待用户确认")
            await _emit_step(2, 2, "等待用户确认", "分镜草稿已生成，确认后再生成视频")
            await _publish_preview_card()
            return _build_preview(True)

        # Step 3: text2image 生成分镜关键帧
        logger.info(f"[3/6] 生成分镜关键帧（{len(shots)} 镜）")
        await _emit_step(3, 6, "生成分镜关键帧", f"共{len(shots)}镜...")

        # 从 storyboard 提取角色锚定（2026-10-18）
        character_lock = storyboard.get("character_lock", "")
        if character_lock:
            logger.info(f"[multi_image_video] 检测到角色锚定 (长度={len(character_lock)})，将注入所有镜头")
        else:
            logger.warning("[multi_image_video] 未检测到角色锚定，各镜头将独立生成（可能存在换脸风险）")

        shots = await _generate_shot_frames(
            shots, params.ratio, str(user_id), image_urls, character_lock=character_lock
        )

        # Step 4: image2video 逐镜生视频
        logger.info(f"[4/6] 逐镜生成视频（{len(shots)} 镜）")
        await _emit_step(4, 6, "逐镜生成视频", f"第1/{len(shots)}镜...")

        clip_on_progress = None
        if workflow_id:
            from app.services.sse_bus import sse_bus

            async def _clip_progress(elapsed: float, max_wait: float, status: str) -> None:
                await sse_bus.publish(workflow_id, "multi_image_video_progress", {
                    "skill": "multi_image_video",
                    "step": 4,
                    "total": 6,
                    "label": "逐镜生成视频",
                    "detail": f"轮询中 {elapsed:.0f}s/{max_wait:.0f}s 状态={status}",
                    "percent": 50 + int(elapsed / max_wait * 15),
                })

            clip_on_progress = _clip_progress

        shots = await _generate_shot_clips(
            shots=shots,
            ratio=params.ratio,
            shot_duration=params.shot_duration,
            user_id=str(user_id),
            provider=params.provider,
            on_progress=clip_on_progress,
            total_timeout=get_settings().multi_image_video_total_timeout,
        )

        # Step 5: 拼接成片
        logger.info("[5/6] 拼接成片")
        await _emit_step(5, 6, "拼接成片", "ffmpeg 拼接视频片段...")

        final_bytes, compose_degradation = await _compose_final(
            shots=shots,
            ratio=params.ratio,
            user_id=str(user_id),
            title=title,
        )
        degradation_notes.extend(compose_degradation)

        if not final_bytes:
            clip_urls = [s["clip_url"] for s in shots if s.get("clip_url")]
            return {
                "success": False,
                "error": "成片拼接失败",
                "degradation": degradation_notes,
                "clip_urls": clip_urls,
                "shots_count": len(shots),
            }

        # Step 6: 存 asset_library + 发 SSE
        logger.info("[6/6] 保存交付")
        await _emit_step(6, 6, "保存交付", "存储视频素材...")

        from app.services.asset_library import save_video_asset

        asset = await save_video_asset(
            user_id=str(user_id),
            filename=f"{title[:20]}_multi_image.mp4",
            content_type="video/mp4",
            data=final_bytes,
            source="ai",
            metadata={
                "source": "multi_image_video",
                "prompt": params.prompt[:200],
                "ratio": params.ratio,
                "target_duration": params.target_duration,
                "style": params.style,
                "shots_count": len(shots),
                "reference_images": len(params.image_asset_ids),
                "degradation": degradation_notes,
            },
        )

        video_url = asset["original_url"]

        if workflow_id:
            from app.services.sse_bus import sse_bus

            card_draft = {
                "title": f"多图视频 - {title}",
                "template": "video",
                "pages": [{
                    "id": "page_video",
                    "type": "content",
                    "mediaType": "video",
                    "title": title,
                    "videoUrl": video_url,
                    "width": 1280,
                    "height": 720,
                }],
                "pngUrls": [],
                "htmlUrls": [],
            }
            await sse_bus.publish(workflow_id, "card_draft_ready", {
                "card_draft": card_draft,
                "png_urls": [],
                "html_urls": [],
            }, turn_id=_turn_id or None)

        return {
            "success": True,
            "asset_id": asset.get("asset_id") or asset.get("id"),
            "asset_url": video_url,
            "title": title,
            "shots_count": len(shots),
            "video_clips": sum(1 for s in shots if s.get("clip_mode") == "video"),
            "ken_burns_clips": sum(1 for s in shots if s.get("clip_mode") != "video"),
            "degradation": degradation_notes,
        }

    async def _execute_refine(
        self,
        params: MultiImageVideoInput,
        user_id: str,
        workflow_id: str | None,
        emit_step: Any,
        session_id: str | None = None,
        turn_id: str = "",
        **kwargs: Any,
    ) -> dict[str, Any]:
        """微调模式：用户编辑了分镜后，跳过 VL+LLM，从 Step 3 开始生成。

        流程：
          1. 解析 refine_shots（用户编辑后的分镜列表）
          2. 解析参考图 URL（可用 refine_image_asset_ids 或原 image_asset_ids）
          3. Step 3→4→5→6 正常执行

        kwargs 可包含 _character_lock（角色锚定），由 confirm-storyboard 接口注入。
        """
        shots = params.refine_shots
        if not shots:
            return {"error": "refine_shots 为空，无镜头可生成"}

        from app.config import get_settings

        asset_ids = params.refine_image_asset_ids or params.image_asset_ids
        if not asset_ids:
            return {
                "error": "refine 模式缺少参考图：请把 image_asset_ids（首次那批）一起传回",
                "hint": "refine_shots 可省略 image_asset_ids，但系统仍需要它来定位关键帧素材；"
                        "首次调用返回的 image_urls 即对应关系",
            }
        degradation_notes: list[str] = ["微调模式：跳过 VL+LLM，使用用户编辑的分镜"]

        # asset_id 守卫（2026-10-14）：refine 可能换参考图，幻觉 ID 同样在此拦截
        from app.services.asset_ref import guard_asset_ids

        _guard = await guard_asset_ids(asset_ids, user_id, session_id)
        if not _guard["ok"]:
            return {
                "error": _guard["error"],
                "hint": _guard["hint"],
                "available_asset_ids": _guard["available_asset_ids"],
            }

        logger.info(f"[refine] 微调模式，{len(shots)} 镜，从 Step 3 开始")

        try:
            # P0-3 修复（2026-10-16）：_resolve_image_urls 返回 (urls, local_paths)
            # 二元组，旧代码把整个元组赋给 image_urls，导致 Step3/4 全部拿
            # 错参考图（len()==2、索引取到的是路径列表）。此处必须解包。
            image_urls, image_local_paths = await _resolve_image_urls(asset_ids, user_id)
        except ValueError as e:
            return {"error": str(e)}

        # 与首次编排保持同样的展开顺序，否则用户 shots 里的
        # reference_images 下标会指到别的图上（串镜）
        image_urls, image_local_paths = await _expand_grid_images(
            image_urls, image_local_paths, user_id, degradation_notes
        )

        # refine 模式跳过 VL+LLM，标题只能靠调用方回传（confirm-storyboard 会从
        # 落盘状态里带上）；否则会退化成 prompt 前 30 字的占位符
        title = params.refine_title or params.prompt[:30]

        # Step 3: text2image
        await emit_step(3, 6, "生成分镜关键帧", f"共{len(shots)}镜...")

        # refine 模式下尝试提取角色锚定（2026-10-18）
        # 优先级：kwargs._character_lock > shots[0].character_lock > 空
        character_lock = kwargs.get("_character_lock", "") or ""
        if not character_lock and shots and isinstance(shots, list) and len(shots) > 0:
            # 尝试从第一个 shot 的元数据中获取（如果用户编辑时保留）
            character_lock = shots[0].get("character_lock", "") if isinstance(shots[0], dict) else ""

        if character_lock:
            logger.info(f"[refine] 检测到角色锚定 (长度={len(character_lock)})，将注入所有镜头")
        else:
            logger.warning(
                "[refine] 未检测到角色锚定，各镜头将独立生成（可能存在换脸风险）。"
                "建议 confirm-storyboard 接口在返回 refine_shots 时保留 character_lock 字段"
            )

        shots = await _generate_shot_frames(
            shots, params.ratio, user_id, image_urls, character_lock=character_lock
        )

        # Step 4: image2video
        await emit_step(4, 6, "逐镜生成视频", f"第1/{len(shots)}镜...")

        clip_on_progress = None
        if workflow_id:
            from app.services.sse_bus import sse_bus

            async def _clip_progress(elapsed: float, max_wait: float, status: str) -> None:
                await sse_bus.publish(workflow_id, "multi_image_video_progress", {
                    "skill": "multi_image_video",
                    "step": 4,
                    "total": 6,
                    "label": "逐镜生成视频",
                    "detail": f"轮询中 {elapsed:.0f}s/{max_wait:.0f}s 状态={status}",
                    "percent": 50 + int(elapsed / max_wait * 15),
                }, turn_id=turn_id or None)

            clip_on_progress = _clip_progress

        shots = await _generate_shot_clips(
            shots=shots,
            ratio=params.ratio,
            shot_duration=params.shot_duration,
            user_id=user_id,
            provider=params.provider,
            on_progress=clip_on_progress,
            total_timeout=get_settings().multi_image_video_total_timeout,
        )

        # Step 5: 拼接
        await emit_step(5, 6, "拼接成片", "ffmpeg 拼接视频片段...")
        final_bytes, compose_degradation = await _compose_final(
            shots=shots, ratio=params.ratio, user_id=user_id, title=title,
        )
        degradation_notes.extend(compose_degradation)

        if not final_bytes:
            clip_urls = [s["clip_url"] for s in shots if s.get("clip_url")]
            return {
                "success": False,
                "error": "成片拼接失败",
                "degradation": degradation_notes,
                "clip_urls": clip_urls,
                "shots_count": len(shots),
            }

        # Step 6: 保存
        await emit_step(6, 6, "保存交付", "存储视频素材...")

        from app.services.asset_library import save_video_asset
        asset = await save_video_asset(
            user_id=user_id,
            filename=f"{title[:20]}_refine.mp4",
            content_type="video/mp4",
            data=final_bytes,
            source="ai",
            metadata={
                "source": "multi_image_video_refine",
                "prompt": params.prompt[:200],
                "ratio": params.ratio,
                "shots_count": len(shots),
                "degradation": degradation_notes,
            },
        )

        video_url = asset["original_url"]

        if workflow_id:
            from app.services.sse_bus import sse_bus
            card_draft = {
                "title": f"微调视频 - {title}",
                "template": "video",
                "pages": [{
                    "id": "page_video",
                    "type": "content",
                    "mediaType": "video",
                    "title": title,
                    "videoUrl": video_url,
                    "width": 1280,
                    "height": 720,
                }],
                "pngUrls": [],
                "htmlUrls": [],
            }
            logger.info(f"[refine] publishing card_draft_ready to workflow_id={workflow_id!r}")
            await sse_bus.publish(workflow_id, "card_draft_ready", {
                "card_draft": card_draft,
                "png_urls": [],
                "html_urls": [],
            }, turn_id=turn_id or None)
        else:
            logger.warning(f"[refine] workflow_id is empty, cannot publish card_draft_ready")

        return {
            "success": True,
            "mode": "refine",
            "asset_id": asset.get("asset_id") or asset.get("id"),
            "asset_url": video_url,
            "title": title,
            "shots_count": len(shots),
            "video_clips": sum(1 for s in shots if s.get("clip_mode") == "video"),
            "ken_burns_clips": sum(1 for s in shots if s.get("clip_mode") != "video"),
            "degradation": degradation_notes,
        }