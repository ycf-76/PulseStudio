<!--
  ThinkActivityIndicator — 思考中的活动指示器。
  1:1 对齐 Claude 桌面端 AssistantThinkingCompactStatus：28px 星芒动画
  （thinking.json 为真身 lottie 解包+图片内嵌版本，8 层位图序列 7fps 循环）。
  播放器用 lottie-web（纯 JS，无 wasm/CDN 依赖——dotlottie 的 wasm 在 dev
  环境 404 后 fallback unpkg 拉取失败会静默空白）。
  renderer 用 svg：canvas 渲染器在 28px inline 容器里只画出中心一点
  （2026-10-22 大哥实测），svg 自适应容器尺寸且位图资产照常渲染。
  仅思考中渲染；完成后由父组件切换为静态摘要行。
-->
<template>
  <span class="rl-think-activity">
    <div ref="boxRef" class="rl-think-activity-box" />
  </span>
</template>

<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref } from 'vue'
import lottie from 'lottie-web/build/player/lottie'

const boxRef = ref<HTMLDivElement | null>(null)
let anim: { destroy: () => void } | null = null

onMounted(async () => {
  if (!boxRef.value) return
  // 运行时 fetch json：老 dev server 进程的 import-analysis 缓存识别不了
  // 新建的 assets 子目录（import 会 404），浏览器原生 URL 解析无此问题；
  // build 时 vite 会把 new URL 资产引用静态化，两条路都通。
  // 注意：相对路径以本文件（src/components/chat/）为基准，需上跳两级到 src/assets
  const url = new URL('../../assets/message-activity/thinking.json', import.meta.url).href
  try {
    const res = await fetch(url)
    if (!res.ok) return
    const animationData = await res.json()
    if (!boxRef.value) return
    anim = lottie.loadAnimation({
      container: boxRef.value,
      renderer: 'svg',
      loop: true,
      autoplay: true,
      animationData,
    }) as unknown as { destroy: () => void }
  } catch {
    // 动画资源加载失败时保持空占位，不影响思考状态本身
  }
})

onBeforeUnmount(() => {
  anim?.destroy()
  anim = null
})
</script>

<style scoped>
.rl-think-activity {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  flex: none;
  line-height: 0;
}
.rl-think-activity-box {
  width: 28px;
  height: 28px;
}
.rl-think-activity-box :deep(svg) {
  width: 28px;
  height: 28px;
  display: block;
}
</style>