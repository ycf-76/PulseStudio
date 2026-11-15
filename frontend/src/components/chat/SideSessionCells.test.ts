/**
 * 侧会话前端契约单测（Task 8）：
 * 1. isSideDigestMessage：agent_meta.message_type === 'side_session_digest' 判定；
 * 2. applySideWriteRequired：SSE side_write_confirmation_required → meta 状态映射；
 * 3. deriveSideWriteVisible：待确认显示、确认/拒绝后隐藏。
 */
import { describe, it, expect } from 'vitest'
import { isSideDigestMessage, applySideWriteRequired, deriveSideWriteVisible } from './cell-types'
import type { AgentMeta } from './cell-types'

function meta(patch: Partial<AgentMeta>): AgentMeta {
  return { ...patch } as AgentMeta
}

describe('isSideDigestMessage', () => {
  it('message_type=side_session_digest → true', () => {
    expect(isSideDigestMessage(meta({ messageType: 'side_session_digest' }))).toBe(true)
  })
  it('普通消息 → false', () => {
    expect(isSideDigestMessage(meta({ workflowStatus: 'completed' }))).toBe(false)
    expect(isSideDigestMessage(meta({ messageType: undefined }))).toBe(false)
  })
})

describe('applySideWriteRequired（SSE 载荷 → meta）', () => {
  it('写入 workflowStatus 与 sideWrite 详情', () => {
    const m = meta({ workflowStatus: 'running' })
    const out = applySideWriteRequired(m, {
      tool_name: 'file_write',
      description: '侧会话请求写文件',
      permissions: ['file_write'],
      side_session_id: 'side1',
      loop_state_path: '/tmp/loop.json',
      iteration: 2,
    })
    expect(out.workflowStatus).toBe('awaiting_side_write_confirmation')
    expect(out.sideWrite?.toolName).toBe('file_write')
    expect(out.sideWrite?.description).toBe('侧会话请求写文件')
    expect(out.sideWrite?.sideSessionId).toBe('side1')
    expect(out.loopStatePath).toBe('/tmp/loop.json')
  })

  it('载荷缺字段时容错', () => {
    const out = applySideWriteRequired(meta({}), {})
    expect(out.workflowStatus).toBe('awaiting_side_write_confirmation')
    expect(out.sideWrite?.toolName).toBe('')
  })
})

describe('deriveSideWriteVisible', () => {
  it('待确认且有 loopStatePath → 显示', () => {
    expect(deriveSideWriteVisible(meta({
      workflowStatus: 'awaiting_side_write_confirmation',
      loopStatePath: '/tmp/loop.json',
    }))).toBe(true)
  })
  it('确认/拒绝后 → 隐藏', () => {
    expect(deriveSideWriteVisible(meta({
      workflowStatus: 'awaiting_side_write_confirmation',
      loopStatePath: '/tmp/loop.json',
      sideWriteResolved: 'confirmed',
    }))).toBe(false)
  })
  it('无 loopStatePath → 隐藏（状态失效，走丢失兜底）', () => {
    expect(deriveSideWriteVisible(meta({
      workflowStatus: 'awaiting_side_write_confirmation',
    }))).toBe(false)
  })
})
