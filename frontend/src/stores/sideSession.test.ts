import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

/**
 * 侧会话 store 契约（Task 7）：
 * 1. create 成功后刷新列表并选中新侧会话；
 * 2. create 失败（如超上限）向上抛错，不吞异常；
 * 3. finalize keep → 列表中状态变 archived；
 * 4. expired getter：expired_turns >= 5 标记过期（Q2 阈值）；
 * 5. activeSide getter 返回当前选中侧会话。
 */

vi.mock('@/api/sideSessions', () => ({
  createSideSession: vi.fn(),
  listSideSessions: vi.fn(),
  updateSideSession: vi.fn(),
  deleteSideSession: vi.fn(),
  finalizeMainSession: vi.fn(),
}))

import {
  createSideSession,
  listSideSessions,
  updateSideSession,
  deleteSideSession,
  finalizeMainSession,
} from '@/api/sideSessions'
import { useSideSessionStore } from './sideSession'
import type { SideSessionItem } from '@/api/sideSessions'

const mocked = {
  create: vi.mocked(createSideSession),
  list: vi.mocked(listSideSessions),
  update: vi.mocked(updateSideSession),
  remove: vi.mocked(deleteSideSession),
  finalize: vi.mocked(finalizeMainSession),
}

function makeSide(overrides: Partial<SideSessionItem> = {}): SideSessionItem {
  return {
    id: 's1',
    title: '侧会话',
    status: 'active',
    has_side_effects: false,
    granularity: 'recent_n',
    baseline_message_id: '01BASE',
    baseline_turn: 0,
    expired_turns: 0,
    token_level: 'low',
    ...overrides,
  }
}

describe('useSideSessionStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('create 成功后刷新列表并选中新侧会话', async () => {
    mocked.create.mockResolvedValue({ id: 'new-side', title: '侧会话' } as any)
    mocked.list.mockResolvedValue([makeSide({ id: 'old' }), makeSide({ id: 'new-side' })])

    const store = useSideSessionStore()
    store.setMainSession('main1')
    await store.create({ granularity: 'recent_n' })

    expect(mocked.create).toHaveBeenCalledWith('main1', { granularity: 'recent_n' })
    expect(mocked.list).toHaveBeenCalledWith('main1')
    expect(store.activeSideId).toBe('new-side')
    expect(store.sides).toHaveLength(2)
  })

  it('create 失败向上抛错', async () => {
    mocked.create.mockRejectedValue(new Error('活跃侧会话已达上限（5 个）'))
    const store = useSideSessionStore()
    store.setMainSession('main1')
    await expect(store.create({ granularity: 'full' })).rejects.toThrow('上限')
  })

  it('finalize keep 后列表状态为 archived', async () => {
    mocked.finalize.mockResolvedValue({ results: { s1: 'archived' } })
    mocked.list.mockResolvedValue([makeSide({ id: 's1', status: 'archived' })])

    const store = useSideSessionStore()
    store.setMainSession('main1')
    await store.finalize({ s1: 'keep' })

    expect(mocked.finalize).toHaveBeenCalledWith('main1', { s1: 'keep' })
    expect(store.sides[0].status).toBe('archived')
  })

  it('expired getter 按 expired_turns >= 5 判定', async () => {
    mocked.list.mockResolvedValue([
      makeSide({ id: 'fresh', expired_turns: 4 }),
      makeSide({ id: 'stale', expired_turns: 5 }),
    ])
    const store = useSideSessionStore()
    store.setMainSession('main1')
    await store.load()

    expect(store.expiredSideIds).toEqual(['stale'])
  })

  it('activeSide 返回当前选中项，未选中为 null', async () => {
    mocked.list.mockResolvedValue([makeSide({ id: 'a' }), makeSide({ id: 'b' })])
    const store = useSideSessionStore()
    store.setMainSession('main1')
    await store.load()

    expect(store.activeSide).toBeNull()
    store.select('b')
    expect(store.activeSide?.id).toBe('b')
  })

  it('remove 后刷新列表且清除选中', async () => {
    mocked.remove.mockResolvedValue(undefined)
    mocked.list.mockResolvedValue([])
    const store = useSideSessionStore()
    store.setMainSession('main1')
    store.select('gone')
    await store.remove('gone')

    expect(mocked.remove).toHaveBeenCalledWith('gone')
    expect(store.sides).toHaveLength(0)
    expect(store.activeSideId).toBeNull()
  })

  it('rename 调用 update 接口并刷新', async () => {
    mocked.update.mockResolvedValue({ id: 's1', title: '查文档', status: 'active' } as any)
    mocked.list.mockResolvedValue([makeSide({ id: 's1', title: '查文档' })])
    const store = useSideSessionStore()
    store.setMainSession('main1')
    await store.rename('s1', '查文档')

    expect(mocked.update).toHaveBeenCalledWith('s1', { title: '查文档' })
    expect(store.sides[0].title).toBe('查文档')
  })
})
