<template>
  <div class="cde">
    <div class="cde-head">
      <div class="cde-head-left">
        <Icon icon="ph:pencil-simple-duotone" width="16" />
        <span class="cde-head-title">卡片编辑</span>
        <span v-if="draft" class="cde-version">v{{ draft.version }}</span>
      </div>
      <div class="cde-head-right">
        <span v-if="saving" class="cde-status">保存中…</span>
        <span v-else-if="savedTick" class="cde-status cde-status-ok">已保存</span>
        <button class="cde-btn" @click="emit('close')">
          <Icon icon="ph:x" width="13" />
          退出编辑
        </button>
      </div>
    </div>

    <div v-if="error" class="cde-error">{{ error }}</div>

    <div class="cde-body">
      <div class="cde-left">
        <div v-if="loading" class="cde-loading">
          <Icon icon="ph:spinner-gap" width="18" class="cde-spin" />
          <span>加载卡片结构…</span>
        </div>

        <div v-else-if="!draft" class="cde-empty">
          <Icon icon="ph:cards-duotone" width="34" style="color: #D4D4D8" />
          <p>这件产物没有结构化卡片，无法进入编辑</p>
          <p class="cde-empty-hint">旧链路产物只保留了最终图片/HTML，缺少可编辑的块结构</p>
        </div>

        <template v-else>
          <div class="cde-pages">
            <button
              v-for="(page, idx) in draft.pages"
              :key="page.id"
              class="cde-page-tab"
              :class="{ 'cde-page-tab-active': idx === activePage }"
              @click="activePage = idx"
            >
              <span class="cde-page-num">{{ idx + 1 }}</span>
              <span class="cde-page-role">{{ roleLabel(idx) }}</span>
            </button>
          </div>

          <div class="cde-section">
            <div class="cde-section-label">整套风格</div>
            <div class="cde-field">
              <span class="cde-field-key">主题</span>
              <select
                class="cde-select"
                :value="draft.themeId"
                :disabled="saving"
                @change="onThemeChange($event)"
              >
                <option v-for="opt in draft.themeOptions" :key="opt.id" :value="opt.id">
                  {{ opt.label }}
                </option>
              </select>
            </div>
            <div class="cde-field">
              <span class="cde-field-key">强调色</span>
              <div class="cde-accents">
                <button
                  v-for="(color, idx) in draft.accentOptions"
                  :key="color"
                  class="cde-accent"
                  :class="{ 'cde-accent-active': idx === draft.accentIndex }"
                  :style="{ background: color }"
                  :disabled="saving"
                  :title="idx === 0 ? '默认强调色' : `强调色 ${idx}`"
                  @click="runEdit({ op: 'set_accent', index: idx })"
                />
              </div>
            </div>
          </div>

          <div class="cde-section" v-if="currentPage">
            <div class="cde-section-label">
              第 {{ activePage + 1 }} 页 · {{ currentPage.layoutId }}
            </div>
            <div class="cde-field">
              <span class="cde-field-key">版式</span>
              <select
                class="cde-select"
                :value="currentPage.layoutId"
                :disabled="saving"
                @change="onLayoutChange($event)"
              >
                <option v-for="layout in currentPage.availableLayouts" :key="layout" :value="layout">
                  {{ layout }}
                </option>
              </select>
            </div>
            <div v-if="densityHint" class="cde-density">{{ densityHint }}</div>
          </div>

          <div class="cde-blocks" v-if="currentBlocks.length">
            <div v-for="block in currentBlocks" :key="block.id" class="cde-block">
              <div class="cde-block-head">
                <span class="cde-block-type">{{ typeLabel(block.type) }}</span>
                <select
                  v-if="variantsOf(block.type).length"
                  class="cde-select cde-select-sm"
                  :value="block.variant || ''"
                  :disabled="saving"
                  @change="onVariantChange(block, $event)"
                >
                  <option v-for="v in variantsOf(block.type)" :key="v" :value="v">
                    {{ v === '' ? '经典' : v }}
                  </option>
                </select>
              </div>

              <template v-if="isTextBlock(block)">
                <textarea
                  v-if="block.type === 'text'"
                  class="cde-input cde-textarea"
                  :value="block.text"
                  :disabled="saving"
                  rows="4"
                  @change="onSetText(block, $event)"
                />
                <input
                  v-else
                  class="cde-input"
                  :value="block.text"
                  :disabled="saving"
                  @change="onSetText(block, $event)"
                />
              </template>

              <template v-else-if="block.type === 'list' || block.type === 'steps'">
                <div class="cde-item" v-for="(item, idx) in itemsOf(block)" :key="idx">
                  <input
                    class="cde-input"
                    :value="item.text"
                    :disabled="saving"
                    placeholder="主行"
                    @change="onSetItem(block, idx, $event)"
                  />
                  <input
                    class="cde-input cde-input-sm"
                    :value="item.note"
                    :disabled="saving"
                    placeholder="副行（可空）"
                    @change="onSetItemNote(block, idx, $event)"
                  />
                  <div class="cde-item-foot">
                    <input
                      class="cde-input cde-input-tag"
                      :value="item.tag"
                      :disabled="saving"
                      placeholder="标签"
                      @change="onSetItemTag(block, idx, $event)"
                    />
                    <button class="cde-icon-btn" :disabled="saving" @click="onRemoveItem(block, idx)">
                      <Icon icon="ph:minus-circle" width="14" />
                    </button>
                  </div>
                </div>
                <div class="cde-add-row">
                  <input
                    class="cde-input"
                    v-model="newItemText[block.id]"
                    :disabled="saving"
                    placeholder="新增条目"
                  />
                  <button class="cde-btn cde-btn-sm" :disabled="saving" @click="onAddItem(block)">
                    <Icon icon="ph:plus" width="12" />
                    添加
                  </button>
                </div>
              </template>

              <template v-else-if="block.type === 'compare'">
                <div class="cde-field">
                  <input
                    class="cde-input"
                    :value="block.left_title"
                    :disabled="saving"
                    placeholder="左列标题"
                    @change="onSetCompareTitle(block, 'left', $event)"
                  />
                  <input
                    class="cde-input"
                    :value="block.right_title"
                    :disabled="saving"
                    placeholder="右列标题"
                    @change="onSetCompareTitle(block, 'right', $event)"
                  />
                </div>
                <div class="cde-item" v-for="(row, idx) in rowsOf(block)" :key="idx">
                  <div class="cde-row-label">{{ row.label || `第 ${idx + 1} 行` }}</div>
                  <div class="cde-field">
                    <input
                      class="cde-input cde-input-sm"
                      :value="row.left"
                      :disabled="saving"
                      placeholder="左"
                      @change="onSetCell(block, idx, 'left', $event)"
                    />
                    <input
                      class="cde-input cde-input-sm"
                      :value="row.right"
                      :disabled="saving"
                      placeholder="右"
                      @change="onSetCell(block, idx, 'right', $event)"
                    />
                    <button class="cde-icon-btn" :disabled="saving" @click="onRemoveRow(block, idx)">
                      <Icon icon="ph:minus-circle" width="14" />
                    </button>
                  </div>
                </div>
                <div class="cde-add-row">
                  <input class="cde-input cde-input-sm" v-model="rowForm(block.id).label" placeholder="维度标签（可空）" />
                  <input class="cde-input cde-input-sm" v-model="rowForm(block.id).left" placeholder="左" />
                  <input class="cde-input cde-input-sm" v-model="rowForm(block.id).right" placeholder="右" />
                  <button class="cde-btn cde-btn-sm" :disabled="saving" @click="onAddRow(block)">
                    <Icon icon="ph:plus" width="12" />
                    添加行
                  </button>
                </div>
              </template>

              <template v-else-if="block.type === 'quote'">
                <textarea
                  class="cde-input cde-textarea"
                  :value="block.text"
                  :disabled="saving"
                  rows="3"
                  placeholder="金句正文"
                  @change="onSetQuote(block, 'text', $event)"
                />
                <div class="cde-field">
                  <input
                    class="cde-input cde-input-sm"
                    :value="block.author"
                    :disabled="saving"
                    placeholder="出处 / 作者"
                    @change="onSetQuote(block, 'author', $event)"
                  />
                  <input
                    class="cde-input cde-input-sm"
                    :value="block.kicker"
                    :disabled="saving"
                    placeholder="顶部标签"
                    @change="onSetQuote(block, 'kicker', $event)"
                  />
                </div>
              </template>

              <template v-else-if="block.type === 'bento_grid' || block.type === 'stat'">
                <div class="cde-cell" v-for="(cell, idx) in cellsOf(block)" :key="idx">
                  <input
                    class="cde-input cde-input-sm"
                    :value="cell.kicker"
                    :disabled="saving"
                    placeholder="分类 kicker"
                    @change="onSetCellField(block, idx, 'kicker', $event)"
                  />
                  <input
                    class="cde-input"
                    :value="cell.big"
                    :disabled="saving"
                    placeholder="主数字"
                    @change="onSetCellField(block, idx, 'big', $event)"
                  />
                  <input
                    class="cde-input cde-input-sm"
                    :value="cell.unit"
                    :disabled="saving"
                    placeholder="单位"
                    @change="onSetCellField(block, idx, 'unit', $event)"
                  />
                  <textarea
                    class="cde-input cde-textarea"
                    :value="cell.note"
                    :disabled="saving"
                    rows="2"
                    placeholder="解读"
                    @change="onSetCellField(block, idx, 'note', $event)"
                  />
                  <input
                    class="cde-input cde-input-sm"
                    :value="cell.source"
                    :disabled="saving"
                    placeholder="来源"
                    @change="onSetCellField(block, idx, 'source', $event)"
                  />
                </div>
              </template>

              <template v-else-if="block.type === 'timeline'">
                <div class="cde-item" v-for="(item, idx) in itemsOf(block)" :key="idx">
                  <input
                    class="cde-input cde-input-sm"
                    :value="item.date"
                    :disabled="saving"
                    placeholder="时间"
                    @change="onSetTimeline(block, idx, 'date', $event)"
                  />
                  <input
                    class="cde-input"
                    :value="item.text"
                    :disabled="saving"
                    placeholder="标题"
                    @change="onSetTimeline(block, idx, 'text', $event)"
                  />
                  <div class="cde-item-foot">
                    <input
                      class="cde-input cde-input-sm"
                      :value="item.note"
                      :disabled="saving"
                      placeholder="补充说明"
                      @change="onSetTimeline(block, idx, 'note', $event)"
                    />
                    <button class="cde-icon-btn" :disabled="saving" @click="onRemoveTimeline(block, idx)">
                      <Icon icon="ph:minus-circle" width="14" />
                    </button>
                  </div>
                </div>
                <div class="cde-add-row">
                  <input class="cde-input cde-input-sm" v-model="timelineForm(block.id).date" placeholder="时间" />
                  <input class="cde-input" v-model="timelineForm(block.id).text" placeholder="标题" />
                  <button class="cde-btn cde-btn-sm" :disabled="saving" @click="onAddTimeline(block)">
                    <Icon icon="ph:plus" width="12" />
                    添加
                  </button>
                </div>
              </template>

              <div v-else class="cde-readonly">
                {{ readOnlySummary(block) || '该组件暂不支持在详情页编辑' }}
              </div>
            </div>
          </div>
        </template>
      </div>

      <div class="cde-right">
        <CardPreviewPane
          :html="currentPage?.htmlContent || ''"
          :title="draft?.title || ''"
          :page-label="currentPage ? roleLabel(activePage) : ''"
          :page-index="activePage"
          :page-count="draft?.pages?.length || 0"
        />
        <div class="cde-nav" v-if="draft && draft.pages.length > 1">
          <button class="cde-btn cde-btn-sm" :disabled="activePage <= 0" @click="activePage--">
            <Icon icon="ph:caret-left" width="12" />
            上一页
          </button>
          <span class="cde-nav-count">{{ activePage + 1 }} / {{ draft.pages.length }}</span>
          <button
            class="cde-btn cde-btn-sm"
            :disabled="activePage >= draft.pages.length - 1"
            @click="activePage++"
          >
            下一页
            <Icon icon="ph:caret-right" width="12" />
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * CardDeckEditor —— 详情页左编辑区 + 右手机预览。
 *
 * 编辑能力不新建一套数据模型：每次操作都发一条块级指令（set_text / set_item / …）
 * 给后端，后端在锁内读-改-写 deck 并返回新版本 + 重新渲染的 HTML。
 * 前端只负责把「新版本」替换进本地 draft，预览自然跟着刷新。
 *
 * 乐观锁：expected_version 用当前 draft 的 version，冲突（409）时整份重载，
 * 避免两个人的编辑互相静默覆盖。
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { Icon } from '@iconify/vue'
import CardPreviewPane from './CardPreviewPane.vue'
import { editCard, getCardDraft, type CardDraft, type CardEditPayload } from '@/api/cards'

const props = defineProps<{ artifactId: string }>()
const emit = defineEmits<{ close: [] }>()

const draft = ref<CardDraft | null>(null)
const loading = ref(true)
const saving = ref(false)
const error = ref('')
const savedTick = ref(false)
const activePage = ref(0)

const newItemText = reactive<Record<string, string>>({})
const newTimeline = reactive<Record<string, { date: string; text: string }>>({})
const newRow = reactive<Record<string, { label: string; left: string; right: string }>>({})

const TYPE_LABELS: Record<string, string> = {
  heading: '标题',
  text: '正文',
  list: '清单',
  steps: '步骤',
  compare: '对比',
  image: '图片位',
  cta: '行动号召',
  bento_grid: '数据矩阵',
  stat: '数据卡',
  timeline: '时间线',
  quote: '金句',
  kicker: '顶部标签',
  footer: '底部信息',
  qa: '问答',
  code: '代码',
  icon_grid: '图标网格',
}

const currentPage = computed(() => draft.value?.pages?.[activePage.value] || null)
const deckPage = computed(() => draft.value?.deck?.pages?.[activePage.value] || null)
const currentBlocks = computed<any[]>(() => deckPage.value?.blocks || [])

const densityHint = computed(() => {
  const density = currentPage.value?.density as any
  if (!density || density.pass) return ''
  const parts: string[] = []
  if (Array.isArray(density.problems)) parts.push(...density.problems.filter(Boolean))
  if (density.hint) parts.push(density.hint)
  return parts.join('；')
})

function typeLabel(type: string): string {
  return TYPE_LABELS[type] || type
}

function roleLabel(idx: number): string {
  const role = draft.value?.deck?.pages?.[idx]?.role
  if (role === 'cover') return '封面'
  if (role === 'ending') return '结尾'
  return `第 ${idx + 1} 页`
}

function isTextBlock(block: any): boolean {
  return block.type === 'heading' || block.type === 'text' || block.type === 'cta'
}

function variantsOf(type: string): string[] {
  return draft.value?.variantOptions?.[type] || []
}

function rowForm(id: string) {
  if (!newRow[id]) newRow[id] = { label: '', left: '', right: '' }
  return newRow[id]
}

function timelineForm(id: string) {
  if (!newTimeline[id]) newTimeline[id] = { date: '', text: '' }
  return newTimeline[id]
}

function cellsOf(block: any): any[] {
  return block.type === 'bento_grid' ? block.cells || [] : block.cell ? [block.cell] : []
}

/** 显式标注成数组：模板里 v-for 才能把索引推断成 number 而不是 string | number。 */
function itemsOf(block: any): any[] {
  return Array.isArray(block.items) ? block.items : []
}

function rowsOf(block: any): any[] {
  return Array.isArray(block.rows) ? block.rows : []
}

function readOnlySummary(block: any): string {
  if (block.type === 'image') return `图片位：${block.alt || block.resolved_url || '未命名'}`
  if (block.type === 'kicker' || block.type === 'footer') return block.text || ''
  if (block.type === 'qa') return `${(block.pairs || []).length} 组问答（暂不支持在此编辑）`
  if (block.type === 'icon_grid') return `${(block.pairs || []).length} 组图标要点（暂不支持在此编辑）`
  if (block.type === 'code') return block.content || ''
  return ''
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    draft.value = await getCardDraft(props.artifactId)
    if (activePage.value >= (draft.value?.pages?.length || 0)) activePage.value = 0
  } catch (e: any) {
    draft.value = null
    const status = e?.response?.status
    if (status === 404) error.value = '作品不存在或已被删除'
    else if (status === 403) error.value = '无权访问该作品'
    else if (status === 400) error.value = '该作品不是结构化卡片'
    else error.value = e?.response?.data?.detail || e?.message || '卡片加载失败'
  } finally {
    loading.value = false
  }
}

async function runEdit(payload: Omit<CardEditPayload, 'expected_version'>) {
  if (!draft.value || saving.value) return
  saving.value = true
  error.value = ''
  savedTick.value = false
  try {
    const next = await editCard(props.artifactId, {
      ...payload,
      expected_version: draft.value.version || 1,
    })
    draft.value = next
    savedTick.value = true
    setTimeout(() => { savedTick.value = false }, 1600)
  } catch (e: any) {
    const status = e?.response?.status
    if (status === 409) {
      error.value = '卡片已被其他会话更新，正在重新加载…'
      await load()
    } else {
      error.value = e?.response?.data?.detail || e?.message || '保存失败'
    }
  } finally {
    saving.value = false
  }
}

function onThemeChange(event: Event) {
  runEdit({ op: 'set_theme', value: (event.target as HTMLSelectElement).value })
}

function onLayoutChange(event: Event) {
  const page = currentPage.value
  if (!page) return
  runEdit({ op: 'set_layout', page_id: page.id, value: (event.target as HTMLSelectElement).value })
}

function onVariantChange(block: any, event: Event) {
  const page = currentPage.value
  if (!page) return
  runEdit({
    op: 'set_variant',
    page_id: page.id,
    block_id: block.id,
    value: (event.target as HTMLSelectElement).value,
  })
}

function onSetText(block: any, event: Event) {
  const page = currentPage.value
  if (!page) return
  runEdit({
    op: 'set_text',
    page_id: page.id,
    block_id: block.id,
    value: (event.target as HTMLInputElement).value,
  })
}

function onSetItem(block: any, index: number, event: Event) {
  const page = currentPage.value
  if (!page) return
  runEdit({
    op: 'set_item',
    page_id: page.id,
    block_id: block.id,
    index,
    value: (event.target as HTMLInputElement).value,
  })
}

function onSetItemNote(block: any, index: number, event: Event) {
  const page = currentPage.value
  if (!page) return
  runEdit({
    op: 'set_item',
    page_id: page.id,
    block_id: block.id,
    index,
    note: (event.target as HTMLInputElement).value,
  })
}

function onSetItemTag(block: any, index: number, event: Event) {
  const page = currentPage.value
  if (!page) return
  runEdit({
    op: 'set_item',
    page_id: page.id,
    block_id: block.id,
    index,
    tag: (event.target as HTMLInputElement).value,
  })
}

function onAddItem(block: any) {
  const page = currentPage.value
  const text = (newItemText[block.id] || '').trim()
  if (!page || !text) return
  runEdit({ op: 'add_item', page_id: page.id, block_id: block.id, value: text })
  newItemText[block.id] = ''
}

function onRemoveItem(block: any, index: number) {
  const page = currentPage.value
  if (!page) return
  runEdit({ op: 'remove_item', page_id: page.id, block_id: block.id, index })
}

function onSetCompareTitle(block: any, column: 'left' | 'right', event: Event) {
  const page = currentPage.value
  if (!page) return
  runEdit({
    op: 'set_title',
    page_id: page.id,
    block_id: block.id,
    column,
    value: (event.target as HTMLInputElement).value,
  })
}

function onSetCell(block: any, index: number, column: 'left' | 'right', event: Event) {
  const page = currentPage.value
  if (!page) return
  runEdit({
    op: 'set_cell',
    page_id: page.id,
    block_id: block.id,
    index,
    column,
    value: (event.target as HTMLInputElement).value,
  })
}

function onAddRow(block: any) {
  const page = currentPage.value
  const form = rowForm(block.id)
  if (!page || !form.left.trim() || !form.right.trim()) return
  runEdit({
    op: 'add_row',
    page_id: page.id,
    block_id: block.id,
    value: form.label.trim() || undefined,
    value_pair: [form.left.trim(), form.right.trim()],
  })
  form.label = ''
  form.left = ''
  form.right = ''
}

function onRemoveRow(block: any, index: number) {
  const page = currentPage.value
  if (!page) return
  runEdit({ op: 'remove_row', page_id: page.id, block_id: block.id, index })
}

function onSetQuote(block: any, field: 'text' | 'author' | 'kicker', event: Event) {
  const page = currentPage.value
  if (!page) return
  const value = (event.target as HTMLInputElement).value
  const payload: Record<string, any> = { op: 'set_quote', page_id: page.id, block_id: block.id }
  if (field === 'text') payload.value = value
  else if (field === 'author') payload.note = value
  else payload.tag = value
  runEdit(payload as Omit<CardEditPayload, 'expected_version'>)
}

function onSetCellField(block: any, index: number, field: string, event: Event) {
  const page = currentPage.value
  if (!page) return
  const value = (event.target as HTMLInputElement).value
  const payload: Record<string, any> = {
    op: block.type === 'stat' ? 'set_stat_value' : 'set_bento_cell',
    page_id: page.id,
    block_id: block.id,
    index,
  }
  if (field === 'big') payload.value = value
  else payload[field] = value
  runEdit(payload as Omit<CardEditPayload, 'expected_version'>)
}

function onSetTimeline(block: any, index: number, field: 'date' | 'text' | 'note', event: Event) {
  const page = currentPage.value
  if (!page) return
  const value = (event.target as HTMLInputElement).value
  const payload: Record<string, any> = { op: 'set_timeline_item', page_id: page.id, block_id: block.id, index }
  if (field === 'date') payload.value = value
  else if (field === 'text') payload.note = value
  else payload.tag = value
  runEdit(payload as Omit<CardEditPayload, 'expected_version'>)
}

function onAddTimeline(block: any) {
  const page = currentPage.value
  const form = timelineForm(block.id)
  if (!page || !form.text.trim()) return
  runEdit({
    op: 'add_timeline_item',
    page_id: page.id,
    block_id: block.id,
    value: form.date.trim(),
    note: form.text.trim(),
  })
  form.date = ''
  form.text = ''
}

function onRemoveTimeline(block: any, index: number) {
  const page = currentPage.value
  if (!page) return
  runEdit({ op: 'remove_timeline_item', page_id: page.id, block_id: block.id, index })
}

onMounted(load)
</script>

<style scoped>
.cde {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 560px;
}

.cde-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 0 14px;
  border-bottom: 1px solid var(--ma-border-default);
  flex-shrink: 0;
}

.cde-head-left { display: flex; align-items: center; gap: 8px; color: var(--rl-text-2); }
.cde-head-title { font-size: 14px; font-weight: 600; color: #1a1a1a; }
.cde-version { font-size: 11px; color: #9a9a9a; font-variant-numeric: tabular-nums; }
.cde-head-right { display: flex; align-items: center; gap: 10px; }
.cde-status { font-size: 12px; color: #9a9a9a; }
.cde-status-ok { color: #30a46c; }

.cde-error {
  margin-top: 10px;
  padding: 8px 12px;
  border-radius: 10px;
  background: #fef2f2;
  border: 1px solid #fecaca;
  color: #dc2626;
  font-size: 12px;
  flex-shrink: 0;
}

.cde-body {
  display: flex;
  gap: 20px;
  flex: 1;
  min-height: 0;
  padding-top: 14px;
}

.cde-left {
  flex: 1;
  min-width: 0;
  overflow-y: auto;
  padding-right: 6px;
}

.cde-right {
  width: 340px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
  align-items: center;
  overflow-y: auto;
}

.cde-loading,
.cde-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  height: 320px;
  color: #9a9a9a;
  font-size: 13px;
}

.cde-empty p { margin: 0; }
.cde-empty-hint { font-size: 12px; color: #b4b4bb; }
.cde-spin { animation: cde-spin 1s linear infinite; }
@keyframes cde-spin { to { transform: rotate(360deg); } }

.cde-pages {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 16px;
}

.cde-page-tab {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  border-radius: 10px;
  border: 1px solid #ededed;
  background: #fff;
  cursor: pointer;
  font-size: 12px;
  color: var(--rl-text-2);
  transition: all 0.15s;
}

.cde-page-tab:hover { border-color: #d4d4d8; background: #fafafa; }
.cde-page-tab-active { border-color: #ff2442; color: #ff2442; background: rgba(255, 36, 66, 0.06); }
.cde-page-num { font-weight: 600; }

.cde-section { margin-bottom: 16px; }
.cde-section-label {
  font-size: 12px;
  color: #9a9a9a;
  margin-bottom: 8px;
  letter-spacing: 0.02em;
}

.cde-field { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; flex-wrap: wrap; }
.cde-field-key { font-size: 12px; color: var(--rl-text-2); width: 48px; flex-shrink: 0; }

.cde-select,
.cde-input {
  border: 1px solid #ededed;
  border-radius: 10px;
  padding: 7px 10px;
  font-size: 12px;
  color: #1a1a1a;
  background: #fff;
  outline: none;
  transition: border-color 0.15s;
  min-width: 0;
  flex: 1;
  font-family: inherit;
}

.cde-select { flex: 0 1 auto; min-width: 120px; }
.cde-input:focus,
.cde-select:focus { border-color: #d4d4d8; }
.cde-input:disabled,
.cde-select:disabled { background: #fafafa; color: #9a9a9a; }
.cde-select-sm { flex: 0 0 auto; min-width: 90px; padding: 4px 8px; font-size: 11px; }
.cde-input-sm { flex: 1 1 120px; }
.cde-input-tag { flex: 0 0 90px; }
.cde-textarea { resize: vertical; line-height: 1.6; width: 100%; }

.cde-accents { display: flex; gap: 8px; }
.cde-accent {
  width: 22px;
  height: 22px;
  border-radius: 50%;
  border: 2px solid transparent;
  cursor: pointer;
  padding: 0;
  box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.06);
}
.cde-accent-active { border-color: #1a1a1a; }
.cde-accent:disabled { opacity: 0.5; cursor: not-allowed; }

.cde-density {
  font-size: 11px;
  color: #d97706;
  background: #fef3c7;
  border-radius: 8px;
  padding: 6px 10px;
  line-height: 1.5;
}

.cde-blocks { display: flex; flex-direction: column; gap: 12px; padding-bottom: 12px; }

.cde-block {
  border: 1px solid #ededed;
  border-radius: 12px;
  padding: 12px;
  background: #fff;
}

.cde-block-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
}

.cde-block-type { font-size: 12px; font-weight: 600; color: #1a1a1a; }

.cde-item {
  border-top: 1px dashed #f0f0f0;
  padding-top: 8px;
  margin-top: 8px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.cde-item:first-of-type { border-top: none; padding-top: 0; margin-top: 0; }
.cde-item-foot { display: flex; align-items: center; gap: 8px; }
.cde-row-label { font-size: 11px; color: #9a9a9a; }

.cde-cell {
  display: flex;
  flex-direction: column;
  gap: 6px;
  border-top: 1px dashed #f0f0f0;
  padding-top: 8px;
  margin-top: 8px;
}

.cde-cell:first-of-type { border-top: none; padding-top: 0; margin-top: 0; }

.cde-add-row { display: flex; align-items: center; gap: 8px; margin-top: 10px; }

.cde-readonly { font-size: 12px; color: #9a9a9a; line-height: 1.6; word-break: break-all; }

.cde-icon-btn {
  border: 1px solid #ededed;
  background: #fff;
  border-radius: 8px;
  padding: 4px 6px;
  cursor: pointer;
  color: #9a9a9a;
  display: inline-flex;
  align-items: center;
}
.cde-icon-btn:hover { color: #ef4444; border-color: #fca5a5; }
.cde-icon-btn:disabled { opacity: 0.4; cursor: not-allowed; }

.cde-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 6px 12px;
  border-radius: 10px;
  border: 1px solid #ededed;
  background: #fff;
  color: var(--rl-text-2);
  font-size: 12px;
  cursor: pointer;
  white-space: nowrap;
  transition: all 0.15s;
}
.cde-btn:hover { border-color: #d4d4d8; background: #f5f5f5; }
.cde-btn:disabled { opacity: 0.4; cursor: not-allowed; }
.cde-btn-sm { padding: 5px 10px; font-size: 11px; }

.cde-nav { display: flex; align-items: center; gap: 10px; flex-shrink: 0; }
.cde-nav-count { font-size: 11px; color: #9a9a9a; font-variant-numeric: tabular-nums; }
</style>