<template>
  <div class="min-h-screen" :class="rootClass" style="position: relative;">
    <div
      class="mint-shell"
      :class="[shellClass, { 'mint-collapsed': isSidebarCollapsed, 'is-left-resizing': resizerVariant === 'overlay' && isResizing }]"
      :style="{ '--left-sidebar-width': width + 'px' }"
    >
      <SidebarNav
        :current-page="currentPage"
        :is-collapsed="isSidebarCollapsed"
        v-bind="$attrs"
        @toggle-sidebar="toggleSidebar"
        @nav-click="handleNavClick"
        @go-eco="goEco"
        @open-settings="handleOpenSettings"
        @new-chat="handleNewChat"
      />

      <div
        v-if="!isSidebarCollapsed"
        :class="resizerVariant === 'overlay' ? 'mint-left-resize-handle' : 'da-sidebar-resizer'"
        @mousedown="startResize"
        @dblclick.prevent="handleResizerDblclick"
      >
        <div v-if="resizerVariant !== 'overlay'" class="da-sidebar-resizer-line"></div>
      </div>

      <slot />
    </div>

    <slot name="overlay" />
  </div>
</template>

<script setup lang="ts">
import { useAttrs } from 'vue'
import { useRouter } from 'vue-router'
import SidebarNav from '@/components/workbench/SidebarNav.vue'
import { useUIState } from '@/composables/useUIState'
import { useResizeHandle } from '@/composables/useResizeHandle'

defineOptions({ inheritAttrs: false })

const props = withDefaults(
  defineProps<{
    currentPage: string
    shellClass?: string
    rootClass?: string
    resizerVariant?: 'thin' | 'overlay'
    settingsSection?: string
  }>(),
  {
    shellClass: 'da-shell',
    rootClass: '',
    resizerVariant: 'thin',
    settingsSection: undefined,
  },
)

const NAV_ROUTES: Record<string, string> = {
  home: '/',
  chat: '/chat',
  'topic-pool': '/topic-pool',
  'my-works': '/my-works',
  'task-plans': '/task-plans',
  portfolio: '/portfolio',
  skills: '/skills',
}

const router = useRouter()
const attrs = useAttrs()
const { isSidebarCollapsed, toggleSidebar: toggleSidebarBase, openSettings } = useUIState()

const { width, isResizing, startResize, resetWidth } = useResizeHandle({
  direction: 'left',
  minWidth: 170,
  maxWidth: 520,
  storageKey: 'mint-left-sidebar-width-v2',
})

function toggleSidebar() {
  if (attrs.onToggleSidebar) return
  toggleSidebarBase()
}

function handleNavClick(pageName: string) {
  if (attrs.onNavClick) return
  if (pageName === props.currentPage) return
  router.push(NAV_ROUTES[pageName] || '/chat').catch(() => {})
}

function goEco() {
  if (attrs.onGoEco) return
  router.push('/eco')
}

function handleOpenSettings() {
  if (attrs.onOpenSettings) return
  openSettings(props.settingsSection)
}

function handleNewChat() {
  if (attrs.onNewChat) return
  router.push('/chat').catch(() => {})
}

function handleResizerDblclick() {
  if (props.resizerVariant === 'overlay') resetWidth()
}
</script>