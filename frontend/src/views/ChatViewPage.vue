<template>
<AppShell
  current-page="chat"
  root-class="cvp-root"
  @nav-click="handleNavClick"
  @new-chat="handleNewChat"
  @exit-creation="handleExitCreation"
  @delete-chat="handleDeleteChat"
  @select-conversation="handleSelectConversation"
  @new-work-conversation="handleNewWorkConversation"
  @new-chat-in-folder="handleNewChatInFolder"
  @select-file="handleSelectFile"
  @select-folder="handleSelectFolder"
>
    <main class="wf-page">
      <div class="page-container page-container-chat">
        <ChatView ref="chatRef" :content-mode="contentMode" />
      </div>
    </main>
</AppShell>
</template>

<script setup lang="ts">
import { ref, computed, nextTick, onMounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import AppShell from '@/components/layout/AppShell.vue'
import ChatView from '@/components/chat/ChatView.vue'
import { useFileStore } from '@/stores/files'
import { useWorkStore } from '@/stores/work'

const router = useRouter()
const route = useRoute()
const chatRef = ref<InstanceType<typeof ChatView> | null>(null)
const fileStore = useFileStore()
const workStore = useWorkStore()

/* 侧边栏切到「内容」Tab 时，chat 页面进入内容模式：
   隐藏创作灵感/能力标签/起手式，问候语切到改稿与发布场景。 */
const contentMode = computed(() => workStore.sidebarTab === 'content')

function handleNewChat() {
  nextTick(() => {
    if (chatRef.value) {
      chatRef.value.newConversation()
    } else {
      setTimeout(() => { chatRef.value?.newConversation() }, 300)
    }
  })
}

function handleDeleteChat(convId: string) {
  if (chatRef.value) {
    chatRef.value.deleteConversation(convId)
  } else {
    setTimeout(() => { chatRef.value?.deleteConversation(convId) }, 300)
  }
}

/* 从「内容」Tab 切回「会话」：作品的解绑由 workStore.switchSidebarTab 完成，
   这里只负责把 chat 重置回默认问候对话框（清会话绑定、收起作品详情）。
   该事件仅在确实解绑了作品时才由侧栏发出，所以不会误伤用户正在看的会话。 */
function handleExitCreation() {
  nextTick(() => {
    if (chatRef.value) {
      chatRef.value.resetToInitial()
    } else {
      setTimeout(() => { chatRef.value?.resetToInitial() }, 300)
    }
  })
}

function handleSelectConversation(convId: string) {
  nextTick(() => {
    if (chatRef.value) {
      chatRef.value.switchToConversation(convId)
    } else {
      setTimeout(() => { chatRef.value?.switchToConversation(convId) }, 300)
    }
  })
}

/** 内容 tab 笔记子列表「添加会话」：在该作品名下新建空草稿会话（带作品上下文）。 */
function handleNewWorkConversation(workId: string) {
  nextTick(() => {
    if (chatRef.value) {
      chatRef.value.newConversation(undefined, workId)
    } else {
      setTimeout(() => { chatRef.value?.newConversation(undefined, workId) }, 300)
    }
  })
}

function handleNewChatInFolder(folderId: string) {
  nextTick(() => {
    if (chatRef.value) {
      chatRef.value.newConversation(folderId)
    } else {
      setTimeout(() => { chatRef.value?.newConversation(folderId) }, 300)
    }
  })
}

function handleSelectFile(fileId: string) {
  fileStore.setActiveFile(fileId)
  const sessionId = fileStore.getFileSessionId(fileId)
  nextTick(() => {
    if (chatRef.value) {
      if (sessionId) {
        chatRef.value.switchToConversation(sessionId)
      } else {
        const file = fileStore.activeFile
        if (file) {
          chatRef.value.newConversation(file.folderId)
        }
      }
    }
  })
}

function handleSelectFolder(folderId: string) {
  nextTick(() => {
    if (chatRef.value) {
      chatRef.value.switchToFolder(folderId)
    } else {
      setTimeout(() => { chatRef.value?.switchToFolder(folderId) }, 300)
    }
  })
}

function handleNavClick(pageName: string) {
  if (pageName === 'chat') return
  if (pageName === 'home') { router.push('/').catch(() => {}); return }
  if (pageName === 'my-works') { router.push('/my-works').catch(() => {}); return }
  if (pageName === 'portfolio') { router.push('/portfolio').catch(() => {}); return }
  if (pageName === 'topic-pool') { router.push('/topic-pool').catch(() => {}); return }
  if (pageName === 'task-plans') { router.push('/task-plans').catch(() => {}); return }
  router.push({ path: '/', query: { page: pageName } })
}

function _queryStr(v: unknown): string {
  return typeof v === 'string' ? v : ''
}

function applyPendingSkill() {
  const skillName = _queryStr(route.query.skill)
  if (!skillName) return
  const payload = {
    node_type: _queryStr(route.query.skillType),
    name: skillName,
    display_name: _queryStr(route.query.skillName) || skillName,
    description: _queryStr(route.query.skillDesc),
  }
  const applyWithRetry = (retries: number) => {
    if (chatRef.value) {
      chatRef.value.activateSkill(payload)
      return
    }
    if (retries > 0) setTimeout(() => applyWithRetry(retries - 1), 200)
  }
  nextTick(() => applyWithRetry(5))
}

function applyPendingAgent() {
  const agentId = _queryStr(route.query.agent)
  if (!agentId) return
  const applyWithRetry = (retries: number) => {
    if (chatRef.value) {
      chatRef.value.activateAgent(agentId)
      return
    }
    if (retries > 0) setTimeout(() => applyWithRetry(retries - 1), 200)
  }
  nextTick(() => applyWithRetry(5))
}

onMounted(() => {
  const hasPending = Boolean(_queryStr(route.query.skill) || _queryStr(route.query.agent))
  applyPendingSkill()
  applyPendingAgent()
  if (hasPending) router.replace({ path: '/chat' }).catch(() => {})
})
</script>

<style scoped>
/* 页面外框与全站功能页共用同一基底灰（--ma-bg-base，即 body 的 #F5F5F7）。
   旧注释假设"其它功能页面是白底"是错的——首页/我的空间/选题池等全是灰底+白卡，
   chat 是唯一白外框的页面，这正是"开启新创作后整背景色和别处不一致"的来源。
   聊天画布（.rl-chat 的 --rl-bg）已取同一个灰，全站功能页只剩一种灰。 */
.cvp-root {
  background: var(--ma-bg-base, #EBEEF2);
}
/* 本页拖拽条热区（left:0/right:-3px）与 hover 深浅（0.08）与其它页略有差异，保留局部覆盖；
   其余栅格与拖拽条基础规则已收敛到全局 _layout.css。 */
:deep(.da-sidebar-resizer)::before { content: ''; position: absolute; top: 0; bottom: 0; left: 0; right: -3px; z-index: 1; }
:deep(.da-sidebar-resizer):hover { background: rgba(0, 0, 0, 0.08); }

.wf-page { flex: 1; min-width: 0; height: 100%; display: flex; flex-direction: column; overflow: hidden; padding: 28px 20px 16px; }
.page-container { display: flex; flex-direction: column; flex: 1; min-height: 0; min-width: 0; overflow: hidden; }
.page-container-chat { max-width: none; align-items: stretch; gap: 0; padding: 0; height: 100%; box-sizing: border-box; border-radius:20px 0 0 16px; }
</style>