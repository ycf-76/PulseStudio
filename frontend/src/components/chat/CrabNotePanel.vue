<!--
  螃蟹便签面板
  ─────────────
  为什么 Teleport 到 body：
  螃蟹住在 .rl-composer-seat 里，座位与 .rl-chat 都有 overflow 约束，
  面板一旦画在座位内部就会被裁切。挪到 body + position:fixed 后，
  它既不受聊天区滚动影响，也不会挤压输入卡布局。

  代价是脱离 .rl-chat 的层叠上下文，取不到 --rl-* 主题变量，
  所以这里用 theme 属性自带一套同名变量，明暗两套值抄自 chat-view.css，
  保证和聊天区视觉一致（改动那边配色时记得同步这里）。
-->
<template>
  <Teleport to="body">
    <Transition name="cnp-pop">
      <div
        v-if="open"
        ref="panelRef"
        class="cnp-root"
        :class="{ 'cnp-dark': theme === 'dark' }"
        :style="panelStyle"
        @mousedown.stop
        @click.stop
      >
        <div class="cnp-head">
          <span class="cnp-title">
            <span class="cnp-crab">🦀</span>
            <span>小本子</span>
          </span>
          <span class="cnp-head-right">
            <span class="cnp-count" :title="`${activeCount} 条生效 / 共 ${notes.length} 条`">
              {{ activeCount }}/{{ notes.length }}
            </span>
            <button class="cnp-icon-btn" title="关闭" @click="emit('close')">×</button>
          </span>
        </div>

        <div class="cnp-tabs">
          <button class="cnp-tab" :class="{ 'is-on': tab === 'notes' }" @click="tab = 'notes'">
            便签
          </button>
          <button class="cnp-tab" :class="{ 'is-on': tab === 'quick' }" @click="tab = 'quick'">
            快捷
          </button>
          <button class="cnp-tab" :class="{ 'is-on': tab === 'settings' }" @click="tab = 'settings'">
            设置
          </button>
        </div>

        <div class="cnp-body">
          <!-- 便签列表 -->
          <template v-if="tab === 'notes'">
            <div v-if="!notes.length" class="cnp-empty">
              还没写便签。比如「每天 9 点发 3 条笔记」，我会在合适的时机提醒你。
            </div>

            <div
              v-for="n in notes"
              :key="n.id"
              class="cnp-note"
              :class="{ 'cnp-note-off': !n.enabled, 'cnp-note-done': doneToday(n) }"
            >
              <button class="cnp-note-emoji" title="换一个图标" @click="cycleEmoji(n)">
                {{ n.emoji }}
              </button>
              <div class="cnp-note-main">
                <div class="cnp-note-text">{{ n.text }}</div>
                <div class="cnp-note-meta">
                  <button class="cnp-chip" :title="'触发时机：' + TRIGGER_LABEL[n.trigger]" @click="cycleTrigger(n)">
                    {{ TRIGGER_LABEL[n.trigger] }}
                  </button>
                  <button class="cnp-chip" :title="'点开后的动作：' + ACTION_LABEL[n.action]" @click="cycleAction(n)">
                    {{ ACTION_LABEL[n.action] }}
                  </button>
                  <span v-if="doneToday(n)" class="cnp-done-tag">今天已做</span>
                </div>
              </div>
              <div class="cnp-note-ops">
                <button class="cnp-op cnp-op-use" title="用这条" @click="onApply(n)">用</button>
                <button class="cnp-op" :title="n.enabled ? '停用' : '启用'" @click="updateNote(n.id, { enabled: !n.enabled })">
                  {{ n.enabled ? '停' : '启' }}
                </button>
                <button class="cnp-op cnp-op-del" title="删除" @click="removeNote(n.id)">×</button>
              </div>
            </div>

            <div class="cnp-add">
              <input
                ref="draftRef"
                v-model="draft"
                class="cnp-input"
                :maxlength="MAX_NOTE_TEXT"
                placeholder="写下要提醒自己的事…"
                @keydown.enter.prevent="onAdd"
              />
              <button class="cnp-add-btn" :disabled="!draft.trim()" @click="onAdd">添加</button>
            </div>
            <div class="cnp-add-trigger">
              <span class="cnp-add-label">何时提醒</span>
              <button
                v-for="opt in TRIGGER_OPTIONS"
                :key="opt.value"
                class="cnp-chip"
                :class="{ 'is-on': draftTrigger === opt.value }"
                @click="draftTrigger = opt.value"
              >
                {{ opt.label }}
              </button>
            </div>
          </template>

          <!-- 快捷指令 -->
          <template v-else-if="tab === 'quick'">
            <div class="cnp-hint">点一下直接填进输入框；点「＋」把它存成会主动提醒的便签。</div>
            <div v-for="(q, i) in QUICK_PHRASES" :key="i" class="cnp-quick">
              <button class="cnp-quick-main" @click="emit('apply', { note: null, text: q.text, action: 'fill' })">
                <span class="cnp-quick-emoji">{{ q.emoji }}</span>
                <span class="cnp-quick-text">{{ q.text }}</span>
              </button>
              <button class="cnp-quick-save" title="存为便签" @click="saveQuick(q)">＋</button>
            </div>
          </template>

          <!-- 触发设置 -->
          <template v-else>
            <label class="cnp-row cnp-row-switch">
              <span class="cnp-row-label">自动冒泡</span>
              <input
                type="checkbox"
                class="cnp-switch"
                :checked="settings.notes_enabled"
                @change="onSettingChange({ notes_enabled: ($event.target as HTMLInputElement).checked })"
              />
            </label>
            <div class="cnp-row">
              <span class="cnp-row-label">免打扰时段</span>
              <input
                class="cnp-input cnp-input-sm"
                :value="settings.quiet_hours"
                placeholder="23-8（留空关闭）"
                @change="onSettingChange({ quiet_hours: ($event.target as HTMLInputElement).value.trim() })"
              />
            </div>
            <div class="cnp-row">
              <span class="cnp-row-label">每轮最多</span>
              <input
                class="cnp-input cnp-input-sm"
                type="number"
                min="0"
                max="20"
                :value="settings.max_per_session"
                @change="onSettingChange({ max_per_session: Number(($event.target as HTMLInputElement).value) })"
              />
            </div>
            <div class="cnp-row">
              <span class="cnp-row-label">气泡最小间隔</span>
              <span class="cnp-row-value">
                <input
                  class="cnp-input cnp-input-sm"
                  type="number"
                  min="0"
                  max="600"
                  :value="settings.global_cooldown_sec"
                  @change="onSettingChange({ global_cooldown_sec: Number(($event.target as HTMLInputElement).value) })"
                />
                <span class="cnp-unit">秒</span>
              </span>
            </div>
            <div class="cnp-hint cnp-hint-foot">
              便签在「会话结束 / 聚焦输入框 / 悬停螃蟹」时可能冒泡；同一轮最多冒 {{ settings.max_per_session }} 次，
              免打扰时段内完全不打扰。
            </div>
            <div v-if="saveError" class="cnp-error">{{ saveError }}</div>
          </template>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import {
  type CrabNote,
  type CrabNoteTrigger,
  type CrabNoteAction,
  type CrabNoteSettings,
  type CrabQuickPhrase,
  TRIGGER_LABEL,
  TRIGGER_OPTIONS,
  ACTION_LABEL,
  ACTION_OPTIONS,
  QUICK_PHRASES,
  MAX_NOTE_TEXT,
  isSameDay,
} from './crabNoteRules'
import { useCrabNotes } from './useCrabNotes'

const props = defineProps<{
  open: boolean
  anchor: { left: number; top: number; right: number; bottom: number } | null
  theme?: 'light' | 'dark'
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'apply', payload: { note: CrabNote | null; text: string; action: CrabNoteAction }): void
}>()

const {
  notes,
  settings,
  activeCount,
  saveError,
  addNote,
  updateNote,
  removeNote,
  markDone,
  queueSave,
} = useCrabNotes()

const tab = ref<'notes' | 'quick' | 'settings'>('notes')
const draft = ref('')
const draftTrigger = ref<CrabNoteTrigger>('turn_end')
const panelRef = ref<HTMLElement | null>(null)
const draftRef = ref<HTMLInputElement | null>(null)

const viewportW = ref(window.innerWidth)
const viewportH = ref(window.innerHeight)

const PANEL_W = 328
const PANEL_MAX_H = 440

const EMOJI_SET = ['📌', '⏰', '✍️', '📧', '🔥', '📊', '✅', '🎯', '💡', '🧾']

const panelStyle = computed(() => {
  const a = props.anchor
  const vw = viewportW.value
  const vh = viewportH.value
  if (!a) {
    return { left: '50%', bottom: '140px', transform: 'translateX(-50%)', maxHeight: `${PANEL_MAX_H}px` }
  }
  const left = Math.min(Math.max(8, a.right - PANEL_W), Math.max(8, vw - PANEL_W - 8))
  // 螃蟹归位时贴着输入卡右上角，空间充裕就往上展开（最自然）；
  // 用户把它拖到屏幕上半部时改为向下展开，避免顶出可视区。
  const above = a.top > 300
  const maxH = above
    ? Math.min(PANEL_MAX_H, Math.max(160, a.top - 24))
    : Math.min(PANEL_MAX_H, Math.max(160, vh - a.bottom - 24))
  return above
    ? { left: `${left}px`, bottom: `${vh - a.top + 10}px`, maxHeight: `${maxH}px` }
    : { left: `${left}px`, top: `${a.bottom + 10}px`, maxHeight: `${maxH}px` }
})

function doneToday(n: CrabNote): boolean {
  return isSameDay(n.last_done_at, Date.now())
}

function cycleTrigger(n: CrabNote) {
  const idx = TRIGGER_OPTIONS.findIndex(o => o.value === n.trigger)
  updateNote(n.id, { trigger: TRIGGER_OPTIONS[(idx + 1) % TRIGGER_OPTIONS.length].value })
}

function cycleAction(n: CrabNote) {
  const idx = ACTION_OPTIONS.findIndex(o => o.value === n.action)
  updateNote(n.id, { action: ACTION_OPTIONS[(idx + 1) % ACTION_OPTIONS.length].value })
}

function cycleEmoji(n: CrabNote) {
  const idx = EMOJI_SET.indexOf(n.emoji)
  updateNote(n.id, { emoji: EMOJI_SET[(idx + 1) % EMOJI_SET.length] })
}

function onAdd() {
  const text = draft.value.trim()
  if (!text) return
  addNote({ text, emoji: '📌', trigger: draftTrigger.value, action: 'fill' })
  draft.value = ''
}

function onApply(n: CrabNote) {
  if (n.action === 'remind') {
    markDone(n)
    emit('apply', { note: n, text: n.text, action: 'remind' })
    return
  }
  emit('apply', { note: n, text: n.text, action: n.action })
}

function saveQuick(q: CrabQuickPhrase) {
  addNote({ text: q.text, emoji: q.emoji, trigger: q.trigger, action: q.action })
  tab.value = 'notes'
}

function onSettingChange(patch: Partial<CrabNoteSettings>) {
  settings.value = { ...settings.value, ...patch }
  queueSave(0)
}

function onResize() {
  viewportW.value = window.innerWidth
  viewportH.value = window.innerHeight
}

function onWindowKey(e: KeyboardEvent) {
  if (e.key === 'Escape') emit('close')
}

watch(
  () => props.open,
  async (v) => {
    if (!v) return
    tab.value = 'notes'
    await nextTick()
    draftRef.value?.focus()
  },
)

onMounted(() => {
  window.addEventListener('resize', onResize)
  window.addEventListener('keydown', onWindowKey)
})

onUnmounted(() => {
  window.removeEventListener('resize', onResize)
  window.removeEventListener('keydown', onWindowKey)
})
</script>

<style scoped>
/* 与 chat-view.css 的 .rl-chat / .is-dark 保持一致；脱离 .rl-chat 后必须自带一份 */
.cnp-root {
  --rl-bg: #ffffff;
  --rl-bg-2: #fafbfc;
  --rl-card: #ffffff;
  --rl-border: rgba(0, 0, 0, 0.1);
  --rl-text-1: #1a1a1a;
  --rl-text-2: #4b4b4b;
  --rl-text-3: #9a9a9a;
  --rl-hover: rgba(0, 0, 0, 0.04);
  --rl-accent: #534ab7;

  position: fixed;
  z-index: 3000;
  width: 328px;
  display: flex;
  flex-direction: column;
  background: var(--rl-card);
  color: var(--rl-text-1);
  border: 1px solid var(--rl-border);
  border-radius: 14px;
  box-shadow: 0 12px 32px rgba(0, 0, 0, 0.14), 0 2px 6px rgba(0, 0, 0, 0.06);
  overflow: hidden;
  font-size: 12.5px;
  transform-origin: bottom right;
}

.cnp-dark {
  --rl-bg: #1a1a1a;
  --rl-bg-2: #1e1e1e;
  --rl-card: #222222;
  --rl-border: rgba(255, 255, 255, 0.09);
  --rl-text-1: #f1f5f9;
  --rl-text-2: #cbd5e1;
  --rl-text-3: #64748b;
  --rl-hover: rgba(255, 255, 255, 0.05);
  --rl-accent: #afa9ec;
  box-shadow: 0 12px 32px rgba(0, 0, 0, 0.55), 0 2px 6px rgba(0, 0, 0, 0.35);
}

.cnp-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 12px 8px;
  border-bottom: 1px solid var(--rl-border);
  flex: none;
}

.cnp-title {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  font-weight: 600;
  letter-spacing: 0.2px;
}

.cnp-crab {
  font-size: 15px;
  line-height: 1;
}

.cnp-head-right {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

.cnp-count {
  font-size: 11px;
  color: var(--rl-text-3);
  font-variant-numeric: tabular-nums;
}

.cnp-icon-btn {
  width: 20px;
  height: 20px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: none;
  background: transparent;
  color: var(--rl-text-3);
  font-size: 15px;
  line-height: 1;
  border-radius: 6px;
  cursor: pointer;
  transition: background 0.15s, color 0.15s;
}

.cnp-icon-btn:hover {
  background: var(--rl-hover);
  color: var(--rl-text-1);
}

.cnp-tabs {
  display: flex;
  gap: 2px;
  padding: 6px 10px;
  flex: none;
  border-bottom: 1px solid var(--rl-border);
}

.cnp-tab {
  flex: 1;
  padding: 5px 0;
  border: none;
  border-radius: 7px;
  background: transparent;
  color: var(--rl-text-3);
  font-size: 12px;
  cursor: pointer;
  transition: background 0.15s, color 0.15s;
}

.cnp-tab:hover {
  background: var(--rl-hover);
  color: var(--rl-text-2);
}

.cnp-tab.is-on {
  background: var(--rl-hover);
  color: var(--rl-text-1);
  font-weight: 600;
}

.cnp-body {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  padding: 8px 10px 10px;
  overscroll-behavior: contain;
}

.cnp-empty,
.cnp-hint {
  color: var(--rl-text-3);
  font-size: 11.5px;
  line-height: 1.6;
  padding: 6px 2px;
}

.cnp-hint-foot {
  border-top: 1px dashed var(--rl-border);
  margin-top: 8px;
  padding-top: 8px;
}

.cnp-note {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 8px;
  border-radius: 10px;
  transition: background 0.15s, opacity 0.15s;
}

.cnp-note:hover {
  background: var(--rl-hover);
}

.cnp-note-off {
  opacity: 0.45;
}

.cnp-note-done .cnp-note-text {
  text-decoration: line-through;
  text-decoration-color: var(--rl-text-3);
}

.cnp-note-emoji {
  flex: none;
  width: 26px;
  height: 26px;
  border: 1px solid var(--rl-border);
  border-radius: 8px;
  background: var(--rl-bg-2);
  font-size: 14px;
  line-height: 1;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  transition: transform 0.12s;
}

.cnp-note-emoji:hover {
  transform: scale(1.08);
}

.cnp-note-main {
  flex: 1 1 auto;
  min-width: 0;
}

.cnp-note-text {
  font-size: 12.5px;
  line-height: 1.5;
  color: var(--rl-text-1);
  word-break: break-word;
}

.cnp-note-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-top: 5px;
}

.cnp-chip {
  padding: 2px 6px;
  border: 1px solid var(--rl-border);
  border-radius: 6px;
  background: transparent;
  color: var(--rl-text-3);
  font-size: 10.5px;
  line-height: 1.4;
  cursor: pointer;
  transition: background 0.15s, color 0.15s, border-color 0.15s;
  white-space: nowrap;
}

.cnp-chip:hover {
  color: var(--rl-text-1);
  background: var(--rl-bg-2);
}

.cnp-chip.is-on {
  color: var(--rl-accent);
  border-color: var(--rl-accent);
}

.cnp-done-tag {
  padding: 2px 6px;
  border-radius: 6px;
  background: var(--rl-hover);
  color: var(--rl-text-3);
  font-size: 10.5px;
}

.cnp-note-ops {
  flex: none;
  display: flex;
  align-items: center;
  gap: 2px;
  opacity: 0;
  transition: opacity 0.15s;
}

.cnp-note:hover .cnp-note-ops {
  opacity: 1;
}

.cnp-op {
  min-width: 20px;
  height: 20px;
  padding: 0 4px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--rl-text-3);
  font-size: 11px;
  cursor: pointer;
  transition: background 0.15s, color 0.15s;
}

.cnp-op:hover {
  background: var(--rl-bg-2);
  color: var(--rl-text-1);
}

.cnp-op-use {
  color: var(--rl-accent);
  font-weight: 600;
}

.cnp-op-del:hover {
  color: #e05252;
}

.cnp-add {
  display: flex;
  gap: 6px;
  margin-top: 8px;
}

.cnp-input {
  flex: 1 1 auto;
  min-width: 0;
  height: 28px;
  padding: 0 9px;
  border: 1px solid var(--rl-border);
  border-radius: 8px;
  background: var(--rl-bg-2);
  color: var(--rl-text-1);
  font-size: 12px;
  outline: none;
  transition: border-color 0.15s;
}

.cnp-input::placeholder {
  color: var(--rl-text-3);
}

.cnp-input:focus {
  border-color: var(--rl-accent);
}

.cnp-input-sm {
  flex: none;
  width: 96px;
  height: 24px;
  text-align: center;
}

.cnp-add-btn {
  flex: none;
  height: 28px;
  padding: 0 12px;
  border: none;
  border-radius: 8px;
  background: var(--rl-accent);
  color: #fff;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  transition: opacity 0.15s;
}

.cnp-add-btn:disabled {
  opacity: 0.35;
  cursor: not-allowed;
}

.cnp-add-trigger {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px;
  margin-top: 8px;
}

.cnp-add-label {
  color: var(--rl-text-3);
  font-size: 11px;
  margin-right: 2px;
}

.cnp-quick {
  display: flex;
  align-items: stretch;
  gap: 4px;
  margin-bottom: 4px;
}

.cnp-quick-main {
  flex: 1 1 auto;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  border: 1px solid transparent;
  border-radius: 9px;
  background: var(--rl-bg-2);
  color: var(--rl-text-1);
  font-size: 12px;
  text-align: left;
  cursor: pointer;
  transition: border-color 0.15s, background 0.15s;
}

.cnp-quick-main:hover {
  border-color: var(--rl-border);
  background: var(--rl-hover);
}

.cnp-quick-emoji {
  flex: none;
  font-size: 14px;
  line-height: 1;
}

.cnp-quick-text {
  line-height: 1.45;
  word-break: break-word;
}

.cnp-quick-save {
  flex: none;
  width: 26px;
  border: 1px solid var(--rl-border);
  border-radius: 9px;
  background: transparent;
  color: var(--rl-text-3);
  font-size: 13px;
  cursor: pointer;
  transition: background 0.15s, color 0.15s;
}

.cnp-quick-save:hover {
  background: var(--rl-hover);
  color: var(--rl-text-1);
}

.cnp-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 7px 2px;
  border-bottom: 1px dashed var(--rl-border);
}

.cnp-row-label {
  color: var(--rl-text-2);
  font-size: 12px;
}

.cnp-row-value {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.cnp-unit {
  color: var(--rl-text-3);
  font-size: 11px;
}

.cnp-switch {
  width: 34px;
  height: 18px;
  appearance: none;
  border-radius: 10px;
  background: var(--rl-border);
  position: relative;
  cursor: pointer;
  transition: background 0.18s;
  outline: none;
}

.cnp-switch::after {
  content: '';
  position: absolute;
  top: 2px;
  left: 2px;
  width: 14px;
  height: 14px;
  border-radius: 50%;
  background: #fff;
  transition: transform 0.18s;
}

.cnp-switch:checked {
  background: var(--rl-accent);
}

.cnp-switch:checked::after {
  transform: translateX(16px);
}

.cnp-error {
  margin-top: 8px;
  padding: 6px 8px;
  border-radius: 8px;
  background: rgba(224, 82, 82, 0.1);
  color: #e05252;
  font-size: 11px;
}

.cnp-pop-enter-active {
  transition: opacity 0.18s ease, transform 0.18s cubic-bezier(0.2, 0.8, 0.2, 1);
}

.cnp-pop-leave-active {
  transition: opacity 0.14s ease, transform 0.14s ease;
}

.cnp-pop-enter-from,
.cnp-pop-leave-to {
  opacity: 0;
  transform: translateY(6px) scale(0.96);
}
</style>