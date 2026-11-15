<template>
  <div v-if="items.length" class="cma-card">
    <div class="cma-head">
      <Video v-if="hasVideo" :size="14" class="cma-head-icon" />
      <FileText v-else :size="14" class="cma-head-icon" />
      <span class="cma-title">{{ hasVideo ? '成片已生成' : '媒体产物已生成' }}</span>
      <span class="cma-count">{{ items.length }} 个文件</span>
    </div>

    <div v-for="item in items" :key="item.url" class="cma-item">
      <video
        v-if="item.kind === 'video'"
        class="cma-video"
        :src="item.url"
        controls
        preload="metadata"
        playsinline
      />
      <audio v-else-if="item.kind === 'audio'" class="cma-audio" :src="item.url" controls preload="metadata" />
      <img v-else class="cma-image" :src="item.url" :alt="item.title" loading="lazy" />

      <div class="cma-meta">
        <span class="cma-name" :title="item.title">{{ item.title || '媒体产物' }}</span>
        <span class="cma-size" v-if="item.size">{{ humanSize(item.size) }}</span>
        <a class="cma-open" :href="item.url" target="_blank" rel="noopener">打开 ↗</a>
      </div>
    </div>

    <p class="cma-hint">已同步到「我的空间」，可直接下载或发布</p>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { FileText, Video } from 'lucide-vue-next'
import type { MediaArtifact } from './cell-types'

const props = defineProps<{ artifacts: MediaArtifact[] }>()

const items = computed(() => (props.artifacts || []).filter((a) => a && a.url))
const hasVideo = computed(() => items.value.some((a) => a.kind === 'video'))

function humanSize(bytes: number): string {
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}
</script>

<style scoped>
.cma-card {
  background: rgba(83, 74, 183, 0.06);
  border: 1px solid rgba(83, 74, 183, 0.22);
  border-radius:14px;
  padding: 10px 12px;
  margin-top: 8px;
}

.cma-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}

.cma-head-icon {
  color: var(--rl-accent);
}

.cma-title {
  font-size: 13px;
  font-weight: 500;
  color: var(--rl-text-1);
  flex: 1;
}

.cma-count {
  font-size: 11px;
  color: var(--rl-text-3);
  background: #e2e8f0;
  padding: 2px 8px;
  border-radius:8px;
}

.cma-item {
  margin-top: 6px;
}

.cma-video {
  width: 100%;
  max-height: 420px;
  border-radius:10px;
  background: #000;
  display: block;
}

.cma-audio {
  width: 100%;
  display: block;
}

.cma-image {
  width: 100%;
  border-radius:10px;
  display: block;
}

.cma-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 6px;
  font-size: 12px;
}

.cma-name {
  color: var(--rl-text-2);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
}

.cma-size {
  color: var(--rl-text-3);
}

.cma-open {
  color: var(--rl-accent);
  text-decoration: none;
  flex-shrink: 0;
}

.cma-hint {
  margin: 8px 0 0;
  font-size: 11px;
  color: var(--rl-text-3);
}
</style>