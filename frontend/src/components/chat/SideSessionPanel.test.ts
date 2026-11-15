/**
 * 侧会话面板挂载测试（Task 7/8）：
 * 1. 列表项渲染：标题/「基于主任务第 N 轮」/已产生写副作用标记；
 * 2. 过期横幅：active 侧会话 expired_turns >= 5 时显示「快照已过期，主任务已推进 X 轮」；
 * 3. 新建弹窗：粒度三选一、recent_n 默认 10，提交走 store.create；
 * 4. 删除走二次确认。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('@/api/sideSessions', () => ({
  createSideSession: vi.fn(async () => ({ id: 'side-new', title: '侧会话', status: 'active', has_side_effects: false, granularity: 'recent_n', baseline_message_id: '', baseline_turn: 0, expired_turns: 0, token_level: 'low' })),
  listSideSessions: vi.fn(async () => []),
  updateSideSession: vi.fn(async () => ({ id: '', title: '', status: '' })),
  deleteSideSession: vi.fn(async () => {}),
  finalizeMainSession: vi.fn(async () => ({ results: {} })),
  generateDigestDraft: vi.fn(async () => ''),
  bringBack: vi.fn(async () => ({ main_session_id: 'm', main_message_id: 'x' })),
  confirmSideWrite: vi.fn(async () => ({ side_session_id: '', status: 'approved' })),
  sendSideAgentMessage: vi.fn(async () => ({ status: 'completed', message: '' })),
}))

vi.mock('@/api/chatSessions', () => ({
  listMessages: vi.fn(async () => []),
}))

vi.mock('./sideEventStream', () => ({
  openSideEventStream: vi.fn(() => ({ close: vi.fn() })),
}))

import SideSessionPanel from './SideSessionPanel.vue'
import { useSideSessionStore } from '@/stores/sideSession'
import { listMessages } from '@/api/chatSessions'
import { sendSideAgentMessage } from '@/api/sideSessions'
import type { SideSessionItem } from '@/api/sideSessions'

function makeSide(patch: Partial<SideSessionItem> = {}): SideSessionItem {
  return {
    id: 'side1',
    title: '竞品分析',
    status: 'active',
    has_side_effects: false,
    granularity: 'recent_n',
    baseline_message_id: 'msg9',
    baseline_turn: 3,
    expired_turns: 0,
    token_level: 'low',
    ...patch,
  }
}

async function mountPanel() {
  const pinia = createPinia()
  setActivePinia(pinia)
  const store = useSideSessionStore()
  store.mainSessionId = 'main1'
  const wrapper = mount(SideSessionPanel, {
    props: { open: true },
    global: { plugins: [pinia] },
  })
  await flushPromises()
  return { wrapper, store }
}

describe('SideSessionPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('列表项渲染标题、基准轮次与写副作用标记', async () => {
    const { wrapper } = await mountPanel()
    const store = useSideSessionStore()
    store.sides = [
      makeSide({}),
      makeSide({ id: 'side2', title: '另一条', has_side_effects: true, status: 'archived' }),
    ]
    await wrapper.vm.$nextTick()
    const text = wrapper.text()
    expect(text).toContain('竞品分析')
    expect(text).toContain('基于主任务第 3 轮')
    expect(text).toContain('已产生写副作用')
    expect(text).toContain('另一条')
  })

  it('active 侧会话过期时显示横幅，未过期不显示', async () => {
    const { wrapper } = await mountPanel()
    const store = useSideSessionStore()
    store.sides = [makeSide({ expired_turns: 7 })]
    await wrapper.vm.$nextTick()
    expect(wrapper.text()).toContain('快照已过期，主任务已推进 7 轮')

    store.sides = [makeSide({ expired_turns: 2 })]
    await wrapper.vm.$nextTick()
    expect(wrapper.text()).not.toContain('快照已过期')
  })

  it('新建弹窗：recent_n 默认 10，提交调用 store.create', async () => {
    const { wrapper, store } = await mountPanel()
    const spy = vi.spyOn(store, 'create').mockResolvedValue(makeSide({}) as any)
    // 打开新建弹窗
    await wrapper.find('.ssp-create-btn').trigger('click')
    await wrapper.vm.$nextTick()
    const recentInput = wrapper.find('.ssp-recent-n input')
    expect((recentInput.element as HTMLInputElement).value).toBe('10')
    // 选 recent_n 粒度后提交
    await wrapper.find('.ssp-granularity-recent_n').trigger('click')
    await wrapper.find('.ssp-submit').trigger('click')
    await flushPromises()
    expect(spy).toHaveBeenCalledWith(expect.objectContaining({ granularity: 'recent_n', recent_n: 10 }))
  })

  it('删除需二次确认后才调用 store.remove', async () => {
    const { wrapper, store } = await mountPanel()
    const spy = vi.spyOn(store, 'remove').mockResolvedValue()
    store.sides = [makeSide({})]
    await wrapper.vm.$nextTick()
    // 确认弹窗未确认前不调用
    await wrapper.find('.ssp-item-delete').trigger('click')
    await wrapper.vm.$nextTick()
    expect(spy).not.toHaveBeenCalled()
    // 点击确认
    await wrapper.find('.ssp-confirm-delete').trigger('click')
    await flushPromises()
    expect(spy).toHaveBeenCalledWith('side1')
  })

  it('点击列表项在面板内进入对话模式，不切换主聊天（不发 open-session）', async () => {
    const { wrapper, store } = await mountPanel()
    vi.mocked(listMessages).mockResolvedValue([
      { id: 'm1', role: 'user', content: '帮我看看竞品' },
      { id: 'm2', role: 'assistant', content: '好的，竞品情况如下…' },
    ] as any)
    store.sides = [makeSide({})]
    await wrapper.vm.$nextTick()
    await wrapper.find('.ssp-item-head').trigger('click')
    await flushPromises()
    // 面板内渲染历史消息与输入框
    expect(wrapper.text()).toContain('帮我看看竞品')
    expect(wrapper.text()).toContain('好的，竞品情况如下…')
    expect(wrapper.find('.ssp-chat-input').exists()).toBe(true)
    // 不再切换主聊天
    const events = wrapper.emitted('open-session')
    expect(events).toBeUndefined()
  })

  it('输入框发送消息调用侧会话 agent 并渲染回复', async () => {
    const { wrapper, store } = await mountPanel()
    vi.mocked(sendSideAgentMessage).mockResolvedValue({
      status: 'completed',
      message: '这是侧会话的回复',
    } as any)
    store.sides = [makeSide({})]
    await wrapper.vm.$nextTick()
    await wrapper.find('.ssp-item-head').trigger('click')
    await flushPromises()
    const input = wrapper.find('.ssp-chat-input')
    await input.setValue('对比一下两个竞品')
    await wrapper.find('.ssp-chat-send').trigger('click')
    await flushPromises()
    expect(sendSideAgentMessage).toHaveBeenCalledWith('side1', '对比一下两个竞品')
    expect(wrapper.text()).toContain('这是侧会话的回复')
  })

  it('输入框顶部的「对话历史」按钮返回列表模式', async () => {
    const { wrapper, store } = await mountPanel()
    store.sides = [makeSide({})]
    await wrapper.vm.$nextTick()
    await wrapper.find('.ssp-item-head').trigger('click')
    await wrapper.vm.$nextTick()
    expect(wrapper.find('.ssp-chat-input').exists()).toBe(true)
    await wrapper.find('.ssp-history-btn').trigger('click')
    await wrapper.vm.$nextTick()
    expect(wrapper.find('.ssp-chat-input').exists()).toBe(false)
    expect(wrapper.find('.ssp-create-btn').exists()).toBe(true)
  })
})
