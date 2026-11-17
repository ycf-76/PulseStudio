"""Reactor session: lightweight ReAct loop + thin primitives.

This is the sole orchestrator. The LLM decides which primitives to call,
in what order, based on the user's intent.

Key design:
- No AgentHarness dependency (no governance/recovery/compaction overhead)
- No LangGraph dependency
- Direct LLM calls with simple retry
- Primitives are dispatched via permission gate
- Observations feed back into the message chain for the next LLM call
- Full SSE event streaming: thought / tool_call_start / tool_call_end / progress / final
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import re
import time
from pathlib import Path
from typing import Any

from app.core.context_vars import current_workspace_root
from app.engine.protocol.dispatch_hooks import DispatchContext, SkillCallUnwrapHook
from app.engine.protocol.events import EndedReason
from app.engine.protocol.transport import InProcessTransport
from app.engine.reactor.primitives import PRIMITIVES_BY_NAME, Primitive
from app.engine.reactor.prompt import (
    build_design_context_block,
    build_skill_doc,
    get_reactor_system_prompt,
)
from app.engine.reactor.task_classifier import (
    TaskClassifier,
    build_classifier_from_skills,
)
from app.engine.schemas import Permission

logger = logging.getLogger(__name__)

# 2026-11-02：12 → 40。视频任务（探测→静音检测→解析→切割→拼接→叠加→导出→
# 验证）动辄 20+ 步，12 步必爆 _loop_truncated；子 Agent 砍半后 20 步（原主循环
# 水平），文本类任务不受影响（只放宽截断上限，不改任何放行语义）。
_DEFAULT_MAX_ITER = 40
_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 1.0
_OBSERVATION_MAX_LEN = 4000


def _paused_reason(reason: str) -> bool:
    """该终止原因是否为「暂停等待用户」（产物不透传，留给续跑）。"""
    return reason in (
        EndedReason.CLARIFICATION_PAUSED.value,
        EndedReason.GUARDIAN_PAUSED.value,
        EndedReason.STORYBOARD_PAUSED.value,
        EndedReason.CUT_PLAN_PAUSED.value,
        EndedReason.SIDE_WRITE_PAUSED.value,
    )


_DISPATCH_TIMEOUT_SECONDS = 120
_MESSAGES_MAX_LEN = 60
# P1 目录路由：技能目录完整条目上限（业界经验线 ~40-50 后选择精度下降；
# 未选中技能以「其他可用技能」列名兜底，不损失可调用性）
_SKILL_CATALOG_MAX_TOOLS = 40


class ReactorSession:
    """Reactor top-level session with full SSE event streaming.

    Usage:
        session = ReactorSession(allowed_permissions={Permission.FILE_READ, ...})
        result = await session.run("帮我做一期手冲咖啡的内容")
    """

    def __init__(
        self,
        llm: Any | None = None,
        primitives: list[Primitive] | None = None,
        allowed_permissions: set[Permission] | None = None,
        max_iterations: int = _DEFAULT_MAX_ITER,
        system_prompt: str | None = None,
        skills: list[Any] | None = None,
        observer: Any | None = None,
        sse_bus: Any | None = None,
        workflow_id: str = "",
        auto_approve: bool = True,
        dispatch_hooks: list[Any] | None = None,
        transport: Any | None = None,
        max_token_budget: int = 200_000,
        collab_mode: str = "proactive",
        creation_type: str | None = None,
        system_prompt_prefix: str | None = None,
    ) -> None:
        self.llm = llm
        self.primitives = {p.name: p for p in (primitives or list(PRIMITIVES_BY_NAME.values()))}
        # 二期契约带②：执行只走 transport（进程内实现与旧执行段等价；
        # 三期外置服务换 HttpTransport，内核零改动）
        self._transport = transport or InProcessTransport(self.primitives)
        # 二期注入口：协议级钩子内置；业务钩子由工厂/路由注入（内核不认识业务工具名）
        self._dispatch_hooks = [
            SkillCallUnwrapHook(),
            *(dispatch_hooks or []),
        ]
        self.allowed_permissions = allowed_permissions or set()
        self.max_iterations = max_iterations
        # P0-1：_skills 必须从构造期注入——system_prompt 在这里生成，
        # 事后赋值（AgentRegistry.build_session 旧写法）会让「可用技能」
        # 目录永远缺节，LLM 看不到 skill_call 与技能清单，视频链路断在第 0 步。
        self._skills: list[Any] = list(skills) if skills else []
        self._task_classifier: TaskClassifier = build_classifier_from_skills(self._skills)
        self._creation_type = creation_type
        # P1 路由哨兵：显式传入的 system_prompt 不覆盖；首轮 run() 按真实
        # user_intent 做触发词路由并重建目录（构建期无用户消息可用）
        self._system_prompt_explicit = system_prompt is not None
        self._prompt_routed = False
        # 人格层：AgentDef.persona 注入在系统提示最前，技能路由重建时同样保留
        self._system_prompt_prefix = (system_prompt_prefix or "").strip()
        # P2-2 二层披露哨兵：每个技能的 skill_doc 只随首次调用返回一次
        self._skill_doc_sent: set[str] = set()
        self.system_prompt = system_prompt or get_reactor_system_prompt(
            skills=self._skills,
            user_message=getattr(self, "_initial_message", ""),
            creation_type=self._creation_type,
        )
        self.system_prompt = self._with_persona(self.system_prompt)
        self.observer = observer
        self.sse_bus = sse_bus
        self.workflow_id = workflow_id
        self.auto_approve = auto_approve
        # skill_call 一次性确认（2026-11-05）：需确认模式（auto_approve=False）下
        # 每个 skill_call 都弹 Guardian 卡，一轮视频任务连撞 N 次闸门（15:47/15:51
        # 日志实锤）。用户批准过一次后，本 turn 内后续 low/medium 风险的 skill_call
        # 直接放行；high 风险仍弹卡。由 confirm-guardian 续跑路径置 True。
        self.skill_call_once_confirmed = False
        # 侧会话（Side Session）写拦截：side_session_id 非空时启用（PRD G6/R5）。
        # _side_write_allow_once：用户批准后的逐次放行签名（call_signature 格式），只放行一次。
        self.side_session_id: str | None = None
        self._side_write_allow_once: str | None = None
        from app.engine.governance.guardian import Guardian

        self._guardian = Guardian()
        self.turn_id: str | None = None
        self._token_count = 0
        self._max_tokens = max_token_budget
        self._steps: list[dict[str, Any]] = []
        from app.engine.protocol.clarify_policy import CLARIFY_TRIGGER_NODE_TYPES

        self._CLARIFY_TRIGGER_NODE_TYPES = CLARIFY_TRIGGER_NODE_TYPES

        # 协作基础设施：AgentManager + InterAgentBus + ConcurrencyPool
        # 按需初始化（首次 spawn_agent 时 lazy create），避免无 collab 场景的开销
        self._collab_tools: Any | None = None
        self._collab_mode = collab_mode
        self._root_agent_id: str | None = None

    def _with_persona(self, base_prompt: str) -> str:
        """把 AgentDef.persona 拼到系统提示最前（人格层 → 技能目录层）。"""
        if not self._system_prompt_prefix:
            return base_prompt
        return f"{self._system_prompt_prefix}\n\n{base_prompt}"

    async def _emit_sse(self, event_type: str, payload: dict[str, Any]) -> None:
        if not self.sse_bus or not self.workflow_id:
            return
        try:
            await self.sse_bus.publish(self.workflow_id, event_type, payload, turn_id=self.turn_id)
        except Exception:
            pass

    async def _persist_card_draft(self, card_draft: dict[str, Any]) -> str:
        """把 card_draft 落成创作产物，返回 artifact_id（失败返回空串）。

        此前 card_draft 只 emit SSE 给前端写本地草稿：刷新即丢、我的空间
        也扫不到，这是「开启新创作后的作品不同步到我的空间」的根因。
        落盘失败只降级记日志，不能连累这一轮创作本身。
        """
        try:
            from app.services.creative_artifact import save_card_draft_artifact

            _ctx = getattr(self, "_run_context", {}) or {}
            _user_id = str(_ctx.get("user_id") or "")
            if not _user_id:
                return ""
            return await save_card_draft_artifact(
                user_id=_user_id,
                card_draft=card_draft,
                session_id=str(_ctx.get("chat_session_id") or ""),
                work_id=str(_ctx.get("work_id") or ""),
            )
        except Exception as exc:  # noqa: BLE001 - 产物落盘失败必须降级
            logger.warning("[reactor] card_draft 落盘失败: %s", exc)
            return ""

    async def _emit_observer(self, method: str, *args: Any, **kwargs: Any) -> None:
        if not self.observer:
            return
        try:
            fn = getattr(self.observer, method, None)
            if fn:
                await fn(*args, **kwargs)
        except Exception:
            pass

    async def run(
        self,
        user_intent: str,
        context: dict[str, Any] | None = None,
        context_manager: Any | None = None,
        seed_messages: list[dict[str, Any]] | None = None,
        emit_workflow_completed: bool = True,
    ) -> dict[str, Any]:
        """Execute user intent through a ReAct loop with full event streaming.

        Args:
            user_intent: User's intent/message.
            context: Additional context dict.
            context_manager: External ContextManager (WorldState + ConversationHistory).
                If provided, the session reuses its history (跨轮存活) and
                auto-ingests tool outputs into WorldState.
                If None, builds initial messages from scratch (旧行为，向后兼容).
            seed_messages: 暂停后续跑用的完整历史（loop_state["messages"] 原样回灌）。
                提供时直接沿用这份 messages 并追加 user_intent，不再重建。
                否则续跑会丢掉"上一轮已经调过什么工具、报了什么错"，
                模型只能凭最后一条 observation 猜 → 大概率重发同一条命令
                → 再次撞 Guardian → 二次暂停 → 死循环。
            emit_workflow_completed: 是否由 run() 内部发射 workflow_completed 终态事件。
                2026-11-04 实锤（大哥"搜今日科技热搜"轮）：SSE 总线实时订阅收到
                第一个终态事件就关闭流（sse_bus.subscribe_stream L257）。恢复路径
                （/confirm-guardian 等）里正文只由调用方在 run() 返回后落库并随
                agent_loop_done 发布——run() 内部先发的 workflow_completed 会把流
                关掉，agent_loop_done 无人接收，用户看到链路跑一半没正文（终稿
                只落在库里）。恢复路径应传 False，让带正文的 agent_loop_done
                当唯一终态。
        """
        if seed_messages:
            # 原样沿用暂停前的对话（含已路由好的 system 消息，不重新路由），
            # 只把本轮 observation 以 loop 原生约定（role=user + [observation]）追加。
            messages = [m for m in seed_messages if isinstance(m, dict)]
            if not any(m.get("role") == "system" for m in messages):
                messages.insert(0, {"role": "system", "content": self.system_prompt})
            # user_intent 为空 = 续跑方已把 observation 追加进 seed_messages 里了，
            # 再追加一次会让同一句 observation 出现两遍。
            if user_intent:
                messages.append({"role": "user", "content": user_intent})
            self._context_manager = context_manager
        elif context_manager is not None:
            # 多轮会话走 ContextManager 时不会经过 _build_initial_messages，
            # 历史上会因此一直沿用构造期的全量技能目录。这里补一次路由，并同步
            # ContextManager 持有的 system prompt，避免每轮都吃 30K token。
            self._route_system_prompt(user_intent)
            try:
                context_manager.set_system_prompt(self.system_prompt)
            except Exception:
                pass
            messages = context_manager.for_prompt()
            # 人格 + 画像注入：context_manager 分支不走 _build_initial_messages，
            # 必须在这里手动提取 personality_prompt / profile_prompt 作为独立 system message。
            if context:
                _personality = context.get("personality_prompt")
                if _personality and isinstance(_personality, str):
                    messages.insert(1, {"role": "system", "content": _personality})
                _profile = context.get("profile_prompt")
                if _profile and isinstance(_profile, str):
                    messages.insert(1, {"role": "system", "content": _profile})
                # 作品上下文注入（2026-11-04 实锤修复）：chat_agent 把 work_context
                # 格式化成 work_context_prompt 传进来，但这个分支此前只认
                # personality/profile 两个 key，作品上下文被静默丢弃 → LLM 看不到
                # "用户正在查看的作品"，转头去翻工作区文件、拿旧卡片文案当分析对象
                # （app.log 17:17:04 那轮 content_postmortem 分析错内容的根因）。
                # 与 personality 同待遇：独立 system 消息，不走 JSON 截断。
                _work_prompt = context.get("work_context_prompt")
                if _work_prompt and isinstance(_work_prompt, str):
                    messages.insert(1, {"role": "system", "content": _work_prompt})
            # 去重闸门：chat_agent 在调 run() 之前已把本轮用户消息写进 DB，
            # ContextManager 从 DB 重建历史时这条已在末尾；再无脑 append 会让
            # 同一句话在 prompt 里出现两遍（E2E 实测：追问「我上一条消息问的是
            # 什么」时，模型把当前这句当成上一句原样复述）。
            _last = next((m for m in reversed(messages) if m.get("role") != "system"), None)
            _already_present = (
                _last is not None
                and _last.get("role") == "user"
                and _last.get("content") == user_intent
            )
            if not _already_present:
                messages.append({"role": "user", "content": user_intent})
            self._context_manager = context_manager
        else:
            messages = self._build_initial_messages(user_intent, context)
            self._context_manager = None
        self._run_context = context or {}
        # 会话级 cwd 绑定（对标 Codex 的 <cwd>）：从 run context 取绝对路径，
        # 供 file_read / file_write / file_edit / glob / grep / bash 解析相对路径。
        # 不 reset——每个 HTTP 请求 / 后台续跑都是独立 asyncio task，contextvar
        # 天然隔离；同一会话内 cwd 不变，整轮沿用同一根目录。
        _ws_root = self._run_context.get("workspace_root")
        if _ws_root:
            current_workspace_root.set(str(_ws_root))
        self._clarification_break = False
        # 工具产物累积（产物透传，见工具循环内注释）
        self._observed_artifacts: dict[str, Any] = {}
        # Receipt Ledger：每次工具执行后自动记录一条不可伪造的回执，
        # Output Gate 据此校验 final output 中的产物声明。
        self._receipt_ledger: list[dict[str, Any]] = []
        total_token_usage = 0
        last_thought = ""
        final_output: dict[str, Any] | None = None
        iteration = 0
        # 一期契约：每个出口必须标注终止原因（docs/内核-能力分层架构方案.md §3.2）
        ended_reason = ""

        # Step 0: pre-flight clarification check
        # 三层澄清体系（对标 Claude Code auto mode）：
        #   Layer 1: LLM 自决策——读 AGENTS.md 澄清纪律，拿不准时调 ask_user_question 工具
        #   Layer 2: 分类器兜底——LLM 忘了问但参数确实缺失时补弹
        #   Layer 3: 用户显式规则——permissions.ask 中的技能无条件弹
        # pre-flight 只做 Layer 3（用户显式规则）检查；
        # 需要先从 user_intent 解析出目标技能名，再查 GuardianPolicy.needs_clarification。
        # Layer 1/2 在循环内由 LLM 和 _classifier_fallback_check 负责。
        _intent_skill = self._resolve_intent_skill(user_intent)
        _pre_clarification = None
        if _intent_skill is not None:
            _pre_clarification = self._check_user_ask_rules(getattr(_intent_skill, "name", ""))
        if _pre_clarification is not None:
            from app.engine.reactor.loop_state import save_loop_state

            loop_state: dict[str, Any] = {
                "messages": messages,
                "iteration": 0,
                "context_snapshot": self._run_context,
                "pending_skill_call": None,
                "clarified_answers": {},
            }
            _loop_state_path = save_loop_state(loop_state)
            _pre_clarification["_loop_state_path"] = _loop_state_path

            await self._emit_sse(
                "agent_clarification_required",
                {
                    "node_id": "reactor",
                    "iteration": 0,
                    "clarification_prompt": _pre_clarification.get("_clarification_prompt", ""),
                    "clarification_skill": _pre_clarification.get("_clarification_skill", ""),
                    "clarification_batches": _pre_clarification.get("_clarification_batches", []),
                    "loop_state_path": _loop_state_path,
                },
            )

            logger.info(
                f"[reactor] pre-flight clarification triggered for intent={user_intent[:60]!r} "
                f"(skill={_pre_clarification.get('_clarification_skill')})"
            )
            final_output = _pre_clarification
            self._clarification_break = True

        if self._clarification_break:
            if not isinstance(final_output, dict):
                final_output = {}
            final_output.setdefault("_token_usage", 0)
            final_output.setdefault("_iterations", 0)
            final_output.setdefault("_last_thought", "")
            final_output["_steps"] = []
            # 一期契约：pre-flight 澄清暂停也是一次"终止"，必须带原因
            final_output["ended_reason"] = EndedReason.CLARIFICATION_PAUSED.value
            return final_output

        await self._emit_sse(
            "workflow_started",
            {
                "intent": user_intent,
                "max_iterations": self.max_iterations,
                "primitives": list(self.primitives.keys()),
            },
        )
        await self._emit_sse(
            "node_started",
            {
                "node_id": "reactor",
                "node_type": "reactor_react",
                "intent": user_intent,
            },
        )

        _nudge_count = 0
        _MAX_NUDGE = 2
        # 重复调用刹车：按「单次调用签名」计数，而不是整批签名。
        # 整批签名只认「相邻两批完全相同」，LLM 只要在批里夹带一个新调用
        # （如 [A,B] → [A,C]）计数器就归零，同一条失败调用可以无限刷。
        # 这里改为逐签名记录「连续失败了多少个 step」，任一签名连败到
        # 阈值即刹车。
        #
        # 只累计「上一次同签名调用没有取得进展」的步数：轮询类调用的语义是
        # 「同参数连调直到状态就绪」，每次返回 ok=true 属正常重试，不该被
        # 刹车误杀；刹车要打断的是「无效重试」，不是「有效重试」。空探针
        # 返回 ok=true 但技能并未执行，属于无进展，照常累计。
        _MAX_REPEAT_STEPS = 3
        _repeat_counts: dict[str, int] = {}
        _repeat_args: dict[str, dict[str, Any]] = {}
        _last_call_ok: dict[str, bool] = {}
        _last_call_error: dict[str, str] = {}
        _fake_final_nudge = 0
        _MAX_FAKE_FINAL_NUDGE = 2
        _any_tool_executed = False
        _is_pure_qa = False
        # 回合起始时间：媒体产物收割的「本轮新增」判据（mtime >= 此值）
        _turn_started_at = time.time()

        for iteration in range(1, self.max_iterations + 1):
            step_record: dict[str, Any] = {
                "iteration": iteration,
                "thought": "",
                "tool_calls": [],
                "observations": [],
                "token_usage": 0,
                "duration_ms": 0,
            }
            step_start = time.monotonic()

            logger.debug(f"[reactor] iteration {iteration}/{self.max_iterations}")

            await self._emit_sse(
                "progress_update",
                {
                    "node_id": "reactor",
                    "current": iteration,
                    "total": self.max_iterations,
                    "label": f"第 {iteration} 步推理",
                },
            )
            await self._emit_observer(
                "emit_progress",
                "reactor",
                iteration,
                self.max_iterations,
                f"ReAct iter {iteration}",
            )

            # emit_delta=False：本循环的 LLM 输出恒为 ReAct 协议 JSON
            # （prompt.py L64-77 规定 {"thought", "tool_calls", "final", "output"}），
            # 不是用户正文。协议原文走 agent_message_delta 直灌前端正文是
            # 「聊天里输出裸 JSON」的根因——thought 已有 decision_made 出口、
            # reasoning 已有 reasoning_summary_text_delta 出口、终态人话由
            # POST 响应 _humanize_output/formatAgentOutput 承担，此处零信息损失。
            # 注：文案类技能的合法散文流走 agent_thinking（builder.py L357），
            # 不经本通道，不受影响。
            content, token_usage, reasoning = await self._call_llm(messages, emit_delta=False)
            total_token_usage += token_usage or 0
            self._token_count += token_usage or 0
            step_record["token_usage"] = token_usage or 0
            _llm_call_failed_this_iter = content.startswith('{"thought": "LLM call failed:')

            if self._token_count >= self._max_tokens:
                logger.warning(
                    f"[reactor] token budget exhausted: {self._token_count}/{self._max_tokens}, "
                    f"terminating loop at iteration {iteration}"
                )
                final_output = self._extract_output_from_messages(messages)
                final_output["_token_budget_exhausted"] = True
                final_output["_token_usage"] = total_token_usage
                ended_reason = EndedReason.TOKEN_BUDGET.value
                step_record["final"] = True
                step_record["output"] = final_output
                break

            if reasoning:
                await self._emit_sse(
                    "reasoning_completed",
                    {
                        "node_id": "reactor",
                        "iteration": iteration,
                        "summary_parts": [reasoning],
                    },
                )

            parsed = self._parse_step(content)
            thought = parsed.get("thought") or ""
            tool_calls = parsed.get("tool_calls") or []
            if not isinstance(tool_calls, list):
                logger.warning(
                    f"[reactor] tool_calls is {type(tool_calls).__name__}, coercing to []"
                )
                tool_calls = []
            is_final = parsed.get("final", False)

            if not tool_calls and not is_final:
                logger.warning(
                    f"[reactor] iter={iteration} LLM output not valid ReAct JSON. "
                    f"Raw content (first 500 chars): {content[:500]!r}"
                )
                # 取证：完整原文落盘，便于定位协议瑕疵模式
                try:
                    fail_log = Path("logs/reactor_parse_failures.log")
                    fail_log.parent.mkdir(parents=True, exist_ok=True)
                    with fail_log.open("a", encoding="utf-8") as fh:
                        fh.write(
                            f"=== {time.strftime('%Y-%m-%d %H:%M:%S')} iter={iteration} ===\n{content}\n"
                        )
                except OSError:
                    pass

            step_record["thought"] = thought

            logger.info(
                f"[reactor] iter={iteration} thought={thought[:120]} "
                f"tool_calls={[tc.get('name') for tc in tool_calls]} final={is_final}"
            )

            if thought:
                last_thought = thought
                await self._emit_sse(
                    "decision_made",
                    {
                        "node_id": "reactor",
                        "iteration": iteration,
                        "decision": thought,
                    },
                )
                await self._emit_observer("emit_decision", "reactor", thought)

            if is_final:
                # 空转检测（anti-hallucination）：LLM 声称 final 但整个会话
                # 没有执行过任何工具、也没有累积任何产物——这是模型幻觉，
                # 它在"假装做完"。典型表现：输出"封面做好了"但从未调
                # skill_call 等工具，_observed_artifacts 为空。
                # 策略：nudge LLM 回去真正调用工具，最多 nudge 2 次；
                # 超限后接受（避免死循环），但标记 _fake_final 供前端/日志诊断。
                #
                # 2026-10 重构：信任 LLM 的 final 判断——如果 LLM 说做完了
                # 且没调过工具，那就是纯问答/闲聊，不需要正则去读 thought
                # 判断。LLM 比任何硬编码词表都更懂自己要不要调工具。
                _has_artifacts = bool(self._observed_artifacts)
                if not _any_tool_executed and not _has_artifacts:
                    _is_pure_qa = True
                if (
                    not _any_tool_executed
                    and not _has_artifacts
                    and not _llm_call_failed_this_iter
                    and _fake_final_nudge < _MAX_FAKE_FINAL_NUDGE
                    and not _is_pure_qa
                ):
                    _fake_final_nudge += 1
                    logger.warning(
                        f"[reactor] iter={iteration} FAKE FINAL detected: "
                        f"LLM claims final:true but no tool was ever executed and no artifacts accumulated. "
                        f"Nudging ({_fake_final_nudge}/{_MAX_FAKE_FINAL_NUDGE})"
                    )
                    messages.append({"role": "assistant", "content": content})
                    _nudge_msg = "[system] 你声称任务已完成，但实际没有调用任何工具、也没有产出任何产物（card_draft/asset_ids 等）。"
                    if _fake_final_nudge == 1:
                        _nudge_msg += (
                            "\n请判断任务类型："
                            "\n1. 纯问答/闲聊（不需要工具）→ 在 thought 中说明「这是纯问答/不需要调用工具」，然后 final:true"
                            "\n2. 传输/操作类（发邮件/下载/分享等）→ 调对应工具（如 email_send），不需要先创作内容"
                            "\n3. 创作类（做卡片/写文案等）→ 选择创作工具执行，输出格式："
                            '\n   {"thought": "你的思考", "tool_calls": [{"name": "工具名", "arguments": {...}}], "final": false}'
                        )
                    else:
                        _nudge_msg += (
                            "这是幻觉——没有工具调用就没有产出。"
                            "请立即选择合适的工具执行任务，不要再说'做好了'。输出格式：\n"
                            '{"thought": "你的思考", "tool_calls": [{"name": "工具名", "arguments": {...}}], "final": false}'
                        )
                    messages.append(
                        {
                            "role": "user",
                            "content": _nudge_msg,
                        }
                    )
                    step_record["observations"].append(
                        "⚠️ 空转检测：LLM 声称完成但无实际产出，已强制继续"
                    )
                    await self._emit_sse(
                        "progress_update",
                        {
                            "node_id": "reactor",
                            "current": iteration,
                            "total": self.max_iterations,
                            "label": "⚠️ 检测到空转，强制继续执行",
                        },
                    )
                    continue

                final_output = parsed.get("output") or self._extract_output_from_messages(messages)
                if _fake_final_nudge > 0 and not _any_tool_executed and not _has_artifacts:
                    final_output["_fake_final"] = True
                    final_output["_fake_final_nudge_used"] = _fake_final_nudge
                    logger.warning(
                        f"[reactor] fake-final nudge limit reached ({_fake_final_nudge}), "
                        f"accepting output but marking _fake_final=True"
                    )
                ended_reason = EndedReason.COMPLETED.value
                step_record["final"] = True
                step_record["output"] = final_output
                break

            if not tool_calls:
                # LLM 输出了非结构化文本（非 JSON 或 JSON 无 tool_calls 且非 final）。
                # 旧行为：直接 break → 循环提前终止（用户报告"AI只说了一句话就不动了"）。
                # 新行为：nudge LLM 继续按 ReAct JSON 格式推理，最多 nudge 2 次。
                # 超过 nudge 上限后接受当前输出作为最终结果。
                #
                # 2026-10 重构：信任 LLM——如果 LLM 输出了足够长的自然语言
                # 且没调过工具，那就是纯问答直出，不需要 TaskClassifier
                # 用 trigger_words 判断。LLM 自己决定要不要调工具，
                # 正则不该替它做这个判断。
                _is_natural_language_qa = False
                if content and len(content.strip()) > 20 and not _any_tool_executed:
                    if "｜｜DSML｜｜" in content:
                        # 协议残片（模型吐了原生 tool-call token 但外壳残缺）
                        # 不是人话，绝不能当纯问答直出给用户，交给 nudge 重试。
                        logger.warning(
                            f"[reactor] iter={iteration} DSML residue without parseable "
                            f"tool_call, refusing to treat as QA: {content[:200]!r}"
                        )
                    elif self._looks_like_react_protocol(content):
                        # 2026-11-04 实锤：ReAct 协议 JSON 残片（tool_calls 数组缺 `]` 等）
                        # 三段解析都没救回时，也不是人话——原样当纯问答直出就是
                        # "聊天里输出裸 JSON"的直接根因（用户 19:39 截图）。
                        # 交给 nudge 重试；_repair_react_json 已能补齐缺失闭合
                        # 括号，大概率下轮直接解析成功并执行工具。
                        logger.warning(
                            f"[reactor] iter={iteration} ReAct JSON residue without parseable "
                            f"tool_calls, refusing to treat as QA: {content[:200]!r}"
                        )
                    else:
                        _is_natural_language_qa = True
                        _is_pure_qa = True

                if _is_natural_language_qa:
                    logger.info(
                        f"[reactor] iter={iteration} natural language output with no creation signals, "
                        f"accepting as pure QA final output"
                    )
                    final_output = {"_message": content}
                    ended_reason = EndedReason.COMPLETED.value
                    step_record["final"] = True
                    step_record["output"] = final_output
                    break

                _nudge_count += 1
                if _nudge_count <= _MAX_NUDGE:
                    logger.info(
                        f"[reactor] iter={iteration} no tool_calls and not final, "
                        f"nudging LLM to continue in ReAct JSON format ({_nudge_count}/{_MAX_NUDGE})"
                    )
                    messages.append({"role": "assistant", "content": content})
                    _format_nudge_msg = "[system] 请按 ReAct JSON 格式输出：\n"
                    if _nudge_count == 1:
                        _format_nudge_msg += (
                            "请判断任务类型：\n"
                            "1. 纯问答/闲聊 → 直接 final：\n"
                            '{"thought": "这是纯问答，不需要调用工具", "final": true, "output": {"_message": "你的回答"}}\n'
                            "2. 传输/操作类（发邮件/下载/分享等）→ 调对应工具：\n"
                            '{"thought": "发邮件", "tool_calls": [{"name": "email_send", "arguments": {...}}], "final": false}\n'
                            "3. 创作类（做卡片/写文案等）→ 调创作工具：\n"
                            '{"thought": "你的思考", "tool_calls": [{"name": "工具名", "arguments": {...}}], "final": false}'
                        )
                    else:
                        _format_nudge_msg += (
                            '{"thought": "你的思考", "tool_calls": [...], "final": false}\n'
                            "或如果已完成：\n"
                            '{"thought": "完成", "final": true, "output": {...}}'
                        )
                    messages.append(
                        {
                            "role": "user",
                            "content": _format_nudge_msg,
                        }
                    )
                    step_record["observations"].append("⏳ LLM 输出非结构化，已引导继续")
                    continue
                else:
                    logger.warning(
                        f"[reactor] nudge limit ({_MAX_NUDGE}) reached, accepting output as final"
                    )
                    final_output = parsed if parsed else {"raw_text": content}
                    ended_reason = EndedReason.NUDGE_EXHAUSTED.value
                    step_record["final"] = True
                    step_record["output"] = final_output
                    break

            messages.append({"role": "assistant", "content": content})

            # LLM 恢复了正常 tool_calls 输出，重置 nudge 计数
            _nudge_count = 0
            _any_tool_executed = True

            # Tool call dedup: 逐签名统计「连续无进展了多少个 step」
            _current_signatures = {
                f"{c.get('name', '')}|{json.dumps(c.get('arguments', {}), sort_keys=True, default=str)}": c
                for c in tool_calls
            }
            # 本 step 没再出现的签名 → 清零（中间穿插了别的调用，不算连败）
            for _sig in [s for s in _repeat_counts if s not in _current_signatures]:
                _repeat_counts.pop(_sig, None)
                _repeat_args.pop(_sig, None)
                _last_call_ok.pop(_sig, None)
                _last_call_error.pop(_sig, None)
            _worst_sig, _worst_n = "", 0
            for _sig, _call in _current_signatures.items():
                # 上一次同签名调用失败才累计；上一次成功（如轮询返回
                # ok=true 但状态未就绪）视为有效重试，计数归 1 重来
                if _last_call_ok.get(_sig) is False:
                    _repeat_counts[_sig] = _repeat_counts.get(_sig, 0) + 1
                else:
                    _repeat_counts[_sig] = 1
                _repeat_args[_sig] = _call.get("arguments", {})
                if _repeat_counts[_sig] > _worst_n:
                    _worst_sig, _worst_n = _sig, _repeat_counts[_sig]

            if _worst_n >= _MAX_REPEAT_STEPS:
                _worst_name = _worst_sig.split("|", 1)[0]
                logger.warning(
                    f"[reactor] repeated failing tool call detected ({_worst_n}x consecutive): "
                    f"name={_worst_name}, args={_repeat_args.get(_worst_sig)}, "
                    f"last_error={_last_call_error.get(_worst_sig, '')[:200]} — forcing final output"
                )
                final_output = self._extract_output_from_messages(messages)
                final_output["_duplicate_tool_calls"] = True
                final_output["_repeated_call"] = {
                    "name": _worst_name,
                    "arguments": _repeat_args.get(_worst_sig),
                    "consecutive_steps": _worst_n,
                    "last_error": _last_call_error.get(_worst_sig, ""),
                }
                ended_reason = EndedReason.DUPLICATE_CALLS.value
                step_record["final"] = True
                step_record["output"] = final_output
                break

            for call in tool_calls:
                tool_name = call.get("name", "?")
                tool_args = call.get("arguments", {})
                if not isinstance(tool_args, dict):
                    tool_args = {}
                # 判重签名必须在 dispatch 之前取：pre_execute 钩子会原地
                # 改写 args，dispatch 后再算签名就与计数表里的对不上了
                _call_sig = f"{tool_name}|" f"{json.dumps(tool_args, sort_keys=True, default=str)}"

                display_args = self._summarize_tool_args(tool_name, tool_args)

                # --- side session write guard（PRD G6/R5：侧会话默认只读）---
                # 必须在 dispatch 之前拦截（写调用不能先执行再确认）。
                # 逐次放行：_side_write_allow_once 与本次签名精确匹配时放行一次。
                if self.side_session_id:
                    from app.engine.reactor.side_guard import (
                        call_signature,
                        declared_permissions_for_skill,
                        describe_write_request,
                        is_write_call,
                        write_permissions_of,
                    )

                    _sg_declared: tuple | None = ()
                    if tool_name == "skill_call":
                        _sg_inner_skill = str(
                            tool_args.get("skill")
                            or (tool_args.get("arguments") or {}).get("skill")
                            or ""
                        )
                        if _sg_inner_skill:
                            _sg_declared = declared_permissions_for_skill(_sg_inner_skill)
                    _sg_prim = self.primitives.get(tool_name)
                    _sg_required = list(_sg_prim.required_permissions) if _sg_prim else []
                    if is_write_call(_sg_required, _sg_declared):
                        _sg_sig = call_signature(tool_name, tool_args)
                        if self._side_write_allow_once == _sg_sig:
                            # 逐次放行：只放这一发，之后恢复拦截（Q9 无会话级放行）
                            logger.info(f"[reactor] side write allow-once matched: {tool_name}")
                            self._side_write_allow_once = None
                        else:
                            _sg_perms = write_permissions_of(_sg_required, _sg_declared)
                            _sg_desc = describe_write_request(tool_name, tool_args, _sg_perms)
                            _sg_info = {
                                "tool_name": tool_name,
                                "arguments": tool_args,
                                "description": _sg_desc,
                                "permissions": sorted(p.value for p in _sg_perms),
                            }
                            logger.warning(
                                f"[reactor] side write guard paused {tool_name}: {_sg_desc}"
                            )
                            try:
                                from app.services.side_session import set_pending_write

                                await set_pending_write(self.side_session_id, _sg_info)
                            except Exception as _sg_db_err:
                                logger.error(
                                    f"[reactor] side pending_write persist failed: {_sg_db_err}"
                                )

                            await self._emit_sse(
                                "tool_call_end",
                                {
                                    "node_id": "reactor",
                                    "iteration": iteration,
                                    "tool_name": tool_name,
                                    "success": True,
                                    "summary": "⏸ 侧会话写操作待用户确认",
                                    "duration_ms": 0,
                                },
                            )
                            from app.engine.reactor.loop_state import save_loop_state

                            loop_state: dict[str, Any] = {
                                "messages": messages,
                                "iteration": iteration,
                                "context_snapshot": self._run_context,
                                "pending_skill_call": {
                                    "name": tool_name,
                                    "arguments": dict(tool_args),
                                },
                                "_awaiting_side_write_confirmation": True,
                                "side_write": _sg_info,
                            }
                            _loop_state_path = save_loop_state(loop_state)

                            await self._emit_sse(
                                "side_write_confirmation_required",
                                {
                                    "node_id": "reactor",
                                    "iteration": iteration,
                                    "tool_name": tool_name,
                                    "description": _sg_desc,
                                    "permissions": _sg_info["permissions"],
                                    "side_session_id": self.side_session_id,
                                    "loop_state_path": _loop_state_path,
                                },
                            )

                            final_output = {
                                "ok": False,
                                "message": "侧会话默认只读：该操作需要用户确认后才能执行",
                                "_awaiting_side_write_confirmation": True,
                                "_loop_state_path": _loop_state_path,
                                "side_write": _sg_info,
                            }
                            ended_reason = EndedReason.SIDE_WRITE_PAUSED.value
                            step_record["tool_calls"].append(
                                {
                                    "name": tool_name,
                                    "args": display_args,
                                    "ok": True,
                                    "summary": "⏸ 侧会话写操作待用户确认",
                                    "duration_ms": 0,
                                }
                            )
                            self._clarification_break = True
                            break

                # 子智能体身份（真实数据，不让前端猜）：
                # - spawn_agent：真名在参数 task_name 里，agent_id 要等 dispatch 后才有
                # - send_message / wait_agent / interrupt_agent：参数里带 agent_id
                # 这里只下发「调用前已知」的部分；权威值由下方 tool_call_end 用
                # dispatch 返回值（obs.agent_id / obs.name）补齐。
                _sub_id = str(tool_args.get("agent_id") or "")
                _sub_name = (
                    str(tool_args.get("task_name") or tool_args.get("name") or "")
                    if tool_name == "spawn_agent"
                    else ""
                )

                await self._emit_sse(
                    "tool_call_start",
                    {
                        "node_id": "reactor",
                        "iteration": iteration,
                        "tool_name": tool_name,
                        "inputs": display_args,
                        "sub_agent_id": _sub_id,
                        "sub_agent_name": _sub_name,
                    },
                )
                await self._emit_observer(
                    "emit_tool_call_start", "reactor", tool_name, display_args
                )

                tool_start = time.monotonic()
                obs = await self._dispatch(call)
                tool_duration = int((time.monotonic() - tool_start) * 1000)

                # --- clarification break signal ---
                if isinstance(obs, dict) and obs.get("_awaiting_clarification"):
                    logger.debug(
                        f"[reactor] clarification signal received for {tool_name}, saving loop state and breaking"
                    )

                    await self._emit_sse(
                        "tool_call_end",
                        {
                            "node_id": "reactor",
                            "iteration": iteration,
                            "tool_name": tool_name,
                            "success": True,
                            "summary": "⏸ 需要用户确认创作偏好",
                            "duration_ms": tool_duration,
                        },
                    )

                    from app.engine.reactor.loop_state import save_loop_state

                    loop_state: dict[str, Any] = {
                        "messages": messages,
                        "iteration": iteration,
                        "context_snapshot": self._run_context,
                        "pending_skill_call": {
                            "name": tool_name,
                            "arguments": obs.get("_clarification_arguments", tool_args),
                        },
                        "clarified_answers": {},
                    }
                    _loop_state_path = save_loop_state(loop_state)

                    obs["_loop_state_path"] = _loop_state_path

                    await self._emit_sse(
                        "agent_clarification_required",
                        {
                            "node_id": "reactor",
                            "iteration": iteration,
                            "clarification_prompt": obs.get("_clarification_prompt", ""),
                            "clarification_skill": obs.get("_clarification_skill", ""),
                            "clarification_batches": obs.get("_clarification_batches", []),
                            "loop_state_path": _loop_state_path,
                        },
                    )

                    final_output = obs
                    ended_reason = EndedReason.CLARIFICATION_PAUSED.value
                    step_record["tool_calls"].append(
                        {
                            "name": tool_name,
                            "args": display_args,
                            "ok": True,
                            "summary": "⏸ 需要用户确认创作偏好",
                            "duration_ms": tool_duration,
                        }
                    )
                    self._clarification_break = True
                    break

                # --- cut_plan confirmation break signal ---
                # 2026-11-01：talking_head_overlay 规划出叠加方案（OverlayPlan）后
                # 返回 event="overlay_plan_ready" + needs_confirmation=True。渲染前
                # 用户必须先看到方案（设计文档 3.4 #2 缺口2），这里把「叠加方案 +
                # 原始调用参数」落盘，用户确认后由 POST /api/v1/chat/confirm-cut-plan
                # 以 gate_mode="auto_confirm" 确定性回放（不经 LLM，避免重规划）。
                # ⚠️ 顺序铁律（2026-11-05 修）：本分支必须在 guardian 分支之前！
                # guardian 的条件（needs_confirmation 且无 shots）同样会命中
                # overlay_plan_ready 信号——之前代码顺序写反，导致叠加方案永远被
                # guardian 通用卡截胡，专用方案卡一次都没弹出来过（15:51 日志实锤）。
                if (
                    isinstance(obs, dict)
                    and obs.get("event") == "overlay_plan_ready"
                    and obs.get("needs_confirmation")
                ):
                    logger.info(
                        f"[reactor] cut_plan confirmation signal for {tool_name}, saving loop state"
                    )

                    await self._emit_sse(
                        "tool_call_end",
                        {
                            "node_id": "reactor",
                            "iteration": iteration,
                            "tool_name": tool_name,
                            "success": True,
                            "summary": "⏸ 叠加方案已规划，等待用户确认",
                            "duration_ms": tool_duration,
                        },
                    )
                    from app.engine.reactor.loop_state import save_loop_state

                    # 与 dispatch 钩子的自动注入同一套取法：skill_call 时取内层技能名/参数。
                    _cp_inner_name = tool_name
                    _cp_inner_args = tool_args
                    if tool_name == "skill_call":
                        _cp_inner_name = str(tool_args.get("skill") or "")
                        _cp_nested = tool_args.get("arguments")
                        _cp_inner_args = _cp_nested if isinstance(_cp_nested, dict) else {}

                    loop_state: dict[str, Any] = {
                        "messages": messages,
                        "iteration": iteration,
                        "context_snapshot": self._run_context,
                        "pending_skill_call": {
                            "name": _cp_inner_name,
                            "arguments": dict(_cp_inner_args),
                        },
                        "cut_plan": obs.get("data", {}),
                        "_awaiting_cut_plan_confirmation": True,
                    }
                    _loop_state_path = save_loop_state(loop_state)
                    obs["_loop_state_path"] = _loop_state_path

                    await self._emit_sse(
                        "cut_plan_confirmation_required",
                        {
                            "node_id": "reactor",
                            "iteration": iteration,
                            "skill": _cp_inner_name,
                            "plan": obs.get("data", {}),
                            "message": obs.get("message", ""),
                            "loop_state_path": _loop_state_path,
                        },
                    )

                    step_record["tool_calls"].append(
                        {
                            "name": tool_name,
                            "args": display_args,
                            "ok": True,
                            "summary": "⏸ 叠加方案已规划，等待用户确认",
                            "duration_ms": tool_duration,
                        }
                    )
                    final_output = obs
                    obs["_awaiting_cut_plan_confirmation"] = True
                    obs["_loop_state_path"] = _loop_state_path
                    obs["_cut_plan"] = obs.get("data", {})
                    ended_reason = EndedReason.CUT_PLAN_PAUSED.value
                    self._clarification_break = True
                    break

                # --- guardian confirmation break signal ---
                # Guardian NEEDS_CONFIRM 返回 needs_confirmation=True 但无 shots/title。
                # 这与技能的分镜确认（有 shots/title）必须区分，否则 Guardian 拦截的
                # 高危操作（如 bash rm -rf）会被路由到分镜确认回放路径，用户点"确认分镜"
                # 时高危操作被执行 → 安全漏洞。
                if isinstance(obs, dict) and obs.get("needs_confirmation") and "shots" not in obs:
                    logger.warning(
                        f"[reactor] Guardian confirmation required for {tool_name}, saving loop state"
                    )

                    await self._emit_sse(
                        "tool_call_end",
                        {
                            "node_id": "reactor",
                            "iteration": iteration,
                            "tool_name": tool_name,
                            "success": False,
                            "summary": f"⏸ Guardian: 需要用户确认 ({obs.get('error', '')})",
                            "duration_ms": tool_duration,
                        },
                    )
                    from app.engine.reactor.loop_state import save_loop_state

                    loop_state: dict[str, Any] = {
                        "messages": messages,
                        "iteration": iteration,
                        "context_snapshot": self._run_context,
                        "pending_skill_call": {
                            "name": tool_name,
                            "arguments": dict(tool_args),
                        },
                        "_awaiting_guardian_confirmation": True,
                        "confirmation_prompt": obs.get("confirmation_prompt", ""),
                    }
                    _loop_state_path = save_loop_state(loop_state)
                    obs["_loop_state_path"] = _loop_state_path

                    await self._emit_sse(
                        "guardian_confirmation_required",
                        {
                            "node_id": "reactor",
                            "iteration": iteration,
                            "tool_name": tool_name,
                            "confirmation_prompt": obs.get("confirmation_prompt", ""),
                            "risk_info": obs.get("error", ""),
                            "loop_state_path": _loop_state_path,
                        },
                    )

                    final_output = obs
                    obs["_awaiting_guardian_confirmation"] = True
                    obs["_loop_state_path"] = _loop_state_path
                    ended_reason = EndedReason.GUARDIAN_PAUSED.value
                    self._clarification_break = True
                    break

                # --- storyboard confirmation break signal ---
                # 2026-10-17：视频类技能出完分镜后返回
                # needs_confirmation=True（确认闸门，见该技能头部的「确认闸门」小节）。
                # 这里把「待确认分镜 + 原始调用参数」一起落盘，用户在 UI 上点确认或
                # 改分镜后，由 POST /api/v1/chat/confirm-storyboard 带 refine_shots
                # 确定性回放。不经 LLM 重吐整份分镜 JSON，避免长 JSON 被改写坏。
                if isinstance(obs, dict) and obs.get("needs_confirmation") and "shots" in obs:
                    logger.info(
                        f"[reactor] storyboard confirmation signal for {tool_name}, saving loop state"
                    )

                    await self._emit_sse(
                        "tool_call_end",
                        {
                            "node_id": "reactor",
                            "iteration": iteration,
                            "tool_name": tool_name,
                            "success": True,
                            "summary": "⏸ 分镜草稿已生成，等待用户确认",
                            "duration_ms": tool_duration,
                        },
                    )
                    from app.engine.reactor.loop_state import save_loop_state

                    # 与 dispatch 钩子的自动注入同一套取法：skill_call 时取内层技能名/参数。
                    # pre_execute 钩子对 args 是原地改写，这里读到的是已注入真实资产 id 的版本。
                    _sb_inner_name = tool_name
                    _sb_inner_args = tool_args
                    if tool_name == "skill_call":
                        _sb_inner_name = str(tool_args.get("skill") or "")
                        _sb_nested = tool_args.get("arguments")
                        _sb_inner_args = _sb_nested if isinstance(_sb_nested, dict) else {}

                    loop_state: dict[str, Any] = {
                        "messages": messages,
                        "iteration": iteration,
                        "context_snapshot": self._run_context,
                        "pending_skill_call": {
                            "name": _sb_inner_name,
                            "arguments": dict(_sb_inner_args),
                        },
                        "storyboard": {
                            "title": obs.get("title", ""),
                            "shots": obs.get("shots", []),
                            "scene_description": obs.get("scene_description"),
                            "estimated_duration": obs.get("estimated_duration"),
                        },
                        "clarified_answers": {},
                        "_awaiting_storyboard_confirmation": True,
                    }
                    _loop_state_path = save_loop_state(loop_state)
                    obs["_loop_state_path"] = _loop_state_path

                    await self._emit_sse(
                        "storyboard_confirmation_required",
                        {
                            "node_id": "reactor",
                            "iteration": iteration,
                            "skill": _sb_inner_name,
                            "title": obs.get("title", ""),
                            "shots": obs.get("shots", []),
                            "estimated_duration": obs.get("estimated_duration"),
                            "loop_state_path": _loop_state_path,
                        },
                    )

                    step_record["tool_calls"].append(
                        {
                            "name": tool_name,
                            "args": display_args,
                            "ok": True,
                            "summary": "⏸ 分镜草稿已生成，等待用户确认",
                            "duration_ms": tool_duration,
                        }
                    )
                    # 把"待确认分镜"信号透传到 final_output，让 ChatAgent
                    # 能识别并返回 awaiting_storyboard_confirmation 状态
                    # （与 _awaiting_clarification 同样的处理路径）。
                    final_output = obs
                    obs["_awaiting_storyboard_confirmation"] = True
                    obs["_loop_state_path"] = _loop_state_path
                    obs["_storyboard_shots"] = obs.get("shots", [])
                    obs["_storyboard_title"] = obs.get("title", "")
                    obs["_storyboard_duration"] = obs.get("estimated_duration")
                    ended_reason = EndedReason.STORYBOARD_PAUSED.value
                    self._clarification_break = True
                    break

                obs_ok = obs.get("ok", True) if isinstance(obs, dict) else True
                obs_summary = self._summarize_observation(tool_name, obs)

                # 回写本签名的「是否取得进展」，供下一步的重复调用刹车判断：
                # - 失败 → 无进展，累计连败步数
                # - 成功（含轮询 ok=true 但状态未就绪）→ 视为有效重试，计数归 1
                # - 空探针（probe）→ 技能根本没执行，同样无进展，必须累计；
                #   否则 LLM 反复探同一个技能会因 ok=true 而永远刹不住
                _progressed = bool(obs_ok) and not (isinstance(obs, dict) and obs.get("probe"))
                _last_call_ok[_call_sig] = _progressed
                if not obs_ok:
                    _last_call_error[_call_sig] = obs_summary
                elif not _progressed:
                    _last_call_error[_call_sig] = "probe: skill not executed"

                # 自动写入 WorldState（对标 Codex ContextManager::ingest_tool_output）
                if getattr(self, "_context_manager", None) is not None and isinstance(obs, dict):
                    try:
                        self._context_manager.ingest_tool_output(tool_name, obs)
                    except Exception as _ws_err:
                        logger.debug(
                            f"[reactor] WorldState ingest failed for {tool_name}: {_ws_err}"
                        )

                # 子智能体身份的权威值来自 dispatch 返回值：spawn_agent 的
                # {"agent_id","name"}，其余工具按调用参数里已有的 agent_id 传。
                if isinstance(obs, dict) and obs.get("agent_id"):
                    _sub_id = str(obs.get("agent_id"))
                    _sub_name = str(obs.get("name") or _sub_name)

                await self._emit_sse(
                    "tool_call_end",
                    {
                        "node_id": "reactor",
                        "iteration": iteration,
                        "tool_name": tool_name,
                        "success": obs_ok,
                        "summary": obs_summary,
                        "duration_ms": tool_duration,
                        "sub_agent_id": _sub_id,
                        "sub_agent_name": _sub_name,
                    },
                )
                await self._emit_observer(
                    "emit_tool_call_end", "reactor", tool_name, obs_ok, obs_summary
                )

                # 2026-10-22：工具失败必须留 INFO 痕迹（此前 obs_summary
                # 只进 SSE 不进日志，skill_call 九连败时无据可查）
                if not obs_ok:
                    logger.info(
                        f"[reactor] iter={iteration} TOOL FAILED {tool_name} "
                        f"({tool_duration}ms): {obs_summary[:300]}"
                    )

                step_record["tool_calls"].append(
                    {
                        "name": tool_name,
                        "args": display_args,
                        "ok": obs_ok,
                        "summary": obs_summary,
                        "duration_ms": tool_duration,
                    }
                )

                obs_str = json.dumps(obs, ensure_ascii=False, default=str)
                if len(obs_str) > _OBSERVATION_MAX_LEN:
                    obs_str = obs_str[:_OBSERVATION_MAX_LEN] + "...(truncated)"
                messages.append(
                    {
                        "role": "user",
                        "content": f"[observation] {tool_name} -> {obs_str}",
                    }
                )

                step_record["observations"].append(obs_summary)

                # 2026-10-22：产物透传。技能返回的产物字段（卡片数据/文件
                # 路径/素材 id）此前全靠 LLM 在 final output 里搬运——
                # observation 超 4000 字被截断后 LLM 根本看不到产物详情，
                # final 只剩一句话总结，前端无卡可渲染（10:57 实锤）。
                # 现在系统侧直接累积，循环出口统一合并进 final_output。
                if isinstance(obs, dict) and obs.get("ok", True):
                    for _a_key in (
                        "card_draft",
                        "asset_ids",
                        "file_path",
                        "html_path",
                        "image_url",
                        "title",
                    ):
                        _a_val = obs.get(_a_key)
                        if _a_val and _a_key not in self._observed_artifacts:
                            self._observed_artifacts[_a_key] = _a_val
                            if _a_key == "card_draft":
                                # 先落盘再推事件：前端拿到的 artifact_id 是真实
                                # 产物 id，可直接替换本地 draft- 占位，刷新后不丢。
                                _artifact_id = await self._persist_card_draft(_a_val)
                                await self._emit_sse(
                                    "card_draft_ready",
                                    {
                                        "node_id": "reactor",
                                        "iteration": iteration,
                                        "card_draft": _a_val,
                                        "artifact_id": _artifact_id,
                                    },
                                )

                    # 作品内容修改（work_edit）：与 card_draft 不同，这是「就地改」
                    # 而不是「新建产物」，因此必须每次调用都推送——同一回合连续
                    # 改标题再改正文，前端要按顺序叠加，不能被去重吃掉。
                    _work_patch = obs.get("work_patch")
                    if isinstance(_work_patch, dict) and _work_patch:
                        await self._emit_sse(
                            "work_patch_ready",
                            {
                                "node_id": "reactor",
                                "iteration": iteration,
                                "work_patch": _work_patch,
                            },
                        )

                # 2026-10-23：Receipt Ledger —— 工具执行后自动建一条不可
                # 伪造的回执。Output Gate 据此校验 final output 中的产物
                # 声明：无回执背书的产物 key 会被删除（Type-2 幻觉出口）。
                _receipt_artifacts: dict[str, Any] = {}
                if isinstance(obs, dict) and obs_ok:
                    for _rk in (
                        "card_draft",
                        "asset_ids",
                        "file_path",
                        "html_path",
                        "image_url",
                        "title",
                        "pages",
                        "deck",
                        "schema_version",
                        "work_patch",
                    ):
                        _rv = obs.get(_rk)
                        if _rv is not None:
                            _receipt_artifacts[_rk] = _rv
                self._receipt_ledger.append(
                    {
                        "call_id": f"call_{iteration}_{len(step_record['tool_calls'])}",
                        "tool": tool_name,
                        "ok": obs_ok,
                        "artifacts": _receipt_artifacts,
                        "iter": iteration,
                    }
                )

            step_record["duration_ms"] = int((time.monotonic() - step_start) * 1000)
            self._steps.append(step_record)
            messages = self._maybe_truncate_messages(messages)

            if getattr(self, "_clarification_break", False):
                break
        else:
            logger.warning(f"[reactor] max_iterations={self.max_iterations} reached")
            final_output = self._extract_output_from_messages(messages)
            final_output.setdefault("_loop_truncated", True)
            ended_reason = EndedReason.MAX_ITERATIONS.value

        if not isinstance(final_output, dict):
            final_output = {"value": final_output}

        # 一期契约兜底：任何漏标的出口按正常完成计（validate 保证枚举合法）
        final_output["ended_reason"] = ended_reason or EndedReason.COMPLETED.value

        final_output["_token_usage"] = total_token_usage
        final_output["_iterations"] = iteration
        final_output.setdefault("_last_thought", last_thought)
        final_output["_steps"] = self._steps

        # 2026-11-08：工作区媒体产物自动发布。
        # 现象：口播成片由 bash/ffmpeg 直接写在工作区根目录，`outputs/` 才是
        # 「我的空间」的扫描根，成片既不在那里、也没作为产物字段挂出，前端
        # 没有任何可渲染地址（19:03 实证：4.96MB 成片完好，用户仍「看不见」）。
        # 对策：回合结束时收割本轮新增的媒体文件，发布到
        # `outputs/<user_id>/agent_media/`，把可直连地址写进 final_output。
        # 只在「本轮真的执行过工具」时收割：纯问答没有产物，别白扫工作区
        if not _paused_reason(ended_reason) and _any_tool_executed:
            _media_items: list[dict[str, Any]] = []
            try:
                from app.services.agent_media_publish import (
                    publish_workspace_media,
                )

                _media_items = await publish_workspace_media(
                    _turn_started_at,
                    str((self._run_context or {}).get("user_id") or ""),
                )
            except Exception as _media_exc:  # noqa: BLE001 - 发布失败不能连累对话
                logger.warning(f"[reactor] 工作区媒体产物发布失败: {_media_exc}")
            if _media_items:
                _primary_video = next((m for m in _media_items if m["kind"] == "video"), None)
                final_output["media_artifacts"] = _media_items
                if _primary_video:
                    final_output.setdefault("video_url", _primary_video["url"])
                # 即时推给前端：不等落库/刷新，本回合就能看到成片
                await self._emit_sse(
                    "media_artifact_ready",
                    {
                        "node_id": "reactor",
                        "iteration": iteration,
                        "media_artifacts": _media_items,
                        "video_url": _primary_video["url"] if _primary_video else "",
                    },
                )
                # 回执背书：媒体产物是系统侧真实落盘的，登记后 Output Gate 放行
                self._receipt_ledger.append(
                    {
                        "call_id": f"media_publish_{len(self._receipt_ledger)}",
                        "tool": "agent_media_publish",
                        "ok": True,
                        "artifacts": {
                            "media_artifacts": _media_items,
                            "video_url": _primary_video["url"] if _primary_video else "",
                        },
                        "iter": iteration,
                    }
                )

        # 2026-10-23：Output Gate —— Receipt Ledger + 产物校验。
        # 替代原来的 setdefault 透传，改为三步：
        #   1. 从 ledger 中提取所有 ok=True 的 receipt 背书的产物 key（_proven_artifacts）
        #   2. 删除 final_output 中无 receipt 背书的产物 key（Type-2 幻觉出口）
        #   3. 补全：ledger 背书但 output 漏掉的产物，从 _observed_artifacts 注入
        # 暂停轮（clarification/guardian）不合并——产物属于下一次续跑。
        if not _paused_reason(ended_reason):
            _PRODUCT_ARTIFACT_KEYS = frozenset(
                {
                    "card_draft",
                    "asset_ids",
                    "file_path",
                    "html_path",
                    "image_url",
                    "title",
                    "pages",
                    "deck",
                }
            )
            _proven_artifacts: set[str] = set()
            for _rcpt in self._receipt_ledger:
                if _rcpt["ok"]:
                    _proven_artifacts.update(_rcpt["artifacts"].keys())

            _unproven_keys: set[str] = set()
            for _gk in list(final_output.keys()):
                if _gk in _PRODUCT_ARTIFACT_KEYS and _gk not in _proven_artifacts:
                    _unproven_keys.add(_gk)
                    del final_output[_gk]
            if _unproven_keys:
                logger.warning(
                    f"[reactor] Output Gate: removed unproven artifact keys "
                    f"(no ok=True receipt): {_unproven_keys}"
                )
                final_output["_gate_removed"] = sorted(_unproven_keys)

            for _a_key, _a_val in getattr(self, "_observed_artifacts", {}).items():
                if _a_key in _proven_artifacts:
                    final_output.setdefault(_a_key, _a_val)

        # Execution Gate —— Type-1 幻觉硬校验。
        # 检测条件：非暂停出口 + 零工具执行（receipt_ledger 为空）+ 零产物累积 + 非纯问答。
        # 这意味着 LLM 从未真正执行任何工作就终止了（format nudge 失败、
        # duplicate call 放弃等），属于 Type-1 假完成。
        # 纯问答场景（_is_pure_qa=True）零工具零产物是正确行为，不触发。
        # 标记 _fake_final=True + _execution_gate 供前端/评测诊断；
        # 同时将 ended_reason 改写为 FAKE_FINAL 让下游明确感知。
        # MAX_ITERATIONS / TOKEN_BUDGET 属于资源截断而非幻觉，不改写。
        if not _paused_reason(ended_reason):
            _has_any_receipt = bool(self._receipt_ledger)
            _has_any_artifact = bool(self._observed_artifacts)
            if not _has_any_receipt and not _has_any_artifact and not _is_pure_qa:
                final_output["_fake_final"] = True
                final_output["_execution_gate"] = {
                    "trigger": ended_reason,
                    "receipt_count": len(self._receipt_ledger),
                    "artifact_count": len(self._observed_artifacts),
                }
                logger.warning(
                    f"[reactor] Execution Gate: zero execution zero artifact "
                    f"termination detected (ended_reason={ended_reason}). "
                    f"Marking _fake_final=True."
                )
                if ended_reason not in (
                    EndedReason.MAX_ITERATIONS.value,
                    EndedReason.TOKEN_BUDGET.value,
                ):
                    ended_reason = EndedReason.FAKE_FINAL.value
                    final_output["ended_reason"] = ended_reason

        display_output = self._format_final_output(final_output)

        _is_paused = final_output["ended_reason"] in (
            EndedReason.CLARIFICATION_PAUSED.value,
            EndedReason.GUARDIAN_PAUSED.value,
            EndedReason.STORYBOARD_PAUSED.value,
            EndedReason.CUT_PLAN_PAUSED.value,
        )

        await self._emit_sse(
            "node_completed",
            {
                "node_id": "reactor",
                "node_type": "reactor_react",
                "output": display_output,
                "iterations": iteration,
                "token_usage": total_token_usage,
                "ended_reason": final_output["ended_reason"],
                "schema_version": "1.0",
            },
        )
        # 暂停等待用户（clarification/guardian/storyboard）时不发 workflow_completed：
        # workflow_completed 是终态事件，SSE 总线收到后关闭流，前端收到后
        # 调 finishStream + transitionTurn('done')，链路彻底断开。
        # 暂停不是完成——用户确认后后台续跑，事件继续在同一条 SSE 流上推送。
        # emit_workflow_completed=False：恢复路径（/confirm-guardian 等）调用方
        # 会在 run() 返回后落库并发布带正文的 agent_loop_done，内部这个
        # 不带正文的终态只会把流提前关掉（2026-11-04 正文丢失实锤）。
        if not _is_paused and emit_workflow_completed:
            await self._emit_sse(
                "workflow_completed",
                {
                    "output": display_output,
                    "iterations": iteration,
                    "token_usage": total_token_usage,
                    "ended_reason": final_output["ended_reason"],
                    "schema_version": "1.0",
                },
            )

        # 协作资源清理：session 结束时 shutdown AgentManager，取消所有子 Agent
        if self._collab_tools is not None:
            try:
                await self._collab_tools.agent_manager.shutdown()
            except Exception:
                pass

        return final_output

    @staticmethod
    def _summarize_tool_args(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        """Summarize tool arguments for display (truncate large values)."""
        display: dict[str, Any] = {}
        for k, v in args.items():
            if isinstance(v, str) and len(v) > 200:
                display[k] = v[:200] + "..."
            elif isinstance(v, list) and len(v) > 5:
                display[k] = v[:5]
            else:
                display[k] = v
        return display

    @staticmethod
    def _summarize_observation(tool_name: str, obs: dict[str, Any]) -> str:
        """Create a human-readable summary of a tool observation."""
        if not obs.get("ok", True):
            return f"❌ {obs.get('error', 'unknown error')}"

        summaries: dict[str, str] = {
            "file_read": lambda o: f"📄 读取成功 ({o.get('total_lines', '?')} 行, {o.get('file_size', '?')} bytes)",
            "file_write": lambda o: f"📝 写入成功 ({o.get('lines', '?')} 行)",
            "file_edit": lambda o: "✏️ 替换成功",
            "bash": lambda o: f"⚡ 执行完成 (exit_code={o.get('exit_code', '?')})",
            "glob": lambda o: f"🔍 找到 {o.get('count', 0)} 个文件",
            "grep": lambda o: f"🔍 匹配 {o.get('count', 0)} 处",
            "llm_generate": lambda o: f"🧠 生成 {len(o.get('text', ''))} 字",
            "viral_score": lambda o: "📊 爆款评分完成",
            "content_check": lambda o: f"✅ 合规{'通过' if o.get('passed') else '未通过'}",
        }

        fn = summaries.get(tool_name)
        if fn:
            try:
                return fn(obs)
            except Exception:
                pass

        return "✅ 执行成功"

    @staticmethod
    def _format_final_output(output: dict[str, Any]) -> dict[str, Any]:
        """Format the final output for frontend display."""
        display: dict[str, Any] = {}

        # 2026-10-22：白名单加入产物字段（产物透传链最后一环）——
        # card_draft/asset_ids/html_path/image_url 不放行的话，SSE 与
        # POST 响应里前端永远拿不到卡片数据，无从渲染
        for key in (
            "title",
            "content",
            "status",
            "tags",
            "file",
            "path",
            "card_draft",
            "asset_ids",
            "html_path",
            "image_url",
            "pages",
            "deck",
        ):
            if key in output:
                display[key] = output[key]

        if "error" in output:
            display["error"] = output["error"]

        # Output Gate 校验结果：被移除的无回执产物 key 列表
        if "_gate_removed" in output:
            display["_gate_removed"] = output["_gate_removed"]

        # Execution Gate 校验结果：零执行零产物假完成诊断
        if "_execution_gate" in output:
            display["_execution_gate"] = output["_execution_gate"]
        if output.get("_fake_final"):
            display["_fake_final"] = True

        display["_iterations"] = output.get("_iterations", 0)
        display["_token_usage"] = output.get("_token_usage", 0)
        display["_last_thought"] = output.get("_last_thought", "")
        # 一期契约：终止原因放行到 SSE/前端（此前 _loop_truncated/_token_budget_exhausted
        # 被本白名单滤掉，前端永远不知道任务是被截断的——根因修复点）
        display["ended_reason"] = output.get("ended_reason", EndedReason.COMPLETED.value)

        return display

    _CONTEXT_SUMMARY_MAX_CHARS = 4000

    def _route_system_prompt(self, user_intent: str) -> None:
        """P1：首轮 run() 时按真实用户意图重建技能目录（触发词路由）。

        - _skills 保持全集（skill_call 执行侧 / 澄清门 / 分类器都查它），
          只有目录展示被路由裁剪；未选中技能以「其他可用技能」列名兜底
        - 显式传入 system_prompt 的调用方不覆盖；只执行一次（首轮后固定，
          保证多轮对话 system prompt 前缀稳定，利于 LLM 上下文缓存命中）
        - 路由结果为空时不替换（防御性兜底，保留全量目录）
        """
        if self._prompt_routed or self._system_prompt_explicit or not self._skills:
            return
        self._prompt_routed = True
        try:
            from app.tools.skill_router import SkillRouter

            routing = SkillRouter(
                max_tools=_SKILL_CATALOG_MAX_TOOLS, enforce_priority=True
            ).select_skills(self._skills, user_intent or "")
            selected = list(routing.selected_skills)
            if not selected:
                return
            selected_names = {getattr(s, "name", "") for s in selected}
            extra_names = [
                n
                for n in (getattr(s, "name", "") for s in self._skills)
                if n and n not in selected_names
            ]
            self.system_prompt = self._with_persona(
                get_reactor_system_prompt(
                    skills=selected,
                    user_message=user_intent,
                    creation_type=self._creation_type,
                    extra_skill_names=extra_names,
                )
            )
        except Exception:
            logger.exception("[reactor] skill catalog routing failed; keep full catalog")

    def _build_initial_messages(
        self,
        user_intent: str,
        context: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        self._route_system_prompt(user_intent)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt},
        ]
        # P2-3：动态设计系统块以独立 system 消息注入（不进 system prompt），
        # 保证 messages[0] 完全静态 → 多轮对话 LLM 前缀缓存可命中
        design_block = build_design_context_block(
            user_message=user_intent,
            creation_type=self._creation_type,
        )
        if design_block:
            messages.append({"role": "system", "content": design_block})
        if context:
            # 作品上下文先行提取（pop）：正文+cardDraft 动辄几千字，若留在 context
            # 里走下面的 JSON 摘要，>800 字会被截成 200 字残句。作为独立 system
            # 消息完整注入（与 context_manager 分支同待遇）。
            _work_prompt = context.pop("work_context_prompt", None)
            if _work_prompt and isinstance(_work_prompt, str):
                messages.append(
                    {
                        "role": "system",
                        "content": _work_prompt,
                    }
                )
            # AI 人格注入：提取 personality_prompt 作为独立 system message，
            # 不埋进 context JSON（否则 JSON 转义会破坏 prompt 效果）。
            _personality = context.pop("personality_prompt", None)
            if _personality and isinstance(_personality, str):
                messages.append(
                    {
                        "role": "system",
                        "content": _personality,
                    }
                )
            _ctx_summary: dict[str, Any] = {}
            _large_keys: set[str] = set()
            for k, v in context.items():
                v_str = (
                    json.dumps(v, ensure_ascii=False, default=str) if not isinstance(v, str) else v
                )
                if len(v_str) > 800:
                    _large_keys.add(k)
                    _ctx_summary[k] = v_str[:200] + f"... [truncated, {len(v_str)} chars total]"
                else:
                    _ctx_summary[k] = v
            context_str = json.dumps(_ctx_summary, ensure_ascii=False, default=str)
            if len(context_str) > self._CONTEXT_SUMMARY_MAX_CHARS:
                context_str = context_str[: self._CONTEXT_SUMMARY_MAX_CHARS] + "... [truncated]"
            messages.append(
                {
                    "role": "system",
                    "content": f"[context] {context_str}",
                }
            )
        messages.append({"role": "user", "content": user_intent})
        return messages

    def _reactor_response_format(self) -> dict[str, str] | None:
        """BUG-006：ReAct 每轮都要求纯 JSON 输出，deepseek-v3 开 JSON 模式
        （response_format=json_object）消除 JSON 前导中文 → nudge 浪费迭代归零。

        deepseek-r1 / reasoner 不支持 response_format（会 400），按模型条件关闭。
        ModelRouter 的 model_name="model-router" 不含 r1，放行（其底层 stream_chat
        走 v3 adapter，response_format 会透传）。
        兼容性：测试桩/第三方 LLM 可能只接受 (messages) 签名，调用方用
        _fn_accepts_response_format 探测后再传。
        """
        if self.llm is None:
            return None
        model = str(
            getattr(self.llm, "model", "") or getattr(self.llm, "model_name", "") or ""
        ).lower()
        if not model or "r1" in model or "reasoner" in model:
            return None
        return {"type": "json_object"}

    @staticmethod
    def _fn_accepts_response_format(fn: Any) -> bool:
        """探测可调用对象是否接受 response_format 参数（VAR_KEYWORD 视为接受）。"""
        try:
            sig = inspect.signature(fn)
        except (TypeError, ValueError):
            return False
        for p in sig.parameters.values():
            if p.kind is inspect.Parameter.VAR_KEYWORD:
                return True
        return "response_format" in sig.parameters

    async def _call_llm(
        self,
        messages: list[dict[str, Any]],
        emit_delta: bool = False,
    ) -> tuple[str, int, str]:
        """Call LLM with streaming + StreamRetryState retry. Returns (content, token_usage, reasoning).

        emit_delta 默认 False：把 LLM chunk 当 agent_message_delta 推送只适用于
        「输出即用户正文」的调用方。reactor 主循环的输出是 ReAct 协议 JSON，
        开了就是把协议原文泄进聊天正文（见 run() L197 处注释）。

        Retry strategy (ref: Codex responses_retry.rs):
          - 401/403/402: immediately give up (unrecoverable)
          - 429 rate limit: retry with longer backoff
          - Connection errors: retry up to 10 times with exponential backoff
          - Server errors (5xx): retry up to 3 times
          - On persistent failure: fallback to non-streaming call
        """
        from app.engine.governance.llm_circuit import get_llm_circuit
        from app.engine.governance.stream_retry import RetryAction, StreamRetryState

        if self.llm is None:
            return '{"final": true, "output": {"error": "no LLM configured"}}', 0, ""

        circuit = get_llm_circuit()
        if not circuit.allow_request():
            logger.warning("[reactor] LLM circuit breaker OPEN — will attempt one probe call")

        state = StreamRetryState(max_retries=_MAX_RETRIES)
        use_stream = True
        _fallback_used = False  # 非流式兜底只允许一次，防止连接错误计数满后 FALLBACK 无限循环
        # BUG-006：按模型条件开 JSON 模式（v3 开、r1 关、未知桩不传）
        _response_format = self._reactor_response_format()

        while True:
            try:
                if use_stream:
                    stream_fn = getattr(self.llm, "stream_chat", None)
                    if stream_fn is not None:
                        content_parts: list[str] = []
                        reasoning_parts: list[str] = []
                        # 协议门：None=未定性，"proto"=思维链是 ReAct 协议 JSON，"nl"=自然语言。
                        # 定性依据首个非空白字符（{ [ ` 开头判为协议草稿）；定性前不透传，
                        # 避免协议原文漏进前端「深度思考」块（2026-10-26 裸 JSON 问题）。
                        reasoning_gate: str | None = None
                        token_usage = 0

                        _stream_kwargs: dict[str, Any] = {"messages": messages}
                        if _response_format and self._fn_accepts_response_format(stream_fn):
                            _stream_kwargs["response_format"] = _response_format
                        async for chunk in stream_fn(**_stream_kwargs):
                            reasoning = chunk.get("reasoning_content")
                            text = chunk.get("content")
                            if reasoning:
                                reasoning_parts.append(reasoning)
                                if reasoning_gate is None:
                                    head = "".join(reasoning_parts).lstrip()[:1]
                                    if head:
                                        reasoning_gate = (
                                            "proto" if head in ("{", "[", "`") else "nl"
                                        )
                                if reasoning_gate == "nl":
                                    await self._emit_sse(
                                        "reasoning_summary_text_delta",
                                        {
                                            "node_id": "reactor",
                                            "delta": reasoning,
                                        },
                                    )
                            if text:
                                content_parts.append(text)
                                if emit_delta:
                                    await self._emit_sse(
                                        "agent_message_delta",
                                        {
                                            "node_id": "reactor",
                                            "delta": text,
                                        },
                                    )
                            if chunk.get("is_final"):
                                token_usage = chunk.get("token_usage", 0) or token_usage

                        content = "".join(content_parts)
                        reasoning = self._sanitize_reasoning("".join(reasoning_parts))
                        if reasoning != "".join(reasoning_parts):
                            logger.info(
                                "[reactor] reasoning was ReAct protocol JSON — sanitized for display"
                            )
                            # 协议原文已拦下，补发消洗后的人话 thought，思维链行不留空
                            await self._emit_sse(
                                "reasoning_summary_text_delta",
                                {
                                    "node_id": "reactor",
                                    "delta": reasoning,
                                },
                            )
                        if reasoning_parts:
                            await self._emit_sse(
                                "reasoning_completed",
                                {
                                    "node_id": "reactor",
                                    "summary_parts": [],
                                },
                            )
                        circuit.record_success()
                        return content, token_usage, reasoning

                _chat_kwargs: dict[str, Any] = {"messages": messages}
                if _response_format and self._fn_accepts_response_format(self.llm.chat):
                    _chat_kwargs["response_format"] = _response_format
                result = await self.llm.chat(**_chat_kwargs)
                content = result.get("content", "")
                token_usage = result.get("token_usage", 0) or 0
                reasoning = self._sanitize_reasoning(result.get("reasoning_content") or "")
                circuit.record_success()
                return content, token_usage, reasoning

            except Exception as e:
                action = state.handle_error(e)

                if action == RetryAction.GIVE_UP:
                    logger.error(f"[reactor] LLM call unrecoverable: {e}")
                    _err_str = str(e).lower()
                    _is_unrecov = any(
                        kw in _err_str
                        for kw in [
                            "402",
                            "401",
                            "403",
                            "payment",
                            "unauthorized",
                            "invalid api key",
                        ]
                    )
                    circuit.record_failure(str(e), is_unrecoverable=_is_unrecov)
                    # 重试耗尽后给用户明确报错（2026-11-07：原来静默返回英文
                    # 异常原文，用户只看到 3 分钟无回复后蹦出一串英文）。
                    _friendly_err = (
                        "抱歉，模型服务暂时不可用（连续多次连接失败），请稍后重试；"
                        "若持续出现请检查网络或 DeepSeek 服务状态。"
                        if not _is_unrecov
                        else "模型服务鉴权失败（API Key 无效或余额不足），请检查配置后重试。"
                    )
                    await self._emit_sse(
                        "llm_retry_status",
                        {
                            "node_id": "reactor",
                            "attempt": max(state.retries, state.connection_retries),
                            "final": True,
                            "label": _friendly_err,
                        },
                    )
                    return (
                        json.dumps(
                            {
                                "thought": _friendly_err,
                                "final": True,
                                "output": {"error": _friendly_err},
                            }
                        ),
                        0,
                        "",
                    )

                if action == RetryAction.FALLBACK:
                    if _fallback_used:
                        logger.error("[reactor] non-streaming fallback already used, giving up")
                        # 走 GIVE_UP 同款友好报错（复用上方分支逻辑不现实，直接返回）
                        _fallback_err = (
                            "抱歉，模型服务暂时不可用（连续多次连接失败），请稍后重试；"
                            "若持续出现请检查网络或 DeepSeek 服务状态。"
                        )
                        circuit.record_failure(str(e))
                        await self._emit_sse(
                            "llm_retry_status",
                            {
                                "node_id": "reactor",
                                "attempt": max(state.retries, state.connection_retries),
                                "final": True,
                                "label": _fallback_err,
                            },
                        )
                        return (
                            json.dumps(
                                {
                                    "thought": _fallback_err,
                                    "final": True,
                                    "output": {"error": _fallback_err},
                                }
                            ),
                            0,
                            "",
                        )
                    logger.info("[reactor] falling back to non-streaming LLM call")
                    await self._emit_sse(
                        "llm_retry_status",
                        {
                            "node_id": "reactor",
                            "attempt": max(state.retries, state.connection_retries),
                            "label": "模型服务响应异常，正在切换备用调用方式…",
                        },
                    )
                    _fallback_used = True
                    use_stream = False
                    state.retries = 0
                    continue

                if action == RetryAction.RETRY:
                    delay = state.next_delay()
                    logger.warning(
                        f"[reactor] LLM call retry {state.retries} " f"(delay={delay:.1f}s): {e}"
                    )
                    # 重试进度推给前端，避免长退避期间用户端完全静默
                    # （2026-11-07「3 分钟无回复」根因之一）。
                    _retry_total = (
                        state.max_connection_retries
                        if state.connection_retries > 0
                        else state.max_retries
                    )
                    _retry_cur = (
                        state.connection_retries if state.connection_retries > 0 else state.retries
                    )
                    await self._emit_sse(
                        "llm_retry_status",
                        {
                            "node_id": "reactor",
                            "attempt": _retry_cur,
                            "total": _retry_total,
                            "delay": delay,
                            "label": (
                                f"模型服务连接不稳定，正在重试（{_retry_cur}/{_retry_total}），"
                                f"{delay:.0f} 秒后自动继续…"
                            ),
                        },
                    )
                    await asyncio.sleep(delay)
                    continue

                logger.error(f"[reactor] unexpected RetryAction: {action}")
                return (
                    json.dumps(
                        {
                            "thought": f"LLM call failed: {e}",
                            "final": True,
                            "output": {"error": str(e)},
                        }
                    ),
                    0,
                    "",
                )

    def _resolve_intent_skill(self, user_intent: str) -> Any | None:
        """用 trigger_words 把用户意图解析到目标技能（预检澄清专用）。

        规则：
          - 统计每个技能明确命中的触发词个数；全部未命中 → None；
          - 必须有唯一最高分者才算定位成功——并列（意图模糊）时返回 None，
            交给循环里的 LLM 选技能，选中后由 dispatch 前的澄清门兜底。

        2026-10 重构：trigger_words 只用于预检澄清的技能定位，
        不用于意图路由（路由交给 LLM）。匹配策略改为完整词匹配
        （词边界），避免"做"在"怎么做"里误命中。
        """
        if not self._skills:
            return None
        intent = (user_intent or "").lower()
        if not intent.strip():
            return None
        scored: list[tuple[int, Any]] = []
        for skill in self._skills:
            words = getattr(skill, "trigger_words", None) or []
            score = 0
            for w in words:
                if not w:
                    continue
                w_lower = str(w).lower()
                if w_lower in intent:
                    w_len = len(w_lower)
                    idx = intent.index(w_lower)
                    before_ok = idx == 0 or not intent[idx - 1].isalnum()
                    after_ok = idx + w_len >= len(intent) or not intent[idx + w_len].isalnum()
                    if before_ok and after_ok:
                        score += 2
                    else:
                        score += 1
            if score > 0:
                scored.append((score, skill))
        if not scored:
            logger.info(
                f"[reactor] pre-flight: no trigger_words matched for intent={user_intent[:60]!r}"
            )
            return None
        best_score = max(s for s, _ in scored)
        winners = [sk for s, sk in scored if s == best_score]
        if len(winners) != 1:
            logger.info(
                f"[reactor] pre-flight intent skill ambiguous: {len(winners)} "
                f"candidates at score {best_score}, skipping clarification"
            )
            return None
        return winners[0]

    def _check_user_ask_rules(
        self,
        tool_name: str,
    ) -> dict[str, Any] | None:
        """Layer 3: 用户显式规则——permissions.ask 中的技能无条件弹澄清。

        对标 Claude Code 的 permissions.ask 配置：
        用户声明"每次做卡片都问我" → 无条件弹，不管 LLM 是否已传参数。
        """
        policy = None
        if hasattr(self, "_guardian") and self._guardian:
            policy = self._guardian._policies.get(tool_name)
        if not policy or not getattr(policy, "needs_clarification", False):
            return None

        target = next(
            (s for s in self._skills if getattr(s, "name", "") == tool_name),
            None,
        )
        if not target or not getattr(target, "clarify_meta", None):
            return None

        from app.agents.clarification_graph import build_batches, collect_clarify_fields

        fields = collect_clarify_fields([target])
        batches = build_batches(fields)
        if not batches:
            return None

        logger.info(f"[reactor] Layer 3: user ask rule triggered for skill={tool_name}")
        return {
            "_awaiting_clarification": True,
            "_clarification_prompt": batches[0].title,
            "_clarification_skill": tool_name,
            "_clarification_arguments": {},
            "_clarification_batches": [b.model_dump() for b in batches],
        }

    def _classifier_fallback_check(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        intent_skill: Any | None = None,
    ) -> dict[str, Any] | None:
        """Layer 2: 分类器兜底——只在 LLM 漏判时补弹。

        触发条件（全部满足才弹）：
        1. 技能有 clarify_meta（声明了可配置项）
        2. 技能在 CLARIFY_TRIGGER_NODE_TYPES 内
        3. LLM 或用户有承诺度（至少传了一个专属参数或之前答过澄清）：
           - LLM 传了部分参数但漏了关键字段 → LLM 确实想调，只是拿不准 → 补弹
           - 用户之前答过部分字段 → 确实要用这个技能 → 补弹
           - LLM 没传任何专属参数且用户也没答过 → 可能是误路由 → 不弹，
             让 LLM 自纠正（走 Layer 1 的 ask_user_question 或 final:true）
        4. 关键参数确实缺失：
           - 不在 tool_args 中（LLM 没传）
           - 不在 clarification_result 中（用户没答过）
           - 没有默认值（ClarifyFieldMeta.default 为空且 auto_default=False）
        5. LLM 本轮没有调过 ask_user_question（如果 LLM 已经问了但用户还没答，
           不应该再补弹——那是正常的等待状态）

        不触发（和旧 _check_needs_clarification 的关键区别）：
        - LLM 没传任何专属参数且用户也没答过 → 不弹（尊重 LLM 判断，可能是误路由）
        - LLM 已经在 tool_args 中显式传了该参数 → 不弹
        - 用户之前已经回答过 → 不弹
        - 参数有默认值且 auto_default=True → 不弹
        """
        _ctx = getattr(self, "_run_context", {}) or {}
        _clarification_result = _ctx.get("clarification_result", {}) or {}

        # 0. 用户本轮已参与过澄清（answer/skip/cancel 后的续跑，
        #    由 /clarify 端点在 context 打 user_clarified=True）：
        #    Layer 1（LLM 自由提问）与 Layer 2（技能表单）键空间不同，
        #    用户答完 Layer 1 后技能字段仍可能"缺失"，此时 Layer 2 再
        #    补弹就是"不相干澄清单"死循环。已参与 → 本轮静默，缺参由
        #    LLM 走 Layer 1 用自然语言追问（答案已在 observation 里）。
        if _ctx.get("user_clarified"):
            logger.info(
                f"[reactor] Layer 2: skipping clarification for skill={tool_name} "
                f"— user already participated in clarification this turn"
            )
            return None

        # 1. 定位目标技能
        target = None
        if tool_name == "skill_call":
            tool_name = str(tool_args.get("skill") or "")
        if tool_name:
            target = next(
                (s for s in self._skills if getattr(s, "name", "") == tool_name),
                None,
            )
        elif intent_skill is not None:
            target = intent_skill
        if target is None:
            return None

        # 2. 目标技能必须在校验节点类型内且有澄清元数据
        if getattr(target, "node_type", "") not in self._CLARIFY_TRIGGER_NODE_TYPES:
            return None
        clarify_meta = getattr(target, "clarify_meta", None)
        if not clarify_meta or not isinstance(clarify_meta, dict):
            return None

        from app.agents.clarification_schema import ClarifyFieldMeta, ClarifyWhen

        # 3. LLM 承诺度守卫：如果 LLM 一个该技能的专属参数都没传，
        #    且用户之前也没答过任何该技能的澄清字段，说明 LLM 自己都
        #    不确定要调这个技能（可能是误路由），此时 Layer 2 不应拦截
        #    ——尊重 LLM 的判断，让它自己处理（走 Layer 1 的
        #    ask_user_question 或直接 final:true 回复）。
        #    只有 LLM 传了部分参数，或用户之前答过部分字段时，才说明
        #    确实要用这个技能，此时 Layer 2 补弹才合理。
        _llm_committed = any(
            fname in tool_args and tool_args[fname] is not None for fname in clarify_meta
        )
        _user_committed = any(fname in _clarification_result for fname in clarify_meta)
        if not _llm_committed and not _user_committed:
            logger.info(
                f"[reactor] Layer 2: skipping clarification for skill={tool_name} "
                f"— LLM passed no skill-specific args and no prior user answers "
                f"(likely misroute), letting LLM self-correct"
            )
            return None

        needs_clarify = False
        for fname, fmeta in clarify_meta.items():
            if not isinstance(fmeta, ClarifyFieldMeta):
                continue
            when_val = fmeta.when

            # LLM 已在 tool_args 中显式传入 → 跳过
            # 但 auto_default=False 的字段（如 recipe_id/theme_id）必须由用户拍板，
            # LLM 填的值不算"已确认"——LLM 倾向填默认值绕过澄清闸门。
            if fname in tool_args and tool_args[fname] is not None:
                if getattr(fmeta, "auto_default", True):
                    continue
                if fname in _clarification_result:
                    continue
                if self.auto_approve:
                    continue

            # 用户之前已回答 → 跳过
            if fname in _clarification_result:
                continue

            # 有默认值且 auto_default=True → 跳过（用默认值）
            if (
                getattr(fmeta, "auto_default", False)
                and getattr(fmeta, "default", None) is not None
            ):
                continue

            # 确实缺失 → 需要补弹
            if when_val == ClarifyWhen.always:
                needs_clarify = True
                break
            if when_val == ClarifyWhen.missing:
                needs_clarify = True
                break
            if when_val == ClarifyWhen.ambiguous:
                needs_clarify = True
                break
        if not needs_clarify:
            return None

        from app.agents.clarification_graph import build_batches, collect_clarify_fields

        fields = collect_clarify_fields([target], clarified_answers=_clarification_result)
        if not fields:
            return None
        batches = build_batches(fields, clarified_answers=_clarification_result)
        if not batches:
            return None

        skill_name = getattr(target, "name", tool_name)
        prompt_title = batches[0].title if batches else "创作偏好确认"

        logger.info(
            f"[reactor] Layer 2: classifier fallback clarification needed for "
            f"skill={skill_name} (LLM did not ask, params missing)"
        )

        return {
            "_awaiting_clarification": True,
            "_clarification_prompt": prompt_title,
            "_clarification_skill": skill_name,
            "_clarification_arguments": tool_args,
            "_clarification_batches": [b.model_dump() for b in batches],
        }

    def _ensure_collab(self) -> Any | None:
        """懒初始化协作基础设施（AgentManager + InterAgentBus + ConcurrencyPool）。

        首次 spawn_agent 调用时创建，之后复用同一实例。
        collab_mode=disabled 时返回 None，LLM 看不到协作工具。
        """
        if self._collab_mode == "disabled":
            return None

        if self._collab_tools is not None:
            return self._collab_tools

        try:
            from app.engine.collab import (
                AgentManager,
                CollaborationTools,
                ConcurrencyPool,
                InterAgentBus,
            )

            manager = AgentManager(
                max_concurrent=3,
                max_depth=3,
            )
            bus = InterAgentBus()
            pool = ConcurrencyPool(max_slots=3)

            root_handle = manager.register_root("root")
            self._root_agent_id = root_handle.agent_id

            self._collab_tools = CollaborationTools(
                agent_manager=manager,
                message_bus=bus,
                concurrency_pool=pool,
            )

            # 设置子 Agent 执行器：用当前 session 的 LLM + primitives 跑 ReAct 循环
            async def _sub_agent_executor(agent_id: str, task_description: str) -> Any:
                # 专家模式：spawn 时若带了已注册 Agent 定义，子 Agent 用它自己的
                # 人格 / 技能白名单 / 模型 / 迭代上限，而不是复制父 Agent 的全量能力
                adef = getattr(manager.get(agent_id), "_registered_agent_def", None)
                sub_llm = self.llm
                sub_skills = self._skills
                sub_max_iter = max(self.max_iterations // 2, 4)
                sub_prefix = None
                if adef is not None:
                    if adef.llm_model:
                        from app.engine.factory import get_deepseek_llm

                        sub_llm = get_deepseek_llm(model=adef.llm_model) or self.llm
                    if adef.skills is not None:
                        from app.agents.registry import _build_skills

                        sub_skills = _build_skills(adef.skills, self._creation_type)
                    sub_max_iter = adef.max_iterations
                    sub_prefix = adef.persona or None
                sub_session = ReactorSession(
                    llm=sub_llm,
                    allowed_permissions=self.allowed_permissions,
                    max_iterations=sub_max_iter,
                    skills=sub_skills,
                    observer=self.observer,
                    sse_bus=self.sse_bus,
                    workflow_id=self.workflow_id,
                    auto_approve=self.auto_approve,
                    collab_mode=self._collab_mode,
                    system_prompt_prefix=sub_prefix,
                )
                # 一次性确认跟随主会话：用户本轮已批准过 skill_call，
                # 子 Agent 里同类低风险调用不该再弹卡打断。
                sub_session.skill_call_once_confirmed = self.skill_call_once_confirmed
                try:
                    result = await sub_session.run(task_description)
                    return result
                except Exception as e:
                    logger.error(f"[reactor] sub-agent {agent_id} execution failed: {e}")
                    return {"ok": False, "error": str(e)}

            manager.set_executor(_sub_agent_executor)

            logger.info(
                f"[reactor] collab infrastructure initialized: "
                f"root={self._root_agent_id}, mode={self._collab_mode}"
            )
            return self._collab_tools
        except Exception as e:
            logger.warning(f"[reactor] collab infrastructure init failed: {e}")
            return None

    async def _dispatch(self, tool_call: dict[str, Any]) -> dict[str, Any]:
        """Dispatch a tool call through Guardian safety gate to the corresponding primitive.

        Pipeline: Step 0 (clarification) → Guardian.check() → permission gate → primitive.execute()
        - CLARIFY: return clarification request, loop breaks
        - ALLOW: proceed to execute
        - DENY: return error as observation (loop continues)
        - NEEDS_CONFIRM: auto_approve=True → 一律放行（仅 DENY 硬拒）；
          auto_approve=False → 弹确认卡（skill_call 一次性确认后 low/medium 放行）
        """
        from app.engine.governance.guardian import GuardianAction

        name = tool_call.get("name", "")
        args = tool_call.get("arguments", {})

        if not isinstance(args, dict):
            args = {}

        ctx = DispatchContext(
            run_context=getattr(self, "_run_context", {}) or {},
            workflow_id=self.workflow_id or "",
            turn_id=self.turn_id,
        )

        if name == "skill_call":
            if "skill" not in args and "arguments" not in args:
                _maybe_skill = args.get("name", "")
                if _maybe_skill and _maybe_skill != "skill_call" and _maybe_skill != name:
                    args["skill"] = _maybe_skill
                    logger.info(
                        f"[reactor] skill_call: promoted args.name='{_maybe_skill}' → args.skill"
                    )
            logger.info(
                f"[reactor] skill_call dispatch: args_keys={list(args.keys())}, args_preview={json.dumps(args, ensure_ascii=False, default=str)[:500]}"
            )
            _deep_args = args.get("arguments") or args.get("args") or args.get("parameters") or {}
            if _deep_args and isinstance(_deep_args, dict):
                logger.info(
                    f"[reactor] skill_call deep arguments: keys={list(_deep_args.keys())}, preview={json.dumps(_deep_args, ensure_ascii=False, default=str)[:500]}"
                )

        # 二期钩子链（pre_clarify）：协议解包只影响澄清判定目标，不改主调用形态
        clarify_name, clarify_args = name, args
        try:
            for hook in self._dispatch_hooks:
                clarify_name, clarify_args = await hook.pre_clarify(clarify_name, clarify_args, ctx)
        except Exception as e:
            logger.warning(f"[reactor] pre_clarify hook failed: {e}")

        # Step 0: 三层澄清体系
        #   ① Layer 3: 用户显式规则（permissions.ask）
        #   ② Layer 1: LLM 自决策（ask_user_question 工具，由 LLM 自己调）
        #   ③ Layer 2: 分类器兜底（_classifier_fallback_check）
        # ① Layer 3: 用户显式规则
        _layer3_signal = self._check_user_ask_rules(clarify_name)
        if _layer3_signal is not None:
            logger.warning(
                f"[reactor] Layer 3 clarification triggered in _dispatch for tool={clarify_name}, args_keys={list(clarify_args.keys()) if isinstance(clarify_args, dict) else 'N/A'}"
            )
            return _layer3_signal

        # ② Layer 1: LLM 自决策——如果 LLM 调了 ask_user_question，
        #    AskUserQuestionPrimitive.execute() 会返回 _awaiting_clarification 信号，
        #    在 primitive.execute() 之后由 _dispatch 的返回值处理。
        #    这里不需要额外代码，LLM 调 ask_user_question 和调其他工具走同一路径。

        # ③ Layer 2: 分类器兜底——只在 LLM 漏判时补弹
        _clarification_signal = self._classifier_fallback_check(clarify_name, clarify_args)
        if _clarification_signal is not None:
            logger.warning(
                f"[reactor] Layer 2 clarification triggered in _dispatch for tool={clarify_name}, args_keys={list(clarify_args.keys()) if isinstance(clarify_args, dict) else 'N/A'}"
            )
            return _clarification_signal

        # 二期钩子链（pre_execute）：上下文注入等参数准备，产出交给 transport
        try:
            for hook in self._dispatch_hooks:
                name, args = await hook.pre_execute(name, args, ctx)
        except Exception as e:
            logger.warning(f"[reactor] pre_execute hook failed: {e}")

        # 二期契约带②：元数据经 transport，执行也经 transport
        tool = self._transport.get_tool(name)
        if tool is None:
            return {
                "ok": False,
                "error": f"unknown tool: {name}",
                "available_tools": list(self.primitives.keys()),
            }

        decision = await self._guardian.check(name, args)

        if decision.action == GuardianAction.DENY:
            logger.warning(f"[reactor] guardian DENIED {name}: {decision.reason}")
            return {"ok": False, "error": decision.to_observation()}

        if decision.action == GuardianAction.NEEDS_CONFIRM:
            _is_skill_call = name == "skill_call"
            _inner_skill = ""
            if _is_skill_call:
                _inner_skill = str(
                    (args.get("arguments") or {}).get("skill") or args.get("skill") or ""
                )
                if not _inner_skill:
                    logger.warning(
                        f"[reactor] skill_call args keys={list(args.keys())}, args_preview={str(args)[:200]}"
                    )
            if self.auto_approve:
                # 自动放行 = 用户在输入框上显式授权「免确认」：Guardian 判
                # NEEDS_CONFIRM 的工具一律放行，只保留 DENY（危险参数硬拒）。
                # 此前只放行 low/medium，high 风险（file_write / 非只读 bash）
                # 仍弹卡，用户开了自动放行却反复被拦，开关形同虚设。
                # 内核不持有业务技能名清单（纯净度契约）：放行与否只看
                # Guardian 给出的 risk_level。技能名仅用于日志定位。
                logger.debug(
                    f"[reactor] guardian auto-approved {name} (risk={decision.risk_level})"
                    + (f" skill={_inner_skill}" if _inner_skill else "")
                )
            elif (
                _is_skill_call
                and self.skill_call_once_confirmed
                and decision.risk_level in ("low", "medium")
            ):
                # 一次性确认：需确认模式下本轮用户已批准过 skill_call 的，
                # low/medium 直接放行，不再重复弹卡（high 风险永远弹卡，
                # 安全底线不放宽）。
                logger.info(
                    f"[reactor] skill_call one-shot confirm already granted, auto-approving: {_inner_skill or name} (risk={decision.risk_level})"
                )
            else:
                logger.warning(
                    f"[reactor] guardian needs confirm for {name} (risk={decision.risk_level})"
                )
                if _is_skill_call:
                    return {
                        "ok": False,
                        "error": f"skill '{_inner_skill}' requires user confirmation. {decision.hint or decision.user_hint or '是否执行此操作？'}",
                        "needs_confirmation": True,
                        "confirmation_prompt": decision.confirmation_prompt
                        or f"技能 {_inner_skill} 即将执行，是否继续？",
                    }
                return {
                    "ok": False,
                    "error": f"tool '{name}' requires user confirmation (risk: {decision.risk_level}). {decision.hint or decision.user_hint}",
                    "needs_confirmation": True,
                    "confirmation_prompt": decision.confirmation_prompt,
                }

        # 四期：技能级权限声明门禁——技能 frontmatter 声明 permissions 的，
        # 调用前必须已在 session.allowed_permissions；未声明的技能不受影响（向后兼容）
        if clarify_name:
            try:
                from app.core.skill_registry import get_skill_class_by_name

                _skill_cls = get_skill_class_by_name(clarify_name)
                _declared = (
                    tuple(getattr(_skill_cls, "skill_permissions", ()) or ()) if _skill_cls else ()
                )
                for perm_value in _declared:
                    perm = Permission(perm_value)
                    if perm not in self.allowed_permissions:
                        logger.warning(
                            f"[reactor] skill '{clarify_name}' requires {perm.value} not granted"
                        )
                        return {
                            "ok": False,
                            "error": f"permission denied: {perm.value} (declared by skill '{clarify_name}'). Ask the user to grant this permission.",
                            "required": perm.value,
                        }
            except ValueError:
                logger.warning(
                    f"[reactor] skill '{clarify_name}' declared invalid permission value"
                )
            except Exception:
                logger.debug("[reactor] skill permission lookup skipped", exc_info=True)

        for perm in tool.required_permissions:
            if perm not in self.allowed_permissions:
                return {
                    "ok": False,
                    "error": f"permission denied: {perm.value}. Ask the user to grant this permission.",
                    "required": perm.value,
                }

        # 协作基础设施注入：skill_call 调协作工具时，注入 _agent_manager /
        # _message_bus / _concurrency_pool / _caller_agent_id / _observer
        if name == "skill_call":
            inner_skill = str((args.get("arguments") or {}).get("skill") or args.get("skill") or "")
            _COLLAB_SKILLS = {
                "spawn_agent",
                "send_message",
                "wait_agent",
                "interrupt_agent",
                "list_agents",
            }
            if inner_skill in _COLLAB_SKILLS:
                collab = self._ensure_collab()
                if collab is not None:
                    inner_args = args.get("arguments")
                    if not isinstance(inner_args, dict):
                        inner_args = {}
                        args["arguments"] = inner_args
                    collab.inject_params(inner_args, self._root_agent_id or "root")
                    inner_args["_observer"] = self.observer

        # 超时 / TypeError / 异常 → observation 的转换全部在 transport 内（与旧执行段等价）
        result = await self._transport.call(name, args)

        # P2-2 二层渐进披露：skill_call 首次调用某技能时，返回值附带
        # skill_doc（完整纪律 + 未截断参数说明）。目录（一层）已瘦身，
        # LLM 凭 desc 猜错参数时由此立即修正；每技能只附一次。
        # 2026-10-29：skill_doc 必须置顶——observation 超 4000 字会被尾部截断，
        # 挂到末尾等于「调用失败时说明书正好被切掉」，修正能力归零。
        if isinstance(result, dict):
            result = self._attach_skill_doc(name, args, result)

        return result

    def _attach_skill_doc(
        self,
        name: str,
        args: dict[str, Any],
        result: dict[str, Any],
    ) -> dict[str, Any]:
        """skill_call 首次调用某技能时，把 skill_doc 作为返回值的第一个键附上。

        哨兵只记「确实发过 doc」的技能：未注册技能 doc 为空，若也记哨兵，
        会把幽灵技能名污染进集合（误导后续审计）。
        """
        if name != "skill_call":
            return result
        nested = args.get("arguments")
        inner_skill = str(
            (nested if isinstance(nested, dict) else {}).get("skill") or args.get("skill") or ""
        )
        if not inner_skill or inner_skill in self._skill_doc_sent:
            return result
        doc = self._build_skill_doc(inner_skill)
        if not doc or "skill_doc" in result:
            return result
        self._skill_doc_sent.add(inner_skill)
        return {"skill_doc": doc, **result}

    def _build_skill_doc(self, skill_name: str) -> str:
        """按名查 _skills 全集构建该技能的二层说明（查不到返回空）。"""
        for s in self._skills:
            if getattr(s, "name", "") == skill_name:
                return build_skill_doc(s)
        return ""

    def _parse_step(self, content: str) -> dict[str, Any]:
        """Parse LLM output as a ReAct step."""
        text = content.strip()

        # DeepSeek DSML native tool-call format: <｜｜DSML｜｜ calls>...</｜｜DSML｜｜ calls>
        dsml_result = self._parse_dsml_tool_calls(text)
        if dsml_result is not None:
            return dsml_result

        if text.startswith("```"):
            text = self._strip_code_fence(text)

        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

        # LLM JSON 常见瑕疵修复（未转义引号/裸换行/尾逗号）后重试
        repaired = self._repair_react_json(text)
        if repaired is not None:
            return repaired

        json_in_text = self._extract_json_from_text(text)
        if json_in_text:
            coerced = self._coerce_skill_json(json_in_text)
            if coerced is not None:
                return coerced
            return json_in_text

        return {"thought": content, "tool_calls": [], "final": False}

    @staticmethod
    def _looks_like_react_protocol(text: str) -> bool:
        """判断一段无法解析的输出是否是 ReAct 协议 JSON 的残片。

        用于"纯问答直出"分支的拦截：协议 JSON 哪怕括号缺损解析失败，
        也绝不能当自然语言原样发给用户。判定从宽——开头是 `{`（剥掉
        ```json 围栏后）且带任一协议关键字段名即命中。
        """
        t = text.strip()
        if t.startswith("```"):
            first_nl = t.find("\n")
            t = (t[first_nl + 1 :] if first_nl >= 0 else t).strip()
        if not t.startswith("{"):
            return False
        return '"tool_calls"' in t or '"thought"' in t or '"final"' in t

    @staticmethod
    def _repair_react_json(text: str) -> dict[str, Any] | None:
        """修复 LLM 输出 JSON 的常见瑕疵后再解析。

        观测到的失败模式（DeepSeek/Qwen 系）：字符串里带未转义的引号、
        裸换行、对象尾部多余逗号——json.loads 直接判死，而
        _extract_json_from_text 只做括号平衡扫描，救不了语法错误，
        最终 nudge 耗尽导致智能体「想了却不做」。逐字符扫描修复：
        字符串外原样保留；字符串内把裸控制字符转义、对「后面不跟
        ,}]: 的引号」补反斜杠；最后剥尾逗号再 strict=False 解析。

        2026-11-04 新增：缺失闭合括号补齐。实锤样本（clarify 续跑首轮）：
        模型输出 ReAct 协议 JSON 时漏了 tool_calls 数组的 `]` 和根对象的
        `}`，三段解析全救不回，残片掉进"纯问答直出"分支把裸 JSON
        原样发给用户。扫描时维护括号栈，结尾把未闭合的按逆序补上。
        """
        out: list[str] = []
        in_string = False
        i = 0
        n = len(text)
        bracket_stack: list[str] = []
        while i < n:
            ch = text[i]
            if not in_string:
                if ch == '"':
                    in_string = True
                elif ch in "{[":
                    bracket_stack.append("}" if ch == "{" else "]")
                elif ch in "}]" and bracket_stack and bracket_stack[-1] == ch:
                    bracket_stack.pop()
                out.append(ch)
                i += 1
                continue
            if ch == "\\" and i + 1 < n:  # 保留转义对
                out.append(ch)
                out.append(text[i + 1])
                i += 2
                continue
            if ch == '"':
                j = i + 1
                while j < n and text[j] in " \t\r\n":
                    j += 1
                if j >= n or text[j] in ",}]:":  # 真闭合
                    in_string = False
                    out.append(ch)
                else:  # 字符串内未转义引号 → 补转义
                    out.append('\\"')
                i += 1
                continue
            if ch == "\n":
                out.append("\\n")
            elif ch == "\r":
                out.append("\\r")
            elif ch == "\t":
                out.append("\\t")
            else:
                out.append(ch)
            i += 1
        repaired_body = "".join(out)
        # 缺失闭合括号补齐（逆序追加；先补括号再剥尾逗号，`...,` + `]}` → `...]}`→剥逗号→`...]}`）
        if bracket_stack:
            repaired_body += "".join(reversed(bracket_stack))
        repaired = re.sub(r",\s*([}\]])", r"\1", repaired_body)
        try:
            parsed = json.loads(repaired, strict=False)
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            return None

    @staticmethod
    def _coerce_skill_json(parsed: dict[str, Any]) -> dict[str, Any] | None:
        """把 `{"skill": ..., "arguments": {...}}` 这种裸技能调用补齐成 ReAct step。

        模型有时只吐技能名+参数、不吐 `tool_calls` 外壳（DeepSeek 原生
        tool-call token 被 API 吞掉开头时尤其常见），这里补成 skill_call，
        否则整段协议残片会被当成「纯问答」直出给用户。
        """
        if not isinstance(parsed, dict) or "tool_calls" in parsed:
            return None
        skill = parsed.get("skill") or parsed.get("skill_name")
        if not isinstance(skill, str) or not skill.strip():
            return None
        args = parsed.get("arguments") or parsed.get("args") or parsed.get("parameters") or {}
        if not isinstance(args, dict):
            return None
        return {
            "thought": "",
            "tool_calls": [
                {"name": "skill_call", "arguments": {"skill": skill.strip(), "arguments": args}}
            ],
            "final": False,
        }

    @staticmethod
    def _parse_dsml_tool_calls(text: str) -> dict[str, Any] | None:
        """Parse DeepSeek native <｜｜DSML｜｜> tool-call format into ReAct JSON.

        Format:
            <｜｜DSML｜｜ calls>
            <｜｜DSML｜｜ invoke name="tool_name">
            <｜｜DSML｜｜ parameter name="param_name" string="true">value</｜｜DSML｜｜ parameter>
            </｜｜DSML｜｜ invoke>
            </｜｜DSML｜｜ calls>

        Returns ReAct dict or None if not DSML format.
        """
        if "<｜｜DSML｜｜" not in text:
            return None

        tool_calls = []
        invoke_pattern = re.compile(r'<｜｜DSML｜｜ invoke name="([^"]+)">')
        param_pattern = re.compile(
            r'<｜｜DSML｜｜ parameter name="([^"]+)"(?:\s+string="true")?>(.*?)</｜｜DSML｜｜ parameter>',
            re.DOTALL,
        )
        invoke_end_pattern = re.compile(r"</｜｜DSML｜｜ invoke>")

        pos = 0
        while pos < len(text):
            invoke_match = invoke_pattern.search(text, pos)
            if not invoke_match:
                break

            tool_name = invoke_match.group(1)
            invoke_start = invoke_match.end()
            invoke_end_match = invoke_end_pattern.search(text, invoke_start)
            if not invoke_end_match:
                break

            invoke_body = text[invoke_start : invoke_end_match.start()]
            arguments = {}
            for param_match in param_pattern.finditer(invoke_body):
                param_name = param_match.group(1)
                param_value = param_match.group(2).strip()
                try:
                    parsed_value = json.loads(param_value)
                    arguments[param_name] = parsed_value
                except (json.JSONDecodeError, TypeError):
                    arguments[param_name] = param_value

            tool_calls.append({"name": tool_name, "arguments": arguments})
            pos = invoke_end_match.end()

        if not tool_calls:
            return None

        logger.debug(f"[reactor] parsed DSML tool calls: {[tc['name'] for tc in tool_calls]}")
        return {"thought": "", "tool_calls": tool_calls, "final": False}

    @staticmethod
    def _strip_code_fence(text: str) -> str:
        """Remove markdown code fences (```json ... ```)."""
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines)

    @staticmethod
    def _sanitize_reasoning(text: str) -> str:
        """思维链消洗：若 reasoning_content 实为 ReAct 协议 JSON，提取 thought 人话；否则原样返回。

        背景（2026-10-26）：deepseek 深度思考模型在 ReAct 任务上的思维链常常
        就是协议 JSON 草稿，原样透传到前端「深度思考」块 = 用户看到裸 JSON。
        返回 "" 表示协议原文无法人话化，调用方应整体拦下不透传。
        """
        s = (text or "").strip()
        if not s:
            return text or ""
        if s.startswith("```"):
            s = ReactorSession._strip_code_fence(s).strip()
        if not s or s[:1] not in ("{", "["):
            return text  # 自然语言思维链，不碰
        try:
            parsed = json.loads(s)
        except json.JSONDecodeError:
            # 半截 JSON（截断/带尾注）：兜底正则抠 thought 字符串
            m = re.search(r'"thought"\s*:\s*"((?:[^"\\]|\\.)*)"', s)
            if not m:
                # 无闭合引号的截断串：宽松匹配到结尾
                m2 = re.search(r'"thought"\s*:\s*"(.*)', s, re.DOTALL)
                if not m2:
                    return ""
                try:
                    return json.loads(f'"{m2.group(1)}"')
                except json.JSONDecodeError:
                    return m2.group(1)
            try:
                return json.loads(f'"{m.group(1)}"')
            except json.JSONDecodeError:
                return m.group(1)
        if isinstance(parsed, dict):
            thought = parsed.get("thought")
            return thought if isinstance(thought, str) else ""
        return ""

    @staticmethod
    def _extract_json_from_text(text: str) -> dict[str, Any] | None:
        """Try to find a JSON object in free-form text.

        Strategy: find candidate '{' positions, then try json.loads on
        progressively larger substrings until we find valid JSON.
        This handles string-internal braces (e.g. "use {key} format")
        by not breaking on the first failed json.loads — we continue
        to the next candidate position.
        """
        for i, ch in enumerate(text):
            if ch == "{":
                depth = 0
                for j in range(i, len(text)):
                    if text[j] == "{":
                        depth += 1
                    elif text[j] == "}":
                        depth -= 1
                        if depth == 0:
                            try:
                                parsed = json.loads(text[i : j + 1])
                                if isinstance(parsed, dict):
                                    return parsed
                            except json.JSONDecodeError:
                                continue
                if depth == 0:
                    continue
        return None

    def _maybe_truncate_messages(
        self,
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Simple truncation: keep system + last N messages.

        WARNING: This is a lossy truncation — early messages (including critical
        decisions) are dropped. A future improvement should use ContextCompactor
        (LLM-based summarization) instead of brute-force truncation.
        """
        if len(messages) <= _MESSAGES_MAX_LEN:
            return messages

        dropped = len(messages) - _MESSAGES_MAX_LEN
        logger.warning(
            f"[reactor] context truncation: {len(messages)} messages > "
            f"{_MESSAGES_MAX_LEN} limit, dropping {dropped} early messages. "
            f"Consider enabling ContextCompactor for lossless compression."
        )

        system_msgs = [m for m in messages if m.get("role") == "system"]
        non_system = [m for m in messages if m.get("role") != "system"]
        keep = _MESSAGES_MAX_LEN - len(system_msgs)
        tail = non_system[-keep:]

        # Protect assistant-observation pairing: if the first kept message
        # is an observation (role=user, starts with [observation]) without
        # its preceding assistant thought, drop it to avoid orphaned obs.
        if (
            tail
            and tail[0].get("role") == "user"
            and str(tail[0].get("content", "")).startswith("[observation]")
        ):
            logger.warning("[reactor] truncation would orphan observation, dropping it")
            tail = tail[1:]

        return system_msgs + tail

    @staticmethod
    def _extract_output_from_messages(messages: list[dict[str, Any]]) -> dict[str, Any]:
        """Fallback: extract output from the last observation in messages."""
        for msg in reversed(messages):
            content = msg.get("content", "")
            if content.startswith("[observation]"):
                try:
                    json_part = content.split(" -> ", 1)[1]
                    return json.loads(json_part)
                except (IndexError, json.JSONDecodeError):
                    pass
        return {"raw_output": messages[-1].get("content", "") if messages else ""}

    def info(self) -> dict[str, Any]:
        """Return session info for debugging."""
        return {
            "primitives": list(self.primitives.keys()),
            "allowed_permissions": [p.value for p in self.allowed_permissions],
            "max_iterations": self.max_iterations,
            "token_count": self._token_count,
            "max_tokens": self._max_tokens,
            "workflow_id": self.workflow_id,
        }


def collect_reactor_skills() -> list[Any]:
    """从 SkillRegistry 收集全部已注册技能实例（P0-1 接线，全入口共用）。

    用途有二：
      1. get_reactor_system_prompt(skills=...) 生成「可用技能」目录——
         LLM 看不到技能清单就不会调 skill_call，"发图做视频"链路
         断在第 0 步（这是链路不通的根因之一）；
      2. ReactorSession._skills 供澄清门 _check_needs_clarification 使用
         （该门内部已按 node_type 过滤，全量列表不会误弹澄清）。

    失败静默返回 []：技能收集失败不阻断会话构建（基础 primitive 仍可用）。
    """
    try:
        from app.core.skill_registry import (
            SkillRegistry,
            ensure_builtin_skills_registered,
        )

        ensure_builtin_skills_registered()
        instances: list[Any] = []
        for skill_cls in SkillRegistry.instance().all_skill_classes():
            try:
                instances.append(skill_cls())
            except Exception:
                continue
        logger.debug(f"[reactor] collect_reactor_skills: {len(instances)} skill instances")
        return instances
    except Exception as e:
        logger.warning(f"[reactor] collect_reactor_skills failed: {e}")
        return []


def build_reactor_session(
    workflow_id: str = "",
    llm: Any | None = None,
    allowed_permissions: set[Permission] | None = None,
    max_iterations: int = _DEFAULT_MAX_ITER,
    auto_approve: bool = True,
    skills: list[Any] | None = None,
    max_token_budget: int = 200_000,
    collab_mode: str = "proactive",
    creation_type: str | None = None,
    system_prompt_prefix: str | None = None,
) -> ReactorSession:
    """Factory: build a ReactorSession with the given LLM, all primitives, and SSE.

    Args:
        workflow_id: SSE channel key.
        llm: Pre-constructed LLM adapter (e.g. DeepSeek with a specific model).
             If None, falls back to the default LLM via get_deepseek_llm().
        allowed_permissions: Permission set for primitive dispatch.
        max_iterations: ReAct loop cap.
        auto_approve: If True, Guardian auto-approves every NEEDS_CONFIRM tool
                      (only DENY is still hard-blocked); if False, such tools
                      pause for user confirmation.
        skills: 技能实例列表（SkillRegistry 全量，见 collect_reactor_skills）。
                None 时工厂自动收集——保证所有入口（AgentRegistry /
                reactor 路由 / 未来新入口）的系统提示都带「可用技能」目录，
                LLM 才知道用 skill_call 调业务技能（P0-1）。
        collab_mode: 协作模式 — "proactive"（默认，子 Agent 可主动协作）/
                     "explicit"（仅显式请求时协作）/"disabled"（禁用子 Agent）。
    """
    if llm is None:
        from app.engine.factory import get_deepseek_llm

        llm = get_deepseek_llm(model="deepseek-chat")
        if llm is None:
            logger.error(
                "[reactor] build_reactor_session: no LLM available "
                "(get_deepseek_llm(model='deepseek-chat') returned None). "
                "ReAct loop will immediately terminate with error. "
                "Check API key / circuit breaker."
            )

    if skills is None:
        skills = collect_reactor_skills()

    # 能力侧业务钩子（延迟 import：agents 包顶层可能反向依赖 engine）
    try:
        from app.agents.dispatch_hooks import DEFAULT_DISPATCH_HOOKS
    except Exception:
        DEFAULT_DISPATCH_HOOKS = ()
        logger.warning("[reactor] agents dispatch hooks unavailable, business hooks disabled")

    observer = None
    sse_bus = None

    if workflow_id:
        try:
            from app.engine.factory import _make_observer

            observer = _make_observer(workflow_id)
        except Exception:
            pass
        try:
            from app.services.sse_bus import sse_bus as _sse_bus

            sse_bus = _sse_bus
        except Exception:
            pass

    return ReactorSession(
        llm=llm,
        allowed_permissions=allowed_permissions or set(),
        max_iterations=max_iterations,
        observer=observer,
        sse_bus=sse_bus,
        workflow_id=workflow_id,
        auto_approve=auto_approve,
        skills=skills,
        dispatch_hooks=list(DEFAULT_DISPATCH_HOOKS),
        max_token_budget=max_token_budget,
        collab_mode=collab_mode,
        creation_type=creation_type,
        system_prompt_prefix=system_prompt_prefix,
    )
