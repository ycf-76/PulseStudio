<template>
  <div class="note-preview">
    <!-- 图片列表 -->
    <div class="np-cover" v-if="coverImages.length">
      <div class="np-gallery-wrap" :style="{ aspectRatio: npAr || undefined }">
        <button
          v-if="coverImages.length > 1 && canScrollPrev"
          class="np-gallery-arrow np-gallery-arrow-left"
          @click="scrollGallery(-1)"
        >‹</button>
        <div ref="galleryRef" class="np-gallery-track" :class="{ 'np-gallery-track--single': coverImages.length === 1 }" @scroll="onGalleryScroll">
          <div
            v-for="(img, i) in coverImages"
            :key="i"
            class="np-gallery-card"
          >
            <img
              :src="img"
              class="np-gallery-img"
              :alt="`图${i + 1}`"
              @load="onImageLoad(i, $event)"
              @error="onImageError(i, $event)"
            />
            <div v-if="imageErrors[i]" class="np-gallery-error">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="m21 15-5-5L5 21"/></svg>
              <span>加载失败</span>
            </div>
          </div>
        </div>
        <button
          v-if="coverImages.length > 1 && canScrollNext"
          class="np-gallery-arrow np-gallery-arrow-right"
          @click="scrollGallery(1)"
        >›</button>
        <div v-if="coverImages.length > 1" class="np-gallery-indicator">
          {{ currentImageIndex + 1 }} / {{ coverImages.length }}
        </div>
      </div>
    </div>

    <!-- 正文区 -->
    <div class="np-body">
      <!-- 标题 -->
      <h3 class="np-title">{{ work.title || '未命名作品' }}</h3>

      <!-- 平台 / 类型 / 等级 -->
      <div class="np-meta">
        <span class="np-meta-item" v-if="work.platform">
          <PlatformLogo :platform="work.platform" :name="platformLabel" />
          {{ platformLabel }}
        </span>
        <span class="np-meta-item" v-if="work.contentType">{{ contentTypeLabel }}</span>
        <span class="np-tier" v-if="work.performanceTier && work.performanceTier !== '-'"
          :class="'np-tier-' + work.performanceTier"
        >{{ work.performanceTier }}</span>
      </div>

      <!-- 正文内容 -->
      <div class="np-content" v-if="work.contentText">
        <div class="np-content-text" v-html="renderedContent"></div>
      </div>

      <!-- 标签 -->
      <div class="np-tags" v-if="tags.length">
        <span v-for="tag in tags" :key="tag" class="np-tag">#{{ tag }}</span>
      </div>

      <!-- 互动数据 -->
      <div class="np-metrics" v-if="hasMetrics">
        <div class="np-metric">
          <svg class="np-metric-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/></svg>
          <span class="np-metric-value">{{ fmtNum(work.likes || 0) }}</span>
          <span class="np-metric-label">点赞</span>
        </div>
        <div class="np-metric">
          <svg class="np-metric-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"/></svg>
          <span class="np-metric-value">{{ fmtNum(work.collects || 0) }}</span>
          <span class="np-metric-label">收藏</span>
        </div>
        <div class="np-metric">
          <svg class="np-metric-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
          <span class="np-metric-value">{{ fmtNum(work.comments || 0) }}</span>
          <span class="np-metric-label">评论</span>
        </div>
        <div class="np-metric">
          <svg class="np-metric-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><line x1="8.59" y1="13.51" x2="15.42" y2="17.49"/><line x1="15.41" y1="6.51" x2="8.59" y2="10.49"/></svg>
          <span class="np-metric-value">{{ fmtNum(work.shares || 0) }}</span>
          <span class="np-metric-label">分享</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, reactive, onMounted, watch } from 'vue'
import { useWorkStore, type WorkItem } from '@/stores/work'
import { renderMarkdown } from './markdown-renderer'
import PlatformLogo from './PlatformLogo.vue'

const props = defineProps<{ work: WorkItem }>()
const workStore = useWorkStore()
const galleryRef = ref<HTMLElement | null>(null)
const currentImageIndex = ref(0)
const canScrollPrev = ref(false)
const canScrollNext = ref(false)
const imageErrors = reactive<Record<number, boolean>>({})
// 预览窗宽高比（首图原始比例，'' = 未定走 CSS 默认 3/4）
const npAr = ref('')

const coverImages = computed(() => {
  const imgs = props.work.images?.filter(Boolean) || []
  if (imgs.length > 0) return imgs
  if (props.work.coverUrl) return [props.work.coverUrl]
  return []
})

const platformLabel = computed(() => workStore.getPlatformLabel(props.work.platform))
const contentTypeLabel = computed(() => workStore.getContentTypeLabel(props.work.contentType))

const tags = computed(() => {
  const raw = props.work.tags
  if (Array.isArray(raw)) return raw.filter(Boolean)
  return []
})

const renderedContent = computed(() => {
  const text = props.work.contentText || ''
  if (!text) return ''
  return renderMarkdown(text)
})

const hasMetrics = computed(() =>
  props.work.likes || props.work.collects || props.work.comments || props.work.shares
)

function fmtNum(n: number): string {
  if (n >= 10000) return (n / 10000).toFixed(1) + 'w'
  if (n >= 1000) return (n / 1000).toFixed(1) + 'k'
  return String(n)
}

function scrollGallery(dir: number) {
  const el = galleryRef.value
  if (!el) return
  el.scrollBy({ left: dir * el.clientWidth * 0.8, behavior: 'smooth' })
}

function onGalleryScroll() {
  const el = galleryRef.value
  if (!el) return
  canScrollPrev.value = el.scrollLeft > 2
  canScrollNext.value = el.scrollLeft + el.clientWidth < el.scrollWidth - 2
  const first = el.firstElementChild as HTMLElement | null
  const cardWidth = first ? first.offsetWidth + 8 : 1
  currentImageIndex.value = Math.round(el.scrollLeft / cardWidth)
}

function onImageLoad(index: number, e: Event) {
  imageErrors[index] = false
  // 2026-11-05：预览窗比例自适应——取首张加载成功的图片原始宽高比
  // （夹 0.7~1.9），窗口整图显示不再裁成窄条
  if (!npAr.value) {
    const img = e.target as HTMLImageElement
    if (img.naturalWidth && img.naturalHeight) {
      const ar = Math.min(1.9, Math.max(0.7, img.naturalWidth / img.naturalHeight))
      npAr.value = String(ar)
    }
  }
}

function onImageError(index: number, _e: Event) {
  imageErrors[index] = true
}

onMounted(() => {
  canScrollNext.value = coverImages.value.length > 1
})

watch(() => props.work?.id, () => { npAr.value = '' })
</script>

<style scoped>
.note-preview {
  display: flex;
  flex-direction: column;
  min-height: 100%;
  overflow-y: auto;
}
.note-preview::-webkit-scrollbar { width: 4px; }
.note-preview::-webkit-scrollbar-thumb { background: rgba(0,0,0,0.12); border-radius:4px; }

/* === 图片画廊 ===
   2026-11-05 重做：全宽大窗 + 比例自适应（首图原始比例注入 --np-ar），
   一屏一图整屏翻页，整图显示不再裁成 120x160 窄条（大哥反馈）。
   晚间补充：高度封顶 min(48vh, 420px) + 宽度随比例收缩 + 居中——
   超大图在小屏下不再撑满整个详情页，任意尺寸都完整、等比、居中呈现 */
.np-cover { flex-shrink: 0; }
.np-gallery-wrap {
  position: relative;
  width: 100%;
  max-width: calc(min(48vh, 420px) * var(--np-ar, 0.75));
  aspect-ratio: var(--np-ar, 3 / 4);
  max-height: min(48vh, 420px);
  margin: 0 auto;
  background: var(--rl-bg-2);
  border-radius:12px;
  overflow: hidden;
}
.np-gallery-track {
  display: flex;
  gap: 0;
  overflow-x: auto;
  padding: 0;
  height: 100%;
  scroll-snap-type: x mandatory;
  scrollbar-width: none;
}
.np-gallery-track::-webkit-scrollbar { display: none; }
.np-gallery-card {
  flex: 0 0 100%;
  width: 100%;
  height: 100%;
  overflow: hidden;
  scroll-snap-align: start;
  background: var(--rl-card);
  position: relative;
}
.np-gallery-img {
  width: 100%;
  height: 100%;
  object-fit: contain;
  display: block;
}
.np-gallery-error {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 4px;
  color: #bbb;
  font-size: 10px;
  background: #f5f5f5;
}
.np-gallery-arrow {
  position: absolute;
  top: 50%;
  transform: translateY(-50%);
  width: 26px;
  height: 26px;
  border-radius: 50%;
  border: none;
  background: rgba(255,255,255,0.92);
  box-shadow: 0 2px 6px rgba(0,0,0,0.15);
  color: #333;
  font-size: 18px;
  font-weight: bold;
  cursor: pointer;
  z-index: 2;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: background 0.15s;
}
.np-gallery-arrow:hover { background: #fff; }
.np-gallery-arrow-left { left: 6px; }
.np-gallery-arrow-right { right: 6px; }
.np-gallery-indicator {
  position: absolute;
  bottom: 10px;
  left: 50%;
  transform: translateX(-50%);
  background: rgba(0,0,0,0.5);
  color: #fff;
  font-size: 10px;
  padding: 2px 8px;
  border-radius:14px;
  pointer-events: none;
}

/* === 正文区 === */
.np-body {
  padding: 14px 12px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.np-title {
  font-size: 15px;
  font-weight: 600;
  color: #1a1a1a;
  margin: 0;
  line-height: 1.45;
  text-align: center;
}
.np-meta {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  flex-wrap: wrap;
}
.np-meta-item {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
  color: #888;
}
.np-tier {
  font-size: 10px;
  font-weight: 700;
  padding: 1px 5px;
  border-radius:8px;
  line-height: 1.4;
}
.np-tier-S { color: #ef4444; background: rgba(239,68,68,0.08); }
.np-tier-A { color: #f97316; background: rgba(249,115,22,0.08); }
.np-tier-B { color: #eab308; background: rgba(234,179,8,0.08); }
.np-tier-C { color: #6b7280; background: rgba(107,114,128,0.08); }

.np-content {
  font-size: 13px;
  line-height: 1.75;
  color: #333;
  /* 长段落用两端对齐：中文等宽字符下段落呈整齐矩形，比居中/居左都干净 */
  text-align: justify;
}
.np-content-text :deep(p) { margin: 0 0 8px; }
.np-content-text :deep(p:last-child) { margin-bottom: 0; }

.np-tags {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  justify-content: center;
}
.np-tag {
  font-size: 11px;
  color: #3b82f6;
  background: rgba(59,130,246,0.06);
  padding: 2px 7px;
  border-radius:8px;
}

/* === 互动数据 === */
.np-metrics {
  display: flex;
  gap: 0;
  padding-top: 10px;
  border-top: 1px solid rgba(0,0,0,0.06);
}
.np-metric {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
  padding: 4px 0;
}
.np-metric-icon {
  width: 16px;
  height: 16px;
  color: #999;
  margin-bottom: 1px;
}
.np-metric-value {
  font-size: 13px;
  font-weight: 600;
  color: #1a1a1a;
  line-height: 1.2;
}
.np-metric-label {
  font-size: 10px;
  color: #999;
}
</style>