/**
 * 螃蟹便签 - 纯规则层
 *
 * 只放「不依赖 Vue / Pinia / DOM」的纯函数与常量，便于单测。
 * 职责边界：
 *   - 数据类型与默认值
 *   - 配置清洗（DB 里的 config_json 可能被手工改坏，必须兜底）
 *   - 触发仲裁（该不该弹、该弹哪一条）
 * UI 与持久化分别放在 CrabNotePanel.vue / useCrabNotes.ts。
 */

export type CrabNoteTrigger = 'turn_end' | 'input_focus' | 'hover' | 'manual'

export type CrabNoteAction = 'fill' | 'send' | 'remind'

export interface CrabNote {
  id: string
  text: string
  emoji: string
  /** 自动弹出的时机；manual = 只在小本子里展示，永不自动冒泡 */
  trigger: CrabNoteTrigger
  /** 点开便签后执行的动作 */
  action: CrabNoteAction
  /** 同一便签两次弹出的最小间隔（分钟） */
  cooldown_min: number
  enabled: boolean
  last_shown_at: string | null
  last_done_at: string | null
  created_at: string
}

export interface CrabNoteSettings {
  /** 总开关：关闭后不再自动冒泡，便签仍可在面板里查看和点用 */
  notes_enabled: boolean
  /** 免打扰区间，如 "23-8"；空串表示不启用 */
  quiet_hours: string
  /** 任意两条气泡之间的最小间隔（秒） */
  global_cooldown_sec: number
  /** 单次会话最多主动冒泡次数 */
  max_per_session: number
}

/** 仲裁所需的运行时上下文（由组件在触发时刻快照出来） */
export interface ShowContext {
  settings: CrabNoteSettings
  isPanelOpen: boolean
  isStreaming: boolean
  heroMode: boolean
  now: number
  lastBubbleAt: number
  sessionBubbleCount: number
  snoozedUntil: Record<string, number>
}

export const MAX_NOTES = 50
export const MAX_NOTE_TEXT = 200

export const DEFAULT_SETTINGS: CrabNoteSettings = {
  notes_enabled: true,
  quiet_hours: '23-8',
  global_cooldown_sec: 5,
  max_per_session: 2,
}

export const TRIGGER_LABEL: Record<CrabNoteTrigger, string> = {
  turn_end: '会话结束',
  input_focus: '聚焦输入框',
  hover: '悬停螃蟹',
  manual: '只在面板里',
}

export const TRIGGER_OPTIONS: { value: CrabNoteTrigger; label: string }[] = [
  { value: 'turn_end', label: '会话结束后' },
  { value: 'input_focus', label: '聚焦输入框时' },
  { value: 'hover', label: '鼠标悬停时' },
  { value: 'manual', label: '只在面板里' },
]

export const ACTION_LABEL: Record<CrabNoteAction, string> = {
  fill: '填入输入框',
  send: '直接发送',
  remind: '记一下完成',
}

export const ACTION_OPTIONS: { value: CrabNoteAction; label: string }[] = [
  { value: 'fill', label: '填入输入框' },
  { value: 'send', label: '直接发送' },
  { value: 'remind', label: '记一下完成' },
]

/** 面板 → ChatView 的动作回传（面板只管选，落输入框 / 发送由 ChatView 负责） */
export interface CrabApplyPayload {
  note: CrabNote | null
  text: string
  action: CrabNoteAction
}

/** 快捷指令：点一下即用，不落库（除非用户点「＋」存成便签） */
export interface CrabQuickPhrase {
  emoji: string
  text: string
  trigger: CrabNoteTrigger
  action: CrabNoteAction
}

export const QUICK_PHRASES: CrabQuickPhrase[] = [
  { emoji: '✍️', text: '帮我写一篇小红书图文文案，先问我主题和人群', trigger: 'manual', action: 'fill' },
  { emoji: '🔥', text: '看看今天有什么热点可以蹭，给我 3 个选题', trigger: 'manual', action: 'fill' },
  { emoji: '📊', text: '复盘一下最近几篇笔记的数据，找出问题', trigger: 'manual', action: 'fill' },
  { emoji: '✅', text: '发布前帮我过一遍合规和敏感词检查', trigger: 'manual', action: 'fill' },
  { emoji: '⏰', text: '每天 9 点提醒我发 3 条笔记', trigger: 'input_focus', action: 'remind' },
]

const TRIGGER_VALUES: CrabNoteTrigger[] = ['turn_end', 'input_focus', 'hover', 'manual']
const ACTION_VALUES: CrabNoteAction[] = ['fill', 'send', 'remind']

let noteSeq = 0

export function createNote(input: {
  text: string
  emoji?: string
  trigger?: CrabNoteTrigger
  action?: CrabNoteAction
  cooldown_min?: number
}): CrabNote {
  noteSeq = (noteSeq + 1) % 100000
  return {
    id: `n_${Date.now().toString(36)}_${noteSeq}`,
    text: input.text.trim().slice(0, MAX_NOTE_TEXT),
    emoji: input.emoji || '📌',
    trigger: input.trigger || 'turn_end',
    action: input.action || 'fill',
    cooldown_min: Number.isFinite(input.cooldown_min) ? Math.max(0, Number(input.cooldown_min)) : 60,
    enabled: true,
    last_shown_at: null,
    last_done_at: null,
    created_at: new Date().toISOString(),
  }
}

/**
 * 把 DB 里读出来的任意形状的 notes 清洗成合法数组。
 * 非法项直接丢弃，字段越界一律回落到默认值 —— 配置被改坏时宁可少一条便签，
 * 也不能让整个面板渲染崩掉。
 */
export function sanitizeNotes(raw: unknown): CrabNote[] {
  if (!Array.isArray(raw)) return []
  const out: CrabNote[] = []
  for (const item of raw) {
    if (!item || typeof item !== 'object') continue
    const it = item as Record<string, unknown>
    const text = typeof it.text === 'string' ? it.text.trim().slice(0, MAX_NOTE_TEXT) : ''
    if (!text) continue

    const trigger = TRIGGER_VALUES.includes(it.trigger as CrabNoteTrigger)
      ? (it.trigger as CrabNoteTrigger)
      : 'turn_end'
    const action = ACTION_VALUES.includes(it.action as CrabNoteAction)
      ? (it.action as CrabNoteAction)
      : 'fill'

    out.push({
      id: typeof it.id === 'string' && it.id ? it.id : createNote({ text }).id,
      text,
      emoji: typeof it.emoji === 'string' && it.emoji ? it.emoji : '📌',
      trigger,
      action,
      cooldown_min: Number.isFinite(Number(it.cooldown_min))
        ? Math.max(0, Number(it.cooldown_min))
        : 60,
      enabled: it.enabled !== false,
      last_shown_at: typeof it.last_shown_at === 'string' ? it.last_shown_at : null,
      last_done_at: typeof it.last_done_at === 'string' ? it.last_done_at : null,
      created_at: typeof it.created_at === 'string' ? it.created_at : new Date().toISOString(),
    })

    if (out.length >= MAX_NOTES) break
  }
  return out
}

/** 配置里的设置项同样可能被改坏，逐字段兜底 */
export function sanitizeSettings(raw: unknown): CrabNoteSettings {
  const it = (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>
  const num = (v: unknown, fallback: number, min: number, max: number) => {
    const n = Number(v)
    if (!Number.isFinite(n)) return fallback
    return Math.min(max, Math.max(min, n))
  }
  return {
    notes_enabled: it.notes_enabled !== false,
    quiet_hours: typeof it.quiet_hours === 'string' ? it.quiet_hours : DEFAULT_SETTINGS.quiet_hours,
    global_cooldown_sec: num(it.global_cooldown_sec, DEFAULT_SETTINGS.global_cooldown_sec, 0, 600),
    max_per_session: num(it.max_per_session, DEFAULT_SETTINGS.max_per_session, 0, 20),
  }
}

/** 解析 "23-8" 这类免打扰区间；非法或首尾相同都视为未启用 */
export function parseQuietHours(raw: string): [number, number] | null {
  if (!raw) return null
  const parts = raw.split('-')
  if (parts.length !== 2) return null
  const start = parseInt(parts[0], 10)
  const end = parseInt(parts[1], 10)
  if (!Number.isFinite(start) || !Number.isFinite(end)) return null
  if (start < 0 || start > 24 || end < 0 || end > 24) return null
  if (start === end) return null
  return [start, end]
}

/** 是否处于免打扰时段（支持跨零点，如 23-8） */
export function inQuietHours(raw: string, now: number): boolean {
  const range = parseQuietHours(raw)
  if (!range) return false
  const hour = new Date(now).getHours()
  const [start, end] = range
  if (start < end) return hour >= start && hour < end
  return hour >= start || hour < end
}

export function isSameDay(isoA: string | null, now: number): boolean {
  if (!isoA) return false
  const a = new Date(isoA)
  if (Number.isNaN(a.getTime())) return false
  const b = new Date(now)
  return (
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate()
  )
}

/**
 * 单条便签是否允许在当前时机冒泡。
 * 判断顺序刻意从「最便宜的全局闸门」到「最贵的单条状态」，
 * 任一条不满足即拒绝。
 */
export function shouldShowNote(
  note: CrabNote,
  source: CrabNoteTrigger,
  ctx: ShowContext,
): boolean {
  if (!ctx.settings.notes_enabled) return false
  if (!note.enabled) return false
  if (ctx.isPanelOpen) return false
  if (ctx.isStreaming) return false
  if (ctx.heroMode) return false
  if (inQuietHours(ctx.settings.quiet_hours, ctx.now)) return false
  if (ctx.now - ctx.lastBubbleAt < ctx.settings.global_cooldown_sec * 1000) return false
  if (ctx.sessionBubbleCount >= ctx.settings.max_per_session) return false
  if (note.trigger !== source) return false

  if (note.last_shown_at) {
    const elapsed = ctx.now - new Date(note.last_shown_at).getTime()
    if (Number.isFinite(elapsed) && elapsed < note.cooldown_min * 60_000) return false
  }
  if (isSameDay(note.last_done_at, ctx.now)) return false

  const snooze = ctx.snoozedUntil[note.id]
  if (typeof snooze === 'number' && ctx.now < snooze) return false

  return true
}

function staleness(note: CrabNote, now: number): number {
  if (!note.last_shown_at) return Infinity
  const t = new Date(note.last_shown_at).getTime()
  return Number.isFinite(t) ? now - t : Infinity
}

/**
 * 在当前时机下挑一条最该弹的便签。
 * 排序：越久没弹过越优先（从没弹过的最优先），同分按创建时间早者优先。
 */
export function pickNote(
  notes: CrabNote[],
  source: CrabNoteTrigger,
  ctx: ShowContext,
): CrabNote | null {
  const candidates = notes.filter(n => shouldShowNote(n, source, ctx))
  if (!candidates.length) return null

  candidates.sort((a, b) => {
    const sa = staleness(a, ctx.now)
    const sb = staleness(b, ctx.now)
    if (sa === sb) {
      return new Date(a.created_at).getTime() - new Date(b.created_at).getTime()
    }
    return sa > sb ? -1 : 1
  })

  return candidates[0]
}

/** 次日零点的时间戳，用于「稍后」= 当日静默 */
export function nextMidnight(now: number): number {
  const d = new Date(now)
  d.setHours(24, 0, 0, 0)
  return d.getTime()
}