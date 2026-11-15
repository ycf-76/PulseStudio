<template>
  <Transition name="cpc-fade">
    <!-- 2026-11-04 改版：排版迁移到 Codex 语法——└ 状态行 + 「• 时间码 内容」条目 +
         页脚动作行（主动作加粗）。自身头部已去掉（VideoTaskCard 统一提供），逻辑不动。 -->
    <div v-if="!isProcessed" class="cpc-card" :class="{ 'cpc-error': errorMsg }">
      <p class="cpc-sub"><span class="cpc-elbow">└</span> {{ messageLine }}</p>

      <!-- 叠加条目：• 时间码 模板 内容 对齐排版 -->
      <div v-if="segments.length" class="cpc-list">
        <div v-for="(seg, i) in segments" :key="seg.segment_id || i" class="cpc-item">
          <span class="cpc-dim">•</span>
          <span class="cpc-time">{{ fmtTime(seg.time_start) }}–{{ fmtTime(seg.time_end) }}</span>
          <span :class="seg.skipped ? 'cpc-dim' : 'cpc-t'">{{ templateLabel(seg) }}{{ seg.skipped ? '（跳过）' : '' }}</span>
        </div>
      </div>

      <p v-if="narrativeTheme" class="cpc-sub"><span class="cpc-elbow">└</span> 主题：{{ narrativeTheme }}</p>
      <p v-for="(s, i) in narrativeSections" :key="i" class="cpc-sub cpc-dim">
        <span class="cpc-elbow">└</span> {{ s.role || '段落 ' + (i + 1) }}：{{ s.visual_intent || '' }}
      </p>
      <p v-if="palette.length" class="cpc-sub cpc-palette">
        <span class="cpc-elbow">└</span> 配色
        <span v-for="(c, i) in palette" :key="i" class="cpc-dot" :style="{ background: c }" :title="c" />
      </p>

      <div v-if="errorMsg" class="cpc-error-msg">
        <span class="cpc-error-text">{{ errorMsg }}</span>
        <button class="cpc-error-retry" @click="errorMsg = ''">关闭</button>
      </div>

      <div class="cpc-revise">
        <input
          v-model="reviseText"
          class="cpc-revise-input"
          placeholder="说说哪里要改，让 AI 重排方案…"
          @keydown.enter="askRevise"
        />
        <button class="cpc-revise-btn" :disabled="submitting || !reviseText.trim()" @click="askRevise">
          让 AI 改
        </button>
      </div>

      <p class="cpc-foot">
        <b class="cpc-act-pri" @click="!submitting && confirmPlan()">{{ submitting ? '渲染中…' : '开始渲染' }}</b>
        <span class="cpc-dim"> · </span>
        <span class="cpc-act" @click="!submitting && cancelAll()">取消</span>
        <span class="cpc-dim cpc-hint">（确认前不会改动或生成任何视频）</span>
      </p>
    </div>
  </Transition>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import { authFetch } from '@/api/client'
import { parseHttpErrorMessage } from './cell-types'

/** OverlayPlan.segments 元素（overlay_orchestrator.py 落盘形状） */
export interface CutPlanSegment {
  segment_id?: string
  time_start?: number
  time_end?: number
  template?: string
  params?: Record<string, any>
  skipped?: boolean
}

export interface CutPlan {
  video_id?: string
  segments?: CutPlanSegment[]
  palette?: string[]
  narrative?: { theme?: string; sections?: { role?: string; visual_intent?: string }[] } | null
}

const TEMPLATE_LABELS: Record<string, string> = {
  highlight: '重点强调',
  subtitle: '花字字幕',
  sticker: '贴纸',
  emoji: '表情',
  caption: '字幕条',
}

const props = defineProps<{
  sessionId: string
  loopStatePath: string
  plan: CutPlan | null
  message?: string
  turnId?: string
  /** SSE 回报的渲染失败原因。失败时卡片不消失，可重试 */
  failedError?: string
}>()

const emit = defineEmits<{
  confirmed: [data: any]
  cancelled: []
  revise: [comment: string]
}>()

const submitting = ref(false)
const isProcessed = ref(false)
const errorMsg = ref('')
const reviseText = ref('')

const segments = computed<CutPlanSegment[]>(() => props.plan?.segments || [])
const activeSegments = computed(() => segments.value.filter(s => !s.skipped))
const activeCount = computed(() => activeSegments.value.length)
const skippedCount = computed(() => segments.value.length - activeSegments.value.length)

const totalDuration = computed(() => {
  const span = timelineSpan.value
  return span ? (span.end - span.start).toFixed(1) : '0'
})

// 时间轴总跨度：取所有 segment 的 min/max，避免留白失真
const timelineSpan = computed(() => {
  const times = segments.value.flatMap(s => [Number(s.time_start ?? NaN), Number(s.time_end ?? NaN)]).filter(t => !Number.isNaN(t))
  if (!times.length) return null
  const start = Math.min(...times)
  const end = Math.max(...times)
  if (end <= start) return null
  return { start, end }
})

// └ 状态行文案
const messageLine = computed(() => {
  if (props.message) return props.message
  const base = `共 ${activeCount.value} 处叠加 · 覆盖 ${totalDuration.value}s`
  return skippedCount.value > 0 ? `${base}（跳过 ${skippedCount.value} 处）` : base
})

function templateLabel(seg: CutPlanSegment): string {
  return TEMPLATE_LABELS[seg.template || ''] || seg.template || '叠加'
}

/** 秒 → m:ss 时间码（如 0:03 / 1:24） */
function fmtTime(t?: number): string {
  const n = Number(t)
  if (Number.isNaN(n)) return '0:00'
  const m = Math.floor(n / 60)
  const s = Math.round(n - m * 60)
  return `${m}:${String(s).padStart(2, '0')}`
}

const narrativeTheme = computed(() => props.plan?.narrative?.theme || '')
const narrativeSections = computed(() => props.plan?.narrative?.sections || [])
const palette = computed(() => (props.plan?.palette || []).slice(0, 8))

// 404 用叠加方案语义的专用文案
function parseErrorMessage(status: number, body: string): string {
  return parseHttpErrorMessage(status, body, {
    notFoundMessage: '叠加方案待确认状态已过期，请重新发起口播增强',
  })
}

async function postConfirm(body: Record<string, any>) {
  const res = await authFetch('/api/v1/chat/confirm-cut-plan', {
    method: 'POST',
    body: JSON.stringify({
      session_id: props.sessionId,
      loop_state_path: props.loopStatePath,
      turn_id: props.turnId || null,
      ...body,
    }),
  })
  if (!res.ok) {
    const errBody = await res.text()
    console.error('[CutPlanConfirmCard] confirm-cut-plan failed:', res.status, errBody)
    throw new Error(parseErrorMessage(res.status, errBody))
  }
  return res.json().catch(() => null)
}

async function confirmPlan() {
  if (submitting.value) return
  if (!props.sessionId || !props.loopStatePath) {
    errorMsg.value = '会话信息缺失，请刷新页面后重试'
    return
  }

  submitting.value = true
  errorMsg.value = ''
  try {
    const data = await postConfirm({ action: 'confirm' })
    isProcessed.value = true
    emit('confirmed', data)
  } catch (e: any) {
    const msg = e?.message || ''
    // 404 = 方案状态已过期（历史死卡）。不再停留报错，直接退场：
    // 后端此时已把库里状态标成 cancelled，这里同步本地状态让卡片收起。
    if (msg.includes('已过期')) {
      isProcessed.value = true
      emit('cancelled')
      return
    }
    errorMsg.value = msg.includes('Failed to fetch')
      ? '网络连接失败，请检查网络后重试'
      : `操作出错：${msg || '未知错误'}`
  } finally {
    submitting.value = false
  }
}

async function cancelAll() {
  if (submitting.value) return
  submitting.value = true
  errorMsg.value = ''
  try {
    await postConfirm({ action: 'cancel' })
    isProcessed.value = true
    emit('cancelled')
  } catch (e: any) {
    const msg = e?.message || ''
    // 同 confirm：过期的死卡点取消直接退场（后端已同步清掉库里状态）
    if (msg.includes('已过期')) {
      isProcessed.value = true
      emit('cancelled')
      return
    }
    errorMsg.value = `取消出错：${msg || '未知错误'}`
  } finally {
    submitting.value = false
  }
}

function askRevise() {
  const comment = reviseText.value.trim()
  if (!comment || submitting.value) return
  // 让 AI 重排：把用户意见带回对话，Agent 重新规划叠加方案（新方案仍过闸门）
  isProcessed.value = true
  emit('revise', comment)
}
</script>

<style scoped>
/* Codex 排版语法 · 墨色系：└ 状态行 / • 时间码条目 / 页脚动作行，零紫 */
.cpc-card {
  margin-top: 4px;
}

.cpc-sub {
  margin: 2px 0 0;
  font-size: 12.5px;
  line-height: 1.7;
  color: #5f5e5a;
  word-break: break-word;
}

.cpc-elbow { color: #b4b2a9; margin-right: 2px; }

.cpc-dim { color: #b4b2a9; }

.cpc-list {
  margin: 6px 0 0 14px;
  font-size: 12.5px;
  line-height: 1.8;
}

.cpc-item { display: flex; align-items: baseline; gap: 8px; min-width: 0; }

.cpc-time {
  flex: none;
  font-family: var(--font-mono, 'SF Mono', 'JetBrains Mono', monospace);
  font-size: 11.5px;
  color: #2c2c2a;
  font-variant-numeric: tabular-nums;
}

.cpc-t { color: #5f5e5a; }

.cpc-palette { display: flex; align-items: center; gap: 4px; }

.cpc-dot {
  display: inline-block;
  width: 10px;
  height: 10px;
  border-radius: 50%;
  border: 0.5px solid rgba(0, 0, 0, 0.12);
}

.cpc-error-card { border-color: #f6cfcb; }

.cpc-error-msg {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 6px 10px;
  margin: 8px 0 0 14px;
  background: #fdf1f0;
  border: 1px solid #f6cfcb;
  border-radius: 8px;
}

.cpc-error-text {
  font-size: 12px;
  color: #a32d2d;
  line-height: 1.5;
  flex: 1;
}

.cpc-error-retry {
  flex-shrink: 0;
  padding: 2px 8px;
  font-size: 11px;
  color: #a32d2d;
  background: #fff;
  border: 1px solid #f6cfcb;
  border-radius: 6px;
  cursor: pointer;
  font-family: inherit;
}

/* 修改意见输入：发丝下划线形态，不做盒子 */
.cpc-revise {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 10px 0 0 14px;
  border-bottom: 0.5px solid rgba(0, 0, 0, 0.12);
  padding-bottom: 4px;
}

.cpc-revise:focus-within { border-bottom-color: #444441; }

.cpc-revise-input {
  flex: 1;
  min-width: 0;
  border: none;
  outline: none;
  background: transparent;
  font-size: 12.5px;
  font-family: inherit;
  color: #2c2c2a;
  padding: 2px 0;
}

.cpc-revise-input::placeholder { color: #b4b2a9; }

.cpc-revise-btn {
  flex: none;
  padding: 2px 6px;
  border: none;
  background: transparent;
  font-size: 12px;
  color: #888780;
  cursor: pointer;
  font-family: inherit;
}

.cpc-revise-btn:hover:not(:disabled) { color: #2c2c2a; }
.cpc-revise-btn:disabled { opacity: 0.4; cursor: default; }

/* 页脚动作行：主动作加粗、其余灰字、· 分隔 */
.cpc-foot {
  margin: 10px 0 0 14px;
  font-size: 12.5px;
  color: #888780;
}

.cpc-act-pri {
  color: #2c2c2a;
  font-weight: 500;
  cursor: pointer;
}

.cpc-act { cursor: pointer; }
.cpc-act:hover { color: #2c2c2a; text-decoration: underline; }
.cpc-act-pri:hover { text-decoration: underline; }

.cpc-hint { font-size: 11px; margin-left: 4px; }

.cpc-fade-leave-active {
  transition: opacity 0.2s ease;
}

.cpc-fade-leave-to {
  opacity: 0;
}
</style>
