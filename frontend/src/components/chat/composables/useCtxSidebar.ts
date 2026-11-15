import type { useChatContextStore } from '@/stores/chatContext'

/**
 * 侧边栏拖拽调宽 — ChatView 拆出
 * 手柄按下后用 rAF 节流写 store，松手清理监听与 body 态。
 */
export function useCtxSidebar(ctxStore: ReturnType<typeof useChatContextStore>) {
  function startCtxResize(e: MouseEvent) {
    e.preventDefault()
    const startX = e.clientX
    const startW = ctxStore.sidebarWidth
    let rafId = 0
    function onMove(ev: MouseEvent) {
      const delta = startX - ev.clientX
      cancelAnimationFrame(rafId)
      rafId = requestAnimationFrame(() => {
        ctxStore.setSidebarWidth(startW + delta)
      })
    }
    function onUp() {
      cancelAnimationFrame(rafId)
      document.removeEventListener('mousemove', onMove)
      document.removeEventListener('mouseup', onUp)
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
      document.body.classList.remove('is-ctx-resizing')
    }
    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup', onUp)
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
    document.body.classList.add('is-ctx-resizing')
  }

  function resetCtxWidth() {
    ctxStore.setSidebarWidth(Math.max(280, Math.min(420, Math.floor(window.innerWidth / 4))))
  }

  return { startCtxResize, resetCtxWidth }
}
