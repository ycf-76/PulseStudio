<template>
  <div class="cpp" ref="rootRef">
    <div class="cpp-scaler" :style="scalerStyle">
      <PhoneFrame variant="full">
        <div class="cpp-screen">
          <slot v-if="$slots.screen" name="screen" />
          <template v-else>
            <div class="cpp-card" :style="cardBoxStyle">
              <iframe
                v-if="html"
                class="cpp-iframe"
                :style="iframeStyle"
                sandbox="allow-same-origin"
                scrolling="no"
                :srcdoc="fixedHtml"
              />
              <img
                v-else-if="imageUrl && !imageFailed"
                class="cpp-image"
                :src="imageUrl"
                :alt="pageLabel || '作品页面'"
                @error="onImageError"
              />
              <div v-else class="cpp-empty">{{ imageUrl ? failedText : emptyText }}</div>
            </div>

            <div class="cpp-meta">
              <div class="cpp-title">{{ title || '未命名作品' }}</div>
              <div class="cpp-sub">
                <span class="cpp-page">{{ pageLabel }}</span>
                <span v-if="pageCount" class="cpp-count">{{ pageIndex + 1 }} / {{ pageCount }}</span>
              </div>
            </div>

            <div class="cpp-foot">
              <span class="cpp-foot-act">
                <svg viewBox="0 0 24 24" class="cpp-foot-icon"><path d="M12 20s-7-4.5-7-9a4 4 0 0 1 7-2.6A4 4 0 0 1 19 11c0 4.5-7 9-7 9z" /></svg>
                点赞
              </span>
              <span class="cpp-foot-act">
                <svg viewBox="0 0 24 24" class="cpp-foot-icon"><path d="M12 4l2.5 5.2 5.5.8-4 3.9 1 5.6-5-2.7-5 2.7 1-5.6-4-3.9 5.5-.8z" /></svg>
                收藏
              </span>
              <span class="cpp-foot-act">
                <svg viewBox="0 0 24 24" class="cpp-foot-icon"><path d="M4 5h16v11H9l-5 4z" /></svg>
                评论
              </span>
              <span class="cpp-foot-input">说点什么...</span>
            </div>
          </template>
        </div>
      </PhoneFrame>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * CardPreviewPane —— 详情页右栏手机预览。
 *
 * 卡片原生画布是 1080×1440，手机屏幕内宽 313px，按宽度等比缩放，
 * 保证「所见即所得」而不是裁切。iframe 用 srcdoc 承接 renderer 输出的完整 HTML，
 * sandbox 只放 same-origin，避免产物 HTML 里的脚本影响宿主页面。
 *
 * 手机外框（PhoneFrame）自身是固定 325×676 的实机比例，编辑区可用高度往往不够，
 * 直接塞进去会把手机壳底部裁掉、看起来就不像手机。这里用 ResizeObserver 量可用高度，
 * 整体等比缩小到刚好放下，保证整只手机壳（刘海/状态栏/home 条）完整可见。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import PhoneFrame from '@/components/chat/PhoneFrame.vue'

const props = withDefaults(
  defineProps<{
    html?: string
    imageUrl?: string
    title?: string
    pageLabel?: string
    pageIndex: number
    pageCount?: number
    /** 无可预览内容时的占位文案（空页用 emptyText，图加载失败用 failedText） */
    emptyText?: string
    failedText?: string
  }>(),
  { emptyText: '本页暂无可预览内容', failedText: '图片已失效（链接过期）' }
)

const CARD_NATIVE_W = 1080
const CARD_NATIVE_H = 1440
/** PhoneFrame(full) 机身宽 325，左右边框各 6px，屏幕内宽即 325 - 12 = 313。 */
const SCREEN_W = 313
const SCALE = SCREEN_W / CARD_NATIVE_W
const CARD_H = Math.round(CARD_NATIVE_H * SCALE)

/** PhoneFrame(full) 的固有尺寸：325×676（真机 1:2.08），屏幕内高 658。 */
const PHONE_H = 676
const PHONE_W = 325

const rootRef = ref<HTMLElement | null>(null)
const availH = ref(0)
let ro: ResizeObserver | null = null

onMounted(() => {
  if (!rootRef.value) return
  ro = new ResizeObserver((entries) => {
    availH.value = entries[0].contentRect.height
  })
  ro.observe(rootRef.value)
  availH.value = rootRef.value.clientHeight
})

onBeforeUnmount(() => {
  ro?.disconnect()
  ro = null
})

/** 可用高度不足时等比缩小，永远让整只手机壳落在可视区内。 */
const fitScale = computed(() => {
  if (!availH.value) return 1
  return Math.min(1, availH.value / PHONE_H)
})

const scalerStyle = computed(() => ({
  width: `${PHONE_W}px`,
  height: `${PHONE_H * fitScale.value}px`,
  transform: `scale(${fitScale.value})`,
  transformOrigin: 'top center',
}))

const cardBoxStyle = computed(() => ({
  width: `${SCREEN_W}px`,
  height: `${CARD_H}px`,
}))

const iframeStyle = computed(() => ({
  width: `${CARD_NATIVE_W}px`,
  height: `${CARD_NATIVE_H}px`,
  transform: `scale(${SCALE})`,
  transformOrigin: 'top left',
}))

const fixedHtml = computed(() =>
  (props.html || '').replace(
    /<meta\s+name=["']viewport["'][^>]*>/gi,
    '<meta name="viewport" content="width=1080,initial-scale=1">'
  )
)

/**
 * 小红书 CDN 有防盗链，直连会 403；这里先退到后端代理（代理内部还会查本地缓存），
 * 代理也拿不到才降级成占位文案，避免手机壳里出现碎图图标。
 *
 * 兜底标记必须绑定到具体 URL：翻页时 <img> 元素被复用，若沿用上一页的标记，
 * 新一页直连失败会被立刻判死，白白丢掉代理和本地缓存这条活路。
 */
const imageFailed = ref(false)
watch(
  () => props.imageUrl,
  () => {
    imageFailed.value = false
  }
)

function onImageError(event: Event) {
  const img = event.target as HTMLImageElement
  const src = props.imageUrl || ''
  if (img.dataset.proxiedFor !== src && src.startsWith('http')) {
    img.dataset.proxiedFor = src
    img.src = '/api/proxy/image?url=' + encodeURIComponent(src)
    return
  }
  imageFailed.value = true
}
</script>

<style scoped>
.cpp {
  flex: 1;
  min-height: 0;
  width: 100%;
  align-self: stretch;
  display: flex;
  justify-content: center;
  align-items: flex-start;
  overflow: hidden;
}

.cpp-scaler { flex-shrink: 0; }

/* 状态栏已成为 .pf-screen 内的兄弟节点，内容区必须 flex 撑满剩余高度，
 * 用 height:100% 会按整屏高度计算导致溢出。
 * position: relative 供底部互动栏做绝对定位。 */
.cpp-screen { position: relative; display: flex; flex-direction: column; flex: 1; min-height: 0; background: #fff; }

.cpp-card {
  position: relative;
  overflow: hidden;
  background: #f4f4f5;
  flex-shrink: 0;
}

.cpp-iframe { border: none; display: block; }

.cpp-image { width: 100%; height: 100%; object-fit: cover; display: block; }

.cpp-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  font-size: 12px;
  color: #a1a1aa;
}

.cpp-meta { padding: 12px 14px 0; }

.cpp-title {
  font-size: 14px;
  font-weight: 600;
  color: #1a1a1a;
  line-height: 1.45;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.cpp-sub {
  margin-top: 8px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 11px;
  color: #9a9a9a;
}

.cpp-page { color: #71717a; }
.cpp-count { font-variant-numeric: tabular-nums; }

/* 底部互动栏：钉在屏幕底部安全区，让 3:4 卡片下方的留白有收口，
 * 形态对齐小红书笔记详情页（点赞/收藏/评论 + 输入框）。 */
.cpp-foot {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 14px 20px;
  background: #fff;
  border-top: 1px solid #f2f2f2;
  z-index: 2;
}

.cpp-foot-act {
  display: flex;
  align-items: center;
  gap: 3px;
  font-size: 10px;
  color: #71717a;
  flex-shrink: 0;
}

.cpp-foot-icon {
  width: 15px;
  height: 15px;
  fill: none;
  stroke: #71717a;
  stroke-width: 1.6;
  stroke-linejoin: round;
}

.cpp-foot-input {
  flex: 1;
  min-width: 0;
  padding: 5px 0;
  border-radius: 12px;
  background: #f6f6f6;
  color: #b0b0b0;
  font-size: 10px;
  text-align: center;
}
</style>