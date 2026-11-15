import { describe, it, expect } from 'vitest'
import { advanceBlocks, createBlockScanState, markBlockEnter } from './stable-blocks'
import { renderMarkdown } from './markdown-renderer'

/**
 * 顶层块安全切分器等价性测试。
 *
 * 核心不变量：流式增量渲染（块缓存拼接 + 尾部渲染）的结果
 * 必须与全文一次性渲染完全一致（HTML 字符串相等）。
 * 这是「每帧只重渲尾部」策略的正确性地基——一旦切错块，
 * 用户看到的流式中间态和最终定稿就会不一样。
 *
 * 流式模拟：全文按 delta 切片喂入，每轮 advanceBlocks（to = 已到达长度），
 * 结束后 blocks.join('') + renderMarkdown(剩余) 对比 renderMarkdown(全文)。
 */

/** 用一组 delta 模拟流式，返回增量渲染的完整 HTML */
function streamRender(full: string, deltas: string[]): string {
  let src = ''
  const st = createBlockScanState()
  for (const d of deltas) {
    src += d
    // 模拟 enqueuedStableLen：最后一个换行（对齐 useChatSSE 的提交边界）
    const to = src.lastIndexOf('\n') + 1
    advanceBlocks(src, to, st, (chunk) => renderMarkdown(chunk))
  }
  // 终态：剩余未闭合组作为尾部渲染
  const tail = renderMarkdown(src.slice(st.blockScanLen))
  return st.blocks.join('') + tail
}

/** 全文切成若干 delta（模拟 token 流） */
function chop(text: string, size = 7): string[] {
  const out: string[] = []
  for (let i = 0; i < text.length; i += size) out.push(text.slice(i, i + size))
  return out
}

const CASES: Array<[string, string]> = [
  ['纯段落', '第一段内容。\n\n第二段内容，跟在空行后面。\n\n第三段收尾。'],
  ['标题与段落', '## 大标题\n\n正文段落。\n\n### 小标题\n\n更多正文。'],
  ['围栏内含空行', '前面的话。\n\n```python\ndef f():\n\n    return 1\n\n```\n\n围栏后的段落。'],
  ['无空行列表', '前置说明：\n\n- 第一项\n- 第二项\n- 第三项\n\n列表后正文。'],
  ['loose list（空行+缩进延续）', '- 第一项\n\n  续段属于第一项。\n\n- 第二项\n\n后续段落。'],
  ['有序列表', '步骤如下：\n\n1. 先做这个\n2. 再做那个\n3. 最后收尾\n\n完成。'],
  ['表格', '表头说明：\n\n| A | B |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |\n\n表格后正文。'],
  ['引用块', '开始。\n\n> 引用第一行\n> 引用第二行\n\n> 另一个引用\n\n结束。'],
  ['混合长文', '## 报告\n\n第一段。\n\n```js\nconst a = 1\n\nconst b = 2\n```\n\n- 甲\n- 乙\n\n| K | V |\n|---|---|\n| 1 | 2 |\n\n> 结论引用\n\n尾段。'],
]

describe('stable-blocks · 分块渲染与整体渲染等价性', () => {
  for (const [name, full] of CASES) {
    it(`等价：${name}`, () => {
      const expected = renderMarkdown(full)
      const actual = streamRender(full, chop(full))
      expect(actual).toBe(expected)
    })
  }

  it('等价：不同 delta 粒度结果一致（7字/13字/整段）', () => {
    const full = CASES[CASES.length - 1][1]
    const expected = renderMarkdown(full)
    for (const size of [7, 13, 40]) {
      expect(streamRender(full, chop(full, size))).toBe(expected)
    }
  })

  it('块缓存单调推进：同源前缀重复调用不产生重复块', () => {
    const src = 'aaa\n\nbbb\n\nccc\n'
    const st = createBlockScanState()
    advanceBlocks(src, 6, st, (c) => renderMarkdown(c))
    const n1 = st.blocks.length
    advanceBlocks(src, 6, st, (c) => renderMarkdown(c))
    expect(st.blocks.length).toBe(n1)
    advanceBlocks(src, src.length, st, (c) => renderMarkdown(c))
    expect(st.blockScanLen).toBeGreaterThan(6)
    expect(st.blocks.length).toBeGreaterThanOrEqual(n1)
  })

  it('围栏内空行不产生切分点', () => {
    const src = '```js\na\n\nb\n```\n\ntail\n'
    const st = createBlockScanState()
    advanceBlocks(src, src.length, st, (c) => renderMarkdown(c))
    // 围栏整体 + 后续段落之间才有切分；围栏内部空行无效
    expect(st.blocks.length).toBeLessThanOrEqual(2)
    expect(st.inFence).toBe(false)
  })

  it('回归：悬置的空行候选在下一行到达后仍可切分（逐字符增量喂入）', () => {
    // 复现修复前的 bug：空行先于其后的内容到达扫描器时，scanPos 越过空行，
    // 边界候选永久丢失——最后一个段落永远无法闭合进块缓存。
    const src = '第一段内容足够长。\n\n尾部正在生长\n'
    const st = createBlockScanState()
    for (let end = 1; end <= src.length; end++) {
      advanceBlocks(src, end, st, (c) => renderMarkdown(c))
    }
    expect(st.blockScanLen).toBeGreaterThan(0)
    expect(st.blocks.join('')).toContain('第一段内容足够长')
    // 终态等价：块缓存 + 尾部 == 整体渲染
    expect(st.blocks.join('') + renderMarkdown(src.slice(st.blockScanLen))).toBe(renderMarkdown(src))
  })
})

describe('markBlockEnter（块级淡入标记）', () => {
  it('裸根标签注入 class', () => {
    expect(markBlockEnter('<p>你好</p>')).toBe('<p class="rl-enter">你好</p>')
    expect(markBlockEnter('<h2>标题</h2>')).toBe('<h2 class="rl-enter">标题</h2>')
  })

  it('根元素已有 class 时合并而非覆盖（双 class 属性会让浏览器丢弃第二个）', () => {
    const out = markBlockEnter('<div class="rl-json-card">{"a":1}</div>')
    expect(out).toBe('<div class="rl-enter rl-json-card">{"a":1}</div>')
  })

  it('无法识别根标签时原样返回，不破坏 HTML', () => {
    expect(markBlockEnter('')).toBe('')
    expect(markBlockEnter('纯文本')).toBe('纯文本')
  })
})
