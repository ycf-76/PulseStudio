<template>
  <div class="pa-wrap">
    <div v-if="loading" class="pa-loading">
      <div class="pa-spinner"></div>
      <span>加载账号信息...</span>
    </div>

    <template v-else>
      <div v-if="errorMsg" class="pa-error">
        <AlertCircle :size="16" />
        <span class="pa-error-text">{{ errorMsg }}</span>
        <button type="button" class="pa-error-btn" @click="loadAccounts('refresh')">重试</button>
      </div>

      <section class="pa-hero">
        <img
          class="pa-hero-img"
          src="/images/setting_images/4.jpg"
          alt="平台账号"
          @error="($event.target as HTMLElement).style.display='none'"
        />
        <div class="pa-hero-body">
          <h3 class="pa-hero-title">绑定账号，让智能体读到真实数据</h3>
          <p class="pa-hero-desc">绑定后可回收你的作品与互动数据，用于选题和内容分析</p>
          <div class="pa-hero-chips">
            <span class="pa-chip" :class="{ 'pa-chip-on': boundCount > 0 }">
              <span class="pa-chip-dot"></span>已绑定 {{ boundCount }} / {{ accounts.length }}
            </span>
            <span v-if="totalWorks > 0" class="pa-chip">已回收 {{ totalWorks }} 篇作品</span>
          </div>
        </div>
      </section>

      <section class="pa-card">
        <header class="pa-card-head">
          <span class="pa-card-icon"><Link2 :size="15" :stroke-width="1.8" /></span>
          <div class="pa-card-head-text">
            <h3 class="pa-card-title">已绑定账号</h3>
            <p class="pa-card-desc">点击卡片可重新绑定、同步或解绑</p>
          </div>
          <button
            type="button"
            class="pa-refresh"
            :disabled="refreshing"
            @click="loadAccounts('refresh')"
          >
            <RefreshCw :size="13" :class="{ 'pa-rotating': refreshing }" />
            <span>刷新</span>
          </button>
        </header>

        <div class="pa-card-body">
          <div class="pa-list">
            <PlatformAccountCard
              v-for="account in accounts"
              :key="account.platform"
              :account="account"
              @bind="openBind(account)"
              @sync="confirmSync(account)"
              @unbind="confirmUnbind(account)"
              @retry="confirmSync(account, true)"
            />
          </div>

          <Transition name="pa-fade">
            <div v-if="toastMsg" class="pa-toast" :class="`pa-toast-${toastType}`">
              <CheckCircle2 v-if="toastType === 'success'" :size="14" />
              <AlertCircle v-else :size="14" />
              <span>{{ toastMsg }}</span>
            </div>
          </Transition>

          <div v-if="boundCount > 0" class="pa-footer">
            <button
              type="button"
              class="pa-sync-all"
              :disabled="syncingAll"
              @click="confirmSyncAll"
            >
              <RefreshCw :size="14" :class="{ 'pa-rotating': syncingAll }" />
              <span>{{ syncingAll ? '正在同步所有平台...' : '一键同步所有平台' }}</span>
            </button>
            <p class="pa-tip">
              <ShieldAlert :size="12" />
              <span>频繁同步可能触发平台风控，建议每周 1–2 次；同步结果会自动进入「已采集」列表</span>
            </p>
          </div>
          <p v-else class="pa-tip">
            <ShieldAlert :size="12" />
            <span>绑定至少一个平台后，即可一键回收该平台的全部已发布作品</span>
          </p>
        </div>
      </section>
    </template>

    <PlatformQrModal
      v-model:visible="qrVisible"
      :platform="qrPlatform"
      :platform-label="qrPlatformLabel"
      @bound="handleBound"
    />

    <ConfirmDialog
      :visible="confirmState.visible"
      :title="confirmState.title"
      :message="confirmState.message"
      :confirm-text="confirmState.confirmText"
      :cancel-text="confirmState.cancelText"
      :danger="confirmState.danger"
      @confirm="onConfirm"
      @cancel="onCancel"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { AlertCircle, CheckCircle2, Link2, RefreshCw, ShieldAlert } from 'lucide-vue-next'
import { useWorkStore } from '@/stores/work'
import { useConfirm } from '@/composables/useConfirm'
import ConfirmDialog from '@/components/common/ConfirmDialog.vue'
import PlatformAccountCard from '@/components/settings/PlatformAccountCard.vue'
import PlatformQrModal from '@/components/settings/PlatformQrModal.vue'
import {
  fetchAccounts,
  syncPlatformAccount,
  syncAllPlatformAccounts,
  unbindPlatformAccount,
  type PlatformAccount,
} from '@/api/platformAccounts'

const DEFAULT_ACCOUNTS: PlatformAccount[] = [
  { id: null, platform: 'xiaohongshu', platform_label: '小红书', platform_uid: null, platform_nickname: null, platform_avatar_url: null, platform_home_url: null, last_synced_at: null, sync_status: 'idle', sync_error: null, works_count: 0, fans_count: null, bound: false },
  { id: null, platform: 'douyin', platform_label: '抖音', platform_uid: null, platform_nickname: null, platform_avatar_url: null, platform_home_url: null, last_synced_at: null, sync_status: 'idle', sync_error: null, works_count: 0, fans_count: null, bound: false },
  { id: null, platform: 'bilibili', platform_label: 'B站', platform_uid: null, platform_nickname: null, platform_avatar_url: null, platform_home_url: null, last_synced_at: null, sync_status: 'idle', sync_error: null, works_count: 0, fans_count: null, bound: false },
]

const workStore = useWorkStore()
const { state: confirmState, confirm, onConfirm, onCancel } = useConfirm()

const accounts = ref<PlatformAccount[]>([...DEFAULT_ACCOUNTS])
const loading = ref(true)
const refreshing = ref(false)
const errorMsg = ref('')
const syncStarting = ref(false)

const qrVisible = ref(false)
const qrPlatform = ref('')
const qrPlatformLabel = ref('')

const toastMsg = ref('')
const toastType = ref<'success' | 'error' | 'info'>('success')
let toastTimer: ReturnType<typeof setTimeout> | null = null
let syncTimer: ReturnType<typeof setInterval> | null = null

const boundCount = computed(() => accounts.value.filter(a => a.bound).length)
const totalWorks = computed(() =>
  accounts.value.filter(a => a.bound).reduce((sum, a) => sum + (a.works_count || 0), 0),
)
const syncingAll = computed(
  () => syncStarting.value || accounts.value.some(a => a.sync_status === 'running'),
)

function showToast(msg: string, type: 'success' | 'error' | 'info' = 'success') {
  toastMsg.value = msg
  toastType.value = type
  if (toastTimer) clearTimeout(toastTimer)
  toastTimer = setTimeout(() => { toastMsg.value = '' }, 3200)
}

async function loadAccounts(mode: 'initial' | 'refresh' | 'quiet' = 'initial'): Promise<boolean> {
  if (mode === 'initial') loading.value = true
  if (mode === 'refresh') refreshing.value = true
  if (mode !== 'quiet') errorMsg.value = ''
  try {
    accounts.value = await fetchAccounts()
    return true
  } catch (e: any) {
    if (mode !== 'quiet') {
      errorMsg.value = e?.message || '加载账号信息失败'
      if (mode === 'initial') accounts.value = [...DEFAULT_ACCOUNTS]
    }
    return false
  } finally {
    if (mode === 'initial') loading.value = false
    if (mode === 'refresh') refreshing.value = false
  }
}

function openBind(account: PlatformAccount) {
  qrPlatform.value = account.platform
  qrPlatformLabel.value = account.platform_label
  qrVisible.value = true
}

function handleBound() {
  showToast(`${qrPlatformLabel.value} 绑定成功，登录态已保存`, 'success')
  loadAccounts('refresh')
}

async function confirmUnbind(account: PlatformAccount) {
  if (!account.id) return
  const ok = await confirm({
    title: '解绑账号',
    message: `确定解绑「${account.platform_label}」？\n解绑后已同步的作品数据仍会保留在已采集列表中。`,
    confirmText: '解绑',
    danger: true,
  })
  if (!ok) return

  try {
    await unbindPlatformAccount(account.id)
    showToast(`${account.platform_label} 已解绑`, 'success')
    await loadAccounts('refresh')
  } catch (e: any) {
    showToast(e?.message || '解绑失败，请重试', 'error')
  }
}

async function confirmSync(account: PlatformAccount, skipRiskCheck = false) {
  if (!account.id || account.sync_status === 'running') return

  if (!skipRiskCheck && account.last_synced_at) {
    const hours = Math.floor((Date.now() - new Date(account.last_synced_at).getTime()) / 3600000)
    if (hours < 24) {
      const ok = await confirm({
        title: '同步频率提醒',
        message: `距上次同步仅 ${hours} 小时，频繁同步可能触发${account.platform_label}风控。\n确定继续同步？`,
        confirmText: '继续同步',
      })
      if (!ok) return
    }
  }
  syncOne(account)
}

function syncOne(account: PlatformAccount) {
  const accountId = account.id
  if (!accountId) return

  account.sync_status = 'running'
  account.sync_error = null
  startSyncPolling([accountId])

  syncPlatformAccount(accountId)
    .then((result) => {
      if (result.status === 'already_running') {
        showToast(`${account.platform_label} 正在同步中，请稍候`, 'info')
      }
    })
    .catch((e: any) => {
      stopSyncPolling()
      syncStarting.value = false
      loadAccounts('refresh')
      showToast(`${account.platform_label} 同步启动失败：${e?.message || '未知错误'}`, 'error')
    })
}

async function confirmSyncAll() {
  const targets = accounts.value.filter(a => a.bound && a.id)
  if (!targets.length) return

  const recent = targets.find(a => {
    if (!a.last_synced_at) return false
    return Date.now() - new Date(a.last_synced_at).getTime() < 86400000
  })
  if (recent) {
    const ok = await confirm({
      title: '同步频率提醒',
      message: `部分平台 24 小时内已同步过，频繁同步可能触发平台风控。\n确定同步全部 ${targets.length} 个平台？`,
      confirmText: '继续同步',
    })
    if (!ok) return
  }
  syncAll(targets)
}

function syncAll(targets: PlatformAccount[]) {
  const ids = targets.map(a => a.id).filter((id): id is string => !!id)
  syncStarting.value = true
  targets.forEach((a) => {
    a.sync_status = 'running'
    a.sync_error = null
  })
  startSyncPolling(ids)

  syncAllPlatformAccounts()
    .then((count) => {
      if (count === 0) {
        stopSyncPolling()
        syncStarting.value = false
        loadAccounts('refresh')
        showToast('没有可同步的账号', 'info')
      }
    })
    .catch((e: any) => {
      stopSyncPolling()
      syncStarting.value = false
      loadAccounts('refresh')
      showToast(e?.message || '启动全平台同步失败', 'error')
    })
}

function startSyncPolling(targetIds: string[]) {
  stopSyncPolling()
  if (!targetIds.length) return

  const startedAt = Date.now()
  let sawRunning = false
  let failedTimes = 0
  let polling = false

  syncTimer = setInterval(async () => {
    if (polling) return
    polling = true
    try {
      const ok = await loadAccounts('quiet')
      failedTimes = ok ? 0 : failedTimes + 1
      if (failedTimes >= 3) {
        stopSyncPolling()
        syncStarting.value = false
        loadAccounts('refresh')
        showToast('同步状态刷新失败，请稍后手动刷新查看结果', 'error')
        return
      }

      const running = accounts.value.filter(
        a => a.id && targetIds.includes(a.id) && a.sync_status === 'running',
      )
      if (running.length) sawRunning = true

      const elapsed = Date.now() - startedAt
      if (!running.length && (sawRunning || elapsed > 10000)) {
        stopSyncPolling()
        finishSync()
      } else if (elapsed > 1800000) {
        stopSyncPolling()
        syncStarting.value = false
        showToast('同步耗时过长，请稍后在账号卡片查看结果', 'info')
      }
    } finally {
      polling = false
    }
  }, 2500)
}

function stopSyncPolling() {
  if (syncTimer) {
    clearInterval(syncTimer)
    syncTimer = null
  }
}

async function finishSync() {
  syncStarting.value = false
  try {
    await workStore.fetchWorks()
  } catch {
    // 作品列表刷新失败不影响同步结果提示
  }

  const failed = accounts.value.find(a => a.sync_error)
  if (failed) {
    showToast(`${failed.platform_label} 同步失败：${failed.sync_error}`, 'error')
    return
  }
  const names = accounts.value
    .filter(a => a.bound && a.last_synced_at)
    .map(a => a.platform_label)
    .join('、')
  showToast(names ? `${names} 同步完成，已采集列表已更新` : '同步完成', 'success')
}

onMounted(() => loadAccounts())

onUnmounted(() => {
  stopSyncPolling()
  if (toastTimer) clearTimeout(toastTimer)
})
</script>

<style scoped>
.pa-wrap {
  display: flex;
  flex-direction: column;
  gap: 14px;
  font-size: 13px;
  color: var(--rl-text-2);
}

/* 顶部说明 */
.pa-hero {
  position: relative;
  aspect-ratio: 3.2 / 1;
  max-height: 240px;
  border: 1px solid #EFEFEF;
  border-radius: 14px;
  overflow: hidden;
  background: #BCDDD5;
  box-shadow: 0 6px 18px rgba(17, 24, 39, 0.05);
}
.pa-hero-img {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
  object-position: center 60%;
}
.pa-hero::after {
  content: '';
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  height: 46%;
  background: linear-gradient(180deg, rgba(255,255,255,0) 0%, rgba(255,255,255,0.62) 40%, rgba(255,255,255,0.9) 100%);
  pointer-events: none;
}
.pa-hero-body {
  position: absolute;
  left: 18px;
  right: 18px;
  bottom: 13px;
  z-index: 1;
  display: flex;
  flex-direction: column;
  gap: 7px;
}
.pa-hero-title {
  margin: 0;
  color: #1A1A1A;
  font-size: 15px;
  font-weight: 500;
  letter-spacing: -0.01em;
}
.pa-hero-desc {
  margin: 0;
  color: #6B6B6B;
  font-size: 13px;
  line-height: 1.5;
}
.pa-hero-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
@media (max-width: 560px) {
  .pa-hero {
    aspect-ratio: auto;
    height: 156px;
  }
}

/* 加载 / 错误 */
.pa-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 48px 0;
  color: #9A9A9A;
  font-size: 13px;
}
.pa-spinner {
  width: 18px;
  height: 18px;
  border: 2px solid #EDEDED;
  border-top-color: #3B6CF6;
  border-radius: 50%;
  animation: pa-spin 0.75s linear infinite;
}
@keyframes pa-spin { to { transform: rotate(360deg); } }
.pa-rotating { animation: pa-spin 0.9s linear infinite; }
.pa-error {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  border: 1px solid #F4DCDC;
  border-radius: 12px;
  background: #FDF4F4;
  color: #B42318;
  font-size: 13px;
}
.pa-error-text {
  flex: 1;
  min-width: 0;
}
.pa-error-btn {
  padding: 3px 10px;
  border: 1px solid #E3C0BC;
  border-radius: 7px;
  background: #FFFFFF;
  color: #B42318;
  font-size: 12px;
  cursor: pointer;
}
.pa-error-btn:hover { background: #FBEDEC; }

/* 主卡片 */
.pa-card {
  border: 1px solid #EFEFEF;
  border-radius: 14px;
  background: #FFFFFF;
  overflow: hidden;
}
.pa-card-head {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 13px 14px 11px;
}
.pa-card-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  flex: none;
  border-radius: 8px;
  background: #EEF3FE;
  color: #3B6CF6;
}
.pa-card-head-text {
  flex: 1;
  min-width: 0;
}
.pa-card-title {
  margin: 0;
  font-size: 13px;
  font-weight: 500;
  color: #1A1A1A;
  line-height: 1.3;
}
.pa-chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 2px 8px;
  border-radius: 999px;
  background: #F5F6F8;
  color: #9CA3AF;
  font-size: 11px;
  font-weight: 500;
  line-height: 1.6;
}
.pa-chip-dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: #C7CBD1;
}
.pa-chip-on {
  background: #ECFDF5;
  color: #0F7A55;
}
.pa-chip-on .pa-chip-dot {
  background: #10B981;
  box-shadow: 0 0 0 3px rgba(16, 185, 129, 0.14);
}
.pa-card-desc {
  margin: 1px 0 0;
  font-size: 12px;
  color: #9A9A9A;
  line-height: 1.4;
}
.pa-refresh {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  flex: none;
  padding: 4px 10px;
  border: 1px solid #EFEFEF;
  border-radius: 8px;
  background: #FFFFFF;
  color: #6B7280;
  font-size: 12px;
  cursor: pointer;
  transition: background 0.15s ease, color 0.15s ease;
}
.pa-refresh:hover:not(:disabled) { background: #F5F5F5; color: #1A1A1A; }
.pa-refresh:disabled { cursor: not-allowed; opacity: 0.6; }
.pa-card-body {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 0 14px 14px;
}

/* 账号列表 */
.pa-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

/* 提示条 */
.pa-toast {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 9px 12px;
  border-radius: 10px;
  font-size: 12px;
  line-height: 1.4;
}
.pa-toast-success { background: #ECFDF5; color: #047857; }
.pa-toast-error { background: #FDF4F4; color: #B42318; }
.pa-toast-info { background: #EEF3FE; color: #2F5BE0; }
.pa-fade-enter-active,
.pa-fade-leave-active { transition: opacity 0.2s ease; }
.pa-fade-enter-from,
.pa-fade-leave-to { opacity: 0; }

/* 底部 */
.pa-footer {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 7px;
  padding-top: 2px;
}
.pa-sync-all {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 9px 20px;
  border: none;
  border-radius: 11px;
  background: #3B6CF6;
  color: #FFFFFF;
  font-size: 13px;
  font-weight: 500;
  box-shadow: 0 3px 10px rgba(59, 108, 246, 0.22);
  cursor: pointer;
  transition: background 0.15s ease, box-shadow 0.15s ease;
}
.pa-sync-all:hover:not(:disabled) {
  background: #2F5BE0;
  box-shadow: 0 5px 14px rgba(59, 108, 246, 0.28);
}
.pa-sync-all:disabled { cursor: not-allowed; opacity: 0.6; box-shadow: none; }
.pa-tip {
  display: flex;
  align-items: flex-start;
  gap: 4px;
  margin: 0;
  font-size: 11px;
  line-height: 1.5;
  color: #9A9A9A;
}
</style>