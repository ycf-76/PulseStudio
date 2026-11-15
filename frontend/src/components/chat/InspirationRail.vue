<template>
  <section class="ir" :class="{ 'is-collapsed': collapsed, 'is-grid': expanded, 'is-intro': props.introPlaying }">
    <div class="ir-head">
      <span class="ir-brand">
        <Lightbulb :size="12" class="ir-brand-icon" />
        灵感模板
      </span>

      <div class="ir-tabs" role="tablist" aria-label="灵感模板分类">
        <button
          v-for="cat in categories"
          :key="cat.key"
          type="button"
          role="tab"
          class="ir-tab"
          :class="{ 'is-active': cat.key === activeCategory }"
          :aria-selected="cat.key === activeCategory"
          @click="onTab(cat.key)"
        >
          <component :is="iconOf(cat.icon)" :size="12" class="ir-tab-icon" />
          <span class="ir-tab-label">{{ cat.label }}</span>
        </button>
      </div>

      <div class="ir-actions">
        <button type="button" class="ir-act" @click="toggleExpanded">
          {{ expanded ? '收起' : '全部' }}
        </button>
        <button
          type="button"
          class="ir-act ir-act-icon"
          :title="collapsed ? '展开灵感模板' : '收起灵感模板'"
          @click="toggleCollapsed"
        >
          <ChevronUp v-if="collapsed" :size="13" />
          <ChevronDown v-else :size="13" />
        </button>
      </div>
    </div>

    <div v-if="!collapsed" class="ir-body">
      <div v-if="expanded" class="ir-groups">
        <div v-for="group in groupedTemplates" :key="group.category.key" class="ir-group">
          <div class="ir-group-title">
            <component :is="iconOf(group.category.icon)" :size="12" class="ir-group-icon" />
            <span>{{ group.category.label }}</span>
            <span class="ir-group-count">{{ group.items.length }}</span>
          </div>
          <div class="ir-group-grid">
            <button
              v-for="tpl in group.items"
              :key="tpl.id"
              type="button"
              class="ir-card"
              :title="tpl.prompt"
              :aria-label="`使用模板：${tpl.title}`"
              @click="onPick(tpl)"
            >
              <TemplateCover :template="tpl" />
            </button>
          </div>
        </div>
      </div>

      <div v-else ref="trackRef" class="ir-track" data-axis="vertical">
        <button
          v-for="tpl in visibleTemplates"
          :key="tpl.id"
          type="button"
          class="ir-card"
          :title="tpl.prompt"
          :aria-label="`使用模板：${tpl.title}`"
          @click="onPick(tpl)"
        >
          <TemplateCover :template="tpl" />
        </button>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { BarChart3, ChevronDown, ChevronUp, Layers, LayoutGrid, Lightbulb, PenLine, Play, Send, Sparkles, Target } from 'lucide-vue-next'
import TemplateCover from './InspirationTemplateCover.vue'
import {
  INSPIRATION_CATEGORIES,
  useInspirationTemplates,
  type InspirationFilterKey,
  type InspirationIconKey,
  type InspirationTemplate,
} from './composables/useInspirationTemplates'

const props = withDefaults(defineProps<{ introPlaying?: boolean }>(), { introPlaying: false })

const emit = defineEmits<{ pick: [prompt: string] }>()

const ICONS: Record<InspirationIconKey, unknown> = {
  layers: Layers,
  pen: PenLine,
  play: Play,
  chart: BarChart3,
  send: Send,
  target: Target,
  sparkles: Sparkles,
  grid: LayoutGrid,
}

function iconOf(key: InspirationIconKey) {
  return ICONS[key] || Sparkles
}

const categories = INSPIRATION_CATEGORIES
const { activeCategory, visibleTemplates, groupedTemplates, selectCategory } = useInspirationTemplates()

const COLLAPSE_KEY = 'pulse_inspiration_rail_collapsed'

function readCollapsed() {
  try {
    const saved = localStorage.getItem(COLLAPSE_KEY)
    if (saved === null) return true
    return saved === '1'
  } catch {
    return true
  }
}

const collapsed = ref(readCollapsed())
const expanded = ref(false)
const trackRef = ref<HTMLElement | null>(null)

function toggleCollapsed() {
  collapsed.value = !collapsed.value
  try {
    localStorage.setItem(COLLAPSE_KEY, collapsed.value ? '1' : '0')
  } catch {
    // 隐私模式下 localStorage 不可写，仅影响下次是否记住折叠状态
  }
}

function toggleExpanded() {
  expanded.value = !expanded.value
  if (!expanded.value && trackRef.value) trackRef.value.scrollTop = 0
}

function onTab(key: InspirationFilterKey) {
  selectCategory(key)
  if (collapsed.value) toggleCollapsed()
  if (expanded.value) expanded.value = false
}

function onPick(tpl: InspirationTemplate) {
  emit('pick', tpl.prompt)
}
</script>

<style scoped>
.ir {
  /* 定高橱窗，不是弹性填充块。
     参考 Kimi/豆包首页「一排功能入口」与 NN/g 提示建议规范：推荐区是输入框的附属物，
     只给固定高度（一行卡片 + 半行露出提示可滚动），绝不 flex:1 吃掉整页剩余空间——
     一旦吃掉，26 张封面会把首屏撑成素材库，输入框反而沦为配角。 */
  --ir-h: clamp(156px, 34vh, 340px);
  /* 列宽固定、行高由列宽推算：卡片是 <button>，浏览器给它的固有高度只算内容，
     aspect-ratio 撑出来的高度不参与网格行高，行会按内容高（约 52px）排下去，
     结果每行都压到下一行卡片上。这里把行高显式钉死，杜绝叠压。 */
  --ir-cw: 108px;
  --ir-ch: calc(var(--ir-cw) * 4 / 3);
  /* 选中页签文字色：暖调深灰，不用纯黑（--rl-text-1 在亮色下是 #1A1A1A，
     压在中性浅底上过硬）。暗色由下方 .rl-chat.is-dark 覆盖为暖白。 */
  --ir-active-ink: #33322E;
  width: 100%;
  max-width: 680px;
  margin: 0 auto;
  padding: 0 16px 12px;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  flex: none;
  height: var(--ir-h);
  align-self: center;
}

.ir.is-collapsed {
  height: auto;
  padding-bottom: 0;
}

/* 新创作入场：等输入卡（1.8s）落位后再浮现，节奏对齐 hero 区的 capabilities/starters */
.ir.is-intro {
  animation: ir-intro-in 0.8s cubic-bezier(0.16, 1, 0.3, 1) 2.4s both;
}

@keyframes ir-intro-in {
  from { opacity: 0; transform: translateY(12px); }
  to { opacity: 1; transform: translateY(0); }
}

.ir-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 2px 7px;
}

.ir-brand {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  flex: none;
  font-size: 12px;
  line-height: 17px;
  color: var(--rl-text-3);
  letter-spacing: 0.02em;
}

.ir-brand-icon {
  color: var(--rl-text-2);
}

.ir-tabs {
  display: flex;
  align-items: center;
  gap: 5px;
  flex: 1;
  min-width: 0;
  overflow-x: auto;
  scrollbar-width: none;
}

.ir-tabs::-webkit-scrollbar {
  display: none;
}

.ir-tab {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  flex: none;
  height: 28px;
  padding: 0 10px;
  border: 1px solid var(--rl-border-thin);
  border-radius: 8px !important;
  background: var(--rl-card);
  color: var(--rl-text-2);
  font-family: inherit;
  font-size: 13px;
  line-height: 26px;
  cursor: pointer;
  transition: color 0.15s ease, background-color 0.15s ease, border-color 0.15s ease;
}

.ir-tab:hover {
  color: var(--rl-text-1);
  background: var(--rl-hover-solid);
  border-color: var(--rl-border-strong);
}

/* 选中态：中性暖灰实心片 + 加粗文字，不再用主色紫。
   与 .rl-pill-active 的「浅底 + 强边框」语言一致，只是这里再加一档对比度，
   避免和 hover（--rl-hover-solid）糊成同一种灰。 */
.ir-tab.is-active {
  color: var(--ir-active-ink);
  background: var(--rl-chip);
  border-color: var(--rl-text-3);
  font-weight: 600;
}

/* 图标与标签文字同色（--rl-text-2），不再比文字浅一档：
   灰色图标若比文字淡，放大字号后会显得「字重、图标虚」。 */
.ir-tab-icon {
  flex: none;
  color: var(--rl-text-2);
}

.ir-tab.is-active .ir-tab-icon {
  color: var(--ir-active-ink);
}

.ir-actions {
  display: flex;
  align-items: center;
  gap: 3px;
  flex: none;
}

.ir-act {
  height: 28px;
  padding: 0 10px;
  border: 1px solid var(--rl-border-thin);
  border-radius: 8px !important;
  background: var(--rl-card);
  color: var(--rl-text-2);
  font-family: inherit;
  font-size: 12px;
  line-height: 26px;
  cursor: pointer;
  transition: color 0.15s ease, background-color 0.15s ease, border-color 0.15s ease;
}

.ir-act:hover {
  color: var(--rl-text-1);
  background: var(--rl-hover-solid);
  border-color: var(--rl-border-strong);
}

.ir-act-icon {
  padding: 0 5px;
}

.ir-body {
  flex: 1 1 auto;
  min-height: 0;
  display: flex;
  flex-direction: column;
  animation: ir-fade-in 0.22s ease-out both;
}

@keyframes ir-fade-in {
  from { opacity: 0; transform: translateY(-4px); }
  to { opacity: 1; transform: translateY(0); }
}

/* 卡片流：固定列宽网格 + 竖向滚动。
   列宽写成固定像素而不是 minmax(…,1fr)：1fr 会随容器变宽把卡片拉高，
   3:4 比例下高度失控，定高橱窗的「露出半行」就失准了。列数由 auto-fill 决定，
   多余宽度用 justify-content: center 居中，窄屏自动减列。 */
.ir-track {
  flex: 1 1 auto;
  min-height: 0;
  display: grid;
  grid-template-columns: repeat(auto-fill, var(--ir-cw));
  grid-auto-rows: var(--ir-ch);
  justify-content: center;
  gap: 8px;
  align-content: start;
  overflow-x: hidden;
  overflow-y: auto;
  overscroll-behavior: contain;
  padding: 3px 2px 6px;
  scrollbar-width: thin;
  scrollbar-color: var(--rl-scrollbar) transparent;
}

.ir-track::-webkit-scrollbar {
  width: 6px;
  height: 0;
}

.ir-track::-webkit-scrollbar-thumb {
  border-radius: 3px;
  background: var(--rl-scrollbar);
}

.ir-card {
  position: relative;
  display: block;
  width: 100%;
  height: var(--ir-ch);
  padding: 0;
  border: 1px solid var(--rl-border-thin);
  /* !important 用于压过 main.css 里对 button 的全局 9999px 全圆角（模板卡是内容块不是控件） */
  border-radius: 12px !important;
  background: var(--rl-card);
  font-family: inherit;
  text-align: left;
  overflow: hidden;
  cursor: pointer;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.05), 0 4px 10px rgba(0, 0, 0, 0.05);
  transition: transform 0.18s ease, box-shadow 0.18s ease, border-color 0.18s ease;
}

.ir-card:hover {
  border-color: var(--rl-border-strong);
  transform: translateY(-3px);
  box-shadow: 0 8px 22px rgba(0, 0, 0, 0.14);
}

.ir-card:active {
  transform: translateY(-1px);
}

/* 全部分类视图：同样交给外层 flex 分配高度，内部竖向滚动 */
.ir-groups {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  overscroll-behavior: contain;
  padding: 2px 2px 6px;
  scrollbar-width: thin;
  scrollbar-color: var(--rl-scrollbar) transparent;
}

.ir-group + .ir-group {
  margin-top: 12px;
}

.ir-group-title {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 0 2px 7px;
  font-size: 12px;
  line-height: 17px;
  font-weight: 500;
  color: var(--rl-text-2);
}

.ir-group-icon {
  color: var(--rl-text-2);
}

.ir-group-count {
  font-size: 10px;
  font-weight: 400;
  color: var(--rl-text-3);
}

.ir-group-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, var(--ir-cw));
  grid-auto-rows: var(--ir-ch);
  justify-content: center;
  gap: 8px;
}

/* 暗色下选中文字换暖白：深底上深灰字几乎不可读（不是纯白，保留暖纸调性） */
.rl-chat.is-dark .ir {
  --ir-active-ink: #E7E3DA;
}

/* 窄容器里先去掉分类图标，保证 7 个分类标签一行排得下、不横滚 */
@media (max-width: 1100px) {
  .ir-tab-icon {
    display: none;
  }
  .ir-tab {
    padding: 0 8px;
  }
}

@media (max-width: 880px) {
  .ir-brand {
    display: none;
  }
  .ir-tab {
    padding: 0 7px;
  }
  .ir-act {
    padding: 0 7px;
  }
}

/* 竖向空间不足时只压缩尺寸、不隐藏：橱窗高度由 --ir-h 的 vh 部分自然缩放，
   卡片区自身仍可竖向滚动，藏掉整个入口等于把功能从用户手里拿走
   （原 max-height:760px 直接 display:none 的根因问题） */
@media (max-height: 820px) {
  .ir {
    padding-bottom: 10px;
  }
  .ir-track {
    gap: 7px;
  }
  .ir-tab {
    height: 26px;
    font-size: 12px;
    line-height: 24px;
  }
  .ir-act {
    height: 26px;
    font-size: 11px;
    line-height: 24px;
  }
}

@media (max-height: 700px) {
  .ir {
    --ir-cw: 92px;
    padding-bottom: 8px;
  }
  .ir-head {
    padding-bottom: 5px;
  }
  .ir-track {
    gap: 7px;
  }
  .ir-group-grid {
    gap: 7px;
  }
}
</style>