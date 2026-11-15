<template>
  <CardPreviewPane :page-index="pageIndex" :page-count="images.length">
    <template #screen>
      <div class="npp">
        <div class="npp-top">
          <span class="npp-avatar">{{ authorInitial }}</span>
          <span class="npp-nick">{{ authorName }}</span>
          <span class="npp-follow">关注</span>
        </div>

        <div class="npp-media">
          <video
            v-if="videoUrl"
            class="npp-img"
            :src="videoUrl"
            :poster="currentImage || undefined"
            controls
            playsinline
            preload="metadata"
          />
          <img
            v-else-if="currentImage && !imgFailed"
            class="npp-img"
            :src="currentImage"
            :alt="title || '作品页面'"
            @error="onImgError"
          />
          <div v-else class="npp-img npp-img--empty">
            {{ currentImage ? '图片已失效（链接过期）' : '暂无图片' }}
          </div>
          <span v-if="images.length > 1" class="npp-badge">{{ pageIndex + 1 }}/{{ images.length }}</span>
        </div>

        <div class="npp-bodywrap">
          <div class="npp-body">
            <div class="npp-title">{{ title || '未命名作品' }}</div>
            <div class="npp-text">{{ bodyText || '还没有正文，左侧填写后这里实时预览' }}</div>
            <div v-if="tagList.length" class="npp-tags">
              <span v-for="tag in tagList" :key="tag" class="npp-tag">#{{ tag }}</span>
            </div>
          </div>
          <div class="npp-fade"></div>
        </div>

        <div class="npp-bar">
          <span class="npp-act">
            <svg viewBox="0 0 24 24" class="npp-icon"><path d="M12 20s-7-4.5-7-9a4 4 0 0 1 7-2.6A4 4 0 0 1 19 11c0 4.5-7 9-7 9z" /></svg>
            点赞
          </span>
          <span class="npp-act">
            <svg viewBox="0 0 24 24" class="npp-icon"><path d="M12 4l2.5 5.2 5.5.8-4 3.9 1 5.6-5-2.7-5 2.7 1-5.6-4-3.9 5.5-.8z" /></svg>
            收藏
          </span>
          <span class="npp-act">
            <svg viewBox="0 0 24 24" class="npp-icon"><path d="M4 5h16v11H9l-5 4z" /></svg>
            评论
          </span>
        </div>
      </div>
    </template>
  </CardPreviewPane>
</template>

<script setup lang="ts">
/**
 * NotePhonePreview —— 采集/生成产物的手机预览。
 *
 * 复用 CardPreviewPane 的 PhoneFrame 与自适应缩放（手机壳必须完整可见），
 * 屏幕内部换成小红书笔记详情形态：顶部作者条、大图、标题正文、话题、底部互动栏。
 * 这样不论产物有没有 CardDeck，右栏都有一只完整手机，不会出现空白。
 */
import { computed, ref, watch } from 'vue'
import CardPreviewPane from '@/components/card-editor/CardPreviewPane.vue'
import { collectHashtags, stripHashtags } from './noteTags'

const props = withDefaults(
  defineProps<{
    title?: string
    contentText?: string
    tags?: string[]
    images?: string[]
    pageIndex?: number
    authorName?: string
    /** 视频产物：有值时屏幕主体换成播放器，封面图退化成 poster */
    videoUrl?: string
  }>(),
  {
    title: '',
    contentText: '',
    tags: () => [],
    images: () => [],
    pageIndex: 0,
    authorName: '我的账号',
    videoUrl: '',
  }
)

const authorInitial = computed(() => (props.authorName || '我').slice(0, 1))
const currentImage = computed(() => props.images[props.pageIndex] || props.images[0] || '')

/** 正文里的话题已经单独列在下方标签区，这里剥掉，避免手机里同一串话题出现两遍。 */
const bodyText = computed(() => stripHashtags(props.contentText))

/** 标签区与详情页共用同一套清洗规则：正文提取在前、传入补全在后，截断值一律剔除。 */
const tagList = computed(() => collectHashtags(props.contentText, props.tags))

/** 小红书 CDN 签名过期时，直连和代理都会失败，最终退成占位文案，避免整屏碎图。 */
const imgFailed = ref(false)
watch(currentImage, () => {
  imgFailed.value = false
})

/** 小红书 CDN 有防盗链，直连 403 时退到后端代理，与详情页大图同一套兜底。 */
function onImgError(event: Event) {
  const img = event.target as HTMLImageElement
  // 标记绑定到具体 URL：翻页复用同一个 <img>，沿用上一页的标记会把新页直接判死
  if (img.dataset.proxiedFor !== currentImage.value && currentImage.value.startsWith('http')) {
    img.dataset.proxiedFor = currentImage.value
    img.src = '/api/proxy/image?url=' + encodeURIComponent(currentImage.value)
    return
  }
  imgFailed.value = true
}
</script>

<style scoped>
.npp {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
  background: #fff;
  font-family: -apple-system, 'PingFang SC', 'Microsoft YaHei', sans-serif;
}

.npp-top {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
  border-bottom: 1px solid #f2f2f2;
  flex-shrink: 0;
}

.npp-avatar {
  width: 26px;
  height: 26px;
  border-radius: 50%;
  background: #3F3F46;
  color: #fff;
  font-size: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.npp-nick {
  font-size: 12px;
  color: #333;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
}

.npp-follow {
  font-size: 11px;
  color: #2563EB;
  border: 1px solid #2563EB;
  border-radius: 10px;
  padding: 2px 9px;
  flex-shrink: 0;
}

/* 笔记配图统一按 3:4 版式，和卡片画布 1080×1440 同比例，
 * 这样两种预览在手机里占的竖向空间一致，切换不会跳。 */
.npp-media {
  position: relative;
  width: 100%;
  aspect-ratio: 3 / 4;
  flex-shrink: 0;
  background: #f4f4f5;
}

.npp-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

.npp-img--empty {
  display: flex;
  align-items: center;
  justify-content: center;
  color: #a1a1aa;
  font-size: 12px;
  background: repeating-linear-gradient(45deg, #fafafa, #fafafa 10px, #f4f4f5 10px, #f4f4f5 20px);
}

.npp-badge {
  position: absolute;
  right: 10px;
  bottom: 10px;
  padding: 1px 8px;
  border-radius: 10px;
  background: rgba(0, 0, 0, 0.45);
  color: #fff;
  font-size: 10.5px;
  font-variant-numeric: tabular-nums;
}

/* 正文区可滚动（长笔记不截断），滚动条隐藏，用底部渐隐提示「还有内容」。 */
.npp-bodywrap {
  position: relative;
  flex: 1;
  min-height: 0;
}

.npp-body {
  position: absolute;
  inset: 0;
  overflow-y: auto;
  padding: 10px 14px 16px;
  scrollbar-width: none;
}

.npp-body::-webkit-scrollbar { display: none; }

.npp-fade {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  height: 26px;
  background: linear-gradient(180deg, rgba(255, 255, 255, 0), #fff 78%);
  pointer-events: none;
}

.npp-title {
  font-size: 14px;
  font-weight: 600;
  color: #1a1a1a;
  line-height: 1.45;
}

.npp-text {
  margin-top: 6px;
  font-size: 12px;
  color: #444;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-word;
}

.npp-tags {
  margin-top: 8px;
  display: flex;
  flex-wrap: wrap;
  gap: 5px 8px;
}

.npp-tag { font-size: 11px; color: #2b6cb0; }

.npp-bar {
  display: flex;
  align-items: center;
  justify-content: space-around;
  /* 底部多留 14px，避开悬浮的 home 指示条 */
  padding: 8px 14px 20px;
  border-top: 1px solid #f2f2f2;
  flex-shrink: 0;
}

.npp-act {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
  color: #71717a;
}

.npp-icon {
  width: 15px;
  height: 15px;
  fill: none;
  stroke: #71717a;
  stroke-width: 1.6;
  stroke-linejoin: round;
}
</style>