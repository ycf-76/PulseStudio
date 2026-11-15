<template>
  <!--
    执行轨迹（Codex ExecCell 对齐）。
    共享样式在 render-layer.css（.rl-traj-* / .rl-trail / .rl-slide），本组件无 scoped 样式。
    行为：默认展开；全部步骤终态后自动折叠一次（watch allDone）。
  -->
  <div class="rl-traj">
    <div class="rl-traj-head" @click="detailOpen = !detailOpen">
      <span class="rl-traj-title">执行轨迹</span>
      <!-- 状态徽标（对齐 Claude McpToolDecisionCard 三态徽标形态） -->
      <span v-if="isAnyRunning" class="rl-traj-badge rl-traj-badge--run"><span class="rl-traj-badge-dot" />执行中</span>
      <span v-else-if="hasAnyError" class="rl-traj-badge rl-traj-badge--err">✕ 部分失败</span>
      <span v-else-if="allDone" class="rl-traj-badge rl-traj-badge--ok">✓ 已完成</span>
      <span class="rl-traj-meta">{{ metaText }}</span>
      <span class="rl-traj-chev" :class="{ 'is-open': detailOpen }">▾</span>
    </div>
    <div class="rl-traj-body" :class="{ 'is-closed': !detailOpen }">
      <div class="rl-traj-items">
          <span class="rl-trail" />
          <!-- 父 Agent 工具行 -->
          <div
            v-for="row in parentRows"
            :key="'p:' + row.key"
            class="rl-traj-item"
          >
            <span class="rl-traj-dot" :class="'rl-traj-dot--' + dotClass(row.status)" />
            <span class="rl-traj-label" :class="{ 'rl-traj-label--run': row.status === 'running' }">{{ row.label }}</span>
            <span v-if="row.count > 1" class="rl-traj-x">×{{ row.count }}</span>
            <span v-if="row.durationMs" class="rl-traj-dur">{{ formatDuration(row.durationMs) }}</span>
          </div>
          <!-- 子智能体分组（组头用 collab 真名） -->
          <template v-for="group in subAgentGroups" :key="'g:' + group.subAgentId">
            <div class="rl-traj-item">
              <span class="rl-traj-dot" :class="'rl-traj-dot--' + dotClass(group.state)" />
              <span class="rl-traj-label" :class="{ 'rl-traj-label--run': group.isRunning }">{{ group.displayName }}</span>
              <span class="rl-traj-x">{{ group.done }}/{{ group.total }}</span>
            </div>
            <div
              v-for="row in group.rows"
              :key="'g:' + group.subAgentId + ':' + row.key"
              class="rl-traj-item rl-traj-sub"
            >
              <span class="rl-traj-dot rl-traj-dot--sm" :class="'rl-traj-dot--' + dotClass(row.status)" />
              <span class="rl-traj-sublabel" :class="{ 'rl-traj-label--run': row.status === 'running' }">└ {{ row.label }}</span>
              <span v-if="row.count > 1" class="rl-traj-x">×{{ row.count }}</span>
              <span v-if="row.durationMs" class="rl-traj-dur">{{ formatDuration(row.durationMs) }}</span>
            </div>
          </template>
        </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { getToolDisplayLabel } from './cell-types'
import type { ToolCall } from './cell-types'

const props = defineProps<{
  toolCalls: ToolCall[]
}>()

const detailOpen = ref(true)

const isAnyRunning = computed(() => props.toolCalls.some(t => t.status === 'running'))
const hasAnyError = computed(() => props.toolCalls.some(t => t.status === 'error'))

function isTerminal(tc: ToolCall): boolean {
  return tc.status === 'done' || tc.status === 'error' || tc.status === 'aborted'
}

const allDone = computed(() =>
  props.toolCalls.length > 0 && props.toolCalls.every(isTerminal),
)

const abortedCount = computed(() => props.toolCalls.filter(t => t.status === 'aborted').length)

const parentCalls = computed(() => props.toolCalls.filter(t => !t.subAgentId))

/** 状态优先级（用于行/组汇总：running 最高） */
const STATUS_RANK: Record<string, number> = { running: 4, error: 3, aborted: 2, pending: 1, done: 0 }

function mergeStatus(a: string, b: string | undefined): string {
  const b1 = b || 'pending'
  return (STATUS_RANK[b1] ?? 0) > (STATUS_RANK[a] ?? 0) ? b1 : a
}

function rowKey(tc: ToolCall): string {
  const args = tc.arguments as Record<string, unknown> | undefined
  if (tc.name === 'spawn_agent') {
    return `spawn:${String(args?.task_name || args?.name || '')}`
  }
  return tc.name
}

interface TrajRow {
  key: string
  label: string
  status: string
  count: number
  durationMs: number
}

function rowsOf(list: ToolCall[]): TrajRow[] {
  const out: TrajRow[] = []
  const byKey = new Map<string, TrajRow>()
  for (const tc of list) {
    const key = rowKey(tc)
    let row = byKey.get(key)
    if (!row) {
      row = { key, label: labelOf(tc), status: tc.status || 'pending', count: 0, durationMs: 0 }
      byKey.set(key, row)
      out.push(row)
    }
    row.count += 1
    if (tc.durationMs) row.durationMs += tc.durationMs
    row.status = mergeStatus(row.status, tc.status)
  }
  return out
}

function doneCount(list: ToolCall[]): number {
  return list.filter(isTerminal).length
}

const parentRows = computed(() => rowsOf(parentCalls.value))

interface SubGroup {
  subAgentId: string
  displayName: string
  rows: TrajRow[]
  state: string
  isRunning: boolean
  done: number
  total: number
}

const subAgentGroups = computed<SubGroup[]>(() => {
  const byId = new Map<string, ToolCall[]>()
  for (const tc of props.toolCalls) {
    if (!tc.subAgentId) continue
    const arr = byId.get(tc.subAgentId)
    if (arr) arr.push(tc)
    else byId.set(tc.subAgentId, [tc])
  }
  return [...byId.entries()].map(([id, items]) => {
    let state = 'pending'
    for (const tc of items) state = mergeStatus(state, tc.status)
    return {
      subAgentId: id,
      // 组头真名来源：行自身的 subAgentName（后端 tool_call_start/end 下发的
      // spawn_agent task_name 与 dispatch 返回的 name）；缺失时回退工具标签。
      displayName: items[0].subAgentName || subAgentName(items[0]),
      rows: rowsOf(items),
      state,
      isRunning: state === 'running',
      done: doneCount(items),
      total: items.length,
    }
  })
})

const totalDurationMs = computed(() => {
  let total = 0
  for (const tc of props.toolCalls) if (tc.durationMs) total += tc.durationMs
  return total || 0
})

const metaText = computed(() => {
  const total = props.toolCalls.length
  if (isAnyRunning.value) return `${doneCount(props.toolCalls)}/${total} 步`
  if (allDone.value) {
    const tail = abortedCount.value > 0 ? ` · ${abortedCount.value} 项未回传` : ''
    return hasAnyError.value
      ? `${total} 步 · ${formatDuration(totalDurationMs.value)} · 部分失败${tail}`
      : `${total} 步 · ${formatDuration(totalDurationMs.value)} · 已完成${tail}`
  }
  return `${total} 步`
})

function dotClass(status: string): string {
  switch (status) {
    case 'running': return 'run'
    case 'done': return 'done'
    case 'error': return 'err'
    case 'aborted': return 'skip'
    default: return 'wait'
  }
}

function labelOf(tc: ToolCall): string {
  const args = tc.arguments as Record<string, unknown> | undefined
  if (tc.name === 'spawn_agent') {
    const taskName = String(args?.task_name || args?.name || '')
    return taskName ? `派发「${taskName}」` : '派发子智能体'
  }
  if (tc.name === 'wait_agent') return '等待完成'
  return getToolDisplayLabel(tc.name).label || tc.name
}

function subAgentName(tc: ToolCall): string {
  return getToolDisplayLabel(tc.name).label || '子智能体'
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`
  const s = ms / 1000
  if (s < 60) return `${s.toFixed(1)}s`
  const m = Math.floor(s / 60)
  const r = Math.round(s % 60)
  return r > 0 ? `${m}分${r}秒` : `${m}分`
}

/** 全部步骤终态后自动折叠一次（进行中不打扰） */
const autoCollapsed = ref(false)
watch(allDone, (v) => {
  if (v && !autoCollapsed.value) {
    autoCollapsed.value = true
    detailOpen.value = false
  }
}, { immediate: true })
</script>
