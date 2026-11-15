/**
 * VideoTaskCard 阶段推导单测（设计文档 3.4 #3：四阶段容器卡，一张卡原位切换）。
 * 覆盖 plan/progress/done/failed/cancelled 五态与「无口播状态不出卡」，
 * 以及真实时序下的优先级：确认回 ok 早于渲染完成、二轮待确认时旧进度残留。
 */
import { describe, it, expect } from 'vitest'
import { deriveVideoStage } from './VideoTaskCard.vue'
import type { AgentMeta } from './cell-types'

function meta(patch: Partial<AgentMeta>): AgentMeta {
  return { ...patch }
}

describe('deriveVideoStage（VideoTaskCard 四阶段推导）', () => {
  it('无任何口播状态 → 不出卡（null）', () => {
    expect(deriveVideoStage(undefined)).toBeNull()
    expect(deriveVideoStage(null)).toBeNull()
    expect(deriveVideoStage(meta({ workflowStatus: 'running' }))).toBeNull()
  })

  it('非口播场景的 videoProgress（auto_short_video）→ 不出卡（口播进度行仍复用）', () => {
    expect(deriveVideoStage(meta({
      videoProgress: { skill: 'auto_short_video', percent: 50 },
    }))).toBeNull()
    // 口播 skill 的进度才触发容器卡
    expect(deriveVideoStage(meta({
      videoProgress: { skill: 'talking_head_overlay', percent: 50 },
    }))).toBe('progress')
  })

  it('awaiting_cut_plan_confirmation 且无决策 → plan', () => {
    expect(deriveVideoStage(meta({
      workflowStatus: 'awaiting_cut_plan_confirmation',
      cutPlan: { plan: { segments: [] } },
    }))).toBe('plan')
  })

  it('有 videoProgress（percent<100）且未确认 → progress', () => {
    expect(deriveVideoStage(meta({
      workflowStatus: 'running',
      videoProgress: { skill: 'talking_head_overlay', phase: 'render', percent: 42 },
    }))).toBe('progress')
  })

  it('confirmed → done（成片/版本）', () => {
    expect(deriveVideoStage(meta({
      cutPlanResolved: 'confirmed',
      cutPlanVersions: [{ version: 1, assetUrl: 'http://x/v1.mp4' }],
    }))).toBe('done')
  })

  it('确认即回 ok 但渲染仍在进行（percent<100）→ 先显示 progress 再转 done', () => {
    const rendering = meta({
      cutPlanResolved: 'confirmed',
      videoProgress: { skill: 'talking_head_overlay', phase: 'render', percent: 60 },
    })
    expect(deriveVideoStage(rendering)).toBe('progress')
    const finished = meta({
      cutPlanResolved: 'confirmed',
      cutPlanVersions: [{ version: 1, assetUrl: 'http://x/v1.mp4' }],
      videoProgress: { skill: 'talking_head_overlay', percent: 100 },
    })
    expect(deriveVideoStage(finished)).toBe('done')
  })

  it('failed 优先于进度（错误 + 重试入口）', () => {
    expect(deriveVideoStage(meta({
      cutPlanResolved: 'failed',
      cutPlanError: 'ffmpeg 渲染失败',
      videoProgress: { skill: 'talking_head_overlay', percent: 30 },
    }))).toBe('failed')
  })

  it('cancelled → cancelled（卡内显示已取消提示）', () => {
    expect(deriveVideoStage(meta({
      cutPlan: { plan: {} },
      workflowStatus: 'awaiting_cut_plan_confirmation',
      cutPlanResolved: 'cancelled',
    }))).toBe('cancelled')
  })

  it('二轮待确认时旧一轮 videoProgress 残留 → 仍显示 plan（方案优先）', () => {
    expect(deriveVideoStage(meta({
      workflowStatus: 'awaiting_cut_plan_confirmation',
      cutPlan: { plan: {} },
      cutPlanResolved: undefined,
      videoProgress: { skill: 'talking_head_overlay', percent: 100 },
    }))).toBe('plan')
  })
})
