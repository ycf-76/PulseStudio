import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { authFetch } from '@/api/client'
import type { CardPage } from '@/card-editor/templates'

export interface WorkItem {
  id: string
  platform: string
  contentType: string
  title: string
  description?: string
  contentText?: string
  scriptText?: string
  coverUrl?: string
  images?: string[]
  videoUrl?: string
  tags?: string[]
  url: string
  likes: number
  comments: number
  shares: number
  collects: number
  views: number
  playCount?: number
  completionRate?: number
  interactionRate: number
  viralScore: number
  performanceTier: string
  viralType?: string
  aiDiagnosis?: {
    performanceReason: string
    boostFactors: Array<{ factor: string; evidence: string; dimension: string }>
    dragFactors: Array<{ factor: string; evidence: string; dimension: string }>
    comparisonInsight: string
    nextAction: string
  }
  publishedAt?: string
  dataCollectedAt: string
  isDraft?: boolean
  publishedPlatforms?: string[]
  contentStatus?: 'draft' | 'published' | 'collected'
  titlePattern?: string
  emotionTrigger?: string
  contentStructure?: string
  firstPageHtml?: string
  allPageHtmls?: string[]
  /** 后端缓存的本地视频（/outputs/...），优先于远程 CDN 用于缩略/播放 */
  localVideoUrl?: string
  svgUrl?: string
  /** 产物归属的会话 id：草稿只在它自己的会话里可见/可更新 */
  sessionId?: string
  /** 产物来源作品 id（CreativeArtifact.source.work_id） */
  sourceWorkId?: string
  cardDraft?: {
    title?: string
    template?: string
    pages: Array<Partial<CardPage> & { type: string }>
    htmlUrls?: string[]
    pngUrls?: string[]
  }
}

export interface WorkContext {
  workId: string
  title: string
  platform: string
  contentType: string
  performanceTier: string
  /** 作品状态：draft=可原地修改 / published|collected=只读分析 */
  contentStatus?: string
  contentText?: string
  scriptText?: string
  tags?: string[]
  /** 作品配图引用（URL / 本地路径）。后端据此提示 Agent 调 vl_analyze 看图。 */
  imageUrls?: string[]
  videoUrl?: string
  localVideoUrl?: string
  cardDraft?: WorkItem['cardDraft']
  metrics: {
    likes: number
    views: number
    interactionRate: number
    completionRate?: number
  }
  diagnosis?: WorkItem['aiDiagnosis']
}

const WORK_CONTEXT_IMAGE_LIMIT = 8

/**
 * 收集作品配图引用，供后端提示 Agent 调 vl_analyze 看图。
 *
 * 只收「可复用的引用」（http(s) URL / 后端 /outputs 路径）：data URI 是
 * 整段 base64，塞进 work_context 会把 prompt 撑爆，这类图必须走聊天上传
 * 的 asset_id 通道。上限与后端 IMAGE_REF_LIMIT 对齐。
 */
function collectWorkImageUrls(work: WorkItem): string[] {
  const refs: string[] = []
  const push = (raw?: string | null) => {
    const text = (raw || '').trim()
    if (!text || text.startsWith('data:')) return
    if (!(text.startsWith('http://') || text.startsWith('https://') || text.startsWith('/'))) return
    if (!refs.includes(text)) refs.push(text)
  }

  for (const img of work.images || []) push(img)
  push(work.coverUrl)

  const pages = work.cardDraft?.pages
  if (Array.isArray(pages)) {
    for (const page of pages) {
      const record = page as Record<string, unknown>
      push(record.imageUrl as string | undefined)
      const pngUrls = record.pngUrls
      if (Array.isArray(pngUrls)) {
        for (const url of pngUrls) push(url as string)
      }
    }
  }

  return refs.slice(0, WORK_CONTEXT_IMAGE_LIMIT)
}

const PLATFORM_LABELS: Record<string, string> = {
  xiaohongshu: '小红书',
  douyin: '抖音',
  kuaishou: '快手',
  bilibili: 'B站',
  wechat_video: '视频号',
}

const CONTENT_TYPE_LABELS: Record<string, string> = {
  image_text: '图文',
  short_video: '短视频',
  long_video: '长视频',
  image_gallery: '图集',
  live_clip: '直播切片',
  ai_edit: 'AI剪辑',
  voiceover: '口播',
  long_article: '长文',
  // 产物/采集可能的形态值（媒体形态判定走字段探测，这里兜住标签显示）
  text: '文本',
  html_page: '网页',
  html: '网页',
  svg: '矢量图',
}

const TIER_COLORS: Record<string, string> = {
  S: '#ef4444',
  A: '#f97316',
  B: '#eab308',
  C: '#6b7280',
}

export const useWorkStore = defineStore('work', () => {
  const works = ref<WorkItem[]>([])
  const activeWorkId = ref<string | null>(null)
  const isLoading = ref(false)
  const creationNavExpanded = ref(false)
  const workSearchQuery = ref('')
  const sidebarTab = ref<'chat' | 'content'>('chat')
  const activeCreationType = ref<'image_text' | 'voiceover' | 'short_video' | 'ai_edit' | 'long_article' | 'live_clip'>('image_text')
  const showWorkDetail = ref(false)

  const activeWork = computed(() =>
    works.value.find(w => w.id === activeWorkId.value) ||
    outputWorks.value.find(w => w.id === activeWorkId.value) ||
    null
  )

  const filteredWorks = computed(() => {
    if (!workSearchQuery.value) return works.value
    const q = workSearchQuery.value.toLowerCase()
    return works.value.filter(w =>
      w.title.toLowerCase().includes(q) ||
      w.tags?.some(t => t.toLowerCase().includes(q))
    )
  })

  const worksByType = computed(() => {
    const groups: Record<string, WorkItem[]> = {}
    for (const w of works.value) {
      const key = w.contentType || 'image_text'
      if (!groups[key]) groups[key] = []
      groups[key].push(w)
    }
    for (const key of Object.keys(groups)) {
      groups[key].sort((a, b) => {
        const tierOrder: Record<string, number> = { S: 0, A: 1, B: 2, C: 3 }
        return (tierOrder[a.performanceTier] ?? 3) - (tierOrder[b.performanceTier] ?? 3)
      })
    }
    return groups
  })

  function setActiveWork(workId: string | null) {
    activeWorkId.value = workId
  }

  /* 侧栏 Tab 切换的唯一入口。
     离开「内容」时顺手解绑作品：9 个功能页共用同一个 SidebarNav，但只有 chat 页
     监听了 exit-creation，其它页切回「会话」时没人清 activeWorkId，
     残留的作品会让下次进 chat 的问候语位置继续显示作品标题。
     返回 true 表示这次切换确实解绑了作品，chat 页据此决定是否重置对话框。 */
  function switchSidebarTab(tab: 'chat' | 'content') {
    const leavingContent = sidebarTab.value === 'content' && tab === 'chat'
    sidebarTab.value = tab
    if (leavingContent && activeWorkId.value) {
      activeWorkId.value = null
      return true
    }
    return false
  }

  function createDraft(contentType: string = 'image_text'): WorkItem {
    const draft: WorkItem = {
      id: 'draft-' + Date.now(),
      platform: 'xiaohongshu',
      contentType,
      title: '未命名创作',
      description: '',
      contentText: '',
      coverUrl: '',
      tags: [],
      url: '',
      likes: 0,
      comments: 0,
      shares: 0,
      collects: 0,
      views: 0,
      interactionRate: 0,
      viralScore: 0,
      performanceTier: '-',
      dataCollectedAt: new Date().toISOString(),
      isDraft: true,
    }
    works.value.unshift(draft)
    activeWorkId.value = draft.id
    return draft
  }

  /**
   * 删除一条作品：先落库再清内存，否则刷新后后端记录会重新拉回来。
   *
   * id 分发：
   * - `draft-` 前缀 = 尚未落库的本地草稿，只清内存
   * - outputWorks 里的条目 = 创作产物，走创作产物接口
   * - 其余先按「我的作品」记录删；404 说明不是 PCP 记录（如已落库草稿用的是
   *   裸 artifact id），回退到创作产物接口
   *
   * 返回 false 表示后端删除失败，调用方应保留条目并提示，不要做假删除。
   */
  async function deleteWork(workId: string): Promise<boolean> {
    const inWorks = works.value.some(w => w.id === workId)
    const inOutput = outputWorks.value.some(w => w.id === workId)
    // 条目已不在列表里（如连点两次）视为已删除，避免第二次调用误报失败
    if (!inWorks && !inOutput) return true

    if (!workId.startsWith('draft-')) {
      const deleted = await deleteRemoteWork(workId, inOutput)
      if (!deleted) return false
    }

    works.value = works.value.filter(w => w.id !== workId)
    outputWorks.value = outputWorks.value.filter(w => w.id !== workId)
    if (activeWorkId.value === workId) {
      activeWorkId.value = null
    }
    window.dispatchEvent(new CustomEvent('works:updated'))
    return true
  }

  /** 删除远端记录：产物走创作产物接口，其余走「我的作品」接口。 */
  async function deleteRemoteWork(workId: string, inOutput: boolean): Promise<boolean> {
    if (inOutput) {
      const { deleteCreativeArtifact } = await import('@/api/creativeArtifact')
      return deleteCreativeArtifact(workId)
    }
    const { deleteMyWork } = await import('@/api/my_works')
    try {
      await deleteMyWork(workId)
      return true
    } catch (error) {
      const status = (error as { response?: { status?: number } })?.response?.status
      if (status !== 404) return false
      const { deleteCreativeArtifact } = await import('@/api/creativeArtifact')
      return deleteCreativeArtifact(workId)
    }
  }

  function updateDraft(workId: string, updates: Partial<WorkItem>) {
    const idx = works.value.findIndex(w => w.id === workId)
    if (idx !== -1) {
      works.value[idx] = { ...works.value[idx], ...updates }
      return
    }
    const oIdx = outputWorks.value.findIndex(w => w.id === workId)
    if (oIdx !== -1) {
      outputWorks.value[oIdx] = { ...outputWorks.value[oIdx], ...updates }
    }
  }

  function extractFirstPageHtml(cardDraft: WorkItem['cardDraft']): string {
    if (!cardDraft?.pages?.length) return ''
    const first = cardDraft.pages[0] as any
    return first?.content?.htmlContent || first?.htmlContent || ''
  }

  /** 本地草稿 → 后端创作产物：把 id 换成真实 artifact id，
   *  同步迁移 activeWorkId，并清掉可能已存在的同 id 条目，避免侧栏出现两条同名作品。 */
  function renameWorkId(oldId: string, artifactId: string): string {
    if (!artifactId || oldId === artifactId) return oldId
    const idx = works.value.findIndex(w => w.id === oldId)
    if (idx === -1) return oldId
    const dupIdx = works.value.findIndex((w, i) => i !== idx && w.id === artifactId)
    if (dupIdx !== -1) works.value.splice(dupIdx, 1)
    works.value[idx].id = artifactId
    if (activeWorkId.value === oldId) activeWorkId.value = artifactId
    return artifactId
  }

  function saveCardAsDraft(
    cardDraft: WorkItem['cardDraft'],
    title?: string,
    coverUrl?: string,
    images?: string[],
    binding: { sessionId?: string; sourceWorkId?: string; artifactId?: string } = {},
  ): WorkItem {
    const firstPageHtml = extractFirstPageHtml(cardDraft)
    // 同一会话重复产出草稿时复用同一条，避免一次创作堆出一串互相覆盖的草稿
    const existing = binding.sessionId
      ? works.value.find(w => w.isDraft && w.sessionId === binding.sessionId)
      : undefined
    if (existing) {
      existing.title = title || cardDraft?.title || existing.title
      existing.cardDraft = cardDraft
      existing.firstPageHtml = firstPageHtml
      if (coverUrl) existing.coverUrl = coverUrl
      if (images?.length) existing.images = images
      if (binding.sourceWorkId) existing.sourceWorkId = binding.sourceWorkId
      if (binding.artifactId) {
        const id = renameWorkId(existing.id, binding.artifactId)
        return works.value.find(w => w.id === id) || existing
      }
      return existing
    }
    const draft: WorkItem = {
      id: binding.artifactId || 'draft-' + Date.now() + '-' + Math.random().toString(36).slice(2, 6),
      platform: 'xiaohongshu',
      contentType: 'image_text',
      title: title || cardDraft?.title || '视觉组图草稿',
      description: '',
      contentText: '',
      coverUrl: coverUrl || '',
      images: images || [],
      tags: [],
      url: '',
      likes: 0,
      comments: 0,
      shares: 0,
      collects: 0,
      views: 0,
      interactionRate: 0,
      viralScore: 0,
      performanceTier: '-',
      dataCollectedAt: new Date().toISOString(),
      isDraft: true,
      contentStatus: 'draft',
      cardDraft,
      firstPageHtml,
      sessionId: binding.sessionId,
      sourceWorkId: binding.sourceWorkId,
    }
    works.value.unshift(draft)
    return draft
  }

  function toggleCreationNav() {
    creationNavExpanded.value = !creationNavExpanded.value
  }

  function getPlatformLabel(platform: string): string {
    return PLATFORM_LABELS[platform] || platform
  }

  function getContentTypeLabel(contentType: string): string {
    return CONTENT_TYPE_LABELS[contentType] || contentType
  }

  function getTierColor(tier: string): string {
    return TIER_COLORS[tier] || '#6b7280'
  }

  function formatNumber(n: number): string {
    if (n >= 10000) return (n / 10000).toFixed(1) + '万'
    if (n >= 1000) return (n / 1000).toFixed(1) + 'k'
    return String(n)
  }

  async function fetchWorks() {
    isLoading.value = true
    try {
      const { listMyWorks } = await import('@/api/my_works')
      const result = await listMyWorks({ page_size: 200 })
      applyRemoteWorks(result.items)
    } catch {
      try {
        const { listMyWorks } = await import('@/api/my_works')
        const result = await listMyWorks({ page_size: 200 })
        applyRemoteWorks(result.items)
      } catch {
        // 同步失败时保持空列表：不展示任何模拟数据，没有数据就是 0 条
        if (works.value.length === 0) works.value = []
      }
    } finally {
      isLoading.value = false
    }
  }

  /** 采集时间戳：优先 collected_at，缺失时回退发布时间，均无则视为最旧（0） */
  function workRecency(item: any): number {
    const raw = item?.collected_at || item?.published_at || ''
    const t = Date.parse(raw)
    return Number.isNaN(t) ? 0 : t
  }

  /** 合并「我的空间」作品：保留本次会话内尚未落库的草稿（id 以 draft- 开头） */
  function applyRemoteWorks(items: any[]) {
    // 后采集的排前面：前端再排一次，不依赖接口是否已按 collected_at 排序
    const remote = [...items]
      .sort((a, b) => workRecency(b) - workRecency(a))
      .map(mapApiToWorkItem)
    const remoteIds = new Set(remote.map(w => w.id))
    const localDrafts = works.value.filter(
      w => w.isDraft && String(w.id).startsWith('draft-') && !remoteIds.has(w.id),
    )
    works.value = [...localDrafts, ...remote]
    // 账号同步完成后「我的空间」用自己的分页列表，需要通知它重新拉取
    window.dispatchEvent(new CustomEvent('works:updated'))
  }

  function mapApiToWorkItem(item: any): WorkItem {
    const tier = item.is_replicated ? 'S' :
      item.performance_score != null ? (
        item.performance_score >= 0.7 ? 'A' :
        item.performance_score >= 0.4 ? 'B' : 'C'
      ) : '-'
    const noteType = item.note_type || item.content_type || 'image_text'
    const contentType = noteType === 'video' ? 'short_video' :
      noteType === 'short_video' ? 'short_video' :
      noteType === 'long_video' ? 'long_video' : 'image_text'
    const platform = item.platform || 'xiaohongshu'
    let url = ''
    if (item.note_url) url = item.note_url
    else if (platform === 'douyin' && item.note_id) url = `https://www.douyin.com/video/${item.note_id}`
    else if (platform === 'bilibili' && item.note_id) url = `https://www.bilibili.com/video/${item.note_id}`
    else if (item.published_note_id) url = `https://www.xiaohongshu.com/explore/${item.published_note_id}`
    return {
      id: item.id,
      platform,
      contentType,
      title: item.title || item.topic || '未命名',
      description: '',
      contentText: item.content_text || '',
      coverUrl: item.cover_img_url || '',
      images: Array.isArray(item.images) ? item.images : [],
      videoUrl: item.video_url || '',
      tags: Array.isArray(item.tags) ? item.tags : [],
      url,
      likes: item.collected_likes || item.likes || 0,
      comments: item.collected_comments || item.comments || 0,
      shares: item.collected_shares || item.shares || 0,
      collects: item.collected_collects || item.collects || 0,
      views: item.collected_views || item.views || 0,
      interactionRate: (item.collected_likes || item.likes) && (item.collected_collects || item.collects)
        ? (((item.collected_likes || item.likes) + (item.collected_collects || item.collects) + (item.collected_comments || item.comments || 0)) / Math.max(1, item.collected_likes || item.likes)) * 100 / 10
        : 0,
      viralScore: item.performance_score != null ? Math.round(item.performance_score * 100) : 0,
      performanceTier: tier,
      dataCollectedAt: item.collected_at || item.published_at || new Date().toISOString(),
      publishedAt: item.published_at || undefined,
      isDraft: item.content_status === 'draft',
      contentStatus: item.content_status || undefined,
      titlePattern: item.title_pattern || undefined,
      emotionTrigger: item.emotion_trigger || undefined,
      contentStructure: item.content_structure || undefined,
      cardDraft: item.card_draft || undefined,
      firstPageHtml: item.first_page_html || undefined,
      localVideoUrl: item.local_video_url || undefined,
      svgUrl: item.svg_url || undefined,
    }
  }

  const worksByStatus = computed(() => {
    const draft: WorkItem[] = []
    const published: WorkItem[] = []
    const collected: WorkItem[] = []
    for (const w of works.value) {
      const status = (w as any).contentStatus as string | undefined
      if (status === 'draft' || w.isDraft) draft.push(w)
      else if (status === 'collected' || (!status && w.dataCollectedAt && !w.isDraft && !(w as any).publishedAt)) collected.push(w)
      else published.push(w)
    }
    return { draft, published, collected }
  })

  async function addWorkByLink(url: string): Promise<WorkItem> {
    const res = await authFetch('/api/my-works/collect', {
      method: 'POST',
      body: JSON.stringify({ note_url: url, platform: 'auto' }),
    })
    const data = await res.json()
    if (data.success && data.data) {
      const raw = data.data
      const mapped = mapApiToWorkItem({
        ...raw,
        published_note_id: raw.note_id,
        collected_likes: raw.likes,
        collected_collects: raw.collects,
        collected_comments: raw.comments,
        collected_shares: raw.shares,
        collected_at: new Date().toISOString(),
        published_at: new Date().toISOString(),
        content_status: 'collected',
      })
      works.value.unshift(mapped)
      return mapped
    }
    throw new Error(data.message || '采集失败')
  }

  /**
   * 用「我的作品」详情接口的返回体把作品灌进 store，并返回 work id。
   *
   * 详情页（PortfolioDetailView）只看不载入 store，直接 setActiveWork 会
   * 拿不到 activeWork，buildWorkContext() 就返回 null——chat 侧收不到作品
   * 上下文，Agent 只能凭空回答。这里做一次 upsert，把详情页的数据变成
   * 可被 buildWorkContext 消费的 WorkItem（含 contentStatus，决定只读/可改）。
   */
  function seedWorkFromApi(item: any): WorkItem | null {
    if (!item || !item.id) return null
    const mapped = mapApiToWorkItem(item)
    const idx = works.value.findIndex(w => w.id === mapped.id)
    if (idx !== -1) {
      works.value[idx] = { ...works.value[idx], ...mapped }
      return works.value[idx]
    }
    works.value.unshift(mapped)
    return mapped
  }

  async function diagnoseWork(workId: string) {
    const res = await authFetch(`/api/my-works/${workId}/diagnose`, {
      method: 'POST',
    })
    if (res.ok) {
      const diagnosis = await res.json()
      const idx = works.value.findIndex(w => w.id === workId)
      if (idx !== -1) {
        works.value[idx].aiDiagnosis = diagnosis
      }
      return diagnosis
    }
    throw new Error('诊断失败')
  }

  function buildWorkContext(): WorkContext | null {
    const work = activeWork.value
    if (!work) return null
    return {
      workId: work.id,
      title: work.title,
      platform: work.platform,
      contentType: work.contentType,
      performanceTier: work.performanceTier,
      contentStatus: work.contentStatus || (work.isDraft ? 'draft' : ''),
      contentText: work.contentText,
      scriptText: work.scriptText,
      tags: work.tags,
      imageUrls: collectWorkImageUrls(work),
      videoUrl: work.videoUrl || undefined,
      localVideoUrl: work.localVideoUrl || undefined,
      cardDraft: work.cardDraft,
      metrics: {
        likes: work.likes,
        views: work.views || work.playCount || 0,
        interactionRate: work.interactionRate,
        completionRate: work.completionRate,
      },
      diagnosis: work.aiDiagnosis,
    }
  }

  const outputWorks = ref<WorkItem[]>([])
  const outputLoading = ref(false)

  async function fetchOutputWorks() {
    outputLoading.value = true
    try {
      const { listCreativeArtifacts } = await import('@/api/creativeArtifact')
      const artifacts = await listCreativeArtifacts(100)
      outputWorks.value = artifacts.map((a: any) => {
        const cardDraft = a.card_draft || undefined
        const allPageHtmls: string[] = a.all_page_htmls || []
        const firstPageHtml = a.first_page_html || (allPageHtmls.length > 0 ? allPageHtmls[0] : '')
        const images: string[] = a.page_images?.filter(Boolean) || []
        if (cardDraft?.pages?.length) {
          for (const p of cardDraft.pages) {
            const page = p as any
            if (page?.imageUrl && !images.includes(page.imageUrl)) images.push(page.imageUrl)
            if (Array.isArray(page?.pngUrls)) {
              for (const u of page.pngUrls) if (u && !images.includes(u)) images.push(u)
            }
          }
        }
        return {
          id: a.artifact_id,
          platform: 'xiaohongshu',
          contentType: 'image_text',
          title: a.title || '未命名作品',
          description: a.topic || '',
          contentText: '',
          coverUrl: a.cover_url || (images.length > 0 ? images[0] : ''),
          images,
          tags: a.tags || [],
          url: '',
          likes: 0,
          comments: 0,
          shares: 0,
          collects: 0,
          views: 0,
          interactionRate: 0,
          viralScore: 0,
          performanceTier: '-',
          dataCollectedAt: new Date(a.updated_at || Date.now()).toISOString(),
          isDraft: true,
          contentStatus: 'draft' as const,
          cardDraft,
          firstPageHtml,
          allPageHtmls,
          sessionId: a.source?.session_id || undefined,
          sourceWorkId: a.source?.work_id || undefined,
        }
      })
    } catch {
      outputWorks.value = []
    } finally {
      outputLoading.value = false
    }
  }

  const analysisContext = ref<Record<string, any> | null>(null)

  function setAnalysisContext(data: Record<string, any> | null) {
    analysisContext.value = data
  }

  function buildAnalysisPrompt(): string | null {
    const ctx = analysisContext.value
    if (!ctx) return null
    const lines: string[] = ['[数据分析报告上下文]']
    if (ctx.overview) {
      const o = ctx.overview
      lines.push(`数据概览：共${o.total}篇内容，爆款${o.replicated}篇，高表现${o.high_performers}篇，均分${(o.avg_score * 100).toFixed(0)}分`)
    }
    if (ctx.writing_prescription) {
      const wp = ctx.writing_prescription
      if (wp.best_patterns?.length) {
        lines.push('最佳模式：' + wp.best_patterns.map((p: any) => `${p.name}(均分${(p.avg_score * 100).toFixed(0)})`).join('、'))
      }
      if (wp.avoid_patterns?.length) {
        lines.push('避坑模式：' + wp.avoid_patterns.map((p: any) => `${p.name}(均分${(p.avg_score * 100).toFixed(0)})`).join('、'))
      }
    }
    if (ctx.action_items?.length) {
      lines.push('行动建议：' + ctx.action_items.map((a: any) => a.action).join('；'))
    }
    if (ctx.platform_comparison?.length) {
      lines.push('跨平台：' + ctx.platform_comparison.map((p: any) => `${p.platform}均分${(p.avg_score * 100).toFixed(0)}爆款率${(p.replicated_rate * 100).toFixed(0)}%`).join('，'))
    }
    if (ctx.pattern_ranking?.length) {
      lines.push('标题模式排行：' + ctx.pattern_ranking.slice(0, 3).map((p: any) => `${p.name}(${p.count}篇)`).join(' > '))
    }
    if (ctx.top_performers?.length) {
      lines.push('最佳作品：' + ctx.top_performers.map((t: any) => `"${t.title}"${t.score ? (t.score * 100).toFixed(0) + '分' : ''}`).join('、'))
    }
    return lines.join('\n')
  }

  return {
    works, activeWorkId, activeWork, isLoading,
    creationNavExpanded, worksByType, worksByStatus, workSearchQuery, filteredWorks,
    analysisContext, sidebarTab, activeCreationType, showWorkDetail,
    outputWorks, outputLoading,
    setActiveWork, toggleCreationNav, createDraft, deleteWork, updateDraft, saveCardAsDraft,
    renameWorkId,
    switchSidebarTab,
    seedWorkFromApi,
    extractFirstPageHtml,
    getPlatformLabel, getContentTypeLabel, getTierColor, formatNumber,
    fetchWorks, addWorkByLink, diagnoseWork, buildWorkContext,
    fetchOutputWorks,
    setAnalysisContext, buildAnalysisPrompt,
  }
})