/**
 * Cell 类型定义 — 严格对齐 Codex CLI 源码设计
 *
 * Codex 核心架构（从源码提取）：
 *
 * 1. TurnLifecycleState: 只有 agent_turn_running: bool
 *    - start() → running=true, finish() → running=false
 *    - 没有thinking/planning/executing/responding细分
 *    - 状态细节由 StatusIndicatorState.header 字符串表达，不是枚举
 *
 * 2. StatusIndicatorState: { header: String, details: Option<String> }
 *    - header 是动态字符串："Working", "Reviewing approval request", "Thinking"
 *    - 由事件自己决定显示什么，不是预设映射
 *
 * 3. EventMsg: TurnStarted/TurnComplete/AgentMessage/AgentReasoning/
 *    ItemStarted/ItemCompleted/ExecCommandBegin/End/PlanDelta/Error
 *
 * 4. TurnItem: UserMessage/AgentMessage/Plan/Reasoning/
 *    CommandExecution/McpToolCall/WebSearch/ImageGeneration/FileChange
 *
 * 5. HistoryCell trait: display_lines() + raw_lines() + transcript_lines()
 *    - 29个实现: UserHistoryCell, AgentMessageCell, ReasoningSummaryCell,
 *      ProposedPlanCell, PlanUpdateCell, ExecCell, McpToolCallCell, etc.
 *
 * 6. Streaming: Reasoning delta 不渲染到历史，只提取 **bold** 标题作状态
 *    AgentMessage delta 流式渲染，Plan delta 只在 Plan 模式渲染
 */

// ═══════════════════════════════════════════
// Turn 生命周期（对齐 Codex turn_lifecycle.rs）
// ═══════════════════════════════════════════

/**
 * Turn 状态 — 对齐 Codex: 只有 running/not-running
 * Codex 源码: TurnLifecycleState { agent_turn_running: bool }
 * 状态文本由 StatusIndicator.header 动态决定，不是枚举映射
 */
export type TurnPhase = 'idle' | 'running' | 'done' | 'error'

/**
 * StatusIndicator — 对齐 Codex status_state.rs
 * Codex 源码: StatusIndicatorState { header: String, details: Option<String> }
 * header 是事件驱动的动态字符串，不是预设枚举
 */
export interface StatusIndicator {
  header: string
  details?: string
}

// ═══════════════════════════════════════════
// Item 事件模型（对齐 Codex protocol.rs EventMsg）
// ═══════════════════════════════════════════

// ═══════════════════════════════════════════
// 基础 Cell 类型
// ═══════════════════════════════════════════

export type CellType = 'user' | 'assistant' | 'thinking' | 'exec' | 'plan' | 'diff' | 'artifact'

export interface ToolCall {
  id: string
  type: CellType
  name: string
  arguments: Record<string, unknown>
  result?: string
  exitCode?: number
  durationMs?: number
  displayArgs?: string
  iteration?: number
  status?: 'running' | 'done' | 'error' | 'aborted'
  subAgentId?: string
  /** 子智能体的真名（spawn_agent 的 task_name / dispatch 返回的 name）。
      由 tool_call_start 与 tool_call_end 的 sub_agent_name 下发，轨迹分组头用它。 */
  subAgentName?: string
}

export interface AgentStep {
  nodeKey: string
  nodeLabel: string
  status: 'pending' | 'running' | 'completed' | 'error' | 'awaiting_review'
  percent: number
}

/** 工作区媒体产物（media_artifact_ready 载荷单项）。
 *  后端把工作区里新生成的 mp4/wav/png 复制进 outputs/ 后下发，url 为
 *  /outputs/... 直连地址——「我的空间」扫描到的是同一份文件。 */
export interface MediaArtifact {
  title: string
  kind: 'video' | 'audio' | 'image' | string
  file_name?: string
  source_path?: string
  output_path?: string
  url: string
  size?: number
}

/** 视频管线进度（video_pipeline_progress 事件载荷，talking_head_overlay /
 *  auto_short_video 发射）。auto_short_video 旧格式无 phase——必须容错。
 *  preview_url：视频工作室 agent start_preview 后中途透传的实时预览地址（可选）。 */
export interface VideoPipelineProgress {
  skill: string
  step?: number
  total?: number
  phase?: 'transcribe' | 'plan' | 'render' | 'review'
  label?: string
  detail?: string
  percent: number
  preview_url?: string
}

export interface AgentMeta {
  /** 会话 id：随 agentMeta 落库，进度条/审核卡/反馈事件都用它定位会话 */
  sessionId?: string | null
  intent?: { action: string; tools?: string[]; topic?: string; params: Record<string, unknown>; confidence: number }
  workflowStatus?: 'running' | 'awaiting_review' | 'awaiting_confirmation' | 'awaiting_clarification' | 'awaiting_storyboard_confirmation' | 'awaiting_guardian_confirmation' | 'awaiting_cut_plan_confirmation' | 'awaiting_side_write_confirmation' | 'suspended' | 'completed' | 'error' | 'confirmation_expired'
  /** 分镜确认闸门（2026-10-17）：loop_state 落盘路径，回放 /confirm-storyboard 要用 */
  storyboardShots?: any[]
  storyboardTitle?: string
  storyboardDuration?: number
  /** confirmed / cancelled / failed — 卡片已处理完则不再渲染 */
  storyboardResolved?: 'confirmed' | 'cancelled' | 'failed'
  storyboardError?: string
  /** 叠加方案确认闸门（2026-11-01，设计文档 3.4 #2 缺口2）：OverlayPlan 摘要 */
  cutPlan?: { plan: any; message?: string }
  cutPlanResolved?: 'confirmed' | 'cancelled' | 'failed'
  cutPlanError?: string
  /** 设计文档 3.4 #4 版本条：历次确认渲染的成片版本（version 递增，assetUrl 为成片地址） */
  cutPlanVersions?: { version: number; assetUrl?: string }[]
  /** Guardian 高危操作确认闸门（2026-10-19） */
  guardianToolName?: string
  riskInfo?: string
  guardianResolved?: 'confirmed' | 'rejected' | 'failed'
  /** 通用确认（awaiting_confirmation）的决策回写 */
  confirmationResolved?: string
  guardianError?: string
  currentStep?: string
  steps?: AgentStep[]
  totalPercent?: number
  /** 回合总耗时（ms）：TurnRuntime 计时或后端 duration_ms，随 agentMeta 落库、历史会话可见 */
  durationMs?: number
  /** 视频管线进度（口播增强/短视频链路）：video_pipeline_progress 事件原位更新 */
  videoProgress?: VideoPipelineProgress
  cardDraft?: { title?: string; template?: string; pages: any[]; pngUrls: string[]; htmlUrls: string[] }
  /** 卡片组对应的草稿作品 id（saveCardAsDraft/updateDraft 返回）——「侧边栏预览」用它在右侧 ContextSidebar 打开，而非临时抽屉 */
  cardDraftWorkId?: string
  /** 草稿就地修改（work_edit → work_patch_ready）：记录本轮改了哪个草稿的哪些字段 */
  workPatch?: { workId: string; fields: Record<string, unknown> }
  /** 本轮生成并已发布到 outputs/ 的媒体产物（media_artifact_ready）：
   *  url 为 /outputs/... 可直连地址，详情页与我的空间同源 */
  mediaArtifacts?: MediaArtifact[]
  /** Turn 阶段 — 对齐 Codex: idle/running/done/error */
  turnPhase?: TurnPhase
  /** 终止原因（BUG-007）：对齐后端 EndedReason 契约。
   *  completed 为正常完成；max_iterations/token_budget/nudge_exhausted/duplicate_calls
   *  为资源截断；fake_final 为假完成拦截——截断类已在消息尾部附可见提示。 */
  endedReason?: string
  /** StatusIndicator — 对齐 Codex: 事件驱动的动态状态文本 */
  statusIndicator?: StatusIndicator
  loopStatePath?: string
  confirmationPrompt?: string
  confirmationSkill?: string
  clarificationPrompt?: string
  clarificationSkill?: string
  /** 首轮回合 ID（L1 隔离）：发送前前端生成，随 agentMeta 入库，
   *  刷新/恢复后可找回，使 resume 订阅能显式传 turnId（而非靠动态兜底）。 */
  turnId?: string
  clarificationBatches?: any[]
  currentBatch?: number
  totalBatches?: number
  /** 侧会话（Side Session）特殊消息（PRD FR-B3/B4）：message_type='side_session_digest' */
  messageType?: string
  sideSessionId?: string
  sideSessionTitle?: string
  /** 侧会话写副作用确认闸门（PRD G6/Q9）：逐次生效，无会话级放行 */
  sideWrite?: { toolName: string; description: string; permissions: string[]; sideSessionId: string }
  sideWriteResolved?: 'confirmed' | 'rejected'
}

/** 判定是否为侧会话摘要特殊消息（message_type='side_session_digest'） */
export function isSideDigestMessage(meta: AgentMeta | undefined | null): boolean {
  return meta?.messageType === 'side_session_digest'
}

/** SSE side_write_confirmation_required 载荷 → AgentMeta 状态映射（pure，可测） */
export function applySideWriteRequired(
  meta: AgentMeta,
  payload: Record<string, any>,
): AgentMeta {
  const out: any = { ...meta }
  out.workflowStatus = 'awaiting_side_write_confirmation'
  out.sideWrite = {
    toolName: String(payload?.tool_name ?? ''),
    description: String(payload?.description ?? ''),
    permissions: Array.isArray(payload?.permissions) ? payload.permissions : [],
    sideSessionId: String(payload?.side_session_id ?? ''),
  }
  if (payload?.loop_state_path) out.loopStatePath = payload.loop_state_path
  return out
}

/** 侧会话写确认 UI 可见性：待确认 + 有 loopStatePath + 未决 */
export function deriveSideWriteVisible(meta: AgentMeta | undefined | null): boolean {
  return Boolean(
    meta?.workflowStatus === 'awaiting_side_write_confirmation'
      && meta?.loopStatePath
      && meta?.sideWriteResolved !== 'confirmed'
      && meta?.sideWriteResolved !== 'rejected',
  )
}

/** 用户消息随附的图片素材（上传后随消息一起发出、一起展示） */
export interface UserAttachment {
  asset_id: string
  filename?: string
  content_type?: string
  original_url?: string
  thumbnail_url?: string
  width?: number | null
  height?: number | null
}

export interface ChatMessage {
  /** 本轮对话的 SSE turn_id（思考流分桶键） */
  turnId?: string
  role: 'user' | 'assistant' | 'system'
  content: string
  /** 随消息发出的图片缩略信息（仅用于渲染，后端仍走 image_asset_ids） */
  attachments?: UserAttachment[]
  reasoning?: string
  decisions?: string[]
  progress?: string[]
  errors?: string[]
  thinking?: string
  isError?: boolean
  toolCalls?: ToolCall[]
  diffFile?: string
  diffContent?: string
  diffAddCount?: number
  diffDelCount?: number
  agentMeta?: AgentMeta
  _persisted?: boolean
  _streamHtml?: string
  /**
   * 双通道流式渲染（对齐 Claude Code blit / Codex stable-tail 两区域）：
   * - _dualStream=true 时，正文区拆成两个 v-html 节点分别消费
   *   _stableHtml（低频更新，emitted 推进才变）与 _tailHtml（每帧更新，
   *   只含尾部活跃段）——稳定区 DOM 子树不重建，等效"未变区域不重绘"。
   * - settle 后 _dualStream=false，回落到 _streamHtml 单节点完整渲染。
   */
  _dualStream?: boolean
  _stableHtml?: string
  _tailHtml?: string
  /** 渲染层 L2 回合沙箱实例（TurnRuntime）：事件准入 + 回合计时 + 拦截计数 */
  _turnRt?: import('@/render/turn-runtime').TurnRuntime
  /** 内容已由 SSE 通道提供（POST 响应不再覆盖/重播） */
  _contentFromSSE?: boolean
  timestamp?: number
}

export interface Conversation {
  id: string
  title: string
  messages: ChatMessage[]
  createdAt: number
  _isDraft?: boolean
  _folderId?: string
  _workId?: string | null
}

export interface ChatStats {
  turns: number
  tokens: number
  latency: number
}

// ═══════════════════════════════════════════
// Presentation Adapter（对齐 Codex HistoryCell 渲染-逻辑分离）
// ═══════════════════════════════════════════

import { getToolIcon, getNodeIcon } from './icon-map'

export function getToolDisplayLabel(toolName: string): { label: string } {
  return { label: getToolIcon(toolName).label }
}

export function getNodeDisplayLabel(nodeKey: string): { label: string } {
  return { label: getNodeIcon(nodeKey).label }
}

/**
 * 获取 Chat 模式下的状态指示器文本
 *
 * 对齐 Codex StatusIndicatorState:
 * - header 是事件驱动的动态字符串
 * - 优先使用 statusIndicator.header（事件设置的）
 * - 降级到从 workflow 状态推断
 *
 * Codex 源码: StatusIndicatorState { header: String, details: Option<String> }
 * 事件自己决定显示什么，不是预设枚举映射
 */
export function getChatStatusText(meta: AgentMeta | undefined): string {
  if (!meta) return ''

  if (meta.workflowStatus === 'completed') return ''
  if (meta.workflowStatus === 'error') return ''

  if (meta.statusIndicator?.header) {
    return meta.statusIndicator.header
  }

  if (meta.workflowStatus === 'running') {
    const activeStep = meta.steps?.find(s => s.status === 'running')
    if (activeStep) {
      const { label } = getNodeDisplayLabel(activeStep.nodeKey)
      return `${label}中…`
    }
    return '处理中…'
  }

  return ''
}

// ═══════════════════════════════════════════
// 工具函数
// ═══════════════════════════════════════════

export function countLines(text: string): number {
  if (!text) return 0
  return text.split('\n').length
}

const THINKING_NOISE_PATTERNS = [
  /^\s*\[progress\]\s*\[\d+\/\d+\]\s*loop\s*iter/i,
  /^\s*\[progress\]\s*\[\d+\/\d+\]/,
  /^\s*\[\d+\/\d+\]\s*loop/i,
]

function filterThinkingNoise(text: string): string {
  if (!text) return ''
  const lines = text.split('\n')
  const filtered = lines.filter(line => {
    const trimmed = line.trim()
    if (!trimmed) return false
    for (const pat of THINKING_NOISE_PATTERNS) {
      if (pat.test(trimmed)) return false
    }
    return true
  })
  return filtered.join('\n')
}

function countThinkingSteps(text: string): number {
  if (!text) return 0
  return text.split('\n').filter(l => l.trim().startsWith('[decision]')).length
}

export function getThinkingSummary(msg: ChatMessage): string {
  const parts: string[] = []
  if (msg.reasoning) {
    const lines = countLines(filterThinkingNoise(msg.reasoning))
    parts.push(`推理 (${lines}行)`)
  }
  if (msg.decisions?.length) parts.push(`${msg.decisions.length}步决策`)
  if (msg.progress?.length) parts.push(`${msg.progress.length}条进度`)
  if (msg.thinking) {
    const clean = filterThinkingNoise(msg.thinking)
    if (clean) {
      const steps = countThinkingSteps(clean)
      if (steps > 0) parts.push(`${steps}步思考`)
      else parts.push(`思考 (${countLines(clean)}行)`)
    }
  }
  if (parts.length === 0) return ''
  return parts.join(' · ')
}

export function getThinkingPreview(text: string): string {
  if (!text) return '思考中…'
  const clean = filterThinkingNoise(text)
  if (!clean) return '思考中…'
  const lines = clean.split('\n').map(l => l.trim()).filter(Boolean)
  if (lines.length === 0) return '思考中…'
  const last = lines[lines.length - 1]
  if (last.length > 80) return last.slice(0, 77) + '…'
  return last
}

export function getThinkingLiveLines(text: string, maxLines = 2): string {
  if (!text) return ''
  const clean = filterThinkingNoise(text)
  if (!clean) return ''
  const lines = clean.split('\n')
  const tail = lines.slice(-maxLines)
  return tail.join('\n')
}

/**
 * 内容净化：移除所有内部技术标记
 * 对齐 Codex: Event Channel 不穿透内部事件到 UI
 */
export function sanitizeContent(raw: string): string {
  if (!raw) return ''
  if (typeof raw !== 'string') return String(raw)
  let cleaned = raw
  // ReAct 协议 JSON 剥壳：{"thought":...,"output":...} → 只留 output 人话
  const t = cleaned.trim()
  if (t.startsWith('{"thought"')) {
    try {
      const j = JSON.parse(t)
      if (j && typeof j.output === 'string' && j.output.trim()) cleaned = j.output
      else if (j && typeof j.thought === 'string' && j.thought.trim()) cleaned = j.thought
    } catch {
      /* 非 JSON 保持原文 */
    }
  }
  const patterns = [
    /_model_used["\s:]+["\']?\w[\w-]*["\']?/g,
    /_duration_ms["\s:]+\d+/g,
    /_message["\s:]+["\'][^"\']*["\']/g,
    /未收到前端注入[^，。]*/g,
    /Data truncated for column[^，。\n]*/g,
    /SQL:\s*[^。\n]*/g,
    /\(pymysql\.err\.[A-Za-z]+\)[^。\n]*/g,
    /File\s+"[^"]+",\s*line\s+\d+/g,
    /pymysql\.err\.\w+/g,
    /image_gen\s*未收到[^\n]*/gi,
    /工作流启动失败[^\n]*/g,
    /工作流启动异常[^\n]*/g,
    /\[object Object\]/g,
  ]
  patterns.forEach(p => {
    cleaned = cleaned.replace(p, '')
  })
  // 清理内部标签及其内容（思考过程和澄清通过独立组件展示）
  const codeBlocks: string[] = []
  cleaned = cleaned.replace(/```[\s\S]*?```/g, (m) => {
    codeBlocks.push(m)
    return `\x00CB${codeBlocks.length - 1}\x00`
  })
  // <thinking> 标签（原始形式）
  cleaned = cleaned.replace(/<thinking>[\s\S]*?<\/thinking>/gi, '')
  cleaned = cleaned.replace(/<thinking>[\s\S]*$/gi, '')
  // <thinking> 标签（HTML 转义形式，防止已转义文本漏网）
  cleaned = cleaned.replace(/&lt;thinking&gt;[\s\S]*?&lt;\/thinking&gt;/gi, '')
  cleaned = cleaned.replace(/&lt;thinking&gt;[\s\S]*$/gi, '')
  cleaned = cleaned.replace(/&lt;\/thinking&gt;/gi, '')
  // <needs_clarification> 标签（原始形式）
  cleaned = cleaned.replace(/<needs_clarification>[\s\S]*?<\/needs_clarification>/gi, '')
  cleaned = cleaned.replace(/<needs_clarification>[\s\S]*$/gi, '')
  // <needs_clarification> 标签（HTML 转义形式）
  cleaned = cleaned.replace(/&lt;needs_clarification&gt;[\s\S]*?&lt;\/needs_clarification&gt;/gi, '')
  cleaned = cleaned.replace(/&lt;needs_clarification&gt;[\s\S]*$/gi, '')
  cleaned = cleaned.replace(/&lt;\/needs_clarification&gt;/gi, '')
  // 恢复代码块
  cleaned = cleaned.replace(/\x00CB(\d+)\x00/g, (_, i) => codeBlocks[Number(i)])
  cleaned = cleaned.replace(/\n{3,}/g, '\n\n')
  return cleaned.trim()
}

// ════════════════════════════════════════════════════════════════
// HTTP 错误体解析（唯一出口）
// ════════════════════════════════════════════════════════════════
// 历史问题：chat/agent 走 `throw new Error(await res.text())`，而 FastAPI 的
// 4xx/5xx 响应体是 `{"detail": "..."}` JSON，该原文被 catch 拼进
// assistantMsg.content → 用户在回复里看到的是裸 JSON 而不是可读错误。
// 现在所有入口统一走这里：优先取 detail/message，其次按状态码给可读兜底；
// 响应体是 JSON 但无 detail/message 时**不透出原文**，按状态码兜底。
export interface HttpErrorParseOptions {
  /** 404 的专用文案（不同入口语义不同，如确认/澄清状态过期） */
  notFoundMessage?: string
}

export function parseHttpErrorMessage(status: number, body: string, opts: HttpErrorParseOptions = {}): string {
  const raw = (body || '').trim()
  if (raw && !/^</.test(raw)) {
    try {
      const parsed = JSON.parse(raw) as Record<string, any>
      const detail = parsed?.detail
      if (typeof detail === 'string' && detail.trim()) return detail.trim()
      if (Array.isArray(detail)) {
        const joined = detail
          .map((d: any) => (d && typeof d === 'object' ? d.msg || d.message || '' : String(d)))
          .filter(Boolean)
          .join('; ')
        if (joined) return joined
      }
      // detail 是对象：取嵌套 message/msg/error，取不到就继续往下找
      if (detail && typeof detail === 'object') {
        const nested = detail.message || detail.msg || detail.error
        if (typeof nested === 'string' && nested.trim()) return nested.trim()
      }
      const message = parsed?.message
      if (typeof message === 'string' && message.trim()) return message.trim()
      const msg = parsed?.msg
      if (typeof msg === 'string' && msg.trim()) return msg.trim()
      // 是 JSON 但没有任何可读字段：不透出原文，落到状态码兜底
    } catch {
      // 非 JSON 响应体（多半是可读纯文本）：压缩空白后透出，截断防超长
      const singleLine = raw.replace(/\s+/g, ' ').slice(0, 200)
      if (singleLine) return singleLine
    }
  }
  if (status === 404) return opts.notFoundMessage || '请求的资源已过期或不存在，请重新发起操作'
  if (status === 401) return '登录已过期，请刷新页面后重试'
  if (status === 403) return '没有权限执行该操作'
  if (status === 422) return '请求参数无效'
  if (status === 429) return '请求过于频繁，请稍后再试'
  if (status >= 500) return '服务器内部错误，请稍后重试'
  return `请求失败 (${status})`
}