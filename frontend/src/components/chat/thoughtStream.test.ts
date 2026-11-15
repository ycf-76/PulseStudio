/**
 * thoughtStream ingest 单测：模拟真实 SSE 帧序列，验证事件归一化。
 * 帧结构与后端一致：{ event_id, type, payload: {...}, turn_id, timestamp }
 */
import { describe, it, expect, beforeEach } from 'vitest'
import { ingestThoughtEvent, useThoughtStream, finalizeActiveTurn } from './thoughtStream'

let seq = 0
const TURN = () => 'turn_test_' + ++seq

function frame(_type: string, payload: Record<string, any> = {}, tid?: string) {
  return { turn_id: tid || TURN(), ...payload }
}

describe('thoughtStream ingest', () => {
  beforeEach(() => {
    // 每例用新 turn 隔离
  })

  it('工具行显示真实工具名（不再是执行任务）', () => {
    const TURN = 'turn_tool_' + ++seq
    ingestThoughtEvent('chat_turn_started', {}, frame('chat_turn_started', {}, TURN))
    ingestThoughtEvent('tool_call_start', { tool_name: 'web_search', iteration: 0 }, frame('tool_call_start', {}, TURN))
    ingestThoughtEvent('tool_call_end', { tool_name: 'web_search', duration_ms: 3200 }, frame('tool_call_end', {}, TURN))
    const { events } = useThoughtStream(() => TURN)
    const toolRow = events.value.find((e) => e.kind === 'web_search')
    expect(toolRow).toBeTruthy()
    expect(toolRow!.label).toBe('搜索网络资料')
    expect(toolRow!.meta).toBe('3.2s')
    expect(toolRow!.active).toBe(false)
    expect(toolRow!.icon).toBe('search')
  })

  it('R1 思考流实时增长（ext 行 detail 累积）', () => {
    const TURN = 'turn_ext_' + ++seq
    ingestThoughtEvent('chat_turn_started', {}, frame('chat_turn_started', {}, TURN))
    ingestThoughtEvent('reasoning_summary_text_delta', { delta: '用户' }, frame('reasoning_summary_text_delta', {}, TURN))
    ingestThoughtEvent('reasoning_summary_text_delta', { delta: '要求搜索' }, frame('reasoning_summary_text_delta', {}, TURN))
    const { events } = useThoughtStream(() => TURN)
    const ext = events.value.find((e) => e.kind === 'ext')
    expect(ext).toBeTruthy()
    expect(ext!.detail).toBe('用户要求搜索')
    expect(ext!.active).toBe(true)
  })

  it('决策行带真实 thought 文本', () => {
    const TURN = 'turn_dec_' + ++seq
    ingestThoughtEvent('chat_turn_started', {}, frame('chat_turn_started', {}, TURN))
    ingestThoughtEvent('decision_made', { decision: '先确定视觉基调再生成卡片' }, frame('decision_made', {}, TURN))
    const { events } = useThoughtStream(() => TURN)
    const direct = events.value.find((e) => e.kind === 'direct')
    expect(direct!.label).toBe('先确定视觉基调再生成卡片')
  })

  it('turn 隔离：不同 turn 的事件互不串扰', () => {
    ingestThoughtEvent('chat_turn_started', {}, frame('chat_turn_started', { turn_id: 'turn_A' }))
    ingestThoughtEvent('tool_call_start', { tool_name: 'search_a' }, frame('tool_call_start', { turn_id: 'turn_A' }))
    ingestThoughtEvent('tool_call_start', { tool_name: 'search_b' }, frame('tool_call_start', { turn_id: 'turn_B' }))
    const a = useThoughtStream(() => 'turn_A')
    const b = useThoughtStream(() => 'turn_B')
    expect(a.events.value.some((e) => e.label === 'search_a')).toBe(true)
    expect(a.events.value.some((e) => e.label === 'search_b')).toBe(false)
    expect(b.events.value.some((e) => e.label === 'search_b')).toBe(true)
    expect(b.events.value.some((e) => e.label === 'search_a')).toBe(false)
  })

  it('workflow_completed 追加 Done 行并置 done', () => {
    const TURN = 'turn_done_' + ++seq
    ingestThoughtEvent('chat_turn_started', {}, frame('chat_turn_started', {}, TURN))
    ingestThoughtEvent('workflow_completed', { success: true }, frame('workflow_completed', {}, TURN))
    const { events, isLive } = useThoughtStream(() => TURN)
    expect(events.value.some((e) => e.kind === 'done')).toBe(true)
    expect(isLive.value).toBe(false)
  })

  it('同名工具双发去重（后端双 start 只显示一行）', () => {
    const TURN = 'turn_dup_' + ++seq
    ingestThoughtEvent('chat_turn_started', {}, frame('chat_turn_started', {}, TURN))
    ingestThoughtEvent('tool_call_start', { tool_name: 'trending_topics' }, frame('tool_call_start', {}, TURN))
    ingestThoughtEvent('tool_call_start', { tool_name: 'trending_topics' }, frame('tool_call_start', {}, TURN))
    const { events } = useThoughtStream(() => TURN)
    const rows = events.value.filter((e) => e.label === 'trending_topics')
    expect(rows.length).toBe(1)
    expect(rows[0].icon).toBe('search')
  })

  it('任务结束清扫：残留 active 行不再扫光', () => {
    const TURN = 'turn_sweep_' + ++seq
    ingestThoughtEvent('chat_turn_started', {}, frame('chat_turn_started', {}, TURN))
    ingestThoughtEvent('tool_call_start', { tool_name: 'feishu_wiki_doc' }, frame('tool_call_start', {}, TURN))
    ingestThoughtEvent('workflow_completed', { success: true }, frame('workflow_completed', {}, TURN))
    const { events, isLive } = useThoughtStream(() => TURN)
    expect(events.value.some((e) => e.active)).toBe(false)
    expect(isLive.value).toBe(false)
  })

  it('未知事件静默吸收（对齐 Claude isAbsorbedSilently，不占渲染行）', () => {
    const TURN = 'turn_fb_' + ++seq
    ingestThoughtEvent('chat_turn_started', {}, frame('chat_turn_started', {}, TURN))
    ingestThoughtEvent('super_new_skill_started', { x: 1 }, frame('super_new_skill_started', {}, TURN))
    ingestThoughtEvent('node_started', { x: 1 }, frame('node_started', {}, TURN))
    const { events } = useThoughtStream(() => TURN)
    expect(events.value.some((e) => e.kind === 'fallback')).toBe(false)
    expect(events.value.length).toBe(0)
  })

  it('连续低层命令合并为一行 ×N（不逐条出行）', () => {
    const TURN = 'turn_merge_' + ++seq
    ingestThoughtEvent('chat_turn_started', {}, frame('chat_turn_started', {}, TURN))
    ingestThoughtEvent('tool_call_start', { tool_name: 'bash', inputs: { command: 'ffmpeg -i a.mp4' } }, frame('tool_call_start', {}, TURN))
    ingestThoughtEvent('tool_call_end', { tool_name: 'bash', success: true, duration_ms: 800 }, frame('tool_call_end', {}, TURN))
    ingestThoughtEvent('tool_call_start', { tool_name: 'bash', inputs: { command: 'ffprobe -v quiet b.mp4' } }, frame('tool_call_start', {}, TURN))
    ingestThoughtEvent('tool_call_end', { tool_name: 'bash', success: true, duration_ms: 300 }, frame('tool_call_end', {}, TURN))
    ingestThoughtEvent('tool_call_start', { tool_name: 'bash', inputs: { command: 'ffmpeg -vf scale c.mp4' } }, frame('tool_call_start', {}, TURN))
    const { events } = useThoughtStream(() => TURN)
    const lowRows = events.value.filter((e) => e.lowLevel)
    expect(lowRows.length).toBe(1)
    expect(lowRows[0].label).toBe('执行命令')
    expect(lowRows[0].count).toBe(3)
    // 三条命令原文都留在详情里，不丢
    expect(lowRows[0].detail).toContain('ffmpeg -i a.mp4')
    expect(lowRows[0].detail).toContain('ffprobe -v quiet b.mp4')
    expect(lowRows[0].detail).toContain('ffmpeg -vf scale c.mp4')
    // 最后一次调用执行中 → 合并行重新点亮
    expect(lowRows[0].active).toBe(true)
  })

  it('低层行被技能行打断后，新命令开新行不并入', () => {
    const TURN = 'turn_split_' + ++seq
    ingestThoughtEvent('chat_turn_started', {}, frame('chat_turn_started', {}, TURN))
    ingestThoughtEvent('tool_call_start', { tool_name: 'bash', inputs: { command: 'ls' } }, frame('tool_call_start', {}, TURN))
    ingestThoughtEvent('tool_call_end', { tool_name: 'bash', success: true }, frame('tool_call_end', {}, TURN))
    ingestThoughtEvent('tool_call_start', { tool_name: 'web_search', inputs: { query: '热点' } }, frame('tool_call_start', {}, TURN))
    ingestThoughtEvent('tool_call_end', { tool_name: 'web_search', success: true }, frame('tool_call_end', {}, TURN))
    ingestThoughtEvent('tool_call_start', { tool_name: 'bash', inputs: { command: 'cat x' } }, frame('tool_call_start', {}, TURN))
    const { events } = useThoughtStream(() => TURN)
    const lowRows = events.value.filter((e) => e.lowLevel)
    expect(lowRows.length).toBe(2)
    expect(lowRows[0].count).toBe(1)
    expect(lowRows[1].count).toBe(1)
  })

  it('失败的低层行标红留原地，后续命令开新行', () => {
    const TURN = 'turn_fail_' + ++seq
    ingestThoughtEvent('chat_turn_started', {}, frame('chat_turn_started', {}, TURN))
    ingestThoughtEvent('tool_call_start', { tool_name: 'bash', inputs: { command: 'ffmpeg -i bad.mp4' } }, frame('tool_call_start', {}, TURN))
    ingestThoughtEvent('tool_call_end', { tool_name: 'bash', success: false }, frame('tool_call_end', {}, TURN))
    ingestThoughtEvent('tool_call_start', { tool_name: 'bash', inputs: { command: 'ffmpeg -i good.mp4' } }, frame('tool_call_start', {}, TURN))
    const { events } = useThoughtStream(() => TURN)
    const lowRows = events.value.filter((e) => e.lowLevel)
    expect(lowRows.length).toBe(2)
    expect(lowRows[0].failed).toBe(true)
    expect(lowRows[0].count).toBe(1)
    expect(lowRows[1].failed).toBeFalsy()
    expect(lowRows[1].active).toBe(true)
  })
})

describe('thoughtStream 暂停/兜底熄灯（2026-11-06）', () => {
  it('guardian_confirmation_required 关桶：摘要行不再永转「思考中」', () => {
    const TURN = 'turn_pause_' + ++seq
    ingestThoughtEvent('chat_turn_started', {}, frame('chat_turn_started', {}, TURN))
    ingestThoughtEvent('tool_call_start', { tool_name: 'bash', inputs: { command: 'base64 x.csv' } }, frame('tool_call_start', {}, TURN))
    // 后端 guardian 暂停：按设计 skip agent_loop_done，只发确认卡事件
    ingestThoughtEvent('guardian_confirmation_required', {}, frame('guardian_confirmation_required', {}, TURN))
    const { events, isLive } = useThoughtStream(() => TURN)
    expect(isLive.value).toBe(false)
    const doneRow = events.value.find((e) => e.kind === 'done')
    expect(doneRow?.label).toBe('已暂停 · 等待确认')
    // 残留 active 行全部落定，扫光熄灭
    expect(events.value.some((e) => e.active)).toBe(false)
  })

  it('cut_plan / clarification / storyboard / side_write 暂停同样关桶', () => {
    for (const ev of ['cut_plan_confirmation_required', 'agent_clarification_required', 'storyboard_confirmation_required', 'side_write_required']) {
      const TURN = 'turn_p_' + ev + '_' + ++seq
      ingestThoughtEvent('chat_turn_started', {}, frame('chat_turn_started', {}, TURN))
      ingestThoughtEvent(ev, {}, frame(ev, {}, TURN))
      const { isLive } = useThoughtStream(() => TURN)
      expect(isLive.value).toBe(false)
    }
  })

  it('暂停后批准续跑：tool_call_start 把 done 桶拉回 live 并清掉假结束行', () => {
    const TURN = 'turn_resume_' + ++seq
    ingestThoughtEvent('chat_turn_started', {}, frame('chat_turn_started', {}, TURN))
    ingestThoughtEvent('guardian_confirmation_required', {}, frame('guardian_confirmation_required', {}, TURN))
    ingestThoughtEvent('tool_call_start', { tool_name: 'skill_call', inputs: { skill: 'feishu_file_ops' } }, frame('tool_call_start', {}, TURN))
    const { events, isLive } = useThoughtStream(() => TURN)
    expect(isLive.value).toBe(true)
    expect(events.value.some((e) => e.kind === 'done')).toBe(false)
    expect(events.value.some((e) => e.active && e.label === 'feishu_file_ops')).toBe(true)
  })

  it('finalizeActiveTurn 兜底：终态丢失（paused skip agent_loop_done + 订阅被清）时 UI 收尾强制熄灯', () => {
    const TURN = 'turn_fin_' + ++seq
    ingestThoughtEvent('chat_turn_started', {}, frame('chat_turn_started', {}, TURN))
    ingestThoughtEvent('tool_call_start', { tool_name: 'file_write' }, frame('tool_call_start', {}, TURN))
    // 没有 workflow_completed / agent_loop_done —— 直接 UI 收尾
    finalizeActiveTurn()
    const { events, isLive } = useThoughtStream(() => TURN)
    expect(isLive.value).toBe(false)
    expect(events.value.some((e) => e.kind === 'done')).toBe(true)
    expect(events.value.some((e) => e.active)).toBe(false)
    // 幂等：再调一次不重复挂 done 行
    finalizeActiveTurn()
    expect(events.value.filter((e) => e.kind === 'done').length).toBe(1)
  })
})
