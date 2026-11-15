import { ref, watch } from 'vue'

/**
 * 流式输出速度配置（渲染层重构 L3）
 *
 * 参数模型对齐 StreamEngine 两档设计（旧版 targetLag 目标延迟模型已退役）：
 * - baseCps：正文 Smooth 档恒定速率（默认 160 ≈ 25~30 汉字/秒，对齐 codex
 *   实际感知速度；两次用户反馈旧默认 400 太快的根治）
 * - thinkCps：思考独立播放器速率（默认 140。2026-11-08 从 90 上调：reasoning
 *   到达速率 ~100-200cps 高于 90，播放永远落后、终态一次性蹦出，用户反馈
 *   「思考链路卡、不流畅」）
 * - minCps / maxCps：CatchUp 追赶的速率上下界（追赶目标 = 积压/catchUpSeconds，
 *   但被 2×baseCps 倍数闸封顶，见 stream-engine.ts maxCatchUpMultiplier）
 * - frameMs：DOM 重渲染节流间隔
 *
 * 老用户 localStorage 旧配置（targetLag 键）不主动迁移：读不到新键就回落默认，
 * 不覆盖用户既有选择。
 */

const STORAGE_KEY = 'stream_speed_config'

const DEFAULTS = {
  baseCps: 160,
  thinkCps: 140,
  minCps: 60,
  maxCps: 4800,
  frameMs: 16,
}

const BOUNDS = {
  baseCps: { min: 40, max: 800, step: 10 },
  thinkCps: { min: 20, max: 400, step: 10 },
  minCps: { min: 20, max: 400, step: 10 },
  maxCps: { min: 500, max: 10000, step: 100 },
  frameMs: { min: 8, max: 64, step: 4 },
}

function _clamp(v: number, b: { min: number; max: number }): number {
  return Math.max(b.min, Math.min(b.max, v))
}

function _load(): typeof DEFAULTS {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw) {
      const parsed = JSON.parse(raw)
      // 旧键 targetLag 已退役，直接忽略；minCps/maxCps/frameMs 沿用旧值（若在界内）
      // thinkCps：恰好等于旧默认 90 视为「从未调过」，迁移到新默认 140——
      // 否则老用户的存量设置会永远锁在旧体验上。显式选了其他值则尊重用户。
      return {
        baseCps: typeof parsed.baseCps === 'number' ? _clamp(parsed.baseCps, BOUNDS.baseCps) : DEFAULTS.baseCps,
        thinkCps: typeof parsed.thinkCps === 'number' && parsed.thinkCps !== 90 ? _clamp(parsed.thinkCps, BOUNDS.thinkCps) : DEFAULTS.thinkCps,
        minCps: typeof parsed.minCps === 'number' ? _clamp(parsed.minCps, BOUNDS.minCps) : DEFAULTS.minCps,
        maxCps: typeof parsed.maxCps === 'number' ? _clamp(parsed.maxCps, BOUNDS.maxCps) : DEFAULTS.maxCps,
        frameMs: typeof parsed.frameMs === 'number' ? _clamp(parsed.frameMs, BOUNDS.frameMs) : DEFAULTS.frameMs,
      }
    }
  } catch {}
  return { ...DEFAULTS }
}

function _save(vals: typeof DEFAULTS) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(vals))
  } catch {}
}

const saved = _load()

export const streamBaseCps = ref(saved.baseCps)
export const streamThinkCps = ref(saved.thinkCps)
export const streamMinCps = ref(saved.minCps)
export const streamMaxCps = ref(saved.maxCps)
export const streamFrameMs = ref(saved.frameMs)

export const streamSpeedBounds = BOUNDS
export const streamSpeedDefaults = DEFAULTS

let _watchInstalled = false

function _syncToStorage() {
  _save({
    baseCps: streamBaseCps.value,
    thinkCps: streamThinkCps.value,
    minCps: streamMinCps.value,
    maxCps: streamMaxCps.value,
    frameMs: streamFrameMs.value,
  })
}

function _ensureWatch() {
  if (_watchInstalled) return
  _watchInstalled = true
  watch([streamBaseCps, streamThinkCps, streamMinCps, streamMaxCps, streamFrameMs], _syncToStorage)
}

export function useStreamSpeed() {
  _ensureWatch()
  return {
    baseCps: streamBaseCps,
    thinkCps: streamThinkCps,
    minCps: streamMinCps,
    maxCps: streamMaxCps,
    frameMs: streamFrameMs,
    bounds: BOUNDS,
    defaults: DEFAULTS,
    reset: resetStreamSpeed,
  }
}

export function resetStreamSpeed() {
  streamBaseCps.value = DEFAULTS.baseCps
  streamThinkCps.value = DEFAULTS.thinkCps
  streamMinCps.value = DEFAULTS.minCps
  streamMaxCps.value = DEFAULTS.maxCps
  streamFrameMs.value = DEFAULTS.frameMs
  _syncToStorage()
}
