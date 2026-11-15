<template>
  <!-- 侧会话摘要特殊消息（PRD FR-B3/B4）：独立视觉样式 + 来源标注，默认展开 -->
  <div class="sd-cell" data-testid="side-digest-cell">
    <div class="sd-banner" @click="expanded = !expanded">
      <GitBranch :size="13" class="sd-banner-icon" />
      <span class="sd-banner-text">来自侧会话{{ sideSessionTitle ? `「${sideSessionTitle}」` : '' }}的结论摘要</span>
      <ChevronDown :size="13" class="sd-chevron" :class="{ 'sd-chevron--open': expanded }" />
    </div>
    <div v-show="expanded" class="sd-body">{{ content }}</div>
  </div>
</template>

<script setup lang="ts">
/**
 * 侧会话摘要特殊消息单元（Task 8 / PRD FR-B3）：
 * - 独立视觉样式与来源标注（G3/G4）；
 * - 默认展开（Q8），可折叠；
 * - 参与主任务上下文，但不可被普通追问展开为侧会话原文。
 */
import { ref } from 'vue'
import { GitBranch, ChevronDown } from 'lucide-vue-next'

defineProps<{
  content: string
  sideSessionTitle?: string
  sideSessionId?: string
}>()

const expanded = ref(true)
</script>

<style scoped>
.sd-cell {
  margin: 6px 0;
  border: 1px solid #e8d9b8;
  border-radius: 10px;
  background: #fdf8ec;
  overflow: hidden;
}
.sd-banner {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 10px;
  cursor: pointer;
  user-select: none;
  color: #8a6d2f;
  font-size: 12px;
}
.sd-banner-icon { flex: none; }
.sd-banner-text { flex: 1; font-weight: 600; }
.sd-chevron { transition: transform 0.15s; flex: none; }
.sd-chevron--open { transform: rotate(180deg); }
.sd-body {
  padding: 8px 12px 10px;
  font-size: 13px;
  line-height: 1.6;
  color: #4a3d20;
  white-space: pre-wrap;
  word-break: break-word;
  border-top: 1px dashed #e8d9b8;
}
</style>
