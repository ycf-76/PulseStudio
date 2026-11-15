<template>
  <div class="script-view rl-content-scroll">
    <div class="rl-content-head">
      <h3 class="rl-content-title">{{ title || '脚本' }}</h3>
      <span v-if="meta" class="rl-content-meta">{{ meta }}</span>
    </div>
    <div class="sv-body">
      <div v-if="!scriptText" class="rl-plain-empty">暂无脚本内容</div>
      <!-- 脚本是纯文本（非 markdown）：pre-wrap 保留换行，等宽变体走共享色板 -->
      <pre v-else class="rl-plain rl-plain--mono">{{ scriptText }}</pre>
    </div>
  </div>
</template>

<script setup lang="ts">
defineProps<{
  scriptText: string
  title?: string
  meta?: string
}>()
</script>

<style scoped>
.script-view {
  display: flex;
  flex-direction: column;
  min-height: 100%;
  overflow-y: auto;
}

.sv-body {
  padding: 12px;
  flex: 1;
}
</style>
