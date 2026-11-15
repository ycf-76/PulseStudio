/**
 * sse-contract.ts — 前后端 SSE 事件契约单一事实源（2026-10-18 重构）
 *
 * 维护规则（来自 .dsh→.rl 教训：契约必须单一来源）：
 * - 后端 app/engine/reactor/session.py / app/api/routers/chat_agent.py / app/skills/*
 *   任何 event_type 增删，必须同步本文件与 useChatSSE.handleWorkflowEvent。
 * - 前端只允许消费 Ev 表内事件；表外事件一律视为「后端未接入」，不收 case，
 *   避免假大空（前端写了渲染逻辑而后端永远不发）。
 *
 * 活事件 24 类（chat SSE 通道 = /api/sse/chat/{session_id}，channel 即 session_id）：
 *   reactor session 13 + chat_agent 6 + skills 4 + reactor.py(stream 端点) 1
 *
 * 已移除的死事件（后端零发射，曾导致前端假大空）：
 *   node_status_changed      — 后端只发 node_started/node_completed
 *   workflow_failed          — 后端异常统一走 workflow_error
 *   workflow_cancelled       — 无发射方
 *   review_required          — app/api/routers/review.py 已随工作流引擎拆除
 *   recovery_attempt / recovery_attempt_failed / recovery_success /
 *   recovery_exhausted / circuit_open — reactor/session.py 明示
 *                            "No AgentHarness dependency (no governance/recovery overhead)"
 *   intent_parsed / draft_patch / plan_ready / stream_chunk / workflow_progress
 *                          — sse_bus 常量表遗留，无任何 emit 点
 *   agent_confirm_required    — 高危确认走后端 agent_clarification_required 通道
 *   agent_clarification_expired — 后端改为 HTTP 410（chat_agent.py L1092）
 *   agent_clarification_hint  — 无发射方
 *   governance_budget_warning / governance_compaction / governance_retry /
 *   governance_context_window / governance_guardian_decision
 *                          — governance/hooks.py 无调用方（仅 __init__ 导出）
 *   collab_agent_spawned / collab_agent_completed / collab_agent_interrupted /
 *   collab_state_update     — agent_manager 调 observer.emit_trace，而
 *                            factory._SSEObserver 只提供 emit()，接口不匹配，死路。
 *                            子智能体信息由 tool_call_start(inputs.spawn_agent) 承载
 *   agent_message_clear / agent_message_completed
 *                          — 正文终态以 workflow_completed 为准（session L432 + chat_agent L930）
 *   agent_error             — reactor 路径异常走 workflow_error（reactor.py L142）
 *   video_job_succeeded / video_job_failed
 *                          — 无发射方（视频链路走 video_pipeline_progress）
 */

/** chat SSE 通道活事件。值即后端 event_type 字符串。 */
export const Ev = {
  /** chat_agent L825 — 新回合开始信号（旧 replay 事件的分界线） */
  CHAT_TURN_STARTED: 'chat_turn_started',
  /** session L165 — ReAct 循环启动 {intent, max_iterations, primitives} */
  WORKFLOW_STARTED: 'workflow_started',
  /** session L170 — reactor 节点开始 {node_id, node_type, intent}（步骤 running 态来源） */
  NODE_STARTED: 'node_started',
  /** session L189 — 每轮推理进度 {node_id, current, total, label} */
  PROGRESS_UPDATE: 'progress_update',
  /** session _call_llm — LLM 重试进度 {node_id, attempt, total?, delay?, final?, label}
   *  模型服务连接失败重试期间的用户可见信号（2026-11-07「3 分钟无回复」修复）；
   *  final=true 表示重试耗尽，label 是最终中文报错文案。 */
  LLM_RETRY_STATUS: 'llm_retry_status',
  /** session L543 — 思考增量 {node_id, delta} */
  REASONING_SUMMARY_DELTA: 'reasoning_summary_text_delta',
  /** session L203/560 — 思考结束 {node_id, iteration, summary_parts: string[]}（完整兜底文本） */
  REASONING_COMPLETED: 'reasoning_completed',
  /** session L550 — 正文增量 {node_id, delta} */
  AGENT_MESSAGE_DELTA: 'agent_message_delta',
  /** session L223 / chat_agent L707 — 决策（thought）{node_id, iteration, decision} */
  DECISION_MADE: 'decision_made',
  /** session L252 — 工具调用开始 {node_id, iteration, tool_name, inputs}（无 sub_agent_id 字段） */
  TOOL_CALL_START: 'tool_call_start',
  /** session L376 — 工具调用结束 {node_id, iteration, tool_name, success, summary, duration_ms} */
  TOOL_CALL_END: 'tool_call_end',
  /** copywrite builder L357 — 技能思考流 {node_id, chunk: {content}}（载荷是 chunk.content，不是 reasoning） */
  AGENT_THINKING: 'agent_thinking',
  /** session L425 — reactor 节点完成 {node_id, node_type, output, iterations, token_usage} */
  NODE_COMPLETED: 'node_completed',
  /** session L432 {output, iterations, token_usage}；chat_agent L930 {workflow_id} — 回合终态唯一信号 */
  WORKFLOW_COMPLETED: 'workflow_completed',
  /** reactor.py L142（/reactor/stream 端点）— run() 异常 {error}；chat 通道断流由订阅 finally 兜底 */
  WORKFLOW_ERROR: 'workflow_error',
  /** session L140/283 — 偏好澄清闸门 {node_id, iteration, clarification_prompt, clarification_skill, clarification_batches, loop_state_path} */
  CLARIFICATION_REQUIRED: 'agent_clarification_required',
  /** chat_agent L1195 — 澄清已回答 {batch_id, action} */
  CLARIFICATION_ACK: 'agent_clarification_ack',
  /** session L344 — 分镜确认闸门 {node_id, iteration, skill, title, shots, estimated_duration, loop_state_path} */
  STORYBOARD_REQUIRED: 'storyboard_confirmation_required',
  /** session L458 — Guardian 高危操作确认闸门 {node_id, iteration, tool_name, confirmation_prompt, risk_info, loop_state_path} */
  GUARDIAN_REQUIRED: 'guardian_confirmation_required',
  /** chat_agent L1322 — 分镜确认回放结束 {action, ok?, error?} */
  STORYBOARD_RESOLVED: 'storyboard_confirmation_resolved',
  /** session — 叠加方案确认闸门 {node_id, iteration, skill, plan, message, loop_state_path}
   *  （talking_head_overlay 规划出 OverlayPlan 后挂起，修复设计文档 3.4 #2 缺口2） */
  CUT_PLAN_REQUIRED: 'cut_plan_confirmation_required',
  /** session — 侧会话写副作用确认闸门（PRD G6）{node_id, iteration, tool_name, description, permissions, side_session_id, loop_state_path} */
  SIDE_WRITE_REQUIRED: 'side_write_confirmation_required',
  /** chat_agent confirm-cut-plan — 叠加方案确认回放结束 {action, ok?, error?} */
  CUT_PLAN_RESOLVED: 'cut_plan_confirmation_resolved',
  /** chat_agent L1037/1243 — 确认/澄清后后台续跑完成 {status, message}（最终文案来源） */
  AGENT_LOOP_DONE: 'agent_loop_done',
  /** produce skills — 卡片草稿就绪 {card_draft, png_urls, html_urls} */
  CARD_DRAFT_READY: 'card_draft_ready',
  /** work_edit — 草稿内容就地修改 {work_patch: {work_id, fields, applied}} */
  WORK_PATCH_READY: 'work_patch_ready',
  /** session — 工作区媒体产物已发布到 outputs/ {media_artifacts: [{title, kind, url, ...}], video_url}
   *  （2026-11-08：口播成片由 bash/ffmpeg 写在工作区，此前前端拿不到可渲染地址） */
  MEDIA_ARTIFACT_READY: 'media_artifact_ready',
  /** video skills（talking_head_overlay / auto_short_video）— 管线进度
   *  {skill, step, total, phase(transcribe/plan/render/review), label, detail,
   *   percent, turn_id, preview_url?}。auto_short_video._emit_step 为旧格式，无
   *  phase 字段——前端必须容错 phase 缺失。preview_url 为 video studio agent
   *  start_preview 后中途透传的实时预览地址（可选，重复事件同 URL 不重复渲染）。 */
  VIDEO_PIPELINE_PROGRESS: 'video_pipeline_progress',
} as const

export type EvType = (typeof Ev)[keyof typeof Ev]

/** 活事件集合（供调试面板/测试断言使用） */
export const LIVE_EVENT_TYPES: readonly string[] = Object.values(Ev)

/** POST 通道结果（非 SSE，走 useChatAgent 的 POST 分支） */
export const PostResult = {
  /** POST /api/chat/shortcut 响应 — 快捷指令完成 {message?, wechat_push?, card_draft?, png_urls?, html_urls?} */
  SHORTCUT_COMPLETED: 'shortcut_completed',
} as const