<template>
  <div class="pac-card" :class="{ 'pac-card-running': account.sync_status === 'running' }">
    <div class="pac-row">
      <div class="pac-avatar" :class="{ 'pac-avatar-round': account.bound && account.platform_avatar_url }">
        <img
          v-if="account.bound && account.platform_avatar_url"
          :src="account.platform_avatar_url"
          :alt="account.platform_label"
          class="pac-avatar-img"
        />
        <PlatformLogo
          v-else-if="hasLogo"
          :platform="account.platform"
          :name="account.platform_label"
          style="width:22px;height:22px;"
        />
        <span v-else class="pac-avatar-text">{{ account.platform_label.charAt(0) }}</span>
      </div>

      <div class="pac-info">
        <div class="pac-title-row">
          <span class="pac-name">{{ account.platform_label }}</span>
          <span v-if="account.sync_status === 'running'" class="pac-badge pac-badge-running">
            <span class="pac-spin"></span>同步中
          </span>
          <span v-else-if="account.bound" class="pac-badge pac-badge-ok">
            <span class="pac-dot"></span>已绑定
          </span>
          <span v-else class="pac-badge pac-badge-idle">未绑定</span>
        </div>

        <div class="pac-meta">
          <template v-if="account.bound">
            <a
              v-if="account.platform_home_url"
              :href="account.platform_home_url"
              target="_blank"
              rel="noopener noreferrer"
              class="pac-nickname pac-nickname-link"
              :title="`打开${account.platform_label}主页`"
            >{{ nickname }}</a>
            <span v-else class="pac-nickname">{{ nickname }}</span>
            <span class="pac-sep">·</span>
            <span>{{ account.works_count }} 篇作品</span>
            <template v-if="account.fans_count && account.fans_count > 0">
              <span class="pac-sep">·</span>
              <span>{{ formatFans(account.fans_count) }} 粉丝</span>
            </template>
          </template>
          <template v-else>扫码登录后即可一键回收该平台的全部已发布作品</template>
        </div>

        <div v-if="account.bound" class="pac-sub">
          <template v-if="account.sync_status === 'running'">正在回收作品数据，请勿关闭当前页面</template>
          <template v-else-if="account.last_synced_at">
            <span :title="fullTime(account.last_synced_at)">上次同步 {{ relativeTime(account.last_synced_at) }}</span>
          </template>
          <template v-else>尚未同步过作品</template>
        </div>

        <div v-if="account.sync_error" class="pac-error">
          <AlertCircle :size="13" />
          <span class="pac-error-text">{{ account.sync_error }}</span>
          <button type="button" class="pac-error-retry" @click="emit('retry')">重试</button>
        </div>
      </div>

      <div class="pac-actions">
        <template v-if="account.bound">
          <button
            type="button"
            class="pac-btn pac-btn-primary"
            :disabled="account.sync_status === 'running'"
            @click="emit('sync')"
          >
            <RefreshCw :size="13" :class="{ 'pac-rotating': account.sync_status === 'running' }" />
            <span>{{ account.sync_status === 'running' ? '同步中' : '同步作品' }}</span>
          </button>
          <button
            type="button"
            class="pac-btn pac-btn-ghost"
            :disabled="account.sync_status === 'running'"
            @click="emit('unbind')"
          >
            解绑
          </button>
        </template>
        <button
          v-else
          type="button"
          class="pac-btn pac-btn-primary"
          @click="emit('bind')"
        >
          <QrCode :size="13" />
          <span>扫码绑定</span>
        </button>
      </div>
    </div>

    <div v-if="account.sync_status === 'running'" class="pac-progress">
      <div class="pac-progress-bar"></div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { AlertCircle, QrCode, RefreshCw } from 'lucide-vue-next'
import PlatformLogo from '@/components/chat/PlatformLogo.vue'
import type { PlatformAccount } from '@/api/platformAccounts'

const props = defineProps<{
  account: PlatformAccount
}>()

const emit = defineEmits<{
  (e: 'bind'): void
  (e: 'sync'): void
  (e: 'unbind'): void
  (e: 'retry'): void
}>()

const LOGO_PLATFORMS = new Set(['xiaohongshu', 'douyin', 'bilibili'])

const hasLogo = computed(() => LOGO_PLATFORMS.has(props.account.platform))
const nickname = computed(() => props.account.platform_nickname || '已绑定账号')

function formatFans(count: number): string {
  if (count >= 10000) return (count / 10000).toFixed(1) + 'w'
  if (count >= 1000) return (count / 1000).toFixed(1) + 'k'
  return String(count)
}

function relativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime()
  if (diff < 60000) return '刚刚'
  if (diff < 3600000) return Math.floor(diff / 60000) + ' 分钟前'
  if (diff < 86400000) return Math.floor(diff / 3600000) + ' 小时前'
  if (diff < 2592000000) return Math.floor(diff / 86400000) + ' 天前'
  return fullTime(iso)
}

function fullTime(iso: string): string {
  const d = new Date(iso)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}
</script>

<style scoped>
.pac-card {
  position: relative;
  border: 1px solid #EFEFEF;
  border-radius: 14px;
  background: #FFFFFF;
  padding: 13px 14px;
  transition: border-color 0.18s ease, box-shadow 0.18s ease, transform 0.18s ease;
  overflow: hidden;
}
.pac-card:hover {
  border-color: #DCE4F7;
  box-shadow: 0 10px 26px rgba(59, 108, 246, 0.08);
  transform: translateY(-1px);
}
.pac-card-running {
  border-color: #DCE4FB;
  background: #FBFCFF;
}
.pac-row {
  display: flex;
  align-items: center;
  gap: 12px;
}
.pac-avatar {
  width: 38px;
  height: 38px;
  flex: none;
  border-radius: 11px;
  background: #F5F5F5;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
}
.pac-avatar-round { border-radius: 50%; }
.pac-avatar-img {
  width: 38px;
  height: 38px;
  object-fit: cover;
}
.pac-avatar-text {
  font-size: 14px;
  font-weight: 500;
  color: #9A9A9A;
}
.pac-info {
  flex: 1;
  min-width: 0;
}
.pac-title-row {
  display: flex;
  align-items: center;
  gap: 6px;
}
.pac-name {
  font-size: 14px;
  font-weight: 500;
  color: #1A1A1A;
  line-height: 1.3;
}
.pac-badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 1px 7px;
  border-radius: 14px;
  font-size: 11px;
  font-weight: 500;
  line-height: 1.6;
}
.pac-badge-ok { background: #ECFDF5; color: #059669; }
.pac-badge-running { background: #EEF3FE; color: #3B6CF6; }
.pac-badge-idle { background: #F3F4F6; color: #9A9A9A; }
.pac-dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: #10B981;
}
.pac-spin {
  width: 9px;
  height: 9px;
  border: 1.5px solid #C7D5FB;
  border-top-color: #3B6CF6;
  border-radius: 50%;
  animation: pac-spin 0.7s linear infinite;
}
@keyframes pac-spin { to { transform: rotate(360deg); } }
.pac-meta {
  margin-top: 2px;
  font-size: 13px;
  color: #6B7280;
  line-height: 1.5;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pac-nickname {
  color: #1A1A1A;
  font-weight: 500;
}
.pac-nickname-link {
  text-decoration: none;
  border-bottom: 1px dashed transparent;
  transition: border-color 0.15s ease;
}
.pac-nickname-link:hover { border-bottom-color: #C7D5FB; }
.pac-sep { color: #D1D5DB; margin: 0 4px; }
.pac-sub {
  margin-top: 1px;
  font-size: 12px;
  color: #9A9A9A;
  line-height: 1.5;
}
.pac-error {
  display: flex;
  align-items: center;
  gap: 5px;
  margin-top: 6px;
  padding: 5px 8px;
  border-radius: 8px;
  background: #FDF4F4;
  color: #B42318;
  font-size: 12px;
}
.pac-error-text {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pac-error-retry {
  flex: none;
  padding: 1px 8px;
  border: 1px solid #E3C0BC;
  border-radius: 6px;
  background: #FFFFFF;
  color: #B42318;
  font-size: 11px;
  cursor: pointer;
  transition: background 0.15s ease;
}
.pac-error-retry:hover { background: #FBEDEC; }
.pac-actions {
  display: flex;
  align-items: center;
  gap: 6px;
  flex: none;
}
.pac-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 7px 12px;
  border: none;
  border-radius: 10px;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  white-space: nowrap;
  transition: background 0.15s ease, opacity 0.15s ease;
}
.pac-btn:disabled { cursor: not-allowed; opacity: 0.55; }
.pac-btn-primary { background: #3B6CF6; color: #FFFFFF; }
.pac-btn-primary:hover:not(:disabled) { background: #2F5BE0; }
.pac-btn-ghost { background: #F5F5F5; color: #6B7280; }
.pac-btn-ghost:hover:not(:disabled) { background: #EBEBEB; color: #1A1A1A; }
.pac-rotating { animation: pac-spin 0.9s linear infinite; }
.pac-progress {
  height: 2px;
  margin: 11px -14px -13px;
  background: #EEF3FE;
  overflow: hidden;
}
.pac-progress-bar {
  width: 40%;
  height: 100%;
  background: linear-gradient(90deg, #3B6CF6, #7C9BFA);
  animation: pac-indeterminate 1.1s ease-in-out infinite;
}
@keyframes pac-indeterminate {
  0% { transform: translateX(-100%); }
  100% { transform: translateX(350%); }
}
</style>