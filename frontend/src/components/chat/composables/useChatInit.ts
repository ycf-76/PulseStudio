import type { Ref } from 'vue'
import type { Conversation } from '../cell-types'
import * as chatSessionsApi from '@/api/chatSessions'
import { agentsApi } from '@/api/agents'
import { useSkillsLibrary } from '@/composables/useSkillsLibrary'


export interface ChatInitDeps {
  conversations: Ref<Conversation[]>
  activeConvId: Ref<string>
  heroMode: Ref<boolean>
  chatHistoryLoaded: Ref<boolean>
  crabVisible: Ref<boolean>
  sse: any
  fileStore: any
  pluginStore: any
  workspaceStore: any
  pickers: any
  addConversation: (id: string, title: string, folderId: string, sessionId: string, workId?: string, times?: { createdAt?: number; updatedAt?: number }) => void
  mergeLoadedHistory: (conv: Conversation, msgs: any[]) => void
  startNotificationSSE: () => void
  onDocumentClick: (e: MouseEvent) => void
  CRAB_PLUGIN_ID: string
  suppressWatcher: () => void
  releaseWatcher: () => void
}

export function useChatInit(deps: ChatInitDeps) {
  const {
    conversations, activeConvId, heroMode, chatHistoryLoaded, crabVisible,
    sse, fileStore, pluginStore, workspaceStore, pickers,
    addConversation, mergeLoadedHistory, startNotificationSSE, onDocumentClick, CRAB_PLUGIN_ID,
    suppressWatcher, releaseWatcher,
  } = deps

  async function init() {
    try {
      const sessions = await chatSessionsApi.listSessions()
      await fileStore.loadFromBackend()

      if (sessions.length > 0) {
        for (const sess of sessions) {
          // 侧会话不进主会话侧边栏（只在侧会话面板出现，PRD FR-E1）
          if (sess.kind === 'side') continue
          const exists = conversations.value.find(c => c.id === sess.id)
          if (!exists) {
            const conv: Conversation = {
              id: sess.id,
              title: sess.title || '新会话',
              messages: [],
              createdAt: sess.created_at ? new Date(sess.created_at).getTime() : Date.now(),
            }
            conversations.value.push(conv)
          }
          addConversation(sess.id, sess.title || '新会话', sess.folder_id || 'chat-files', sess.id, sess.work_id || undefined)
        }
        conversations.value.sort((a, b) => b.createdAt - a.createdAt)

        const latest = conversations.value[0]
        if (latest) {
          suppressWatcher()
          activeConvId.value = latest.id
          sse.activeSessionId.value = latest.id
          try {
            const msgs = await chatSessionsApi.listMessages(latest.id)
            mergeLoadedHistory(latest, msgs)
          } catch {
          }
          releaseWatcher()
        }
        heroMode.value = false
        chatHistoryLoaded.value = true
      } else {
        heroMode.value = true
        chatHistoryLoaded.value = true
      }
    } catch {
      heroMode.value = true
      chatHistoryLoaded.value = true
    }

    await pluginStore.loadInstalledPlugins().catch(() => {})

    workspaceStore.fetchWorkspaces().catch(() => {})
    crabVisible.value = true
    console.log('[ChatView] crabVisible=true, isEnabled(crab-companion)=', pluginStore.isEnabled(CRAB_PLUGIN_ID))

    agentsApi.list().then(list => {
      const agents = Array.isArray(list) ? list : (Array.isArray((list as any)?.data) ? (list as any).data : [])
      console.log('[ChatInit] agentsApi.list loaded:', agents.length, 'agents')
      pickers.availableAgents.value = agents
    }).catch(e => {
      console.error('[ChatInit] agentsApi.list failed:', e)
    })

    const skillsLib = useSkillsLibrary()
    skillsLib.load().then(() => {
      pickers.allSkills.value = skillsLib.flatSkills.value.map(s => ({
        node_type: s.node_type,
        name: s.name,
        display_name: s.display_name,
        description: s.description || '',
      }))
    }).catch(e => {
      console.error('[ChatInit] skills load failed:', e)
    })
    document.addEventListener('click', onDocumentClick)

    startNotificationSSE()
  }

  return { init }
}