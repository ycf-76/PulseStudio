<template>
<AppShell current-page="task-plans">
    <main class="da-page">
      <div class="da-content-card-wrapper">
      <div class="da-content-card">

        <div class="da-topbar">
          <div class="da-topbar-main">
            <h1 class="da-topbar-title">任务清单</h1>
            <p v-if="plans.length" class="da-topbar-sub">{{ activeCount }} 个进行中 · 共 {{ plans.length }} 个清单</p>
            <p v-else class="da-topbar-sub">一句话意图，逐日自动写图文并定时发布</p>
          </div>
          <div class="da-topbar-actions">
            <button class="tp-btn tp-btn-primary" @click="openCreate"><Plus :size="14" :stroke-width="2" /> 新建清单</button>
            <button class="tp-btn tp-btn-ghost" @click="loadPlans" :disabled="loading"><RefreshCw :size="14" :stroke-width="2" :class="{ 'tp-spin': loading }" /> 刷新</button>
          </div>
        </div>

      <div class="tp-container">

        <div v-if="plans.length === 0 && !loading" class="tp-empty-state">
          <div class="tp-empty-icon-wrap">
            <svg width="48" height="48" viewBox="0 0 48 48" fill="none"><rect x="6" y="8" width="36" height="34" rx="4" stroke="#D4D4D8" stroke-width="2"/><path d="M6 18h36M15 4v8M33 4v8" stroke="#D4D4D8" stroke-width="2" stroke-linecap="round"/><path d="M17 28l4 4 8-8" stroke="#2563EB" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/></svg>
          </div>
          <p class="tp-empty-title">还没有定时任务清单</p>
          <p class="tp-empty-desc">发布一句话意图，例如「10 个单词每天发一个」，系统逐日自动写图文并定时发布</p>
          <button class="tp-btn tp-btn-primary" @click="openCreate"><Plus :size="14" :stroke-width="2" /> 新建清单</button>
          <div class="tp-empty-examples">
            <button v-for="ex in INTENT_EXAMPLES" :key="ex.title" class="tp-example-chip" @click="useExample(ex)">
              <span class="tp-example-chip-title">{{ ex.title }}</span>
              <span class="tp-example-chip-desc">{{ ex.desc }}</span>
            </button>
          </div>
        </div>

        <template v-else>

          <!-- ====== 概览面板 ====== -->
          <section v-if="plans.length > 0" class="tp-overview">
            <div class="tp-overview-hero">
              <div class="tp-ring-wrap">
                <svg viewBox="0 0 80 80" class="tp-ring-svg">
                  <defs>
                    <linearGradient id="tpRingGrad" x1="0%" y1="0%" x2="100%" y2="100%">
                      <stop offset="0%" stop-color="#60A5FA" />
                      <stop offset="100%" stop-color="#2563EB" />
                    </linearGradient>
                  </defs>
                  <circle class="tp-ring-track" cx="40" cy="40" r="32" />
                  <circle
                    class="tp-ring-fill"
                    cx="40" cy="40" r="32"
                    :stroke-dasharray="RING_C"
                    :stroke-dashoffset="RING_C * (1 - overallPercent / 100)"
                  />
                </svg>
                <div class="tp-ring-center">
                  <b>{{ overallPercent }}<i>%</i></b>
                  <span>整体完成度</span>
                </div>
              </div>
              <div class="tp-overview-copy">
                <h2>发布进度总览</h2>
                <p>已发布 <b>{{ totalPublishedDays }}</b> 天，剩余 <b>{{ totalPendingDays }}</b> 天待执行</p>
                <div class="tp-legend">
                  <span><i class="tp-legend-dot tp-legend-published"></i>已发布 {{ totalPublishedDays }}</span>
                  <span><i class="tp-legend-dot tp-legend-pending"></i>待执行 {{ totalPendingDays }}</span>
                  <span><i class="tp-legend-dot tp-legend-failed"></i>失败 {{ totalFailedDays }}</span>
                </div>
              </div>
            </div>

            <div class="tp-stat-grid">
              <div class="tp-stat">
                <div class="tp-stat-icon"><ListChecks :size="16" :stroke-width="2" /></div>
                <div class="tp-stat-body"><span class="tp-stat-val">{{ plans.length }}</span><span class="tp-stat-label">清单总数</span></div>
              </div>
              <div class="tp-stat">
                <div class="tp-stat-icon"><Activity :size="16" :stroke-width="2" /></div>
                <div class="tp-stat-body"><span class="tp-stat-val">{{ activeCount }}</span><span class="tp-stat-label">进行中</span></div>
              </div>
              <div class="tp-stat">
                <div class="tp-stat-icon"><Hourglass :size="16" :stroke-width="2" /></div>
                <div class="tp-stat-body"><span class="tp-stat-val">{{ totalPendingDays }}</span><span class="tp-stat-label">待执行天数</span></div>
              </div>
              <div class="tp-stat" :class="{ 'tp-stat-warn': totalFailedDays > 0 }">
                <div class="tp-stat-icon"><AlertTriangle :size="16" :stroke-width="2" /></div>
                <div class="tp-stat-body"><span class="tp-stat-val">{{ totalFailedDays }}</span><span class="tp-stat-label">失败天数</span></div>
              </div>
            </div>
          </section>

          <!-- ====== 清单工具栏 ====== -->
          <div v-if="!loading && plans.length > 0" class="tp-list-head">
            <div class="tp-list-head-left">
              <h2 class="tp-list-title">全部清单</h2>
              <span class="tp-list-count">{{ filteredPlans.length }}</span>
            </div>
            <div class="tp-status-filter">
              <button
                v-for="f in STATUS_FILTERS"
                :key="f.key"
                class="tp-filter-btn"
                :class="{ 'tp-filter-btn-active': statusFilter === f.key }"
                @click="statusFilter = f.key"
              >{{ f.label }}<span class="tp-filter-count">{{ statusCount(f.key) }}</span></button>
            </div>
          </div>

          <!-- ====== 骨架屏 ====== -->
          <div v-if="loading" class="tp-plans-grid">
            <div v-for="n in 4" :key="n" class="tp-plan-card tp-skeleton-card">
              <div class="tp-sk-line tp-sk-title"></div>
              <div class="tp-sk-line tp-sk-meta"></div>
              <div class="tp-sk-line tp-sk-track"></div>
              <div class="tp-sk-line tp-sk-foot"></div>
            </div>
          </div>

          <!-- ====== 清单卡片 ====== -->
          <div v-else-if="filteredPlans.length > 0" class="tp-plans-grid">
            <article
              v-for="p in filteredPlans"
              :key="p.id"
              class="tp-plan-card"
              :class="'tp-plan-' + p.status"
              @click="openDetail(p.id)"
            >
              <div class="tp-plan-card-head">
                <span class="tp-status-badge" :class="'tp-status-' + p.status">{{ statusLabel(p.status) }}</span>
                <div class="tp-plan-actions" @click.stop>
                  <button v-if="p.status === 'active'" class="tp-icon-btn" title="暂停" @click="handlePause(p)"><Pause :size="15" :stroke-width="2" /></button>
                  <button v-if="p.status === 'paused'" class="tp-icon-btn tp-icon-btn-accent" title="恢复" @click="handleResume(p)"><Play :size="15" :stroke-width="2" /></button>
                  <button v-if="p.status === 'active' || p.status === 'paused'" class="tp-icon-btn tp-icon-btn-danger" title="取消清单" @click="handleCancel(p)"><X :size="15" :stroke-width="2" /></button>
                </div>
              </div>

              <h3 class="tp-plan-title" :title="p.title">{{ p.title || '未命名清单' }}</h3>

              <div class="tp-plan-meta">
                <span class="tp-meta-chip"><Clock :size="12" :stroke-width="2" />{{ p.plan_config?.daily_time || '09:00' }}</span>
                <span class="tp-meta-chip"><CalendarDays :size="12" :stroke-width="2" />{{ p.total_days }} 天</span>
                <span class="tp-meta-chip"><ShieldCheck :size="12" :stroke-width="2" />{{ reviewModeLabel(p.plan_config?.review_mode) }}</span>
                <span class="tp-meta-chip"><MousePointerClick :size="12" :stroke-width="2" />{{ p.plan_config?.publish_strategy === 'auto' ? '自动发布' : '半自动' }}</span>
              </div>

              <div class="tp-plan-progress">
                <div class="tp-day-track">
                  <span v-for="(seg, i) in daySegments(p)" :key="i" class="tp-day-seg" :class="'tp-seg-' + seg"></span>
                </div>
                <div class="tp-progress-line">
                  <span class="tp-progress-num">{{ progressPercent(p) }}%</span>
                  <span class="tp-progress-text">已发布 {{ p.published_days }}/{{ p.total_days }}<template v-if="p.failed_days"> · 失败 {{ p.failed_days }}</template></span>
                </div>
              </div>

              <div class="tp-plan-foot">
                <span class="tp-plan-created">{{ formatDate(p.created_at) }}</span>
                <span class="tp-plan-open">查看详情 <ArrowRight :size="13" :stroke-width="2" /></span>
              </div>
            </article>
          </div>

          <!-- ====== 筛选空态 ====== -->
          <div v-else class="tp-filter-empty">
            <p>该状态下暂无清单</p>
            <button class="tp-btn tp-btn-ghost" @click="statusFilter = 'all'">查看全部</button>
          </div>

        </template>

      </div>
      </div>
      </div>
    </main>

    <template #overlay>
  <!-- ===== 新建清单抽屉 ===== -->
  <div class="tp-drawer-overlay" v-if="createVisible" @click.self="closeCreate">
    <div class="tp-drawer tp-drawer-wide">
      <div class="tp-drawer-header">
        <h2>新建定时任务清单</h2>
        <button class="tp-drawer-close" @click="closeCreate"><X :size="16" :stroke-width="2" /></button>
      </div>
      <div class="tp-drawer-body">

        <p class="tp-hint">用一句话或多行清单描述发布意图，系统拆解成逐日任务：每天到点自动写图文并发小红书。</p>

        <div class="tp-form-row">
          <label class="tp-form-label">发布意图</label>
          <textarea v-model="createForm.intent" rows="6" class="tp-textarea" placeholder="示例：&#10;背单词计划：ability, benefit, culture, develop, economy, factor...&#10;每天发一个单词的记忆卡笔记，早上 9 点发布"></textarea>
        </div>

        <div class="tp-form-grid">
          <div class="tp-form-row">
            <label class="tp-form-label">每日发布时间</label>
            <input v-model="createForm.dailyTime" type="time" class="tp-input" />
          </div>
          <div class="tp-form-row">
            <label class="tp-form-label">账号</label>
            <select v-model="createForm.accountId" class="tp-select">
              <option value="">默认账号</option>
            </select>
          </div>
        </div>

        <div class="tp-form-grid">
          <div class="tp-form-row">
            <label class="tp-form-label">审核方式</label>
            <select v-model="createForm.reviewMode" class="tp-select">
              <option value="quality_gate">质量门 · AI 审核不达标转人工（推荐）</option>
              <option value="auto">全自动 · 不打断</option>
              <option value="manual">人工 · 每次发飞书卡片确认</option>
            </select>
          </div>
          <div class="tp-form-row">
            <label class="tp-form-label">发布方式</label>
            <select v-model="createForm.publishStrategy" class="tp-select">
              <option value="manual">半自动 · 到点填好内容，手动点发布</option>
              <option value="auto">自动点击 · 实验性，失败自动降级半自动</option>
            </select>
          </div>
        </div>

        <div class="tp-form-actions">
          <button class="tp-btn tp-btn-primary" @click="handleDecompose(false)" :disabled="decomposing || !createForm.intent.trim()">
            <Sparkles :size="14" :stroke-width="2" /> {{ decomposing ? '拆解中...' : 'AI 拆解' }}
          </button>
          <button class="tp-btn tp-btn-ghost" @click="handleDecompose(true)" :disabled="!createForm.intent.trim()" title="按行切分意图，不调用大模型，不消耗 Token">
            <FileText :size="14" :stroke-width="2" /> 规则拆解（零 Token 测试）
          </button>
        </div>

        <!-- 拆解结果：可编辑预览 -->
        <template v-if="preview">
          <div class="tp-preview-head">
            <h3>拆解预览（共 {{ preview.items.length }} 天，可编辑）</h3>
            <span v-if="previewTitle !== preview.title" class="tp-preview-title-hint">标题：{{ preview.title }}</span>
          </div>
          <div class="tp-preview-warnings" v-if="preview.warnings?.length">
            <div v-for="(w, i) in preview.warnings" :key="i" class="tp-warning-item"><AlertTriangle :size="13" :stroke-width="2" /> {{ w }}</div>
          </div>
          <div class="tp-preview-table">
            <div class="tp-pt-row tp-pt-header">
              <span class="tp-pt-day">天</span>
              <span class="tp-pt-topic">当天主题（可编辑）</span>
              <span class="tp-pt-keyword">关键词（可编辑）</span>
            </div>
            <div class="tp-pt-row" v-for="(it, idx) in preview.items" :key="idx">
              <span class="tp-pt-day">D{{ it.day_index }}</span>
              <input class="tp-pt-input tp-pt-topic-input" v-model="it.topic" placeholder="当天发布主题" />
              <input class="tp-pt-input" v-model="it.keyword" placeholder="搜索关键词（可选）" />
            </div>
          </div>
          <div class="tp-form-actions">
            <button class="tp-btn tp-btn-primary" @click="handleCreate" :disabled="creating || !validPreview">
              <Check :size="14" :stroke-width="2" /> {{ creating ? '创建中...' : '确认创建并生效' }}
            </button>
            <button class="tp-btn tp-btn-ghost" @click="preview = null">重新拆解</button>
          </div>
        </template>

      </div>
    </div>
  </div>

  <!-- ===== 清单详情弹窗 ===== -->
  <div class="tp-modal-overlay" v-if="detailVisible" @click.self="closeDetail">
    <div class="tp-modal" v-if="detail">
      <div class="tp-modal-head">
        <div class="tp-modal-head-main">
          <div class="tp-modal-title-row">
            <h2 class="tp-modal-title">{{ detail.title || '清单详情' }}</h2>
            <span class="tp-status-badge" :class="'tp-status-' + detail.status">{{ statusLabel(detail.status) }}</span>
          </div>
          <div class="tp-modal-sub">
            <span class="tp-meta-item"><Clock :size="13" :stroke-width="2" /> 每日 {{ detail.plan_config?.daily_time || '09:00' }}</span>
            <span class="tp-meta-item"><CalendarDays :size="13" :stroke-width="2" /> 共 {{ detail.total_days }} 天</span>
            <span class="tp-meta-item"><ShieldCheck :size="13" :stroke-width="2" /> {{ reviewModeLabel(detail.plan_config?.review_mode) }}</span>
          </div>
        </div>
        <div class="tp-modal-head-actions">
          <button v-if="detail.status === 'active'" class="tp-btn tp-btn-sm" @click="handlePause(detail); refreshDetail()"><Pause :size="13" :stroke-width="2" /> 暂停</button>
          <button v-if="detail.status === 'paused'" class="tp-btn tp-btn-sm tp-btn-primary" @click="handleResume(detail); refreshDetail()"><Play :size="13" :stroke-width="2" /> 恢复</button>
          <button v-if="detail.status === 'active' || detail.status === 'paused'" class="tp-btn tp-btn-sm tp-btn-ghost" @click="handleCancel(detail); refreshDetail()"><X :size="13" :stroke-width="2" /> 取消</button>
          <button class="tp-modal-close" title="关闭" @click="closeDetail"><X :size="16" :stroke-width="2" /></button>
        </div>
      </div>

      <div class="tp-modal-body">

        <div class="tp-detail-overview">
          <div class="tp-detail-overview-head">
            <h3>进度总览</h3>
            <span class="tp-detail-overview-sub">已发布 {{ detail.published_days }}/{{ detail.total_days }} 天<template v-if="detail.failed_days"> · 失败 {{ detail.failed_days }} 天</template></span>
          </div>
          <div class="tp-detail-dots">
            <span
              v-for="it in detail.items"
              :key="it.id"
              class="tp-detail-dot"
              :class="'tp-dot-' + it.status"
              :title="`Day ${it.day_index} · ${itemStatusLabel(it.status)}`"
            ></span>
          </div>
          <div class="tp-detail-legend">
            <span><i class="tp-legend-dot tp-legend-published"></i>已发布</span>
            <span><i class="tp-legend-dot tp-legend-running"></i>执行中 / 待确认</span>
            <span><i class="tp-legend-dot tp-legend-failed"></i>失败</span>
            <span><i class="tp-legend-dot tp-legend-pending"></i>待执行</span>
          </div>
        </div>

        <div class="tp-modal-grid">
          <aside class="tp-modal-aside">
            <h3 class="tp-detail-title">原始意图</h3>
            <pre class="rl-plain">{{ detail.intent_text }}</pre>
          </aside>

          <section class="tp-modal-main">
            <h3 class="tp-detail-title">逐日安排</h3>
            <div class="tp-timeline">
              <div v-for="it in detail.items" :key="it.id" class="tp-tl-item" :class="'tp-tl-' + it.status">
                <div class="tp-tl-marker"></div>
                <div class="tp-tl-body">
                  <div class="tp-tl-head">
                    <span class="tp-tl-day">Day {{ it.day_index }}</span>
                    <span class="tp-item-status" :class="'tp-item-' + it.status">{{ itemStatusLabel(it.status) }}</span>
                    <span class="tp-tl-time">{{ formatDateTime(it.plan_time) }}</span>
                  </div>
                  <div class="tp-tl-topic">{{ it.topic }}</div>
                  <div class="tp-tl-sub" v-if="it.keyword">关键词：{{ it.keyword }}</div>
                  <div class="tp-tl-error" v-if="it.last_error?.reason">
                    <AlertTriangle :size="12" :stroke-width="2" /> {{ it.last_error.reason }}
                    <span v-if="it.retry_count > 0">（已重试 {{ it.retry_count }} 次）</span>
                  </div>
                  <div class="tp-tl-sub tp-tl-confirm" v-if="it.latest_run?.status === 'awaiting_confirmation'">
                    <Bell :size="12" :stroke-width="2" /> 已发飞书卡片等待确认（确认 / 打回 / 跳过）
                  </div>
                </div>
              </div>
            </div>
          </section>
        </div>

      </div>
    </div>
  </div>
    </template>
</AppShell>

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

</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import {
  Plus, RefreshCw, Clock, CalendarDays, ShieldCheck, MousePointerClick,
  Pause, Play, X, Sparkles, FileText, Check, AlertTriangle, Bell,
  ArrowRight, Activity, Hourglass, ListChecks,
} from 'lucide-vue-next'
import AppShell from '@/components/layout/AppShell.vue'
import { useAccountStore } from '@/stores/account'
import {
  listTaskPlans, getTaskPlanDetail, decomposeIntent, createTaskPlan,
  pauseTaskPlan, resumeTaskPlan, cancelTaskPlan,
  type TaskPlanSummary, type TaskPlanDetail, type DecomposeResult,
} from '@/api/taskPlan'
import { useConfirm } from '@/composables/useConfirm'
import ConfirmDialog from '@/components/common/ConfirmDialog.vue'

const { state: confirmState, confirm, onConfirm, onCancel } = useConfirm()
const accountStore = useAccountStore()

// ---------- 状态 ----------
const plans = ref<TaskPlanSummary[]>([])
const loading = ref(false)

const createVisible = ref(false)
const decomposing = ref(false)
const creating = ref(false)
const preview = ref<DecomposeResult | null>(null)

const detailVisible = ref(false)
const detail = ref<TaskPlanDetail | null>(null)

const STATUS_FILTERS = [
  { key: 'all', label: '全部' },
  { key: 'active', label: '进行中' },
  { key: 'paused', label: '已暂停' },
  { key: 'completed', label: '已完成' },
  { key: 'cancelled', label: '已取消' },
] as const
type StatusFilterKey = typeof STATUS_FILTERS[number]['key']
const statusFilter = ref<StatusFilterKey>('all')

const INTENT_EXAMPLES = [
  { title: '背单词计划', desc: '10 个单词每天发一个', intent: '背单词计划：ability, benefit, culture, develop, economy...\n每天发一个单词的记忆卡笔记，早上 9 点发布' },
  { title: '每日一句英文', desc: '连续 30 天打卡', intent: '每日分享一句地道英文表达，配中文释义和例句，连续 30 天，早上 8 点发布' },
  { title: '健身打卡', desc: '21 天习惯养成', intent: '21 天健身打卡计划，每天分享一个基础动作和训练建议，晚上 7 点发布' },
]

const RING_C = 2 * Math.PI * 32

const createForm = ref({
  intent: '',
  dailyTime: '09:00',
  accountId: '',
  reviewMode: 'quality_gate' as 'quality_gate' | 'auto' | 'manual',
  publishStrategy: 'manual' as 'manual' | 'auto',
})

// ---------- 计算属性 ----------
const activeCount = computed(() => plans.value.filter(p => p.status === 'active').length)
const totalPublishedDays = computed(() => plans.value.reduce((s, p) => s + (p.published_days || 0), 0))
const totalFailedDays = computed(() => plans.value.reduce((s, p) => s + (p.failed_days || 0), 0))
const totalPendingDays = computed(() =>
  plans.value.filter(p => p.status === 'active').reduce((s, p) => s + Math.max(0, p.total_days - p.published_days - p.failed_days), 0),
)
const totalDays = computed(() => plans.value.reduce((s, p) => s + (p.total_days || 0), 0))
const overallPercent = computed(() =>
  totalDays.value ? Math.round((totalPublishedDays.value / totalDays.value) * 100) : 0,
)

const STATUS_ORDER: Record<string, number> = { active: 0, paused: 1, completed: 2, cancelled: 3 }
const filteredPlans = computed(() => {
  const list = statusFilter.value === 'all'
    ? [...plans.value]
    : plans.value.filter(p => p.status === statusFilter.value)
  return list.sort((a, b) => {
    const byStatus = (STATUS_ORDER[a.status] ?? 9) - (STATUS_ORDER[b.status] ?? 9)
    if (byStatus !== 0) return byStatus
    return String(b.created_at || '').localeCompare(String(a.created_at || ''))
  })
})

function statusCount(key: StatusFilterKey) {
  if (key === 'all') return plans.value.length
  return plans.value.filter(p => p.status === key).length
}

const previewTitle = computed(() => preview.value?.title || '')
const validPreview = computed(() =>
  !!preview.value && preview.value.items.length > 0 &&
  preview.value.items.every(it => it.topic.trim().length > 0),
)

// ---------- 标签 ----------
const STATUS_LABELS: Record<string, string> = { active: '进行中', paused: '已暂停', completed: '已完成', cancelled: '已取消' }
function statusLabel(s: string) { return STATUS_LABELS[s] || s }

const ITEM_STATUS_LABELS: Record<string, string> = {
  pending: '待发布', running: '执行中', published: '已发布',
  failed: '失败', skipped: '已跳过', cancelled: '已取消',
}
function itemStatusLabel(s: string) { return ITEM_STATUS_LABELS[s] || s }

function reviewModeLabel(mode?: string) {
  const m: Record<string, string> = { quality_gate: '质量门审核', auto: '全自动', manual: '人工确认' }
  return m[mode || 'quality_gate'] || mode || '质量门审核'
}

function progressPercent(p: TaskPlanSummary) {
  if (!p.total_days) return 0
  return Math.round(((p.published_days + p.failed_days) / p.total_days) * 100)
}

function daySegments(p: TaskPlanSummary): string[] {
  const total = Math.min(p.total_days || 0, 60)
  if (total <= 0) return []
  const published = Math.min(p.published_days || 0, total)
  const failed = Math.min(p.failed_days || 0, total - published)
  return Array.from({ length: total }, (_, i) => {
    if (i < published) return 'published'
    if (i < published + failed) return 'failed'
    return 'pending'
  })
}

function formatDateTime(iso: string | null) {
  if (!iso) return '—'
  const d = new Date(iso)
  if (isNaN(d.getTime())) return iso
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getMonth() + 1}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function formatDate(iso: string | null) {
  if (!iso) return '—'
  const d = new Date(iso)
  if (isNaN(d.getTime())) return '—'
  const pad = (n: number) => String(n).padStart(2, '0')
  return `创建于 ${d.getMonth() + 1}-${pad(d.getDate())}`
}

// ---------- 列表 ----------
async function loadPlans() {
  loading.value = true
  try {
    plans.value = await listTaskPlans()
  } catch (e: any) {
    console.error('[TaskPlan] load plans failed:', e)
  } finally {
    loading.value = false
  }
}

// ---------- 创建流 ----------
function openCreate() {
  createVisible.value = true
  preview.value = null
  if (!accountStore.accounts.length) accountStore.fetchAccounts()
}
function useExample(ex: { title: string; desc: string; intent: string }) {
  createForm.value.intent = ex.intent
  openCreate()
}
function closeCreate() {
  createVisible.value = false
  preview.value = null
}

async function handleDecompose(dryRun: boolean) {
  const intent = createForm.value.intent.trim()
  if (!intent) return
  decomposing.value = true
  try {
    preview.value = await decomposeIntent(intent, createForm.value.dailyTime, dryRun)
    if (dryRun && !preview.value.items.length) preview.value = null
  } catch (e: any) {
    console.error('[TaskPlan] decompose failed:', e)
    alert(e.response?.data?.detail || e.response?.data?.message || '拆解失败，请稍后重试')
  } finally {
    decomposing.value = false
  }
}

async function handleCreate() {
  if (!preview.value || !validPreview.value) return
  creating.value = true
  try {
    await createTaskPlan({
      intent_text: createForm.value.intent.trim(),
      title: createForm.value.intent.trim().split('\n')[0].slice(0, 100),
      account_id: createForm.value.accountId || null,
      items: preview.value.items
        .filter(it => it.topic.trim())
        .map((it, i) => ({
          day_index: i + 1,
          topic: it.topic.trim(),
          keyword: it.keyword?.trim() || null,
        })),
      plan_config: {
        daily_time: createForm.value.dailyTime,
        review_mode: createForm.value.reviewMode,
        publish_strategy: createForm.value.publishStrategy,
      },
    })
    closeCreate()
    createForm.value.intent = ''
    await loadPlans()
  } catch (e: any) {
    console.error('[TaskPlan] create failed:', e)
    alert(e.response?.data?.detail || e.response?.data?.message || '创建失败，请稍后重试')
  } finally {
    creating.value = false
  }
}

// ---------- 生命周期 ----------
async function handlePause(p: TaskPlanSummary | TaskPlanDetail) {
  try {
    await pauseTaskPlan(p.id)
    await loadPlans()
  } catch (e: any) {
    alert(e.response?.data?.detail || '暂停失败')
  }
}
async function handleResume(p: TaskPlanSummary | TaskPlanDetail) {
  try {
    await resumeTaskPlan(p.id)
    await loadPlans()
  } catch (e: any) {
    alert(e.response?.data?.detail || '恢复失败')
  }
}
async function handleCancel(p: TaskPlanSummary | TaskPlanDetail) {
  const ok = await confirm({
    title: '取消任务清单',
    message: `确定取消清单「${p.title}」？\n未执行的条目将全部取消，不可恢复。`,
    danger: true,
  })
  if (!ok) return
  try {
    await cancelTaskPlan(p.id)
    await loadPlans()
    if (detail.value?.id === p.id) await refreshDetail()
  } catch (e: any) {
    alert(e.response?.data?.detail || '取消失败')
  }
}

// ---------- 详情 ----------
async function openDetail(planId: string) {
  detailVisible.value = true
  detail.value = null
  try {
    detail.value = await getTaskPlanDetail(planId)
  } catch (e: any) {
    console.error('[TaskPlan] load detail failed:', e)
    detailVisible.value = false
  }
}
function closeDetail() {
  detailVisible.value = false
  detail.value = null
}
async function refreshDetail() {
  if (!detail.value) return
  try {
    detail.value = await getTaskPlanDetail(detail.value.id)
  } catch { /* 静默 */ }
}

onMounted(() => {
  loadPlans()
})
</script>

<style scoped>
.da-page { flex: 1; min-width: 0; height: 100%; display: flex; flex-direction: column; overflow: hidden; padding: 28px 20px 16px; }
.da-content-card-wrapper { display: flex; flex-direction: column; flex: 1; min-height: 0; min-width: 0; overflow: hidden; }
.da-content-card { flex: 1; min-height: 0; min-width: 0; background: #FFFFFF; border-radius:20px; border: 1px solid var(--ma-border-default, #EDEDED); box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04); display: flex; flex-direction: column; overflow: hidden; }
.da-topbar { display: flex; align-items: flex-end; justify-content: space-between; gap: 20px; flex-wrap: wrap; padding: 22px 24px 16px; flex-shrink: 0; background: transparent; }
.da-topbar-main { display: flex; flex-direction: column; gap: 6px; min-width: 0; }
.da-topbar-title { font-size: 18px; font-weight: 500; margin: 0; color: var(--rl-text-1); letter-spacing: -0.2px; }
.da-topbar-sub { margin: 0; font-size: 13px; color: #9A9A9A; }
.da-topbar-actions { display: flex; gap: 8px; flex-shrink: 0; }

/* ---------- 按钮（蓝色主色，线框风格图标） ---------- */
.tp-btn { display: inline-flex; align-items: center; gap: 6px; padding: 7px 14px; border-radius:12px; cursor: pointer; font-size: 13px; border: 1px solid #EDEDED; background: #fff; color: var(--rl-text-2); transition: all 0.15s; white-space: nowrap; }
.tp-btn:hover { border-color: #93C5FD; background: #F8FAFF; color: #1D4ED8; }
.tp-btn-primary { background: #2563EB; color: #fff; border-color: #2563EB; }
.tp-btn-primary:hover { background: #1D4ED8; border-color: #1D4ED8; color: #fff; }
.tp-btn-ghost { background: transparent; }
.tp-btn-ghost:hover { background: #F5F5F5; }
.tp-btn-sm { padding: 4px 10px; font-size: 12px; }
.tp-btn:disabled { opacity: 0.4; cursor: not-allowed; }
.tp-spin { animation: tp-rotate 1s linear infinite; }
@keyframes tp-rotate { to { transform: rotate(360deg); } }

/* ---------- 容器 ---------- */
.tp-container { flex: 1; overflow-y: auto; padding: 4px 24px 24px; }

/* ---------- 空态 ---------- */
.tp-empty-state { display: flex; flex-direction: column; align-items: center; justify-content: center; padding: 80px 20px; gap: 6px; }
.tp-empty-icon-wrap { margin-bottom: 10px; }
.tp-empty-title { font-size: 14px; font-weight: 500; color: var(--rl-text-2); margin: 0; }
.tp-empty-desc { font-size: 13px; color: #9A9A9A; margin: 0 0 16px; text-align: center; max-width: 420px; line-height: 1.6; }
.tp-empty-examples { display: flex; flex-wrap: wrap; justify-content: center; gap: 8px; margin-top: 18px; max-width: 620px; }
.tp-example-chip { display: flex; flex-direction: column; align-items: flex-start; gap: 2px; padding: 8px 12px; border: 1px solid #EDEDED; border-radius: 10px; background: #fff; cursor: pointer; text-align: left; transition: border-color 0.15s, background 0.15s, transform 0.15s; }
.tp-example-chip:hover { border-color: #BFD4FE; background: #F8FAFF; transform: translateY(-1px); }
.tp-example-chip-title { font-size: 12px; font-weight: 500; color: var(--rl-text-1); }
.tp-example-chip-desc { font-size: 11px; color: #9A9A9A; }

/* ---------- 概览面板 ---------- */
.tp-overview { display: grid; grid-template-columns: minmax(260px, 0.9fr) 1.5fr; gap: 14px; padding: 14px; margin-bottom: 22px; border: 1px solid #ECEEF2; border-radius: 18px; background: linear-gradient(135deg, #F7F9FF 0%, #FCFCFD 55%, #F8F8FA 100%); }
.tp-overview-hero { display: flex; align-items: center; gap: 16px; padding: 4px 10px 4px 4px; min-width: 0; }
.tp-ring-wrap { position: relative; width: 92px; height: 92px; flex-shrink: 0; }
.tp-ring-svg { width: 100%; height: 100%; transform: rotate(-90deg); }
.tp-ring-track { fill: none; stroke: #E6EAF2; stroke-width: 8; }
.tp-ring-fill { fill: none; stroke: url(#tpRingGrad); stroke-width: 8; stroke-linecap: round; transition: stroke-dashoffset 0.6s cubic-bezier(0.16, 1, 0.3, 1); }
.tp-ring-center { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 1px; }
.tp-ring-center b { font-size: 18px; font-weight: 500; color: var(--rl-text-1); line-height: 1; letter-spacing: -0.3px; }
.tp-ring-center b i { font-size: 12px; font-style: normal; font-weight: 500; color: #6B7280; margin-left: 1px; }
.tp-ring-center span { font-size: 10px; color: #9A9A9A; }
.tp-overview-copy { min-width: 0; display: flex; flex-direction: column; gap: 6px; }
.tp-overview-copy h2 { margin: 0; font-size: 14px; font-weight: 500; color: var(--rl-text-1); }
.tp-overview-copy p { margin: 0; font-size: 12px; color: #6B7280; line-height: 1.5; }
.tp-overview-copy p b { color: #2563EB; font-weight: 500; }
.tp-legend { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 2px; }
.tp-legend > span { display: inline-flex; align-items: center; gap: 5px; font-size: 11px; color: #6B7280; }
.tp-legend-dot { width: 7px; height: 7px; border-radius: 2px; display: inline-block; }
.tp-legend-published { background: #2563EB; }
.tp-legend-pending { background: #E4E7EE; }
.tp-legend-failed { background: #EF4444; }
.tp-legend-running { background: #F59E0B; }

.tp-stat-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; }
.tp-stat { display: flex; align-items: center; gap: 10px; padding: 10px 12px; background: #fff; border: 1px solid #ECEEF2; border-radius: 14px; transition: border-color 0.15s, box-shadow 0.15s; }
.tp-stat:hover { border-color: #DCE3F0; box-shadow: 0 2px 8px rgba(17, 24, 39, 0.04); }
.tp-stat-icon { width: 30px; height: 30px; border-radius: 10px; display: flex; align-items: center; justify-content: center; background: #F1F5FD; color: #2563EB; flex-shrink: 0; }
.tp-stat-body { display: flex; flex-direction: column; min-width: 0; }
.tp-stat-val { font-size: 18px; font-weight: 500; color: var(--rl-text-1); line-height: 1.2; }
.tp-stat-label { font-size: 11px; color: #9A9A9A; white-space: nowrap; }
.tp-stat-warn .tp-stat-icon { background: #FEF2F2; color: #DC2626; }
.tp-stat-warn .tp-stat-val { color: #DC2626; }

/* ---------- 清单工具栏 ---------- */
.tp-list-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; margin-bottom: 12px; }
.tp-list-head-left { display: flex; align-items: baseline; gap: 8px; }
.tp-list-title { margin: 0; font-size: 14px; font-weight: 500; color: var(--rl-text-1); }
.tp-list-count { font-size: 12px; color: #9A9A9A; }
.tp-status-filter { display: flex; gap: 2px; padding: 3px; background: #F5F5F7; border-radius: 10px; flex-wrap: wrap; }
.tp-filter-btn { display: inline-flex; align-items: center; gap: 5px; padding: 5px 10px; border: none; border-radius: 8px; background: transparent; color: #6B7280; font-size: 12px; cursor: pointer; transition: all 0.15s; white-space: nowrap; }
.tp-filter-btn:hover { color: var(--rl-text-1); background: rgba(255, 255, 255, 0.7); }
.tp-filter-btn-active { background: #fff; color: var(--rl-text-1); font-weight: 500; box-shadow: 0 1px 3px rgba(17, 24, 39, 0.08); }
.tp-filter-count { font-size: 10px; color: #9A9A9A; }
.tp-filter-btn-active .tp-filter-count { color: #2563EB; }

/* ---------- 清单卡片 ---------- */
.tp-plans-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 12px; }
.tp-plan-card { position: relative; display: flex; flex-direction: column; gap: 12px; padding: 16px 18px 14px; background: #fff; border: 1px solid #EDEDED; border-radius: 16px; cursor: pointer; overflow: hidden; transition: border-color 0.18s, box-shadow 0.18s, transform 0.18s; }
.tp-plan-card::before { content: ''; position: absolute; top: 0; left: 0; right: 0; height: 3px; background: #E4E7EE; }
.tp-plan-active::before { background: linear-gradient(90deg, #60A5FA, #2563EB); }
.tp-plan-paused::before { background: linear-gradient(90deg, #FCD34D, #F59E0B); }
.tp-plan-completed::before { background: linear-gradient(90deg, #6EE7B7, #10B981); }
.tp-plan-cancelled::before { background: #E4E7EE; }
.tp-plan-card:hover { border-color: #BFD4FE; box-shadow: 0 6px 20px rgba(37, 99, 235, 0.1); transform: translateY(-2px); }
.tp-plan-card-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; min-height: 26px; }
.tp-plan-title { margin: 0; font-size: 14px; font-weight: 500; line-height: 1.45; color: var(--rl-text-1); display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.tp-plan-meta { display: flex; flex-wrap: wrap; gap: 6px; }
.tp-meta-chip { display: inline-flex; align-items: center; gap: 4px; padding: 3px 8px; border-radius: 8px; background: #F7F8FA; font-size: 11px; color: #4B4B4B; white-space: nowrap; }
.tp-meta-chip svg { color: #2563EB; flex-shrink: 0; }
.tp-meta-item { display: inline-flex; align-items: center; gap: 4px; font-size: 12px; color: #4B4B4B; }
.tp-meta-item svg { color: #2563EB; }
.tp-meta-warn { color: #D97706; }
.tp-plan-progress { display: flex; flex-direction: column; gap: 6px; margin-top: auto; }
.tp-day-track { display: flex; gap: 2px; height: 8px; align-items: stretch; }
.tp-day-seg { flex: 1; min-width: 2px; border-radius: 2px; background: #EEF0F4; }
.tp-seg-published { background: #2563EB; }
.tp-seg-failed { background: #EF4444; }
.tp-seg-pending { background: #EEF0F4; }
.tp-progress-line { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; }
.tp-progress-num { font-size: 13px; font-weight: 500; color: #2563EB; }
.tp-progress-text { font-size: 11px; color: #9A9A9A; white-space: nowrap; }
.tp-plan-foot { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding-top: 10px; border-top: 1px dashed #F0F0F2; }
.tp-plan-created { font-size: 11px; color: #B4B4BB; }
.tp-plan-open { display: inline-flex; align-items: center; gap: 3px; font-size: 12px; color: #9A9A9A; transition: color 0.15s, gap 0.15s; }
.tp-plan-card:hover .tp-plan-open { color: #2563EB; gap: 5px; }
.tp-plan-actions { display: flex; gap: 4px; }
.tp-icon-btn { display: inline-flex; align-items: center; justify-content: center; width: 28px; height: 28px; border-radius: 8px; border: 1px solid #EDEDED; background: #fff; color: #6B7280; cursor: pointer; transition: all 0.15s; }
.tp-icon-btn:hover { border-color: #93C5FD; color: #2563EB; background: #F8FAFF; }
.tp-icon-btn-accent:hover { border-color: #86EFAC; color: #16A34A; background: #F0FDF4; }
.tp-icon-btn-danger:hover { border-color: #FCA5A5; color: #DC2626; background: #FEF2F2; }

/* ---------- 骨架屏 / 筛选空态 ---------- */
.tp-skeleton-card { cursor: default; pointer-events: none; }
.tp-skeleton-card::before { background: #F2F3F6; }
.tp-sk-line { border-radius: 6px; background: #F2F3F6; animation: tp-pulse 1.4s ease-in-out infinite; }
.tp-sk-title { height: 16px; width: 62%; }
.tp-sk-meta { height: 12px; width: 88%; }
.tp-sk-track { height: 8px; width: 100%; }
.tp-sk-foot { height: 12px; width: 40%; margin-top: auto; }
@keyframes tp-pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.45; } }
.tp-filter-empty { display: flex; flex-direction: column; align-items: center; gap: 10px; padding: 48px 20px; color: #9A9A9A; font-size: 13px; }
.tp-filter-empty p { margin: 0; }

/* ---------- 状态徽章 ---------- */
.tp-status-badge { display: inline-flex; align-items: center; padding: 2px 10px; border-radius: 999px; font-size: 11px; font-weight: 500; border: 1px solid transparent; flex-shrink: 0; }
.tp-status-active { background: #EFF6FF; color: #2563EB; border-color: #BFDBFE; }
.tp-status-paused { background: #FFFBEB; color: #D97706; border-color: #FDE68A; }
.tp-status-completed { background: #F0FDF4; color: #16A34A; border-color: #BBF7D0; }
.tp-status-cancelled { background: #F5F5F5; color: #9A9A9A; border-color: #EDEDED; }

/* ---------- 抽屉 ---------- */
.tp-drawer-overlay { position: fixed; inset: 0; background: rgba(17, 24, 39, 0.4); z-index: 100; display: flex; justify-content: flex-end; }
.tp-drawer { width: 480px; max-width: 92vw; height: 100%; background: #fff; box-shadow: -4px 0 24px rgba(0, 0, 0, 0.1); display: flex; flex-direction: column; animation: tp-slide-in 0.2s ease-out; }
.tp-drawer-wide { width: 640px; }
@keyframes tp-slide-in { from { transform: translateX(30px); opacity: 0; } to { transform: translateX(0); opacity: 1; } }
.tp-drawer-header { display: flex; align-items: center; justify-content: space-between; padding: 16px 24px; border-bottom: 1px solid #EDEDED; flex-shrink: 0; }
.tp-drawer-header h2 { font-size: 15px; font-weight: 500; margin: 0; color: var(--rl-text-1); }
.tp-drawer-close { display: inline-flex; align-items: center; justify-content: center; width: 30px; height: 30px; border-radius:12px; border: 1px solid #EDEDED; background: #fff; color: #4B4B4B; cursor: pointer; }
.tp-drawer-close:hover { border-color: #FCA5A5; color: #DC2626; }
.tp-drawer-body { flex: 1; overflow-y: auto; padding: 20px 24px; display: flex; flex-direction: column; gap: 16px; }

/* ---------- 详情弹窗 ---------- */
.tp-modal-overlay { position: fixed; inset: 0; z-index: 120; display: flex; align-items: center; justify-content: center; padding: 32px 24px; background: rgba(17, 24, 39, 0.42); backdrop-filter: blur(2px); animation: tp-fade-in 0.18s ease-out; }
.tp-modal { width: min(880px, 100%); max-height: min(88vh, 860px); display: flex; flex-direction: column; background: #fff; border-radius: 20px; box-shadow: 0 24px 64px rgba(15, 23, 42, 0.24), 0 2px 8px rgba(15, 23, 42, 0.08); overflow: hidden; animation: tp-modal-in 0.22s cubic-bezier(0.16, 1, 0.3, 1); }
@keyframes tp-fade-in { from { opacity: 0; } to { opacity: 1; } }
@keyframes tp-modal-in { from { opacity: 0; transform: translateY(12px) scale(0.98); } to { opacity: 1; transform: translateY(0) scale(1); } }
.tp-modal-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; padding: 18px 22px 14px; border-bottom: 1px solid #F0F1F4; flex-shrink: 0; }
.tp-modal-head-main { min-width: 0; display: flex; flex-direction: column; gap: 6px; }
.tp-modal-title-row { display: flex; align-items: center; gap: 8px; min-width: 0; }
.tp-modal-title { margin: 0; font-size: 15px; font-weight: 500; color: var(--rl-text-1); line-height: 1.4; overflow: hidden; text-overflow: ellipsis; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }
.tp-modal-sub { display: flex; flex-wrap: wrap; gap: 12px; }
.tp-modal-head-actions { display: flex; align-items: center; gap: 6px; flex-shrink: 0; }
.tp-modal-close { display: inline-flex; align-items: center; justify-content: center; width: 30px; height: 30px; border-radius: 10px; border: 1px solid #EDEDED; background: #fff; color: #6B7280; cursor: pointer; transition: all 0.15s; }
.tp-modal-close:hover { border-color: #FCA5A5; color: #DC2626; background: #FEF2F2; }
.tp-modal-body { flex: 1; min-height: 0; overflow-y: auto; padding: 16px 22px 22px; display: flex; flex-direction: column; gap: 14px; }
.tp-modal-grid { display: grid; grid-template-columns: minmax(0, 0.85fr) minmax(0, 1.15fr); gap: 16px; align-items: start; }
.tp-modal-aside { display: flex; flex-direction: column; gap: 8px; padding: 14px; border: 1px solid #ECEEF2; border-radius: 14px; background: #FAFBFD; }
.tp-modal-aside .rl-plain { margin: 0; max-height: 320px; overflow-y: auto; font-size: 13px; }
.tp-modal-main { display: flex; flex-direction: column; gap: 8px; min-width: 0; }

/* ---------- 表单 ---------- */
.tp-hint { font-size: 13px; color: #4B4B4B; margin: 0; line-height: 1.6; }
.tp-form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.tp-form-row { display: flex; flex-direction: column; gap: 6px; }
.tp-form-label { font-size: 12px; font-weight: 500; color: var(--rl-text-2); }
.tp-textarea, .tp-input, .tp-select { width: 100%; padding: 8px 10px; border: 1px solid #EDEDED; border-radius:12px; font-size: 13px; color: var(--rl-text-1); background: #fff; box-sizing: border-box; font-family: inherit; }
.tp-textarea { resize: vertical; line-height: 1.6; }
.tp-textarea:focus, .tp-input:focus, .tp-select:focus, .tp-pt-input:focus { outline: none; border-color: #2563EB; box-shadow: 0 0 0 2px rgba(37, 99, 235, 0.08); }
.tp-form-actions { display: flex; gap: 8px; }

/* ---------- 拆解预览 ---------- */
.tp-preview-head { display: flex; align-items: baseline; gap: 10px; }
.tp-preview-head h3 { font-size: 14px; font-weight: 500; margin: 0; color: var(--rl-text-1); }
.tp-preview-title-hint { font-size: 12px; color: #9A9A9A; }
.tp-preview-warnings { display: flex; flex-direction: column; gap: 6px; }
.tp-warning-item { display: flex; align-items: center; gap: 6px; padding: 8px 12px; background: #FFFBEB; border: 1px solid #FDE68A; border-radius:12px; font-size: 12px; color: #92400E; }
.tp-warning-item svg { color: #D97706; flex-shrink: 0; }
.tp-preview-table { border: 1px solid #EDEDED; border-radius:14px; overflow: hidden; }
.tp-pt-row { display: grid; grid-template-columns: 52px 1fr 1fr; gap: 0; border-bottom: 1px solid #F5F5F5; }
.tp-pt-row:last-child { border-bottom: none; }
.tp-pt-header { background: #F5F5F5; font-size: 11px; font-weight: 500; color: #4B4B4B; }
.tp-pt-header span { padding: 8px 10px; }
.tp-pt-row:not(.tp-pt-header) span, .tp-pt-row:not(.tp-pt-header) input { padding: 6px 10px; }
.tp-pt-day { display: flex; align-items: center; font-size: 12px; font-weight: 500; color: #2563EB; background: #F8FAFF; }
.tp-pt-input { border: none; font-size: 13px; color: var(--rl-text-1); background: transparent; min-width: 0; }
.tp-pt-input:focus { background: #fff; }
.tp-pt-topic-input { font-weight: 500; }

/* ---------- 详情 ---------- */
.tp-detail-meta { display: flex; align-items: center; flex-wrap: wrap; gap: 12px; }
.tp-detail-overview { display: flex; flex-direction: column; gap: 10px; padding: 14px; border: 1px solid #ECEEF2; border-radius: 14px; background: #FAFBFD; }
.tp-detail-overview-head { display: flex; align-items: baseline; justify-content: space-between; gap: 10px; }
.tp-detail-overview-head h3 { margin: 0; font-size: 13px; font-weight: 500; color: var(--rl-text-1); }
.tp-detail-overview-sub { font-size: 11px; color: #9A9A9A; }
.tp-detail-dots { display: flex; flex-wrap: wrap; gap: 4px; }
.tp-detail-dot { width: 14px; height: 14px; border-radius: 4px; background: #EEF0F4; transition: transform 0.15s; }
.tp-detail-dot:hover { transform: scale(1.15); }
.tp-dot-published { background: #2563EB; }
.tp-dot-running { background: #F59E0B; }
.tp-dot-failed { background: #EF4444; }
.tp-dot-skipped, .tp-dot-cancelled { background: #D9DBE1; }
.tp-dot-pending { background: #EEF0F4; }
.tp-detail-legend { display: flex; flex-wrap: wrap; gap: 12px; }
.tp-detail-legend > span { display: inline-flex; align-items: center; gap: 5px; font-size: 11px; color: #6B7280; }
.tp-detail-section { display: flex; flex-direction: column; gap: 8px; }
.tp-detail-title { font-size: 14px; font-weight: 500; margin: 0; color: var(--rl-text-1); }
/* 「原始意图」正文块已收拢到 render-layer.css 的 .rl-plain（暖纸色板 + pre-wrap），此处不再本地重复定义 */

/* ---------- 时间线 ---------- */
.tp-timeline { display: flex; flex-direction: column; }
.tp-tl-item { display: flex; gap: 12px; padding: 10px 0 14px; position: relative; }
.tp-tl-item:not(:last-child)::before { content: ''; position: absolute; left: 5px; top: 26px; bottom: -4px; width: 2px; background: #F5F5F5; }
.tp-tl-marker { width: 12px; height: 12px; border-radius: 50%; border: 2px solid #D4D4D8; background: #fff; margin-top: 4px; flex-shrink: 0; }
.tp-tl-published .tp-tl-marker { border-color: #2563EB; background: #2563EB; }
.tp-tl-running .tp-tl-marker { border-color: #2563EB; background: #fff; }
.tp-tl-failed .tp-tl-marker { border-color: #DC2626; background: #DC2626; }
.tp-tl-skipped .tp-tl-marker, .tp-tl-cancelled .tp-tl-marker { border-color: #D4D4D8; background: #EDEDED; }
.tp-tl-body { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 4px; }
.tp-tl-head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.tp-tl-day { font-size: 13px; font-weight: 500; color: var(--rl-text-1); }
.tp-tl-time { font-size: 12px; color: #9A9A9A; }
.tp-item-status { font-size: 11px; padding: 1px 8px; border-radius: 999px; }
.tp-item-pending { background: #F5F5F5; color: #4B4B4B; }
.tp-item-running { background: #EFF6FF; color: #2563EB; }
.tp-item-published { background: #EFF6FF; color: #2563EB; border: 1px solid #BFDBFE; }
.tp-item-failed { background: #FEF2F2; color: #DC2626; }
.tp-item-skipped { background: #F5F5F5; color: #9A9A9A; }
.tp-item-cancelled { background: #F5F5F5; color: #9A9A9A; }
.tp-tl-topic { font-size: 13px; color: var(--rl-text-2); line-height: 1.5; }
.tp-tl-sub { font-size: 12px; color: #9A9A9A; display: inline-flex; align-items: center; gap: 4px; }
.tp-tl-confirm { color: #D97706; }
.tp-tl-error { display: inline-flex; align-items: center; gap: 4px; font-size: 12px; color: #DC2626; background: #FEF2F2; border-radius:10px; padding: 4px 8px; }
.tp-tl-error svg { flex-shrink: 0; }
.tp-tl-link { color: #2563EB; cursor: pointer; font-size: 12px; }
.tp-tl-link:hover { text-decoration: underline; }

@media (max-width: 1180px) {
  .tp-overview { grid-template-columns: 1fr; }
  .tp-overview-hero { justify-content: flex-start; }
  .tp-stat-grid { grid-template-columns: repeat(4, 1fr); }
}
@media (max-width: 900px) {
  .tp-form-grid { grid-template-columns: 1fr; }
  .tp-stat-grid { grid-template-columns: repeat(2, 1fr); }
  .tp-plans-grid { grid-template-columns: 1fr; }
  .tp-list-head { align-items: flex-start; flex-direction: column; }
  .tp-modal-grid { grid-template-columns: 1fr; }
  .tp-modal-aside .rl-plain { max-height: 180px; }
}
@media (max-width: 560px) {
  .tp-overview-hero { flex-direction: column; align-items: flex-start; gap: 10px; }
  .tp-plan-meta { gap: 5px; }
  .tp-modal-overlay { padding: 12px; }
  .tp-modal { max-height: 92vh; border-radius: 16px; }
  .tp-modal-head { flex-direction: column; align-items: stretch; }
  .tp-modal-head-actions { justify-content: flex-end; }
}
</style>