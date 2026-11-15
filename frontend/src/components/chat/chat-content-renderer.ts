import { renderMarkdown } from './markdown-renderer'
import { sanitizeContent } from './cell-types'

// ════════════════════════════════════════════════════════════════
// Hidden tag registry — 新增标签只需往这里加一行，其余全自动
// ════════════════════════════════════════════════════════════════
const HIDDEN_TAGS = ['thinking', 'needs_clarification'] as const

// ────────────────────────────────────────────────────────────────
// 工具函数
// ────────────────────────────────────────────────────────────────

/** 正则特殊字符转义 */
function _esc(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

/**
 * 嵌套可选组：为标签名生成一个匹配其任意前缀的正则片段。
 *
 * "thinking" → "t(?:h(?:i(?:n(?:k(?:i(?:n(?:g)?)?)?)?)?)?)?"
 *
 * 这一个正则等价于 8 个独立正则：
 *   /<t$/i, /<th$/i, /<thi$/i, /<thin$/i, /<think$/i,
 *   /<thinki$/i, /<thinkin$/i, /<thinking$/i
 *
 * 原理：每个字符用 (?:x)? 包裹，表示"可选但必须按顺序"，
 * 所以只能匹配 tag 的前缀，不会误匹配 <the、<they 等。
 */
function _prefixRe(tag: string): string {
  if (!tag) return ''
  let p = tag[0]
  for (let i = 1; i < tag.length; i++) p += `(?:${tag[i]}`
  for (let i = 1; i < tag.length; i++) p += ')?'
  return p
}

// ────────────────────────────────────────────────────────────────
// 文本层清洗（markdown 渲染前调用）
// ────────────────────────────────────────────────────────────────

/**
 * 从原始文本中剥离所有 HIDDEN_TAGS 及其内容。
 *
 * 处理四种形态：
 *   1) 完整标签对    <tag>...</tag>
 *   2) 未闭合标签    <tag>...$        （流式截断 / max_tokens 截断）
 *   3) 带属性未闭合  <tag attr...$
 *   4) 部分前缀      <t, <th, <thi …  （逐字截断，用 _prefixRe 一条搞定）
 */
export function stripHiddenTags(text: string): string {
  if (!text) return ''

  // 保护代码块，防止误删代码中的标签
  const cbs: string[] = []
  let s = text.replace(/```[\s\S]*?```/g, (m) => {
    cbs.push(m)
    return `\x00CB${cbs.length - 1}\x00`
  })

  for (const tag of HIDDEN_TAGS) {
    const e = _esc(tag)
    const pfx = _prefixRe(tag)
    // 1) 完整标签对
    s = s.replace(new RegExp(`<${e}>[\\s\\S]*?<\\/${e}>`, 'gi'), '')
    // 2) 未闭合到末尾
    s = s.replace(new RegExp(`<${e}>[\\s\\S]*$`, 'gi'), '')
    // 3) 带属性未闭合
    s = s.replace(new RegExp(`<${e}[^>]*$`, 'gi'), '')
    // 4) 部分前缀（一条正则覆盖所有截断位置）
    s = s.replace(new RegExp(`<${pfx}$`, 'gi'), '')
  }

  // 行尾孤立的 < （不属于任何已知标签的截断残留）
  s = s.replace(/(?<![\/\w])<(?![\w\/])(?=\s*$)/gm, '')

  // 恢复代码块
  s = s.replace(/\x00CB(\d+)\x00/g, (_, i) => cbs[Number(i)])
  return s.trim()
}

// ────────────────────────────────────────────────────────────────
// HTML 层清洗（markdown 渲染后调用，安全网）
// ────────────────────────────────────────────────────────────────

/**
 * 从渲染后的 HTML 中剥离隐藏标签。
 *
 * 需要同时处理两种形式：
 *   - 原始形式：浏览器直接解析的 <tag>...</tag>
 *   - 转义形式：markdown-it 输出的 &lt;tag&gt;...&lt;/tag&gt;
 *
 * 部分前缀用 (?![\w-]) 做词界守卫，防止误杀 &lt;the、&lt;they 等正常文本。
 */
export function stripThinkingFromHtml(html: string): string {
  if (!html) return ''
  let s = html

  for (const tag of HIDDEN_TAGS) {
    const e = _esc(tag)
    const pfx = _prefixRe(tag)

    // ── 原始 HTML 形式 ──
    s = s.replace(new RegExp(`<${e}>[\\s\\S]*?<\\/${e}>`, 'gi'), '')
    s = s.replace(new RegExp(`<${e}>[\\s\\S]*$`, 'gi'), '')
    s = s.replace(new RegExp(`<${e}[^>]*$`, 'gi'), '')
    // 部分前缀：词界守卫，<thi 不会误杀 <the / <they
    s = s.replace(new RegExp(`<${pfx}(?![\\w-])`, 'gi'), '')

    // ── 转义 HTML 形式（markdown-it 输出） ──
    s = s.replace(new RegExp(`&lt;${e}&gt;[\\s\\S]*?&lt;\\/${e}&gt;`, 'gi'), '')
    s = s.replace(new RegExp(`&lt;${e}&gt;[\\s\\S]*$`, 'gi'), '')
    // 部分转义前缀：同样的词界守卫
    s = s.replace(new RegExp(`&lt;${pfx}(?![\\w-])`, 'gi'), '')
    // 孤立闭合标签残留
    s = s.replace(new RegExp(`&lt;\\/${e}&gt;`, 'gi'), '')
    s = s.replace(new RegExp(`<code>&lt;\\/${e}&gt;<\\/code>`, 'g'), '')
  }

  // 行尾孤立的 <
  s = s.replace(/(?<![\/\w])<(?![\w\/])(?=\s*$)/gm, '')
  return s.trim()
}

// ────────────────────────────────────────────────────────────────
// Markdown 渲染管线
// ────────────────────────────────────────────────────────────────

/**
 * 整段内容就是裸 JSON（对象/数组）时，包成 ```json 代码块。
 *
 * 兜底场景：某些后端字段 / 错误分支会把 JSON 原文当作文本下发。若不包裹，
 * markdown-it 会把它当普通段落，配合 breaks:true 渲染成塌成一团的裸 JSON——
 * 用户看到的就是 `{"a":1,"b":2}` 而不是「渲染过的内容」。
 * 只有整串 JSON.parse 成功才判定为裸 JSON，正常自然语言不会误触。
 *
 * 只用于**完整正文**的渲染点（settle / 历史加载）：流式增量渲染时正文尚未
 * 收尾，此刻包围栏会出现「有开无闭」的半个代码块，故不用于逐帧路径。
 */
export function fenceBareJson(text: string): string {
  const t = text.trim()
  if (t.length < 2) return text
  const first = t[0]
  const last = t[t.length - 1]
  const isJsonish = (first === '{' && last === '}') || (first === '[' && last === ']')
  if (!isJsonish) return text
  try {
    JSON.parse(t)
  } catch {
    // 不是合法 JSON（例如以 { 开头的普通文本），保持原样
    return text
  }
  return `\`\`\`json\n${t}\n\`\`\``
}

function normalizeChatMarkdown(text: string): string {
  let cleaned = typeof text === 'string' ? text : String(text || '')
  cleaned = cleaned.replace(/\r\n?/g, '\n')
  cleaned = stripHiddenTags(cleaned)
  cleaned = sanitizeContent(cleaned)
  return fenceBareJson(cleaned.trim())
}

function wrapMarkdownHtml(html: string, extraClass = ''): string {
  if (!html) return ''
  const className = ['rl-md', extraClass].filter(Boolean).join(' ')
  return `<div class="${className}">${html}</div>`
}

export function renderAssistantHtml(text: string): string {
  const normalized = normalizeChatMarkdown(text)
  if (!normalized) return ''
  return wrapMarkdownHtml(renderMarkdown(normalized))
}

// ────────────────────────────────────────────────────────────────
// 用户消息：剥离"配图素材"注入行
// ────────────────────────────────────────────────────────────────

/**
 * 发送时为了告诉 LLM「优先用这些已上传图片」，正文末尾会被追加一行
 *   [本次配图素材] 使用结构化图文卡片能力，asset_ids=["xxx"]…
 *
 * 这行是给模型看的指令，不是用户说的话——气泡里必须剥掉，
 * 图片改由 msg.attachments 以缩略图形式展示。
 * 只剥末尾那一段，正文中间用户自己写的同名文字不受影响。
 */
export function stripAssetHint(text: string): string {
  if (typeof text !== 'string' || !text) return ''
  return text.replace(/\s*\[本次配图素材\][\s\S]*$/, '').trimEnd()
}

/**
 * 同 stripAssetHint：选题参考行（[选题参考] {json}；…）也是发送时追加给模型的指令，
 * 气泡里必须剥掉——参考内容由 chat 顶部的选题参考条展示，不占用户措辞。
 * 必须匹配 `{json}；` 的完整载荷结构才算数：用户正文里顺手打了「选题参考」四个字
 * 也不会被误剥（与 stripAssetHint 同理，只认我们自己的注入格式）。
 * 两条 hint 同时存在时顺序无关（各自只认自己的前缀）。
 */
export function stripTopicHint(text: string): string {
  if (typeof text !== 'string' || !text) return ''
  return text.replace(/\s*\[选题参考\]\s*\{[\s\S]*?\}\s*[；;][\s\S]*$/, '').trimEnd()
}

/** 气泡展示用的正文：把两条发送期注入的指令行都剥掉 */
export function stripSendHints(text: string): string {
  return stripTopicHint(stripAssetHint(text))
}

export interface TopicReferencePayload {
  title: string
  summary: string
  url: string
  platform: string
  author: string
  likes: number
  comments: number
  collects: number
  shares: number
  source_keyword: string
}

/**
 * 从用户消息正文里反解 [选题参考] {json} 指令行。
 * 重试（重新生成）时参考条已被摘除，需要靠它把参考还原回输入态，
 * 否则重发的消息会丢掉热点上下文。payload 是扁面 JSON，第一个 } 即结束。
 */
export function extractTopicReference(text: string): TopicReferencePayload | null {
  if (typeof text !== 'string' || !text.includes('[选题参考]')) return null
  const m = text.match(/\[选题参考\]\s*(\{[\s\S]*?\})\s*[；;]/)
  if (!m) return null
  try {
    const data = JSON.parse(m[1])
    if (data && typeof data.title === 'string' && data.title) {
      return data as TopicReferencePayload
    }
  } catch {}
  return null
}

export function renderUserHtml(text: string): string {
  const normalized = normalizeChatMarkdown(stripSendHints(text))
  if (!normalized) return ''
  return wrapMarkdownHtml(renderMarkdown(normalized), 'rl-md-user')
}

export function renderReasoningHtml(text: string): string {
  const normalized = normalizeChatMarkdown(text)
  if (!normalized) return ''
  return wrapMarkdownHtml(renderMarkdown(normalized), 'rl-md-reasoning')
}

export function wrapStreamHtml(html: string): string {
  return wrapMarkdownHtml(stripThinkingFromHtml(html))
}