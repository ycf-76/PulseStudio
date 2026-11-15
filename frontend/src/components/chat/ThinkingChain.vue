<!--
  ThinkingChain — 思考时间线通用渲染器（架构第 ③ 层）。
  1:1 移植真身 AssistantThinkingChain.tsx（claude-desktop-app src/ 渲染层）：
  - 无框，左侧 1px 分段导轨连接图标行
  - 每行 = 15px 真身 SVG 图标（thought-chain assets）+ 14px 文字
  - 当前执行行 TextShimmer 扫光（灰渐变 220%、3.2s、白波紫晕）+ 执行行白晕呼吸
  - 行入场 220ms cubic-bezier(.22,1,.36,1) + 每行 45ms 延迟
  - ext 行（R1 思考）：200px 限高 + 底部渐隐 + Show more
  -   2026-11-06 二次改版（竞品对齐 · 大哥反馈）：标题行只做状态指示（思考中●●● + 当前动作 /
    完成态「已思考 Xs·N 步骤」），不再内嵌思考全文打字机（之前思考中+正文挤一行、常驻顶部，
    长链路展开后看不清过程）。思考全文移到展开区顶部「思考内容」子块（完整文本、实时增长、
    块内自滚）；.tc-fold 展开后 max-height 自滚，正文始终在思考块之外可见。
  只认 ThoughtEvent 模型（useThoughtEvents 归一化产物）——任何任务类型
  的事件流都被兼容渲染，渲染器永不因业务扩展而改动。
-->
<template>
  <div class="tc-root" :class="{ 'tc-live': isThinking, 'tc-open': open }">
    <button type="button" class="tc-sum" @click="toggle">
      <!-- 2026-11-06 二次改版（竞品对齐 · 大哥反馈）：标题行只做「状态指示」，
           不再内嵌思考全文打字机（之前思考中+正文挤一行、常驻顶部，长链路展开后看不清过程）。
           - 思考中：显示「思考中●●●」+ 当前动作短句（statusText，如"搜索网络资料"），脉冲点呼吸；
           - 完成态：显示「已思考 Xs · N 步骤」。
           思考全文移到展开区顶部「思考内容」子块（完整文本、实时增长、块内自滚），与正文解耦。 -->
      <!-- 2026-11-07 四次改版修复：tag 必须挂 v-if——之前无条件渲染，
           完成态会同时显示「思考中..」+「已思考 Xs」（大哥截图实锤） -->
      <span v-if="isThinking" class="tc-sum-tag">思考中<span class="rl-quiet-dots" aria-hidden="true"><i class="rl-quiet-dot"></i><i class="rl-quiet-dot"></i><i class="rl-quiet-dot"></i></span></span>
      <!-- 2026-11-14：确认闸门暂停态（guardian/clarify/storyboard/cut_plan/side_write）——
           显「等待确认」，不再出现「思考中」与已关桶的「思考完成」同框的矛盾态 -->
      <span v-else-if="isPaused" class="tc-sum-tag tc-sum-wait">等待确认</span>
      <!-- 2026-11-06 三次改版：思考时间紧跟「思考中」之后（大哥要求时间后置到标题行内） -->
      <span v-if="isThinking || isPaused" class="tc-sum-time">{{ liveTimeLabel }}</span>
      <span v-if="isThinking && statusText" class="tc-sum-status">{{ statusText }}</span>
      <span v-else-if="isPaused" class="tc-sum-status">点上方「批准执行」继续</span>
      <span v-if="!isThinking && !isPaused" class="tc-sum-done">{{ summaryLabel }}</span>
      <svg width="12" height="12" viewBox="0 0 12 12" class="tc-chev" :class="{ open }">
        <path d="M2 4.5L6 8.5L10 4.5" stroke="currentColor" stroke-width="1.2" fill="none" />
      </svg>
    </button>
    <!-- 2026-11-06 三次改版：深度思考块提到标题行正下方（大哥钦定），
         辉光从标题行移到这里渲染；默认一行预览（流式实时增长），点开看全文。
         它是「模型的反馈」，与时间线里「工具的反馈」视觉分层。 -->
    <div v-if="extRow" class="tc-ext" :class="{ 'is-glow': glowLive }" @click.stop="detailOpen = !detailOpen">
      <div class="tc-ext-ic">
        <Brain :size="14" stroke-width="1.75" />
      </div>
      <span class="tc-ext-label">深度思考</span>
      <!-- 扫光：流式期间预览文字扫光生长（复用 cd-shimmer 材质）；辉光在标签上，扫光在预览上 -->
      <span class="tc-ext-preview" :class="{ 'tc-shim': glowLive && extPreview }"><span>{{ extPreview }}</span></span>
      <svg width="11" height="11" viewBox="0 0 12 12" class="tc-ext-chev" :class="{ open: detailOpen }">
        <path d="M2 4.5L6 8.5L10 4.5" stroke="currentColor" stroke-width="1.2" fill="none" />
      </svg>
    </div>
    <div v-if="extRow && detailOpen" class="tc-ext-full">
      <!-- 2026-11-08 七次改版：展开全文默认跟随最新输出（钉底），用户上滑即暂停、
           滑回底部自动恢复跟随（对齐终端/Claude 全文流的 stick-bottom 惯例） -->
      <p ref="extTextEl" class="tc-ext-text" @scroll="onExtScroll">{{ extRow.ev.detail }}</p>
    </div>
    <div class="tc-fold" :style="{ gridTemplateRows: open ? '1fr' : '0fr' }">
      <div class="tc-fold-clip">
        <!-- 2026-11-08 六次改版：原「思考内容」子块已移除——与深度思考块全文重复展示
             （大哥截图实锤：展开后两个思考过程）。思考全文现在只在深度思考块呈现 -->
        <template v-for="row in visibleRows" :key="`${row.ev.kind}-${row.i}`">
          <!-- 窗口折叠条：隐藏的旧步骤收在这里（对齐 Claude 折叠组 / Codex 披露条） -->
          <button v-if="row.showBar" type="button" class="tc-collapse-bar" @click.stop="chainOpen = true">
            <span class="tc-bar-chev">▸</span>
            已完成 {{ hiddenCount }} 步 · 点开查看
          </button>
          <div v-if="row.i > 0" class="tc-rail" :style="{ height: '8px' }" />
          <!-- 入场延迟封顶 200ms：不按全量行号累加，行多时新事件不再迟到 -->
          <div class="tc-row" :class="{ mut: row.ev.mut, on: row.ev.active, fail: row.ev.failed, act: TOOL_KINDS.has(row.ev.kind), dec: row.ev.kind === 'direct' }" :style="{ animationDelay: `${Math.min(row.i * 45, 200)}ms` }">
            <div class="tc-ic" :class="{ 'tc-ic-ext': row.ev.kind === 'ext' }">
              <component
                :is="iconComp(row.ev)"
                :size="14"
                :stroke-width="1.75"
                class="tc-ic-lucide"
                :class="{ 'tc-ic-accent': row.ev.kind === 'done' }"
              />
            </div>
            <div class="tc-tx">
              <p class="tc-label" :class="{ 'tc-shim': row.ev.active }">
                <span>{{ row.ev.label }}</span>
                <!-- 连续同类调用聚合计数（对齐 Claude「Ran 3 bash commands」摘要行） -->
                <span v-if="(row.ev.count || 0) > 1" class="tc-count">×{{ row.ev.count }}</span>
              </p>
              <p v-if="row.ev.meta" class="tc-meta">{{ row.ev.meta }}</p>
              <!-- ext（深度思考）行不渲染通用「查看执行过程」：它和下方专属块的「查看完整思考」是同一份 ev.detail，重复入口 -->
              <template v-if="row.ev.detail && !row.ev.active && row.ev.kind !== 'ext'">
                <button type="button" class="tc-detail-toggle" @click.stop="toggleDetail(`${row.ev.kind}-${row.i}`)">
                  {{ openDetails[`${row.ev.kind}-${row.i}`] ? '收起过程' : '查看执行过程' }}
                </button>
                <div v-if="openDetails[`${row.ev.kind}-${row.i}`]" class="tc-detail-wrap">
                  <div class="tc-detail tc-detail--tool">{{ row.ev.detail }}</div>
                </div>
              </template>
            </div>
          </div>
        </template>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { Search, FileText, HelpCircle, ShieldCheck, CheckCircle2, Sparkles, Brain, Cog, SquareTerminal } from 'lucide-vue-next'
import { useThoughtStream } from './thoughtStream'

// 订阅思考流事件总线——按消息自己的 turnId 取桶（轮次隔离，不串线）
const props = defineProps<{ turnId?: string | null; streaming?: boolean }>()
const { events, isLive, elapsedMs, statusText, paused: bucketPaused } = useThoughtStream(() => props.turnId)
// 思考中 = store 活跃 或 本消息仍在流式（空桶窗口也显示"思考中…"）
// 2026-11-14：确认闸门暂停态（bucketPaused）不算思考中——SSE 流在暂停期
// 仍开着（streaming prop 为 true），不加排除的话暂停后「思考中●●●」会和
// 已关桶产生的「思考完成」同框（大哥截图实锤）。暂停时标题行显「等待确认」。
const isPaused = computed(() => bucketPaused.value && !isLive.value)
const isThinking = computed(() => (isLive.value || !!props.streaming) && !isPaused.value)

const open = ref(false)
const detailOpen = ref(false)
const openDetails = reactive<Record<string, boolean>>({})
function toggleDetail(key: string) {
  openDetails[key] = !openDetails[key]
}

// ── 思考内容（竞品对齐 · 2026-11-06 二次改版）──
// 标题行不再内嵌思考全文打字机（大哥反馈：思考中+正文挤一行、常驻顶部，长链路展开后看不清过程）。
// 思考全文移到展开区「思考内容」子块，完整文本、随流式实时增长、块内自滚。
// 标题行只显示状态：思考中●●● + 当前动作短句（statusText）/ 完成态「已思考 Xs · N 步骤」。

function cleanThought(raw: string): string {
  return raw
    .replace(/\*\*/g, '')
    .replace(/#{1,4}\s/g, '')
    .replace(/\s*\n+\s*/g, ' ')
    .replace(/\s{2,}/g, ' ')
    .trim()
}

/** 辉光窗口 = 本轮仍在流式且未暂停（2026-11-14：暂停态扫光/呼吸全熄，
 *  与「等待确认」状态对齐——等用户批准的活不该继续演"正在思考"） */
const glowLive = computed(() => isThinking.value)

// ── 标题行思考时间（2026-11-06 三次改版；2026-11-07 五次改版去毫秒小数）──
// 「思考中」之后实时计时：<60s 显示 9s（整数秒，大哥要求不带小数），≥60s 显示 2分6秒；
// 完成态并入摘要行不再重复显示
function fmtElapsed(ms: number): string {
  const s = Math.floor(ms / 1000)
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  return `${m}分${s - m * 60}秒`
}
const liveTimeLabel = computed(() => (isThinking.value || isPaused.value ? fmtElapsed(elapsedMs.value) : ''))

// ── 深度思考块（2026-11-06 三次改版，2026-11-07 四次改版强化流式感）──
// 取最近一条 ext 事件（多轮取最后一轮），默认一行预览，点开看全文。
// 预览用客户端打字机向目标文本追赶（2026-11-13 十次改版：24ms×3字符 →
// 32ms×4字符，吞吐不变 125 字/s，渲染频率对齐 codex status_indicator 的
// 32ms 帧节奏，Vue patch 频率降 25%，减轻流式输出与扫光叠加的卡顿）：
// 后端 reasoning 大块一次性到达时也能呈现流式生长（真身观感）。
const extRow = computed(() => {
  const list = events.value.filter((e) => e.kind === 'ext')
  return list.length ? { ev: list[list.length - 1]! } : null
})
const extPreviewTarget = computed(() => cleanThought(String(extRow.value?.ev.detail || '')))

// ── 展开全文跟随底部（2026-11-08 七次改版）──
// 默认钉底看最新输出；用户上滑（距底 > 16px）暂停跟随，滑回底部自动恢复。
// 流式期间全文实时增长，watch 里 nextTick 后钉底；重新展开/换轮次自动恢复跟随。
const extTextEl = ref<HTMLElement | null>(null)
const extStick = ref(true)
function onExtScroll() {
  const el = extTextEl.value
  if (!el) return
  extStick.value = el.scrollTop + el.clientHeight >= el.scrollHeight - 16
}
watch(extRow, () => {
  if (!detailOpen.value) return
  extStick.value = true
  nextTick(() => {
    const el = extTextEl.value
    if (el && extStick.value) el.scrollTop = el.scrollHeight
  })
})
watch(
  () => extRow.value?.ev.detail,
  () => {
    if (!detailOpen.value || !extStick.value) return
    nextTick(() => {
      const el = extTextEl.value
      if (el && extStick.value) el.scrollTop = el.scrollHeight
    })
  }
)
watch(detailOpen, (v) => {
  if (v) {
    extStick.value = true
    nextTick(() => {
      const el = extTextEl.value
      if (el) el.scrollTop = el.scrollHeight
    })
  }
})

const extShown = ref('')
let extTwTimer: number | null = null
watch(extPreviewTarget, (t) => {
  // 新一轮思考（目标不含已展示内容）→ 从头打起
  if (!t.startsWith(extShown.value)) extShown.value = ''
  if (extTwTimer != null) return
  extTwTimer = window.setInterval(() => {
    const target = extPreviewTarget.value
    if (extShown.value.length >= target.length) {
      if (extTwTimer != null) clearInterval(extTwTimer)
      extTwTimer = null
      return
    }
    extShown.value = target.slice(0, extShown.value.length + 4)
  }, 32)
}, { immediate: true })
onBeforeUnmount(() => {
  if (extTwTimer != null) clearInterval(extTwTimer)
  extTwTimer = null
})
// ── 折叠态预览：翻页式最新输出（2026-11-08 八次改版）──
// 旧版预览恒显示开头段（尾部省略号），永远停在开场白。现改为「翻页打字」：
// 从左往右打字，打满一块就清屏换最新内容重打——预览行始终跟上最新思考，
// 逐字流出、无左移滑动（大哥点名不要 ticker 式左移）；展开态看全文不受影响
const PREVIEW_CHUNK = 42
const extPreview = computed(() => {
  const s = extShown.value
  if (s.length <= PREVIEW_CHUNK) return s
  const start = Math.floor(s.length / PREVIEW_CHUNK) * PREVIEW_CHUNK
  return s.slice(start, start + PREVIEW_CHUNK)
})

// ── 窗口折叠（对齐 Claude collapseReadSearch / Codex 披露条的异质组形态）──
// 真实任务链是十几个不同技能的 skill_call（ffmpeg 在技能内部跑、无独立事件），
// 同类 ×N 合并打不中它们。这里按窗口渲染：只露最近几行动作，旧步骤收进
// 「已完成 N 步」折叠条，数据层全量保留，点开即看全轨迹。
// 窗口覆盖动作行 + 决策行（direct 每轮 ReAct 一条，40 轮迭代同样刷屏）；
// ext 深度思考块与 Done 行不参与折叠，始终可见
const ACTION_KINDS = new Set(['tool', 'web_search', 'write_preview', 'fallback', 'direct'])
// 样式分类（与折叠计数解耦）：工具调用行 = mono 胶囊 + 图标方底（"动作"实体感）；
// direct 决策行 = 纯文本弱灰（"语言"感）。两类在时间线上一眼可分（大哥 2026-11-05）。
const TOOL_KINDS = new Set(['tool', 'web_search', 'write_preview', 'fallback'])
const CHAIN_WINDOW = 3
const chainOpen = ref(false)
const actionCount = computed(() => events.value.filter((e) => ACTION_KINDS.has(e.kind)).length)
const hiddenCount = computed(() => Math.max(0, actionCount.value - CHAIN_WINDOW))
interface VisibleRow { ev: (typeof events.value)[number]; i: number; showBar?: boolean }
const visibleRows = computed<VisibleRow[]>(() => {
  const all = events.value
  // Done 行不渲染：外层摘要行（已思考 Xs · N 个步骤）已覆盖，纯重复；
  // ext 深度思考块 2026-11-06 三次改版后上移到标题行下方专属渲染，时间线不再重复出它
  const list = all.filter((e) => e.kind !== 'done' && e.kind !== 'ext')
  if (!hiddenCount.value || chainOpen.value) {
    return list.map((ev, i) => ({ ev, i }))
  }
  // 动作行在数组中的序号 → 窗口外（较旧的）隐藏
  let ai = -1
  const actionIndexOf = new Map<object, number>()
  for (const e of list) if (ACTION_KINDS.has(e.kind)) actionIndexOf.set(e, ++ai)
  const rows: VisibleRow[] = []
  let barPending = true
  for (let i = 0; i < list.length; i++) {
    const ev = list[i]!
    const a = actionIndexOf.get(ev)
    if (a !== undefined && a < hiddenCount.value) continue
    // 第一个可见动作行前插入折叠条
    if (a !== undefined && barPending) {
      rows.push({ ev, i, showBar: true })
      barPending = false
    } else {
      rows.push({ ev, i })
    }
  }
  return rows
})
const ICON_MUTED = '#888780'
const ICON_ACCENT = '#534AB7'


const ICON_MAP: Record<string, any> = {
  brain: Brain,
  ext: Brain,
  search: Search,
  doc: FileText,
  ask: HelpCircle,
  check: ShieldCheck,
  step: Cog,
  terminal: SquareTerminal,
  done: CheckCircle2,
  fallback: Cog,
}

function iconComp(ev: { kind: string; icon?: string }) {
  if (ev.kind === 'done') return CheckCircle2
  return ICON_MAP[ev.icon || ''] || Cog
}

const summaryLabel = computed(() => {
  // 完成态摘要：思考中期间不消费（走 glowLive 状态分支）；
  // 这里只覆盖完成态「已思考 Xs · N 个步骤」与纯工具轮收尾
  if (isThinking.value) return '思考中'
  // 2026-11-07 五次改版：摘要时间同样取整秒（大哥要求时间不带小数）
  const secs = Math.floor(elapsedMs.value / 1000)
  // Codex 形态：摘要带步骤计数，不再贴最后一行决策原文（和链内 direct 行重复）
  const n = actionCount.value
  return n > 0 ? `已思考 ${secs}s · ${n} 个步骤` : `已思考 ${secs}s`
})

// 折叠状态机（2026-11-07 四次改版，修复「工具调用链路被吃掉」）：
// - 流式开始自动展开链路：工具行实时生长可见（大哥要求链路全程看得见）
// - 思考结束自动收成摘要行；展开随时可点
watch(
  () => isThinking.value,
  (live, prev) => {
    if (live && !prev) open.value = true
    // 2026-11-14：暂停态（等待确认）不收链路——用户正看着过程决定批不批，
    // 这时把时间线收走等于把上下文藏起来；真完成（非暂停）才收成摘要行
    if (prev && !live && !isPaused.value) {
      open.value = false
      detailOpen.value = false
    }
    // 新一轮流式开始 / 结束都重置窗口折叠（每回合从窗口态起手）
    chainOpen.value = false
  },
  { immediate: true }
)

function toggle() {
  open.value = !open.value
}
</script>

<style scoped>
.tc-root {
  margin: 4px 0 14px 8px;
}
.tc-sum {
  display: flex;
  width: 100%;
  align-items: center;
  gap: 4px;
  padding: 4px 0;
  border: none;
  background: none;
  text-align: left;
  color: #7b7974;
  cursor: pointer;
  font: inherit;
  transition: color 0.15s;
  position: relative;
}
.tc-sum:hover {
  color: #373734;
}
/* ── 2026-11-06 三次改版（大哥钦定）：标题行去掉辉光和雾罩（原 ::before 径向晕 +
   drop-shadow 双层辉光整体移除），只留干净的状态行；辉光改到 .tc-ext 深度思考块渲染。
   标题行新增 .tc-sum-time：思考时间紧跟「思考中」之后。 ── */
/* ── 2026-11-14：暂停态标签「等待确认」──
   陶土色系（与确认卡「批准执行」/ 自动放行 pill 同族），一眼区分
   「在思考」（灰）「等用户拍板」（暖琥珀）「已完成」三种状态 */
.tc-sum-tag.tc-sum-wait {
  color: #b4531f;
}
.tc-sum-time {
  flex: none;
  font-size: 12px;
  line-height: 18px;
  color: #9c9ba6;
  font-variant-numeric: tabular-nums;
  margin-left: 2px;
}
/* ── 深度思考块（辉光新载体）──
   ① 行底氛围光晕：::before 径向渐变，呼吸 2.8s（复用原 tc-halo-breathe）；
   ② 标签文字辉光：drop-shadow 双层（近距紫 + 远距浅紫），随呼吸同步涨落。
   本轮流式期间点亮（.is-glow），完成态熄灭。 */
.tc-ext {
  position: relative;
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0 0 2px 2px;
  padding: 4px 8px 4px 4px;
  border-radius: 10px;
  cursor: pointer;
  width: fit-content;
  max-width: calc(100% - 8px);
}
.tc-ext::before {
  content: '';
  position: absolute;
  inset: 0;
  border-radius: 10px;
  background: radial-gradient(120% 100% at 8% 50%, rgba(83, 74, 183, 0.055) 0%, rgba(175, 169, 236, 0.035) 45%, transparent 78%);
  opacity: 0;
  pointer-events: none;
  transition: opacity 0.4s ease;
}
.tc-ext.is-glow::before {
  opacity: 1;
  animation: tc-halo-breathe 2.8s ease-in-out infinite;
}
.tc-ext-ic {
  flex: none;
  display: inline-flex;
  color: #888780;
  position: relative;
  z-index: 1;
}
/* 2026-11-07 五次改版：思考中大脑图标「时小时大」呼吸缩放（大哥要求放映效果） */
@keyframes tc-brain-breathe {
  0%,
  100% {
    transform: scale(0.82);
    opacity: 0.75;
  }
  50% {
    transform: scale(1.12);
    opacity: 1;
  }
}
.tc-ext.is-glow .tc-ext-ic svg {
  animation: tc-brain-breathe 1.6s ease-in-out infinite;
  transform-origin: center;
  will-change: transform;
}
.tc-ext-label {
  flex: none;
  font-size: 12.5px;
  line-height: 18px;
  color: #6b6a66;
  position: relative;
  z-index: 1;
}
/* 2026-11-13 十次改版：标签紫字改静态，去掉 filter drop-shadow 呼吸动画
   （filter 逐帧 repaint 是输出卡顿主凶；对齐 codex status_indicator：状态行无辉光） */
.tc-ext.is-glow .tc-ext-label {
  color: #534AB7;
}
.tc-ext-preview {
  min-width: 0;
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: var(--rl-font-serif, Georgia, 'Songti SC', SimSun, serif);
  font-style: italic;
  font-size: 12.5px;
  line-height: 18px;
  color: #8a8880;
  position: relative;
  z-index: 1;
}
.tc-ext-chev {
  flex: none;
  color: #9c9ba6;
  transition: transform 0.2s;
  position: relative;
  z-index: 1;
}
.tc-ext-chev.open {
  transform: rotate(180deg);
}
/* 全文展开区：衬线弱灰（模型的反馈 = 语言感，区别于工具行的 mono 实体感） */
.tc-ext-full {
  margin: 4px 0 8px 6px;
  padding: 8px 12px;
  border-left: 1.5px solid rgba(83, 74, 183, 0.18);
}
.tc-ext-text {
  margin: 0;
  font-family: var(--rl-font-serif, Georgia, 'Songti SC', SimSun, serif);
  font-size: 12.5px;
  line-height: 19px;
  color: #6b6a66;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 240px;
  overflow-y: auto;
}
/* 深色主题：halo 透明度提一档保持可见度（标签辉光动画已随十次改版移除） */
.rl-chat.is-dark .tc-ext.is-glow::before {
  background: radial-gradient(120% 100% at 8% 50%, rgba(175, 169, 236, 0.14) 0%, rgba(83, 74, 183, 0.08) 45%, transparent 78%);
}
@keyframes tc-halo-breathe {
  0%, 100% { opacity: 0.4; }
  50%      { opacity: 1; }
}
.tc-sum-label {
  min-width: 0;
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
  line-height: 18px;
  letter-spacing: -0.15px;
}
.tc-chev {
  flex: none;
  transition: transform 0.2s;
}
.tc-chev.open {
  transform: rotate(180deg);
}
/* ── 2026-11-06 二次改版（竞品对齐）：标题行状态化 + 思考内容下沉 + 块内自滚 ── */
.tc-sum-status {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12.5px;
  color: #7b7974;
  margin-left: 2px;
}
.tc-sum-done {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
  color: #55534e;
}
/* 2026-11-08 六次改版：「思考内容」子块样式已随模板移除（与深度思考块全文重复） */
/* 展开后块内自滚：长链路不把正文顶飞（竞品对齐 Claude/Codex/ChatGPT）
   2026-11-07 四次改版修复「展开后链路被截断」：滚动容器必须放在 grid ITEM
   （.tc-fold-clip）上——max-height 加在 grid 容器上时，1fr 行轨道会被压缩到
   约束高度、内容被 clip 的 overflow:hidden 裁掉，grid 自身永不溢出，滚动条
   永远出不来（大哥截图实锤）。放在 clip 上后内容在 clip 内部滚动。 */
.tc-root.tc-open .tc-fold-clip {
  max-height: 42vh;
  overflow-y: auto;
  overscroll-behavior: contain;
  scrollbar-width: thin;
}
/* ── 陶土跳点（timeline-quiet-dots 反编译原版，2026-11-04 从
      chat-view-global.css 迁入 scoped）：「思考中」尾部三点，
      3px / gap 3.5px / 1.9s / 相位差 0s-0.15s-0.3s ── */
.rl-quiet-dots {
  display: inline-flex;
  gap: 3.5px;
  margin-left: 5px;
  position: relative;
  top: 0.26em;
  flex: none;
}
.rl-quiet-dot {
  width: 3px;
  height: 3px;
  border-radius: 50%;
  background: var(--cds-clay, #d97757);
  animation: rl-quiet-dot 1.9s cubic-bezier(0.4, 0, 0.2, 1) infinite;
}
.rl-quiet-dot:nth-child(2) { animation-delay: 0.15s; }
.rl-quiet-dot:nth-child(3) { animation-delay: 0.3s; }
@keyframes rl-quiet-dot {
  0%, 8%   { opacity: 0; transform: translateY(2px); }
  22%      { opacity: 1; transform: translateY(-2.5px); }
  36%, 70% { opacity: 1; transform: translateY(0); }
  90%, 100%{ opacity: 0; transform: translateY(0); }
}
.tc-fold {
  display: grid;
  transition: grid-template-rows 0.2s cubic-bezier(0.19, 1, 0.22, 1);
}
.tc-fold-clip {
  min-height: 0;
  overflow: hidden;
}
.tc-rail {
  position: relative;
  left: 9.5px;
  width: 1px;
  background: rgba(31, 31, 30, 0.15);
}
.tc-row {
  display: flex;
  align-items: flex-start;
  border-radius:12px;
  animation: tc-row-enter 220ms cubic-bezier(0.22, 1, 0.36, 1) both;
}
.tc-ic {
  width: 20px;
  display: flex;
  align-items: flex-start;
  justify-content: center;
  padding: 2px 2px 0;
}
.tc-ic svg {
  width: 15px;
  height: 15px;
  flex: none;
}
.tc-tx {
  min-width: 0;
  flex: 1;
  padding-left: 10px;
  padding-top: 2px;
}
.tc-label {
  margin: 0;
  min-width: 0;
  flex: 1;
  /* 13px < 正文 14px：思考链路是辅助信息，字号和颜色都要弱于回复正文 */
  font-size: 13px;
  line-height: 18px;
  letter-spacing: -0.15px;
  color: #373734;
}
.tc-row.mut .tc-label {
  color: #7b7974;
}
/* ── 工具调用行 vs 决策行 区分度（2026-11-05 大哥报两类行长得一样）──
   工具行：mono 胶囊 pill + 图标小方底 → "动作"实体感；
   direct 决策行：纯文本弱灰 → "语言"感。双通道区分，折叠计数不受影响。 */
.tc-row.act .tc-label {
  display: inline-flex;
  align-items: center;
  width: fit-content;
  gap: 6px;
  padding: 3px 10px;
  border-radius: 8px;
  background: rgba(31, 31, 30, 0.045);
  font-family: var(--rl-font-mono, 'SF Mono', ui-monospace, Menlo, Consolas, monospace);
  font-size: 12.5px;
  color: #55534e;
}
.tc-row.act .tc-ic {
  width: 24px;
  height: 24px;
  padding: 0;
  margin-top: 2px;
  border-radius: 7px;
  background: rgba(31, 31, 30, 0.045);
  align-items: center;
}
.tc-row.act .tc-ic svg {
  width: 13px;
  height: 13px;
}
/* 决策/思考行（模型的反馈）：衬线斜体弱灰——「语言感」，
   与工具行的 mono 胶囊（「动作」实体感）双通道区分（2026-11-06 三次改版强化） */
.tc-row.dec .tc-label {
  font-family: var(--rl-font-serif, Georgia, 'Songti SC', SimSun, serif);
  font-style: italic;
  color: #6b6a66;
}
.tc-row.dec .tc-ic svg {
  color: #9a9891;
}
/* 聚合计数：×N 弱灰小字，紧贴标签（Codex 统一行形态） */
.tc-count {
  margin-left: 6px;
  font-size: 12px;
  color: #7b7974;
}
/* 窗口折叠条：隐藏的旧步骤收口（Claude ⤿ hint 形态） */
.tc-collapse-bar {
  display: flex;
  align-items: center;
  gap: 5px;
  margin: 2px 0 2px 8px;
  padding: 2px 0;
  border: none;
  background: none;
  font-size: 12px;
  line-height: 16px;
  color: rgba(123, 121, 116, 0.8);
  cursor: pointer;
  font-family: inherit;
  transition: color 0.15s;
}
.tc-collapse-bar:hover {
  color: #373734;
}
.tc-bar-chev {
  font-size: 10px;
}
/* 失败行标红留原地（行业 pattern：只显示成功的时间线藏住审计故事） */
.tc-row.fail .tc-label {
  color: #a32d2d;
}
.tc-row.fail .tc-ic svg {
  color: #a32d2d;
}
.tc-meta {
  margin: 1px 0 0;
  flex: none;
  font-size: 11.5px;
  line-height: 16px;
  color: #7b7974;
}
/* ── 文本扫光（2026-11-13 十次改版：对齐 codex tui/src/shimmer.rs）──
   codex 参数：2s 一整趟线性匀速、余弦钟形光带无硬边、高亮只是朝背景色
   变亮 90%（无色相偏移、无外发光）、32ms 帧节奏。CSS 映射：
   - background-size 200% + position 200%→0%：位移恰为一个图块宽，
     循环首尾外观全等（offset 相差恰一图块）→ 真·无缝循环，光带每趟
     从行左滑入、行右滑出，无蓄势停顿、无跳变；
   - 渐变钟形：基准灰 #575653 → 峰值 #eeedeb（= 白底混入 90%，即 codex
     blend(bg, base, 0.9) 的反演），不掺任何紫色；
   - 去掉 drop-shadow filter：background-clip:text 挂 filter 每帧强制
     重绘文字光栅化，是掉帧主凶。 */
.tc-shim span,
.tc-sum-shim {
  background: linear-gradient(90deg,
    #575653 0%,
    #575653 38%,
    #9d9c99 46%,
    #eeedeb 50%,
    #9d9c99 54%,
    #575653 62%,
    #575653 100%);
  background-size: 200% 100%;
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
  animation: cds-shimmer-text-shine 2s linear infinite;
}
/* 2026-11-08 九次改版：扫光与文字输出速度解耦——内层 span 从 inline 改为
   撑满整行的固定盒宽（block + 100%），background-position 百分比映射到
   恒定几何，光带按自身节奏扫过整行，不再随打字机逐字增宽而抖动 */
.tc-shim span {
  display: block;
  width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
@keyframes cds-shimmer-text-shine {
  0%   { background-position: 200% 0; }
  100% { background-position: 0% 0; }
}
@keyframes tc-row-enter {
  from {
    opacity: 0;
    transform: translateY(4px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}
.tc-detail-wrap {
  position: relative;
}
.tc-detail {
  overflow: hidden;
  white-space: pre-wrap;
  word-break: break-word;
  /* CDS Thinking：内部思维日志 Mono 11.5px/16px 弱灰无背景包裹 */
  font-family: var(--rl-font-mono, 'SF Mono', ui-monospace, Menlo, Consolas, monospace);
  font-size: 11.5px;
  line-height: 16px;
  color: #6b6a66;
}
.tc-detail-fade {
  position: absolute;
  inset-inline: 0;
  bottom: 0;
  height: 48px;
  background: linear-gradient(to top, #fff, transparent);
  pointer-events: none;
}
.tc-more,
.tc-detail-toggle {
  margin-top: 8px;
  font-size: 12px;
  line-height: 16px;
  color: rgba(123, 121, 116, 0.8);
  background: none;
  border: none;
  cursor: pointer;
  padding: 0;
  font-family: inherit;
  transition: color 0.15s;
}
.tc-detail-toggle:hover {
  color: #373734;
}
.tc-detail--tool {
  margin-top: 6px;
  padding: 8px 10px;
  border-radius:12px;
  background: rgba(31, 31, 30, 0.03);
  font-size: 12px;
  line-height: 17px;
  color: #6b6b6b;
  white-space: pre-wrap;
  word-break: break-word;
}
.tc-more:hover {
  color: #373734;
}
.tc-root :deep(.tc-ic svg [style*='fill']) {
}
</style>
