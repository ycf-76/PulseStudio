import apiClient from './client'

export interface VideoImageItem {
  type: 'video'
  media_url?: string
  publish_status?: string
  post_id?: string
  message?: string
}

export interface MyWorkCardDraftPage {
  type?: string
  imageUrl?: string
  image_url?: string
  pngUrls?: string[]
  htmlUrl?: string
  htmlContent?: string
  audioUrl?: string
  videoUrl?: string
  content?: { htmlContent?: string } | null
}

export interface MyWorkCardDraft {
  title?: string
  template?: string
  pages?: MyWorkCardDraftPage[]
  htmlUrls?: string[]
  pngUrls?: string[]
  /** 结构化卡片本体（CardDeck dump），有它才能进编辑态 */
  deck?: Record<string, any> | null
}

export interface MyWorkItem {
  id: string
  origin?: 'collected' | 'generated'
  origin_kind?: 'collect' | 'artifact' | 'output' | 'workflow'
  workflow_id: string
  published_note_id: string | null
  topic: string | null
  title: string | null
  cover_img_url: string | null
  images: (string | VideoImageItem)[] | null
  video_url: string
  /** outputs 产物目录中的矢量图（.svg）静态路径，首个为封面，无则为空 */
  svg_url?: string | null
  svg_urls?: string[] | null
  /** yt-dlp 下载的视频本地缓存 Web 路径（/outputs/...），未下载为 null */
  local_video_url?: string | null
  platform: string
  published_at: string | null
  collected_likes: number | null
  collected_collects: number | null
  collected_comments: number | null
  collected_shares: number | null
  performance_score: number | null
  is_replicated: boolean | null
  predicted_viral_score: number | null
  actual_viral_score: number | null
  prediction_error: number | null
  collected_at: string | null
  content_status: 'draft' | 'published' | 'collected'
  title_pattern: string | null
  emotion_trigger: string | null
  content_structure: string | null
  content_text?: string | null
  tags?: string[] | null
  card_draft?: MyWorkCardDraft | null
  first_page_html?: string | null
}

export interface MyWorkDetail extends MyWorkItem {
  content_text: string | null
  tags: any
  cover_img_url: string | null
  title_pattern: string | null
  emotion_trigger: string | null
  content_structure: string | null
  selected_pattern: any
  selected_direction: any
  created_at: string | null
}

export interface AttributionResult {
  my_attribution: Record<string, any>
  avoid_patterns: any[]
}

export async function listMyWorks(params: {
  page?: number
  page_size?: number
  platform?: string
  sort_by?: string
  content_status?: string
  origin?: 'collected' | 'generated' | 'all'
} = {}): Promise<{ items: MyWorkItem[]; total: number; page: number; page_size: number }> {
  const res = await apiClient.get('/my-works', { params })
  return res.data
}

export interface WorkFacetPlatform {
  platform: string
  label: string
  count: number
  bound: boolean
  supported: boolean
  nickname: string | null
}

export interface WorkFacets {
  platforms: WorkFacetPlatform[]
  total: number
}

export async function getWorkFacets(): Promise<WorkFacets> {
  const res = await apiClient.get('/my-works/facets')
  return res.data
}

export async function getMyWorkDetail(recordId: string): Promise<MyWorkDetail> {
  const res = await apiClient.get(`/my-works/${recordId}`)
  return res.data
}

/** 内容层编辑可提交字段；只传要改的，未传的保持原样 */
export interface ContentEditPayload {
  title?: string
  content_text?: string
  tags?: string[]
  images?: string[]
  cover_img_url?: string
}

export async function updateWorkContent(
  recordId: string,
  payload: ContentEditPayload,
): Promise<MyWorkDetail> {
  const res = await apiClient.patch(`/my-works/${recordId}/content`, payload)
  return res.data
}

export async function getAttribution(): Promise<AttributionResult> {
  const res = await apiClient.get('/my-works/attribution')
  return res.data
}

export async function collectNoteData(noteUrl: string, platform = 'auto'): Promise<any> {
  const res = await apiClient.post('/my-works/collect', { note_url: noteUrl, platform })
  return res
}

export async function csvImport(records: any[], platform = 'xiaohongshu'): Promise<{ imported: number; errors: number }> {
  const res = await apiClient.post('/my-works/csv-import', { records, platform })
  return res.data
}

export async function deleteMyWork(recordId: string): Promise<void> {
  await apiClient.delete(`/my-works/${recordId}`)
}

export interface AnalysisRankItem {
  name: string
  avg_score: number
  count: number
}

export interface AnalysisPlatformItem {
  platform: string
  count: number
  avg_score: number
  replicated_rate: number
  total_likes: number
  total_collects: number
}

export interface AnalysisTopPerformer {
  id: string
  title: string | null
  platform: string | null
  score: number
  likes: number | null
  collects: number | null
  comments: number | null
  cover_img_url: string | null
}

export interface AnalysisActionItem {
  priority: string
  action: string
  reason: string
}

export interface AnalysisOverview {
  total: number
  high_performers: number
  low_performers: number
  replicated: number
  total_likes: number
  total_collects: number
  total_comments: number
  avg_score: number
}

/** status 为 'no_data' 时其余字段均缺失 */
export interface AnalysisReport {
  status: string
  message?: string
  actions?: string[]
  confidence?: string
  sample_size?: number
  my_weight?: number
  overview?: AnalysisOverview
  platform_comparison?: AnalysisPlatformItem[]
  pattern_ranking?: AnalysisRankItem[]
  emotion_ranking?: AnalysisRankItem[]
  structure_ranking?: AnalysisRankItem[]
  top_performers?: AnalysisTopPerformer[]
  writing_prescription?: Record<string, any>
  avoid_patterns?: any
  action_items?: AnalysisActionItem[]
}

export async function getAnalysisReport(platform?: string): Promise<AnalysisReport> {
  const res = await apiClient.get('/my-works/analysis-report', { params: platform ? { platform } : {} })
  return res.data
}

export async function triggerAttribution(): Promise<any> {
  const res = await apiClient.post('/my-works/trigger-attribution')
  return res
}

export async function createDraft(title = '未命名草稿', platform = 'xiaohongshu', topic?: string): Promise<{ id: string; title: string; platform: string; content_status: string }> {
  const res = await apiClient.post('/my-works/draft', { title, platform, topic })
  return res.data
}

export async function updateContentStatus(recordId: string, contentStatus: 'draft' | 'published' | 'collected'): Promise<{ id: string; content_status: string }> {
  const res = await apiClient.patch(`/my-works/${recordId}/status`, { content_status: contentStatus })
  return res.data
}

export interface RefreshImagesResult {
  id: string
  status: 'refreshed' | 'already_cached' | 'failed'
  cover_img_url?: string
  images?: number
  message?: string
}

export async function refreshWorkImages(recordId: string): Promise<RefreshImagesResult> {
  // 响应拦截器已把 axios 响应替换成 StandardResponse 本体（{success, data, message}），
  // 因此这里按实际结构断言，避免按 AxiosResponse 取值时丢掉 message。
  const res = (await apiClient.post(`/my-works/${recordId}/refresh-images`)) as unknown as {
    data: RefreshImagesResult
    message?: string
  }
  return { ...res.data, message: res.message }
}

export interface DownloadMediaResult {
  status: 'started' | 'ready' | 'blocked'
  message: string
  local_video_url?: string | null
}

/** 启动作品视频后台下载（yt-dlp，平台白名单路由），完成后轮询作品详情取 local_video_url */
export async function downloadWorkMedia(recordId: string): Promise<DownloadMediaResult> {
  const res = await apiClient.post(`/my-works/${recordId}/download-media`)
  return res.data?.data ?? res.data
}