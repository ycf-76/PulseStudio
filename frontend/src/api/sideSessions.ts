import { authFetch } from './client'

export interface SideSessionItem {
  id: string
  title: string
  status: 'active' | 'archived'
  has_side_effects: boolean
  granularity: 'full' | 'recent_n' | 'digest' | string
  baseline_message_id: string
  /** 快照基准点前的主任务用户轮次数（徽标「基于主任务第 N 轮」） */
  baseline_turn: number
  expired_turns: number
  token_level: 'low' | 'mid' | 'high' | string
}

export interface CreateSideSessionPayload {
  granularity: 'full' | 'recent_n' | 'digest'
  recent_n?: number
}

/** 创建侧会话：以当前主会话状态为基准点生成快照（201）。超上限等校验失败抛错。 */
export async function createSideSession(
  mainSessionId: string,
  payload: CreateSideSessionPayload,
): Promise<SideSessionItem> {
  const res = await authFetch(`/api/chat/sessions/${mainSessionId}/side-sessions`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
  if (!res.ok) {
    const detail = await _errorDetail(res)
    throw new Error(detail || `createSideSession failed: ${res.status}`)
  }
  const data = await res.json()
  return data.data
}

export async function listSideSessions(mainSessionId: string): Promise<SideSessionItem[]> {
  const res = await authFetch(`/api/chat/sessions/${mainSessionId}/side-sessions`)
  if (!res.ok) return []
  const data = await res.json()
  return data.data || []
}

export async function updateSideSession(
  sideSessionId: string,
  payload: { title?: string; status?: 'active' | 'archived' },
): Promise<{ id: string; title: string; status: string }> {
  const res = await authFetch(`/api/side-sessions/${sideSessionId}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })
  if (!res.ok) {
    const detail = await _errorDetail(res)
    throw new Error(detail || `updateSideSession failed: ${res.status}`)
  }
  const data = await res.json()
  return data.data
}

export async function deleteSideSession(sideSessionId: string): Promise<void> {
  const res = await authFetch(`/api/side-sessions/${sideSessionId}`, { method: 'DELETE' })
  if (!res.ok) {
    const detail = await _errorDetail(res)
    throw new Error(detail || `deleteSideSession failed: ${res.status}`)
  }
}

/** 主任务收尾：decisions 形如 { [sideId]: 'keep' | 'discard' } */
export async function finalizeMainSession(
  mainSessionId: string,
  decisions: Record<string, 'keep' | 'discard'>,
): Promise<{ results: Record<string, string> }> {
  const res = await authFetch(`/api/chat/sessions/${mainSessionId}/finalize`, {
    method: 'POST',
    body: JSON.stringify({ decisions }),
  })
  if (!res.ok) {
    const detail = await _errorDetail(res)
    throw new Error(detail || `finalizeMainSession failed: ${res.status}`)
  }
  return await res.json()
}

/** 生成摘要草稿（LLM 提炼，用户可编辑后再带回）。 */
export async function generateDigestDraft(sideSessionId: string): Promise<string> {
  const res = await authFetch(`/api/side-sessions/${sideSessionId}/bring-back/draft`, {
    method: 'POST',
  })
  if (!res.ok) {
    const detail = await _errorDetail(res)
    throw new Error(detail || `generateDigestDraft failed: ${res.status}`)
  }
  const data = await res.json()
  return data.data?.digest || ''
}

/** 带回主任务：摘要注入主会话为特殊消息。超长度上限（问答总字符 ×30%）抛错。 */
export async function bringBack(
  sideSessionId: string,
  digest: string,
): Promise<{ main_session_id: string; main_message_id: string }> {
  const res = await authFetch(`/api/side-sessions/${sideSessionId}/bring-back`, {
    method: 'POST',
    body: JSON.stringify({ digest }),
  })
  if (!res.ok) {
    const detail = await _errorDetail(res)
    throw new Error(detail || `bringBack failed: ${res.status}`)
  }
  const data = await res.json()
  return data.data
}

/** 侧会话写操作确认：approve 逐次放行恢复执行；reject 只读继续。 */
export async function confirmSideWrite(
  sideSessionId: string,
  payload: { action: 'approve' | 'reject'; loop_state_path: string; turn_id?: string | null },
): Promise<{ side_session_id: string; status: 'approved' | 'rejected' }> {
  const res = await authFetch(`/api/chat/sessions/${sideSessionId}/side-write-confirm`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
  if (!res.ok) {
    const detail = await _errorDetail(res)
    throw new Error(detail || `confirmSideWrite failed: ${res.status}`)
  }
  const data = await res.json()
  return data.data
}

/** 面板内联聊天：向侧会话发送消息（非流式端点，正文增量走 SSE 事件流）。 */
export async function sendSideAgentMessage(
  sideSessionId: string,
  message: string,
): Promise<{ status: string; message?: string; output?: Record<string, unknown>; turn_id?: string }> {
  const res = await authFetch('/api/v1/chat/agent', {
    method: 'POST',
    body: JSON.stringify({ message, session_id: sideSessionId, mode: 'auto' }),
  })
  if (!res.ok) {
    const detail = await _errorDetail(res)
    throw new Error(detail || `sendSideAgentMessage failed: ${res.status}`)
  }
  return await res.json()
}

async function _errorDetail(res: Response): Promise<string> {
  try {
    const body = await res.json()
    return body?.detail || body?.message || ''
  } catch {
    return ''
  }
}
