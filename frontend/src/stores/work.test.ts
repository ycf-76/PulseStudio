import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useWorkStore } from './work'
import type { WorkItem } from './work'
import { deleteMyWork } from '@/api/my_works'
import { deleteCreativeArtifact } from '@/api/creativeArtifact'

vi.mock('@/api/my_works', () => ({
  listMyWorks: vi.fn(async () => ({ items: [] })),
  deleteMyWork: vi.fn(async () => {}),
}))

vi.mock('@/api/creativeArtifact', () => ({
  deleteCreativeArtifact: vi.fn(async () => true),
}))

/**
 * work store 产物隔离契约：
 * 卡片草稿必须绑定到产生它的会话（sessionId），不同会话的草稿互不覆盖。
 */

function makeDraft(title: string): WorkItem['cardDraft'] {
  return {
    title,
    template: 'default',
    pages: [{ type: 'cover', content: { htmlContent: `<p>${title}</p>` } } as any],
  }
}

function makeOutputWork(id: string, sessionId: string): WorkItem {
  return {
    id,
    platform: 'xiaohongshu',
    contentType: 'image_text',
    title: '已保存作品',
    url: '',
    likes: 0,
    comments: 0,
    shares: 0,
    collects: 0,
    views: 0,
    interactionRate: 0,
    viralScore: 0,
    performanceTier: '-',
    dataCollectedAt: new Date().toISOString(),
    isDraft: true,
    contentStatus: 'draft',
    sessionId,
  }
}

function makeCollectedWork(id: string): WorkItem {
  return {
    id,
    platform: 'xiaohongshu',
    contentType: 'image_text',
    title: '采集作品',
    url: '',
    likes: 0,
    comments: 0,
    shares: 0,
    collects: 0,
    views: 0,
    interactionRate: 0,
    viralScore: 0,
    performanceTier: '-',
    dataCollectedAt: new Date().toISOString(),
    contentStatus: 'collected',
  }
}

describe('useWorkStore 产物隔离', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('不同会话的草稿各自独立，互不覆盖', () => {
    const store = useWorkStore()
    const a = store.saveCardAsDraft(makeDraft('会话A草稿'), undefined, undefined, undefined, { sessionId: 'sess-a' })
    const b = store.saveCardAsDraft(makeDraft('会话B草稿'), undefined, undefined, undefined, { sessionId: 'sess-b' })

    expect(a.id).not.toBe(b.id)
    expect(a.sessionId).toBe('sess-a')
    expect(b.sessionId).toBe('sess-b')
    expect(a.cardDraft?.title).toBe('会话A草稿')
    expect(b.cardDraft?.title).toBe('会话B草稿')
  })

  it('同一会话重复产出草稿时复用同一条', () => {
    const store = useWorkStore()
    const first = store.saveCardAsDraft(makeDraft('第一版'), undefined, undefined, undefined, { sessionId: 'sess-a' })
    const second = store.saveCardAsDraft(makeDraft('第二版'), undefined, undefined, undefined, { sessionId: 'sess-a' })

    expect(second.id).toBe(first.id)
    expect(store.works.filter(w => w.isDraft && w.sessionId === 'sess-a')).toHaveLength(1)
    expect(store.works.find(w => w.id === first.id)?.cardDraft?.title).toBe('第二版')
  })

  it('未绑定会话的草稿不会串到已有会话草稿上', () => {
    const store = useWorkStore()
    const bound = store.saveCardAsDraft(makeDraft('绑定草稿'), undefined, undefined, undefined, { sessionId: 'sess-a' })
    const loose = store.saveCardAsDraft(makeDraft('游离草稿'))

    expect(loose.id).not.toBe(bound.id)
    expect(bound.sessionId).toBe('sess-a')
    expect(loose.sessionId).toBeUndefined()
    expect(store.works.find(w => w.id === bound.id)?.cardDraft?.title).toBe('绑定草稿')
  })

  it('草稿携带来源作品 id', () => {
    const store = useWorkStore()
    const draft = store.saveCardAsDraft(makeDraft('来源草稿'), undefined, undefined, undefined, {
      sessionId: 'sess-a',
      sourceWorkId: 'work-1',
    })
    expect(draft.sourceWorkId).toBe('work-1')
  })

  it('updateDraft 同时作用于内存草稿与后端产物列表', () => {
    const store = useWorkStore()
    store.works.push(store.createDraft('image_text'))
    const localId = store.works[0].id
    store.updateDraft(localId, { title: '本地改标题' })
    expect(store.works.find(w => w.id === localId)?.title).toBe('本地改标题')

    store.outputWorks.push(makeOutputWork('artifact-1', 'sess-a'))
    store.updateDraft('artifact-1', { title: '后端产物改标题' })
    expect(store.outputWorks.find(w => w.id === 'artifact-1')?.title).toBe('后端产物改标题')
  })

  /**
   * 账号同步完成后「我的空间」需要重新拉取列表，
   * 否则停留在该页时看不到同步结果（图文依旧显示过期图片）。
   */
  it('fetchWorks 合并远端数据后派发 works:updated 事件', async () => {
    const store = useWorkStore()
    const handler = vi.fn()
    window.addEventListener('works:updated', handler)
    try {
      await store.fetchWorks()
    } finally {
      window.removeEventListener('works:updated', handler)
    }
    expect(handler).toHaveBeenCalledTimes(1)
  })
})

/**
 * 删除作品契约：采集作品必须先删后端记录再清内存，
 * 否则刷新（fetchWorks 重新拉取）后条目会复活。
 */
describe('useWorkStore 删除作品', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('采集作品删除时先请求后端记录接口，再移除内存条目', async () => {
    const store = useWorkStore()
    const work = makeCollectedWork('pcp-1')
    store.works.push(work)

    const ok = await store.deleteWork('pcp-1')

    expect(ok).toBe(true)
    expect(deleteMyWork).toHaveBeenCalledWith('pcp-1')
    expect(store.works.some(w => w.id === 'pcp-1')).toBe(false)
  })

  it('后端删除失败时保留条目并返回 false（不做假删除）', async () => {
    const store = useWorkStore()
    store.works.push(makeCollectedWork('pcp-2'))
    vi.mocked(deleteMyWork).mockRejectedValueOnce({ response: { status: 500 } })

    const ok = await store.deleteWork('pcp-2')

    expect(ok).toBe(false)
    expect(store.works.some(w => w.id === 'pcp-2')).toBe(true)
  })

  it('未落库的本地草稿只清内存，不请求后端', async () => {
    const store = useWorkStore()
    const draft = store.createDraft('image_text')

    const ok = await store.deleteWork(draft.id)

    expect(ok).toBe(true)
    expect(deleteMyWork).not.toHaveBeenCalled()
    expect(store.works.some(w => w.id === draft.id)).toBe(false)
  })

  it('outputWorks 里的产物走创作产物接口删除', async () => {
    const store = useWorkStore()
    store.outputWorks.push(makeOutputWork('artifact-1', 'sess-a'))

    const ok = await store.deleteWork('artifact-1')

    expect(ok).toBe(true)
    expect(deleteCreativeArtifact).toHaveBeenCalledWith('artifact-1')
    expect(store.outputWorks.some(w => w.id === 'artifact-1')).toBe(false)
  })

  it('裸 artifact id 在后端记录接口 404 时回退到创作产物接口', async () => {
    const store = useWorkStore()
    store.works.push(makeOutputWork('artifact-raw', 'sess-a'))
    vi.mocked(deleteMyWork).mockRejectedValueOnce({ response: { status: 404 } })
    vi.mocked(deleteCreativeArtifact).mockResolvedValueOnce(true)

    const ok = await store.deleteWork('artifact-raw')

    expect(ok).toBe(true)
    expect(deleteCreativeArtifact).toHaveBeenCalledWith('artifact-raw')
    expect(store.works.some(w => w.id === 'artifact-raw')).toBe(false)
  })
})