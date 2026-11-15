<template>
  <div class="mr-lb-iframe-wrapper" :style="wrapperStyle">
    <iframe
      sandbox="allow-same-origin allow-scripts"
      :srcdoc="page.htmlContent"
      class="mr-lb-iframe"
      :style="iframeStyle"
    ></iframe>
  </div>
</template>

<script setup lang="ts">
import type { MediaPage } from '@/types/media-content'

defineProps<{
  page: MediaPage
  wrapperStyle?: Record<string, string | number>
  iframeStyle?: Record<string, string | number>
}>()
</script>

<style scoped>
.mr-lb-iframe-wrapper {
  border-radius:12px;
  overflow: hidden;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.4);
}
.mr-lb-iframe {
  border: none;
  border-radius:12px;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.4);
  background: #fff;
}
</style>