/**
 * stable-blocks — 顶层块安全切分器（渲染层 L4）
 *
 * 对齐 Codex streaming/render.rs 的 stable_source_len 语义：流式 markdown 的
 * 增量渲染单元是「顶层块」而不是 token / 行。已完全闭合的顶层块渲染一次进
 * 缓存永不重渲；正在生长的内容留在可变尾部。
 *
 * 安全切分点 = 空行边界，且满足：
 * - 切分点不在代码围栏内（围栏内的空行不是块边界）
 * - 空行后的第一个非空行不是缩进行（可能是 loose list / 引用内容的延续，
 *   它与前面的块同属一个顶层块——此时该候选作废，组继续延伸）
 *
 * 设计约束：
 * - 保守优先：宁可少切（该组留在尾部多渲染几次）也不切错（切错会导致
 *   「分块渲染拼接 != 整体渲染」）。对正确性无影响的粗粒度（如缩进代码块
 *   与前块并组）可接受。
 * - 状态机单调前进（scanPos / blockScanLen 只增不减），同一源前缀上重复
 *   调用安全；shownText 是 raw 的前缀切片，前缀内容不变，偏移永久有效。
 * - 与框架无关（纯 TS），可单测。
 */

export interface BlockScanState {
  /** 已切分到的源偏移：块缓存覆盖 [0, blockScanLen)，一旦推进永不回退 */
  blockScanLen: number
  /** 已扫描到的源偏移（围栏状态只对 scanPos 之后的新内容翻转一次） */
  scanPos: number
  /** 代码围栏状态（跨调用保持） */
  inFence: boolean
  /** 已完成块的 HTML 缓存（按切分顺序） */
  blocks: string[]
}

export function createBlockScanState(): BlockScanState {
  return { blockScanLen: 0, scanPos: 0, inFence: false, blocks: [] }
}

/**
 * 给块 HTML 的根元素注入动画 class（默认 rl-enter）——块级淡入动画的标记。
 *
 * 设计要点：
 * - 只动根开标签，块内其余 HTML 原样保留（join 拼接语义不受影响）；
 * - 根元素已有 class（如 json 卡片 <div class="rl-json-card">）时追加而非
 *   覆盖——HTML 双 class 属性会让浏览器丢弃第二个，必须合并进第一个；
 * - 无法识别根开标签（空串/注释开头等）时原样返回，宁可不播动画也不破坏 HTML。
 */
export function markBlockEnter(html: string, cls = 'rl-enter'): string {
  const m = html.match(/^(\s*<[a-zA-Z][a-zA-Z0-9]*)([^>]*)(>[\s\S]*)$/)
  if (!m) return html
  const [, open, attrs, rest] = m
  if (/\bclass\s*=\s*(["'])/.test(attrs)) {
    return open + attrs.replace(/\bclass\s*=\s*(["'])([^"']*)\1/, (_s, q, existing) => `class=${q}${cls} ${existing}${q}`) + rest
  }
  return `${open} class="${cls}"${attrs}${rest}`
}

/** 围栏行（CommonMark：缩进最多 3 空格的 ``` / ~~~） */
const FENCE_RE = /^\s{0,3}(```|~~~)/

/** 缩进延续行：空行后以此开头的行与前一空行前的内容同属一个顶层块
 *  （loose list 项间空行、列表项内续段、引用内容缩进等）。 */
const INDENTED_RE = /^[ \t]{2,}\S/

/**
 * 顶级列表标记行：CommonMark 中空行分隔的列表项仍同属一个列表
 * （loose list，整文渲染只有一个 <ul>）——空行后紧跟列表标记时不能切，
 * 否则「分块渲染拼接」会产生两个 <ul>，与整体渲染不等价。
 */
const LIST_ITEM_RE = /^([-*+]|\d{1,9}[.)])[ \t]/

/**
 * 把 [scanPos, to) 范围内新完成的顶层块切出并缓存。
 *
 * @param source      流式源文本（shownText，前缀稳定、只增不减）
 * @param to          本轮扫描终点（通常 = enqueuedStableLen）
 * @param st          跨调用保持的扫描状态
 * @param renderChunk 把一个顶层块（源文本片段）渲染成 HTML 的回调
 */
export function advanceBlocks(
  source: string,
  to: number,
  st: BlockScanState,
  renderChunk: (chunk: string) => string,
): void {
  let i = st.scanPos
  const flushTo = (end: number) => {
    if (end > st.blockScanLen) {
      st.blocks.push(renderChunk(source.slice(st.blockScanLen, end)))
      st.blockScanLen = end
    }
  }

  while (i < to) {
    const nl = source.indexOf('\n', i)
    if (nl === -1) break
    const line = source.slice(i, nl)
    st.scanPos = nl + 1

    if (st.inFence) {
      if (FENCE_RE.test(line)) st.inFence = false
      i = nl + 1
      continue
    }
    if (FENCE_RE.test(line)) {
      st.inFence = true
      i = nl + 1
      continue
    }
    if (line.trim() !== '') {
      i = nl + 1
      continue
    }

    // 空行：向后找 to 范围内的第一个非空行
    let j = nl + 1
    let nextStart = -1
    let nextLine = ''
    while (j < to) {
      const nl2 = source.indexOf('\n', j)
      const l2 = nl2 === -1 ? source.slice(j) : source.slice(j, nl2)
      if (l2.trim() !== '') {
        nextStart = j
        nextLine = l2
        break
      }
      if (nl2 === -1) break
      j = nl2 + 1
    }

    if (nextStart === -1) {
      // 空行之后（到 to 为止）无更多内容：判定悬置。
      // 关键：把扫描指针退回空行行首（撤销循环顶部的 scanPos 推进），
      // 否则下一次内容到达时扫描已越过此空行，这个块边界候选永久丢失
      // ——表现为「最后一个段落永远无法闭合进块缓存」。
      st.scanPos = i
      i = to
      break
    }
    if (INDENTED_RE.test(nextLine) || LIST_ITEM_RE.test(nextLine)) {
      // 缩进延续 / 顶级列表延续：此空行不是顶层块边界，候选作废，组继续延伸
      i = nextStart
      st.scanPos = nextStart
      continue
    }
    // 安全切分点：切在下一个非空行行首
    flushTo(nextStart)
    st.scanPos = nextStart
    i = nextStart
  }
}
