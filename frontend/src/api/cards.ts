import apiClient from './client'

interface StandardResponse<T> {
  data?: T
  success?: boolean
  message?: string
}

function unwrap<T>(res: unknown): T {
  const payload = res as StandardResponse<T> | T
  if (payload && typeof payload === 'object' && 'data' in (payload as object)) {
    return (payload as StandardResponse<T>).data as T
  }
  return payload as T
}

export interface CardEditPayload {
  expected_version: number
  page_id?: string
  block_id?: string
  op: string
  index?: number
  column?: 'left' | 'right'
  value?: string
  value_pair?: string[]
  note?: string
  tag?: string
  kicker?: string
  unit?: string
  source?: string
}

export interface CardDraftDeckPage {
  id: string
  role: string
  layout_id: string
  blocks: Array<Record<string, any> & { id: string; type: string }>
}

export interface CardDraftDeck {
  schema_version?: number
  deck_id: string
  title: string
  kicker?: string
  theme_id: string
  accent_index: number
  pages: CardDraftDeckPage[]
}

export interface CardDraftPage {
  id: string
  type: string
  layoutId: string
  availableLayouts: string[]
  title: string
  htmlContent: string
  density?: Record<string, any>
}

export interface CardDraft {
  schema_version?: number
  title: string
  template?: string
  deck: CardDraftDeck
  themeId: string
  themeOptions: Array<{ id: string; label: string; hint: string; accents: string[] }>
  accentIndex: number
  accentOptions: string[]
  variantOptions: Record<string, string[]>
  availableDirections: string[]
  pages: CardDraftPage[]
  artifact_id?: string
  version?: number
}

/** 读取作品的结构化卡片（deck + 版本号 + 每页已渲染 HTML）。 */
export async function getCardDraft(artifactId: string): Promise<CardDraft> {
  const res = await apiClient.get(`/cards/${artifactId}`)
  return unwrap<CardDraft>(res)
}

/** 执行一次块级编辑，返回新版本 draft。版本过期时抛 409。 */
export async function editCard(artifactId: string, payload: CardEditPayload): Promise<CardDraft> {
  const res = await apiClient.post(`/cards/${artifactId}/edit`, payload)
  return unwrap<CardDraft>(res)
}