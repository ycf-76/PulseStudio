import { authFetch } from './client'

export interface ChatFolderItem {
  id: string
  name: string
  path: string
  position: number
  created_at: string | null
  updated_at: string | null
}

export async function listFolders(): Promise<ChatFolderItem[]> {
  const res = await authFetch('/api/chat/folders')
  if (!res.ok) return []
  const data = await res.json()
  return data.data || []
}

export async function createFolder(name: string, path = ''): Promise<ChatFolderItem | null> {
  const res = await authFetch('/api/chat/folders', {
    method: 'POST',
    body: JSON.stringify({ name, path }),
  })
  if (!res.ok) return null
  const data = await res.json()
  return data.data || null
}

export async function updateFolder(
  folderId: string,
  payload: { name?: string; path?: string },
): Promise<ChatFolderItem | null> {
  const res = await authFetch(`/api/chat/folders/${folderId}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })
  if (!res.ok) return null
  const data = await res.json()
  return data.data || null
}

export async function deleteFolder(folderId: string): Promise<boolean> {
  const res = await authFetch(`/api/chat/folders/${folderId}`, { method: 'DELETE' })
  if (!res.ok) return false
  const data = await res.json()
  return data.data === true
}