<template>
  <div class="fm">
    <div v-if="fileStore.folders.length === 0" class="fm-empty">
      <FolderOpen :size="26" :stroke-width="1.2" />
      <span class="fm-empty-title">暂无素材文件夹</span>
      <span class="fm-empty-desc">在左侧素材区导入文件夹后，可在这里展开查看</span>
    </div>

    <div v-for="folder in fileStore.folders" :key="folder.id" class="fm-folder">
      <button class="fm-folder-row" @click="toggleFolder(folder)">
        <ChevronDown v-if="expandedFolderIds.has(folder.id)" :size="12" :stroke-width="1.8" class="fm-chevron" />
        <ChevronRight v-else :size="12" :stroke-width="1.8" class="fm-chevron" />
        <Folder :size="14" :stroke-width="1.6" class="fm-folder-icon" />
        <span class="fm-folder-name">{{ folder.name }}</span>
        <Loader2 v-if="loadingFolderIds.has(folder.id)" :size="12" class="fm-spin fm-folder-count" />
        <span v-else-if="countByFolder[folder.id] !== null" class="fm-folder-count">{{ countByFolder[folder.id] }}</span>
      </button>

      <div v-if="expandedFolderIds.has(folder.id)" class="fm-children">
        <div v-if="folderErrors[folder.id]" class="fm-error">{{ folderErrors[folder.id] }}</div>
        <div v-else-if="(rowsByFolder[folder.id] || []).length === 0" class="fm-empty-inner">
          {{ loadingFolderIds.has(folder.id) ? '读取中…' : '空文件夹' }}
        </div>

        <button
          v-for="row in rowsByFolder[folder.id] || []"
          :key="row.key"
          class="fm-row"
          :class="{ 'fm-row-active': selectedKey === row.key, 'fm-row-dir': row.node.isDir }"
          :style="{ paddingLeft: 12 + row.depth * 14 + 'px' }"
          @click="onRowClick(folder, row)"
        >
          <template v-if="row.node.isDir">
            <Loader2 v-if="loadingDirKeys.has(dirKey(folder.id, row.node))" :size="12" class="fm-spin" />
            <ChevronDown v-else-if="expandedDirKeys.has(dirKey(folder.id, row.node))" :size="12" :stroke-width="1.8" />
            <ChevronRight v-else :size="12" :stroke-width="1.8" />
            <Folder :size="14" :stroke-width="1.5" class="fm-row-icon" />
          </template>
          <FileText v-else :size="14" :stroke-width="1.5" class="fm-row-icon" />
          <span class="fm-row-name">{{ row.node.name }}</span>
          <span v-if="!row.node.isDir && row.node.size > 0" class="fm-row-size">{{ fileStore.formatFileSize(row.node.size) }}</span>
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { ChevronDown, ChevronRight, FileText, Folder, FolderOpen, Loader2 } from 'lucide-vue-next'
import { listFiles } from '@/api/workspace'
import { useChatContextStore } from '@/stores/chatContext'
import { useFileStore, type FolderItem } from '@/stores/files'
import {
  buildFileNodes,
  flattenFileTree,
  type FileTreeNode,
  type FlatFileRow,
} from './file-tree'

const WS_FOLDER_PREFIX = 'ws-'

const fileStore = useFileStore()
const ctxStore = useChatContextStore()

const expandedFolderIds = reactive(new Set<string>())
const expandedDirKeys = reactive(new Set<string>())
const loadingFolderIds = reactive(new Set<string>())
const loadingDirKeys = reactive(new Set<string>())
const folderErrors = reactive<Record<string, string>>({})
const wsRoots = reactive<Record<string, FileTreeNode[]>>({})
const selectedKey = ref('')

function isWsFolder(folder: FolderItem): boolean {
  return folder.id.startsWith(WS_FOLDER_PREFIX)
}

function wsIdOf(folder: FolderItem): string {
  return folder.id.slice(WS_FOLDER_PREFIX.length)
}

function dirKey(folderId: string, node: FileTreeNode): string {
  return `${folderId}:${node.path}`
}

async function loadRoot(folder: FolderItem) {
  if (!isWsFolder(folder) || wsRoots[folder.id]) return
  loadingFolderIds.add(folder.id)
  folderErrors[folder.id] = ''
  try {
    const result = await listFiles(wsIdOf(folder), '', 1)
    wsRoots[folder.id] = buildFileNodes(result.dirs ?? [], result.files ?? [])
  } catch {
    folderErrors[folder.id] = '读取文件夹失败'
  } finally {
    loadingFolderIds.delete(folder.id)
  }
}

async function toggleFolder(folder: FolderItem) {
  if (expandedFolderIds.has(folder.id)) {
    expandedFolderIds.delete(folder.id)
    return
  }
  expandedFolderIds.add(folder.id)
  await loadRoot(folder)
}

async function toggleDir(folder: FolderItem, node: FileTreeNode) {
  const key = dirKey(folder.id, node)
  if (expandedDirKeys.has(key)) {
    expandedDirKeys.delete(key)
    return
  }
  expandedDirKeys.add(key)
  if (node.loaded) return

  loadingDirKeys.add(key)
  folderErrors[folder.id] = ''
  try {
    const result = await listFiles(wsIdOf(folder), node.path, 1)
    node.children = buildFileNodes(result.dirs ?? [], result.files ?? [])
    node.loaded = true
  } catch {
    folderErrors[folder.id] = `读取 ${node.name} 失败`
  } finally {
    loadingDirKeys.delete(key)
  }
}

function rowsFor(folder: FolderItem): FlatFileRow[] {
  if (isWsFolder(folder)) {
    const nodes = wsRoots[folder.id]
    if (!nodes) return []
    return flattenFileTree(
      nodes,
      node => expandedDirKeys.has(dirKey(folder.id, node)),
      0,
      `${folder.id}:`,
    )
  }
  return fileStore.getFilesForFolder(folder.id).map(file => ({
    node: {
      name: file.name,
      path: file.id,
      isDir: false,
      size: file.size,
      loaded: true,
      children: [],
    },
    depth: 0,
    key: `${folder.id}:${file.id}`,
  }))
}

const rowsByFolder = computed<Record<string, FlatFileRow[]>>(() => {
  const map: Record<string, FlatFileRow[]> = {}
  for (const folder of fileStore.folders) {
    map[folder.id] = rowsFor(folder)
  }
  return map
})

const countByFolder = computed<Record<string, number | null>>(() => {
  const map: Record<string, number | null> = {}
  for (const folder of fileStore.folders) {
    if (isWsFolder(folder)) {
      map[folder.id] = wsRoots[folder.id] ? wsRoots[folder.id].length : null
    } else {
      map[folder.id] = fileStore.getFilesForFolder(folder.id).length
    }
  }
  return map
})

function onRowClick(folder: FolderItem, row: FlatFileRow) {
  if (row.node.isDir) {
    toggleDir(folder, row.node)
    return
  }
  selectedKey.value = row.key
  if (isWsFolder(folder)) {
    const wsId = wsIdOf(folder)
    ctxStore.addContextItem({
      type: 'file',
      label: row.node.name,
      summary: `${folder.name} / ${row.node.path}`,
      pinned: true,
      meta: {
        refId: `ws:${wsId}:${row.node.path}`,
        workspaceId: wsId,
        path: row.node.path,
        size: row.node.size,
      },
    })
    return
  }
  fileStore.setActiveFile(row.node.path)
}
</script>

<style scoped>
.fm {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 8px 8px 12px;
  overflow-y: auto;
  min-height: 0;
}

.fm-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  padding: 28px 12px;
  color: #bbb;
  text-align: center;
}
.fm-empty-title {
  font-size: 12px;
  color: #888;
}
.fm-empty-desc {
  font-size: 10.5px;
  line-height: 1.5;
  color: #aaa;
}

.fm-folder {
  border-radius: 10px;
}
.fm-folder-row {
  display: flex;
  align-items: center;
  gap: 5px;
  width: 100%;
  padding: 6px 8px 6px 12px;
  border: none;
  border-radius: 10px;
  background: transparent;
  color: #4b4b4b;
  font-family: inherit;
  font-size: 12px;
  font-weight: 500;
  text-align: left;
  cursor: pointer;
  transition: background 0.15s ease;
}
.fm-folder-row:hover {
  background: rgba(0, 0, 0, 0.035);
}
.fm-chevron {
  flex: none;
  color: #aaa;
}
.fm-folder-icon {
  flex: none;
  color: #f97316;
}
.fm-folder-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.fm-folder-count {
  flex: none;
  font-size: 9.5px;
  font-weight: 600;
  color: #aaa;
  background: rgba(0, 0, 0, 0.04);
  border-radius: 10px;
  padding: 1px 6px;
  min-width: 16px;
  text-align: center;
}

.fm-children {
  display: flex;
  flex-direction: column;
  gap: 1px;
  padding: 2px 0 4px;
}
.fm-row {
  display: flex;
  align-items: center;
  gap: 5px;
  width: 100%;
  padding: 5px 8px;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: #666;
  font-family: inherit;
  font-size: 11.5px;
  text-align: left;
  cursor: pointer;
  transition: background 0.12s ease, color 0.12s ease;
}
.fm-row:hover {
  background: rgba(0, 0, 0, 0.035);
  color: #1a1a1a;
}
.fm-row-active {
  background: rgba(249, 115, 22, 0.08);
  color: #ea580c;
}
.fm-row-dir {
  color: #4b4b4b;
  font-weight: 500;
}
.fm-row-icon {
  flex: none;
  color: #999;
}
.fm-row-dir .fm-row-icon {
  color: #f97316;
}
.fm-row-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.fm-row-size {
  flex: none;
  font-size: 9.5px;
  color: #aaa;
}
.fm-empty-inner {
  padding: 4px 12px 6px;
  font-size: 10.5px;
  color: #bbb;
}
.fm-error {
  padding: 4px 12px 6px;
  font-size: 10.5px;
  color: #dc2626;
}
.fm-spin {
  animation: fm-spin 1s linear infinite;
}
@keyframes fm-spin {
  to {
    transform: rotate(360deg);
  }
}

:global(.ctx-sidebar.is-dark) .fm-folder-row {
  color: #bbb;
}
:global(.ctx-sidebar.is-dark) .fm-folder-row:hover {
  background: rgba(255, 255, 255, 0.06);
}
:global(.ctx-sidebar.is-dark) .fm-folder-count {
  color: #666;
  background: rgba(255, 255, 255, 0.06);
}
:global(.ctx-sidebar.is-dark) .fm-row {
  color: #999;
}
:global(.ctx-sidebar.is-dark) .fm-row:hover {
  background: rgba(255, 255, 255, 0.06);
  color: #e0e0e0;
}
:global(.ctx-sidebar.is-dark) .fm-row-dir {
  color: #bbb;
}
:global(.ctx-sidebar.is-dark) .fm-row-size {
  color: #666;
}
:global(.ctx-sidebar.is-dark) .fm-empty-title {
  color: #888;
}
:global(.ctx-sidebar.is-dark) .fm-empty-desc,
:global(.ctx-sidebar.is-dark) .fm-empty-inner {
  color: #555;
}
</style>