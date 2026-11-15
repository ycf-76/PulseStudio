<template>
<AppShell current-page="home">
    <main class="hp-page">
      <div class="hp-shell">
      <div class="da-content-card">

        <div class="hp-container">

          <!-- ====== 身份 + 海报 ====== -->
          <div class="hp-hero">
            <HeroBanner
              :avatar-url="authStore.user?.avatar_url"
              :nickname="authStore.user?.nickname || '创作者'"
              :status-label="accountStatusLabel"
              :status-text="heroStatus"
              :stats="heroStats"
              active-page="home"
              @create="goCreate"
              @tasks="goTo('/task-plans')"
              @logout="handleLogout"
              @navigate="handleNavClick"
            />
          </div>

          <!-- ====== 数据概览 ====== -->
          <section class="hp-block">
            <div class="hp-block-head">
              <h2 class="hp-block-title">数据概览</h2>
              <span class="hp-block-note">{{ periodLabel }}</span>
              <button class="hp-more" @click="goTo('/my-works')">查看详情<ArrowRight :size="12" /></button>
            </div>
            <div class="hp-metrics">
              <div v-for="m in metrics" :key="m.label" class="hp-metric">
                <span class="hp-metric-label">{{ m.label }}</span>
                <b class="hp-metric-value">{{ m.value }}</b>
              </div>
            </div>
          </section>

          <!-- ====== 待你处理 ====== -->
          <section class="hp-block">
            <div class="hp-block-head">
              <h2 class="hp-block-title">待你处理</h2>
              <span v-if="pendingTotal > 0" class="hp-block-note">{{ pendingTotal }} 项</span>
              <button class="hp-more" @click="goTo('/task-plans')">全部<ArrowRight :size="12" /></button>
            </div>

            <div v-if="decisions.length" class="hp-list">
              <div v-for="d in decisions" :key="d.key" class="hp-task" @click="goTo('/task-plans')">
                <span class="hp-dot" :class="'is-' + d.tone"></span>
                <span class="hp-task-tag">{{ d.tag }}</span>
                <span class="hp-task-topic">{{ d.topic }}</span>
                <span class="hp-task-meta">{{ d.meta }}</span>
                <span class="hp-task-act">{{ d.action }}<ChevronRight :size="12" /></span>
              </div>
            </div>
            <p v-else class="hp-empty">暂无待处理</p>
          </section>

          <!-- ====== 内容 + 侧栏 ====== -->
          <div class="hp-cols">
            <section class="hp-col">
              <div class="hp-block-head">
                <h2 class="hp-block-title">最近笔记</h2>
                <span class="hp-block-note">{{ topItems.length }} 篇</span>
                <button class="hp-more" @click="goTo('/portfolio')">全部<ArrowRight :size="12" /></button>
              </div>

              <div v-if="topItems.length" class="hp-notes">
                <div v-for="item in topItems" :key="item.id" class="hp-note" @click="goTo('/portfolio/' + item.id)">
                  <div class="hp-note-thumb">
                    <img v-if="item.cover_img_url" :src="item.cover_img_url" alt="" />
                  </div>
                  <div class="hp-note-main">
                    <span class="hp-note-title">{{ item.title || '无标题' }}</span>
                    <span class="hp-note-sub">{{ platformLabel(item.platform) }} · {{ formatTime(item.published_at || item.collected_at) }}</span>
                  </div>
                  <div class="hp-note-stats">
                    <span><em>阅读</em><b>{{ formatNum(item.reads) }}</b></span>
                    <span><em>赞</em><b>{{ formatNum(item.likes) }}</b></span>
                    <span><em>藏</em><b>{{ formatNum(item.collects) }}</b></span>
                    <span><em>评</em><b>{{ formatNum(item.comments) }}</b></span>
                  </div>
                </div>
              </div>
              <p v-else class="hp-empty">还没有笔记数据</p>
            </section>

            <aside class="hp-side">
              <div class="hp-block-head">
                <h2 class="hp-block-title">{{ scheduleTitle }}</h2>
                <span class="hp-block-note">{{ schedule.length }} 条</span>
                <button class="hp-more" @click="goTo('/task-plans')">清单<ArrowRight :size="12" /></button>
              </div>

              <div v-if="schedule.length" class="hp-tl">
                <div v-for="s in schedule" :key="s.key" class="hp-tl-row">
                  <span class="hp-tl-time">{{ s.time }}</span>
                  <span class="hp-dot" :class="'is-' + s.tone"></span>
                  <span class="hp-tl-topic">{{ s.topic }}</span>
                  <span class="hp-tl-status">{{ s.statusText }}</span>
                </div>
              </div>
              <p v-else class="hp-empty">今天没有排期</p>

              <div class="hp-block-head hp-head-gap">
                <h2 class="hp-block-title">账号</h2>
                <span class="hp-block-note">{{ boundCount }}/{{ platformAccounts.length }}</span>
                <button class="hp-more" @click="openSettings('accounts')">管理<ArrowRight :size="12" /></button>
              </div>

              <div class="hp-accs">
                <div
                  v-for="acc in platformAccounts"
                  :key="acc.platform"
                  class="hp-acc"
                  :class="{ 'is-unbound': !acc.bound }"
                  @click="!acc.bound && openSettings('accounts')"
                >
                  <img v-if="acc.platform_avatar_url" class="hp-acc-av" :src="acc.platform_avatar_url" alt="" />
                  <span v-else class="hp-acc-av hp-acc-av-ph">{{ acc.platform_label.slice(0, 1) }}</span>
                  <span class="hp-acc-name">{{ acc.platform_nickname || acc.platform_label }}</span>
                  <span class="hp-acc-val">{{ acc.bound ? formatNum(acc.fans_count) + ' 粉丝' : '未绑定' }}</span>
                </div>
              </div>
            </aside>
          </div>

        </div>

      </div>
      <div class="hp-notch" aria-hidden="true"></div>
      </div>
    </main>
</AppShell>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ArrowRight, ChevronRight } from 'lucide-vue-next'
import AppShell from '@/components/layout/AppShell.vue'
import HeroBanner from '@/components/home/HeroBanner.vue'
import { useAuthStore } from '@/stores/auth'
import { useUIState } from '@/composables/useUIState'
import {
  getDashboardOverview, getContentAnalysis, getFollowerAnalysis,
  type DashboardOverview, type ContentAnalysis, type FollowerAnalysis, type PlatformAccountInfo,
} from '@/api/dashboard'
import { listTaskPlans, getTaskPlanDetail, type TaskPlanSummary, type TaskPlanDetail, type TaskItemInfo } from '@/api/taskPlan'

const router = useRouter()
const authStore = useAuthStore()
const uiState = useUIState()

function openSettings(section?: string) { uiState.openSettings(section) }
function goCreate() { goTo('/chat') }
function goTo(path: string) { router.push(path).catch(() => {}) }
async function handleLogout() {
  try {
    await authStore.logout()
  } catch (e) {
    console.error('[HomeView] logout error:', e)
  }
  router.push('/login').catch(() => {})
}
function handleNavClick(pageName: string) {
  const map: Record<string, string> = {
    'chat': '/chat', 'skills': '/skills', 'my-works': '/my-works',
    'portfolio': '/portfolio', 'topic-pool': '/topic-pool', 'task-plans': '/task-plans',
  }
  if (pageName === 'home') return
  goTo(map[pageName] || '/chat')
}

const overview = ref<DashboardOverview | null>(null)
const contentAnalysis = ref<ContentAnalysis | null>(null)
const followerData = ref<FollowerAnalysis | null>(null)
const planDetails = ref<TaskPlanDetail[]>([])

const DAYS = 7
const EMPTY_SUMMARY = { total_reads: 0, total_interactions: 0, total_count: 0, collected_count: 0, hot_count: 0, avg_engagement_rate: 0 }
const summary = computed(() => overview.value?.summary ?? EMPTY_SUMMARY)
const platformAccounts = computed<PlatformAccountInfo[]>(() =>
  [...(overview.value?.platform_accounts ?? [])].sort((a, b) => Number(b.bound) - Number(a.bound))
)
const boundCount = computed(() => platformAccounts.value.filter(a => a.bound).length)
const followerTotalFans = computed(() => followerData.value?.total_fans ?? 0)
const topItems = computed(() => (contentAnalysis.value?.top_items ?? []).slice(0, 5))
const dailyTrend = computed(() => overview.value?.daily_trend ?? [])

const aggStats = computed(() => {
  const stats = overview.value?.platform_content_stats ?? {}
  let likes = 0, collects = 0, comments = 0, shares = 0
  for (const key of Object.keys(stats)) {
    likes += Number(stats[key].likes) || 0
    collects += Number(stats[key].collects) || 0
    comments += Number(stats[key].comments) || 0
    shares += Number(stats[key].shares) || 0
  }
  return { likes, collects, comments, shares }
})

const allItems = computed<TaskItemInfo[]>(() => {
  const out: TaskItemInfo[] = []
  for (const d of planDetails.value) for (const it of d.items) out.push(it)
  return out
})

interface Decision {
  key: string; tone: string; tag: string; topic: string; meta: string; action: string; rank: number
}
function dayKey(d: Date) { return `${d.getFullYear()}-${d.getMonth() + 1}-${d.getDate()}` }
const todayKey = dayKey(new Date())

const allDecisions = computed<Decision[]>(() => {
  const list: Decision[] = []
  const now = Date.now()
  for (const it of allItems.value) {
    const run = it.latest_run
    if (run?.status === 'awaiting_confirmation') {
      list.push({ key: it.id, tone: 'review', tag: '待审核', topic: it.topic, meta: '确认后才会发布', action: '去审核', rank: 0 })
    } else if (it.status === 'failed') {
      list.push({ key: it.id, tone: 'fail', tag: '发布失败', topic: it.topic, meta: it.last_error?.reason || '未知原因', action: '重试', rank: 1 })
    } else if (it.status === 'pending' && it.plan_time) {
      const ts = new Date(it.plan_time).getTime()
      if (Number.isNaN(ts)) continue
      const due = ts <= now
      const isToday = dayKey(new Date(it.plan_time)) === todayKey
      if (due || isToday) {
        list.push({
          key: it.id,
          tone: due ? 'run' : 'idle',
          tag: '待发布',
          topic: it.topic,
          meta: due ? `已到点 · 原定 ${formatClock(it.plan_time)}` : `今天 ${formatClock(it.plan_time)} 发布`,
          action: '去发布',
          rank: 2,
        })
      }
    }
  }
  return list.sort((a, b) => a.rank - b.rank)
})
const decisions = computed(() => allDecisions.value.slice(0, 6))
const pendingTotal = computed(() => allDecisions.value.length)

const todayItems = computed(() =>
  allItems.value.filter(it => it.plan_time && dayKey(new Date(it.plan_time)) === todayKey)
)
const upcomingItems = computed(() =>
  allItems.value
    .filter(it => it.plan_time && it.status === 'pending' && new Date(it.plan_time) > new Date())
    .sort((a, b) => new Date(a.plan_time as string).getTime() - new Date(b.plan_time as string).getTime())
)

const STATUS_TEXT: Record<string, string> = {
  pending: '待发布', running: '生成中', published: '已发布',
  failed: '失败', skipped: '已跳过', cancelled: '已取消',
}
const STATUS_TONE: Record<string, string> = {
  pending: 'idle', running: 'run', published: 'ok', failed: 'fail', skipped: 'idle', cancelled: 'idle',
}
const schedule = computed(() => {
  const src = todayItems.value.length
    ? [...todayItems.value].sort((a, b) => new Date(a.plan_time as string).getTime() - new Date(b.plan_time as string).getTime())
    : upcomingItems.value
  return src.slice(0, 5).map(it => ({
    key: it.id,
    time: formatClock(it.plan_time),
    topic: it.topic,
    statusText: STATUS_TEXT[it.status] || it.status,
    tone: STATUS_TONE[it.status] || 'idle',
  }))
})
const scheduleTitle = computed(() => (todayItems.value.length ? '今日排期' : '接下来'))

const periodLabel = computed(() => {
  const data = dailyTrend.value
  if (!data.length) return `近 ${DAYS} 天`
  return `近 ${DAYS} 天 · ${data[0].date} 至 ${data[data.length - 1].date}`
})

const accountStatusLabel = computed(() => (boundCount.value > 0 ? '账号正常' : '待绑定'))
const heroStatus = computed(() => {
  const d = new Date()
  const week = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'][d.getDay()]
  const date = `${d.getMonth() + 1} 月 ${d.getDate()} 日 ${week}`
  if (pendingTotal.value > 0) return `${date} · ${pendingTotal.value} 件事待处理`
  if (todayItems.value.length) return `${date} · 今天 ${todayItems.value.length} 条排期`
  return `${date} · 今天还没有排期`
})
const heroStats = computed(() => [
  { label: '笔记', value: summary.value.total_count },
  { label: '粉丝', value: formatNum(followerTotalFans.value) },
  { label: '获赞与收藏', value: formatNum(aggStats.value.likes + aggStats.value.collects) },
])

const metrics = computed(() => [
  { label: '点赞', value: formatNum(aggStats.value.likes) },
  { label: '收藏', value: formatNum(aggStats.value.collects) },
  { label: '评论', value: formatNum(aggStats.value.comments) },
  { label: '转发', value: formatNum(aggStats.value.shares) },
  { label: '笔记', value: formatNum(summary.value.total_count) },
  { label: '粉丝', value: formatNum(followerTotalFans.value) },
])

const PLATFORM_LABELS: Record<string, string> = {
  xiaohongshu: '小红书', douyin: '抖音', bilibili: 'B站', wechat_mp: '公众号', weibo: '微博',
}
function platformLabel(platform: string) { return PLATFORM_LABELS[platform] || platform }

function formatNum(value: number | null | undefined): string {
  const n = Number(value) || 0
  if (n >= 10000) return (n / 10000).toFixed(1).replace(/\.0$/, '') + '万'
  return String(n)
}
function formatClock(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}
function formatTime(iso: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  return `${d.getMonth() + 1}/${d.getDate()}`
}

async function loadAll() {
  const [ov, ca, fa, plans] = await Promise.allSettled([
    getDashboardOverview(DAYS),
    getContentAnalysis({ sort_by: 'latest', limit: 5 }),
    getFollowerAnalysis(),
    listTaskPlans(),
  ])
  if (ov.status === 'fulfilled') overview.value = ov.value
  if (ca.status === 'fulfilled') contentAnalysis.value = ca.value
  if (fa.status === 'fulfilled') followerData.value = fa.value

  if (plans.status === 'fulfilled') {
    const list: TaskPlanSummary[] = plans.value ?? []
    const active = list.filter(p => p.status === 'active' || p.status === 'paused')
    const details = await Promise.allSettled(active.map(p => getTaskPlanDetail(p.id)))
    planDetails.value = details
      .filter(r => r.status === 'fulfilled' && r.value)
      .map(r => (r as PromiseFulfilledResult<TaskPlanDetail>).value)
  }
}

onMounted(loadAll)
</script>

<style scoped>
.hp-page { flex: 1; min-width: 0; height: 100%; display: flex; flex-direction: column; overflow: hidden; padding: 28px 20px 16px; }
.hp-shell { position: relative; display: flex; flex-direction: column; flex: 1; min-height: 0; min-width: 0; --hp-poster-h: clamp(190px, 17vw, 220px); --hp-poster-w: calc(var(--hp-poster-h) * 16 / 9); --hp-gap: 14px; --hp-notch-r: 20px; --hp-notch-h: calc(var(--hp-poster-h) + var(--hp-gap) * 2); --hp-notch-w: calc(var(--hp-poster-w) + var(--hp-gap) * 2); }
.da-content-card { flex: 1; min-height: 0; min-width: 0; background: #FFFFFF; border-radius: 20px; border: 1px solid var(--ma-border-default, #EDEDED); box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04); display: flex; flex-direction: column; overflow: hidden; }
.hp-container { flex: 1; overflow-y: auto; padding: 0 24px 20px; margin-top: var(--hp-notch-h); }
.hp-hero { position: absolute; top: 0; left: 0; right: 0; height: var(--hp-notch-h); padding-left: 24px; z-index: 3; }
.hp-notch { position: absolute; top: 0; right: 0; width: var(--hp-notch-w); height: var(--hp-notch-h); background: var(--ma-bg-base, #EBEEF2); border-radius: var(--hp-notch-r) 0 var(--hp-notch-r) var(--hp-notch-r); z-index: 2; pointer-events: none; }
/* ====== 区块头 ====== */
.hp-block { margin-top: 34px; padding-top: 22px; border-top: 1px solid #F0F0F0; }
.hp-block-head { display: flex; align-items: center; gap: 9px; margin-bottom: 4px; }
.hp-head-gap { margin-top: 26px; padding-top: 20px; border-top: 1px solid #F0F0F0; }
.hp-block-title { margin: 0; font-size: 14px; font-weight: 500; color: var(--rl-text-1, #1A1A1A); }
.hp-block-note { font-size: 12px; color: #9A9A9A; font-variant-numeric: tabular-nums; }
.hp-more { display: inline-flex; align-items: center; gap: 1px; margin-left: auto; padding: 3px 6px; border: none; background: transparent; color: #9A9A9A; font-size: 12px; cursor: pointer; border-radius: 5px; transition: color 0.15s, background 0.15s; }
.hp-more:hover { color: var(--rl-text-1, #1A1A1A); background: #F5F5F5; }

/* ====== 数据概览 ====== */
.hp-metrics { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); margin-top: 14px; }
.hp-metric { display: flex; flex-direction: column; gap: 4px; padding: 0 20px; min-width: 0; }
.hp-metric:first-child { padding-left: 0; }
.hp-metric:last-child { padding-right: 0; }
.hp-metric + .hp-metric { border-left: 1px solid #F0F0F0; }
.hp-metric-label { font-size: 12px; color: #9A9A9A; }
.hp-metric-value { font-size: 18px; font-weight: 500; line-height: 1.2; letter-spacing: -0.1px; color: var(--rl-text-1, #1A1A1A); font-variant-numeric: tabular-nums; }

/* ====== 待你处理 ====== */
.hp-list { margin-top: 8px; }
.hp-task { display: grid; grid-template-columns: 6px 54px minmax(0, 1fr) minmax(0, 190px) auto; align-items: center; gap: 14px; padding: 12px 8px; margin: 0 -8px; border-radius: 7px; cursor: pointer; transition: background 0.15s; }
.hp-task:hover { background: #F7F7F7; }
.hp-dot { width: 6px; height: 6px; border-radius: 50%; background: #D4D4D8; }
.hp-dot.is-review { background: #D97706; }
.hp-dot.is-fail { background: #DC2626; }
.hp-dot.is-ok { background: #16A34A; }
.hp-dot.is-run { background: #2563EB; }
.hp-task-tag { font-size: 12px; color: var(--rl-text-2, #4B4B4B); }
.hp-task-topic { font-size: 13px; color: var(--rl-text-1, #1A1A1A); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.hp-task-meta { font-size: 12px; color: #9A9A9A; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.hp-task-act { display: inline-flex; align-items: center; gap: 1px; font-size: 12px; color: #FF2442; white-space: nowrap; }

/* ====== 双栏 ====== */
.hp-cols { display: grid; grid-template-columns: minmax(0, 1.55fr) minmax(0, 1fr); gap: 44px; margin-top: 34px; padding-top: 22px; border-top: 1px solid #F0F0F0; }
.hp-col { min-width: 0; }
.hp-side { min-width: 0; }

/* ====== 最近笔记 ====== */
.hp-notes { margin-top: 6px; }
.hp-note { display: grid; grid-template-columns: 42px minmax(0, 1fr) auto; align-items: center; gap: 12px; padding: 11px 8px; margin: 0 -8px; border-radius: 7px; cursor: pointer; transition: background 0.15s; }
.hp-note:hover { background: #F7F7F7; }
.hp-note-thumb { width: 42px; height: 42px; border-radius: 5px; overflow: hidden; background: #F2F2F2; flex-shrink: 0; }
.hp-note-thumb img { width: 100%; height: 100%; object-fit: cover; display: block; }
.hp-note-main { min-width: 0; display: flex; flex-direction: column; gap: 3px; }
.hp-note-title { font-size: 13px; color: var(--rl-text-1, #1A1A1A); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.hp-note-sub { font-size: 12px; color: #9A9A9A; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.hp-note-stats { display: flex; align-items: flex-end; gap: 16px; flex-shrink: 0; }
.hp-note-stats span { display: flex; flex-direction: column; align-items: flex-end; gap: 1px; }
.hp-note-stats em { font-style: normal; font-size: 10px; color: #B0B0B0; }
.hp-note-stats b { font-size: 13px; font-weight: 500; color: var(--rl-text-1, #1A1A1A); font-variant-numeric: tabular-nums; }

/* ====== 排期 ====== */
.hp-tl { margin-top: 6px; }
.hp-tl-row { display: grid; grid-template-columns: 38px 6px minmax(0, 1fr) auto; align-items: center; gap: 11px; padding: 10px 8px; margin: 0 -8px; border-radius: 7px; transition: background 0.15s; }
.hp-tl-row:hover { background: #F7F7F7; }
.hp-tl-time { font-size: 12px; color: var(--rl-text-2, #4B4B4B); font-variant-numeric: tabular-nums; }
.hp-tl-topic { font-size: 13px; color: var(--rl-text-1, #1A1A1A); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.hp-tl-status { font-size: 11px; color: #9A9A9A; white-space: nowrap; }

/* ====== 账号 ====== */
.hp-accs { margin-top: 4px; }
.hp-acc { display: flex; align-items: center; gap: 9px; padding: 8px; margin: 0 -8px; border-radius: 7px; }
.hp-acc.is-unbound { cursor: pointer; transition: background 0.15s; }
.hp-acc.is-unbound:hover { background: #F7F7F7; }
.hp-acc-av { width: 20px; height: 20px; border-radius: 50%; object-fit: cover; flex-shrink: 0; background: #F2F2F2; }
.hp-acc-av-ph { display: flex; align-items: center; justify-content: center; font-size: 10px; color: #9A9A9A; }
.hp-acc-name { font-size: 13px; color: var(--rl-text-1, #1A1A1A); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.hp-acc-val { margin-left: auto; font-size: 12px; color: #9A9A9A; white-space: nowrap; font-variant-numeric: tabular-nums; }

/* ====== 空态 ====== */
.hp-empty { margin: 0; padding: 16px 0 2px; font-size: 12px; color: #B0B0B0; }

@media (max-width: 1180px) {
  .hp-shell { --hp-poster-h: clamp(176px, 16vw, 204px); --hp-gap: 12px; }
}
@media (max-width: 1080px) {
  .hp-shell { --hp-poster-h: clamp(168px, 16vw, 196px); }
  .hp-metrics { grid-template-columns: repeat(3, minmax(0, 1fr)); row-gap: 20px; }
  .hp-metric:nth-child(3n+1) { border-left: none; padding-left: 0; }
  .hp-cols { grid-template-columns: 1fr; gap: 0; }
  .hp-side { margin-top: 34px; padding-top: 22px; border-top: 1px solid #F0F0F0; }
  .hp-task { grid-template-columns: 6px 54px minmax(0, 1fr) auto; }
  .hp-task-meta { display: none; }
}
@media (max-width: 860px) {
  .hp-hero { position: static; height: auto; padding-left: 0; z-index: auto; }
  .hp-notch { display: none; }
  .hp-container { margin-top: 0; padding-top: 20px; }
}
@media (max-width: 720px) {
  .hp-page { padding: 16px 12px 12px; }
  .hp-container { padding: 16px 16px 20px; }
  .hp-metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .hp-metric { border-left: none; padding-left: 0; }
  .hp-metric:nth-child(even) { border-left: 1px solid #F0F0F0; padding-left: 16px; }
  .hp-note-stats span:nth-child(n+3) { display: none; }
  .hp-task { grid-template-columns: 6px minmax(0, 1fr) auto; }
  .hp-task-tag { display: none; }
}
</style>