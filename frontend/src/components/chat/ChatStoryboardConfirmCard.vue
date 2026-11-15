<template>
  <Transition name="sbc-fade">
    <div v-if="!isProcessed" class="sbc-card" :class="{ 'sbc-error': errorMsg }">
      <div class="sbc-header">
        <Clapperboard :size="18" class="sbc-icon" />
        <span class="sbc-title">分镜草稿待确认</span>
        <span class="sbc-badge">{{ shots.length }} 镜 · {{ totalDuration }}s</span>
      </div>

      <p class="sbc-desc">
        下面是 AI 根据你的照片编排的分镜脚本。<b>确认后才会开始生成视频</b>，
        你可以直接改文案、调时长、删镜头，或者让 AI 按你的意见重编。
      </p>

      <div class="sbc-shots">
        <div v-for="(shot, i) in shots" :key="i" class="sbc-shot">
          <div class="sbc-shot-head">
            <span class="sbc-shot-idx">{{ i + 1 }}</span>
            <input
              class="sbc-shot-caption"
              :value="shot.caption || ''"
              @input="patchShot(i, 'caption', ($event.target as HTMLInputElement).value)"
              placeholder="这一幕的旁白字幕"
            />
            <div class="sbc-shot-ops">
              <button class="sbc-icon-btn" :disabled="i === 0" title="上移" @click="moveShot(i, -1)">↑</button>
              <button class="sbc-icon-btn" :disabled="i === shots.length - 1" title="下移" @click="moveShot(i, 1)">↓</button>
              <button class="sbc-icon-btn sbc-icon-danger" :disabled="shots.length <= 1" title="删除此镜" @click="removeShot(i)">×</button>
            </div>
          </div>
          <div class="sbc-shot-row">
            <label class="sbc-field">
              <span class="sbc-field-label">画面</span>
              <input
                class="sbc-input"
                :value="shot.image_prompt || ''"
                @input="patchShot(i, 'image_prompt', ($event.target as HTMLInputElement).value)"
                placeholder="画面描述"
              />
            </label>
            <label class="sbc-field sbc-field-sm">
              <span class="sbc-field-label">运镜</span>
              <input
                class="sbc-input"
                :value="shot.motion_prompt || ''"
                @input="patchShot(i, 'motion_prompt', ($event.target as HTMLInputElement).value)"
                placeholder="镜头运动"
              />
            </label>
            <label class="sbc-field sbc-field-xs">
              <span class="sbc-field-label">秒</span>
              <input
                class="sbc-input"
                type="number"
                min="1"
                max="15"
                step="0.5"
                :value="shot.duration ?? 5"
                @input="patchShot(i, 'duration', Number(($event.target as HTMLInputElement).value) || 5)"
              />
            </label>
          </div>
        </div>
      </div>

      <div v-if="errorMsg" class="sbc-error-msg">
        <span class="sbc-error-text">{{ errorMsg }}</span>
        <button class="sbc-error-retry" @click="errorMsg = ''">关闭</button>
      </div>

      <div class="sbc-revise">
        <input
          v-model="reviseText"
          class="sbc-revise-input"
          placeholder="或者说说哪里要改，让 AI 重编一版…"
          @keydown.enter="askRevise"
        />
        <button class="sbc-btn sbc-btn-revise" :disabled="submitting || !reviseText.trim()" @click="askRevise">
          让 AI 改
        </button>
      </div>

      <div class="sbc-actions">
        <button class="sbc-btn sbc-btn-primary" :disabled="submitting || !shots.length" @click="confirmShots">
          <CheckCircle2 :size="14" /> {{ submitting ? '生成中…' : '确认并生成视频' }}
        </button>
        <button class="sbc-btn sbc-btn-cancel" :disabled="submitting" @click="cancelAll">
          取消
        </button>
      </div>
      <p class="sbc-scope-hint">未点「确认并生成视频」之前，不会生成任何画面</p>
    </div>
  </Transition>
</template>

<script setup lang="ts">
import { ref, reactive, computed, watch } from 'vue'
import { authFetch } from '@/api/client'
import { Clapperboard, CheckCircle2 } from 'lucide-vue-next'
import { parseHttpErrorMessage } from './cell-types'

export interface StoryboardShot {
  shot_index?: number
  caption?: string
  image_prompt?: string
  motion_prompt?: string
  duration?: number
  transition?: string
  reference_images?: number[]
}

const props = defineProps<{
  sessionId: string
  loopStatePath: string
  shots: StoryboardShot[]
  title?: string
  turnId?: string
  /** SSE 回报的生成失败原因。失败时卡片不消失，让用户改完直接重试 */
  failedError?: string
}>()

const emit = defineEmits<{
  confirmed: [data: any]
  cancelled: []
  revise: [comment: string]
}>()

// 本地可编辑副本：直接改 props.shots 会污染 SSE 落进来的原始快照
const shots = reactive<StoryboardShot[]>(props.shots.map(s => ({ ...s })))
const submitting = ref(false)
const isProcessed = ref(false)
const errorMsg = ref('')
const reviseText = ref('')

// 后端回放失败经 SSE 送回来时，把原因显示在卡片上（卡片保持可重试）
watch(
  () => props.failedError,
  (v) => { if (v) errorMsg.value = v },
  { immediate: true },
)

const totalDuration = computed(() =>
  shots.reduce((sum, s) => sum + (Number(s.duration) || 0), 0).toFixed(1)
)

function patchShot(i: number, field: keyof StoryboardShot, value: any) {
  ;(shots[i] as any)[field] = value
}

function moveShot(i: number, delta: number) {
  const j = i + delta
  if (j < 0 || j >= shots.length) return
  const tmp = shots[i]
  shots[i] = shots[j]
  shots[j] = tmp
}

function removeShot(i: number) {
  if (shots.length <= 1) return
  shots.splice(i, 1)
}

// 404 用分镜语义的专用文案（与 ChatClarificationCard 一致的处理方式）
function parseErrorMessage(status: number, body: string): string {
  return parseHttpErrorMessage(status, body, {
    notFoundMessage: '分镜待确认状态已过期，请重新发起做视频',
  })
}

async function postConfirm(body: Record<string, any>) {
  const res = await authFetch('/api/v1/chat/confirm-storyboard', {
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
    console.error('[ChatStoryboardConfirmCard] confirm-storyboard failed:', res.status, errBody)
    throw new Error(parseErrorMessage(res.status, errBody))
  }
  return res.json().catch(() => null)
}

async function confirmShots() {
  if (submitting.value) return
  if (!props.sessionId || !props.loopStatePath) {
    errorMsg.value = '会话信息缺失，请刷新页面后重试'
    return
  }
  if (!shots.length) {
    errorMsg.value = '一个镜头都不剩了，至少保留一镜'
    return
  }

  submitting.value = true
  errorMsg.value = ''
  try {
    const data = await postConfirm({ shots: shots.map(s => ({ ...s })), action: 'confirm' })
    // 失败也在 SSE storyboard_confirmation_resolved 里露过一面；这里就地显示结果
    if (data && data.ok === false) {
      errorMsg.value = data.output?.error || '生成失败，请重试或让 AI 重编'
      return
    }
    isProcessed.value = true
    emit('confirmed', data)
  } catch (e: any) {
    errorMsg.value = e?.message?.includes('Failed to fetch')
      ? '网络连接失败，请检查网络后重试'
      : `操作出错：${e?.message || '未知错误'}`
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
    errorMsg.value = `取消出错：${e?.message || '未知错误'}`
  } finally {
    submitting.value = false
  }
}

function askRevise() {
  const comment = reviseText.value.trim()
  if (!comment || submitting.value) return
  // 让 AI 重编：把当前分镜 + 用户意见带回对话，Agent 会重新出稿
  isProcessed.value = true
  emit('revise', comment)
}
</script>

<style scoped>
.sbc-card {
  background: var(--rl-card);
  border: 1px solid #e5e7eb;
  border-radius:14px;
  padding: 12px 14px;
  margin-top: 6px;
  display: flex;
  flex-direction: column;
}

.sbc-header {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 6px;
}

.sbc-icon { color: var(--rl-accent); }

.sbc-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--rl-text-2);
}

.sbc-badge {
  font-size: 11px;
  color: #444441;
  background: rgba(83, 74, 183, 0.06);
  padding: 1px 6px;
  border-radius:8px;
  margin-left: auto;
}

.sbc-desc {
  font-size: 12px;
  color: #6b7280;
  line-height: 1.6;
  margin: 0 0 10px;
}
.sbc-desc b { color: var(--rl-text-2); }

.sbc-shots {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 340px;
  overflow-y: auto;
}

.sbc-shot {
  border: 1px solid #e5e7eb;
  border-radius:12px;
  padding: 8px 10px;
  background: #f9fafb;
}

.sbc-shot-head {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 6px;
}

.sbc-shot-idx {
  flex-shrink: 0;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: #444441;
  color: #fff;
  font-size: 11px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.sbc-shot-caption {
  flex: 1;
  min-width: 0;
  border: 1px solid transparent;
  border-radius:8px;
  padding: 3px 6px;
  font-size: 13px;
  font-weight: 500;
  color: var(--rl-text-2);
  background: transparent;
  outline: none;
}
.sbc-shot-caption:hover { border-color: #e5e7eb; background: #fff; }
.sbc-shot-caption:focus { border-color: #444441; background: #fff; }

.sbc-shot-ops { display: flex; gap: 2px; flex-shrink: 0; }

.sbc-icon-btn {
  width: 20px;
  height: 20px;
  border: none;
  border-radius:8px;
  background: transparent;
  color: #94a3b8;
  font-size: 12px;
  line-height: 1;
  cursor: pointer;
}
.sbc-icon-btn:hover:not(:disabled) { background: #e2e8f0; color: #475569; }
.sbc-icon-btn:disabled { opacity: 0.3; cursor: not-allowed; }
.sbc-icon-danger:hover:not(:disabled) { background: #fee2e2; color: #dc2626; }

.sbc-shot-row { display: flex; gap: 6px; }

.sbc-field { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.sbc-field-sm { flex: 0.8; }
.sbc-field-xs { flex: 0 0 52px; }

.sbc-field-label {
  font-size: 10px;
  color: #94a3b8;
}

.sbc-input {
  width: 100%;
  border: 1px solid #e5e7eb;
  border-radius:8px;
  padding: 3px 6px;
  font-size: 12px;
  color: var(--rl-text-2);
  background: #fff;
  outline: none;
}
.sbc-input:focus { border-color: #444441; }

.sbc-revise {
  display: flex;
  gap: 6px;
  margin-top: 10px;
}

.sbc-revise-input {
  flex: 1;
  border: 1px solid #e5e7eb;
  border-radius:10px;
  padding: 6px 8px;
  font-size: 12px;
  outline: none;
  background: #fff;
  color: var(--rl-text-2);
}
.sbc-revise-input:focus { border-color: #444441; }

.sbc-actions {
  display: flex;
  gap: 8px;
  margin-top: 8px;
}

.sbc-btn {
  padding: 6px 12px;
  border-radius:10px;
  border: none;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  transition: all 0.15s;
}
.sbc-btn:disabled { opacity: 0.5; cursor: not-allowed; }

.sbc-btn-primary { flex: 1; background: #444441; color: #fff; }
.sbc-btn-primary:hover:not(:disabled) { background: #2c2c2a; }

.sbc-btn-cancel { flex: 0 0 72px; background: #f1f5f9; color: #64748b; border: 1px solid #e2e8f0; }
.sbc-btn-cancel:hover:not(:disabled) { background: #e2e8f0; }

.sbc-btn-revise {
  flex: 0 0 76px;
  background: #f1f5f9;
  color: #64748b;
  border: 1px solid #e2e8f0;
}
.sbc-btn-revise:hover:not(:disabled) { background: #e2e8f0; color: #334155; }

.sbc-scope-hint {
  text-align: center;
  font-size: 11px;
  color: #94a3b8;
  margin: 6px 0 0;
}

.sbc-error { border-color: #fca5a5; }

.sbc-error-msg {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 6px 8px;
  margin-top: 8px;
  background: #fef2f2;
  border: 1px solid #fecaca;
  border-radius:10px;
}

.sbc-error-text { font-size: 12px; color: #dc2626; line-height: 1.5; flex: 1; }

.sbc-error-retry {
  flex-shrink: 0;
  padding: 2px 8px;
  font-size: 11px;
  font-weight: 500;
  color: #dc2626;
  background: #fff;
  border: 1px solid #fecaca;
  border-radius:8px;
  cursor: pointer;
}

.sbc-fade-leave-active { transition: opacity 0.3s ease, transform 0.3s ease; }
.sbc-fade-leave-to { opacity: 0; transform: translateY(-4px); }
</style>
