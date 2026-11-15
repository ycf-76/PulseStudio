<template>
  <div class="fs-doc-panel">
    <!-- 工具栏：粘贴链接打开 -->
    <div class="fs-doc-toolbar">
      <input
        v-model="urlInput"
        class="fs-doc-input"
        placeholder="粘贴飞书文档链接（docx / wiki / base）"
        @keydown.enter="openFromInput"
      />
      <button class="fs-doc-btn" :disabled="isLoading || !urlInput.trim()" @click="openFromInput">打开</button>
    </div>

    <!-- 错误态 -->
    <div v-if="errorMsg" class="fs-doc-state">
      <FileWarning :size="32" :stroke-width="1.2" />
      <span class="fs-doc-state-title">{{ isBitableMode ? '多维表格加载失败' : '文档加载失败' }}</span>
      <span class="fs-doc-state-desc">{{ errorMsg }}</span>
      <button class="fs-doc-btn" @click="retry">重试</button>
    </div>

    <!-- 多维表格：自渲染表格视图（官方 SDK 不支持 /base/ 链接） -->
    <div v-else-if="bitableData" class="fs-bitable">
      <div class="fs-bitable-head">
        <div class="fs-bitable-title">
          <Table2 :size="15" :stroke-width="1.6" />
          <span class="fs-bitable-name">{{ bitableData.tableName || '数据表' }}</span>
          <span class="fs-bitable-count">{{ bitableData.records.length }} 条记录</span>
          <span v-if="bitableData.truncated" class="fs-bitable-trunc" title="为避免卡顿仅展示前 2000 条">已截断</span>
        </div>
        <a class="fs-doc-btn fs-bitable-open" :href="docUrl" target="_blank" rel="noopener">在飞书中打开</a>
      </div>
      <div class="fs-bitable-scroll">
        <table class="fs-bitable-table">
          <thead>
            <tr>
              <th
                v-for="f in bitableData.fields"
                :key="f.fieldId || f.fieldName"
                :class="{ 'is-primary': f.isPrimary }"
              >{{ f.fieldName }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(row, ri) in bitableData.records" :key="ri">
              <td
                v-for="f in bitableData.fields"
                :key="f.fieldName"
                :class="{ 'is-primary': f.isPrimary }"
                :title="row[f.fieldName]"
              >{{ row[f.fieldName] || '—' }}</td>
            </tr>
            <tr v-if="!bitableData.records.length">
              <td :colspan="bitableData.fields.length || 1" class="fs-bitable-empty">表中暂无记录</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- 空态 / 加载态 -->
    <div v-else-if="!hasMounted" class="fs-doc-state">
      <BookOpen :size="32" :stroke-width="1.2" />
      <span class="fs-doc-state-title">{{ isLoading ? '文档加载中…' : '飞书文档' }}</span>
      <span class="fs-doc-state-desc">{{ isLoading ? docUrl : '粘贴文档链接，或点击聊天中的飞书文档链接自动打开' }}</span>
    </div>

    <!-- 组件挂载点：官方要求固定高度；外层 viewport 做 ResizeObserver，
         面板窄于基准宽度时对 mount 整体等比 scale，避免文档字号相对面板过大 -->
    <div v-show="hasMounted" ref="viewportRef" class="fs-doc-viewport">
      <div ref="mountRef" class="fs-doc-mount" :style="mountStyle"></div>
    </div>

    <div v-if="hasMounted && identity === 'app'" class="fs-doc-identity-hint" title="应用身份鉴权：只读，限 100 次/分钟">
      应用身份 · 只读
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import { BookOpen, FileWarning, Table2 } from 'lucide-vue-next'
import { fetchBitableData, fetchSdkSignature, isFeishuDocUrl, parseBitableUrl, type BitableData } from '@/api/feishuDocs'

/**
 * 飞书官方「云文档组件」封装（open.feishu.cn/document/common-capabilities/web-components）。
 * 每次渲染前向后端取一次性签名（10 分钟有效），再 new DocComponentSdk 挂载。
 * /base/ 多维表格链接 SDK 不支持（-100），走后端拉数 + 自渲染表格视图。
 */

type DocComponentInstance = { start: () => Promise<unknown>; destroy: () => void; invoke: (...args: unknown[]) => Promise<unknown> }

const urlInput = ref('')
const docUrl = ref('')
const isLoading = ref(false)
const errorMsg = ref('')
const hasMounted = ref(false)
const identity = ref<'user' | 'app'>('user')
const bitableData = ref<BitableData | null>(null)
const isBitableMode = ref(false)
const mountRef = ref<HTMLDivElement>()
const viewportRef = ref<HTMLDivElement>()

// ==================== 响应式缩放 ====================
// 文档组件渲染在自身 iframe 内，字号不随容器宽度变化；面板变窄时按
// 「基准文档宽度」整体等比 scale（transform 不影响组件内部逻辑）。
const DOC_BASE_WIDTH = 780 // 视觉基准：面板达到该宽度时 scale=1，字号为组件原生大小
const MIN_SCALE = 0.5
const viewportWidth = ref(0)
let resizeObserver: ResizeObserver | null = null

const scale = computed(() => {
  if (!viewportWidth.value) return 1
  return Math.max(MIN_SCALE, Math.min(1, viewportWidth.value / DOC_BASE_WIDTH))
})

// 缩放后为保持铺满：宽高按 1/scale 放大，再被 transform 等比缩回
const mountStyle = computed(() => {
  const s = scale.value
  if (s >= 1) return {}
  return {
    width: `${100 / s}%`,
    height: `${100 / s}%`,
    transform: `scale(${s})`,
  }
})

onMounted(() => {
  resizeObserver = new ResizeObserver((entries) => {
    viewportWidth.value = entries[0]?.contentRect.width || 0
  })
  if (viewportRef.value) resizeObserver.observe(viewportRef.value)
})

let instance: DocComponentInstance | null = null
let lastUrl = ''

// 官方错误码：open.feishu.cn 云文档组件 ERROR_TYPE
const SDK_ERROR_MAP: Record<string, string> = {
  '4': '当前身份没有该文档的阅读权限',
  '1002': '文档已被删除',
  '1004': '找不到该文档',
  '-8': '加载时网络错误，请检查网络后重试',
  '1': '飞书后端请求失败',
  '-100': '不支持该地址（仅支持 docx/wiki 等云文档链接）',
  '-500': '组件加载错误',
}

function mapSdkError(err: unknown): string {
  const code = typeof err === 'object' && err !== null ? String((err as any).code ?? (err as any).type ?? '') : String(err ?? '')
  return SDK_ERROR_MAP[code] || '组件内部错误，请稍后重试'
}

function destroyInstance() {
  if (instance) {
    try { instance.destroy() } catch { /* 组件已卸载时忽略 */ }
    instance = null
  }
  hasMounted.value = false
}

async function openDoc(url: string) {
  const target = url.trim()
  if (!isFeishuDocUrl(target)) {
    errorMsg.value = '不是有效的飞书文档链接（支持 docx / wiki / base）'
    return
  }
  // 多维表格链接：SDK 不支持，走自渲染表格视图
  if (parseBitableUrl(target)) {
    await openBitable(target)
    return
  }
  isBitableMode.value = false
  bitableData.value = null
  lastUrl = target
  docUrl.value = target
  urlInput.value = target
  errorMsg.value = ''
  isLoading.value = true
  destroyInstance()

  try {
    const Ctor = (window as any).DocComponentSdk
    if (!Ctor) {
      errorMsg.value = '飞书组件 SDK 未加载（检查 CDN 可达性或脚本是否被拦截）'
      return
    }
    // 签名用的 url 必须与 auth.url 一致，且不含 #/? 后参数（后端也会裁剪）
    const pageUrl = window.location.origin + window.location.pathname
    const sign = await fetchSdkSignature(pageUrl)
    identity.value = sign.identity

    await nextTick()
    if (!mountRef.value) return

    const comp = new Ctor({
      src: target,
      mount: mountRef.value,
      theme: 'light',
      size: { height: '100%' },
      auth: {
        openId: sign.openId || undefined,
        signature: sign.signature,
        appId: sign.appId,
        timestamp: sign.timestamp,
        nonceStr: sign.nonceStr,
        url: sign.url,
        jsApiList: ['DocsComponent'],
      },
      onAuthError: () => {
        errorMsg.value = 'SDK 鉴权失败：请检查飞书应用的可信域名配置与 drive:drive 权限'
        destroyInstance()
      },
      onError: (err: unknown) => {
        errorMsg.value = mapSdkError(err)
        destroyInstance()
      },
      onMountTimeout: () => {
        errorMsg.value = '组件挂载超时，请重试'
        destroyInstance()
      },
    }) as DocComponentInstance

    await comp.start()
    instance = comp
    hasMounted.value = true
  } catch (err: any) {
    const detail = err?.response?.data?.detail || err?.message
    errorMsg.value = typeof detail === 'string' ? detail : '签名获取失败，请稍后重试'
    destroyInstance()
  } finally {
    isLoading.value = false
  }
}

function openFromInput() {
  if (urlInput.value.trim()) openDoc(urlInput.value)
}

function retry() {
  if (lastUrl) openDoc(lastUrl)
}

/** 多维表格：后端拉取字段+记录，前端自渲染表格 */
async function openBitable(target: string) {
  isBitableMode.value = true
  lastUrl = target
  docUrl.value = target
  urlInput.value = target
  errorMsg.value = ''
  bitableData.value = null
  destroyInstance()
  isLoading.value = true
  try {
    bitableData.value = await fetchBitableData(target)
  } catch (err: any) {
    const detail = err?.response?.data?.detail || err?.message
    errorMsg.value = typeof detail === 'string' ? detail : '多维表格读取失败，请稍后重试'
  } finally {
    isLoading.value = false
  }
}

onUnmounted(() => {
  resizeObserver?.disconnect()
  resizeObserver = null
  destroyInstance()
})

defineExpose({ openDoc })
</script>

<style scoped>
.fs-doc-panel { height: 100%; display: flex; flex-direction: column; position: relative; }

.fs-doc-toolbar { flex: none; display: flex; gap: 6px; padding: 8px 10px; border-bottom: 1px solid rgba(0, 0, 0, 0.06); }
.fs-doc-input {
  flex: 1; min-width: 0; height: 28px; padding: 0 8px; font-size: 12px;
  border: 1px solid rgba(0, 0, 0, 0.1); border-radius: 6px; outline: none; background: #fafafa;
}
.fs-doc-input:focus { border-color: rgba(0, 0, 0, 0.24); background: #fff; }
.fs-doc-btn {
  flex: none; height: 28px; padding: 0 10px; font-size: 12px; border: none; border-radius: 6px;
  background: #1f2329; color: #fff; cursor: pointer;
}
.fs-doc-btn:disabled { opacity: 0.4; cursor: not-allowed; }
.fs-doc-btn:not(:disabled):hover { background: #33404f; }

.fs-doc-state { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 8px; padding: 20px; color: #9a9a9a; }
.fs-doc-state-title { font-size: 13px; color: #6b6b6b; }
.fs-doc-state-desc { font-size: 12px; text-align: center; word-break: break-all; max-width: 90%; line-height: 1.6; }

.fs-doc-viewport { flex: 1; min-height: 0; position: relative; overflow: hidden; }
.fs-doc-mount { width: 100%; height: 100%; transform-origin: 0 0; }
.fs-doc-mount :deep(iframe) { width: 100%; height: 100%; border: none; }

.fs-doc-identity-hint {
  position: absolute; right: 10px; bottom: 10px; padding: 2px 8px;
  font-size: 11px; color: #8a6d1a; background: rgba(240, 200, 80, 0.18);
  border-radius: 10px; pointer-events: none;
}

/* ==================== 多维表格视图 ==================== */
.fs-bitable { flex: 1; min-height: 0; display: flex; flex-direction: column; }

.fs-bitable-head {
  flex: none; display: flex; align-items: center; justify-content: space-between;
  gap: 8px; padding: 8px 10px; border-bottom: 1px solid rgba(0, 0, 0, 0.06);
}
.fs-bitable-title { display: flex; align-items: center; gap: 6px; min-width: 0; color: #1f2329; font-size: 13px; }
.fs-bitable-name { font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.fs-bitable-count, .fs-bitable-trunc { flex: none; font-size: 11px; color: #9a9a9a; white-space: nowrap; }
.fs-bitable-trunc { color: #8a6d1a; }
.fs-bitable-open { text-decoration: none; display: inline-flex; align-items: center; flex: none; }

.fs-bitable-scroll { flex: 1; min-height: 0; overflow: auto; }
.fs-bitable-table { border-collapse: separate; border-spacing: 0; min-width: 100%; font-size: 12px; }
.fs-bitable-table th,
.fs-bitable-table td {
  padding: 6px 10px; text-align: left; white-space: nowrap; max-width: 320px;
  overflow: hidden; text-overflow: ellipsis;
  border-bottom: 1px solid rgba(0, 0, 0, 0.06); border-right: 1px solid rgba(0, 0, 0, 0.04);
}
.fs-bitable-table thead th {
  position: sticky; top: 0; z-index: 1;
  background: #f5f6f7; color: #646a73; font-weight: 500;
}
.fs-bitable-table tbody tr:hover td { background: #f8f9fa; }
.fs-bitable-table .is-primary { font-weight: 500; color: #1f2329; }
.fs-bitable-empty { text-align: center; color: #9a9a9a; padding: 24px 0; }
</style>
