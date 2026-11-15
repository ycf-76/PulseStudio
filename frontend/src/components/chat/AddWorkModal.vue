<template>
  <Teleport to="body">
    <div class="awm-overlay" v-if="visible" @click.self="$emit('close')">
      <div class="awm">
        <div class="awm-header">
          <span class="awm-title">采集内容</span>
          <button class="awm-close" @click="$emit('close')">
            <X :size="16" :stroke-width="1.8" />
          </button>
        </div>

        <div class="awm-tabs">
          <button
            class="awm-tab"
            :class="{ 'awm-tab-active': tab === 'link' }"
            @click="tab = 'link'"
          >
            <Link :size="14" :stroke-width="1.7" />
            <span>粘贴链接</span>
          </button>
          <button
            class="awm-tab"
            :class="{ 'awm-tab-active': tab === 'platform' }"
            @click="switchToPlatformTab"
          >
            <RefreshCw :size="14" :stroke-width="1.7" />
            <span>平台同步</span>
          </button>
        </div>

        <div class="awm-body" v-if="tab === 'link'">
          <div class="awm-label">粘贴作品链接（支持批量，每行一个）</div>
          <div class="awm-platforms">
            <span>支持：小红书 · 抖音 · 快手 · B站 · 视频号</span>
          </div>
          <textarea
            class="awm-input"
            v-model="links"
            placeholder="https://www.xiaohongshu.com/explore/xxx&#10;https://www.douyin.com/video/yyy&#10;https://www.bilibili.com/video/BVzzz"
            rows="5"
            ref="inputRef"
          ></textarea>

          <div class="awm-divider">或</div>

          <div class="awm-label">导入 CSV 文件</div>
          <div class="awm-csv-area" @click="triggerFileInput" @dragover.prevent @drop.prevent="onDrop">
            <Upload :size="20" :stroke-width="1.5" />
            <span>点击选择文件 或 拖拽到此处</span>
          </div>
          <input type="file" ref="fileInputRef" accept=".csv,.xlsx" style="display:none" @change="onFileSelect" />
        </div>

        <div class="awm-body" v-else>
          <div v-if="accountsLoading" class="awm-plat-state">
            <div class="awm-spinner"></div>
            <span>正在加载平台账号…</span>
          </div>

          <div v-else-if="accountsError" class="awm-plat-state awm-plat-state-err">
            <AlertCircle :size="16" />
            <span class="awm-plat-err-text">{{ accountsError }}</span>
            <button class="awm-plat-retry" @click="loadAccounts">重试</button>
          </div>

          <template v-else>
            <div class="awm-label">已绑定平台（同步会回收该平台全部已发布作品）</div>
            <div class="awm-plat-list">
              <div
                v-for="account in accounts"
                :key="account.platform"
                class="awm-plat"
                :class="{ 'awm-plat-running': account.sync_status === 'running' }"
              >
                <div class="awm-plat-avatar">
                  <img
                    v-if="account.bound && account.platform_avatar_url"
                    :src="account.platform_avatar_url"
                    :alt="account.platform_label"
                    class="awm-plat-avatar-img"
                  />
                  <PlatformLogo
                    v-else
                    :platform="account.platform"
                    :name="account.platform_label"
                    style="width:20px;height:20px;"
                  />
                </div>
                <div class="awm-plat-info">
                  <div class="awm-plat-name">
                    <span>{{ account.platform_label }}</span>
                    <span v-if="account.bound" class="awm-plat-count">{{ account.works_count }} 篇</span>
                    <span v-else class="awm-plat-idle">未绑定</span>
                  </div>
                  <div class="awm-plat-meta">
                    <template v-if="account.sync_status === 'running'">正在回收作品数据…</template>
                    <template v-else-if="account.sync_error">{{ account.sync_error }}</template>
                    <template v-else-if="account.last_synced_at">上次同步 {{ formatSyncTime(account.last_synced_at) }}</template>
                    <template v-else-if="account.bound">尚未同步过作品</template>
                    <template v-else>扫码绑定后可一键回收该平台作品</template>
                  </div>
                </div>
                <button
                  v-if="account.bound"
                  class="awm-plat-btn"
                  :disabled="account.sync_status === 'running'"
                  @click="syncOne(account)"
                >
                  <RefreshCw :size="12" :class="{ 'awm-spin': account.sync_status === 'running' }" />
                  <span>{{ account.sync_status === 'running' ? '同步中' : '同步' }}</span>
                </button>
                <button v-else class="awm-plat-btn" @click="openBind(account)">
                  <QrCode :size="12" />
                  <span>扫码绑定</span>
                </button>
              </div>
            </div>

            <button
              class="awm-btn awm-btn-primary awm-sync-all"
              :disabled="boundCount === 0 || syncingAll"
              @click="syncAll"
            >
              <RefreshCw :size="14" :class="{ 'awm-spin': syncingAll }" />
              <span>{{ syncingAll ? '正在同步所有平台…' : '一键同步所有平台' }}</span>
            </button>

            <p class="awm-plat-tip">
              <ShieldAlert :size="12" />
              <span>频繁同步可能触发平台风控，建议每周 1–2 次；同步结果会自动进入「采集」列表</span>
            </p>
          </template>
        </div>

        <div class="awm-footer" v-if="tab === 'link'">
          <button class="awm-btn awm-btn-cancel" @click="$emit('close')">取消</button>
          <button class="awm-btn awm-btn-primary" :disabled="!links.trim() || isCollecting" @click="startCollect">
            {{ isCollecting ? '采集中...' : '开始采集' }}
          </button>
        </div>

        <div class="awm-results" v-if="tab === 'link' && results.length > 0">
          <div class="awm-result" v-for="r in results" :key="r.url" :class="{ 'awm-result-ok': r.ok, 'awm-result-err': !r.ok }">
            <CheckCircle2 v-if="r.ok" :size="14" />
            <AlertCircle v-else :size="14" />
            <span class="awm-result-title">{{ r.title || r.url }}</span>
            <span class="awm-result-status">{{ r.ok ? '成功' : r.error }}</span>
          </div>
        </div>
      </div>
    </div>
  </Teleport>

  <PlatformQrModal
    v-model:visible="qrVisible"
    :platform="qrPlatform"
    :platform-label="qrPlatformLabel"
    @bound="onBound"
  />
</template>

<script setup lang="ts">
import { ref, computed, nextTick, onUnmounted, watch } from 'vue'
import { X, Upload, CheckCircle2, AlertCircle, Link, RefreshCw, QrCode, ShieldAlert } from 'lucide-vue-next'
import { useWorkStore } from '@/stores/work'
import PlatformLogo from '@/components/chat/PlatformLogo.vue'
import PlatformQrModal from '@/components/settings/PlatformQrModal.vue'
import {
  fetchAccounts,
  syncPlatformAccount,
  syncAllPlatformAccounts,
  type PlatformAccount,
} from '@/api/platformAccounts'

const props = defineProps<{
  visible: boolean
}>()

const emit = defineEmits<{
  close: []
  synced: []
}>()

const workStore = useWorkStore()
const links = ref('')
const isCollecting = ref(false)
const results = ref<Array<{ url: string; title: string; ok: boolean; error?: string }>>([])
const inputRef = ref<HTMLTextAreaElement | null>(null)
const fileInputRef = ref<HTMLInputElement | null>(null)

// ===== 平台同步：从设置页搬过来的快捷同步入口 =====
const tab = ref<'link' | 'platform'>('link')
const accounts = ref<PlatformAccount[]>([])
const accountsLoading = ref(false)
const accountsError = ref('')
const accountsLoaded = ref(false)

const qrVisible = ref(false)
const qrPlatform = ref('')
const qrPlatformLabel = ref('')

const boundCount = computed(() => accounts.value.filter(a => a.bound).length)
const syncingAll = computed(() => accounts.value.some(a => a.sync_status === 'running'))

let syncPollTimer: ReturnType<typeof setInterval> | null = null
let syncStartedAt = 0
let syncSawRunning = false

function stopSyncPolling() {
  if (syncPollTimer) {
    clearInterval(syncPollTimer)
    syncPollTimer = null
  }
}

function startSyncPolling() {
  if (syncPollTimer) return
  syncStartedAt = Date.now()
  syncSawRunning = false
  syncPollTimer = setInterval(async () => {
    try {
      accounts.value = await fetchAccounts()
    } catch {
      return
    }
    const running = accounts.value.some(a => a.sync_status === 'running')
    if (running) syncSawRunning = true
    const elapsed = Date.now() - syncStartedAt
    // 后端置 running 有延迟：至少等到观察到 running，或超过 10 秒兜底
    if (!running && (syncSawRunning || elapsed > 10_000)) {
      stopSyncPolling()
      void onSyncFinished()
    } else if (elapsed > 600_000) {
      stopSyncPolling()
    }
  }, 2500)
}

async function onSyncFinished() {
  try {
    await workStore.fetchWorks()
  } catch {
    // 列表刷新失败不影响同步结果
  }
  emit('synced')
}

async function loadAccounts() {
  accountsLoading.value = true
  accountsError.value = ''
  try {
    accounts.value = await fetchAccounts()
    accountsLoaded.value = true
  } catch (e: any) {
    accountsError.value = e?.message || '加载平台账号失败'
  } finally {
    accountsLoading.value = false
  }
}

function switchToPlatformTab() {
  tab.value = 'platform'
  if (!accountsLoaded.value && !accountsLoading.value) void loadAccounts()
}

async function syncOne(account: PlatformAccount) {
  if (!account.id || account.sync_status === 'running') return
  account.sync_status = 'running'
  account.sync_error = null
  startSyncPolling()
  try {
    await syncPlatformAccount(account.id)
  } catch (e: any) {
    stopSyncPolling()
    account.sync_status = 'idle'
    account.sync_error = e?.message || '同步启动失败'
  }
}

async function syncAll() {
  const targets = accounts.value.filter(a => a.bound && a.id)
  if (!targets.length) return
  targets.forEach((a) => {
    a.sync_status = 'running'
    a.sync_error = null
  })
  startSyncPolling()
  try {
    const count = await syncAllPlatformAccounts()
    if (count === 0) {
      stopSyncPolling()
      await loadAccounts()
    }
  } catch {
    stopSyncPolling()
    await loadAccounts()
  }
}

function openBind(account: PlatformAccount) {
  qrPlatform.value = account.platform
  qrPlatformLabel.value = account.platform_label
  qrVisible.value = true
}

async function onBound() {
  await loadAccounts()
}

function formatSyncTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime()
  if (!Number.isFinite(diff)) return ''
  if (diff < 60_000) return '刚刚'
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)} 分钟前`
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)} 小时前`
  return `${Math.floor(diff / 86_400_000)} 天前`
}

onUnmounted(() => stopSyncPolling())

watch(() => props.visible, (v) => {
  if (v) {
    links.value = ''
    results.value = []
    isCollecting.value = false
    tab.value = 'link'
    nextTick(() => inputRef.value?.focus())
  } else {
    // 关闭后停止轮询，避免后台持续请求；重新打开时按需重新拉取
    stopSyncPolling()
    accountsLoaded.value = false
  }
})

function triggerFileInput() {
  fileInputRef.value?.click()
}

function onFileSelect(e: Event) {
  const file = (e.target as HTMLInputElement).files?.[0]
  if (file) {
    links.value = `[CSV: ${file.name}]`
  }
}

function onDrop(e: DragEvent) {
  const file = e.dataTransfer?.files[0]
  if (file) {
    links.value = `[CSV: ${file.name}]`
  }
}

async function startCollect() {
  const urls = links.value.split('\n').map(l => l.trim()).filter(Boolean)
  if (urls.length === 0) return

  isCollecting.value = true
  results.value = []

  for (const url of urls) {
    try {
      const work = await workStore.addWorkByLink(url)
      results.value.push({ url, title: work.title, ok: true })
    } catch (err: any) {
      results.value.push({ url, title: '', ok: false, error: err.message || '采集失败' })
    }
  }

  isCollecting.value = false
  if (results.value.some(r => r.ok)) emit('synced')
}
</script>

<style scoped>
.awm-overlay {
  position: fixed;
  top: 0;
  left: 0;
  width: 100vw;
  height: 100vh;
  background: rgba(0,0,0,0.3);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 9999;
}
.awm {
  width: 480px;
  max-height: 80vh;
  background: #fff;
  border-radius:16px;
  box-shadow: 0 8px 32px rgba(0,0,0,0.15);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.awm-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 20px;
  border-bottom: 1px solid rgba(0,0,0,0.06);
}
.awm-title {
  font-size: 15px;
  font-weight: 600;
  color: #1a1a1a;
}
.awm-close {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: none;
  background: none;
  border-radius:10px;
  color: #888;
  cursor: pointer;
}
.awm-close:hover {
  background: rgba(0,0,0,0.05);
}
.awm-body {
  padding: 20px;
  overflow-y: auto;
}

/* ====== 两个采集入口的 Tab 切换 ====== */
.awm-tabs {
  display: flex;
  gap: 6px;
  padding: 10px 20px 0;
  border-bottom: 1px solid rgba(0, 0, 0, 0.06);
}
.awm-tab {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 7px 12px;
  margin-bottom: -1px;
  border: none;
  background: none;
  border-bottom: 2px solid transparent;
  font-family: inherit;
  font-size: 13px;
  font-weight: 500;
  color: #8e8e93;
  cursor: pointer;
  transition: color 0.15s ease, border-color 0.15s ease;
}
.awm-tab:hover {
  color: #1a1a1a;
}
.awm-tab-active {
  color: #1a1a1a;
  border-bottom-color: #1a1a1a;
}

/* ====== 平台同步面板 ====== */
.awm-plat-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  padding: 28px 0;
  font-size: 13px;
  color: #8e8e93;
}
.awm-plat-state-err {
  gap: 8px;
}
.awm-plat-err-text {
  color: #cf222e;
  text-align: center;
}
.awm-plat-retry {
  padding: 5px 12px;
  border: 1px solid rgba(0, 0, 0, 0.12);
  border-radius: 10px;
  background: #fff;
  font-family: inherit;
  font-size: 12px;
  color: #1a1a1a;
  cursor: pointer;
}
.awm-spinner {
  width: 18px;
  height: 18px;
  border: 2px solid rgba(0, 0, 0, 0.08);
  border-top-color: #4a90d9;
  border-radius: 50%;
  animation: awm-spin 0.8s linear infinite;
}
@keyframes awm-spin {
  to { transform: rotate(360deg); }
}
.awm-spin {
  animation: awm-spin 0.9s linear infinite;
}
.awm-plat-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.awm-plat {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 10px;
  border: 1px solid rgba(0, 0, 0, 0.06);
  border-radius: 12px;
  background: #fbfbfc;
  transition: border-color 0.15s ease, background 0.15s ease;
}
.awm-plat-running {
  border-color: rgba(74, 144, 217, 0.35);
  background: rgba(74, 144, 217, 0.05);
}
.awm-plat-avatar {
  display: flex;
  align-items: center;
  justify-content: center;
  flex: none;
  width: 30px;
  height: 30px;
  border-radius: 9px;
  background: #fff;
  overflow: hidden;
}
.awm-plat-avatar-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.awm-plat-info {
  flex: 1;
  min-width: 0;
}
.awm-plat-name {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  font-weight: 500;
  color: #1a1a1a;
}
.awm-plat-count {
  font-size: 11px;
  font-weight: 400;
  color: #8e8e93;
}
.awm-plat-idle {
  padding: 1px 6px;
  border-radius: 999px;
  background: rgba(0, 0, 0, 0.05);
  font-size: 10px;
  font-weight: 400;
  color: #8e8e93;
}
.awm-plat-meta {
  margin-top: 2px;
  font-size: 11px;
  color: #8e8e93;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.awm-plat-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  flex: none;
  padding: 5px 10px;
  border: none;
  border-radius: 9px;
  background: #1a1a1a;
  font-family: inherit;
  font-size: 12px;
  font-weight: 500;
  color: #fff;
  cursor: pointer;
  transition: opacity 0.15s ease;
}
.awm-plat-btn:hover:not(:disabled) {
  opacity: 0.85;
}
.awm-plat-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.awm-sync-all {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  width: 100%;
  margin-top: 12px;
}
.awm-plat-tip {
  display: flex;
  align-items: flex-start;
  gap: 5px;
  margin: 10px 0 0;
  font-size: 11px;
  line-height: 1.5;
  color: #8e8e93;
}
.awm-label {
  font-size: 13px;
  font-weight: 500;
  color: #333;
  margin-bottom: 6px;
}
.awm-platforms {
  font-size: 11px;
  color: #888;
  margin-bottom: 10px;
}
.awm-input {
  width: 100%;
  padding: 10px 12px;
  border: 1px solid rgba(0,0,0,0.12);
  border-radius:12px;
  font-size: 13px;
  font-family: 'SF Mono', 'JetBrains Mono', Consolas, monospace;
  resize: vertical;
  min-height: 80px;
  box-sizing: border-box;
  outline: none;
  background: #fff;
  transition: border-color 0.15s;
}
.awm-input:focus {
  border-color: #4a90d9;
}
.awm-divider {
  text-align: center;
  color: #aaa;
  font-size: 12px;
  margin: 16px 0;
  position: relative;
}
.awm-csv-area {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 20px;
  border: 2px dashed rgba(0,0,0,0.1);
  border-radius:12px;
  color: #888;
  font-size: 13px;
  cursor: pointer;
  transition: border-color 0.15s, background 0.15s;
}
.awm-csv-area:hover {
  border-color: #4a90d9;
  background: rgba(74,144,217,0.04);
}
.awm-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding: 12px 20px;
  border-top: 1px solid rgba(0,0,0,0.06);
}
.awm-btn {
  padding: 8px 16px;
  border-radius:12px;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  border: none;
  transition: all 0.15s;
}
.awm-btn-cancel {
  background: #f3f4f6;
  color: #555;
}
.awm-btn-cancel:hover {
  background: #e5e7eb;
}
.awm-btn-primary {
  background: #4a90d9;
  color: #fff;
}
.awm-btn-primary:hover:not(:disabled) {
  background: #3a7bc8;
}
.awm-btn-primary:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.awm-results {
  padding: 0 20px 16px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.awm-result {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 10px;
  border-radius:10px;
  font-size: 12px;
}
.awm-result-ok {
  background: #f0fdf4;
  color: #2da44e;
}
.awm-result-err {
  background: #fef2f2;
  color: #cf222e;
}
.awm-result-title {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.awm-result-status {
  flex: none;
  font-size: 11px;
}
</style>