<template>
  <Teleport to="body">
    <transition name="fmd">
      <div v-if="visible" class="fmd-overlay" @click.self="emit('close')">
        <div class="fmd-panel">
          <div class="fmd-head">
            <div class="fmd-head-title">
              <FolderCog :size="16" :stroke-width="1.6" />
              <span>文件夹管理</span>
            </div>
            <button class="fmd-close" @click="emit('close')">
              <X :size="16" :stroke-width="1.6" />
            </button>
          </div>

          <div v-if="fileStore.folders.length === 0" class="fmd-empty">
            暂无素材文件夹
          </div>

          <div v-else class="fmd-list">
            <div v-for="folder in fileStore.folders" :key="folder.id" class="fmd-item">
              <div class="fmd-item-main">
                <input
                  v-if="editingId === folder.id"
                  ref="editInput"
                  v-model="editingName"
                  class="fmd-rename-input"
                  @keydown.enter="commitRename(folder)"
                  @keydown.esc="cancelRename"
                  @blur="commitRename(folder)"
                />
                <span v-else class="fmd-item-name" @dblclick="startRename(folder)">{{ folder.name }}</span>
                <span class="fmd-item-path">{{ folder.path || '未关联本地路径' }}</span>
              </div>
              <div class="fmd-item-actions">
                <button class="fmd-act" title="重命名" @click="startRename(folder)">
                  <PencilLine :size="14" :stroke-width="1.6" />
                </button>
                <button class="fmd-act" title="在资源管理器打开" @click="onReveal(folder)">
                  <FolderOpen :size="14" :stroke-width="1.6" />
                </button>
                <button class="fmd-act fmd-act-danger" title="删除" @click="onRemove(folder)">
                  <Trash2 :size="14" :stroke-width="1.6" />
                </button>
              </div>
            </div>
          </div>

          <div class="fmd-foot">
            <button class="fmd-btn" @click="onImport">
              <FolderPlus :size="14" :stroke-width="1.6" />
              <span>导入文件夹</span>
            </button>
            <button class="fmd-btn fmd-btn-primary" @click="emit('close')">完成</button>
          </div>
        </div>
      </div>
    </transition>
  </Teleport>
</template>

<script setup lang="ts">
import { nextTick, ref } from 'vue'
import { FolderCog, FolderOpen, FolderPlus, PencilLine, Trash2, X } from 'lucide-vue-next'
import { useFileStore, type FolderItem } from '@/stores/files'
import { useWorkspaceStore } from '@/stores/workspace'
import { isAbsoluteLocalPath, revealFolder } from '@/api/workspace'

defineProps<{ visible: boolean }>()
const emit = defineEmits<{ close: [] }>()

const fileStore = useFileStore()
const workspaceStore = useWorkspaceStore()

const WS_FOLDER_PREFIX = 'ws-'
const editingId = ref('')
const editingName = ref('')
const editInput = ref<HTMLInputElement | null>(null)

function isWsFolder(folder: FolderItem): boolean {
  return folder.id.startsWith(WS_FOLDER_PREFIX)
}

async function startRename(folder: FolderItem) {
  editingId.value = folder.id
  editingName.value = folder.name
  await nextTick()
  const raw: any = editInput.value
  const el: HTMLInputElement | null = Array.isArray(raw) ? (raw[0] ?? null) : raw
  el?.focus()
  el?.select()
}

function cancelRename() {
  editingId.value = ''
  editingName.value = ''
}

async function commitRename(folder: FolderItem) {
  if (editingId.value !== folder.id) return
  const name = editingName.value.trim()
  if (name && name !== folder.name) {
    if (isWsFolder(folder)) {
      await workspaceStore.renameWorkspace(folder.id.slice(WS_FOLDER_PREFIX.length), name)
    } else {
      fileStore.renameFolder(folder.id, name)
    }
  }
  cancelRename()
}

async function onReveal(folder: FolderItem) {
  if (!isAbsoluteLocalPath(folder.path)) return
  try {
    await revealFolder(folder.path)
  } catch {
    /* 忽略打开失败 */
  }
}

async function onRemove(folder: FolderItem) {
  if (isWsFolder(folder)) {
    await workspaceStore.unbind(folder.id.slice(WS_FOLDER_PREFIX.length))
  } else {
    fileStore.removeFolder(folder.id)
  }
}

async function onImport() {
  await fileStore.importFolder()
}
</script>

<style scoped>
.fmd-overlay {
  position: fixed;
  inset: 0;
  z-index: var(--z-top, 1000);
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(0, 0, 0, 0.28);
}
.fmd-panel {
  width: 480px;
  max-width: calc(100vw - 32px);
  max-height: 70vh;
  display: flex;
  flex-direction: column;
  background: #fff;
  border-radius: 16px;
  box-shadow: 0 16px 40px rgba(0, 0, 0, 0.18);
  overflow: hidden;
}
.fmd-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 16px;
  border-bottom: 1px solid #F0F0F0;
}
.fmd-head-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
  font-weight: 500;
  color: #2B2B2B;
}
.fmd-close {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  border: none;
  border-radius: 8px;
  background: none;
  color: #9A9A9A;
  cursor: pointer;
  transition: background 0.15s ease, color 0.15s ease;
}
.fmd-close:hover {
  background: #F5F5F5;
  color: #4B4B4B;
}
.fmd-empty {
  padding: 32px 16px;
  text-align: center;
  font-size: 13px;
  color: #9A9A9A;
}
.fmd-list {
  flex: 1;
  overflow-y: auto;
  padding: 8px;
}
.fmd-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 10px;
  border-radius: 10px;
  transition: background 0.15s ease;
}
.fmd-item:hover {
  background: #FAFAFA;
}
.fmd-item-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.fmd-item-name {
  font-size: 13px;
  font-weight: 500;
  color: #333;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  cursor: text;
}
.fmd-item-path {
  font-size: 11px;
  color: #A0A0A0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.fmd-rename-input {
  width: 100%;
  padding: 3px 8px;
  font-size: 13px;
  font-family: inherit;
  color: #333;
  border: 1px solid #E0E0E0;
  border-radius: 6px;
  outline: none;
}
.fmd-rename-input:focus {
  border-color: #F97316;
}
.fmd-item-actions {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
}
.fmd-act {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  border: none;
  border-radius: 8px;
  background: none;
  color: #9A9A9A;
  cursor: pointer;
  transition: background 0.15s ease, color 0.15s ease;
}
.fmd-act:hover {
  background: rgba(0, 0, 0, 0.05);
  color: #4B4B4B;
}
.fmd-act-danger:hover {
  background: rgba(220, 38, 38, 0.1);
  color: #DC2626;
}
.fmd-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  border-top: 1px solid #F0F0F0;
}
.fmd-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 7px 14px;
  font-size: 13px;
  font-family: inherit;
  color: #4B4B4B;
  background: #F5F5F5;
  border: none;
  border-radius: 9px;
  cursor: pointer;
  transition: background 0.15s ease;
}
.fmd-btn:hover {
  background: #EDEDED;
}
.fmd-btn-primary {
  color: #fff;
  background: #F97316;
}
.fmd-btn-primary:hover {
  background: #EA6A0C;
}
.fmd-enter-active,
.fmd-leave-active {
  transition: opacity 0.18s ease;
}
.fmd-enter-from,
.fmd-leave-to {
  opacity: 0;
}
</style>