"""视频工坊编排服务：VL 理解 / 分镜编排 / 参考图解析。

W3a（2026-10-16）从 app/skills/produce/multi_image_video.py 迁出，
让「编排」成为可被多方调用的纯 service：
  - REST 接口层（W4，/api/video/*）
  - 对话 ReAct 入口（旧 skill 退化为薄封装，方案 §3）
  - 旧 Skill（multi_image_video.py 以同名再导出保持兼容，见该文件头部注释）

mock/real 双模分发（settings.video_provider_mode，阶段 0 默认 mock）：
  - mock：不走 LLM/ VL，Provider.generate_storyboard 本地规则拆镜（零成本）；
  - real：VL 理解参考图 + 分级路由 LLM 编排分镜（阶段 1 接入）。

为什么只迁 VL/分镜/参考图三件，不迁关键帧/片段/拼接：
  方案 W3 原文写的是"五个函数全迁"，但那是 W2（MediaProvider 契约 +
  JobService 任务层）落地前的判断。现在镜头三阶段已有更好的家——
  app/services/video_handlers.py 的三个 handler + Provider 作业契约
  （mock/real 切换零改动）。若再往 orchestrator 迁一份，等于同一逻辑
  两处实现，违背"唯一耦合点"原则。因此本条只迁编排层（LLM 相关），
  镜头生成走 handler/provider，旧 skill 内的 _generate_shot_frames /
  _generate_shot_clips / _compose_final 保留为 legacy 同步路径。
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


# ═══ 1. Prompt 常量（迁自 multi_image_video.py） ═══════════

_VL_SCENE_PROMPT = """你是一个专业的视频策划师。请仔细观察以下参考图片，分析并输出 JSON（不要 markdown 代码块，直接输出 JSON）：

{
  "scenes": [
    {
      "image_index": 1,
      "location": "场景地点",
      "subjects": ["主体1", "主体2"],
      "props": ["道具1", "道具2"],
      "lighting": "光线描述",
      "color_tone": "色调",
      "composition": "构图描述",
      "mood": "情绪氛围"
    }
  ],
  "cross_scene": {
    "shared_subjects": ["跨场景出现的主体"],
    "visual_style": "统一视觉风格",
    "color_system": "色彩体系"
  },
  "character_anchor": {
    "anchor_image_index": 1,
    "character_description": "主角的详细外观描述（发型、脸型、肤色、体型、穿着特征），用于后续所有镜头保持同一人",
    "face_features": "面部具体特征（脸型轮廓、眼睛形状、鼻子、嘴唇等），英文描述用于AI生图一致性"
  }
}

要求：
- 每张图单独分析，image_index 从 1 开始
- cross_scene 描述多图之间的关联（重复出现的角色/道具/场景）
- 描述要具体到可用于 AI 生图的提示词
- 【关键】character_anchor 是最重要的字段：
  - anchor_image_index：选择一张最清晰展示主角正面的照片（优先选脸部清晰、光线好的）
  - character_description：用中文详细描述主角外观（如"圆脸短发女生，皮肤白皙，穿绿色吊带和白色长裤"）
  - face_features：用英文精确描述面部特征（如 "young asian woman, round face, long dark hair, fair skin, gentle smile, natural makeup"）
  - 后续所有镜头的生图/生视频都必须基于此描述确保是同一个人
"""

_STORYBOARD_PROMPT = """你是一个专业的短视频导演。根据以下参考图分析和用户描述，编排逐镜头脚本。

参考图分析：
{scene_description}

用户描述：{user_prompt}
目标时长：{duration}秒
风格：{style}
每镜头时长：{shot_duration}秒

请输出 JSON（不要 markdown 代码块，直接输出 JSON）：
{{
  "title": "视频标题",
  "total_shots": 5,
  "character_lock": "主角外观锁定描述（从 character_anchor 复制，所有镜头必须严格遵循）",
  "shots": [
    {{
      "shot_index": 1,
      "caption": "这镜头的口播或字幕文案",
      "image_prompt": "关键帧画面描述（英文，用于 AI 生图），必须以 character_lock 开头，然后接场景/动作/构图描述",
      "motion_prompt": "镜头运动描述（只允许：缓慢推进、缓慢拉远、微微侧身、转身微笑、伸手、抬头、低头、端起杯子、整理头发、轻步走动、静立、坐下、躺下——禁止跳跃、奔跑、翻滚、跳舞、大幅度挥舞等 AI 渲染不真实的动作）",
      "reference_images": [1],
      "duration": 5.0,
      "transition": "cut"
    }}
  ]
}}

【角色一致性硬性规则 - 违反将导致视频不可用】：
1. 如果 scene_description 中有 character_anchor 字段，必须将其 face_features 和 character_description 复制到顶层 character_lock
2. 每个 shot 的 image_prompt 必须以 character_lock 的内容开头（如 "{{face_features}}, {{scene_description}}"）
3. 所有镜头的主角必须是同一个人——相同的脸型、发型、肤色、体型
4. 允许换衣服，但必须是同一人穿不同衣服（不能变成另一个人）
5. 禁止出现与主角完全不同的新角色作为"主角"

【动作安全规则】：
- motion_prompt 只允许静态/微动态动作（见上方列表）
- 禁止任何剧烈/大幅度动作（AI 视频模型无法真实渲染）
- 镜头运动优先用运镜（推进/拉远/横移/摇镜）而非人物大动作

【空间连贯性规则】：
- 相邻镜头之间必须有合理的空间/时间过渡（不能从走廊突然跳到户外草坪）
- 利用 transition 字段标记转场方式

其他要求：
- reference_images 引用参考图的编号（1开始），表示这镜参考哪张图
- image_prompt 英文描述，具体有画面感
- 分镜数 = 目标时长 / 每镜时长（向上取整）
- transition: cut / dissolve / wipe
- 第一个镜头要有 hook（3秒内抓住注意力）
- 每句 caption 15-30 字，口语化
"""


# ═══ 2. 参考图解析（迁自 multi_image_video._resolve_image_urls） ══


async def resolve_reference_images(
    image_asset_ids: list[str],
    user_id: str,
) -> tuple[list[str], list[str]]:
    """从 asset_id 列表解析图片 URL 列表 + 本地绝对路径列表。

    返回 (urls, local_paths)：
      - urls: /uploads/assets/... 相对 URL（存库格式）
      - local_paths: 对应的本地绝对路径（已验证存在），不存在时为空字符串
    """
    from sqlalchemy import select

    from app.db.models import ImageAsset
    from app.db.session import AsyncSessionLocal
    from app.services.asset_library import url_to_local_path

    urls: list[str] = []
    local_paths: list[str] = []
    async with AsyncSessionLocal() as session:
        for aid in image_asset_ids:
            stmt = select(ImageAsset).where(
                ImageAsset.id == aid,
                ImageAsset.user_id == user_id,
            )
            result = await session.execute(stmt)
            asset = result.scalar_one_or_none()
            if asset is None:
                raise ValueError(f"未找到 asset_id={aid} 的图片素材")
            url = asset.original_url
            urls.append(url)
            local = url_to_local_path(url) or ""
            if not local:
                logger.warning(f"[video_orchestrator] 图片本地路径不存在：{url}")
            local_paths.append(local)
    return urls, local_paths


async def resolve_video_assets(
    video_asset_ids: list[str],
    user_id: str,
) -> tuple[list[str], list[str]]:
    """从 video_asset_id 列表解析视频 URL 列表 + 本地绝对路径列表。

    返回 (urls, local_paths)：
      - urls: /uploads/videos/... 相对 URL（存库格式）
      - local_paths: 对应的本地绝对路径（已验证存在），不存在时为空字符串
    """
    from sqlalchemy import select

    from app.db.models import VideoAsset
    from app.db.session import AsyncSessionLocal
    from app.services.asset_library import url_to_local_path

    urls: list[str] = []
    local_paths: list[str] = []
    async with AsyncSessionLocal() as session:
        for aid in video_asset_ids:
            stmt = select(VideoAsset).where(
                VideoAsset.id == aid,
                VideoAsset.user_id == user_id,
            )
            result = await session.execute(stmt)
            asset = result.scalar_one_or_none()
            if asset is None:
                raise ValueError(f"未找到 video_asset_id={aid} 的视频素材")
            url = asset.original_url
            urls.append(url)
            local = url_to_local_path(url) or ""
            if not local:
                logger.warning(f"[video_orchestrator] 视频本地路径不存在：{url}")
            local_paths.append(local)
    return urls, local_paths


# ═══ 3. VL 理解（迁自 multi_image_video._vl_understand_images） ══


async def vl_understand_images(
    image_urls: list[str],
) -> dict[str, Any] | None:
    """用 QwenVL 理解多张参考图的场景语义。

    降级：VL 不可用时返回 None，调用方跳过此步。
    """
    from app.config import get_settings

    settings = get_settings()
    api_key = (settings.dashscope_api_key or "").strip()
    if not api_key:
        logger.info("[video_orchestrator] 未配置 DASHSCOPE_API_KEY，跳过 VL 理解")
        return None

    try:
        from app.services.model_tier import PipelineStage, get_tier_router
        from capabilities.image_gen.adapters.qwen_vl import QwenVLAdapter

        router = get_tier_router()
        vl_model = router.resolve(PipelineStage.VL_UNDERSTAND, "vl")
        logger.info(f"[video_orchestrator] VL 理解使用分级模型: {vl_model}")
        adapter = QwenVLAdapter(api_key=api_key, model=vl_model)

        from app.services.asset_library import url_to_base64, url_to_local_path

        content: list[dict[str, Any]] = [{"text": _VL_SCENE_PROMPT}]
        for i, url in enumerate(image_urls, 1):
            if url.startswith("http"):
                image_src = url
            elif url.startswith("data:"):
                image_src = url
            else:
                image_src = url_to_base64(url)
                if not image_src:
                    local = url_to_local_path(url)
                    if local:
                        image_src = url_to_base64(local)
                if not image_src:
                    logger.warning(f"[video_orchestrator] VL 图片无法读取，跳过第{i}张：{url[:80]}")
                    continue
            content.append({"image": image_src})

        messages = [{"role": "user", "content": content}]
        result = await adapter.chat(messages)

        # 防御（2026-10-13）：DashScope 多模态返回的 content 可能是
        # list[{"text": ...}]（多图输入时常见），统一拉平成字符串再解析，
        # 否则 text.find("{") 会 AttributeError 把 VL 步骤意外降级。
        raw = result.get("content", "")
        if isinstance(raw, list):
            text = "".join(
                item.get("text", "") if isinstance(item, dict) else str(item) for item in raw
            )
        else:
            text = str(raw or "")
        json_start = text.find("{")
        json_end = text.rfind("}") + 1
        if json_start >= 0 and json_end > json_start:
            scene_desc = json.loads(text[json_start:json_end])
            logger.info(
                f"[video_orchestrator] VL 理解完成：{len(scene_desc.get('scenes', []))} 个场景"
            )
            return scene_desc

        logger.warning(f"[video_orchestrator] VL 返回非 JSON，跳过：{text[:200]}")
        return None

    except Exception as e:
        logger.warning(f"[video_orchestrator] VL 理解失败，降级跳过：{e}")
        return None


# ═══ 4. 分镜编排（迁自 multi_image_video._generate_storyboard） ══


def _enforce_character_lock(
    storyboard: dict[str, Any],
    scene_description: dict[str, Any] | None,
) -> dict[str, Any]:
    """强制注入角色锚定（2026-10-18 后处理）。

    LLM 可能不遵循 _STORYBOARD_PROMPT 输出 character_lock 字段，
    此函数在返回前检查并补充：
      1. 优先从 VL 的 character_anchor.face_features 提取
      2. 其次从第一镜 image_prompt 提取角色描述部分
      3. 注入到顶层 character_lock + 每镜 image_prompt 开头
    """
    shots = storyboard.get("shots", [])
    if not shots:
        return storyboard

    # 步骤 1：获取或构建 character_lock
    character_lock = storyboard.get("character_lock", "")

    if not character_lock or len(character_lock.strip()) < 10:
        # 尝试从 VL 的 character_anchor 提取（增加异常保护）
        try:
            if scene_description and isinstance(scene_description, dict):
                anchor = scene_description.get("character_anchor")
                logger.debug(
                    f"[video_orchestrator] VL character_anchor 类型: {type(anchor)}, 值: {str(anchor)[:200]}"
                )

                if isinstance(anchor, dict):
                    face_features = anchor.get("face_features", "")
                    char_desc = anchor.get("character_description", "")
                    if face_features and len(face_features.strip()) > 5:
                        character_lock = face_features
                        logger.info(
                            f"[video_orchestrator] 从 VL character_anchor.face_features 提取角色锚定 "
                            f"(长度={len(character_lock)})"
                        )
                    elif char_desc and len(char_desc.strip()) > 5:
                        # 如果没有英文 face_features，用中文描述
                        character_lock = char_desc
                        logger.info(
                            f"[video_orchestrator] 使用 character_description 作为角色锚定 "
                            f"(长度={len(character_lock)})"
                        )
                elif isinstance(anchor, str) and len(anchor.strip()) > 5:
                    # 如果 character_anchor 是字符串而非字典
                    character_lock = anchor
                    logger.info(
                        f"[video_orchestrator] character_anchor 是字符串，直接使用 "
                        f"(长度={len(character_lock)})"
                    )
                else:
                    logger.debug(f"[video_orchestrator] character_anchor 格式异常: {type(anchor)}")
        except Exception as e:
            logger.warning(
                f"[video_orchestrator] 提取 character_anchor 时出错: {e}，将尝试其他方式"
            )

    # 步骤 2：如果仍没有，尝试从第一镜提取
    if not character_lock or len(character_lock.strip()) < 10:
        first_prompt = shots[0].get("image_prompt", "")
        if first_prompt and len(first_prompt) > 20:
            # 简单启发式：提取第一个逗号前的内容（通常是角色描述）
            comma_pos = first_prompt.find(",")
            if comma_pos > 10:
                candidate = first_prompt[:comma_pos].strip()
                # 验证候选字符串看起来像角色描述（包含常见关键词）
                role_keywords = ["woman", "girl", "man", "boy", "person", "asian", "young"]
                if any(kw in candidate.lower() for kw in role_keywords):
                    character_lock = candidate
                    logger.info(
                        f"[video_orchestrator] 从第一镜 image_prompt 提取角色锚定 "
                        f"(长度={len(character_lock)})"
                    )

    # 步骤 3：如果没有找到任何角色锚定，记录警告
    if not character_lock or len(character_lock.strip()) < 10:
        logger.warning(
            "[video_orchestrator] ⚠️ 无法提取角色锚定！"
            "各镜头将独立生成，可能存在换脸风险。"
            "建议检查参考图是否包含清晰的人物正面照。"
        )
        return storyboard

    # 步骤 4：注入到 storyboard 顶层
    storyboard["character_lock"] = character_lock
    logger.info(f"[video_orchestrator] ✅ 角色锚定已注入 (长度={len(character_lock)})")

    # 步骤 5：遍历所有镜头，确保 image_prompt 以 character_lock 开头
    for i, shot in enumerate(shots):
        prompt = shot.get("image_prompt", "")
        if not prompt:
            continue

        # 检查是否已包含 character_lock 前缀（避免重复）
        lock_prefix = character_lock[:50].lower()
        prompt_start = prompt[:50].lower()

        if not prompt_start.startswith(lock_prefix):
            original_prompt = prompt
            shot["image_prompt"] = f"{character_lock}, {prompt}"
            logger.info(
                f"[video_orchestrator] 镜头{i+1} 角色锚定已注入 "
                f"(原prompt长度={len(original_prompt)}, 新prompt长度={len(shot['image_prompt'])})"
            )
        else:
            logger.debug(f"[video_orchestrator] 镜头{i+1} 已包含角色锚定前缀，跳过")

    return storyboard


async def generate_storyboard(
    scene_description: dict[str, Any] | None,
    user_prompt: str,
    target_duration: float,
    shot_duration: float,
    style: str,
    num_reference_images: int,
) -> dict[str, Any]:
    """LLM 编排分镜脚本。

    降级：LLM 不可用或 JSON 解析失败时，生成默认分镜。
    """
    if scene_description:
        scene_text = json.dumps(scene_description, ensure_ascii=False, indent=2)
    else:
        scene_text = (
            f"（VL 未分析，但有 {num_reference_images} 张不同的参考图，每张图对应一个镜头，"
            f"共 {num_reference_images} 镜。请根据用户描述，为每张图想象不同的画面场景，"
            f"确保每镜的 image_prompt 各不相同——例如第1张可能是大堂/入口，"
            f"第2张可能是室内/泳池，第3张可能是露台/景观……按度假动线编排，"
            f"不要7镜用同一个描述复制7遍。）"
        )

    prompt = _STORYBOARD_PROMPT.format(
        scene_description=scene_text,
        user_prompt=user_prompt,
        duration=int(target_duration),
        style=style,
        shot_duration=int(shot_duration),
    )

    try:
        from app.adapters.llm_base import llm_prompt_to_text
        from app.engine.factory import get_deepseek_llm
        from app.services.model_tier import PipelineStage, get_tier_router

        router = get_tier_router()
        llm_model = router.resolve(PipelineStage.LLM_STORYBOARD, "llm")
        logger.info(f"[video_orchestrator] LLM 编排使用分级模型: {llm_model}")
        llm = get_deepseek_llm(model=llm_model)
        if llm is None:
            raise ValueError("LLM 不可用（熔断器开启或未配置 API Key）")
        response_text = await llm_prompt_to_text(llm, prompt)
        text = response_text

        json_start = text.find("{")
        json_end = text.rfind("}") + 1
        if json_start >= 0 and json_end > json_start:
            storyboard = json.loads(text[json_start:json_end])
            if "shots" in storyboard and storyboard["shots"]:
                # ── 角色锚定后处理（2026-10-18）─────────────────────
                # 暂时禁用以排查 face_features 错误
                # TODO: 修复后重新启用
                try:
                    storyboard = _enforce_character_lock(storyboard, scene_description)
                except Exception as e:
                    logger.error(
                        f"[video_orchestrator] ⚠️ _enforce_character_lock 出错: {e}，跳过角色锚定注入"
                    )

                logger.info(f"[video_orchestrator] 分镜脚本生成：{len(storyboard['shots'])} 镜")
                return storyboard

        raise ValueError("脚本缺少 shots 字段")

    except Exception as e:
        logger.warning(f"[video_orchestrator] LLM 编排失败，使用默认分镜：{e}")
        num_shots = max(1, int(target_duration / shot_duration))
        _default_motions = [
            "缓慢推进",
            "横向跟移",
            "微俯缓推",
            "低机位跟拍",
            "静物微动",
            "匀速横移",
            "拉远",
            "环绕",
            "仰拍推进",
            "手持晃动",
        ]
        _default_captions = [
            "开场引入",
            "场景展开",
            "细节特写",
            "人物互动",
            "氛围营造",
            "情绪推进",
            "转折过渡",
            "高潮呈现",
            "收束回味",
            "离场留白",
        ]
        return {
            "title": user_prompt[:30],
            "total_shots": num_shots,
            "shots": [
                {
                    "shot_index": i + 1,
                    "caption": f"第{i + 1}镜：{_default_captions[i % len(_default_captions)]}，{user_prompt[:15]}",
                    "image_prompt": f"{user_prompt}, shot {i + 1} of {num_shots}, {_default_captions[i % len(_default_captions)]}, cinematic, high quality, detailed, varied composition",
                    "motion_prompt": _default_motions[i % len(_default_motions)],
                    "reference_images": [min(i + 1, num_reference_images)],
                    "duration": shot_duration,
                    "transition": "cut",
                }
                for i in range(num_shots)
            ],
        }


# ═══ 5. 编排入口：mock/real 双模分发 ═══════════════════════


class VideoOrchestrator:
    """编排入口。W4 REST 接口与未来 ReAct 薄封装都走这里。

    用法：
        orch = VideoOrchestrator()
        images = await orch.prepare_images(image_asset_ids, user_id)
        storyboard = await orch.create_storyboard(
            prompt="...", image_urls=images["image_urls"],
            target_duration=15.0, shot_duration=5.0, style="电影感", ratio="9:16",
        )
        # storyboard["shots"] → 交 W1 任务层（submit_job）逐镜生成
    """

    # ── 配置 ──────────────────────────────────────────────

    @property
    def mode(self) -> str:
        """当前 provider 模式：mock（阶段 0）/ real（阶段 1+）。"""
        from app.config import get_settings

        return (getattr(get_settings(), "video_provider_mode", "mock") or "mock").strip()

    async def _provider(self) -> Any:
        from capabilities.video.adapters.media_provider import resolve_media_provider

        return await resolve_media_provider()

    # ── 参考图 ────────────────────────────────────────────

    async def prepare_images(
        self,
        image_asset_ids: list[str],
        user_id: str,
    ) -> dict[str, Any]:
        """解析参考图 asset_id → {"image_urls": [...], "image_local_paths": [...]}。

        素材不存在时抛 ValueError（由调用方转 4xx）。
        """
        urls, local_paths = await resolve_reference_images(image_asset_ids, user_id)
        return {"image_urls": urls, "image_local_paths": local_paths}

    # ── 分镜 ──────────────────────────────────────────────

    async def create_storyboard(
        self,
        *,
        prompt: str,
        image_urls: list[str] | None = None,
        scene_description: dict[str, Any] | None = None,
        target_duration: float = 15.0,
        shot_duration: float = 5.0,
        style: str = "电影感",
        ratio: str = "9:16",
    ) -> dict[str, Any]:
        """产出分镜脚本 {"title": ..., "shots": [...], ...}。

        mock 模式：Provider 规则拆镜（不调 LLM），shots 带 shot_id；
        real 模式：VL 理解 + LLM 编排（任一步降级都有兜底分镜）。
        """
        if self.mode == "mock":
            provider = await self._provider()
            shot_count = max(1, round(target_duration / shot_duration))
            storyboard = await provider.generate_storyboard(
                prompt,
                shot_count=shot_count,
                shot_duration=shot_duration,
                style=style,
                ratio=ratio,
            )
            storyboard["degradation"] = ["mock 模式：规则拆镜，未调用 LLM"]
            logger.info(f"[video_orchestrator] mock 分镜：{len(storyboard.get('shots', []))} 镜")
            return storyboard

        # real 模式：VL → LLM 编排（可传入已算好的 scene_description 跳过 VL）
        if scene_description is None:
            scene_description = await vl_understand_images(image_urls or [])
        return await generate_storyboard(
            scene_description=scene_description,
            user_prompt=prompt,
            target_duration=target_duration,
            shot_duration=shot_duration,
            style=style,
            num_reference_images=len(image_urls or []),
        )

    # ── 自检 ──────────────────────────────────────────────

    async def status(self) -> dict[str, Any]:
        """模式 + Provider 离线自检（不发外部请求）。"""
        provider = await self._provider()
        check = await provider.check()
        return {
            "mode": self.mode,
            "provider": provider.name,
            "check": check,
            "fallback_note": getattr(provider, "fallback_note", None),
        }


video_orchestrator = VideoOrchestrator()
