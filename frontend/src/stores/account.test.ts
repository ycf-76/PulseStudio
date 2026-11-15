import { describe, it, expect, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useAccountStore } from './account'

/**
 * account store 测试（迁移后契约）
 *
 * 平台账号管理已迁移（api/account.ts 为空壳），store 退化为状态壳：
 * fetchAccounts 为空操作，getAccountById 返回 null。
 */

describe('useAccountStore（迁移后契约）', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('initial state has correct default values', () => {
    const store = useAccountStore()
    expect(store.accounts).toHaveLength(0)
    expect(store.loading).toBe(false)
    expect(store.error).toBeNull()
    expect(store.currentAccountId).toBeNull()
    expect(store.currentAccount).toBeNull()
  })

  it('fetchAccounts() is a no-op', async () => {
    const store = useAccountStore()
    await store.fetchAccounts()
    expect(store.accounts).toHaveLength(0)
  })

  it('getAccountById() returns null', () => {
    const store = useAccountStore()
    expect(store.getAccountById('whatever')).toBeNull()
  })
})
