import { ref, reactive, computed } from 'vue'
import * as chatSessionsApi from '@/api/chatSessions'

export interface ChatHistoryItem {
  id: string
  title: string
  folderId?: string
  sessionId?: string
  workId?: string | null
  createdAt: number
  updatedAt?: number
}

export interface ChatHistoryTimes {
  createdAt?: number
  updatedAt?: number
}

const MAX_DISPLAY = 20

function activityAt(item: ChatHistoryItem): number {
  return item.updatedAt || item.createdAt
}

function sortByRecent() {
  history.value.sort((a, b) => activityAt(b) - activityAt(a))
}

const history = ref<ChatHistoryItem[]>([])

// ═══ 正在执行的会话注册表（2026-11-02）═══
// 模块级单例：ChatView 的回合状态机（_transitionTurn）写入，
// SidebarNav 读取 → 会话/文件夹行右侧渲染螃蟹放大缩小标志。
// 等待澄清/确认（awaiting_*）不算执行中，由 ChatView 判定后再写。
export const runningSessionIds = reactive(new Set<string>())

export function markSessionRunning(sessionId: string | null | undefined, running: boolean) {
  if (!sessionId) return
  if (running) runningSessionIds.add(sessionId)
  else runningSessionIds.delete(sessionId)
}
const allExpanded = ref(false)
const loaded = ref(false)

export function useChatHistory() {
  const displayItems = computed(() => {
    if (allExpanded.value) return history.value
    return history.value.slice(0, MAX_DISPLAY)
  })
  const hasMore = computed(() => history.value.length > MAX_DISPLAY)
  const totalCount = computed(() => history.value.length)

  async function loadFromBackend(workId?: string | null) {
    try {
      const sessions = await chatSessionsApi.listSessions(workId)
      if (workId) {
        const filtered = history.value.filter(h => h.workId !== workId)
        for (const s of sessions) {
          const exists = filtered.find(h => h.sessionId === s.id || h.id === s.id)
          if (!exists) {
            filtered.push({
              id: s.id,
              title: s.title || '新会话',
              folderId: s.folder_id || undefined,
              sessionId: s.id,
              workId: s.work_id,
              createdAt: s.created_at ? new Date(s.created_at).getTime() : Date.now(),
              updatedAt: s.updated_at ? new Date(s.updated_at).getTime() : undefined,
            })
          }
        }
        filtered.sort((a, b) => activityAt(b) - activityAt(a))
        history.value = filtered
      } else {
        for (const s of sessions) {
          const exists = history.value.find(h => h.sessionId === s.id || h.id === s.id)
          if (!exists) {
            history.value.push({
              id: s.id,
              title: s.title || '新会话',
              folderId: s.folder_id || undefined,
              sessionId: s.id,
              workId: s.work_id,
              createdAt: s.created_at ? new Date(s.created_at).getTime() : Date.now(),
              updatedAt: s.updated_at ? new Date(s.updated_at).getTime() : undefined,
            })
          }
        }
        sortByRecent()
      }
      loaded.value = true
    } catch {
      loaded.value = true
    }
  }

  function addConversation(
    id: string,
    title: string,
    folderId?: string,
    sessionId?: string,
    workId?: string | null,
    times?: ChatHistoryTimes,
  ) {
    const exists = history.value.find(item => item.id === id)
    if (exists) {
      exists.title = title
      if (folderId) exists.folderId = folderId
      if (sessionId) exists.sessionId = sessionId
      if (workId !== undefined) exists.workId = workId
      if (times?.createdAt) exists.createdAt = times.createdAt
      if (times?.updatedAt) exists.updatedAt = times.updatedAt
      sortByRecent()
      console.log('[useChatHistory] addConversation: updated existing', id, 'history.length=', history.value.length)
      return
    }
    const createdAt = times?.createdAt ?? Date.now()
    history.value.unshift({
      id,
      title: title || '新会话',
      folderId,
      sessionId,
      workId,
      createdAt,
      updatedAt: times?.updatedAt ?? createdAt,
    })
    sortByRecent()
    console.log('[useChatHistory] addConversation: added new', id, 'history.length=', history.value.length)
  }

  function touchConversation(id: string) {
    const item = history.value.find(h => h.id === id)
    if (!item) return
    item.updatedAt = Date.now()
    sortByRecent()
  }

  function getConversationsForFolder(folderId: string) {
    return history.value.filter(item => item.folderId === folderId)
  }

  function getConversationsForWork(workId: string) {
    return history.value.filter(item => item.workId === workId)
  }

  function removeConversation(id: string) {
    const idx = history.value.findIndex(item => item.id === id)
    if (idx !== -1) history.value.splice(idx, 1)
  }

  function updateTitle(id: string, title: string) {
    const item = history.value.find(item => item.id === id)
    if (item) item.title = title || '新会话'
  }

  function unfileConversations(folderId: string) {
    for (const item of history.value) {
      if (item.folderId === folderId) item.folderId = undefined
    }
  }

  function getSessionId(convId: string): string | null {
    const item = history.value.find(h => h.id === convId)
    return item?.sessionId || null
  }

  function getFolderId(convId: string): string | null {
    const item = history.value.find(h => h.id === convId)
    return item?.folderId || null
  }

  function findBySessionId(sessionId: string): ChatHistoryItem | null {
    return history.value.find(h => h.sessionId === sessionId) || null
  }

  function clearAll() {
    history.value = []
    allExpanded.value = false
  }

  function toggleExpand() {
    allExpanded.value = !allExpanded.value
  }

  return {
    history,
    displayItems,
    hasMore,
    totalCount,
    allExpanded,
    loaded,
    addConversation,
    touchConversation,
    getConversationsForFolder,
    getConversationsForWork,
    removeConversation,
    updateTitle,
    unfileConversations,
    getSessionId,
    getFolderId,
    findBySessionId,
    loadFromBackend,
    clearAll,
    toggleExpand,
    runningSessionIds,
  }
}