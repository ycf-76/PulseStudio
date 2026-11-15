1/**
 * 螃蟹便签 - 状态与持久化
 *
 * 配置真身在 DB（plugin_configs 表），经由 pluginStore 读写，
 * 这里只负责：读出来 -> 清洗 -> 内存态；改动 -> 合并写回。
 *
 * 合并写回是关键：config_json 里还有 crab_size / speech_enabled 等
 * 别人的字段，不能整体覆盖，否则会把用户的螃蟹尺寸设置清掉。
 */

import { ref, computed } from 'vue'
import { usePluginStore } from '@/stores/plugin'
import {
  type CrabNote,
  type CrabNoteSettings,
  type CrabNoteTrigger,
  type CrabNoteAction,
  type ShowContext,
  DEFAULT_SETTINGS,
  MAX_NOTES,
  createNote,
  sanitizeNotes,
  sanitizeSettings,
  pickNote,
  nextMidnight,
} from './crabNoteRules'

const PLUGIN_ID = 'crab-companion'
const CONFIG_KEY = 'crab_notes'

interface RuntimeFacts {
  isPanelOpen: boolean
  isStreaming: boolean
  heroMode: boolean
}

const notes = ref<CrabNote[]>([])
const settings = ref<CrabNoteSettings>({ ...DEFAULT_SETTINGS })

const loaded = ref(false)
const loading = ref(false)
const saving = ref(false)
const saveError = ref<string | null>(null)

const lastBubbleAt = ref(0)
const sessionBubbleCount = ref(0)
const snoozedUntil = ref<Record<string, number>>({})

let saveTimer: ReturnType<typeof setTimeout> | null = null
let inFlight: Promise<void> | null = null

const hasNotes = computed(() => notes.value.length > 0)
const activeCount = computed(() => notes.value.filter(n => n.enabled).length)

function applyConfig(raw: Record<string, any>) {
  const block = (raw?.[CONFIG_KEY] && typeof raw[CONFIG_KEY] === 'object' ? raw[CONFIG_KEY] : {}) as Record<string, unknown>
  notes.value = sanitizeNotes(block.notes)
  settings.value = sanitizeSettings(block.settings)
}

async function load(force = false): Promise<void> {
  if (loaded.value && !force) return
  if (loading.value) return
  loading.value = true
  try {
    const store = usePluginStore()
    await store.loadPluginConfig(PLUGIN_ID)
    const cfg = store.getConfig(PLUGIN_ID)
    if (!cfg) {
      saveError.value = '读取螃蟹便签配置失败'
      return
    }
    applyConfig(cfg)
    saveError.value = null
    loaded.value = true
  } finally {
    loading.value = false
  }
}

async function persist(): Promise<void> {
  if (inFlight) {
    await inFlight.catch(() => {})
  }
  saving.value = true
  inFlight = (async () => {
    try {
      const store = usePluginStore()
      const base = { ...(store.getConfig(PLUGIN_ID) || {}) }
      await store.updatePluginConfig(PLUGIN_ID, {
        config_json: {
          ...base,
          [CONFIG_KEY]: {
            settings: settings.value,
            notes: notes.value,
          },
        },
        // 后端对缺失的 is_enabled 会按 true 处理，显式带上当前状态，
        // 免得「保存便签」这种纯配置动作把插件的启停状态改掉
        is_enabled: store.isEnabled(PLUGIN_ID),
      })
      saveError.value = null
    } catch (e: any) {
      saveError.value = e?.response?.data?.detail || e?.message || '保存失败'
      throw e
    } finally {
      saving.value = false
    }
  })()
  await inFlight.catch(() => {})
}

function queueSave(delay = 600) {
  if (saveTimer) clearTimeout(saveTimer)
  saveTimer = setTimeout(() => {
    saveTimer = null
    persist().catch(() => {})
  }, delay)
}

async function saveNow(): Promise<void> {
  if (saveTimer) {
    clearTimeout(saveTimer)
    saveTimer = null
  }
  await persist()
}

function addNote(input: {
  text: string
  emoji?: string
  trigger?: CrabNoteTrigger
  action?: CrabNoteAction
  cooldown_min?: number
}): CrabNote | null {
  if (!input.text.trim()) return null
  if (notes.value.length >= MAX_NOTES) return null
  const note = createNote(input)
  notes.value = [note, ...notes.value]
  queueSave()
  return note
}

function updateNote(id: string, patch: Partial<CrabNote>) {
  const idx = notes.value.findIndex(n => n.id === id)
  if (idx < 0) return
  const next = { ...notes.value[idx], ...patch, id: notes.value[idx].id }
  notes.value = notes.value.map((n, i) => (i === idx ? next : n))
  queueSave()
}

function removeNote(id: string) {
  notes.value = notes.value.filter(n => n.id !== id)
  delete snoozedUntil.value[id]
  queueSave()
}

function markShown(note: CrabNote) {
  const now = Date.now()
  lastBubbleAt.value = now
  sessionBubbleCount.value += 1
  notes.value = notes.value.map(n =>
    n.id === note.id ? { ...n, last_shown_at: new Date(now).toISOString() } : n,
  )
  queueSave()
}

function markDone(note: CrabNote) {
  const now = new Date().toISOString()
  notes.value = notes.value.map(n => (n.id === note.id ? { ...n, last_done_at: now } : n))
  queueSave()
}

function snooze(note: CrabNote) {
  snoozedUntil.value = { ...snoozedUntil.value, [note.id]: nextMidnight(Date.now()) }
}

/** 每轮对话开始时清零，让 max_per_session 语义是「每轮」而不是「整个页面生命周期」 */
function beginTurn() {
  sessionBubbleCount.value = 0
}

function buildContext(runtime: RuntimeFacts): ShowContext {
  return {
    settings: settings.value,
    isPanelOpen: runtime.isPanelOpen,
    isStreaming: runtime.isStreaming,
    heroMode: runtime.heroMode,
    now: Date.now(),
    lastBubbleAt: lastBubbleAt.value,
    sessionBubbleCount: sessionBubbleCount.value,
    snoozedUntil: snoozedUntil.value,
  }
}

/** 在当前时机下挑一条该弹的便签；没有则返回 null */
function pick(source: CrabNoteTrigger, runtime: RuntimeFacts): CrabNote | null {
  return pickNote(notes.value, source, buildContext(runtime))
}

export function useCrabNotes() {
  return {
    notes,
    settings,
    loaded,
    loading,
    saving,
    saveError,
    hasNotes,
    activeCount,
    load,
    saveNow,
    queueSave,
    addNote,
    updateNote,
    removeNote,
    markShown,
    markDone,
    snooze,
    beginTurn,
    pick,
  }
}