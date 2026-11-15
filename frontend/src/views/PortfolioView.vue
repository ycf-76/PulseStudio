<template>
<AppShell current-page="portfolio" settings-section="accounts">
    <main class="mw-page">
      <div class="da-content-card-wrapper">
      <div class="da-content-card">

        <div class="mw-container">

          <!-- ====== 顶部标题 + 搜索 + 视图切换 ====== -->
          <header class="mw-head">
            <div class="mw-head-main">
              <h1 class="mw-title">我的空间</h1>
              <p class="mw-sub">{{ total }} 个作品 · 图文 / 视频 / 音频 / 网页统一收纳</p>
            </div>
            <div class="mw-tools">
              <div class="mw-search">
                <Search :size="16" />
                <input v-model="searchQuery" type="text" placeholder="搜索标题、话题、标签" />
                <button v-if="searchQuery" class="mw-search-clear" title="清空" @click="searchQuery = ''">
                  <X :size="14" />
                </button>
              </div>
              <div class="mw-viewtoggle">
                <button
                  class="mw-vt"
                  :class="{ 'mw-vt-active': viewMode === 'grid' }"
                  title="网格视图"
                  @click="setViewMode('grid')"
                ><LayoutGrid :size="16" /></button>
                <button
                  class="mw-vt"
                  :class="{ 'mw-vt-active': viewMode === 'list' }"
                  title="列表视图"
                  @click="setViewMode('list')"
                ><ListIcon :size="16" /></button>
              </div>
            </div>
          </header>

          <!-- ====== 来源 + 平台 + 产出类型筛选 ====== -->
          <div class="mw-filters">
            <div class="mw-tabs">
              <button
                v-for="tab in ORIGIN_TABS"
                :key="tab.key"
                class="mw-tab"
                :class="{ 'mw-tab-active': activeOrigin === tab.key }"
                @click="switchOrigin(tab.key)"
              >{{ tab.label }}</button>
            </div>
            <div v-if="activeOrigin === 'collected'" class="mw-platforms">
              <button
                class="mw-platform"
                :class="{ 'mw-platform-active': activePlatform === 'all' }"
                @click="switchPlatform('all')"
              >全部平台</button>
              <button
                v-for="p in platformOptions"
                :key="p.platform"
                class="mw-platform"
                :class="{ 'mw-platform-active': activePlatform === p.platform, 'mw-platform-idle': !p.bound && p.count === 0 }"
                :title="p.bound ? `已绑定：${p.nickname || p.label}` : (p.count > 0 ? '该平台作品来自历史同步，绑定账号后可继续同步新作品' : '未绑定账号，可在设置页绑定后同步')"
                @click="switchPlatform(p.platform)"
              >{{ p.label }}<span class="mw-platform-count">{{ p.count }}</span><span v-if="!p.bound && p.count === 0" class="mw-platform-unbound">未绑定</span></button>
            </div>
            <div class="mw-kinds">
              <button
                v-for="kind in WORK_KIND_FILTERS"
                :key="kind.key"
                class="mw-kind"
                :class="{ 'mw-kind-active': activeKind === kind.key }"
                @click="activeKind = kind.key"
              >{{ kind.label }}</button>
            </div>
          </div>

          <!-- ====== 骨架屏 ====== -->
          <div v-if="loading" class="mw-grid" :class="`mw-grid--${viewMode}`">
            <div v-for="n in 8" :key="n" class="mw-skeleton" :class="`mw-skeleton--${viewMode}`">
              <div class="mw-skeleton-media"></div>
              <div class="mw-skeleton-lines"><span></span><span></span></div>
            </div>
          </div>

          <!-- ====== 作品网格 / 列表 ====== -->
          <div v-else-if="filteredWorks.length > 0" class="mw-grid" :class="`mw-grid--${viewMode}`">
            <WorkCard
              v-for="w in filteredWorks"
              :key="w.id"
              :work="w"
              :variant="viewMode"
              :deleting="deletingIds.has(w.id)"
              @open="goToDetail"
              @remove="handleDeleteWork"
            />
          </div>

          <!-- ====== 空状态 ====== -->
          <div v-else class="mw-empty">
            <div class="mw-empty-icon"><Inbox :size="44" /></div>
            <p class="mw-empty-text">{{ emptyText }}</p>
            <button
              v-if="!searchQuery && activeKind === 'all' && activeOrigin !== 'collected'"
              class="mw-btn-primary"
              @click="goToWorkbench"
            >开始创作</button>
            <button
              v-else-if="activeOrigin === 'collected'"
              class="mw-btn-primary"
              @click="openSettings"
            >去绑定账号</button>
          </div>

          <!-- ====== 分页 ====== -->
          <div v-if="total > pageSize" class="mw-pagination">
            <button class="mw-pg-btn" :disabled="page <= 1" @click="page--; loadWorks()">&lt; 上一页</button>
            <span class="mw-pg-info">{{ page }} / {{ Math.ceil(total / pageSize) }}</span>
            <button class="mw-pg-btn" :disabled="page >= Math.ceil(total / pageSize)" @click="page++; loadWorks()">下一页 &gt;</button>
          </div>

        </div>
      </div>
      </div>
    </main>

    <template #overlay>
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
</AppShell>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Inbox, LayoutGrid, List as ListIcon, Search, X } from 'lucide-vue-next'
import AppShell from '@/components/layout/AppShell.vue'
import WorkCard from '@/components/portfolio/WorkCard.vue'
import ConfirmDialog from '@/components/common/ConfirmDialog.vue'
import { WORK_KIND_FILTERS, matchesWorkKind, type WorkMediaKind } from '@/components/portfolio/workMedia'
import { useWorkStore } from '@/stores/work'
import { useUIState } from '@/composables/useUIState'
import { useConfirm } from '@/composables/useConfirm'
import { getWorkFacets, deleteMyWork, listMyWorks, type MyWorkItem, type WorkFacetPlatform } from '@/api/my_works'

const router = useRouter()
const workStore = useWorkStore()
const uiState = useUIState()
const { state: confirmState, confirm, onConfirm, onCancel } = useConfirm()

function openSettings() { uiState.openSettings('accounts') }
function goToWorkbench() { router.push('/chat') }

const ORIGIN_TABS = [
  { key: 'collected', label: '采集' },
  { key: 'generated', label: '平台生成' },
  { key: 'all', label: '全部' },
] as const

type OriginKey = typeof ORIGIN_TABS[number]['key']

const works = ref<MyWorkItem[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const loading = ref(true)
const activeOrigin = ref<OriginKey>('collected')
const activePlatform = ref('all')
const platformOptions = ref<WorkFacetPlatform[]>([])
const activeKind = ref<WorkMediaKind | 'all'>('all')
const searchQuery = ref('')
const viewMode = ref<'grid' | 'list'>('grid')
const deletingIds = ref<Set<string>>(new Set())

/** 采集时间戳：优先 collected_at，缺失时回退发布时间，均无则视为最旧（0） */
function workRecency(w: MyWorkItem): number {
  const raw = w.collected_at || w.published_at || ''
  const t = Date.parse(raw)
  return Number.isNaN(t) ? 0 : t
}

const filteredWorks = computed(() => {
  let list = works.value
  const keyword = searchQuery.value.trim().toLowerCase()
  if (keyword) {
    list = list.filter((w) => {
      const title = (w.title || '').toLowerCase()
      const topic = (w.topic || '').toLowerCase()
      const tags = (w.tags || []).join(' ').toLowerCase()
      return title.includes(keyword) || topic.includes(keyword) || tags.includes(keyword)
    })
  }
  if (activeKind.value !== 'all') {
    list = list.filter((w) => matchesWorkKind(w, activeKind.value))
  }
  // 后采集的排前面：前端兜底排序，保证顺序不依赖接口默认值
  return [...list].sort((a, b) => workRecency(b) - workRecency(a))
})

const emptyText = computed(() => {
  if (searchQuery.value.trim()) return '没有找到匹配的作品'
  if (activeKind.value !== 'all') return `暂无${WORK_KIND_FILTERS.find((k) => k.key === activeKind.value)?.label || ''}作品`
  if (activeOrigin.value === 'generated') return '还没有平台生成的产物'
  if (activeOrigin.value === 'collected' && activePlatform.value !== 'all') {
    const label = platformOptions.value.find((p) => p.platform === activePlatform.value)?.label || ''
    return `「${label}」还没有采集到作品，去设置页绑定账号并同步试试`
  }
  return '还没有作品'
})

function setViewMode(mode: 'grid' | 'list') {
  viewMode.value = mode
  try { localStorage.setItem('portfolio-view-mode', mode) } catch { /* ignore */ }
}

async function switchOrigin(key: OriginKey) {
  activeOrigin.value = key
  activePlatform.value = 'all'
  page.value = 1
  await loadWorks()
}

async function switchPlatform(platform: string) {
  activePlatform.value = platform
  page.value = 1
  await loadWorks()
}

async function loadFacets() {
  try {
    const facets = await getWorkFacets()
    platformOptions.value = facets.platforms || []
  } catch (e) {
    console.error('loadFacets failed:', e)
    platformOptions.value = []
  }
}

/** 账号同步完成后刷新当前页列表，避免停留在本页时看不到新采集/更新的作品 */
function handleWorksUpdated() {
  loadWorks()
}

async function loadWorks() {
  loading.value = true
  try {
    const params: {
      page: number
      page_size: number
      origin: OriginKey
      sort_by: 'collected_at'
      platform?: string
    } = {
      page: page.value,
      page_size: pageSize.value,
      origin: activeOrigin.value,
      sort_by: 'collected_at',
    }
    if (activeOrigin.value !== 'generated' && activePlatform.value !== 'all') {
      params.platform = activePlatform.value
    }
    const res = await listMyWorks(params)
    works.value = res.items
    total.value = res.total
  } catch (e) {
    console.error('loadWorks failed:', e)
  } finally {
    loading.value = false
  }
}

function goToDetail(w: MyWorkItem) {
  if (w.origin_kind === 'artifact') {
    const rawId = String(w.id).replace(/^artifact:/, '')
    router.push(`/portfolio/${rawId}?source=artifact`).catch(() => {})
    return
  }
  if (w.origin_kind === 'output') {
    router.push(`/portfolio/${encodeURIComponent(w.id)}?source=generated`).catch(() => {})
    return
  }
  const source = w.content_status === 'draft' ? 'draft'
    : w.content_status === 'collected' ? 'collected'
    : 'published'
  router.push(`/portfolio/${w.id}?source=${source}`).catch(() => {})
}

async function handleDeleteWork(w: MyWorkItem) {
  if (deletingIds.value.has(w.id)) return
  const label = w.title || w.topic || '无标题'
  const hint = w.origin_kind === 'output'
    ? '会同时删掉 outputs 目录里这条产物的图片/视频文件，不可恢复。'
    : w.origin_kind === 'artifact'
      ? '会删除这条创作产物记录，不可恢复。'
      : '会删除这条作品记录，不可恢复。'
  const ok = await confirm({
    title: '删除作品',
    message: `确定删除「${label}」？\n${hint}`,
    confirmText: '删除',
    danger: true,
  })
  if (!ok) return

  deletingIds.value.add(w.id)
  try {
    await deleteMyWork(w.id)
    works.value = works.value.filter((item) => item.id !== w.id)
    total.value = Math.max(0, total.value - 1)
    try { await workStore.fetchWorks() } catch (e) { console.error(e) }
    if (w.origin_kind === 'artifact') {
      try { await workStore.fetchOutputWorks() } catch (e) { console.error(e) }
    }
    try { await loadFacets() } catch (e) { console.error(e) }
  } catch (e) {
    const err = e as { response?: { data?: { detail?: string } }; message?: string }
    console.error('删除失败:', e)
    alert('删除失败：' + (err.response?.data?.detail || err.message))
  } finally {
    deletingIds.value.delete(w.id)
  }
}

onMounted(() => {
  try {
    const saved = localStorage.getItem('portfolio-view-mode')
    if (saved === 'grid' || saved === 'list') viewMode.value = saved
  } catch { /* ignore */ }
  loadFacets()
  loadWorks()
  window.addEventListener('works:updated', handleWorksUpdated)
})

onBeforeUnmount(() => {
  window.removeEventListener('works:updated', handleWorksUpdated)
})
</script>

<style scoped>
.mw-page { flex: 1; min-width: 0; height: 100%; display: flex; flex-direction: column; overflow: hidden; padding: 28px 20px 16px; }
.da-content-card-wrapper { display: flex; flex-direction: column; flex: 1; min-height: 0; min-width: 0; overflow: hidden; }
.da-content-card { flex: 1; min-height: 0; min-width: 0; background: #FFFFFF; border-radius: 16px; border: 1px solid rgba(0, 0, 0, 0.06); box-shadow: none; display: flex; flex-direction: column; overflow: hidden; }
.mw-container { flex: 1; overflow-y: auto; padding: 8px 24px 24px; }

/* ====== Header ====== */
.mw-head { display: flex; align-items: flex-end; justify-content: space-between; gap: 20px; flex-wrap: wrap; padding: 16px 0 18px; }
.mw-title { margin: 0; font-size: 18px; font-weight: 500; letter-spacing: -0.2px; color: #1a1a1a; }
.mw-sub { margin: 6px 0 0; font-size: 13px; color: #9A9A9A; }
.mw-tools { display: flex; align-items: center; gap: 10px; }

.mw-search { display: flex; align-items: center; gap: 8px; height: 34px; padding: 0 14px; background: #F7F7F8; border: 1px solid transparent; border-radius: 999px; min-width: 260px; transition: background 0.18s ease, border-color 0.18s ease, box-shadow 0.18s ease; }
.mw-search:hover { background: #fff; border-color: #E8E8EA; }
.mw-search:focus-within { background: #fff; border-color: #D4D4D8; box-shadow: 0 0 0 3px rgba(0, 0, 0, 0.04); }
.mw-search > svg { color: #A1A1AA; flex-shrink: 0; transition: color 0.18s ease; }
.mw-search:focus-within > svg { color: #71717A; }
.mw-search input { flex: 1; min-width: 0; border: none !important; outline: none; background: transparent !important; box-shadow: none !important; font-size: 13px; color: #1a1a1a; }
.mw-search input::placeholder { color: #B4B4BB; }
.mw-search-clear { display: flex; align-items: center; justify-content: center; width: 18px; height: 18px; border: none; border-radius: 50%; background: #E4E4E7; color: #71717A; cursor: pointer; padding: 0; transition: background 0.15s ease, color 0.15s ease; }
.mw-search-clear:hover { background: #D4D4D8; color: #3F3F46; }
@media (max-width: 720px) { .mw-search { min-width: 0; flex: 1; } }

.mw-viewtoggle { display: flex; padding: 3px; background: #F5F5F5; border-radius: 10px; }
.mw-vt { width: 30px; height: 28px; border: none; border-radius: 8px; background: transparent; color: #9A9A9A; display: flex; align-items: center; justify-content: center; cursor: pointer; transition: all 0.15s; }
.mw-vt:hover { color: #4B4B4B; }
.mw-vt-active { background: #fff; color: #1a1a1a; box-shadow: 0 1px 3px rgba(0, 0, 0, 0.08); }

/* ====== Filters ====== */
.mw-filters { display: flex; align-items: center; justify-content: space-between; gap: 16px; flex-wrap: wrap; padding-bottom: 18px; border-bottom: 1px solid #F0F0F0; margin-bottom: 20px; }
.mw-tabs { display: flex; gap: 6px; align-items: center; }
.mw-tab { padding: 6px 14px; border-radius: 10px; font-size: 13px; cursor: pointer; background: #fff; border: 1px solid #EDEDED; color: #4B4B4B; transition: all 0.15s; white-space: nowrap; }
.mw-tab:hover { background: #F5F5F5; border-color: #D4D4D8; }
.mw-tab-active { background: #333; color: #fff; border-color: #333; font-weight: 500; }

.mw-platforms { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
.mw-platform { display: inline-flex; align-items: center; gap: 4px; padding: 5px 12px; border-radius: 999px; font-size: 12px; cursor: pointer; background: #fff; border: 1px solid #EDEDED; color: #6B6B6B; transition: all 0.15s; }
.mw-platform:hover { border-color: #D4D4D8; color: #333; }
.mw-platform-active { background: #F4F4F5; border-color: #D4D4D8; color: #333; font-weight: 500; }
.mw-platform-count { font-size: 11px; color: #A1A1AA; }
.mw-platform-active .mw-platform-count { color: #71717A; }
.mw-platform-unbound { font-size: 10px; color: #B4B4BB; border: 1px solid #EDEDED; border-radius: 4px; padding: 0 3px; }
.mw-platform-idle { opacity: 0.62; }
.mw-platform-idle:hover { opacity: 1; }

.mw-kinds { display: flex; gap: 6px; flex-wrap: wrap; }
.mw-kind { padding: 5px 12px; border-radius: 999px; font-size: 12px; cursor: pointer; background: transparent; border: 1px dashed #E4E4E7; color: #9A9A9A; transition: all 0.15s; }
.mw-kind:hover { color: #4B4B4B; border-color: #D4D4D8; }
.mw-kind-active { background: #F4F4F5; border: 1px solid #D4D4D8; color: #333; font-weight: 500; }

/* ====== Grid / List ====== */
.mw-grid { display: grid; gap: 14px; }
.mw-grid--grid { grid-template-columns: repeat(auto-fill, minmax(190px, 1fr)); }
.mw-grid--list { grid-template-columns: 1fr; gap: 10px; }
@media (max-width: 720px) { .mw-grid--grid { grid-template-columns: repeat(auto-fill, minmax(148px, 1fr)); gap: 10px; } }

/* ====== Skeleton ====== */
.mw-skeleton { background: #fff; border: 1px solid #F5F5F5; border-radius: 16px; overflow: hidden; }
.mw-skeleton--grid { display: flex; flex-direction: column; }
.mw-skeleton--list { display: flex; flex-direction: row; }
.mw-skeleton-media { background: #F5F5F5; animation: mw-pulse 1.4s ease-in-out infinite; }
.mw-skeleton--grid .mw-skeleton-media { aspect-ratio: 1 / 1; width: 100%; }
.mw-skeleton--list .mw-skeleton-media { width: 92px; aspect-ratio: 1 / 1; flex-shrink: 0; }
.mw-skeleton-lines { flex: 1; padding: 10px 11px; display: flex; flex-direction: column; gap: 7px; }
.mw-skeleton-lines span { height: 10px; border-radius: 6px; background: #F5F5F5; animation: mw-pulse 1.4s ease-in-out infinite; }
.mw-skeleton-lines span:last-child { width: 55%; }
@keyframes mw-pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.55; } }

/* ====== Empty ====== */
.mw-empty { display: flex; flex-direction: column; align-items: center; justify-content: center; padding: 80px 0; gap: 12px; }
.mw-empty-icon { color: #D4D4D8; }
.mw-empty-text { font-size: 14px; color: #9A9A9A; margin: 0; }
.mw-btn-primary { display: inline-flex; align-items: center; gap: 6px; padding: 8px 20px; border-radius: 12px; cursor: pointer; font-size: 14px; border: none; background: #FF2442; color: #fff; transition: all 0.15s; }
.mw-btn-primary:hover { background: #E0203C; }

/* ====== Pagination ====== */
.mw-pagination { display: flex; align-items: center; justify-content: center; gap: 16px; padding: 24px 0 8px; }
.mw-pg-btn { padding: 6px 14px; border-radius: 10px; font-size: 13px; cursor: pointer; border: 1px solid #EDEDED; background: #fff; color: #4B4B4B; transition: all 0.15s; }
.mw-pg-btn:hover:not(:disabled) { background: #F5F5F5; border-color: #D4D4D8; }
.mw-pg-btn:disabled { opacity: 0.4; cursor: not-allowed; }
.mw-pg-info { font-size: 13px; color: #4B4B4B; }
</style>