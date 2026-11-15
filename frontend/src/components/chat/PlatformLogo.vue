<template>
  <span
    v-if="masked"
    class="plogo plogo-mask"
    :style="{ '--plogo-color': color, maskImage: `url(${src})`, WebkitMaskImage: `url(${src})` }"
    :title="name"
  ></span>
  <img v-else-if="src" :src="src" :alt="name" class="plogo" />
  <span v-else class="plogo plogo-fallback" :style="{ color: color }" :title="name">{{ glyph }}</span>
</template>

<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(defineProps<{
  platform: string
  name: string
  color?: string
  size?: number
}>(), {
  color: '',
  size: 20,
})

const ICON_MAP: Record<string, { src: string; masked: boolean; color: string }> = {
  xiaohongshu: { src: '/icons/xiaohongshu-si.svg', masked: true, color: '#FF2442' },
  rednote: { src: '/icons/xiaohongshu-si.svg', masked: true, color: '#FF2442' },
  douyin: { src: '/icons/douyin-app.svg', masked: false, color: '#000000' },
  kuaishou: { src: '/icons/kuaishou-si.svg', masked: true, color: '#FF4906' },
  bilibili: { src: '/icons/bilibili-si.svg', masked: true, color: '#00A1D6' },
  zhihu: { src: '/icons/zhihu-si.svg', masked: true, color: '#0084FF' },
  weibo: { src: '/icons/weibo-si.svg', masked: true, color: '#E6162D' },
  wechat: { src: '/icons/wechat-si.svg', masked: true, color: '#07C160' },
  wechat_video: { src: '/icons/wechat-si.svg', masked: true, color: '#07C160' },
  pinterest: { src: '/icons/pinterest-si.svg', masked: true, color: '#E60023' },
  instagram: { src: '/icons/instagram-si.svg', masked: true, color: '#E4405F' },
  twitter: { src: '/icons/twitter-si.svg', masked: true, color: '#0F1419' },
  youtube: { src: '/icons/youtube-si.svg', masked: true, color: '#FF0000' },
  tiktok: { src: '/icons/tiktok-si.svg', masked: true, color: '#0F1419' },
  medium: { src: '/icons/medium-si.svg', masked: true, color: '#0F1419' },
  devto: { src: '/icons/devto-si.svg', masked: true, color: '#0F1419' },
  github: { src: '/icons/github-logo.svg', masked: true, color: '#24292F' },
  hackernews: { src: '/icons/hackernews-si.svg', masked: true, color: '#FF6600' },
  reddit: { src: '/icons/reddit-si.svg', masked: true, color: '#FF4500' },
  baidu: { src: '/icons/baidu-si.svg', masked: true, color: '#2932E1' },
}

const HOTBOARD_PREFIX = 'hb-'

const normalized = computed(() => {
  const raw = (props.platform || '').toLowerCase()
  return raw.startsWith(HOTBOARD_PREFIX) ? raw.slice(HOTBOARD_PREFIX.length) : raw
})

const icon = computed(() => ICON_MAP[normalized.value] || null)
const src = computed(() => icon.value?.src || '')
const masked = computed(() => icon.value?.masked ?? false)
const color = computed(() => props.color || icon.value?.color || '#6B7280')
const glyph = computed(() => (props.name || props.platform || '?').trim().charAt(0) || '?')
const sizePx = computed(() => `${props.size}px`)
</script>

<style scoped>
.plogo {
  width: v-bind(sizePx);
  height: v-bind(sizePx);
  border-radius: 6px;
  object-fit: contain;
  flex-shrink: 0;
  display: inline-block;
}
.plogo-mask {
  background-color: var(--plogo-color);
  -webkit-mask-size: contain;
  mask-size: contain;
  -webkit-mask-repeat: no-repeat;
  mask-repeat: no-repeat;
  -webkit-mask-position: center;
  mask-position: center;
}
.plogo-fallback {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 1px solid currentColor;
  border-radius: 6px;
  font-size: calc(v-bind(sizePx) * 0.5);
  font-weight: 700;
  line-height: 1;
  opacity: 0.75;
  box-sizing: border-box;
}
</style>