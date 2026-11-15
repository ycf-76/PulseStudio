1<template>
  <div style="position:fixed;inset:0;z-index:10001;background:rgba(0,0,0,0.25);backdrop-filter:blur(2px);display:flex;align-items:center;justify-content:center;" @click.self="close">
    <div style="width:900px;max-width:92vw;height:620px;max-height:85vh;background:#FFFFFF;border-radius:20px;display:flex;overflow:hidden;box-shadow:0 20px 60px rgba(0,0,0,0.15);">
      <div style="width:220px;flex-shrink:0;background:var(--ma-bg-base, #EBEEF2);display:flex;flex-direction:column;border-right:1px solid #DFE5EE;">
        <div class="settings-avatar-container" style="padding:16px 16px 16px 20px;border-bottom:1px solid #DFE5EE;display:flex;align-items:center;justify-content:space-between;gap:8px;position:relative;">
          <div style="flex:none;" :title="loginMethodLabel">
            <img
              v-if="authStore.user?.avatar_url"
              :src="authStore.user.avatar_url"
              alt="头像"
              style="width:48px;height:48px;border-radius:50%;object-fit:cover;border:2px solid #E5E7EB;transition:border-color 0.2s;display:block;"
              @mouseenter="($event.target as HTMLElement).style.borderColor='#3B6CF6'"
              @mouseleave="($event.target as HTMLElement).style.borderColor='#E5E7EB'"
            />
            <img
              v-else
              src="/images/avatar/@man.svg"
              alt="默认头像"
              style="width:48px;height:48px;border-radius:50%;object-fit:cover;border:2px solid #E5E7EB;transition:border-color 0.2s;display:block;"
              @mouseenter="($event.target as HTMLElement).style.borderColor='#3B6CF6'"
              @mouseleave="($event.target as HTMLElement).style.borderColor='#E5E7EB'"
            />
          </div>

          <button
            @click="handleLogout"
            title="退出登录"
            aria-label="退出登录"
            style="display:flex;align-items:center;justify-content:center;width:32px;height:32px;flex:none;border:1px solid #E5E7EB;border-radius:10px;background:#FFFFFF;color:#9CA3AF;cursor:pointer;transition:all 0.15s;"
            onmouseover="this.style.borderColor='#FCA5A5';this.style.color='#EF4444';this.style.background='#FEF2F2'"
            onmouseout="this.style.borderColor='#E5E7EB';this.style.color='#9CA3AF';this.style.background='#FFFFFF'"
          >
            <LogOut :size="15" />
          </button>
        </div>
        <nav style="flex:1;padding:10px 8px 0;display:flex;flex-direction:column;gap:2px;">
          <button v-for="sec in sections" :key="sec.key"
            style="display:flex;align-items:center;gap:10px;padding:9px 11px;border:none;background:transparent;color:#4B4B4B;font-size:13px;cursor:pointer;border-radius:12px;text-align:left;width:100%;"
            :style="{ background: activeSection === sec.key ? '#DBE2EC' : 'transparent', color: activeSection === sec.key ? '#1A1A1A' : '#4B4B4B', fontWeight: activeSection === sec.key ? 600 : 400 }"
            @click="activeSection = sec.key">
            {{ sec.label }}
          </button>
        </nav>
        <div style="padding:10px 8px 12px;border-top:1px solid #DFE5EE;">
          <button @click="close" style="display:flex;align-items:center;gap:8px;padding:7px 11px;border:none;background:transparent;color:#9A9A9A;font-size:12px;cursor:pointer;border-radius:12px;width:100%;">
            返回工作台
          </button>
        </div>
      </div>
      <div style="flex:1;display:flex;flex-direction:column;min-width:0;">
        <div style="padding:16px 20px 0;flex-shrink:0;">
          <h2 style="margin:0 0 2px;color:#1A1A1A;font-size:18px;font-weight: 500;">{{ currentTitle }}</h2>
          <p style="margin:0;color:#9A9A9A;font-size:13px;">{{ currentDesc }}</p>
        </div>
        <div style="flex:1;padding:12px 20px 20px;overflow-y:auto;min-height:0;">
          <div v-if="childError" style="color:#EF4444;padding:20px;background:rgba(239,68,68,0.08);border-radius:12px;font-size:var(--text-sm);word-break:break-all;">
            {{ childError }}
          </div>
          <div v-else-if="activeSection === 'profile'" style="color:var(--rl-text-2);">
            <CreatorProfile />
          </div>
          <div v-else-if="activeSection === 'agents'" style="color:var(--rl-text-2);">
            <AgentManager />
          </div>
          <div v-else-if="activeSection === 'wechat'" class="ao-page">
            <section class="stg-hero stg-hero-auto">
              <img class="stg-hero-img stg-hero-img-auto" src="/images/setting_images/5.jpg" alt="自动化办公" />
              <div class="stg-hero-body">
                <h3 class="stg-hero-title">连接你的通讯工具，即可开始使用</h3>
                <p class="stg-hero-desc">绑定后，智能体会在飞书 / 微信里随时待命，帮你写文案、查资料、推进任务。</p>
                <div class="stg-hero-chips">
                  <span class="ao-chip" :class="{ 'ao-chip-on': feishuStatus.connected, 'ao-chip-wait': !feishuStatus.connected && feishuStatus.loading }">
                    <span class="ao-chip-dot"></span>飞书{{ feishuStatus.connected ? '已连接' : feishuStatus.loading ? '连接中' : '未连接' }}
                  </span>
                  <span class="ao-chip" :class="{ 'ao-chip-on': wechatStatus.connected, 'ao-chip-wait': !wechatStatus.connected && wechatStatus.loading }">
                    <span class="ao-chip-dot"></span>微信{{ wechatStatus.connected ? '已连接' : wechatStatus.loading ? '连接中' : '未连接' }}
                  </span>
                </div>
              </div>
            </section>

            <div class="ao-channels">
              <section class="ao-channel">
                <div class="ao-channel-top">
                  <span class="ao-logo"><img src="/icons/feishu-app.svg" alt="飞书" /></span>
                  <div class="ao-channel-copy">
                    <div class="ao-channel-name">
                      飞书
                      <span v-if="feishuStatus.connected" class="ao-status ao-status-on"><span class="ao-status-dot"></span>已连接</span>
                      <span v-else-if="feishuStatus.loading" class="ao-status ao-status-wait"><span class="ao-spin"></span>连接中</span>
                      <span v-else class="ao-status">未连接</span>
                    </div>
                    <div class="ao-channel-desc">{{ feishuStatus.connected ? feishuStatus.account : '在飞书里 @ 机器人，就能直接和智能体对话' }}</div>
                  </div>
                </div>

                <div class="ao-steps">
                  <div class="ao-step" :class="{ 'ao-step-done': feishuOAuthStatus.connected }">
                    <span class="ao-step-idx">1</span>
                    <div class="ao-step-copy">
                      <div class="ao-step-title">授权 Wiki / Doc</div>
                      <div class="ao-step-desc">{{ feishuOAuthStatus.connected ? `已授权：${feishuOAuthStatus.account}` : '授权后，智能体才能读取你的飞书知识库与文档' }}</div>
                    </div>
                    <button v-if="!feishuOAuthStatus.connected && !feishuOAuthStatus.loading"
                      class="ao-btn ao-btn-ghost" @click="bindFeishuAccount">去授权</button>
                    <button v-else-if="feishuOAuthStatus.loading"
                      class="ao-btn ao-btn-ghost" disabled>授权中...</button>
                    <button v-else
                      class="ao-btn ao-btn-text" @click="unbindFeishuAccount">解除授权</button>
                  </div>

                  <div class="ao-step" :class="{ 'ao-step-done': feishuStatus.connected }">
                    <span class="ao-step-idx">2</span>
                    <div class="ao-step-copy">
                      <div class="ao-step-title">启动飞书机器人</div>
                      <div class="ao-step-desc">在飞书里 @ 机器人，即可与智能体对话</div>
                    </div>
                    <button v-if="!feishuStatus.connected && !feishuStatus.loading"
                      class="ao-btn ao-btn-primary" @click="toggleBot('feishu', true)">去绑定</button>
                    <button v-else-if="feishuStatus.loading"
                      class="ao-btn ao-btn-primary" disabled>连接中...</button>
                    <button v-else
                      class="ao-btn ao-btn-ghost" @click="toggleBot('feishu', false)">断开</button>
                  </div>
                </div>
              </section>

              <section class="ao-channel">
                <div class="ao-channel-top">
                  <span class="ao-logo"><img src="/icons/wechat-si.svg" alt="微信" /></span>
                  <div class="ao-channel-copy">
                    <div class="ao-channel-name">
                      微信
                      <span v-if="wechatStatus.connected" class="ao-status ao-status-on"><span class="ao-status-dot"></span>已连接</span>
                      <span v-else-if="wechatStatus.loading" class="ao-status ao-status-wait"><span class="ao-spin"></span>{{ wechatStatus.statusLabel || '连接中' }}</span>
                      <span v-else class="ao-status">未连接</span>
                    </div>
                    <div class="ao-channel-desc">{{ wechatStatus.connected ? wechatStatus.account : '扫码登录后，在微信里随时向智能体提问' }}</div>
                  </div>
                  <div class="ao-channel-actions">
                    <button v-if="!wechatStatus.connected && !wechatStatus.loading"
                      class="ao-btn ao-btn-primary" @click="toggleBot('wechat', true)">去绑定</button>
                    <button v-else-if="wechatStatus.loading"
                      class="ao-btn ao-btn-primary" disabled>{{ wechatStatus.statusLabel || '连接中' }}...</button>
                    <button v-else
                      class="ao-btn ao-btn-ghost" @click="toggleBot('wechat', false)">断开</button>
                  </div>
                </div>

                <transition name="ao-fade">
                  <div v-if="wechatQrCode" class="ao-qr">
                    <img :src="wechatQrCode" alt="微信登录二维码" class="ao-qr-img" />
                    <div class="ao-qr-copy">
                      <div class="ao-qr-title">用微信扫描二维码</div>
                      <p class="ao-qr-desc">扫码后，在手机微信上确认登录即可完成绑定。</p>
                      <div class="ao-qr-tip">
                        <span class="ao-spin ao-spin-dark"></span>
                        等待扫码中，状态会自动刷新
                      </div>
                    </div>
                  </div>
                </transition>
              </section>
            </div>

            <div class="ao-coming">
              <div class="ao-coming-title">即将支持</div>
              <div class="ao-coming-grid">
                <div class="ao-coming-card">
                  <span class="ao-coming-logo">企微</span>
                  <div>
                    <div class="ao-coming-name">企业微信</div>
                    <div class="ao-coming-desc">即将上线</div>
                  </div>
                </div>
                <div class="ao-coming-card">
                  <span class="ao-coming-logo">钉钉</span>
                  <div>
                    <div class="ao-coming-name">钉钉</div>
                    <div class="ao-coming-desc">即将上线</div>
                  </div>
                </div>
              </div>
            </div>
          </div>
          <div v-else-if="activeSection === 'plugins'" class="ao-page">
            <section v-if="!selectedPlugin" class="stg-hero stg-hero-wide stg-hero-plugin">
              <img class="stg-hero-img stg-hero-img-plugin" src="/images/setting_images/img_1.png" alt="插件管理" />
              <div class="stg-hero-body">
                <h3 class="stg-hero-title">用插件扩展你的创作能力</h3>
                <p class="stg-hero-desc">接入更多发布渠道、数据源与工作流节点，让平台按你的方式运转。</p>
                <div class="stg-hero-chips">
                  <span class="stg-chip">内置插件开箱可用</span>
                  <span class="stg-chip">支持上传 .zip</span>
                  <span class="stg-chip">支持 GitHub 安装</span>
                </div>
              </div>
            </section>

            <PluginDetailView
              v-if="selectedPlugin"
              :plugin="selectedPlugin"
              @back="selectedPlugin = null"
            />
            <PluginManagerInline v-else @select-plugin="selectedPlugin = $event" />
          </div>
          <div v-else-if="activeSection === 'skills'" class="ao-page">
            <section class="stg-hero stg-hero-skills">
              <img class="stg-hero-img stg-hero-img-skills" src="/images/setting_images/img_2.png" alt="Skills 技能" @error="($event.target as HTMLElement).style.display='none'" />
              <div class="stg-hero-body">
                <h3 class="stg-hero-title">给智能体装配可复用的技能</h3>
                <p class="stg-hero-desc">把常用的工具组合与提示词封装成技能模板，一次配置，反复调用。</p>
                <div class="stg-hero-chips">
                  <span class="stg-chip">技能模板</span>
                  <span class="stg-chip">提示词型 Skill</span>
                  <span class="stg-chip">可用工具</span>
                </div>
              </div>
            </section>
            <SkillsManager />
          </div>
          <div v-else-if="activeSection === 'accounts'" style="color:var(--rl-text-2);">
            <PlatformAccounts />
          </div>
          <div v-else-if="activeSection === 'config'" style="color:var(--rl-text-2);">
            <ModelKeyManager />
          </div>
          <div v-else-if="activeSection === 'display'" style="color:var(--rl-text-2);">
            <h3 style="margin:0 0 6px;color:#1A1A1A;font-size:14px;font-weight: 500;">流式输出速度</h3>
            <p style="margin:0 0 16px;color:#9CA3AF;font-size:13px;">调节 AI 回复的逐字显示速度。改完即时生效，无需刷新。</p>

            <div style="display:flex;flex-direction:column;gap:16px;">
              <div style="background:#F9FAFB;border-radius:14px;padding:14px 16px;">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
                  <label style="font-size:13px;font-weight:500;color:var(--rl-text-2);">正文速度</label>
                  <span style="font-size:12px;color:#6B7280;font-variant-numeric:tabular-nums;">{{ streamSpeed.baseCps.value }} 字/秒</span>
                </div>
                <input type="range" :min="streamSpeedBounds.baseCps.min" :max="streamSpeedBounds.baseCps.max" :step="streamSpeedBounds.baseCps.step" v-model.number="streamSpeed.baseCps.value"
                  style="width:100%;accent-color:#3B6CF6;" />
                <div style="display:flex;justify-content:space-between;margin-top:4px;">
                  <span style="font-size:11px;color:#9CA3AF;">更慢</span>
                  <span style="font-size:11px;color:#9CA3AF;">更快</span>
                </div>
              </div>

              <div style="background:#F9FAFB;border-radius:14px;padding:14px 16px;">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
                  <label style="font-size:13px;font-weight:500;color:var(--rl-text-2);">思考过程速度</label>
                  <span style="font-size:12px;color:#6B7280;font-variant-numeric:tabular-nums;">{{ streamSpeed.thinkCps.value }} 字/秒</span>
                </div>
                <input type="range" :min="streamSpeedBounds.thinkCps.min" :max="streamSpeedBounds.thinkCps.max" :step="streamSpeedBounds.thinkCps.step" v-model.number="streamSpeed.thinkCps.value"
                  style="width:100%;accent-color:#3B6CF6;" />
              </div>

              <div style="background:#F9FAFB;border-radius:14px;padding:14px 16px;">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
                  <label style="font-size:13px;font-weight:500;color:var(--rl-text-2);">最快速度</label>
                  <span style="font-size:12px;color:#6B7280;font-variant-numeric:tabular-nums;">{{ streamSpeed.maxCps.value }} 字/秒</span>
                </div>
                <input type="range" :min="streamSpeedBounds.maxCps.min" :max="streamSpeedBounds.maxCps.max" :step="streamSpeedBounds.maxCps.step" v-model.number="streamSpeed.maxCps.value"
                  style="width:100%;accent-color:#3B6CF6;" />
              </div>

              <div style="background:#F9FAFB;border-radius:14px;padding:14px 16px;">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
                  <label style="font-size:13px;font-weight:500;color:var(--rl-text-2);">渲染帧间隔</label>
                  <span style="font-size:12px;color:#6B7280;font-variant-numeric:tabular-nums;">{{ streamSpeed.frameMs.value }}ms</span>
                </div>
                <input type="range" :min="streamSpeedBounds.frameMs.min" :max="streamSpeedBounds.frameMs.max" :step="streamSpeedBounds.frameMs.step" v-model.number="streamSpeed.frameMs.value"
                  style="width:100%;accent-color:#3B6CF6;" />
                <div style="display:flex;justify-content:space-between;margin-top:4px;">
                  <span style="font-size:11px;color:#9CA3AF;">更流畅</span>
                  <span style="font-size:11px;color:#9CA3AF;">省性能</span>
                </div>
              </div>

              <div style="display:flex;gap:8px;align-items:center;">
                <button @click="streamSpeed.reset()"
                  style="padding:7px 14px;border:1px solid #E5E7EB;background:#FFFFFF;color:#6B7280;font-size:13px;cursor:pointer;border-radius:10px;transition:all 0.15s;"
                  onmouseover="this.style.borderColor='#3B6CF6';this.style.color='#3B6CF6'" onmouseout="this.style.borderColor='#E5E7EB';this.style.color='#6B7280'">
                  恢复默认
                </button>
                <span style="font-size:12px;color:#9CA3AF;">默认：正文 160 · 思考 90 · 最慢 60 · 最快 4800 · 帧间隔 16ms</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, reactive, onMounted, onUnmounted, onErrorCaptured, defineAsyncComponent, watch } from 'vue'
import { useAuthStore } from '@/stores/auth'
import { LogOut } from 'lucide-vue-next'
import { useStreamSpeed, streamSpeedBounds } from '@/components/chat/composables/useStreamSpeed'
import { useUIState } from '@/composables/useUIState'

const authStore = useAuthStore()
const streamSpeed = useStreamSpeed()
const { settingsSection } = useUIState()

const AgentManager = defineAsyncComponent(() => import('@/components/settings/AgentManager.vue'))
const CreatorProfile = defineAsyncComponent(() => import('@/components/settings/CreatorProfile.vue'))
const PlatformAccounts = defineAsyncComponent(() => import('@/components/settings/PlatformAccounts.vue'))
const ConfigPage = defineAsyncComponent(() => import('@/components/workbench/ConfigPage.vue'))
const ModelKeyManager = defineAsyncComponent(() => import('@/components/settings/ModelKeyManager.vue'))
const PluginManagerInline = defineAsyncComponent(() => import('@/components/settings/PluginManagerInline.vue'))
const PluginDetailView = defineAsyncComponent(() => import('@/components/settings/PluginDetailView.vue'))
const SkillsManager = defineAsyncComponent(() => import('@/components/settings/SkillsManager.vue'))
import type { Plugin } from '@/api/plugins'

const emit = defineEmits<{ close: [] }>()

const childError = ref('')
const selectedPlugin = ref<Plugin | null>(null)

interface BotStatus {
  connected: boolean
  loading: boolean
  account: string
  statusLabel: string
}

const wechatStatus = reactive<BotStatus>({
  connected: false,
  loading: false,
  account: '',
  statusLabel: '',
})

const wechatQrCode = ref<string>('')

const feishuStatus = reactive<BotStatus>({
  connected: false,
  loading: false,
  account: '',
  statusLabel: '',
})

interface FeishuOAuthStatus {
  connected: boolean
  loading: boolean
  account: string
}

const feishuOAuthStatus = reactive<FeishuOAuthStatus>({
  connected: false,
  loading: false,
  account: '',
})

let wechatPollTimer: ReturnType<typeof setInterval> | null = null
let feishuPollTimer: ReturnType<typeof setInterval> | null = null

onErrorCaptured((err, instance, info) => {
  console.error('[SettingsView] ERROR CAPTURED:', err, info)
  childError.value = String(err)
  return false
})

async function apiFetch(path: string, opts?: RequestInit) {
  const token = localStorage.getItem('token')
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (token) headers['Authorization'] = `Bearer ${token}`
  const res = await fetch(`${path}`, { headers, ...opts })
  return res.json()
}

async function loadFeishuOAuthStatus() {
  try {
    const r = await apiFetch('/api/feishu/oauth/status')
    if (r.success && r.data) {
      feishuOAuthStatus.connected = !!r.data.connected
      feishuOAuthStatus.account = r.data.account || ''
    }
  } catch {}
}

async function bindFeishuAccount() {
  feishuOAuthStatus.loading = true
  try {
    const r = await apiFetch('/api/feishu/oauth/authorize')
    if (!r.success || !r.authorize_url) {
      throw new Error(r.detail || r.error || '无法生成飞书授权链接')
    }
    window.location.href = r.authorize_url
  } catch (e: any) {
    feishuOAuthStatus.loading = false
    alert(e.message || '飞书授权启动失败')
  }
}

async function unbindFeishuAccount() {
  if (!confirm('解除授权后，智能体将不能访问你的飞书 Wiki 和 Doc，确定继续吗？')) return
  try {
    await apiFetch('/api/feishu/oauth/connection', { method: 'DELETE' })
    feishuOAuthStatus.connected = false
    feishuOAuthStatus.account = ''
  } catch (e: any) {
    alert(e.message || '解除授权失败')
  }
}

async function toggleBot(type: 'wechat' | 'feishu', enable: boolean) {
  const apiBase = type === 'wechat' ? '/api/wechat' : '/api/feishu'
  const statusObj = type === 'wechat' ? wechatStatus : feishuStatus

  if (enable) {
    statusObj.loading = true
    statusObj.statusLabel = type === 'wechat' ? '启动中' : '连接中'

    try {
      const r = await apiFetch(`${apiBase}/start`, { method: 'POST' })
      if (r.success) {
        if (type === 'wechat') {
          startWechatPolling()
        } else {
          startFeishuPolling()
        }
      } else {
        statusObj.loading = false
        alert(r.message || r.detail || `${type === 'wechat' ? '微信' : '飞书'}连接失败`)
      }
    } catch (e: any) {
      statusObj.loading = false
      alert(e.message || '网络错误')
    }
  } else {
    try {
      await apiFetch(`${apiBase}/stop`, { method: 'POST' })
    } catch {}
    stopBotTimers(type)
    statusObj.connected = false
    statusObj.loading = false
    statusObj.account = ''
    statusObj.statusLabel = ''
    if (type === 'wechat') wechatQrCode.value = ''
  }
}

async function fetchWechatQrCode() {
  try {
    const r = await apiFetch('/api/wechat/qrcode')
    if (r.success && r.data && r.data.qrcode) {
      wechatQrCode.value = r.data.qrcode
    }
  } catch {}
}

async function pollWechatStatus() {
  try {
    const r = await apiFetch('/api/wechat/status')
    if (!r.success || !r.data) return
    const d = r.data
    if (d.status === 'logged_in') {
      wechatStatus.connected = true
      wechatStatus.loading = false
      wechatStatus.account = d.wxid || d.nickname || '已登录'
      wechatStatus.statusLabel = ''
      wechatQrCode.value = ''
      enableAgentBridge('wechat')
    } else if (d.status === 'waiting_qr') {
      wechatStatus.loading = true
      wechatStatus.statusLabel = '等待扫码'
      if (d.qr_code_base64) {
        wechatQrCode.value = d.qr_code_base64
      } else if (!wechatQrCode.value) {
        fetchWechatQrCode()
      }
    } else if (d.status === 'error') {
      wechatStatus.loading = false
      wechatStatus.statusLabel = ''
      stopWechatPolling()
    } else {
      wechatStatus.statusLabel = d.status === 'starting' ? '启动中...' : d.status === 'scanned' ? '已扫码' : '连接中...'
    }
  } catch {}
}

async function pollFeishuStatus() {
  try {
    const r = await apiFetch('/api/feishu/status')
    if (!r.success || !r.data) return
    const d = r.data
    if (d.status === 'running' || d.is_running) {
      feishuStatus.connected = true
      feishuStatus.loading = false
      feishuStatus.account = d.bot_name || d.app_id || '已连接'
      feishuStatus.statusLabel = ''
      enableAgentBridge('feishu')
    } else if (d.status === 'error') {
      feishuStatus.loading = false
      feishuStatus.statusLabel = ''
      stopFeishuPolling()
    } else {
      feishuStatus.statusLabel = d.status === 'starting' ? '连接中...' : ''
    }
  } catch {}
}

async function enableAgentBridge(type: 'wechat' | 'feishu') {
  const apiBase = type === 'wechat' ? '/api/wechat' : '/api/feishu'
  try {
    await apiFetch(`${apiBase}/agent-bridge`, {
      method: 'POST',
      body: JSON.stringify({ enabled: true }),
    })
  } catch {}
}

function startWechatPolling() {
  stopWechatPolling()
  wechatPollTimer = setInterval(pollWechatStatus, 2000)
  pollWechatStatus()
}

function startFeishuPolling() {
  stopFeishuPolling()
  feishuPollTimer = setInterval(pollFeishuStatus, 3000)
  pollFeishuStatus()
}

function stopWechatPolling() {
  if (wechatPollTimer) { clearInterval(wechatPollTimer); wechatPollTimer = null }
}

function stopFeishuPolling() {
  if (feishuPollTimer) { clearInterval(feishuPollTimer); feishuPollTimer = null }
}

function stopBotTimers(type: 'wechat' | 'feishu') {
  if (type === 'wechat') stopWechatPolling()
  else stopFeishuPolling()
}

onMounted(async () => {
  const oauthResult = new URLSearchParams(window.location.search).get('feishu_oauth')
  const oauthDetail = new URLSearchParams(window.location.search).get('detail')
  if (oauthResult === 'success') {
    activeSection.value = 'wechat'
    window.history.replaceState({}, document.title, window.location.pathname)
    alert('飞书账号已授权，智能体现在可以查找你的 Wiki 和 Doc 了')
  } else if (oauthResult === 'error') {
    activeSection.value = 'wechat'
    window.history.replaceState({}, document.title, window.location.pathname)
    alert(`飞书授权失败：${oauthDetail || '请重试'}`)
  }
  loadFeishuOAuthStatus()
  try {
    const [wRes, fRes] = await Promise.all([
      apiFetch('/api/wechat/status'),
      apiFetch('/api/feishu/status'),
    ])
    if (wRes.success && wRes.data) {
      const d = wRes.data
      if (d.is_running || d.status === 'logged_in') {
        wechatStatus.connected = true
        wechatStatus.account = d.wxid || d.nickname || '已登录'
        startWechatPolling()
      }
    }
    if (fRes.success && fRes.data) {
      const d = fRes.data
      if (d.is_running || d.status === 'running') {
        feishuStatus.connected = true
        feishuStatus.account = d.bot_name || d.app_id || '已连接'
        startFeishuPolling()
      }
    }
  } catch {}
})

onUnmounted(() => {
  stopWechatPolling()
  stopFeishuPolling()
})

const sections = [
  { key: 'profile', label: '创作者画像', desc: '设置你的领域、调性和内容禁忌' },
  { key: 'agents', label: '智能体管理', desc: '创建、编辑和管理智能体' },
  { key: 'accounts', label: '平台账号', desc: '绑定各平台账号，同步作品数据' },
  { key: 'wechat', label: '自动化办公', desc: '连接通讯工具，智能办公助手' },
  { key: 'plugins', label: '插件管理', desc: '管理和配置您的插件' },
  { key: 'skills', label: 'Skills 技能', desc: '管理 Skill 技能配置' },
  { key: 'config', label: '模型配置', desc: '管理 API Key，解锁更多模型' },
  { key: 'display', label: '显示设置', desc: '流式输出速度与渲染参数' },
]

const activeSection = ref(
  settingsSection.value && sections.some(s => s.key === settingsSection.value)
    ? settingsSection.value
    : 'profile'
)

const currentTitle = computed(() => sections.find(s => s.key === activeSection.value)?.label ?? '')
const currentDesc = computed(() => sections.find(s => s.key === activeSection.value)?.desc ?? '')

const loginMethodLabel = computed(() => {
  const method = authStore.user?.login_method || ''
  const map: Record<string, string> = {
    qrcode: '扫码登录',
    email: '邮箱登录',
    plugin: '插件登录',
    session_refresh: '会话刷新',
  }
  return map[method] || ''
})

async function handleLogout() {
  try {
    await authStore.logout()
    close()
  } catch (e) {
    console.error('[SettingsView] logout error:', e)
  }
}

function close() {
  stopWechatPolling()
  stopFeishuPolling()
  emit('close')
}
</script>

<style scoped>
@keyframes ao-spin { to { transform: rotate(360deg); } }
.ao-spin { width:10px;height:10px;flex:none;border:1.5px solid #D97706;border-top-color:transparent;border-radius:50%;animation:ao-spin 0.8s linear infinite;display:inline-block; }
.ao-spin-dark { border-color:#9CA3AF;border-top-color:transparent; }
.ao-spinner-lg { width:28px;height:28px;border:3px solid #E5E7EB;border-top-color:#9CA3AF;border-radius:50%;animation:ao-spin 0.8s linear infinite; }

/* ===== 通用页头封面 ===== */
.stg-hero { position:relative;aspect-ratio:3.2/1;max-height:240px;border:1px solid #EFEFEF;border-radius:14px;overflow:hidden;background:#F7F8FA;box-shadow:0 6px 18px rgba(17,24,39,0.05); }
.stg-hero-wide { aspect-ratio:3.6/1; }
.stg-hero-img { position:absolute;inset:0;width:100%;height:100%;object-fit:cover;object-position:center top; }
.stg-hero-img-plugin { object-position:center 55%; }
.stg-hero-img-auto { object-position:center 45%; }
.stg-hero-img-skills { top:0;bottom:auto;left:50%;right:auto;height:100%;width:auto;transform:translateX(-50%);object-fit:contain; }
.stg-hero::after { content:'';position:absolute;left:0;right:0;bottom:0;height:58%;background:linear-gradient(180deg,rgba(255,255,255,0) 0%,rgba(255,255,255,0.92) 32%,#FFFFFF 100%);pointer-events:none; }
.stg-hero-auto::after { height:46%;background:linear-gradient(180deg,rgba(255,255,255,0) 0%,rgba(255,255,255,0.62) 40%,rgba(255,255,255,0.9) 100%); }
.stg-hero-skills { background:#FFFFFF; }
.stg-hero-skills::after { height:34%;background:linear-gradient(180deg,rgba(255,255,255,0) 0%,rgba(255,255,255,0.72) 45%,#FFFFFF 100%); }
.stg-hero-body { position:absolute;left:18px;right:18px;bottom:13px;z-index:1;display:flex;flex-direction:column;gap:7px; }
@media (max-width:560px) { .stg-hero,.stg-hero-wide { aspect-ratio:auto;height:156px; } }

.stg-hero-title { margin:0;color:#1A1A1A;font-size:15px;font-weight: 500;letter-spacing:-0.01em; }
.stg-hero-desc { margin:0;color:#6B6B6B;font-size: 13px;line-height:1.5; }
.stg-hero-chips { display:flex;flex-wrap:wrap;gap:6px; }
.stg-chip { display:inline-flex;align-items:center;padding:3px 9px;border-radius:999px;background:#EEF3FE;color:#2F5BE0;font-size: 12px;font-weight:500; }

/* ===== 自动化办公 ===== */
.ao-page { display:flex;flex-direction:column;gap:16px;color:var(--rl-text-2); }

.ao-chip { display:inline-flex;align-items:center;gap:5px;padding:4px 10px;border-radius:999px;background:#F5F6F8;color:#9CA3AF;font-size: 12px;font-weight:500; }
.ao-chip-dot { width:5px;height:5px;border-radius:50%;background:#C7CBD1; }
.ao-chip-on { background:#ECFDF5;color:#0F7A55; }
.ao-chip-on .ao-chip-dot { background:#10B981;box-shadow:0 0 0 3px rgba(16,185,129,0.14); }
.ao-chip-wait { background:#FEF3C7;color:#B45309; }
.ao-chip-wait .ao-chip-dot { background:#F59E0B; }

.ao-channels { display:flex;flex-direction:column;gap:10px; }
.ao-channel { padding:14px 15px;border:1px solid #EFEFEF;border-radius:14px;background:#FFFFFF;transition:border-color 0.18s ease,box-shadow 0.18s ease,transform 0.18s ease; }
.ao-channel:hover { border-color:#DCE4F7;box-shadow:0 10px 26px rgba(59,108,246,0.08);transform:translateY(-1px); }
.ao-channel-top { display:flex;align-items:center;gap:12px; }
.ao-logo { width:40px;height:40px;flex:none;display:flex;align-items:center;justify-content:center;border-radius:12px;background:#F6F7F9;border:1px solid #F0F1F3; }
.ao-logo img { width:24px;height:24px;display:block; }
.ao-channel-copy { flex:1;min-width:0; }
.ao-channel-name { display:flex;align-items:center;gap:8px;color:#1A1A1A;font-size:14px;font-weight: 500;line-height:1.35; }
.ao-channel-desc { margin-top:2px;color:#9A9A9A;font-size: 13px;line-height:1.5;word-break:break-all; }
.ao-channel-actions { display:flex;gap:8px;flex:none; }

.ao-status { display:inline-flex;align-items:center;gap:5px;padding:2px 8px;border-radius:999px;background:#F5F6F8;color:#9CA3AF;font-size:11px;font-weight:500; }
.ao-status-dot { width:5px;height:5px;border-radius:50%;background:#10B981;box-shadow:0 0 0 3px rgba(16,185,129,0.14); }
.ao-status-on { background:#ECFDF5;color:#0F7A55; }
.ao-status-wait { background:#FEF3C7;color:#B45309; }

.ao-btn { display:inline-flex;align-items:center;justify-content:center;gap:5px;padding:7px 14px;border:1px solid transparent;border-radius:9px;font-size: 13px;font-weight:500;line-height:1;cursor:pointer;white-space:nowrap;transition:background 0.15s ease,color 0.15s ease,box-shadow 0.15s ease,opacity 0.15s ease; }
.ao-btn:disabled { cursor:not-allowed;opacity:0.55; }
.ao-btn-primary { background:#3B6CF6;color:#FFFFFF;box-shadow:0 3px 10px rgba(59,108,246,0.22); }
.ao-btn-primary:hover:not(:disabled) { background:#2F5BE0;box-shadow:0 5px 14px rgba(59,108,246,0.28); }
.ao-btn-ghost { background:#F5F6F8;color:#4B4B4B; }
.ao-btn-ghost:hover:not(:disabled) { background:#ECEFF3;color:#1A1A1A; }
.ao-btn-text { background:transparent;color:#9CA3AF; }
.ao-btn-text:hover:not(:disabled) { color:#EF4444; }

.ao-steps { display:flex;flex-direction:column;gap:8px;margin-top:12px;padding-top:12px;border-top:1px dashed #EFEFEF; }
.ao-step { display:flex;align-items:center;gap:10px;padding:9px 11px;border-radius:11px;background:#FAFAFB;border:1px solid #F3F4F6;transition:background 0.15s ease,border-color 0.15s ease; }
.ao-step:hover { background:#F7F9FE;border-color:#E7EDFB; }
.ao-step-idx { width:20px;height:20px;flex:none;display:flex;align-items:center;justify-content:center;border-radius:50%;background:#EEF3FE;color:#3B6CF6;font-size:11px;font-weight: 500; }
.ao-step-done .ao-step-idx { background:#ECFDF5;color:#0F7A55; }
.ao-step-copy { flex:1;min-width:0; }
.ao-step-title { color:#1A1A1A;font-size: 13px;font-weight:500; }
.ao-step-desc { margin-top:1px;color:#9A9A9A;font-size: 12px;line-height:1.45;word-break:break-all; }

.ao-qr { display:flex;align-items:center;gap:16px;margin-top:12px;padding:14px;border-radius:13px;border:1px dashed #D8E2FB;background:linear-gradient(135deg,#F6F9FF,#FBFDFF); }
.ao-qr-img { width:150px;height:150px;flex:none;border-radius:11px;border:1px solid #E5E7EB;background:#FFFFFF; }
.ao-qr-copy { min-width:0; }
.ao-qr-title { margin:0;color:#1A1A1A;font-size:13px;font-weight: 500; }
.ao-qr-desc { margin:4px 0 0;color:#6B7280;font-size:12px;line-height:1.55; }
.ao-qr-tip { display:flex;align-items:center;gap:6px;margin-top:10px;color:#9CA3AF;font-size: 12px; }

.ao-coming { padding:12px 14px;border:1px dashed #E5E7EB;border-radius:14px;background:#FCFCFD; }
.ao-coming-title { margin-bottom:8px;color:#9A9A9A;font-size:11px;font-weight: 500;letter-spacing:0.08em; }
.ao-coming-grid { display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px; }
.ao-coming-card { display:flex;align-items:center;gap:10px;padding:10px 12px;border:1px solid #EFEFEF;border-radius:11px;background:#FFFFFF;opacity:0.7; }
.ao-coming-logo { width:30px;height:30px;flex:none;display:flex;align-items:center;justify-content:center;border-radius:9px;background:#F3F4F6;color:#9CA3AF;font-size: 11px;font-weight: 500; }
.ao-coming-name { color:#4B4B4B;font-size: 13px;font-weight:500; }
.ao-coming-desc { margin-top:1px;color:#9CA3AF;font-size:11px; }

.ao-fade-enter-active,
.ao-fade-leave-active { transition:opacity 0.22s ease,transform 0.22s ease; }
.ao-fade-enter-from,
.ao-fade-leave-to { opacity:0;transform:translateY(-6px); }
</style>