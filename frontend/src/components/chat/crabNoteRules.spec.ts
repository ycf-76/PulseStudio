import { describe, it, expect } from 'vitest'
import {
  type CrabNote,
  type ShowContext,
  DEFAULT_SETTINGS,
  MAX_NOTES,
  MAX_NOTE_TEXT,
  createNote,
  sanitizeNotes,
  sanitizeSettings,
  parseQuietHours,
  inQuietHours,
  isSameDay,
  shouldShowNote,
  pickNote,
  nextMidnight,
} from './crabNoteRules'

/** 造一条可控便签，只覆盖测试关心的字段，其余用默认值 */
function makeNote(patch: Partial<CrabNote> = {}): CrabNote {
  return {
    id: 'n1',
    text: '发邮件',
    emoji: '📧',
    trigger: 'turn_end',
    action: 'fill',
    cooldown_min: 60,
    enabled: true,
    last_shown_at: null,
    last_done_at: null,
    created_at: '2026-01-01T00:00:00.000Z',
    ...patch,
  }
}

/** 默认全绿上下文：无冷却、无免打扰、配额充足 */
function makeCtx(patch: Partial<ShowContext> = {}): ShowContext {
  return {
    settings: { ...DEFAULT_SETTINGS, quiet_hours: '' },
    isPanelOpen: false,
    isStreaming: false,
    heroMode: false,
    now: new Date('2026-01-02T12:00:00').getTime(),
    lastBubbleAt: 0,
    sessionBubbleCount: 0,
    snoozedUntil: {},
    ...patch,
  }
}

describe('createNote', () => {
  it('文本首尾空白被裁掉，超长文本截断到上限', () => {
    const n = createNote({ text: '   写文案   ' })
    expect(n.text).toBe('写文案')

    const long = createNote({ text: 'a'.repeat(MAX_NOTE_TEXT + 50) })
    expect(long.text.length).toBe(MAX_NOTE_TEXT)
  })

  it('未指定的字段回落到默认值，且 id 唯一', () => {
    const a = createNote({ text: 'x' })
    const b = createNote({ text: 'x' })
    expect(a.emoji).toBe('📌')
    expect(a.trigger).toBe('turn_end')
    expect(a.action).toBe('fill')
    expect(a.cooldown_min).toBe(60)
    expect(a.enabled).toBe(true)
    expect(a.id).not.toBe(b.id)
  })

  it('负的冷却时间被夹到 0', () => {
    expect(createNote({ text: 'x', cooldown_min: -5 }).cooldown_min).toBe(0)
  })
})

describe('sanitizeNotes', () => {
  it('非数组输入返回空数组', () => {
    expect(sanitizeNotes(null)).toEqual([])
    expect(sanitizeNotes({})).toEqual([])
    expect(sanitizeNotes('abc')).toEqual([])
  })

  it('丢弃无文本或非对象的条目，保留合法条目', () => {
    const out = sanitizeNotes([
      { text: '有效' },
      { text: '   ' },
      null,
      'str',
      { noText: 1 },
    ])
    expect(out).toHaveLength(1)
    expect(out[0].text).toBe('有效')
  })

  it('非法 trigger/action 回落到默认值', () => {
    const out = sanitizeNotes([{ text: 'a', trigger: 'bogus', action: 'bogus' }])
    expect(out[0].trigger).toBe('turn_end')
    expect(out[0].action).toBe('fill')
  })

  it('缺失 id 时补一个，enabled 缺省为 true', () => {
    const out = sanitizeNotes([{ text: 'a' }])
    expect(out[0].id).toBeTruthy()
    expect(out[0].enabled).toBe(true)
  })

  it('数量超过上限时截断', () => {
    const raw = Array.from({ length: MAX_NOTES + 10 }, (_, i) => ({ text: `n${i}` }))
    expect(sanitizeNotes(raw)).toHaveLength(MAX_NOTES)
  })
})

describe('sanitizeSettings', () => {
  it('非对象输入全部回落到默认值', () => {
    expect(sanitizeSettings(undefined)).toEqual(DEFAULT_SETTINGS)
  })

  it('数值越界被夹到边界', () => {
    const s = sanitizeSettings({ max_per_session: 999, global_cooldown_sec: -3 })
    expect(s.max_per_session).toBe(20)
    expect(s.global_cooldown_sec).toBe(0)
  })

  it('数值非法时回落默认值', () => {
    const s = sanitizeSettings({ max_per_session: 'abc' })
    expect(s.max_per_session).toBe(DEFAULT_SETTINGS.max_per_session)
  })
})

describe('parseQuietHours / inQuietHours', () => {
  it('解析跨零点区间', () => {
    expect(parseQuietHours('23-8')).toEqual([23, 8])
  })

  it('非法格式或首尾相同视为未启用', () => {
    expect(parseQuietHours('')).toBeNull()
    expect(parseQuietHours('abc')).toBeNull()
    expect(parseQuietHours('9-9')).toBeNull()
    expect(parseQuietHours('25-8')).toBeNull()
  })

  it('跨零点区间：23 点与 3 点算免打扰，12 点不算', () => {
    expect(inQuietHours('23-8', new Date('2026-01-02T23:30:00').getTime())).toBe(true)
    expect(inQuietHours('23-8', new Date('2026-01-02T03:00:00').getTime())).toBe(true)
    expect(inQuietHours('23-8', new Date('2026-01-02T12:00:00').getTime())).toBe(false)
  })

  it('非跨零点区间：9-18 之间的 10 点算免打扰', () => {
    expect(inQuietHours('9-18', new Date('2026-01-02T10:00:00').getTime())).toBe(true)
    expect(inQuietHours('9-18', new Date('2026-01-02T20:00:00').getTime())).toBe(false)
  })
})

describe('isSameDay', () => {
  it('空值返回 false', () => {
    expect(isSameDay(null, Date.now())).toBe(false)
  })

  it('同一天返回 true，跨天返回 false', () => {
    const now = new Date('2026-01-02T12:00:00').getTime()
    expect(isSameDay('2026-01-02T01:00:00', now)).toBe(true)
    expect(isSameDay('2026-01-01T23:00:00', now)).toBe(false)
  })
})

describe('shouldShowNote 全局闸门', () => {
  it('总开关关闭时不弹', () => {
    const ctx = makeCtx({ settings: { ...DEFAULT_SETTINGS, quiet_hours: '', notes_enabled: false } })
    expect(shouldShowNote(makeNote(), 'turn_end', ctx)).toBe(false)
  })

  it('单条便签被停用时不弹', () => {
    expect(shouldShowNote(makeNote({ enabled: false }), 'turn_end', makeCtx())).toBe(false)
  })

  it('面板打开时不弹', () => {
    expect(shouldShowNote(makeNote(), 'turn_end', makeCtx({ isPanelOpen: true }))).toBe(false)
  })

  it('流式回复中不弹', () => {
    expect(shouldShowNote(makeNote(), 'turn_end', makeCtx({ isStreaming: true }))).toBe(false)
  })

  it('hero 模式不弹', () => {
    expect(shouldShowNote(makeNote(), 'turn_end', makeCtx({ heroMode: true }))).toBe(false)
  })

  it('免打扰时段内不弹', () => {
    const ctx = makeCtx({
      settings: { ...DEFAULT_SETTINGS, quiet_hours: '23-8' },
      now: new Date('2026-01-02T23:30:00').getTime(),
    })
    expect(shouldShowNote(makeNote(), 'turn_end', ctx)).toBe(false)
  })

  it('全局冷却未过不弹', () => {
    const now = new Date('2026-01-02T12:00:00').getTime()
    const ctx = makeCtx({ now, lastBubbleAt: now - 1000 })
    expect(shouldShowNote(makeNote(), 'turn_end', ctx)).toBe(false)
  })

  it('本轮配额用尽后不弹', () => {
    const ctx = makeCtx({ settings: { ...DEFAULT_SETTINGS, quiet_hours: '', max_per_session: 2 }, sessionBubbleCount: 2 })
    expect(shouldShowNote(makeNote(), 'turn_end', ctx)).toBe(false)
  })
})

describe('shouldShowNote 单条状态', () => {
  it('触发时机不匹配时不弹', () => {
    expect(shouldShowNote(makeNote({ trigger: 'hover' }), 'turn_end', makeCtx())).toBe(false)
  })

  it('单条冷却未过不弹', () => {
    const now = new Date('2026-01-02T12:00:00').getTime()
    const ctx = makeCtx({ now })
    const note = makeNote({ cooldown_min: 60, last_shown_at: new Date(now - 30 * 60_000).toISOString() })
    expect(shouldShowNote(note, 'turn_end', ctx)).toBe(false)
  })

  it('单条冷却已过则允许', () => {
    const now = new Date('2026-01-02T12:00:00').getTime()
    const ctx = makeCtx({ now })
    const note = makeNote({ cooldown_min: 60, last_shown_at: new Date(now - 90 * 60_000).toISOString() })
    expect(shouldShowNote(note, 'turn_end', ctx)).toBe(true)
  })

  it('今天已完成过的便签当天不再弹', () => {
    const now = new Date('2026-01-02T12:00:00').getTime()
    const ctx = makeCtx({ now })
    const note = makeNote({ last_done_at: '2026-01-02T08:00:00' })
    expect(shouldShowNote(note, 'turn_end', ctx)).toBe(false)
  })

  it('昨天完成的便签今天可以弹', () => {
    const now = new Date('2026-01-02T12:00:00').getTime()
    const ctx = makeCtx({ now })
    const note = makeNote({ last_done_at: '2026-01-01T08:00:00' })
    expect(shouldShowNote(note, 'turn_end', ctx)).toBe(true)
  })

  it('被「稍后」静默的便签在静默期内不弹', () => {
    const now = new Date('2026-01-02T12:00:00').getTime()
    const ctx = makeCtx({ now, snoozedUntil: { n1: now + 60_000 } })
    expect(shouldShowNote(makeNote(), 'turn_end', ctx)).toBe(false)
  })
})

describe('pickNote', () => {
  it('没有可用便签时返回 null', () => {
    expect(pickNote([], 'turn_end', makeCtx())).toBeNull()
    expect(pickNote([makeNote({ trigger: 'hover' })], 'turn_end', makeCtx())).toBeNull()
  })

  it('优先选最久没弹过的那条', () => {
    const now = new Date('2026-01-02T12:00:00').getTime()
    const ctx = makeCtx({ now })
    const recent = makeNote({ id: 'recent', last_shown_at: new Date(now - 90 * 60_000).toISOString() })
    const older = makeNote({ id: 'older', last_shown_at: new Date(now - 300 * 60_000).toISOString() })
    expect(pickNote([recent, older], 'turn_end', ctx)?.id).toBe('older')
  })

  it('从没弹过的便签优先级最高', () => {
    const now = new Date('2026-01-02T12:00:00').getTime()
    const ctx = makeCtx({ now })
    const shown = makeNote({ id: 'shown', last_shown_at: new Date(now - 999 * 60_000).toISOString() })
    const never = makeNote({ id: 'never', last_shown_at: null })
    expect(pickNote([shown, never], 'turn_end', ctx)?.id).toBe('never')
  })

  it('同样久没弹过时按创建时间早者优先', () => {
    const ctx = makeCtx()
    const late = makeNote({ id: 'late', created_at: '2026-01-01T10:00:00.000Z' })
    const early = makeNote({ id: 'early', created_at: '2026-01-01T02:00:00.000Z' })
    expect(pickNote([late, early], 'turn_end', ctx)?.id).toBe('early')
  })
})

describe('nextMidnight', () => {
  it('返回当天之后的零点', () => {
    const now = new Date('2026-01-02T12:00:00').getTime()
    const t = new Date(nextMidnight(now))
    expect(t.getDate()).toBe(3)
    expect(t.getHours()).toBe(0)
    expect(t.getMinutes()).toBe(0)
  })
})