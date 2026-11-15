<template>
  <!-- 原型 progCell：无边框、无底色、无进度条，仅一行 space-between
       （左：当前在做什么；右：百分比 tabular-nums）。
       原 2px 进度条与步骤清单已删除——步骤清单与 ToolCallCell 的执行轨迹
       重复渲染同一份数据（三方同轨），进度条则是原型明确没有的装饰。 -->
  <div class="rl-ap-card">
    <div class="rl-ap-header">
      <span>{{ statusText }}</span>
      <span v-if="(totalPercent ?? 0) > 0" class="rl-ap-percent">{{ totalPercent ?? 0 }}%</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { AgentStep } from './cell-types'

// steps 仍由 AssistantMessageCell 传入但本组件不再渲染：步骤清单与 ToolCallCell
// 的执行轨迹是同一份数据的第二、三处渲染（原型 progCell 只留一行状态文字）。
// 保留签名以免父组件改动面扩大，实际展示只取 workflowStatus + totalPercent。
const props = defineProps<{
  steps: AgentStep[]
  workflowStatus?: string
  totalPercent?: number
}>()

const statusText = computed(() => {
  switch (props.workflowStatus) {
    case 'completed': return '工作流完成'
    case 'error': return '工作流出错'
    case 'awaiting_review': return '等待审核'
    case 'suspended': return '已暂停'
    default: return '执行中…'
  }
})
</script>

<style scoped>
/* 原型 progCell：无边框、无底色、无进度条。
   容器 margin-top:8px、11px、text-tertiary，一行 space-between（文案 / 百分比）。 */
.rl-ap-card {
  background: transparent;
  border: none;
  border-radius: 0;
  margin-top: 8px;
  padding: 0;
}

.rl-ap-header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  font-size: 11px;
  font-weight: 400;
  color: var(--rl-ink-3, #888780);
}

.rl-ap-percent {
  color: var(--rl-ink-3, #888780);
  font-variant-numeric: tabular-nums;
  font-size: 11px;
  flex: none;
}
</style>
