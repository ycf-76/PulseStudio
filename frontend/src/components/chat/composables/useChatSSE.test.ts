import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { ref } from 'vue'
import { mount } from '@vue/test-utils'
import { useChatSSE } from './useChatSSE'
import type { ChatSSECallbacks } from './useChatSSE'
import type { ChatMessage } from '../cell-types'
import VideoTaskCard from '../VideoTaskCard.vue'
import { getSessionRuntime } from './chatSessionRuntime'

/**
 * 回合沙箱闸（L2 TurnRuntime）的前端单测。
 *
 * 覆盖实现说明 P2 第 3 项：subscribeChatSSE 的沙箱闸逻辑——
 * - 显式 turnId 分支：URL 带参 + 本回合放行 / 异回合拦截计数
 * - 动态启用分支（旧路径无 turnId）：收到带 turn_id 的 chat_turn_started 后启用隔离
 * - newWorkflowStarted 门：启动事件前的旧 replay 一律忽略
 * - 消息代（epoch）过滤：advanceMsgEpoch 后旧订阅事件被丢弃
 * - 401 → 刷新 token → 重试
 */

// ── mock 外部依赖 ─────────────────────────────────────────────
vi.mock('@/api/client', () => ({
  tryRefreshToken: vi.fn(async () => 'refreshed-token'),
  authFetch: vi.fn(),
}))

// rAF 打桩为注册但不触发：所有内容落地都走 finally 的 flushStreamDelta
// （同步 settle），测试因此完全确定性，不依赖 jsdom 定时器推进。
const RAF_STUB = () => 1
vi.stubGlobal('requestAnimationFrame', RAF_STUB)
vi.stubGlobal('cancelAnimationFrame', () => {})

const SESSION_ID = 'sess-test-1'
const TURN_A = 'turn_A_111'
const TURN_B = 'turn_B_222'
const TURN_X = 'turn_X_333'

function makeMsg(): ChatMessage {
  return {
    role: 'assistant',
    content: '',
    reasoning: '',
    agentMeta: {
      sessionId: null,
      steps: [],
      totalPercent: 0,
      turnPhase: 'running',
      statusIndicator: { header: '' },
    },
  } as ChatMessage
}

function makeCallbacks(): ChatSSECallbacks {
  return {
    transitionTurn: vi.fn(),
    emit: vi.fn(),
    isCodexMode: () => false,
    isChatMode: () => false,
    streamingThinking: { value: false },
    streamingHasContent: { value: false },
    persistAssistantMessage: vi.fn(),
    onSseComplete: vi.fn(),
    onContentReady: vi.fn(),
    onStreamDelta: vi.fn(),
  }
}

/** 把事件数组编码成一次性关闭的 SSE 响应体 */
function sseBody(events: Array<Record<string, unknown> | string>): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder()
  const text = events
    .map((e) => (typeof e === 'string' ? e : `data: ${JSON.stringify(e)}\n`))
    .join('')
  return new ReadableStream({
    start(controller) {
      controller.enqueue(encoder.encode(text))
      controller.close()
    },
  })
}

function okResponse(events: Array<Record<string, unknown> | string>) {
  return { ok: true, status: 200, body: sseBody(events) } as unknown as Response
}

function unauthorizedResponse() {
  return { ok: false, status: 401, body: null } as unknown as Response
}

/** 手动可控流：分段灌入，用于 epoch 时序测试 */
function manualStream() {
  const encoder = new TextEncoder()
  let ctrl!: ReadableStreamDefaultController<Uint8Array>
  const stream = new ReadableStream<Uint8Array>({
    start(c) {
      ctrl = c
    },
  })
  return {
    stream,
    push(ev: Record<string, unknown>) {
      ctrl.enqueue(encoder.encode(`data: ${JSON.stringify(ev)}\n`))
    },
    close() {
      ctrl.close()
    },
  }
}

const flush = () => new Promise((r) => setTimeout(r, 5))

describe('useChatSSE · subscribeChatSSE 回合沙箱闸', () => {
  let fetchMock: ReturnType<typeof vi.fn>

  beforeEach(() => {
    fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    localStorage.setItem('token', 'test-token')
  })

  afterEach(() => {
    localStorage.clear()
    vi.unstubAllGlobals()
    vi.stubGlobal('requestAnimationFrame', RAF_STUB)
    vi.stubGlobal('cancelAnimationFrame', () => {})
  })

  it('显式 turnId：URL 带 turn_id 参数，本回合事件放行并正常收尾', async () => {
    fetchMock.mockResolvedValue(
      okResponse([
        { type: 'chat_turn_started', turn_id: TURN_A },
        { type: 'agent_message_delta', turn_id: TURN_A, payload: { delta: '你好' } },
        { type: 'workflow_completed', turn_id: TURN_A, payload: { duration_ms: 1200 } },
      ]),
    )
    const cb = makeCallbacks()
    const msg = makeMsg()
    const { subscribeChatSSE } = useChatSSE(cb)

    await subscribeChatSSE(SESSION_ID, msg, 0, TURN_A)

    // URL 带参（后端按 turn_id 回放）
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(String(fetchMock.mock.calls[0][0])).toBe(`/api/sse/chat/${SESSION_ID}?turn_id=${TURN_A}`)
    // 鉴权头
    expect((fetchMock.mock.calls[0][1] as any).headers.Authorization).toBe('Bearer test-token')
    // 本回合终态正常收尾：内容、耗时、落库、onSseComplete
    expect(msg.content).toBe('你好')
    expect(msg.agentMeta!.workflowStatus).toBe('completed')
    expect(msg.agentMeta!.durationMs).toBe(1200)
    expect(cb.persistAssistantMessage).toHaveBeenCalledTimes(1)
    expect(cb.transitionTurn).toHaveBeenCalledWith('done', '')
    expect(cb.onSseComplete).toHaveBeenCalledTimes(1)
  })

  it('显式 turnId：异回合事件被拦截并计数，内容不污染', async () => {
    fetchMock.mockResolvedValue(
      okResponse([
        { type: 'chat_turn_started', turn_id: TURN_A },
        // 上一回合漏网的 delta：turn_id 不匹配 → 拒绝并计数
        { type: 'agent_message_delta', turn_id: TURN_B, payload: { delta: '上上轮的残影' } },
        { type: 'workflow_completed', turn_id: TURN_A, payload: { duration_ms: 300 } },
      ]),
    )
    const cb = makeCallbacks()
    const msg = makeMsg()
    const { subscribeChatSSE } = useChatSSE(cb)

    await subscribeChatSSE(SESSION_ID, msg, 0, TURN_A)

    // 异回合 delta 未进入播放器：content 保持空
    expect(msg.content).toBe('')
    expect(msg.agentMeta!.workflowStatus).toBe('completed')
    expect((msg as any)._turnRt.droppedEvents).toBe(1)
  })

  it('动态启用（无 turnId）：收到带 turn_id 的 chat_turn_started 后启用隔离', async () => {
    fetchMock.mockResolvedValue(
      okResponse([
        { type: 'chat_turn_started', turn_id: TURN_X },
        { type: 'agent_message_delta', turn_id: TURN_X, payload: { delta: '动态启用' } },
        // 隔离启用后的遗留无 turn_id 事件 → 拒绝并计数
        { type: 'agent_message_delta', payload: { delta: '旧replay尾巴' } },
        { type: 'workflow_completed', turn_id: TURN_X, payload: { duration_ms: 500 } },
      ]),
    )
    const cb = makeCallbacks()
    const msg = makeMsg()
    const { subscribeChatSSE } = useChatSSE(cb)

    await subscribeChatSSE(SESSION_ID, msg)

    // 旧路径 URL 不带参
    expect(String(fetchMock.mock.calls[0][0])).toBe(`/api/sse/chat/${SESSION_ID}`)
    // 本回合 delta 落地，无 turn_id 的尾巴被拦
    expect(msg.content).toBe('动态启用')
    expect((msg as any)._turnRt).toBeTruthy()
    expect((msg as any)._turnRt.droppedEvents).toBe(1)
    expect(msg.agentMeta!.durationMs).toBe(500)
  })

  it('newWorkflowStarted 门：启动事件之前的旧 replay 一律忽略', async () => {
    fetchMock.mockResolvedValue(
      okResponse([
        // 旧消息回放：没有任何启动事件，直接忽略（沙箱未启用也无妨）
        { type: 'agent_message_delta', payload: { delta: '旧消息的残影' } },
        { type: 'chat_turn_started' },
        { type: 'agent_message_delta', payload: { delta: '新内容' } },
        { type: 'workflow_completed' },
      ]),
    )
    const cb = makeCallbacks()
    const msg = makeMsg()
    const { subscribeChatSSE } = useChatSSE(cb)

    await subscribeChatSSE(SESSION_ID, msg)

    expect(msg.content).toBe('新内容')
    expect(msg.agentMeta!.workflowStatus).toBe('completed')
  })

  it('消息代过滤：advanceMsgEpoch 之后旧订阅的事件全部丢弃', async () => {
    const manual = manualStream()
    fetchMock.mockResolvedValue({ ok: true, status: 200, body: manual.stream } as unknown as Response)
    const cb = makeCallbacks()
    const msg = makeMsg()
    const { subscribeChatSSE, advanceMsgEpoch } = useChatSSE(cb)

    const done = subscribeChatSSE(SESSION_ID, msg, 0, TURN_A)
    await flush()

    // 第一回合事件正常处理（starter 放行）
    manual.push({ type: 'chat_turn_started', turn_id: TURN_A })
    await flush()

    // 用户发了新消息 → 消息代递增，本订阅后续事件全部作废
    advanceMsgEpoch()
    manual.push({ type: 'agent_message_delta', turn_id: TURN_A, payload: { delta: '作废' } })
    manual.push({ type: 'workflow_completed', turn_id: TURN_A, payload: { duration_ms: 999 } })
    manual.close()
    await done

    expect(msg.content).toBe('')
    expect(msg.agentMeta!.workflowStatus).toBe('running')
    // 旧订阅不得再触发收尾回调
    expect(cb.onSseComplete).not.toHaveBeenCalled()
    expect(cb.persistAssistantMessage).not.toHaveBeenCalled()
    // starter 已被本回合沙箱记录
    expect((msg as any)._turnRt.turnStarted).toBe(true)
  })

  it('401：先刷新 token 再用新 token 重试', async () => {
    fetchMock
      .mockResolvedValueOnce(unauthorizedResponse())
      .mockResolvedValueOnce(
        okResponse([
          { type: 'chat_turn_started', turn_id: TURN_A },
          { type: 'workflow_completed', turn_id: TURN_A, payload: { duration_ms: 800 } },
        ]),
      )
    const cb = makeCallbacks()
    const msg = makeMsg()
    const { subscribeChatSSE } = useChatSSE(cb)

    await subscribeChatSSE(SESSION_ID, msg, 0, TURN_A)

    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect((fetchMock.mock.calls[1][1] as any).headers.Authorization).toBe('Bearer refreshed-token')
    expect(String(fetchMock.mock.calls[1][0])).toBe(`/api/sse/chat/${SESSION_ID}?turn_id=${TURN_A}`)
    expect(msg.agentMeta!.workflowStatus).toBe('completed')
  })

  // ── 2026-11-08 暂停态关流（连接配额治理）回归 ──────────────────

  it('回放中的旧暂停事件不关流：后面跟着续跑事件与终态，订阅完整走完', async () => {
    // 场景：刷新恢复/重连时全量回放回合历史——历史里有一次 guardian 暂停，
    // 用户确认后同一回合续跑直到完成。立即 abort 会把这次回放拦腰杀掉
    // （大哥截图实锤「还在思考，按钮提前结束」）。
    fetchMock.mockResolvedValue(
      okResponse([
        { type: 'chat_turn_started', turn_id: TURN_A },
        { type: 'guardian_confirmation_required', turn_id: TURN_A, payload: { tool_name: 'bash' } },
        // 确认续跑后的新事件（同一回合）
        { type: 'agent_message_delta', turn_id: TURN_A, payload: { delta: '续跑输出' } },
        { type: 'workflow_completed', turn_id: TURN_A, payload: { duration_ms: 3000 } },
      ]),
    )
    const cb = makeCallbacks()
    const msg = makeMsg()
    const { subscribeChatSSE } = useChatSSE(cb)

    await subscribeChatSSE(SESSION_ID, msg, 0, TURN_A)

    // 只建立一次连接：没被暂停事件 abort、也没触发重订
    expect(fetchMock).toHaveBeenCalledTimes(1)
    // 续跑内容与终态完整落地（立即 abort 时 status 会卡在 awaiting_guardian_confirmation）
    expect(msg.content).toBe('续跑输出')
    expect(msg.agentMeta!.workflowStatus).toBe('completed')
    expect(msg.agentMeta!.durationMs).toBe(3000)
  })

  it('实时暂停态静默 3s 后关流释放连接，不触发收尾回调', async () => {
    // 场景：需确认模式回合真实暂停（后端不发终态事件，流会无限期挂着）。
    // 延迟 3s 复查：仍处暂停态且事件流安静 → abort 释放连接配额。
    const manual = manualStream()
    fetchMock.mockResolvedValue({ ok: true, status: 200, body: manual.stream } as unknown as Response)
    const cb = makeCallbacks()
    const msg = makeMsg()
    const { subscribeChatSSE } = useChatSSE(cb)

    const done = subscribeChatSSE(SESSION_ID, msg, 0, TURN_A)
    await flush()
    manual.push({ type: 'chat_turn_started', turn_id: TURN_A })
    manual.push({ type: 'guardian_confirmation_required', turn_id: TURN_A, payload: { tool_name: 'bash' } })
    await flush()

    // 暂停事件刚到时不关流（延迟复查）
    expect(msg.agentMeta!.workflowStatus).toBe('awaiting_guardian_confirmation')

    // mock fetch 不会像真实 fetch 那样响应 abort——把 abort 打桩成
    // 「置位 signal + 关闭 mock 流」，模拟真实浏览器行为
    const controller = getSessionRuntime(SESSION_ID).chatSseController.value!
    const abortSpy = vi.spyOn(controller, 'abort').mockImplementation(function (this: AbortController) {
      AbortController.prototype.abort.call(this)
      manual.close()
    } as never)

    // 3s 静默后连接被主动释放（订阅 promise 正常结束）
    await done
    expect(abortSpy).toHaveBeenCalledTimes(1)
    expect(getSessionRuntime(SESSION_ID).chatSseController.value).toBeNull()
    // 暂停等待用户确认：绝不触发收尾（workflow 并未完成）
    expect(cb.onSseComplete).not.toHaveBeenCalled()
  }, 8000)
})

describe('useChatSSE · video_pipeline_progress 视频管线进度', () => {
  let fetchMock: ReturnType<typeof vi.fn>

  beforeEach(() => {
    fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    localStorage.setItem('token', 'test-token')
  })

  afterEach(() => {
    localStorage.clear()
    vi.unstubAllGlobals()
    vi.stubGlobal('requestAnimationFrame', RAF_STUB)
    vi.stubGlobal('cancelAnimationFrame', () => {})
  })

  it('新格式（talking_head_overlay）：写入 meta.videoProgress 并同步状态头', async () => {
    fetchMock.mockResolvedValue(
      okResponse([
        { type: 'chat_turn_started', turn_id: TURN_A },
        {
          type: 'video_pipeline_progress',
          turn_id: TURN_A,
          payload: {
            skill: 'talking_head_overlay',
            step: 3,
            total: 6,
            phase: 'render',
            label: '渲染叠加动画预览',
            detail: '正在渲染第 3 个叠加',
            percent: 50,
            turn_id: TURN_A,
          },
        },
        { type: 'workflow_completed', turn_id: TURN_A, payload: { duration_ms: 100 } },
      ]),
    )
    const cb = makeCallbacks()
    const msg = makeMsg()
    const { subscribeChatSSE } = useChatSSE(cb)

    await subscribeChatSSE(SESSION_ID, msg, 0, TURN_A)

    expect(msg.agentMeta!.videoProgress).toEqual({
      skill: 'talking_head_overlay',
      step: 3,
      total: 6,
      phase: 'render',
      label: '渲染叠加动画预览',
      detail: '正在渲染第 3 个叠加',
      percent: 50,
    })
    expect(cb.transitionTurn).toHaveBeenCalledWith('running', '渲染叠加动画预览')
  })

  it('旧格式（auto_short_video 无 phase 字段）：容错不报错', async () => {
    fetchMock.mockResolvedValue(
      okResponse([
        { type: 'chat_turn_started', turn_id: TURN_A },
        {
          type: 'video_pipeline_progress',
          turn_id: TURN_A,
          payload: { skill: 'auto_short_video', label: '剪辑合成', percent: 30 },
        },
        { type: 'workflow_completed', turn_id: TURN_A, payload: {} },
      ]),
    )
    const cb = makeCallbacks()
    const msg = makeMsg()
    const { subscribeChatSSE } = useChatSSE(cb)

    await subscribeChatSSE(SESSION_ID, msg, 0, TURN_A)

    expect(msg.agentMeta!.videoProgress).toMatchObject({
      skill: 'auto_short_video',
      phase: undefined,
      percent: 30,
    })
  })

  it('percent 非数字：忽略事件，不写 videoProgress', async () => {
    fetchMock.mockResolvedValue(
      okResponse([
        { type: 'chat_turn_started', turn_id: TURN_A },
        {
          type: 'video_pipeline_progress',
          turn_id: TURN_A,
          payload: { skill: 'talking_head_overlay', label: '异常载荷' },
        },
        { type: 'workflow_completed', turn_id: TURN_A, payload: {} },
      ]),
    )
    const cb = makeCallbacks()
    const msg = makeMsg()
    const { subscribeChatSSE } = useChatSSE(cb)

    await subscribeChatSSE(SESSION_ID, msg, 0, TURN_A)

    expect(msg.agentMeta!.videoProgress).toBeUndefined()
  })

  // ── 实时预览透传（2026-11-11 video studio P2 Task 5）────────────────

  it('preview_url 事件：透传到 videoProgress，VideoTaskCard 渲染单个预览按钮，点击开新窗', async () => {
    const PREVIEW = 'http://127.0.0.1:54321/'
    fetchMock.mockResolvedValue(
      okResponse([
        { type: 'chat_turn_started', turn_id: TURN_A },
        {
          type: 'video_pipeline_progress',
          turn_id: TURN_A,
          payload: {
            skill: 'talking_head_overlay',
            phase: 'render',
            label: '场景工程',
            percent: 40,
            preview_url: PREVIEW,
          },
        },
        // 同一 URL 的重复事件（agent 后续每次 emit 都带）：按钮不得重复
        {
          type: 'video_pipeline_progress',
          turn_id: TURN_A,
          payload: {
            skill: 'talking_head_overlay',
            phase: 'review',
            label: '质检',
            percent: 60,
            preview_url: PREVIEW,
          },
        },
        { type: 'workflow_completed', turn_id: TURN_A, payload: {} },
      ]),
    )
    const cb = makeCallbacks()
    const msg = makeMsg()
    const { subscribeChatSSE } = useChatSSE(cb)

    await subscribeChatSSE(SESSION_ID, msg, 0, TURN_A)

    expect(msg.agentMeta!.videoProgress!.preview_url).toBe(PREVIEW)

    const wrapper = mount(VideoTaskCard, {
      props: { meta: msg.agentMeta!, sessionId: SESSION_ID },
    })
    const openSpy = vi.spyOn(window, 'open').mockImplementation(() => null)
    const previewBtns = wrapper.findAll('button').filter((b) => b.text().includes('预览'))
    expect(previewBtns.length).toBe(1)
    await previewBtns[0].trigger('click')
    expect(openSpy).toHaveBeenCalledTimes(1)
    expect(openSpy).toHaveBeenCalledWith(PREVIEW)
    wrapper.unmount()
    openSpy.mockRestore()
  })

  it('不含 preview_url 的事件：VideoTaskCard 不渲染预览按钮', async () => {
    fetchMock.mockResolvedValue(
      okResponse([
        { type: 'chat_turn_started', turn_id: TURN_A },
        {
          type: 'video_pipeline_progress',
          turn_id: TURN_A,
          payload: { skill: 'talking_head_overlay', phase: 'render', label: '渲染中', percent: 45 },
        },
        { type: 'workflow_completed', turn_id: TURN_A, payload: {} },
      ]),
    )
    const cb = makeCallbacks()
    const msg = makeMsg()
    const { subscribeChatSSE } = useChatSSE(cb)

    await subscribeChatSSE(SESSION_ID, msg, 0, TURN_A)

    expect(msg.agentMeta!.videoProgress!.preview_url).toBeUndefined()

    const wrapper = mount(VideoTaskCard, {
      props: { meta: msg.agentMeta!, sessionId: SESSION_ID },
    })
    const previewBtns = wrapper.findAll('button').filter((b) => b.text().includes('预览'))
    expect(previewBtns.length).toBe(0)
    wrapper.unmount()
  })
})

describe('useChatSSE · cut_plan 叠加方案确认闸门（缺口2）', () => {
  let fetchMock: ReturnType<typeof vi.fn>

  beforeEach(() => {
    fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    localStorage.setItem('token', 'test-token')
  })

  afterEach(() => {
    localStorage.clear()
    vi.unstubAllGlobals()
    vi.stubGlobal('requestAnimationFrame', RAF_STUB)
    vi.stubGlobal('cancelAnimationFrame', () => {})
  })

  it('cut_plan_confirmation_required：写入 awaiting 状态 + cutPlan 快照', async () => {
    const plan = {
      video_id: 'v1',
      segments: [
        { segment_id: 's1', time_start: 0, time_end: 2.5, template: 'highlight', params: {}, skipped: false },
        { segment_id: 's2', time_start: 3, time_end: 5, template: 'subtitle', params: {}, skipped: false },
      ],
      palette: ['#ff0000', '#00ff00'],
      needs_confirmation: true,
      narrative: { theme: '周末探店', sections: [{ role: '开场', visual_intent: '城市空镜' }] },
    }
    fetchMock.mockResolvedValue(
      okResponse([
        { type: 'chat_turn_started', turn_id: TURN_A },
        {
          type: 'cut_plan_confirmation_required',
          turn_id: TURN_A,
          payload: {
            node_id: 'reactor',
            iteration: 2,
            skill: 'talking_head_overlay',
            plan,
            message: '叠加方案已规划，请确认后渲染',
            loop_state_path: '/tmp/loop_state_x.json',
          },
        },
      ]),
    )
    const cb = makeCallbacks()
    const msg = makeMsg()
    const { subscribeChatSSE } = useChatSSE(cb)

    await subscribeChatSSE(SESSION_ID, msg, 0, TURN_A)

    expect(msg.agentMeta!.workflowStatus).toBe('awaiting_cut_plan_confirmation')
    expect(msg.agentMeta!.loopStatePath).toBe('/tmp/loop_state_x.json')
    expect(msg.agentMeta!.turnId).toBe(TURN_A)
    expect((msg.agentMeta as any).cutPlan).toEqual({ plan, message: '叠加方案已规划，请确认后渲染' })
    // 暂停态不发 workflow_completed → 不落库、不收尾
    expect(cb.persistAssistantMessage).not.toHaveBeenCalled()
    expect(cb.transitionTurn).toHaveBeenCalledWith('running', '等待确认叠加方案…')
  })

  it('cut_plan_confirmation_resolved：confirm/cancel/failed 三态回写', async () => {
    fetchMock.mockResolvedValue(
      okResponse([
        { type: 'chat_turn_started', turn_id: TURN_A },
        {
          type: 'cut_plan_confirmation_required',
          turn_id: TURN_A,
          payload: { plan: { segments: [] }, loop_state_path: '/tmp/x.json' },
        },
        { type: 'cut_plan_confirmation_resolved', turn_id: TURN_A, payload: { action: 'confirm', ok: true } },
      ]),
    )
    const cb = makeCallbacks()
    const msg = makeMsg()
    const { subscribeChatSSE } = useChatSSE(cb)
    await subscribeChatSSE(SESSION_ID, msg, 0, TURN_A)
    expect((msg.agentMeta as any).cutPlanResolved).toBe('confirmed')

    // cancel
    const msg2 = makeMsg()
    fetchMock.mockResolvedValue(
      okResponse([
        { type: 'chat_turn_started', turn_id: TURN_A },
        {
          type: 'cut_plan_confirmation_required',
          turn_id: TURN_A,
          payload: { plan: { segments: [] }, loop_state_path: '/tmp/x.json' },
        },
        { type: 'cut_plan_confirmation_resolved', turn_id: TURN_A, payload: { action: 'cancel' } },
      ]),
    )
    await subscribeChatSSE(SESSION_ID, msg2, 0, TURN_A)
    expect((msg2.agentMeta as any).cutPlanResolved).toBe('cancelled')

    // failed
    const msg3 = makeMsg()
    fetchMock.mockResolvedValue(
      okResponse([
        { type: 'chat_turn_started', turn_id: TURN_A },
        {
          type: 'cut_plan_confirmation_required',
          turn_id: TURN_A,
          payload: { plan: { segments: [] }, loop_state_path: '/tmp/x.json' },
        },
        {
          type: 'cut_plan_confirmation_resolved',
          turn_id: TURN_A,
          payload: { action: 'confirm', ok: false, error: '渲染超时' },
        },
      ]),
    )
    await subscribeChatSSE(SESSION_ID, msg3, 0, TURN_A)
    expect((msg3.agentMeta as any).cutPlanResolved).toBe('failed')
    expect((msg3.agentMeta as any).cutPlanError).toBe('渲染超时')
  })
})

describe('useChatSSE · 双通道流式渲染（_stableHtml/_tailHtml）', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
    vi.stubGlobal('requestAnimationFrame', RAF_STUB)
    vi.stubGlobal('cancelAnimationFrame', () => {})
  })

  /**
   * rAF + performance.now 测试沙箱（工厂函数，闭包共享同一数组引用）。
   *
   * 两个关键点（都是踩过的坑）：
   * 1. tick 内部用 performance.now() 计时——rAF 回调被连续同步调用时真实
   *    时钟间隔只有微秒级，引擎不会推进。必须把 performance.now 一并 mock
   *    成跟随测试步进的时钟（每帧 +16ms）。
   * 2. rAF stub 与 drive 必须共享同一个回调数组——如果把数组当参数传给
   *    驱动函数并在内部重新赋值，stub 闭包 push 的还是外层原数组，两边
   *    从此指向不同数组，循环第一帧后就"看不到"新注册的回调而提前退出。
   *    所以这里用工厂返回 drive，数组活在工厂闭包里。
   */
  function makeRafHarness() {
    const cbs: FrameRequestCallback[] = []
    vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => {
      cbs.push(cb)
      return cbs.length
    })
    vi.stubGlobal('cancelAnimationFrame', () => {})
    let testNow = 1000
    vi.spyOn(performance, 'now').mockImplementation(() => testNow)
    return {
      /** 驱动至多 frames 帧（每帧 +16ms），rAF 队列空即提前停 */
      drive(frames = 40) {
        for (let i = 0; i < frames && cbs.length; i++) {
          const batch = cbs.splice(0)
          testNow += 16
          batch.forEach((f) => f(testNow))
        }
      },
    }
  }

  it('流式帧写入双通道字段；flushStreamDelta 落地后回落单通道', () => {
    const raf = makeRafHarness()

    const cb = makeCallbacks()
    const msg = makeMsg()
    const { pushStreamDelta, flushStreamDelta } = useChatSSE(cb)

    // 灌入跨块结构的 delta：第一段 + 空行（顶层块边界）+ 尾部生长段。
    // 任务2 后 emitted = 最后一个安全顶层块边界——第一段闭合入缓存，
    // 未闭合的尾部段留在 tail。行尾 \n 对齐真实流式 delta 形态
    // （enqueued 边界 = 最后一个换行，切分判定需要看到空行后的行）。
    pushStreamDelta(
      msg,
      '第一段稳定内容，这一行足够长，一定会触发提交边界。\n\n尾部正在生长\n',
    )
    raf.drive(120)

    console.log('[dbg] content=', JSON.stringify(msg.content))
    console.log('[dbg] stable=', (msg as any)._stableHtml)
    console.log('[dbg] tail=', (msg as any)._tailHtml)
    const m = msg as any
    // 流式中：双通道开启，稳定段/尾部段独立成字段
    expect(m._dualStream).toBe(true)
    expect(typeof m._stableHtml).toBe('string')
    expect(m._stableHtml).toContain('第一段稳定内容')
    // 块级淡入：新进入稳定区的块根元素带 rl-enter 标记（CSS 淡入+上浮）
    expect(m._stableHtml).toContain('rl-enter')
    expect(m._tailHtml).toContain('尾部正在')
    // 整串兼容字段仍然保留（非双通道消费点兜底）
    expect(m._streamHtml).toContain('尾部正在')

    // 落地：回落单节点完整渲染，分段字段清空
    flushStreamDelta(msg)
    expect(m._dualStream).toBe(false)
    expect(m._streamHtml).toContain('尾部正在')
    expect(m._stableHtml).toBe('')
    expect(m._tailHtml).toBe('')
  })

  it('discardStreamContent 清空全部渲染残留', () => {
    const raf = makeRafHarness()

    const cb = makeCallbacks()
    const msg = makeMsg()
    const { pushStreamDelta, discardStreamContent } = useChatSSE(cb)

    pushStreamDelta(msg, '污染内容第一行。\n污染内容第二行')
    raf.drive(80)
    expect((msg as any)._dualStream).toBe(true)

    discardStreamContent(msg)
    const m = msg as any
    expect(m._dualStream).toBe(false)
    expect(m._stableHtml).toBe('')
    expect(m._tailHtml).toBe('')
    expect(m._streamHtml).toBe('')
    expect(msg.content).toBe('')
  })
})

describe('useChatSSE · 思考链路平滑（REASONING_COMPLETED 不整段蹦出）', () => {
  afterEach(() => {
    localStorage.clear()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
    vi.stubGlobal('requestAnimationFrame', RAF_STUB)
    vi.stubGlobal('cancelAnimationFrame', () => {})
  })

  /**
   * rAF + performance.now 测试沙箱（与「双通道流式渲染」describe 同款）。
   * tick 内部用 performance.now() 计时，必须一并 mock 成跟随测试步进的时钟。
   */
  function makeRafHarness() {
    const cbs: FrameRequestCallback[] = []
    vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => {
      cbs.push(cb)
      return cbs.length
    })
    vi.stubGlobal('cancelAnimationFrame', () => {})
    let testNow = 1000
    vi.spyOn(performance, 'now').mockImplementation(() => testNow)
    return {
      /** 驱动至多 frames 帧（每帧 +16ms），rAF 队列空即提前停 */
      drive(frames = 40) {
        for (let i = 0; i < frames && cbs.length; i++) {
          const batch = cbs.splice(0)
          testNow += 16
          batch.forEach((f) => f(testNow))
        }
      },
    }
  }

  function stubFetch(body: Array<Record<string, unknown>>) {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    localStorage.setItem('token', 'test-token')
    fetchMock.mockResolvedValue(okResponse(body))
    return fetchMock
  }

  it('大块思维链（proto 门扣发）终态后按收尾档平滑追完，不瞬间整段蹦出', async () => {
    const raf = makeRafHarness()
    const bigThink = '深度思考内容。'.repeat(60)
    stubFetch([
      { type: 'chat_turn_started', turn_id: TURN_A },
      { type: 'reasoning_summary_text_delta', turn_id: TURN_A, payload: { node_id: 'reactor', delta: bigThink } },
      { type: 'reasoning_completed', turn_id: TURN_A, payload: { node_id: 'reactor', summary_parts: [] } },
      { type: 'workflow_completed', turn_id: TURN_A, payload: {} },
    ])
    const cb = makeCallbacks()
    const msg = makeMsg()
    const { subscribeChatSSE } = useChatSSE(cb)

    await subscribeChatSSE(SESSION_ID, msg, 0, TURN_A)

    // 关键：终态事件已到，但思维链不能瞬间全量（旧版 settle 会整段蹦出）
    expect(msg.reasoning).not.toBe(bigThink)

    // 驱动帧后平滑追完
    raf.drive(2000)
    expect(msg.reasoning).toBe(bigThink)
  })

  it('summary_parts 全量补发长于已喂 delta：差值喂播放器续播，不直接赋值', async () => {
    const raf = makeRafHarness()
    const streamed = '已流式到达的思考。'.repeat(30)
    const full = streamed + '这是终态补发的尾段。'.repeat(30)
    stubFetch([
      { type: 'chat_turn_started', turn_id: TURN_A },
      { type: 'reasoning_summary_text_delta', turn_id: TURN_A, payload: { node_id: 'reactor', delta: streamed } },
      { type: 'reasoning_completed', turn_id: TURN_A, payload: { node_id: 'reactor', summary_parts: [full] } },
      { type: 'workflow_completed', turn_id: TURN_A, payload: {} },
    ])
    const cb = makeCallbacks()
    const msg = makeMsg()
    const { subscribeChatSSE } = useChatSSE(cb)

    await subscribeChatSSE(SESSION_ID, msg, 0, TURN_A)

    // 补发尾段不能瞬间出现（旧版会直接 msg.reasoning = summary）
    expect(msg.reasoning).not.toContain('终态补发的尾段')

    raf.drive(2000)
    expect(msg.reasoning).toBe(full)
  })
})

describe('useChatSSE · playFinalText append-only（杜绝文字倒退/重播）', () => {
  it('已流式显示部分正文时，终稿只补齐增量，绝不重播或重复', () => {
    const cb = makeCallbacks()
    const msg = makeMsg()
    const { pushStreamDelta, playFinalText, flushStreamDelta } = useChatSSE(cb)

    // 1) 实时 delta 流先到达部分正文
    pushStreamDelta(msg, '你好世界')
    // 2) 终稿事件（agent_loop_done）携带更长完整正文
    playFinalText(msg, '你好世界，这是完整回复', { onDone: () => {} })
    // 3) 流结束落地
    flushStreamDelta(msg)

    // 关键：content 必须是完整终稿，不得出现「部分 + 全文」的重复串
    expect(msg.content).toBe('你好世界，这是完整回复')
  })

  it('纯 POST 一次性终稿：首次替换占位并完整播出', () => {
    const cb = makeCallbacks()
    const msg = makeMsg()
    msg.content = '生成中…'
    const { playFinalText, flushStreamDelta } = useChatSSE(cb)

    playFinalText(msg, '完整终稿', { onDone: () => {} })
    flushStreamDelta(msg)

    expect(msg.content).toBe('完整终稿')
  })

  it('agent_loop_done 重放（SSE 重连）不翻倍、不重播', () => {
    const cb = makeCallbacks()
    const msg = makeMsg()
    const { pushStreamDelta, playFinalText, flushStreamDelta } = useChatSSE(cb)

    pushStreamDelta(msg, '你好世界')
    playFinalText(msg, '你好世界，这是完整回复', { onDone: () => {} })
    // 重放相同终稿（SSE 重连 / 事件重放场景）
    playFinalText(msg, '你好世界，这是完整回复', { onDone: () => {} })
    flushStreamDelta(msg)

    expect(msg.content).toBe('你好世界，这是完整回复')
  })
})

/**
 * 多会话并行：收尾判定必须只看「本订阅会话自己的」运行时。
 * 旧实现读的是视图投影，后台会话收尾那一刻用户可能正停在另一个会话上，
 * 那个会话的 workflowSseController 会把别人的收尾误挡掉，后台任务永远"思考中"。
 */
describe('useChatSSE · 多会话并行的收尾隔离', () => {
  const OTHER_SESSION = 'sess-test-2'
  let fetchMock: ReturnType<typeof vi.fn>

  beforeEach(() => {
    fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    localStorage.setItem('token', 'test-token')
  })

  afterEach(() => {
    getSessionRuntime(SESSION_ID).dispose()
    getSessionRuntime(OTHER_SESSION).dispose()
    localStorage.clear()
    vi.unstubAllGlobals()
    vi.stubGlobal('requestAnimationFrame', RAF_STUB)
    vi.stubGlobal('cancelAnimationFrame', () => {})
  })

  it('视图停在别的会话且它挂着 workflow 订阅时，后台会话仍能正常收尾', async () => {
    fetchMock.mockResolvedValue(
      okResponse([
        { type: 'chat_turn_started', turn_id: TURN_A },
        { type: 'workflow_completed', turn_id: TURN_A, payload: { duration_ms: 100 } },
      ]),
    )
    const viewSid = ref<string | null>(OTHER_SESSION)
    const cb = makeCallbacks()
    const msg = makeMsg()
    const api = useChatSSE(cb, { activeSessionId: viewSid })
    // 当前视图会话（B）上挂着一个 workflow 订阅
    api.workflowSseController.value = new AbortController()

    await api.subscribeChatSSE(SESSION_ID, msg, 0, TURN_A)

    expect(cb.onSseComplete).toHaveBeenCalledWith(SESSION_ID)
    api.workflowSseController.value = null
  })

  it('本会话自己挂着 workflow 订阅时，依旧不触发收尾回调（保留原语义）', async () => {
    fetchMock.mockResolvedValue(
      okResponse([
        { type: 'chat_turn_started', turn_id: TURN_A },
        { type: 'workflow_completed', turn_id: TURN_A, payload: { duration_ms: 100 } },
      ]),
    )
    const viewSid = ref<string | null>(SESSION_ID)
    const cb = makeCallbacks()
    const msg = makeMsg()
    const api = useChatSSE(cb, { activeSessionId: viewSid })
    api.workflowSseController.value = new AbortController()

    await api.subscribeChatSSE(SESSION_ID, msg, 0, TURN_A)

    expect(cb.onSseComplete).not.toHaveBeenCalled()
    api.workflowSseController.value = null
  })
})