<template>
<AppShell current-page="my-works">
    <main class="an-page">
      <div class="da-content-card-wrapper">
      <div class="da-content-card">

        <div class="an-container" ref="scrollContainer">

          <!-- ====== 顶部：标题 + 平台筛选 ====== -->
          <header class="an-header">
            <div class="an-header-left">
              <h1 class="an-title">数据分析</h1>
              <div v-if="report && report.status === 'ok'" class="an-meta">
                <span class="an-meta-item">样本 <b>{{ report.sample_size }}</b> 篇</span>
                <span class="an-meta-div"></span>
                <span class="an-meta-item">置信度 <b :class="confidenceClass">{{ confidenceLabel }}</b></span>
                <span class="an-meta-div"></span>
                <span class="an-meta-item">权重 <b>{{ Math.round((report.my_weight || 0) * 100) }}%</b></span>
              </div>
            </div>
            <div class="an-header-right">
              <div class="an-select-wrap">
                <Globe :size="14" class="an-select-icon" />
                <select class="an-select" v-model="activePlatform" @change="loadReport">
                  <option value="">全部平台</option>
                  <option v-for="p in platformOptions" :key="p.value" :value="p.value">{{ p.label }}</option>
                </select>
              </div>
              <button class="an-refresh" :disabled="loading" @click="loadReport" title="刷新分析">
                <RefreshCw :size="15" :class="{ 'an-spin': loading }" />
              </button>
            </div>
          </header>

          <!-- ====== 首次加载骨架 ====== -->
          <div v-if="loading && !report" class="an-skeleton">
            <div class="an-skeleton-kpis">
              <div v-for="i in 4" :key="i" class="an-sk-card"></div>
            </div>
            <div class="an-skeleton-split">
              <div class="an-sk-block"></div>
              <div class="an-sk-block"></div>
            </div>
            <div class="an-sk-block an-sk-short"></div>
          </div>

          <!-- ====== 错误 ====== -->
          <div v-else-if="error" class="an-error">
            <AlertCircle :size="18" />
            <span>{{ error }}</span>
            <button class="an-link-btn" @click="loadReport">重试</button>
          </div>

          <!-- ====== 无数据 ====== -->
          <div v-else-if="report && report.status !== 'ok'" class="an-empty">
            <div class="an-empty-icon"><Inbox :size="44" /></div>
            <p class="an-empty-title">{{ report.message || '暂无采集数据' }}</p>
            <p class="an-empty-hint">分析依赖已采集作品的表现数据，先去我的空间采集一批笔记。</p>
            <div class="an-empty-actions">
              <button class="an-btn-primary" @click="goToPortfolio">去我的空间采集</button>
              <button class="an-btn-ghost" @click="goToWorkbench">去创作</button>
            </div>
          </div>

          <!-- ====== 分析看板 ====== -->
          <div v-else-if="report" class="an-body" :class="{ 'an-refreshing': loading }">

            <!-- KPI 卡 -->
            <section class="an-kpis">
              <div class="an-kpi">
                <div class="an-kpi-top"><span class="an-kpi-label">采集作品</span><Layers :size="15" class="an-kpi-icon" /></div>
                <div class="an-kpi-value">{{ formatNum(overview?.total) }}<span class="an-kpi-unit">篇</span></div>
                <div class="an-kpi-foot">
                  <span class="an-foot-good">高 {{ overview?.high_performers ?? 0 }}</span>
                  <span class="an-foot-dot">·</span>
                  <span class="an-foot-bad">低 {{ overview?.low_performers ?? 0 }}</span>
                  <span class="an-foot-dot">·</span>
                  <span>中 {{ midPerformers }}</span>
                </div>
                <div class="an-kpi-track an-kpi-stack">
                  <span v-for="r in distRows" :key="r.key" class="an-stack-seg" :class="`is-${r.key}`" :style="{ width: r.width }"></span>
                </div>
              </div>

              <div class="an-kpi">
                <div class="an-kpi-top"><span class="an-kpi-label">平均表现分</span><Target :size="15" class="an-kpi-icon" /></div>
                <div class="an-kpi-value">{{ scoreText(overview?.avg_score) }}<span class="an-kpi-unit">分</span></div>
                <div class="an-kpi-foot">
                  <span class="an-kpi-delta" :class="avgScoreDelta >= 0 ? 'is-good' : 'is-bad'">
                    {{ avgScoreDelta >= 0 ? '+' : '-' }}{{ Math.abs(Math.round(avgScoreDelta * 100)) }} 个百分点
                  </span>
                  <span>爆款线 {{ VIRAL_LINE * 100 }} 分</span>
                </div>
                <div class="an-kpi-gauge">
                  <div class="an-kpi-gauge-fill" :style="{ width: gaugeWidth }"></div>
                  <div class="an-kpi-gauge-mark" :style="{ left: VIRAL_LINE * 100 + '%' }" title="爆款线"></div>
                </div>
              </div>

              <div class="an-kpi">
                <div class="an-kpi-top"><span class="an-kpi-label">总互动量</span><Heart :size="15" class="an-kpi-icon" /></div>
                <div class="an-kpi-value">{{ formatNum(totalEngagement) }}</div>
                <div class="an-kpi-foot an-kpi-foot-legend">
                  <span class="an-leg"><i class="an-leg-dot is-like"></i>赞 {{ formatNum(overview?.total_likes) }}</span>
                  <span class="an-leg"><i class="an-leg-dot is-collect"></i>藏 {{ formatNum(overview?.total_collects) }}</span>
                  <span class="an-leg"><i class="an-leg-dot is-comment"></i>评 {{ formatNum(overview?.total_comments) }}</span>
                </div>
              </div>

              <div class="an-kpi">
                <div class="an-kpi-top"><span class="an-kpi-label">爆款率</span><Flame :size="15" class="an-kpi-icon" /></div>
                <div class="an-kpi-value">{{ Math.round(viralRate * 100) }}<span class="an-kpi-unit">%</span></div>
                <div class="an-kpi-foot">
                  <span>篇均互动 {{ formatNum(avgPerWork) }}</span>
                  <span class="an-foot-dot">·</span>
                  <span>已复制 {{ overview?.replicated ?? 0 }}</span>
                </div>
                <div class="an-kpi-track">
                  <span class="an-kpi-track-fill" :style="{ width: Math.min(100, viralRate * 100) + '%' }"></span>
                </div>
              </div>
            </section>

            <!-- 平台对比 + 表现分布 -->
            <div class="an-split" v-if="sortedPlatforms.length > 0">
              <section class="an-section an-section-platforms">
                <div class="an-section-head">
                  <h2 class="an-section-title">平台对比</h2>
                  <span class="an-section-desc">{{ sortedPlatforms.length }} 个平台 · 按平均表现分排序</span>
                </div>
                <div class="an-platform-list">
                  <div v-for="p in sortedPlatforms" :key="p.platform" class="an-platform-row">
                    <div class="an-platform-name">{{ platformLabel(p.platform) }}</div>
                    <div class="an-platform-bar-wrap">
                      <div class="an-platform-bar" :style="{ width: barWidth(p.avg_score, platformMaxScore) }"></div>
                    </div>
                    <div class="an-platform-score">{{ scoreText(p.avg_score) }}</div>
                    <div class="an-platform-meta">{{ p.count }} 篇 · 赞 {{ formatNum(p.total_likes) }} · 藏 {{ formatNum(p.total_collects) }} · 复制率 {{ Math.round((p.replicated_rate || 0) * 100) }}%</div>
                  </div>
                </div>
              </section>

              <section class="an-section an-section-dist">
                <div class="an-section-head">
                  <h2 class="an-section-title">表现分布</h2>
                  <span class="an-section-desc">共 {{ overview?.total ?? 0 }} 篇</span>
                </div>
                <div class="an-dist-bar">
                  <span v-for="r in distRows" :key="r.key" class="an-dist-seg" :class="`is-${r.key}`" :style="{ width: r.width }"></span>
                </div>
                <div class="an-dist-list">
                  <div v-for="r in distRows" :key="r.key" class="an-dist-item">
                    <i class="an-leg-dot" :class="`is-${r.key}`"></i>
                    <span class="an-dist-label">{{ r.label }}</span>
                    <b>{{ r.count }}</b>
                    <span class="an-dist-pct">{{ r.pct }}%</span>
                  </div>
                </div>
                <div class="an-dist-foot">
                  <span>复制率<b>{{ Math.round(replicateRate * 100) }}%</b></span>
                  <span>篇均互动<b>{{ formatNum(avgPerWork) }}</b></span>
                </div>
              </section>
            </div>

            <!-- 维度榜单 -->
            <section class="an-section" v-if="rankingGroups.length > 0">
              <div class="an-section-head an-section-head-tabs">
                <h2 class="an-section-title">爆款维度排行</h2>
                <div class="an-rank-tabs">
                  <button
                    v-for="g in rankingGroups"
                    :key="g.key"
                    class="an-rank-tab"
                    :class="{ 'is-active': g.key === activeRankGroup?.key }"
                    type="button"
                    @click="activeRankKey = g.key"
                  >
                    {{ g.title }}<span class="an-rank-tab-count">{{ g.items.length }}</span>
                  </button>
                </div>
              </div>
              <div v-if="activeRankGroup" class="an-rank-list">
                <div v-for="(item, idx) in activeRankGroup.items" :key="item.name" class="an-rank-row">
                  <span class="an-rank-idx" :class="{ 'an-rank-idx-top': idx === 0 }">{{ idx + 1 }}</span>
                  <div class="an-rank-main">
                    <div class="an-rank-name-row">
                      <span class="an-rank-name" :title="item.name">{{ item.name }}</span>
                      <span class="an-rank-val">{{ scoreText(item.avg_score) }} 分</span>
                    </div>
                    <div class="an-rank-track">
                      <div class="an-rank-bar" :class="{ 'an-rank-bar-top': idx === 0 }" :style="{ width: barWidth(item.avg_score, activeRankGroup.max) }"></div>
                    </div>
                  </div>
                  <span class="an-rank-samples">{{ item.count }} 篇</span>
                </div>
              </div>
              <div class="an-rank-hint">同一维度内，平均表现分越高越有效</div>
            </section>

            <!-- Top 作品 -->
            <section class="an-section" v-if="topPerformers.length > 0">
              <div class="an-section-head">
                <h2 class="an-section-title">表现最好的作品</h2>
                <button class="an-link-btn" @click="goToPortfolio">查看全部 <ChevronRight :size="14" /></button>
              </div>
              <div class="an-top-grid">
                <div v-for="(w, idx) in topPerformers" :key="w.id" class="an-top-card" @click="goToWork(w.id)">
                  <div class="an-top-cover">
                    <span class="an-top-rank" :class="{ 'is-top': idx === 0 }">{{ idx + 1 }}</span>
                    <img v-if="w.cover_img_url" :src="w.cover_img_url" alt="" @error="onCoverError($event, w.cover_img_url)" />
                    <div v-else class="an-top-cover-ph"><ImageIcon :size="22" /></div>
                  </div>
                  <div class="an-top-body">
                    <div class="an-top-title">{{ w.title || '无标题' }}</div>
                    <div class="an-top-meta">
                      <span class="an-top-score">{{ scoreText(w.score) }}</span>
                      <span class="an-top-plat">{{ platformLabel(w.platform) }}</span>
                    </div>
                    <div class="an-top-stats">
                      <span><Heart :size="12" /> {{ formatNum(w.likes) }}</span>
                      <span><Star :size="12" /> {{ formatNum(w.collects) }}</span>
                      <span><MessageCircle :size="12" /> {{ formatNum(w.comments) }}</span>
                    </div>
                  </div>
                </div>
              </div>
            </section>

            <!-- 行动建议 -->
            <section class="an-section" v-if="actionItems.length > 0">
              <div class="an-section-head">
                <h2 class="an-section-title">下一步做什么</h2>
                <span class="an-section-desc">基于你的真实数据生成</span>
              </div>
              <div class="an-actions-list">
                <div v-for="(a, idx) in actionItems" :key="idx" class="an-action-row">
                  <span class="an-action-priority" :class="priorityClass(a.priority)">{{ priorityLabel(a.priority) }}</span>
                  <div class="an-action-main">
                    <div class="an-action-text">{{ a.action }}</div>
                    <div class="an-action-reason">{{ a.reason }}</div>
                  </div>
                </div>
              </div>
            </section>

          </div>
        </div>
      </div>
      </div>
    </main>
</AppShell>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import {
  Globe, RefreshCw, AlertCircle, Inbox, Layers, Target, Heart, Flame,
  Star, MessageCircle, ChevronRight, ImageIcon,
} from 'lucide-vue-next'
import AppShell from '@/components/layout/AppShell.vue'
import { getAnalysisReport, type AnalysisReport, type AnalysisRankItem } from '@/api/my_works'

const router = useRouter()

function goToWorkbench() { router.push('/chat') }
function goToPortfolio() { router.push('/portfolio').catch(() => {}) }
function goToWork(id: string) { router.push(`/portfolio/${id}?source=published`).catch(() => {}) }

const PLATFORM_MAP: Record<string, string> = { xiaohongshu: '小红书', douyin: '抖音', bilibili: 'B站', wechat_mp: '微信', instagram: 'Instagram', threads: 'Threads', web: '网页', unknown: '未知' }
function platformLabel(p: string | null | undefined): string { return PLATFORM_MAP[p || 'unknown'] || p || '未知' }

const platformOptions = [
  { value: 'xiaohongshu', label: '小红书' },
  { value: 'douyin', label: '抖音' },
  { value: 'bilibili', label: 'B站' },
  { value: 'wechat_mp', label: '微信' },
  { value: 'instagram', label: 'Instagram' },
  { value: 'threads', label: 'Threads' },
  { value: 'web', label: '网页' },
]

const VIRAL_LINE = 0.3

const activePlatform = ref('')
const report = ref<AnalysisReport | null>(null)
const loading = ref(false)
const error = ref('')
const scrollContainer = ref<HTMLElement | null>(null)
let reqSeq = 0

const overview = computed(() => report.value?.overview)
const totalEngagement = computed(() => {
  const o = overview.value
  if (!o) return 0
  return (o.total_likes || 0) + (o.total_collects || 0) + (o.total_comments || 0)
})
const viralRate = computed(() => {
  const o = overview.value
  if (!o || !o.total) return 0
  return (o.high_performers || 0) / o.total
})
const avgPerWork = computed(() => {
  const o = overview.value
  if (!o || !o.total) return 0
  return Math.round(totalEngagement.value / o.total)
})
const avgScoreDelta = computed(() => (overview.value?.avg_score || 0) - VIRAL_LINE)
const midPerformers = computed(() => {
  const o = overview.value
  if (!o) return 0
  return Math.max(0, (o.total || 0) - (o.high_performers || 0) - (o.low_performers || 0))
})
const distRows = computed(() => {
  const o = overview.value
  const total = o?.total || 0
  const items = [
    { key: 'high', label: '高表现', count: o?.high_performers || 0 },
    { key: 'mid', label: '中间层', count: midPerformers.value },
    { key: 'low', label: '低表现', count: o?.low_performers || 0 },
  ]
  return items.map(it => ({
    ...it,
    width: total > 0 ? (it.count / total) * 100 + '%' : '0%',
    pct: total > 0 ? Math.round((it.count / total) * 100) : 0,
  }))
})
const gaugeWidth = computed(() => Math.min(100, Math.max(0, (overview.value?.avg_score || 0) * 100)) + '%')
const replicateRate = computed(() => {
  const o = overview.value
  if (!o || !o.total) return 0
  return (o.replicated || 0) / o.total
})

const confidenceLabel = computed(() => {
  const c = report.value?.confidence
  if (c === 'high') return '高'
  if (c === 'medium') return '中'
  return '低'
})
const confidenceClass = computed(() => {
  const c = report.value?.confidence
  if (c === 'high') return 'an-conf-high'
  if (c === 'medium') return 'an-conf-mid'
  return 'an-conf-low'
})

const sortedPlatforms = computed(() =>
  [...(report.value?.platform_comparison || [])].sort((a, b) => b.avg_score - a.avg_score)
)
const platformMaxScore = computed(() =>
  Math.max(0.0001, ...sortedPlatforms.value.map(p => p.avg_score))
)

interface RankGroup { key: string; title: string; items: AnalysisRankItem[]; max: number }
const rankingGroups = computed<RankGroup[]>(() => {
  const raw = [
    { key: 'pattern', title: '标题模式', items: report.value?.pattern_ranking || [] },
    { key: 'emotion', title: '情绪触发', items: report.value?.emotion_ranking || [] },
    { key: 'structure', title: '内容结构', items: report.value?.structure_ranking || [] },
  ]
  return raw
    .filter(g => g.items.length > 0)
    .map(g => ({
      ...g,
      max: Math.max(0.0001, ...g.items.map(i => i.avg_score)),
    }))
})

const activeRankKey = ref('')
const activeRankGroup = computed<RankGroup | null>(() => {
  const groups = rankingGroups.value
  return groups.find(g => g.key === activeRankKey.value) || groups[0] || null
})

const topPerformers = computed(() => report.value?.top_performers || [])
const actionItems = computed(() => report.value?.action_items || [])

async function loadReport() {
  const seq = ++reqSeq
  loading.value = true
  error.value = ''
  try {
    const data = await getAnalysisReport(activePlatform.value || undefined)
    if (seq !== reqSeq) return
    report.value = data
    if (scrollContainer.value) scrollContainer.value.scrollTop = 0
  } catch (e: any) {
    if (seq !== reqSeq) return
    error.value = e?.response?.data?.detail || e?.message || '分析数据加载失败'
  } finally {
    if (seq === reqSeq) loading.value = false
  }
}

function formatNum(n: number | null | undefined): string {
  if (n === null || n === undefined) return '0'
  if (n >= 100000000) return (n / 100000000).toFixed(1) + '亿'
  if (n >= 10000) return (n / 10000).toFixed(1) + '万'
  return String(n)
}

function scoreText(s: number | null | undefined): string {
  if (s === null || s === undefined) return '0'
  return (s * 100).toFixed(1)
}

function barWidth(v: number, max: number): string {
  const ratio = max > 0 ? v / max : 0
  return Math.max(4, Math.min(100, ratio * 100)) + '%'
}

function priorityClass(p: string): string {
  if (p === 'high') return 'an-pri-high'
  if (p === 'low') return 'an-pri-low'
  return 'an-pri-mid'
}
function priorityLabel(p: string): string {
  if (p === 'high') return '高'
  if (p === 'low') return '低'
  return '中'
}

function onCoverError(e: Event, originalUrl: string | null) {
  const img = e.target as HTMLImageElement
  if (!originalUrl) return
  const currentSrc = img.src
  if (!currentSrc.includes('/api/proxy/image')) {
    if (
      originalUrl.includes('xhscdn.com') ||
      originalUrl.includes('xiaohongshu.com') ||
      originalUrl.includes('picasso-static') ||
      originalUrl.includes('douyinvod.com') ||
      originalUrl.includes('bytedanceimg.com') ||
      originalUrl.includes('bilibili.com') ||
      originalUrl.startsWith('http')
    ) {
      img.src = '/api/proxy/image?url=' + encodeURIComponent(originalUrl)
      return
    }
  }
  img.style.display = 'none'
}

onMounted(() => { loadReport() })
</script>

<style scoped>
.an-page { flex: 1; min-width: 0; height: 100%; display: flex; flex-direction: column; overflow: hidden; padding: 24px 20px 16px; background: var(--ma-bg-base, #EBEEF2); }
.da-content-card-wrapper { display: flex; flex-direction: column; flex: 1; min-height: 0; min-width: 0; overflow: hidden; }
.da-content-card { flex: 1; min-height: 0; min-width: 0; background: transparent; border-radius: 0; border: none; box-shadow: none; display: flex; flex-direction: column; overflow: hidden; }
.an-container { flex: 1; overflow-y: auto; padding: 20px 24px 24px; }

/* ====== Header ====== */
.an-header { display: flex; align-items: center; justify-content: space-between; gap: 16px; flex-wrap: wrap; margin-bottom: 14px; }
.an-header-left { display: flex; align-items: baseline; gap: 12px; min-width: 0; flex-wrap: wrap; }
.an-title { font-size: 18px; font-weight: 500; color: var(--rl-text-1, #1A1A1A); margin: 0; letter-spacing: -0.2px; line-height: 1.3; }
.an-meta { display: flex; align-items: center; gap: 8px; font-size: 12px; color: #9A9A9A; }
.an-meta-item b { color: var(--rl-text-2, #4B4B4B); font-weight: 500; }
.an-meta-div { width: 1px; height: 10px; background: #E5E5E5; }
.an-conf-high { color: #16A34A !important; }
.an-conf-mid { color: #D97706 !important; }
.an-conf-low { color: #9A9A9A !important; }
.an-header-right { display: flex; align-items: center; gap: 10px; }

.an-select-wrap { display: flex; align-items: center; gap: 6px; position: relative; }
.an-select-icon { color: #9A9A9A; flex-shrink: 0; }
.an-select { appearance: none; -webkit-appearance: none; padding: 7px 32px 7px 12px; border-radius: 12px; font-size: 13px; cursor: pointer; background: #fff; border: 1px solid #EDEDED; color: var(--rl-text-2, #4B4B4B); transition: all 0.15s; background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='%239A9A9A' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpolyline points='6 9 12 15 18 9'/%3E%3C/svg%3E"); background-repeat: no-repeat; background-position: right 10px center; }
.an-select:hover { border-color: #D4D4D8; }
.an-select:focus { outline: none; border-color: #999; box-shadow: 0 0 0 3px rgba(0,0,0,0.04); }

.an-refresh { width: 34px; height: 34px; border-radius: 12px; border: 1px solid #EDEDED; background: #fff; color: #6B6B6B; display: flex; align-items: center; justify-content: center; cursor: pointer; transition: all 0.15s; }
.an-refresh:hover:not(:disabled) { background: #F5F5F5; border-color: #D4D4D8; color: #1A1A1A; }
.an-refresh:disabled { opacity: 0.5; cursor: not-allowed; }
.an-spin { animation: an-rotate 0.9s linear infinite; }
@keyframes an-rotate { to { transform: rotate(360deg); } }

/* ====== 骨架 ====== */
.an-skeleton { display: flex; flex-direction: column; gap: 14px; }
.an-skeleton-kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; }
.an-skeleton-split { display: grid; grid-template-columns: minmax(0, 1.55fr) minmax(0, 1fr); gap: 12px; }
@media (max-width: 1100px) { .an-skeleton-split { grid-template-columns: 1fr; } }
.an-sk-card { height: 118px; border-radius: 16px; background: #fff; border: 1px solid #F0F0F0; position: relative; overflow: hidden; }
.an-sk-block { height: 220px; border-radius: 16px; background: #fff; border: 1px solid #F0F0F0; position: relative; overflow: hidden; }
.an-sk-short { height: 140px; }
.an-sk-card::after, .an-sk-block::after { content: ''; position: absolute; inset: 0; background: linear-gradient(90deg, transparent, rgba(0,0,0,0.035), transparent); animation: an-shimmer 1.4s infinite; }
@keyframes an-shimmer { 0% { transform: translateX(-100%); } 100% { transform: translateX(100%); } }

/* ====== 错误 / 空 ====== */
.an-error { display: flex; align-items: center; gap: 10px; padding: 14px 18px; border-radius: 14px; background: #FEF2F2; border: 1px solid #FECACA; color: #B91C1C; font-size: 13px; }
.an-error button { margin-left: auto; }

.an-empty { display: flex; flex-direction: column; align-items: center; justify-content: center; padding: 90px 0; gap: 10px; text-align: center; }
.an-empty-icon { color: #D4D4D8; }
.an-empty-title { font-size: 14px; font-weight: 500; color: var(--rl-text-1, #1A1A1A); margin: 4px 0 0; }
.an-empty-hint { font-size: 13px; color: #9A9A9A; margin: 0; max-width: 380px; line-height: 1.6; }
.an-empty-actions { display: flex; gap: 10px; margin-top: 8px; }

.an-btn-primary { padding: 9px 20px; border-radius: 12px; border: none; background: #1A1A1A; color: #fff; font-size: 13px; cursor: pointer; transition: background 0.15s; }
.an-btn-primary:hover { background: #333; }
.an-btn-ghost { padding: 9px 20px; border-radius: 12px; border: 1px solid #EDEDED; background: #fff; color: var(--rl-text-2, #4B4B4B); font-size: 13px; cursor: pointer; transition: all 0.15s; }
.an-btn-ghost:hover { background: #F5F5F5; border-color: #D4D4D8; }

/* ====== KPI ====== */
.an-body { display: flex; flex-direction: column; gap: 14px; transition: opacity 0.2s; }
.an-refreshing { opacity: 0.55; }

.an-kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 12px; }
.an-kpi { background: #fff; border: 1px solid #EFEFEF; border-radius: 16px; padding: 15px 16px 14px; display: flex; flex-direction: column; gap: 7px; transition: box-shadow 0.15s, border-color 0.15s; }
.an-kpi:hover { box-shadow: 0 4px 14px rgba(0,0,0,0.04); border-color: #E6E6E6; }
.an-kpi-top { display: flex; align-items: center; justify-content: space-between; }
.an-kpi-label { font-size: 12px; color: #8E8E93; letter-spacing: 0.2px; }
.an-kpi-icon { color: #C4C9D4; }
.an-kpi-value { font-size: 18px; font-weight: 500; color: var(--rl-text-1, #1A1A1A); line-height: 1.1; letter-spacing: -0.4px; font-variant-numeric: tabular-nums; }
.an-kpi-unit { font-size: 12px; font-weight: 400; color: #9A9A9A; margin-left: 3px; }
.an-kpi-foot { font-size: 12px; color: #9A9A9A; display: flex; flex-wrap: wrap; gap: 5px; align-items: center; font-variant-numeric: tabular-nums; }
.an-kpi-foot-legend { gap: 10px; }
.an-kpi-delta { padding: 1px 7px; border-radius: 999px; font-size: 11px; font-weight: 500; }
.an-kpi-delta.is-good { background: #ECFDF5; color: #15803D; }
.an-kpi-delta.is-bad { background: #FEF2F2; color: #B91C1C; }
.an-foot-good { color: #16A34A; }
.an-foot-bad { color: #DC2626; }
.an-foot-dot { color: #D4D4D8; }

.an-kpi-track { height: 5px; border-radius: 999px; background: #F2F2F4; overflow: hidden; display: flex; gap: 2px; margin-top: auto; }
.an-stack-seg { height: 100%; border-radius: 999px; transition: width 0.35s ease; }
.an-stack-seg.is-high { background: #FF2442; }
.an-stack-seg.is-mid { background: #FFB3BE; }
.an-stack-seg.is-low { background: #E3E3E8; }
.an-kpi-track-fill { height: 100%; border-radius: 999px; background: linear-gradient(90deg, #FF8FA3, #FF2442); transition: width 0.35s ease; }

.an-kpi-gauge { position: relative; height: 5px; border-radius: 999px; background: #F2F2F4; margin-top: auto; }
.an-kpi-gauge-fill { height: 100%; border-radius: 999px; background: #C4C9D4; transition: width 0.35s ease; }
.an-kpi-gauge-mark { position: absolute; top: -3px; bottom: -3px; width: 2px; border-radius: 2px; background: #FF2442; }

.an-leg { display: inline-flex; align-items: center; gap: 4px; }
.an-leg-dot { width: 6px; height: 6px; border-radius: 2px; display: inline-block; flex-shrink: 0; }
.an-leg-dot.is-high, .an-leg-dot.is-like { background: #FF2442; }
.an-leg-dot.is-mid, .an-leg-dot.is-collect { background: #FFB3BE; }
.an-leg-dot.is-low, .an-leg-dot.is-comment { background: #C4C9D4; }

/* ====== Section ====== */
.an-section { background: #fff; border: 1px solid #EFEFEF; border-radius: 16px; padding: 16px 18px 18px; }
.an-section-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 14px; }
.an-section-title { font-size: 14px; font-weight: 500; color: var(--rl-text-1, #1A1A1A); margin: 0; letter-spacing: -0.1px; }
.an-section-desc { font-size: 12px; color: #9A9A9A; }
.an-link-btn { display: inline-flex; align-items: center; gap: 2px; border: none; background: transparent; color: #6B6B6B; font-size: 12px; cursor: pointer; padding: 4px 6px; border-radius: 8px; transition: all 0.15s; }
.an-link-btn:hover { background: #F5F5F5; color: #1A1A1A; }

/* ====== 双栏：平台对比 + 表现分布 ====== */
.an-split { display: grid; grid-template-columns: minmax(0, 1.55fr) minmax(0, 1fr); gap: 12px; align-items: stretch; }
@media (max-width: 1100px) { .an-split { grid-template-columns: 1fr; } }
.an-split .an-section { display: flex; flex-direction: column; }

.an-dist-bar { display: flex; gap: 3px; height: 10px; border-radius: 999px; overflow: hidden; background: #F2F2F4; }
.an-dist-seg { height: 100%; transition: width 0.35s ease; }
.an-dist-seg.is-high { background: #FF2442; }
.an-dist-seg.is-mid { background: #FFB3BE; }
.an-dist-seg.is-low { background: #E3E3E8; }
.an-dist-list { display: flex; flex-direction: column; gap: 9px; margin-top: 14px; }
.an-dist-item { display: flex; align-items: center; gap: 8px; font-size: 12px; color: #6B6B6B; }
.an-dist-label { flex: 1; min-width: 0; }
.an-dist-item b { color: var(--rl-text-1, #1A1A1A); font-weight: 500; font-variant-numeric: tabular-nums; }
.an-dist-pct { width: 42px; text-align: right; color: #9A9A9A; font-variant-numeric: tabular-nums; }
.an-dist-foot { margin-top: auto; padding-top: 12px; border-top: 1px solid #F2F2F4; display: flex; gap: 18px; font-size: 12px; color: #9A9A9A; }
.an-dist-foot b { color: var(--rl-text-1, #1A1A1A); font-weight: 500; margin-left: 4px; font-variant-numeric: tabular-nums; }

/* ====== 平台对比 ====== */
.an-platform-list { display: flex; flex-direction: column; gap: 12px; }
.an-platform-row { display: grid; grid-template-columns: 76px minmax(0, 1fr) 56px; align-items: center; gap: 12px; }
.an-platform-name { font-size: 13px; color: var(--rl-text-2, #4B4B4B); font-weight: 500; }
.an-platform-bar-wrap { height: 8px; background: #F5F5F5; border-radius: 6px; overflow: hidden; }
.an-platform-bar { height: 100%; background: linear-gradient(90deg, #FF8FA3, #FF2442); border-radius: 6px; transition: width 0.35s ease; }
.an-platform-score { font-size: 13px; font-weight: 500; color: var(--rl-text-1, #1A1A1A); text-align: right; font-variant-numeric: tabular-nums; }
.an-platform-meta { grid-column: 2 / -1; font-size: 11px; color: #9A9A9A; margin-top: -6px; }

/* ====== 榜单（标签页切换） ====== */
.an-section-head-tabs { align-items: center; flex-wrap: wrap; }
.an-rank-tabs { display: flex; gap: 4px; padding: 3px; background: #F5F5F7; border-radius: 10px; }
.an-rank-tab { display: inline-flex; align-items: center; gap: 6px; border: none; background: transparent; color: #6B6B6B; font-size: 12px; padding: 5px 10px; border-radius: 8px; cursor: pointer; transition: all 0.15s; }
.an-rank-tab:hover { color: #1A1A1A; }
.an-rank-tab.is-active { background: #fff; color: var(--rl-text-1, #1A1A1A); font-weight: 500; box-shadow: 0 1px 2px rgba(0,0,0,0.06); }
.an-rank-tab-count { font-size: 10px; color: #9A9A9A; font-variant-numeric: tabular-nums; }
.an-rank-tab.is-active .an-rank-tab-count { color: #FF2442; }
.an-rank-list { display: flex; flex-direction: column; gap: 12px; }
.an-rank-row { display: flex; align-items: center; gap: 10px; }
.an-rank-idx { width: 18px; height: 18px; border-radius: 6px; background: #EFEFEF; color: #9A9A9A; font-size: 11px; display: flex; align-items: center; justify-content: center; flex-shrink: 0; font-variant-numeric: tabular-nums; }
.an-rank-idx-top { background: #1A1A1A; color: #fff; }
.an-rank-main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 5px; }
.an-rank-name-row { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; }
.an-rank-name { font-size: 13px; color: var(--rl-text-2, #4B4B4B); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.an-rank-val { font-size: 12px; font-weight: 500; color: var(--rl-text-1, #1A1A1A); flex-shrink: 0; font-variant-numeric: tabular-nums; }
.an-rank-track { height: 5px; background: #F2F2F4; border-radius: 4px; overflow: hidden; }
.an-rank-bar { height: 100%; background: #C4C9D4; border-radius: 4px; transition: width 0.35s ease; }
.an-rank-bar-top { background: linear-gradient(90deg, #FF8FA3, #FF2442); }
.an-rank-samples { font-size: 11px; color: #B0B0B0; flex-shrink: 0; width: 36px; text-align: right; font-variant-numeric: tabular-nums; }
.an-rank-hint { margin-top: 14px; padding-top: 12px; border-top: 1px solid #F2F2F4; font-size: 11px; color: #B0B0B0; }

/* ====== Top 作品 ====== */
.an-top-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 12px; }
.an-top-card { display: flex; gap: 12px; padding: 10px; border-radius: 12px; border: 1px solid #F0F0F0; cursor: pointer; transition: all 0.15s; background: #fff; }
.an-top-card:hover { border-color: #EDEDED; box-shadow: 0 4px 12px rgba(0,0,0,0.05); transform: translateY(-1px); }
.an-top-cover { position: relative; width: 62px; height: 62px; border-radius: 10px; overflow: hidden; background: #F5F5F5; flex-shrink: 0; display: flex; align-items: center; justify-content: center; }
.an-top-rank { position: absolute; top: 5px; left: 5px; z-index: 1; min-width: 18px; height: 18px; padding: 0 4px; border-radius: 6px; background: rgba(0, 0, 0, 0.55); color: #fff; font-size: 10px; font-weight: 500; display: flex; align-items: center; justify-content: center; font-variant-numeric: tabular-nums; }
.an-top-rank.is-top { background: #FF2442; }
.an-top-cover img { width: 100%; height: 100%; object-fit: cover; }
.an-top-cover-ph { color: #D4D4D8; }
.an-top-body { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 5px; justify-content: center; }
.an-top-title { font-size: 14px; font-weight: 500; color: var(--rl-text-1, #1A1A1A); overflow: hidden; text-overflow: ellipsis; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; line-height: 1.45; }
.an-top-meta { display: flex; align-items: center; gap: 6px; }
.an-top-score { font-size: 12px; font-weight: 500; color: #FF2442; }
.an-top-plat { font-size: 11px; color: #9A9A9A; }
.an-top-stats { display: flex; gap: 10px; font-size: 11px; color: #9A9A9A; }
.an-top-stats span { display: inline-flex; align-items: center; gap: 3px; }

/* ====== 行动建议 ====== */
.an-actions-list { display: flex; flex-direction: column; gap: 10px; }
.an-action-row { display: flex; align-items: flex-start; gap: 10px; padding: 12px 14px; border-radius: 12px; background: #FAFAFA; border: 1px solid #F0F0F0; }
.an-action-priority { width: 22px; height: 22px; border-radius: 7px; font-size: 11px; font-weight: 500; display: flex; align-items: center; justify-content: center; flex-shrink: 0; }
.an-pri-high { background: #FEE2E2; color: #DC2626; }
.an-pri-mid { background: #FEF3C7; color: #D97706; }
.an-pri-low { background: #E5E7EB; color: #6B7280; }
.an-action-main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 3px; }
.an-action-text { font-size: 13px; font-weight: 500; color: var(--rl-text-1, #1A1A1A); }
.an-action-reason { font-size: 12px; color: #9A9A9A; line-height: 1.5; }
</style>