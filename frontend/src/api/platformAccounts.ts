import { authFetch } from './client'

export interface PlatformAccount {
  id: string | null
  platform: string
  platform_label: string
  platform_uid: string | null
  platform_nickname: string | null
  platform_avatar_url: string | null
  platform_home_url: string | null
  last_synced_at: string | null
  sync_status: string
  sync_error: string | null
  works_count: number
  fans_count: number | null
  bound: boolean
}

export interface QrCodePayload {
  qr_base64?: string
  qr_url?: string
  error?: string
}

export interface BindResult {
  task_id: string
  platform: string
  qr_code: QrCodePayload
}

export type QrBindStatus = 'pending' | 'scanned' | 'confirmed' | 'failed' | 'expired'

export interface QrStatusResult {
  task_id: string
  status: QrBindStatus
  platform: string
  error?: string
  account?: { nickname?: string; uid?: string; avatar_url?: string }
}

export interface SyncStartResult {
  status: 'started' | 'already_running'
  message?: string
}

async function parseJson(res: Response, action: string): Promise<Record<string, any>> {
  if (!res.ok) throw new Error(`${action}失败 (${res.status})`)
  return res.json()
}

export async function fetchAccounts(): Promise<PlatformAccount[]> {
  const data = await parseJson(await authFetch('/api/accounts'), '加载账号')
  if (data?.success === false) throw new Error(data.message || '加载账号失败')
  return (data.data || []) as PlatformAccount[]
}

export async function bindPlatformAccount(platform: string): Promise<BindResult> {
  const data = await parseJson(
    await authFetch('/api/accounts/bind', {
      method: 'POST',
      body: JSON.stringify({ platform }),
    }),
    '获取二维码',
  )
  if (data?.success === false) throw new Error(data.message || '获取二维码失败')
  return data.data as BindResult
}

export async function fetchQrStatus(taskId: string): Promise<QrStatusResult> {
  const data = await parseJson(
    await authFetch(`/api/accounts/qr-status/${encodeURIComponent(taskId)}`),
    '查询扫码状态',
  )
  if (data?.success === false) throw new Error(data.message || '查询扫码状态失败')
  return data.data as QrStatusResult
}

export async function unbindPlatformAccount(accountId: string): Promise<void> {
  const data = await parseJson(
    await authFetch(`/api/accounts/${encodeURIComponent(accountId)}`, { method: 'DELETE' }),
    '解绑',
  )
  if (data?.success === false) throw new Error(data.message || '解绑失败')
}

export async function syncPlatformAccount(accountId: string, force = true): Promise<SyncStartResult> {
  const data = await parseJson(
    await authFetch(`/api/accounts/${encodeURIComponent(accountId)}/sync`, {
      method: 'POST',
      body: JSON.stringify({ force }),
    }),
    '启动同步',
  )
  if (data?.success === false) {
    return { status: 'already_running', message: data.message }
  }
  return { status: 'started', message: data?.data?.message }
}

export async function syncAllPlatformAccounts(force = true): Promise<number> {
  const data = await parseJson(
    await authFetch('/api/accounts/sync-all', {
      method: 'POST',
      body: JSON.stringify({ force }),
    }),
    '启动全平台同步',
  )
  if (data?.success === false) throw new Error(data.message || '启动全平台同步失败')
  return data?.data?.synced ?? 0
}