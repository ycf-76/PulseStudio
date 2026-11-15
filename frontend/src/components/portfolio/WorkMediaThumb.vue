<template>
  <div ref="rootRef" class="wmt" :class="[`wmt-${info.kind}`, { 'wmt-thumb': thumb }]">
    <img
      v-if="info.kind === 'svg' && info.svgUrl"
      class="wmt-img wmt-svg"
      :src="info.svgUrl"
      :alt="title || '矢量图'"
      loading="lazy"
    />

    <img
      v-else-if="showImage"
      class="wmt-img"
      :src="imageSrc"
      :alt="title || '封面'"
      loading="lazy"
      @error="onImageError"
    />

    <div v-else-if="info.previewHtml && info.kind !== 'video' && htmlMounted" class="wmt-html">
      <div ref="scalerRef" class="wmt-html-scaler">
        <iframe :srcdoc="info.previewHtml" sandbox="allow-same-origin" scrolling="no" tabindex="-1" />
      </div>
    </div>

    <template v-else-if="info.kind === 'video' && !thumb">
      <video class="wmt-img" :src="info.videoUrl" preload="metadata" muted playsinline />
    </template>

    <div v-else-if="info.kind === 'video'" class="wmt-icon">
      <Play :size="26" fill="currentColor" />
    </div>

    <div v-else-if="info.kind === 'audio'" class="wmt-icon">
      <Music :size="26" />
      <span class="wmt-icon-text">音频</span>
    </div>

    <div v-else-if="info.kind === 'html'" class="wmt-icon">
      <Code2 :size="26" />
      <span class="wmt-icon-text">网页</span>
    </div>

    <div v-else-if="info.kind === 'text'" class="wmt-text">{{ info.snippet || '纯文本作品' }}</div>

    <div v-else class="wmt-icon">
      <FileText :size="26" />
      <span class="wmt-icon-text">暂无预览</span>
    </div>

    <span v-if="info.kind === 'video' && (showImage || !thumb)" class="wmt-play">
      <Play :size="18" fill="currentColor" />
    </span>

    <span class="wmt-kind" :class="`wmt-kind-${info.kind}`">{{ info.label }}</span>
    <span v-if="info.pageCount > 1" class="wmt-count">{{ info.pageCount }} 页</span>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Code2, FileText, Music, Play } from 'lucide-vue-next'
import type { WorkMediaInfo } from './workMedia'

const NATIVE_W = 1080
const NATIVE_H = 1440

const props = defineProps<{
  info: WorkMediaInfo
  title?: string
  /** 列表缩略图模式：视频优先用封面图（避免一屏几十个 <video> 拉流），并隐藏角标 */
  thumb?: boolean
}>()

const imageSrc = ref(props.info.coverUrl)
const scalerRef = ref<HTMLElement | null>(null)
const rootRef = ref<HTMLElement | null>(null)
// 列表缩略图默认懒挂载 iframe：草稿页可能几十条，一次性渲染全部 1080x1440 HTML 会卡顿
const htmlMounted = ref(!props.thumb)
let resizeObserver: ResizeObserver | null = null
let intersectionObserver: IntersectionObserver | null = null

const showImage = computed(() => {
  if (!imageSrc.value) return false
  if (props.info.kind === 'video') return !!props.thumb && !!props.info.coverUrl
  return true
})

watch(
  () => props.info.coverUrl,
  (url) => {
    imageSrc.value = url
  },
)

function onImageError(event: Event) {
  const img = event.target as HTMLImageElement
  const original = props.info.coverUrl
  if (!original) return
  if (!img.src.includes('/api/proxy/image')) {
    img.src = '/api/proxy/image?url=' + encodeURIComponent(original)
    return
  }
  img.style.display = 'none'
  imageSrc.value = ''
}

function applyScale() {
  const scaler = scalerRef.value
  const host = scaler?.parentElement
  if (!scaler || !host) return
  const scale = host.clientWidth / NATIVE_W
  scaler.style.width = `${NATIVE_W}px`
  scaler.style.height = `${NATIVE_H}px`
  scaler.style.transform = `scale(${scale})`
  scaler.style.transformOrigin = 'top left'
}

function observeHost() {
  resizeObserver?.disconnect()
  const host = scalerRef.value?.parentElement
  if (!host) return
  resizeObserver = new ResizeObserver(applyScale)
  resizeObserver.observe(host)
  applyScale()
}

function mountHtml() {
  intersectionObserver?.disconnect()
  intersectionObserver = null
  if (htmlMounted.value) return
  htmlMounted.value = true
  nextTick(observeHost)
}

/** 缩略图模式下，仅当缩略图接近可视区域时才挂载 HTML 预览 */
function watchVisibility() {
  if (htmlMounted.value || !props.info.previewHtml) return
  if (typeof IntersectionObserver === 'undefined') {
    mountHtml()
    return
  }
  intersectionObserver?.disconnect()
  intersectionObserver = new IntersectionObserver(
    (entries) => {
      if (entries.some((entry) => entry.isIntersecting)) mountHtml()
    },
    { rootMargin: '240px' },
  )
  if (rootRef.value) intersectionObserver.observe(rootRef.value)
}

watch(
  () => props.info.previewHtml,
  async () => {
    watchVisibility()
    await nextTick()
    observeHost()
  },
)

onMounted(() => {
  watchVisibility()
  observeHost()
})
onBeforeUnmount(() => {
  resizeObserver?.disconnect()
  intersectionObserver?.disconnect()
})
</script>

<style scoped>
.wmt {
  position: relative;
  width: 100%;
  height: 100%;
  overflow: hidden;
  background: #F5F5F5;
  display: flex;
  align-items: center;
  justify-content: center;
}

.wmt-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  object-position: center top;
  display: block;
}

.wmt-svg {
  object-fit: contain;
  object-position: center;
  background: #FFFFFF;
}

.wmt-html { position: absolute; inset: 0; overflow: hidden; background: #F5F5F5; }
.wmt-html-scaler { position: absolute; top: 0; left: 0; }
.wmt-html-scaler iframe { width: 1080px; height: 1440px; border: none; display: block; background: #F5F5F5; pointer-events: none; }

.wmt-icon {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  color: #C4C9D4;
  background: linear-gradient(135deg, #F7F7F8, #EFEFF1);
  width: 100%;
  height: 100%;
  justify-content: center;
}
.wmt-icon-text { font-size: 12px; color: #9A9A9A; }

.wmt-text {
  width: 100%;
  height: 100%;
  padding: 14px;
  font-size: 12px;
  line-height: 1.6;
  color: #4B4B4B;
  background: #FBFBFC;
  overflow: hidden;
  display: -webkit-box;
  -webkit-line-clamp: 8;
  -webkit-box-orient: vertical;
  word-break: break-all;
}

.wmt-play {
  position: absolute;
  left: 50%;
  top: 50%;
  transform: translate(-50%, -50%);
  width: 40px;
  height: 40px;
  border-radius: 50%;
  background: rgba(0, 0, 0, 0.55);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  pointer-events: none;
}

.wmt-kind {
  position: absolute;
  top: 6px;
  left: 6px;
  padding: 1px 6px;
  border-radius: 6px;
  font-size: 10px;
  font-weight: 500;
  color: #fff;
  background: rgba(0, 0, 0, 0.5);
  backdrop-filter: blur(6px);
}
.wmt-kind-image_text { background: rgba(255, 36, 66, 0.9); }
.wmt-kind-video { background: rgba(37, 99, 235, 0.9); }
.wmt-kind-audio { background: rgba(124, 58, 237, 0.9); }
.wmt-kind-svg { background: rgba(13, 148, 136, 0.9); }
.wmt-kind-html { background: rgba(217, 119, 6, 0.9); }
.wmt-kind-text { background: rgba(75, 75, 75, 0.85); }

.wmt-count {
  position: absolute;
  top: 6px;
  right: 6px;
  padding: 1px 6px;
  border-radius: 6px;
  font-size: 10px;
  color: #fff;
  background: rgba(0, 0, 0, 0.5);
  backdrop-filter: blur(6px);
}

/* 缩略图模式：44px 级别的小格子，隐藏角标、缩小播放键与文字 */
.wmt-thumb .wmt-kind,
.wmt-thumb .wmt-count { display: none; }

.wmt-thumb .wmt-icon { gap: 0; }
.wmt-thumb .wmt-icon svg { width: 18px; height: 18px; }
.wmt-thumb .wmt-icon-text { display: none; }

.wmt-thumb .wmt-play { width: 22px; height: 22px; }
.wmt-thumb .wmt-play svg { width: 12px; height: 12px; }

.wmt-thumb .wmt-text {
  padding: 4px 5px;
  font-size: 9px;
  line-height: 1.35;
  -webkit-line-clamp: 3;
}
</style>