import { inject, provide, type Ref, type ComputedRef } from 'vue'
import type { ChatMessage, StatusIndicator, TurnPhase } from './cell-types'

export interface ChatRenderContext {
  isChatMode: ComputedRef<boolean>
  isCodexMode: ComputedRef<boolean>
  isStreaming: Ref<boolean>
  streamingMsg: Ref<ChatMessage | null>
  streamingThinking: Ref<boolean>
  streamingHasContent: Ref<boolean>
  streamingClock: Ref<number>
  currentStatusIndicator: Ref<StatusIndicator>
  isExecExpanded: (msgIdx: number, tcIdx: number) => boolean
  toggleExec: (msgIdx: number, tcIdx: number) => void
  isDiffExpanded: (idx: number) => boolean
  toggleDiff: (idx: number) => void
  isThinkingExpanded: (idx: number) => boolean
  toggleThinking: (idx: number) => void
  expandedAgentId: Ref<string | null>
  toggleAgentExpand: (agentId: string) => void
  copyMessage: (content: string) => void
  retryLastMessage: () => void
  onChatConfirmed: (msg: ChatMessage, action: 'approve' | 'reject', data?: any) => void
  onConfirmationExpired: (msg: ChatMessage) => void
  onChatClarified?: (msg: ChatMessage, answers: Record<string, any>, data?: any) => void
  sendMessage: () => Promise<void>
  /** 把一段文本填进输入框并发送。用于"让 AI 重编分镜"这类需要带上下文回灌的场景 */
  sendMessageWithText?: (text: string) => Promise<void>
  /** 停止当前正在进行的流式任务（abort SSE + 复位流状态）。用户消息"修改"前调用 */
  stopActiveStream?: () => void
  openInBrowser: (url: string) => void
  /** 在右侧 ContextSidebar 打开一个作品（linkWork）。卡片组「侧边栏预览」入口 */
  openCardSidebar?: (workId: string) => void
}

const CHAT_RENDER_KEY = Symbol('chat-render-context')

export function provideChatRenderContext(ctx: ChatRenderContext) {
  provide(CHAT_RENDER_KEY, ctx)
}

export function useChatRenderContext(): ChatRenderContext {
  const ctx = inject<ChatRenderContext>(CHAT_RENDER_KEY)
  if (!ctx) throw new Error('useChatRenderContext must be used inside ChatView')
  return ctx
}