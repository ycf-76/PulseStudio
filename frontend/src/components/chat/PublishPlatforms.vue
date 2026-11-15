<template>
  <div class="pub-block" v-if="showPublish">
    <div class="pub-head">
      <span class="pub-title">发布到</span>
      <span class="pub-count" v-if="publishedCount > 0">{{ publishedCount }}/{{ platforms.length }} 已发布</span>
    </div>
    <div class="pub-grid">
      <button
        v-for="p in platforms"
        :key="p.key"
        class="pub-tile"
        :class="{ 'pub-tile-done': p.published }"
        :style="{ '--brand': p.color, '--brand-soft': p.soft }"
        :title="`发布到${p.name}`"
        @click="onPublish(p.key)"
      >
        <span class="pub-glyph">
          <PlatformLogo :platform="p.key" :name="p.name" :size="15" />
        </span>
        <span class="pub-name">{{ p.name }}</span>
        <Check v-if="p.published" :size="12" :stroke-width="2.6" class="pub-check" />
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { Check } from 'lucide-vue-next'
import PlatformLogo from './PlatformLogo.vue'
import type { WorkItem } from '@/stores/work'

const props = defineProps<{
  work: WorkItem
}>()

const emit = defineEmits<{
  'chat-action': [action: string]
}>()

const showPublish = computed(() => props.work.isDraft)

const PLATFORMS = [
  { key: 'xiaohongshu', name: '小红书', action: 'publish_xiaohongshu', color: '#FF2442', soft: '#FFF1F3' },
  { key: 'douyin', name: '抖音', action: 'publish_douyin', color: '#161823', soft: '#F3F3F5' },
  { key: 'kuaishou', name: '快手', action: 'publish_kuaishou', color: '#FF4906', soft: '#FFF2EA' },
  { key: 'bilibili', name: 'B站', action: 'publish_bilibili', color: '#00A1D6', soft: '#E9F7FC' },
  { key: 'zhihu', name: '知乎', action: 'publish_zhihu', color: '#0084FF', soft: '#EAF3FF' },
  { key: 'wechat_video', name: '视频号', action: 'publish_wechat', color: '#07C160', soft: '#EAFBF1' },
]

const platforms = computed(() => {
  const publishedPlatforms: string[] = props.work.publishedPlatforms || []
  return PLATFORMS.map(p => ({
    ...p,
    published: publishedPlatforms.includes(p.key),
  }))
})

const publishedCount = computed(() => platforms.value.filter(p => p.published).length)

function onPublish(key: string) {
  const p = PLATFORMS.find(x => x.key === key)
  if (p) emit('chat-action', p.action)
}
</script>

<style scoped>
.pub-block {
  margin-top: 10px;
  padding-top: 12px;
  border-top: 1px solid rgba(0, 0, 0, 0.05);
}
.pub-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
  padding: 0 2px;
}
.pub-title {
  font-size: 12px;
  font-weight: 600;
  color: #4B4B4B;
  letter-spacing: 0.2px;
}
.pub-count {
  font-size: 10.5px;
  color: #9CA3AF;
}
.pub-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 6px;
}
/* 2026-11-14 排版优化：竖排大 tile 改为横向胶囊（图标+名字同行），
   高度减半、密度更合理；细边框卡片，hover 翻品牌浅底 */
.pub-tile {
  position: relative;
  display: flex;
  flex-direction: row;
  align-items: center;
  gap: 6px;
  padding: 5px 8px;
  border: 1px solid rgba(0, 0, 0, 0.07);
  /* main.css 的 button { border-radius: 9999px !important } 是全站按钮胶囊约定，
     会把 tile 强制拉成胶囊。用更高优先级的同级 !important 夺回方角。 */
  border-radius: 10px !important;
  background: #fff;
  font-family: inherit;
  cursor: pointer;
  transition: background 0.15s ease, border-color 0.15s ease;
}
/* hover：品牌浅底 + 品牌描边 + 名字变色 */
.pub-tile:hover {
  background: var(--brand-soft);
  border-color: color-mix(in srgb, var(--brand) 32%, transparent);
}
.pub-tile:hover .pub-name {
  color: var(--brand);
}
/* 2026-11-05：真实平台 logo（PlatformLogo，品牌色 mask 渲染）取代首字占位 */
.pub-glyph {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border-radius: 7px;
  background: var(--brand-soft);
  line-height: 1;
  flex-shrink: 0;
}
.pub-name {
  flex: 1;
  min-width: 0;
  font-size: 11px;
  font-weight: 500;
  color: #555;
  transition: color 0.18s ease;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  text-align: left;
}
/* 已发布：品牌浅底 + 行内绿勾（不再整块降透明度，信息更清晰） */
.pub-tile-done {
  background: var(--brand-soft);
  border-color: color-mix(in srgb, var(--brand) 32%, transparent);
}
.pub-tile-done .pub-name {
  color: var(--brand);
}
.pub-tile-done:hover {
  opacity: 1;
}
.pub-check {
  color: #10b981;
  flex-shrink: 0;
}
</style>