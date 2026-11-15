<template>
  <span class="tc" :style="{ background: tone, '--tc-ink': accent }">
    <span class="tc-paper" :class="`is-${template.category}`">
      <span class="tc-paper-title"></span>
      <span v-if="template.category === 'graphic'" class="tc-paper-block"></span>
      <span v-else class="tc-paper-glyph">
        <component :is="glyphOf(template.category)" :size="20" :stroke-width="1.8" />
      </span>
      <span class="tc-paper-line"></span>
      <span class="tc-paper-line is-short"></span>
    </span>

    <span class="tc-scrim"></span>

    <span class="tc-meta">
      <span class="tc-title">{{ template.title }}</span>
      <span class="tc-sub">{{ template.subtitle }}</span>
    </span>

    <span v-if="template.hot" class="tc-hot">热门</span>
    <span class="tc-use">用这个</span>
  </span>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { BarChart3, Layers, PenLine, Play, Send, Target } from 'lucide-vue-next'
import {
  INSPIRATION_ACCENT,
  INSPIRATION_TONE,
  type InspirationCategoryKey,
  type InspirationTemplate,
} from './composables/useInspirationTemplates'

const props = defineProps<{ template: InspirationTemplate }>()

const GLYPHS: Record<InspirationCategoryKey, unknown> = {
  graphic: Layers,
  copy: PenLine,
  video: Play,
  analysis: BarChart3,
  publish: Send,
  plan: Target,
}

function glyphOf(key: InspirationCategoryKey) {
  return GLYPHS[key] || Layers
}

const tone = computed(() => INSPIRATION_TONE[props.template.category])
const accent = computed(() => INSPIRATION_ACCENT[props.template.category])
</script>

<style scoped>
.tc {
  position: absolute;
  inset: 0;
  display: block;
  overflow: hidden;
}

/* 纸面：白色笔记卡斜放，底部被卡片裁掉，形成「一张卡片从画面里出来」的预览感 */
.tc-paper {
  position: absolute;
  left: 11%;
  right: 11%;
  top: 13%;
  bottom: -6%;
  display: flex;
  flex-direction: column;
  gap: 5px;
  padding: 9px 8px;
  border-radius: 8px;
  background: #fff;
  box-shadow: 0 8px 18px rgba(0, 0, 0, 0.18);
  transform: rotate(-3deg);
  overflow: hidden;
}

.tc-paper-title {
  flex: none;
  width: 72%;
  height: 7px;
  border-radius: 2px;
  background: #24242a;
}

.tc-paper-block {
  flex: none;
  height: 46%;
  margin: 2px 0;
  border-radius: 4px;
  background: var(--tc-ink);
}

.tc-paper-glyph {
  flex: none;
  display: flex;
  align-items: center;
  justify-content: center;
  height: 46%;
  margin: 2px 0;
  border-radius: 4px;
  color: var(--tc-ink);
  background: rgba(0, 0, 0, 0.045);
}

.tc-paper-line {
  flex: none;
  height: 4px;
  border-radius: 2px;
  background: rgba(0, 0, 0, 0.13);
}

.tc-paper-line.is-short {
  width: 62%;
}

/* 文案类纸面偏「文字稿」：强调块更矮，多留正文空间 */
.tc-paper.is-copy .tc-paper-glyph {
  height: 30%;
}

/* 视频类偏「分镜条」：强调块更宽更扁 */
.tc-paper.is-video .tc-paper-glyph {
  height: 34%;
}

.tc-scrim {
  position: absolute;
  inset: auto 0 0 0;
  height: 62%;
  background: linear-gradient(
    180deg,
    rgba(0, 0, 0, 0) 0%,
    rgba(0, 0, 0, 0.42) 46%,
    rgba(0, 0, 0, 0.72) 100%
  );
  pointer-events: none;
}

.tc-meta {
  position: absolute;
  right: 9px;
  bottom: 9px;
  left: 9px;
  display: flex;
  flex-direction: column;
  gap: 2px;
  transition: opacity 0.16s ease;
}

.tc-title {
  font-size: 12px;
  font-weight: 600;
  line-height: 16px;
  color: #fff;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  text-shadow: 0 1px 3px rgba(0, 0, 0, 0.35);
}

.tc-sub {
  font-size: 10px;
  line-height: 14px;
  color: rgba(255, 255, 255, 0.76);
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.tc-hot {
  position: absolute;
  top: 8px;
  left: 8px;
  height: 17px;
  padding: 0 5px;
  border-radius: 5px;
  background: rgba(255, 255, 255, 0.94);
  color: #e8484a;
  font-size: 10px;
  font-weight: 600;
  line-height: 17px;
}

.tc-use {
  position: absolute;
  right: 8px;
  bottom: 8px;
  left: 8px;
  height: 26px;
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.95);
  color: #1a1a1a;
  font-size: 11px;
  font-weight: 500;
  line-height: 26px;
  text-align: center;
  opacity: 0;
  transform: translateY(8px);
  transition: opacity 0.16s ease, transform 0.16s ease;
}

.tc:hover .tc-use,
.tc:focus-visible .tc-use {
  opacity: 1;
  transform: translateY(0);
}

.tc:hover .tc-meta {
  opacity: 0;
}
</style>