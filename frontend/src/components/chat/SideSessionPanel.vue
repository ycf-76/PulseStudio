<script setup lang="ts">
/**
 * 侧会话面板（Task 7/8 + 面板内联聊天改版，PRD FR-E）：
 * - 双模式：list=对话历史列表；chat=面板内直接与侧会话对话（不切换主聊天区）；
 * - chat 模式：历史消息气泡 + 底部输入框；「对话历史」按钮在输入框上方，点按回列表；
 * - 正文增量走 sideEventStream（GET /api/sse/chat/{id}），发送走非流式 /api/v1/chat/agent；
 * - 写副作用确认内联在气泡流里（批准=逐次放行，拒绝=只读继续）；
 * - list 模式：新建弹窗（粒度/recent_n/token 提示）、改名、删除（二次确认）、带回主任务。
 */
import { ref, computed, nextTick } from 'vue'
import { Plus, X, Pencil, Trash2, MessageSquare, CornerUpLeft, Sparkles, ArrowLeft, Send } from 'lucide-vue-next'
import { useSideSessionStore, EXPIRED_TURN_THRESHOLD } from '@/stores/sideSession'
import {
  generateDigestDraft,
  bringBack,
  sendSideAgentMessage,
  confirmSideWrite,
} from '@/api/sideSessions'
import { listMessages } from '@/api/chatSessions'
import { openSideEventStream, type SideStreamHandle, type SideWriteRequestInfo } from './sideEventStream'
import type { SideSessionItem } from '@/api/sideSessions'

const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{
  (e: 'close'): void
  /** 带回成功（ChatView 刷新主会话消息） */
  (e: 'brought-back', mainSessionId: string): void
}>()

const store = useSideSessionStore()

// ── 模式切换 ────────────────────────────────
const mode = ref<'list' | 'chat'>('list')
const activeChatSide = ref<SideSessionItem | null>(null)

function bannerOf(expiredTurns: number): string {
  if (expiredTurns < EXPIRED_TURN_THRESHOLD) return ''
  return `快照已过期，主任务已推进 ${expiredTurns} 轮`
}

// ── 新建弹窗 ────────────────────────────────
const showCreate = ref(false)
const granularity = ref<'full' | 'recent_n' | 'digest'>('recent_n')
const recentN = ref(10)
const creating = ref(false)
const createError = ref('')

const GRANULARITY_LABELS: Record<string, string> = {
  full: '完整继承（全部历史）',
  recent_n: '最近 N 轮',
  digest: '仅摘要',
}

function openCreate() {
  granularity.value = 'recent_n'
  recentN.value = 10
  createError.value = ''
  showCreate.value = true
}

async function submitCreate() {
  creating.value = true
  createError.value = ''
  try {
    const created = await store.create({
      granularity: granularity.value,
      recent_n: granularity.value === 'recent_n' ? recentN.value : undefined,
    })
    showCreate.value = false
    await enterChat(created)
  } catch (e: any) {
    createError.value = e?.message || '创建失败'
  } finally {
    creating.value = false
  }
}

// ── 改名 / 删除 ─────────────────────────────
const renamingId = ref<string | null>(null)
const renameText = ref('')
const pendingDeleteId = ref<string | null>(null)

function startRename(id: string, title: string) {
  renamingId.value = id
  renameText.value = title
}
async function submitRename() {
  if (!renamingId.value || !renameText.value.trim()) return
  await store.rename(renamingId.value, renameText.value.trim())
  renamingId.value = null
}
async function confirmRemove() {
  if (!pendingDeleteId.value) return
  const id = pendingDeleteId.value
  pendingDeleteId.value = null
  if (activeChatSide.value?.id === id) await backToList()
  await store.remove(id)
}

// ── 面板内联聊天 ────────────────────────────
interface SideChatMsg {
  role: 'user' | 'assistant'
  content: string
  pending?: boolean
  isError?: boolean
}
const chatMsgs = ref<SideChatMsg[]>([])
const chatInput = ref('')
const sending = ref(false)
const pendingWrite = ref<SideWriteRequestInfo | null>(null)
const writeResolving = ref(false)
let activeStream: SideStreamHandle | null = null
const chatScrollRef = ref<HTMLElement | null>(null)

const GRANULARITY_SHORT: Record<string, string> = {
  full: '完整',
  recent_n: '最近N轮',
  digest: '摘要',
}

function _closeStream() {
  activeStream?.close()
  activeStream = null
}

function _scrollBottom() {
  nextTick(() => {
    const el = chatScrollRef.value
    if (el) el.scrollTop = el.scrollHeight
  })
}

async function enterChat(side: SideSessionItem) {
  _closeStream()
  activeChatSide.value = side
  store.select(side.id)
  mode.value = 'chat'
  pendingWrite.value = null
  chatInput.value = ''
  chatMsgs.value = []
  const history = await listMessages(side.id)
  chatMsgs.value = (history || [])
    .filter((m: any) => (m.content || '').trim())
    .map((m: any) => ({ role: m.role === 'user' ? 'user' : 'assistant', content: m.content }))
  _scrollBottom()
}

async function backToList() {
  _closeStream()
  mode.value = 'list'
  activeChatSide.value = null
  pendingWrite.value = null
  await store.load()
}

async function sendChat() {
  const text = chatInput.value.trim()
  const side = activeChatSide.value
  if (!text || !side || sending.value || pendingWrite.value) return
  chatInput.value = ''
  chatMsgs.value.push({ role: 'user', content: text })
  const bubble: SideChatMsg = { role: 'assistant', content: '', pending: true }
  chatMsgs.value.push(bubble)
  sending.value = true
  _scrollBottom()

  // 先订阅事件流（不带 turn_id：订阅时刻起的新事件），再发非流式请求
  activeStream = openSideEventStream(side.id, {
    onDelta: (d) => {
      bubble.content += d
      _scrollBottom()
    },
    onSideWriteRequired: (info) => {
      pendingWrite.value = info
      bubble.pending = false
    },
    onLoopDone: () => {
      bubble.pending = false
    },
  })

  try {
    const res = await sendSideAgentMessage(side.id, text)
    bubble.pending = false
    if (res.status === 'awaiting_side_write_confirmation') {
      const out: any = res.output || {}
      if (!pendingWrite.value && out.loop_state_path) {
        pendingWrite.value = {
          tool_name: out.side_write?.tool_name || '',
          description: out.side_write?.description || '',
          permissions: out.side_write?.permissions || [],
          loop_state_path: out.loop_state_path,
        }
      }
    } else if (res.message && res.message.length > bubble.content.length) {
      bubble.content = res.message
    }
  } catch (e: any) {
    bubble.pending = false
    bubble.isError = true
    bubble.content = e?.message || '发送失败'
  } finally {
    sending.value = false
    _scrollBottom()
  }
}

async function resolveSideWrite(action: 'approve' | 'reject') {
  const side = activeChatSide.value
  const info = pendingWrite.value
  if (!side || !info || writeResolving.value) return
  writeResolving.value = true
  try {
    await confirmSideWrite(side.id, { action, loop_state_path: info.loop_state_path })
    pendingWrite.value = null
    // 批准/拒绝后后台续跑：重开事件流跟随恢复执行
    const bubble: SideChatMsg = { role: 'assistant', content: '', pending: true }
    if (action === 'reject') bubble.content = '（已拒绝该写操作，以只读方式继续）\n'
    chatMsgs.value.push(bubble)
    _scrollBottom()
    activeStream = openSideEventStream(side.id, {
      onDelta: (d) => {
        bubble.content += d
        _scrollBottom()
      },
      onSideWriteRequired: (i2) => {
        pendingWrite.value = i2
        bubble.pending = false
      },
      onLoopDone: () => {
        bubble.pending = false
        _closeStream()
      },
    })
  } catch (e: any) {
    chatMsgs.value.push({ role: 'assistant', content: e?.message || '确认失败', isError: true })
  } finally {
    writeResolving.value = false
  }
}

// ── 带回主任务（Task 6 前端）────────────────
const bringingBackId = ref<string | null>(null)
const digestText = ref('')
const draftLoading = ref(false)
const bringError = ref('')
const bringing = ref(false)

const bringingBackSide = computed(() =>
  store.sides.find((s) => s.id === bringingBackId.value) || null,
)

function startBringBack(id: string) {
  bringingBackId.value = id
  digestText.value = ''
  bringError.value = ''
}
async function loadDraft() {
  if (!bringingBackId.value) return
  draftLoading.value = true
  bringError.value = ''
  try {
    digestText.value = await generateDigestDraft(bringingBackId.value)
  } catch (e: any) {
    bringError.value = e?.message || '生成草稿失败'
  } finally {
    draftLoading.value = false
  }
}
async function submitBringBack() {
  if (!bringingBackId.value || !digestText.value.trim()) return
  bringing.value = true
  bringError.value = ''
  try {
    const res = await bringBack(bringingBackId.value, digestText.value.trim())
    bringingBackId.value = null
    await store.load()
    emit('brought-back', res.main_session_id)
  } catch (e: any) {
    bringError.value = e?.message || '带回失败'
  } finally {
    bringing.value = false
  }
}
</script>

<template>
  <div v-if="open" class="ssp-panel">
    <!-- ═══ 对话模式：面板内直接与侧会话聊天 ═══ -->
    <template v-if="mode === 'chat' && activeChatSide">
      <div class="ssp-header">
        <button class="ssp-close" title="返回对话历史" @click="backToList">
          <ArrowLeft :size="14" />
        </button>
        <span class="ssp-title">{{ activeChatSide.title }}</span>
        <span v-if="activeChatSide.status === 'archived'" class="ssp-badge is-archived">已归档</span>
        <button class="ssp-close" title="关闭面板" @click="emit('close')">
          <X :size="14" />
        </button>
      </div>
      <div v-if="bannerOf(activeChatSide.expired_turns) && activeChatSide.status === 'active'" class="ssp-expired-banner is-block">
        {{ bannerOf(activeChatSide.expired_turns) }}
      </div>

      <div ref="chatScrollRef" class="ssp-chat-scroll">
        <div v-for="(m, i) in chatMsgs" :key="i" class="ssp-msg" :class="`is-${m.role}`">
          <div class="ssp-msg-bubble" :class="{ 'is-error': m.isError }">
            {{ m.content }}<span v-if="m.pending" class="ssp-typing">▍</span>
          </div>
        </div>

        <!-- 写副作用确认（内联在气泡流里，FR-D5） -->
        <div v-if="pendingWrite" class="ssp-write-card">
          <div class="ssp-write-title">请求写操作</div>
          <div class="ssp-write-desc">{{ pendingWrite.tool_name }}：{{ pendingWrite.description }}</div>
          <div class="ssp-modal-actions">
            <button class="ssp-btn" :disabled="writeResolving" @click="resolveSideWrite('reject')">拒绝</button>
            <button class="ssp-btn is-primary" :disabled="writeResolving" @click="resolveSideWrite('approve')">
              {{ writeResolving ? '处理中…' : '批准本次' }}
            </button>
          </div>
        </div>
      </div>

      <div class="ssp-chat-toolbar">
        <button class="ssp-history-btn" title="对话历史" @click="backToList">
          <MessageSquare :size="12" /> 对话历史
        </button>
      </div>
      <div class="ssp-chat-compose">
        <input
          v-model="chatInput"
          class="ssp-chat-input"
          :placeholder="activeChatSide.status === 'archived' ? '侧会话已归档，不能继续对话' : '与侧会话对话…'"
          :disabled="activeChatSide.status === 'archived' || sending"
          @keydown.enter.exact.prevent="sendChat"
        />
        <button class="ssp-chat-send" :disabled="!chatInput.trim() || sending || !!pendingWrite" title="发送" @click="sendChat">
          <Send :size="13" />
        </button>
      </div>
    </template>

    <!-- ═══ 列表模式：对话历史 ═══ -->
    <template v-else>
      <div class="ssp-header">
        <span class="ssp-title">对话历史</span>
        <button class="ssp-create-btn" title="新建侧会话" @click="openCreate">
          <Plus :size="14" /> 新建
        </button>
        <button class="ssp-close" title="关闭面板" @click="emit('close')">
          <X :size="14" />
        </button>
      </div>

      <!-- 新建弹窗（面板内浮层） -->
      <div v-if="showCreate" class="ssp-modal">
        <div class="ssp-modal-card">
          <div class="ssp-modal-title">新建侧会话</div>
          <div class="ssp-field">
            <span class="ssp-field-label">继承粒度</span>
            <div class="ssp-granularity-row">
              <button
                v-for="(label, key) in GRANULARITY_LABELS"
                :key="key"
                class="ssp-granularity-option"
                :class="[`ssp-granularity-${key}`, { 'is-on': granularity === key }]"
                @click="granularity = key as any"
              >{{ label }}</button>
            </div>
          </div>
          <div v-if="granularity === 'recent_n'" class="ssp-field">
            <span class="ssp-field-label">携带最近轮数</span>
            <div class="ssp-recent-n">
              <input v-model.number="recentN" type="number" min="5" max="50" />
              <span class="ssp-field-hint">5–50，默认 10</span>
            </div>
          </div>
          <p class="ssp-token-hint">
            快照内容超过约 6.4 万字符（high 档）时，每轮对话将携带约 32k tokens 背景开销
          </p>
          <p v-if="createError" class="ssp-error">{{ createError }}</p>
          <div class="ssp-modal-actions">
            <button class="ssp-btn" @click="showCreate = false">取消</button>
            <button class="ssp-btn ssp-submit is-primary" :disabled="creating" @click="submitCreate">
              {{ creating ? '创建中…' : '创建' }}
            </button>
          </div>
        </div>
      </div>

      <!-- 删除二次确认 -->
      <div v-if="pendingDeleteId" class="ssp-modal">
        <div class="ssp-modal-card">
          <div class="ssp-modal-title">删除侧会话</div>
          <p class="ssp-modal-msg">删除后该侧会话及其全部对话不可恢复，确定删除？</p>
          <div class="ssp-modal-actions">
            <button class="ssp-btn" @click="pendingDeleteId = null">取消</button>
            <button class="ssp-btn ssp-confirm-delete is-danger" @click="confirmRemove">删除</button>
          </div>
        </div>
      </div>

      <!-- 列表 -->
      <div class="ssp-list">
        <p v-if="store.loading" class="ssp-empty">加载中…</p>
        <p v-else-if="store.sides.length === 0" class="ssp-empty">暂无侧会话。侧会话基于主任务某一时刻的快照独立探索，结论可提炼后带回。</p>
        <div
          v-for="side in store.sides"
          :key="side.id"
          class="ssp-item"
          :class="{ 'is-active': side.id === activeChatSide?.id }"
        >
          <template v-if="renamingId === side.id">
            <input v-model="renameText" class="ssp-rename-input" @keyup.enter="submitRename" />
            <button class="ssp-item-btn" @click="submitRename">保存</button>
          </template>
          <template v-else>
            <div class="ssp-item-head" @click="enterChat(side)">
              <MessageSquare :size="13" class="ssp-item-icon" />
              <span class="ssp-item-title">{{ side.title }}</span>
              <span v-if="side.status === 'archived'" class="ssp-badge is-archived">已归档</span>
              <span v-if="side.has_side_effects" class="ssp-badge is-effect">已产生写副作用</span>
            </div>
            <div class="ssp-item-meta">
              <span>基于主任务第 {{ side.baseline_turn }} 轮 · {{ GRANULARITY_SHORT[side.granularity] || side.granularity }}</span>
              <span v-if="side.token_level === 'high'" class="ssp-token-warn">高 token 开销</span>
            </div>
            <div v-if="side.status === 'active' && bannerOf(side.expired_turns)" class="ssp-expired-banner">
              {{ bannerOf(side.expired_turns) }}
            </div>
            <div class="ssp-item-actions">
              <button class="ssp-item-btn" title="打开" @click="enterChat(side)">
                <MessageSquare :size="12" /> 打开
              </button>
              <button class="ssp-item-btn" title="重命名" @click="startRename(side.id, side.title)">
                <Pencil :size="12" />
              </button>
              <button class="ssp-item-btn" title="带回主任务" @click="startBringBack(side.id)">
                <CornerUpLeft :size="12" /> 带回
              </button>
              <button class="ssp-item-btn ssp-item-delete" title="删除" @click="pendingDeleteId = side.id">
                <Trash2 :size="12" />
              </button>
            </div>
          </template>
        </div>
      </div>
    </template>

    <!-- 带回弹窗 -->
    <div v-if="bringingBackSide" class="ssp-modal">
      <div class="ssp-modal-card is-wide">
        <div class="ssp-modal-title">
          <Sparkles :size="14" /> 带回「{{ bringingBackSide.title }}」的结论到主任务
        </div>
        <div class="ssp-bring-body">
          <div class="ssp-bring-toolbar">
            <button class="ssp-btn" :disabled="draftLoading" @click="loadDraft">
              {{ draftLoading ? '提炼中…' : '生成摘要草稿' }}
            </button>
            <span class="ssp-field-hint">可编辑后带回，以一条特殊消息注入主任务</span>
          </div>
          <textarea
            v-model="digestText"
            class="ssp-digest-text"
            rows="6"
            placeholder="结论摘要（留空则先点「生成摘要草稿」）"
          />
          <p v-if="bringError" class="ssp-error">{{ bringError }}</p>
        </div>
        <div class="ssp-modal-actions">
          <button class="ssp-btn" @click="bringingBackId = null">取消</button>
          <button class="ssp-btn is-primary" :disabled="bringing || !digestText.trim()" @click="submitBringBack">
            {{ bringing ? '带回中…' : '带回主任务' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.ssp-panel {
  position: absolute;
  top: 12px;
  right: 16px;
  width: 340px;
  /* 固定高度：空列表/短列表时面板不得塌缩，否则内嵌弹窗会被裁剪 */
  height: 480px;
  display: flex;
  flex-direction: column;
  background: var(--rl-panel-bg, #fff);
  border: 1px solid var(--rl-border, rgba(0, 0, 0, 0.08));
  border-radius: 12px;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.12);
  z-index: 40;
  overflow: hidden;
}
.ssp-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  border-bottom: 1px solid var(--rl-border, rgba(0, 0, 0, 0.06));
}
.ssp-title { font-size: 13px; font-weight: 600; flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ssp-create-btn, .ssp-close {
  display: inline-flex; align-items: center; gap: 4px;
  font-size: 12px; padding: 4px 8px; border-radius: 8px;
  border: 0.5px solid var(--rl-border, rgba(0,0,0,0.1)); background: transparent; cursor: pointer;
  flex-shrink: 0;
}
.ssp-create-btn:hover { background: var(--rl-bg-secondary, rgba(0,0,0,0.04)); }
.ssp-close { padding: 4px; }

/* ── 对话模式 ── */
.ssp-chat-scroll {
  flex: 1;
  overflow-y: auto;
  padding: 10px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.ssp-msg { display: flex; }
.ssp-msg.is-user { justify-content: flex-end; }
.ssp-msg.is-assistant { justify-content: flex-start; }
.ssp-msg-bubble {
  max-width: 86%;
  font-size: 12.5px;
  line-height: 1.6;
  padding: 6px 10px;
  border-radius: 10px;
  white-space: pre-wrap;
  word-break: break-word;
}
.ssp-msg.is-user .ssp-msg-bubble { background: var(--rl-accent, #6366f1); color: #fff; border-bottom-right-radius: 4px; }
.ssp-msg.is-assistant .ssp-msg-bubble { background: var(--rl-bg-secondary, rgba(0,0,0,0.05)); color: var(--rl-text, #222); border-bottom-left-radius: 4px; }
.ssp-msg-bubble.is-error { background: rgba(220,38,38,0.08); color: #dc2626; }
.ssp-typing { opacity: 0.6; animation: ssp-blink 1s infinite; }
@keyframes ssp-blink { 50% { opacity: 0.15; } }

.ssp-write-card {
  border: 1px solid rgba(245, 158, 11, 0.4);
  background: rgba(245, 158, 11, 0.06);
  border-radius: 10px;
  padding: 10px;
}
.ssp-write-title { font-size: 12px; font-weight: 600; color: #b45309; margin-bottom: 4px; }
.ssp-write-desc { font-size: 12px; color: var(--rl-text, #444); line-height: 1.5; }

.ssp-chat-toolbar {
  display: flex;
  padding: 4px 10px 0;
}
.ssp-history-btn {
  display: inline-flex; align-items: center; gap: 4px;
  font-size: 11px; padding: 3px 8px; border-radius: 6px; border: none;
  background: transparent; color: var(--rl-text-secondary, #888); cursor: pointer;
}
.ssp-history-btn:hover { background: rgba(0,0,0,0.05); color: var(--rl-text, #222); }
.ssp-chat-compose {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 10px 10px;
}
.ssp-chat-input {
  flex: 1;
  font-size: 12.5px;
  padding: 7px 10px;
  border-radius: 10px;
  border: 1px solid var(--rl-border, rgba(0,0,0,0.14));
  background: var(--rl-bg-secondary, rgba(0,0,0,0.02));
}
.ssp-chat-input:focus { outline: none; border-color: var(--rl-accent, #6366f1); }
.ssp-chat-send {
  display: inline-flex; align-items: center; justify-content: center;
  width: 30px; height: 30px; border-radius: 10px; border: none;
  background: var(--rl-accent, #6366f1); color: #fff; cursor: pointer; flex-shrink: 0;
}
.ssp-chat-send:disabled { opacity: 0.4; cursor: not-allowed; }

.ssp-expired-banner {
  font-size: 11px; padding: 4px 8px; border-radius: 6px;
  background: rgba(220, 38, 38, 0.08); color: #dc2626;
}
.ssp-expired-banner.is-block { margin: 8px 10px 0; }

/* ── 列表模式 ── */
.ssp-list { flex: 1; overflow-y: auto; padding: 8px; display: flex; flex-direction: column; gap: 8px; }
.ssp-empty { font-size: 12px; color: var(--rl-text-secondary, #888); padding: 12px 8px; line-height: 1.6; }
.ssp-item {
  border: 1px solid var(--rl-border, rgba(0,0,0,0.07));
  border-radius: 10px;
  padding: 8px 10px;
  background: var(--rl-side-tint, rgba(99, 102, 241, 0.03));
}
.ssp-item.is-active { border-color: var(--rl-accent, #6366f1); }
.ssp-item-head { display: flex; align-items: center; gap: 6px; cursor: pointer; }
.ssp-item-icon { flex-shrink: 0; color: var(--rl-accent, #6366f1); }
.ssp-item-title { font-size: 13px; font-weight: 500; flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ssp-badge {
  font-size: 10px; padding: 1px 6px; border-radius: 6px; flex-shrink: 0;
  background: rgba(0,0,0,0.06); color: var(--rl-text-secondary, #666);
}
.ssp-badge.is-archived { background: rgba(0,0,0,0.08); }
.ssp-badge.is-effect { background: rgba(245, 158, 11, 0.15); color: #b45309; }
.ssp-item-meta { display: flex; gap: 8px; font-size: 11px; color: var(--rl-text-secondary, #888); margin-top: 4px; }
.ssp-token-warn { color: #dc2626; }
.ssp-item-actions { display: flex; gap: 4px; margin-top: 6px; }
.ssp-item-btn {
  display: inline-flex; align-items: center; gap: 3px;
  font-size: 11px; padding: 3px 6px; border-radius: 6px; border: none;
  background: transparent; color: var(--rl-text-secondary, #666); cursor: pointer;
}
.ssp-item-btn:hover { background: rgba(0,0,0,0.05); color: var(--rl-text, #222); }
.ssp-item-delete:hover { color: #dc2626; }
.ssp-rename-input { flex: 1; font-size: 13px; padding: 4px 8px; border-radius: 6px; border: 1px solid var(--rl-accent, #6366f1); }

.ssp-modal {
  position: absolute; inset: 0; z-index: 10;
  background: rgba(0,0,0,0.3);
  display: flex; align-items: center; justify-content: center;
  /* 弹窗兜底滚动：内容超出面板高度时可滚动查看，不再裁剪 */
  overflow-y: auto;
}
.ssp-modal-card {
  width: calc(100% - 32px); max-width: 300px;
  max-height: calc(100% - 24px);
  overflow-y: auto;
  background: var(--rl-panel-bg, #fff); border-radius: 12px; padding: 14px;
  flex-shrink: 0;
}
.ssp-modal-card.is-wide { max-width: 320px; }
.ssp-modal-title { font-size: 13px; font-weight: 600; display: flex; align-items: center; gap: 6px; margin-bottom: 10px; }
.ssp-modal-msg { font-size: 12px; color: var(--rl-text-secondary, #555); margin: 0 0 12px; line-height: 1.6; }
.ssp-modal-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 12px; }
.ssp-btn {
  font-size: 12px; padding: 5px 12px; border-radius: 8px;
  border: 0.5px solid var(--rl-border, rgba(0,0,0,0.12)); background: transparent; cursor: pointer;
}
.ssp-btn.is-primary { background: var(--rl-accent, #6366f1); color: #fff; border-color: transparent; }
.ssp-btn.is-danger { background: #dc2626; color: #fff; border-color: transparent; }
.ssp-btn:disabled { opacity: 0.5; cursor: not-allowed; }

.ssp-field { margin-bottom: 10px; }
.ssp-field-label { display: block; font-size: 12px; color: var(--rl-text-secondary, #666); margin-bottom: 6px; }
.ssp-granularity-row { display: flex; flex-direction: column; gap: 4px; }
.ssp-granularity-option {
  text-align: left; font-size: 12px; padding: 6px 10px; border-radius: 8px;
  border: 1px solid var(--rl-border, rgba(0,0,0,0.1)); background: transparent; cursor: pointer;
}
.ssp-granularity-option.is-on { border-color: var(--rl-accent, #6366f1); background: rgba(99,102,241,0.06); }
.ssp-recent-n { display: flex; align-items: center; gap: 8px; }
.ssp-recent-n input { width: 72px; padding: 4px 8px; border-radius: 6px; border: 1px solid var(--rl-border, rgba(0,0,0,0.15)); }
.ssp-field-hint { font-size: 11px; color: var(--rl-text-secondary, #999); }
.ssp-token-hint { font-size: 11px; color: var(--rl-text-secondary, #999); margin: 0 0 4px; line-height: 1.5; }
.ssp-error { font-size: 12px; color: #dc2626; margin: 6px 0 0; }

.ssp-bring-toolbar { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.ssp-digest-text {
  width: 100%; box-sizing: border-box; font-size: 12px; line-height: 1.6;
  padding: 8px; border-radius: 8px; border: 1px solid var(--rl-border, rgba(0,0,0,0.15));
  resize: vertical;
}
</style>
