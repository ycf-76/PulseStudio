<template>
  <aside class="wdp" v-if="work">
    <div class="wdp-header">
      <div class="wdp-header-info">
        <span class="wdp-header-title">{{ work.title }}</span>
      </div>
      <button class="wdp-close" @click="$emit('close')" title="关闭">
        <X :size="16" :stroke-width="1.8" />
      </button>
    </div>

    <nav class="wdp-tabs" v-if="currentTabs.length">
      <a
        v-for="tab in currentTabs"
        :key="tab.key"
        class="wdp-tab"
        :class="{ 'wdp-tab-active': activeTab === tab.key }"
        @click="activeTab = tab.key"
      >
        <component :is="tab.icon" :size="13" :stroke-width="1.8" />
        <span>{{ tab.label }}</span>
      </a>
    </nav>

    <div class="wdp-body">
      <!-- ===== 未发布（草稿）模式：横向图文 + 发布平台 ===== -->
      <!-- 2026-11-04 统一预览格式：全类型固定 260px 高预览区，操作区位置不再随类型漂移 -->
      <template v-if="isDraft">
        <div class="wdp-preview-uniform" v-if="allPageHtmls.length > 1" :ref="setUniformRef" :style="uniformScaleStyle">
          <button class="wdp-gallery-nav wdp-gallery-prev" @click="scrollGallery(-1)" :class="{ 'wdp-gallery-nav-hidden': !canScrollPrev }" title="上一页">
            <ChevronLeft :size="14" :stroke-width="2.2" />
          </button>
          <div class="wdp-draft-gallery-track" ref="galleryRef" @scroll="onGalleryScroll">
            <div v-for="(html, i) in allPageHtmls" :key="i" class="wdp-draft-html-card">
              <iframe :srcdoc="html" sandbox="allow-same-origin" />
              <span class="wdp-page-num">{{ i + 1 }}</span>
            </div>
          </div>
          <button class="wdp-gallery-nav wdp-gallery-next" @click="scrollGallery(1)" :class="{ 'wdp-gallery-nav-hidden': !canScrollNext }" title="下一页">
            <ChevronRight :size="14" :stroke-width="2.2" />
          </button>
          <span class="wdp-draft-gallery-indicator">{{ currentImageIndex + 1 }} / {{ allPageHtmls.length }}</span>
        </div>
        <div class="wdp-preview-uniform" v-else-if="allImages.length" :style="{ aspectRatio: previewAr || undefined, '--wdp-preview-ar-num': previewAr || undefined }">
          <button class="wdp-gallery-nav wdp-gallery-prev" v-if="allImages.length > 1" @click="scrollGallery(-1)" :class="{ 'wdp-gallery-nav-hidden': !canScrollPrev }" title="上一张">
            <ChevronLeft :size="14" :stroke-width="2.2" />
          </button>
          <div class="wdp-draft-gallery-track" ref="galleryRef" @scroll="onGalleryScroll">
            <div v-for="(img, i) in allImages" :key="i" class="wdp-draft-gallery-card">
              <img :src="img" :alt="`${work.title} - ${i + 1}`" @load="onPreviewImgLoad" @error="onImageError($event, img)" />
            </div>
          </div>
          <button class="wdp-gallery-nav wdp-gallery-next" v-if="allImages.length > 1" @click="scrollGallery(1)" :class="{ 'wdp-gallery-nav-hidden': !canScrollNext }" title="下一张">
            <ChevronRight :size="14" :stroke-width="2.2" />
          </button>
          <span class="wdp-draft-gallery-indicator" v-if="allImages.length > 1">{{ currentImageIndex + 1 }} / {{ allImages.length }}</span>
        </div>
        <div v-else-if="firstPageHtml" class="wdp-preview-uniform wdp-draft-html-preview" :ref="setPreviewBoxRef" :style="previewScaleStyle">
          <iframe :srcdoc="firstPageHtml" class="wdp-draft-iframe" sandbox="allow-same-origin" />
        </div>
        <div v-else class="wdp-preview-uniform wdp-draft-gallery-empty">
          <ImageIcon :size="32" :stroke-width="1.2" />
          <span>暂无图片，可通过对话生成</span>
        </div>

        <div class="wdp-section">
          <div class="wdp-section-title">作品信息</div>
          <div class="wdp-meta">
            <span class="wdp-meta-chip wdp-type-chip">{{ contentTypeLabel }}</span>
            <span class="wdp-meta-chip">{{ work.title || '未命名' }}</span>
          </div>
          <div class="wdp-tags" v-if="work.tags?.length">
            <span class="wdp-tag" v-for="tag in work.tags.slice(0, 8)" :key="tag">#{{ tag }}</span>
          </div>
          <div class="wdp-content-text rl-plain rl-plain--scroll wdp-text--compact" v-if="work.contentText">{{ work.contentText }}</div>
        </div>

        <!-- 2026-11-14 草稿 → 卡片编辑器最小闭环：选目标（新建/已有）+ 覆盖/追加，同步后直接进编辑器 -->
        <div class="wdp-section">
          <div class="wdp-section-title">同步到我的空间</div>
          <button class="wdp-action-btn" :disabled="!hasSyncableDeck" @click="openSyncPanel">
            <FolderOutput :size="14" /> 同步到卡片编辑器
          </button>
          <p class="wdp-hint" v-if="!hasSyncableDeck">该草稿暂无卡片结构（deck），暂不支持同步</p>
        </div>
      </template>

      <!-- ===== 已有作品模式 ===== -->
      <template v-else>
        <!-- 预览 Tab（含数据） -->
        <template v-if="activeTab === 'preview'">
          <div class="wdp-preview-uniform" v-if="allImages.length" :style="{ aspectRatio: previewAr || undefined, '--wdp-preview-ar-num': previewAr || undefined }">
            <button class="wdp-gallery-nav wdp-gallery-prev" v-if="allImages.length > 1" @click="scrollGallery(-1)" :class="{ 'wdp-gallery-nav-hidden': !canScrollPrev }">
              <ChevronLeft :size="16" :stroke-width="2" />
            </button>
            <div class="wdp-draft-gallery-track" ref="galleryRef" @scroll="onGalleryScroll">
              <div v-for="(img, i) in allImages" :key="i" class="wdp-draft-gallery-card">
                <img :src="img" :alt="`${work.title} - ${i + 1}`" @load="onPreviewImgLoad" @error="onImageError($event, img)" />
              </div>
            </div>
            <button class="wdp-gallery-nav wdp-gallery-next" v-if="allImages.length > 1" @click="scrollGallery(1)" :class="{ 'wdp-gallery-nav-hidden': !canScrollNext }">
              <ChevronRight :size="16" :stroke-width="2" />
            </button>
            <span class="wdp-draft-gallery-indicator" v-if="allImages.length > 1">{{ currentImageIndex + 1 }} / {{ allImages.length }}</span>
          </div>
          <div class="wdp-section">
            <div class="wdp-meta">
              <span class="wdp-meta-chip wdp-platform-chip">{{ platformLabel }}</span>
              <span class="wdp-meta-chip wdp-type-chip">{{ contentTypeLabel }}</span>
              <span class="wdp-meta-chip" v-if="work.publishedAt">{{ formatDate(work.publishedAt) }}</span>
            </div>
            <div class="wdp-tags" v-if="work.tags?.length">
              <span class="wdp-tag" v-for="tag in work.tags.slice(0, 8)" :key="tag">#{{ tag }}</span>
            </div>
          </div>
          <div class="wdp-section">
            <div class="wdp-metrics">
              <div class="wdp-metric">
                <span class="wdp-metric-value">{{ fmtNum(work.views || work.playCount || 0) }}</span>
                <span class="wdp-metric-label">{{ isVideo ? '播放' : '浏览' }}</span>
              </div>
              <div class="wdp-metric">
                <span class="wdp-metric-value">{{ fmtNum(work.likes) }}</span>
                <span class="wdp-metric-label">点赞</span>
              </div>
              <div class="wdp-metric">
                <span class="wdp-metric-value">{{ fmtNum(work.collects) }}</span>
                <span class="wdp-metric-label">收藏</span>
              </div>
              <div class="wdp-metric">
                <span class="wdp-metric-value">{{ fmtNum(work.comments) }}</span>
                <span class="wdp-metric-label">评论</span>
              </div>
              <div class="wdp-metric">
                <span class="wdp-metric-value">{{ fmtNum(work.shares) }}</span>
                <span class="wdp-metric-label">分享</span>
              </div>
              <div class="wdp-metric">
                <span class="wdp-metric-value">{{ (work.interactionRate * 100).toFixed(1) }}%</span>
                <span class="wdp-metric-label">互动率</span>
              </div>
              <div class="wdp-metric" v-if="work.completionRate != null">
                <span class="wdp-metric-value">{{ (work.completionRate * 100).toFixed(0) }}%</span>
                <span class="wdp-metric-label">完播率</span>
              </div>
              <div class="wdp-metric">
                <span class="wdp-metric-value">{{ work.viralScore }}</span>
                <span class="wdp-metric-label">传播分</span>
              </div>
            </div>
            <div class="wdp-viral-type" v-if="work.viralType">{{ viralTypeLabel }}</div>
          </div>
          <div class="wdp-section" v-if="work.contentText || work.scriptText">
            <div class="wdp-section-title">{{ work.contentType === 'image_text' ? '正文' : '脚本' }}</div>
            <div class="wdp-content-text rl-plain rl-plain--scroll">{{ work.contentText || work.scriptText }}</div>
          </div>
        </template>

        <!-- 分析 Tab -->
        <template v-if="activeTab === 'analysis'">
          <div class="wdp-section" v-if="work.aiDiagnosis">
            <div class="wdp-diagnosis">
              <div class="wdp-diagnosis-reason">{{ work.aiDiagnosis.performanceReason }}</div>
              <div class="wdp-diagnosis-group" v-if="work.aiDiagnosis.boostFactors?.length">
                <div class="wdp-diagnosis-label wdp-boost">加分因子</div>
                <div class="wdp-diagnosis-item" v-for="(f, i) in work.aiDiagnosis.boostFactors" :key="i">
                  <span class="wdp-diagnosis-factor">{{ f.factor }}</span>
                  <span class="wdp-diagnosis-evidence">{{ f.evidence }}</span>
                </div>
              </div>
              <div class="wdp-diagnosis-group" v-if="work.aiDiagnosis.dragFactors?.length">
                <div class="wdp-diagnosis-label wdp-drag">扣分因子</div>
                <div class="wdp-diagnosis-item" v-for="(f, i) in work.aiDiagnosis.dragFactors" :key="i">
                  <span class="wdp-diagnosis-factor">{{ f.factor }}</span>
                  <span class="wdp-diagnosis-evidence">{{ f.evidence }}</span>
                </div>
              </div>
              <div class="wdp-diagnosis-action" v-if="work.aiDiagnosis.nextAction">
                <span class="wdp-diagnosis-label wdp-action">下一步</span>
                {{ work.aiDiagnosis.nextAction }}
              </div>
            </div>
          </div>
          <div class="wdp-empty" v-else>
            <span>暂无 AI 诊断数据</span>
            <button class="wdp-action-btn" @click="$emit('chat-action', 'analyze')">
              <BarChart3 :size="14" /> 开始归因分析
            </button>
          </div>
        </template>

        <!-- 修改 Tab -->
        <template v-if="activeTab === 'edit'">
          <div class="wdp-section">
            <div class="wdp-section-title">导入已完成作品</div>
            <p class="wdp-hint">从已采集的作品中导入内容作为改写基础</p>
            <button class="wdp-action-btn" @click="showImportPanel = !showImportPanel">
              <Download :size="14" /> {{ showImportPanel ? '收起列表' : '选择作品导入' }}
            </button>
            <div class="wdp-import-list" v-if="showImportPanel && importableWorks.length">
              <a
                v-for="iw in importableWorks"
                :key="iw.id"
                class="wdp-similar-item wdp-import-item"
                @click="importFromWork(iw)"
              >
                <span class="wdp-work-mini-cover" :style="iw.coverUrl ? { backgroundImage: `url(${iw.coverUrl})` } : {}">
                  <span v-if="!iw.coverUrl" class="wdp-cover-placeholder">{{ iw.title.charAt(0) }}</span>
                </span>
                <div class="wdp-similar-info">
                  <span class="wdp-similar-title">{{ iw.title }}</span>
                  <span class="wdp-similar-meta">{{ workStore.getPlatformLabel(iw.platform) }} · {{ iw.performanceTier }}级</span>
                </div>
              </a>
            </div>
            <div class="wdp-import-empty" v-if="showImportPanel && !importableWorks.length">
              <span>暂无可导入的作品，请先采集</span>
            </div>
          </div>
          <div class="wdp-section">
            <div class="wdp-section-title">快捷操作</div>
            <div class="wdp-actions">
              <button class="wdp-action-btn" @click="$emit('chat-action', 'optimize_title')">
                <Type :size="14" /> 优化标题
              </button>
              <button class="wdp-action-btn" @click="$emit('chat-action', 'rewrite')">
                <PenLine :size="14" /> 改写内容
              </button>
              <button class="wdp-action-btn" @click="$emit('chat-action', 'design_cover')">
                <ImageIcon :size="14" /> 设计封面
              </button>
              <button class="wdp-action-btn" @click="$emit('chat-action', 'evolve')">
                <Dna :size="14" /> 进化提炼
              </button>
            </div>
          </div>
        </template>

        <!-- 操作 Tab（已发布专属） -->
        <template v-if="activeTab === 'action'">
          <div class="wdp-section">
            <div class="wdp-section-title">二次创作</div>
            <p class="wdp-hint">基于已发布作品创建新版本草稿</p>
            <div class="wdp-actions">
              <button class="wdp-action-btn wdp-action-primary" @click="onForkAsDraft">
                <Sparkles :size="14" /> 复制为草稿
              </button>
              <button class="wdp-action-btn" @click="$emit('chat-action', 'evolve')">
                <Dna :size="14" /> 进化提炼
              </button>
              <button class="wdp-action-btn" @click="$emit('chat-action', 'rewrite')">
                <PenLine :size="14" /> 改写内容
              </button>
            </div>
          </div>
          <div class="wdp-section">
            <div class="wdp-section-title">优化</div>
            <div class="wdp-actions">
              <button class="wdp-action-btn" @click="$emit('chat-action', 'optimize_title')">
                <Type :size="14" /> 优化标题
              </button>
              <button class="wdp-action-btn" @click="$emit('chat-action', 'design_cover')">
                <ImageIcon :size="14" /> 设计封面
              </button>
            </div>
          </div>
          <div class="wdp-section" v-if="work.url">
            <div class="wdp-section-title">原文链接</div>
            <a :href="work.url" target="_blank" rel="noopener" class="wdp-original-link">
              <Search :size="12" />
              <span>{{ work.url.length > 50 ? work.url.slice(0, 47) + '…' : work.url }}</span>
            </a>
          </div>
          <div class="wdp-section">
            <div class="wdp-section-title">数据刷新</div>
            <p class="wdp-hint">重新采集最新数据指标</p>
            <div class="wdp-actions">
              <button class="wdp-action-btn" @click="$emit('chat-action', 'analyze')">
                <BarChart3 :size="14" /> 归因分析
              </button>
            </div>
          </div>
        </template>

        <!-- 视频 Tab -->
        <template v-if="activeTab === 'video'">
          <div class="wdp-section">
            <div class="wdp-section-title">视频数据</div>
            <div class="wdp-metrics">
              <div class="wdp-metric">
                <span class="wdp-metric-value">{{ fmtNum(work.playCount || work.views || 0) }}</span>
                <span class="wdp-metric-label">播放量</span>
              </div>
              <div class="wdp-metric" v-if="work.completionRate != null">
                <span class="wdp-metric-value">{{ (work.completionRate * 100).toFixed(0) }}%</span>
                <span class="wdp-metric-label">完播率</span>
              </div>
              <div class="wdp-metric">
                <span class="wdp-metric-value">{{ (work.interactionRate * 100).toFixed(1) }}%</span>
                <span class="wdp-metric-label">互动率</span>
              </div>
            </div>
          </div>
          <div class="wdp-section" v-if="work.scriptText">
            <div class="wdp-section-title">脚本</div>
            <div class="wdp-content-text rl-plain rl-plain--scroll">{{ work.scriptText }}</div>
          </div>
          <div class="wdp-section">
            <div class="wdp-section-title">视频操作</div>
            <div class="wdp-actions">
              <button class="wdp-action-btn" @click="$emit('chat-action', 'analyze')">
                <BarChart3 :size="14" /> 归因分析
              </button>
              <button class="wdp-action-btn" @click="$emit('chat-action', 'rewrite')">
                <PenLine :size="14" /> 改写脚本
              </button>
              <button class="wdp-action-btn" @click="$emit('chat-action', 'design_cover')">
                <ImageIcon :size="14" /> 设计封面
              </button>
              <button class="wdp-action-btn" @click="$emit('chat-action', 'evolve')">
                <Dna :size="14" /> 进化提炼
              </button>
            </div>
          </div>
        </template>
      </template>
    </div>

    <!-- 同步目标选择弹窗（2026-11-14 草稿 → 卡片编辑器） -->
    <Teleport to="body">
      <div class="wdp-sync-overlay" v-if="syncPanelOpen" @click.self="syncPanelOpen = false">
        <div class="wdp-sync-dialog">
          <div class="wdp-sync-head">
            <span class="wdp-sync-title">同步到卡片编辑器</span>
            <button class="wdp-sync-close" @click="syncPanelOpen = false"><X :size="14" /></button>
          </div>
          <div class="wdp-sync-body">
            <div class="wdp-sync-label">目标作品</div>
            <div class="wdp-sync-list">
              <button
                class="wdp-sync-item"
                :class="{ 'wdp-sync-item-active': syncTargetId === '' }"
                @click="syncTargetId = ''"
              >
                <FolderOutput :size="14" />
                <span class="wdp-sync-item-title">新建卡片作品</span>
                <span class="wdp-sync-item-meta">以当前草稿建一张新卡</span>
              </button>
              <div v-if="syncLoadingList" class="wdp-sync-loading">加载中...</div>
              <template v-else>
                <button
                  v-for="t in syncTargets"
                  :key="t.artifact_id"
                  class="wdp-sync-item"
                  :class="{ 'wdp-sync-item-active': syncTargetId === t.artifact_id }"
                  @click="syncTargetId = t.artifact_id"
                >
                  <FileText :size="14" />
                  <span class="wdp-sync-item-title">{{ t.title || '未命名卡片' }}</span>
                </button>
                <div v-if="!syncTargets.length" class="wdp-sync-loading">暂无已有卡片作品</div>
              </template>
            </div>
            <template v-if="syncTargetId">
              <div class="wdp-sync-label">写入方式</div>
              <div class="wdp-sync-modes">
                <button
                  class="wdp-sync-mode"
                  :class="{ 'wdp-sync-mode-active': syncMode === 'append' }"
                  @click="syncMode = 'append'"
                >
                  <span class="wdp-sync-mode-title">追加</span>
                  <span class="wdp-sync-mode-desc">把草稿页面拼到目标作品末尾</span>
                </button>
                <button
                  class="wdp-sync-mode"
                  :class="{ 'wdp-sync-mode-active': syncMode === 'overwrite' }"
                  @click="syncMode = 'overwrite'"
                >
                  <span class="wdp-sync-mode-title">覆盖</span>
                  <span class="wdp-sync-mode-desc">用草稿整体替换目标内容</span>
                </button>
              </div>
            </template>
            <div class="wdp-sync-error" v-if="syncError">{{ syncError }}</div>
          </div>
          <div class="wdp-sync-foot">
            <button class="wdp-sync-btn" @click="syncPanelOpen = false">取消</button>
            <button class="wdp-sync-btn wdp-sync-btn-primary" :disabled="syncBusy" @click="confirmSync">
              {{ syncBusy ? '同步中...' : '同步并打开编辑器' }}
            </button>
          </div>
        </div>
      </div>
    </Teleport>
  </aside>
</template>

<script setup lang="ts">
import './creation/panel-shared.css'
import { computed, ref, watch, nextTick, onBeforeUnmount, type Component } from 'vue'
import { useRouter } from 'vue-router'
import {
  X, Eye, BarChart3, PenLine, Type, Image as ImageIcon, Dna,
  Lightbulb, Sparkles, Send, Search, ChevronLeft, ChevronRight,
  Download, Wand2, FolderOutput
} from 'lucide-vue-next'
import { useWorkStore, type WorkItem } from '@/stores/work'
import {
  buildCreativeArtifact,
  buildStoryboard,
  getCreativeArtifact,
  listCreativeArtifacts,
  patchCreativeArtifact,
} from '@/api/creativeArtifact'
import type { ArtifactSummary } from '@/types/creativeArtifact'

interface TabDef {
  key: string
  label: string
  icon: Component
}

const props = defineProps<{
  work: WorkItem | null

  visible: boolean
}>()

const emit = defineEmits<{
  close: []
  'chat-action': [action: string]
}>()

const workStore = useWorkStore()
const activeTab = ref('discover')
const showImportPanel = ref(false)

// ===== 统一预览区的 iframe 自适应缩放（2026-11-04 修侧边栏大空白）=====
// 旧实现 .wdp-draft-iframe 是 1080x1440 布局尺寸 + transform scale(0.16)：
// transform 不改布局盒，容器被撑到 1440px 高 → 中间大块空白、操作区挤到底部。
// 现在：预览区固定 260px 高，ResizeObserver 按容器宽算 scale + 居中偏移。
const NATIVE_W = 1080
const NATIVE_H = 1440
const previewScaleStyle = ref<Record<string, string>>({})
let previewRO: ResizeObserver | null = null

function setPreviewBoxRef(el: unknown) {
  if (!el || !(el instanceof HTMLElement)) return
  previewRO?.disconnect()
  previewRO = new ResizeObserver((entries) => {
    const rect = entries[0]?.contentRect
    if (!rect) return
    const scale = Math.min(rect.width / NATIVE_W, rect.height / NATIVE_H)
    const offsetX = Math.max(0, (rect.width - NATIVE_W * scale) / 2)
    const offsetY = Math.max(0, (rect.height - NATIVE_H * scale) / 2)
    previewScaleStyle.value = {
      '--preview-scale': String(scale),
      '--preview-x': `${offsetX}px`,
      '--preview-y': `${offsetY}px`,
    }
  })
  previewRO.observe(el)
}

function teardownPreviewRO() {
  previewRO?.disconnect()
  previewRO = null
}

// ===== 2026-11-05：预览窗重做 —— 全宽大窗 + 比例自适应，图片整图显示不裁切 =====
// 旧版固定 150px 高 + 108x144 小卡，图被裁成窄条（大哥反馈"窗口被限制了"）。
// 预览窗宽高比取首张加载成功的图片的原始比例（夹 0.7~1.9），作品切换时重置；
// HTML 卡片分支固定 3/4（原生 1080x1440），scale 按容器宽实时计算整屏铺满。
const previewAr = ref('')
function onPreviewImgLoad(e: Event) {
  const img = e.target as HTMLImageElement
  if (!img.naturalWidth || !img.naturalHeight || previewAr.value) return
  const ar = Math.min(1.9, Math.max(0.7, img.naturalWidth / img.naturalHeight))
  previewAr.value = String(ar)
}

const uniformScaleStyle = ref<Record<string, string>>({})
let uniformRO: ResizeObserver | null = null
function setUniformRef(el: unknown) {
  if (!el || !(el instanceof HTMLElement)) return
  uniformRO?.disconnect()
  uniformRO = new ResizeObserver((entries) => {
    const w = entries[0]?.contentRect.width
    if (!w) return
    uniformScaleStyle.value = { '--wdp-card-scale': String(w / NATIVE_W) }
  })
  uniformRO.observe(el)
}
function teardownUniformRO() {
  uniformRO?.disconnect()
  uniformRO = null
}

const allImages = computed(() => {
  if (!props.work) return []
  const imgs = props.work.images?.filter(Boolean) || []
  if (imgs.length > 0) return imgs
  const pages = props.work.cardDraft?.pages
  if (pages && pages.length > 0) {
    const pageImages: string[] = []
    for (const p of pages) {
      const page = p as any
      if (page?.imageUrl) pageImages.push(page.imageUrl)
      if (Array.isArray(page?.pngUrls)) pageImages.push(...page.pngUrls.filter(Boolean))
      if (page?.content?.imageUrl) pageImages.push(page.content.imageUrl)
      if (Array.isArray(page?.content?.pngUrls)) pageImages.push(...page.content.pngUrls.filter(Boolean))
    }
    if (pageImages.length > 0) return pageImages
  }
  const pngUrls = props.work.cardDraft?.pngUrls
  if (Array.isArray(pngUrls) && pngUrls.length > 0) return pngUrls.filter(Boolean)
  if (props.work.coverUrl) return [props.work.coverUrl]
  return []
})

const firstPageHtml = computed(() => {
  if (!props.work) return ''
  if (props.work.firstPageHtml) return props.work.firstPageHtml
  const pages = props.work.cardDraft?.pages
  if (pages && pages.length > 0) {
    const first = pages[0] as any
    if (first?.content?.htmlContent) return first.content.htmlContent
    if (first?.htmlContent) return first.htmlContent
  }
  return ''
})

const allPageHtmls = computed(() => {
  if (!props.work) return []
  if (props.work.allPageHtmls && props.work.allPageHtmls.length > 0) return props.work.allPageHtmls
  const pages = props.work.cardDraft?.pages
  if (pages && pages.length > 0) {
    const htmls: string[] = []
    for (const p of pages) {
      const page = p as any
      const html = page?.content?.htmlContent || page?.htmlContent || ''
      if (html) htmls.push(html)
    }
    if (htmls.length > 0) return htmls
  }
  return []
})

const galleryRef = ref<HTMLElement | null>(null)
const canScrollPrev = ref(false)
const canScrollNext = ref(false)
const currentImageIndex = ref(0)

function scrollGallery(dir: number) {
  const el = galleryRef.value
  if (!el) return
  const scrollAmount = el.clientWidth * 0.8
  el.scrollBy({ left: dir * scrollAmount, behavior: 'smooth' })
}

function onGalleryScroll() {
  const el = galleryRef.value
  if (!el) return
  canScrollPrev.value = el.scrollLeft > 2
  canScrollNext.value = el.scrollLeft + el.clientWidth < el.scrollWidth - 2
  const cardWidth = el.firstElementChild ? (el.firstElementChild as HTMLElement).offsetWidth + 8 : 1
  currentImageIndex.value = Math.round(el.scrollLeft / cardWidth)
}

//画廊挂载/切换作品后同步一次按钮可见态，否则「下一页」要等首次滚动才出现
watch([allPageHtmls, allImages], () => {
  nextTick(() => onGalleryScroll())
})

const isDraft = computed(() => props.work?.isDraft === true)

const isPublished = computed(() => {
  if (!props.work) return false
  const status = (props.work as any).contentStatus as string | undefined
  return status === 'published' || (!status && !props.work.isDraft && !!props.work.publishedAt)
})

const isVideo = computed(() => {
  if (props.work) return ['short_video', 'long_video', 'live_clip'].includes(props.work.contentType)
  return workStore.activeCreationType === 'short_video'
})

const creationType = computed(() => {
  if (props.work) {
    const ct = props.work.contentType
    if (ct === 'image_text' || ct === 'image_gallery') return 'image_text'
    if (ct === 'short_video') return 'short_video'
    if (ct === 'long_video') return 'voiceover'
    if (ct === 'ai_edit') return 'ai_edit'
    if (ct === 'live_clip') return 'live_clip'
    if (ct === 'long_article') return 'long_article'
    return 'image_text'
  }
  return workStore.activeCreationType
})

const FIVE_LAYER_TABS: TabDef[] = [
  { key: 'discover', label: '发现', icon: Eye },
  { key: 'plan', label: '策划', icon: Lightbulb },
  { key: 'produce', label: '制作', icon: Wand2 },
  { key: 'publish', label: '发布', icon: Send },
  { key: 'attribute', label: '归因', icon: BarChart3 },
]

const ANALYSIS_TABS: TabDef[] = [
  { key: 'preview', label: '预览', icon: Eye },
  { key: 'analysis', label: '分析', icon: BarChart3 },
  { key: 'edit', label: '修改', icon: PenLine },
]

const VIDEO_ANALYSIS_TABS: TabDef[] = [
  { key: 'preview', label: '预览', icon: Eye },
  { key: 'video', label: '视频', icon: ImageIcon },
  { key: 'analysis', label: '分析', icon: BarChart3 },
  { key: 'edit', label: '修改', icon: PenLine },
]

const PUBLISHED_TABS: TabDef[] = [
  { key: 'preview', label: '数据', icon: Eye },
  { key: 'analysis', label: '诊断', icon: BarChart3 },
  { key: 'action', label: '操作', icon: PenLine },
]

const PUBLISHED_VIDEO_TABS: TabDef[] = [
  { key: 'preview', label: '数据', icon: Eye },
  { key: 'video', label: '视频', icon: ImageIcon },
  { key: 'analysis', label: '诊断', icon: BarChart3 },
  { key: 'action', label: '操作', icon: PenLine },
]

const currentTabs = computed(() => {
  if (isDraft.value) return []
  if (isPublished.value) return isVideo.value ? PUBLISHED_VIDEO_TABS : PUBLISHED_TABS
  return isVideo.value ? VIDEO_ANALYSIS_TABS : ANALYSIS_TABS
})

const importableWorks = computed(() => {
  return workStore.worksByStatus.collected.filter(w =>
    w.id !== props.work?.id &&
    (w.contentText || w.coverUrl || w.tags?.length)
  )
})

function importFromWork(source: WorkItem) {
  if (!props.work) return
  const updates: Partial<WorkItem> = {}
  if (source.title && (!props.work.title || props.work.title === '未命名创作')) {
    updates.title = source.title
  }
  if (source.contentText && !props.work.contentText) {
    updates.contentText = source.contentText
  }
  if (source.coverUrl && !props.work.coverUrl) {
    updates.coverUrl = source.coverUrl
  }
  if (source.images?.length && !props.work.images?.length) {
    updates.images = source.images
  }
  if (source.tags?.length && (!props.work.tags || props.work.tags.length === 0)) {
    updates.tags = [...source.tags]
  }
  if (source.platform && props.work.isDraft) {
    updates.platform = source.platform
  }
  if (Object.keys(updates).length > 0) {
    workStore.updateDraft(props.work.id, updates)
  }
  showImportPanel.value = false
}

function onForkAsDraft() {
  if (!props.work) return
  const draft = workStore.createDraft(props.work.contentType || 'image_text')
  workStore.updateDraft(draft.id, {
    title: props.work.title ? props.work.title + '（副本）' : '未命名创作',
    contentText: props.work.contentText || '',
    coverUrl: props.work.coverUrl || '',
    images: props.work.images ? [...props.work.images] : [],
    tags: props.work.tags ? [...props.work.tags] : [],
    platform: props.work.platform || 'xiaohongshu',
  })
  workStore.setActiveWork(draft.id)
}

// ===== 草稿 → 卡片编辑器同步（2026-11-14 最小闭环）=====
// 只支持带 deck 结构的草稿：deck.pages 是编辑器的块结构，没有它编辑器无从编辑。
// 新建目标走 /build（persist），已有目标走 PATCH 写 card_deck + storyboard；
// 追加时先拉目标产物把 deck.pages 拼起来，覆盖时直接整份替换。
const router = useRouter()

const syncPanelOpen = ref(false)
const syncLoadingList = ref(false)
const syncTargets = ref<ArtifactSummary[]>([])
const syncTargetId = ref('')
const syncMode = ref<'append' | 'overwrite'>('append')
const syncBusy = ref(false)
const syncError = ref('')

const hasSyncableDeck = computed(() => {
  const deck = (props.work?.cardDraft as any)?.deck
  return Boolean(deck?.pages?.length)
})

async function openSyncPanel() {
  syncPanelOpen.value = true
  syncError.value = ''
  syncTargetId.value = ''
  syncMode.value = 'append'
  if (syncTargets.value.length) return
  syncLoadingList.value = true
  try {
    syncTargets.value = await listCreativeArtifacts(30)
  } catch {
    syncTargets.value = []
  } finally {
    syncLoadingList.value = false
  }
}

async function confirmSync() {
  if (!props.work || syncBusy.value) return
  const cardDraft = (props.work.cardDraft || {}) as Record<string, any>
  const deck = cardDraft.deck
  if (!deck?.pages?.length) return
  syncBusy.value = true
  syncError.value = ''
  try {
    let targetId = syncTargetId.value
    if (!targetId) {
      // 新建：整份 card_draft 交给后端 build 落盘
      const built = await buildCreativeArtifact({
        card_draft: cardDraft,
        brief: { title: props.work.title || '未命名卡片' },
        persist: true,
      })
      targetId = built.artifact_id
    } else if (syncMode.value === 'overwrite') {
      const storyboard = await buildStoryboard(cardDraft).catch(() => [])
      await patchCreativeArtifact(targetId, {
        card_deck: deck,
        storyboard,
        brief: { title: props.work.title || '' },
      })
    } else {
      const target = await getCreativeArtifact(targetId)
      if (!target) throw new Error('目标作品加载失败')
      const baseDeck = (target.card_deck || {}) as Record<string, any>
      const draftStoryboard = await buildStoryboard(cardDraft).catch(() => [])
      await patchCreativeArtifact(targetId, {
        card_deck: { ...baseDeck, pages: [...(baseDeck.pages || []), ...deck.pages] },
        storyboard: [...((target.storyboard || []) as any[]), ...draftStoryboard],
      })
    }
    syncPanelOpen.value = false
    await router.push(`/portfolio/${targetId}?source=artifact&edit=1`)
  } catch (e: any) {
    syncError.value = e?.message || '同步失败，请重试'
  } finally {
    syncBusy.value = false
  }
}

const VIRAL_TYPE_LABELS: Record<string, string> = {
  collect_driven: '收藏驱动',
  share_driven: '分享驱动',
  comment_driven: '评论驱动',
  like_driven: '点赞驱动',
  balanced: '均衡型',
}

const viralTypeLabel = computed(() => {
  if (!props.work?.viralType) return ''
  return VIRAL_TYPE_LABELS[props.work.viralType] || props.work.viralType
})

const platformLabel = computed(() =>
  props.work ? workStore.getPlatformLabel(props.work.platform) : ''
)

const contentTypeLabel = computed(() =>
  props.work ? workStore.getContentTypeLabel(props.work.contentType) : ''
)

function fmtNum(n: number): string {
  return workStore.formatNumber(n)
}

function formatDate(d: string): string {
  try {
    return new Date(d).toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' })
  } catch {
    return d
  }
}

function onImageError(e: Event, originalUrl: string) {
  const img = e.target as HTMLImageElement
  const currentSrc = img.src

  if (!currentSrc.includes('/api/proxy/image')) {
    if (originalUrl && (originalUrl.includes('xhscdn.com') ||
        originalUrl.includes('xiaohongshu.com') ||
        originalUrl.includes('picasso-static') ||
        originalUrl.includes('douyinvod.com') ||
        originalUrl.includes('bytedanceimg.com') ||
        originalUrl.includes('bilibili.com') ||
        originalUrl.startsWith('http'))) {
      img.src = '/api/proxy/image?url=' + encodeURIComponent(originalUrl)
      return
    }
  }

  img.style.display = 'none'
}

watch(() => props.work?.id, () => {
  previewAr.value = ''
  if (isDraft.value) {
    activeTab.value = ''
  } else if (isPublished.value) {
    activeTab.value = 'preview'
  } else {
    activeTab.value = 'preview'
  }
  showImportPanel.value = false
})
onBeforeUnmount(() => {
  teardownPreviewRO()
  teardownUniformRO()
})
</script>

<style scoped>
.wdp {
  height: 100%;
  background: #fff;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  flex-shrink: 0;
}

/* 「作品信息」里的正文比常规更矮（原为内联 style="max-height:120px"） */
.wdp-text--compact {
  max-height: 120px;
}

.wdp-header {
  flex: none;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 12px 6px;
}
.wdp-header-info {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  overflow: hidden;
}
.wdp-header-title {
  font-size: 13px;
  font-weight: 600;
  color: #1a1a1a;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.wdp-close {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border: none;
  background: none;
  border-radius:12px;
  color: #888;
  cursor: pointer;
  flex-shrink: 0;
}
.wdp-close:hover {
  background: rgba(0,0,0,0.05);
  color: var(--rl-text-1,#1A1A1A);
}

.wdp-tabs {
  flex: none;
  display: flex;
  gap: 2px;
  padding: 0 10px 0;
  border-bottom: 1px solid rgba(0,0,0,0.06);
}
.wdp-tab {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 7px 8px 9px;
  font-size: 11.5px;
  color: #888;
  cursor: pointer;
  border-bottom: 2px solid transparent;
  margin-bottom: -1px;
  transition: color 0.15s, border-color 0.15s;
  user-select: none;
  white-space: nowrap;
}
.wdp-tab:hover {
  color: #555;
}
.wdp-tab-active {
  color: #1a1a1a;
  font-weight: 600;
  border-bottom-color: #1a1a1a;
}

.wdp-body {
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden;
  padding: 10px 12px 16px;
  scrollbar-width: thin;
  scrollbar-color: rgba(0,0,0,0.12) transparent;
}
.wdp-body::-webkit-scrollbar { width: 3px; }
.wdp-body::-webkit-scrollbar-thumb { background: rgba(0,0,0,0.12); border-radius:4px; }

.wdp-gallery {
  position: relative;
  margin: 0 -12px 0;
  background: #f5f5f5;
  border-radius:12px;
  padding-bottom: 4px;
}
.wdp-gallery-track {
  display: flex;
  gap: 8px;
  overflow-x: auto;
  scroll-snap-type: x mandatory;
  padding: 8px 10px;
  scrollbar-width: thin;
  scrollbar-color: rgba(0,0,0,0.25) rgba(0,0,0,0.06);
  align-items: center;
}
.wdp-gallery-track::-webkit-scrollbar {
  height: 6px;
}
.wdp-gallery-track::-webkit-scrollbar-thumb {
  background: rgba(0,0,0,0.25);
  border-radius:6px;
}
.wdp-gallery-track::-webkit-scrollbar-thumb:hover {
  background: rgba(0,0,0,0.4);
}
.wdp-gallery-track::-webkit-scrollbar-track {
  background: rgba(0,0,0,0.06);
  border-radius:6px;
}
.wdp-gallery-card {
  flex: 0 0 auto;
  width: 120px;
  height: 160px;
  border-radius:10px;
  overflow: hidden;
  border: 1px solid rgba(0,0,0,0.08);
  scroll-snap-align: start;
  background: #fff;
}
.wdp-gallery-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
  cursor: pointer;
  transition: transform 0.15s;
}
.wdp-gallery-img:hover {
  transform: scale(1.02);
}
/* 2026-11-14 精细化翻页按钮：毛玻璃圆钮 + 细边 + 柔和投影，hover 微放大、按下回缩 */
.wdp-gallery-nav {
  position: absolute;
  top: 50%;
  transform: translateY(-50%);
  width: 26px;
  height: 26px;
  border-radius: 50%;
  border: 1px solid rgba(0, 0, 0, 0.06);
  background: rgba(255, 255, 255, 0.88);
  backdrop-filter: blur(6px);
  -webkit-backdrop-filter: blur(6px);
  box-shadow: 0 1px 6px rgba(0, 0, 0, 0.12);
  color: #4b4b4b;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  z-index: 3;
  padding: 0;
  transition: opacity 0.2s, background 0.15s, color 0.15s, transform 0.15s, box-shadow 0.15s;
}
.wdp-gallery-nav:hover {
  background: #fff;
  color: #111;
  transform: translateY(-50%) scale(1.08);
  box-shadow: 0 2px 10px rgba(0, 0, 0, 0.18);
}
.wdp-gallery-nav:active {
  transform: translateY(-50%) scale(0.94);
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.14);
}
.wdp-gallery-prev { left: 8px; }
.wdp-gallery-next { right: 8px; }
.wdp-gallery-nav-hidden { opacity: 0; pointer-events: none; }

/* 2026-11-14 与 NotePreview 排版方案对齐：信息行/标签居中，展示正文两端对齐。
   只覆盖展示文本，编辑框（wdp-draft-textarea）保持居左便于输入 */
.wdp-meta { justify-content: center; }
.wdp-tags { justify-content: center; }
.wdp-content-text { text-align: justify; }
.wdp-gallery-indicator {
  position: absolute;
  bottom: 8px;
  right: 12px;
  font-size: 9.5px;
  color: #fff;
  background: rgba(0,0,0,0.45);
  padding: 2px 6px;
  border-radius:12px;
}

/* ── 统一预览区（2026-11-05 重做）：全宽大窗 + 比例自适应，图片整图显示不裁切 ──
   旧版固定 150px 高 + 108x144 小卡，图被裁成窄条（大哥反馈"窗口被限制了"）。
   预览窗占满面板宽，宽高比由首图原始比例注入 CSS 变量（默认 3/4），
   多图整屏翻页（scroll-snap 一屏一图），HTML 卡片按容器宽实时缩放铺满。
   晚间补充：高度封顶 min(48vh, 420px) + 宽度随比例收缩 + 居中（与 NotePreview 同款），
   超大图在小屏下不撑满整页。 */
.wdp-preview-uniform {
  position: relative;
  width: 100%;
  max-width: calc(min(48vh, 420px) * var(--wdp-preview-ar-num, 0.75));
  aspect-ratio: var(--wdp-preview-ar, 3 / 4);
  max-height: min(48vh, 420px);
  margin: 0 auto 10px;
  border-radius: 12px;
  overflow: hidden;
  background: var(--rl-bg-2, #FAFBFC);
  border: 1px solid rgba(0, 0, 0, 0.05);
}
.wdp-preview-uniform .wdp-draft-gallery-track {
  display: flex;
  gap: 0;
  overflow-x: auto;
  scroll-snap-type: x mandatory;
  padding: 0;
  height: 100%;
  scrollbar-width: none;
}
.wdp-preview-uniform .wdp-draft-gallery-track::-webkit-scrollbar { display: none; }
.wdp-draft-html-card {
  flex: 0 0 100%;
  width: 100%;
  height: 100%;
  overflow: hidden;
  scroll-snap-align: start;
  background: #fff;
  position: relative;
}
.wdp-draft-html-card iframe {
  width: 1080px;
  height: 1440px;
  transform: scale(var(--wdp-card-scale, 0.26));
  transform-origin: top left;
  pointer-events: none;
  border: none;
}
.wdp-page-num {
  position: absolute;
  bottom: 6px;
  right: 8px;
  font-size: 10px;
  color: #666;
  background: rgba(255, 255, 255, 0.88);
  padding: 2px 7px;
  border-radius: 8px;
}
.wdp-draft-gallery-card {
  flex: 0 0 100%;
  width: 100%;
  height: 100%;
  overflow: hidden;
  scroll-snap-align: start;
  background: #fff;
}
.wdp-draft-gallery-card img {
  width: 100%;
  height: 100%;
  object-fit: contain;
  display: block;
}
.wdp-preview-uniform.wdp-draft-gallery-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  color: var(--rl-text-3, #9A9A9A);
  font-size: 12px;
}
.wdp-preview-uniform.wdp-draft-html-preview {
  background: #fff;
}
.wdp-draft-iframe {
  position: absolute;
  top: 0;
  left: 0;
  width: 1080px;
  height: 1440px;
  /* scale/x/y 由 setPreviewBoxRef 按容器实际宽高实时计算，完整居中显示 */
  transform: translate(var(--preview-x, 0px), var(--preview-y, 0px)) scale(var(--preview-scale, 0.1));
  transform-origin: top left;
  border: none;
  pointer-events: none;
}
.wdp-preview-uniform .wdp-draft-gallery-indicator {
  position: absolute;
  bottom: 10px;
  right: 12px;
  font-size: 10.5px;
  color: #fff;
  background: rgba(0, 0, 0, 0.5);
  padding: 2px 8px;
  border-radius: 12px;
  z-index: 2;
}

.wdp-original-link {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 11.5px;
  color: #3b82f6;
  text-decoration: none;
  word-break: break-all;
  padding: 3px 0;
}
.wdp-original-link:hover {
  color: #2563eb;
  text-decoration: underline;
}

/* ===== 同步到卡片编辑器弹窗（Teleport 到 body）===== */
.wdp-sync-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.32);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}
.wdp-sync-dialog {
  width: 320px;
  max-height: 72vh;
  display: flex;
  flex-direction: column;
  background: #fff;
  border-radius: 14px;
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.18);
  overflow: hidden;
}
.wdp-sync-head {
  flex: none;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 14px 10px;
  border-bottom: 1px solid rgba(0, 0, 0, 0.06);
}
.wdp-sync-title { font-size: 13px; font-weight: 600; color: #1a1a1a; }
.wdp-sync-close {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border: none;
  background: none;
  border-radius: 8px;
  color: #888;
  cursor: pointer;
}
.wdp-sync-close:hover { background: rgba(0, 0, 0, 0.05); color: #333; }
.wdp-sync-body { flex: 1; overflow-y: auto; padding: 12px 14px; }
.wdp-sync-label {
  font-size: 10px;
  font-weight: 600;
  color: #999;
  text-transform: uppercase;
  letter-spacing: 0.4px;
  margin-bottom: 6px;
}
.wdp-sync-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-height: 200px;
  overflow-y: auto;
  margin-bottom: 10px;
}
.wdp-sync-item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 8px 10px;
  border: 1px solid rgba(0, 0, 0, 0.06);
  border-radius: 10px;
  background: #fff;
  cursor: pointer;
  text-align: left;
  transition: border-color 0.15s, background 0.15s;
}
.wdp-sync-item:hover { background: #f7f7f8; }
.wdp-sync-item-active { border-color: #2563eb; background: rgba(37, 99, 235, 0.05); }
.wdp-sync-item-title { font-size: 12.5px; color: #1a1a1a; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.wdp-sync-item-meta { font-size: 10.5px; color: #999; margin-left: auto; flex-shrink: 0; }
.wdp-sync-loading { font-size: 11.5px; color: #aaa; padding: 8px 2px; }
.wdp-sync-modes { display: flex; gap: 8px; margin-bottom: 8px; }
.wdp-sync-mode {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: 9px 10px;
  border: 1px solid rgba(0, 0, 0, 0.06);
  border-radius: 10px;
  background: #fff;
  cursor: pointer;
  text-align: left;
  transition: border-color 0.15s, background 0.15s;
}
.wdp-sync-mode:hover { background: #f7f7f8; }
.wdp-sync-mode-active { border-color: #2563eb; background: rgba(37, 99, 235, 0.05); }
.wdp-sync-mode-title { font-size: 12.5px; font-weight: 600; color: #1a1a1a; }
.wdp-sync-mode-desc { font-size: 10.5px; color: #888; line-height: 1.4; }
.wdp-sync-error { font-size: 11.5px; color: #dc2626; padding: 6px 2px 0; }
.wdp-sync-foot {
  flex: none;
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding: 10px 14px 12px;
  border-top: 1px solid rgba(0, 0, 0, 0.06);
}
.wdp-sync-btn {
  padding: 6px 14px;
  border-radius: 10px;
  border: 1px solid #EDEDED;
  background: #fff;
  color: #555;
  font-size: 12px;
  cursor: pointer;
  transition: background 0.15s;
}
.wdp-sync-btn:hover { background: #f5f5f5; }
.wdp-sync-btn-primary {
  background: #2563eb;
  border-color: #2563eb;
  color: #fff;
}
.wdp-sync-btn-primary:hover { background: #1d4ed8; }
.wdp-sync-btn:disabled { opacity: 0.5; cursor: not-allowed; }
</style>