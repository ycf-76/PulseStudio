import { describe, it, expect } from 'vitest'
import { renderAssistantHtml, renderUserHtml, fenceBareJson, stripTopicHint, stripSendHints, extractTopicReference } from './chat-content-renderer'
import { parseHttpErrorMessage } from './cell-types'
import { formatAgentOutput } from './agent-output-formatter'

/**
 * 回归测试：用户反馈「前端回复文本中部分没有做 markdown 渲染，直接返回 json 格式」。
 *
 * 根因是两类：
 *  1) HTTP 失败分支 `throw new Error(await res.text())` 把 FastAPI 的
 *     `{"detail":...}` JSON 原文拼进了 assistantMsg.content；
 *  2) agent-output-formatter 的兜底分支直接 `JSON.stringify` 把 JSON 写进正文。
 * 叠加渲染器覆写 fence/code_block 不输出 `<pre>`，多行 JSON 会塌成裸文本。
 *
 * 防线分三层，本文件逐层锁定：
 *  A. parseHttpErrorMessage —— 错误体解析，绝不透出 JSON 原文
 *  B. fenceBareJson / normalizeChatMarkdown —— 整段裸 JSON 包成 json 代码块
 *  C. formatAgentOutput —— 兜底分支不再 dump 裸 JSON
 */

describe('A. parseHttpErrorMessage：错误体不透出裸 JSON', () => {
  it('提取 FastAPI 的 detail 字符串', () => {
    expect(parseHttpErrorMessage(500, '{"detail":"模型服务暂时不可用"}')).toBe('模型服务暂时不可用')
  })

  it('detail 为数组时取 msg 拼接', () => {
    expect(parseHttpErrorMessage(422, '{"detail":[{"msg":"字段缺失"},{"msg":"格式错误"}]}'))
      .toBe('字段缺失; 格式错误')
  })

  it('detail 为非字符串对象时取嵌套 message，取不到则按状态码兜底（不出 [object Object]）', () => {
    expect(parseHttpErrorMessage(400, '{"detail":{"message":"参数不合法"}}')).toBe('参数不合法')
    const out = parseHttpErrorMessage(400, '{"detail":{"code":"E1"}}')
    expect(out).toBe('请求失败 (400)')
    expect(out).not.toContain('[object Object]')
  })

  it('JSON 无 detail/message/msg 时不透出原文，按状态码兜底', () => {
    const out = parseHttpErrorMessage(500, '{"foo":"bar","trace":"xxxx"}')
    expect(out).toBe('服务器内部错误，请稍后重试')
    expect(out).not.toContain('foo')
    expect(out).not.toContain('trace')
  })

  it('非 JSON 纯文本响应体压缩空白后透出', () => {
    expect(parseHttpErrorMessage(502, 'Bad\nGateway')).toBe('Bad Gateway')
  })

  it('HTML 错误页不透出', () => {
    expect(parseHttpErrorMessage(500, '<html><body>502 Bad Gateway</body></html>'))
      .toBe('服务器内部错误，请稍后重试')
  })

  it('空体按状态码给可读兜底', () => {
    expect(parseHttpErrorMessage(404, '')).toBe('请求的资源已过期或不存在，请重新发起操作')
    expect(parseHttpErrorMessage(401, '')).toBe('登录已过期，请刷新页面后重试')
    expect(parseHttpErrorMessage(429, '')).toBe('请求过于频繁，请稍后再试')
    expect(parseHttpErrorMessage(418, '')).toBe('请求失败 (418)')
  })

  it('404 支持入口自定义文案（确认/澄清状态过期）', () => {
    expect(parseHttpErrorMessage(404, '', { notFoundMessage: '澄清状态已过期，请重新发起操作' }))
      .toBe('澄清状态已过期，请重新发起操作')
  })
})

describe('B. fenceBareJson：整段裸 JSON 渲染成数据卡', () => {
  it('JSON 对象整体渲染成 JSON 数据卡（Oct-18：不再走暗色代码块）', () => {
    const html = renderAssistantHtml('{"detail":"boom","code":42}')
    expect(html).toContain('rl-json-card')
    // 不允许以普通段落形式出现在正文里
    expect(html).not.toMatch(/<p>\s*\{/)
  })

  it('JSON 数组同样渲染成数据卡', () => {
    expect(renderAssistantHtml('[{"a":1},{"b":2}]')).toContain('rl-json-card')
  })

  it('用户消息也走同一兜底', () => {
    expect(renderUserHtml('{"x":1}')).toContain('rl-json-card')
  })

  it('自然语言不受影响', () => {
    const html = renderAssistantHtml('今天天气不错，我们开始创作吧')
    expect(html).not.toContain('rl-code-block')
    expect(html).toContain('今天天气不错')
  })

  it('以 { 开头但不是合法 JSON 的文本保持原样', () => {
    expect(fenceBareJson('{ 未闭合的括号')).toBe('{ 未闭合的括号')
    expect(fenceBareJson('{普通文本开头}')).toBe('{普通文本开头}')
  })

  it('fenceBareJson 幂等：已包裹不重复包裹', () => {
    const once = fenceBareJson('{"a":1}')
    expect(fenceBareJson(once)).toBe(once)
    expect((once.match(/```/g) || []).length).toBe(2)
  })

  it('短文本/空串安全返回', () => {
    expect(fenceBareJson('')).toBe('')
    expect(fenceBareJson('a')).toBe('a')
  })
})

describe('C. formatAgentOutput：兜底分支不 dump 裸 JSON', () => {
  it('未知对象字段包成 json 代码块', () => {
    const md = formatAgentOutput({ weird_key: { a: 1, b: [1, 2] } })
    expect(md).toContain('```json')
    expect(md).not.toContain('**weird_key**: {')
  })

  it('数组元素无可识别字段时退化为 key=value', () => {
    const md = formatAgentOutput({ recommendations: [{ foo: 'bar', baz: 2 }] })
    expect(md).toContain('foo=bar，baz=2')
    expect(md).not.toContain('{"foo"')
  })

  it('嵌套对象无可识别字段时同样退化', () => {
    const md = formatAgentOutput({ key_findings: [{ alpha: 'x', beta: 'y' }] })
    expect(md).toContain('alpha=x，beta=y')
    expect(md).not.toContain('{"alpha"')
  })

  it('已知字段的可读路径不受影响', () => {
    const md = formatAgentOutput({ summary: '已完成 3 张卡片生成' })
    expect(md).toBe('已完成 3 张卡片生成')
    expect(md).not.toContain('```')
  })
})

/**
 * D. 选题参考（选题池「发起工作流」链路）：
 *    TopicPool 写入 sessionStorage.pending_topic_reference → ChatView 挂载消费 →
 *    发送时注入 [选题参考] {json} 指令行 → 气泡必须剥掉 → 重试时反解还原。
 *    历史上这条链路只写不读（参考被静默丢弃），本组锁定读写两侧契约。
 */
const SAMPLE_PAYLOAD = {
  title: '秋冬干皮救星！5 步急救护肤法',
  summary: '换季烂脸期如何急救，含产品清单',
  url: 'https://example.com/note/123',
  platform: '小红书',
  author: '护肤小课堂',
  likes: 1280,
  comments: 96,
  collects: 452,
  shares: 33,
  source_keyword: '秋冬护肤',
}

function composeTopicHint(payload: Record<string, unknown>): string {
  return `[选题参考] ${JSON.stringify(payload)}；请围绕该热点选题进行创作，优先使用参考中的数据与链接。`
}

describe('D. 选题参考注入行的剥离与反解', () => {
  it('stripTopicHint 剥掉末尾选题参考行，保留用户原话', () => {
    const text = `帮我写个脚本\n\n${composeTopicHint(SAMPLE_PAYLOAD)}`
    expect(stripTopicHint(text)).toBe('帮我写个脚本')
  })

  it('stripTopicHint 不误伤用户正文里提到的「选题参考」四个字', () => {
    const text = '这个选题参考有点旧了，换个新的'
    expect(stripTopicHint(text)).toBe(text)
  })

  it('stripSendHints 同时剥掉配图素材行与选题参考行（两条都在时）', () => {
    const text = '写脚本\n\n'
      + '[本次配图素材] 使用结构化图文卡片能力，asset_ids=["a1","a2"]；这些是我已上传的图片，请优先使用，不要重新生成。'
      + '\n\n' + composeTopicHint(SAMPLE_PAYLOAD)
    expect(stripSendHints(text)).toBe('写脚本')
  })

  it('stripSendHints 只有选题参考行时也能剥掉', () => {
    const text = `写脚本\n\n${composeTopicHint(SAMPLE_PAYLOAD)}`
    expect(stripSendHints(text)).toBe('写脚本')
  })

  it('renderUserHtml 渲染的用户气泡不含选题参考 JSON', () => {
    const html = renderUserHtml(`写脚本\n\n${composeTopicHint(SAMPLE_PAYLOAD)}`)
    expect(html).not.toContain('选题参考')
    expect(html).not.toContain('source_keyword')
    expect(html).toContain('写脚本')
  })

  it('extractTopicReference 从注入消息反解出完整载荷（重试还原用）', () => {
    const text = `写脚本\n\n${composeTopicHint(SAMPLE_PAYLOAD)}`
    expect(extractTopicReference(text)).toEqual(SAMPLE_PAYLOAD)
  })

  it('extractTopicReference 对无参考/结构不完整/JSON 损坏返回 null', () => {
    expect(extractTopicReference('普通消息')).toBeNull()
    expect(extractTopicReference('[选题参考] 没有 JSON 结构；')).toBeNull()
    expect(extractTopicReference('[选题参考] {坏 JSON；')).toBeNull()
    expect(extractTopicReference('[选题参考] {"no_title":1}；')).toBeNull()
  })

  it('剥离与反解互为正交：剥过的正文不再被反解命中', () => {
    const text = `写脚本\n\n${composeTopicHint(SAMPLE_PAYLOAD)}`
    expect(extractTopicReference(stripSendHints(text))).toBeNull()
  })
})
