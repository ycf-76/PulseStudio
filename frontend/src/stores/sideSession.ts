import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import {
  createSideSession,
  listSideSessions,
  updateSideSession,
  deleteSideSession,
  finalizeMainSession,
  type SideSessionItem,
  type CreateSideSessionPayload,
} from '@/api/sideSessions'

/** 快照过期阈值：基准点后新增 ≥5 个用户轮次提示过期（PRD §12 Q2） */
export const EXPIRED_TURN_THRESHOLD = 5

export const useSideSessionStore = defineStore('sideSession', () => {
  const mainSessionId = ref<string | null>(null)
  const sides = ref<SideSessionItem[]>([])
  const activeSideId = ref<string | null>(null)
  const loading = ref(false)
  const error = ref('')

  function setMainSession(id: string | null) {
    mainSessionId.value = id
    if (!id) {
      sides.value = []
      activeSideId.value = null
    }
  }

  async function load(): Promise<void> {
    if (!mainSessionId.value) return
    loading.value = true
    error.value = ''
    try {
      sides.value = await listSideSessions(mainSessionId.value)
    } catch (e: any) {
      error.value = e?.message || '加载侧会话列表失败'
    } finally {
      loading.value = false
    }
  }

  async function create(payload: CreateSideSessionPayload): Promise<SideSessionItem> {
    if (!mainSessionId.value) throw new Error('未选择主会话')
    try {
      await createSideSession(mainSessionId.value, payload)
    } catch (e: any) {
      error.value = e?.message || '创建侧会话失败'
      throw e
    }
    await load()
    // 刷新后选中新创建的侧会话（列表按创建时间排序，取最后一个）
    const created = sides.value[sides.value.length - 1]
    if (created) activeSideId.value = created.id
    return created
  }

  async function rename(id: string, title: string): Promise<void> {
    await updateSideSession(id, { title })
    await load()
  }

  async function remove(id: string): Promise<void> {
    await deleteSideSession(id)
    if (activeSideId.value === id) activeSideId.value = null
    await load()
  }

  async function finalize(decisions: Record<string, 'keep' | 'discard'>): Promise<void> {
    if (!mainSessionId.value) return
    await finalizeMainSession(mainSessionId.value, decisions)
    await load()
  }

  function select(id: string | null) {
    activeSideId.value = id
  }

  const activeSide = computed<SideSessionItem | null>(
    () => sides.value.find((s) => s.id === activeSideId.value) || null,
  )

  const expiredSideIds = computed<string[]>(() =>
    sides.value.filter((s) => s.expired_turns >= EXPIRED_TURN_THRESHOLD).map((s) => s.id),
  )

  const activeCount = computed(() => sides.value.filter((s) => s.status === 'active').length)

  return {
    mainSessionId,
    sides,
    activeSideId,
    loading,
    error,
    setMainSession,
    load,
    create,
    rename,
    remove,
    finalize,
    select,
    activeSide,
    expiredSideIds,
    activeCount,
  }
})
