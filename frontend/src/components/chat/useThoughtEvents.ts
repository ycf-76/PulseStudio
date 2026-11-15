import { computed, type ComputedRef } from 'vue'
import type { ChatMessage, ToolCall } from './cell-types'

/**
 * useThoughtEvents — 思考时间线事件归一化层（架构第 ① 层）。
 *
 * 把消息上已有的事件数据（toolCalls / decisions / reasoning）归一成
 * 统一的 ThoughtEvent 流，供 ThinkingChain.vue 通用渲染器消费。
 *
 * 兼容性保证：
 * - 后端新增技能 → tool_call_start/end 自动落 tool 行（kindOfTool 兜底）
 * - 未知事件类型 → fallback 不会让渲染器炸
 * - 渲染器只认 ThoughtEvent，不知道业务任务是什么
 */

export interface ThoughtEvent {
  kind: 'ext' | 'direct' | 'tool' | 'web_search' | 'write_preview' | 'done' | 'fallback'
  label: string
  detail?: string
  meta?: string
  results?: { title: string; source?: string }[]
  /** 当前执行中 → TextShimmer 扫光 */
  active?: boolean
  /** 已完成 → 灰字 */
  mut?: boolean
}

/** 工具名 → kind 启发式映射（未命中一律落 tool 兜底，前端零改动兼容新技能） */
function kindOfTool(name: string): ThoughtEvent['kind'] {
  const n = (name || '').toLowerCase()
  if (n.includes('search') || n.includes('web') || n.includes('热点') || n.includes('trend')) {
    return 'web_search'
  }
  if (
    n.includes('write') ||
    n.includes('save') ||
    n.includes('preview') ||
    n.includes('export') ||
    n.includes('draft')
  ) {
    return 'write_preview'
  }
  return 'tool'
}

/** thought 原文收敛成一行（真身 getThinkingLines 逻辑：去列表符、截断） */
function normalizeLine(line: string, max = 120): string {
  const t = line
    .trim()
    .replace(/^[-*•]\s*/, '')
    .replace(/^\d+[.)]\s*/, '')
    .replace(/\s+/g, ' ')
  return t.length > max ? `${t.slice(0, max)}…` : t
}

export function useThoughtEvents(msg: () => ChatMessage): ComputedRef<ThoughtEvent[]> {
  return computed(() => {
    const m = msg()
    if (!m) return []
    const evs: ThoughtEvent[] = []
    const isLive = m === undefined ? false : true

    // 1) R1 深度思考流 → ext 行（detail 实时增长，Show more 展开）
    const reasoning = (m.reasoning || '').trim()
    if (reasoning) {
      evs.push({
        kind: 'ext',
        label: '深度思考',
        detail: reasoning,
      })
    }

    // 2) ReAct 轮次交错：每轮 = decisions[i]（thought）→ 本轮全部 toolCalls
    const calls: ToolCall[] = m.toolCalls || []
    const decisions: string[] = m.decisions || []
    const callsByIter = new Map<number, ToolCall[]>()
    calls.forEach((c) => {
      const it = c.iteration || 0
      if (!callsByIter.has(it)) callsByIter.set(it, [])
      callsByIter.get(it)!.push(c)
    })
    const iters = [...callsByIter.keys()].sort((a, b) => a - b)
    const totalIters = Math.max(iters.length, decisions.length)

    for (let i = 0; i < totalIters; i++) {
      // thought 行（决策记录）
      if (decisions[i]) {
        evs.push({
          kind: 'direct',
          label: normalizeLine(decisions[i]),
          mut: true,
        })
      }
      // 本轮工具行
      const iterCalls = callsByIter.get(i) || []
      iterCalls.forEach((c) => {
        const running = c.status === 'running'
        const kind = kindOfTool(c.name)
        evs.push({
          kind,
          label:
            c.displayArgs ||
            normalizeLine(c.name || '执行任务', 80),
          meta: c.durationMs ? `${(c.durationMs / 1000).toFixed(1)}s` : undefined,
          active: running,
          mut: !running,
        })
      })
    }

    // 3) 完成行：全部工具都已落定（非流式）时追加 Done
    const anyRunning = calls.some((c) => c.status === 'running')
    if (calls.length > 0 && !anyRunning) {
      evs.push({ kind: 'done', label: 'Done', mut: true })
    }

    return evs
  })
}
