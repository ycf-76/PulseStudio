<template>
  <div v-if="visible" class="gc-card" :class="{ 'gc-error': errorMsg }">
    <div class="gc-head">
      <ShieldAlert :size="15" class="gc-icon" />
      <span class="gc-title">需要你的确认</span>
      <span v-if="riskLabel" class="gc-risk-tag" :class="{ high: riskLabel === '高风险' }">{{ riskLabel }}</span>
    </div>

    <p class="gc-verb">{{ humanSummary }}</p>

    <div class="gc-actions">
      <button
        class="gc-btn gc-btn-approve"
        :disabled="submitting"
        @click="approve"
      >
        {{ submitting ? '确认中…' : '批准执行' }}
      </button>
      <button
        class="gc-btn gc-btn-reject"
        :disabled="submitting || expired"
        :title="expired ? '确认状态已过期' : ''"
        @click="reject"
      >
        {{ expired ? '已过期' : '取消' }}
      </button>
    </div>

    <p v-if="errorMsg" class="gc-error-msg">{{ errorMsg }}</p>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import { ShieldAlert } from 'lucide-vue-next'
import { authFetch } from '@/api/client'
import { parseHttpErrorMessage } from './cell-types'

const props = defineProps<{
  sessionId: string
  loopStatePath: string
  toolName?: string
  confirmationPrompt?: string
  riskInfo?: string
  turnId?: string
  failedError?: string
}>()

const emit = defineEmits<{
  confirmed: [data?: any]
  rejected: []
}>()

const submitting = ref(false)
const errorMsg = ref(props.failedError || '')
const expired = ref(props.failedError?.includes('过期') || false)
const visible = ref(true)

/** 确认/取消后必须收场：成功关卡片；失败（含过期）显示原因后 3 秒自动关，绝不卡死用户 */
function closeWith(msg?: string) {
  if (msg) errorMsg.value = msg
  if (msg) {
    setTimeout(() => {
      visible.value = false
      emit('rejected')
    }, 3000)
  } else {
    visible.value = false
  }
}

/** 协议原文 → 人话：从 args JSON 提取关键字段，失败则通用表述 */
const humanSummary = computed(() => {
  const tool = props.toolName || '操作'
  const prompt = props.confirmationPrompt || ''
  const m = prompt.match(/arguments:\s*(\{[\s\S]*?\})\s*[\n}]/)
  let detail = ''
  if (m) {
    try {
      const args = JSON.parse(m[1].replace(/'/g, '"'))
      const parts: string[] = []
      for (const k of ['path', 'skill', 'query', 'topic', 'url', 'file', 'name', 'target']) {
        const v = args[k]
        if (v) {
          const s = String(v).slice(0, 80)
          parts.push(`${k}: ${s}`)
        }
      }
      detail = parts.join('；')
    } catch {
      detail = ''
    }
  }
  const verb = /write|save/.test(tool) ? '写入' : /search/.test(tool) ? '搜索' : /read|cat/.test(tool) ? '读取' : '执行'
  const toolPart = tool && tool !== '操作' ? ` ${tool}` : ''
  return detail ? `即将${verb}：${detail}` : `即将执行${toolPart} 操作`
})

const riskLabel = computed(() => {
  const m = (props.riskInfo || '').match(/risk:\s*(\w+)/)
  if (!m) return ''
  return { high: '高风险', medium: '中风险', low: '低风险' }[m[1]] || m[1]
})

function parseErrorMessage(status: number, body: string): string {
  return parseHttpErrorMessage(status, body, {
    notFoundMessage: '确认状态已过期，请重新发起操作',
  })
}

async function postGuardianConfirm(action: 'approve' | 'reject') {
  const res = await authFetch('/api/v1/chat/confirm-guardian', {
    method: 'POST',
    body: JSON.stringify({
      session_id: props.sessionId,
      loop_state_path: props.loopStatePath,
      action,
      turn_id: props.turnId || null,
    }),
  })
  if (!res.ok) {
    const errBody = await res.text()
    console.error('[ChatGuardianConfirmCard] confirm-guardian failed:', res.status, errBody)
    const err = new Error(parseErrorMessage(res.status, errBody))
    ;(err as any).expired = res.status === 404 || errBody.includes('过期') || errBody.includes('expired')
    throw err
  }
  return res.json().catch(() => null)
}

async function approve() {
  if (submitting.value) return
  submitting.value = true
  errorMsg.value = ''
  try {
    await postGuardianConfirm('approve')
    emit('confirmed')
    closeWith()
  } catch (e: any) {
    if (e.expired) {
      expired.value = true
      emit('rejected')
      closeWith('确认状态已过期，如需继续请重新发起')
    } else {
      errorMsg.value = e.message || '确认失败'
    }
  } finally {
    submitting.value = false
  }
}

async function reject() {
  if (submitting.value) return
  submitting.value = true
  errorMsg.value = ''
  try {
    await postGuardianConfirm('reject')
  } catch (e: any) {
    // 取消失败（含过期）也收场——agent 侧会因确认超时自行解除阻塞
    console.warn('[ChatGuardianConfirmCard] reject failed:', e?.message)
  }
  emit('rejected')
  closeWith()
  submitting.value = false
}
</script>

<style scoped>
/* dock 浮层卡本体：位置/阴影/限宽由 .rl-confirm-dock（全局）提供，
   这里补基础样式。2026-10-23：此前 .gc-card 根本没定义过样式——
   模板早从中心模态（gc-overlay/gc-modal）改成了输入框上方停靠，
   旧模态样式没清、新卡片样式没加，导致卡片透明（透出画布白底）、
   四个直角、无内边距，和 ccc/sbc 卡的设计语言完全脱节。 */
.gc-card {
  background: var(--rl-card, #FFFFFF);
  border: 1px solid #e5e7eb;
  border-radius:16px;
  padding: 14px 16px;
  box-sizing: border-box;
}
@media (max-width: 640px) {
  /* 窄屏两按钮平分宽度，避免"批准执行/取消"挤成一团 */
  .gc-actions .gc-btn { flex: 1; }
}
.gc-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}
.gc-icon { color: #888780; flex: none; }
.gc-title {
  font-weight: 600;
  font-size: 15px;
  color: #1a1a1a;
  flex: 1;
}
.gc-verb {
  font-size: 14px;
  line-height: 21px;
  color: #373734;
  margin: 0 0 16px;
  word-break: break-word;
}
.gc-risk-tag {
  flex: none;
  font-size: 11px;
  padding: 2px 8px;
  border-radius:10px;
  background: rgba(31, 31, 30, 0.05);
  color: #888780;
}
.gc-risk-tag.high {
  background: rgba(226, 75, 74, 0.08);
  color: #e24b4a;
}
.gc-actions {
  display: flex;
  gap: 8px;
  margin-top: 12px;
}
.gc-btn {
  padding: 6px 16px;
  border-radius:10px;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  border: none;
}
.gc-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.gc-btn-approve { background: #2C2C2A; color: #fff; }
.gc-btn-approve:hover:not(:disabled) { background: #453D9E; }
.gc-btn-reject { background: #f3f4f6; color: #6b7280; }
.gc-btn-reject:hover:not(:disabled) { background: #e5e7eb; }
.gc-error-msg {
  color: #dc2626;
  font-size: 12px;
  margin-top: 8px;
}
</style>