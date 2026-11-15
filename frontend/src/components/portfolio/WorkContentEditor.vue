<template>
  <div class="wce">
    <header class="wce-head">
      <div class="wce-head-text">
        <h3 class="wce-h">编辑内容</h3>
        <p class="wce-sub">图片 · 标题 · 正文 · 话题，保存后立即生效</p>
      </div>
      <div class="wce-head-ops">
        <span v-if="dirty" class="wce-dirty">未保存</span>
        <button class="wce-btn" :disabled="!dirty || saving" @click="emit('reset')">撤销</button>
        <button class="wce-btn wce-btn--primary" :disabled="!dirty || saving" @click="emit('save')">
          {{ saving ? '保存中…' : '保存' }}
        </button>
      </div>
    </header>

    <section class="wce-field">
      <div class="wce-field-head">
        <label class="wce-label">
          图片
          <em>{{ modelValue.images.length }} 张 · 可调顺序、设封面、删除</em>
        </label>
        <button
          v-if="refreshable"
          class="wce-btn wce-btn--refresh"
          type="button"
          :disabled="refreshing"
          @click="emit('refresh-images')"
        >
          {{ refreshing ? '抓取中…' : '重新抓取图片' }}
        </button>
      </div>
      <p v-if="failedCount" class="wce-warn">
        有 {{ failedCount }} 张图片链接已失效（CDN 签名过期），点「重新抓取图片」从原笔记取回。
      </p>
      <div v-if="modelValue.images.length" class="wce-grid">
        <div
          v-for="(url, idx) in modelValue.images"
          :key="url + idx"
          class="wce-thumb"
          :class="{ 'wce-thumb--active': idx === pageIndex }"
          @click="emit('select-page', idx)"
        >
          <img :src="url" alt="" loading="lazy" @error="onImgError" />
          <span class="wce-thumb-no">{{ idx + 1 }}</span>
          <span v-if="isCover(url)" class="wce-thumb-cover">封面</span>
          <div class="wce-thumb-ops" @click.stop>
            <button type="button" :disabled="idx === 0" title="上移" @click="move(idx, idx - 1)">上</button>
            <button
              type="button"
              :disabled="idx === modelValue.images.length - 1"
              title="下移"
              @click="move(idx, idx + 1)"
            >
              下
            </button>
            <button type="button" title="设为封面" @click="emit('update:modelValue', setCover(modelValue, idx))">
              封面
            </button>
            <button
              type="button"
              class="wce-thumb-del"
              title="删除这张"
              @click="emit('update:modelValue', removeImage(modelValue, idx))"
            >
              删除
            </button>
          </div>
        </div>
      </div>
      <p v-else class="wce-empty">这个作品没有可编辑的图片</p>
    </section>

    <section class="wce-field">
      <label class="wce-label">
        标题
        <em>{{ modelValue.title.length }}/200</em>
      </label>
      <input
        class="wce-input"
        :value="modelValue.title"
        maxlength="200"
        placeholder="填写作品标题"
        @input="patchTitle(($event.target as HTMLInputElement).value)"
      />
    </section>

    <section class="wce-field">
      <label class="wce-label">
        正文
        <em>{{ modelValue.contentText.length }} 字</em>
      </label>
      <textarea
        ref="textareaRef"
        class="wce-textarea"
        :value="modelValue.contentText"
        rows="1"
        placeholder="填写正文，换行会原样保留"
        @input="patchText(($event.target as HTMLTextAreaElement).value)"
      />
    </section>

    <section class="wce-field">
      <label class="wce-label">
        话题标签
        <em>{{ modelValue.tags.length }}/40</em>
      </label>
      <div class="wce-tags">
        <span v-for="(tag, idx) in modelValue.tags" :key="tag + idx" class="wce-tag">
          #{{ tag }}
          <button class="wce-tag-x" type="button" title="移除" @click="removeTag(idx)">×</button>
        </span>
        <input
          v-model="tagInput"
          class="wce-tag-input"
          placeholder="输入后回车添加"
          maxlength="30"
          @keydown.enter.prevent="addTag"
          @blur="addTag"
        />
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
/**
 * WorkContentEditor —— 页面级「内容层」编辑器。
 *
 * 采集与平台生成产物没有 CardDeck 块结构，进不去卡片编辑器，
 * 这里直接给一套内容表单：标题、正文、话题、图片顺序/封面。
 * 只改内容这一层，原始采集快照（指标、归因字段）保持不动。
 */
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import {
  moveImage,
  removeImage,
  setCover,
  type WorkContentDraft,
} from '@/components/portfolio/contentDraft'

const props = withDefaults(
  defineProps<{
    modelValue: WorkContentDraft
    saving?: boolean
    dirty?: boolean
    pageIndex?: number
    refreshable?: boolean
    refreshing?: boolean
  }>(),
  { saving: false, dirty: false, pageIndex: 0, refreshable: false, refreshing: false }
)

const emit = defineEmits<{
  'update:modelValue': [value: WorkContentDraft]
  save: []
  reset: []
  'select-page': [index: number]
  'refresh-images': []
}>()

const tagInput = ref('')
const failedUrls = ref<Set<string>>(new Set())
const failedCount = computed(() => failedUrls.value.size)
const textareaRef = ref<HTMLTextAreaElement | null>(null)

/**
 * 正文框随内容自动长高，替代原来右下角可拖拽的手柄。
 * 先把高度归零再读 scrollHeight：否则删字时高度只会保持、不会回落。
 * 全局是 border-box，scrollHeight 不含边框，需要补上 offsetHeight - clientHeight，
 * 否则每次重算都会少 2px，末行被压住。
 */
function autoGrowTextarea() {
  const el = textareaRef.value
  if (!el) return
  el.style.height = 'auto'
  const borderY = el.offsetHeight - el.clientHeight
  el.style.height = `${el.scrollHeight + borderY}px`
}

onMounted(() => nextTick(autoGrowTextarea))

// 内容来源不只有手动输入：撤销、切作品、外部重置都会改它，统一在这里重算高度
watch(() => props.modelValue.contentText, () => nextTick(autoGrowTextarea))

watch(
  () => props.modelValue.images,
  (images) => {
    const alive = new Set(images)
    const next = new Set<string>()
    failedUrls.value.forEach((url) => {
      if (alive.has(url)) next.add(url)
    })
    failedUrls.value = next
  }
)

function patchTitle(value: string) {
  emit('update:modelValue', { ...props.modelValue, title: value })
}

function patchText(value: string) {
  emit('update:modelValue', { ...props.modelValue, contentText: value })
}

function addTag() {
  const tag = tagInput.value.trim().replace(/^#+/, '').trim()
  tagInput.value = ''
  if (!tag || props.modelValue.tags.includes(tag)) return
  if (props.modelValue.tags.length >= 40) return
  emit('update:modelValue', { ...props.modelValue, tags: [...props.modelValue.tags, tag] })
}

function removeTag(index: number) {
  emit('update:modelValue', {
    ...props.modelValue,
    tags: props.modelValue.tags.filter((_, idx) => idx !== index),
  })
}

function move(from: number, to: number) {
  emit('update:modelValue', moveImage(props.modelValue, from, to))
}

function isCover(url: string) {
  return url === (props.modelValue.coverImgUrl || props.modelValue.images[0] || '')
}

/** 小红书 CDN 防盗链，缩略图同样走代理兜底；两次都失败就把碎图藏掉，避免整格是破图标。 */
function onImgError(event: Event) {
  const img = event.target as HTMLImageElement
  const original = img.dataset.origin || img.src
  img.dataset.origin = original
  if (img.dataset.proxied !== '1' && img.src.startsWith('http')) {
    img.dataset.proxied = '1'
    img.src = '/api/proxy/image?url=' + encodeURIComponent(img.src)
    return
  }
  img.dataset.failed = '1'
  failedUrls.value = new Set([...failedUrls.value, original])
}
</script>

<style scoped>
.wce {
  display: flex;
  flex-direction: column;
  gap: 18px;
  padding: 4px 2px 24px;
}

.wce-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding-bottom: 14px;
  border-bottom: 1px solid #eee;
}

.wce-h {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  color: #18181b;
}

.wce-sub {
  margin: 4px 0 0;
  font-size: 12px;
  color: #9a9a9a;
}

.wce-head-ops {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.wce-dirty {
  font-size: 11px;
  color: #b45309;
  background: #fef3c7;
  border-radius: 999px;
  padding: 2px 8px;
}

.wce-btn {
  height: 30px;
  padding: 0 14px;
  border-radius: 8px;
  border: 1px solid #e4e4e7;
  background: #fff;
  color: #3f3f46;
  font-size: 12px;
  cursor: pointer;
  transition: background 0.15s, border-color 0.15s;
}

.wce-btn:hover:not(:disabled) { background: #fafafa; }

.wce-btn:disabled { opacity: 0.45; cursor: not-allowed; }

.wce-btn--primary {
  background: #2563EB;
  border-color: #2563EB;
  color: #fff;
}

.wce-btn--primary:hover:not(:disabled) { background: #1D4ED8; border-color: #1D4ED8; }

.wce-field { display: flex; flex-direction: column; gap: 8px; }

.wce-field-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.wce-btn--refresh {
  height: 26px;
  padding: 0 10px;
  font-size: 11px;
  border-radius: 7px;
  flex-shrink: 0;
}

.wce-warn {
  margin: 0;
  font-size: 11px;
  line-height: 1.5;
  color: #b45309;
  background: #fffbeb;
  border: 1px solid #fde68a;
  border-radius: 8px;
  padding: 7px 10px;
}

.wce-label {
  display: flex;
  align-items: baseline;
  gap: 8px;
  font-size: 12px;
  font-weight: 600;
  color: #3f3f46;
}

.wce-label em {
  font-style: normal;
  font-weight: 400;
  font-size: 11px;
  color: #a1a1aa;
}

.wce-input,
.wce-textarea {
  width: 100%;
  box-sizing: border-box;
  border: 1px solid #e4e4e7;
  border-radius: 10px;
  padding: 10px 12px;
  font-size: 13px;
  color: #18181b;
  background: #fff;
  outline: none;
  transition: border-color 0.15s, box-shadow 0.15s;
  font-family: inherit;
}

.wce-input:focus,
.wce-textarea:focus {
  border-color: #2563EB;
  box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.1);
}

/* 自适应高度：高度由 JS 按内容算，这里只兜住下限与超长上限。
   resize: none 去掉右下角的拖拽手柄，避免出现一个能手动拉、又会被自动重算的框。 */
.wce-textarea {
  resize: none;
  min-height: 180px;
  max-height: 52vh;
  line-height: 1.7;
  overflow-y: auto;
}

.wce-tags {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  border: 1px solid #e4e4e7;
  border-radius: 10px;
  padding: 8px 10px;
  background: #fff;
}

.wce-tag {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  font-size: 12px;
  color: #2b6cb0;
  background: #eff6ff;
  border-radius: 999px;
  padding: 3px 8px;
}

.wce-tag-x {
  border: none;
  background: transparent;
  color: #93a8c4;
  font-size: 12px;
  line-height: 1;
  cursor: pointer;
  padding: 0;
}

.wce-tag-x:hover { color: #2b6cb0; }

.wce-tag-input {
  flex: 1;
  min-width: 120px;
  border: none;
  outline: none;
  font-size: 12px;
  padding: 3px 0;
  color: #18181b;
  font-family: inherit;
}

/* 图片区现在是表单第一块，作为主视觉呈现：
   缩略图放大到 132 起步，图片少时不会在 500px 宽的行里缩成一小撮 */
.wce-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(132px, 1fr));
  gap: 10px;
}

.wce-thumb {
  position: relative;
  border-radius: 10px;
  overflow: hidden;
  border: 2px solid transparent;
  background: #f4f4f5;
  cursor: pointer;
  aspect-ratio: 3 / 4;
}

.wce-thumb--active { border-color: #2563EB; }

.wce-thumb img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

/* 图片链接彻底失效时把碎图藏掉，格子保留占位底色和序号，仍可排序/删除 */
.wce-thumb img[data-failed="1"] {
  visibility: hidden;
}

.wce-thumb-no {
  position: absolute;
  top: 6px;
  left: 6px;
  min-width: 18px;
  height: 18px;
  border-radius: 9px;
  background: rgba(0, 0, 0, 0.5);
  color: #fff;
  font-size: 11px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.wce-thumb-cover {
  position: absolute;
  top: 6px;
  right: 6px;
  padding: 1px 6px;
  border-radius: 8px;
  background: #2563EB;
  color: #fff;
  font-size: 10px;
}

.wce-thumb-ops {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  display: flex;
  gap: 4px;
  padding: 6px;
  background: linear-gradient(to top, rgba(0, 0, 0, 0.62), rgba(0, 0, 0, 0));
}

.wce-thumb-ops button {
  flex: 1;
  border: none;
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.9);
  color: #3f3f46;
  font-size: 11px;
  padding: 3px 0;
  cursor: pointer;
}

.wce-thumb-ops button:disabled { opacity: 0.4; cursor: not-allowed; }

.wce-thumb-del { color: #71717A !important; }

.wce-empty {
  margin: 0;
  font-size: 12px;
  color: #a1a1aa;
  padding: 12px;
  border: 1px dashed #e4e4e7;
  border-radius: 10px;
  text-align: center;
}
</style>