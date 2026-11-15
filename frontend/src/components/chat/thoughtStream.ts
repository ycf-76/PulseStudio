import { reactive, computed } from 'vue'

/**
 * thoughtStream — 思考流事件总线（按 turnId 分桶隔离版）。
 *
 * 每一轮对话（turn_id）一个独立的事件桶，消息 cell 只渲染自己
 * turn 的桶——上一轮的时间线不会跟着新一轮的事件动。
 *
 * SSE 层每收到一帧调用 ingest(type, payload)，按 payload.turn_id
 * 路由到对应桶。未知事件静默吸收（不炸、不占渲染行）。
 */

export type ThoughtIcon = 'search' | 'doc' | 'ask' | 'check' | 'step' | 'done' | 'ext' | 'brain' | 'terminal'

export interface ThoughtEvent {
  kind: 'ext' | 'direct' | 'tool' | 'web_search' | 'write_preview' | 'done' | 'fallback'
  /** 具体图标（按技能类型），渲染器按此选图标 */
  icon?: ThoughtIcon
  label: string
  detail?: string
  meta?: string
  results?: { title: string; source?: string }[]
  /** 当前执行中 → TextShimmer 扫光 */
  active?: boolean
  /** 已完成 → 灰字 */
  mut?: boolean
  /** tool_call 的 id，end 时匹配行 */
  toolId?: string
  /**
   * 聚合计数（对齐 Claude 真身 collapseReadSearch / Codex 统一行）：
   * 连续同类调用不新增行，并入上一行 ×N。低层命令（bash/ffmpeg 类）必然走合并。
   */
  count?: number
  /** 低层实现细节命令（bash/shell/exec 类）——渲染层据此降级展示，原文只进详情 */
  lowLevel?: boolean
  /** 执行失败（tool_call_end success=false）——失败行留原地标红，不折叠隐藏 */
  failed?: boolean
}

interface StreamBucket {
  events: ThoughtEvent[]
  status: 'live' | 'done'
  /**
   * 暂停态（2026-11-14）：guardian/clarify/storyboard/cut_plan/side_write
   * 确认闸门关桶时置 true。桶关了（done）≠ 思考完成——暂停态下标题行
   * 要显「等待确认」而不是「思考完成」（大哥截图实锤：暂停后同框出现
   * 「思考中+思考完成」矛盾态）。批准续跑（tool_call_start / reasoning
   * delta 重新拉 live）或真正终态时清掉。
   */
  paused: boolean
  startedAt: number
  elapsedMs: number
  /**
   * 摘要行思考流水线（2026-11-06）：喂给 ThinkingChain 单行打字机的全文。
   * 两个来源按优先级互斥：
   * ① R1 式真流式 → reasoning_summary_text_delta 逐字追加（同时建 ext 行）；
   * ② DSML/ReAct 式（模型不吐 reasoning_content，思考在 JSON thought 字段）
   *    → decision_made 的完整 thought 整块追加（仅当本桶没有任何 ext 时，
   *    避免和 ① 双份）。
   */
  lineText: string
}

const EMPTY: StreamBucket = { events: [], status: 'done', paused: false, startedAt: 0, elapsedMs: 0, lineText: '' }

const state = reactive({
  /** turnId → 事件桶（每轮独立） */
  buckets: new Map<string, StreamBucket>(),
  /** 当前活跃 turn（SSE 流正在写入的） */
  activeTurnId: null as string | null,
})

/** 已处理帧去重：SSE 重连/replay 会重发历史帧，按 event_id 防重复渲染 */
const seenFrameIds = new Set<string>()

let tickTimer: ReturnType<typeof setInterval> | null = null

function startTick(turnId: string) {
  const b = state.buckets.get(turnId)
  if (!b) return
  b.startedAt = Date.now()
  b.elapsedMs = 0
  if (tickTimer) clearInterval(tickTimer)
  tickTimer = setInterval(() => {
    const cur = state.buckets.get(turnId)
    if (cur && cur.status === 'live') cur.elapsedMs = Date.now() - cur.startedAt
    else stopTick()
  }, 100)
}
function stopTick() {
  if (tickTimer) {
    clearInterval(tickTimer)
    tickTimer = null
  }
}

function bucketOf(turnId: string): StreamBucket {
  let b = state.buckets.get(turnId)
  if (!b) {
    b = { events: [], status: 'live', paused: false, startedAt: Date.now(), elapsedMs: 0, lineText: '' }
    state.buckets.set(turnId, b)
  }
  return b
}

/** 工具名 → kind 启发式（未命中一律 tool 兜底） */
function kindOfTool(name: string): ThoughtEvent['kind'] {
  const n = (name || '').toLowerCase()
  if (n.includes('search') || n.includes('web') || n.includes('热点') || n.includes('trend')) return 'web_search'
  if (
    n.includes('write') ||
    n.includes('save') ||
    n.includes('preview') ||
    n.includes('export') ||
    n.includes('draft') ||
    n.includes('compose')
  ) {
    return 'write_preview'
  }
  return 'tool'
}

/** 技能名 → 图标（搜索/文档/提问/检查/通用） */
function iconOfTool(name: string, kind: ThoughtEvent['kind']): ThoughtIcon {
  if (kind === 'web_search' || kind === 'write_preview') {
    return kind === 'web_search' ? 'search' : 'doc'
  }
  const n = (name || '').toLowerCase()
  if (/search|trend|topic|热点|新闻|news|glob|grep|scan|find/.test(n)) return 'search'
  if (/wiki|doc|file|write|preview|compose|note|card|feishu|lark|drive|folder|bitable|create_/.test(n)) return 'doc'
  if (/bash|shell|exec|command|terminal|run/.test(n)) return 'terminal'
  if (/ask|question|clarif/.test(n)) return 'ask'
  if (/check|audit|verify|guard/.test(n)) return 'check'
  return 'step'
}

function isLowLevelTool(name: string): boolean {
  return /bash|shell|exec|command|terminal|ffmpeg|ffprobe/i.test(name || '')
}

function normalizeLine(line: string, max = 120): string {
  const t = (line || '')
    .trim()
    .replace(/^[-*•]\s*/, '')
    .replace(/^\d+[.)]\s*/, '')
    .replace(/\s+/g, ' ')
  return t.length > max ? `${t.slice(0, max)}…` : t
}

function pushEvent(b: StreamBucket, ev: ThoughtEvent) {
  b.events.push(ev)
}

/** R1 思考流：ext 行创建/追加 */
function appendExtDetail(b: StreamBucket, delta: string) {
  if (!delta) return
  let ext = b.events.find((e) => e.kind === 'ext')
  if (!ext) {
    ext = { kind: 'ext', icon: 'ext', label: '深度思考', detail: '', active: true }
    b.events.push(ext)
  }
  ext.detail = (ext.detail || '') + delta
  // Codex 机制：提取思考里最新的粗体要点驱动状态行标题（正文不实时显示）
  const bolds = (ext.detail || '').match(/\*\*([^*]+)\*\*/g)
  if (bolds && bolds.length) {
    const tip = bolds[bolds.length - 1].replace(/\*\*/g, '').trim()
    if (tip) ext.label = '深度思考 · ' + tip.slice(0, 40)
  }
}

/**
 * 唯一写入点：SSE 层每帧调用。
 * 按 payload.turn_id 路由到对应桶——多轮并发/历史轮互不干扰。
 */
export function ingestThoughtEvent(
  type: string,
  payload: Record<string, any>,
  frame?: Record<string, any>
) {
  const t = String(type || '')
  // 帧去重：SSE replay 重发的帧（event_id 相同）直接跳过
  const frameId = String(frame?.event_id || payload?.event_id || '')
  if (frameId) {
    if (seenFrameIds.has(frameId)) return
    seenFrameIds.add(frameId)
    if (seenFrameIds.size > 800) {
      const it = seenFrameIds.values()
      for (let i = 0; i < 400; i++) { const v = it.next().value; if (v !== undefined) seenFrameIds.delete(v) }
    }
  }
  // turn_id 在 SSE 帧顶层（payload 里也可能有），帧级优先
  const frameTurnId = frame?.turn_id ? String(frame.turn_id) : ''
  if (frameTurnId && !payload?.turn_id) payload = { ...payload, turn_id: frameTurnId }

  switch (t) {
    case 'chat_turn_started':
    case 'workflow_started': {
      const tid = String(payload?.turn_id || '')
      if (!tid) break
      state.activeTurnId = tid
      const existed = state.buckets.get(tid)
      if (!existed) {
        bucketOf(tid)
      } else if (existed.paused) {
        // 2026-11-14：批准续跑后端会重发 chat_turn_started（同 turn_id）。
        // 暂停桶必须原地复活——否则桶停在 done+paused，要等第一个工具/
        // 思考事件才解除，期间标题行滞留「等待确认」。真正的完成桶
        // （done 且非 paused）不动：SSE replay 重发历史帧时不毁时间线。
        existed.status = 'live'
        existed.paused = false
        for (let i = existed.events.length - 1; i >= 0; i--) {
          if (existed.events[i].kind === 'done') existed.events.splice(i, 1)
        }
      }
      startTick(tid)
      break
    }

    case 'reasoning_summary_text_delta': {
      const tid = String(payload?.turn_id || state.activeTurnId || '')
      if (!tid) break
      const b = bucketOf(tid)
      state.activeTurnId = tid
      if (b.status !== 'live') {
        b.status = 'live'
        b.startedAt = Date.now()
      }
      // 批准续跑后思考恢复：暂停态解除（标题行从「等待确认」回到「思考中」）
      b.paused = false
      appendExtDetail(b, String(payload?.delta || ''))
      // 来源①：R1 式真流式 → 逐字进摘要行水线
      b.lineText += String(payload?.delta || '')
      break
    }

    case 'reasoning_completed': {
      const b = state.buckets.get(String(payload?.turn_id || state.activeTurnId || ''))
      const ext = b?.events.find((e) => e.kind === 'ext')
      if (ext) {
        ext.active = false
        ext.mut = true
      }
      break
    }

    case 'decision_made': {
      const tid = String(payload?.turn_id || state.activeTurnId || '')
      const b = tid ? state.buckets.get(tid) : null
      if (!b) break
      const d = String(payload?.decision || '').trim()
      if (!d) break
      // 过滤 ReAct 协议噪音（纯 JSON / 工具调用声明不算思考行）
      if (/^[<{\[]/.test(d) || d.startsWith('Let me output')) break
      // 收敛为一句话（首个句号/问号处截断，最多 60 字）
      const first = d.split(/[。！？!?]/)[0] || d
      const label = normalizeLine(first, 60)
      // 内容去重：同桶内已有相同行就跳过（防双发/重放）
      if (b.events.some((e) => e.kind === 'direct' && e.label === label)) break
      pushEvent(b, { kind: 'direct', icon: 'brain', label, mut: true })
      // 来源②：DSML/ReAct 式思考（模型不吐 reasoning_content）→ 整块进摘要行
      // 水线。仅当本桶没有 ext（即来源①从未流过）时追加，双通路互斥防双份。
      if (!b.events.some((e) => e.kind === 'ext')) {
        b.lineText += (b.lineText ? '\n' : '') + d
      }
      break
    }

    case 'tool_call_start': {
      const tid = String(payload?.turn_id || state.activeTurnId || '')
      const b = tid ? bucketOf(tid) : null
      if (!b) break
      // guardian 暂停关桶后用户批准续跑：done 桶拉回 live，新工具行才有扫光。
      // 残留的 done 行（「已暂停」）一并清掉，避免时间线中段出现假结束行。
      if (b.status !== 'live') {
        b.status = 'live'
        b.startedAt = Date.now()
        b.elapsedMs = 0
        b.paused = false
        for (let i = b.events.length - 1; i >= 0; i--) {
          if (b.events[i].kind === 'done') b.events.splice(i, 1)
        }
      }
      const name = String(payload?.tool_name || '执行任务')
      const toolId = String(payload?.tool_id || payload?.event_id || `${name}-${Date.now()}`)
      // 低层实现细节命令（bash/shell/exec 类）：对齐 Claude 真身 collapseReadSearch——
      // 不逐条出行，并入连续的低层行 ×N，命令原文只进详情（「查看执行过程」）
      const lowLevel = isLowLevelTool(name)
      // 提炼有意义的 label：skill_call 这类协议名用实际技能名/参数摘要替代
      const inputs = (payload?.inputs || {}) as Record<string, any>
      const SKILL_NAMES: Record<string, string> = {
        web_search: '搜索网络资料',
        trending_search: '搜索平台热点',
        content_check: '内容合规检查',
        skill_call: '',
        spawn_agent: '',
      }
      const innerSkill = String(inputs.skill || inputs.skill_name || inputs.skill_id || '')
      const query = String(inputs.query || inputs.keyword || inputs.topic || '')
      let label = SKILL_NAMES[name] ?? ''
      if (name === 'skill_call' || name === 'spawn_agent') {
        label = innerSkill || query || name
        if (query && innerSkill) label = innerSkill + '（' + query + '）'
      } else if (!label) {
        label = query || name
      }
      if (lowLevel) label = '执行命令'
      const kind = kindOfTool(name)
      const normLabel = normalizeLine(label, 60)
      // 执行过程详情：inputs 关键字段摘要（展开可看）
      let detail = ''
      try {
        const keys = Object.keys(inputs)
        if (keys.length > 0) {
          const parts: string[] = []
          for (const k of keys) {
            const v = inputs[k]
            const s = typeof v === 'string' ? v : JSON.stringify(v)
            if (s) parts.push(`${k}: ${s.length > 160 ? s.slice(0, 160) + '…' : s}`)
          }
          detail = parts.join('\n')
        }
      } catch {
        detail = ''
      }
      // 低层命令合并：仅当**紧邻**上一条动作行就是低层行（未被 spawn/技能打断、未失败）时
      // 并入 ×N 并重新点亮。失败行不并入——失败步骤留原地标红（只显示成功的时间线藏住审计故事）
      if (lowLevel) {
        const lastAction = [...b.events].reverse().find((e) => e.kind !== 'ext' && e.kind !== 'done')
        if (lastAction && lastAction.lowLevel && !lastAction.failed) {
          lastAction.count = (lastAction.count || 1) + 1
          lastAction.active = true
          lastAction.mut = false
          lastAction.failed = false
          lastAction.toolId = toolId
          lastAction.meta = undefined
          if (detail) lastAction.detail = lastAction.detail ? `${lastAction.detail}\n${detail}` : detail
          break
        }
      }
      // 去重：同一桶内已有"同名+执行中"的行 → 后端双发，跳过
      if (b.events.some((e) => e.active && e.kind !== 'ext' && e.label === normLabel)) {
        break
      }
      pushEvent(b, {
        kind,
        icon: iconOfTool(name, kind),
        label: normLabel,
        detail,
        toolId,
        active: true,
        count: 1,
        lowLevel,
      })
      break
    }

    case 'tool_call_end': {
      const tid = String(payload?.turn_id || state.activeTurnId || '')
      const b = tid ? state.buckets.get(tid) : null
      if (!b) break
      // 工具调用顺序执行：最后一条 active 的非 ext 行即刚结束的（label 已被提炼成中文，不能按名字匹配）
      const row = [...b.events].reverse().find((e) => e.active && e.kind !== 'ext')
      if (row) {
        row.active = false
        row.mut = true
        if (payload?.duration_ms) row.meta = `${(Number(payload.duration_ms) / 1000).toFixed(1)}s`
        // 失败标记：失败行留原地标红，不随成功流折叠隐藏
        if (payload?.success === false) row.failed = true
        // 执行结果摘要追加进详情（展开可看）
        const summary = String(payload?.summary || payload?.result || '').trim()
        if (summary) {
          const s = summary.length > 400 ? summary.slice(0, 400) + '…' : summary
          row.detail = row.detail ? `${row.detail}

【结果】${s}` : `【结果】${s}`
        }
      }
      break
    }

    case 'guardian_confirmation_required':
    case 'cut_plan_confirmation_required':
    case 'agent_clarification_required':
    case 'storyboard_confirmation_required':
    case 'side_write_required': {
      // 2026-11-06：暂停类事件也必须关桶。后端 paused 路径按设计 skip
      // agent_loop_done（等用户确认续跑），桶收不到终态就永远 live——
      // 「思考中」跳点永久转（飞书选题会话 08:44 实锤：暂停后 SSE 订阅被
      // cleanup 清掉，卡和终态都没到前端，桶无限期 live）。
      // 摘要行从「思考中…」落定为「等待确认」，扫光全部熄灭。
      const ptid = String(payload?.turn_id || state.activeTurnId || '')
      const pb = ptid ? state.buckets.get(ptid) : null
      if (!pb || pb.status !== 'live') break
      pushEvent(pb, { kind: 'done', icon: 'done', label: '已暂停 · 等待确认', mut: true })
      pb.events.forEach((e) => {
        if (e.active) {
          e.active = false
          e.mut = true
        }
      })
      pb.status = 'done'
      pb.paused = true
      pb.elapsedMs = Date.now() - pb.startedAt
      break
    }

    case 'workflow_completed':
    case 'workflow_error':
    case 'agent_loop_done': {
      // 2026-11-04：补 agent_loop_done——后端恢复路径（guardian/confirm/clarify
      // resume）为修"正文丢失"已不再发 workflow_completed，只发带正文的
      // agent_loop_done（唯一终态）。不补这条的话桶永远停在 live，
      // 链路摘要行卡在「思考中」不熄（大哥截图实锤）。
      const tid = String(payload?.turn_id || state.activeTurnId || '')
      const b = tid ? state.buckets.get(tid) : null
      if (!b) break
      pushEvent(b, { kind: 'done', icon: 'done', label: payload?.success === false || payload?.status === 'rejected' ? '已结束（有错误）' : 'Done', mut: true })
      // 收尾清扫：所有残留的 active 行落定（结束不再扫光）
      b.events.forEach((e) => {
        if (e.active) {
          e.active = false
          e.mut = true
        }
      })
      b.status = 'done'
      b.paused = false
      b.elapsedMs = Date.now() - b.startedAt
      break
    }

    default: {
      // 未知事件一律静默吸收（对齐 Claude 真身 isAbsorbedSilently）：
      // 时间线只收「有价值」的动作行，协议噪音不占行、不渲染。
      // 兼容性兜底依然成立——不炸、不丢（事件本身仍在 SSE 层日志里）。
      break
    }
  }
}

/** 完成后快照（挂回消息做持久化回看用） */
export function snapshotTurn(turnId: string): ThoughtEvent[] {
  const b = state.buckets.get(turnId)
  return b ? JSON.parse(JSON.stringify(b.events)) : []
}

/**
 * 兜底熄灯（2026-11-06）：前端流式状态收尾时强制关掉活跃桶。
 * 后端 paused 路径不发终态、SSE 订阅可能被 cleanup 提前清掉——桶收不到
 * workflow_completed/agent_loop_done 就永远 live，思考链「思考中」永转。
 * 这条保险丝保证：只要 UI 认为流结束了，思考链必须停。
 * 幂等：桶已 done 直接跳过；done 行只挂一条。
 */
export function finalizeTurn(turnId: string | null | undefined) {
  const tid = String(turnId || '')
  if (!tid) return
  const b = state.buckets.get(tid)
  if (!b || b.status !== 'live') return
  pushEvent(b, { kind: 'done', icon: 'done', label: '已结束', mut: true })
  b.events.forEach((e) => {
    if (e.active) {
      e.active = false
      e.mut = true
    }
  })
  b.status = 'done'
  b.elapsedMs = Date.now() - b.startedAt
  if (state.activeTurnId === tid) state.activeTurnId = null
}

export function finalizeActiveTurn() {
  finalizeTurn(state.activeTurnId)
}

/** 页面级订阅：跟随当前活跃轮（品牌头等全局状态用） */
export function useActiveThoughtStream() {
  const bucket = computed(() => {
    const tid = state.activeTurnId
    return (tid && state.buckets.get(tid)) || EMPTY
  })
  const isLive = computed(() => bucket.value.status === 'live')
  return { isLive }
}

/**
 * 消息 cell 的订阅入口：按消息自己的 turnId 取桶。
 * 没有 turnId 的历史消息 → 空桶（时间线不渲染，避免串轮）。
 */
export function useThoughtStream(turnId: () => string | null | undefined) {
  const bucket = computed(() => {
    const tid = turnId()
    return (tid && state.buckets.get(tid)) || EMPTY
  })
  const events = computed(() => bucket.value.events)
  const isLive = computed(() => bucket.value.status === 'live')
  const elapsedMs = computed(() => bucket.value.elapsedMs)
  /** 暂停态：确认闸门关桶但未终态（渲染层据此显「等待确认」，扫光/呼吸全熄） */
  const paused = computed(() => bucket.value.status === 'done' && !!bucket.value.paused)
  /** 摘要行思考水线：R1 delta 逐字 或 ReAct thought 整块（ThinkingChain 打字机用） */
  const lineFeed = computed(() => bucket.value.lineText)
  /** 状态行文字：最后一个 active 行的 label（衬线斜体打字段用） */
  const statusText = computed(() => {
    // 2026-11-14：done ≠ 完成——确认闸门暂停的桶也走这里，必须区分
    // （大哥截图实锤：暂停后标题行同框出现「思考中 + 思考完成」矛盾态）
    if (bucket.value.status === 'done') return bucket.value.paused ? '等待确认' : '思考完成'
    const active = [...bucket.value.events].reverse().find((e) => e.active && e.kind !== 'done')
    if (active) return active.label
    const ext = bucket.value.events.find((e) => e.kind === 'ext')
    return ext ? '正在深度思考…' : 'Thinking deeply, stand by...'
  })
  return { events, isLive, elapsedMs, paused, lineFeed, statusText }
}