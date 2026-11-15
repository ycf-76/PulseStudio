import { ref } from 'vue'

/**
 * 输入区偏好开关 — ChatView 拆出
 * - 自动放行：工具调用是否需要人工确认（当前仅前端态 + toast，后端按会话配置走）
 */
export function useChatHeroPrefs(_deps: {
  setActiveWorkspace: (wsId: string) => void
  switchToFolder: (folderId: string) => void
}) {
  const autoApproveEnabled = ref(false)

  let _autoApproveToastTimer: number | null = null

  function toggleAutoApprove() {
    autoApproveEnabled.value = !autoApproveEnabled.value
    const existing = document.querySelector('.rl-copy-toast')
    if (existing) existing.remove()
    if (_autoApproveToastTimer !== null) window.clearTimeout(_autoApproveToastTimer)
    const el = document.createElement('div')
    el.className = 'rl-copy-toast'
    el.textContent = autoApproveEnabled.value
      ? '已开启自动放行：工具调用将直接执行'
      : '已切换为需确认：工具调用将暂停等待您批准'
    document.body.appendChild(el)
    _autoApproveToastTimer = window.setTimeout(() => {
      el.remove()
      _autoApproveToastTimer = null
    }, 2000)
  }

  return {
    autoApproveEnabled,
    toggleAutoApprove,
  }
}