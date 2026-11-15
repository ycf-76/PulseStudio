import MarkdownIt from 'markdown-it'
import markdownItCjkFriendly from 'markdown-it-cjk-friendly'
import hljs from 'highlight.js/lib/core'
import python from 'highlight.js/lib/languages/python'
import json from 'highlight.js/lib/languages/json'
import yaml from 'highlight.js/lib/languages/yaml'
import bash from 'highlight.js/lib/languages/bash'
import xml from 'highlight.js/lib/languages/xml'
import css from 'highlight.js/lib/languages/css'
import javascript from 'highlight.js/lib/languages/javascript'
import typescript from 'highlight.js/lib/languages/typescript'
import sql from 'highlight.js/lib/languages/sql'
import markdown from 'highlight.js/lib/languages/markdown'

hljs.registerLanguage('python', python)
hljs.registerLanguage('py', python)
hljs.registerLanguage('json', json)
hljs.registerLanguage('yaml', yaml)
hljs.registerLanguage('yml', yaml)
hljs.registerLanguage('bash', bash)
hljs.registerLanguage('sh', bash)
hljs.registerLanguage('shell', bash)
hljs.registerLanguage('xml', xml)
hljs.registerLanguage('html', xml)
hljs.registerLanguage('svg', xml)
hljs.registerLanguage('css', css)
hljs.registerLanguage('javascript', javascript)
hljs.registerLanguage('js', javascript)
hljs.registerLanguage('typescript', typescript)
hljs.registerLanguage('ts', typescript)
hljs.registerLanguage('sql', sql)
hljs.registerLanguage('markdown', markdown)
hljs.registerLanguage('md', markdown)

const COPY_ICONS_SVG = '<svg class="rl-copy-icon-default" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg><svg class="rl-copy-icon-check" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>'

const COPY_BTN_SVG = `<button class="rl-code-copy-btn" title="复制代码">${COPY_ICONS_SVG}</button>`

const md = new MarkdownIt({
  html: false,
  linkify: true,
  typographer: false,
  breaks: true,
})

// CJK 加粗补丁：CommonMark flanking 规则对「闭合 ** 前是全角标点、后是汉字」
// 不闭合（如 **【我要打控卫】**这条 → 星号裸奔）。该补丁只改 CJK 邻接行为，
// 全部 CommonMark 测试用例输出不变，ASCII 内容零影响。
md.use(markdownItCjkFriendly)

// ════════════════════════════════════════════════════════════════
// 加粗滥用治理（2026-11-07 代码级约束）：提示词约束「别整段加粗」在长任务里
// 会被 LLM 忘掉（治标不治本），这里在 token 流层做确定性归一化：
//   1. 单个加粗片段 > 30 字（标题式长加粗）→ 拆掉 **，只留文本
//   2. 一个块级元素（段落/列表项/表格单元格）加粗字符占比 > 50% → 整块拆掉
// 短促真强调（≤30 字、块内占比低）不受影响；标题本身的样式来自 h1-h4 规则，
// 块内加粗被拆后标题观感不变。core rule 在缓存层之下、流式/settle 共用。
// ════════════════════════════════════════════════════════════════

const BOLD_SPAN_MAX = 30
const BOLD_RATIO_MAX = 0.5
/** 占比判定只对足够长的块生效：短整块加粗（如 **关键发现：**）是合法小标题，
 *  与 stripWholeBlockBold 的 WHOLE_BLOCK_BOLD_MIN_CHARS 语义对齐（2026-11-02） */
const BOLD_RATIO_MIN_BLOCK = 20

/** 拆掉 children 里指定下标的 strong_open/strong_close（保留内部内容） */
function stripBoldSpans(children: any[], opens: Set<number>, closes: Set<number>): void {
  if (!opens.size) return
  const out: any[] = []
  for (let i = 0; i < children.length; i++) {
    if (!opens.has(i) && !closes.has(i)) out.push(children[i]!)
  }
  children.length = 0
  for (const t of out) children.push(t)
}

function normalizeBoldInInline(tok: any): void {
  const children: any[] = tok.children
  if (!Array.isArray(children) || children.length < 2) return
  const spans: Array<{ open: number; close: number; len: number }> = []
  let open = -1
  let totalText = 0
  let boldText = 0
  for (let i = 0; i < children.length; i++) {
    const t = children[i]!
    if (t.type === 'strong_open') {
      open = i
    } else if (t.type === 'strong_close') {
      if (open >= 0) {
        let len = 0
        for (let j = open + 1; j < i; j++) len += (children[j]!.content || '').length
        spans.push({ open, close: i, len })
        boldText += len
        open = -1
      }
    } else {
      totalText += (t.content || '').length
    }
  }
  if (!spans.length) return
  const ratio = totalText >= BOLD_RATIO_MIN_BLOCK ? boldText / totalText : 0
  const targets = ratio > BOLD_RATIO_MAX ? spans : spans.filter((s) => s.len > BOLD_SPAN_MAX)
  if (!targets.length) return
  const opens = new Set<number>()
  const closes = new Set<number>()
  for (const s of targets) {
    opens.add(s.open)
    closes.add(s.close)
  }
  stripBoldSpans(children, opens, closes)
}

/** 递归遍历块级 token：inline 与 td/th（children 为行内级 token）都归一化 */
function normalizeBoldTokens(tokens: any[]): void {
  for (const tok of tokens) {
    if (!tok || typeof tok !== 'object') continue
    if (tok.type === 'inline' || tok.type === 'td' || tok.type === 'th') {
      normalizeBoldInInline(tok)
    }
    if (Array.isArray(tok.children)) normalizeBoldTokens(tok.children)
  }
}

md.core.ruler.push('normalize_bold_overuse', (state) => {
  normalizeBoldTokens(state.tokens as any[])
})

export function escapeHtml(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')
}

// ════════════════════════════════════════════════════════════════
// JSON 数据卡：可解析的 JSON 不再渲染成暗色代码块，改为结构化树卡。
// 解析失败（典型：流式中途 JSON 未收尾）返回 null，走原代码块路径。
// ════════════════════════════════════════════════════════════════

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v)
}

/** 原始值渲染：字符串截断 200 字符，null/布尔/数字带类型色 */
function jsonValHtml(v: unknown): string {
  if (v === null) return '<span class="rl-json-val rl-json-null">null</span>'
  if (typeof v === 'string') {
    const s = v.length > 200 ? v.slice(0, 200) + '…' : v
    return `<span class="rl-json-val rl-json-str">${escapeHtml(s)}</span>`
  }
  if (typeof v === 'number' || typeof v === 'boolean') {
    return `<span class="rl-json-val rl-json-num">${escapeHtml(String(v))}</span>`
  }
  return ''
}

/**
 * 数组元素的紧凑行：字段全为原始值且 ≤6 个的对象，渲染成
 * 「主文案 · key: value · …」单行（主文案取 名称/名字/标题/name/title 字段，
 * 没有则取第一个字符串字段）。不满足条件返回 null，调用方退回树节点。
 */
function compactItemHtml(item: unknown): string | null {
  if (!isPlainObject(item)) return null
  const entries = Object.entries(item)
  if (entries.length === 0 || entries.length > 6) return null
  if (entries.some(([, v]) => v !== null && typeof v === 'object')) return null
  const namedIdx = entries.findIndex(([k, v]) => typeof v === 'string' && /名称|名字|标题|文件名|name|title|filename/i.test(k))
  const pIdx = namedIdx >= 0 ? namedIdx : entries.findIndex(([, v]) => typeof v === 'string')
  let primary = ''
  const rest: string[] = []
  entries.forEach(([k, v], i) => {
    const text = v === null || v === undefined ? '' : typeof v === 'string' ? v : String(v)
    if (!text) return
    const clipped = text.length > 80 ? text.slice(0, 80) + '…' : text
    if (i === pIdx) primary = clipped
    else rest.push(`${escapeHtml(k)}：${escapeHtml(clipped)}`)
  })
  const primaryHtml = primary ? `<span class="rl-json-item-primary">${escapeHtml(primary)}</span>` : ''
  const restHtml = rest.length
    ? `<span class="rl-json-item-meta">${rest.map((r) => `<span class="rl-json-sep">·</span>${r}`).join(' ')}</span>`
    : ''
  if (!primaryHtml && !restHtml) return null
  return `<div class="rl-json-leaf">${primaryHtml}${restHtml}</div>`
}

function jsonChildrenHtml(value: Record<string, unknown> | unknown[], depth: number): string {
  const entries: [string, unknown][] = Array.isArray(value)
    ? value.map((v, i) => [String(i), v])
    : Object.entries(value)
  if (entries.length === 0) {
    return '<div class="rl-json-empty">（空）</div>'
  }
  let html = ''
  for (const [k, v] of entries) {
    // 数组元素优先走紧凑行（目录清单、搜索结果这类场景最常见）
    if (Array.isArray(value) && depth >= 0) {
      const compact = compactItemHtml(v)
      if (compact) {
        html += compact
        continue
      }
    }
    html += jsonNodeHtml(k, v, depth)
  }
  return html
}

function jsonNodeHtml(key: string, value: unknown, depth: number): string {
  if (Array.isArray(value) || isPlainObject(value)) {
    const entries: [string, unknown][] = Array.isArray(value)
      ? value.map((v, i) => [String(i), v])
      : Object.entries(value)
    const badge = Array.isArray(value) ? `${entries.length} 项` : `${entries.length} 个字段`
    const collapsed = depth >= 2 ? ' rl-json-collapsed' : ''
    const clamped = entries.length > 12
    const childrenClass = clamped ? ' rl-json-children rl-json-clamped' : ' rl-json-children'
    const expandBtn = clamped
      ? `<button class="rl-json-expand" type="button">展开全部 ${entries.length} 项</button>`
      : ''
    return (
      `<div class="rl-json-node${collapsed}">`
      + `<div class="rl-json-row"><span class="rl-json-caret"></span>`
      + `<span class="rl-json-key">${escapeHtml(key)}</span>`
      + `<span class="rl-json-badge">${badge}</span></div>`
      + `<div class="${childrenClass}">${jsonChildrenHtml(value, depth + 1)}</div>`
      + `${expandBtn}</div>`
    )
  }
  const val = jsonValHtml(value)
  if (!val) return ''
  return `<div class="rl-json-leaf"><span class="rl-json-leaf-key">${escapeHtml(key)}：</span>${val}</div>`
}

/** 生成 JSON 数据卡；非对象/数组根或解析失败返回 null（退回代码块） */
function renderJsonCard(content: string): string | null {
  const t = content.trim()
  if (!t.startsWith('{') && !t.startsWith('[')) return null
  let data: unknown
  try {
    data = JSON.parse(t)
  } catch {
    return null
  }
  if (!Array.isArray(data) && !isPlainObject(data)) return null
  const tree = jsonChildrenHtml(data as Record<string, unknown> | unknown[], 0)
  const raw = JSON.stringify(data)
  return `<div class="rl-json-card">
  <div class="rl-json-head">
    <span class="rl-json-title">JSON 数据</span>
    <button class="rl-json-copy-btn" type="button" title="复制 JSON">${COPY_ICONS_SVG}</button>
  </div>
  <div class="rl-json-body">${tree}</div>
  <code class="rl-json-raw" hidden>${escapeHtml(raw)}</code>
</div>`
}

function renderCodeBlock(lang: string, content: string): string {
  const safeLang = escapeHtml(lang || 'text')
  const source = content.replace(/\n$/, '')

  // json 围栏优先渲染成数据卡（解析失败/流式未收尾时退回代码块）
  if (/^jsonc?$/.test(lang.trim().toLowerCase())) {
    const card = renderJsonCard(content)
    if (card) return card
  }

  const lineCount = source ? source.split('\n').length : 1
  const gutter = Array.from({ length: lineCount }, (_, i) => i + 1).join('\n')

  let highlighted = ''
  if (_streamingMode) {
    // 流式期间免高亮（对齐 Claude Code 懒高亮）：未闭合围栏每帧重跑 hljs
    // 是纯浪费（highlightAuto 尤其贵），流结束后 settle 全文重渲染一次性上色。
    highlighted = escapeHtml(content)
  } else if (lang && hljs.getLanguage(lang)) {
    try {
      highlighted = hljs.highlight(content, { language: lang, ignoreIllegals: true }).value
    } catch {
      highlighted = escapeHtml(content)
    }
  } else {
    highlighted = hljs.highlightAuto(content).value
  }

  return `<div class="rl-code-block">
  <div class="rl-code-header">
    <span class="rl-code-lang">${safeLang}</span>
    <span class="rl-code-actions">${COPY_BTN_SVG}</span>
  </div>
  <div class="rl-code-body">
    <span class="rl-code-gutter" aria-hidden="true">${gutter}</span>
    <code class="hljs">${highlighted}</code>
  </div>
</div>`
}

md.inline.ruler.push('highlight_mark', (state, silent) => {
  const max = state.posMax
  const start = state.pos
  if (start + 3 > max) return false
  if (state.src.charCodeAt(start) !== 0x3D /* = */) return false
  if (state.src.charCodeAt(start + 1) !== 0x3D /* = */) return false
  if (silent) return false
  let pos = start + 2
  let found = -1
  while (pos < max - 1) {
    if (state.src.charCodeAt(pos) === 0x3D && state.src.charCodeAt(pos + 1) === 0x3D) {
      found = pos
      break
    }
    pos++
  }
  if (found === -1) return false
  const content = state.src.slice(start + 2, found)
  if (!content.trim()) return false
  const tokenOpen = state.push('highlight_mark_open', 'mark', 1)
  tokenOpen.markup = '=='
  const tokenText = state.push('text', '', 0)
  tokenText.content = content
  const tokenClose = state.push('highlight_mark_close', 'mark', -1)
  tokenClose.markup = '=='
  state.pos = found + 2
  return true
})

md.renderer.rules.code_inline = function (tokens, idx) {
  const content = escapeHtml(tokens[idx].content)
  return `<code class="rl-inline-code" title="点击复制">${content}</code>`
}

function normalizePath(href: string): string {
  return href.replace(/\\/g, '/')
}

md.renderer.rules.link_open = function (tokens, idx) {
  let href = String(tokens[idx].attrGet('href') || '')
  if (/^[A-Za-z]:\\|^\\\\|^~\/|^\.\.?\//.test(href)) {
    href = normalizePath(href)
    if (!href.startsWith('file://') && /^[A-Za-z]:\//.test(href)) {
      href = 'file:///' + href
    }
  }
  const safeHref = href.replace(/"/g, '&quot;')
  return `<span class="rl-link-wrap"><a class="rl-link-text" href="${safeHref}" target="_blank" rel="noopener noreferrer">`
}

md.renderer.rules.link_close = function () {
  return `</a></span>`
}

md.renderer.rules.fence = function (tokens, idx) {
  const token = tokens[idx]
  const info = token.info ? token.info.trim() : ''
  const lang = info.split(/\s+/g)[0] || ''
  return renderCodeBlock(lang, token.content)
}

md.renderer.rules.code_block = function (tokens, idx) {
  return renderCodeBlock('', tokens[idx].content)
}

const _mdCache = new Map<string, string>()

// 流式免高亮开关（渲染层 L4）：流式路径用 renderMarkdownStreaming 包裹，
// settle 定稿走普通 renderMarkdown（带 hljs）。同步单线程，开关安全。
let _streamingMode = false

/** 流式渲染：markdown 解析正常，但跳过 hljs 高亮（settle 时一次性上色）。
 *  绕过缓存：命中旧的高亮版会让开关失效，而免高亮结果入缓存又会
 *  污染 settle 定稿渲染——进出都不走缓存。 */
export function renderMarkdownStreaming(text: string): string {
  if (!text) return ''
  _streamingMode = true
  try {
    _mdCache.delete(text)
    const result = renderMarkdown(text)
    _mdCache.delete(text)
    return result
  } finally {
    _streamingMode = false
  }
}

// ════════════════════════════════════════════════════════════════
// 尾部纯文本快速路径（对齐 Claude Code hasMarkdownSyntax）
// ════════════════════════════════════════════════════════════════

/**
 * markdown 结构语法嗅探。判定方向刻意保守：**宁可漏报走慢路径**
 * （markdown-it 兜底，安全），不可误报吞掉语法（用户看到裸星号/井号）。
 * 覆盖：行内代码/粗斜体/删除线/高亮、链接与图片、标题、tag、表格、
 * 引用、HTML 标签、有序/无序列表、自动链接、分隔线。
 */
const MD_SYNTAX_RE =
  /[`*_~=[\]<>#|]|![\[]|\d{1,9}[.)][ \t]|(^|\n)[ \t]*[-*+][ \t]|https?:\/\/|(^|\n)[ \t]*[-=_*]{3,}[ \t]*($|\n)/

/**
 * 尾部纯文本快速路径：不含 markdown 语法时跳过 markdown-it 整条管线
 * （tokenize + rules + cache 查写），直接转义输出。
 * 渲染语义与 markdown-it(breaks:true) 的纯文本段落一致；这是流式瞬态，
 * settle 时会全文严格重渲染，微小输出差异无碍。
 *
 * @returns 含 markdown 语法时返回 null（调用方退回 renderMarkdownStreaming）
 */
export function renderPlainTail(text: string): string | null {
  if (!text) return null
  if (MD_SYNTAX_RE.test(text)) return null
  return '<p>' + escapeHtml(text).replace(/\n/g, '<br/>') + '</p>'
}

const CARD_HTML_OPEN = '<!--card-html-->'
const CARD_HTML_CLOSE = '<!--/card-html-->'

/**
 * markdown 表格修补：LLM 输出的表格常紧贴上文段落（缺空行），
 * markdown-it 的 table 规则要求表格块前有空行，缺了整段渲染成裸文本。
 * 识别「|...| 行 + 下一行 |---| 分隔行」的表格头，前面补空行；
 * 表格结束行的下一行非空也补（结束边界，防止下一段被吞进表格）。
 */
function ensureTableBlankLines(mdText: string): string {
  const lines = mdText.split('\n')
  const rowRe = /^\s*\|.*\|\s*$/
  const sepRe = /^\s*\|[\s:|-]+\|\s*$/
  const out: string[] = []
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]
    const next = lines[i + 1] || ''
    const prev = out.length > 0 ? out[out.length - 1] : ''
    const isRow = rowRe.test(line)
    // 表格头前补空行
    if (isRow && sepRe.test(next) && prev.trim() !== '' && !rowRe.test(prev)) {
      out.push('')
    }
    out.push(line)
    // 表格末行后补空行
    if (isRow && !sepRe.test(line) && next.trim() !== '' && !rowRe.test(next)) {
      out.push('')
    }
  }
  return out.join('\n')
}

export function renderMarkdown(text: string): string {
  if (!text) return ''
  const cached = _mdCache.get(text)
  if (cached !== undefined) return cached

  // 整段加粗降噪（缓存键仍是原文，变换在命中之后做，行为确定可复现）
  text = stripWholeBlockBold(text)

  let preprocessed = text
  const cardHtmlBlocks: string[] = []
  preprocessed = preprocessed.replace(
    new RegExp(escapeRegex(CARD_HTML_OPEN) + '([\\s\\S]*?)' + escapeRegex(CARD_HTML_CLOSE), 'g'),
    (_, html) => {
      const idx = cardHtmlBlocks.length
      cardHtmlBlocks.push(html)
      return `\x00CARD_HTML_${idx}\x00`
    }
  )

  const stripped = ensureTableBlankLines(stripMarkdownFence(preprocessed))
  let result = md.render(stripped)

  if (cardHtmlBlocks.length > 0) {
    for (let i = 0; i < cardHtmlBlocks.length; i++) {
      const placeholder = `\x00CARD_HTML_${i}\x00`
      const escapedPlaceholder = placeholder.replace(/\x00/g, '&#x00;')
      const sandboxedHtml = `<div class="rl-card-html-wrap"><iframe sandbox="allow-same-origin" srcdoc="${escapeAttr(cardHtmlBlocks[i])}" class="rl-card-iframe" loading="lazy"></iframe></div>`
      result = result.replace(escapedPlaceholder, sandboxedHtml)
      result = result.replace(placeholder, sandboxedHtml)
    }
  }

  result = degradeWideTables(result)
  if (_mdCache.size > 64) {
    const firstKey = _mdCache.keys().next().value as string
    if (firstKey !== undefined) _mdCache.delete(firstKey)
  }
  _mdCache.set(text, result)
  return result
}

function escapeRegex(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

function escapeAttr(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

function degradeWideTables(html: string): string {
  return html.replace(/<table>([\s\S]*?)<\/table>/g, (match) => {
    const rows = match.match(/<tr[\s\S]*?<\/tr>/g)
    if (!rows || rows.length < 2) return match
    const headerRow = rows[0]
    const headers = (headerRow.match(/<th[\s\S]*?>([\s\S]*?)<\/th>/g) || []).map(
      (h) => h.replace(/<th[^>]*>/, '').replace(/<\/th>/, '').trim()
    )
    if (headers.length !== 2) return match
    const bodyRows = rows.slice(1)
    let hasLongContent = false
    for (const row of bodyRows) {
      const cells = (row.match(/<td[\s\S]*?>([\s\S]*?)<\/td>/g) || []).map(
        (c) => c.replace(/<td[^>]*>/, '').replace(/<\/td>/, '').trim()
      )
      for (const cell of cells) {
        if (cell.length > 60 || /\/|\\|https?:\/\//.test(cell)) {
          hasLongContent = true
          break
        }
      }
      if (hasLongContent) break
    }
    if (!hasLongContent) return match
    let kvHtml = '<dl class="rl-kv-list">'
    for (const row of bodyRows) {
      const cells = (row.match(/<td[\s\S]*?>([\s\S]*?)<\/td>/g) || []).map(
        (c) => c.replace(/<td[^>]*>/, '').replace(/<\/td>/, '').trim()
      )
      if (cells.length >= 2) {
        kvHtml += `<dt>${cells[0]}</dt><dd>${cells[1]}</dd>`
      }
    }
    kvHtml += '</dl>'
    return kvHtml
  })
}

function stripMarkdownFence(text: string): string {
  return text.replace(/^```(?:md|markdown)\s*\n([\s\S]*?)```$/m, '$1')
}

// ════════════════════════════════════════════════════════════════
// 整段加粗降噪（2026-11-02）
// reactor 模型偶发把整段话包进一对 **…**（违反 base.md「禁止整句/整段
// 加粗」的排版纪律），渲染出来就是「最终输出整段粗体」（大哥截图实锤）。
// 纯前端兜底：空行分隔的块自始至终被一对 ** 包住、内部无其他 **、
// 且足够长（短块是合法的加粗小标题，如「**关键发现：**」，不碰）时
// 剥掉包裹还原为普通正文。行内强调、列表项、表格零影响。
// ════════════════════════════════════════════════════════════════

/** 整段加粗判定的最小块长（含 ** 包裹）：低于此值的加粗块视为合法小标题 */
const WHOLE_BLOCK_BOLD_MIN_CHARS = 28

function stripWholeBlockBold(text: string): string {
  if (!text.includes('**')) return text
  return text
    .split(/\n{2,}/)
    .map((block) => {
      const trimmed = block.trim()
      if (trimmed.length < WHOLE_BLOCK_BOLD_MIN_CHARS + 4) return block
      const m = trimmed.match(/^\*\*([\s\S]+?)\*\*$/)
      if (m && !m[1].includes('**')) return m[1].trim()
      return block
    })
    .join('\n\n')
}

export function renderThinkingText(text: string): string {
  if (!text) return ''
  const escaped = escapeHtml(text)
  return escaped.replace(/\n/g, '<br/>')
}