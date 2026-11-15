import { ref, type Ref } from 'vue'
import type { ChatMessage, TurnPhase, StatusIndicator } from '../cell-types'
import { finalizeTurn } from '../thoughtStream'

/**
 * 会话运行时（Session Runtime）注册表 —— 多任务并行的地基。
 *
 * 旧实现把「流式状态 / SSE 控制器 / 消息代 epoch」全部放在 ChatView 的单例里，
 * 于是所有会话共享同一个 isStreaming：A 会话在跑，B 会话就发不出消息；
 * 切会话时 abort 全局控制器，A 的任务当场被杀。
 *
 * 现在这些状态按 sessionId 各自持有一份（含草稿态占位 key），
 * ChatView 只负责把「当前视图会话」的运行时投影给模板，后台会话继续跑自己的。
 */

export const DRAFT_RUNTIME_KEY = '__draft__'

const PAUSED_STATUSES = new Set([
  'awaiting_clarification',
  'awaiting_confirmation',
  'awaiting_guardian_confirmation',
  'awaiting_storyboard_confirmation',
  'awaiting_cut_plan_confirmation',
])

function _isPausedStatus(ws?: string | null): boolean {
  return PAUSED_STATUSES.has(String(ws || ''))
}

export interface SessionRuntime {
  key: string
  isStreaming: Ref<boolean>
  streamingHasContent: Ref<boolean>
  streamingThinking: Ref<boolean>
  streamingClock: Ref<number>
  streamingMsg: Ref<ChatMessage | null>
  turnPhase: Ref<TurnPhase>
  statusIndicator: Ref<StatusIndicator>
  chatSseController: Ref<AbortController | null>
  workflowSseController: Ref<AbortController | null>
  chatSseLastEventId: Ref<string>
  /** 消息代：本会话专属，新回合递增只作废本会话的旧订阅 */
  epoch: number
  markSseReady: () => void
  waitSseReady: () => Promise<void>
  startClock: () => void
  stopClock: () => void
  createAbortController: () => AbortController
  getAbortController: () => AbortController | null
  clearAbortController: () => void
  abort: () => void
  resetStreamState: () => void
  beginStream: (onComplete: (rt: SessionRuntime) => void, idleMs?: number) => void
  touchStream: () => void
  setIdleTimeout: (idleMs: number) => void
  setBeforeFinish: (fn: (() => void) | null) => void
  notifySseComplete: () => void
  notifyAgentLoopDone: (graceMs?: number) => void
  notifyImmediateComplete: () => void
  forceInterrupt: () => void
  notifyPaused: () => void
  resumeFromPause: () => void
  dispose: () => void
}

function createSessionRuntime(key: string): SessionRuntime {
  const isStreaming = ref(false)
  const streamingHasContent = ref(false)
  const streamingThinking = ref(false)
  const streamingClock = ref(0)
  const streamingMsg = ref<ChatMessage | null>(null)
  const turnPhase = ref<TurnPhase>('idle')
  const statusIndicator = ref<StatusIndicator>({ header: '' })

  const chatSseController = ref<AbortController | null>(null)
  const workflowSseController = ref<AbortController | null>(null)
  const chatSseLastEventId = ref<string>('')
  let _chatSseReadyResolvers: Array<() => void> = []

  let streamStartTs = 0
  let clockTimer: ReturnType<typeof setInterval> | null = null
  let abortController: AbortController | null = null

  let _onStreamComplete: ((rt: SessionRuntime) => void) | null = null
  let _beforeFinish: (() => void) | null = null
  let _safetyTimeout: ReturnType<typeof setTimeout> | null = null
  let _agentDoneTimer: ReturnType<typeof setTimeout> | null = null
  let _sseDone = false
  let _idleMs = 180_000

  function markSseReady() {
    const resolvers = _chatSseReadyResolvers
    _chatSseReadyResolvers = []
    for (const resolve of resolvers) resolve()
  }

  function waitSseReady(): Promise<void> {
    return new Promise((resolve) => {
      _chatSseReadyResolvers.push(resolve)
    })
  }

  function startClock() {
    streamStartTs = Date.now()
    streamingClock.value = 0
    stopClock()
    clockTimer = setInterval(() => {
      streamingClock.value = Math.floor((Date.now() - streamStartTs) / 1000)
    }, 500)
  }

  function stopClock() {
    if (clockTimer) {
      clearInterval(clockTimer)
      clockTimer = null
    }
  }

  function createAbortController(): AbortController {
    abortController = new AbortController()
    return abortController
  }

  function getAbortController(): AbortController | null {
    return abortController
  }

  function clearAbortController() {
    abortController = null
  }

  function abort() {
    if (abortController) {
      abortController.abort()
      abortController = null
    }
  }

  function resetStreamState() {
    if (_isPausedStatus(streamingMsg.value?.agentMeta?.workflowStatus)) {
      console.error('[ChatStream] BLOCKED resetStreamState during pause!', {
        key,
        workflowStatus: streamingMsg.value?.agentMeta?.workflowStatus,
      })
      return
    }
    isStreaming.value = false
    streamingHasContent.value = false
    streamingThinking.value = false
    streamingMsg.value = null
    abortController = null
    _onStreamComplete = null
    _sseDone = false
    turnPhase.value = 'idle'
    if (_safetyTimeout) {
      clearTimeout(_safetyTimeout)
      _safetyTimeout = null
    }
    if (_agentDoneTimer) {
      clearTimeout(_agentDoneTimer)
      _agentDoneTimer = null
    }
  }

  function _armIdleTimer(idleMs: number) {
    if (_safetyTimeout) {
      clearTimeout(_safetyTimeout)
      _safetyTimeout = null
    }
    if (idleMs <= 0) return
    _safetyTimeout = setTimeout(() => {
      if (isStreaming.value && !_sseDone) {
        console.warn(`[ChatStream] idle timeout: no activity for ${idleMs}ms, forcing reset`, { key })
        _finishStream()
      }
    }, idleMs)
  }

  function beginStream(onComplete: (rt: SessionRuntime) => void, idleMs = 300_000) {
    _onStreamComplete = onComplete
    _sseDone = false
    _idleMs = idleMs
    isStreaming.value = true
    _armIdleTimer(idleMs)
  }

  function touchStream() {
    if (!isStreaming.value || _sseDone) return
    _armIdleTimer(_idleMs)
  }

  function setIdleTimeout(idleMs: number) {
    _idleMs = idleMs
    if (isStreaming.value && !_sseDone) _armIdleTimer(idleMs)
  }

  function notifySseComplete() {
    _sseDone = true
    if (_safetyTimeout) {
      clearTimeout(_safetyTimeout)
      _safetyTimeout = null
    }
    if (_agentDoneTimer) {
      clearTimeout(_agentDoneTimer)
      _agentDoneTimer = null
    }
    _finishStream()
  }

  function notifyAgentLoopDone(graceMs = 10_000) {
    if (_sseDone || !isStreaming.value) {
      _finishStream()
      return
    }
    if (_agentDoneTimer) clearTimeout(_agentDoneTimer)
    _agentDoneTimer = setTimeout(() => {
      _agentDoneTimer = null
      if (isStreaming.value && !_sseDone) {
        console.warn('[ChatStream] agent loop done but SSE did not finish, finishing after grace period', { key })
        _finishStream()
      }
    }, graceMs)
  }

  function notifyImmediateComplete() {
    _sseDone = true
    if (_safetyTimeout) {
      clearTimeout(_safetyTimeout)
      _safetyTimeout = null
    }
    _finishStream()
  }

  function forceInterrupt() {
    const msg = streamingMsg.value
    const _ws = msg?.agentMeta?.workflowStatus
    if (_isPausedStatus(_ws) && msg?.agentMeta) {
      console.warn('[ChatStream] forceInterrupt: user stopped during pause, releasing UI lock', { key, workflowStatus: _ws })
      ;(msg.agentMeta as any).workflowStatus = 'suspended'
      ;(msg.agentMeta as any).interruptedByUser = true
    }
    _finishStream()
  }

  function notifyPaused() {
    if (_safetyTimeout) {
      clearTimeout(_safetyTimeout)
      _safetyTimeout = null
    }
    if (_agentDoneTimer) {
      clearTimeout(_agentDoneTimer)
      _agentDoneTimer = null
    }
  }

  function resumeFromPause() {
    _sseDone = false
    if (_agentDoneTimer) {
      clearTimeout(_agentDoneTimer)
      _agentDoneTimer = null
    }
    _armIdleTimer(_idleMs)
  }

  function _finishStream() {
    if (!isStreaming.value) return
    if (_isPausedStatus(streamingMsg.value?.agentMeta?.workflowStatus)) {
      console.error('[ChatStream] BLOCKED _finishStream during pause!', {
        key,
        workflowStatus: streamingMsg.value?.agentMeta?.workflowStatus,
      })
      return
    }
    if (_beforeFinish) {
      _beforeFinish()
    }
    // 兜底熄灯：只关本会话消息所属的思考流桶，绝不误杀别的会话
    finalizeTurn(streamingMsg.value?.turnId || null)
    isStreaming.value = false
    if (_onStreamComplete) {
      const cb = _onStreamComplete
      _onStreamComplete = null
      cb(rt)
    }
  }

  function dispose() {
    stopClock()
    abort()
    if (chatSseController.value) {
      chatSseController.value.abort()
      chatSseController.value = null
    }
    if (workflowSseController.value) {
      workflowSseController.value.abort()
      workflowSseController.value = null
    }
    if (_safetyTimeout) {
      clearTimeout(_safetyTimeout)
      _safetyTimeout = null
    }
    if (_agentDoneTimer) {
      clearTimeout(_agentDoneTimer)
      _agentDoneTimer = null
    }
    markSseReady()
  }

  const rt: SessionRuntime = {
    key,
    isStreaming,
    streamingHasContent,
    streamingThinking,
    streamingClock,
    streamingMsg,
    turnPhase,
    statusIndicator,
    chatSseController,
    workflowSseController,
    chatSseLastEventId,
    epoch: 0,
    markSseReady,
    waitSseReady,
    startClock,
    stopClock,
    createAbortController,
    getAbortController,
    clearAbortController,
    abort,
    resetStreamState,
    beginStream,
    touchStream,
    setIdleTimeout,
    setBeforeFinish: (fn: (() => void) | null) => { _beforeFinish = fn },
    notifySseComplete,
    notifyAgentLoopDone,
    notifyImmediateComplete,
    forceInterrupt,
    notifyPaused,
    resumeFromPause,
    dispose,
  }
  return rt
}

const runtimes = new Map<string, SessionRuntime>()

function _keyOf(sessionId?: string | null): string {
  return sessionId || DRAFT_RUNTIME_KEY
}

/** 取（必要时创建）某个会话的运行时。传 null 走草稿态占位运行时。 */
export function getSessionRuntime(sessionId?: string | null): SessionRuntime {
  const key = _keyOf(sessionId)
  let rt = runtimes.get(key)
  if (!rt) {
    rt = createSessionRuntime(key)
    runtimes.set(key, rt)
  }
  return rt
}

/** 只读探查：不存在则返回 undefined，不产生新运行时。 */
export function peekSessionRuntime(sessionId?: string | null): SessionRuntime | undefined {
  return runtimes.get(_keyOf(sessionId))
}

/**
 * 草稿态运行时「认领」为真实 sessionId。
 * 发送消息的时序是：先把 isStreaming / streamingMsg 写进草稿运行时（此时 session
 * 还没落库），随后 ensureSession 拿到真实 id——必须把整份运行时搬过去，
 * 否则这一回合的状态就留在了没人认领的草稿槽里。
 */
export function adoptSessionRuntime(sessionId?: string | null): void {
  if (!sessionId) return
  const draft = runtimes.get(DRAFT_RUNTIME_KEY)
  if (!draft) return
  const alive = draft.isStreaming.value || !!draft.streamingMsg.value || !!draft.getAbortController()
  runtimes.delete(DRAFT_RUNTIME_KEY)
  if (!alive) {
    draft.dispose()
    return
  }
  const existing = runtimes.get(sessionId)
  if (existing && (existing.isStreaming.value || !!existing.streamingMsg.value)) {
    // 目标会话本身还在跑（理论上不该发生）：保留目标，草稿态作废
    runtimes.set(DRAFT_RUNTIME_KEY, draft)
    return
  }
  if (existing) existing.dispose()
  draft.key = sessionId
  runtimes.set(sessionId, draft)
}

/** 终止某个会话的全部在途任务（删除会话 / 用户点停止用）。 */
export function abortSessionRuntime(sessionId?: string | null): void {
  const rt = peekSessionRuntime(sessionId)
  if (!rt) return
  rt.dispose()
  rt.isStreaming.value = false
  rt.streamingHasContent.value = false
  rt.streamingThinking.value = false
  rt.streamingMsg.value = null
  rt.turnPhase.value = 'idle'
}

/** 页面卸载：把并行中的每个会话都收干净。 */
export function abortAllRuntimes(): void {
  for (const rt of runtimes.values()) rt.dispose()
  runtimes.clear()
}

/** 从消息上反查所属会话（agentMeta.sessionId 由 SSE 订阅时盖章）。 */
export function sessionIdOfMessage(msg?: ChatMessage | null, fallback?: string | null): string | null {
  const meta = msg?.agentMeta as any
  return (meta?.sessionId as string) || (msg as any)?.sessionId || fallback || null
}