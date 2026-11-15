<template>
  <div class="rl-assistant-row group">
    <!-- ═══ BrandHeader: logo + 智能体名称 ═══
         logo 用 <img> 引用 public 静态资源：浏览器异步解码 + 全局缓存，流式输出零阻塞。
         切勿改为内联 SVG 组件（1329 path 会进 JS bundle，且每条消息复制一份 DOM 节点）。 -->
    <!-- ═══ BrandHeader（原型 #msgCard 首行）═══
         logo 26×26 / radius 7 / 名称 12px·500·text-secondary / 状态 pill /
         右侧 margin-left:auto 常驻本轮耗时（11px·text-tertiary·tabular-nums + 11×11 时钟）。
         形态要点第一条：「品牌头常驻本轮耗时」——耗时不再下移到正文上方。 -->
    <div class="rl-brand">
      <!-- 品牌名「Pulse」已按大哥要求移除（2026-11-04）：品牌头只剩行尾耗时 -->
      <span v-if="brandDurVisible" class="rl-brand-dur">
        <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
          <circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2" stroke-linecap="round"/>
        </svg>
        <span>{{ brandDurText }}</span>
      </span>
    </div>
    <!-- 回合隔离可视化：调试信息，默认对用户隐藏。
         开发者开关：localStorage.setItem('rl_debug_turn', '1') 后刷新可见。 -->
    <div v-if="turnDebugVisible && turnRtDropped > 0" class="rl-turn-chip">
      turn {{ turnShortId }} · 已拦截 {{ turnRtDropped }} 条旧回合事件 · 回合隔离已生效
    </div>

    <!-- DecisionCell（决策记录）渲染已按大哥指示移除（2026-10-20）：
         decision_made 数据仍在收集（msg.decisions），待思考块数据管道打通后
         融合进 Claude 式混合时间线，届时复用。 -->

    <!-- ProgressCell 已移除：原型 progCell 只有一行无边框状态（文案 + 百分比），
         由下方 AgentProgressCard 承担；旧 .rl-cell-progress-inline 与它是同一份
         progress 数据的第二处渲染，且与 ToolCallCell 轨迹三方同轨。 -->

    <!-- ═══ ErrorCell: 独立渲染错误（对齐 Codex ErrorCell）═══ -->
    <div v-if="msg.errors && msg.errors.length > 0" class="rl-cell rl-cell-errors">
      <div class="rl-cell-header" @click="toggleExpand(msg, 'errors')">
        <AlertTriangle :size="14" class="rl-cell-icon" />
        <span class="rl-cell-label">错误 ({{ msg.errors.length }})</span>
        <span class="rl-cell-chevron" :class="{ 'is-open': isExpanded(msg, 'errors') }">▾</span>
      </div>
      <div class="rl-cell-grid" :style="{ gridTemplateRows: isExpanded(msg, 'errors') ? '1fr' : '0fr' }">
        <div class="rl-cell-grid-clip">
          <div class="rl-cell-body">
            <div v-for="(e, ei) in msg.errors" :key="ei" class="rl-cell-error-item">{{ e }}</div>
          </div>
        </div>
      </div>
    </div>

    <!-- ═══ 思考时间线（真身 AssistantThinkingChain 1:1 · 三层架构）═══
       ① useThoughtEvents 归一化层：toolCalls/decisions/reasoning → ThoughtEvent[]
       ② ThinkingChain.vue 通用渲染器：导轨+图标行+逐行 shimmer+Detail 展开
       ③ 兼容保证：未知事件走 fallback 行，新技能零前端改动 -->
    <div v-if="isThisStreaming || thoughtEvents.length > 0" class="rl-tc-wrap">
      <ThinkingChain :turn-id="msg.turnId" :streaming="isThisStreaming" />
    </div>

    <!-- ═══ 视频管线进度（video_pipeline_progress，口播增强/短视频链路）═══
         一行式，对齐 AgentProgressCard（rl-ap-*）视觉语言：无边框无底色，
         左侧当前阶段（含 step/total），右侧百分比 tabular-nums。
         percent>=100 即完成，收尾不占位——成片展示由后续版本条/作品面板承担。
         设计文档 3.4 #3：该行同时被非口播场景（auto_short_video）复用，
         口播态（videoStage 非空）下隐藏，进度改由 VideoTaskCard 容器卡承载。 -->
    <div v-if="videoProgressRow && !videoStage" class="rl-vp-line" :title="videoProgressRow.detail">
      <span class="rl-vp-label">
        <template v-if="videoProgressRow.step && videoProgressRow.total">
          AI 口播增强 · {{ videoProgressRow.label }}（{{ videoProgressRow.step }}/{{ videoProgressRow.total }}）
        </template>
        <template v-else>AI 口播增强 · {{ videoProgressRow.label }}</template>
      </span>
      <span class="rl-vp-percent">{{ videoProgressRow.percent }}%</span>
    </div>



    <!-- ═══ AgentMarkdownCell: 正式回复内容（对齐 Codex AgentMessageCell）═══ -->
    <!--
      三态渲染（对齐 Codex 渲染态/数据态解耦）：
      - preview (streaming): _streamHtml = stableHtml + renderMarkdown(tail)，粗糙但快，服务于感知进度
      - settling: 计算落地 HTML，同帧赋值，无闪烁
      - settled (completed): _streamHtml = settledHtml = renderMarkdown(fullContent)，完整、确定、值得信任
    -->
    <template v-if="msg.agentMeta?.workflowStatus !== 'awaiting_confirmation' && msg.agentMeta?.workflowStatus !== 'awaiting_clarification' && msg.agentMeta?.workflowStatus !== 'awaiting_guardian_confirmation' && msg._dualStream">
      <!-- 双通道流式渲染（对齐 Claude Code blit / Codex stable-tail）：
           稳定区 v-html 字符串不变时 Vue 跳过 patch，DOM 子树原地保留；
           每帧真正重建的只有尾部节点——等效"未变区域不重绘"。 -->
      <div
        class="rl-assistant-content"
        :class="{
          'rl-streaming-light': isStreamingSelf,
          'is-aura': isStreamingSelf
        }"
        @click="onContentClick"
      >
        <div v-if="msg._stableHtml" class="rl-md rl-md-stable" v-html="msg._stableHtml"></div>
        <!-- 尾部容器始终渲染（流式中）：tailHtml 为空时用 :empty 兜底显示呼吸光标 -->
        <div class="rl-md rl-md-tail" v-html="msg._tailHtml"></div>
      </div>
    </template>
    <div
      v-else-if="msg.agentMeta?.workflowStatus !== 'awaiting_confirmation' && msg.agentMeta?.workflowStatus !== 'awaiting_clarification' && msg.agentMeta?.workflowStatus !== 'awaiting_guardian_confirmation'"
      class="rl-assistant-content"
      :class="{
        'rl-streaming-light': isStreamingSelf,
        'is-aura': isStreamingSelf,
        'rl-md-settled': !isDualStream(msg)
      }"
      v-html="getSafeStreamHtml(msg)"
      @click="onContentClick"
    ></div>

    <!-- ═══ 设计文档 3.4 #3：四阶段容器卡（方案→进度→成片→版本）═══
         一张卡原位状态切换，替代旧的 dock 确认卡 + 独立进度行 + 独立版本条；
         任一口播状态（cutPlan / videoProgress / cutPlanVersions）出现即渲染。
         确认/取消/让 AI 改 事件转发给下方 onCutPlan* 三个 handler 回写 meta。 -->
    <VideoTaskCard
      v-if="videoStage"
      :meta="msg.agentMeta!"
      :session-id="sseSessionId"
      :turn-id="msg.agentMeta?.turnId"
      @confirmed="onCutPlanConfirmed"
      @cancelled="onCutPlanCancelled"
      @revise="(comment: string) => onCutPlanRevise(msg, comment)"
    />


    <!-- ═══ CollabPanel: 子智能体协作面板（rl- 令牌，与执行轨迹同一视觉语言）═══ -->
    <template v-if="ctx.isCodexMode.value">
      <div v-if="msg.diffFile" class="rl-diff">
        <div class="rl-diff-line" @click="ctx.toggleDiff(idx)">
          <span class="rl-diff-file">{{ msg.diffFile }}</span>
          <span class="rl-diff-stats">
            <span class="rl-diff-add">+{{ msg.diffAddCount || 0 }}</span>
            <span class="rl-diff-del">-{{ msg.diffDelCount || 0 }}</span>
          </span>
          <span class="rl-diff-toggle">{{ ctx.isDiffExpanded(idx) ? '收起' : '展开' }}</span>
        </div>
        <Transition name="rl-slide">
          <div v-show="ctx.isDiffExpanded(idx) && msg.diffContent" class="rl-diff-body">
            <pre class="rl-diff-output" v-html="renderDiff(msg.diffContent || '')"></pre>
          </div>
        </Transition>
      </div>

      <div v-if="msg.isError" class="rl-error-row">
        <button class="rl-retry-btn" @click="ctx.retryLastMessage">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>
          重试
        </button>
      </div>
    </template>

    <!-- 旧位置（正文之后）的 AgentProgressCard 已上移到轨迹之后，见上方 progCell -->

    <div
      v-if="msg.agentMeta?.workflowStatus === 'awaiting_confirmation' && msg.agentMeta?.loopStatePath"
      class="rl-guardian-wait"
    >
      <ShieldAlert :size="14" class="rl-guardian-wait-icon" />
      <span>等待确认 {{ msg.agentMeta?.confirmationSkill || '操作' }}…</span>
    </div>
    <div
      v-else-if="msg.agentMeta?.workflowStatus === 'awaiting_confirmation' || msg.agentMeta?.workflowStatus === 'confirmation_expired'"
      class="rl-confirm-lost"
    >
      <AlertTriangle :size="16" class="rl-confirm-lost-icon" />
      <div class="rl-confirm-lost-body">
        <p class="rl-confirm-lost-title">有一个操作等待确认，但确认状态已失效</p>
        <p class="rl-confirm-lost-msg">{{ msg.agentMeta.confirmationPrompt || msg.content || '请重新发起该操作' }}</p>
        <button class="rl-confirm-lost-btn" @click="ctx.sendMessage">重新发起</button>
      </div>
    </div>

    <!-- 2026-11-04：awaiting_clarification 的消息内占位行/失效卡已整体移除——
         澄清交互统一由 dock 里的 ChatClarificationCard 承接（大哥实锤：消息内
         两块与 dock 活卡三卡同屏、占位行与失效卡还互相打架）。历史消息里的
         awaiting_clarification 会被 mergeLoadedHistory 降级为 completed，不留鬼影。 -->
    <div
      v-if="msg.agentMeta?.workflowStatus === 'awaiting_storyboard_confirmation'
        && msg.agentMeta?.loopStatePath
        && msg.agentMeta?.storyboardResolved !== 'confirmed'
        && msg.agentMeta?.storyboardResolved !== 'cancelled'"
      class="rl-guardian-wait"
    >
      <ShieldAlert :size="14" class="rl-guardian-wait-icon" />
      <span>等待确认分镜：{{ msg.agentMeta?.storyboardTitle || '草稿已生成' }}…</span>
    </div>

    <!-- guardian 确认：弹窗在输入框上方渲染（confirmRequest store），此处只留等待行 -->
    <div
      v-if="msg.agentMeta?.workflowStatus === 'awaiting_guardian_confirmation'
        && msg.agentMeta?.loopStatePath
        && msg.agentMeta?.guardianResolved !== 'confirmed'
        && msg.agentMeta?.guardianResolved !== 'rejected'"
      class="rl-guardian-wait"
    >
      <ShieldAlert :size="14" class="rl-guardian-wait-icon" />
      <span>等待确认执行 {{ msg.agentMeta?.guardianToolName || '操作' }}…</span>
    </div>

    <!-- 侧会话写副作用确认（PRD G6/Q9）：逐次生效，弹窗内完成批准/拒绝 -->
    <SideWriteConfirmDialog
      v-if="msg.agentMeta?.workflowStatus === 'awaiting_side_write_confirmation'
        && deriveSideWriteVisible(msg.agentMeta)"
      :agent-meta="msg.agentMeta"
      @resolved="onSideWriteResolved"
    />
    <div
      v-else-if="msg.agentMeta?.workflowStatus === 'awaiting_side_write_confirmation'"
      class="rl-guardian-wait"
    >
      <ShieldAlert :size="14" class="rl-guardian-wait-icon" />
      <span>侧会话写操作{{ msg.agentMeta?.sideWriteResolved === 'confirmed' ? '已批准，执行中…' : msg.agentMeta?.sideWriteResolved === 'rejected' ? '已拒绝，只读继续…' : '待确认…' }}</span>
    </div>
    <!-- 2026-11-04：awaiting_clarification 的「确认状态已失效」卡已移除（同上，
         交互统一走 dock 活卡；过期场景由 dock 卡自身的 404 过期文案兜底） -->

    <ChatCardDraft
      v-if="msg.agentMeta?.cardDraft && (msg.agentMeta.cardDraft.pages?.length > 0 || msg.agentMeta.cardDraft.pngUrls?.length > 0)"
      :card-draft="msg.agentMeta.cardDraft"
      :work-id="msg.agentMeta.cardDraftWorkId"
      @open-studio="onOpenStudio"
      @saved-draft="onCardDraftSaved"
      @preview-sidebar="onPreviewSidebar"
    />

    <!-- 本轮生成并发布到 outputs/ 的媒体产物（口播成片等）：此前只把文件路径
         写在文字里，用户「生成了但看不见」，这里直接给可播放的地址 -->
    <ChatMediaArtifacts
      v-if="msg.agentMeta?.mediaArtifacts?.length"
      :artifacts="msg.agentMeta.mediaArtifacts"
    />

    <div v-if="ctx.isChatMode.value && msg.isError" class="rl-chat-error">
      <AlertTriangle :size="14" /> <span>处理出现问题，可以重试或换个说法</span>
    </div>

    <!-- ═══ actions（原型 #actions）═══
         margin-top:12px / gap:8px，按钮 12px·padding 5px·12px·radius 8px·
         border .5px border-secondary·透明底，hover 填 bg-secondary。
         原型是「有帮助 / 复制 / 重新生成」三个文字按钮，本处多一个「无帮助」——
         负反馈需带维度归因落库（结果不对/太慢/格式乱/其他），是有业务价值的信号，
         不能为了形态一致丢掉；样式与其余三个完全一致。 -->
    <div
      v-if="msg.content && msg !== ctx.streamingMsg.value && !isAwaitingUserAction"
      class="rl-msg-actions"
    >
      <div class="rl-feedback-wrap">
        <button
          class="rl-act-btn"
          :class="{ 'is-on': feedbackState === 'like' }"
          title="有帮助"
          @click="toggleFeedback('like')"
        ><ThumbsUp :size="15" /></button>
        <button
          class="rl-act-btn"
          :class="{ 'is-on': feedbackState === 'dislike' }"
          title="无帮助"
          @click="toggleFeedback('dislike')"
        ><ThumbsDown :size="15" /></button>
        <!-- ═══ 点踩维度选择弹层 ═══ -->
        <div v-if="showDislikeDimensions" class="rl-dislike-dimensions">
          <span class="rl-dislike-dimensions-title">哪里不满意？</span>
          <div class="rl-dislike-dimension-options">
            <button
              v-for="dim in DISLIKE_DIMENSIONS"
              :key="dim"
              class="rl-dislike-dimension-btn"
              @click="submitDislikeWithDimension(dim)"
            >{{ dim }}</button>
          </div>
          <button class="rl-dislike-cancel" @click="showDislikeDimensions = false">取消</button>
        </div>
      </div>
      <button class="rl-act-btn" :class="{ 'is-copied': isCopied }" :title="isCopied ? '已复制' : '复制'" @click="onCopyMessage">
        <Check v-if="isCopied" :size="15" class="rl-copy-check" />
        <Copy v-else :size="15" />
      </button>
      <button class="rl-act-btn" title="重新生成" @click="onRetryClick">
        <RefreshCw :size="15" />
      </button>
      <span v-if="msg.timestamp" class="rl-act-time">{{ formatFullTime(msg.timestamp) }}</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, defineAsyncComponent, onUnmounted, ref, watch } from 'vue'
import { useChatRenderContext } from './chat-context'
import type { ChatMessage } from './cell-types'
import { getToolDisplayLabel, deriveSideWriteVisible } from './cell-types'
import SideWriteConfirmDialog from './SideWriteConfirmDialog.vue'
import { renderAssistantHtml, wrapStreamHtml } from './chat-content-renderer'
import { createFeedbackEvent, deleteFeedbackEvent } from '@/api/feedback'
import { diceSimilarity as textSimilarity } from '@/render/similarity'
import { AlertTriangle, Check, CheckCircle2, ChevronDown, Copy, Lightbulb, RefreshCw, ThumbsDown, ThumbsUp } from 'lucide-vue-next'
import ThinkingChain from './ThinkingChain.vue'
import { requestConfirmation, clearConfirmationForSession } from './confirmRequest'
import { useThoughtStream } from './thoughtStream'
import ThinkActivityIndicator from './ThinkActivityIndicator.vue'
import ChatStoryboardConfirmCard from './ChatStoryboardConfirmCard.vue'
import ChatGuardianConfirmCard from './ChatGuardianConfirmCard.vue'
import VideoTaskCard, { deriveVideoStage } from './VideoTaskCard.vue'

// 异步组件（进度卡 / 卡片草稿）——模板使用，缺失会导致 Vue 解析失败不渲染
const AgentProgressCard = defineAsyncComponent(() => import('./AgentProgressCard.vue'))
const ChatCardDraft = defineAsyncComponent(() => import('./ChatCardDraft.vue'))
const ChatMediaArtifacts = defineAsyncComponent(() => import('./ChatMediaArtifacts.vue'))

const props = defineProps<{
  msg: ChatMessage
  idx: number
  sseSessionId: string
}>()

const ctx = useChatRenderContext()

const feedbackState = ref<'like' | 'dislike' | null>(null)
const likeCount = ref(0)
const isReasoningExpanded = ref(false)
// ── Claude ThinkingAccordion 互斥锁（逆向参照 ThinkingAccordion.tsx）──
// 生成中强制展开并剥夺折叠权限（防流式输出期间视口剧烈跳动），
// 完成后折叠权交还用户；展开动画用 CSS Grid 0fr↔1fr 消除高度抖动。
const isThinkOpen = computed(() => isCurrentlyStreamingThinking.value || isReasoningExpanded.value)
// 时间线正文溢出与 Show more（对齐真实 isOverflowing/isBodyExpanded）
const thinkBodyExpanded = ref(false)
const thinkOverflowing = computed(() => (props.msg.reasoning || '').length > 350)
function toggleThink() {
  if (isCurrentlyStreamingThinking.value) return
  isReasoningExpanded.value = !isReasoningExpanded.value
}
// 思考耗时（对齐真实 "Thought for X.Xs (Nms)"）：reasoning 首现 → 思考流结束
const thinkStartAt = ref<number | null>(null)
const thinkElapsedMs = ref<number | null>(null)
watch(() => !!props.msg.reasoning, (has) => {
  if (has && thinkStartAt.value === null) thinkStartAt.value = performance.now()
})
// Copy as quote（对齐真实 handleCopy：整段转引用格式，2s 反馈）
const thinkCopied = ref(false)
function copyThinkQuote() {
  const t = props.msg.reasoning || ''
  navigator.clipboard.writeText('> ' + t.split('\n').join('\n> '))
  thinkCopied.value = true
  setTimeout(() => { thinkCopied.value = false }, 2000)
}
// ── 思考/回复去重（问题 2 前端侧）──
// 模型把 answer 先写进 reasoning 再原样作为 content 吐出时，两侧文本高度重复。
// 流式过程中不判定（reasoning 还在生长）；落地后按字符二元组相似度折叠。
const reasoningDupScore = computed(() => {
  const r = props.msg.reasoning || ''
  const c = props.msg.content || ''
  if (!r || !c || r.length < 20 || c.length < 20) return 0
  if (isCurrentlyStreamingThinking.value) return 0
  return textSimilarity(r, c)
})
const reasoningDeduped = computed(() => reasoningDupScore.value >= 0.85)
const reasoningDupPct = computed(() => Math.round(reasoningDupScore.value * 100) + '%')
// 判定为重复时自动折叠（用户仍可手动展开查看）
watch(reasoningDeduped, (v) => { if (v) isReasoningExpanded.value = false })

// ── 侧会话写副作用确认（PRD G6/Q9）────────────────────────
// 弹窗内批准/拒绝已各自调用 /write-confirm 端点；这里只回写 agentMeta，
// 让等待行文案切换（已批准执行中 / 已拒绝只读继续）且弹窗退场。
function onSideWriteResolved(action: 'approve' | 'reject') {
  if (props.msg.agentMeta) {
    props.msg.agentMeta = {
      ...props.msg.agentMeta,
      sideWriteResolved: action === 'approve' ? 'confirmed' : 'rejected',
    }
  }
}

async function onOpenStudio(workId?: string) {
  const { useWorkStore } = await import('@/stores/work')
  const workStore = useWorkStore()
  workStore.showWorkDetail = true
}

function onCardDraftSaved(draft: any) {
  if (props.msg.agentMeta) {
    props.msg.agentMeta = { ...props.msg.agentMeta, cardDraft: draft }
  }
}

// 「侧边栏预览」→ 右侧 ContextSidebar 打开（ChatView 提供的 openCardSidebar → linkWork）
function onPreviewSidebar(workId: string) {
  ctx.openCardSidebar?.(workId)
}

// ── 分镜确认（确认闸门的前端侧）────────────────────────────
// 确认/取消都走后端 /confirm-storyboard 确定性回放，卡片自己发请求；
// 这里只负责把状态回写到 agentMeta，让卡片退场或保留可重试。

function _patchStoryboardMeta(patch: Record<string, any>) {
  if (!props.msg.agentMeta) return
  props.msg.agentMeta = { ...props.msg.agentMeta, ...patch }
}

function onStoryboardConfirmed(data?: any) {
  _patchStoryboardMeta({
    storyboardResolved: 'confirmed',
    storyboardError: '',
  })
  // 成片由技能自己的 card_draft_ready 推到 workStore，这里只提示用户去哪看
  if (data?.output?.asset_url) {
    showToast('视频已生成，正在打开作品面板')
  }
}

function onStoryboardCancelled() {
  _patchStoryboardMeta({ storyboardResolved: 'cancelled', storyboardError: '' })
}

// ── Guardian 确认（高危操作确认闸门的前端侧）────────────────────

function _patchGuardianMeta(patch: Record<string, any>) {
  if (!props.msg.agentMeta) return
  props.msg.agentMeta = { ...props.msg.agentMeta, ...patch }
}

function onGuardianConfirmed() {
  // 批准后不能只写个标记了事：必须把流状态从"暂停"接回"运行"，
  // 并在旧 SSE 已断开时重订阅，否则续跑输出无处可落（界面永远"思考中"）。
  _patchGuardianMeta({ guardianResolved: 'confirmed', guardianError: '' })
  if (typeof ctx.onChatConfirmed === 'function') {
    ctx.onChatConfirmed(props.msg, 'approve')
  }
}

function onGuardianRejected() {
  // 只清本会话的卡：别的会话可能同时在等确认，不能被顺手抹掉
  clearConfirmationForSession(props.sseSessionId)
  _patchGuardianMeta({ guardianResolved: 'rejected', guardianError: '' })
}

function onStoryboardRevise(msg: ChatMessage, comment: string) {
  // 让 AI 重编：把用户意见带回对话。Agent 据上轮 tool result 里的 shots 重出稿，
  // 新稿仍会先过确认闸门，不会直接生成。
  const shots = (msg.agentMeta as any)?.storyboardShots || []
  const lines = shots
    .map((s: any, i: number) => `${i + 1}. ${s.caption || s.image_prompt || '未命名镜头'}`)
    .join('\n')
  const text = `分镜请按这个意见重编：${comment}\n\n当前分镜：\n${lines}`
  // ctx.sendMessage 不收参数，回灌输入框要走 ChatView 提供的 sendMessageWithText
  if (typeof ctx.sendMessageWithText === 'function') {
    ctx.sendMessageWithText(text)
    return
  }
  // 兜底：宿主未提供时至少把指令放进输入框，让用户手动回车发送，而不是静默丢掉
  showToast('已把修改意见填入输入框，确认后发送')
  window.dispatchEvent(new CustomEvent('chat:fill-input', { detail: { text } }))
}

// ── 叠加方案确认（cut_plan 闸门的前端侧，设计文档 3.4 #2 缺口2）────

function _patchCutPlanMeta(patch: Record<string, any>) {
  if (!props.msg.agentMeta) return
  props.msg.agentMeta = { ...props.msg.agentMeta, ...patch }
}

function onCutPlanConfirmed(data?: any) {
  _patchCutPlanMeta({
    cutPlanResolved: 'confirmed',
    cutPlanError: '',
  })
  if (data?.output?.asset_url) {
    showToast('视频已渲染完成，正在打开作品面板')
  }
}

function onCutPlanCancelled() {
  // 设计文档 3.4 #3：确认卡已收进消息内 VideoTaskCard，不再走 dock，
  // 这里只回写 meta（旧实现里的 clearConfirmation 是清 dock 的，已不需要）
  _patchCutPlanMeta({ cutPlanResolved: 'cancelled', cutPlanError: '' })
}

function onCutPlanRevise(msg: ChatMessage, comment: string) {
  // 让 AI 重排：把用户意见带回对话，重新规划叠加方案（新方案仍过闸门）
  const plan = (msg.agentMeta as any)?.cutPlan?.plan || {}
  const segs = plan.segments || []
  const lines = segs
    .map((s: any, i: number) => `${i + 1}. [${Number(s.time_start ?? 0).toFixed(1)}-${Number(s.time_end ?? 0).toFixed(1)}s] ${s.template || '叠加'}${s.skipped ? '（跳过）' : ''}`)
    .join('\n')
  const text = `叠加方案请按这个意见重排：${comment}${lines ? `\n\n当前方案：\n${lines}` : ''}`
  if (typeof ctx.sendMessageWithText === 'function') {
    ctx.sendMessageWithText(text)
    return
  }
  showToast('已把修改意见填入输入框，确认后发送')
  window.dispatchEvent(new CustomEvent('chat:fill-input', { detail: { text } }))
}

// ── 设计文档 3.4 #3：四阶段容器卡渲染条件 ─────────────
// 由 agentMeta 推导口播卡阶段（plan/progress/done/failed/cancelled），
// 非空即渲染 VideoTaskCard，同时用于口播态下隐藏旧独立进度行。
const videoStage = computed(() => deriveVideoStage(props.msg.agentMeta))

const feedbackEventId = ref<string | null>(null)
const showDislikeDimensions = ref(false)
const DISLIKE_DIMENSIONS = ['结果不对', '太慢', '格式乱', '其他']

/**
 * 点赞/点踩切换逻辑：
 * - 点赞：裸信号，无维度，直接落库
 * - 点踩：必须先弹维度选择器，用户选完维度才落库（保证踩的信号有归因价值）
 * - 重复点击：删除已落库的反馈事件（取消反馈）
 */
async function toggleFeedback(type: 'like' | 'dislike') {
  // 已是当前状态 → 取消反馈（删除事件）
  if (feedbackState.value === type) {
    if (type === 'dislike') showDislikeDimensions.value = false
    feedbackState.value = null
    if (type === 'like') likeCount.value = Math.max(0, likeCount.value - 1)
    if (feedbackEventId.value) {
      try { await deleteFeedbackEvent(feedbackEventId.value) } catch { /* 忽略 */ }
      feedbackEventId.value = null
    }
    return
  }

  // 切换到新的反馈类型
  if (type === 'like') {
    // 点赞：直接落库，无维度
    if (feedbackEventId.value) {
      try { await deleteFeedbackEvent(feedbackEventId.value) } catch { /* 忽略 */ }
      feedbackEventId.value = null
    }
    showDislikeDimensions.value = false
    feedbackState.value = 'like'
    likeCount.value++
    const event = await createFeedbackEvent('like', {
      workflow_id: props.msg.agentMeta?.sessionId ?? null,
      session_id: props.sseSessionId,
    })
    if (event) feedbackEventId.value = event.id
  } else {
    // 点踩：先弹维度选择器，等用户选完维度再落库
    showDislikeDimensions.value = true
  }
}

/**
 * 点踩维度选择完成后落库
 */
async function submitDislikeWithDimension(dim: string) {
  if (feedbackEventId.value) {
    try { await deleteFeedbackEvent(feedbackEventId.value) } catch { /* 忽略 */ }
    feedbackEventId.value = null
  }
  showDislikeDimensions.value = false
  feedbackState.value = 'dislike'
  const event = await createFeedbackEvent('dislike', {
    workflow_id: props.msg.agentMeta?.sessionId ?? null,
    session_id: props.sseSessionId,
    dimension: dim,
  })
  if (event) feedbackEventId.value = event.id
}

/**
 * 重试按钮：先落库重试事件（最强的负反馈信号），再执行实际重试
 */
async function onRetryClick() {
  // fire-and-forget：重试事件落库失败不影响重试操作本身
  createFeedbackEvent('retry', {
    workflow_id: props.msg.agentMeta?.sessionId ?? null,
    session_id: props.sseSessionId,
  }).catch(() => { /* 忽略 */ })
  await ctx.retryLastMessage()
}

const isCopied = ref(false)
let copyDecayTimer: ReturnType<typeof setTimeout> | null = null

async function onCopyMessage() {
  try {
    await navigator.clipboard.writeText(props.msg.content || '')
  } catch {
    const textarea = document.createElement('textarea')
    textarea.value = props.msg.content || ''
    textarea.style.position = 'fixed'
    textarea.style.opacity = '0'
    document.body.appendChild(textarea)
    textarea.select()
    document.execCommand('copy')
    document.body.removeChild(textarea)
  }
  // saved-check-decay（手册）：复制后图标变绿勾，静默 2.5s 平滑淡退
  isCopied.value = true
  if (copyDecayTimer) clearTimeout(copyDecayTimer)
  copyDecayTimer = setTimeout(() => { isCopied.value = false }, 2500)
  showToast('已复制')
}

async function copyToClipboard(text: string, triggerEl: HTMLElement) {
  try {
    await navigator.clipboard.writeText(text)
  } catch {
    const textarea = document.createElement('textarea')
    textarea.value = text
    textarea.style.position = 'fixed'
    textarea.style.opacity = '0'
    document.body.appendChild(textarea)
    textarea.select()
    document.execCommand('copy')
    document.body.removeChild(textarea)
  }
  showCopyFeedback(triggerEl)
}

function showCopyFeedback(el: HTMLElement) {
  el.classList.add('rl-copy-success')
  setTimeout(() => el.classList.remove('rl-copy-success'), 1500)
  // saved-check-decay（手册）：复制后图标变绿勾，静默 2.5s 平滑淡退
  isCopied.value = true
  if (copyDecayTimer) clearTimeout(copyDecayTimer)
  copyDecayTimer = setTimeout(() => { isCopied.value = false }, 2500)
  showToast('已复制')
}

let toastTimer: number | null = null

function showToast(message: string) {
  const existing = document.querySelector('.rl-copy-toast')
  if (existing) existing.remove()
  if (toastTimer !== null) window.clearTimeout(toastTimer)

  const el = document.createElement('div')
  el.className = 'rl-copy-toast'
  el.textContent = message
  document.body.appendChild(el)

  toastTimer = window.setTimeout(() => {
    el.remove()
    toastTimer = null
  }, 1600)
}

function onContentClick(e: MouseEvent) {
  const target = e.target as HTMLElement

  const linkEl = target.closest('a.rl-link-text') as HTMLAnchorElement | null
  if (linkEl && linkEl.href) {
    const href = linkEl.href
    if (href.startsWith('http://') || href.startsWith('https://')) {
      e.preventDefault()
      e.stopPropagation()
      ctx.openInBrowser(href)
      return
    }
  }

  const codeCopyBtn = target.closest('.rl-code-copy-btn') as HTMLElement | null
  if (codeCopyBtn) {
    const pre = codeCopyBtn.closest('.rl-code-block')
    const code = pre?.querySelector('code')
    if (code) {
      copyToClipboard(code.textContent || '', codeCopyBtn)
    }
    e.preventDefault()
    e.stopPropagation()
    return
  }

  const inlineCode = target.closest('.rl-inline-code') as HTMLElement | null
  if (inlineCode) {
    copyToClipboard(inlineCode.textContent || '', inlineCode)
    e.preventDefault()
    e.stopPropagation()
    return
  }
}

function extractFirstBold(text: string): string {
  if (!text) return ''
  const match = text.match(/\*\*(.+?)\*\*/)
  if (match) return match[1]
  return ''
}

const _expandedState = new WeakMap<any, Record<string, boolean>>()

function isExpanded(msg: any, key: string): boolean {
  const state = _expandedState.get(msg)
  if (state && key in state) return state[key]
  return false
}

function toggleExpand(msg: any, key: string) {
  let state = _expandedState.get(msg)
  if (!state) {
    state = {}
    _expandedState.set(msg, state)
  }
  state[key] = !state[key]
}

function renderDiff(content: string): string {
  if (!content) return ''
  return content.split('\n').map(line => {
    if (line.startsWith('+') && !line.startsWith('+++')) {
      return `<span class="rl-diff-line-add">${escapeHtml(line)}</span>`
    } else if (line.startsWith('-') && !line.startsWith('---')) {
      return `<span class="rl-diff-line-del">${escapeHtml(line)}</span>`
    } else if (line.startsWith('@@')) {
      return `<span class="rl-diff-line-hunk">${escapeHtml(line)}</span>`
    }
    return escapeHtml(line)
  }).join('\n')
}

function escapeHtml(str: string): string {
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

const { events: thoughtEvents, isLive: thoughtLive, statusText: thoughtStatusText } = useThoughtStream(() => props.msg.turnId)

// ── 视频管线进度行（video_pipeline_progress）──
// 渲染中（percent<100）显示；完成即收尾不占位。label 缺失兜底「视频生成中」。
const videoProgressRow = computed(() => {
  const vp = props.msg.agentMeta?.videoProgress
  if (!vp || vp.percent >= 100) return null
  return { ...vp, label: vp.label || '视频生成中' }
})

// guardian 确认请求：进入等待状态时注册到全局（输入框上方弹窗渲染）
watch(
  () => props.msg.agentMeta?.workflowStatus,
  (st) => {
    if (
      st === 'awaiting_guardian_confirmation' &&
      props.msg.agentMeta?.loopStatePath &&
      props.msg.agentMeta?.guardianResolved !== 'confirmed' &&
      props.msg.agentMeta?.guardianResolved !== 'rejected'
    ) {
      requestConfirmation({
        kind: 'guardian',
        sessionId: props.sseSessionId,
        loopStatePath: props.msg.agentMeta.loopStatePath,
        turnId: props.msg.agentMeta?.turnId,
        msg: props.msg,
        // dock 读取的是顶层字段（ChatView 用 req.toolName / req.riskInfo），
        // 此前只塞进 cardProps，导致确认卡永远显示"即将执行 操作"、无风险标签。
        toolName: props.msg.agentMeta.guardianToolName,
        confirmationPrompt: props.msg.agentMeta.confirmationPrompt,
        riskInfo: props.msg.agentMeta.riskInfo,
        failedError: props.msg.agentMeta.guardianError,
        cardProps: {
          toolName: props.msg.agentMeta.guardianToolName,
          confirmationPrompt: props.msg.agentMeta.confirmationPrompt,
          riskInfo: props.msg.agentMeta.riskInfo,
          failedError: props.msg.agentMeta.guardianError,
        },
        // 批准后必须把流状态接回运行并确保 SSE 订阅存活：旧连接可能已断
        // （停止/断网/热重载），不重订阅则续跑事件全落空、界面永远"思考中"。
        onAction: (action: string) => {
          if (action === 'confirmed') onGuardianConfirmed()
        },
      })
    }
  },
  { immediate: true }
)

// 另外三类确认/澄清/分镜：统一注册到 dock
watch(
  () => [props.msg.agentMeta?.workflowStatus, props.msg.agentMeta?.guardianResolved, props.msg.agentMeta?.confirmationResolved],
  ([st]) => {
    const meta = props.msg.agentMeta
    if (!meta?.loopStatePath) return
    const sid = props.sseSessionId
    if (st === 'awaiting_confirmation' && meta.confirmationResolved !== 'confirmed' && meta.confirmationResolved !== 'rejected') {
      requestConfirmation({
        kind: 'confirm',
        sessionId: sid,
        loopStatePath: meta.loopStatePath,
        turnId: meta?.turnId,
        msg: props.msg,
        cardProps: {
          prompt: meta.confirmationPrompt || props.msg.content,
          skillName: meta.confirmationSkill,
        },
        onAction: (action, data) => {
          if (action !== 'expired') meta.confirmationResolved = action
          ctx.onChatConfirmed(props.msg, action as 'approve' | 'reject', data)
        },
      })
    } else if (st === 'awaiting_clarification') {
      requestConfirmation({
        kind: 'clarify',
        sessionId: sid,
        loopStatePath: meta.loopStatePath,
        turnId: meta?.turnId,
        msg: props.msg,
        cardProps: {
          batches: meta.clarificationBatches || [],
          prompt: meta.clarificationPrompt || props.msg.content,
          skillName: meta.clarificationSkill,
        },
        onAction: (action, data) => {
          if (action === 'clarified') ctx.onChatClarified?.(props.msg, data?.answers || {}, data)
          else ctx.onChatClarified?.(props.msg, {}, undefined)
        },
      })
    } else if (
      st === 'awaiting_storyboard_confirmation' &&
      meta.storyboardResolved !== 'confirmed' &&
      meta.storyboardResolved !== 'cancelled'
    ) {
      requestConfirmation({
        kind: 'storyboard',
        sessionId: sid,
        loopStatePath: meta.loopStatePath,
        turnId: meta?.turnId,
        msg: props.msg,
        cardProps: {
          shots: meta.storyboardShots || [],
          title: meta.storyboardTitle,
          failedError: meta.storyboardError,
        },
        onAction: (action, data) => {
          if (action === 'confirmed') onStoryboardConfirmed(data)
          else if (action === 'revise') onStoryboardRevise(props.msg, String(data?.comment || data || ''))
          else onStoryboardCancelled()
        },
      })
    }
    // cutplan（叠加方案确认）分支已删除（设计文档 3.4 #3）：
    // 确认卡改由消息内 VideoTaskCard 容器卡原位渲染，不再走 dock。
  },
  { immediate: true }
)
const isCurrentlyStreamingThinking = computed(() => {
  return props.msg === ctx.streamingMsg.value && ctx.isStreaming.value && ctx.streamingThinking.value
})
// 等待用户操作中（澄清/确认/守卫/分镜）——操作栏此时不该出现（对齐 Claude：
// 动作按钮只在回复完成后显示，确认卡片旁边挂一排按钮是错位）
const isAwaitingUserAction = computed(() => {
  const st = props.msg.agentMeta?.workflowStatus || ''
  return st.startsWith('awaiting_')
})
// 思考耗时落定（对齐真实 "Thought for X.Xs (Nms)"）：思考流结束瞬间计时
watch(isCurrentlyStreamingThinking, (live, prev) => {
  if (prev && !live && thinkStartAt.value !== null && thinkElapsedMs.value === null) {
    thinkElapsedMs.value = Math.round(performance.now() - thinkStartAt.value)
  }
})

/**
 * 本条消息「正在输出正文」。Aura 三层的总开关：
 * - A 层用它挂 .is-aura（描边染色 + .7s 回落）
 * - B/C 层用它决定是否注入打字头锚点
 */
const isStreamingSelf = computed(() => {
  return props.msg === ctx.streamingMsg.value && ctx.isStreaming.value && !!props.msg.content
})

// 本条消息正处于流式（思考阶段或正文阶段都算）；用于驱动计时器，
// 使「思考耗时」在纯思考期（尚未产出正文）也能实时走动，而非只钉在顶部品牌头。
const isThisStreaming = computed(() => {
  return props.msg === ctx.streamingMsg.value && ctx.isStreaming.value
})

// ── 渲染层原型移植：品牌头状态 pill ──
// 原型（tick() 里 updTools / extractHeaders）：状态头由「当前在哪一步」驱动——
// 思考阶段取 reasoning 里最后一个 **加粗标题**（codex 哲学：reasoning 不全量入历史，
// 只把当前小节标题作为可见状态），工具阶段取正在执行的工具名，正文阶段固定文案。
// 后端若显式下发 statusIndicator.header 则优先用它（更权威），否则纯前端推导。
const statusPillText = computed(() => {
  const h = props.msg.agentMeta?.statusIndicator?.header
  if (h && h.trim()) return h
  if (isCurrentlyStreamingThinking.value) {
    const hs = _extractBoldHeaders(props.msg.reasoning || '')
    const last = hs[hs.length - 1]
    if (last) return `${last}…`
    return '思考中…'
  }
  const running = (props.msg.toolCalls || []).filter(t => t.status === 'running')
  if (running.length > 0) {
    const tc = running[running.length - 1]
    return `${getToolDisplayLabel(tc.name).label || tc.name}中…`
  }
  if (ctx.streamingHasContent?.value) return '生成回复中…'
  return '思考中…'
})

// 回合隔离计数器（问题 6 可视化）
const turnRtDropped = computed(() => (props.msg as any)._turnRt?.droppedEvents || 0)
// 回合隔离调试条：仅开发者显式打开 localStorage 开关时可见，用户界面不显示
const turnDebugVisible = localStorage.getItem('rl_debug_turn') === '1'

// turnChip 上的回合短 id（原型文案形如「turn a3f9c2 · 已拦截 3 条旧回合事件」）
const turnShortId = computed(() => {
  const t: string = props.msg.agentMeta?.turnId || (props.msg as any)._turnRt?.turnId || ''
  return t ? String(t).slice(0, 6) : '—'
})

// ── 品牌头实时计时（流式中每 500ms 刷新；完成后回落为 durationMs）──
const liveElapsedMs = ref(0)
let _rlClockTimer: ReturnType<typeof setInterval> | null = null
watch(isThisStreaming, (v) => {
  if (_rlClockTimer) { clearInterval(_rlClockTimer); _rlClockTimer = null }
  if (v) {
    liveElapsedMs.value = Date.now() - (props.msg.timestamp || Date.now())
    _rlClockTimer = setInterval(() => {
      liveElapsedMs.value = Date.now() - (props.msg.timestamp || Date.now())
    }, 500)
  }
}, { immediate: true })
onUnmounted(() => { if (_rlClockTimer) clearInterval(_rlClockTimer) })
const liveElapsedText = computed(() => formatDuration(liveElapsedMs.value))

// ── 品牌头常驻本轮耗时（原型形态要点第一条）──
// 流式中显示实时走动时长，完成后回落为后端 durationMs。
// 确认/澄清闸门态不显示（那时回合还没真正开始跑）。
const brandDurVisible = computed(() => {
  const ws = props.msg.agentMeta?.workflowStatus
  if (ws === 'awaiting_confirmation' || ws === 'awaiting_clarification') return false
  if (isThisStreaming.value) return true
  return !isStreamingSelf.value && !!props.msg.agentMeta?.durationMs
})
const brandDurText = computed(() =>
  isThisStreaming.value
    ? liveElapsedText.value
    : formatDuration(props.msg.agentMeta?.durationMs ?? 0),
)

// ── 思考区双区渲染（原型：已提交行稳定 + 尾行生长 + 闪烁光标）──
function _stripBold(s: string): string { return s.replace(/\*\*/g, '') }
function _extractBoldHeaders(t: string): string[] {
  const out: string[] = []
  const re = /\*\*(.+?)\*\*/g
  let x: RegExpExecArray | null
  while ((x = re.exec(t))) out.push(x[1])
  return out
}
const reasoningLines = computed(() => (props.msg.reasoning || '').split('\n'))
const reasoningCommitted = computed(() => _stripBold(reasoningLines.value.slice(0, -1).join('\n')))
const reasoningTail = computed(() => _stripBold(reasoningLines.value[reasoningLines.value.length - 1] || ''))
const reasoningSummaryText = computed(() => {
  const hs = _extractBoldHeaders(props.msg.reasoning || '')
  if (hs.length) return hs.join(' · ')
  const t = reasoningTail.value.trim()
  if (!t) return ''
  return t.length > 46 ? t.slice(0, 46) + '…' : t
})

const reasoningOneLine = computed(() => {
  const raw = props.msg.reasoning || ''
  if (!raw) return ''
  const cleaned = raw
    .replace(/\*\*/g, '')
    .replace(/\*/g, '')
    .replace(/#{1,4}\s/g, '')
    .replace(/\n+/g, ' ')
    .replace(/\s{2,}/g, ' ')
    .trim()
  return cleaned
})

function getReasoningPreview(text: string): string {
  if (!text) return ''
  const cleaned = text
    .replace(/\*\*/g, '')
    .replace(/\*/g, '')
    .replace(/#{1,4}\s/g, '')
    .replace(/\n+/g, ' ')
    .replace(/\s{2,}/g, ' ')
    .trim()
  const MAX = 80
  if (cleaned.length <= MAX) return cleaned
  return '…' + cleaned.slice(cleaned.length - MAX)
}

function getSafeStreamHtml(msg: ChatMessage): string {
  if (msg._streamHtml) {
    // 流结束后 _streamHtml 仍保留 settledHtml，此时不再注入锚点，
    // 否则尾巴会永久挂在消息末尾。
    return wrapStreamHtml(msg._streamHtml)
  }
  return renderAssistantHtml(msg.content || '')
}

/** 双通道流式进行中？（用于区分「流式 tail」与「已落地正文」——
 *  text-wrap: pretty 只允许作用于落地态，流式区开会抖行） */
function isDualStream(msg: ChatMessage): boolean {
  return !!(msg as any)._dualStream
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`
  const s = ms / 1000
  if (s < 60) return `${s.toFixed(1)}s`
  const m = Math.floor(s / 60)
  const rest = Math.round(s % 60)
  return rest > 0 ? `${m}分${rest}秒` : `${m}分`
}

function formatTime(ts: number): string {
  const d = new Date(ts)
  const h = d.getHours().toString().padStart(2, '0')
  const m = d.getMinutes().toString().padStart(2, '0')
  return `${h}:${m}`
}

function formatFullTime(ts: number): string {
  const d = new Date(ts)
  const y = d.getFullYear()
  const mon = (d.getMonth() + 1).toString().padStart(2, '0')
  const day = d.getDate().toString().padStart(2, '0')
  const h = d.getHours().toString().padStart(2, '0')
  const m = d.getMinutes().toString().padStart(2, '0')
  const s = d.getSeconds().toString().padStart(2, '0')
  return `${y}-${mon}-${day} ${h}:${m}:${s}`
}
</script>

<style scoped>

/* ── 视频管线进度行（video_pipeline_progress）──
   对齐 AgentProgressCard（rl-ap-*）：无边框无底色，11px text-tertiary，
   一行 space-between，右侧百分比 tabular-nums。 */
.rl-vp-line {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  margin-top: 8px;
  font-size: 11px;
  color: var(--rl-ink-3, #888780);
}
.rl-vp-label {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.rl-vp-percent {
  flex: none;
  font-variant-numeric: tabular-nums;
}

/* ⚡ 任务监控：回复底部的任务执行耗时（.rl-msg-duration 已随本轮样式统一移除，
     单轮耗时现由品牌头常驻，见 .rl-brand-dur） */
/* ⚡ 设计文档 3.4 #3：旧独立版本条样式（.rl-cp-*）已随版本条模板一并移除，
     成片版本 chips 改由 VideoTaskCard（vtc-*）承载 */

/* ═══════════ 渲染层原型移植（rl- 前缀，视觉与 render-layer-prototype.html 一致）═══ */
.rl-brand {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
/* 原型：logo 26×26 / border-radius 7（品牌头不再做 logo+耗时竖列，
   耗时改到行尾常驻，见 .rl-brand-dur） */
.rl-brand-logo-wrap {
  width: 26px;
  height: 26px;
  border-radius:10px;
  overflow: hidden;
  display: flex;
  align-items: center;
  justify-content: center;
  flex: none;
}
.rl-brand-logo {
  width: 26px;
  height: 26px;
  border-radius:10px;
  display: block;
  object-fit: contain;
}
/* .rl-brand-name 已随品牌名移除（2026-11-04） */
.rl-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  color: var(--rl-ink-2, #5F5E5A);
  background: var(--rl-chip, #F1EFE8);
  border-radius: 999px;
  padding: 2px 9px;
}
.rl-pill-spin {
  width: 9px;
  height: 9px;
  border-radius: 50%;
  border: 1.5px solid var(--rl-ink-3, #888780);
  border-top-color: transparent;
  animation: rl-spin 0.8s linear infinite;
  flex: none;
}
@keyframes rl-spin { to { transform: rotate(360deg); } }
/* 原型品牌头行尾：11px · text-tertiary · tabular-nums · 11×11 时钟，常驻本轮耗时 */
.rl-brand-dur {
  margin-left: auto;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 11px;
  color: var(--rl-ink-3, #888780);
  font-variant-numeric: tabular-nums;
  flex: none;
  white-space: nowrap;
}
.rl-turn-chip {
  margin-top: 6px;
  font-size: 11px;
  color: var(--rl-ink-3, #888780);
  animation: rl-in 0.3s ease;
}
@keyframes rl-in { from { opacity: 0; } to { opacity: 1; } }

/* ── 思考过程：对齐 Claude 桌面端真身（无卡片无边框）──
   颜色逐条映射真身：#7b7974→--rl-text-2 / #373734→--rl-text-1 /
   rail rgba(31,31,30,.15)→--rl-border-thin / 渐隐 from-claude-bg→--rl-bg。
   动画参数取自取证 CSS（timeline-*：quiet-dots 1.9s / status-rise .24s）。 */
.rl-think {
  margin: 6px 0;
  padding-left: 8px;
}
.rl-think-head {
  display: flex;
  align-items: center;
  gap: 4px;
  width: 100%;
  padding: 4px 0;
  border: none;
  background: none;
  text-align: left;
  font-size: 14px;
  line-height: 19.6px;
  letter-spacing: -0.15px;
  color: var(--rl-text-2, #7b7974);
  cursor: pointer;
  user-select: none;
  transition: color 0.15s ease;
}
.rl-think-head:hover {
  color: var(--rl-text-1, #373734);
}
.rl-think--live .rl-think-head {
  cursor: default;
}
.rl-think-label {
  min-width: 0;
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
/* 思考中：shimmer 扫光（对齐 TextShimmer：3 档灰渐变 220%，3.2s 线性——两轮放慢后定档） */
.rl-think-label--live {
  color: transparent;
  background-image: linear-gradient(90deg, rgba(123, 121, 116, 0.72) 0%, rgba(55, 55, 52, 0.92) 48%, rgba(123, 121, 116, 0.72) 100%);
  background-size: 220% 100%;
  background-clip: text;
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  animation: rl-think-shimmer 3.2s linear infinite;
}
.rl-chat.is-dark .rl-think-label--live {
  background-image: linear-gradient(90deg, rgba(158, 158, 152, 0.72) 0%, rgba(240, 240, 236, 0.92) 48%, rgba(158, 158, 152, 0.72) 100%);
}
@keyframes rl-think-shimmer {
  from { background-position: 200% center; }
  to   { background-position: 0% center; }
}
.rl-think-dup {
  flex: none;
  font-size: 11px;
  color: var(--rl-amber-ink, #854F0B);
  background: var(--rl-amber-bg, #FAEEDA);
  border-radius: 999px;
  padding: 1px 8px;
  white-space: nowrap;
}
.rl-think-chev {
  flex: none;
  color: var(--rl-ink-3, #888780);
  transition: transform 0.2s ease;
  display: inline-flex;
}
.rl-think-chev.is-open {
  transform: rotate(180deg);
}

/* 时间线（对齐真实 rail 竖线 + w-20px 图标列 + flex-1 内容列） */
.rl-think-timeline {
  overflow: hidden;
}
.rl-think-row {
  display: flex;
  align-items: flex-start;
}
.rl-think-node {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  width: 20px;
  padding: 4px 2px 0;
  flex: none;
}
.rl-think-node-icon {
  color: var(--rl-text-2, #7b7974);
  flex: none;
}
.rl-think-rail {
  width: 1px;
  min-height: 8px;
  flex: 1;
  background: var(--rl-border-thin, rgba(31, 31, 30, 0.15));
}
.rl-think-rail--last {
  flex: 0 0 8px;
}
.rl-think-content-wrap {
  min-width: 0;
  flex: 1;
  padding-left: 10px;
  padding-top: 2px;
  position: relative;
}
.rl-think-text {
  overflow: hidden;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 14px;
  line-height: 19.6px;
  letter-spacing: -0.15px;
  color: var(--rl-text-1, #373734);
}
.rl-think-committed { display: block; }
.rl-think-tail { display: inline; }
.rl-think-cursor {
  display: inline-block;
  width: 1px;
  height: 13px;
  background: var(--rl-text-2, #4B4B4B);
  margin-left: 1px;
  vertical-align: -1px;
  animation: rl-blink 1s step-end infinite;
}
@keyframes rl-blink { 0%, 50% { opacity: 1; } 51%, 100% { opacity: 0; } }
/* 200px 截断渐隐蒙版（对齐 h-12 bg-gradient-to-t from-claude-bg） */
.rl-think-fade {
  pointer-events: none;
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  height: 48px;
  background: linear-gradient(to top, var(--rl-bg, #F5F5F3), transparent);
}
.rl-think-more {
  margin-top: 8px;
  padding: 0;
  border: none;
  background: none;
  font-size: 12px;
  line-height: 16px;
  color: var(--rl-ink-3, #888780);
  cursor: pointer;
  transition: color 0.15s ease;
}
.rl-think-more:hover {
  color: var(--rl-text-1, #373734);
}
/* 完成节点：小空心圆 + 已完成（灰，对齐 Done 行） */
.rl-think-done-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  border: 1.5px solid var(--rl-ink-3, #888780);
  background: var(--rl-bg, #F5F5F3);
  flex: none;
}
.rl-think-done-label {
  font-size: 14px;
  line-height: 19.6px;
  color: var(--rl-ink-3, #888780);
}
.rl-think-dup-hint {
  font-size: 11px;
  color: var(--rl-ink-3, #888780);
  padding-bottom: 6px;
}

/* ── 确认状态失效：信息卡

/* ── 确认状态失效：信息卡（自 chat-view.css 死区迁入活区并重做）──
   Claude 式信息卡语气：中性底 + 左缘 amber 提示条，弱化警告感、强化行动引导。 */
.rl-confirm-lost {
  display: flex;
  gap: 10px;
  margin: 10px 0;
  padding: 12px 14px;
  border: 1px solid var(--rl-border-thin, rgba(0, 0, 0, 0.07));
  border-left: 3px solid var(--rl-amber-ink, #854F0B);
  border-radius:14px;
  background: var(--rl-card, #FFFFFF);
}
.rl-confirm-lost-icon {
  font-size: 15px;
  line-height: 1.4;
  color: var(--rl-amber-ink, #854F0B);
  opacity: 0.8;
  flex: none;
  margin-top: 1px;
}
.rl-confirm-lost-body { flex: 1; min-width: 0; }
.rl-confirm-lost-title { margin: 0 0 4px; font-size: 13px; font-weight: 600; color: var(--rl-text-1, #1A1A1A); }
.rl-confirm-lost-msg { margin: 0 0 10px; font-size: 12px; line-height: 1.6; color: var(--rl-text-2, #4B4B4B); word-break: break-word; }
.rl-confirm-lost-btn {
  padding: 6px 16px;
  border: none;
  border-radius:12px;
  background: var(--rl-accent, #534AB7);
  color: #fff;
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  transition: background 0.15s ease, transform 0.1s ease;
}
.rl-confirm-lost-btn:hover { background: var(--rl-accent, #534AB7); filter: brightness(1.08); }
.rl-confirm-lost-btn:active { transform: scale(0.98); }

/* ── 决策/错误面板 Grid 折叠（与思考块/工具卡统一的 0fr↔1fr 语言）── */
.rl-cell-grid {
  display: grid;
  transition: grid-template-rows 0.2s cubic-bezier(0.19, 1, 0.22, 1);
}
.rl-cell-grid-clip {
  min-height: 0;
  overflow: hidden;
}
.rl-cell-chevron.is-open {
  transform: rotate(180deg);
}

/* 执行轨迹/协作面板的共享样式在 render-layer.css（全局）：
   跨组件复用的 rl-traj-* 不能放 scoped——ToolCallCell 与本组件会各打不同
   data-v 属性导致样式互不生效。 */


/* ── 自 chat-view.css 死区迁入（2026-10-20 系统性清理）──
   以下 .rl-cell-* 规则原本写在 chat-view.css（ChatView scoped），
   但节点属于本组件（data-v 不同）导致全部失效裸奔；迁入后选择器
   编译为 [data-v-本组件] 真正生效。 */
.rl-pill {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 28px;
  padding: 0 10px;
  border: 1px solid var(--rl-border-thin);
  border-radius:12px;
  background: transparent;
  color: var(--rl-text-3);
  font-size: 13px;
  font-weight: 500;
  line-height: 20px;
  cursor: pointer;
  transition: background 0.12s ease, color 0.12s ease;
}

.rl-pill:hover {
  background: var(--rl-hover);
  color: var(--rl-text-2);
}

.rl-assistant-row {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
}

.rl-assistant-content {
  position: relative;
  font-size: 14px;
  line-height: 22px;
  color: var(--rl-text-1);
  word-break: break-word;
  overflow-wrap: anywhere;
  padding-top: 4px;
}

.rl-assistant-content h4 {
  font-size: 14px;
  font-weight: 600;
  line-height: 22px;
  margin: 8px 0 4px;
}

.rl-assistant-content table {
  font-size: 13px;
  line-height: 1.4;
}

.rl-assistant-content td {
  font-size: 13px;
  padding: 4px 8px;
}

.rl-assistant-content code {
  font-size: 13px;
}

.rl-assistant-row:hover .rl-msg-time {
  opacity: 1;
  pointer-events: auto;
}

.rl-cell {
  margin: 6px 0;
  border-radius:12px;
  border: 1px solid var(--rl-border);
  overflow: hidden;
  cursor: pointer;
  transition: border-color 0.12s;
}

.rl-cell:hover {
  border-color: var(--rl-border-strong, var(--rl-border));
}

.rl-cell-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  font-size: 13px;
  line-height: 1.4;
  user-select: none;
  cursor: pointer;
  border-radius:10px;
  transition: background 0.15s;
}

.rl-cell-header:hover {
  background: var(--rl-bg-2);
}

.rl-cell-icon {
  flex: none;
  opacity: 0.7;
}

.rl-cell-label {
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.rl-cell-chevron {
  margin-left: auto;
  flex: none;
  font-size: 11px;
  color: var(--rl-text-3);
  transition: transform 0.2s ease;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
}

.rl-cell-body {
  padding: 0 12px 10px;
}


.rl-cell-decision-item {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  padding: 4px 0;
  font-size: 12.5px;
  line-height: 1.4;
  color: var(--rl-text-2);
}

.rl-cell-decision-bullet {
  flex: none;
  color: #10b981;
  font-size: 11px;
  margin-top: 1px;
}

.rl-cell-decision-text {
  flex: 1;
  min-width: 0;
  word-break: break-word;
}

.rl-cell-errors {
  background: rgba(180, 35, 24, 0.04);
  border-color: rgba(180, 35, 24, 0.2);
}

.rl-cell-errors:hover {
  border-color: rgba(180, 35, 24, 0.35);
}

.rl-cell-errors .rl-cell-header {
  color: #dc2626;
}

.rl-cell-errors .rl-cell-icon {
  color: #ef4444;
}

.rl-cell-error-item {
  padding: 4px 0;
  font-size: 12.5px;
  line-height: 1.4;
  color: #b91c1c;
  word-break: break-word;
}

.rl-cell-errors .rl-cell-chevron {
  color: #dc2626;
}


.rl-chat.is-dark .rl-cell-decision-bullet {
  color: #34d399;
}

.rl-chat.is-dark .rl-cell-errors {
  background: rgba(180, 35, 24, 0.08);
  border-color: rgba(180, 35, 24, 0.25);
}

.rl-chat.is-dark .rl-cell-errors .rl-cell-header {
  color: #f87171;
}

.rl-chat.is-dark .rl-cell-errors .rl-cell-icon {
  color: #f87171;
}

.rl-chat.is-dark .rl-cell-error-item {
  color: #fca5a5;
}
</style>