import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { ref } from 'vue'
import { useChatInteractions } from './useChatInteractions'
import { getSessionRuntime } from './chatSessionRuntime'
import type { ChatMessage, Conversation } from '../cell-types'

/**
 * P2#5：resume 订阅显式传 turnId。
 *
 * 背景：确认/澄清卡片的「续跑」会重新订阅 SSE。此前 5 处订阅都不带 turnId，
 * 只能靠 subscribeChatSSE 内部的「动态启用兜底」放行事件——一旦首轮事件里
 * 混进别回合内容，续跑窗口就是敞开的。现在首轮 turnId 存进 agentMeta
 * （随 JSONB 入库，刷新后仍可找回），这 5 处一律显式传回。
 */

const SESSION_ID = 'sess-resume-1'
const TURN_ID = 'turn_resume_abc123'

function makeMsg(withTurnId: boolean): ChatMessage {
  return {
    role: 'assistant',
    content: '等待确认',
    agentMeta: {
      sessionId: null,
      workflowStatus: 'awaiting_confirmation',
      loopStatePath: '/tmp/loop_state.json',
      turnId: withTurnId ? TURN_ID : undefined,
    },
  } as ChatMessage
}

function setup(msg: ChatMessage) {
  const conv: Conversation = {
    id: 'conv-1',
    title: '测试会话',
    messages: [{ role: 'user', content: '帮我做一期内容' }, msg],
    createdAt: Date.now(),
  }
  const conversations = ref<Conversation[]>([conv])
  const activeSessionId = ref<string | null>(SESSION_ID)
  const subscribeChatSSE = vi.fn(async () => {})

  const deps: any = {
    conversations,
    activeConvId: ref('conv-1'),
    inputText: ref(''),
    activeSessionId,
    // 交互按「消息所属会话」解析运行时：conv-1 → SESSION_ID
    getSessionId: (convId: string) => (convId === 'conv-1' ? SESSION_ID : null),
    subscribeChatSSE,
    sendMessageRef: { value: async () => {} },
  }

  const api = useChatInteractions(deps)
  return { ...api, deps, subscribeChatSSE, msg, conv }
}

describe('useChatInteractions · resume 订阅显式传 turnId', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
    // 运行时是模块级注册表，用例之间必须清干净，否则残留的 isStreaming/controller 会串场
    getSessionRuntime(SESSION_ID).dispose()
  })

  it('onChatConfirmed 续跑分支：带上 agentMeta.turnId', () => {
    const ctx = setup(makeMsg(true))
    ctx.onChatConfirmed(ctx.msg, 'approve', { status: 'resumed' })

    // 只允许一条长连接：resumed 会同时命中「前置订阅」与尾部「其他状态」分支，
    // 不去重就是两条连接各收一遍 live 事件 → 正文重复
    expect(ctx.subscribeChatSSE).toHaveBeenCalledTimes(1)
    // 参数顺序必须是 (sessionId, msg, retryCount, turnId, skipStartGate)。
    // 历史 bug：写成 (sid, msg, turnId, 0, true) → turnId 被当成 retryCount，
    // turnId 实参为 0 → 订阅 URL 丢掉 turn_id，且重试分支永不触发。
    expect(ctx.subscribeChatSSE).toHaveBeenCalledWith(SESSION_ID, ctx.msg, 0, TURN_ID, true)
  })

  it('onChatConfirmed 完成分支：带上 agentMeta.turnId（含 3s 兜底）', () => {
    const ctx = setup(makeMsg(true))
    ctx.onChatConfirmed(ctx.msg, 'approve', { status: 'completed', message: '已完成' })

    expect(ctx.subscribeChatSSE).toHaveBeenCalledTimes(1)
    expect(ctx.subscribeChatSSE).toHaveBeenCalledWith(SESSION_ID, ctx.msg, 0, TURN_ID, true)
    // 兜底定时器：SSE 迟迟无内容时用 POST 响应填充
    vi.advanceTimersByTime(3000)
    expect(ctx.msg.agentMeta!.workflowStatus).toBe('completed')
  })

  it('onChatConfirmed 其他状态分支：带上 agentMeta.turnId 且只订阅一次', () => {
    const ctx = setup(makeMsg(true))
    ctx.onChatConfirmed(ctx.msg, 'approve', { status: 'suspended' })

    expect(ctx.subscribeChatSSE).toHaveBeenCalledTimes(1)
    expect(ctx.subscribeChatSSE).toHaveBeenCalledWith(SESSION_ID, ctx.msg, 0, TURN_ID, true)
  })

  it('onChatClarified 无响应分支：带上 agentMeta.turnId', () => {
    const ctx = setup(makeMsg(true))
    ctx.onChatClarified(ctx.msg, {}, undefined)

    expect(ctx.subscribeChatSSE).toHaveBeenCalledWith(SESSION_ID, ctx.msg, 0, TURN_ID, true)
  })

  it('onChatClarified 未知状态分支：带上 agentMeta.turnId', () => {
    const ctx = setup(makeMsg(true))
    ctx.onChatClarified(ctx.msg, {}, { status: 'running_after_resume' })

    expect(ctx.subscribeChatSSE).toHaveBeenCalledWith(SESSION_ID, ctx.msg, 0, TURN_ID, true)
  })

  it('旧消息没有 turnId 时传 undefined（保持动态启用兜底，行为不回退）', () => {
    const ctx = setup(makeMsg(false))
    ctx.onChatConfirmed(ctx.msg, 'approve', { status: 'resumed' })

    expect(ctx.subscribeChatSSE).toHaveBeenCalledWith(SESSION_ID, ctx.msg, 0, undefined, true)
  })

  it('旧 SSE controller 仍非空时也要重订阅（暂停→批准窗口内不能漏事件）', () => {
    const ctx = setup(makeMsg(true))
    // 运行时是模块级注册表：这里直接给「该会话的运行时」挂一个旧 controller
    getSessionRuntime(SESSION_ID).chatSseController.value = new AbortController()
    ctx.onChatConfirmed(ctx.msg, 'approve', { status: 'resumed' })

    // subscribeChatSSE 内部会先 abort 旧 controller 再用 Last-Event-ID 续传，
    // 因此这里必须无条件重订，否则窗口期点批准会导致续跑事件无人接收（死锁）。
    expect(ctx.subscribeChatSSE).toHaveBeenCalledTimes(1)
  })

  it('confirmAction 请求体带回 turn_id', async () => {
    const ctx = setup(makeMsg(true))
    const fetchMock = vi.fn(async (_url?: any, _opts?: any) => ({
      ok: true,
      json: async () => ({ status: 'resumed' }),
    }))
    vi.stubGlobal('fetch', fetchMock)

    await ctx.confirmAction('approve', {
      sessionId: SESSION_ID,
      loopStatePath: '/tmp/loop_state.json',
      turnId: TURN_ID,
    })

    const body = JSON.parse((fetchMock.mock.calls[0][1] as any).body)
    expect(body.turn_id).toBe(TURN_ID)
    vi.unstubAllGlobals()
  })
})