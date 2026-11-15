<template>
  <Transition name="clfc-fade">
    <!-- 已处理：Codex 形态的结果记录卡——问答留痕，不静默消失 -->
    <div v-if="isProcessed && answeredPairs.length > 0" class="clfc-card clfc-result-card">
      <div class="clfc-result-head">
        <Check :size="14" class="clfc-result-icon" />
        <span class="clfc-result-title">创作偏好已确认</span>
        <span class="clfc-result-meta">{{ answeredPairs.length }} 项<template v-if="currentBatch && currentBatch.total_batches > 1"> · 第 {{ (currentBatch.batch_index ?? 0) + 1 }}/{{ currentBatch.total_batches }} 批</template></span>
      </div>
      <div class="clfc-result-list">
        <div v-for="pair in answeredPairs" :key="pair.q" class="clfc-result-row">
          <span class="clfc-result-q">{{ pair.q }}</span>
          <span class="clfc-result-a">{{ pair.a }}</span>
        </div>
      </div>
    </div>

    <!-- 提问态：对齐 Codex request_user_input——灰字进度行置顶，无图标无大标题 -->
    <div v-else-if="!isProcessed && currentQuestion" class="clfc-card" :class="{ 'clfc-error-card': errorMsg }">
      <!-- 进度行：问题 1/2 · 未答 1 · 第 1/2 批 -->
      <div class="clfc-progress-line">
        <span>问题 {{ questionPage + 1 }}/{{ totalPages }}</span>
        <span v-if="unansweredCount > 0"> · 未答 {{ unansweredCount }}</span>
        <span v-if="currentBatch && currentBatch.total_batches > 1"> · 第 {{ currentBatch.batch_index + 1 }}/{{ currentBatch.total_batches }} 批</span>
        <span v-if="currentBatch?.title" class="clfc-progress-batch">{{ currentBatch.title }}</span>
      </div>

      <p v-if="currentBatch?.description && questionPage === 0" class="clfc-desc">
        {{ currentBatch.description }}
      </p>

      <!-- 单页单问 -->
      <Transition name="clfc-slide" mode="out-in">
        <div :key="currentBatch?.batch_id + '-' + questionPage" class="clfc-question">
          <div class="clfc-q-head">
            <span class="clfc-q-index">Q{{ questionPage + 1 }}</span>
            <span class="clfc-q-text">
              {{ currentQuestion.question }}
              <span v-if="currentQuestion.required" class="clfc-required">*</span>
              <span v-else class="clfc-optional-tag">选填</span>
            </span>
          </div>

          <!-- 单选：单列全宽选项行（Claude AskUserQuestion 形态） -->
          <div v-if="currentQuestion.type === 'single_choice' && currentQuestion.options.length > 0" class="clfc-opt-list">
            <button
              v-for="opt in currentQuestion.options"
              :key="opt.value"
              type="button"
              class="clfc-opt-row"
              :class="{ 'clfc-opt-selected': answers[currentQuestion.id] === opt.value }"
              @click="selectOption(currentQuestion.id, opt.value)"
            >
              <span class="clfc-marker" aria-hidden="true"></span>
              <img v-if="opt.preview_url" :src="opt.preview_url" class="clfc-opt-preview" alt="" />
              <span class="clfc-opt-body">
                <span class="clfc-opt-label">{{ opt.label }}</span>
                <span v-if="opt.description" class="clfc-opt-desc">{{ opt.description }}</span>
              </span>
            </button>
            <!-- 其他：并入选项列表末行，radio + 行内输入 -->
            <div
              class="clfc-opt-row clfc-custom-row"
              :class="{ 'clfc-opt-selected': isCustomSelected(currentQuestion.id, currentQuestion.options) }"
            >
              <span class="clfc-marker" aria-hidden="true"></span>
              <input
                type="text"
                class="clfc-custom-input"
                :value="customValue(currentQuestion.id, currentQuestion.options)"
                @input="selectOption(currentQuestion.id, ($event.target as HTMLInputElement).value)"
                placeholder="其他，自定义…"
              />
            </div>
          </div>

          <!-- 多选：方形 checkbox 行 -->
          <div v-else-if="currentQuestion.type === 'multi_choice' && currentQuestion.options.length > 0" class="clfc-opt-list">
            <button
              v-for="opt in currentQuestion.options"
              :key="opt.value"
              type="button"
              class="clfc-opt-row clfc-multi"
              :class="{ 'clfc-opt-selected': (answers[currentQuestion.id] || []).includes(opt.value) }"
              @click="toggleMultiOption(currentQuestion.id, opt.value)"
            >
              <span class="clfc-marker" aria-hidden="true"></span>
              <span class="clfc-opt-body">
                <span class="clfc-opt-label">{{ opt.label }}</span>
                <span v-if="opt.description" class="clfc-opt-desc">{{ opt.description }}</span>
              </span>
            </button>
            <div
              class="clfc-opt-row clfc-multi clfc-custom-row"
              :class="{ 'clfc-opt-selected': isCustomSelectedMulti(currentQuestion.id, currentQuestion.options) }"
            >
              <span class="clfc-marker" aria-hidden="true"></span>
              <input
                type="text"
                class="clfc-custom-input"
                :value="customValueMulti(currentQuestion.id, currentQuestion.options)"
                @input="answers[currentQuestion.id] = ($event.target as HTMLInputElement).value"
                placeholder="其他，自定义…"
              />
            </div>
          </div>

          <!-- 滑杆 -->
          <div v-else-if="currentQuestion.type === 'slider'" class="clfc-slider-row">
            <input
              type="range"
              :min="currentQuestion.min_value"
              :max="currentQuestion.max_value"
              :value="answers[currentQuestion.id] ?? currentQuestion.default ?? currentQuestion.min_value"
              @input="answers[currentQuestion.id] = Number(($event.target as HTMLInputElement).value)"
              class="clfc-slider"
            />
            <span class="clfc-slider-val">{{ answers[currentQuestion.id] ?? currentQuestion.default ?? currentQuestion.min_value }}</span>
          </div>

          <!-- 文本输入 -->
          <input
            v-else
            type="text"
            :value="answers[currentQuestion.id] ?? currentQuestion.default ?? ''"
            @input="answers[currentQuestion.id] = ($event.target as HTMLInputElement).value"
            :placeholder="currentQuestion.placeholder || '请输入…'"
            class="clfc-text-input"
          />
        </div>
      </Transition>

      <p v-if="currentBatch?.next_batch_hint && isLastPage" class="clfc-hint">
        下一批预告：{{ currentBatch.next_batch_hint }}
      </p>

      <!-- 错误条 -->
      <div v-if="errorMsg" class="clfc-error-msg">
        <span class="clfc-error-text">{{ errorMsg }}</span>
        <button class="clfc-error-retry" @click="clearError">重试</button>
      </div>

      <!-- 底部操作：左侧取消/跳过，右侧翻页 -->
      <div class="clfc-footer">
        <div class="clfc-footer-left">
          <button class="clfc-btn-text" @click="cancelAll" :disabled="submitting">取消</button>
          <button class="clfc-btn-text" @click="skipBatch" :disabled="submitting">跳过本批</button>
          <span class="clfc-scope">仅本次生效</span>
        </div>
        <div class="clfc-footer-right">
          <button v-if="questionPage > 0" class="clfc-btn clfc-btn-secondary" @click="prevPage" :disabled="submitting">
            上一步
          </button>
          <button
            v-if="!isLastPage"
            class="clfc-btn clfc-btn-primary"
            @click="nextPage"
            :disabled="submitting || !canAdvance"
          >
            下一步
          </button>
          <button
            v-else
            class="clfc-btn clfc-btn-primary"
            @click="submitAnswers"
            :disabled="submitting || !canSubmit"
          >
            {{ submitting ? '提交中…' : submitLabel }}
          </button>
        </div>
      </div>
    </div>
  </Transition>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { authFetch } from '@/api/client'
import { Check } from 'lucide-vue-next'
import { parseHttpErrorMessage } from './cell-types'

interface ClarifyOption {
  label: string
  value: string
  description?: string
  preview_url?: string
}

interface ClarifyQuestion {
  id: string
  source_skill: string
  question: string
  type: string
  options: ClarifyOption[]
  default: string
  required: boolean
  placeholder: string
  min_value: number
  max_value: number
}

interface ClarificationBatch {
  batch_id: string
  batch_index: number
  total_batches: number
  title: string
  description: string
  questions: ClarifyQuestion[]
  next_batch_hint: string
  requires_confirmation: boolean
}

const props = defineProps<{
  sessionId: string
  loopStatePath: string
  batches: ClarificationBatch[]
  prompt?: string
  skillName?: string
  turnId?: string
}>()

const emit = defineEmits<{
  clarified: [answers: Record<string, any>, data?: any]
  skipped: []
  cancelled: []
}>()

const submitting = ref(false)
const isProcessed = ref(false)
const errorMsg = ref('')
const answers = ref<Record<string, any>>({})
const currentBatchIndex = ref(0)
// 翻页态：一页一问，批次切换时回到第一页
const questionPage = ref(0)

const currentBatch = computed(() => {
  if (!props.batches || props.batches.length === 0) return null
  return props.batches[Math.min(currentBatchIndex.value, props.batches.length - 1)]
})

const currentQuestions = computed(() => currentBatch.value?.questions || [])
const totalPages = computed(() => Math.max(currentQuestions.value.length, 1))
const currentQuestion = computed(() => currentQuestions.value[questionPage.value] || null)
const isLastPage = computed(() => questionPage.value >= totalPages.value - 1)

// Codex 进度行语义：未答 = 本批必填里还没给出有效答案的题数
const unansweredCount = computed(() => {
  let n = 0
  for (const q of currentQuestions.value) {
    if (!q.required) continue
    const val = answers.value[q.id]
    if (val === undefined || val === null || val === '' || (Array.isArray(val) && val.length === 0)) n++
  }
  return n
})

// 结果记录卡：把已答的「问题 → 答案」对齐排版（Codex RequestUserInputResultCell 形态）
const answeredPairs = computed(() => {
  const pairs: Array<{ q: string; a: string }> = []
  for (const batch of props.batches || []) {
    for (const q of batch.questions || []) {
      const val = answers.value[q.id]
      if (val === undefined || val === null || val === '' || (Array.isArray(val) && val.length === 0)) continue
      let a: string
      if (Array.isArray(val)) {
        a = val
          .map((v: string) => q.options.find((o) => o.value === v)?.label || v)
          .join('、')
      } else if (q.options.length > 0) {
        a = q.options.find((o) => o.value === val)?.label || String(val)
      } else {
        a = String(val)
      }
      pairs.push({ q: q.question, a })
    }
  }
  return pairs
})

// 批次变化（含后端返回下一批）时页码归零
watch(
  () => currentBatch.value?.batch_id,
  () => {
    questionPage.value = 0
  }
)

const submitLabel = computed(() => {
  if (!currentBatch.value) return '确认'
  if (currentBatch.value.requires_confirmation) return '确认并提交'
  if (currentBatchIndex.value < props.batches.length - 1) return '下一步'
  return '确认并继续'
})

// 当前页必填已答才允许翻下一页
const canAdvance = computed(() => {
  const q = currentQuestion.value
  if (!q || !q.required) return true
  const val = answers.value[q.id]
  return !(val === undefined || val === null || val === '' || (Array.isArray(val) && val.length === 0))
})

const canSubmit = computed(() => {
  if (!props.sessionId || !props.loopStatePath) return false
  if (!currentBatch.value) return false
  for (const q of currentBatch.value.questions) {
    if (q.required) {
      const val = answers.value[q.id]
      if (val === undefined || val === null || val === '' || (Array.isArray(val) && val.length === 0)) {
        return false
      }
    }
  }
  return true
})

function nextPage() {
  if (questionPage.value < totalPages.value - 1) questionPage.value++
}

function prevPage() {
  if (questionPage.value > 0) questionPage.value--
}

function clearError() {
  errorMsg.value = ''
}

function selectOption(qId: string, value: string) {
  answers.value[qId] = value
}

function toggleMultiOption(qId: string, value: string) {
  const current = (answers.value[qId] || []) as string[]
  const idx = current.indexOf(value)
  if (idx >= 0) {
    answers.value[qId] = current.filter((v: string) => v !== value)
  } else {
    answers.value[qId] = [...current, value]
  }
}

// "其他"行的选中态：答案存在且不命中任何预置选项
function isCustomSelected(qId: string, options: ClarifyOption[]): boolean {
  const val = answers.value[qId]
  return !!val && !options.some((o) => o.value === val)
}

function customValue(qId: string, options: ClarifyOption[]): string {
  const val = answers.value[qId]
  return isCustomSelected(qId, options) ? (val as string) : ''
}

function isCustomSelectedMulti(qId: string, options: ClarifyOption[]): boolean {
  const val = answers.value[qId]
  return typeof val === 'string' && val !== '' && !options.some((o) => val.includes(o.value))
}

function customValueMulti(qId: string, options: ClarifyOption[]): string {
  const val = answers.value[qId]
  return isCustomSelectedMulti(qId, options) ? (val as string) : ''
}

// 错误体解析统一走 cell-types 的 parseHttpErrorMessage（曾与 ChatConfirmCard
// 各存一份重复实现）。404 用澄清语义的专用文案。
function parseErrorMessage(status: number, body: string): string {
  return parseHttpErrorMessage(status, body, { notFoundMessage: '澄清状态已过期，请重新发起操作' })
}

async function submitAnswers() {
  if (submitting.value) return
  if (!props.sessionId || !props.loopStatePath) {
    errorMsg.value = '会话信息缺失，请刷新页面后重试'
    return
  }

  submitting.value = true
  errorMsg.value = ''

  try {
    const fieldAnswers: Record<string, any> = {}
    for (const [qId, val] of Object.entries(answers.value)) {
      let fieldName = qId
      if (fieldName.includes('__')) {
        fieldName = fieldName.split('__').pop() || fieldName
      }
      fieldName = fieldName.replace(/^clarify_/, '')
      fieldAnswers[fieldName] = val
    }

    const res = await authFetch('/api/v1/chat/clarify', {
      method: 'POST',
      body: JSON.stringify({
        session_id: props.sessionId,
        loop_state_path: props.loopStatePath,
        answers: fieldAnswers,
        batch_id: currentBatch.value?.batch_id || '',
        action: 'answer',
        turn_id: props.turnId || null,
      }),
    })

    if (!res.ok) {
      const errBody = await res.text()
      console.error('[ChatClarificationCard] clarify failed:', res.status, errBody)
      errorMsg.value = parseErrorMessage(res.status, errBody)
      return
    }

    const data = await res.json().catch(() => null)
    isProcessed.value = true
    emit('clarified', fieldAnswers, data)
  } catch (e: any) {
    console.error('[ChatClarificationCard] clarify error:', e)
    errorMsg.value = e?.message?.includes('Failed to fetch')
      ? '网络连接失败，请检查网络后重试'
      : `操作出错：${e?.message || '未知错误'}`
  } finally {
    submitting.value = false
  }
}

async function skipBatch() {
  if (submitting.value) return
  submitting.value = true
  errorMsg.value = ''
  try {
    const res = await authFetch('/api/v1/chat/clarify', {
      method: 'POST',
      body: JSON.stringify({
        session_id: props.sessionId,
        loop_state_path: props.loopStatePath,
        answers: {},
        batch_id: currentBatch.value?.batch_id || '',
        action: 'skip',
        turn_id: props.turnId || null,
      }),
    })
    if (!res.ok) {
      const errBody = await res.text()
      errorMsg.value = parseErrorMessage(res.status, errBody)
      return
    }
    const data = await res.json().catch(() => null)
    if (data?.status === 'awaiting_clarification' && data?.clarification_batches?.length) {
      // 后端返回下一批问题，更新批次
      currentBatchIndex.value++
    } else {
      // 澄清完成或最后一批被跳过
      isProcessed.value = true
      emit('skipped')
    }
  } catch (e: any) {
    errorMsg.value = `跳过出错：${e?.message || '未知错误'}`
  } finally {
    submitting.value = false
  }
}

async function cancelAll() {
  if (submitting.value) return
  submitting.value = true
  try {
    const res = await authFetch('/api/v1/chat/clarify', {
      method: 'POST',
      body: JSON.stringify({
        session_id: props.sessionId,
        loop_state_path: props.loopStatePath,
        answers: {},
        action: 'cancel',
        turn_id: props.turnId || null,
      }),
    })
    if (res.ok) {
      isProcessed.value = true
      emit('cancelled')
    } else {
      const errBody = await res.text()
      errorMsg.value = parseErrorMessage(res.status, errBody)
    }
  } catch (e: any) {
    errorMsg.value = `取消出错：${e?.message || '未知错误'}`
  } finally {
    submitting.value = false
  }
}
</script>

<style scoped>
.clfc-card {
  width: 100%;
  max-width: 560px;
  background: var(--rl-card, #ffffff);
  border: 1px solid #e7e5f0;
  /* 2026-11-05：圆角对齐输入卡（.rl-composer-card 24px），同一视觉语言 */
  border-radius: 24px;
  box-shadow: 0 2px 4px rgba(28, 25, 54, 0.04), 0 8px 24px rgba(28, 25, 54, 0.07);
  padding: 14px 18px 12px;
  margin-top: 6px;
  transition: border-color 0.2s ease;
  display: flex;
  flex-direction: column;
}

/* ===== Codex 式进度行：置顶灰字，无图标无大标题 ===== */
.clfc-progress-line {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
  color: #8a86a3;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
  overflow: hidden;
}

.clfc-progress-batch {
  margin-left: auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  color: #a5a1bd;
}

/* ===== 结果记录卡：问答留痕 ===== */
.clfc-result-card {
  padding: 10px 14px;
}

.clfc-result-head {
  display: flex;
  align-items: center;
  gap: 6px;
}

.clfc-result-icon {
  color: #444441;
  flex-shrink: 0;
}

.clfc-result-title {
  font-size: 12.5px;
  font-weight: 500;
  color: #2b2749;
}

.clfc-result-meta {
  margin-left: auto;
  font-size: 11px;
  color: #a5a1bd;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.clfc-result-list {
  margin-top: 8px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.clfc-result-row {
  display: flex;
  align-items: baseline;
  gap: 10px;
  font-size: 12px;
  line-height: 1.5;
  min-width: 0;
}

.clfc-result-q {
  flex: 0 0 auto;
  max-width: 45%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: #8a86a3;
}

.clfc-result-a {
  flex: 1;
  min-width: 0;
  color: #35315a;
  font-weight: 500;
}

.clfc-desc {
  font-size: 12px;
  color: #7d7994;
  line-height: 1.5;
  margin: 10px 0 0;
}

/* ===== 问题分组 ===== */
.clfc-question {
  padding-top: 12px;
}

.clfc-q-head {
  display: flex;
  align-items: baseline;
  gap: 6px;
  margin-bottom: 10px;
}

.clfc-q-index {
  font-size: 11px;
  font-weight: 600;
  color: #8d87b8;
  background: #f7f7f5;
  border-radius: 5px;
  padding: 1px 6px;
  flex-shrink: 0;
  transform: translateY(-1px);
}

.clfc-q-text {
  font-size: 13.5px;
  font-weight: 600;
  color: #1e1b33;
  line-height: 1.5;
}

.clfc-required {
  color: #e0443c;
  font-weight: 600;
}

.clfc-optional-tag {
  font-size: 11px;
  color: #a5a1bd;
  font-weight: 400;
}

/* ===== 选项行 ===== */
.clfc-opt-list {
  display: flex;
  flex-direction: column;
  gap: 7px;
}

.clfc-opt-row {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  width: 100%;
  text-align: left;
  padding: 9px 12px;
  background: #f9f9f7;
  border: 1px solid #e8e7e2;
  border-radius: 12px;
  cursor: pointer;
  transition: border-color 0.15s, background 0.15s, box-shadow 0.15s;
  font-family: inherit;
}

.clfc-opt-row:hover {
  border-color: #b4b2a9;
  background: #f2f2ef;
}

.clfc-opt-row.clfc-opt-selected {
  border-color: #444441;
  background: #efefec;
  box-shadow: 0 0 0 3px rgba(83, 74, 183, 0.08);
}

.clfc-opt-row:focus-visible {
  outline: 2px solid #444441;
  outline-offset: 2px;
}

/* radio / checkbox 圆点 */
.clfc-marker {
  width: 15px;
  height: 15px;
  border-radius: 50%;
  border: 1.5px solid #c5c0dd;
  flex-shrink: 0;
  margin-top: 2px;
  position: relative;
  background: #fff;
  transition: border-color 0.15s;
}

.clfc-opt-row.clfc-multi .clfc-marker {
  border-radius: 5px;
}

.clfc-opt-row.clfc-opt-selected .clfc-marker {
  border-color: #444441;
}

.clfc-opt-row.clfc-opt-selected .clfc-marker::after {
  content: "";
  position: absolute;
  inset: 3px;
  border-radius: 50%;
  background: #444441;
}

.clfc-opt-row.clfc-multi.clfc-opt-selected .clfc-marker::after {
  border-radius: 2.5px;
}

.clfc-opt-body {
  flex: 1;
  min-width: 0;
}

.clfc-opt-label {
  display: block;
  font-size: 13px;
  font-weight: 500;
  color: #35315a;
  line-height: 1.45;
}

.clfc-opt-row.clfc-opt-selected .clfc-opt-label {
  color: #2b2570;
}

.clfc-opt-desc {
  display: block;
  font-size: 11.5px;
  color: #928daa;
  line-height: 1.5;
  margin-top: 1px;
}

.clfc-opt-preview {
  width: 44px;
  height: 33px;
  border-radius: 8px;
  object-fit: cover;
  flex-shrink: 0;
  order: 2;
  margin-left: auto;
}

/* 其他行：虚线边框 + 行内输入 */
.clfc-custom-row {
  background: transparent;
  border-style: dashed;
  border-color: #d9d8d2;
  cursor: text;
}

.clfc-custom-row:hover {
  border-color: #b4b2a9;
}

.clfc-custom-row.clfc-opt-selected {
  border-style: solid;
}

.clfc-custom-input {
  flex: 1;
  border: none;
  outline: none;
  background: transparent;
  font-size: 13px;
  font-family: inherit;
  color: #35315a;
  padding: 0;
}

.clfc-custom-input::placeholder {
  color: #b3aecb;
}

/* ===== 滑杆 ===== */
.clfc-slider-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 14px;
  background: #f9f9f7;
  border: 1px solid #e8e7e2;
  border-radius: 12px;
}

.clfc-slider {
  flex: 1;
  accent-color: #444441;
}

.clfc-slider-val {
  font-size: 13px;
  font-weight: 600;
  color: #444441;
  min-width: 30px;
  text-align: right;
  font-variant-numeric: tabular-nums;
}

/* ===== 文本输入 ===== */
.clfc-text-input {
  width: 100%;
  padding: 9px 12px;
  border: 1px solid #e8e7e2;
  border-radius: 12px;
  font-size: 13px;
  font-family: inherit;
  background: #f9f9f7;
  outline: none;
  transition: border-color 0.15s;
}

.clfc-text-input:focus {
  border-color: #444441;
  background: #fff;
}

/* ===== 提示 / 错误 ===== */
.clfc-hint {
  font-size: 11px;
  color: #a5a1bd;
  margin: 8px 0 0;
  font-style: italic;
}

.clfc-error-card {
  border-color: #f6cfcb;
}

.clfc-error-msg {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 7px 10px;
  margin-top: 10px;
  background: #fdf1f0;
  border: 1px solid #f6cfcb;
  border-radius: 10px;
}

.clfc-error-text {
  font-size: 12px;
  color: #c93f37;
  line-height: 1.5;
  flex: 1;
}

.clfc-error-retry {
  flex-shrink: 0;
  padding: 2px 8px;
  font-size: 11px;
  font-weight: 500;
  color: #c93f37;
  background: #fff;
  border: 1px solid #f6cfcb;
  border-radius: 8px;
  cursor: pointer;
  font-family: inherit;
}

/* ===== 底部操作 ===== */
.clfc-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px solid #eceae4;
}

.clfc-footer-left {
  display: flex;
  align-items: center;
  gap: 4px;
  min-width: 0;
}

.clfc-btn-text {
  background: transparent;
  border: none;
  font-family: inherit;
  font-size: 12px;
  color: #8a86a3;
  padding: 4px 8px;
  border-radius: 7px;
  cursor: pointer;
  transition: background 0.15s, color 0.15s;
}

.clfc-btn-text:hover:not(:disabled) {
  background: #f7f7f5;
  color: #5a5580;
}

.clfc-btn-text:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.clfc-scope {
  font-size: 11px;
  color: #b8b4cc;
  margin-left: 4px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.clfc-footer-right {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.clfc-btn {
  font-family: inherit;
  font-size: 12.5px;
  font-weight: 500;
  padding: 6px 16px;
  border-radius: 10px;
  border: none;
  cursor: pointer;
  transition: background 0.15s, border-color 0.15s, transform 0.1s;
}

.clfc-btn:active:not(:disabled) {
  transform: scale(0.97);
}

.clfc-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.clfc-btn-secondary {
  background: #fff;
  color: #5a5580;
  border: 1px solid #d9d8d2;
}

.clfc-btn-secondary:hover:not(:disabled) {
  border-color: #b4b2a9;
  background: #f9f9f7;
}

.clfc-btn-primary {
  background: #444441;
  color: #fff;
  box-shadow: 0 1px 3px rgba(83, 74, 183, 0.35);
}

.clfc-btn-primary:hover:not(:disabled) {
  background: #463da3;
}

/* ===== 动画 ===== */
.clfc-slide-enter-active,
.clfc-slide-leave-active {
  transition: opacity 0.18s ease, transform 0.18s ease;
}

.clfc-slide-enter-from {
  opacity: 0;
  transform: translateX(10px);
}

.clfc-slide-leave-to {
  opacity: 0;
  transform: translateX(-10px);
}

.clfc-fade-leave-active {
  transition: opacity 0.3s ease, transform 0.3s ease;
}

.clfc-fade-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}
</style>
