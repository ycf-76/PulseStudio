<template>
  <!-- 设计文档 3.4 #3：四阶段容器卡，一张卡原位切换。
       2026-11-04 改版：排版整体迁移到 Codex 语法（大哥拍板）——
       头部「• 粗标签 + 灰注」行、细节挂「└」缩进行、阶段清单 ✔/□ 字符、
       无图标无色块，进度条是唯一图形件（2px 发丝线），配色墨色系零紫。 -->
  <div v-if="stage" class="vtc-card">
    <!-- 头部行：• 粗标签 + 灰状态注，右侧大阶段收成字符状态 -->
    <p class="vtc-h">
      <span class="vtc-bullet">•</span>
      <span class="vtc-title">AI 口播增强</span>
      <span class="vtc-status">{{ STATUS_TEXT[stage] }}</span>
      <span class="vtc-stages">
        <span
          v-for="(s, i) in STAGE_LIST"
          :key="s.key"
          class="vtc-stage"
          :class="{ 'is-done': i < stageActiveIdx, 'is-cur': i === stageActiveIdx, 'is-err': stage === 'failed' && i === 1 }"
        >{{ stageMark(i) }} {{ s.label }}</span>
      </span>
    </p>

    <!-- ═══ 阶段体：同一张卡内按 stage 原位切换 ═══ -->

    <!-- 方案 / 失败重试：内嵌方案确认卡（确认/取消/让 AI 改 的 POST 由卡片自理） -->
    <div v-if="stage === 'plan' || stage === 'failed'" class="vtc-body">
      <p v-if="stage === 'failed'" class="vtc-sub vtc-sub-err">
        <span class="vtc-elbow">└</span> 渲染失败：{{ meta.cutPlanError || '未知原因，可重试' }}
      </p>
      <CutPlanConfirmCard
        v-if="!localResolved"
        :session-id="sessionId"
        :loop-state-path="meta.loopStatePath || ''"
        :plan="meta.cutPlan?.plan || null"
        :message="meta.cutPlan?.message"
        :turn-id="turnId"
        :failed-error="meta.cutPlanError"
        @confirmed="onInnerConfirmed"
        @cancelled="onInnerCancelled"
        @revise="onInnerRevise"
      />
      <!-- 内嵌卡自身 POST 成功后卡体消失，到 SSE 状态翻转之间的占位提示，避免空壳 -->
      <p v-else class="vtc-sub"><span class="vtc-elbow">└</span> {{ LOCAL_HINTS[localResolved] }}</p>
    </div>

    <!-- 渲染进度：└ 状态行 + 发丝进度条 + ✔/□ 阶段清单 -->
    <div v-else-if="stage === 'progress'" class="vtc-body">
      <p class="vtc-sub"><span class="vtc-elbow">└</span> {{ progressMainLine }}</p>
      <div class="vtc-bar"><i :style="{ width: percent + '%' }" /></div>
      <p v-if="progressSteps.length" class="vtc-phases">
        <template v-for="(st, i) in progressSteps" :key="st.key">
          <span v-if="i > 0" class="vtc-dim"> · </span>
          <span :class="{ 'is-done': st.state === 'done', 'is-cur': st.state === 'active' }">{{ st.state === 'done' ? '✔' : '□' }} {{ st.label }}</span>
        </template>
      </p>
      <p v-if="progressDetail" class="vtc-sub"><span class="vtc-elbow">└</span> {{ progressDetail }}</p>
      <!-- 实时预览（video studio agent start_preview 后由进度事件中途透传 preview_url）：
           单按钮常驻，重复事件同 URL 不重复渲染、不自动弹窗，点击才开新窗 -->
      <p v-if="previewUrl" class="vtc-sub">
        <span class="vtc-elbow">└</span> 实时预览
        <button class="vtc-version" :title="previewUrl" @click="openPreview">打开预览页</button>
      </p>
    </div>

    <!-- 成片：<video> 预览 + 版本切换（文字态，点击切换预览源，默认最新版） -->
    <div v-else-if="stage === 'done'" class="vtc-body">
      <video
        v-if="activeVideoUrl"
        :key="activeVideoUrl"
        class="vtc-video"
        controls
        playsinline
        preload="metadata"
        :src="activeVideoUrl"
      />
      <p v-else class="vtc-sub"><span class="vtc-elbow">└</span> 成片生成中，完成后可在此预览</p>
      <p v-if="versions.length" class="vtc-versions">
        <span class="vtc-dim">成片版本</span>
        <button
          v-for="(v, vi) in versions"
          :key="`${v.version}-${vi}`"
          class="vtc-version"
          :class="{ 'is-active': vi === activeVersionIdx }"
          :title="v.assetUrl ? '预览该版本成片' : '该版本暂无成片地址'"
          @click="selectVersion(vi)"
        >第 {{ v.version }} 版</button>
      </p>
    </div>

    <!-- 已取消 -->
    <div v-else-if="stage === 'cancelled'" class="vtc-body">
      <p class="vtc-sub"><span class="vtc-elbow">└</span> 已取消本次口播增强，可重新发送指令再试</p>
    </div>
  </div>
</template>

<script lang="ts">
// 设计文档 3.4 #3：四阶段容器卡，一张卡原位切换。
// 阶段推导为纯函数，导出供单测与宿主组件（AssistantMessageCell 渲染条件）复用。
import type { AgentMeta } from './cell-types'

export type VideoTaskStage = 'plan' | 'progress' | 'done' | 'failed' | 'cancelled'

/**
 * 由 agentMeta 推导口播卡当前阶段（顺序即优先级）：
 * - failed    cutPlanResolved==='failed'（错误 + 重试入口）
 * - cancelled cutPlanResolved==='cancelled'
 * - progress  videoProgress 且 percent<100（渲染进行中；先于 done 判定，
 *             兼容「确认即回 ok、渲染完成后补 version/asset_url」的时序）
 * - done      cutPlanResolved==='confirmed'（成片 + 版本条）
 * - plan      workflowStatus==='awaiting_cut_plan_confirmation'（先于 progress：
 *             二轮待确认时旧一轮残留的 videoProgress 不应盖住方案卡）
 * - 其余（无任何口播状态）返回 null，宿主据此不渲染卡片
 */
export function deriveVideoStage(meta: AgentMeta | undefined | null): VideoTaskStage | null {
  if (!meta) return null
  // videoProgress 被口播（talking_head_overlay）与非口播（auto_short_video）共用，
  // 只有口播 skill 的进度才算口播状态，避免短视频任务误渲染「AI 口播增强」卡
  const vp = meta.videoProgress?.skill === 'talking_head_overlay' ? meta.videoProgress : undefined
  const hasVoiceover =
    !!meta.cutPlan ||
    !!vp ||
    !!(meta.cutPlanVersions && meta.cutPlanVersions.length) ||
    meta.workflowStatus === 'awaiting_cut_plan_confirmation'
  if (!hasVoiceover) return null
  if (meta.cutPlanResolved === 'failed') return 'failed'
  if (meta.cutPlanResolved === 'cancelled') return 'cancelled'
  if (vp && vp.percent < 100) return 'progress'
  if (meta.cutPlanResolved === 'confirmed') return 'done'
  if (meta.workflowStatus === 'awaiting_cut_plan_confirmation') return 'plan'
  if (vp) return 'progress'
  return null
}
</script>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import CutPlanConfirmCard from './CutPlanConfirmCard.vue'

const props = defineProps<{
  /** 消息 agentMeta：口播全部状态（cutPlan / videoProgress / cutPlanVersions / ...）来源 */
  meta: AgentMeta
  /** 会话 id：透传给内嵌方案确认卡发 POST 用 */
  sessionId: string
  turnId?: string
}>()

// 决策事件一律转发给宿主（AssistantMessageCell 的 onCutPlan* 回写 meta）
const emit = defineEmits<{
  confirmed: [data: any]
  cancelled: []
  revise: [comment: string]
}>()

const stage = computed(() => deriveVideoStage(props.meta))

// ── 头部状态注与右侧阶段字符 ──
const STAGE_LIST = [
  { key: 'plan', label: '方案' },
  { key: 'progress', label: '渲染' },
  { key: 'done', label: '成片' },
] as const

const STATUS_TEXT: Record<string, string> = {
  plan: '方案待确认',
  progress: '渲染中',
  done: '完成',
  failed: '渲染失败',
  cancelled: '已取消',
}

// 当前高亮阶段序号（failed 算渲染段出错；cancelled 不高亮）
const stageActiveIdx = computed(() => {
  switch (stage.value) {
    case 'plan': return 0
    case 'progress': return 1
    case 'done': return 2
    case 'failed': return 1
    default: return -1
  }
})

// 右侧阶段字符：已过段绿 ✓、当前段实点（失败红 ✕）、未来段空心 ○
function stageMark(i: number): string {
  const idx = stageActiveIdx.value
  if (idx < 0) return '○'
  if (i < idx) return '✓'
  if (i === idx) return stage.value === 'failed' ? '✕' : '●'
  return '○'
}

// ── 渲染进度阶段 ──
const vp = computed(() => props.meta.videoProgress)
const percent = computed(() => Math.min(100, Math.max(0, Math.round(vp.value?.percent ?? 0))))
const progressLabel = computed(() => vp.value?.label || '视频生成中')
const progressMainLine = computed(() => {
  const parts = [`正在${progressLabel.value}`]
  if (vp.value?.step && vp.value?.total) parts.push(`第 ${vp.value.step}/${vp.value.total} 步`)
  parts.push(`${percent.value}%`)
  return parts.join(' · ')
})
const progressDetail = computed(() => vp.value?.detail || '')

// ── 实时预览 ──
// preview_url 可能中途出现（agent start_preview 后才有），进度事件原位更新
// meta.videoProgress → computed 自动跟随；同一 URL 重复事件值不变，v-if 渲染
// 的按钮恒一个，window.open 只在用户点击时触发（不随事件自动弹窗）。
const previewUrl = computed(() => vp.value?.preview_url || '')
function openPreview() {
  if (previewUrl.value) window.open(previewUrl.value)
}

// 阶段小清单：phase 枚举与后端 video_pipeline_progress 对齐（含 auto_short_video 旧格式无 phase 的容错）
const PHASE_STEPS = [
  { key: 'transcribe', label: '转写' },
  { key: 'plan', label: '规划' },
  { key: 'render', label: '渲染' },
  { key: 'review', label: '审片' },
] as const
const progressSteps = computed(() => {
  const cur = PHASE_STEPS.findIndex(p => p.key === vp.value?.phase)
  if (cur < 0) return []
  return PHASE_STEPS.map((p, i) => ({
    ...p,
    state: i < cur ? 'done' : i === cur ? 'active' : 'pending',
  }))
})

// ── 成片/版本阶段 ──
const versions = computed(() => props.meta.cutPlanVersions || [])
// 当前预览的版本下标：null = 跟随最新版（新版本到达时自动切到最新）
const selectedIdx = ref<number | null>(null)
watch(() => props.meta.cutPlanVersions?.length, () => { selectedIdx.value = null })
const activeVersionIdx = computed(() => selectedIdx.value ?? versions.value.length - 1)
const activeVideoUrl = computed(() => versions.value[activeVersionIdx.value]?.assetUrl || '')
function selectVersion(i: number) {
  selectedIdx.value = i
}

// ── 内嵌方案卡的本轮决策回执 ──
// 方案卡 POST 成功后自身退场，到 SSE 把 meta 状态翻转之间的占位提示；
// 新一轮方案到达（后端二次暂停会清 cutPlanResolved）时自动清掉，方案卡重现。
type LocalResolved = 'confirmed' | 'cancelled' | 'revise'
const localResolved = ref<LocalResolved | null>(null)
const LOCAL_HINTS: Record<LocalResolved, string> = {
  confirmed: '已确认，正在准备渲染…',
  cancelled: '已取消本次口播增强',
  revise: '已把修改意见带回对话，等 AI 重排方案…',
}
watch(() => props.meta.cutPlanResolved, (v) => {
  if (!v) localResolved.value = null
})
function onInnerConfirmed(data: any) {
  localResolved.value = 'confirmed'
  emit('confirmed', data)
}
function onInnerCancelled() {
  localResolved.value = 'cancelled'
  emit('cancelled')
}
function onInnerRevise(comment: string) {
  localResolved.value = 'revise'
  emit('revise', comment)
}
</script>

<style scoped>
/* Codex 排版语法 · 墨色系：卡底 #FCFCFA / 0.5px 边 / 文本排版层级，零紫 */
.vtc-card {
  background: #fcfcfa;
  border: 0.5px solid rgba(0, 0, 0, 0.08);
  border-radius: 10px;
  padding: 12px 16px;
  margin-top: 6px;
  display: flex;
  flex-direction: column;
}

/* 头部行：• 粗标签 + 灰注 */
.vtc-h {
  margin: 0;
  font-size: 13px;
  line-height: 1.7;
  display: flex;
  align-items: baseline;
  gap: 6px;
  flex-wrap: wrap;
}

.vtc-bullet { color: #b4b2a9; flex: none; }

.vtc-title {
  font-weight: 500;
  color: #2c2c2a;
  flex: none;
}

.vtc-status { color: #888780; min-width: 0; }

.vtc-stages {
  margin-left: auto;
  display: flex;
  gap: 10px;
  font-size: 11px;
  color: #b4b2a9;
  flex: none;
}

.vtc-stage.is-done { color: #3b6d11; }
.vtc-stage.is-cur { color: #2c2c2a; }
.vtc-stage.is-err { color: #a32d2d; }

/* └ 缩进细节行 */
.vtc-body { margin-top: 4px; }

.vtc-sub {
  margin: 2px 0 0;
  font-size: 12.5px;
  line-height: 1.7;
  color: #5f5e5a;
  word-break: break-word;
}

.vtc-elbow { color: #b4b2a9; margin-right: 2px; }

.vtc-sub-err { color: #a32d2d; }

/* 发丝进度条：唯一图形件 */
.vtc-bar {
  height: 2px;
  margin: 8px 0 0 14px;
  background: #eceae4;
  border-radius: 999px;
  overflow: hidden;
}

.vtc-bar i {
  display: block;
  height: 100%;
  background: #444441;
  border-radius: 999px;
  transition: width 0.4s ease;
}

/* ✔/□ 阶段清单 */
.vtc-phases {
  margin: 8px 0 0 14px;
  font-size: 12px;
  line-height: 1.7;
  color: #b4b2a9;
}

.vtc-phases .is-done { color: #3b6d11; }
.vtc-phases .is-cur { color: #2c2c2a; }
.vtc-dim { color: #b4b2a9; }

/* 成片/版本 */
.vtc-video {
  display: block;
  width: 100%;
  max-height: 320px;
  border-radius: 8px;
  background: #000;
  margin-top: 8px;
}

.vtc-versions {
  margin: 8px 0 0 14px;
  font-size: 12px;
  color: #888780;
  display: flex;
  align-items: baseline;
  gap: 10px;
}

.vtc-version {
  padding: 0;
  border: none;
  background: transparent;
  font-size: 12px;
  color: #888780;
  cursor: pointer;
  font-family: inherit;
}

.vtc-version:hover { color: #2c2c2a; text-decoration: underline; }

.vtc-version.is-active {
  color: #2c2c2a;
  font-weight: 500;
  text-decoration: underline;
}
</style>
