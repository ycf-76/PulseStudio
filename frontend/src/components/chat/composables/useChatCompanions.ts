import { ref, watch, onUnmounted, defineAsyncComponent, type Ref } from 'vue'

/**
 * 桌面伙伴挂件（螃蟹/猫/海绵宝宝）— ChatView 拆出
 *
 * 三只伙伴是输入框上方的浮动陪伴组件，显隐由输入框聚焦驱动
 * （useChatInit 里 crabVisible 的赋值），位置用 rAF 跟随 .rl-composer-seat。
 * 另外挂 P2 的「分析→创作」承接卡开关（handoffOpen / onHandoffStart）。
 */

export const CRAB_PLUGIN_ID = 'crab-companion'
export const CAT_PLUGIN_ID = 'cat-companion'
export const SPONGEBOB_PLUGIN_ID = 'spongebob-companion'

export function useChatCompanions(deps: {
  inputText: Ref<string>
  sendMessage: () => Promise<void>
}) {
  const { inputText, sendMessage } = deps

  // P2：分析到创作的沉浸式承接卡开关
  const handoffOpen = ref(false)

  function onHandoffStart(payload: {
    direction: string
    proposedStructure: string[]
    analysis: Record<string, any> | null
  }) {
    const planLabel =
      payload.direction === 'reuse' ? '沿用结构' : payload.direction === 'remix' ? '改造结构' : '全新表达'
    const structureText = payload.proposedStructure.join(' → ')
    const best = payload.analysis?.writing_prescription?.best_patterns?.[0]?.name
    const avoid = payload.analysis?.writing_prescription?.avoid_patterns?.[0]?.name
    const parts = [
      `基于刚才的作品分析，帮我创作一篇新图文。`,
      `创作方向：${planLabel}。`,
      `建议分镜结构（供参考，生成时请过质量门禁细化）：${structureText}。`,
    ]
    if (best) parts.push(`请延续已验证模式「${best}」。`)
    if (avoid) parts.push(`请规避「${avoid}」。`)
    parts.push(`分析上下文已自动带入，直接产出 card_draft 即可。`)
    inputText.value = parts.join('\n')
    handoffOpen.value = false
    void sendMessage()
  }

  const CrabCompanion = defineAsyncComponent(() => import('../CrabCompanion.vue'))
  const CatCompanion = defineAsyncComponent(() => import('../CatCompanion.vue'))
  const SpongeBobCompanion = defineAsyncComponent(() => import('../SpongeBobCompanion.vue'))

  // ── 位置跟随：可见时每帧刷新输入框座位矩形 ──
  const crabVisible = ref(false)
  const crabInputRect = ref<DOMRect | null>(null)
  const crabSeatRect = ref<DOMRect | null>(null)
  let crabRafId: number | null = null

  function updateCrabPosition() {
    const seat = document.querySelector('.rl-composer-seat:not(.rl-composer-hero)')
    if (seat) {
      crabSeatRect.value = seat.getBoundingClientRect()
      const card = seat.querySelector('.rl-composer-card')
      if (card) {
        crabInputRect.value = card.getBoundingClientRect()
      }
    }
    if (crabVisible.value) {
      crabRafId = requestAnimationFrame(updateCrabPosition)
    }
  }

  watch(crabVisible, (v) => {
    if (v) {
      crabRafId = requestAnimationFrame(updateCrabPosition)
    } else if (crabRafId) {
      cancelAnimationFrame(crabRafId)
      crabRafId = null
    }
  })

  // 组件卸载时停掉位置跟随循环（原在 ChatView.onUnmounted，随拆分内移）
  onUnmounted(() => {
    if (crabRafId !== null) {
      cancelAnimationFrame(crabRafId)
      crabRafId = null
    }
  })

  return {
    handoffOpen,
    onHandoffStart,
    CrabCompanion,
    CatCompanion,
    SpongeBobCompanion,
    CRAB_PLUGIN_ID,
    CAT_PLUGIN_ID,
    SPONGEBOB_PLUGIN_ID,
    crabVisible,
    crabInputRect,
    crabSeatRect,
  }
}