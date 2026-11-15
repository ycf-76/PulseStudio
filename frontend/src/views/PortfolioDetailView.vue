<template>
<AppShell
  current-page="portfolio"
  shell-class="mint-right-collapsed"
  resizer-variant="overlay"
  @nav-click="handleNavClick"
>
    <main class="da-page">
      <div class="da-content-card-wrapper">
      <div class="da-content-card">

        <div class="da-topbar">
          <button class="da-btn da-btn-ghost" @click="goBack">
            <Icon icon="ph:caret-left" width="14" />
            返回我的空间
          </button>
          <div class="da-topbar-right">
            <span v-if="mediaDownloadError" class="da-dl-error">{{ mediaDownloadError }}</span>
            <span v-if="workDetail?.local_video_url" class="da-dl-badge">
              <Icon icon="ph:check-circle" width="13" /> 本地已缓存
            </span>
            <button
              v-else-if="canDownloadMedia"
              class="da-btn"
              :disabled="mediaDownloadState === 'downloading'"
              :title="mediaDownloadError || '用 yt-dlp 下载视频到本地，解决远程链接无法播放的问题'"
              @click="onDownloadMedia"
            >
              <Icon icon="ph:download-simple" width="14" :class="{ 'pf-spin': mediaDownloadState === 'downloading' }" />
              <span>{{ mediaDownloadState === 'downloading' ? '下载中...' : '下载视频' }}</span>
            </button>
            <button
              v-if="canCopyText"
              class="da-btn"
              :title="textCopied ? '已复制到剪贴板' : '复制正文到剪贴板'"
              @click="onCopyText"
            >
              <Icon :icon="textCopied ? 'ph:check-simple' : 'ph:copy-simple'" width="14" />
              <span>{{ textCopied ? '已复制' : '复制文本' }}</span>
            </button>
            <button
              v-if="hasEditableDeck && !editing"
              class="da-btn da-btn-primary"
              @click="editing = true"
            >
              <Icon icon="ph:pencil-simple" width="14" />
              编辑卡片
            </button>
            <button
              v-else-if="canContentEdit"
              class="da-btn"
              :class="{ 'da-btn-primary': !contentEditing }"
              @click="contentEditing = !contentEditing"
            >
              <Icon :icon="contentEditing ? 'ph:eye' : 'ph:pencil-simple'" width="14" />
              {{ contentEditing ? '查看详情' : '编辑内容' }}
            </button>
            <span
              v-if="artifact && !hasEditableDeck && !canContentEdit"
              class="da-noedit-hint"
              title="该产物由旧链路生成，只落盘了最终图片/视频，没有可编辑的 CardDeck 结构"
            >无结构化卡片</span>
            <button
              v-if="source === 'draft' || source === 'published'"
              class="da-btn"
              title="回到对话继续修改这篇笔记"
              @click="onEdit"
            >
              <Icon icon="ph:note-pencil" width="14" />
              编辑
            </button>
            <button
              v-else-if="source === 'collected'"
              class="da-btn"
              title="把这篇采集作品带进对话做拆解分析（原作品只读，不会被修改）"
              @click="onAnalyze"
            >
              <Icon icon="ph:chat-circle-dots" width="14" />
              分析
            </button>
            <button
              v-if="source !== 'generated'"
              class="da-btn da-btn-danger"
              :disabled="deleting"
              @click="onDelete"
            >
              <Icon icon="ph:trash" width="14" />
              {{ deleting ? '删除中...' : '删除' }}
            </button>
            <span class="da-work-badge" :class="sourceBadgeClass">{{ sourceLabel }}</span>
            <span class="da-topbar-time">{{ formatFullDate(artifact?.updated_at || 0) }}</span>
          </div>
        </div>

        <div v-if="editing && artifact" class="da-editor-wrap">
          <CardDeckEditor :artifact-id="artifactId" @close="onEditorClose" />
        </div>

        <div
          v-else
          class="da-container"
          :class="{ 'da-container--split': previewablePages.length || canContentEdit }"
        >
          <div v-if="pageLoading" class="da-loading">
            <Icon icon="ph:spinner-gap" width="20" class="pf-spin" />
            <span>加载中...</span>
          </div>

          <div v-else-if="!artifact" class="da-empty-state">
            <Icon icon="ph:warning-circle-duotone" width="40" style="color: #D4D4D8" />
            <p class="da-empty-title">作品不存在</p>
            <button class="da-btn" @click="goBack" style="margin-top: 12px;">返回我的空间</button>
          </div>

          <template v-else>
            <div v-if="contentEditing && canContentEdit" class="dt-main dt-main--editor">
              <WorkContentEditor
                v-model="contentDraft"
                :saving="contentSaving"
                :dirty="contentDirty"
                :page-index="previewIndex"
                :refreshable="canRefreshImages"
                :refreshing="contentRefreshing"
                @save="onSaveContent"
                @reset="resetContentDraft"
                @select-page="previewIndex = $event"
                @refresh-images="onRefreshImages"
              />
              <p v-if="contentNotice" class="dt-notice" :class="{ 'dt-notice--error': contentNoticeError }">
                {{ contentNotice }}
              </p>
            </div>

            <div v-else class="dt-main">

            <div class="dt-header">
              <h1 class="dt-title">
                {{ artifact.brief?.title || '未命名作品' }}
                <span v-if="mediaInfo" class="dt-kind-badge">{{ mediaInfo.label }}</span>
              </h1>
              <p class="dt-topic" v-if="artifact.brief?.topic">{{ artifact.brief.topic }}</p>
            </div>

            <div v-if="heroKind" class="dt-block dt-block--media">
              <div class="dt-block-head">
                <span class="dt-block-title">作品图</span>
                <span class="dt-block-hint">{{ mediaKindLabel }}</span>
              </div>
              <div class="dt-hero" :class="`dt-hero--${heroKind}`">
              <template v-if="heroKind === 'image'">
                <img
                  v-if="mediaInfo?.coverUrl"
                  :src="mediaInfo.coverUrl"
                  class="dt-hero-image"
                  :alt="artifact.brief?.title || ''"
                />
                <div v-else class="dt-hero-placeholder">
                  <Icon icon="ph:images-duotone" width="28" />
                  <span>封面暂不可用，页图见下方</span>
                </div>
              </template>
              <template v-if="heroKind === 'video'">
                <video
                  v-if="mediaInfo?.videoUrl"
                  class="dt-hero-video"
                  :src="mediaInfo.videoUrl"
                  :poster="mediaInfo.coverUrl || undefined"
                  controls
                  playsinline
                />
                <div v-else class="dt-hero-placeholder">
                  <Icon icon="ph:film-strip-duotone" width="28" />
                  <span>视频文件暂不可用</span>
                </div>
              </template>
              <template v-else-if="heroKind === 'audio'">
                <div class="dt-hero-audio-card">
                  <div class="dt-hero-audio-cover">
                    <img v-if="mediaInfo?.coverUrl" :src="mediaInfo.coverUrl" alt="" />
                    <Icon v-else icon="ph:waveform" width="26" />
                  </div>
                  <div class="dt-hero-audio-main">
                    <div class="dt-hero-audio-label">音频作品</div>
                    <audio v-if="mediaInfo?.audioUrl" class="dt-hero-audio" :src="mediaInfo.audioUrl" controls />
                    <div v-else class="dt-hero-placeholder-inline">音频文件暂不可用</div>
                  </div>
                </div>
              </template>
              <template v-else-if="heroKind === 'svg'">
                <div class="dt-hero-svg-wrap">
                  <img
                    v-if="mediaInfo?.svgUrl"
                    :src="mediaInfo.svgUrl"
                    class="dt-hero-svg"
                    alt=""
                  />
                  <div v-else class="dt-hero-placeholder">
                    <Icon icon="ph:pen-nib-duotone" width="28" />
                    <span>矢量图暂不可用</span>
                  </div>
                </div>
              </template>
              <template v-else-if="heroKind === 'html'">
                <div :ref="setHeroHtmlRef" class="dt-hero-html-wrap">
                  <div v-if="mediaInfo?.previewHtml" class="dt-page-scaler">
                    <iframe
                      :srcdoc="mediaInfo.previewHtml"
                      class="dt-page-iframe"
                      sandbox="allow-same-origin allow-scripts"
                      scrolling="no"
                    />
                  </div>
                  <div v-else class="dt-hero-placeholder">
                    <Icon icon="ph:browser-duotone" width="28" />
                    <span>网页预览暂不可用</span>
                  </div>
                </div>
              </template>
              <template v-else>
                <div class="dt-hero-text">{{ workDetail?.content_text || mediaInfo?.snippet || '暂无正文内容' }}</div>
              </template>
              </div>
            </div>

            <div class="dt-pages" v-if="visiblePages.length">
              <div class="dt-pages-label">作品图 · 共 {{ visiblePages.length }} 张</div>
              <div class="dt-pages-grid">
                <div v-for="(page, idx) in visiblePages" :key="page.page_id || idx" class="dt-page-wrap">
                  <div class="dt-page" :ref="(el) => setPageRef(el, idx)">
                    <img
                      v-if="page.image_url && !page.__failed"
                      :src="page.image_url"
                      :class="{ 'dt-page-img--contain': page.component?.page_type === 'svg' }"
                      :data-original="page.image_url"
                      :data-fallback="workDetail?.cover_img_url || ''"
                      :alt="'第' + (idx + 1) + '页'"
                      loading="lazy"
                      @error="onPageImageError($event, page, idx === 0)"
                    />
                    <div v-else-if="getPageHtml(page)" class="dt-page-scaler">
                      <iframe
                        :srcdoc="getPageHtml(page)"
                        class="dt-page-iframe"
                        sandbox="allow-same-origin allow-scripts"
                        scrolling="no"
                      />
                    </div>
                    <div v-else class="dt-page-fallback">
                      <span>{{ idx + 1 }}</span>
                      <span v-if="page.__failed" class="dt-page-fallback-note">图片已失效</span>
                    </div>
                  </div>
                  <div class="dt-page-info">
                    <span class="dt-page-role">{{ page.role_label || `第${idx+1}页` }}</span>
                  </div>
                </div>
              </div>
            </div>

            <div class="dt-block dt-block--body" v-if="heroKind !== 'text' && displayBody">
              <div class="dt-block-head">
                <span class="dt-block-title">正文</span>
                <span class="dt-block-hint">{{ displayBody.length }} 字</span>
              </div>
              <div class="dt-body-text">{{ displayBody }}</div>
            </div>

            <div class="dt-block dt-block--tags" v-if="displayTags.length">
              <div class="dt-block-head">
                <span class="dt-block-title">标签</span>
                <span class="dt-block-hint">{{ displayTags.length }} 个</span>
              </div>
              <div class="dt-body-tags">
                <span v-for="tag in displayTags" :key="tag" class="dt-body-tag">#{{ tag }}</span>
              </div>
            </div>

            <div class="dt-stats" v-if="mediaInfo?.hasMetrics">
              <div class="dt-stat"><span class="dt-stat-val">{{ formatCount(workDetail?.collected_likes) }}</span><span class="dt-stat-key">点赞</span></div>
              <div class="dt-stat"><span class="dt-stat-val">{{ formatCount(workDetail?.collected_collects) }}</span><span class="dt-stat-key">收藏</span></div>
              <div class="dt-stat"><span class="dt-stat-val">{{ formatCount(workDetail?.collected_comments) }}</span><span class="dt-stat-key">评论</span></div>
              <div class="dt-stat"><span class="dt-stat-val">{{ formatCount(workDetail?.collected_shares) }}</span><span class="dt-stat-key">分享</span></div>
            </div>

            <div class="dt-meta" v-if="hasMeta">
              <div class="dt-meta-item" v-if="artifact.brief.goal">
                <span class="dt-meta-key">目标</span>
                <span class="dt-meta-val">{{ artifact.brief.goal }}</span>
              </div>
              <div class="dt-meta-item" v-if="artifact.brief.audience">
                <span class="dt-meta-key">受众</span>
                <span class="dt-meta-val">{{ artifact.brief.audience }}</span>
              </div>
              <div class="dt-meta-item" v-if="artifact.brief.tone">
                <span class="dt-meta-key">语气</span>
                <span class="dt-meta-val">{{ artifact.brief.tone }}</span>
              </div>
              <div class="dt-meta-item" v-if="artifact.brief.visual_direction">
                <span class="dt-meta-key">视觉方向</span>
                <span class="dt-meta-val">{{ artifact.brief.visual_direction }}</span>
              </div>
              <div class="dt-meta-item" v-if="artifact.brief.keep_patterns?.length">
                <span class="dt-meta-key">保留模式</span>
                <div class="dt-meta-tags">
                  <span v-for="t in artifact.brief.keep_patterns" :key="t" class="dt-tag dt-tag-keep">{{ t }}</span>
                </div>
              </div>
              <div class="dt-meta-item" v-if="artifact.brief.avoid_patterns?.length">
                <span class="dt-meta-key">避免模式</span>
                <div class="dt-meta-tags">
                  <span v-for="t in artifact.brief.avoid_patterns" :key="t" class="dt-tag dt-tag-avoid">{{ t }}</span>
                </div>
              </div>
            </div>

            <div class="dt-actions" v-if="source === 'draft' || source === 'artifact'">
              <button class="da-btn da-btn-primary" @click="onPublish" :disabled="publishing">
                <Icon icon="ph:paper-plane-tilt" width="14" />
                {{ publishing ? '发布中...' : '发布' }}
              </button>
            </div>

            </div>

            <aside v-if="canContentEdit || previewablePages.length" class="dt-preview">
              <template v-if="canContentEdit">
                <NotePhonePreview
                  :title="contentDraft.title"
                  :content-text="contentDraft.contentText"
                  :tags="displayTags"
                  :images="contentDraft.images"
                  :page-index="previewIndex"
                  :video-url="previewVideoUrl"
                />
                <div class="dt-preview-nav" v-if="contentDraft.images.length > 1">
                  <button class="da-btn da-btn-sm" :disabled="previewIndex <= 0" @click="previewIndex--">
                    <Icon icon="ph:caret-left" width="12" />
                    上一张
                  </button>
                  <span class="dt-preview-count">{{ previewIndex + 1 }} / {{ contentDraft.images.length }}</span>
                  <button
                    class="da-btn da-btn-sm"
                    :disabled="previewIndex >= contentDraft.images.length - 1"
                    @click="previewIndex++"
                  >
                    下一张
                    <Icon icon="ph:caret-right" width="12" />
                  </button>
                </div>
              </template>

              <template v-else>
                <CardPreviewPane
                  :html="previewPageHtml"
                  :image-url="previewImageUrl"
                  :empty-text="previewEmptyText"
                  :title="artifact.brief?.title || '未命名作品'"
                  :page-label="currentPreviewPage?.role_label || `第 ${previewIndex + 1} 页`"
                  :page-index="previewIndex"
                  :page-count="previewablePages.length"
                />
                <div class="dt-preview-nav" v-if="previewablePages.length > 1">
                  <button class="da-btn da-btn-sm" :disabled="previewIndex <= 0" @click="previewIndex--">
                    <Icon icon="ph:caret-left" width="12" />
                    上一页
                  </button>
                  <span class="dt-preview-count">{{ previewIndex + 1 }} / {{ previewablePages.length }}</span>
                  <button
                    class="da-btn da-btn-sm"
                    :disabled="previewIndex >= previewablePages.length - 1"
                    @click="previewIndex++"
                  >
                    下一页
                    <Icon icon="ph:caret-right" width="12" />
                  </button>
                </div>
              </template>
            </aside>

          </template>

        </div>

      </div>
      </div>
    </main>
</AppShell>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { Icon } from '@iconify/vue'
import AppShell from '@/components/layout/AppShell.vue'
import CardDeckEditor from '@/components/card-editor/CardDeckEditor.vue'
import CardPreviewPane from '@/components/card-editor/CardPreviewPane.vue'
import WorkContentEditor from '@/components/portfolio/WorkContentEditor.vue'
import NotePhonePreview from '@/components/portfolio/NotePhonePreview.vue'
import {
  draftFromWork,
  draftPayload,
  isDraftDirty,
  type WorkContentDraft,
} from '@/components/portfolio/contentDraft'
import { useWorkStore } from '@/stores/work'
import { useUIState } from '@/composables/useUIState'
import { getCreativeArtifact, deleteCreativeArtifact } from '@/api/creativeArtifact'
import {
  getMyWorkDetail,
  deleteMyWork,
  downloadWorkMedia,
  updateWorkContent,
  refreshWorkImages,
  type MyWorkDetail,
  type MyWorkItem,
} from '@/api/my_works'
import { collectWorkImageUrls, formatCount, summarizeWorkMedia } from '@/components/portfolio/workMedia'
import { collectHashtags, stripHashtags } from '@/components/portfolio/noteTags'
import type { CreativeArtifact, StoryboardPage } from '@/types/creativeArtifact'

const router = useRouter()
const route = useRoute()
const workStore = useWorkStore()
const uiState = useUIState()

const artifactId = computed(() => route.params.id as string)
/**
 * 入口来源，决定这条 id 该走哪条链路。
 *
 * 首页「最近笔记」等入口只给了裸 id，没有 source，早期一律按 'artifact' 处理，
 * 结果采集/已发布作品去查创作产物接口必然 404，整页空白。
 * 现在允许在装载时按作品记录的真实 origin 纠正来源，所以这里用 ref 而不是 computed。
 */
const source = ref<string>((route.query.source as string) || 'artifact')

const pageLoading = ref(true)
const artifact = ref<CreativeArtifact | null>(null)
const workDetail = ref<MyWorkDetail | null>(null)

const mediaInfo = computed(() => (workDetail.value ? summarizeWorkMedia(workDetail.value) : null))

const heroKind = computed<'video' | 'audio' | 'text' | 'svg' | 'html' | 'image' | null>(() => {
  if (source.value === 'artifact' || !mediaInfo.value) return null
  const kind = mediaInfo.value.kind
  if (kind === 'video') return 'video'
  if (kind === 'audio') return 'audio'
  if (kind === 'text') return 'text'
  // 矢量图/网页此前无 hero 分支，这两类作品详情页头部直接空白（2026-11-04 补齐）
  if (kind === 'svg') return 'svg'
  if (kind === 'html') return 'html'
  // 图文/图集：有封面或页图时显示大图 hero——给图文类一个可见的"分类感"
  if (kind === 'image_text' && (mediaInfo.value.coverUrl || mediaInfo.value.pageCount)) return 'image'
  return null
})
const publishing = ref(false)
const deleting = ref(false)
const editing = ref(false)

/** 只有带 card_deck 的产物才进得去编辑态：旧链路产物只有最终图，没有块结构。 */
const hasEditableDeck = computed(() => {
  const deck = (artifact.value as unknown as Record<string, unknown> | null)?.card_deck
  return Boolean(deck && typeof deck === 'object')
})

// 详情页带 ?edit=1 时装载完成后直接进编辑态（右侧详情面板「同步到卡片编辑器」的落地入口）
watch(hasEditableDeck, (ok) => {
  if (ok && route.query.edit === '1') editing.value = true
}, { immediate: true })

// ===== 内容层编辑（方案B：页面级编辑器）=====
// 采集记录与 outputs 产物没有 CardDeck，进不去卡片编辑器；
// 这里直接给一套内容表单，覆盖全部作品，不丢任何图片。
const contentEditing = ref(false)
const contentSaving = ref(false)
const contentRefreshing = ref(false)
const contentNotice = ref('')
const contentNoticeError = ref(false)
const contentDraft = ref<WorkContentDraft>(draftFromWork(null))
const contentBaseline = ref<WorkContentDraft>(draftFromWork(null))

// 就地编辑收敛（2026-11-04 定位切分）：只给草稿/已发布的轻量微调。
// 采集作品是别人的原作，只读 + 分析；产物走 CardDeck 编辑器，不在此列。
const canContentEdit = computed(
  () =>
    (source.value === 'draft' || source.value === 'published') &&
    !!workDetail.value &&
    !hasEditableDeck.value
)
const contentDirty = computed(() => isDraftDirty(contentDraft.value, contentBaseline.value))

/**
 * 详情页展示用话题。
 *
 * 采集回来的正文里本身带着一串 `#话题[投票]#`，存盘 tags 又因为正则只吃到成对的 `#…#`
 * 而残缺成「标签|话题|」这类脏值。两路来源直接拼在一起，就会出现截断标签 + 正文重复标签。
 * 这里统一按「正文提取 → 存盘补全 → 去重 → 去残缺」一条链收敛，详情页与手机预览共用同一份结果。
 */
const displayTags = computed(() =>
  collectHashtags(
    workDetail.value?.content_text,
    Array.isArray(workDetail.value?.tags) ? workDetail.value?.tags : []
  )
)

/**
 * 详情页展示用正文。
 *
 * 采集回来的正文末尾自带一整串 `#话题# [投票]#`，不剥离就会和下方「标签」区
 * 重复展示同一批话题，视觉上正文永远没有结尾。这里先剥话题再展示。
 */
const displayBody = computed(() => stripHashtags(workDetail.value?.content_text))

/** 作品图区块的类型提示，让「图」和「文」在标题上就能区分开。 */
const mediaKindLabel = computed(() => {
  const info = mediaInfo.value
  if (!info) return ''
  if (info.kind === 'video') return '视频'
  if (info.kind === 'audio') return '音频'
  if (info.kind === 'svg') return '矢量图'
  if (info.kind === 'html') return '网页'
  return info.pageCount ? `${info.pageCount} 张` : '图片'
})

/** 只有 PCP 采集记录能重新抓图：合成 id（artifact: / output:）后端查不到原笔记。 */
const canRefreshImages = computed(
  () =>
    !artifactId.value.includes(':') &&
    !!workDetail.value?.published_note_id &&
    (workDetail.value?.platform || 'xiaohongshu') === 'xiaohongshu'
)

/** 视频产物在手机预览里放播放器：本地缓存优先，远程链接做兜底。 */
const previewVideoUrl = computed(() => {
  if (!workDetail.value || source.value === 'artifact') return ''
  if (heroKind.value !== 'video') return ''
  return workDetail.value.local_video_url || workDetail.value.video_url || ''
})

function resetContentDraft() {
  contentDraft.value = { ...contentBaseline.value }
}

function flashContentNotice(text: string, isError = false) {
  contentNotice.value = text
  contentNoticeError.value = isError
  window.setTimeout(() => {
    if (contentNotice.value === text) contentNotice.value = ''
  }, 2600)
}

async function onSaveContent() {
  if (!workDetail.value || contentSaving.value || !contentDirty.value) return
  contentSaving.value = true
  try {
    const updated = await updateWorkContent(workDetail.value.id, draftPayload(contentDraft.value))
    workDetail.value = updated
    const fresh = draftFromWork(updated)
    contentDraft.value = fresh
    contentBaseline.value = fresh
    artifact.value = buildArtifactFromMyWork(updated, artifactId.value)
    flashContentNotice('已保存')
  } catch (e: any) {
    flashContentNotice(e?.response?.data?.detail || e?.message || '保存失败', true)
  } finally {
    contentSaving.value = false
  }
}

/**
 * 重新抓取失效图片。
 * 后端会重新解析原笔记、把图片缓存到本地 uploads，再回写 PCP 记录，
 * 因此成功后必须重载详情，草稿与基线一起换成新链接，脏态自动归零。
 */
async function onRefreshImages() {
  if (!workDetail.value || contentRefreshing.value || !canRefreshImages.value) return
  contentRefreshing.value = true
  try {
    const result = await refreshWorkImages(workDetail.value.id)
    if (result.status === 'already_cached') {
      flashContentNotice('图片已在本地，无需重复抓取')
      return
    }
    if (result.status !== 'refreshed') {
      flashContentNotice(result.message || '重新抓取失败，原笔记可能已删除或需要重新采集', true)
      return
    }
    const detail = await getMyWorkDetail(workDetail.value.id)
    workDetail.value = detail
    const fresh = draftFromWork(detail)
    contentDraft.value = fresh
    contentBaseline.value = fresh
    artifact.value = buildArtifactFromMyWork(detail, artifactId.value)
    flashContentNotice(`图片已重新抓取并缓存到本地（${result.images ?? 0} 张）`)
  } catch (e: any) {
    flashContentNotice(e?.response?.data?.detail || e?.message || '重新抓取失败', true)
  } finally {
    contentRefreshing.value = false
  }
}

watch(
  workDetail,
  (detail) => {
    const fresh = draftFromWork(detail)
    contentDraft.value = fresh
    contentBaseline.value = fresh
  },
  { immediate: true }
)

watch(
  canContentEdit,
  (editable) => {
    contentEditing.value = editable
  },
  { immediate: true }
)

watch(
  () => contentDraft.value.images.length,
  (len) => {
    if (previewIndex.value >= len) previewIndex.value = Math.max(0, len - 1)
  }
)

async function onEditorClose() {
  editing.value = false
  if (source.value === 'artifact') {
    const fresh = await getCreativeArtifact(artifactId.value).catch(() => null)
    if (fresh) artifact.value = fresh
    return
  }
  // 作品记录链路：编辑后 storyboard 已被 _commit 重建，重新装载才能看到新图
  await loadArtifact()
}

// ===== 视频本地下载（yt-dlp）=====
const mediaDownloadState = ref<'idle' | 'downloading' | 'ready' | 'failed'>('idle')
const mediaDownloadError = ref('')
let mediaPollTimer: ReturnType<typeof setInterval> | null = null

const canDownloadMedia = computed(() =>
  source.value !== 'artifact' &&
  !!workDetail.value?.video_url &&
  !workDetail.value?.local_video_url
)

// ===== 复制正文（文本/长文/图文通用，2026-11-04 操作矩阵裁剪）=====
const textCopied = ref(false)
const canCopyText = computed(() => !!workDetail.value?.content_text?.trim())
async function onCopyText() {
  try {
    await navigator.clipboard.writeText(workDetail.value?.content_text || '')
    textCopied.value = true
    setTimeout(() => { textCopied.value = false }, 2000)
  } catch {
    // 剪贴板权限不可用时静默失败（HTTP 环境下 clipboard API 不可用）
  }
}

function stopMediaPolling() {
  if (mediaPollTimer) {
    clearInterval(mediaPollTimer)
    mediaPollTimer = null
  }
}

async function reloadWorkDetail() {
  const detail = await getMyWorkDetail(artifactId.value).catch(() => null)
  if (detail) {
    workDetail.value = detail
    artifact.value = buildArtifactFromMyWork(detail, artifactId.value)
  }
}

function startMediaPolling() {
  stopMediaPolling()
  const startedAt = Date.now()
  mediaPollTimer = setInterval(async () => {
    if (Date.now() - startedAt > 5 * 60 * 1000) {
      stopMediaPolling()
      mediaDownloadState.value = 'failed'
      mediaDownloadError.value = '下载超时，请稍后重试'
      return
    }
    const detail = await getMyWorkDetail(artifactId.value).catch(() => null)
    if (detail?.local_video_url) {
      stopMediaPolling()
      mediaDownloadState.value = 'ready'
      mediaDownloadError.value = ''
      workDetail.value = detail
      artifact.value = buildArtifactFromMyWork(detail, artifactId.value)
    }
  }, 2000)
}

async function onDownloadMedia() {
  if (!workDetail.value || mediaDownloadState.value === 'downloading') return
  mediaDownloadState.value = 'downloading'
  mediaDownloadError.value = ''
  try {
    const result = await downloadWorkMedia(workDetail.value.id)
    if (result.status === 'blocked') {
      mediaDownloadState.value = 'failed'
      mediaDownloadError.value = result.message
      return
    }
    if (result.status === 'ready') {
      mediaDownloadState.value = 'ready'
      await reloadWorkDetail()
      return
    }
    startMediaPolling()
  } catch (e: any) {
    mediaDownloadState.value = 'failed'
    mediaDownloadError.value = e?.response?.data?.message || e?.message || '下载启动失败'
  }
}

/**
 * 分页结构必须保持完整。
 *
 * 早期这里会把 __failed 的页面从列表里剔除，结果是一页图挂掉整组只剩首页
 * （首图有封面兜底不会失败，其余页面失败即被过滤），页码和翻页器一起消失。
 * 现在失效页只降级成占位卡片，页数与翻页器始终与真实页数一致。
 *
 * __failed 是前端运行时标记（两段图片兜底都失败后才置位），
 * 不属于后端返回的 StoryboardPage，这里用一个交叉类型单独声明，
 * 避免为了一个渲染态去污染共享的产物类型。
 */
type DetailPage = StoryboardPage & { __failed?: boolean }
const visiblePages = computed<DetailPage[]>(
  () => (artifact.value?.storyboard || []) as DetailPage[]
)

/**
 * 右栏手机预览的页码。
 *
 * 详情页对所有作品都给出手机预览：有 CardDeck 的走 iframe 渲染，
 * 只有落盘图片的走 img 承接，保证「手机壳」在每一种产物上都出现，
 * 而不是只在少数带结构化卡片的作品上出现。
 */
const previewIndex = ref(0)
const previewablePages = computed(() =>
  visiblePages.value.filter((page: any) => Boolean(page.image_url || getPageHtml(page)))
)
const currentPreviewPage = computed<any>(() => previewablePages.value[previewIndex.value] || null)
const previewPageHtml = computed(() => getPageHtml(currentPreviewPage.value))
/** 失效页不给手机预览喂坏链接，交给 CardPreviewPane 渲染占位文案 */
const previewImageUrl = computed(() => {
  const page = currentPreviewPage.value
  if (!page || page.__failed) return ''
  return page.image_url || ''
})
const previewEmptyText = computed(() =>
  currentPreviewPage.value?.__failed ? '图片已失效（链接过期）' : '本页暂无可预览内容'
)
watch(previewablePages, (pages) => {
  if (previewIndex.value >= pages.length) previewIndex.value = 0
})

const hasMeta = computed(() => {
  const brief = artifact.value?.brief
  if (!brief) return false
  return Boolean(
    brief.goal ||
    brief.audience ||
    brief.tone ||
    brief.visual_direction ||
    brief.keep_patterns?.length ||
    brief.avoid_patterns?.length
  )
})

function handleNavClick(pageName: string) {
  if (pageName === 'home') { router.push('/').catch(() => {}); return }
  if (pageName === 'portfolio') { router.push('/portfolio').catch(() => {}); return }
  if (pageName === 'topic-pool') { router.push('/topic-pool').catch(() => {}); return }
  if (pageName === 'my-works') { router.push('/my-works').catch(() => {}); return }
  if (pageName === 'task-plans') { router.push('/task-plans').catch(() => {}); return }
  router.push('/chat')
}

function goBack() { router.push('/portfolio').catch(() => {}) }

const sourceLabel = computed(() => {
  if (source.value === 'draft') return '草稿'
  if (source.value === 'artifact') return '创作'
  if (source.value === 'generated') return '平台生成'
  if (source.value === 'collected') return '采集'
  if (source.value === 'published') return '已发布'
  return '已发布'
})

const sourceBadgeClass = computed(() => {
  if (source.value === 'draft') return 'da-badge-draft'
  if (source.value === 'artifact') return 'da-badge-artifact'
  if (source.value === 'generated') return 'da-badge-artifact'
  if (source.value === 'collected') return 'da-badge-published'
  if (source.value === 'published') return 'da-badge-published'
  return ''
})

function getPageHtml(page: any): string {
  if (!page) return ''
  if (page.content?.htmlContent) return page.content.htmlContent
  if (page.htmlContent) return page.htmlContent
  return ''
}

function onPageImageError(event: Event, page: any, isPrimary: boolean) {
  const img = event.target as HTMLImageElement
  const original = img.dataset.original || ''
  const fallback = img.dataset.fallback || ''
  // 兜底阶段绑定到具体 URL：图片被重新抓取后同一元素换了链接，阶段必须归零重试
  if (img.dataset.stageFor !== original) {
    img.dataset.stageFor = original
    img.dataset.stage = '0'
  }
  const stage = img.dataset.stage || '0'
  if (stage === '0' && original) {
    img.dataset.stage = '1'
    img.src = '/api/proxy/image?url=' + encodeURIComponent(original)
    return
  }
  if (isPrimary && stage === '1' && fallback) {
    img.dataset.stage = '2'
    img.src = fallback
    return
  }
  // 两段兜底都失败：只把这一页降级成占位，页面本身留在分页结构里，
  // 页数与翻页器不受影响，用户可以继续翻到后面还有效的页面。
  page.__failed = true
}

const NATIVE_W = 1080
const NATIVE_H = 1440
const pageRefs = new Map<number, HTMLElement>()
let pageRO: ResizeObserver | null = null

function setPageRef(el: unknown, idx: number) {
  if (!el || !(el instanceof HTMLElement)) return
  pageRefs.set(idx, el)
  if (pageRO) pageRO.observe(el)
}

/** 网页卡片的 hero 预览也复用同一套 ResizeObserver 缩放（.dt-page-scaler 结构） */
function setHeroHtmlRef(el: unknown) {
  if (!el || !(el instanceof HTMLElement)) return
  if (pageRO) pageRO.observe(el)
}

function applyPageScale(entries: ResizeObserverEntry[]) {
  for (const entry of entries) {
    const scaler = entry.target.querySelector('.dt-page-scaler') as HTMLElement | null
    if (!scaler) continue
    const w = entry.contentBoxSize?.[0]?.inlineSize ?? entry.contentRect.width
    const scale = w / NATIVE_W
    scaler.style.transform = `scale(${scale})`
    scaler.style.transformOrigin = 'top left'
    scaler.style.width = `${NATIVE_W}px`
    scaler.style.height = `${NATIVE_H}px`
  }
}

function formatFullDate(ts: number): string {
  if (!ts) return '-'
  const d = new Date(ts * 1000)
  if (isNaN(d.getTime())) return '-'
  return d.toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

/** 把当前详情灌进 work store：chat 侧 buildWorkContext 只读 activeWork，
 *  详情页不 seed 的话路由过去 chat 收不到任何作品上下文。 */
function seedActiveWork(): string | null {
  const detail = workDetail.value
  if (!detail) {
    workStore.setActiveWork(artifactId.value)
    return artifactId.value
  }
  const seeded = workStore.seedWorkFromApi(detail)
  const workId = seeded?.id || artifactId.value
  workStore.setActiveWork(workId)
  return workId
}

/** 草稿 / 已发布：进对话继续改（Agent 可直接 work_edit 草稿） */
function onEdit() {
  if (source.value === 'draft' || source.value === 'published') {
    seedActiveWork()
    router.push('/chat').catch(() => {})
  }
}

/** 采集作品：进对话只做分析（原作品只读，后端 require_status 会拒绝写入） */
function onAnalyze() {
  if (source.value !== 'collected') return
  seedActiveWork()
  router.push('/chat').catch(() => {})
}

async function onPublish() {
  publishing.value = true
  try {
    if (source.value === 'draft') {
      const work = workStore.works.find(w => w.id === artifactId.value)
      if (work) {
        workStore.updateDraft(artifactId.value, { contentStatus: 'published' } as any)
      }
    }
    router.push('/portfolio').catch(() => {})
  } finally {
    publishing.value = false
  }
}

async function onDelete() {
  deleting.value = true
  try {
    if (source.value === 'artifact') {
      await deleteCreativeArtifact(artifactId.value)
      await workStore.fetchOutputWorks()
    } else {
      const inStore = workStore.works.some(w => w.id === artifactId.value)
        || workStore.outputWorks.some(w => w.id === artifactId.value)
      if (inStore) await workStore.deleteWork(artifactId.value)
      else await deleteMyWork(artifactId.value)
    }
    router.push('/portfolio').catch(() => {})
  } finally {
    deleting.value = false
  }
}

function buildArtifactFromMyWork(detail: MyWorkDetail, recordId: string): CreativeArtifact {
  const pages: StoryboardPage[] = (detail.card_draft?.pages || []).map((page, idx) => ({
    page_id: `page-${idx}`,
    index: idx,
    role: '',
    role_label: page.type ? `${page.type} · 第${idx + 1}页` : `第${idx + 1}页`,
    rationale: '',
    source_evidence: '',
    visual_goal: '',
    component: { page_type: page.type || '' },
    image_role: '',
    content: page.content || {},
    image_url: page.imageUrl || page.image_url || (page.pngUrls || [])[0] || '',
    html_url: page.htmlUrl || '',
  }))

  if (pages.length === 0) {
    for (const [idx, url] of collectWorkImageUrls(detail as unknown as MyWorkItem).entries()) {
      pages.push({
        page_id: `page-${idx}`,
        index: idx,
        role: '',
        role_label: `第${idx + 1}页`,
        rationale: '',
        source_evidence: '',
        visual_goal: '',
        component: { page_type: 'image' },
        image_role: '',
        content: {},
        image_url: url,
        html_url: '',
      })
    }
  }

  if (pages.length === 0 && detail.first_page_html) {
    pages.push({
      page_id: 'page-0',
      index: 0,
      role: '',
      role_label: '第1页',
      rationale: '',
      source_evidence: '',
      visual_goal: '',
      component: { page_type: 'html' },
      image_role: '',
      content: { htmlContent: detail.first_page_html },
      image_url: '',
      html_url: '',
    })
  }

  const svgUrls = (detail as unknown as Record<string, unknown>).svg_urls
  const svgUrl = (detail as unknown as Record<string, unknown>).svg_url
  const svgList = Array.isArray(svgUrls) && svgUrls.length
    ? svgUrls.filter((u): u is string => typeof u === 'string' && !!u)
    : (typeof svgUrl === 'string' && svgUrl ? [svgUrl] : [])
  if (svgList.length) {
    pages.unshift(...svgList.map((url, idx) => ({
      page_id: `page-svg-${idx}`,
      index: 0,
      role: '',
      role_label: svgList.length > 1 ? `矢量图 · 第${idx + 1}张` : '矢量图',
      rationale: '',
      source_evidence: '',
      visual_goal: '',
      component: { page_type: 'svg' },
      image_role: '',
      content: {},
      image_url: url,
      html_url: '',
    })))
  }

  const created = detail.published_at ? new Date(detail.published_at).getTime() / 1000 : 0
  return {
    artifact_id: recordId,
    version: 1,
    created_at: created,
    updated_at: created,
    // 我的空间走的是「作品记录」链路，card_deck 藏在 card_draft.deck 里，
    // 不透传出来详情页就永远进不去编辑态。
    card_deck: detail.card_draft?.deck || null,
    source: {},
    analysis: {},
    brief: {
      goal: '',
      audience: '',
      tone: '',
      topic: detail.topic || '',
      title: detail.title || '',
      visual_direction: '',
      template_id: '',
      keep_patterns: [],
      avoid_patterns: [],
    },
    storyboard: pages,
    review: { score: 0, issues: [], suggestions: [] },
    history: [],
  } as unknown as CreativeArtifact
}

async function loadArtifact() {
  pageLoading.value = true
  try {
    if (source.value === 'artifact') {
      const fresh = await getCreativeArtifact(artifactId.value).catch(() => null)
      if (fresh) {
        artifact.value = fresh
      } else {
        // 裸 id 的入口（首页「最近笔记」、直接输 URL、旧书签）不一定真是创作产物。
        // 创作产物查不到时退回作品记录链路，并按真实 origin 纠正来源，
        // 否则 hero/封面、内容编辑、重新抓图等按来源分支的功能会一并失效。
        const detail = await getMyWorkDetail(artifactId.value).catch(() => null)
        if (detail) {
          workDetail.value = detail
          source.value = detail.origin === 'collected' ? 'collected' : 'published'
          artifact.value = buildArtifactFromMyWork(detail, artifactId.value)
        } else {
          artifact.value = null
        }
      }
    } else {
      const work = workStore.works.find(w => w.id === artifactId.value)
      if (work?.cardDraft?.pages?.length) {
        artifact.value = {
          artifact_id: artifactId.value,
          version: 1,
          created_at: 0,
          updated_at: new Date(work.dataCollectedAt).getTime() / 1000,
          card_deck: (work.cardDraft as any)?.deck || null,
          source: {},
          analysis: {},
          brief: { goal: '', audience: '', tone: '', topic: work.description || '', title: work.title || '', visual_direction: '', template_id: '', keep_patterns: [], avoid_patterns: [] },
          storyboard: work.cardDraft.pages.map((p: any, idx: number) => ({
            page_id: p.id || `page-${idx}`,
            index: idx,
            role: p.role || '',
            role_label: p.role_label || `第${idx+1}页`,
            rationale: '',
            source_evidence: '',
            visual_goal: '',
            component: { page_type: p.type || '' },
            image_role: '',
            content: p.content || {},
            image_url: p.imageUrl || '',
            html_url: p.htmlUrl || '',
          })),
          review: { score: 0, issues: [], suggestions: [] },
          history: [],
        } as any
      } else {
        const detail = await getMyWorkDetail(artifactId.value).catch(() => null)
        workDetail.value = detail
        if (detail) {
          artifact.value = buildArtifactFromMyWork(detail, artifactId.value)
        } else {
          // 作品记录查不到（比如裸 ca_ id 被当成 PCP 记录），退回创作产物本体
          artifact.value = await getCreativeArtifact(artifactId.value).catch(() => null)
        }
      }
    }
  } catch {
    artifact.value = null
  } finally {
    pageLoading.value = false
  }
}

onMounted(() => {
  pageRO = new ResizeObserver(applyPageScale)
  for (const el of pageRefs.values()) pageRO.observe(el)
  loadArtifact()
})

onBeforeUnmount(() => {
  pageRO?.disconnect()
  pageRO = null
  stopMediaPolling()
})
</script>

<style scoped>
.da-page { flex: 1; min-width: 0; height: 100%; display: flex; flex-direction: column; overflow: hidden; padding: 28px 20px 16px; }
.da-content-card-wrapper { display: flex; flex-direction: column; flex: 1; min-height: 0; min-width: 0; overflow: hidden; }
.da-content-card { flex: 1; min-height: 0; min-width: 0; background: #FFFFFF; border-radius:20px; border: 1px solid var(--ma-border-default); box-shadow: 0 1px 3px rgba(0,0,0,0.04); display: flex; flex-direction: column; overflow: hidden; }
.da-container { flex: 1; overflow-y: auto; padding: 20px 24px; }

/* 有可预览页面时切成两栏：左内容、右手机预览，对齐小红书发布页的形态 */
.da-container--split {
  display: flex;
  gap: 20px;
  overflow: hidden;
  min-height: 0;
}

.dt-main { min-width: 0; }

/* 编辑态左栏：表单自带滚动，这里只负责给底部提示留位置 */
.dt-main--editor {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding-right: 6px;
  display: flex;
  flex-direction: column;
}

.dt-notice {
  margin: 10px 0 0;
  padding: 7px 12px;
  border-radius: 8px;
  font-size: 12px;
  background: #ecfdf5;
  color: #047857;
  border: 1px solid #a7f3d0;
  align-self: flex-start;
}

.dt-notice--error {
  background: #FFFBEB;
  color: #B45309;
  border-color: #FDE68A;
}

.da-container--split .dt-main {
  flex: 1;
  overflow-y: auto;
  padding-right: 6px;
}

.dt-preview {
  width: 340px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
  align-items: center;
  min-height: 0;
  padding: 4px 0;
}

.dt-preview-nav { display: flex; align-items: center; gap: 10px; flex-shrink: 0; }
.dt-preview-count { font-size: 11px; color: #9a9a9a; font-variant-numeric: tabular-nums; }

.da-btn-sm { padding: 5px 10px; font-size: 11px; border-radius: 8px; }

.da-editor-wrap { flex: 1; min-height: 0; overflow: hidden; padding: 18px 24px 20px; display: flex; flex-direction: column; }

/* 顶部栏与其它页面统一：直接坐在白色卡片上，不铺页面底色、不加分隔线，
   避免卡片顶部出现一条与卡片本体不同色的灰带 */
.da-topbar { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 16px 24px 12px; flex-shrink: 0; background: transparent; }
.da-topbar-right { display: flex; align-items: center; gap: 10px; }
.da-dl-error { font-size: 12px; color: #B45309; max-width: 260px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.da-noedit-hint { font-size: 12px; color: #A1A1AA; padding: 4px 10px; border-radius: 8px; background: #F4F4F5; border: 1px dashed #E4E4E7; white-space: nowrap; cursor: help; }
.da-dl-badge { display: inline-flex; align-items: center; gap: 4px; font-size: 12px; color: #30A46C; background: rgba(48, 164, 108, 0.1); border-radius: 6px; padding: 3px 8px; }
.da-topbar-time { font-size: 12px; color: #9A9A9A; }

.da-btn { display: inline-flex; align-items: center; gap: 6px; padding: 7px 14px; border-radius:12px; cursor: pointer; font-size: 13px; border: 1px solid #EDEDED; background: #fff; color: var(--rl-text-2); transition: all 0.15s; white-space: nowrap; }
.da-btn:hover { border-color: #D4D4D8; background: #F5F5F5; }
.da-btn-ghost { background: transparent; border-color: #EDEDED; }
.da-btn-ghost:hover { background: #F5F5F5; }
.da-btn-primary { background: #2563EB; color: #fff; border-color: #2563EB; }
.da-btn-primary:hover { background: #1D4ED8; border-color: #1D4ED8; }
.da-btn-danger { background: transparent; border-color: #D4D4D8; color: #71717A; }
.da-btn-danger:hover { background: #F4F4F5; border-color: #A1A1AA; color: #3F3F46; }
.da-btn:disabled { opacity: 0.4; cursor: not-allowed; }

.da-work-badge { display: inline-block; font-size: 10px; padding: 1px 6px; border-radius:8px; font-weight: 500; }
.da-badge-draft { background: #FEF3C7; color: #D97706; }
.da-badge-artifact { background: #EDE9FE; color: #7C3AED; }
.da-badge-published { background: #D1FAE5; color: var(--rl-green); }

.da-loading { display: flex; align-items: center; justify-content: center; gap: 8px; padding: 60px 0; color: #9A9A9A; font-size: 13px; }
.da-empty-state { text-align: center; padding: 80px 0; display: flex; flex-direction: column; align-items: center; gap: 8px; }
.da-empty-title { font-size: 14px; font-weight: 500; color: var(--rl-text-1); margin: 0; }

.pf-spin { animation: pf-spin-anim 1s linear infinite; }
@keyframes pf-spin-anim { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }

.dt-header { margin-bottom: 24px; }
.dt-title { font-size: 18px; font-weight: 500; color: var(--rl-text-1); margin: 0 0 6px; display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.dt-kind-badge { display: inline-flex; align-items: center; padding: 2px 8px; border-radius: 999px; background: #F0F0F2; color: #6B6B74; font-size: 11px; font-weight: 500; vertical-align: middle; }
.dt-topic { font-size: 14px; color: #4B4B4B; margin: 0; }

.dt-hero { margin-bottom: 24px; display: flex; justify-content: center; }
.dt-hero--image { border-radius: 12px; overflow: hidden; background: #FFFFFF; border: 1px solid #EDEDED; display: flex; justify-content: center; width: 100%; }
.dt-hero-image { max-width: 100%; max-height: 52vh; object-fit: contain; display: block; }
.dt-hero--video { border-radius: 12px; overflow: hidden; background: #000; border: 1px solid #EDEDED; width: fit-content; max-width: 100%; margin: 0 auto; }
.dt-hero-video { display: block; margin: 0 auto; width: auto; height: auto; max-width: 100%; max-height: 52vh; background: #000; object-fit: contain; }
.dt-hero-placeholder { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 8px; padding: 48px 0; color: #9A9A9A; font-size: 13px; background: #F7F7F8; }
.dt-hero-placeholder-inline { font-size: 12px; color: #9A9A9A; }
.dt-hero--audio { padding: 16px; background: #F7F7F8; border: 1px solid #EDEDED; border-radius: 16px; }
.dt-hero-audio-card { display: flex; align-items: center; gap: 14px; }
.dt-hero-audio-cover { width: 64px; height: 64px; border-radius: 12px; overflow: hidden; flex-shrink: 0; background: #fff; display: flex; align-items: center; justify-content: center; color: #C4C9D4; }
.dt-hero-audio-cover img { width: 100%; height: 100%; object-fit: cover; }
.dt-hero-audio-main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 8px; }
.dt-hero-audio-label { font-size: 12px; color: #9A9A9A; font-weight: 500; }
.dt-hero-audio { width: 100%; height: 36px; }
.dt-hero--text { padding: 20px; background: #FBFBFC; border: 1px solid #EDEDED; border-radius: 16px; }
.dt-hero-text { font-size: 14px; line-height: 1.85; color: var(--rl-text-2); white-space: pre-wrap; word-break: break-word; }
.dt-hero--svg { border-radius: 16px; overflow: hidden; background: #FFFFFF; border: 1px solid #EDEDED; display: flex; justify-content: center; }
.dt-hero-svg { max-width: 100%; max-height: 68vh; object-fit: contain; display: block; }
.dt-hero--html { border-radius: 16px; overflow: hidden; background: #F5F5F5; border: 1px solid #EDEDED; }
.dt-hero-html-wrap { position: relative; width: 100%; aspect-ratio: 3 / 4; overflow: hidden; }

.dt-pages { margin-bottom: 24px; }
.dt-pages-label { font-size: 12px; color: #9A9A9A; margin-bottom: 12px; font-weight: 500; }
.dt-pages-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 16px; }
.dt-page-wrap { border-radius:14px; overflow: hidden; background: #F5F5F5; border: 1px solid #EDEDED; }
.dt-page { position: relative; aspect-ratio: 3 / 4; overflow: hidden; }
.dt-page img { width: 100%; height: 100%; object-fit: cover; display: block; }
.dt-page img.dt-page-img--contain { object-fit: contain; background: #FFFFFF; }
.dt-page-scaler { position: absolute; top: 0; left: 0; }
.dt-page-iframe { width: 1080px; height: 1440px; border: none; display: block; background: #F5F5F5; }
.dt-page-fallback { width: 100%; height: 100%; display: flex; flex-direction: column; gap: 6px; align-items: center; justify-content: center; font-size: 28px; font-weight: 500; color: #9A9A9A; background: linear-gradient(135deg, #F5F5F5, #EDEDED); }
.dt-page-fallback-note { font-size: 12px; font-weight: 400; color: #B4B4B8; }
.dt-page-info { padding: 8px 12px; border-top: 1px solid #EDEDED; }
.dt-page-role { font-size: 12px; color: #4B4B4B; }

/* 作品图 / 正文 / 标签 拆成三块独立卡片上下排列：
   每一块自带标题栏与外框，图是图、文是文，滚到底也不会糊成一片 */
.dt-block {
  margin-bottom: 20px;
  padding: 16px 18px 18px;
  background: #FFFFFF;
  border: 1px solid #EDEDED;
  border-radius: 16px;
}

.dt-block-head {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin-bottom: 12px;
  padding-bottom: 10px;
  border-bottom: 1px solid #F2F2F3;
}

.dt-block-title {
  font-size: 13px;
  font-weight: 600;
  color: #1A1A1A;
  letter-spacing: 0.02em;
}

.dt-block-hint { font-size: 11px; color: #A1A1AA; font-variant-numeric: tabular-nums; }

/* 图块用浅底衬托作品本身，与正文块的白底拉开层次 */
.dt-block--media { background: #FAFAFB; }

/* 区块内部不再需要自带外边距，避免与标题栏间距叠加 */
.dt-block .dt-hero { margin-bottom: 0; }
.dt-block .dt-body-text { margin: 0; }

.dt-body { margin-bottom: 24px; }
.dt-section-label { font-size: 12px; color: #9A9A9A; margin-bottom: 10px; font-weight: 500; }
.dt-body-text { font-size: 14px; line-height: 1.85; color: var(--rl-text-2); white-space: pre-wrap; word-break: break-word; }
.dt-body-tags { display: flex; flex-wrap: wrap; gap: 6px; }
.dt-body-tag { font-size: 12px; padding: 3px 10px; border-radius: 999px; background: #F5F6F8; color: #6B6B6B; }

.dt-stats { display: flex; gap: 28px; margin-bottom: 24px; padding: 16px 18px; background: #FBFBFC; border: 1px solid #EDEDED; border-radius: 14px; }
.dt-stat { display: flex; flex-direction: column; gap: 4px; }
.dt-stat-val { font-size: 18px; font-weight: 500; color: #1a1a1a; }
.dt-stat-key { font-size: 12px; color: #9A9A9A; }

.dt-meta { margin-bottom: 24px; display: flex; flex-direction: column; gap: 12px; padding: 16px; background: #F5F5F5; border-radius:14px; }
.dt-meta-item { display: flex; gap: 12px; align-items: flex-start; }
.dt-meta-key { font-size: 12px; color: #9A9A9A; min-width: 72px; flex-shrink: 0; padding-top: 2px; }
.dt-meta-val { font-size: 13px; color: var(--rl-text-2); }
.dt-meta-tags { display: flex; flex-wrap: wrap; gap: 4px; }
.dt-tag { font-size: 11px; padding: 2px 8px; border-radius:8px; }
.dt-tag-keep { background: #D1FAE5; color: var(--rl-green); }
.dt-tag-avoid { background: #F4F4F5; color: #71717A; }

.dt-actions { display: flex; gap: 8px; padding-top: 16px; border-top: 1px solid #EDEDED; }
</style>