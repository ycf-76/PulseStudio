"""Chat Agent：用户消息 → 守卫 → ReactorSession (ReAct Loop)。

ReAct 模式：LLM 自己看工具列表、自己决定调什么、自己观察结果、自己决定是否继续。
不再有 TopPlanner 预先决定工具链——LLM 有完全的选择权和信任。
fallback 路径不再做正则意图路由，只做 TaskClassifier 分类 + 诚实告知。
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from app.agents.input_rules import ChatInputRule
from app.agents.registry import AgentRegistry
from app.agents.top_planner import ParsedIntent
from app.core.context_vars import current_db_session
from app.engine.reactor.session import ReactorSession
from app.engine.reactor.task_classifier import TaskType, build_classifier_from_skills
from app.engine.schemas import AgentOutput, LLMProtocol, WorkflowContext

logger = logging.getLogger(__name__)

# 编程错误白名单：这些异常说明代码本身写错了（属性名拼错、类型不匹配、
# 变量未定义），而不是「LLM/网络不可用」。必须直接抛出，不能降级掩盖。
_PROGRAMMING_ERRORS = (AttributeError, TypeError, NameError, NotImplementedError)


@dataclass
class ChatResult:
    status: str
    intent: ParsedIntent | None = None
    output: AgentOutput | None = None
    message: str = ""
    card_draft: dict[str, Any] | None = None


class ChatAgent:
    """编排 Chat 请求：守卫 → ReAct Loop。"""

    def __init__(
        self,
        registry: AgentRegistry | None = None,
        input_rule: ChatInputRule | None = None,
        llm: LLMProtocol | None = None,
        emit_callback: Any = None,
    ) -> None:
        self._registry = registry or AgentRegistry()
        self._input_rule = input_rule or ChatInputRule()
        self._llm = llm
        self._emit_callback = emit_callback

    async def process(
        self,
        message: str,
        session_id: str,
        user_id: str,
        account_id: str = "",
        db: Any = None,
        workflow_id: str | None = None,
        agent_id: str | None = None,
        model_override: str | None = None,
        creation_type: str | None = None,
        extra_system_prompt: str | None = None,
        max_iterations: int | None = None,
        work_context: dict[str, Any] | None = None,
        auto_approve: bool = True,
        image_asset_ids: list[str] | None = None,
        video_asset_ids: list[str] | None = None,
        workspace_root: str | None = None,
    ) -> ChatResult:
        context = WorkflowContext(
            workflow_id=workflow_id or session_id,
            node_id="chat",
            user_id=user_id,
            account_id=account_id,
        )
        # 会话级工作目录（对标 Codex 的 cwd）：只透传绝对路径，不预加载文件。
        # 放进 extra 后随 context_snapshot 落盘，暂停/续跑时不会丢。
        if workspace_root:
            context.extra["workspace_root"] = workspace_root

        if not await self._input_rule.check_pre({"message": message}, context):
            return ChatResult(status="blocked", message="输入未通过安全校验")

        target_agent = agent_id or "chat_agent"
        try:
            session = self._registry.build_session(
                target_agent,
                workflow_id=workflow_id,
                creation_type=creation_type,
                auto_approve=auto_approve,
            )
        except KeyError:
            if target_agent != "chat_agent":
                logger.warning(
                    f"[ChatAgent] agent '{target_agent}' not found, falling back to chat_agent"
                )
                try:
                    session = self._registry.build_session(
                        "chat_agent",
                        workflow_id=workflow_id,
                        creation_type=creation_type,
                        auto_approve=auto_approve,
                    )
                except KeyError:
                    return await self._fallback_intent_route(message, context, workflow_id)
            else:
                return await self._fallback_intent_route(message, context, workflow_id)

        if self._llm is not None:
            session.llm = self._llm
        else:
            # 输入框手动选择的模型 > 「模型配置」页为智能体/全局指定的模型 > AgentDef 自带模型
            override_applied = False
            if model_override:
                try:
                    from app.engine.factory import get_llm_for_model

                    _override_llm = await get_llm_for_model(model_override, user_id=user_id)
                    if _override_llm is not None:
                        session.llm = _override_llm
                        override_applied = True
                        logger.info(f"[ChatAgent] model override applied: {model_override}")
                    else:
                        logger.warning(
                            f"[ChatAgent] model override '{model_override}' unavailable, fallback to assignment"
                        )
                except Exception as e:
                    logger.warning(f"[ChatAgent] apply model override failed: {e}")

            if not override_applied:
                try:
                    from app.engine.factory import get_llm_for_model
                    from app.services.model_assignment import (
                        get_cached_agent_model,
                        get_cached_default_model,
                    )

                    _assigned = get_cached_agent_model(
                        user_id, target_agent
                    ) or get_cached_default_model(user_id)
                    if _assigned:
                        _assigned_llm = await get_llm_for_model(_assigned, user_id=user_id)
                        if _assigned_llm is not None:
                            session.llm = _assigned_llm
                        else:
                            logger.warning(
                                f"[ChatAgent] assigned model '{_assigned}' unavailable, keep session default"
                            )
                except Exception as e:
                    logger.warning(f"[ChatAgent] apply model assignment failed: {e}")

        if max_iterations is not None:
            session.max_iterations = int(max_iterations)

        if session.llm is not None:
            guidance = extra_system_prompt or ""
            return await self._agentic_loop(
                message,
                session_id,
                session,
                context,
                db,
                plan_guidance=guidance,
                image_asset_ids=image_asset_ids,
                video_asset_ids=video_asset_ids,
                workspace_root=workspace_root,
            )

        return await self._fallback_intent_route(message, context, workflow_id)

    async def _generate_session_summary(
        self,
        session_id: str,
        recent_limit: int = 10,
        llm_override: Any = None,
    ) -> str | None:
        _llm = llm_override or self._llm
        if _llm is None:
            try:
                from app.agents.registry import AgentRegistry

                _session = AgentRegistry().build_session("chat_agent")
                _llm = _session.llm
            except Exception:
                pass
        if _llm is None:
            logger.warning("[ChatAgent] no LLM available for summary generation")
            return None
        try:
            from app.services import chat_session

            all_messages = await chat_session.list_messages(session_id, limit=200)
            user_assistant_msgs = [m for m in all_messages if m["role"] in ("user", "assistant")]
            if len(user_assistant_msgs) <= recent_limit:
                return None
            old_msgs = user_assistant_msgs[:-recent_limit]
            history_lines = []
            for m in old_msgs:
                role = "用户" if m["role"] == "user" else "助手"
                content = m["content"][:500]
                history_lines.append(f"{role}：{content}")
            history_text = "\n".join(history_lines)
            if not history_text.strip():
                return None
            summary_prompt = (
                "请将以下对话历史压缩为简洁但完整的摘要。必须保留：\n"
                "1. 用户的核心需求（要写什么主题、什么风格、什么平台）\n"
                "2. 关键决策和修改指令（用户要求改了什么、调整了什么）\n"
                "3. 已产出的文案标题和关键内容\n"
                "4. 用户的偏好和反馈（喜欢什么、不喜欢什么）\n"
                "不要丢失任何重要信息，但可以省略过程细节。\n"
                "摘要长度控制在 300-500 字。"
            )
            result = await _llm.chat(
                [
                    {"role": "system", "content": summary_prompt},
                    {"role": "user", "content": f"对话历史：\n{history_text}"},
                ],
                response_format=None,
            )
            summary = result.get("content", "").strip()
            if summary and len(summary) >= 20:
                return summary
        except Exception as e:
            logger.warning(f"[ChatAgent] session summary generation failed: {e}")
        return None

    @staticmethod
    def _build_side_system_prompt(base_prompt: str, snapshot: dict[str, Any]) -> str:
        """侧会话 system prompt：原提示词 + 主任务快照背景块（PRD FR-B1）。"""
        content = snapshot.get("content") or {}
        lines = [
            f"【侧会话背景·基于主任务第 {content.get('baseline_turn', '?')} 轮快照】",
            f"任务目标：{content.get('goal', '') or '（未提供）'}",
        ]
        if content.get("summary"):
            lines.append(f"关键结论：{content['summary']}")
        msgs = content.get("messages") or []
        if msgs:
            lines.append("[主任务快照对话]")
            for m in msgs:
                role = "用户" if m.get("role") == "user" else "助手"
                lines.append(f"{role}: {m.get('content', '')}")
            lines.append("[快照结束]")
        lines.append(
            "以上是主任务的背景快照（创建侧会话时复制，不随主任务推进更新）。"
            "当前对话是侧会话：探索性问答在此进行，不会自动进入主任务。"
        )
        block = "\n".join(lines)
        return f"{base_prompt}\n\n{block}" if base_prompt else block

    async def _resolve_work_video_path(self, db: Any, user_id: str, work_id: str) -> str | None:
        """取作品本地缓存视频的绝对路径（yt-dlp 已下载的那份）。

        只读查询，任何失败都返回 None——视频路径缺失不阻断文本分析链路。
        artifact:/output: 前缀不是 PCP 记录，直接跳过。
        """
        if not work_id or not user_id or work_id.startswith(("artifact:", "output:")):
            return None
        try:
            from sqlalchemy import select

            from app.db.models import PublishedContentPerformance
            from app.db.session import AsyncSessionLocal

            async with AsyncSessionLocal() as session:
                record = await session.scalar(
                    select(PublishedContentPerformance).where(
                        PublishedContentPerformance.id == work_id,
                        PublishedContentPerformance.user_id == user_id,
                    )
                )
            path = str(getattr(record, "local_video_path", "") or "").strip()
            if not path:
                return None
            from pathlib import Path

            return path if Path(path).exists() else None
        except Exception as exc:
            logger.debug(f"[ChatAgent] resolve work video path skipped: {exc}")
            return None

    async def _agentic_loop(
        self,
        message: str,
        session_id: str,
        session: ReactorSession,
        context: WorkflowContext,
        db: Any,
        plan_guidance: str = "",
        image_asset_ids: list[str] | None = None,
        video_asset_ids: list[str] | None = None,
        workspace_root: str | None = None,
    ) -> ChatResult:
        """LLM 自主决策循环：思考 → 调 Primitive → 观察 → 再思考 → 完成。"""

        token = current_db_session.set(db) if db is not None else None
        try:
            input_data = {"topic": message}

            # 会话级 cwd 随 run context 落进 context_snapshot：
            # ReactorSession 执行时据此绑定 current_workspace_root，
            # 暂停后续跑（confirm / clarify）也能恢复同一个目录。
            _ws_root = workspace_root or context.extra.get("workspace_root")
            if _ws_root:
                input_data["workspace_root"] = _ws_root

            # P0-1 接线：user_id 必须进 run context——session._dispatch 的
            # skill_call 分支从这里取 _user_id 传给 SkillRegistry 技能，
            # 缺失时 multi_image_video 等直接报 "missing user context" 失败
            # （用户实跑复现：缺 _user_id）。context.user_id 来自入口鉴权。
            input_data["user_id"] = context.user_id

            # 自动放行开关随 run context 落进 context_snapshot：Guardian 暂停后
            # 续跑（confirm / clarify / cut_plan）是独立请求，重建 session 时
            # 必须据此恢复用户设置，否则默认 True 会把「需确认」模式切成自动放行。
            input_data["auto_approve"] = bool(session.auto_approve)

            # 会话 id 进 run context：ReactorSession 把 card_draft 落成创作产物
            # 时用它做 source.session_id，同一会话反复出稿只更新同一条产物。
            input_data["chat_session_id"] = session_id

            if image_asset_ids:
                input_data["image_asset_ids"] = image_asset_ids
                logger.debug(
                    f"[ChatAgent._agentic_loop] image_asset_ids injected into context: {image_asset_ids}"
                )

            if video_asset_ids:
                input_data["video_asset_ids"] = video_asset_ids
                logger.debug(
                    f"[ChatAgent._agentic_loop] video_asset_ids injected into context: {video_asset_ids}"
                )

            # 作品上下文：work_id 必须进 run context——work_edit 靠它定位要写
            # 的那一条记录（LLM 看不到数据库 id，由 WorkEditContextHook 强制覆盖）。
            # work_status 用于让钩子/技能侧知道这是草稿还是只读作品。
            _work_ctx = context.extra.get("work_context") or {}
            _work_id = str(_work_ctx.get("workId") or "").strip()
            if _work_id:
                input_data["work_id"] = _work_id
                input_data["work_status"] = str(_work_ctx.get("contentStatus") or "")
                logger.debug(
                    f"[ChatAgent._agentic_loop] work context injected: work_id={_work_id}, "
                    f"status={input_data['work_status']}"
                )
                # 视频作品：把本地缓存视频路径带进 run context，
                # CreativeVideoContextHook 据此注入 video_path（用户未另外上传视频时）。
                if not video_asset_ids:
                    _work_video_path = await self._resolve_work_video_path(
                        db, context.user_id, _work_id
                    )
                    if _work_video_path:
                        input_data["work_video_path"] = _work_video_path
                        logger.info(
                            f"[ChatAgent._agentic_loop] work video path injected: {_work_video_path}"
                        )

            _work_content = ""
            _other_guidance = ""
            if plan_guidance:
                if "【重要】当前用户正在查看以下作品" in plan_guidance:
                    _work_marker = plan_guidance.find("【重要】当前用户正在查看以下作品")
                    _work_content = plan_guidance[_work_marker:]
                    _other_guidance = plan_guidance[:_work_marker].strip()
                else:
                    _other_guidance = plan_guidance

            if _work_content:
                input_data["work_context_prompt"] = _work_content

            if _other_guidance:
                input_data["topic"] = f"用户请求：{message}\n\n执行计划与纪律：\n{_other_guidance}"

            if db is not None:
                # D18 创作者画像软注入：LLM 层（自由回复/追问也带画像调性）。
                # 读失败/无画像静默跳过，不拦 chat；走 profile_service TTL 缓存。
                try:
                    from app.services.profile_service import get_profile_cached

                    _profile = await get_profile_cached(db, context.user_id)
                    if _profile is not None:
                        input_data["profile_prompt"] = (
                            "[创作者画像 — 你作为该创作者 AI 搭档的身份与约束]\n"
                            + _profile.to_prompt_context()
                        )
                        logger.debug(
                            f"[ChatAgent] profile injected: "
                            f"domain={_profile.primary_domain.value}"
                        )
                        # AI 人格注入：基于用户选择的预设或自定义人格
                        # 改变 AI 自身的说话风格（自我介绍、闲聊、评价等）
                        try:
                            from app.agents.prompts.personalities import build_personality_prompt

                            _personality_block = build_personality_prompt(
                                ai_personality=_profile.ai_personality,
                                ai_personality_custom=_profile.ai_personality_custom,
                            )
                            if _personality_block:
                                input_data["personality_prompt"] = _personality_block
                                logger.debug(
                                    f"[ChatAgent] personality injected: "
                                    f"personality={_profile.ai_personality}"
                                )
                        except Exception as _personality_err:
                            logger.debug(
                                f"[ChatAgent] personality inject skipped: {_personality_err}"
                            )
                except Exception as _profile_err:
                    logger.debug(f"[ChatAgent] profile soft-inject skipped: {_profile_err}")

                try:
                    from app.engine.governance.context_manager import ContextManager
                    from app.engine.governance.token_budget import TokenBudgetConfig
                    from app.services import chat_session
                    from app.services import side_session as _side_svc

                    # 侧会话判定：有快照即为侧会话（快照与侧会话同事务创建，一一对应）
                    _side_snapshot = await _side_svc.get_snapshot(session_id)
                    _is_side = _side_snapshot is not None
                    # 侧会话写拦截接线（PRD G6）：Reactor 据此启用 side guard
                    session.side_session_id = session_id if _is_side else None

                    _cm = ContextManager(
                        session_id=session_id,
                        token_budget_config=TokenBudgetConfig(),
                    )
                    creative_state = None
                    if not _is_side:
                        creative_state = await chat_session.get_creative_state(session_id)
                    if creative_state and "world_state" in creative_state:
                        _cm = ContextManager.from_snapshot(
                            session_id,
                            creative_state,
                            token_budget_config=TokenBudgetConfig(),
                        )
                    elif creative_state:
                        _legacy_cr = creative_state.get("copywrite_result")
                        if _legacy_cr and isinstance(_legacy_cr, dict):
                            _cm.world_state.copywrite.update(_legacy_cr)
                        _legacy_cd = creative_state.get("card_draft")
                        if _legacy_cd and isinstance(_legacy_cd, dict):
                            _cm.world_state.card_draft.update(_legacy_cd)

                    all_messages = await chat_session.list_messages(session_id, limit=200)
                    summary_text = await chat_session.get_session_summary(session_id)

                    if summary_text:
                        _cm.history.add_system_message(
                            f"[早期对话摘要]\n{summary_text}\n[摘要结束]"
                        )

                    for m in all_messages:
                        if m["role"] == "user":
                            _cm.history.add_user_message(m["content"])
                        elif m["role"] == "assistant":
                            _cm.history.add_assistant_message(m["content"])

                    _side_system_prompt = session.system_prompt
                    if _is_side and _side_snapshot:
                        _side_system_prompt = self._build_side_system_prompt(
                            session.system_prompt, _side_snapshot
                        )
                    _cm.set_system_prompt(_side_system_prompt)
                    fragments, rollout_item = _cm.update_world_state()

                    if fragments:
                        logger.info(f"[ChatAgent] WorldState diff injected: {len(fragments)} chars")

                    _cr = creative_state.get("copywrite_result") if creative_state else None
                    if _cr:
                        _ai_copywrite = (
                            f"【当前创作文案（以此为准，不要用原始作品正文）】\n"
                            f"标题: {_cr.get('title', '')}\n"
                            f"正文: {_cr.get('content', '')}\n"
                            f"标签: {', '.join(_cr.get('tags', []))}"
                        )
                        _existing_work_prompt = input_data.get("work_context_prompt", "")
                        input_data["work_context_prompt"] = (
                            f"{_existing_work_prompt}\n\n{_ai_copywrite}".strip()
                            if _existing_work_prompt
                            else _ai_copywrite
                        )

                    input_data["_context_manager"] = _cm

                    last_sid = await chat_session.get_last_session_id(session_id)
                    if last_sid:
                        input_data["last_session_id"] = last_sid
                except Exception as _ctx_err:
                    logger.warning(f"[ChatAgent._agentic_loop] context injection error: {_ctx_err}")

            _cm = input_data.pop("_context_manager", None)

            start = time.time()
            result = await session.run(
                input_data.get("topic", message),
                context=input_data,
                context_manager=_cm,
            )
            duration_ms = int((time.time() - start) * 1000)

            output_data = result or {}
            output_data["duration_ms"] = duration_ms

            if _cm is not None:
                try:
                    from app.services import chat_session as _cs

                    new_snapshot = _cm.take_snapshot()
                    await _cs.update_creative_state(session_id, new_snapshot)
                    logger.debug(
                        f"[ChatAgent] WorldState snapshot persisted for session={session_id}"
                    )
                except Exception as _snap_err:
                    logger.warning(f"[ChatAgent] WorldState persist failed: {_snap_err}")

            if output_data.get("_awaiting_clarification"):
                return ChatResult(
                    status="awaiting_clarification",
                    message=output_data.get("_clarification_prompt", "需要确认创作偏好"),
                    output=AgentOutput(output=output_data, summary=""),
                )
            if output_data.get("_awaiting_confirmation"):
                return ChatResult(
                    status="awaiting_confirmation",
                    message=output_data.get("_confirmation_prompt", "需要确认操作"),
                )
            if output_data.get("_awaiting_storyboard_confirmation"):
                return ChatResult(
                    status="awaiting_storyboard_confirmation",
                    message=output_data.get("title", "分镜草稿已生成，请确认后再生成视频"),
                    output=AgentOutput(output=output_data, summary=""),
                )
            if output_data.get("_awaiting_cut_plan_confirmation"):
                return ChatResult(
                    status="awaiting_cut_plan_confirmation",
                    message=output_data.get("message", "叠加方案已规划，请确认后再渲染视频"),
                    output=AgentOutput(output=output_data, summary=""),
                )
            if output_data.get("_awaiting_guardian_confirmation"):
                return ChatResult(
                    status="awaiting_guardian_confirmation",
                    message=output_data.get("confirmation_prompt", "此操作需要确认"),
                    output=AgentOutput(output=output_data, summary=""),
                )
            # 侧会话写副作用暂停：独立状态，路由层据此落库 awaiting_side_write_confirmation
            # 并持久化 loop_state_path，刷新/重连后确认弹窗可恢复（review #3）。
            if output_data.get("_awaiting_side_write_confirmation"):
                _sw = output_data.get("side_write") or {}
                return ChatResult(
                    status="awaiting_side_write_confirmation",
                    message=str(_sw.get("description") or "侧会话请求写操作，等待确认"),
                    output=AgentOutput(output=output_data, summary=""),
                )
            output = AgentOutput(
                output=output_data,
                summary=output_data.get("summary", ""),
                duration_ms=duration_ms,
            )
            return ChatResult(status="agent_output", output=output)

        except _PROGRAMMING_ERRORS as e:
            # 编程错误（属性名拼错 / 类型不匹配 / 变量未定义）不属于「LLM 不可用」。
            # 静默降级会把真实缺陷伪装成「能用」：2026-11 实测中 FakeHarness 缺
            # auto_approve 触发 AttributeError，被这里吞掉后降级到 fallback，
            # 用例拿到 status="chat" 而非 "agent_output"，缺陷被掩盖。
            # 因此直接抛出，由路由层落 workflow_error 事件暴露给前端与日志。
            logger.error(
                f"[ChatAgent] agentic loop raised programming error, NOT degrading: "
                f"{type(e).__name__}: {e}",
                exc_info=True,
            )
            raise
        except Exception as e:
            # 运行时错误（LLM 超时 / 网络失败 / 第三方返回异常）才允许降级。
            logger.error(
                f"[ChatAgent] agentic loop failed, falling back to intent route: {e}",
                exc_info=True,
            )
            return await self._fallback_intent_route(message, context, None)
        finally:
            if token is not None:
                current_db_session.reset(token)

    async def _fallback_intent_route(
        self,
        message: str,
        context: WorkflowContext,
        workflow_id: str | None = None,
    ) -> ChatResult:
        """LLM 不可用时的 fallback：TaskClassifier 分类 + 诚实告知。

        不再做正则意图路由（_keyword_fallback）——正则写死了意图→工具映射，
        新增工具必须改正则，且和 TaskClassifier 触发词重叠维护。
        fallback 路径没有 LLM 就无法执行工具，诚实告知比假装路由更好。
        """
        classifier = build_classifier_from_skills(None)
        task_type = classifier.classify(message)

        if task_type == TaskType.PURE_QA:
            return ChatResult(
                status="chat",
                message="当前 AI 服务暂时不可用，请稍后重试。",
            )

        if task_type == TaskType.OPERATION:
            return ChatResult(
                status="chat",
                message=f"「{message[:30]}」需要 AI 服务支持，当前暂时不可用，请稍后重试。",
            )

        return ChatResult(
            status="chat",
            message=f"创作类任务需要 AI 服务支持，当前暂时不可用，请稍后重试。",
        )
