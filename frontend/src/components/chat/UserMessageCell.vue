<template>
  <div class="rl-user-row">
    <div class="rl-user-stack" :class="{ 'rl-user-stack--editing': editing }">
      <!-- 编辑态：任务已停，textarea 原位改写 -->
      <div v-if="editing" class="rl-user-edit">
        <textarea
          v-model="editDraft"
          class="rl-user-edit-area"
          rows="3"
          @keydown.enter.exact.prevent="confirmEdit"
          @keydown.esc="editing = false"
        ></textarea>
        <div class="rl-user-edit-actions">
          <button class="rl-act-btn" @click="editing = false">取消</button>
          <button class="rl-act-btn rl-user-edit-send" :disabled="!editDraft.trim()" @click="confirmEdit">
            发送新消息
          </button>
        </div>
      </div>
      <div v-else class="rl-bubble" :class="{ 'rl-bubble-attach-only': !displayText }">
        <!-- 随消息一起发出的图片：缩略图直接进气泡，和文字同属一条消息 -->
        <div v-if="attachments.length" class="rl-user-attach">
          <a
            v-for="a in attachments"
            :key="a.asset_id"
            class="rl-user-attach-item"
            :href="a.original_url || a.thumbnail_url || undefined"
            :title="a.filename || '配图'"
            target="_blank"
            rel="noopener noreferrer"
          >
            <img :src="a.thumbnail_url || a.original_url" :alt="a.filename || '配图'" loading="lazy" />
          </a>
        </div>
        <div v-if="displayText" class="rl-user-text" v-html="renderUserHtml(displayText)"></div>
      </div>
      <div class="rl-user-ops">
        <button class="rl-act-btn" title="复制" @click="copyText"><Copy :size="14" /></button>
        <button class="rl-act-btn" title="修改（将停止进行中的任务）" @click="startEdit"><Pencil :size="14" /></button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { Copy, Pencil } from 'lucide-vue-next'
import { renderUserHtml, stripSendHints } from './chat-content-renderer'
import { useChatRenderContext } from './chat-context'
import type { ChatMessage, UserAttachment } from './cell-types'

const props = defineProps<{
  msg: ChatMessage
}>()

const emit = defineEmits<{
  resend: [text: string]
}>()

const ctx = useChatRenderContext()
const editing = ref(false)
const editDraft = ref('')

function copyText() {
  ctx.copyMessage(props.msg.content || '')
}
/** 修改：先停止进行中的任务，再进入原位编辑 */
function startEdit() {
  ctx.stopActiveStream?.()
  editDraft.value = stripSendHints(props.msg.content || '')
  editing.value = true
}
/** 确认：以新消息为主注入（全新一轮对话） */
function confirmEdit() {
  const text = editDraft.value.trim()
  if (!text) return
  editing.value = false
  emit('resend', text)
}

// 正文里给 LLM 的 asset_ids / 选题参考注入行不展示（图片已由 attachments 渲染）
const displayText = computed(() => stripSendHints(props.msg.content || ''))
const attachments = computed<UserAttachment[]>(() =>
  Array.isArray(props.msg.attachments) ? props.msg.attachments : [],
)
</script>

<style scoped>
/* 随消息发出的图片网格：右对齐，最多 3 列，超过自动换行 */
.rl-user-attach {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 6px;
}

/* 只有图片没有文字时不需要下间距 */
.rl-user-attach:not(:last-child) {
  margin-bottom: 8px;
}

.rl-user-attach-item {
  display: block;
  width: 76px;
  height: 76px;
  flex: none;
  border-radius:12px;
  overflow: hidden;
  border: 1px solid var(--rl-border-thin, rgba(0, 0, 0, 0.08));
  background: var(--rl-hover-solid, #F5F5F5);
  transition: opacity 0.12s ease;
}

.rl-user-attach-item:hover {
  opacity: 0.88;
}

.rl-user-attach-item img {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: cover;
}

/* 纯图片消息：气泡收窄成只包住缩略图 */
.rl-bubble-attach-only {
  padding: 6px;
}

.rl-user-text {
  min-width: 0;
}


/* ── 自 chat-view.css 死区迁入（2026-10-20 系统性排查）──
   .rl-user-stack / .rl-bubble 是本组件内部节点，chat-view.css（ChatView
   scoped）里的规则拿不到 data-v，从未生效；迁入后真正生效。 */
.rl-user-stack {
  min-width: 0;
  max-width: min(525px, 82%);
}

/* 编辑态必须显式定宽：.rl-user-stack 处在 column flex（align-items:flex-end）
   里是 shrink-to-fit，内部 textarea 的 width:100% 参照的父宽又由内容决定，
   循环解析后 textarea 塌缩到浏览器默认 ~180px，文本挤成竖条、
   「发送新消息」按钮被裁掉。给 stack 自身定宽（% 参照 .rl-user-row，
   与普通气泡的 max-width 同口径），编辑框和气泡一样宽。 */
.rl-user-stack--editing {
  width: min(525px, 82%);
}

.rl-bubble {
  background: var(--rl-bubble);
  max-width: 100%;
  color: var(--rl-text-1);
  border: 1px solid var(--rl-border);
  word-break: break-word;
  text-align: left;
  border-radius:24px 20px 8px;
  padding: 10px 14px;
  font-size: 14px;
  line-height: 1.428;
  text-wrap: pretty;
}

.rl-bubble p {
  margin: 0;
}

.rl-bubble p + p {
  margin-top: 6px;
}
</style>
