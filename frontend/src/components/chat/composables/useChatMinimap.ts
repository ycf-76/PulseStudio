import { ref, computed, watch, nextTick, type Ref, type ComputedRef } from 'vue'
import type { ChatMessage } from '../cell-types'

interface MinimapParticle {
  x: number
  y: number
  baseX: number
  baseY: number
  vx: number
  vy: number
  r: number
  alpha: number
  glow: number
}

/**
 * Minimap 锚点导航 — ChatView 拆出
 *
 * 用户消息在滚动容器里打 data-user-idx 锚点，minimap 按锚点渲染紫色粒子；
 * 粒子之间用半透明线串联，发送消息时粒子向四周发散。
 * 鼠标悬浮粒子时暴露 hoveredIndex，由 ChatView 渲染 DOM tooltip 显示消息预览。
 */
export function useChatMinimap(deps: {
  currentMessages: ComputedRef<ChatMessage[]>
  scrollBodyRef: Ref<HTMLElement | null>
}) {
  const { currentMessages, scrollBodyRef } = deps

  const minimapUserIndices = computed(() => {
    return currentMessages.value
      .map((m, i) => m.role === 'user' ? i : -1)
      .filter(i => i !== -1)
  })

  const minimapDots = computed(() => {
    return minimapUserIndices.value.map(idx => {
      const msg = currentMessages.value[idx]
      const text = typeof msg.content === 'string' ? msg.content : ''
      const preview = text.length > 30 ? text.slice(0, 30) + '…' : text
      return { msgIdx: idx, preview }
    })
  })

  const minimapActiveIndex = ref(-1)
  const minimapHoveredIndex = ref(-1)

  const minimapCanvasRef = ref<HTMLCanvasElement | null>(null)
  let particles: MinimapParticle[] = []
  let rafId = 0
  let canvasW = 0
  let canvasH = 0
  let dispersing = false
  let disperseStartTime = 0
  const DISPERSE_DURATION = 800

  const DOT_GAP = 14
  const DOT_R = 2.4
  const DOT_R_ACTIVE = 3.6
  const LINE_MAX_DIST = DOT_GAP + DOT_R * 2 + 4
  const PURPLE_R = 124
  const PURPLE_G = 58
  const PURPLE_B = 237
  const HIT_RADIUS = 8

  function layoutParticles() {
    const count = minimapDots.value.length
    if (count === 0) {
      particles = []
      return
    }
    const totalH = (count - 1) * DOT_GAP
    const startY = (canvasH - totalH) / 2
    const cx = canvasW / 2

    const newParticles: MinimapParticle[] = []
    for (let i = 0; i < count; i++) {
      const baseY = startY + i * DOT_GAP
      const existing = particles[i]
      if (existing) {
        existing.baseX = cx
        existing.baseY = baseY
        newParticles.push(existing)
      } else {
        newParticles.push({
          x: cx,
          y: baseY,
          baseX: cx,
          baseY: baseY,
          vx: 0,
          vy: 0,
          r: DOT_R,
          alpha: 0.2 + Math.random() * 0.1,
          glow: 0,
        })
      }
    }
    particles = newParticles
  }

  function triggerDisperse() {
    if (particles.length === 0) return
    dispersing = true
    disperseStartTime = performance.now()
    for (const p of particles) {
      const angle = Math.random() * Math.PI * 2
      const speed = 2 + Math.random() * 4
      p.vx = Math.cos(angle) * speed
      p.vy = Math.sin(angle) * speed
    }
  }

  function tick() {
    const canvas = minimapCanvasRef.value
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    ctx.clearRect(0, 0, canvasW, canvasH)

    const now = performance.now()
    const isDispersing = dispersing && (now - disperseStartTime < DISPERSE_DURATION)
    if (dispersing && !isDispersing) {
      dispersing = false
    }

    const returnForce = isDispersing ? 0.008 : 0.06
    const friction = isDispersing ? 0.96 : 0.85

    for (let i = 0; i < particles.length; i++) {
      const p = particles[i]
      p.vx += (p.baseX - p.x) * returnForce
      p.vy += (p.baseY - p.y) * returnForce
      p.vx *= friction
      p.vy *= friction
      p.x += p.vx
      p.y += p.vy

      const isActive = i === minimapActiveIndex.value
      const isHovered = i === minimapHoveredIndex.value
      const targetR = isActive ? DOT_R_ACTIVE : (isHovered ? DOT_R + 0.8 : DOT_R)
      p.r += (targetR - p.r) * 0.15
      const targetAlpha = isActive ? 0.55 : (isHovered ? 0.45 : 0.22)
      p.alpha += (targetAlpha - p.alpha) * 0.1
      const targetGlow = isActive ? 1 : (isHovered ? 0.6 : 0)
      p.glow += (targetGlow - p.glow) * 0.12
    }

    for (let i = 0; i < particles.length - 1; i++) {
      const a = particles[i]
      const b = particles[i + 1]
      const dx = a.x - b.x
      const dy = a.y - b.y
      const dist = Math.sqrt(dx * dx + dy * dy)
      if (dist < LINE_MAX_DIST) {
        const lineAlpha = (1 - dist / LINE_MAX_DIST) * 0.08
        ctx.beginPath()
        ctx.moveTo(a.x, a.y)
        ctx.lineTo(b.x, b.y)
        ctx.strokeStyle = `rgba(${PURPLE_R}, ${PURPLE_G}, ${PURPLE_B}, ${lineAlpha})`
        ctx.lineWidth = 0.8
        ctx.stroke()
      }
    }

    for (const p of particles) {
      if (p.glow > 0.05) {
        const gradient = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, p.r * 4)
        gradient.addColorStop(0, `rgba(${PURPLE_R}, ${PURPLE_G}, ${PURPLE_B}, ${0.12 * p.glow})`)
        gradient.addColorStop(1, `rgba(${PURPLE_R}, ${PURPLE_G}, ${PURPLE_B}, 0)`)
        ctx.beginPath()
        ctx.arc(p.x, p.y, p.r * 4, 0, Math.PI * 2)
        ctx.fillStyle = gradient
        ctx.fill()
      }

      ctx.beginPath()
      ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2)
      ctx.fillStyle = `rgba(${PURPLE_R}, ${PURPLE_G}, ${PURPLE_B}, ${p.alpha})`
      ctx.fill()
    }

    rafId = requestAnimationFrame(tick)
  }

  function initCanvas(canvas: HTMLCanvasElement) {
    minimapCanvasRef.value = canvas
    const parent = canvas.parentElement
    if (!parent) return
    const rect = parent.getBoundingClientRect()
    const dpr = window.devicePixelRatio || 1
    canvasW = rect.width
    canvasH = rect.height
    canvas.width = canvasW * dpr
    canvas.height = canvasH * dpr
    canvas.style.width = canvasW + 'px'
    canvas.style.height = canvasH + 'px'
    const ctx = canvas.getContext('2d')
    if (ctx) ctx.scale(dpr, dpr)
    layoutParticles()
    cancelAnimationFrame(rafId)
    tick()
  }

  function destroyCanvas() {
    cancelAnimationFrame(rafId)
    particles = []
    minimapCanvasRef.value = null
  }

  function getParticleIndexAt(canvasX: number, canvasY: number): number {
    let closest = -1
    let closestDist = Infinity
    for (let i = 0; i < particles.length; i++) {
      const p = particles[i]
      const dx = canvasX - p.x
      const dy = canvasY - p.y
      const dist = Math.sqrt(dx * dx + dy * dy)
      if (dist < HIT_RADIUS && dist < closestDist) {
        closestDist = dist
        closest = i
      }
    }
    return closest
  }

  function getParticleY(index: number): number {
    if (index < 0 || index >= particles.length) return 0
    return particles[index].y
  }

  function updateMinimapActive() {
    const el = scrollBodyRef.value
    if (!el || minimapUserIndices.value.length === 0) return
    const viewportMid = el.scrollTop + el.clientHeight / 2
    let active = -1
    const items = el.querySelectorAll('.rl-flow-item[data-user-idx]')
    items.forEach((item) => {
      const idxStr = (item as HTMLElement).dataset.userIdx
      if (idxStr === undefined || idxStr === '') return
      const idx = parseInt(idxStr, 10)
      if (isNaN(idx)) return
      const rect = item.getBoundingClientRect()
      const containerRect = el.getBoundingClientRect()
      const itemTop = rect.top - containerRect.top + el.scrollTop
      if (itemTop <= viewportMid) {
        active = idx
      }
    })
    minimapActiveIndex.value = active
  }

  function minimapScrollTo(dotIndex: number) {
    const el = scrollBodyRef.value
    if (!el) return
    const target = el.querySelector(`.rl-flow-item[data-user-idx="${dotIndex}"]`)
    if (target) {
      target.scrollIntoView({ behavior: 'smooth', block: 'center' })
    }
  }

  watch(minimapDots, () => {
    layoutParticles()
    nextTick(() => updateMinimapActive())
  })

  watch(currentMessages, () => {
    nextTick(() => updateMinimapActive())
  })

  return {
    minimapUserIndices,
    minimapDots,
    minimapActiveIndex,
    minimapHoveredIndex,
    updateMinimapActive,
    minimapScrollTo,
    minimapCanvasRef,
    initMinimapCanvas: initCanvas,
    destroyMinimapCanvas: destroyCanvas,
    triggerMinimapDisperse: triggerDisperse,
    getParticleIndexAt,
    getParticleY,
  }
}