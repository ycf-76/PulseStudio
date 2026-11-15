import { ref, nextTick, type Ref, type ComputedRef } from 'vue'
import * as chatSessionsApi from '@/api/chatSessions'
import { useChatHistory } from '@/composables/useChatHistory'
import { useFileStore } from '@/stores/files'
import { useWorkspaceStore } from '@/stores/workspace'
import type { ChatMessage, Conversation, ChatStats } from '../cell-types'
import { abortSessionRuntime, peekSessionRuntime, adoptSessionRuntime } from './chatSessionRuntime'

function normalizeAgentMeta(raw: any): any {
  if (!raw || typeof raw !== 'object') return raw
  const meta: any = { ...raw }
  const map: Record<string, string> = {
    workflow_status: 'workflowStatus',
    loop_state_path: 'loopStatePath',
    confirmation_prompt: 'confirmationPrompt',
    confirmation_skill: 'confirmationSkill',
    clarification_prompt: 'clarificationPrompt',
    clarification_skill: 'clarificationSkill',
    clarification_batches: 'clarificationBatches',
    workflow_id: 'sessionId',
    current_step: 'currentStep',
    total_percent: 'totalPercent',
    // 口播叠加方案（cut_plan）状态落库是 snake_case，重进会话后
    // VideoTaskCard 靠 camelCase 字段推导阶段，必须补映射（2026-11-03 死卡修复）
    cut_plan: 'cutPlan',
    cut_plan_resolved: 'cutPlanResolved',
    cut_plan_error: 'cutPlanError',
    cut_plan_versions: 'cutPlanVersions',
    // 侧会话（Side Session）特殊消息与写确认闸门（PRD FR-B3/G6）
    message_type: 'messageType',
    side_session_id: 'sideSessionId',
    side_session_title: 'sideSessionTitle',
    side_write: 'sideWrite',
    side_write_resolved: 'sideWriteResolved',
    // 分镜 / guardian / 通用确认的 resolved 决策标记落库（2026-11-03 死卡推广），
    // 重载历史后 AssistantMessageCell 靠这些 camelCase 字段判稳不再弹卡
    storyboard_shots: 'storyboardShots',
    storyboard_title: 'storyboardTitle',
    storyboard_duration: 'storyboardDuration',
    storyboard_resolved: 'storyboardResolved',
    storyboard_error: 'storyboardError',
    guardian_tool_name: 'guardianToolName',
    guardian_resolved: 'guardianResolved',
    guardian_error: 'guardianError',
    confirmation_resolved: 'confirmationResolved',
    risk_info: 'riskInfo',
  }
  for (const [snake, camel] of Object.entries(map)) {
    if (meta[snake] !== undefined && meta[camel] === undefined) {
      meta[camel] = meta[snake]
    }
  }
  // 版本条内层结构：后端存 asset_url，前端消费 assetUrl
  if (Array.isArray(meta.cutPlanVersions)) {
    meta.cutPlanVersions = meta.cutPlanVersions.map((v: any) => ({
      version: v?.version,
      assetUrl: v?.assetUrl ?? v?.asset_url,
    }))
  }
  // 侧会话写确认详情：后端内层是 snake_case，统一转 camelCase
  if (meta.sideWrite && typeof meta.sideWrite === 'object') {
    meta.sideWrite = {
      toolName: meta.sideWrite.toolName ?? meta.sideWrite.tool_name ?? '',
      description: meta.sideWrite.description ?? '',
      permissions: Array.isArray(meta.sideWrite.permissions) ? meta.sideWrite.permissions : [],
      sideSessionId: meta.sideWrite.sideSessionId ?? meta.sideWrite.side_session_id ?? '',
    }
  }
  return meta
}

export interface ChatActionsDeps {
  conversations: Ref<Conversation[]>
  activeConvId: Ref<string>
  inputText: Ref<string>
  lastStats: Ref<ChatStats | null>
  heroMode: Ref<boolean>
  activeSessionId: Ref<string | null>
  scrollBodyRef: Ref<HTMLElement | null>
  inputRef: Ref<HTMLTextAreaElement | null>
  resetExpandAll: () => void
}

export function useChatActions(deps: ChatActionsDeps) {
  const {
    conversations, activeConvId, inputText, lastStats, heroMode,
    activeSessionId, scrollBodyRef, inputRef, resetExpandAll,
  } = deps

  const fileStore = useFileStore()
  const workspaceStore = useWorkspaceStore()
  const {
    addConversation, getSessionId, removeConversation,
    getConversationsForFolder, getFolderId,
  } = useChatHistory()

  let _switchingConv = false
  let _creatingConv = false

  /**
   * 终止指定会话的在途任务（只影响这一个会话）。
   * 多任务并行后，切会话/新建会话都不该再动别的会话的连接，
   * 所以这里只保留「删除会话」这类确实要收尾的场景。
   */
  function abortSession(sessionId?: string | null) {
    if (!sessionId) return
    abortSessionRuntime(sessionId)
  }

  async function scrollToBottom(smooth = false) {
    await nextTick()
    if (scrollBodyRef.value) {
      scrollBodyRef.value.scrollTo({
        top: scrollBodyRef.value.scrollHeight,
        behavior: smooth ? 'smooth' : 'auto',
      })
    }
  }

  async function newConversation(folderId?: string, workId?: string) {
    if (_creatingConv) return

    const currentConv = conversations.value.find(c => c.id === activeConvId.value)
    if (currentConv && currentConv.messages.length === 0) {
      heroMode.value = true
      // preventScroll 必须带：此时入场动画把输入卡摆在视口下方 64vh 处，
      // 不带 preventScroll 的 focus() 会让浏览器把 .rl-chat 滚过去，
      // 内容整体被顶上去，动画推进时又被夹回 —— 就是「输入框飞到顶部再回来」。
      nextTick(() => { inputRef.value?.focus({ preventScroll: true }) })
      return
    }

    _creatingConv = true
    try {
      // 新建会话只换视图目标：别的会话若还在跑，它的连接与状态必须原样保留
      let sessionId: string | null = null
      try {
        const sess = await chatSessionsApi.createSession('新会话', workId, folderId || 'chat-files')
        sessionId = sess.id
        activeSessionId.value = sessionId
      } catch {
        // session creation failed, continue without backend persistence
      }

      const id = sessionId || 'conv_' + Date.now()
      const conv: Conversation = {
        id,
        title: '新会话',
        messages: [],
        createdAt: Date.now(),
      }
      conversations.value.unshift(conv)
      activeConvId.value = id
      const effectiveFolderId = folderId || 'chat-files'
      addConversation(id, '新会话', effectiveFolderId, sessionId || undefined, workId)
      fileStore.setActiveFolder(effectiveFolderId)
      // ws- 文件夹对应本地工作区：必须同步激活，否则 workspace_id 不会随请求发出，
      // 模型既不知道文件夹名，也拿不到目录里的文件。
      if (effectiveFolderId.startsWith('ws-')) {
        workspaceStore.setActive(effectiveFolderId.slice(3))
      }
      inputText.value = ''
      lastStats.value = null
      heroMode.value = true
      resetExpandAll()
      nextTick(() => {
        // 同上一处：入场动画期间 focus 会触发滚动容器滚动，必须 preventScroll
        inputRef.value?.focus({ preventScroll: true })
      })
    } finally {
      _creatingConv = false
    }
  }

  async function switchToFolder(folderId: string) {
    fileStore.setActiveFolder(folderId)
    if (folderId.startsWith('ws-')) {
      workspaceStore.setActive(folderId.slice(3))
    }
    const alreadyInFolder = !!activeConvId.value &&
      getConversationsForFolder(folderId).some(h => h.id === activeConvId.value)
    if (alreadyInFolder) {
      const conv = conversations.value.find(c => c.id === activeConvId.value)
      heroMode.value = !conv || conv.messages.length === 0
      return
    }
    await newConversation(folderId)
  }

  function onSwitchConv(e: Event) {
    const v = (e.target as HTMLSelectElement).value
    const conv = conversations.value.find(c => c.id === v)
    heroMode.value = !conv || conv.messages.length === 0
    activeConvId.value = v
    const sid = getSessionId(v) || v
    activeSessionId.value = sid
    void scrollToBottom()
  }

  function deleteConversation(convId: string) {
    const idx = conversations.value.findIndex(c => c.id === convId)
    if (idx === -1) return
    conversations.value.splice(idx, 1)
    removeConversation(convId)
    chatSessionsApi.deleteSession(convId).catch(() => {})
    if (activeConvId.value === convId) {
      // 删掉的这个会话若还在跑，只收它自己的连接
      abortSession(getSessionId(convId) || convId)
      if (conversations.value.length > 0) {
        const next = conversations.value[0]
        activeConvId.value = next.id
        const sid = getSessionId(next.id) || next.id
        activeSessionId.value = sid
      } else {
        activeConvId.value = ''
        activeSessionId.value = null
        heroMode.value = true
      }
    }
  }

  async function switchToConversation(convId: string, mergeLoadedHistory?: (conv: Conversation, msgs: any[]) => void) {
    const conv = conversations.value.find(c => c.id === convId)
    const sid = getSessionId(convId) || convId
    // 切会话只换视图投影：目标会话若还在后台跑，直接接上它自己的运行时；
    // 原会话的连接与状态保持不动，切回来接着看。
    activeSessionId.value = sid
    _switchingConv = true
    try {
      activeConvId.value = convId
      // 切换对话时恢复其文件夹归属：否则 activeFolderId 还停在上一个对话的文件夹，
      // 发消息时 folder_context 会张冠李戴。
      const convFolderId = getFolderId(convId)
      if (convFolderId) {
        fileStore.setActiveFolder(convFolderId)
        if (convFolderId.startsWith('ws-')) {
          workspaceStore.setActive(convFolderId.slice(3))
        }
      }
      if (conv && conv.messages.length === 0) {
        heroMode.value = true
        try {
          const msgs = await chatSessionsApi.listMessages(sid)
          if (mergeLoadedHistory) {
            mergeLoadedHistory(conv, msgs)
          } else {
            conv.messages = msgs.map(m => ({
              role: m.role as 'user' | 'assistant' | 'system',
              content: m.content,
              agentMeta: (normalizeAgentMeta(m.agent_meta) || {
                sessionId: null,
                steps: [],
                totalPercent: 0,
                workflowStatus: 'completed' as const,
                turnPhase: 'done' as const,
                statusIndicator: { header: '' },
              }) as any,
            })).filter(m => m.role !== 'system')
          }
          if (conv.messages.length > 0) heroMode.value = false
          // 防御性降级（与 useChatHistorySync.mergeLoadedHistory 同款）：
          // 历史里所有 awaiting_* 都是死状态，降级 completed 防止进会话
          // 就弹确认/澄清 dock 卡。
          // 多任务并行后要留活口：该会话在本标签页仍有在途运行时（正在跑或
          // 暂停等确认），状态是活的，降级会抹掉它自己的确认卡 → 服务端空等。
          if (!peekSessionRuntime(sid)?.isStreaming.value) {
            for (const m of conv.messages) {
              if (m.role !== 'assistant' || !m.agentMeta?.workflowStatus) continue
              if (String(m.agentMeta.workflowStatus).startsWith('awaiting_')) {
                m.agentMeta.workflowStatus = 'completed'
              }
            }
          }
        } catch {
          // failed to load messages
        }
      } else {
        heroMode.value = !conv || conv.messages.length === 0
      }
      await fileStore.loadFilesForSession(sid)
      void scrollToBottom()
    } finally {
      _switchingConv = false
    }
  }

  function isSwitchingConv() {
    return _switchingConv
  }

  function enterHeroMode() {
    heroMode.value = true
  }

  // 发送前确保 session 存在：已有 activeSessionId 直接复用，否则按标题创建。
  // 返回真实 session_id；后端不可用时返回 null（调用方走本地草稿态兜底）。
  async function ensureSession(title?: string): Promise<string | null> {
    if (activeSessionId.value) return activeSessionId.value
    try {
      const sess = await chatSessionsApi.createSession(
        title || '新会话',
        undefined,
        fileStore.activeFolderId || 'chat-files',
      )
      if (sess?.id) {
        // 与 useChatAgent 同一处理：草稿态运行时先认领到真实 id，
        // 否则「已置 isStreaming=true 的草稿运行时」会留在草稿槽里，
        // 后面的投影落到一个新建的空运行时，状态分家、草稿槽还被污染。
        adoptSessionRuntime(sess.id)
        activeSessionId.value = sess.id
        return sess.id
      }
    } catch {
      // session creation failed, caller falls back to local draft
    }
    return null
  }

  function resetToInitial() {
    activeConvId.value = ''
    /* 会话标识一并解绑：否则回到初始对话框后发的第一条消息，
       会被 ensureSession 复用上一个作品的 session，写进别人的会话里。 */
    activeSessionId.value = null
    /* 文件夹 / 工作区归属也回落到默认：folder_context 直接取自 activeFolderId，
       不重置的话，回到初始对话框后发的消息仍会带着上一个作品所在文件夹的上下文。 */
    fileStore.setActiveFolder('chat-files')
    workspaceStore.setActive(null)
    heroMode.value = true
    inputText.value = ''
    lastStats.value = null
    resetExpandAll()
  }

  return {
    newConversation,
    switchToFolder,
    onSwitchConv,
    deleteConversation,
    switchToConversation,
    isSwitchingConv,
    enterHeroMode,
    resetToInitial,
    ensureSession,
    scrollToBottom,
    normalizeAgentMeta,
  }
}