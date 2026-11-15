<template>
  <div
    class="crab-companion"
    :class="{
      'crab-visible': visible,
      'crab-hiding': isHiding,
      'crab-dragging': isDragging,
      'crab-peeks': isPeeking,
    }"
    :style="crabStyle"
    @mousedown.prevent="onDragStart"
    @touchstart.prevent="onTouchStart"
    @mouseenter="onMouseEnter"
    @mouseleave="onMouseLeave"
    @click="onCrabClick"
  >
    <svg
      class="crab-svg"
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 200 180"
    >
      <g class="crab-body">
        <path d="M52,86 Q36,76 30,68" stroke="#D94F44" stroke-width="11" stroke-linecap="round" fill="none"/>
        <path d="M148,86 Q164,76 170,68" stroke="#D94F44" stroke-width="11" stroke-linecap="round" fill="none"/>
        <g>
          <ellipse cx="26" cy="56" rx="18" ry="11" transform="rotate(-45 26 56)" fill="#E8655A"/>
          <ellipse cx="42" cy="80" rx="16" ry="10" transform="rotate(15 42 80)" fill="#E8655A"/>
        </g>
        <g>
          <ellipse cx="174" cy="56" rx="18" ry="11" transform="rotate(45 174 56)" fill="#E8655A"/>
          <ellipse cx="158" cy="80" rx="16" ry="10" transform="rotate(-15 158 80)" fill="#E8655A"/>
        </g>
        <ellipse cx="100" cy="105" rx="58" ry="48" fill="#E8655A"/>
        <path d="M55,110 Q30,118 22,134" stroke="#E8655A" stroke-width="7" stroke-linecap="round" fill="none"/>
        <path d="M52,125 Q28,136 18,154" stroke="#E8655A" stroke-width="7" stroke-linecap="round" fill="none"/>
        <path d="M62,138 Q48,152 42,170" stroke="#E8655A" stroke-width="7" stroke-linecap="round" fill="none"/>
        <path d="M145,110 Q170,118 178,134" stroke="#E8655A" stroke-width="7" stroke-linecap="round" fill="none"/>
        <path d="M148,125 Q172,136 182,154" stroke="#E8655A" stroke-width="7" stroke-linecap="round" fill="none"/>
        <path d="M138,138 Q152,152 158,170" stroke="#E8655A" stroke-width="7" stroke-linecap="round" fill="none"/>
        <circle cx="30" cy="68" r="8" fill="#D94F44"/>
        <circle cx="170" cy="68" r="8" fill="#D94F44"/>
      </g>

      <g class="crab-eyes">
        <circle cx="78" cy="62" r="16" fill="white"/>
        <circle cx="122" cy="62" r="16" fill="white"/>
        <g class="crab-pupil-left" :style="leftPupilStyle">
          <circle cx="80" cy="62" r="8" fill="#1a1a1a"/>
          <circle cx="83" cy="59" r="3" fill="white"/>
        </g>
        <g class="crab-pupil-right" :style="rightPupilStyle">
          <circle cx="120" cy="62" r="8" fill="#1a1a1a"/>
          <circle cx="123" cy="59" r="3" fill="white"/>
        </g>
      </g>

      <g class="crab-blink" v-if="isBlinking">
        <ellipse cx="78" cy="62" rx="16" ry="2" fill="#E8655A"/>
        <ellipse cx="122" cy="62" rx="16" ry="2" fill="#E8655A"/>
      </g>
    </svg>

    <div
      class="crab-speech-bubble"
      v-if="speechText"
      :class="{ 'crab-speech-bubble--note': speechIsNote }"
      @click.stop="onSpeechClick"
    >
      <span>{{ speechText }}</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useCrabNotes } from './useCrabNotes'
import type { CrabNoteTrigger } from './crabNoteRules'

type CrabRect = { left: number; top: number; right: number; bottom: number }

const props = defineProps<{
  show: boolean
  inputRect: DOMRect | null
  seatRect: DOMRect | null
  /** 流式回复中：此时不冒便签气泡，避免和「思考中」抢注意力 */
  isStreaming?: boolean
  /** 便签面板开着：所有自动冒泡静默，防止气泡被面板遮住还照常消耗冷却 */
  panelOpen?: boolean
}>()

const emit = defineEmits<{
  (e: 'toggle-notes', rect: CrabRect): void
}>()

const notesApi = useCrabNotes()

const visible = ref(false)
const isHiding = ref(false)
const isHovered = ref(false)
const isBlinking = ref(false)
const isDragging = ref(false)
const isPeeking = ref(false)
const speechText = ref('')
const speechIsNote = ref(false)

const mouseX = ref(0)
const mouseY = ref(0)

const CRAB_SIZE = 52

const dragX = ref<number | null>(null)
const dragY = ref<number | null>(null)
const isSnapped = ref(true)

let dragOffsetX = 0
let dragOffsetY = 0
let hasDragged = false

const CRAB_H = CRAB_SIZE * 0.9
// 必须与 <style> 里的 --crab-rot 保持一致，位置计算依赖这个角度。
// 正值 = 顺时针：右钳下沉、左钳抬起，朝着输入卡右上角内侧压下去，
// 看起来像用右钳扣住卡片边角。负值会变成仰着往左上滑，趴在右上角时很别扭。
const CRAB_ROT = 10

// viewBox 是 200×180，但画面并没有铺满：钳子最高到 y≈41，腿尖到 y≈173.5，
// 左右钳各到 x≈11 / x≈189。旋转后可见内容的外接矩形才是用户眼里的「螃蟹」，
// 位置必须以它的边角对齐输入卡，否则算出来的框会带上透明留白，看起来悬空。
const ART_LEFT = 11
const ART_RIGHT = 189
const ART_TOP = 41
const ART_BOTTOM = 173.5

const SX = CRAB_SIZE / 200
const SY = CRAB_H / 180

const ART_W = (ART_RIGHT - ART_LEFT) * SX
const ART_H = (ART_BOTTOM - ART_TOP) * SY

// 可见内容的几何中心相对盒子中心的偏移（盒子未旋转时）
const REL_X = ((ART_LEFT + ART_RIGHT) / 2) * SX - CRAB_SIZE / 2
const REL_Y = ((ART_TOP + ART_BOTTOM) / 2) * SY - CRAB_H / 2

// 盒子绕自身中心旋转后，这个中心也跟着转
const RAD = (CRAB_ROT * Math.PI) / 180
const BODY_SHIFT_X = REL_X * Math.cos(RAD) - REL_Y * Math.sin(RAD)
const BODY_SHIFT_Y = REL_X * Math.sin(RAD) + REL_Y * Math.cos(RAD)

// 旋转后可见内容外接矩形的尺寸（用 |cos|/|sin| 的极值公式）
const ROT_W = ART_W * Math.abs(Math.cos(RAD)) + ART_H * Math.abs(Math.sin(RAD))
const ROT_H = ART_W * Math.abs(Math.sin(RAD)) + ART_H * Math.abs(Math.cos(RAD))

// 外接矩形右下角相对盒子左上角的偏移
const CORNER_RX = CRAB_SIZE / 2 + BODY_SHIFT_X + ROT_W / 2
const CORNER_RY = CRAB_H / 2 + BODY_SHIFT_Y + ROT_H / 2

// 归位：让可见内容的右下角压在输入卡右上角上。
// Y：正数表示「往下沉」——腿尖越过卡片上沿沉进卡片里，被卡片盖住一截。
//    这样视觉上是螃蟹站在/藏在输入框上沿，而不是飘在输入框头顶。
//    只沉几条腿，眼睛和双钳仍在边框之上，保持「明显可见」。
// X：-1.5 表示右钳略微越过卡片右边缘，把右上角「咬」住 —— .rl-chat 右边界
//    还比卡片宽 70px，越出这一两个像素不会被 overflow-x:hidden 裁掉。
// 注：可见像素的右下角比上面算出的几何包围盒右下角内收约 2.5px（包围盒的角上
//    其实是空的，钳子和腿够不到那个尖角），所以这里要扣掉这段才能真正贴到边框。
const HOME_INSET_X = -1.5
const HOME_INSET_Y = 20

function homeScreenPos() {
  const rect = props.inputRect
  if (!rect) return null
  return {
    x: rect.right - HOME_INSET_X - CORNER_RX,
    y: rect.top + HOME_INSET_Y - CORNER_RY,
  }
}

const crabStyle = computed(() => {
  if (!props.show) return { display: 'none' }

  const seatLeft = props.seatRect ? props.seatRect.left : 0
  const seatTop = props.seatRect ? props.seatRect.top : 0

  const pos = isSnapped.value || dragX.value === null || dragY.value === null
    ? homeScreenPos()
    : { x: dragX.value, y: dragY.value }

  if (!pos) return { display: 'none' }

  return {
    position: 'absolute' as const,
    left: `${pos.x - seatLeft}px`,
    top: `${pos.y - seatTop}px`,
    width: `${CRAB_SIZE}px`,
    height: `${CRAB_H}px`,
    zIndex: isSnapped.value ? 0 : 9999,
    overflow: 'visible' as const,
  }
})

const leftPupilStyle = computed(() => {
  const { dx, dy } = calcEyeOffset(78, 62)
  return { transform: `translate(${dx}px, ${dy}px)` }
})

const rightPupilStyle = computed(() => {
  const { dx, dy } = calcEyeOffset(122, 62)
  return { transform: `translate(${dx}px, ${dy}px)` }
})

function getCrabScreenPos() {
  if (!isSnapped.value && dragX.value !== null && dragY.value !== null) {
    return { x: dragX.value, y: dragY.value }
  }
  return homeScreenPos() ?? { x: 0, y: 0 }
}

/** 面板锚点：螃蟹盒子的屏幕矩形，面板据此决定往上还是往下展开 */
function crabRect(): CrabRect {
  const pos = getCrabScreenPos()
  return { left: pos.x, top: pos.y, right: pos.x + CRAB_SIZE, bottom: pos.y + CRAB_H }
}

function calcEyeOffset(eyeCx: number, eyeCy: number) {
  const pos = getCrabScreenPos()
  const scale = CRAB_SIZE / 200
  const eyeScreenX = pos.x + eyeCx * scale
  const eyeScreenY = pos.y + eyeCy * scale

  const diffX = mouseX.value - eyeScreenX
  const diffY = mouseY.value - eyeScreenY
  const dist = Math.sqrt(diffX * diffX + diffY * diffY)
  const maxShift = 4.5

  if (dist < 1) return { dx: 0, dy: 0 }

  const shift = Math.min(maxShift, dist * 0.025)
  const dx = (diffX / dist) * shift
  const dy = (diffY / dist) * shift

  return { dx, dy }
}

function onDragStart(e: MouseEvent) {
  hasDragged = false
  isDragging.value = true
  isSnapped.value = false
  const pos = getCrabScreenPos()
  dragOffsetX = e.clientX - pos.x
  dragOffsetY = e.clientY - pos.y
  dragX.value = pos.x
  dragY.value = pos.y
  document.addEventListener('mousemove', onDragMove)
  document.addEventListener('mouseup', onDragEnd)
}

let dragRafId = 0
function onDragMove(e: MouseEvent) {
  if (!isDragging.value) return
  hasDragged = true
  const cx = e.clientX - dragOffsetX
  const cy = e.clientY - dragOffsetY
  cancelAnimationFrame(dragRafId)
  dragRafId = requestAnimationFrame(() => {
    dragX.value = cx
    dragY.value = cy
  })
}

function onDragEnd(e: MouseEvent) {
  cancelAnimationFrame(dragRafId)
  isDragging.value = false
  document.removeEventListener('mousemove', onDragMove)
  document.removeEventListener('mouseup', onDragEnd)
  if (hasDragged) {
    snapIfNearInput()
  }
}

function onTouchStart(e: TouchEvent) {
  hasDragged = false
  const touch = e.touches[0]
  isDragging.value = true
  isSnapped.value = false
  const pos = getCrabScreenPos()
  dragOffsetX = touch.clientX - pos.x
  dragOffsetY = touch.clientY - pos.y
  dragX.value = pos.x
  dragY.value = pos.y
  document.addEventListener('touchmove', onTouchMove, { passive: false })
  document.addEventListener('touchend', onTouchEnd)
}

let touchRafId = 0
function onTouchMove(e: TouchEvent) {
  e.preventDefault()
  if (!isDragging.value) return
  hasDragged = true
  const touch = e.touches[0]
  const cx = touch.clientX - dragOffsetX
  const cy = touch.clientY - dragOffsetY
  cancelAnimationFrame(touchRafId)
  touchRafId = requestAnimationFrame(() => {
    dragX.value = cx
    dragY.value = cy
  })
}

function onTouchEnd() {
  cancelAnimationFrame(touchRafId)
  isDragging.value = false
  document.removeEventListener('touchmove', onTouchMove)
  document.removeEventListener('touchend', onTouchEnd)
  if (hasDragged) {
    snapIfNearInput()
  }
}

function snapIfNearInput() {
  if (dragX.value === null || dragY.value === null) return
  const home = homeScreenPos()
  if (!home) return

  if (Math.abs(dragX.value - home.x) < 120 && Math.abs(dragY.value - home.y) < 120) {
    isSnapped.value = true
    dragX.value = null
    dragY.value = null
    showSpeech('到家了~')
  }
}

let blinkTimer: ReturnType<typeof setInterval> | null = null
let speechTimer: ReturnType<typeof setTimeout> | null = null
let peekTimer: ReturnType<typeof setTimeout> | null = null
let hideTimer: ReturnType<typeof setTimeout> | null = null

function startBlinking() {
  blinkTimer = setInterval(() => {
    if (Math.random() < 0.3) {
      isBlinking.value = true
      setTimeout(() => { isBlinking.value = false }, 150)
    }
  }, 2000)
}

function stopBlinking() {
  if (blinkTimer) {
    clearInterval(blinkTimer)
    blinkTimer = null
  }
}

function startPeeking() {
  const doPeek = () => {
    if (!visible.value || isDragging.value) {
      scheduleNext()
      return
    }
    isPeeking.value = true
    peekTimer = setTimeout(() => {
      isPeeking.value = false
      scheduleNext()
    }, 1500 + Math.random() * 2000)
  }
  const scheduleNext = () => {
    peekTimer = setTimeout(doPeek, 4000 + Math.random() * 6000)
  }
  scheduleNext()
}

function stopPeeking() {
  if (peekTimer) {
    clearTimeout(peekTimer)
    peekTimer = null
  }
  isPeeking.value = false
}

const hoverPhrases = [
  '嘿！别碰我~',
  '我在角落看着你呢！',
  '躲好了，别拉我出来~',
  '嘘，我在偷听你打字...',
  '别看我，看输入框！',
  '你拖我干嘛！',
  '角落里很舒服的~',
]

const dragPhrases = [
  '放开我！',
  '我要回角落！',
  '你把我拖哪去？',
  '我腿多但跑不快啊！',
]

/** 自动冒泡的运行时事实，每次触发时现取，保证拿到的是当下状态 */
function showFacts() {
  return {
    isPanelOpen: !!props.panelOpen,
    isStreaming: !!props.isStreaming,
    heroMode: false,
  }
}

/**
 * 在当前时机下尝试冒一条便签气泡。
 * 返回 false 表示没有合适的便签（被冷却/免打扰/开关拦住），
 * 调用方据此决定要不要退回到原本的随机闲聊，保证「有便签就提醒、没便签不冷场」。
 */
function tryShowNote(source: CrabNoteTrigger): boolean {
  if (!props.show || !visible.value) return false
  const note = notesApi.pick(source, showFacts())
  if (!note) return false
  notesApi.markShown(note)
  showSpeech(`${note.emoji} ${note.text}`, { duration: 9000, isNote: true })
  return true
}

function onMouseEnter() {
  isHovered.value = true
  if (tryShowNote('hover')) return
  const msg = hoverPhrases[Math.floor(Math.random() * hoverPhrases.length)]
  showSpeech(msg)
}

function onMouseLeave() {
  isHovered.value = false
}

function onCrabClick() {
  if (hasDragged) {
    hasDragged = false
    return
  }
  dismissSpeech()
  emit('toggle-notes', crabRect())
}

function showSpeech(text: string, opts?: { duration?: number; isNote?: boolean }) {
  speechText.value = text
  speechIsNote.value = !!opts?.isNote
  if (speechTimer) clearTimeout(speechTimer)
  speechTimer = setTimeout(() => {
    speechText.value = ''
    speechIsNote.value = false
  }, opts?.duration ?? 2500)
}

function dismissSpeech() {
  speechText.value = ''
  speechIsNote.value = false
  if (speechTimer) {
    clearTimeout(speechTimer)
    speechTimer = null
  }
}

function onSpeechClick() {
  if (speechIsNote.value) {
    dismissSpeech()
    emit('toggle-notes', crabRect())
    return
  }
  dismissSpeech()
}

function onMouseMove(e: MouseEvent) {
  mouseX.value = e.clientX
  mouseY.value = e.clientY
}

const greetings = [
  '开始聊天啦~',
  '我来帮你盯着输入框！',
  '有什么想说的？',
  '我在这儿呢~',
  '悄悄冒个头~',
]

function onInputFocus() {
  if (props.panelOpen) return
  if (tryShowNote('input_focus')) return
  if (Math.random() < 0.4) {
    const msg = greetings[Math.floor(Math.random() * greetings.length)]
    showSpeech(msg)
  }
}

function onWindowFocusIn(e: FocusEvent) {
  const target = e.target as HTMLElement
  if (target?.tagName === 'TEXTAREA' || target?.tagName === 'INPUT') {
    onInputFocus()
  }
}

/* 流式开始 = 新一轮开始（清零每轮冒泡配额）；
   流式结束 = 本轮结束，延迟 600ms 再冒泡，等界面把最后一条消息渲染完，
   否则气泡会出现在「正在生成」的残影旁边，看起来像抢答。 */
let turnEndTimer: ReturnType<typeof setTimeout> | null = null

watch(() => props.isStreaming, (now, prev) => {
  if (now && !prev) {
    notesApi.beginTurn()
    return
  }
  if (!now && prev) {
    if (turnEndTimer) clearTimeout(turnEndTimer)
    turnEndTimer = setTimeout(() => {
      turnEndTimer = null
      tryShowNote('turn_end')
    }, 600)
  }
})

watch(() => props.show, (newVal) => {
  if (newVal) {
    hideTimer && clearTimeout(hideTimer)
    hideTimer = null
    isHiding.value = false
    setTimeout(() => { visible.value = true }, 50)
    startBlinking()
    startPeeking()
  } else {
    hideTimer = setTimeout(() => {
      if (!props.show) {
        isHiding.value = true
        stopBlinking()
        stopPeeking()
        setTimeout(() => {
          visible.value = false
          isHiding.value = false
        }, 400)
      }
    }, 3000)
  }
}, { immediate: true })

watch(isDragging, (val) => {
  if (val) {
    const msg = dragPhrases[Math.floor(Math.random() * dragPhrases.length)]
    showSpeech(msg)
  }
})

onMounted(() => {
  window.addEventListener('mousemove', onMouseMove)
  window.addEventListener('focusin', onWindowFocusIn)
  notesApi.load()
})

onUnmounted(() => {
  window.removeEventListener('mousemove', onMouseMove)
  window.removeEventListener('focusin', onWindowFocusIn)
  stopBlinking()
  stopPeeking()
  if (speechTimer) clearTimeout(speechTimer)
  if (hideTimer) clearTimeout(hideTimer)
  if (turnEndTimer) clearTimeout(turnEndTimer)
})
</script>

<style scoped>
.crab-companion {
  pointer-events: none;
  opacity: 0;
  /* 倾斜角只在这里定义，脚本里的 CRAB_ROT 必须与它同步，位置计算依赖这个值。 */
  --crab-rot: 10deg;
  transform: scale(0.7) rotate(var(--crab-rot));
  transition: opacity 0.5s cubic-bezier(0.2, 0.8, 0.2, 1),
              transform 0.5s cubic-bezier(0.2, 0.8, 0.2, 1);
}

.crab-visible {
  opacity: 1;
  transform: scale(1) rotate(var(--crab-rot));
  pointer-events: auto;
  cursor: grab;
}

.crab-visible:active {
  cursor: grabbing;
}

.crab-hiding {
  opacity: 0;
  transform: scale(0.8) rotate(var(--crab-rot));
}

.crab-dragging {
  z-index: 9999 !important;
  transform: scale(1.1) rotate(3deg);
  transition: transform 0.15s ease;
}

.crab-peeks {
  animation: crab-peek 1.5s ease-in-out;
}

@keyframes crab-peek {
  0%   { transform: scale(1) rotate(var(--crab-rot)); }
  30%  { transform: scale(1.08) rotate(4deg); }
  70%  { transform: scale(1.08) rotate(4deg); }
  100% { transform: scale(1) rotate(var(--crab-rot)); }
}

.crab-svg {
  width: 100%;
  height: 100%;
  overflow: visible;
}

.crab-eyes {
  transition: transform 0.12s ease-out;
}

.crab-blink {
  transition: none;
}

/* 气泡向右对齐而不是居中：螃蟹归位在输入卡右上角，离 .rl-chat 右边界只有几个像素，
   居中的气泡（white-space:nowrap）会有一半被 .rl-chat 的 overflow-x:hidden 切掉。
   右对齐后气泡从螃蟹右边缘向左展开，永远落在可视区内。 */
.crab-speech-bubble {
  position: absolute;
  bottom: 100%;
  right: 0;
  transform: translateY(-6px);
  background: var(--rl-bg, #fff);
  color: var(--rl-text-1, #1a1a1a);
  border: 1px solid var(--rl-border, #e5e5e5);
  border-radius:14px;
  padding: 5px 12px;
  font-size: 12px;
  font-weight: 500;
  white-space: nowrap;
  pointer-events: auto;
  cursor: pointer;
  box-shadow: 0 2px 10px rgba(0,0,0,0.08);
  animation: crab-bubble-in 0.3s cubic-bezier(0.2, 0.8, 0.2, 1);
  z-index: 100;
}

/* 便签气泡：内容可能是一整句话，不能沿用闲聊气泡的 nowrap，否则会横向冲出可视区。
   宽度按内容撑开并封顶 216px，超出自动换行；仍然右对齐，向左展开。 */
.crab-speech-bubble--note {
  white-space: normal;
  width: max-content;
  max-width: 216px;
  text-align: left;
  line-height: 1.5;
  padding: 8px 11px;
  border-radius: 12px;
  border-left: 3px solid var(--rl-accent, #534ab7);
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.12);
}

/* 小尖角指向螃蟹身体中心（身体中心距盒子右边缘约 26px），而不是气泡中心 */
.crab-speech-bubble::after {
  content: '';
  position: absolute;
  top: 100%;
  right: 20px;
  border: 5px solid transparent;
  border-top-color: var(--rl-bg, #fff);
}

.crab-speech-bubble::before {
  content: '';
  position: absolute;
  top: 100%;
  right: 20px;
  border: 6px solid transparent;
  border-top-color: var(--rl-border, #e5e5e5);
}

@keyframes crab-bubble-in {
  from {
    opacity: 0;
    transform: translateY(4px) scale(0.85);
  }
  to {
    opacity: 1;
    transform: translateY(-6px) scale(1);
  }
}
</style>