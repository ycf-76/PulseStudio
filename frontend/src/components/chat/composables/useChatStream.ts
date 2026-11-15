import { computed, onUnmounted, type Ref } from 'vue'
import { abortAllRuntimes, getSessionRuntime, type SessionRuntime } from './chatSessionRuntime'

/**
 * 流式状态投影层（2026-11-06 多任务并行改造）。
 *
 * 状态本体不再由本模块持有，而是按会话存放在 chatSessionRuntime 注册表里。
 * 这里返回的每个 ref 都是「当前视图会话」的可写投影：
 * - 视图层读写 isStreaming / streamingMsg 时，自动落到当前会话自己的运行时；
 * - 后台会话的流式状态互不干扰，A 在跑不会挡住 B 发消息；
 * - 切换会话只换投影目标，不中断任何在途任务。
 *
 * getSid 返回当前视图的 sessionId（null = 草稿态，落到草稿占位运行时）。
 */
export function useChatStream(getSid: () => string | null | undefined = () => null) {
  const rt = (): SessionRuntime => getSessionRuntime(getSid())

  function project<T>(pick: (r: SessionRuntime) => Ref<T>) {
    return computed<T>({
      get: () => pick(rt()).value,
      set: (v: T) => { pick(rt()).value = v },
    })
  }

  const isStreaming = project(r => r.isStreaming)
  const streamingHasContent = project(r => r.streamingHasContent)
  const streamingThinking = project(r => r.streamingThinking)
  const streamingClock = project(r => r.streamingClock)
  const streamingMsg = project(r => r.streamingMsg)
  const turnPhase = project(r => r.turnPhase)
  const statusIndicator = project(r => r.statusIndicator)

  const startClock = () => rt().startClock()
  const stopClock = () => rt().stopClock()
  const createAbortController = () => rt().createAbortController()
  const getAbortController = () => rt().getAbortController()
  const clearAbortController = () => rt().clearAbortController()
  const abort = () => rt().abort()
  const resetStreamState = () => rt().resetStreamState()
  const beginStream = (onComplete: (r: SessionRuntime) => void, idleMs?: number) => rt().beginStream(onComplete, idleMs)
  const touchStream = () => rt().touchStream()
  const setIdleTimeout = (idleMs: number) => rt().setIdleTimeout(idleMs)
  const setBeforeFinish = (fn: (() => void) | null) => rt().setBeforeFinish(fn)
  const notifySseComplete = () => rt().notifySseComplete()
  const notifyAgentLoopDone = (graceMs?: number) => rt().notifyAgentLoopDone(graceMs)
  const notifyImmediateComplete = () => rt().notifyImmediateComplete()
  const forceInterrupt = () => rt().forceInterrupt()
  const notifyPaused = () => rt().notifyPaused()
  const resumeFromPause = () => rt().resumeFromPause()

  // 页面卸载：并行中的每个会话都要收干净（不再只 abort 当前这一个）
  onUnmounted(() => abortAllRuntimes())

  return {
    isStreaming,
    streamingHasContent,
    streamingThinking,
    streamingClock,
    streamingMsg,
    turnPhase,
    statusIndicator,
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
    setBeforeFinish,
    notifySseComplete,
    notifyAgentLoopDone,
    notifyImmediateComplete,
    forceInterrupt,
    notifyPaused,
    resumeFromPause,
  }
}