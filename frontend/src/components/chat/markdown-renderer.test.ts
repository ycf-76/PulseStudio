import { describe, it, expect } from 'vitest'
import { renderPlainTail, renderMarkdownStreaming, renderMarkdown } from './markdown-renderer'

describe('markdown-renderer · 尾部纯文本快速路径', () => {
  it('纯文本走快速路径：转义 + breaks 语义', () => {
    const html = renderPlainTail('普通段落第一行\n第二行')
    expect(html).toBe('<p>普通段落第一行<br/>第二行</p>')
  })

  it('HTML 特殊字符被转义', () => {
    // 注意：< 会被语法嗅探判定为潜在 HTML 标签而走慢路径（保守方向，正确），
    // 纯文本快速路径用例只放 & 和 "
    const html = renderPlainTail('a & b " c')
    expect(html).toBe('<p>a &amp; b &quot; c</p>')
  })

  it('空串返回 null（调用方兜底）', () => {
    expect(renderPlainTail('')).toBeNull()
  })

  const MD_SAMPLES: Array<[string, string]> = [
    ['粗体', '这是**加粗**文本'],
    ['斜体', '这是 *斜体* 文本'],
    ['行内代码', '运行 `npm run dev` 即可'],
    ['删除线', '~~废弃方案~~'],
    ['高亮', '这是==重点==内容'],
    ['链接', '见 [文档](https://example.com)'],
    ['图片', '看这张 ![封面](a.png)'],
    ['标题', '## 小节标题'],
    ['无序列表', '步骤：\n- 先做这个\n- 再做那个'],
    ['有序列表', '1. 第一\n2. 第二'],
    ['表格', '| A | B |\n|---|---|'],
    ['引用', '> 引用一行'],
    ['HTML 标签', 'hello <tag> world'],
    ['URL 自动链接', '访问 https://example.com 查看'],
    ['分隔线', '上文\n\n---\n\n下文'],
  ]

  for (const [name, text] of MD_SAMPLES) {
    it(`含${name}语法时退回慢路径（null）`, () => {
      expect(renderPlainTail(text)).toBeNull()
    })
  }
})

describe('markdown-renderer · 流式免高亮开关', () => {
  const CODE = '```python\ndef hello():\n    return "hi"\n```\n'

  it('普通渲染：代码块带 hljs 高亮 span', () => {
    const html = renderMarkdown(CODE)
    expect(html).toContain('rl-code-block')
    expect(html).toContain('hljs-')
  })

  it('流式渲染：跳过高亮，原文转义输出，且开关不外泄', () => {
    const streamingHtml = renderMarkdownStreaming(CODE)
    expect(streamingHtml).toContain('rl-code-block')
    expect(streamingHtml).not.toContain('hljs-')
    // 开关恢复：同内容普通渲染仍带高亮
    expect(renderMarkdown(CODE)).toContain('hljs-')
  })

  it('流式渲染对无围栏文本与普通渲染等价', () => {
    const text = '## 标题\n\n正文**加粗**。'
    expect(renderMarkdownStreaming(text)).toBe(renderMarkdown(text))
  })
})

describe('markdown-renderer · CJK 加粗补丁（markdown-it-cjk-friendly）', () => {
  it('闭合 ** 前是全角标点、后跟汉字时正常闭合（CommonMark 裸星号 bug）', () => {
    const html = renderMarkdown('**【我要打控卫】**这条，钩子是标准的双段式')
    expect(html).toContain('<strong>【我要打控卫】</strong>')
    expect(html).not.toContain('**')
  })

  it('起始 ** 前是汉字、后跟全角标点时正常开启', () => {
    const html = renderMarkdown('我说**「重点」**就一句')
    expect(html).toContain('<strong>「重点」</strong>')
  })

  it('ASCII 内容行为不变（补丁零外溢）', () => {
    expect(renderMarkdown('a **bold** b')).toContain('<strong>bold</strong>')
  })
})

describe('markdown-renderer · 整段加粗降噪（模型违反排版纪律的前端兜底）', () => {
  const LONG_P1 = '分析做完了，但先跟你说话：小红书笔记接口这会彻底空转，四次搜索原始数据都是 0 条，不是被阈值过滤掉的，是源就不吐。'
  const LONG_P2 = '下面的拆解是「类型画像」不是具体账号实测，没有编造任何账号名和点赞数，这点你必须知道。'

  it('整段被 ** 包裹且足够长时剥掉包裹（大哥截图的加粗形态）', () => {
    const html = renderMarkdown(`**${LONG_P1}**`)
    expect(html).not.toContain('<strong>')
    expect(html).not.toContain('**')
    expect(html).toContain('分析做完了')
  })

  it('多段各自被 ** 包裹时逐段剥掉', () => {
    const html = renderMarkdown(`**${LONG_P1}**\n\n**${LONG_P2}**`)
    expect(html).not.toContain('<strong>')
    expect(html).toContain('分析做完了')
    expect(html).toContain('类型画像')
  })

  it('短的整段加粗块是合法小标题，保留强调', () => {
    const html = renderMarkdown('**关键发现：**\n\n- 要点一\n- 要点二')
    expect(html).toContain('<strong>关键发现：</strong>')
  })

  it('段内局部加粗（行内强调）不受影响', () => {
    const text = `这是正文前缀，**${LONG_P2.slice(0, 12)}**是关键结论，后文继续展开说明整个结论的来龙去脉。`
    const html = renderMarkdown(text)
    expect(html).toContain('<strong>')
  })

  it('块内加粗占比 > 50%（多段碎片式加粗刷屏）时整块拆掉（token 层，2026-11-07）', () => {
    const text =
      '**报告显示**，**数据表现良好**，**用户增长明显**，**转化率也在提升**，整体大盘基本符合我们上周的预期目标。'
    const html = renderMarkdown(text)
    expect(html).not.toContain('<strong>')
  })

  it('块内单个超长加粗片段（>30 字）拆掉，占比不过半时短强调保留', () => {
    const longSpan = '这句话是一个非常长的加粗片段，特意写到三十个字符以上来触发长度阈值判定逻辑。'
    const text = `前缀说明。**${longSpan}**后缀收尾，这一段还补了大量普通文本让加粗占比降到一半以下，而**重点**这个词保留。`
    const html = renderMarkdown(text)
    // 拆加粗 = 保留文本、去掉 <strong> 包裹（不是删字）
    expect(html).not.toContain(`<strong>${longSpan}`)
    expect(html).toContain('<strong>重点</strong>')
  })

  it('列表项里的碎片式加粗刷屏同样被治理', () => {
    const text =
      '- **第一点内容很长**，**第二点内容更长**，**第三点还要长一些**，**第四点也不算短**，**第五点同样如此**。'
    const html = renderMarkdown(text)
    expect(html).not.toContain('<strong>')
  })

  it('列表项加粗、前后有别的块级内容时不动', () => {
    const text = `- **标题A**：说明文字\n- **标题B**：说明文字`
    const html = renderMarkdown(text)
    expect(html).toContain('<strong>标题A</strong>')
  })

  it('段内中等长度加粗（≤30 字、占比不过半）不动', () => {
    const text = `前缀文字。**${LONG_P2.slice(0, 20)}**后缀文字继续，中间再补一段说明，保证普通文本占比过半。`
    const html = renderMarkdown(text)
    expect(html).toContain('<strong>')
  })

  it('段内超长加粗片段（2026-11-07 收紧：不再限于整段包裹）同样被治理', () => {
    const text = `前缀文字。**${LONG_P2}**后缀文字继续。`
    const html = renderMarkdown(text)
    expect(html).not.toContain('<strong>')
  })
})
