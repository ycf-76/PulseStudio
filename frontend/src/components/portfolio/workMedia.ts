import type { MyWorkItem } from '@/api/my_works'

export type WorkMediaKind = 'image_text' | 'video' | 'audio' | 'svg' | 'html' | 'text' | 'unknown'

export interface WorkMediaInfo {
  kind: WorkMediaKind
  label: string
  coverUrl: string
  videoUrl: string
  audioUrl: string
  svgUrl: string
  previewHtml: string
  snippet: string
  pageCount: number
  hasMetrics: boolean
}

const KIND_LABELS: Record<WorkMediaKind, string> = {
  image_text: '图文',
  video: '视频',
  audio: '音频',
  svg: '矢量图',
  html: '网页',
  text: '文本',
  unknown: '其他',
}

/** 侧边栏 / 卡片分组时的固定展示顺序（与 WORK_KIND_FILTERS 保持同一套类型） */
export const WORK_KIND_ORDER: WorkMediaKind[] = ['image_text', 'video', 'audio', 'svg', 'html', 'text', 'unknown']

export function workKindLabel(kind: WorkMediaKind): string {
  return KIND_LABELS[kind] || KIND_LABELS.unknown
}

export const WORK_KIND_FILTERS: Array<{ key: WorkMediaKind | 'all'; label: string }> = [
  { key: 'all', label: '全部类型' },
  { key: 'image_text', label: '图文' },
  { key: 'svg', label: '矢量图' },
  { key: 'video', label: '视频' },
  { key: 'audio', label: '音频' },
  { key: 'html', label: '网页' },
  { key: 'text', label: '文本' },
]

interface MediaEntry {
  url: string
  type: string
}

function readString(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

function readArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : []
}

function readRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' ? (value as Record<string, unknown>) : null
}

function toEntries(images: unknown): MediaEntry[] {
  const entries: MediaEntry[] = []
  for (const raw of readArray(images)) {
    if (typeof raw === 'string') {
      if (raw) entries.push({ url: raw, type: 'image' })
      continue
    }
    const rec = readRecord(raw)
    if (!rec) continue
    const url = readString(rec.media_url) || readString(rec.url) || readString(rec.image_url)
    if (!url) continue
    entries.push({ url, type: readString(rec.type) || 'image' })
  }
  return entries
}

function pageImages(page: Record<string, unknown>): string[] {
  const urls: string[] = []
  for (const item of readArray(page.pngUrls)) {
    if (typeof item === 'string' && item) urls.push(item)
  }
  const single = readString(page.imageUrl) || readString(page.image_url)
  if (single) urls.push(single)
  return urls
}

function pageHtml(page: Record<string, unknown>): string {
  const direct = readString(page.htmlContent)
  if (direct) return direct
  const nested = readRecord(page.content)
  return nested ? readString(nested.htmlContent) : ''
}

function hasMetrics(work: MyWorkItem): boolean {
  const keys = ['collected_likes', 'collected_collects', 'collected_comments', 'collected_shares']
  return keys.some((key) => Number((work as unknown as Record<string, unknown>)[key] || 0) > 0)
}

export function summarizeWorkMedia(work: MyWorkItem): WorkMediaInfo {
  return summarizeMediaParts({
    coverUrl: work.cover_img_url,
    images: work.images,
    videoUrl: work.video_url,
    localVideoUrl: (work as unknown as Record<string, unknown>).local_video_url,
    svgUrl: (work as unknown as Record<string, unknown>).svg_url,
    firstPageHtml: work.first_page_html,
    contentText: work.content_text,
    cardDraft: work.card_draft,
    hasMetrics: hasMetrics(work),
  })
}

/**
 * 侧边栏用的 WorkItem（camelCase，来自 workStore）媒体识别。
 * 结构与 MyWorkItem 不同，但复用同一套判定规则，保证两处缩略图表现一致。
 */
export interface WorkItemMediaSource {
  coverUrl?: unknown
  images?: unknown
  videoUrl?: unknown
  localVideoUrl?: unknown
  svgUrl?: unknown
  firstPageHtml?: unknown
  contentText?: unknown
  cardDraft?: unknown
  likes?: number
  collects?: number
  comments?: number
  shares?: number
}

export function summarizeWorkItemMedia(work: WorkItemMediaSource): WorkMediaInfo {
  return summarizeMediaParts({
    coverUrl: work.coverUrl,
    images: work.images,
    videoUrl: work.videoUrl,
    localVideoUrl: work.localVideoUrl,
    svgUrl: work.svgUrl,
    firstPageHtml: work.firstPageHtml,
    contentText: work.contentText,
    cardDraft: work.cardDraft,
    hasMetrics: [work.likes, work.collects, work.comments, work.shares]
      .some((value) => Number(value || 0) > 0),
  })
}

export interface WorkMediaParts {
  coverUrl?: unknown
  images?: unknown
  videoUrl?: unknown
  localVideoUrl?: unknown
  svgUrl?: unknown
  firstPageHtml?: unknown
  contentText?: unknown
  cardDraft?: unknown
  hasMetrics?: boolean
}

/**
 * 媒体判定唯一入口：图片 / 封面 / 视频 / 音频 / 矢量图 / 网页 / 文本。
 * 顺序即优先级：视频 > 音频 > 矢量图 > 图文 > 网页 > 文本。
 */
export function summarizeMediaParts(parts: WorkMediaParts): WorkMediaInfo {
  const entries = toEntries(parts.images)
  const pages = readArray((readRecord(parts.cardDraft) || {}).pages)
    .map(readRecord)
    .filter((page): page is Record<string, unknown> => !!page)

  const imageUrls = entries.filter((entry) => entry.type !== 'video' && entry.type !== 'audio').map((entry) => entry.url)
  const videoFromImages = entries.find((entry) => entry.type === 'video')?.url || ''
  const audioFromImages = entries.find((entry) => entry.type === 'audio')?.url || ''

  const videoPage = pages.find((page) => readString(page.videoUrl) || page.type === 'video')
  const audioPage = pages.find((page) => readString(page.audioUrl) || page.type === 'audio')
  const htmlFromPages = pages.map(pageHtml).find(Boolean) || ''

  // 本地缓存优先：远程 CDN 链接常因 referer 限制无法直接播放
  const videoUrl = readString(parts.localVideoUrl) || readString(parts.videoUrl) || videoFromImages || readString(videoPage?.videoUrl)
  const audioUrl = readString(audioPage?.audioUrl) || audioFromImages
  const pageImageUrls = pages.flatMap(pageImages)
  const previewHtml = readString(parts.firstPageHtml) || htmlFromPages
  const svgUrl = readString(parts.svgUrl)
  const contentText = readString(parts.contentText).trim()
  // 只有真实图片才构成「图文」；卡片页可能仅携带 HTML（未截图），不能据此判定图文
  const hasImage = imageUrls.length > 0 || pageImageUrls.length > 0
  const htmlPageCount = pages.filter((page) => !!pageHtml(page)).length
  const pageCount = hasImage
    ? (pages.length || imageUrls.length || pageImageUrls.length)
    : (htmlPageCount || (previewHtml ? 1 : 0))
  const coverUrl = readString(parts.coverUrl) || imageUrls[0] || pageImageUrls[0] || ''

  let kind: WorkMediaKind = 'unknown'
  if (videoUrl) {
    kind = 'video'
  } else if (audioUrl) {
    kind = 'audio'
  } else if (svgUrl) {
    kind = 'svg'
  } else if (hasImage) {
    kind = 'image_text'
  } else if (previewHtml) {
    kind = 'html'
  } else if (contentText) {
    kind = 'text'
  }

  return {
    kind,
    label: KIND_LABELS[kind],
    coverUrl,
    videoUrl,
    audioUrl,
    svgUrl,
    previewHtml,
    snippet: contentText.slice(0, 160),
    pageCount: kind === 'svg' ? 1 : pageCount,
    hasMetrics: !!parts.hasMetrics,
  }
}

export function matchesWorkKind(work: MyWorkItem, kind: WorkMediaKind | 'all'): boolean {
  if (kind === 'all') return true
  return summarizeWorkMedia(work).kind === kind
}

export function collectWorkImageUrls(work: MyWorkItem): string[] {
  const entries = toEntries(work.images)
  const imageUrls = entries
    .filter((entry) => entry.type !== 'video' && entry.type !== 'audio')
    .map((entry) => entry.url)
  const draft = (work.card_draft || null) as { pages?: unknown } | null
  const pageImageUrls = readArray(draft?.pages)
    .map(readRecord)
    .filter((page): page is Record<string, unknown> => !!page)
    .flatMap(pageImages)
  const urls = imageUrls.length > 0 ? imageUrls : pageImageUrls
  if (urls.length === 0 && readString(work.cover_img_url)) {
    return [readString(work.cover_img_url)]
  }
  return urls
}

const PLATFORM_LABELS: Record<string, string> = {
  xiaohongshu: '小红书',
  douyin: '抖音',
  bilibili: 'B站',
  wechat_mp: '微信',
  instagram: 'Instagram',
  threads: 'Threads',
  kuaishou: '快手',
  web: '网页',
  unknown: '未知',
}

export function platformLabelOf(platform: string | null | undefined): string {
  if (!platform) return '未知'
  return PLATFORM_LABELS[platform] || platform
}

const STATUS_LABELS: Record<string, string> = {
  draft: '草稿',
  published: '已发布',
  collected: '已采集',
  reviewing: '审核中',
  rejected: '未通过',
}

export function statusLabelOf(status: string | null | undefined): string {
  if (!status) return '未知'
  return STATUS_LABELS[status] || status
}

export function formatWorkDate(value: string | null | undefined): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

export function formatCount(value: number | null | undefined): string {
  const num = Number(value || 0)
  if (num >= 10000) return `${(num / 10000).toFixed(1)}w`
  if (num >= 1000) return `${(num / 1000).toFixed(1)}k`
  return String(num)
}