/**
 * ThinkingChain 组件级回归（2026-11-07 四次改版，大哥报的三个 bug）：
 * ① 工具调用链路被吃掉 → 流式期间折叠层必须自动展开（工具行实时可见）
 * ② 深度思考预览不流式 → 客户端打字机向目标追赶，即使后端大块一次性下发也流式生长
 * ③ 没有扫光 → 流式期间预览文字带 tc-shim 扫光
 */
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import ThinkingChain from './ThinkingChain.vue'
import { ingestThoughtEvent } from './thoughtStream'

const TID = 'turn_tc_comp_1'

function fire(type: string, payload: Record<string, any> = {}) {
  ingestThoughtEvent(type, payload, { turn_id: TID, event_id: `${type}_${Math.random()}`, type, payload })
}

const wait = (ms: number) => new Promise((r) => setTimeout(r, ms))

describe('ThinkingChain 四次改版回归', () => {
  it('流式期间自动展开链路，工具行实时可见', async () => {
    fire('chat_turn_started')
    fire('reasoning_summary_text_delta', { delta: '先搜索资料' })
    fire('tool_call_start', { tool_name: 'web_search', inputs: { query: '知识管理' } })
    fire('tool_call_end', { duration_ms: 2100 })
    fire('decision_made', { decision: '搜到了，开始整理' })

    const w = mount(ThinkingChain, { props: { turnId: TID } })
    await nextTick()
    // 流式中折叠层自动展开（grid-template-rows: 1fr），不用手动点
    expect(w.find('.tc-fold').attributes('style')).toContain('1fr')
    const labels = w.findAll('.tc-row .tc-label').map((n) => n.text())
    expect(labels.some((t) => t.includes('搜索网络资料'))).toBe(true)
    expect(labels.some((t) => t.includes('搜到了，开始整理'))).toBe(true)
    expect(w.find('.tc-ext').text()).toContain('深度思考')
    w.unmount()
  })

  it('深度思考预览打字机流式生长 + 带扫光', async () => {
    fire('chat_turn_started')
    fire('reasoning_summary_text_delta', { delta: '第一段思考' })
    const w = mount(ThinkingChain, { props: { turnId: TID } })
    await nextTick()
    // 打字机追赶（慢环境下放宽等待：125 字/s，5 字符 ~2 tick 也留足余量）
    await wait(400)
    expect(w.find('.tc-ext-preview').text()).toContain('第一段思考')
    expect(w.find('.tc-ext-preview.tc-shim').exists()).toBe(true)
    const before = w.find('.tc-ext-preview').text().length
    fire('reasoning_summary_text_delta', { delta: '，第二段继续生长' })
    await nextTick()
    await wait(500)
    const after = w.find('.tc-ext-preview').text()
    expect(after.length).toBeGreaterThan(before)
    expect(after).toContain('第二段继续')
    w.unmount()
  })

  it('完成态：扫光熄灭、折叠层收成摘要行', async () => {
    fire('chat_turn_started')
    fire('reasoning_summary_text_delta', { delta: '思考内容' })
    fire('workflow_completed', { ok: true })
    const w = mount(ThinkingChain, { props: { turnId: TID, streaming: false } })
    await nextTick()
    await wait(400)
    expect(w.find('.tc-sum').text()).toContain('已思考')
    // 2026-11-07 修复回归：完成态标题行不得再出现「思考中」（tag 曾无条件渲染）
    expect(w.find('.tc-sum').text()).not.toContain('思考中')
    expect(w.find('.tc-sum-tag').exists()).toBe(false)
    expect(w.find('.tc-ext.tc-shim, .tc-ext-preview.tc-shim').exists()).toBe(false)
    w.unmount()
  })

  // 2026-11-14 回归（大哥截图实锤）：guardian 确认闸门暂停后，SSE 流还开着
  //（streaming prop 为 true）而已关桶的 statusText 又是「思考完成」——标题行
  // 同框出现「思考中 + 思考完成」矛盾态。暂停态必须显「等待确认」、扫光熄灭。
  it('暂停态：显「等待确认」扫光熄灭，批准续跑回到「思考中」', async () => {
    fire('chat_turn_started')
    fire('reasoning_summary_text_delta', { delta: '准备清理未跟踪文件' })
    fire('guardian_confirmation_required', {})
    const w = mount(ThinkingChain, { props: { turnId: TID, streaming: true } })
    await nextTick()
    const sum = w.find('.tc-sum').text()
    expect(sum).toContain('等待确认')
    expect(sum).not.toContain('思考中')
    expect(sum).not.toContain('思考完成')
    // 暂停态扫光必须熄灭（等用户拍板的活不演"正在思考"）
    expect(w.find('.tc-ext-preview.tc-shim').exists()).toBe(false)
    // 批准续跑：tool_call_start 把桶拉回 live，暂停态解除
    fire('tool_call_start', { tool_name: 'bash', inputs: { command: 'git clean -f x.log' } })
    await nextTick()
    expect(w.find('.tc-sum').text()).toContain('思考中')
    w.unmount()
  })
})
