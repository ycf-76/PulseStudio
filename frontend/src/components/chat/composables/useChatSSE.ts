import { ref, computed, onUnmounted, type Ref } from 'vue'
import { getSessionRuntime, abortAllRuntimes, sessionIdOfMessage } from './chatSessionRuntime'
import type { ChatMessage, AgentMeta, TurnPhase, StatusIndicator, ToolCall } from '../cell-types'
import { getToolDisplayLabel, getNodeDisplayLabel, getThinkingPreview, sanitizeContent, applySideWriteRequired } from '../cell-types'
import { stripThinkingFromHtml, stripHiddenTags, fenceBareJson } from '../chat-content-renderer'
import { getToolIcon, getNodeIcon } from '../icon-map'
import { AGENT_NODE_LABELS } from '../agent-output-formatter'
import { renderMarkdown, renderMarkdownStreaming, renderPlainTail, escapeHtml } from '../markdown-renderer'
import { advanceBlocks, createBlockScanState, markBlockEnter } from '../stable-blocks'
import { normalizeCardDraft, pickCoverUrl, pickImages } from '@/composables/cardDraft'
import { streamBaseCps, streamThinkCps, streamMinCps, streamMaxCps, streamFrameMs } from './useStreamSpeed'
import { StreamEngine } from '@/render/stream-engine'
import { TurnRuntime } from '@/render/turn-runtime'
import { ingestThoughtEvent } from '../thoughtStream'
import { Ev, PostResult } from '../sse-contract'

function _stripThinkBlocks(text: string): string {
  if (!text) return ''
  // 先清理 RichMediaReference（SSE 专用标签，不在通用 HIDDEN_TAGS 中）
  const cleaned = text.replace(/<RichMediaReference>[\s\S]*?superscript:/g, '')
  // 复用通用隐藏标签剥离逻辑（thinking / needs_clarification 等）
  return stripHiddenTags(cleaned)
}

export function _ingestContentDelta(msg: ChatMessage, delta: string): string {
  if (!delta) return ''
  const st = (msg as any)._thinkState || { inThink: false, base: '', buf: '', carry: '' }
  // 只识别 <thinking> 标签。
  // unicode think 标签(❨/❩)已移除——后端通过 reasoning_summary_text_delta 事件路由，
  // 前端不再需要从 agent_message_delta 的正文中用正则提取。
  const OPENS = ['<thinking>']
  const CLOSES = ['</thinking>']

  // 拼接上一块暂存的"部分标签"前缀，再统一扫描
  const text = (st.carry || '') + delta
  st.carry = ''
  let out = ''
  let i = 0
  while (i < text.length) {
    if (!st.inThink) {
      let oi = -1
      let openLen = 0
      for (const tag of OPENS) {
        const idx = text.indexOf(tag, i)
        if (idx !== -1 && (oi === -1 || idx < oi || (idx === oi && tag.length > openLen))) {
          oi = idx
          openLen = tag.length
        }
      }
      if (oi === -1) {
        // 本块无完整 OPEN： OPEN 前缀的字节暂存，等下一块
        const k = Math.max(...OPENS.map((t) => _trailingPartial(text, t)))
        out += text.slice(i, text.length - k)
        st.carry = text.slice(text.length - k)
        break
      }
      out += text.slice(i, oi)
      // 进入思考块：把此前已定稿的思考文本作为基底
      // 路由到 msg.reasoning（统一走 ReasoningSummaryCell 渲染）
      st.base = msg.reasoning || ''
      if (st.base && !st.base.endsWith('\n')) st.base += '\n'
      st.inThink = true
      st.buf = ''
      i = oi + openLen
    } else {
      let ci = -1
      let closeLen = 0
      for (const tag of CLOSES) {
        const idx = text.indexOf(tag, i)
        if (idx !== -1 && (ci === -1 || idx < ci || (idx === ci && tag.length > closeLen))) {
          ci = idx
          closeLen = tag.length
        }
      }
      if (ci === -1) {
        // 本块无完整 CLOSE： CLOSE 前缀的字节暂存，等下一块
        const k = Math.max(...CLOSES.map((t) => _trailingPartial(text.slice(i), t)))
        st.buf += text.slice(i, text.length - k)
        msg.reasoning = st.base + st.buf
        st.carry = text.slice(text.length - k)
        break
      }
      st.buf += text.slice(i, ci)
      msg.reasoning = st.base + st.buf + '\n'
      st.inThink = false
      st.buf = ''
      i = ci + closeLen
    }
  }
  ;(msg as any)._thinkState = st
  return out
}

// 返回 s 末尾与 tag 前缀匹配的最长长度（且 < tag.length，即"部分匹配"而非完整匹配）。
// 完整匹配由 indexOf 负责，这里只处理跨 chunk 截断的尾巴。
function _trailingPartial(s: string, tag: string): number {
  const maxk = Math.min(tag.length - 1, s.length)
  let best = 0
  for (let k = 1; k <= maxk; k++) {
    if (s.slice(s.length - k) === tag.slice(0, k)) best = k
  }
  return best
}

// 同一轮里首个 stream_chunk 用于替换启动占位文案，后续块才追加
const streamedPlaceholderReplaced = new WeakSet<ChatMessage>()

// BUG-007：资源截断/假完成类 ended_reason → 用户可见提示。
// 后端契约见 backend/app/engine/protocol/events.py EndedReason：
// max_iterations / token_budget / nudge_exhausted / duplicate_calls / fake_final
// 此前这些终止被静默当正常完成收尾，用户看到半截结果却以为任务做完了。
const TRUNCATION_NOTICES: Record<string, string> = {
    max_iterations: '⚠️ 本轮已达到最大迭代次数，任务被截断，以上结果可能不完整。发送「继续」可接着当前进度推进。',
    token_budget: '⚠️ 本轮 Token 预算已用尽，任务被截断，以上结果可能不完整。发送「继续」可接着当前进度推进。',
    nudge_exhausted: '⚠️ 本轮多次输出格式异常后被终止，以上结果可能不完整。建议重发或换个说法再试。',
    duplicate_calls: '⚠️ 本轮因重复执行相同操作被终止，以上结果可能不完整。建议换个说法再试。',
    fake_final: '⚠️ 本轮未产生实际执行结果（假完成已被拦截），以上内容不可靠。建议重发或换个说法再试。',
}

function _truncationNotice(reason?: string | null): string {
    if (!reason) return ''
    return TRUNCATION_NOTICES[reason] || ''
}

// 2026-11-03 占位文案守卫（大哥截图实锤：turn 正常 done，但正文停在
// "正在继续创作…"且被持久化进库，刷新后看到的是占位行而非最终回复）。
// 终态事件（workflow_completed / agent_loop_done）落库前必须：
// 1) 正文是占位行时优先用终态载荷里的最终消息顶掉；
// 2) 占位行绝不作为正文持久化（后端自己会落一条 completed 正文，前端再落占位行就是脏数据）。
const _PLACEHOLDER_CONTENTS = ['正在继续创作…', '正在恢复创作…', '创作偏好已确认，正在继续执行…', '处理中…', '思考中…']
// 2026-11-08 连接配额治理：暂停态集合——后端在这些状态不发终态事件，
// SSE 流不会自然关闭，必须由前端主动断开（见 subscribeChatSSE 事件循环）。
const _PAUSE_STATUSES = new Set([
    'awaiting_clarification',
    'awaiting_confirmation',
    'awaiting_storyboard_confirmation',
    'awaiting_cut_plan_confirmation',
    'awaiting_guardian_confirmation',
    'awaiting_side_write_confirmation',
])
function _isPlaceholderContent(c: string | undefined | null): boolean {
    const t = (c || '').trim()
    if (!t) return true
    return _PLACEHOLDER_CONTENTS.includes(t)
}

export interface ChatSSECallbacks {
  transitionTurn: (phase: TurnPhase, statusHeader?: string) => void
  /** 会话级转场：后台会话只写它自己的运行时，不污染当前视图的回合状态 */
  transitionTurnScoped?: (phase: TurnPhase, statusHeader: string | undefined, sessionId: string | null, msg?: ChatMessage | null) => void
  emit: (event: 'open-image-workspace' | 'draft-panel-update', draft?: any, sessionId?: string | null) => void
  isCodexMode: () => boolean
  isChatMode: () => boolean
  /** @deprecated 流式标记已按会话落到 chatSessionRuntime，保留仅为兼容旧调用方 */
  streamingThinking?: { value: boolean }
  /** @deprecated 同上 */
  streamingHasContent?: { value: boolean }
  persistAssistantMessage?: (content: string, agentMeta: AgentMeta, sessionId?: string | null) => void
  onSseComplete?: (sessionId?: string | null) => void
  onContentReady?: (sessionId?: string | null) => void
  onStreamDelta?: (sessionId?: string | null) => void
  /** 会话 → 绑定的作品 id（产物隔离：草稿必须落到该会话自己的作品上） */
  resolveWorkIdForSession?: (sessionId: string) => string | null
}

export interface ChatSSEOptions {
  /** 外部持有的「当前视图会话」标识：与 ChatView / useChatStream 共用同一个 ref */
  activeSessionId?: Ref<string | null>
}

export function useChatSSE(callbacks: ChatSSECallbacks, options?: ChatSSEOptions) {
    // 控制器 / 消息代 / Last-Event-ID / ready 信号全部按会话存放（chatSessionRuntime）。
    // 这样 A 会话在跑时，B 会话订阅不会 abort 掉 A，B 的新回合也不会作废 A 的事件。
    const workflowSseLastEventId = ref<string>('')
    const activeWorkflowId = ref<string | null>(null)
    const activeSessionId = options?.activeSessionId ?? ref<string | null>(null)
    let _lastSubscribedSid: string | null = null

    const _rtOf = (sid?: string | null) => getSessionRuntime(sid || activeSessionId.value)
    /** 消息所属会话的运行时（消息上的 sessionId 由订阅时盖章） */
    const _rtOfMsg = (msg?: ChatMessage | null) =>
        getSessionRuntime(sessionIdOfMessage(msg, _lastSubscribedSid || activeSessionId.value))

    const chatSseController = computed<AbortController | null>({
        get: () => _rtOf().chatSseController.value,
        set: (v: AbortController | null) => { _rtOf().chatSseController.value = v },
    })
    const workflowSseController = computed<AbortController | null>({
        get: () => _rtOf().workflowSseController.value,
        set: (v: AbortController | null) => { _rtOf().workflowSseController.value = v },
    })

    function waitForChatSseReady(sid?: string | null): Promise<void> {
        // 显式传 null（草稿态）时不能回落到"上次订阅的会话"，否则会挂到别人的就绪队列上
        const target = sid === undefined ? (_lastSubscribedSid || activeSessionId.value) : sid
        return getSessionRuntime(target).waitSseReady()
    }

    /** 递增消息代：只作废「目标会话」的旧订阅，别的会话照常跑 */
    function advanceMsgEpoch(sid?: string | null) {
        // 同上：显式 null（草稿态）必须落到草稿运行时，
        // 绝不能回落到上次订阅的会话——那会把后台正在跑的会话的订阅作废掉。
        const target = sid === undefined ? (_lastSubscribedSid || activeSessionId.value) : sid
        getSessionRuntime(target).epoch++
    }

    function _dispatchTrans(phase: TurnPhase, header: string | undefined, sid?: string | null, msg?: ChatMessage | null) {
        if (callbacks.transitionTurnScoped) {
            callbacks.transitionTurnScoped(phase, header, sid ?? null, msg ?? null)
            return
        }
        callbacks.transitionTurn(phase, header)
    }

    onUnmounted(() => {
        // 并行中的每个会话都要收干净
        abortAllRuntimes()
    })


    // ── 流式文本播放器：StreamEngine（渲染层 L3，codex 两档模型）──
    // 1) 事件处理只把 delta feed 进引擎 raw 缓冲，独立 rAF 循环按帧 step()
    //    释放到 shownText 再写入响应式 content。渲染节奏与事件到达频率解耦，
    //    杜绝 Vue 批量合并导致的「整段打印」。显示内容永远 ≤ 真实已到达内容。
    // 2) 速率模型：Smooth 严格匀速 baseCps（默认 160）+ CatchUp 积压追赶
    //    （250ms 滞回，2×baseCps 倍数闸）；终态 markFinished 后按
    //    settleMultiplier（2.2×）封顶温和追完——后端一次性吐几千字（agent 路径
    //    正文随 POST 到达）也匀速播完，不会「飞速倾泻」。
    // 3) 增量 markdown 渲染（稳定段缓存）：稳定段只解析一次并缓存，
    //    每帧只重解析末尾活跃段——长回复每帧渲染成本恒定。
    // 4) reasoning 独立播放器（thinkCps=90）：思考匀速流出，已显示前缀不跳动。
    interface StreamPlayer {
        engine: StreamEngine
        raf: number | null
        last: number
        lastPaint: number
        // 双游标（对齐 Codex emitted_stable_len / enqueued_stable_len）
        // enqueued: 已识别为稳定但尚未渲染到 DOM 的边界
        // emitted: 已渲染到 DOM 的稳定段边界，一旦推进永不回退
        // 任务2后 emitted == blockScanLen（最后一个安全顶层块边界），
        // 稳定段 HTML 来自块缓存 blocks.join('')，旧块永不重渲染。
        enqueuedStableLen: number
        emittedStableLen: number
        emittedStableHtml: string
        // 块级渲染缓存（stable-blocks.ts）：已完成顶层块的 HTML 数组
        blockState: ReturnType<typeof createBlockScanState>
        // 已注入淡入动画标记（rl-enter）的块数游标：blocks 中索引小于它的块
        // 视为"动画已播过"，join 时不注入标记——稳定区 HTML 每次推进都是
        // innerHTML 全量重建，若不区分新旧块，旧块 DOM 重建会重播动画（闪烁）。
        animatedUpTo: number
        phase: 'preview' | 'settling' | 'settled'
        settledHtml: string
        // 流式输入已结束（agent_message_completed / workflow_completed 到达），
        // 播放器追完后自动落地（settled），不再需要外部 cancelStreamDelta 跳帧
        pendingSettle: boolean
        // 落地内容：若 completed 事件携带的文本比 delta 累积更长，用此值覆盖
        settleContent: string | null
        // finishStream(msg, onDone) 的完成回调：播放落地后触发（数据态与渲染态分离）
        onSettled: (() => void) | null
    }

    const streamPlayers = new WeakMap<ChatMessage, StreamPlayer>()

    // 思考播放器（reasoning）：与正文同一两档模型，速率 thinkCps=90。
    // 种子为 tag 提取已写入的前缀（_ingestContentDelta 的 <thinking> 分支），
    // 之后 delta 流式追赶——已显示前缀不再跳动，仅尾部生长。
    interface ReasoningPlayer {
        engine: StreamEngine
        raf: number | null
        last: number
        lastPaint: number
    }

    const reasoningPlayers = new WeakMap<ChatMessage, ReasoningPlayer>()

    // 参数从 useStreamSpeed 读取，用户可在设置面板实时调节：
    // baseCps（正文匀速）/ thinkCps（思考匀速）/ minCps·maxCps（追赶上下界）/
    // frameMs（DOM 重渲染节流）。追赶/收尾速率按档位倍数闸封顶（正文 2×/2.2×，
    // 思考 4×/6×，见 _makeEngine）。
    function _makeEngine(kind: 'body' | 'think'): StreamEngine {
        // 思考档追赶/收尾倍数更高（4×/6×）：reasoning 到达速率（~100-200cps）
        // 高于 thinkCps 播放速率，且 proto 协议门会扣发整段到生成结束才一次性
        // 补发——低倍数下积压只能靠终态 settle 兜底（整段蹦出）。
        return new StreamEngine({
            baseCps: kind === 'think' ? streamThinkCps.value : streamBaseCps.value,
            minCps: streamMinCps.value,
            maxCps: streamMaxCps.value,
            maxCatchUpMultiplier: kind === 'think' ? 4 : 2,
            settleMultiplier: kind === 'think' ? 6 : 2.2,
        })
    }

    function _ensureStreamPlayer(msg: ChatMessage): StreamPlayer {
        let p = streamPlayers.get(msg)
        if (!p) {
            p = {
                engine: _makeEngine('body'), raf: null, last: 0, lastPaint: 0,
                enqueuedStableLen: 0, emittedStableLen: 0, emittedStableHtml: '',
                blockState: createBlockScanState(), animatedUpTo: 0,
                phase: 'preview', settledHtml: '', pendingSettle: false,
                settleContent: null, onSettled: null,
            }
            streamPlayers.set(msg, p)
        }
        return p
    }

    function _ensureReasoningPlayer(msg: ChatMessage): ReasoningPlayer {
        let rp = reasoningPlayers.get(msg)
        if (!rp) {
            rp = { engine: _makeEngine('think'), raf: null, last: 0, lastPaint: 0 }
            // 种子：tag 提取已直接写入的前缀（接续播放，不重播）
            const seed = msg.reasoning || ''
            if (seed) rp.engine.feed(seed)
            reasoningPlayers.set(msg, rp)
        }
        return rp
    }

    function _sanitizeForStream(text: string): string {
        return callbacks.isChatMode() ? sanitizeContent(text) : text
    }

    // 入队稳定边界（对齐 Codex enqueued_stable_len）
    // 换行提交策略：遇到换行符就标记为稳定行（Codex 默认策略）
    // 但代码围栏内不提交——等围栏闭合后再整体提交
    // 这避免了长段落中内容迟迟不被提交的问题（双换行太保守）
    function _enqueueStableBoundary(p: StreamPlayer, shownText: string) {
        // 注：旧版这里有个「新增 >= 20 字符才重新扫描」的增量缓冲。任务2 后必须移除：
        // 块切分器（advanceBlocks）判定切分需要看到「空行后的第一行」，增量缓冲会让
        // 末段空行后的行长期进不了 enqueued，块永远无法闭合（emitted 卡死）。
        // enqueued 推进成本 = O(新增字符) 的字符串扫描，远低于 markdown 渲染，无碍。
        // 从当前 enqueued 位置向后扫描，找到最后一个可提交的换行
        let lastCommitableNewline = -1
        let inCodeFence = false
        const start = p.enqueuedStableLen
        for (let i = start; i < shownText.length; i++) {
            if (shownText[i] === '`' && shownText[i+1] === '`' && shownText[i+2] === '`') {
                inCodeFence = !inCodeFence
                i += 2
                continue
            }
            if (!inCodeFence && shownText[i] === '\n') {
                lastCommitableNewline = i + 1
            }
        }
        if (lastCommitableNewline > p.enqueuedStableLen) {
            p.enqueuedStableLen = lastCommitableNewline
        }
    }

    // 提交稳定段（对齐 Codex emitted_stable_len）
    // 任务2：块级缓存——advanceBlocks 把新完成的顶层块切出并各渲染一次，
    // emittedStableHtml 来自块缓存拼接（纯字符串 join，零 markdown 解析）。
    // emitted 语义从「最后一个换行」收紧为「最后一个安全顶层块边界」：
    // 未闭合组（当前段落/列表）留在尾部继续生长，这是 Codex
    // "stable_source_len = completed top-level blocks" 的本意。
    function _emitStableBoundary(p: StreamPlayer, shownText: string) {
        if (p.enqueuedStableLen <= p.blockState.blockScanLen) return
        advanceBlocks(shownText, p.enqueuedStableLen, p.blockState, (chunk) =>
            renderMarkdownStreaming(_sanitizeForStream(chunk)),
        )
        p.emittedStableLen = p.blockState.blockScanLen
        // 块级淡入（Claude.ai 输出体验）：只有本轮新进入稳定区的块注入
        // rl-enter 动画标记；旧块保持原始 HTML——它们在下次推进时会被
        // innerHTML 重建，但没有动画标记就不会重播（稳定区不闪烁的关键）。
        const total = p.blockState.blocks.length
        if (total > p.animatedUpTo) {
            p.emittedStableHtml = p.blockState.blocks
                .map((b, i) => (i < p.animatedUpTo ? b : markBlockEnter(b)))
                .join('')
            p.animatedUpTo = total
        }
        // 注意：没有新块时不重写 emittedStableHtml——否则会把已注入的
        // rl-enter 标记用无标记版本覆盖掉（每帧 advanceBlocks 空跑时
        // blocks 数组不变，旧值就是最新正确值）。
    }

    // 渲染当前帧：emitted 稳定段 HTML + 可变尾部增量渲染。
    // 双通道输出（任务1）：稳定段/尾部段写入独立字段，消费侧（AssistantMessageCell）
    // 拆成两个 v-html 节点——稳定区字符串不变时 Vue 不 patch，DOM 子树保留，
    // 每帧真正重建的只有尾部节点（对齐 Claude Code blit：未变区域不重绘）。
    function _paintStreamFrame(msg: ChatMessage, p: StreamPlayer) {
        const shownText = p.engine.shownText
        msg.content = shownText
        _enqueueStableBoundary(p, shownText)
        _emitStableBoundary(p, shownText)
        const tail = shownText.slice(p.emittedStableLen)
        ;(msg as any)._dualStream = true
        ;(msg as any)._stableHtml = stripThinkingFromHtml(p.emittedStableHtml)
        // 尾部渲染：纯文本快速路径优先（跳过 markdown-it 管线），
        // 含 markdown 语法则走免高亮的流式渲染
        const cleanTail = tail ? _sanitizeForStream(tail) : ''
        const plainHtml = tail ? renderPlainTail(cleanTail) : null
        let tailHtml: string
        if (plainHtml !== null) {
            // Claude 真身 Grapheme Blur Reveal：tail 单调增长，帧间长度 diff
            // 出新增 token 包 .token-reveal（blur 6px 显影，参数照抄 CDS）
            const prevLen = (p as any)._revealLen || 0
            if (cleanTail.length > prevLen && prevLen > 0) {
                const stable = escapeHtml(cleanTail.slice(0, prevLen))
                const fresh = escapeHtml(cleanTail.slice(prevLen))
                tailHtml = `<p>${stable}<span class="token-reveal">${fresh}</span></p>`
            } else if (cleanTail.length > prevLen) {
                tailHtml = `<p><span class="token-reveal">${escapeHtml(cleanTail)}</span></p>`
            } else {
                tailHtml = plainHtml
            }
            ;(p as any)._revealLen = cleanTail.length
        } else {
            // markdown 路径：帧间 HTML 公共前缀 diff，差异段（阅读前沿）晕染。
            // safe 截到最后一个 '>' 完整标签尾，避免 reveal span 切进标签属性。
            const html = renderMarkdownStreaming(cleanTail)
            const prevHtml = (p as any)._lastTailHtml || ''
            if (html.length > prevHtml.length && html.startsWith(prevHtml.slice(0, Math.min(prevHtml.length, html.length - 1)))) {
                let common = 0
                const minL = Math.min(prevHtml.length, html.length)
                while (common < minL && prevHtml[common] === html[common]) common++
                const safe = html.lastIndexOf('>', common - 1) + 1
                if (safe > 0 && html.length - safe < 4000) {
                    tailHtml = html.slice(0, safe) +
                        '<span class="token-reveal">' + html.slice(safe) + '</span>'
                } else {
                    tailHtml = html
                }
            } else {
                tailHtml = html
            }
            ;(p as any)._lastTailHtml = html
            ;(p as any)._revealLen = cleanTail.length
        }
        ;(msg as any)._tailHtml = tail ? stripThinkingFromHtml(tailHtml) : ''
        // _streamHtml 保留整串拼接：非双通道消费点 / 调试路径的兼容兜底
        msg._streamHtml = stripThinkingFromHtml(
            p.emittedStableHtml +
                (tail ? (renderPlainTail(cleanTail) ?? renderMarkdownStreaming(cleanTail)) : ''),
        )
    }

    // 正文播放循环：固定节流 + 引擎步进；追完后按需自动落地
    function _startPlayerLoop(msg: ChatMessage, p: StreamPlayer) {
        if (p.raf != null) return
        p.last = performance.now()
        p.lastPaint = 0
        const tick = () => {
            const cur = streamPlayers.get(msg)
            if (!cur) return
            const now = performance.now()
            const advanced = cur.engine.step(now)
            const _frameMs = Math.max(8, streamFrameMs.value || 16)
            if (advanced || now - cur.lastPaint >= _frameMs) {
                cur.lastPaint = now
                _paintStreamFrame(msg, cur)
            }
            if (cur.engine.done) {
                // 输入已结束且全部显示完毕：停机，pendingSettle 时自动落地
                cur.raf = null
                if (cur.pendingSettle && cur.phase !== 'settled') {
                    _doSettle(msg, cur)
                }
            } else {
                cur.raf = requestAnimationFrame(tick)
            }
        }
        p.raf = requestAnimationFrame(tick)
    }

    // 思考播放循环：同两档模型（thinkCps），只写 msg.reasoning
    function _startReasoningLoop(msg: ChatMessage, rp: ReasoningPlayer) {
        if (rp.raf != null) return
        rp.last = performance.now()
        rp.lastPaint = 0
        const tick = () => {
            const cur = reasoningPlayers.get(msg)
            if (!cur) return
            const now = performance.now()
            const advanced = cur.engine.step(now)
            const _frameMs = Math.max(8, streamFrameMs.value || 16)
            if (advanced || now - cur.lastPaint >= _frameMs) {
                cur.lastPaint = now
                msg.reasoning = cur.engine.shownText
            }
            if (cur.engine.done) {
                cur.raf = null
            } else {
                cur.raf = requestAnimationFrame(tick)
            }
        }
        rp.raf = requestAnimationFrame(tick)
    }

    
/** 终态整块消息的流式落地：分片喂 StreamPlayer（晕染/辉光跟随），替代一次性赋值 */
function landContent(assistantMsg: ChatMessage, text: string) {
  const content = sanitizeContent(text)
  if (content.length <= 60) {
    assistantMsg.content = content
    return
  }
  const step = Math.max(24, Math.ceil(content.length / 14))
  let i = 0
  const tick = () => {
    if (i >= content.length) return
    assistantMsg.content += content.slice(i, i + step)
    i += step
    setTimeout(tick, 26)
  }
  tick()
}
function pushStreamDelta(msg: ChatMessage, delta: string) {
        if (!delta) return
        if (!streamedPlaceholderReplaced.has(msg)) {
            // 首个真实内容块：抛弃启动占位文案
            msg.content = ''
            streamedPlaceholderReplaced.add(msg)
        }
        const cleaned = _ingestContentDelta(msg, delta)
        const inThink = !!(msg as any)._thinkState?.inThink
        if (inThink) {
            _rtOfMsg(msg).streamingThinking.value = true
        }
        if (!cleaned) return
        const p = _ensureStreamPlayer(msg)
        p.engine.feed(cleaned)
        ;(msg as any)._contentFromSSE = true
        _rtOfMsg(msg).streamingHasContent.value = true
        _dispatchTrans('running', '生成回复中…', sessionIdOfMessage(msg, activeSessionId.value), msg)
        callbacks.onStreamDelta?.(sessionIdOfMessage(msg, activeSessionId.value))
        _startPlayerLoop(msg, p)
    }

    // reasoning delta 独立播放（thinkCps=90 匀速）。已显示前缀不跳动，仅尾部生长。
    function pushReasoningDelta(msg: ChatMessage, delta: string) {
        if (!delta) return
        const rp = _ensureReasoningPlayer(msg)
        rp.engine.feed(delta)
        // 思考中标记落到「消息所属会话」的运行时，后台会话不点亮当前视图
        _rtOfMsg(msg).streamingThinking.value = true
        _startReasoningLoop(msg, rp)
    }

    // 思考播放器落地（reasoning_completed / 停止路径）：立即显示全部
    function _flushReasoningPlayer(msg: ChatMessage) {
        const rp = reasoningPlayers.get(msg)
        if (!rp) return
        if (rp.raf != null) {
            cancelAnimationFrame(rp.raf)
            rp.raf = null
        }
        rp.engine.settle()
        msg.reasoning = rp.engine.shownText
    }

    // 状态头预览：取引擎「即将显示」的文本（msg.reasoning 要等下一帧才写入）
    function _reasoningPreview(msg: ChatMessage): string {
        const rp = reasoningPlayers.get(msg)
        return rp ? rp.engine.shownText : (msg.reasoning || '')
    }

    function _doSettle(msg: ChatMessage, p: StreamPlayer) {
        const cb = p.onSettled
        if (p.phase === 'settled') {
            // 已落地过：仅触发迟到的回调（8.5 补丁——finishStream 在 tick 落地后才被调用）
            p.onSettled = null
            if (cb) cb()
            return
        }
        const finalContent = p.settleContent ?? p.engine.raw
        msg.content = finalContent
        p.phase = 'settling'
        p.settledHtml = stripThinkingFromHtml(renderMarkdown(_sanitizeForStream(fenceBareJson(finalContent))))
        msg._streamHtml = p.settledHtml
        // 落地 = 单节点完整渲染：关闭双通道，清空分段字段与块缓存
        ;(msg as any)._dualStream = false
        ;(msg as any)._stableHtml = ''
        ;(msg as any)._tailHtml = ''
        p.emittedStableLen = 0
        p.emittedStableHtml = ''
        p.enqueuedStableLen = 0
        p.blockState = createBlockScanState()
        p.animatedUpTo = 0
        p.phase = 'settled'
        p.pendingSettle = false
        p.settleContent = null
        p.onSettled = null
        if (cb) cb()
    }

    function _stopPlayer(msg: ChatMessage) {
        const p = streamPlayers.get(msg)
        if (!p) return
        if (p.raf != null) {
            cancelAnimationFrame(p.raf)
            p.raf = null
        }
        p.engine.settle()
        _doSettle(msg, p)
    }

    // 标记流式输入结束，让播放器加速追完后自动落地（不跳帧）
    function _markPendingSettle(msg: ChatMessage, settleContent: string | null = null) {
        const st = (msg as any)._thinkState
        if (st) {
            if (st.carry) {
                if (st.inThink) {
                    st.buf += st.carry
                    msg.reasoning = (st.base || '') + st.buf + '\n'
                } else if (st.carry.startsWith('<') && !st.carry.includes('>')) {
                    // 不完整的标签前缀（如 '<', '<t', '<thin'），直接丢弃
                } else {
                    // carry 是普通文本（如被截断的非标签内容），追加到引擎
                    const p = streamPlayers.get(msg)
                    if (p) p.engine.feed(st.carry)
                }
                st.carry = ''
            }
            if (st.inThink) {
                st.inThink = false
                st.buf = ''
            }
        }
        const p = streamPlayers.get(msg)
        if (!p) {
            // 没有 player 说明没有 delta 到达，直接设内容
            if (settleContent) {
                msg.content = settleContent
                msg._streamHtml = stripThinkingFromHtml(renderMarkdown(_sanitizeForStream(fenceBareJson(settleContent))))
            }
            return
        }
        p.pendingSettle = true
        p.engine.markFinished()
        if (settleContent && settleContent.length > p.engine.raw.length) {
            p.settleContent = settleContent
        }
        // 引擎已追完（含 raf 已退出的情形）：立即落地
        if (p.engine.done) {
            _doSettle(msg, p)
        } else if (p.raf == null) {
            // 有积压但循环没在跑（罕见）：补齐循环让它追完自动落地
            _startPlayerLoop(msg, p)
        }
        // 否则 tick 循环会在追完后自动调用 _doSettle
    }

    function cancelStreamDelta(msg: ChatMessage) {
        _stopPlayer(msg)
    }

    /**
     * 丢弃被污染的流式内容（ReAct 协议 JSON 兜底防御专用）：
     * 停掉 rAF 循环并删除播放器实例——下次 _ensureStreamPlayer 会以空引擎
     * 重建，避免 playFinalText 的新正文接在污染残液后面。同时清空 content
     * 与 _streamHtml 渲染残留。
     */
    function discardStreamContent(msg: ChatMessage) {
        const p = streamPlayers.get(msg)
        if (p && p.raf != null) {
            cancelAnimationFrame(p.raf)
        }
        streamPlayers.delete(msg)
        msg.content = ''
        if ((msg as any)._streamHtml) (msg as any)._streamHtml = ''
        ;(msg as any)._dualStream = false
        ;(msg as any)._stableHtml = ''
        ;(msg as any)._tailHtml = ''
    }

    function flushStreamDelta(msg: ChatMessage) {
        _stopPlayer(msg)
    }

    /**
     * 结束流式播放并等待落地（数据态与渲染态分离的关键出口）。
     * 调用方把 transitionTurn(done)/notify/落库放进 onDone——状态先于文字
     * 结束、落库拿到半段 content 的问题由此根治。
     * 含兜底分支：引擎已追完但 rAF 循环已退出（raf==null）时 tick 不会再
     * 触发 _doSettle，这里补一次落地+回调。
     */
    function finishStream(msg: ChatMessage, onDone?: () => void) {
        const p = streamPlayers.get(msg)
        if (!p) {
            // 没有播放器（无 delta 到达）：直接回调，内容由调用方自理
            if (onDone) onDone()
            return
        }
        p.onSettled = onDone || null
        _markPendingSettle(msg)
        if (p.raf == null && p.phase !== 'settled') {
            // 循环已停且未落地（异常/竞态路径）：跳帧落地并回调
            _doSettle(msg, p)
        }
    }

    /**
     * POST 出口的一次性全文播放（agent 路径正文随 POST 到达的场景）。
     * feed 全文 + 立即 markFinished → 按 settleMultiplier 匀速追完，
     * done 收尾（transitionTurn/notify）移入 onDone 回调。
     */
    function playFinalText(msg: ChatMessage, text: string, opts?: { onDone?: () => void }) {
        if (!text) {
            if (opts?.onDone) opts.onDone()
            return
        }
        const p = _ensureStreamPlayer(msg)
        const isFirst = !streamedPlaceholderReplaced.has(msg)
        if (isFirst) {
            // 该 msg 此前没有任何真实正文（纯 POST 一次性到达）：
            // 抛弃启动占位文案，喂入完整终稿。
            msg.content = ''
            streamedPlaceholderReplaced.add(msg)
            p.engine.feed(text)
        } else {
            // 已通过实时 delta 流显示过正文：append-only 补齐增量，
            // 绝不清空、绝不重播已显示内容（修复「文字往前倒退 / 从头再播」）。
            const already = p.engine.raw.length
            if (text.length > already) p.engine.feed(text.slice(already))
            // text.length <= already：终稿已在流式中完整到达，幂等收尾即可。
        }
        p.engine.markFinished()
        ;(msg as any)._contentFromSSE = true
        _rtOfMsg(msg).streamingHasContent.value = true
        p.onSettled = opts?.onDone || null
        // 已落地且追完：直接触发回调，避免重复启动循环 / 重复回调。
        if (p.phase === 'settled' && p.engine.done) {
            if (opts?.onDone) opts.onDone()
            return
        }
        _startPlayerLoop(msg, p)
        if (p.phase !== 'settled' && p.engine.done) _doSettle(msg, p)
    }

    function _recalcPercent(meta: AgentMeta) {
        const steps = meta.steps || []
        if (steps.length === 0) {
            meta.totalPercent = 0;
            return
        }
        const done = steps.filter(s => s.status === 'completed').length
        meta.totalPercent = Math.round((done / steps.length) * 100)
    }

    /**
     * 卡片草稿落地（产物隔离）：
     * 只写进「本会话绑定的作品」，没绑定就新建一条带 sessionId 的草稿，
     * 绝不落到别的会话/作品上，避免两个创作任务互相覆盖。
     */
    async function _landCardDraft(sessionId: string, payload: any, meta?: AgentMeta | null) {
        const {useWorkStore} = await import('@/stores/work')
        const workStore = useWorkStore()
        const normalized = normalizeCardDraft(payload.card_draft)
        const pngUrls: string[] = payload.png_urls?.length
            ? payload.png_urls
            : normalized.pngUrls
        const htmlUrls: string[] = payload.html_urls?.length
            ? payload.html_urls
            : normalized.htmlUrls
        normalized.pngUrls = pngUrls
        normalized.htmlUrls = htmlUrls

        const coverUrl = pickCoverUrl(normalized.pages, pngUrls)
        const images = pickImages(normalized.pages, pngUrls)
        const firstPageHtml = workStore.extractFirstPageHtml(normalized)

        const boundWorkId = callbacks.resolveWorkIdForSession?.(sessionId) || ''
        const boundWork = boundWorkId
            ? workStore.works.find(w => w.id === boundWorkId) ||
              workStore.outputWorks.find(w => w.id === boundWorkId)
            : null

        let draftWorkId = ''
        if (boundWork && boundWork.isDraft) {
            workStore.updateDraft(boundWork.id, {
                cardDraft: normalized,
                coverUrl: coverUrl || boundWork.coverUrl,
                images,
                firstPageHtml,
            })
            draftWorkId = boundWork.id
        } else {
            const saved = workStore.saveCardAsDraft(normalized, normalized.title, coverUrl, images, {
                sessionId,
                sourceWorkId: boundWorkId || undefined,
            })
            draftWorkId = saved?.id || ''
        }

        if (meta) {
            meta.cardDraft = normalized
            // 草稿作品 id：右侧侧边栏预览入口用（同 useChatAgent.landCardDraft）
            if (draftWorkId) meta.cardDraftWorkId = draftWorkId
        }
        callbacks.emit('draft-panel-update', {cardDraft: normalized, pngUrls, htmlUrls}, sessionId)
    }

    /**
     * 草稿就地修改（work_edit 产物）：
     * 后端写的是内容覆盖层（原始快照不动），前端只需把「覆盖后的字段」
     * 回写到本地草稿，保证详情页/列表刷新前就能看到效果。
     * 只动提交过的字段，未提交的保持原样；不新建作品。
     */
    async function _applyWorkPatch(sessionId: string, payload: any, meta?: AgentMeta | null) {
        const patch = payload?.work_patch
        if (!patch || typeof patch !== 'object') return
        const fields = patch.fields || {}
        const applied = patch.applied || {}
        const {useWorkStore} = await import('@/stores/work')
        const workStore = useWorkStore()

        const targetId = String(
            patch.work_id || callbacks.resolveWorkIdForSession?.(sessionId) || ''
        )
        if (!targetId) return

        const updates: Record<string, any> = {}
        if (fields.title !== undefined) updates.title = applied.title ?? fields.title
        if (fields.content_text !== undefined) updates.contentText = applied.content_text ?? fields.content_text
        if (fields.tags !== undefined) updates.tags = applied.tags ?? fields.tags
        if (fields.images !== undefined) updates.images = applied.images ?? fields.images
        if (fields.cover_img_url !== undefined) updates.coverUrl = applied.cover_img_url ?? fields.cover_img_url
        if (Object.keys(updates).length === 0) return

        workStore.updateDraft(targetId, updates as any)
        if (meta) meta.workPatch = { workId: targetId, fields: updates }
        callbacks.emit('draft-panel-update', {workPatch: {workId: targetId, fields: updates}}, sessionId)
    }

    async function subscribeChatSSE(
        sessionId: string,
        assistantMsg: ChatMessage,
        retryCount = 0,
        turnId?: string,
        skipStartGate = false
    ) {
        const {tryRefreshToken} = await import('@/api/client')
        // 只替换「本会话」的旧控制器：别的会话在跑不受影响
        const srt = getSessionRuntime(sessionId)
        if (srt.chatSseController.value) {
            srt.chatSseController.value.abort()
            srt.chatSseController.value = null
        }
        const controller = new AbortController()
        srt.chatSseController.value = controller
        // 捕获本会话当前消息代，用于过滤旧 replay 事件
        const epoch = srt.epoch
        _lastSubscribedSid = sessionId
        // 是否已收到新消息的 workflow_started 事件
        // 在此之前的所有事件都是旧消息的 replay，必须忽略
        // skipStartGate=true：澄清/确认续跑时旧 SSE 可能已收到 workflow_started
        // 并更新了 chatSseLastEventId，新订阅回放跳过该事件导致闸门永远锁死，
        // 所以续跑场景直接跳过闸门检查
        let newWorkflowStarted = skipStartGate
        let _sawPauseBeforeClose = false
        // 把真实会话 id 种进 agentMeta（Oct-18 修复：此前该字段恒为 null，
        // 导致进度条/审核卡永不渲染、反馈事件 workflow_id 恒空）
        if (assistantMsg.agentMeta) {
          assistantMsg.agentMeta.sessionId = assistantMsg.agentMeta.sessionId ?? sessionId
        }

        const token = localStorage.getItem("token")
        const headers: Record<string, string> = {Accept: "text/event-stream"}
        if (token) headers["Authorization"] = "Bearer " + token
        if (srt.chatSseLastEventId.value) {
            headers["Last-Event-ID"] = srt.chatSseLastEventId.value
        }
        console.log('[subscribeChatSSE] START', {
            sessionId, retryCount, turnId, skipStartGate,
            lastEventId: srt.chatSseLastEventId.value,
            epoch, currentEpoch: srt.epoch,
        })
        // 回合隔离（L1+L2 双保险）：
        // - 显式 turnId（useChatAgent 发送前 genTurnId）→ URL 带参让后端只回放
        //   本回合事件 + 前端沙箱闸二次过滤
        // - 不传 turnId（resume 等旧路径）→ 收到带 turn_id 的 chat_turn_started
        //   后动态启用沙箱，向后兼容
        const _sseUrl = turnId
            ? `/api/sse/chat/${sessionId}?turn_id=${encodeURIComponent(turnId)}`
            : `/api/sse/chat/${sessionId}`

        // 2026-11-08 暂停态延迟关流：暂停事件到达后不立即 abort——
        // 刷新恢复/重连时会全量回放回合历史，回放撞到旧暂停事件后通常还跟着
        // 确认续跑的新事件（状态早已离开暂停态），立即关流会把运行中的回合
        // 拦腰杀掉（大哥截图实锤「还在思考，按钮提前结束」）。
        // 改为：暂停检测后延迟 3s 复查——仍处暂停态、且 1.5s 内无任何新事件
        // （真实暂停时事件流彻底安静）才关流释放连接配额。
        // 注意必须声明在 try 之外：finally 里要清理定时器。
        let _lastEventAt = Date.now()
        let _pauseCloseTimer: ReturnType<typeof setTimeout> | null = null
        const schedulePauseClose = () => {
            if (_pauseCloseTimer) clearTimeout(_pauseCloseTimer)
            _pauseCloseTimer = setTimeout(() => {
                _pauseCloseTimer = null
                const cur = assistantMsg?.agentMeta?.workflowStatus
                if (_PAUSE_STATUSES.has(cur || '')
                    && Date.now() - _lastEventAt >= 1500
                    && srt.chatSseController.value === controller) {
                    console.log('[ChatSSE] pause state settled, closing stream to free connection quota')
                    controller.abort()
                }
            }, 3000)
        }

        try {
            let response = await fetch(_sseUrl, {
                headers,
                signal: controller.signal,
            })

            if (response.status === 401 && token) {
                const newToken = await tryRefreshToken()
                if (newToken) {
                    headers["Authorization"] = "Bearer " + newToken
                    response = await fetch(_sseUrl, {
                        headers,
                        signal: controller.signal,
                    })
                }
            }

            if (!response.ok || !response.body) {
                console.warn(`[ChatSSE] connection failed: status=${response.status}, ok=${response.ok}`)
                srt.markSseReady()
                return
            }

            const reader = response.body.getReader()
            const decoder = new TextDecoder()
            srt.markSseReady()
            let buffer = ""

            while (true) {
                const {done, value} = await reader.read()
                if (done) break
                buffer += decoder.decode(value, {stream: true})
                const lines = buffer.split("\n")
                buffer = lines.pop() || ""
                for (const line of lines) {
                    if (line.startsWith("id: ")) {
                        srt.chatSseLastEventId.value = line.slice(4).trim()
                        continue
                    }
                    if (!line.startsWith("data: ")) continue
                    const jsonStr = line.slice(6)
                    if (!jsonStr.trim()) continue
                    try {
                        const parsed = JSON.parse(jsonStr)
                        const _et = parsed.type || parsed.event_type
                        console.log('[ChatSSE] event received', {
                            type: _et, eventId: parsed.event_id,
                            epochMatch: epoch === srt.epoch,
                            newWorkflowStarted, turnId: parsed.turn_id,
                        })
                        // 思考流事件总线：每帧实时归一化进 ThoughtStream（唯一写入点）
                        ingestThoughtEvent(_et, parsed.payload || parsed, parsed)
                        // 消息代检查：如果 epoch 不匹配，说明这是旧消息的 replay 事件，忽略
                        if (epoch !== srt.epoch) {
                            console.log('[ChatSSE] DROPPED: epoch mismatch', { epoch, current: srt.epoch })
                            continue
                        }
                        // ── 回合沙箱闸（L2 TurnRuntime）──
                        // 异回合/无 turn_id 事件一律拦截并计数（UI 展示「已拦截 N 条」）。
                        // 显式 turnId 模式：本订阅只应收到本回合事件，漏网的也拦掉。
                        // 旧路径（无 turnId）：收到带 turn_id 的 chat_turn_started 后
                        // 动态启用隔离。
                        if (turnId) {
                            let rt = (assistantMsg as any)._turnRt
                            if (!rt) {
                                rt = new TurnRuntime(turnId)
                                ;(assistantMsg as any)._turnRt = rt
                            }
                            if (!rt.accepts(parsed)) continue
                        } else {
                            const evTurnId = parsed.turn_id
                            if (evTurnId && _et === 'chat_turn_started' && !(assistantMsg as any)._turnRt) {
                                ;(assistantMsg as any)._turnRt = new TurnRuntime(evTurnId)
                            }
                            const rtDyn = (assistantMsg as any)._turnRt
                            if (rtDyn && !rtDyn.accepts(parsed)) continue
                        }
                        // 在收到 chat_turn_started / workflow_started 之前，忽略所有事件（防止旧消息 replay 污染新消息）
                        if (!newWorkflowStarted) {
                            if (_et === 'chat_turn_started' || _et === 'workflow_started') {
                                newWorkflowStarted = true
                                console.log('[ChatSSE] GATE OPENED by', _et)
                            } else {
                                console.log('[ChatSSE] DROPPED: gate not open, waiting for workflow_started', { type: _et })
                                continue
                            }
                        }
                        handleWorkflowEvent(parsed, assistantMsg, sessionId)
                        _lastEventAt = Date.now()
                        const _wsAfter = assistantMsg?.agentMeta?.workflowStatus
                        if (_PAUSE_STATUSES.has(_wsAfter || '')) {
                            _sawPauseBeforeClose = true
                            // 连接配额治理：暂停态后端不发终态事件、SSE 流会被无限期
                            // 挂住——多个暂停会话把浏览器每主机 6 连接配额吃光后，
                            // 图片生成等新请求全部排队（「没有任何反应」→「Failed to
                            // fetch」）。延迟复查后关流（见 schedulePauseClose）；
                            // 用户确认续跑时 resume 路径本就带 skipStartGate=true 重订。
                            schedulePauseClose()
                        }
                    } catch {
                        // ignore
                    }
                }
            }

            // 2026-11-08 连接配额治理配套：流被服务端正常关闭（done）但回合仍是
            // running → 这是异常断流（服务端空闲超时关流 / 网络闪断），不能当完成
            // 收尾。带 Last-Event-ID 重连续传（服务端历史回放），skipStartGate=true
            // ——闸门本回合已开过。终态/暂停态不受影响：暂停已 abort 走 catch，
            // 终态时 workflowStatus 已非 running，落到 finally 正常收尾。
            // 守卫：消息代已作废（epoch 变更）或闸门从未打开（纯旧 replay）的
            // 订阅是死订阅，静默清理即可，绝不重订/收尾。
            const _wsEnd = assistantMsg?.agentMeta?.workflowStatus
            if (_wsEnd === 'running' && retryCount < 2 && epoch === srt.epoch && newWorkflowStarted) {
                console.warn(`[ChatSSE] stream closed mid-turn (status=running), resubscribing (attempt ${retryCount + 1})`)
                await new Promise(r => setTimeout(r, 1500))
                return subscribeChatSSE(sessionId, assistantMsg, retryCount + 1, turnId, true)
            }
        } catch (err: any) {
            srt.markSseReady()
            if (err?.name === 'AbortError') return
            if (retryCount < 2) {
                const delay = 2000 * (retryCount + 1)
                console.warn(`[ChatSSE] connection lost, retrying in ${delay}ms (attempt ${retryCount + 1})`)
                await new Promise(r => setTimeout(r, delay))
                return subscribeChatSSE(sessionId, assistantMsg, retryCount + 1, turnId, skipStartGate)
            }
        } finally {
            if (_pauseCloseTimer) {
                clearTimeout(_pauseCloseTimer)
                _pauseCloseTimer = null
            }
            console.log('[ChatSSE] FINALLY block', {
                isCurrentController: srt.chatSseController.value === controller,
                epochStillCurrent: epoch === srt.epoch,
                workflowStatus: assistantMsg?.agentMeta?.workflowStatus,
                _sawPauseBeforeClose,
            })
            if (srt.chatSseController.value === controller) {
                srt.chatSseController.value = null
            }
            const epochStillCurrent = epoch === srt.epoch
            flushStreamDelta(assistantMsg)
            // 暂停等待用户时（clarification/guardian/storyboard），SSE 流可能因
            // 旧版后端仍发 workflow_completed 终态事件而关闭，但工作流并未真正
            // 完成——用户确认后后台续跑。此时不能调 onSseComplete，否则
            // _finishStream 会把 isStreaming 翻回 false，链路断开。
            // 用 _sawPauseBeforeClose 而非 workflowStatus 做守卫：
            // CLARIFICATION_ACK 事件会在 SSE 流关闭前将 workflowStatus 改为 'running'，
            // 导致基于 workflowStatus 的守卫失效。_sawPauseBeforeClose 是订阅级局部变量，
            // 只在事件循环中 set、finally 中 read，不会被 ACK 事件"提前修改"。
            // 这里必须读「本订阅会话自己的」运行时，不能读视图投影：
            // 多任务并行时后台会话收尾那一刻，用户可能正停在另一个会话上，
            // 那个会话的 workflowSseController 会把别人的收尾误挡掉，
            // 结果后台任务永远停在"思考中"。
            if (epochStillCurrent && callbacks.onSseComplete && !srt.workflowSseController.value && !_sawPauseBeforeClose) {
                callbacks.onSseComplete(sessionId)
            } else if (_sawPauseBeforeClose) {
                console.warn('[ChatSSE] FINALLY: skipping onSseComplete because _sawPauseBeforeClose=true (workflow was paused before stream close)')
            }
        }
    }

    async function subscribeWorkflowSSE(
        _workflowId: string,
        _assistantMsg: ChatMessage,
        _retryCount = 0
    ) {
        console.warn('[WorkflowSSE] workflow SSE subscription disabled (no workflow engine)')
    }

    async function _pollShortcutResult(sessionId: string, assistantMsg: ChatMessage) {
        const {authFetch} = await import('@/api/client')
        const _trans = (phase: TurnPhase, header?: string) => _dispatchTrans(phase, header, sessionId, assistantMsg)
        console.warn(`[PollFallback] SSE failed, polling session ${sessionId} for result`)
        for (let attempt = 0; attempt < 30; attempt++) {
            await new Promise(r => setTimeout(r, 3000))
            try {
                const res = await authFetch(`/api/chat/sessions/${sessionId}/messages?limit=1&role=assistant`)
                if (!res.ok) continue
                const data = await res.json()
                // 后端 StandardResponse: data 直接是消息数组；消息元数据字段为 agent_meta（下划线）
                const messages = Array.isArray(data?.data) ? data.data : (data?.data?.messages || data?.messages || [])
                const last = [...messages].reverse().find((m: any) => m.role === 'assistant')
                const meta = last?.agent_meta || last?.metadata || {}
                if (last && meta.workflow_status === 'completed') {
                    landContent(assistantMsg, last.content || '处理完成')
                    assistantMsg.agentMeta!.workflowStatus = 'completed'
                    assistantMsg.agentMeta!.totalPercent = 100
                    if (assistantMsg.agentMeta!.steps) {
                        for (const s of assistantMsg.agentMeta!.steps) {
                            s.status = 'completed'
                            s.percent = 100
                        }
                    }
                    _trans('done', '')
                    return
                }
                if (last && meta.workflow_status === 'awaiting_clarification') {
                    landContent(assistantMsg, last.content || '需要确认创作偏好')
                    assistantMsg.agentMeta!.workflowStatus = 'awaiting_clarification'
                    if (meta.loop_state_path) {
                        assistantMsg.agentMeta!.loopStatePath = meta.loop_state_path
                    }
                    if (meta.clarification_prompt) {
                        assistantMsg.agentMeta!.clarificationPrompt = meta.clarification_prompt
                    }
                    if (meta.clarification_skill) {
                        assistantMsg.agentMeta!.clarificationSkill = meta.clarification_skill
                    }
                    if (meta.clarification_batches) {
                        assistantMsg.agentMeta!.clarificationBatches = meta.clarification_batches
                    }
                    _trans('running', '等待偏好确认…')
                    return
                }
                if (last && meta.workflow_status === 'awaiting_confirmation') {
                    landContent(assistantMsg, last.content || '需要确认操作')
                    assistantMsg.agentMeta!.workflowStatus = 'awaiting_confirmation'
                    if (meta.loop_state_path) {
                        assistantMsg.agentMeta!.loopStatePath = meta.loop_state_path
                    }
                    if (meta.confirmation_prompt) {
                        assistantMsg.agentMeta!.confirmationPrompt = meta.confirmation_prompt
                    }
                    if (meta.confirmation_skill) {
                        assistantMsg.agentMeta!.confirmationSkill = meta.confirmation_skill
                    }
                    _trans('running', '等待确认…')
                    return
                }
                if (last && meta.workflow_status === 'error') {
                    landContent(assistantMsg, last.content || '处理出错')
                    assistantMsg.agentMeta!.workflowStatus = 'error'
                    assistantMsg.isError = true
                    _trans('error')
                    return
                }
            } catch {
            }
        }
        assistantMsg.content = '处理超时，请刷新页面查看结果'
        assistantMsg.agentMeta!.workflowStatus = 'error'
    }

    async function handleWorkflowEvent(event: any, assistantMsg: ChatMessage, sessionId?: string) {
        // 产物隔离：事件所属会话。显式传入优先，否则回落到当前活跃会话。
        const _sid = sessionId || activeSessionId.value || ''
        // 本函数内的转场 / 流式标记一律只作用于「这条消息所属的会话」
        const _trans = (phase: TurnPhase, header?: string) => _dispatchTrans(phase, header, _sid, assistantMsg)
        const _msgRt = () => getSessionRuntime(sessionIdOfMessage(assistantMsg, _sid))
        const eventType = event.type || event.event_type
        const payload = event.payload || {}
        const meta = assistantMsg.agentMeta
        if (!meta) return

        switch (eventType) {
            case Ev.CHAT_TURN_STARTED: {
                // 新一轮对话开始，标记 running 状态（chat_agent L825）
                meta.workflowStatus = 'running'
                meta.totalPercent = 0
                // 思考流分桶：本轮 turn_id 绑到消息上，时间线按轮隔离
                if (payload.turn_id && assistantMsg) assistantMsg.turnId = String(payload.turn_id)
                _trans('running', '思考中…')
                break
            }
            case Ev.WORKFLOW_STARTED: {
                // ReAct 循环启动（session L165）
                meta.workflowStatus = 'running'
                meta.totalPercent = 0
                _trans('running', '处理中…')
                break
            }
            case Ev.NODE_STARTED: {
                // reactor 节点开始（session L170）：补步骤 running 态。
                // 此前只接 node_completed → 步骤永远只有完成/等待两态，没有"进行中"。
                const nodeKey = payload.node_id || ''
                if (nodeKey) {
                    const step = meta.steps?.find(s => s.nodeKey === nodeKey)
                    if (step) {
                        step.status = 'running'
                        step.percent = 50
                    } else {
                        meta.steps?.push({
                            nodeKey,
                            nodeLabel: AGENT_NODE_LABELS[nodeKey] || nodeKey,
                            status: 'running',
                            percent: 50,
                        })
                    }
                    _recalcPercent(meta)
                    const {label} = getNodeDisplayLabel(nodeKey)
                    _trans('running', `${label}中…`)
                }
                break
            }
            case Ev.NODE_COMPLETED: {
                // reactor 节点完成（session L425）
                // 暂停等待用户时，node_completed 的 ended_reason 是 *_paused，
                // 不应触发 transitionTurn('running') 覆盖澄清/确认等待状态
                const _nodeEndedReason = payload.ended_reason
                if (_nodeEndedReason === 'clarification_paused' || _nodeEndedReason === 'guardian_paused' || _nodeEndedReason === 'storyboard_paused') {
                    break
                }
                const nodeKey = payload.node_id || ''
                const step = meta.steps?.find(s => s.nodeKey === nodeKey)
                if (step) {
                    step.status = 'completed'
                    step.percent = 100
                }
                _recalcPercent(meta)
                const nextRunning = meta.steps?.find(s => s.status === 'running')
                if (nextRunning) {
                    const {label} = getNodeDisplayLabel(nextRunning.nodeKey)
                    _trans('running', `${label}中…`)
                } else {
                    _trans('running', '处理中…')
                }
                break
            }
            case Ev.WORKFLOW_COMPLETED: {
                const _endedReason = payload.ended_reason || payload.output?.ended_reason
                console.warn('[ChatSSE] WORKFLOW_COMPLETED received', {
                    endedReason: _endedReason,
                    isPaused: _endedReason === 'clarification_paused' || _endedReason === 'guardian_paused' || _endedReason === 'storyboard_paused',
                    hasStreamPlayer: !!streamPlayers.get(assistantMsg),
                    contentLength: assistantMsg.content?.length,
                    workflowStatus: meta.workflowStatus,
                })
                if (_endedReason === 'clarification_paused' || _endedReason === 'guardian_paused' || _endedReason === 'storyboard_paused') {
                    console.warn('[ChatSSE] WORKFLOW_COMPLETED with pause ended_reason, skipping finishStream', _endedReason)
                    break
                }
                // BUG-007：截断/假完成不再静默当正常完成——记原因、给出可见提示
                meta.endedReason = _endedReason || 'completed'
                const _truncNotice = _truncationNotice(_endedReason)
                // 回合计时冻结：后端回传 duration_ms 优先，否则用前端沙箱计时
                const _rt = (assistantMsg as any)._turnRt
                if (_rt) _rt.end(payload.duration_ms)
                const _turnDuration = _rt ? (_rt.durationMs ?? _rt.elapsedMs) : null
                if (_turnDuration) meta.durationMs = _turnDuration
                meta.workflowStatus = 'completed'
                meta.totalPercent = 100
                _msgRt().streamingThinking.value = false
                // 占位正文优先被终态载荷的最终消息顶掉（output.summary/message）
                const _wcFinal = (typeof payload.message === 'string' && payload.message.trim())
                    || (typeof payload.output?.summary === 'string' && payload.output.summary.trim())
                    || (typeof payload.output?.message === 'string' && payload.output.message.trim())
                    || ''
                if (_wcFinal && _isPlaceholderContent(assistantMsg.content)) {
                    assistantMsg.content = sanitizeContent(_wcFinal)
                }
                // 等播放器落地再转 done/落库——状态不先于文字结束，
                // 落库拿到的是完整正文而非半段（数据态与渲染态分离）
                finishStream(assistantMsg, () => {
                    _trans('done', _truncNotice ? '⚠️ 任务被截断' : '')
                    if (callbacks.onContentReady) {
                        callbacks.onContentReady(_sid)
                    }
                    if (_truncNotice) {
                        assistantMsg.content = sanitizeContent(
                            (assistantMsg.content ? assistantMsg.content + '\n\n' : '') + _truncNotice
                        )
                    }
                    // 占位行不落库：后端已落 completed 正文，前端再落占位就是脏数据
                    if (callbacks.persistAssistantMessage && assistantMsg.content && !_isPlaceholderContent(assistantMsg.content) && !assistantMsg._persisted) {
                        assistantMsg._persisted = true
                        callbacks.persistAssistantMessage(assistantMsg.content, meta, _sid)
                    }
                })
                break
            }
            case Ev.WORKFLOW_ERROR: {
                // reactor/stream 端点 run() 异常（reactor.py L142）。
                // chat 通道（session.run 抛异常）不走本事件——流断由订阅
                // finally 的 onSseComplete 兜底。
                cancelStreamDelta(assistantMsg)
                meta.workflowStatus = 'error'
                assistantMsg.isError = true
                _msgRt().streamingThinking.value = false
                const failMsg = payload.message || payload.error || '处理失败'
                if (!assistantMsg.content || assistantMsg.content.length < 5) {
                    assistantMsg.content = sanitizeContent(`[error] ${failMsg}`)
                }
                _trans('error')
                if (callbacks.onContentReady) {
                    callbacks.onContentReady(_sid)
                }
                if (callbacks.persistAssistantMessage && !assistantMsg._persisted) {
                    assistantMsg._persisted = true
                    callbacks.persistAssistantMessage(assistantMsg.content, meta, _sid)
                }
                break
            }
            case Ev.CARD_DRAFT_READY: {
                // 卡片草稿就绪（produce skills）：{card_draft, png_urls, html_urls}
                try {
                    await _landCardDraft(_sid, payload, meta)
                } catch (e) {
                    console.error('[ChatSSE] card_draft_ready handling failed:', e)
                }
                break
            }
            case Ev.WORK_PATCH_READY: {
                // 草稿就地修改（work_edit）：{work_patch: {work_id, fields, applied}}
                try {
                    await _applyWorkPatch(_sid, payload, meta)
                } catch (e) {
                    console.error('[ChatSSE] work_patch_ready handling failed:', e)
                }
                break
            }
            case Ev.MEDIA_ARTIFACT_READY: {
                // 工作区媒体产物已发布到 outputs/：{media_artifacts, video_url}
                // 2026-11-08：口播成片由 bash/ffmpeg 写在工作区，此前只把路径写在
                // 文字里，前端没有任何可渲染地址——消息里看不到成片。这里把发布
                // 结果挂到 agentMeta，由消息卡片渲染 <video> 播放器。
                const items = Array.isArray(payload.media_artifacts)
                    ? payload.media_artifacts.filter((m: any) => m && typeof m.url === 'string' && m.url)
                    : []
                if (!items.length) break
                meta.mediaArtifacts = items
                break
            }
            case Ev.VIDEO_PIPELINE_PROGRESS: {
                // 视频管线进度（talking_head_overlay / auto_short_video）：
                // {skill, step, total, phase, label, detail, percent, turn_id, preview_url?}
                // 原位写入 meta.videoProgress（消息内一行式进度展示），
                // 并把当前阶段同步到状态头。auto_short_video 旧格式无 phase——
                // 直接透传，容错交给展示层。percent>=100 由展示层收尾。
                // preview_url：agent start_preview 后中途才出现（可选），
                // 原样透传，展示层据此渲染「实时预览」入口。
                if (typeof payload.percent !== 'number') break
                meta.videoProgress = {
                    skill: payload.skill || '',
                    step: payload.step,
                    total: payload.total,
                    phase: payload.phase,
                    label: payload.label || '',
                    detail: payload.detail || '',
                    percent: payload.percent,
                    preview_url: payload.preview_url,
                }
                const vpLabel = payload.label || '视频生成中'
                _trans('running', vpLabel)
                break
            }
            case Ev.TOOL_CALL_START: {
                // 工具调用开始（session L262）：
                // {node_id, iteration, tool_name, inputs}，另有 sub_agent_id /
                // sub_agent_name（2026-10-19：reactor 下发的子智能体身份）。
                // spawn_agent 此刻 agent_id 还没生成，只有真名 task_name，
                // 身份由 tool_call_end 用 dispatch 返回值补齐（见下）。
                if (!assistantMsg.toolCalls) assistantMsg.toolCalls = []
                const args = payload.inputs || {}
                const toolName = payload.tool_name || ''
                console.log('[ChatSSE] tool_call_start:', toolName, 'node:', payload.node_id)
                let argsStr = ''
                if (Object.keys(args).length > 0) {
                    if (toolName === 'spawn_agent') {
                        const tn = args.task_name || args.name || ''
                        const td = args.task_description || ''
                        const role = args.role || ''
                        const parts: string[] = []
                        if (tn) parts.push(`任务=${tn}`)
                        if (role) parts.push(`角色=${role}`)
                        if (td) parts.push(`描述=${td.length > 120 ? td.slice(0, 120) + '…' : td}`)
                        argsStr = parts.join(', ')
                    } else if (toolName === 'wait_agent') {
                        const aid = args.agent_id || ''
                        argsStr = aid ? `等待=${aid}` : ''
                    } else {
                        argsStr = Object.entries(args).map(([k, v]) => {
                            const val = typeof v === 'string' ? (v.length > 60 ? v.slice(0, 60) + '…' : v) : JSON.stringify(v)
                            return `${k}=${val}`
                        }).join(', ')
                    }
                }
                assistantMsg.toolCalls.push({
                    id: `tc_${Date.now()}`,
                    type: 'exec',
                    name: toolName,
                    arguments: args,
                    displayArgs: argsStr,
                    iteration: payload.iteration,
                    status: 'running',
                    subAgentId: payload.sub_agent_id || '',
                    subAgentName: payload.sub_agent_name || '',
                })
                // 工具开始调用 → 规划阶段结束，后续 delta 直接进 msg.content
                ;(assistantMsg as any)._planningMode = false
                const {label} = getToolDisplayLabel(payload.tool_name || '')
                _trans('running', `${label}中…`)
                break
            }
            case Ev.TOOL_CALL_END: {
                // 工具调用结束（session L376）：
                // {node_id, iteration, tool_name, success, summary, duration_ms}
                // 后端不发 result_data，target.result 落 summary 兜底。
                const endToolName = payload.tool_name || ''
                const endSubId = payload.sub_agent_id || ''
                let target = assistantMsg.toolCalls?.[assistantMsg.toolCalls.length - 1]
                if (target && target.name !== endToolName) {
                  target = [...(assistantMsg.toolCalls || [])].reverse().find(
                    (tc: ToolCall) => tc.name === endToolName && (tc.subAgentId || '') === endSubId && tc.status === 'running'
                  )
                }
                if (target) {
                    // 子智能体身份的权威值：dispatch 返回的 agent_id / name。
                    // 收敛时若两处都空才回退到 tool_call_start 的 sub_agent_id。
                    if (payload.sub_agent_id) target.subAgentId = payload.sub_agent_id
                    if (payload.sub_agent_name) target.subAgentName = payload.sub_agent_name
                    if (endSubId && !target.subAgentId) target.subAgentId = endSubId
                    if (payload.result_data !== undefined && payload.result_data !== null) {
                        try {
                            target.result = JSON.stringify(payload.result_data, null, 2)
                        } catch {
                            target.result = payload.summary || (payload.success ? '成功' : '失败')
                        }
                    } else {
                        target.result = payload.summary || (payload.success ? '成功' : '失败')
                    }
                    target.status = payload.success ? 'done' : 'error'
                    if (payload.duration_ms) target.durationMs = payload.duration_ms
                }
                break
            }
            case Ev.CLARIFICATION_REQUIRED: {
                // 偏好澄清闸门（session L140 pre-flight / L283 dispatch 门）：
                // {clarification_prompt, clarification_skill, clarification_batches, loop_state_path}
                const clMeta: any = assistantMsg.agentMeta || {
                    sessionId: null, steps: [], totalPercent: 0,
                    turnPhase: 'running', statusIndicator: {header: ''},
                }
                clMeta.workflowStatus = 'awaiting_clarification'
                if (payload.loop_state_path) clMeta.loopStatePath = payload.loop_state_path
                if (payload.clarification_prompt) clMeta.clarificationPrompt = payload.clarification_prompt
                if (payload.clarification_skill) clMeta.clarificationSkill = payload.clarification_skill
                if (payload.clarification_batches) clMeta.clarificationBatches = payload.clarification_batches
                assistantMsg.agentMeta = clMeta
                if (payload.clarification_prompt && !assistantMsg.content) {
                    assistantMsg.content = sanitizeContent(payload.clarification_prompt)
                }
                _msgRt().streamingThinking.value = false
                _trans('running', '等待偏好确认…')
                break
            }
            case Ev.CLARIFICATION_ACK: {
                console.warn('[ChatSSE] CLARIFICATION_ACK received', {
                    batchId: payload.batch_id,
                    action: payload.action,
                    workflowStatusBefore: assistantMsg.agentMeta?.workflowStatus,
                })
                const ackMeta: any = assistantMsg.agentMeta || {
                    sessionId: null, steps: [], totalPercent: 0,
                    turnPhase: 'running', statusIndicator: {header: ''},
                }
                ackMeta.clarificationAcked = true
                if (ackMeta.workflowStatus === 'awaiting_clarification') {
                    ackMeta.workflowStatus = 'running'
                }
                assistantMsg.agentMeta = ackMeta
                break
            }
            case Ev.STORYBOARD_REQUIRED: {
                // 分镜草稿待确认（session L344）：
                // {skill, title, shots, estimated_duration, loop_state_path}
                const sbMeta: any = assistantMsg.agentMeta || {
                    sessionId: null, steps: [], totalPercent: 0,
                    turnPhase: 'running', statusIndicator: {header: ''},
                }
                sbMeta.workflowStatus = 'awaiting_storyboard_confirmation'
                if (payload.loop_state_path) sbMeta.loopStatePath = payload.loop_state_path
                if (payload.shots) sbMeta.storyboardShots = payload.shots
                if (payload.title) sbMeta.storyboardTitle = payload.title
                if (payload.estimated_duration != null) sbMeta.storyboardDuration = payload.estimated_duration
                if (payload.skill) sbMeta.storyboardSkill = payload.skill
                assistantMsg.agentMeta = sbMeta
                if (!assistantMsg.content) {
                    assistantMsg.content = sanitizeContent(
                        payload.title ? `分镜草稿《${payload.title}》已生成，请确认后开始制作视频。` : '分镜草稿已生成，请确认后开始制作视频。'
                    )
                }
                _msgRt().streamingThinking.value = false
                _trans('running', '等待确认分镜…')
                break
            }
            case Ev.GUARDIAN_REQUIRED: {
                // Guardian 高危操作待确认（session L458）：
                // {tool_name, confirmation_prompt, risk_info, loop_state_path}
                const grMeta: any = assistantMsg.agentMeta || {
                    sessionId: null, steps: [], totalPercent: 0,
                    turnPhase: 'running', statusIndicator: {header: ''},
                }
                grMeta.workflowStatus = 'awaiting_guardian_confirmation'
                // 每次暂停都是一次"新的"确认：必须把上一轮的决策标记清掉。
                // 此前 guardianResolved 一旦写成 confirmed 就永久粘住，
                // 第二轮暂停时 AssistantMessageCell 的注册条件
                // （guardianResolved !== 'confirmed'）恒为假 → 不再弹确认卡，
                // 服务端在等批准、客户端却一个可点的东西都没有 → 死锁。
                grMeta.guardianResolved = undefined
                grMeta.guardianError = ''
                // 记下本回合 turn_id：批准续跑时重订阅要带上它，
                // 否则后端按 session 全量回放（上一回合事件混进来）。
                if (event.turn_id) grMeta.turnId = String(event.turn_id)
                if (payload.loop_state_path) grMeta.loopStatePath = payload.loop_state_path
                grMeta.confirmationPrompt = payload.confirmation_prompt || ''
                grMeta.riskInfo = payload.risk_info || ''
                grMeta.guardianToolName = payload.tool_name || ''
                assistantMsg.agentMeta = grMeta
                if (!assistantMsg.content) {
                    assistantMsg.content = sanitizeContent(
                        payload.risk_info
                            ? `⚠️ ${payload.risk_info}`
                            : '此操作需要您的确认才能继续。'
                    )
                }
                _msgRt().streamingThinking.value = false
                _trans('running', '等待确认操作…')
                break
            }
            case Ev.SIDE_WRITE_REQUIRED: {
                // 侧会话写副作用待确认（session，PRD G6/Q9）：
                // {tool_name, description, permissions, side_session_id, loop_state_path}
                const swMeta: any = assistantMsg.agentMeta || {
                    sessionId: null, steps: [], totalPercent: 0,
                    turnPhase: 'running', statusIndicator: {header: ''},
                }
                Object.assign(swMeta, applySideWriteRequired(swMeta as any, payload) as any)
                // 每次暂停都是一次新确认：清掉上一轮决策标记（对齐 guardian 死锁教训）
                swMeta.sideWriteResolved = undefined
                swMeta.sideWrite = applySideWriteRequired(swMeta as any, payload).sideWrite
                if (event.turn_id) swMeta.turnId = String(event.turn_id)
                assistantMsg.agentMeta = swMeta
                if (!assistantMsg.content) {
                    assistantMsg.content = sanitizeContent(
                        payload.description || '侧会话请求执行写操作，需要您的确认。'
                    )
                }
                _msgRt().streamingThinking.value = false
                _trans('running', '等待确认写操作…')
                break
            }
            case Ev.STORYBOARD_RESOLVED: {
                // 分镜确认回放结束（chat_agent L1322）：{action, ok?, error?}
                const rsMeta: any = assistantMsg.agentMeta || {
                    sessionId: null, steps: [], totalPercent: 0,
                    turnPhase: 'running', statusIndicator: {header: ''},
                }
                if (payload.ok) {
                    rsMeta.storyboardResolved = 'confirmed'
                } else {
                    rsMeta.storyboardResolved = 'failed'
                    if (payload.error) rsMeta.storyboardError = payload.error
                }
                assistantMsg.agentMeta = rsMeta
                if (payload.action === 'cancel') rsMeta.storyboardResolved = 'cancelled'
                break
            }
            case Ev.CUT_PLAN_REQUIRED: {
                // 叠加方案待确认（session，修复设计文档 3.4 #2 缺口2）：
                // {skill, plan, message, loop_state_path} — plan 为 OverlayPlan
                const cpMeta: any = assistantMsg.agentMeta || {
                    sessionId: null, steps: [], totalPercent: 0,
                    turnPhase: 'running', statusIndicator: {header: ''},
                }
                cpMeta.workflowStatus = 'awaiting_cut_plan_confirmation'
                if (event.turn_id) cpMeta.turnId = String(event.turn_id)
                if (payload.loop_state_path) cpMeta.loopStatePath = payload.loop_state_path
                cpMeta.cutPlan = { plan: payload.plan || {}, message: payload.message || '' }
                // 二次暂停必须能重新弹卡：清掉上一轮决策标记
                cpMeta.cutPlanResolved = undefined
                cpMeta.cutPlanError = ''
                assistantMsg.agentMeta = cpMeta
                if (!assistantMsg.content) {
                    assistantMsg.content = sanitizeContent(
                        payload.message || 'AI 口播叠加方案已规划完成，请确认后开始渲染。'
                    )
                }
                _msgRt().streamingThinking.value = false
                _trans('running', '等待确认叠加方案…')
                break
            }
            case Ev.CUT_PLAN_RESOLVED: {
                // 叠加方案确认回放结束（chat_agent confirm-cut-plan）：{action, ok?, error?}
                const crMeta: any = assistantMsg.agentMeta || {
                    sessionId: null, steps: [], totalPercent: 0,
                    turnPhase: 'running', statusIndicator: {header: ''},
                }
                if (payload.action === 'cancel') {
                    crMeta.cutPlanResolved = 'cancelled'
                } else if (payload.ok) {
                    crMeta.cutPlanResolved = 'confirmed'
                    // 设计文档 3.4 #4 版本条：渲染成功时记录本次成片版本
                    // （后端按历史待确认消息条数计算 version，随 SSE 载荷下发）
                    if (payload.version) {
                        if (!Array.isArray(crMeta.cutPlanVersions)) crMeta.cutPlanVersions = []
                        crMeta.cutPlanVersions.push({
                            version: payload.version,
                            assetUrl: payload.asset_url || undefined,
                        })
                    }
                } else {
                    crMeta.cutPlanResolved = 'failed'
                    if (payload.error) crMeta.cutPlanError = payload.error
                }
                assistantMsg.agentMeta = crMeta
                break
            }
            case Ev.AGENT_LOOP_DONE: {
                console.warn('[ChatSSE] AGENT_LOOP_DONE received', {
                    status: payload.status,
                    ended_reason: payload.ended_reason,
                    hasStreamPlayer: !!streamPlayers.get(assistantMsg),
                    contentLength: assistantMsg.content?.length,
                    workflowStatus: meta.workflowStatus,
                })
                const rt = (assistantMsg as any)._turnRt
                if (rt) rt.end()
                meta.workflowStatus = 'completed'
                meta.totalPercent = 100
                // BUG-007：resume 路径的 agent_loop_done 现在也带 ended_reason（chat_agent 三处发布点已补），
                // 截断/假完成同样给出可见提示，不再静默当正常完成
                meta.endedReason = payload.ended_reason || meta.endedReason || 'completed'
                const _loopTruncNotice = _truncationNotice(payload.ended_reason)
                _msgRt().streamingThinking.value = false
                const loopMsg = typeof payload.message === 'string' ? payload.message.trim() : ''
                // 2026-11-04 大哥反馈"最后回复是阻塞式整段蹦出"：恢复路径（guardian/
                // confirm/clarify resume）的终稿原先在 finishStream 回调里一次性赋
                // content。改走 playFinalText 播放器匀速播出（与 POST 路径
                // _playFinalAfterSettle 同款），落地/落库/转 done 移入 onDone。
                const _settleLoopDone = () => {
                    _trans('done', _loopTruncNotice ? '⚠️ 任务被截断' : '')
                    if (callbacks.onContentReady) callbacks.onContentReady(_sid)
                    if (_loopTruncNotice) {
                        assistantMsg.content = sanitizeContent(
                            (assistantMsg.content ? assistantMsg.content + '\n\n' : '') + _loopTruncNotice
                        )
                    }
                    // 占位行不落库（同 WORKFLOW_COMPLETED：后端已落 completed 正文）
                    if (callbacks.persistAssistantMessage && assistantMsg.content && !_isPlaceholderContent(assistantMsg.content) && !assistantMsg._persisted) {
                        assistantMsg._persisted = true
                        callbacks.persistAssistantMessage(assistantMsg.content, meta, _sid)
                    }
                }
                if (loopMsg && (assistantMsg.content || '').trim().length < loopMsg.length) {
                    playFinalText(assistantMsg, loopMsg, { onDone: _settleLoopDone })
                } else {
                    finishStream(assistantMsg, _settleLoopDone)
                }
                break
            }
            case Ev.AGENT_THINKING: {
                // 技能思考流（copywrite builder L357）。
                // 载荷真相：{node_id, chunk: {content}}——不是 reasoning！
                // 旧代码读 payload.reasoning 恒为空，整个 case 是死的。
                const thinkText = payload.chunk?.content || payload.reasoning || ''
                if (thinkText) {
                    pushReasoningDelta(assistantMsg, '\n' + thinkText)
                    const _preview = _reasoningPreview(assistantMsg)
                    const boldMatch = _preview.match(/\*\*(.+?)\*\*/)
                    if (boldMatch) {
                        _trans('running', boldMatch[1])
                    } else {
                        _trans('running', getThinkingPreview(_preview))
                    }
                }
                break
            }
            case Ev.REASONING_SUMMARY_DELTA: {
                // 思考增量（session L543）：{node_id, delta}，独立播放器 thinkCps 匀速
                const delta = payload.delta || ''
                if (delta) {
                    pushReasoningDelta(assistantMsg, delta)
                    const _preview = _reasoningPreview(assistantMsg)
                    const boldMatch = _preview.match(/\*\*(.+?)\*\*/)
                    if (boldMatch) {
                        _trans('running', boldMatch[1])
                    } else {
                        _trans('running', getThinkingPreview(_preview))
                    }
                }
                break
            }
            case Ev.REASONING_COMPLETED: {
                // 思考终止（session L203/560）。
                // 载荷带 summary_parts: string[]（完整思考文本）——旧代码只塞
                // 占位文案「思考完成」，delta 丢帧时用户看不到思考内容。
                // 2026-11-08 卡顿修复：不再瞬间 settle。后端 proto 协议门会把
                // 整段思维链扣发到生成结束才一次性补发（session.py L1627-1675），
                // nl 流式路径也有持续积压——此刻积压可能数千字，settle 即整段蹦出
                // （用户反馈「思考链不流畅」）。改 markFinished 让播放器按收尾档
                // 匀速追完；仅 rAF 循环未运行（后台标签页）才兜底全量。
                const _rp = reasoningPlayers.get(assistantMsg)
                const _rpRawLen = _rp ? _rp.engine.raw.length : 0
                _msgRt().streamingThinking.value = false
                const parts: string[] = Array.isArray(payload.summary_parts) ? payload.summary_parts : []
                const summary = parts.filter(p => typeof p === 'string' && p.trim()).join('\n').trim()
                if (summary && _rp && _rpRawLen < summary.length) {
                    // summary 是权威全量且播放器缺料（delta 丢帧/终态补发尾段）：
                    // 补喂差值续播，保持匀速落地
                    _rp.engine.feed(summary.slice(_rpRawLen))
                } else if (summary && !_rp && (!assistantMsg.reasoning || assistantMsg.reasoning.trim().length < summary.length)) {
                    assistantMsg.reasoning = summary
                }
                if (_rp) {
                    _rp.engine.markFinished()
                    if (_rp.raf == null) _flushReasoningPlayer(assistantMsg)
                } else if ((!assistantMsg.reasoning || assistantMsg.reasoning.trim().length === 0) && _rpRawLen === 0) {
                    assistantMsg.reasoning = '思考完成'
                }
                break
            }
            case Ev.AGENT_MESSAGE_DELTA: {
                // 正文增量：{node_id, delta}
                // 2026-10-19：reactor 主循环已停止把协议 JSON 走本通道
                // （session.run 调 _call_llm 时 emit_delta=False，根因见
                // agent-output-formatter.isReactProtocolJson 注释）。本 case
                // 保留给「输出即用户散文」的合法发射方（显式 emit_delta=True），
                // 以及未来技能的正文流；reactor 自身不再产生本事件。
                const delta = payload.delta || ''
                if (!delta) break
                pushStreamDelta(assistantMsg, delta)
                break
            }
            case Ev.DECISION_MADE: {
                // 决策/thought（session L223 / chat_agent L707）：{iteration, decision}
                const decision = payload.decision || ''
                if (decision) {
                    if (!assistantMsg.decisions) assistantMsg.decisions = []
                    assistantMsg.decisions.push(decision)
                    const boldMatch = decision.match(/\*\*(.+?)\*\*/)
                    if (boldMatch) {
                        _trans('running', boldMatch[1])
                    } else {
                        const header = decision.length > 40 ? decision.slice(0, 37) + '…' : decision
                        _trans('running', header)
                    }
                }
                break
            }
            case Ev.PROGRESS_UPDATE: {
                // 推理轮次进度（session L189）：{current, total, label}
                const label = payload.label || ''
                if (label) {
                    if (!assistantMsg.progress) assistantMsg.progress = []
                    assistantMsg.progress.push(label)
                }
                break
            }
            case Ev.LLM_RETRY_STATUS: {
                // LLM 重试进度（session _call_llm）：{attempt, total?, delay?, final?, label}
                // 模型服务连接失败重试期间把状态透出到回合头部，避免用户端静默假死
                // （2026-11-07「帮我分析视频 3 分钟无回复」修复）。
                const retryLabel = payload.label || ''
                if (retryLabel) {
                    _trans('running', retryLabel)
                }
                break
            }
            case PostResult.SHORTCUT_COMPLETED: {
                // POST /api/chat/shortcut 响应（非 SSE，useChatAgent 分支）：
                // {message?, wechat_push?, card_draft?, png_urls?, html_urls?}
                cancelStreamDelta(assistantMsg)
                const _rtSc = (assistantMsg as any)._turnRt
                if (_rtSc) _rtSc.end()
                let content = payload.message || '已完成'
                if (payload.wechat_push) {
                    if (payload.wechat_push === 'sent') {
                        content += '\n\n[done] 已推送到微信'
                    } else if (payload.wechat_push.startsWith('skipped:')) {
                        content += `\n\n[warn] 微信推送跳过：${payload.wechat_push.slice(8)}`
                    } else if (payload.wechat_push.startsWith('error:')) {
                        content += `\n\n[error] 微信推送失败：${payload.wechat_push.slice(6)}`
                    }
                }
                meta.workflowStatus = 'completed'
                meta.totalPercent = 100
                if (meta.steps) {
                    for (const s of meta.steps) {
                        s.status = 'completed'
                        s.percent = 100
                    }
                }
                _trans('done', '')
                // 同 workflow_completed：终态文本比 delta 累积更短时，保留更完整的 delta 累积结果
                const _p = streamPlayers.get(assistantMsg)
                const _accumulated = _p ? _p.engine.raw : ''
                const _finalText = sanitizeContent(_stripThinkBlocks(content))
                if (_finalText.length >= _accumulated.length || !_accumulated) {
                    assistantMsg.content = _finalText
                }
                ;(assistantMsg as any)._contentFromSSE = true
                if (callbacks.onContentReady) {
                    callbacks.onContentReady(_sid)
                }
                if (payload.card_draft && payload.card_draft.pages && payload.card_draft.pages.length > 0) {
                    try {
                        await _landCardDraft(_sid, payload, meta)
                    } catch (e) {
                        console.error('[ChatSSE] shortcut_completed card_draft handling failed:', e)
                    }
                }
                break
            }
        }
    }

    return {
        workflowSseController,
        workflowSseLastEventId,
        chatSseController,
        activeWorkflowId,
        activeSessionId,
        subscribeChatSSE,        subscribeWorkflowSSE,
        handleWorkflowEvent,
        waitForChatSseReady,
        flushStreamDelta,
        // 渲染层 L3 出口：正文/思考 delta 灌入同一播放引擎，
        // finishStream 等落地再收尾，playFinalText 播 POST 一次性全文
        pushStreamDelta,
        pushReasoningDelta,
        finishStream,
        playFinalText,
        // 丢弃协议 JSON 污染残留（停播 + 空引擎重建），供 POST 分支兜底用
        discardStreamContent,
        // 递增消息代，在新消息发送前调用，防止旧 SSE replay 事件污染新消息
        // 只作废目标会话的旧订阅，别的会话照常跑
        advanceMsgEpoch,
        getChatSseLastEventId: (sid?: string | null) => getSessionRuntime(sid || activeSessionId.value).chatSseLastEventId.value,
        resetChatSseLastEventId: (sid?: string | null) => { getSessionRuntime(sid || activeSessionId.value).chatSseLastEventId.value = '' },
    }
}