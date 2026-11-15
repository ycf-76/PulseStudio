<template>
  <button type="button" class="ccd-link" @click="onPreviewClick">
    <FileText :size="15" class="ccd-link-icon" />
    <span class="ccd-link-title">{{ draftTitle }}</span>
    <span class="ccd-link-meta">{{ pageCount }}页</span>
    <span class="ccd-link-open">侧边栏预览 →</span>
  </button>

  <!-- 侧边栏浏览器：右侧滑出，iframe 渲染当前页 -->
  <Teleport to="body">
    <Transition name="ccd-drawer">
      <div v-if="drawerOpen" class="ccd-drawer-mask" @click.self="drawerOpen = false">
        <div class="ccd-drawer">
          <div class="ccd-drawer-head">
            <span class="ccd-drawer-title">{{ draftTitle }}</span>
            <span class="ccd-drawer-page">{{ drawerPage + 1 }} / {{ pageCount }}</span>
            <div class="ccd-drawer-nav">
              <button class="ccd-drawer-btn" :disabled="drawerPage <= 0" @click="drawerPage--">‹</button>
              <button class="ccd-drawer-btn" :disabled="drawerPage >= pageCount - 1" @click="drawerPage++">›</button>
              <button class="ccd-drawer-btn" @click="drawerOpen = false"><X :size="15" /></button>
            </div>
          </div>
          <div class="ccd-drawer-body">
            <iframe
              v-if="currentPage && currentPage.htmlContent"
              sandbox="allow-same-origin"
              :srcdoc="currentPage.htmlContent"
              class="ccd-drawer-iframe"
            ></iframe>
            <img
              v-else-if="currentPage && currentPage.imageUrl"
              :src="currentPage.imageUrl"
              class="ccd-drawer-img"
              :alt="draftTitle"
            />
            <div v-else class="ccd-drawer-empty">本页暂无可预览内容</div>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { ZoomIn, Eye, Edit3, X, ChevronLeft, ChevronRight, FileText } from 'lucide-vue-next'
import type { NormalizedCardDraft } from '@/composables/cardDraft'

const props = defineProps<{
  cardDraft: NormalizedCardDraft
  workId?: string
}>()

const emit = defineEmits<{
  openStudio: [workId?: string]
  /** 2026-11-03：在右侧 ContextSidebar 打开（linkWork），替代临时抽屉 */
  previewSidebar: [workId: string]
}>()

// 2026-11-03 抽拉式改侧边栏：草稿作品 id 在（agentMeta.cardDraftWorkId）时，
// 点「侧边栏预览」直接在右侧工作台面板打开；历史老消息没有 id，兜底走旧抽屉。
function onPreviewClick() {
  if (props.workId) {
    emit('previewSidebar', props.workId)
    return
  }
  drawerOpen.value = true
}

const lightboxOpen = ref(false)
const currentIndex = ref(0)

const CARD_NATIVE_W = 1080
const CARD_NATIVE_H = 1440
const THUMB_W = 160
const THUMB_H = Math.round(THUMB_W * (CARD_NATIVE_H / CARD_NATIVE_W))

const thumbScale = THUMB_W / CARD_NATIVE_W
const thumbIframeStyle = {
  width: `${CARD_NATIVE_W}px`,
  height: `${CARD_NATIVE_H}px`,
  transform: `scale(${thumbScale})`,
  transformOrigin: 'top left',
}

const lightboxSize = computed(() => {
  const maxW = Math.min(window.innerWidth * 0.85, 540)
  const maxH = window.innerHeight * 0.85
  const scaleW = maxW / CARD_NATIVE_W
  const scaleH = maxH / CARD_NATIVE_H
  const scale = Math.min(scaleW, scaleH)
  return {
    w: Math.round(CARD_NATIVE_W * scale),
    h: Math.round(CARD_NATIVE_H * scale),
    scale,
  }
})

const lightboxIframeStyle = computed(() => {
  const { scale } = lightboxSize.value
  return {
    width: `${CARD_NATIVE_W}px`,
    height: `${CARD_NATIVE_H}px`,
    transform: `scale(${scale})`,
    transformOrigin: 'top left',
  }
})

const lightboxWrapperStyle = computed(() => {
  const { w, h } = lightboxSize.value
  return {
    width: `${w}px`,
    height: `${h}px`,
  }
})

const drawerOpen = ref(false)
const drawerPage = ref(0)

const draftTitle = computed(() => props.cardDraft.title || '小红书卡片')

function fixViewport(html: string): string {
  return html.replace(
    /<meta\s+name=["']viewport["'][^>]*>/gi,
    '<meta name="viewport" content="width=1080,initial-scale=1">'
  )
}

const previewPages = computed(() => {
  const pages = props.cardDraft.pages || []
  const pngUrls = props.cardDraft.pngUrls || []
  if (pages.length > 0) {
    return pages.map((p: any) => ({
      ...p,
      htmlContent: p.htmlContent ? fixViewport(p.htmlContent) : p.htmlContent,
    }))
  }
  return pngUrls.map((url: string, i: number) => ({
    id: `png_${i}`,
    type: i === 0 ? 'cover' : 'content',
    title: `卡片 ${i + 1}`,
    imageUrl: url,
    htmlContent: '',
  }))
})

const pageCount = computed(() => previewPages.value.length)

const currentPage = computed(() => previewPages.value[currentIndex.value] || null)

const currentPageTitle = computed(() => {
  const page = currentPage.value
  if (!page) return ''
  return page.title || (page.type === 'cover' ? '封面页' : page.type === 'end' ? '结尾页' : '')
})

function openLightbox(index: number) {
  currentIndex.value = Math.min(index, pageCount.value - 1)
  lightboxOpen.value = true
  document.body.style.overflow = 'hidden'
}

function closeLightbox() {
  lightboxOpen.value = false
  document.body.style.overflow = ''
}

function prevPage() {
  if (currentIndex.value > 0) currentIndex.value--
}

function nextPage() {
  if (currentIndex.value < pageCount.value - 1) currentIndex.value++
}

function openStudio() {
  emit('openStudio', props.workId)
}

function onThumbError(e: Event, originalUrl: string) {
  const img = e.target as HTMLImageElement
  if (!img.src.includes('/api/proxy/image') && originalUrl?.startsWith('http')) {
    img.src = '/api/proxy/image?url=' + encodeURIComponent(originalUrl)
    return
  }
  img.style.display = 'none'
}

function onImgError(e: Event, originalUrl: string) {
  const img = e.target as HTMLImageElement
  if (!img.src.includes('/api/proxy/image') && originalUrl?.startsWith('http')) {
    img.src = '/api/proxy/image?url=' + encodeURIComponent(originalUrl)
    return
  }
}

function onKeydown(e: KeyboardEvent) {
  if (!lightboxOpen.value) return
  if (e.key === 'Escape') closeLightbox()
  if (e.key === 'ArrowLeft') prevPage()
  if (e.key === 'ArrowRight') nextPage()
}

onMounted(() => {
  document.addEventListener('keydown', onKeydown)
})

onUnmounted(() => {
  document.removeEventListener('keydown', onKeydown)
  if (lightboxOpen.value) {
    document.body.style.overflow = ''
  }
})
</script>

<style scoped>
.ccd-card {
  background: rgba(83, 74, 183, 0.06);
  border: 1px solid rgba(83, 74, 183, 0.22);
  border-radius:14px;
  padding: 10px 12px;
  margin-top: 8px;
}

.ccd-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}

.ccd-badge {
  font-size: 11px;
  font-weight: 600;
  padding: 2px 8px;
  border-radius:8px;
  color: #FFFFFF;
  background: var(--rl-accent);
}

.ccd-title {
  font-size: 13px;
  font-weight: 500;
  color: var(--rl-text-1);
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ccd-count {
  font-size: 11px;
  color: var(--rl-text-3);
  background: #e2e8f0;
  padding: 1px 6px;
  border-radius:6px;
}

.ccd-previews {
  display: flex;
  gap: 8px;
  overflow-x: auto;
  padding-bottom: 4px;
  scrollbar-width: thin;
}

.ccd-thumb-wrap {
  position: relative;
  flex: 0 0 160px;
  height: 213px;
  border-radius:12px;
  overflow: hidden;
  cursor: pointer;
  border: 1px solid #e2e8f0;
  background: #fff;
  transition: transform 0.15s, box-shadow 0.15s;
  contain: layout;
}

.ccd-thumb-wrap:hover {
  transform: translateY(-2px);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
}

.ccd-thumb-wrap:hover .ccd-thumb-overlay {
  opacity: 1;
}

.ccd-thumb {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.ccd-thumb-iframe-wrap {
  width: 100%;
  height: 100%;
  overflow: hidden;
  pointer-events: none;
}

.ccd-thumb-iframe {
  border: none;
}

.ccd-thumb-placeholder {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #f1f5f9;
  color: #94a3b8;
  font-size: 13px;
  font-weight: 500;
}

.ccd-thumb-overlay {
  position: absolute;
  inset: 0;
  background: rgba(0, 0, 0, 0.3);
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  opacity: 0;
  transition: opacity 0.15s;
}

.ccd-actions {
  display: flex;
  gap: 8px;
  margin-top: 8px;
}

.ccd-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 5px 10px;
  border-radius:10px;
  border: none;
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s;
}

.ccd-btn-view {
  background: #3b82f6;
  color: #fff;
}

.ccd-btn-view:hover {
  background: #2563eb;
}

.ccd-btn-edit {
  background: #f1f5f9;
  color: #475569;
  border: 1px solid #e2e8f0;
}

.ccd-btn-edit:hover {
  background: #e2e8f0;
}

/* Lightbox */
.clb-overlay {
  position: fixed;
  inset: 0;
  z-index: 9999;
  background: rgba(0, 0, 0, 0.85);
  display: flex;
  align-items: center;
  justify-content: center;
}

.clb-container {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
}

.clb-close {
  position: absolute;
  top: 12px;
  right: 12px;
  z-index: 10;
  background: rgba(255, 255, 255, 0.15);
  border: none;
  border-radius:12px;
  width: 36px;
  height: 36px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  cursor: pointer;
  transition: background 0.15s;
}

.clb-close:hover {
  background: rgba(255, 255, 255, 0.3);
}

.clb-nav {
  position: absolute;
  top: 50%;
  transform: translateY(-50%);
  z-index: 10;
  background: rgba(255, 255, 255, 0.1);
  border: none;
  border-radius: 50%;
  width: 44px;
  height: 44px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  cursor: pointer;
  transition: background 0.15s;
}

.clb-nav:hover:not(.clb-nav-disabled) {
  background: rgba(255, 255, 255, 0.25);
}

.clb-nav-disabled {
  opacity: 0.3;
  cursor: not-allowed;
}

.clb-prev {
  left: 12px;
}

.clb-next {
  right: 12px;
}

.clb-content {
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
}

.clb-img {
  max-width: min(85vw, 540px);
  max-height: 85vh;
  object-fit: contain;
  border-radius:12px;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.4);
}

.clb-iframe {
  border: none;
  border-radius:12px;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.4);
  background: #fff;
}

.clb-iframe-wrapper {
  border-radius:12px;
  overflow: hidden;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.4);
}

.clb-empty {
  color: #94a3b8;
  font-size: 14px;
}

.clb-footer {
  position: absolute;
  bottom: 16px;
  left: 50%;
  transform: translateX(-50%);
  display: flex;
  align-items: center;
  gap: 12px;
  color: rgba(255, 255, 255, 0.7);
  font-size: 13px;
}

.clb-page-info {
  background: rgba(255, 255, 255, 0.15);
  padding: 3px 10px;
  border-radius:8px;
}

.clb-page-title {
  max-width: 200px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.clb-fade-enter-active,
.clb-fade-leave-active {
  transition: opacity 0.2s ease;
}

.clb-fade-enter-from,
.clb-fade-leave-to {
  opacity: 0;
}
/* ── 链接行形态（聊天内标准链接样式）── */
.ccd-link {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  max-width: 100%;
  padding: 6px 10px;
  margin-top: 6px;
  border: none;
  border-radius:12px;
  background: rgba(83, 74, 183, 0.05);
  cursor: pointer;
  font: inherit;
  text-align: left;
  transition: background 0.15s;
}
.ccd-link:hover {
  background: rgba(83, 74, 183, 0.1);
}
.ccd-link:hover .ccd-link-open {
  text-decoration: underline;
}
.ccd-link-icon {
  color: #534AB7;
  flex: none;
}
.ccd-link-title {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
  font-weight: 500;
  color: #26215C;
}
.ccd-link-meta {
  flex: none;
  font-size: 12px;
  color: #888780;
}
.ccd-link-open {
  flex: none;
  font-size: 12px;
  color: #534AB7;
}

/* ── 侧边栏浏览器抽屉 ── */
.ccd-drawer-mask {
  position: fixed;
  inset: 0;
  z-index: 9998;
  background: rgba(0, 0, 0, 0.25);
}
.ccd-drawer {
  position: absolute;
  top: 0;
  right: 0;
  bottom: 0;
  width: min(60vw, 900px);
  background: #fff;
  box-shadow: -16px 0 48px rgba(0, 0, 0, 0.16);
  display: flex;
  flex-direction: column;
}
.ccd-drawer-head {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 16px;
  border-bottom: 0.5px solid rgba(31, 31, 30, 0.1);
}
.ccd-drawer-title {
  min-width: 0;
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 14px;
  font-weight: 500;
  color: #1a1a1a;
}
.ccd-drawer-page {
  flex: none;
  font-size: 12px;
  font-family: var(--font-mono, monospace);
  color: #888780;
  font-variant-numeric: tabular-nums;
}
.ccd-drawer-nav {
  display: flex;
  align-items: center;
  gap: 6px;
}
.ccd-drawer-btn {
  width: 28px;
  height: 28px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 0.5px solid rgba(31, 31, 30, 0.15);
  border-radius:12px;
  background: #fff;
  cursor: pointer;
  color: #373734;
  transition: background 0.15s;
}
.ccd-drawer-btn:hover:not(:disabled) {
  background: rgba(31, 31, 30, 0.04);
}
.ccd-drawer-btn:disabled {
  opacity: 0.35;
  cursor: default;
}
.ccd-drawer-body {
  flex: 1;
  min-height: 0;
  overflow: auto;
  background: #1F1F1D;
  display: flex;
  align-items: flex-start;
  justify-content: center;
  padding: 16px;
}
.ccd-drawer-iframe {
  width: 100%;
  height: 100%;
  min-height: 400px;
  border: none;
  border-radius:12px;
  background: #fff;
}
.ccd-drawer-img {
  max-width: 100%;
  max-height: 100%;
  border-radius:12px;
}
.ccd-drawer-empty {
  padding: 40px;
  font-size: 13px;
  color: #888780;
}
.ccd-drawer-enter-active,
.ccd-drawer-leave-active {
  transition: opacity 0.2s ease;
}
.ccd-drawer-enter-active .ccd-drawer,
.ccd-drawer-leave-active .ccd-drawer {
  transition: transform 0.22s cubic-bezier(0.22, 1, 0.36, 1);
}
.ccd-drawer-enter-from,
.ccd-drawer-leave-to {
  opacity: 0;
}
.ccd-drawer-enter-from .ccd-drawer,
.ccd-drawer-leave-to .ccd-drawer {
  transform: translateX(40px);
}
</style>