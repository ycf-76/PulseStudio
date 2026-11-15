<template>
  <div class="md-view rl-content-scroll">
    <div v-if="title" class="rl-content-head">
      <h3 class="rl-content-title">{{ title }}</h3>
      <span v-if="meta" class="rl-content-meta">{{ meta }}</span>
    </div>
    <!--
      md-view-body 同时挂 rl-md：ChatView.vue 全局 style 里 82 条 .rl-md * 规则
      （标题/列表/表格/引用/链接/代码块 chrome/hljs 语法色/kv-list/card iframe）
      对非 scoped 的全局样式生效，这里直接复用，不再本地重复维护一套。
      此前本地只有裸元素规则，且 :deep(pre) 永远匹配不到（markdown-renderer
      覆写了 fence/code_block 规则，不输出 <pre>），导致代码块既无 chrome
      又多行文本塌陷。
    -->
    <div class="md-view-body rl-md" v-html="renderedHtml"></div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { renderMarkdown } from './markdown-renderer'
import { fenceBareJson } from './chat-content-renderer'

const props = defineProps<{
  content: string
  title?: string
  meta?: string
}>()

// fenceBareJson：整段是裸 JSON 时包成 ```json 代码块，避免原文当普通段落塌成一片
const renderedHtml = computed(() => {
  if (!props.content) return ''
  return renderMarkdown(fenceBareJson(props.content))
})
</script>

<style scoped>
.md-view {
  display: flex;
  flex-direction: column;
  min-height: 100%;
  overflow-y: auto;
}

/* .rl-md 的字号/颜色/行高全部 inherit，必须由容器显式给出 */
.md-view-body {
  padding: 12px;
  font-size: 13px;
  line-height: 1.7;
  color: var(--rl-text-1, #1A1A1A);
}

/* 空状态走共享色板（原来是内联 style="color:#aaa"） */
.md-view-body:empty::after {
  content: '暂无内容';
  display: block;
  color: var(--rl-text-3, #9A9A9A);
  font-size: 13px;
}
</style>
