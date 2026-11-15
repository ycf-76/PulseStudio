import { reactive, nextTick, type Ref } from 'vue'
import { authFetch } from '@/api/client'
import { stripSendHints, extractTopicReference, type TopicReferencePayload } from '../chat-content-renderer'
import type { ChatMessage, Conversation, UserAttachment } from '../cell-types'
import { getSessionRuntime } from './chatSessionRuntime'

export interface ChatInteractionsDeps {
  conversations: Ref<Conversation[]>
  activeConvId: Ref<string>
  inputText: Ref<string>
  activeSessionId: Ref<string | null>
  /**
   * conv.id → 后端 sessionId。
   * 多任务并行的关键：确认/澄清/停止这些交互必须落到「消息所属会话」的运行时上，
   * 不能落在"当前视图会话"的全局投影上——否则切走后点批准，会把状态写到别人头上。
   */
  getSessionId: (convId: string) => string | null
  // 与实现（useChatSSE 690）一致：retryCount 在 turnId 前
  subscribeChatSSE: (sessionId: string, assistantMsg: ChatMessage, retryCount?: number, turnId?: string, skipStartGate?: boolean) => Promise<void>
  sendMessageRef: Ref<() => Promise<void>>
  /** 重试时把上一条用户消息带的图片放回待选区（否则重发会丢 image_asset_ids） */
  restorePendingImages: (atts: UserAttachment[]) => void
  /** 重试时把上一条用户消息带的选题参考还原回参考条（否则重发会丢热点上下文） */
  restoreTopicReference: (ref: TopicReferencePayload) => void
}

export function useChatInteractions(deps: ChatInteractionsDeps) {
  const {
    conversations, activeConvId, inputText, activeSessionId,
    getSessionId, subscribeChatSSE, sendMessageRef,
    restorePendingImages, restoreTopicReference,
  } = deps

  /** 解析某个会话的运行时：交互只改「这条消息所属会话」的状态 */
  function _rtOf(conv: Conversation) {
    const sid = getSessionId(conv.id) || (conv.id === activeConvId.value ? activeSessionId.value : null)
    return { sid, rt: getSessionRuntime(sid) }
  }

  function onChatConfirmed(msg: ChatMessage, action: 'approve' | 'reject', data?: any) {
    if (msg.agentMeta) {
      msg.agentMeta.workflowStatus = action === 'approve' ? 'running' : 'error'
    }
    const conv = conversations.value.find((c) => c.messages.includes(msg))
    if (!conv) return
    // 只操作本消息所属会话的运行时（多任务并行：别的会话在跑，互不影响）
    const { sid: convSid, rt } = _rtOf(conv)

    if (action === 'reject') {
      rt.isStreaming.value = false
      rt.streamingMsg.value = null
      rt.notifyImmediateComplete()
      return
    }

    // approve: 恢复流式状态，重新订阅 SSE 接收续跑输出
    rt.resumeFromPause()
    rt.isStreaming.value = true
    rt.streamingMsg.value = msg
    rt.streamingHasContent.value = false
    rt.streamingThinking.value = false
    // 2026-11-02（Fix Issue 1 同款，对齐 onChatClarified）：批准续跑后
    // 旧正文必须清空——不清的话上一阶段残留（确认兜底行/旧阶段文字）
    // 会和续跑的最终输出拼在同一条消息里，确认文案永远留在回复顶部
    // （大哥截图实锤）。续跑输出由 SSE delta 首块接管 content。
    msg.content = ''

    // 订阅去重（本函数内只允许一条长连接）：
    // 后端异步续跑立即返回 status=resumed，若「前置订阅」与尾部「兜底/其他状态订阅」
    // 各开一条，两条连接同时收到同一批 live 事件 → 同一个播放器被投喂两遍 → 正文重复。
    // Last-Event-ID 只能挡住 replay 重复，拦不住 live 重复，故必须去重。
    let sseSubscribed = false
    const subscribeOnce = (targetSid: string) => {
      if (sseSubscribed) return
      sseSubscribed = true
      // 续跑必须拿到一条"活着"的连接。旧写法「chatSseController 非空就跳过」
      // 有一个致命的时序洞：Guardian 暂停事件到达与 SSE 流关闭之间存在窗口，
      // 用户恰在窗口内点批准时 controller 仍非空 → 不重订，而后端此刻已经
      // 结束上一轮生成、续跑事件无人接收 → 界面永远"思考中"（死锁）。
      // subscribeChatSSE 内部会先 abort 旧 controller 再建新连接，并用
      // Last-Event-ID 续传，因此重复投喂由该机制兜住，这里可以放心重订。
      // 参数顺序：sessionId, msg, retryCount, turnId, skipStartGate。
      // 此前把 turnId 传进了 retryCount、把 0 传进了 turnId → 订阅 URL
      // 丢掉 turn_id，后端按 session 全量回放（含上一回合事件），
      // 且失败重试分支（retryCount < 2）永远不成立。
      subscribeChatSSE(targetSid, msg, 0, msg.agentMeta?.turnId, true).catch(() => {})
    }

    if (convSid) {
      subscribeOnce(convSid)
    }

    if (!data) return

    if (data.status === 'rejected') {
      rt.isStreaming.value = false
      rt.streamingMsg.value = null
      rt.notifyImmediateComplete()
      return
    }

    if (data.status === 'awaiting_clarification') {
      const newMsg: ChatMessage = {
        role: 'assistant',
        timestamp: Date.now(),
        content: data.clarification_prompt || data.message || '需要进一步确认创作偏好',
        agentMeta: {
          sessionId: null,
          workflowStatus: 'awaiting_clarification',
          loopStatePath: data.loop_state_path,
          clarificationPrompt: data.clarification_prompt,
          clarificationSkill: data.clarification_skill,
          clarificationBatches: data.clarification_batches,
        },
      }
      conv.messages.push(newMsg)
      rt.streamingMsg.value = newMsg
      return
    }

    if (data.status === 'awaiting_confirmation') {
      if (msg.agentMeta) {
        msg.agentMeta.workflowStatus = 'awaiting_confirmation'
        msg.agentMeta.loopStatePath = data.loop_state_path
        msg.agentMeta.confirmationPrompt = data.confirmation_prompt
        msg.agentMeta.confirmationSkill = data.confirmation_skill
      }
      // 2026-11-02：prompt 原文只进 agentMeta（确认卡展示），不进正文，
      // 防止残留到确认后的最终回复里。awaiting 态正文不渲染。
      msg.content = ''
      rt.streamingMsg.value = msg
      return
    }

    if (data.status === 'completed') {
      // 后端续跑完成：先尝试通过 SSE 接收流式输出
      // SSE 会推送 agent_message_delta → 播放器渲染
      // 如果 SSE 无内容到达，用 POST 响应的 message 兜底
      const completedSid = convSid
      if (completedSid) {
        subscribeOnce(completedSid)
        // 兜底：如果 3s 后仍无 SSE 内容，用 POST 响应填充
        const fallbackMsg = data.message || '操作已完成。'
        setTimeout(() => {
          if (!msg.content || msg.content.length < 5) {
            msg.content = fallbackMsg
          }
          if (msg.agentMeta) msg.agentMeta.workflowStatus = 'completed'
        }, 3000)
      } else {
        // 无 sessionId：直接用 POST 响应
        if (data.message) {
          msg.content = data.message
        }
        if (msg.agentMeta) msg.agentMeta.workflowStatus = 'completed'
        rt.isStreaming.value = false
        rt.streamingMsg.value = null
        rt.notifyImmediateComplete()
      }
      return
    }

    // 其他 status：保持流式等 SSE
    const otherSid = convSid
    if (otherSid) {
      subscribeOnce(otherSid)
    }
  }

  function onConfirmationExpired(msg: ChatMessage) {
    if (msg.agentMeta) {
      msg.agentMeta.workflowStatus = 'confirmation_expired'
    }
  }

  async function confirmAction(action: "approve" | "reject", meta: any) {
    const resp = await authFetch("/api/v1/chat/confirm", {
      method: "POST",
      body: JSON.stringify({
        session_id: meta.sessionId,
        loop_state_path: meta.loopStatePath,
        action: action,
        feedback: "",
        turn_id: meta.turnId ?? null,
      }),
    })
    return await resp.json()
  }

  function getLastUserMessage(): string {
    const conv = conversations.value.find(c => c.id === activeConvId.value)
    if (!conv) return ''
    for (let i = conv.messages.length - 1; i >= 0; i--) {
      if (conv.messages[i].role === 'user') return conv.messages[i].content
    }
    return ''
  }

  /** 取最后一条用户消息的完整对象（重试要连 attachments 一起还原） */
  function getLastUserMessageEntry(): ChatMessage | null {
    const conv = conversations.value.find(c => c.id === activeConvId.value)
    if (!conv) return null
    for (let i = conv.messages.length - 1; i >= 0; i--) {
      if (conv.messages[i].role === 'user') return conv.messages[i]
    }
    return null
  }

  function removeLastAssistantMessage() {
    const conv = conversations.value.find(c => c.id === activeConvId.value)
    if (!conv) return
    for (let i = conv.messages.length - 1; i >= 0; i--) {
      if (conv.messages[i].role === 'assistant') {
        conv.messages.splice(i, 1)
        break
      }
    }
  }

  function removeLastUserMessage() {
    const conv = conversations.value.find(c => c.id === activeConvId.value)
    if (!conv) return
    for (let i = conv.messages.length - 1; i >= 0; i--) {
      if (conv.messages[i].role === 'user') {
        conv.messages.splice(i, 1)
        break
      }
    }
  }

  function stopStreaming() {
    // 只停「当前视图会话」的运行时：后台会话的任务继续跑（多任务并行）
    const conv = conversations.value.find(c => c.id === activeConvId.value)
    const sid = conv ? _rtOf(conv).sid : activeSessionId.value
    const rt = getSessionRuntime(sid)
    rt.abort()
    if (rt.chatSseController.value) {
      rt.chatSseController.value.abort()
      rt.chatSseController.value = null
    }
    if (rt.workflowSseController.value) {
      rt.workflowSseController.value.abort()
      rt.workflowSseController.value = null
    }
    // 2026-11-05：改走 forceInterrupt——暂停态下 _finishStream 会被 pause-guard
    // 拦下（isStreaming 恒 true），停止按钮就废了。逃生通道必须永远可用。
    rt.forceInterrupt()
  }

  async function retryLastMessage() {
    const conv = conversations.value.find(c => c.id === activeConvId.value)
    const curRt = conv ? _rtOf(conv).rt : getSessionRuntime(activeSessionId.value)
    if (curRt.isStreaming.value) {
      stopStreaming()
      await new Promise(r => setTimeout(r, 400))
    }
    removeLastAssistantMessage()
    const lastUser = getLastUserMessageEntry()
    if (lastUser) {
      // 正文末尾那行 `[本次配图素材] asset_ids=…` / `[选题参考] {json}` 是给 LLM 的指令，
      // 不能直接回灌进输入框让用户看到；图片改由 restorePendingImages 还原，
      // 选题参考改由 restoreTopicReference 还原回参考条（重发时重新注入）。
      const topicRef = extractTopicReference(lastUser.content)
      if (topicRef) restoreTopicReference(topicRef)
      inputText.value = stripSendHints(lastUser.content)
      restorePendingImages(lastUser.attachments || [])
      removeLastUserMessage()
      nextTick(() => sendMessageRef.value())
    }
  }

  const dangerDialog = reactive({
    visible: false,
    title: '',
    message: '',
    resolve: null as null | ((value: boolean) => void),
  })

  function showDangerConfirm(title: string, message: string): Promise<boolean> {
    dangerDialog.title = title
    dangerDialog.message = message
    dangerDialog.visible = true
    return new Promise<boolean>((resolve) => {
      dangerDialog.resolve = resolve
    })
  }

  function onDangerConfirm() {
    dangerDialog.resolve?.(true)
    dangerDialog.resolve = null
    dangerDialog.visible = false
  }

  function onDangerCancel() {
    dangerDialog.resolve?.(false)
    dangerDialog.resolve = null
    dangerDialog.visible = false
  }

  function copyMessage(content: string) {
    navigator.clipboard.writeText(content)
  }

  function onChatClarified(msg: ChatMessage, answers: Record<string, any>, data?: any) {
    if (msg.agentMeta) {
      msg.agentMeta.workflowStatus = 'running'
    }
    const conv = conversations.value.find((c) => c.messages.includes(msg))
    if (!conv) return
    // 同 onChatConfirmed：所有状态改写都落在本消息所属会话的运行时
    const { sid: convSid, rt } = _rtOf(conv)

    console.log('[onChatClarified] ENTER', {
      dataStatus: data?.status,
      chatSseController: !!rt.chatSseController.value,
      turnId: msg.agentMeta?.turnId,
      sessionId: convSid,
    })

    // 把同会话中所有旧的 awaiting_clarification 消息标记为 completed，
    // 防止刷新页面时旧澄清卡片重新出现
    for (const m of conv.messages) {
      if (m !== msg && m.role === 'assistant' && m.agentMeta?.workflowStatus === 'awaiting_clarification') {
        m.agentMeta.workflowStatus = 'completed'
      }
    }

    rt.resumeFromPause()
    rt.isStreaming.value = true
    rt.streamingMsg.value = msg
    rt.streamingHasContent.value = false
    rt.streamingThinking.value = false

    // Fix Issue 1: 清除旧的工具执行文字，显示"继续创作中"状态
    msg.content = ''

    if (!data) {
      // 网络错误或解析失败，显示加载状态
      msg.content = '正在恢复创作…'
      // 旧 SSE 仍活跃时不重建订阅（避免竞态，见 resumed 分支注释）；
      // 旧连接已断时用 skipStartGate=true 重订，跳过启动闸门
      if (!rt.chatSseController.value) {
        if (convSid) {
          subscribeChatSSE(convSid, msg, 0, msg.agentMeta?.turnId, true).catch(() => {})
        }
      }
      return
    }

    if (data.status === 'awaiting_clarification') {
      rt.isStreaming.value = false
      const newMsg: ChatMessage = {
        role: 'assistant',
        timestamp: Date.now(),
        content: data.clarification_prompt || data.message || '需要进一步确认创作偏好',
        agentMeta: {
          sessionId: null,
          workflowStatus: 'awaiting_clarification',
          loopStatePath: data.loop_state_path,
          clarificationPrompt: data.clarification_prompt,
          clarificationSkill: data.clarification_skill,
          clarificationBatches: data.clarification_batches,
        },
      }
      conv.messages.push(newMsg)
      rt.streamingMsg.value = newMsg
      return
    }

    if (data.status === 'awaiting_confirmation') {
      rt.isStreaming.value = false
      const newMsg: ChatMessage = {
        role: 'assistant',
        content: data.confirmation_prompt || data.message || '需要进一步确认操作',
        agentMeta: {
          sessionId: null,
          workflowStatus: 'awaiting_confirmation',
          loopStatePath: data.loop_state_path,
          confirmationPrompt: data.confirmation_prompt,
          confirmationSkill: data.confirmation_skill,
        },
      }
      conv.messages.push(newMsg)
      rt.streamingMsg.value = newMsg
      return
    }

    if (data.status === 'completed') {
      msg.content = data.message || '操作已完成。'
      if (msg.agentMeta) {
        msg.agentMeta.workflowStatus = 'completed'
      }
      rt.isStreaming.value = false
      rt.streamingHasContent.value = true
      rt.notifyImmediateComplete()
      return
    }

    // resumed: 后台续跑已启动（/clarify 改为 asyncio.create_task），
    // 订阅 SSE 接收实时进度事件
    if (data.status === 'resumed') {
      msg.content = data.message || '创作偏好已确认，正在继续执行…'
      console.log('[onChatClarified] resumed branch', {
        chatSseController: !!rt.chatSseController.value,
        willCreateNewSSE: !rt.chatSseController.value,
        isStreaming: rt.isStreaming.value,
        streamingMsg: !!rt.streamingMsg.value,
        workflowStatus: msg.agentMeta?.workflowStatus,
      })
      if (!rt.chatSseController.value) {
        if (convSid) {
          console.log('[onChatClarified] creating new SSE subscription, skipStartGate=true')
          subscribeChatSSE(convSid, msg, 0, msg.agentMeta?.turnId, true).catch(() => {})
        }
      } else {
        console.log('[onChatClarified] reusing existing SSE connection, events should arrive on old connection')
      }
      return
    }

    // 其他未知状态：尝试订阅 SSE
    msg.content = '正在继续创作…'
    if (!rt.chatSseController.value) {
      if (convSid) {
        subscribeChatSSE(convSid, msg, 0, msg.agentMeta?.turnId, true).catch(() => {})
      }
    }
  }

  return {
    onChatConfirmed,
    onConfirmationExpired,
    onChatClarified,
    confirmAction,
    getLastUserMessage,
    removeLastAssistantMessage,
    removeLastUserMessage,
    stopStreaming,
    retryLastMessage,
    dangerDialog,
    showDangerConfirm,
    onDangerConfirm,
    onDangerCancel,
    copyMessage,
  }
}