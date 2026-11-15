import apiClient from './client'
import type {
  ArtifactSummary,
  CreativeArtifact,
  StoryboardPage,
} from '@/types/creativeArtifact'

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

export async function listCreativeArtifacts(
  limit = 50,
  scope: { workId?: string; sessionId?: string } = {},
): Promise<ArtifactSummary[]> {
  const params: Record<string, any> = { limit }
  // 产物隔离：按作品/会话收窄，避免不同创作任务互相串台
  if (scope.workId) params.work_id = scope.workId
  if (scope.sessionId) params.session_id = scope.sessionId
  const res = await apiClient.get('/creative-artifacts', { params })
  return unwrap<ArtifactSummary[]>(res) || []
}

export async function getCreativeArtifact(id: string): Promise<CreativeArtifact | null> {
  try {
    const res = await apiClient.get(`/creative-artifacts/${id}`)
    return unwrap<CreativeArtifact>(res) || null
  } catch {
    return null
  }
}

export async function buildCreativeArtifact(payload: {
  artifact_id?: string
  card_draft?: Record<string, any>
  brief?: Record<string, any>
  analysis?: Record<string, any>
  source?: Record<string, any>
  persist?: boolean
}): Promise<CreativeArtifact> {
  const res = await apiClient.post('/creative-artifacts/build', payload)
  return unwrap<CreativeArtifact>(res)
}

export async function buildStoryboard(
  cardDraft: Record<string, any>,
): Promise<StoryboardPage[]> {
  const res = await apiClient.post('/creative-artifacts/storyboard', {
    card_draft: cardDraft,
  })
  return unwrap<StoryboardPage[]>(res) || []
}

export async function saveCreativeArtifact(
  id: string,
  artifact: Partial<CreativeArtifact>,
): Promise<CreativeArtifact | null> {
  try {
    const res = await apiClient.put(`/creative-artifacts/${id}`, artifact)
    return unwrap<CreativeArtifact>(res) || null
  } catch {
    return null
  }
}

export async function patchCreativeArtifact(
  id: string,
  updates: Record<string, any>,
): Promise<CreativeArtifact | null> {
  try {
    const res = await apiClient.patch(`/creative-artifacts/${id}`, updates)
    return unwrap<CreativeArtifact>(res) || null
  } catch {
    return null
  }
}

export async function getArtifactCardDraft(
  id: string,
): Promise<Record<string, any> | null> {
  try {
    const res = await apiClient.get(`/creative-artifacts/${id}/card-draft`)
    return unwrap<Record<string, any>>(res) || null
  } catch {
    return null
  }
}

export async function deleteCreativeArtifact(id: string): Promise<boolean> {
  try {
    await apiClient.delete(`/creative-artifacts/${id}`)
    return true
  } catch {
    return false
  }
}