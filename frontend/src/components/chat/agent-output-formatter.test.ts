import { describe, it, expect } from 'vitest'
import { isReactProtocolJson, formatAgentOutput } from './agent-output-formatter'

describe('isReactProtocolJson — ReAct 协议 JSON 污染识别', () => {
  it('识别带 thought 的协议 JSON', () => {
    expect(isReactProtocolJson('{"thought": "先搜爆款", "tool_calls": []}')).toBe(true)
  })

  it('识别带 final 的协议 JSON', () => {
    expect(isReactProtocolJson('{"thought": "完成", "final": true, "output": {"_message": "hi"}}')).toBe(true)
  })

  it('识别流式截断的残 JSON（未闭合）', () => {
    expect(isReactProtocolJson('{"thought": "先搜爆款", "tool_calls": [')).toBe(true)
  })

  it('不误判用户正文散文', () => {
    expect(isReactProtocolJson('这篇笔记的标题我建议用「手冲入门」，开头先抛一个痛点。')).toBe(false)
    expect(isReactProtocolJson('')).toBe(false)
    expect(isReactProtocolJson('**标题**：三步搞定')).toBe(false)
  })

  it('不误判合法 JSON 卡片（无协议键）', () => {
    expect(isReactProtocolJson('{"content": "正文", "tags": ["咖啡"]}')).toBe(false)
  })

  it('不误判数组 JSON', () => {
    expect(isReactProtocolJson('[{"thought": "x"}]')).toBe(false)
  })
})

describe('formatAgentOutput — 后端产出渲染成人话', () => {
  it('_message 作为首选人话正文', () => {
    const out = formatAgentOutput({
      _message: '已帮你写完这期笔记',
      _iterations: 3,
      _token_usage: 100,
      thought: '完成',
      tool_calls: [],
    })
    expect(out).toContain('已帮你写完这期笔记')
    expect(out).not.toContain('"thought"')
    expect(out).not.toContain('"tool_calls"')
  })

  it('无用户字段时不输出内部字段', () => {
    const out = formatAgentOutput({ _iterations: 2, _last_thought: 'x', _token_usage: 5 })
    expect(out).toBe('（处理完成）')
  })
})
