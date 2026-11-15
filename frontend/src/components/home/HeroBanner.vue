<template>
  <section class="bn">
    <div class="bn-left">
      <div class="bn-id">
        <div class="bn-avatar-wrap" ref="avatarWrap">
          <button
            class="bn-avatar-btn"
            type="button"
            title="账号"
            :aria-expanded="menuOpen"
            @click="menuOpen = !menuOpen"
          >
            <img v-if="avatarUrl" class="bn-avatar" :src="avatarUrl" alt="" />
            <div v-else class="bn-avatar bn-avatar-ph"><User :size="20" /></div>
          </button>

          <Transition name="bn-pop">
            <div v-if="menuOpen" class="bn-menu">
              <div class="bn-menu-head">
                <span class="bn-menu-name">{{ nickname }}</span>
                <span class="bn-menu-sub">{{ statusLabel }}</span>
              </div>
              <button class="bn-menu-item" type="button" @click="handleLogout">
                <LogOut :size="13" /><span>退出登录</span>
              </button>
            </div>
          </Transition>
        </div>
        <div class="bn-id-text">
          <div class="bn-name-row">
            <span class="bn-name">{{ nickname }}</span>
            <span class="bn-badge">{{ statusLabel }}</span>
          </div>
          <span class="bn-sub">{{ statusText }}</span>
        </div>
      </div>

      <div class="bn-nav">
        <button
          v-for="item in NAV_ITEMS"
          :key="item.key"
          type="button"
          class="bn-nav-item"
          :class="{ 'is-active': item.key === activePage }"
          :title="item.label"
          :aria-label="item.label"
          @click="emit('navigate', item.key)"
        >
          <component :is="item.icon" :size="14" :stroke-width="1.6" />
          <span>{{ item.label }}</span>
        </button>
      </div>

      <div class="bn-stats">
        <template v-for="(s, i) in stats" :key="s.label">
          <span v-if="i > 0" class="bn-div"></span>
          <span class="bn-stat"><b>{{ s.value }}</b>{{ s.label }}</span>
        </template>
      </div>

      <div class="bn-actions">
        <button class="bn-btn" @click="emit('create')">开启新创作</button>
        <button class="bn-ghost" @click="emit('tasks')">任务清单</button>
      </div>
    </div>

    <div class="bn-media"><HeroCarousel /></div>
  </section>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted, ref, type Component } from 'vue'
import {
  Blocks,
  ChartNoAxesCombined,
  Home,
  LayoutGrid,
  Lightbulb,
  LogOut,
  User,
} from 'lucide-vue-next'
import HeroCarousel from './HeroCarousel.vue'

const NAV_ITEMS: Array<{ key: string; label: string; icon: Component }> = [
  { key: 'home', label: '首页', icon: Home },
  { key: 'topic-pool', label: '选题池', icon: Lightbulb },
  { key: 'my-works', label: '数据分析', icon: ChartNoAxesCombined },
  { key: 'portfolio', label: '我的空间', icon: LayoutGrid },
  { key: 'skills', label: '技能库', icon: Blocks },
]

defineProps<{
  avatarUrl?: string | null
  nickname: string
  statusLabel: string
  statusText: string
  stats: Array<{ label: string; value: string | number }>
  activePage?: string
}>()

const emit = defineEmits<{
  create: []
  tasks: []
  logout: []
  navigate: [page: string]
}>()

const menuOpen = ref(false)
const avatarWrap = ref<HTMLElement | null>(null)

function handleLogout() {
  menuOpen.value = false
  emit('logout')
}

function onDocClick(e: MouseEvent) {
  if (!menuOpen.value) return
  if (avatarWrap.value && !avatarWrap.value.contains(e.target as Node)) {
    menuOpen.value = false
  }
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') menuOpen.value = false
}

onMounted(() => {
  document.addEventListener('click', onDocClick)
  document.addEventListener('keydown', onKeydown)
})

onUnmounted(() => {
  document.removeEventListener('click', onDocClick)
  document.removeEventListener('keydown', onKeydown)
})
</script>

<style scoped>
.bn {
  position: relative;
  display: flex;
  align-items: center;
  gap: 32px;
  height: 100%;
}

.bn-left {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 15px;
}

.bn-media {
  flex: 0 0 auto;
  height: var(--hp-poster-h, 200px);
  margin-right: var(--hp-gap, 14px);
  aspect-ratio: 16 / 9;
  overflow: hidden;
  background: #0B0B0C;
  border-radius: 12px;
  box-shadow: inset 0 0 0 1px rgba(0, 0, 0, .04);
}

.bn-id { display: flex; align-items: center; gap: 12px; }
.bn-avatar-wrap { position: relative; flex-shrink: 0; }
.bn-avatar-btn { display: block; padding: 0; border: none; background: transparent; cursor: pointer; border-radius: 50%; transition: box-shadow 0.15s, transform 0.15s; }
.bn-avatar-btn:hover { box-shadow: 0 0 0 3px rgba(0, 0, 0, 0.05); }
.bn-avatar-btn:active { transform: scale(0.96); }
.bn-avatar-btn:focus-visible { outline: none; box-shadow: 0 0 0 3px rgba(255, 36, 66, 0.18); }
.bn-avatar { width: 46px; height: 46px; border-radius: 50%; object-fit: cover; flex-shrink: 0; background: #F2F2F2; display: block; }
.bn-avatar-ph { display: flex; align-items: center; justify-content: center; color: #B0B0B0; }

/* 头像下拉菜单 */
.bn-menu {
  position: absolute;
  top: calc(100% + 8px);
  left: 0;
  z-index: 20;
  min-width: 168px;
  padding: 6px;
  border-radius: 12px;
  background: #fff;
  border: 1px solid #EDEDED;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.1), 0 2px 6px rgba(0, 0, 0, 0.04);
}
.bn-menu-head { display: flex; flex-direction: column; gap: 1px; padding: 7px 9px 9px; border-bottom: 1px solid #F2F2F4; margin-bottom: 4px; }
.bn-menu-name { font-size: 13px; font-weight: 500; color: var(--rl-text-1, #1A1A1A); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.bn-menu-sub { font-size: 11px; color: #9A9A9A; }
.bn-menu-item { display: flex; align-items: center; gap: 7px; width: 100%; padding: 7px 9px; border: none; border-radius: 8px; background: transparent; color: #B42318; font-size: 13px; text-align: left; cursor: pointer; transition: background 0.15s; }
.bn-menu-item:hover { background: #FEF2F2; }
.bn-pop-enter-active, .bn-pop-leave-active { transition: opacity 0.14s ease, transform 0.14s ease; }
.bn-pop-enter-from, .bn-pop-leave-to { opacity: 0; transform: translateY(-4px) scale(0.97); }

.bn-id-text { min-width: 0; display: flex; flex-direction: column; gap: 3px; }

/* 头像下方快捷导航（与侧边栏导航区一致） */
.bn-nav { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; }
.bn-nav-item { display: inline-flex; align-items: center; gap: 4px; height: 26px; padding: 0 9px; border: 1px solid #ECECEE; border-radius: 6px; background: #fff; color: var(--rl-text-2, #4B4B4B); font-size: 12px; line-height: 1; cursor: pointer; transition: background 0.15s, border-color 0.15s, color 0.15s; }
.bn-nav-item svg { flex: none; }
.bn-nav-item:hover { background: #F5F5F5; border-color: #DCDCE0; color: var(--rl-text-1, #1A1A1A); }
.bn-nav-item.is-active { color: #FF2442; border-color: #FFD7DE; background: #FFF5F7; }
.bn-name-row { display: flex; align-items: center; gap: 7px; min-width: 0; }
.bn-name { font-size: 14px; font-weight: 500; line-height: 1.3; letter-spacing: -0.1px; color: var(--rl-text-1, #1A1A1A); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.bn-badge { flex-shrink: 0; padding: 0 6px; height: 17px; display: inline-flex; align-items: center; border-radius: 4px; background: #ECFDF5; color: #15803D; font-size: 11px; font-weight: 500; }
.bn-sub { font-size: 12px; line-height: 1.4; color: #9A9A9A; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

.bn-stats { display: flex; align-items: center; gap: 14px; }
.bn-div { width: 1px; height: 11px; background: #E5E5E5; }
.bn-stat { font-size: 12px; color: #9A9A9A; white-space: nowrap; }
.bn-stat b { margin-right: 3px; font-size: 13px; font-weight: 500; color: var(--rl-text-1, #1A1A1A); font-variant-numeric: tabular-nums; }

.bn-actions { display: flex; align-items: center; gap: 8px; }

.bn-btn {
  height: 32px; padding: 0 16px; border: none; border-radius: 8px;
  background: #FF2442; color: #fff; font-size: 13px; font-weight: 500;
  cursor: pointer; transition: background 0.15s;
}
.bn-btn:hover { background: #E01D3A; }

.bn-ghost {
  height: 32px; padding: 0 14px; border: 1px solid #E5E5E5; border-radius: 8px;
  background: #fff; color: var(--rl-text-2, #4B4B4B); font-size: 13px; cursor: pointer;
  transition: background 0.15s, border-color 0.15s, color 0.15s;
}
.bn-ghost:hover { background: #F5F5F5; border-color: #D4D4D8; color: var(--rl-text-1, #1A1A1A); }

@media (max-width: 1180px) {
  .bn { gap: 24px; }
}

@media (max-width: 1080px) {
  .bn-name { font-size: 14px; }
  .bn-avatar { width: 42px; height: 42px; }
  .bn-nav-item { padding: 0 8px; }
  .bn-nav-item span { display: none; }
}

@media (max-width: 860px) {
  .bn {
    flex-direction: column-reverse;
    align-items: stretch;
    height: auto;
    gap: 16px;
    padding-bottom: 4px;
  }
  .bn-media {
    width: 100%;
    height: auto;
    max-height: 230px;
    margin-right: 0;
    border-radius: 12px;
  }
  .bn-left { gap: 13px; }
  .bn-sub { white-space: normal; }
  .bn-nav-item span { display: inline; }
}
</style>