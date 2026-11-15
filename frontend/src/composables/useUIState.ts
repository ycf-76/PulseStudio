import { ref } from 'vue'

const SK_COLLAPSED = 'mint_sidebar_collapsed'
const isSidebarCollapsed = ref(localStorage.getItem(SK_COLLAPSED) === '1')
const showSettings = ref(false)
const settingsSection = ref<string | null>(null)

export function useUIState() {
  function toggleSidebar() {
    isSidebarCollapsed.value = !isSidebarCollapsed.value
    localStorage.setItem(SK_COLLAPSED, isSidebarCollapsed.value ? '1' : '0')
  }

  function openSettings(section?: string) {
    settingsSection.value = section ?? null
    showSettings.value = true
  }

  function closeSettings() {
    showSettings.value = false
    settingsSection.value = null
  }

  return {
    isSidebarCollapsed,
    toggleSidebar,
    showSettings,
    settingsSection,
    openSettings,
    closeSettings,
  }
}