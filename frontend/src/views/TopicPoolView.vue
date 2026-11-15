<template>
<AppShell current-page="topic-pool" shell-class="tp-shell">

    <!-- ============ MIDDLE COLUMN ============ -->
    <main class="tp-page">
      <div class="tp-content-card-wrapper">
      <div class="tp-content-card">
      <div class="tp-container" ref="scrollContainer">

      <!-- 顶部标题栏：统计 + 主操作 -->
      <section class="tp-header">
        <div class="tp-header-main">
          <h2 class="tp-header-title">选题池</h2>
          <div class="tp-header-stats">
            <span class="tp-header-stat" title="全部选题"><Layers />{{ stats.total }}</span>
            <span class="tp-header-stat" title="已收藏"><Star />{{ stats.favorited }}</span>
            <span class="tp-header-stat" title="监控入库"><Radar />{{ stats.monitor_count }}</span>
            <span class="tp-header-stat" title="平均热度分"><Flame />{{ stats.avg_heat_score }}</span>
          </div>
        </div>
        <div class="tp-header-actions">
          <button
            class="tp-ghost-btn"
            :class="{ 'is-active': fetchPanelOpen }"
            @click="toggleFetchPanel"
            type="button"
          >
            <DownloadCloud /><span>搜索</span>
          </button>
          <button
            class="tp-ghost-btn"
            @click="doMonitorFetch"
            :disabled="monitorFetching"
            type="button"
            title="手动触发一次监控热点（评分入池）"
          >
            <Zap v-if="!monitorFetching" />
            <Loader2 class="tp-spin" v-else />
            <span>{{ monitorFetching ? '监控热点中...' : '监控热点' }}</span>
          </button>
          <button class="tp-ghost-btn tp-ghost-btn-primary" @click="refresh" :disabled="loading" type="button">
            <RefreshCw :class="{ 'tp-spin': loading }" /><span>刷新</span>
          </button>
        </div>
      </section>

      <!-- 抓取面板（默认收起，点「搜索」展开） -->
      <section class="tp-fetch-panel" v-if="fetchPanelOpen">
        <div class="tp-fetch-form">
          <input
            class="tp-fetch-input"
            v-model="fetchForm.keyword"
            type="text"
            placeholder="输入关键词抓取爆款内容，或直接粘贴网页链接采集..."
            @keydown.enter="doFetch"
            :disabled="fetching"
          />
          <select class="tp-fetch-select" v-model="fetchForm.platform" :disabled="fetching">
            <option v-for="plat in platformTabs" :key="plat.value" :value="plat.value">
              {{ plat.label }}{{ plat.is_hotboard ? ' 🔥' : '' }}{{ plat.billable ? '（付费）' : '' }}
            </option>
          </select>
          <button
            class="tp-fetch-btn"
            @click="doFetch"
            :disabled="fetching || !fetchForm.keyword.trim()"
            type="button"
          >
            <Download v-if="!fetching" />
            <Loader2 class="tp-spin" v-else />
            <span>{{ fetching ? '抓取中...' : '抓取' }}</span>
          </button>
          <button class="tp-fetch-close" @click="fetchPanelOpen = false" type="button" title="收起">
            <X />
          </button>
        </div>
        <div class="tp-fetch-hint">通过搜索引擎安全获取热点数据，零风控</div>
      </section>

      <div class="tp-fetch-msg" v-if="fetchMessage">{{ fetchMessage }}</div>
      <div class="tp-fetch-msg tp-fetch-msg-monitor" v-if="monitorMessage">{{ monitorMessage }}</div>

      <!-- 平台分类标签栏 -->
      <section class="tp-platform-tabs">
        <button
          class="tp-plat-chip"
          :class="{ 'is-active': filters.platform === '' }"
          @click="setPlatform('')"
          type="button"
        >
          <span class="tp-plat-logo tp-plat-logo-all">
            <LayoutGrid :size="15" />
          </span>
          <span class="tp-plat-label">全部</span>
          <span class="tp-plat-count">{{ stats.total }}</span>
        </button>
        <button
          v-for="plat in platformTabs"
          :key="plat.value"
          class="tp-plat-chip"
          :class="{ 'is-active': filters.platform === plat.value, 'is-hotboard': plat.is_hotboard }"
          @click="setPlatform(plat.value)"
          type="button"
        >
          <PlatformLogo
            :platform="plat.value"
            :name="plat.label"
            :size="16"
            class="tp-plat-logo"
            :class="{ 'is-muted': filters.platform !== plat.value }"
          />
          <span class="tp-plat-label">{{ plat.label }}</span>
          <span class="tp-plat-count" v-if="stats.platforms[plat.value]">{{ stats.platforms[plat.value] }}</span>
          <Flame v-if="plat.is_hotboard" :size="11" class="tp-plat-flame" />
        </button>
      </section>

      <!-- 筛选栏 -->
      <section class="tp-filters">
        <div class="tp-filter-item tp-filter-search">
          <div class="tp-search-wrap">
            <Search class="tp-search-icon" />
            <input
              class="tp-input"
              v-model="filters.keyword"
              type="text"
              placeholder="搜索标题、摘要或关键词..."
            />
            <button
              v-if="filters.keyword"
              class="tp-search-clear"
              @click="filters.keyword = ''"
              type="button"
              title="清空搜索"
            >
              <X />
            </button>
          </div>
        </div>

        <div class="tp-filter-item tp-filter-source">
          <div class="tp-source-group">
            <button
              class="tp-source-chip"
              :class="{ 'is-active': filters.auto_source === '' }"
              @click="filters.auto_source = ''"
              type="button"
            >全部</button>
            <button
              class="tp-source-chip"
              :class="{ 'is-active': filters.auto_source === 'monitor' }"
              @click="filters.auto_source = 'monitor'"
              type="button"
            >监控</button>
            <button
              class="tp-source-chip"
              :class="{ 'is-active': filters.auto_source === 'manual' }"
              @click="filters.auto_source = 'manual'"
              type="button"
            >手动</button>
          </div>
        </div>

        <div class="tp-filter-item tp-filter-toggle">
          <label class="tp-switch-label">
            <span>仅看收藏</span>
            <button
              class="tp-switch"
              :class="{ 'is-on': filters.favorited_only }"
              @click="filters.favorited_only = !filters.favorited_only"
              type="button"
              role="switch"
              :aria-checked="filters.favorited_only"
            ></button>
          </label>
        </div>

        <div class="tp-sort-group" role="group" aria-label="排序方式">
          <button
            class="tp-sort-seg"
            :class="{ 'is-active': filters.sort === 'created_desc' }"
            @click="setSort('created_desc')"
            type="button"
            title="最新入库优先"
          >
            <Clock /><span>最新</span>
          </button>
          <button
            class="tp-sort-seg"
            :class="{ 'is-active': filters.sort === 'heat_desc' }"
            @click="setSort('heat_desc')"
            type="button"
            title="按热度分从高到低"
          >
            <ArrowDownWideNarrow /><span>热度</span>
          </button>
          <button
            class="tp-sort-seg"
            :class="{ 'is-active': filters.sort === 'personalized' }"
            @click="setSort('personalized')"
            type="button"
            title="按你的画像与搜索习惯加权"
          >
            <Sparkles /><span>个性</span>
          </button>
        </div>
      </section>

      <!-- 情绪维度筛选（有监控数据时才出现） -->
      <section class="tp-dims" v-if="emotionOptions.length > 0">
        <span class="tp-dims-label">情绪</span>
        <button
          class="tp-dim-chip"
          :class="{ 'is-active': filters.emotion === '' }"
          @click="setEmotion('')"
          type="button"
        >全部</button>
        <button
          v-for="opt in emotionOptions"
          :key="opt.name"
          class="tp-dim-chip"
          :class="{ 'is-active': filters.emotion === opt.name }"
          @click="setEmotion(opt.name)"
          type="button"
        >
          <span>{{ opt.name }}</span>
          <span class="tp-dim-count">{{ opt.count }}</span>
        </button>
      </section>

      <!-- 列表区 -->
      <div class="tp-list-wrap">
        <!-- 骨架屏（首次加载 / 无旧数据时） -->
        <div v-if="loading && items.length === 0" class="tp-skeleton-grid">
          <div v-for="n in 8" :key="n" class="tp-skeleton-card">
            <div class="tp-skeleton-cover"></div>
            <div class="tp-skeleton-line tp-skeleton-line-lg"></div>
            <div class="tp-skeleton-line"></div>
            <div class="tp-skeleton-line tp-skeleton-line-sm"></div>
          </div>
        </div>

        <!-- 空状态 -->
        <div v-else-if="items.length === 0" class="tp-empty">
          <Inbox />
          <div class="tp-empty-title">暂无选题</div>
          <div class="tp-empty-desc">{{ emptyHint }}</div>
          <div class="tp-empty-actions">
            <button v-if="hasActiveFilters" class="tp-empty-btn" @click="clearFilters" type="button">
              <RefreshCw /><span>清除筛选</span>
            </button>
            <button class="tp-empty-btn tp-empty-btn-primary" @click="openFetchPanel" type="button">
              <DownloadCloud /><span>去抓取爆款</span>
            </button>
          </div>
        </div>

        <!-- 卡片列表 -->
        <template v-else>
          <div class="tp-list-bar">
            <button class="tp-select-all" @click="toggleSelectAll" type="button">
              <span class="tp-select-box" :class="{ 'is-checked': allSelected }">
                <Check v-if="allSelected" :size="11" />
              </span>
              <span>{{ allSelected ? '取消全选' : '全选本页' }}</span>
            </button>
            <span class="tp-list-bar-info">
              <span v-if="selectedCount > 0">已选 {{ selectedCount }} 条</span>
              <span v-else>本页 {{ items.length }} 条</span>
            </span>
          </div>

          <section class="tp-list" :class="{ 'is-refreshing': listRefreshing }">
            <article
              v-for="(item, idx) in items"
              :key="item.id"
              class="tp-card"
              :class="{ 'is-selected': isSelected(item.id) }"
              :style="{ animationDelay: `${Math.min(idx, 12) * 40}ms` }"
            >
              <!-- 封面区（仅在有图片时渲染） -->
              <div class="tp-card-cover" v-if="hasCoverImg(item)">
                <img
                  :src="safeImageUrl(item.cover_img!)"
                  :alt="item.title"
                  loading="lazy"
                  referrerpolicy="no-referrer"
                  @error="onCoverImgError($event, item)"
                />
                <!-- 平台标签（左上角悬浮） -->
                <span class="tp-platform-tag tp-platform-tag-float" :style="platformStyle(item.platform)">{{ platformLabel(item.platform) }}</span>
                <!-- 图片数量角标 -->
                <span class="tp-img-count" v-if="(item.images?.length || 0) > 1">{{ item.images.length }}图</span>
                <!-- 监控热度角标（右上角） -->
                <span class="tp-heat-badge" v-if="item.auto_source === 'monitor'" :class="`tp-heat-status-${heatStatusClass(item.heat_status)}`">
                  <Flame />
                  <span>{{ item.heat_score.toFixed(0) }}</span>
                </span>
                <!-- hover 时浮现的快捷操作 -->
                <div class="tp-cover-actions">
                  <button class="tp-cover-btn tp-cover-btn-primary" @click="startWorkflowFromTopic(item)" type="button" title="以此选题发起工作流">
                    <Play /><span>发起工作流</span>
                  </button>
                  <a v-if="item.url" class="tp-cover-btn" :href="item.url" target="_blank" rel="noopener noreferrer" title="查看原文">
                    <ExternalLink />
                  </a>
                </div>
              </div>

              <!-- 内容区 -->
              <div class="tp-card-body">
                <div class="tp-card-meta-top">
                  <span v-if="!hasCoverImg(item)" class="tp-platform-tag" :style="platformStyle(item.platform)">{{ platformLabel(item.platform) }}</span>
                  <span v-else-if="item.author" class="tp-author">
                    <User />
                    <span>{{ item.author }}</span>
                  </span>
                  <span class="tp-card-meta-right">
                    <span class="tp-card-monitor" v-if="item.auto_source === 'monitor'">{{ item.heat_status }}</span>
                    <button
                      class="tp-select-box"
                      :class="{ 'is-checked': isSelected(item.id) }"
                      @click.stop="toggleSelect(item.id)"
                      type="button"
                      role="checkbox"
                      :aria-checked="isSelected(item.id)"
                      :title="isSelected(item.id) ? '取消选择' : '选择此选题'"
                    >
                      <Check v-if="isSelected(item.id)" :size="11" />
                    </button>
                  </span>
                </div>
                <h3 class="tp-card-title tp-card-title-clickable" @click="goDetail(item.id)" title="查看详情">{{ item.title }}</h3>
                <p class="tp-card-summary" v-if="item.summary">{{ item.summary }}</p>
                <!-- 无封面图时：作者信息放在摘要下方 -->
                <div class="tp-no-cover-author" v-if="!hasCoverImg(item) && item.author">
                  <User /><span>{{ item.author }}</span>
                </div>
                <div class="tp-card-dims" v-if="item.dimensions">
                  <span v-if="item.dimensions.emotion">{{ item.dimensions.emotion }}</span>
                  <span v-if="item.dimensions.scene">{{ item.dimensions.scene }}</span>
                  <span v-if="item.dimensions.visual">{{ item.dimensions.visual }}</span>
                </div>
              </div>

              <!-- 底部：指标 + 操作 -->
              <div class="tp-card-bottom">
                <div class="tp-metrics">
                  <span class="tp-metric" v-if="item.fans_count > 0" title="粉丝">
                    <Users /><span>{{ formatNum(item.fans_count) }}</span>
                  </span>
                  <span class="tp-metric" title="点赞">
                    <ThumbsUp /><span>{{ formatNum(item.likes) }}</span>
                  </span>
                  <span class="tp-metric" v-if="item.collects > 0" title="收藏">
                    <Bookmark /><span>{{ formatNum(item.collects) }}</span>
                  </span>
                  <span class="tp-metric" title="评论">
                    <MessageSquare /><span>{{ formatNum(item.comments) }}</span>
                  </span>
                </div>
                <div class="tp-actions">
                  <a v-if="item.url" class="tp-orig-link" :href="item.url" target="_blank" rel="noopener noreferrer" title="查看原文">
                    <ExternalLink />
                  </a>
                  <button class="tp-icon-btn" :class="{ 'is-favorited': item.is_favorited }" @click="toggleFavorite(item)" type="button" :title="item.is_favorited ? '取消收藏' : '收藏'">
                    <Star />
                  </button>
                  <button class="tp-icon-btn" @click="deleteItem(item)" type="button" title="删除">
                    <Trash2 />
                  </button>
                  <button class="tp-workflow-btn" @click="startWorkflowFromTopic(item)" type="button" title="以此选题发起工作流">
                    <Play /><span>发起工作流</span>
                  </button>
                </div>
              </div>
            </article>
          </section>
        </template>
      </div>

    </div>

    <!-- 批量操作条 -->
    <section class="tp-batch-bar" v-if="selectedCount > 0">
      <span class="tp-batch-count">已选 {{ selectedCount }} 条</span>
      <div class="tp-batch-actions">
        <button class="tp-batch-btn" @click="batchFavorite" type="button">
          <Star /><span>批量收藏</span>
        </button>
        <button class="tp-batch-btn tp-batch-btn-danger" @click="batchDelete" type="button">
          <Trash2 /><span>批量删除</span>
        </button>
        <button class="tp-batch-btn tp-batch-btn-ghost" @click="clearSelection" type="button">
          <span>取消选择</span>
        </button>
      </div>
    </section>

    <!-- 分页（固定在 tp-page 底部，独立于瀑布流容器） -->
    <section class="tp-pagination" v-if="items.length > 0">
      <button
        class="tp-page-btn"
        :disabled="page <= 1"
        @click="changePage(page - 1)"
        type="button"
      >
        <ChevronLeft />
        <span>上一页</span>
      </button>

      <div class="tp-page-numbers">
        <button
          v-for="p in pageNumbers"
          :key="p"
          class="tp-page-num"
          :class="{ 'is-active': p === page }"
          @click="changePage(p)"
          type="button"
        >{{ p }}</button>
      </div>

      <button
        class="tp-page-btn"
        :disabled="page >= totalPages"
        @click="changePage(page + 1)"
        type="button"
      >
        <span>下一页</span>
        <ChevronRight />
      </button>

      <span class="tp-page-info">第 {{ page }} / {{ totalPages }} 页 · 共 {{ total }} 条</span>
    </section>
      </div>
      <div class="tp-content-resize-handle" @mousedown="startContentResize">
        <div class="tp-content-resize-line"></div>
      </div>
      </div>
    </main>

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
</AppShell>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  Layers, Star, DownloadCloud, Download, Loader2,
  Search, RefreshCw, Inbox, User, Users, ThumbsUp, Bookmark,
  MessageSquare, Play, Trash2, ExternalLink, ChevronLeft, ChevronRight,
  Radar, Flame, Zap, ArrowDownWideNarrow, Clock, Sparkles, Check, X, LayoutGrid,
} from 'lucide-vue-next'
import AppShell from '@/components/layout/AppShell.vue'
import PlatformLogo from '@/components/chat/PlatformLogo.vue'
import { topicPoolApi, type TopicPoolItem, type TopicPoolStats, type TopicPoolFetchResponse } from '@/api/topic_pool'
import { useConfirm } from '@/composables/useConfirm'
import ConfirmDialog from '@/components/common/ConfirmDialog.vue'

const { state: confirmState, confirm, onConfirm, onCancel } = useConfirm()

const emit = defineEmits<{
  'start-workflow': [reference: Record<string, unknown>]
}>()

const router = useRouter()

// ===== 状态 =====
const items = ref<TopicPoolItem[]>([])
const stats = ref<TopicPoolStats>({ total: 0, favorited: 0, monitor_count: 0, avg_heat_score: 0, platforms: {}, emotions: {} })
const loading = ref(false)          // 首次加载 / 无旧数据：显示骨架屏
const listRefreshing = ref(false)   // 有旧数据：静默刷新，保留列表
const scrollContainer = ref<HTMLElement | null>(null)

const filters = reactive({
  platform: '',          // '' = 全部
  keyword: '',
  favorited_only: false,
  auto_source: '' as '' | 'manual' | 'monitor',  // '' = 全部
  emotion: '',
  sort: 'created_desc' as 'created_desc' | 'heat_desc' | 'personalized',
})
const page = ref(1)
const size = ref(20)
const total = ref(0)

// ===== 批量选择 =====
const selectedIds = ref<Set<string>>(new Set())
const selectedCount = computed(() => selectedIds.value.size)
const allSelected = computed(() =>
  items.value.length > 0 && items.value.every(i => selectedIds.value.has(i.id)),
)

function isSelected(id: string): boolean {
  return selectedIds.value.has(id)
}

function toggleSelect(id: string) {
  const next = new Set(selectedIds.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  selectedIds.value = next
}

function toggleSelectAll() {
  if (allSelected.value) {
    selectedIds.value = new Set()
    return
  }
  selectedIds.value = new Set(items.value.map(i => i.id))
}

function clearSelection() {
  selectedIds.value = new Set()
}

// ===== 情绪维度选项（来自统计接口） =====
const emotionOptions = computed(() => {
  const map = stats.value.emotions || {}
  return Object.entries(map)
    .filter(([name, count]) => name && count > 0)
    .map(([name, count]) => ({ name, count }))
    .sort((a, b) => b.count - a.count)
})

const hasActiveFilters = computed(() =>
  !!filters.platform || !!filters.keyword.trim() || filters.favorited_only
  || !!filters.auto_source || !!filters.emotion,
)

const emptyHint = computed(() =>
  hasActiveFilters.value
    ? '当前筛选条件下没有选题，试试放宽条件'
    : '选题池还是空的，先抓取一批爆款内容吧',
)

// 平台分类标签（动态从 API 加载，含热点源 hb-* 平台）
const platformTabs = ref<{ value: string; label: string; is_hotboard?: boolean; is_default?: boolean; billable?: boolean }[]>([])

// 选题池手动抓取的默认平台：优先免费源（sogou > builtin > 任意非付费非热榜源）。
// 付费源（DeepSeek 等）不设为默认，避免用户每次抓取都产生费用；
// 用户仍可在下拉框里主动选择付费源。
const PREFERRED_FREE_PLATFORMS = ['sogou', 'builtin']

function pickDefaultFetchPlatform(list: { value: string; is_hotboard?: boolean; billable?: boolean }[]): string {
  for (const name of PREFERRED_FREE_PLATFORMS) {
    const hit = list.find(t => t.value === name && !t.billable)
    if (hit) return hit.value
  }
  const free = list.find(t => !t.billable && !t.is_hotboard)
  if (free) return free.value
  return list[0]?.value || ''
}

async function loadPlatformTabs() {
  try {
    const resp = await topicPoolApi.getPlatforms()
    const list = Array.isArray(resp) ? resp : []
    platformTabs.value = list.map((p: any) => ({
      value: p.name,
      label: p.label,
      is_hotboard: p.is_hotboard || false,
      is_default: p.is_default || false,
      billable: p.billable || false,
    }))
    if (platformTabs.value.length > 0 && !platformTabs.value.find(t => t.value === fetchForm.platform)) {
      fetchForm.platform = pickDefaultFetchPlatform(platformTabs.value)
    }
  } catch (e) {
    console.error('加载平台列表失败:', e)
    platformTabs.value = [
      { value: 'sogou', label: '头条搜索' },
      { value: 'builtin', label: '热门话题' },
      { value: 'deepseek_api', label: '全网搜索(DeepSeek API)', is_default: true, billable: true },
      { value: 'deepseek', label: '全网搜索(DeepSeek)', billable: true },
      { value: 'zhihu', label: '知乎' },
      { value: 'weibo', label: '微博' },
      { value: 'hackernews', label: 'HackerNews' },
      { value: 'tavily', label: 'Tavily' },
    ]
    fetchForm.platform = pickDefaultFetchPlatform(platformTabs.value)
  }
}

// ===== 抓取状态 =====
const fetching = ref(false)
const fetchPanelOpen = ref(false)
const fetchForm = reactive({
  keyword: '',
  platform: '',
})
const fetchMessage = ref('')  // 抓取结果提示

function toggleFetchPanel() {
  fetchPanelOpen.value = !fetchPanelOpen.value
}

function openFetchPanel() {
  fetchPanelOpen.value = true
}

// ===== 监控抓取状态 =====
const monitorFetching = ref(false)
const monitorMessage = ref('')

const totalPages = computed(() => Math.max(1, Math.ceil(total.value / size.value)))

// 分页页码列表（当前页前后各 2 个，共最多 5 个）
const pageNumbers = computed(() => {
  const cur = page.value
  const tp = totalPages.value
  const arr: number[] = []
  const start = Math.max(1, cur - 2)
  const end = Math.min(tp, start + 4)
  const realStart = Math.max(1, end - 4)
  for (let i = realStart; i <= end; i++) arr.push(i)
  return arr
})

// ===== 数据加载 =====
// 请求序号：快速切换筛选/翻页时，丢弃过期响应，避免旧结果覆盖新结果
let listSeq = 0

async function loadList() {
  const seq = ++listSeq
  const hasData = items.value.length > 0
  if (hasData) listRefreshing.value = true
  else loading.value = true
  try {
    const resp = await topicPoolApi.list({
      platform: filters.platform || undefined,
      keyword: filters.keyword.trim() || undefined,
      favorited_only: filters.favorited_only || undefined,
      auto_source: filters.auto_source || undefined,
      emotion: filters.emotion || undefined,
      sort: filters.sort,
      page: page.value,
      size: size.value,
    })
    if (seq !== listSeq) return
    items.value = resp.items || []
    total.value = resp.total || 0
    // 修正后端返回的实际页码
    if (resp.page && resp.page !== page.value) {
      page.value = resp.page
    }
    // 换了一批数据，清空上一批的选择
    clearSelection()
  } catch (e) {
    if (seq !== listSeq) return
    console.error('加载选题列表失败:', e)
    items.value = []
    total.value = 0
  } finally {
    if (seq === listSeq) {
      loading.value = false
      listRefreshing.value = false
    }
  }
}

// 回到列表顶部（翻页/切筛选后避免停留在旧滚动位置）
function scrollToTop() {
  const el = scrollContainer.value
  if (!el) return
  el.scrollTo({ top: 0, behavior: 'auto' })
}

// 筛选变化统一入口：重置到第一页并回到顶部
function applyFilterChange() {
  if (page.value === 1) {
    loadList()
    scrollToTop()
  } else {
    page.value = 1
  }
}

function setPlatform(value: string) {
  if (filters.platform === value) return
  filters.platform = value
}

function setEmotion(value: string) {
  if (filters.emotion === value) return
  filters.emotion = value
}

function setSort(value: 'created_desc' | 'heat_desc' | 'personalized') {
  if (filters.sort === value) return
  filters.sort = value
  applyFilterChange()
}

function clearFilters() {
  filters.platform = ''
  filters.keyword = ''
  filters.favorited_only = false
  filters.auto_source = ''
  filters.emotion = ''
}

async function loadStats() {
  try {
    const resp = await topicPoolApi.getStats()
    stats.value = resp
  } catch (e) {
    console.error('加载统计失败:', e)
  }
}

// ===== 操作 =====
async function toggleFavorite(item: TopicPoolItem) {
  const prev = item.is_favorited
  item.is_favorited = !prev  // 乐观更新
  try {
    const resp = await topicPoolApi.toggleFavorite(item.id)
    item.is_favorited = resp.is_favorited
    loadStats()
  } catch (e) {
    console.error('切换收藏失败:', e)
    item.is_favorited = prev  // 回滚
  }
}

async function deleteItem(item: TopicPoolItem) {
  const ok = await confirm({
    title: '删除选题',
    message: `确认删除选题「${item.title}」？\n此操作不可撤销。`,
    danger: true,
  })
  if (!ok) return
  try {
    await topicPoolApi.deleteItem(item.id)
    items.value = items.value.filter(i => i.id !== item.id)
    total.value = Math.max(0, total.value - 1)
    loadStats()
  } catch (e) {
    console.error('删除失败:', e)
  }
}

// ===== 批量操作 =====
async function batchFavorite() {
  const targets = items.value.filter(i => isSelected(i.id) && !i.is_favorited)
  if (targets.length === 0) {
    clearSelection()
    return
  }
  for (const t of targets) {
    try {
      const resp = await topicPoolApi.toggleFavorite(t.id)
      t.is_favorited = resp.is_favorited
    } catch (e) {
      console.error('批量收藏失败:', t.id, e)
    }
  }
  clearSelection()
  loadStats()
}

async function batchDelete() {
  const ids = Array.from(selectedIds.value)
  if (ids.length === 0) return
  const ok = await confirm({
    title: '批量删除选题',
    message: `确认删除选中的 ${ids.length} 条选题？\n此操作不可撤销。`,
    danger: true,
  })
  if (!ok) return
  let removed = 0
  for (const id of ids) {
    try {
      await topicPoolApi.deleteItem(id)
      removed += 1
    } catch (e) {
      console.error('批量删除失败:', id, e)
    }
  }
  clearSelection()
  if (removed > 0) {
    await Promise.all([loadList(), loadStats()])
  }
}

// ===== 主动抓取 =====
async function doFetch() {
  const keyword = fetchForm.keyword.trim()
  if (!keyword || fetching.value) return

  // 兜底：平台列表还没加载完时，用免费源默认值，避免把空平台传给后端
  if (!fetchForm.platform) {
    fetchForm.platform = pickDefaultFetchPlatform(platformTabs.value)
  }

  fetching.value = true
  fetchMessage.value = ''
  try {
    const resp: TopicPoolFetchResponse = await topicPoolApi.fetch({
      keyword,
      platform: fetchForm.platform,
      limit: 100,
    })

    if (resp.error) {
      fetchMessage.value = `抓取失败：${resp.error}`
      // 失败提示保留，不自动清空，让用户看清楚
    } else if (resp.saved_count > 0) {
      fetchMessage.value = `成功抓取 ${resp.saved_count} 条新内容（跳过 ${resp.skipped_duplicate} 条重复）`
      // 刷新列表和统计
      // 以后端实际落库的平台为准：粘贴链接时平台由采集器判定，
      // 不一定是下拉里选的那个搜索源
      const landedPlatform = resp.platform || fetchForm.platform
      if (landedPlatform && filters.platform !== landedPlatform) {
        filters.platform = landedPlatform
      } else {
        await loadList()
        loadStats()
      }
      // 成功提示 5 秒后自动清除
      setTimeout(() => { fetchMessage.value = '' }, 5000)
    } else if (resp.fetched_count > 0 && resp.saved_count === 0) {
      fetchMessage.value = `抓取到 ${resp.fetched_count} 条内容，但全部已存在（重复跳过）`
      setTimeout(() => { fetchMessage.value = '' }, 5000)
    } else {
      fetchMessage.value = '未抓取到相关内容，请尝试其他关键词'
      setTimeout(() => { fetchMessage.value = '' }, 5000)
    }
  } catch (e: any) {
    fetchMessage.value = `抓取失败：${e?.response?.data?.detail || e?.message || '未知错误'}`
  } finally {
    fetching.value = false
  }
}

function refresh() {
  loadList()
  loadStats()
}

// ===== 监控抓取（MonitorAgent）=====
async function doMonitorFetch() {
  if (monitorFetching.value) return
  monitorFetching.value = true
  monitorMessage.value = ''
  try {
    const resp = await topicPoolApi.monitorFetch()
    const r = resp.result
    monitorMessage.value = `监控热点完成：新增 ${r.new} 条，更新 ${r.updated} 条，跳过 ${r.skipped} 条，失败 ${r.failed} 条`
    await Promise.all([loadList(), loadStats()])
    setTimeout(() => { monitorMessage.value = '' }, 6000)
  } catch (e: any) {
    monitorMessage.value = `监控热点失败：${e?.response?.data?.detail || e?.message || '未知错误'}`
  } finally {
    monitorFetching.value = false
  }
}

// ===== 热度状态样式类 =====
function heatStatusClass(status: string): string {
  if (status === '活跃') return 'active'
  if (status === '衰退') return 'decay'
  return 'expired'
}

function changePage(p: number) {
  if (p < 1 || p > totalPages.value || p === page.value) return
  page.value = p
}

// ===== 跳转到详情页 =====
function goDetail(id: string) {
  router.push(`/topic-pool/${id}`)
}

// ===== 从选题池发起新工作流 =====
// 把选题条目存到 sessionStorage（避免 URL 参数过长），跳转到工作台
// 工作台 onMounted 时读取并预填搜索框 + 设置 reference
function startWorkflowFromTopic(item: TopicPoolItem) {
  const reference = {
    title: item.title,
    summary: item.summary || '',
    url: item.url || '',
    platform: item.platform,
    author: item.author || '',
    likes: item.likes || 0,
    comments: item.comments || 0,
    collects: item.collects || 0,
    shares: item.shares || 0,
    source_keyword: item.source_keyword || '',
  }
  sessionStorage.setItem('pending_topic_reference', JSON.stringify(reference))
  router.push('/chat')
}

// ===== 筛选联动 =====
// 关键词搜索：防抖 350ms
let keywordTimer: number | undefined
watch(() => filters.keyword, () => {
  if (keywordTimer) window.clearTimeout(keywordTimer)
  keywordTimer = window.setTimeout(() => {
    applyFilterChange()
  }, 350)
})

// 平台 / 收藏 / 来源 / 情绪切换：重置到第一页（合并为一个 watcher，避免清空筛选时连发多次请求）
watch(
  () => [filters.platform, filters.favorited_only, filters.auto_source, filters.emotion],
  () => applyFilterChange(),
)

// 翻页：加载后回到顶部
watch(page, () => {
  loadList()
  scrollToTop()
})

// ===== 数字格式化（万/亿）=====
function formatNum(n: number): string {
  if (n == null || isNaN(n)) return '0'
  if (n >= 100000000) return (n / 100000000).toFixed(1).replace(/\.0$/, '') + '亿'
  if (n >= 10000) return (n / 10000).toFixed(1).replace(/\.0$/, '') + '万'
  return String(n)
}

// ===== 平台标签样式 =====
function platformStyle(platform: string) {
  const p = (platform || '').toLowerCase()
  let color = '#9A9A9A'
  if (p === 'xiaohongshu' || p === 'hb-rednote') color = '#FF2442'
  else if (p === 'zhihu' || p === 'hb-zhihu') color = '#0066FF'
  else if (p === 'weibo' || p === 'hb-weibo') color = '#E6162D'
  else if (p === 'bilibili' || p === 'hb-bilibili') color = '#00A1D6'
  else if (p === 'douyin' || p === 'hb-douyin') color = '#000000'
  else if (p === 'pinterest') color = '#E60023'
  else if (p === 'instagram') color = '#E4405F'
  else if (p === 'hackernews') color = '#FF6600'
  else if (p === 'reddit') color = '#FF4500'
  else if (p === 'tavily') color = '#FF2442'
  else if (p === 'github') color = '#24292E'
  else if (p === 'hb-toutiao') color = '#F85959'
  else if (p === 'hb-baidu') color = '#3388FF'
  else if (p === 'web') color = '#5B6B7C'
  const r = parseInt(color.slice(1, 3), 16)
  const g = parseInt(color.slice(3, 5), 16)
  const b = parseInt(color.slice(5, 7), 16)
  return {
    color,
    backgroundColor: `rgba(${r}, ${g}, ${b}, 0.12)`,
  }
}

// 封面图加载失败记录
const coverImgFailed = ref<Record<string, boolean>>({})

function hasCoverImg(item: TopicPoolItem): boolean {
  return !!(item.cover_img && !coverImgFailed.value[item.id])
}

function safeImageUrl(url: string): string {
  if (!url) return ''
  const u = url.trim()
  if (u.startsWith('data:image')) return u
  if (u.startsWith('/uploads/')) return u
  return u.replace(/^http:/, 'https:')
}

function onCoverImgError(e: Event, item: TopicPoolItem) {
  const img = e.target as HTMLImageElement
  const currentSrc = img.src || ''

  if (!currentSrc.includes('/api/proxy/image')) {
    const originalUrl = item.cover_img || ''
    if (originalUrl && (originalUrl.includes('xhscdn.com') ||
        originalUrl.includes('xiaohongshu.com') ||
        originalUrl.includes('picasso-static') ||
        originalUrl.startsWith('http'))) {
      img.src = '/api/proxy/image?url=' + encodeURIComponent(originalUrl)
      return
    }
  }

  coverImgFailed.value[item.id] = true
}

function platformLabel(platform: string) {
  const p = (platform || '').toLowerCase()
  if (p === 'xiaohongshu') return '小红书'
  if (p === 'zhihu') return '知乎'
  if (p === 'weibo') return '微博'
  if (p === 'bilibili') return 'B站'
  if (p === 'douyin') return '抖音'
  if (p === 'pinterest') return 'Pinterest'
  if (p === 'instagram') return 'Instagram'
  if (p === 'hackernews') return 'HackerNews'
  if (p === 'reddit') return 'Reddit'
  if (p === 'tavily') return 'Tavily'
  if (p === 'github') return 'GitHub'
  if (p === 'hb-weibo') return '微博热搜'
  if (p === 'hb-douyin') return '抖音热搜'
  if (p === 'hb-zhihu') return '知乎热榜'
  if (p === 'hb-toutiao') return '头条热搜'
  if (p === 'hb-baidu') return '百度热搜'
  if (p === 'hb-bilibili') return 'B站热搜'
  if (p === 'hb-rednote') return '小红书热搜'
  if (p === 'web') return '网页'
  const tab = platformTabs.value.find(t => t.value === p)
  if (tab) return tab.label
  return platform || '未知'
}

// ===== 初始化 =====
const contentResizing = ref(false)
function startContentResize(e: MouseEvent) {
  e.preventDefault()
  contentResizing.value = true
  const startX = e.clientX
  const contentEl = document.querySelector('.tp-content-card-wrapper') as HTMLElement | null
  const startW = contentEl ? contentEl.offsetWidth : 600
  document.body.style.cursor = 'col-resize'
  document.body.style.userSelect = 'none'
  document.body.classList.add('is-panel-resizing')
  const onMove = (ev: MouseEvent) => {
    const delta = ev.clientX - startX
    const newW = Math.min(1100, Math.max(280, startW + delta))
    if (contentEl) contentEl.style.width = newW + 'px'
  }
  const onUp = () => {
    contentResizing.value = false
    document.body.style.cursor = ''
    document.body.style.userSelect = ''
    document.body.classList.remove('is-panel-resizing')
    document.removeEventListener('mousemove', onMove)
    document.removeEventListener('mouseup', onUp)
  }
  document.addEventListener('mousemove', onMove)
  document.addEventListener('mouseup', onUp)
}

onMounted(() => {
  loadPlatformTabs()
  loadList()
  loadStats()
})
</script>

<style scoped>
.tp-page {
  --ma-blue-50: #FFF1F3;
  --ma-blue-100: #FFE4E8;
  --ma-blue-200: #FFCDD5;
  --ma-blue-300: #FF9AA7;
  --ma-blue-400: #FF2442;
  --ma-blue-500: #FF2442;
  --ma-blue-600: #FF2442;
  --ma-blue-700: #FF2442;
  --ma-blue-800: #FF2442;
  --ma-blue-900: #FF2442;
  --ma-accent: #FFF1F3;
  --ma-accent-foreground: #FF2442;
  flex: 1;
  min-width: 0;
  height: 100%;
  font-family: var(--ma-font-sans);
  color: var(--ma-text-primary);
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  padding: 28px 20px 16px;
}
/* 选题池内容卡片包装层：覆盖全局 mint-content-card-wrapper 的 row 布局，打通滚动链路 */
.tp-content-card-wrapper {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
  min-width: 0;
  overflow: hidden;
}

.tp-content-card {
  flex: 1;
  min-height: 0;
min-width: 0;
  background: #FFFFFF;
  border-radius:20px;
  border: 1px solid var(--ma-border-default);
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

/* ===== 容器（主滚动区）===== */
.tp-container {
  max-width: none;
  width: 100%;
  margin: 0;
  padding: 20px 24px 16px;
  display: flex;
  flex-direction: column;
  gap: 16px;
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  overflow-x: hidden;
  /* 作为容器，供下方 @container 按实际宽度（侧边栏展开/拖拽分栏）降级筛选栏排版 */
  container-type: inline-size;
  overscroll-behavior: contain;
  -webkit-overflow-scrolling: touch;
  scrollbar-width: auto;
  scrollbar-color: initial;
}
/* WebKit 滚动条样式 */
.tp-container::-webkit-scrollbar {
  width: auto;
}
.tp-container::-webkit-scrollbar-track {
  background: initial;
}
.tp-container::-webkit-scrollbar-thumb {
  background: initial;
  border-radius: initial;
}
.tp-container::-webkit-scrollbar-thumb:hover {
  background: initial;
}

/* ===== 抓取区域 ===== */
.tp-fetch-form {
  display: flex;
  gap: 10px;
  align-items: center;
  flex-wrap: wrap;
}
.tp-fetch-input {
  flex: 1 1 320px;
  min-width: 200px;
  width: auto;
  height: 38px;
  padding: 0 14px;
  border: 1px solid #D4D4D8;
  border-radius:16px;
  font-size: var(--ma-font-sm);
  font-family: inherit;
  color: var(--ma-text-primary);
  background: #F7F8FA !important;
  outline: none;
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
  box-sizing: border-box;
}
.tp-fetch-input:focus {
  border-color: #9A9A9A;
  box-shadow: none;
  outline: none;
}
.tp-fetch-input::placeholder {
  color: #9A9A9A !important;
  opacity: 1 !important;
}
.tp-fetch-input:disabled {
  background: var(--ma-bg-subtle);
  cursor: not-allowed;
}
.tp-fetch-select {
  height: 38px;
  padding: 0 28px 0 12px;
  border: 1px solid var(--ma-border-default);
  border-radius: var(--ma-radius-sm);
  font-size: var(--ma-font-sm);
  font-family: inherit;
  color: var(--ma-text-primary);
  background: var(--ma-bg-base);
  cursor: pointer;
  outline: none;
  flex-shrink: 1;
  min-width: 0;
}
.tp-fetch-select:focus {
  border-color: var(--ma-border-default);
  box-shadow: none;
  outline: none;
}
.tp-fetch-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 38px;
  padding: 0 20px;
  border: 1px solid #9A9A9A;
  background: #9A9A9A;
  color: #fff;
  font-size: var(--ma-font-sm);
  font-weight: 500;
  font-family: inherit;
  border-radius: var(--ma-radius-sm);
  cursor: pointer;
  flex-shrink: 0;
  transition: background-color 0.15s ease, border-color 0.15s ease;
}
.tp-fetch-btn:hover:not(:disabled) {
  background: #4B4B4B;
  border-color: #4B4B4B;
}
.tp-fetch-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.tp-fetch-btn svg {
  width: 16px;
  height: 16px;
}
.tp-fetch-hint {
  margin-top: 10px;
  font-size: var(--ma-font-xs);
  color: var(--ma-text-tertiary);
  line-height: 1.5;
}
.tp-fetch-msg {
  margin-top: 10px;
  padding: 8px 12px;
  font-size: var(--ma-font-sm);
  color: var(--ma-accent-foreground);
  background: var(--ma-accent);
  border-radius: var(--ma-radius-sm);
  line-height: 1.5;
}

/* ===== 平台分类标签栏（Logo 标签样式） ===== */
.tp-platform-tabs {
  display: flex;
  gap: 8px;
  padding: 0;
  background: transparent;
  border: none;
  border-radius: 0;
  box-shadow: none;
  overflow-x: auto;
  flex-shrink: 0;
  flex-wrap: wrap;
  position: relative;
  z-index: 2;
}
.tp-plat-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 34px;
  padding: 0 11px 0 10px;
  border: 1px solid #E8E8EA;
  background: #FFFFFF;
  color: var(--ma-text-secondary);
  font-size: var(--ma-font-sm);
  font-weight: 500;
  font-family: inherit;
  border-radius: 10px;
  cursor: pointer;
  white-space: nowrap;
  transition: border-color 0.18s ease, background 0.18s ease, color 0.18s ease, box-shadow 0.18s ease;
  user-select: none;
}
.tp-plat-chip:hover {
  border-color: #CFCFD3;
  background: #FAFAFB;
  color: var(--ma-text-primary);
}
.tp-plat-chip.is-active {
  border-color: #1A1A1A;
  background: #FFFFFF;
  color: #1A1A1A;
  font-weight: 500;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.06);
}
.tp-plat-logo {
  transition: opacity 0.18s ease, filter 0.18s ease;
}
.tp-plat-logo.is-muted {
  opacity: 0.55;
  filter: grayscale(0.55);
}
.tp-plat-chip:hover .tp-plat-logo.is-muted {
  opacity: 0.85;
  filter: grayscale(0.2);
}
.tp-plat-chip.is-active .tp-plat-logo {
  opacity: 1;
  filter: none;
}
.tp-plat-logo-all {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  color: #8A8A8F;
  flex-shrink: 0;
}
.tp-plat-chip.is-active .tp-plat-logo-all {
  color: #1A1A1A;
}
.tp-plat-label {
  line-height: 1;
}
.tp-plat-count {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 18px;
  height: 17px;
  padding: 0 5px;
  font-size: 11px;
  font-weight: 500;
  border-radius: 9px;
  background: #F2F2F4;
  color: #8A8A8F;
  transition: all 0.18s ease;
}
.tp-plat-chip.is-active .tp-plat-count {
  background: #1A1A1A;
  color: #FFFFFF;
}
.tp-plat-flame {
  color: #E85D3A;
  flex-shrink: 0;
  opacity: 0.85;
}

/* ===== 筛选栏 ===== */
.tp-filters {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 16px 20px;
  background: var(--ma-bg-base);
  border: none;
  border-radius: var(--ma-radius-md);
  box-shadow: none;
  flex-shrink: 0;
  flex-wrap: wrap;
}
.tp-filter-item {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.tp-filter-search {
  flex: 1 1 280px;
  min-width: 180px;
  width: auto;
}
.tp-filter-toggle {
  flex-shrink: 0;
}
.tp-search-wrap {
  position: relative;
  width: 100%;
}
.tp-search-icon {
  position: absolute;
  left: 12px;
  top: 50%;
  transform: translateY(-50%);
  width: 16px;
  height: 16px;
  color: var(--ma-text-tertiary);
  pointer-events: none;
}
.tp-input {
  width: 100%;
  height: 36px;
  padding: 0 18px 0 36px;
  border: 1px solid #D4D4D8;
  border-radius: 9999px;
  background: #F7F8FA !important;
  color: var(--ma-text-primary);
  font-size: var(--ma-font-sm);
  font-family: inherit;
  outline: none;
  box-sizing: border-box;
  transition: background-color 0.2s ease, color 0.2s ease, border-color 0.2s ease, box-shadow 0.2s ease, opacity 0.2s ease, transform 0.2s ease;
}
.tp-input:focus {
  border-color: #9A9A9A;
  background: #F7F8FA !important;
  box-shadow: none;
  outline: none;
}
.tp-input::placeholder {
  color: #9A9A9A !important;
  opacity: 1 !important;
}

/* ===== 开关 ===== */
.tp-switch-label {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: var(--ma-font-sm);
  color: var(--ma-text-secondary);
  cursor: pointer;
  height: 36px;
  user-select: none;
}
.tp-switch {
  position: relative;
  width: 38px;
  height: 22px;
  border: none;
  border-radius: 9999px;
  background: var(--ma-border-strong);
  cursor: pointer;
  transition: background-color 0.2s ease;
  padding: 0;
  flex-shrink: 0;
}
.tp-switch::after {
  content: '';
  position: absolute;
  top: 3px;
  left: 3px;
  width: 16px;
  height: 16px;
  border-radius: 50%;
  background: #fff;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.15);
  transition: transform 0.2s ease;
}
.tp-switch.is-on {
  background: #4B4B4B;
}
.tp-switch.is-on::after {
  transform: translateX(16px);
}

/* ===== 列表区：跟随容器宽度自适应的网格 ===== */
/* 用 grid 而非 column-count：列数由实际容器宽度决定，拖拽分栏时不会压扁卡片 */
.tp-list {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(230px, 1fr));
  gap: 14px;
  align-items: start;
  min-height: 200px;
  transition: opacity 0.15s ease;
}
.tp-list.is-refreshing {
  opacity: 0.55;
}
/* 宽屏时允许更密的排布 */
@media (min-width: 1600px) {
  .tp-list { grid-template-columns: repeat(auto-fill, minmax(250px, 1fr)); }
}
@media (max-width: 640px) {
  .tp-list { grid-template-columns: 1fr; }
}

/* 加载中 */
.tp-spin {
  animation: tp-spin 1s linear infinite;
}
@keyframes tp-spin {
  to { transform: rotate(360deg); }
}

/* 空状态 */
.tp-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 64px 0;
  background: var(--ma-bg-base);
  border: 1px solid var(--ma-border-default);
  border-radius: var(--ma-radius-md);
}
.tp-empty svg {
  width: 40px;
  height: 40px;
  color: var(--ma-text-tertiary);
  margin-bottom: 4px;
}
.tp-empty-title {
  font-size: 14px;
  font-weight: 500;
  color: var(--ma-text-secondary);
}
.tp-empty-desc {
  font-size: var(--ma-font-sm);
  color: var(--ma-text-tertiary);
}

/* ===== 选题卡片 ===== */
@keyframes tp-card-enter {
  from { opacity: 0; }
  to { opacity: 1; }
}
.tp-card {
  display: block;
  position: relative;
  break-inside: avoid;
  page-break-inside: avoid;
  margin-bottom: 0;
  background: var(--ma-bg-base);
  border: 1px solid var(--ma-border-default);
  border-radius:20px;
  overflow: hidden;
  animation: tp-card-enter 0.4s ease both;
  transition: transform 0.25s cubic-bezier(0.16, 1, 0.3, 1),
              box-shadow 0.25s ease,
              border-color 0.25s ease;
}
.tp-card.is-selected {
  border-color: var(--ma-blue-400);
  box-shadow: 0 0 0 2px var(--ma-blue-100);
}
.tp-card:hover {
  transform: translateY(-4px);
  box-shadow: 0 10px 28px rgba(17, 24, 39, 0.12);
  border-color: rgba(59, 108, 246, 0.35);
}

/* 封面区 */
.tp-card-cover {
  position: relative;
  overflow: hidden;
  background: var(--ma-bg-subtle);
}
.tp-card-cover img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
  transition: transform 0.45s cubic-bezier(0.16, 1, 0.3, 1);
}
.tp-card:hover .tp-card-cover img {
  transform: scale(1.06);
}
.tp-card-cover-fallback,
.tp-cover-letter { display: none; }

/* 无封面图卡片：内容区扩展 */
.tp-card:not(:has(.tp-card-cover)) .tp-card-body {
  padding-top: 14px;
}
.tp-no-cover-author {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: var(--ma-text-tertiary);
}
.tp-no-cover-author svg {
  width: 13px;
  height: 13px;
}

/* 底部操作区：发起工作流按钮 + 原文链接 */
.tp-workflow-btn {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  padding: 3px 8px;
  font-size: 11px;
  font-weight: 500;
  color: #fff;
  background: #9A9A9A;
  border: none;
  border-radius:8px;
  cursor: pointer;
  transition: background 0.15s ease;
}
.tp-workflow-btn:hover {
  background: #4B4B4B;
}
.tp-workflow-btn svg {
  width: 12px;
  height: 12px;
}
.tp-orig-link {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  color: var(--ma-text-tertiary);
  border-radius:8px;
  transition: color 0.15s, background 0.15s;
}
.tp-orig-link:hover {
  color: #9A9A9A;
  background: rgba(154, 154, 154, 0.08);
}
.tp-orig-link svg {
  width: 13px;
  height: 13px;
}

/* 封面上的悬浮标签 */
.tp-platform-tag-float {
  position: absolute;
  top: 8px;
  left: 8px;
  backdrop-filter: blur(6px);
  -webkit-backdrop-filter: blur(6px);
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.12);
}
.tp-img-count {
  position: absolute;
  right: 8px;
  bottom: 8px;
  padding: 2px 7px;
  border-radius:8px;
  background: rgba(17, 24, 39, 0.72);
  backdrop-filter: blur(4px);
  -webkit-backdrop-filter: blur(4px);
  color: #fff;
  font-size: 11px;
  line-height: 1.4;
}
.tp-heat-badge {
  position: absolute;
  top: 8px;
  right: 8px;
  display: inline-flex;
  align-items: center;
  gap: 3px;
  padding: 2px 8px;
  border-radius:16px;
  background: rgba(255, 255, 255, 0.92);
  backdrop-filter: blur(6px);
  -webkit-backdrop-filter: blur(6px);
  font-size: 11px;
  font-weight: 500;
  color: #B42318;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
}
.tp-heat-badge svg {
  width: 12px;
  height: 12px;
}

/* hover 时浮现的快捷操作 */
.tp-cover-actions {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  background: rgba(17, 24, 39, 0.42);
  backdrop-filter: blur(2px);
  -webkit-backdrop-filter: blur(2px);
  opacity: 0;
  transition: opacity 0.25s ease;
}
.tp-card:hover .tp-cover-actions {
  opacity: 1;
}
.tp-cover-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 34px;
  padding: 0 16px;
  border: none;
  border-radius:10px;
  background: rgba(255, 255, 255, 0.95);
  color: #1A1A1A;
  font-size: 13px;
  font-weight: 500;
  font-family: inherit;
  cursor: pointer;
  text-decoration: none;
  transition: transform 0.15s ease, background 0.15s ease;
  transform: translateY(8px);
}
.tp-card:hover .tp-cover-btn {
  transform: translateY(0);
}
.tp-cover-btn:hover {
  background: #fff;
  transform: translateY(-1px);
}
.tp-cover-btn-primary {
  background: #9A9A9A;
  color: #fff;
}
.tp-cover-btn-primary:hover {
  background: #4B4B4B;
}
.tp-cover-btn svg {
  width: 14px;
  height: 14px;
}

/* 内容区 */
.tp-card-body {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 12px 14px 10px;
}
.tp-card-meta-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.tp-card-monitor {
  font-size: 11px;
  color: var(--ma-text-tertiary);
  font-variant-numeric: tabular-nums;
}
.tp-card-title {
  font-size: 14px;
  font-weight: 500;
  color: var(--ma-text-primary);
  line-height: 1.45;
  margin: 0;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  transition: color 0.2s ease;
}
.tp-card:hover .tp-card-title {
  color: #4B4B4B;
}
.tp-card-title-clickable {
  cursor: pointer;
  transition: color 0.15s ease;
}
.tp-card-title-clickable:hover {
  color: #4B4B4B;
  text-decoration: underline;
  text-decoration-color: rgba(75, 75, 75, 0.4);
  text-underline-offset: 2px;
}
.tp-card-summary {
  font-size: 13px;
  color: var(--ma-text-secondary);
  line-height: 1.5;
  margin: 0;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.tp-card-dims {
  display: flex;
  gap: 5px;
  flex-wrap: wrap;
  margin-top: 2px;
}
.tp-card-dims span {
  font-size: 11px;
  color: var(--ma-text-tertiary);
  padding: 1px 7px;
  background: var(--ma-bg-subtle);
  border-radius:16px;
}

/* 底部 */
.tp-card-bottom {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 8px 14px;
  border-top: 1px solid var(--ma-border-subtle, #EDEDED);
}
.tp-metrics {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.tp-metric {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  font-size: 12px;
  color: var(--ma-text-tertiary);
  font-variant-numeric: tabular-nums;
}
.tp-metric svg {
  width: 13px;
  height: 13px;
}
.tp-actions {
  display: flex;
  align-items: center;
  gap: 2px;
  opacity: 0.5;
  transition: opacity 0.2s ease;
}
.tp-card:hover .tp-actions {
  opacity: 1;
}
.tp-icon-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: none;
  border-radius:10px;
  background: transparent;
  color: var(--ma-text-tertiary);
  cursor: pointer;
  transition: background 0.15s ease, color 0.15s ease, transform 0.15s ease;
}
.tp-icon-btn:hover {
  background: var(--ma-bg-subtle);
  color: var(--ma-text-secondary);
  transform: scale(1.1);
}
.tp-icon-btn svg {
  width: 15px;
  height: 15px;
}
.tp-icon-btn.is-favorited {
  color: #BA7517;
}
.tp-icon-btn.is-disabled {
  opacity: 0.3;
  cursor: not-allowed;
}
.tp-author {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  font-size: 12px;
  color: var(--ma-text-tertiary);
  max-width: 160px;
}
.tp-author svg {
  width: 12px;
  height: 12px;
  flex-shrink: 0;
}
.tp-author span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.tp-platform-tag {
  display: inline-flex;
  align-items: center;
  padding: 2px 8px;
  border-radius:6px;
  font-size: 11px;
  font-weight: 500;
  line-height: 1.5;
}

/* ===== 分页（tp-page 底部固定栏） ===== */
.tp-pagination {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  flex-wrap: wrap;
  padding: 10px 20px;
  margin: 0 24px;
  background: var(--ma-bg-base, #fff);
  border-top: 1px solid var(--ma-border-subtle, #EDEDED);
  border-radius:16px;
}
.tp-page-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 34px;
  padding: 0 14px;
  border: 1px solid var(--ma-border-default);
  border-radius: var(--ma-radius-sm);
  background: var(--ma-bg-base);
  color: var(--ma-text-secondary);
  font-size: var(--ma-font-sm);
  font-family: inherit;
  cursor: pointer;
  transition: border-color 0.15s ease, color 0.15s ease;
}
.tp-page-btn:hover:not(:disabled) {
  border-color: #9A9A9A;
  color: #9A9A9A;
}
.tp-page-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}
.tp-page-btn svg {
  width: 14px;
  height: 14px;
}
.tp-page-numbers {
  display: flex;
  align-items: center;
  gap: 4px;
}
.tp-page-num {
  min-width: 34px;
  height: 34px;
  padding: 0 8px;
  border: 1px solid #D4D4D8;
  border-radius: var(--ma-radius-sm);
  background: var(--ma-bg-base);
  color: #4B4B4B;
  font-size: var(--ma-font-sm);
  font-family: var(--ma-font-mono);
  font-variant-numeric: tabular-nums;
  cursor: pointer;
  transition: border-color 0.15s ease, background-color 0.15s ease, color 0.15s ease;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
.tp-page-num:hover:not(.is-active) {
  border-color: #9A9A9A;
}
.tp-page-num.is-active {
  background: #9A9A9A;
  border-color: #9A9A9A;
  color: #fff;
  cursor: default;
}
.tp-page-info {
  font-size: var(--ma-font-xs);
  color: var(--ma-text-tertiary);
  margin-left: 12px;
  font-variant-numeric: tabular-nums;
}

/* ===== v6 监控融合样式 ===== */
.tp-monitor-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  margin-left: 12px;
  padding: 5px 12px;
  background: #9A9A9A;
  border: none;
  border-radius:10px;
  color: #FFFFFF;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: background 0.15s;
}
.tp-monitor-btn:hover:not(:disabled) { background: #4B4B4B; }
.tp-monitor-btn:disabled { opacity: 0.6; cursor: not-allowed; }
.tp-monitor-btn svg { width: 14px; height: 14px; }

.tp-fetch-msg-monitor {
  background: rgba(154,154,154,0.08);
  border-color: rgba(154,154,154,0.25);
  color: #4B4B4B;
}

.tp-filter-source, .tp-filter-sort {
  display: flex;
  align-items: center;
  gap: 8px;
}
.tp-source-group { display: flex; gap: 6px; }
.tp-source-chip {
  padding: 4px 10px;
  background: #F5F5F5 !important;
  border: 1px solid var(--ma-border, #EDEDED);
  border-radius:10px;
  font-size: 13px;
  color: var(--ma-text-secondary, var(--rl-text-2));
  cursor: pointer;
  transition: all 0.15s;
  height: 36px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
.tp-source-chip:hover { border-color: #9A9A9A; }
.tp-source-chip.is-active {
  background: #EDEDED;
  border-color: #4B4B4B;
  color: var(--rl-text-2);
}

.tp-heat-status-active { color: #4B4B4B; }
.tp-heat-status-decay { color: #BA7517; }
.tp-heat-status-expired { color: #4B4B4B; }

/* ===== 顶部标题栏 ===== */
.tp-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
  flex-shrink: 0;
}
.tp-header-main {
  display: flex;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;
  min-width: 0;
}
.tp-header-title {
  margin: 0;
  font-size: 18px;
  font-weight: 500;
  letter-spacing: -0.01em;
  color: var(--ma-text-primary);
}
.tp-header-stats {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.tp-header-stat {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 24px;
  padding: 0 9px;
  border-radius: 9999px;
  background: #F7F7F7;
  font-size: 12px;
  font-weight: 500;
  color: var(--ma-text-secondary, #4B4B4B);
  white-space: nowrap;
}
.tp-header-stat svg {
  width: 13px;
  height: 13px;
  color: var(--ma-text-tertiary, #9A9A9A);
}
.tp-header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.tp-ghost-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 34px;
  padding: 0 13px;
  border: 1px solid var(--ma-border-default, #EDEDED);
  border-radius: 10px;
  background: #fff;
  color: var(--ma-text-secondary, #4B4B4B);
  font-size: 13px;
  font-family: inherit;
  cursor: pointer;
  white-space: nowrap;
  transition: border-color 0.15s ease, background-color 0.15s ease, color 0.15s ease;
}
.tp-ghost-btn svg {
  width: 15px;
  height: 15px;
}
.tp-ghost-btn:hover:not(:disabled) {
  border-color: #9A9A9A;
  color: var(--ma-text-primary);
}
.tp-ghost-btn:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}
.tp-ghost-btn.is-active {
  background: #EDEDED;
  border-color: #4B4B4B;
}
.tp-ghost-btn-primary {
  background: #4B4B4B;
  border-color: #4B4B4B;
  color: #fff;
}
.tp-ghost-btn-primary:hover:not(:disabled) {
  background: #2E2E2E;
  border-color: #2E2E2E;
  color: #fff;
}

/* ===== 折叠抓取面板 ===== */
.tp-fetch-panel {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 14px 16px;
  background: #FAFAFA;
  border: 1px solid var(--ma-border-default, #EDEDED);
  border-radius: var(--ma-radius-md);
  flex-shrink: 0;
  animation: tp-panel-in 0.18s ease both;
}
@keyframes tp-panel-in {
  from { opacity: 0; transform: translateY(-4px); }
  to { opacity: 1; transform: translateY(0); }
}
.tp-fetch-close {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 36px;
  border: 1px solid var(--ma-border-default, #EDEDED);
  border-radius: 10px;
  background: #fff;
  color: var(--ma-text-tertiary, #9A9A9A);
  cursor: pointer;
  flex-shrink: 0;
  transition: color 0.15s ease, border-color 0.15s ease;
}
.tp-fetch-close:hover {
  color: var(--ma-text-primary);
  border-color: #9A9A9A;
}
.tp-fetch-close svg {
  width: 15px;
  height: 15px;
}

/* ===== 搜索清空按钮 ===== */
.tp-search-clear {
  position: absolute;
  top: 50%;
  right: 10px;
  transform: translateY(-50%);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  border: none;
  border-radius: 50%;
  background: #EDEDED;
  color: #6B6B6B;
  cursor: pointer;
  padding: 0;
  transition: background-color 0.15s ease, color 0.15s ease;
}
.tp-search-clear:hover {
  background: #DDDDDD;
  color: #2E2E2E;
}
.tp-search-clear svg {
  width: 12px;
  height: 12px;
}
.tp-search-wrap:has(.tp-search-clear) .tp-input {
  padding-right: 38px;
}

/* ===== 排序分段控件 ===== */
.tp-sort-group {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  padding: 3px;
  height: 36px;
  border-radius: 10px;
  background: #F5F5F5;
  border: 1px solid var(--ma-border, #EDEDED);
  flex-shrink: 0;
  margin-left: auto;
}
.tp-sort-seg {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 28px;
  padding: 0 11px;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: var(--ma-text-secondary, #4B4B4B);
  font-size: 13px;
  font-family: inherit;
  cursor: pointer;
  white-space: nowrap;
  transition: background-color 0.15s ease, color 0.15s ease, box-shadow 0.15s ease;
}
.tp-sort-seg svg {
  width: 13px;
  height: 13px;
}
.tp-sort-seg:hover:not(.is-active) {
  color: var(--ma-text-primary);
}
.tp-sort-seg.is-active {
  background: #fff;
  color: var(--ma-text-primary);
  font-weight: 500;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.08);
}

/* ===== 筛选栏窄宽度降级：压缩搜索框，优先保证排序按钮留在同一行 ===== */
@container (max-width: 780px) {
  .tp-filters {
    gap: 12px;
  }
  .tp-filter-search {
    flex: 1 1 160px;
    min-width: 120px;
  }
  .tp-sort-seg {
    padding: 0 8px;
    gap: 4px;
  }
}
@container (max-width: 640px) {
  .tp-filter-search {
    flex-basis: 120px;
    min-width: 96px;
  }
  .tp-source-chip {
    padding: 4px 8px;
  }
}

/* ===== 情绪维度筛选 ===== */
.tp-dims {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  padding: 0 20px;
  flex-shrink: 0;
}
.tp-dims-label {
  font-size: 12px;
  font-weight: 500;
  color: var(--ma-text-tertiary, #9A9A9A);
  letter-spacing: 0.02em;
  flex-shrink: 0;
}
.tp-dim-chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 26px;
  padding: 0 10px;
  border: 1px solid var(--ma-border-default, #EDEDED);
  border-radius: 9999px;
  background: #fff;
  color: var(--ma-text-secondary, #4B4B4B);
  font-size: 12px;
  font-family: inherit;
  cursor: pointer;
  transition: border-color 0.15s ease, background-color 0.15s ease, color 0.15s ease;
}
.tp-dim-chip:hover {
  border-color: #9A9A9A;
}
.tp-dim-chip.is-active {
  background: #EDEDED;
  border-color: #4B4B4B;
  color: var(--ma-text-primary);
  font-weight: 500;
}
.tp-dim-count {
  font-size: 11px;
  color: var(--ma-text-tertiary, #9A9A9A);
}
.tp-dim-chip.is-active .tp-dim-count {
  color: var(--ma-text-secondary, #4B4B4B);
}

/* ===== 骨架屏 ===== */
.tp-skeleton-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(230px, 1fr));
  gap: 14px;
  min-height: 200px;
}
.tp-skeleton-card {
  padding: 0 0 14px;
  border-radius: 20px;
  border: 1px solid var(--ma-border-default, #EDEDED);
  background: #fff;
  overflow: hidden;
}
.tp-skeleton-cover {
  height: 150px;
  background: #F2F2F2;
}
.tp-skeleton-line {
  height: 11px;
  margin: 12px 14px 0;
  border-radius: 6px;
  background: #F2F2F2;
}
.tp-skeleton-line-lg {
  height: 14px;
  width: 72%;
  margin-top: 14px;
}
.tp-skeleton-line-sm {
  width: 42%;
}
.tp-skeleton-cover,
.tp-skeleton-line {
  animation: tp-skeleton-pulse 1.4s ease-in-out infinite;
}
@keyframes tp-skeleton-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.55; }
}

/* ===== 列表工具条 ===== */
.tp-list-wrap {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.tp-list-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 0 2px;
}
.tp-select-all {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  height: 26px;
  padding: 0 8px 0 2px;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: var(--ma-text-secondary, #4B4B4B);
  font-size: 13px;
  font-family: inherit;
  cursor: pointer;
  transition: color 0.15s ease;
}
.tp-select-all:hover {
  color: var(--ma-text-primary);
}
.tp-list-bar-info {
  font-size: 12px;
  color: var(--ma-text-tertiary, #9A9A9A);
}

/* ===== 卡片选择框 ===== */
.tp-card-meta-right {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  margin-left: auto;
}
.tp-select-box {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  padding: 0;
  border: 1.5px solid #C9C9C9;
  border-radius: 4px;
  background: #fff;
  color: #fff;
  flex-shrink: 0;
  cursor: pointer;
  transition: background-color 0.15s ease, border-color 0.15s ease;
}
.tp-select-box.is-checked {
  background: #4B4B4B;
  border-color: #4B4B4B;
}

/* ===== 批量操作条 ===== */
.tp-batch-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
  margin: 0 24px 10px;
  padding: 9px 14px;
  border: 1px solid #4B4B4B;
  border-radius: 12px;
  background: #2E2E2E;
  flex-shrink: 0;
  animation: tp-panel-in 0.18s ease both;
}
.tp-batch-count {
  font-size: 13px;
  font-weight: 500;
  color: #fff;
  white-space: nowrap;
}
.tp-batch-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.tp-batch-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 28px;
  padding: 0 11px;
  border: 1px solid rgba(255, 255, 255, 0.28);
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.12);
  color: #fff;
  font-size: 12px;
  font-family: inherit;
  cursor: pointer;
  transition: background-color 0.15s ease, border-color 0.15s ease;
}
.tp-batch-btn svg {
  width: 13px;
  height: 13px;
}
.tp-batch-btn:hover {
  background: rgba(255, 255, 255, 0.22);
  border-color: rgba(255, 255, 255, 0.45);
}
.tp-batch-btn-danger {
  border-color: rgba(255, 138, 150, 0.5);
  color: #FFB3BC;
}
.tp-batch-btn-danger:hover {
  background: rgba(255, 36, 66, 0.22);
  border-color: #FF8A96;
}
.tp-batch-btn-ghost {
  border-color: transparent;
  background: transparent;
  color: rgba(255, 255, 255, 0.65);
}
.tp-batch-btn-ghost:hover {
  background: rgba(255, 255, 255, 0.1);
  border-color: transparent;
  color: #fff;
}

/* ===== 空状态操作 ===== */
.tp-empty-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 6px;
  flex-wrap: wrap;
  justify-content: center;
}
.tp-empty-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 34px;
  padding: 0 14px;
  border: 1px solid var(--ma-border-default, #EDEDED);
  border-radius: 10px;
  background: #fff;
  color: var(--ma-text-secondary, #4B4B4B);
  font-size: 13px;
  font-family: inherit;
  cursor: pointer;
  transition: border-color 0.15s ease, color 0.15s ease, background-color 0.15s ease;
}
.tp-empty-btn svg {
  width: 15px;
  height: 15px;
}
.tp-empty-btn:hover {
  border-color: #9A9A9A;
  color: var(--ma-text-primary);
}
.tp-empty-btn-primary {
  background: #4B4B4B;
  border-color: #4B4B4B;
  color: #fff;
}
.tp-empty-btn-primary:hover {
  background: #2E2E2E;
  border-color: #2E2E2E;
  color: #fff;
}

.tp-content-resize-handle {
  position: relative;
  width: 0;
  cursor: col-resize;
  flex-shrink: 0;
  align-self: stretch;
  z-index: 5;
  overflow: visible;
}
.tp-content-resize-handle::before {
  content: '';
  position: absolute;
  top: 0;
  bottom: 0;
  left: -4px;
  right: -4px;
  z-index: 1;
}
.tp-content-resize-line {
  display: none !important;
}
.tp-content-resize-handle:hover { background: rgba(0, 0, 0, 0.04); }
</style>