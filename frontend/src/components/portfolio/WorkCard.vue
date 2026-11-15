<template>
  <article class="wc" :class="`wc--${variant}`" @click="$emit('open', work)">
    <div class="wc-media">
      <WorkMediaThumb :info="media" :title="title" />
      <div class="wc-actions" @click.stop>
        <button class="wc-act" title="打开详情" @click="$emit('open', work)">
          <Maximize2 :size="14" />
        </button>
        <button
          class="wc-act wc-act--danger"
          title="删除"
          :disabled="deleting"
          @click="$emit('remove', work)"
        >
          <Trash2 :size="14" />
        </button>
      </div>
    </div>

    <div class="wc-body">
      <h3 class="wc-title">{{ title }}</h3>
      <div class="wc-meta">
        <span class="wc-platform">{{ platform }}</span>
        <span class="wc-sep">·</span>
        <span class="wc-date">{{ dateText }}</span>
      </div>
      <div class="wc-foot">
        <span class="wc-status" :class="originClass">{{ originText }}</span>
        <div v-if="media.hasMetrics" class="wc-stats">
          <span class="wc-stat"><Heart :size="12" />{{ counts.likes }}</span>
          <span class="wc-stat"><Star :size="12" />{{ counts.collects }}</span>
          <span class="wc-stat"><MessageCircle :size="12" />{{ counts.comments }}</span>
        </div>
      </div>
    </div>
  </article>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { Heart, Maximize2, MessageCircle, Star, Trash2 } from 'lucide-vue-next'
import type { MyWorkItem } from '@/api/my_works'
import WorkMediaThumb from './WorkMediaThumb.vue'
import {
  formatCount,
  formatWorkDate,
  platformLabelOf,
  summarizeWorkMedia,
} from './workMedia'

const props = withDefaults(defineProps<{
  work: MyWorkItem
  variant?: 'grid' | 'list'
  deleting?: boolean
}>(), {
  variant: 'grid',
  deleting: false,
})

defineEmits<{
  (event: 'open', work: MyWorkItem): void
  (event: 'remove', work: MyWorkItem): void
}>()

const media = computed(() => summarizeWorkMedia(props.work))
const title = computed(() => props.work.title || props.work.topic || '未命名作品')
const platform = computed(() => platformLabelOf(props.work.platform))
const dateText = computed(() => formatWorkDate(props.work.collected_at || props.work.published_at))

const ORIGIN_TEXT: Record<string, string> = {
  collect: '采集',
  artifact: '生成·创作',
  output: '生成·产物',
  workflow: '生成·发布',
}

const originKind = computed(() => props.work.origin_kind || 'collect')
const originText = computed(() => ORIGIN_TEXT[originKind.value] || '采集')
const originClass = computed(() => `wc-status--${originKind.value}`)

const counts = computed(() => ({
  likes: formatCount(props.work.collected_likes),
  collects: formatCount(props.work.collected_collects),
  comments: formatCount(props.work.collected_comments),
}))
</script>

<style scoped>
.wc {
  display: flex;
  position: relative;
  background: #fff;
  border: 1px solid #F0F0F0;
  border-radius: 16px;
  overflow: hidden;
  cursor: pointer;
  transition: transform 0.16s ease, box-shadow 0.16s ease, border-color 0.16s ease;
}
.wc:hover {
  transform: translateY(-2px);
  box-shadow: 0 8px 20px rgba(0, 0, 0, 0.07);
  border-color: #E4E4E7;
}

.wc--grid { flex-direction: column; }
.wc--list { flex-direction: row; align-items: stretch; }

.wc-media {
  position: relative;
  background: #F5F5F5;
  flex-shrink: 0;
  overflow: hidden;
}
.wc--grid .wc-media { aspect-ratio: 1 / 1; width: 100%; }
.wc--list .wc-media { width: 92px; aspect-ratio: 1 / 1; position: static; overflow: visible; }

.wc-actions {
  position: absolute;
  right: 8px;
  bottom: 8px;
  display: flex;
  gap: 4px;
  opacity: 0;
  transform: translateY(4px);
  transition: opacity 0.16s ease, transform 0.16s ease;
}
.wc:hover .wc-actions { opacity: 1; transform: translateY(0); }

.wc--list .wc-actions {
  right: 14px;
  bottom: auto;
  top: 50%;
  transform: translateY(-50%);
}
.wc--list:hover .wc-actions { transform: translateY(-50%); }

.wc-act {
  width: 26px;
  height: 26px;
  border: none;
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.92);
  color: #4B4B4B;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: all 0.12s;
}
.wc-act:hover { background: #fff; color: #1a1a1a; }
.wc-act--danger:hover { color: #EF4444; }
.wc-act:disabled { opacity: 0.45; cursor: default; }
.wc-act:disabled:hover { color: #4B4B4B; }

.wc-body {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 9px 11px 10px;
}
.wc--list .wc-body { justify-content: center; padding: 14px 16px; }
.wc--list:has(.wc-act--danger) .wc-body { padding-right: 72px; }

.wc-title {
  margin: 0;
  font-size: 14px;
  font-weight: 500;
  line-height: 1.45;
  color: #1a1a1a;
  overflow: hidden;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  word-break: break-all;
}

.wc-meta {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 11px;
  color: #9A9A9A;
  min-width: 0;
}
.wc-platform { color: #4B4B4B; flex-shrink: 0; }
.wc-date { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.wc-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-top: auto;
}

.wc-status {
  font-size: 10px;
  padding: 1px 6px;
  border-radius: 7px;
  background: #F5F5F5;
  color: #4B4B4B;
  flex-shrink: 0;
}
.wc-status--published { background: #D1FAE5; color: #059669; }
.wc-status--collected { background: #DBEAFE; color: #2563EB; }
.wc-status--collect { background: #DBEAFE; color: #2563EB; }
.wc-status--artifact { background: #E7F6F3; color: #0F766E; }
.wc-status--output { background: #EDE9FE; color: #7C3AED; }
.wc-status--workflow { background: #D1FAE5; color: #059669; }
.wc-status--draft { background: #FEF3C7; color: #D97706; }
.wc-status--reviewing { background: #EDE9FE; color: #7C3AED; }
.wc-status--rejected { background: #FEE2E2; color: #DC2626; }

.wc-stats { display: flex; align-items: center; gap: 10px; }
.wc-stat { display: inline-flex; align-items: center; gap: 3px; font-size: 12px; color: #9A9A9A; }
</style>