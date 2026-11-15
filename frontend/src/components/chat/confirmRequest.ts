/**
 * pendingConfirm — 确认/澄清请求的会话级状态（输入框上方 dock 渲染）。
 * 四类确认统一走此通道：guardian / confirm / clarify / storyboard。
 * 消息 cell 检测到对应 awaiting 状态时 requestConfirmation()（携带 props
 * 与 onAction 回调）；ChatView dock 渲染对应卡片，用户决策后回写消息状态。
 *
 * 多任务并行改造（2026-11-06）：
 * 卡片本体按 sessionId 各存一份，pendingConfirm.req 只是「当前视图会话」的投影。
 * 于是 A 会话等确认、B 会话也在等确认时，两张卡不会互相顶掉；
 * 切回 A 时它的卡片自动回到 dock，切到 B 时 dock 换成 B 的。
 */
import { reactive } from 'vue'
import type { ChatMessage } from './cell-types'

export type PendingKind = 'guardian' | 'confirm' | 'clarify' | 'storyboard' | 'cutplan'

export interface PendingConfirm {
  kind: PendingKind
  sessionId: string
  loopStatePath: string
  turnId?: string
  msg: ChatMessage
  /** 各卡片的原生 props（dock 渲染透传） */
  cardProps: Record<string, any>
  /** 决策回调：cell 注入，内含消息状态回写与业务处理 */
  onAction: (action: string, data?: any) => void
  /** 各卡片专有的透传 props（toolName/confirmationPrompt/batches 等） */
  [key: string]: any
}

export const pendingConfirm = reactive<{ req: PendingConfirm | null }>({ req: null })

/** 每个会话最多一张待决策卡（同会话二次暂停会覆盖旧的，符合语义） */
const _requests = new Map<string, PendingConfirm>()
let _viewSessionId: string | null = null

function _keyOf(sid: string | null | undefined): string {
  return String(sid || '')
}

function _syncView() {
  const next = _requests.get(_keyOf(_viewSessionId)) || null
  if (pendingConfirm.req !== next) pendingConfirm.req = next
}

/** 视图切到某个会话：dock 换成该会话自己的卡（没有就收起） */
export function setConfirmViewSession(sessionId: string | null | undefined) {
  _viewSessionId = sessionId || null
  _syncView()
}

export function requestConfirmation(req: PendingConfirm) {
  _requests.set(_keyOf(req.sessionId), req)
  // 视图未绑定（草稿态）或正是该会话时，立即上屏
  if (!_viewSessionId || _viewSessionId === _keyOf(req.sessionId)) {
    pendingConfirm.req = req
  }
}

/** 只清指定会话的卡（后台会话卡片被自己的消息收尾时用） */
export function clearConfirmationForSession(sessionId: string | null | undefined) {
  const key = _keyOf(sessionId)
  const existing = _requests.get(key)
  if (!existing) return
  _requests.delete(key)
  if (pendingConfirm.req === existing) pendingConfirm.req = null
}

export function clearConfirmation(expected?: PendingConfirm | null) {
  // 身份校验式清空：只清"自己那一条"。
  // 场景：用户点批准 → onAction 里续跑可能同步触发二次暂停并注册新卡片，
  // 无条件清空会把刚注册的新卡片抹掉 → 服务端在等批准、界面却没有入口（死锁）。
  if (expected) {
    const key = _keyOf(expected.sessionId)
    if (_requests.get(key) !== expected) return
    _requests.delete(key)
    if (pendingConfirm.req === expected) pendingConfirm.req = null
    return
  }
  _requests.clear()
  pendingConfirm.req = null
}