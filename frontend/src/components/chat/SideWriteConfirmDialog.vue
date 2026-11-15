<template>
  <!-- 侧会话写副作用确认弹窗（PRD G6/Q9）：逐次生效，无会话级放行 -->
  <div v-if="visible" class="sw-dialog" data-testid="side-write-dialog" role="alertdialog">
    <div class="sw-head">
      <ShieldAlert :size="15" class="sw-head-icon" />
      <span class="sw-title">侧会话请求执行写操作</span>
    </div>
    <p class="sw-desc">{{ description }}</p>
    <p class="sw-note">侧会话默认只读。批准后仅放行本次调用，无会话级放行。</p>
    <div class="sw-actions">
      <button class="sw-btn sw-btn--reject" :disabled="busy" @click="resolve('reject')">拒绝</button>
      <button class="sw-btn sw-btn--approve" :disabled="busy" @click="resolve('approve')">
        {{ busy ? '处理中…' : '批准本次' }}
      </button>
    </div>
    <p v-if="errorText" class="sw-error">{{ errorText }}</p>
  </div>
</template>

<script setup lang="ts">
/**
 * 侧会话写副作用确认弹窗（Task 8 / PRD G6）：
 * - 批准：POST side-write-confirm approve → 后端逐次放行并恢复执行；
 * - 拒绝：POST reject → 后端以只读方式继续回答，不中断会话（FR-D5）。
 */
import { ref, computed } from 'vue'
import { ShieldAlert } from 'lucide-vue-next'
import { confirmSideWrite } from '@/api/sideSessions'
import { deriveSideWriteVisible } from './cell-types'
import type { AgentMeta } from './cell-types'

const props = defineProps<{
  agentMeta: AgentMeta | null | undefined
}>()

const emit = defineEmits<{
  (e: 'resolved', action: 'approve' | 'reject'): void
}>()

const busy = ref(false)
const errorText = ref('')

const visible = computed(() => deriveSideWriteVisible(props.agentMeta))
const description = computed(() => props.agentMeta?.sideWrite?.description || '')

async function resolve(action: 'approve' | 'reject') {
  const meta = props.agentMeta
  const sideSessionId = meta?.sideWrite?.sideSessionId
  const loopStatePath = meta?.loopStatePath
  if (!sideSessionId || !loopStatePath) return
  busy.value = true
  errorText.value = ''
  try {
    await confirmSideWrite(sideSessionId, {
      action,
      loop_state_path: loopStatePath,
      turn_id: meta?.turnId ?? null,
    })
    emit('resolved', action)
  } catch (e: any) {
    errorText.value = e?.message || '确认请求失败，请重试'
  } finally {
    busy.value = false
  }
}
</script>

<style scoped>
.sw-dialog {
  margin: 8px 0;
  padding: 10px 12px;
  border: 1px solid #f0c987;
  border-radius: 10px;
  background: #fff9ef;
}
.sw-head { display: flex; align-items: center; gap: 6px; color: #b0731f; font-size: 13px; font-weight: 600; }
.sw-desc { margin: 8px 0 4px; font-size: 13px; line-height: 1.5; color: #5a4626; word-break: break-all; }
.sw-note { margin: 0 0 10px; font-size: 12px; color: #9b8a68; }
.sw-actions { display: flex; gap: 8px; justify-content: flex-end; }
.sw-btn { padding: 5px 14px; border-radius: 8px; font-size: 12px; cursor: pointer; border: 1px solid transparent; }
.sw-btn:disabled { opacity: 0.6; cursor: not-allowed; }
.sw-btn--reject { background: #fff; border-color: #d8cbb2; color: #7a6a4d; }
.sw-btn--reject:hover { background: #f5efe2; }
.sw-btn--approve { background: #b0731f; color: #fff; }
.sw-btn--approve:hover { background: #9a6418; }
.sw-error { margin: 8px 0 0; font-size: 12px; color: #c0392b; }
</style>
