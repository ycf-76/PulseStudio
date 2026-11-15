import type { Ref } from 'vue'
import type { Conversation, ChatMessage, ChatStats, TurnPhase } from '../cell-types'
import { _ingestContentDelta } from './useChatSSE'
import { parseHttpErrorMessage } from '../cell-types'
import * as chatSessionsApi from '@/api/chatSessions'
import { tryRefreshToken } from '@/api/client'
import { useChatContextStore } from '@/stores/chatContext'
import { getSessionRuntime } from './chatSessionRuntime'

export interface ChatSendDeps {
  activeWork: Ref<any>
  workStore: any
  currentModel: Ref<string>
  thinkingDepth: Ref<string>
  scrollToBottom: () => Promise<void>
  transitionTurn: (phase: string, label?: string) => void
  /** 会话级回合转场：后台会话的收尾只改它自己的运行时 */
  transitionTurnScoped?: (phase: TurnPhase, label: string, sessionId: string | null, msg?: ChatMessage | null) => void
  lastStats: Ref<ChatStats | null>
  // 渲染层 L3：chat 直连路径的 delta 也灌进同一个播放引擎（与 agent SSE 路径速率一致）
  pushStreamDelta: (msg: ChatMessage, delta: string) => void
  pushReasoningDelta: (msg: ChatMessage, delta: string) => void
  finishStream: (msg: ChatMessage, onDone?: () => void) => void
}

export function useChatSend(deps: ChatSendDeps) {
  const {
    activeWork, workStore,
    currentModel, thinkingDepth,
    scrollToBottom, transitionTurn, transitionTurnScoped, lastStats,
    pushStreamDelta, pushReasoningDelta, finishStream,
  } = deps

  async function sendMessageReal(
    conv: Conversation,
    startTime: number,
    sid: string | null,
    assistantMsg: ChatMessage,
  ) {
    // 本回合的运行时锁定（同 useChatAgent）：用户中途切会话时，
    // 收尾/中止仍落回这条消息自己的会话，不串到别人身上。
    const rt = getSessionRuntime(sid)
    let assistantContent = ''
    let thinkingContent = ''

    const token = localStorage.getItem('token')
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
    }
    if (token) {
      headers['Authorization'] = `Bearer ${token}`
    }
    const messagesPayload = conv.messages
      .filter(m => m !== assistantMsg && m.content)
      .map(m => ({ role: m.role, content: m.content }))

    const ctxStore = useChatContextStore()
    ctxStore.bindConversation(conv.id || null)
    const contextSystemPrompt = ctxStore.buildSystemPrompt()
    if (contextSystemPrompt) {
      messagesPayload.unshift({ role: 'system', content: contextSystemPrompt })
      console.log('[sendMessageReal] contextTrack INJECTED, pinnedItems:', ctxStore.pinnedItems.length, 'promptLen:', contextSystemPrompt.length)
    } else {
      console.log('[sendMessageReal] no pinned context, skip injection')
    }
    const bodyPayload = JSON.stringify({
      messages: messagesPayload,
      model: currentModel.value || undefined,
      stream: true,
      thinking_depth: thinkingDepth.value,
    })

    let response = await fetch('/api/v1/chat/completions', {
      method: 'POST',
      headers,
      body: bodyPayload,
      signal: rt.getAbortController()?.signal,
    })

    if (response.status === 401 && token) {
      const newToken = await tryRefreshToken()
      if (newToken) {
        headers['Authorization'] = `Bearer ${newToken}`
        response = await fetch('/api/v1/chat/completions', {
          method: 'POST',
          headers,
          body: bodyPayload,
          signal: rt.getAbortController()?.signal,
        })
      }
    }

    if (!response.ok) {
      // 与 chat/agent 一致：错误体先解析成可读文案，避免裸 JSON 透出到回复文本
      const errText = await response.text()
      throw new Error(parseHttpErrorMessage(response.status, errText))
    }

    const reader = response.body?.getReader()
    const decoder = new TextDecoder()

    if (reader) {
      let currentEvent = ''
      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        const chunk = decoder.decode(value, { stream: true })
        const lines = chunk.split('\n')

        for (const line of lines) {
          if (line.startsWith('event: ')) {
            currentEvent = line.slice(7).trim()
            continue
          }
          if (!line.startsWith('data: ')) {
            continue
          }
          const data = line.slice(6).trim()
          if (data === '[DONE]') {
            currentEvent = ''
            continue
          }

          try {
            currentEvent = ''

            const parsed = JSON.parse(data)
            const delta = parsed.choices?.[0]?.delta
            const finishReason = parsed.choices?.[0]?.finish_reason
            if (!delta) continue

            if (delta.reasoning_content) {
              thinkingContent += delta.reasoning_content
              rt.streamingThinking.value = true
              if (!assistantMsg.reasoning) assistantMsg.reasoning = ''
              // reasoning 独立播放器匀速流出（thinkCps=90）——旧版裸 += 不过节流
              pushReasoningDelta(assistantMsg, delta.reasoning_content)
              await scrollToBottom()
            }

            if (delta.content) {
              const clean = _ingestContentDelta(assistantMsg, delta.content)
              if (clean) {
                assistantContent += clean
                rt.streamingHasContent.value = true
                // 正文灌入播放引擎（与 agent SSE 路径同一速率模型）——
                // msg.content 由播放器按帧写入，这里不再直接赋值
                pushStreamDelta(assistantMsg, clean)
              }
              assistantMsg.thinking = thinkingContent || assistantMsg.thinking || undefined
              rt.streamingThinking.value = !!(assistantMsg as any)._thinkState?.inThink || !!thinkingContent
            }

            if (finishReason === 'stop') {
              rt.streamingThinking.value = false
            }
          } catch {
          }
        }
      }
    }

    const hasReasoning = !!(assistantMsg.reasoning && assistantMsg.reasoning.trim())
    if (!assistantContent && !thinkingContent && !hasReasoning) {
      assistantMsg.content = '（无回复内容）'
    } else {
      if (!thinkingContent && !hasReasoning) {
        assistantMsg.reasoning = undefined
      }
    }

    // 数据态立即落定（不等播放）：状态 / 意图 / 落库 / 耗时
    assistantMsg.agentMeta!.workflowStatus = 'completed'
    if (!assistantMsg.agentMeta!.intent) {
      assistantMsg.agentMeta!.intent = { action: 'chat', params: {}, confidence: 0.5 } as any
    }
    // 落库用原文（完整累积），不等播放器渲染完——否则拿到的是半段 content。
    // _persisted 守卫：与 SSE workflow_completed 路径共用，保证每条消息只落一次
    if (sid && !assistantMsg._persisted) {
      assistantMsg._persisted = true
      chatSessionsApi.addMessage(sid, 'assistant', assistantContent || assistantMsg.content, assistantMsg.agentMeta).catch(() => {})
    }
    rt.notifySseComplete()
    if (rt.chatSseController.value) {
      rt.chatSseController.value.abort()
    }

    const latency = Date.now() - startTime
    if (assistantMsg.agentMeta) {
      assistantMsg.agentMeta.durationMs = latency
    }
    const userTurns = conv.messages.filter(m => m.role === 'user').length
    lastStats.value = {
      turns: userTurns,
      tokens: assistantContent.length,
      latency,
    }
    // 渲染态收尾：等播放器匀速落地后再转 done（状态不先于文字结束）
    // 转场钉在本会话上，切走后到达的 done 只改它自己的状态
    const _transDone = () => {
      if (transitionTurnScoped) transitionTurnScoped('done', '', sid, assistantMsg)
      else transitionTurn('done', '')
    }
    if (assistantContent) {
      finishStream(assistantMsg, () => { _transDone() })
    } else {
      _transDone()
    }
  }

  return { sendMessageReal }
}