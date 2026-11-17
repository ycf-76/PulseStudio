"""能力侧 dispatch 钩子（业务上下文注入）。

这里属于能力装配层，不是内核——内核 session 只跑钩子链，
不认识 multi_image_video / auto_short_video 这些业务工具名。
工厂（agents/registry.build_session）与 reactor 路由在构造
ReactorSession 时显式注入 DEFAULT_DISPATCH_HOOKS。
"""

from __future__ import annotations

import logging
from typing import Any

from app.engine.protocol.dispatch_hooks import DispatchContext, unwrap_skill_call

logger = logging.getLogger(__name__)

# 需要真实图片资产的工具（LLM 幻觉 image_asset_ids 的兜底注入目标）
_IMAGE_ASSET_TOOLS = frozenset({"multi_image_video", "auto_short_video"})

# 需要真实图片资产的「看图」工具：LLM 只拿到 asset_id 文本，
# 忘传或编了不存在的 id 时由钩子兜底注入真实 id。
_VL_ASSET_TOOLS = frozenset({"vl_analyze"})

# 需要真实视频资产的工具（LLM 幻觉 video_asset_ids 的兜底注入目标）
_VIDEO_ASSET_TOOLS = frozenset({"smart_cut", "talking_head_overlay"})

# 只需要 video_path、不接受 video_asset_ids 的工具：
# 作品视频分析（采集/草稿视频进 chat）先转写再分析，转写入参就是一条本地路径。
_VIDEO_PATH_ONLY_TOOLS = frozenset({"video_transcribe"})


class CreativeImageContextHook:
    """创作图片上下文注入：skill 需要图片但 LLM 未传或传了假值时，
    从 run_context 取真实 asset id 覆盖，防止幻觉导致图片发不出去。

    必须排在 SkillCallUnwrapHook 之后（pre_execute 链内自解包内层参数）。
    """

    async def pre_clarify(self, name, args, ctx: DispatchContext):
        return name, args

    async def pre_execute(
        self, name: str, args: dict[str, Any], ctx: DispatchContext
    ) -> tuple[str, dict[str, Any]]:
        ctx_ids = (ctx.run_context or {}).get("image_asset_ids")
        if not ctx_ids:
            return name, args

        # skill_call：注入目标是内层 arguments；直接调用：就是 args 本身
        outer_name, inner_name, inner_args, is_nested = name, name, args, False
        if name == "skill_call":
            inner_name, inner_args = unwrap_skill_call(name, args)
            is_nested = True
            if not isinstance(args.get("arguments"), dict):
                args["arguments"] = inner_args

        if inner_name not in _IMAGE_ASSET_TOOLS:
            return name, args

        llm_ids = inner_args.get("image_asset_ids")
        if not llm_ids:
            inner_args["image_asset_ids"] = ctx_ids
            logger.debug(f"[hook] auto-injected image_asset_ids={ctx_ids} into {inner_name}")
        elif isinstance(llm_ids, list) and llm_ids:
            looks_fake = any(not str(_id).startswith("asset_") for _id in llm_ids)
            if looks_fake:
                inner_args["image_asset_ids"] = ctx_ids
                logger.debug(
                    f"[hook] replaced hallucinated image_asset_ids {llm_ids} "
                    f"with real {ctx_ids} for {inner_name}"
                )
        # is_nested 时 inner_args 就是 args["arguments"] 的引用，原地改已生效
        if is_nested:
            args["arguments"] = inner_args
        return name, args


class VLAnalyzeContextHook:
    """看图上下文注入（兜底）：vl_analyze 的 asset_id 由框架保证真实。

    LLM 只能看到聊天里透出的 asset_id 文本，看不见像素——它可能忘传、
    也可能编一个不存在的 id（幻觉）。这里从 run_context 的真实
    image_asset_ids 兜底：缺省则补第一张；编造则替换为第一张。
    已显式给了 image_base64 / image_path / image_url 时不干预
    （前两条自带图源，image_url 是作品配图通道，优先级高于 asset_id）。
    """

    async def pre_clarify(self, name, args, ctx: DispatchContext):
        return name, args

    async def pre_execute(
        self, name: str, args: dict[str, Any], ctx: DispatchContext
    ) -> tuple[str, dict[str, Any]]:
        ctx_ids = (ctx.run_context or {}).get("image_asset_ids")
        if not ctx_ids:
            return name, args

        inner_name, inner_args, is_nested = name, args, False
        if name == "skill_call":
            inner_name, inner_args = unwrap_skill_call(name, args)
            is_nested = True
            if not isinstance(args.get("arguments"), dict):
                args["arguments"] = inner_args

        if inner_name not in _VL_ASSET_TOOLS:
            return name, args

        if (
            inner_args.get("image_base64")
            or inner_args.get("image_path")
            or inner_args.get("image_url")
        ):
            return name, args

        real_id = str(ctx_ids[0])
        llm_id = str(inner_args.get("asset_id") or "").strip()
        if not llm_id:
            inner_args["asset_id"] = real_id
            logger.debug(f"[hook] auto-injected asset_id={real_id} into {inner_name}")
        elif llm_id not in [str(i) for i in ctx_ids]:
            inner_args["asset_id"] = real_id
            logger.debug(
                f"[hook] replaced hallucinated asset_id {llm_id} with {real_id} for {inner_name}"
            )

        if is_nested:
            args["arguments"] = inner_args
        return name, args


async def _resolve_video_paths(video_asset_ids: list[str], user_id: str | None) -> list[str]:
    """把 video_asset_ids 解析为本地文件路径列表。

    调用 resolve_video_assets 从 DB 查 original_url，再用 url_to_local_path
    转为本地绝对路径。解析失败时返回空列表（不阻断 Skill 执行）。
    """
    if not video_asset_ids or not user_id:
        return []
    try:
        from capabilities.video.services.video_orchestrator import resolve_video_assets

        _urls, local_paths = await resolve_video_assets(video_asset_ids, user_id)
        valid = [p for p in local_paths if p]
        if valid:
            logger.debug(f"[hook] resolved video_asset_ids {video_asset_ids} -> {valid}")
        return valid
    except Exception as exc:
        logger.warning(f"[hook] failed to resolve video_asset_ids {video_asset_ids}: {exc}")
        return []


class CreativeVideoContextHook:
    """创作视频上下文注入：skill 需要视频但 LLM 未传或传了假值时，
    从 run_context 取真实 video_asset_id，解析为本地 video_path 注入 Skill。

    核心职责：
      1. 把 video_asset_ids（ULID）解析为 video_path（本地绝对路径）
      2. 注入到 Skill 的 video_path 参数（smart_cut / talking_head_overlay 的入口）
      3. 同时注入 video_asset_ids 供 Skill 内部引用

    必须排在 SkillCallUnwrapHook 之后（pre_execute 链内自解包内层参数）。
    """

    async def pre_clarify(self, name, args, ctx: DispatchContext):
        return name, args

    async def pre_execute(
        self, name: str, args: dict[str, Any], ctx: DispatchContext
    ) -> tuple[str, dict[str, Any]]:
        ctx_ids = (ctx.run_context or {}).get("video_asset_ids")
        # 视频作品（详情页路由进 chat）：用户没另外上传素材，但作品本身有本地缓存视频，
        # 由 chat_agent 预先解析成绝对路径放进 run context。
        work_video_path = str((ctx.run_context or {}).get("work_video_path") or "").strip()
        if not ctx_ids and not work_video_path:
            return name, args

        outer_name, inner_name, inner_args, is_nested = name, name, args, False
        if name == "skill_call":
            inner_name, inner_args = unwrap_skill_call(name, args)
            is_nested = True
            if not isinstance(args.get("arguments"), dict):
                args["arguments"] = inner_args

        if inner_name not in _VIDEO_ASSET_TOOLS and inner_name not in _VIDEO_PATH_ONLY_TOOLS:
            return name, args

        # 注入 video_asset_ids（与图片 Hook 一致，防 LLM 幻觉）
        # video_transcribe 只吃 video_path，多塞一个 id 数组会污染入参，跳过这一段。
        if inner_name in _VIDEO_ASSET_TOOLS:
            llm_ids = inner_args.get("video_asset_ids")
            if not ctx_ids:
                pass
            elif not llm_ids:
                inner_args["video_asset_ids"] = ctx_ids
                logger.debug(f"[hook] auto-injected video_asset_ids={ctx_ids} into {inner_name}")
            elif isinstance(llm_ids, list) and llm_ids:
                looks_fake = any(not str(_id).startswith("asset_") for _id in llm_ids)
                if looks_fake:
                    inner_args["video_asset_ids"] = ctx_ids
                    logger.debug(
                        f"[hook] replaced hallucinated video_asset_ids {llm_ids} "
                        f"with real {ctx_ids} for {inner_name}"
                    )

        # 解析 video_asset_ids → video_path（本地绝对路径）
        user_id = (ctx.run_context or {}).get("user_id")
        video_paths = await _resolve_video_paths(ctx_ids, user_id) if ctx_ids else []
        if not video_paths and work_video_path:
            video_paths = [work_video_path]
        if video_paths:
            existing_path = inner_args.get("video_path")
            if not existing_path:
                inner_args["video_path"] = video_paths[0]
                logger.debug(f"[hook] auto-injected video_path={video_paths[0]} into {inner_name}")
            elif existing_path and not str(existing_path).startswith("/"):
                looks_like_id = len(str(existing_path)) < 30 and "/" not in str(existing_path)
                if looks_like_id:
                    inner_args["video_path"] = video_paths[0]
                    logger.debug(
                        f"[hook] replaced non-path video_path '{existing_path}' "
                        f"with resolved {video_paths[0]} for {inner_name}"
                    )

        # gate_mode 注入范围收窄（2026-11-01 修复设计文档 3.4 #2 缺口2）：
        # 之前对 talking_head_overlay 一律注入 auto_confirm，导致用户在渲染前
        # 看不到叠加方案（cut_plan）。现在仅 pip/overlay_decor 场景保持自动确认
        # （这两类一次性装饰路径无独立确认卡），主流程「AI 口播增强」必须走
        # cut_plan_confirmation_required 闸门，由 confirm-cut-plan 回放续跑。
        # 实现说明：钩子层只能看到调用参数，无法预知技能是否会规划出 cut_plan，
        # 因此约定——主流程（无 pip/overlay_decor）不注入 gate_mode，技能内部
        # 规划完成后自然挂起等待确认；confirm-cut-plan 回放时显式带
        # gate_mode="auto_confirm" 续跑。
        if (
            inner_name == "talking_head_overlay"
            and "gate_mode" not in inner_args
            and (inner_args.get("pip") or inner_args.get("overlay_decor"))
        ):
            inner_args["gate_mode"] = "auto_confirm"
            logger.debug(
                f"[hook] auto-injected gate_mode=auto_confirm into {inner_name} (pip/decor path)"
            )

        # 口播叠加默认走 AI 叠加增强模式（Whisper→LLM规划→渲染→合成）
        # pip=True 仅在用户显式要求画中画时才启用
        if inner_name == "talking_head_overlay" and "pip" not in inner_args:
            inner_args["pip"] = False
            logger.debug(f"[hook] pip=False (AI overlay mode) for {inner_name}")

        if is_nested:
            args["arguments"] = inner_args
        return name, args


class WorkEditContextHook:
    """作品写入上下文注入：work_edit 必须落到「当前会话绑定的那一条作品」。

    LLM 不该自己编 work_id（它根本看不到数据库 id），因此这里强制用
    run_context 里的真实 work_id 覆盖；`_user_id` 同理从鉴权上下文补齐。

    作品只读边界不在这一层判定——采集/已发布作品的拒绝由
    work_content_edit.save_record_edit(require_status="draft") 兜底，
    钩子只负责把「改哪条」说清楚。
    """

    async def pre_clarify(self, name, args, ctx: DispatchContext):
        return name, args

    async def pre_execute(
        self, name: str, args: dict[str, Any], ctx: DispatchContext
    ) -> tuple[str, dict[str, Any]]:
        if name != "work_edit":
            return name, args

        run_ctx = ctx.run_context or {}
        if not args.get("_user_id"):
            args["_user_id"] = run_ctx.get("user_id")
        work_id = str(run_ctx.get("work_id") or "").strip()
        if work_id:
            if str(args.get("work_id") or "") != work_id:
                logger.debug(f"[hook] work_edit work_id overridden -> {work_id}")
            args["work_id"] = work_id
        elif not args.get("work_id"):
            logger.warning("[hook] work_edit called without work context (no bound work)")
        return name, args


DEFAULT_DISPATCH_HOOKS: tuple[Any, ...] = (
    CreativeImageContextHook(),
    VLAnalyzeContextHook(),
    CreativeVideoContextHook(),
    WorkEditContextHook(),
)
