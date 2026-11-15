<template>
  <router-view />
  <SettingsView v-if="showSettings" @close="closeSettings" />
  <Teleport to="body">
    <Transition name="ad-fade">
      <div v-if="showAd" class="ad-overlay" @click.self="closeAd">
        <div class="ad-card">
          <button class="ad-close" type="button" aria-label="关闭广告" @click="closeAd">
            <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
          </button>
          <img class="ad-img" src="/icons/pulse_poster.svg" alt="活动海报" />
        </div>
      </div>
    </Transition>
  </Teleport>
</template>
<script setup lang="ts">
import { defineAsyncComponent, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useUIState } from '@/composables/useUIState'

const SettingsView = defineAsyncComponent(() => import('@/views/SettingsView.vue'))
const { showSettings, closeSettings } = useUIState()

const SK_WELCOME_AD = 'mint_show_welcome_ad'
const route = useRoute()
const showAd = ref(false)

function consumeWelcomeAd() {
  if (!sessionStorage.getItem(SK_WELCOME_AD)) return
  sessionStorage.removeItem(SK_WELCOME_AD)
  showAd.value = true
}

function closeAd() {
  showAd.value = false
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape' && showAd.value) closeAd()
}

watch(() => route.fullPath, consumeWelcomeAd)

onMounted(() => {
  consumeWelcomeAd()
  window.addEventListener('app:show-welcome-ad', consumeWelcomeAd)
  window.addEventListener('keydown', onKeydown)
})

onUnmounted(() => {
  window.removeEventListener('app:show-welcome-ad', consumeWelcomeAd)
  window.removeEventListener('keydown', onKeydown)
})
</script>
<style scoped>
.ad-overlay {
  position: fixed;
  inset: 0;
  z-index: 1200;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  background: rgba(0, 0, 0, 0.55);
  backdrop-filter: blur(3px);
}

.ad-card {
  position: relative;
  width: min(1100px, 92vw);
  max-height: 88vh;
  border-radius: 18px;
  overflow: hidden;
  background: #f3f2f0;
  box-shadow: 0 24px 64px rgba(0, 0, 0, 0.45);
}

.ad-img {
  display: block;
  width: 100%;
  height: auto;
  max-height: 88vh;
  object-fit: contain;
}

.ad-close {
  position: absolute;
  top: 12px;
  right: 12px;
  z-index: 2;
  width: 32px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  border: none;
  border-radius: 50%;
  background: rgba(0, 0, 0, 0.45);
  color: #fff;
  cursor: pointer;
  transition: background 0.2s ease, transform 0.2s ease;
}

.ad-close:hover {
  background: rgba(0, 0, 0, 0.7);
  transform: scale(1.06);
}

.ad-fade-enter-active,
.ad-fade-leave-active {
  transition: opacity 0.28s ease;
}

.ad-fade-enter-from,
.ad-fade-leave-to {
  opacity: 0;
}

.ad-fade-enter-active .ad-card {
  transition: transform 0.28s cubic-bezier(0.16, 1, 0.3, 1);
}

.ad-fade-enter-from .ad-card {
  transform: scale(0.94);
}
</style>