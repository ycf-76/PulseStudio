<template>
  <Teleport to="body">
    <Transition name="pqm-fade">
      <div v-if="visible" class="pqm-overlay" @click.self="handleClose">
        <div class="pqm-modal" role="dialog" aria-modal="true">
          <div class="pqm-header">
            <div class="pqm-header-text">
              <span class="pqm-title">扫码登录{{ platformLabel }}</span>
              <span class="pqm-subtitle">登录态仅保存在本地，用于回收作品数据</span>
            </div>
            <button type="button" class="pqm-close" aria-label="关闭" @click="handleClose">
              <X :size="15" />
            </button>
          </div>

          <div class="pqm-body">
            <div v-if="phase === 'loading'" class="pqm-state">
              <div class="pqm-spinner"></div>
              <span>正在唤起{{ platformLabel }}登录...</span>
            </div>

            <div v-else-if="phase === 'error'" class="pqm-state">
              <AlertCircle :size="20" class="pqm-error-icon" />
              <span class="pqm-error-text">{{ errorMsg }}</span>
              <button type="button" class="pqm-btn pqm-btn-primary" @click="startBind">重新获取二维码</button>
            </div>

            <div v-else-if="phase === 'success'" class="pqm-state">
              <div class="pqm-success-icon">
                <Check :size="20" />
              </div>
              <span class="pqm-success-text">{{ platformLabel }} 绑定成功</span>
              <span class="pqm-success-sub">正在刷新账号信息...</span>
            </div>

            <div v-else class="pqm-qr-wrap">
              <div class="pqm-qr-frame">
                <img v-if="qrSrc" :src="qrSrc" alt="登录二维码" class="pqm-qr-img" />
                <div v-else class="pqm-qr-placeholder">二维码加载失败</div>
              </div>

              <p class="pqm-hint">请使用{{ platformLabel }} App 扫描上方二维码</p>

              <div class="pqm-footer-row">
                <span v-if="countdown > 0" class="pqm-countdown">二维码 {{ countdown }} 秒后失效</span>
                <span v-else class="pqm-expired">二维码已失效</span>
                <button type="button" class="pqm-refresh" @click="startBind">刷新二维码</button>
              </div>

              <div class="pqm-polling">
                <span class="pqm-dot"></span>
                <span>等待扫码中，扫码后请在手机上确认</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup lang="ts">
import { ref, watch, onUnmounted } from 'vue'
import { AlertCircle, Check, X } from 'lucide-vue-next'
import { bindPlatformAccount, fetchQrStatus } from '@/api/platformAccounts'

const props = defineProps<{
  visible: boolean
  platform: string
  platformLabel: string
}>()

const emit = defineEmits<{
  (e: 'update:visible', value: boolean): void
  (e: 'bound'): void
}>()

const phase = ref<'loading' | 'qr' | 'error' | 'success'>('loading')
const qrSrc = ref('')
const errorMsg = ref('')
const countdown = ref(120)

let pollTimer: ReturnType<typeof setInterval> | null = null
let countdownTimer: ReturnType<typeof setInterval> | null = null
let successTimer: ReturnType<typeof setTimeout> | null = null
let requestSeq = 0

function clearTimers() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
  if (countdownTimer) { clearInterval(countdownTimer); countdownTimer = null }
  if (successTimer) { clearTimeout(successTimer); successTimer = null }
}

async function startBind() {
  clearTimers()
  const seq = ++requestSeq
  phase.value = 'loading'
  errorMsg.value = ''
  qrSrc.value = ''
  countdown.value = 120

  try {
    const result = await bindPlatformAccount(props.platform)
    if (seq !== requestSeq) return

    const qrCode = result?.qr_code || {}
    if (qrCode.qr_base64 || qrCode.qr_url) {
      qrSrc.value = qrCode.qr_base64 || qrCode.qr_url || ''
    }

    if (!result?.task_id) {
      phase.value = 'error'
      errorMsg.value = qrCode.error || '未能获取登录二维码'
      return
    }
    if (!qrSrc.value) {
      phase.value = 'error'
      errorMsg.value = qrCode.error || '未能获取登录二维码'
      return
    }

    phase.value = 'qr'
    startPolling(result.task_id, seq)
    startCountdown()
  } catch (e: any) {
    if (seq !== requestSeq) return
    phase.value = 'error'
    errorMsg.value = e?.message || '获取二维码失败'
  }
}

function startPolling(taskId: string, seq: number) {
  if (pollTimer) clearInterval(pollTimer)
  pollTimer = setInterval(async () => {
    try {
      const info = await fetchQrStatus(taskId)
      if (seq !== requestSeq) return

      if (info.status === 'confirmed') {
        clearTimers()
        phase.value = 'success'
        emit('bound')
        successTimer = setTimeout(() => emit('update:visible', false), 1200)
      } else if (info.status === 'failed' || info.status === 'expired') {
        clearTimers()
        phase.value = 'error'
        errorMsg.value = info.status === 'expired' ? '二维码已失效，请重新获取' : (info.error || '登录失败，请重试')
      }
    } catch (e: any) {
      if (seq !== requestSeq) return
      clearTimers()
      phase.value = 'error'
      errorMsg.value = e?.message || '网络异常，请重试'
    }
  }, 2500)
}

function startCountdown() {
  if (countdownTimer) clearInterval(countdownTimer)
  countdown.value = 120
  countdownTimer = setInterval(() => {
    countdown.value -= 1
    if (countdown.value <= 0) {
      clearTimers()
      errorMsg.value = '二维码已失效，请重新获取'
      phase.value = 'error'
    }
  }, 1000)
}

function handleClose() {
  requestSeq += 1
  clearTimers()
  emit('update:visible', false)
}

function handleKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') handleClose()
}

watch(
  () => props.visible,
  (open) => {
    if (open) {
      window.addEventListener('keydown', handleKeydown)
      startBind()
    } else {
      window.removeEventListener('keydown', handleKeydown)
      requestSeq += 1
      clearTimers()
    }
  },
)

onUnmounted(() => {
  window.removeEventListener('keydown', handleKeydown)
  clearTimers()
})
</script>

<style scoped>
.pqm-overlay {
  position: fixed;
  inset: 0;
  z-index: 10010;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(17, 24, 39, 0.42);
  backdrop-filter: blur(3px);
}
.pqm-modal {
  width: 340px;
  max-width: 90vw;
  border-radius: 18px;
  background: #FFFFFF;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.18);
  overflow: hidden;
}
.pqm-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
  padding: 16px 18px 12px;
}
.pqm-header-text {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.pqm-title {
  font-size: 15px;
  font-weight: 500;
  color: #1A1A1A;
  line-height: 1.3;
}
.pqm-subtitle {
  font-size: 12px;
  color: #9A9A9A;
  line-height: 1.4;
}
.pqm-close {
  flex: none;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  border: none;
  border-radius: 50%;
  background: #F5F5F5;
  color: #6B7280;
  cursor: pointer;
  transition: background 0.15s ease;
}
.pqm-close:hover { background: #EBEBEB; }
.pqm-body {
  padding: 6px 18px 20px;
}
.pqm-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  padding: 26px 0 18px;
  text-align: center;
  font-size: 13px;
  color: #9A9A9A;
}
.pqm-spinner {
  width: 20px;
  height: 20px;
  border: 2px solid #EDEDED;
  border-top-color: #3B6CF6;
  border-radius: 50%;
  animation: pqm-spin 0.75s linear infinite;
}
@keyframes pqm-spin { to { transform: rotate(360deg); } }
.pqm-error-icon { color: #B42318; }
.pqm-error-text {
  color: #B42318;
  line-height: 1.5;
  word-break: break-all;
}
.pqm-success-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 44px;
  height: 44px;
  border-radius: 50%;
  background: #ECFDF5;
  color: #059669;
}
.pqm-success-text {
  font-size: 14px;
  font-weight: 500;
  color: #1A1A1A;
}
.pqm-success-sub { font-size: 12px; color: #9A9A9A; }
.pqm-qr-wrap {
  display: flex;
  flex-direction: column;
  align-items: center;
}
.pqm-qr-frame {
  position: relative;
  width: 196px;
  height: 196px;
  border: 1px solid #EFEFEF;
  border-radius: 14px;
  background: #FAFAFA;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
}
.pqm-qr-img {
  width: 176px;
  height: 176px;
  object-fit: contain;
}
.pqm-qr-placeholder { font-size: 12px; color: #9A9A9A; }
.pqm-hint {
  margin: 13px 0 0;
  font-size: 13px;
  font-weight: 500;
  color: #1A1A1A;
  text-align: center;
  line-height: 1.5;
}
.pqm-footer-row {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 7px;
}
.pqm-countdown { font-size: 12px; color: #9A9A9A; }
.pqm-expired { font-size: 12px; color: #B42318; }
.pqm-refresh {
  padding: 2px 9px;
  border: 1px solid #EFEFEF;
  border-radius: 7px;
  background: #FFFFFF;
  color: #6B7280;
  font-size: 12px;
  cursor: pointer;
  transition: background 0.15s ease;
}
.pqm-refresh:hover { background: #F7F7F7; }
.pqm-polling {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 14px;
  font-size: 12px;
  color: #9A9A9A;
}
.pqm-dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: #3B6CF6;
  animation: pqm-pulse 1.2s ease-in-out infinite;
}
@keyframes pqm-pulse {
  0%, 100% { opacity: 0.3; }
  50% { opacity: 1; }
}
.pqm-btn {
  padding: 7px 14px;
  border: none;
  border-radius: 9px;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: background 0.15s ease;
}
.pqm-btn-primary { background: #3B6CF6; color: #FFFFFF; }
.pqm-btn-primary:hover { background: #2F5BE0; }
.pqm-fade-enter-active,
.pqm-fade-leave-active { transition: opacity 0.18s ease; }
.pqm-fade-enter-from,
.pqm-fade-leave-to { opacity: 0; }
</style>