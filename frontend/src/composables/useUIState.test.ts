import { describe, it, expect, beforeEach } from 'vitest'
import { useUIState } from './useUIState'

describe('useUIState', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  describe('isSidebarCollapsed', () => {
    it('should have a boolean initial value', () => {
      const { isSidebarCollapsed } = useUIState()
      expect(typeof isSidebarCollapsed.value).toBe('boolean')
    })
  })

  describe('toggleSidebar', () => {
    it('should toggle sidebar collapsed state and persist to localStorage', () => {
      const { isSidebarCollapsed, toggleSidebar } = useUIState()
      const before = isSidebarCollapsed.value

      toggleSidebar()
      expect(isSidebarCollapsed.value).toBe(!before)
      expect(localStorage.getItem('mint_sidebar_collapsed')).toBe(!before ? '1' : '0')

      toggleSidebar()
      expect(isSidebarCollapsed.value).toBe(before)
      expect(localStorage.getItem('mint_sidebar_collapsed')).toBe(before ? '1' : '0')
    })
  })
})