<template>
  <div
    class="hc"
    role="group"
    aria-roledescription="carousel"
    aria-label="今日海报"
    @mouseenter="hovering = true"
    @mouseleave="hovering = false"
    @focusin="focused = true"
    @focusout="focused = false"
  >
    <div
      v-for="(s, i) in slides"
      :key="s.src"
      class="hc-slide"
      :class="{ 'is-active': i === active }"
      role="group"
      aria-roledescription="slide"
      :aria-label="`${i + 1} / ${slides.length}`"
    >
      <img :src="s.src" :alt="s.alt" />
    </div>

    <div class="hc-controls">
      <button class="hc-nav" aria-label="上一张" @click="step(-1)">
        <ChevronLeft :size="14" />
      </button>
      <div class="hc-dots">
        <button
          v-for="(s, i) in slides"
          :key="s.src"
          class="hc-dot"
          :class="{ 'is-active': i === active }"
          :aria-label="`第 ${i + 1} 张`"
          :aria-current="i === active"
          @click="go(i)"
        ></button>
      </div>
      <button class="hc-nav" aria-label="下一张" @click="step(1)">
        <ChevronRight :size="14" />
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import { ChevronLeft, ChevronRight } from 'lucide-vue-next'

const props = withDefaults(
  defineProps<{
    slides?: Array<{ src: string; alt: string }>
    interval?: number
  }>(),
  {
    slides: () => [
      { src: '/images/setting_images/1.jpg', alt: '今日海报' },
      { src: '/images/setting_images/6.jpg', alt: '今日海报' },
      { src: '/images/setting_images/3.jpg', alt: '创作灵感' },
    ],
    interval: 6000,
  }
)

const prefersReduced =
  typeof window !== 'undefined' && typeof window.matchMedia === 'function'
    ? window.matchMedia('(prefers-reduced-motion: reduce)').matches
    : false

const active = ref(0)
const hovering = ref(false)
const focused = ref(false)
let timer: number | undefined

function step(dir: number) {
  active.value = (active.value + dir + props.slides.length) % props.slides.length
}
function go(index: number) {
  active.value = index
}

onMounted(() => {
  if (prefersReduced) return
  timer = window.setInterval(() => {
    if (hovering.value || focused.value) return
    step(1)
  }, props.interval)
})

onUnmounted(() => {
  if (timer !== undefined) window.clearInterval(timer)
})
</script>

<style scoped>
.hc { position: relative; width: 100%; height: 100%; overflow: hidden; border-radius: inherit; background: #0B0B0C; }
.hc-slide { position: absolute; inset: 0; opacity: 0; transition: opacity .65s ease; }
.hc-slide.is-active { opacity: 1; }
.hc-slide img { width: 100%; height: 100%; object-fit: cover; display: block; }

.hc-controls {
  position: absolute;
  right: 12px;
  bottom: 12px;
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 5px;
  border-radius: 11px;
  background: rgba(12, 12, 13, .52);
  backdrop-filter: blur(14px);
  border: 1px solid rgba(255, 255, 255, .14);
}
.hc-nav {
  width: 22px;
  height: 22px;
  padding: 0;
  border: none;
  border-radius: 7px;
  background: transparent;
  color: rgba(255, 255, 255, .72);
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: background .16s, color .16s;
}
.hc-nav:hover { background: rgba(255, 255, 255, .16); color: #fff; }

.hc-dots { display: flex; align-items: center; gap: 5px; }
.hc-dot {
  width: 5px;
  height: 5px;
  padding: 0;
  border: none;
  border-radius: 3px;
  background: rgba(255, 255, 255, .34);
  cursor: pointer;
  transition: width .22s ease, background .22s ease;
}
.hc-dot.is-active { width: 16px; background: #fff; }
.hc-dot:hover { background: rgba(255, 255, 255, .72); }

@media (max-width: 640px) {
  .hc-controls { right: 10px; bottom: 10px; }
}
</style>