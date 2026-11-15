<template>
<AppShell current-page="skills">
    <main class="sk-page">
      <div class="da-content-card-wrapper">
      <div class="da-content-card">

      <div class="sk-container">

      <div class="sk-header">
        <div class="sk-header-left">
          <h1 class="sk-title">技能库</h1>
          <span class="sk-subtitle">
            {{ total }} 个技能 · {{ groups.length }} 个分组
          </span>
        </div>
        <div class="sk-header-right">
          <div class="sk-search" role="search">
            <Search :size="15" :stroke-width="1.5" />
            <input
              v-model="query"
              type="text"
              placeholder="搜索技能名称或描述"
              aria-label="搜索技能名称或描述"
            />
            <button
              v-if="query"
              class="sk-search-clear"
              aria-label="清空搜索"
              @click="query = ''"
            >
              <X :size="13" />
            </button>
          </div>
          <button
            class="sk-refresh"
            :class="{ 'is-loading': loading }"
            :disabled="loading"
            @click="reload"
            title="刷新"
            aria-label="刷新技能列表"
          >
            <RefreshCw :size="15" :stroke-width="1.5" />
          </button>
        </div>
      </div>

      <div
        class="sk-carousel"
        @mouseenter="stopAuto"
        @mouseleave="startAuto"
        @pointerdown.passive="pauseAutoTemporarily()"
      >
        <div ref="carViewport" class="sk-car-viewport">
          <div
            v-for="expert in experts"
            :key="expert.id"
            class="sk-car-card"
            :class="{ 'sk-car-active': activeExpertId === expert.id }"
            :style="{
              '--sk-accent': expert.accent,
              '--sk-photo': expert.photo ? `url(${expert.photo})` : 'none',
            }"
            role="button"
            tabindex="0"
            :aria-pressed="activeExpertId === expert.id"
            :aria-label="`筛选 ${expert.name} 的精选技能`"
            @click="toggleExpert(expert)"
            @keydown.enter.prevent="toggleExpert(expert)"
            @keydown.space.prevent="toggleExpert(expert)"
          >
            <span class="sk-car-media" aria-hidden="true"></span>
            <span class="sk-car-veil" aria-hidden="true"></span>
            <span v-if="expert.hot" class="sk-car-hot">热门</span>
            <span class="sk-car-body">
              <span class="sk-car-name">{{ expert.name }}</span>
              <span class="sk-car-tagline">{{ expert.tagline }}</span>
              <span class="sk-car-count">{{ expertCounts[expert.id] || 0 }} 个技能</span>
            </span>
            <button
              class="sk-car-summon"
              :title="`召唤 ${expert.name} 到对话`"
              :aria-label="`召唤 ${expert.name} 到对话`"
              @click.stop="summonExpert(expert)"
            >
              召唤
            </button>
          </div>
        </div>
        <button class="sk-car-arrow sk-car-prev" aria-label="上一组" @click="scrollCarousel(-1)">
          <ChevronLeft :size="16" />
        </button>
        <button class="sk-car-arrow sk-car-next" aria-label="下一组" @click="scrollCarousel(1)">
          <ChevronRight :size="16" />
        </button>
      </div>

      <div class="sk-body">
        <div v-if="activeExpert" class="sk-filter-chip">
          <span>{{ activeExpert.name }} 精选</span>
          <button aria-label="取消专家筛选" @click="activeExpertId = ''"><X :size="12" /></button>
        </div>
        <div v-if="loading && total === 0" class="sk-state">正在加载技能...</div>
        <div v-else-if="error && total === 0" class="sk-state">
          <span>{{ error }}</span>
          <button class="sk-retry" @click="reload">重试</button>
        </div>
        <div v-else-if="filteredGroups.length === 0" class="sk-state">
          {{ query.trim() || activeExpert ? '没有匹配的技能' : '暂无技能' }}
        </div>

        <section v-for="group in filteredGroups" :key="group.nodeType" class="sk-group">
          <header class="sk-group-head">
            <span class="sk-group-label">{{ group.label }}</span>
            <span class="sk-group-count">{{ group.skills.length }}</span>
            <span class="sk-group-key">{{ group.nodeType }}</span>
          </header>
          <div class="sk-grid">
            <button
              v-for="skill in group.skills"
              :key="group.nodeType + '/' + skill.name"
              class="sk-card"
              :title="skill.description || skill.name"
              @click="onUseSkill(skill)"
            >
              <div class="sk-card-top">
                <span class="sk-card-name">{{ skill.display_name || skill.name }}</span>
                <span v-if="skill.is_prompt_skill" class="sk-badge">提示词</span>
              </div>
              <p class="sk-card-desc">{{ skill.description || '暂无描述' }}</p>
              <div class="sk-card-foot">
                <code class="sk-card-key">/{{ skill.name }}</code>
              </div>
            </button>
          </div>
        </section>
      </div>

      </div>
      </div>
      </div>

      <Teleport to="body">
        <div v-if="activeSkill" class="sk-modal-mask" @click.self="closeSkill">
          <div class="sk-modal">
            <header class="sk-modal-head">
              <div class="sk-modal-head-main">
                <h2 class="sk-modal-title">{{ activeSkill.display_name || activeSkill.name }}</h2>
                <div class="sk-modal-meta">
                  <span class="sk-modal-slug">/{{ activeSkill.name }}</span>
                  <span class="sk-modal-dot">·</span>
                  <span>{{ labelOf(activeSkill.node_type) }}</span>
                  <span v-if="activeSkill.is_prompt_skill" class="sk-modal-flag">提示词技能</span>
                  <span v-if="activeSkill.is_third_party" class="sk-modal-flag">第三方</span>
                </div>
              </div>
              <button class="sk-modal-close" @click="closeSkill"><X :size="16" /></button>
            </header>

            <div class="sk-modal-body">
              <div v-if="detailLoading" class="sk-modal-loading">正在加载技能详情...</div>
              <template v-else>
                <p v-if="detailError" class="sk-modal-err">{{ detailError }}</p>

                <section class="sk-modal-sec">
                  <h3 class="sk-modal-h">技能说明</h3>
                  <p class="sk-modal-desc">{{ activeSkill.description || '暂无描述' }}</p>
                </section>

                <section v-if="triggerWords.length" class="sk-modal-sec">
                  <h3 class="sk-modal-h">触发词</h3>
                  <div class="sk-modal-tags">
                    <span v-for="word in triggerWords" :key="word" class="sk-modal-tag">{{ word }}</span>
                  </div>
                </section>

                <section v-if="inputFields.length" class="sk-modal-sec">
                  <h3 class="sk-modal-h">输入参数</h3>
                  <table class="sk-modal-table">
                    <thead>
                      <tr><th>参数</th><th>类型</th><th>说明</th></tr>
                    </thead>
                    <tbody>
                      <tr v-for="field in inputFields" :key="field.name">
                        <td>
                          <code class="sk-modal-code">{{ field.name }}</code>
                          <span v-if="field.required" class="sk-modal-req">必填</span>
                        </td>
                        <td class="sk-modal-type">{{ field.type }}</td>
                        <td>{{ field.description || '—' }}</td>
                      </tr>
                    </tbody>
                  </table>
                </section>

                <section v-if="promptContent" class="sk-modal-sec">
                  <h3 class="sk-modal-h">提示词内容</h3>
                  <pre class="sk-modal-pre">{{ promptContent }}</pre>
                </section>
              </template>
            </div>

            <footer class="sk-modal-foot">
              <button class="sk-modal-copy" @click="copySkillName">复制 /{{ activeSkill.name }}</button>
              <button class="sk-modal-enable" @click="enableSkill">启用该技能</button>
            </footer>
          </div>
        </div>
      </Teleport>

      <transition name="sk-toast">
        <div v-if="toast" class="sk-toast">{{ toast }}</div>
      </transition>
    </main>
</AppShell>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ChevronLeft, ChevronRight, RefreshCw, Search, X } from 'lucide-vue-next'
import AppShell from '@/components/layout/AppShell.vue'
import { useSkillsLibrary } from '@/composables/useSkillsLibrary'
import { skillsApi, type SkillDetail, type SkillMeta } from '@/api/skills'
import { agentsApi } from '@/api/agents'

const router = useRouter()

const lib = useSkillsLibrary()
const { groups, total, loading, error, load, flatSkills, labelOf } = lib

interface Expert {
  id: string
  name: string
  tagline: string
  accent: string
  photo?: string
  hot?: boolean
  nodeTypes?: string[]
  skillNames?: string[]
}

const coverModules = import.meta.glob<string>('../assets/skills/*.{png,jpg,jpeg,webp,avif,svg}', {
  eager: true,
  query: '?url',
  import: 'default',
})

const coversById = new Map<string, string>()
for (const [path, url] of Object.entries(coverModules)) {
  const base = path.split('/').pop()?.replace(/\.\w+$/, '') || ''
  if (base && !coversById.has(base)) coversById.set(base, url)
}

const experts: Expert[] = [
  {
    id: 'trend-hunter',
    name: '情报师',
    tagline: '追热点、扒竞品、拆爆款',
    accent: '#FF7A59',
    hot: true,
    nodeTypes: ['discover', 'search'],
  },
  {
    id: 'strategist',
    name: '策划师',
    tagline: '账号定位、内容矩阵、排期',
    accent: '#6366F1',
    nodeTypes: ['plan'],
  },
  {
    id: 'copywriter',
    name: '文案师',
    tagline: '标题钩子、笔记正文、口播稿',
    accent: '#10B981',
    nodeTypes: ['copywrite'],
  },
  {
    id: 'longform',
    name: '长文编辑',
    tagline: '公众号长文、知乎回答、论文解读',
    accent: '#7C3AED',
    nodeTypes: ['produce'],
  },
  {
    id: 'visual',
    name: '视觉师',
    tagline: '封面卡片、信息图、海报',
    accent: '#F59E0B',
    hot: true,
  },
  {
    id: 'video',
    name: '视频师',
    tagline: '分镜脚本、剪辑、卡点',
    accent: '#06B6D4',
    hot: true,
  },
  {
    id: 'inspector',
    name: '质检师',
    tagline: '风险扫描、人设校验、评分',
    accent: '#8B5CF6',
    nodeTypes: ['quality_gate', 'polish'],
  },
  {
    id: 'meme',
    name: '梗图师',
    tagline: '玩梗、表情包、情绪图',
    accent: '#EC4899',
    hot: true,
  },
  {
    id: 'sound',
    name: '声音师',
    tagline: '配音、音色克隆、BGM 卡点',
    accent: '#22C55E',
  },
  {
    id: 'community',
    name: '社群运营官',
    tagline: '评论回复、粉丝分层、合作洽谈',
    accent: '#14B8A6',
  },
  {
    id: 'reviewer',
    name: '复盘师',
    tagline: '数据追踪、归因、ROI 复盘',
    accent: '#0EA5E9',
    nodeTypes: ['attribute'],
  },
]

for (const expert of experts) {
  const photo = coversById.get(expert.id)
  if (photo) expert.photo = photo
}

const activeExpertId = ref('')
const activeExpert = computed(() => experts.find(e => e.id === activeExpertId.value) || null)

// 技能白名单以后端 AgentDef 为唯一事实来源，卡片不再手写技能清单
const agentSkills = ref<Record<string, string[]>>({})

async function loadAgentSkills() {
  try {
    const list = await agentsApi.list()
    const map: Record<string, string[]> = {}
    for (const a of list) map[a.agent_id] = a.skills || []
    agentSkills.value = map
  } catch {
    agentSkills.value = {}
  }
}

const expertMatchers = computed(() =>
  experts.map(expert => {
    const fromAgent = agentSkills.value[expert.id]
    return {
      expert,
      nodeTypes: new Set(expert.nodeTypes || []),
      skillNames: new Set(fromAgent && fromAgent.length ? fromAgent : (expert.skillNames || [])),
      fromAgent: Boolean(fromAgent && fromAgent.length),
    }
  })
)

const expertMatcherById = computed(() => new Map(expertMatchers.value.map(m => [m.expert.id, m])))

function matchesExpert(skill: SkillMeta, expert: Expert) {
  const matcher = expertMatcherById.value.get(expert.id)
  if (!matcher) return false
  if (matcher.skillNames.has(skill.name)) return true
  // 后端 Agent 已加载时以白名单为准，node_type 只在拿不到定义时兜底
  return matcher.fromAgent ? false : matcher.nodeTypes.has(skill.node_type)
}

const expertCounts = computed<Record<string, number>>(() => {
  const map: Record<string, number> = {}
  for (const matcher of expertMatchers.value) map[matcher.expert.id] = 0
  for (const skill of flatSkills.value) {
    for (const matcher of expertMatchers.value) {
      if (matcher.skillNames.has(skill.name)) map[matcher.expert.id] += 1
      else if (!matcher.fromAgent && matcher.nodeTypes.has(skill.node_type)) map[matcher.expert.id] += 1
    }
  }
  return map
})

const carViewport = ref<HTMLElement | null>(null)
let _carTimer: number | undefined
let _resumeTimer: number | undefined

const prefersReducedMotion =
  typeof window !== 'undefined' &&
  window.matchMedia?.('(prefers-reduced-motion: reduce)').matches === true

function carouselStep(): number {
  const el = carViewport.value
  const first = el?.querySelector<HTMLElement>('.sk-car-card')
  if (!el || !first) return 258
  const gap = parseFloat(getComputedStyle(el).columnGap || '') || 12
  return first.offsetWidth + gap
}

function scrollCarousel(direction: number) {
  const el = carViewport.value
  if (!el) return
  const behavior: ScrollBehavior = prefersReducedMotion ? 'auto' : 'smooth'
  const step = carouselStep()
  const max = el.scrollWidth - el.clientWidth
  if (max <= 0) return
  if (direction > 0 && el.scrollLeft >= max - 8) {
    el.scrollTo({ left: 0, behavior })
    return
  }
  if (direction < 0 && el.scrollLeft <= 8) {
    el.scrollTo({ left: max, behavior })
    return
  }
  el.scrollBy({ left: direction * step, behavior })
}

function startAuto() {
  stopAuto()
  if (prefersReducedMotion || document.hidden) return
  _carTimer = window.setInterval(() => scrollCarousel(1), 4200)
}

function stopAuto() {
  if (_carTimer) window.clearInterval(_carTimer)
  _carTimer = undefined
  if (_resumeTimer) window.clearTimeout(_resumeTimer)
  _resumeTimer = undefined
}

function pauseAutoTemporarily(delay = 5000) {
  stopAuto()
  _resumeTimer = window.setTimeout(() => {
    _resumeTimer = undefined
    startAuto()
  }, delay)
}

function handleVisibilityChange() {
  if (document.hidden) stopAuto()
  else startAuto()
}

function toggleExpert(expert: Expert) {
  activeExpertId.value = activeExpertId.value === expert.id ? '' : expert.id
}

function summonExpert(expert: Expert) {
  router.push({ path: '/chat', query: { agent: expert.id } }).catch(() => {})
}

const query = ref('')
const toast = ref('')
let _toastTimer: number | undefined

const filteredGroups = computed(() => {
  const q = query.value.trim().toLowerCase()
  const expert = activeExpert.value
  if (!q && !expert) return groups.value
  return groups.value
    .map(g => ({
      ...g,
      skills: g.skills.filter(s => {
        if (expert && !matchesExpert(s, expert)) return false
        if (!q) return true
        return s.name.toLowerCase().includes(q) ||
          (s.display_name || '').toLowerCase().includes(q) ||
          (s.description || '').toLowerCase().includes(q)
      }),
    }))
    .filter(g => g.skills.length > 0)
})

function reload() {
  load(true)
  loadAgentSkills()
}

function fallbackCopy(text: string): boolean {
  try {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.setAttribute('readonly', '')
    ta.style.position = 'fixed'
    ta.style.top = '-9999px'
    document.body.appendChild(ta)
    ta.select()
    const ok = document.execCommand('copy')
    document.body.removeChild(ta)
    return ok
  } catch {
    return false
  }
}

async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch {
    return fallbackCopy(text)
  }
  return fallbackCopy(text)
}

const activeSkill = ref<SkillMeta | null>(null)
const skillDetail = ref<SkillDetail | null>(null)
const detailLoading = ref(false)
const detailError = ref('')
let _detailReqId = 0

const triggerWords = computed(() => skillDetail.value?.trigger_words || [])
const inputFields = computed(() => skillDetail.value?.input_fields || [])
const promptContent = computed(() => skillDetail.value?.prompt_content || '')

function onUseSkill(skill: SkillMeta) {
  activeSkill.value = skill
  skillDetail.value = null
  detailError.value = ''
  detailLoading.value = true
  const reqId = ++_detailReqId
  skillsApi
    .detail(skill.node_type, skill.name)
    .then(detail => {
      if (reqId !== _detailReqId) return
      skillDetail.value = detail
    })
    .catch((e: any) => {
      if (reqId !== _detailReqId) return
      detailError.value = e?.response?.data?.detail || e?.message || '技能详情加载失败'
    })
    .finally(() => {
      if (reqId !== _detailReqId) return
      detailLoading.value = false
    })
}

function closeSkill() {
  activeSkill.value = null
  skillDetail.value = null
  detailError.value = ''
  detailLoading.value = false
  _detailReqId++
}

function enableSkill() {
  const skill = activeSkill.value
  if (!skill) return
  const query: Record<string, string> = {
    skill: skill.name,
    skillType: skill.node_type,
    skillName: skill.display_name || skill.name,
  }
  if (skill.description) query.skillDesc = skill.description.slice(0, 200)
  closeSkill()
  router.push({ path: '/chat', query }).catch(() => {})
}

async function copySkillName() {
  if (!activeSkill.value) return
  const text = `/${activeSkill.value.name}`
  const copied = await copyText(text)
  showToast(copied ? `已复制 ${text}` : `复制失败，请手动输入 ${text}`)
}

function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape' && activeSkill.value) closeSkill()
}

function showToast(message: string) {
  toast.value = message
  if (_toastTimer) window.clearTimeout(_toastTimer)
  _toastTimer = window.setTimeout(() => {
    toast.value = ''
  }, 2000)
}

onMounted(() => {
  load()
  loadAgentSkills()
  startAuto()
  document.addEventListener('visibilitychange', handleVisibilityChange)
  document.addEventListener('keydown', onKeydown)
})

onUnmounted(() => {
  stopAuto()
  document.removeEventListener('visibilitychange', handleVisibilityChange)
  document.removeEventListener('keydown', onKeydown)
  if (_toastTimer) window.clearTimeout(_toastTimer)
})
</script>

<style scoped>
.sk-page {
  flex: 1;
  min-width: 0;
  height: 100%;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  padding: 28px 20px 16px;
  position: relative;
}

.da-content-card-wrapper { display: flex; flex-direction: column; flex: 1; min-height: 0; min-width: 0; overflow: hidden; }
.da-content-card { flex: 1; min-height: 0; min-width: 0; background: #FFFFFF; border-radius: 16px; border: 1px solid rgba(0, 0, 0, 0.06); box-shadow: none; display: flex; flex-direction: column; overflow: hidden; }
.sk-container { flex: 1; min-height: 0; min-width: 0; display: flex; flex-direction: column; overflow: hidden; padding: 4px 24px 12px; }

.sk-carousel {
  flex: none;
  position: relative;
  margin-bottom: 18px;
}
.sk-car-viewport {
  display: flex;
  gap: 14px;
  overflow-x: auto;
  scroll-snap-type: x mandatory;
  scrollbar-width: none;
  padding: 4px 2px 10px;
}
.sk-car-viewport::-webkit-scrollbar { display: none; }
.sk-car-card {
  flex: 0 0 246px;
  height: 208px;
  scroll-snap-align: start;
  display: block;
  padding: 0;
  border: none;
  border-radius: 18px !important;
  background: linear-gradient(180deg, color-mix(in srgb, var(--sk-accent) 24%, #fff) 0%, #fff 68%);
  text-align: left;
  cursor: pointer;
  position: relative;
  overflow: hidden;
  isolation: isolate;
  box-shadow: 0 1px 2px rgba(17, 24, 39, 0.04), 0 10px 24px -20px rgba(17, 24, 39, 0.5);
  transition: transform 0.25s ease, box-shadow 0.25s ease;
}
.sk-car-media {
  position: absolute;
  inset: 0;
  z-index: 0;
  background-image: var(--sk-photo, none);
  background-size: cover;
  background-position: center top;
  background-repeat: no-repeat;
  transform: scale(1.001);
  transition: transform 0.5s ease;
}
.sk-car-veil {
  position: absolute;
  inset: 0;
  z-index: 1;
  pointer-events: none;
  background: linear-gradient(
    180deg,
    color-mix(in srgb, var(--sk-accent) 22%, transparent) 0%,
    rgba(255, 255, 255, 0.06) 28%,
    rgba(255, 255, 255, 0.9) 54%,
    #ffffff 72%,
    #ffffff 100%
  );
}
.sk-car-card:hover {
  transform: translateY(-3px);
  box-shadow: 0 2px 6px rgba(17, 24, 39, 0.06), 0 18px 32px -22px rgba(17, 24, 39, 0.55);
}
.sk-car-card:focus-visible {
  outline: 2px solid var(--sk-accent);
  outline-offset: 2px;
}
.sk-car-card:hover .sk-car-media { transform: scale(1.04); }
.sk-car-active {
  box-shadow:
    0 0 0 2px #fff,
    0 0 0 4px color-mix(in srgb, var(--sk-accent) 70%, #111827),
    0 18px 32px -22px rgba(17, 24, 39, 0.5);
}
.sk-car-body {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  z-index: 2;
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: 0 16px 14px;
}
.sk-car-name {
  font-size: 14px;
  font-weight: 500;
  letter-spacing: -0.01em;
  color: #111827;
}
.sk-car-tagline {
  font-size: 12px;
  line-height: 1.5;
  color: #6b7280;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.sk-car-count {
  margin-top: 2px;
  font-size: 11px;
  letter-spacing: 0.02em;
  color: #9ca3af;
}
.sk-car-hot {
  position: absolute;
  top: 12px;
  right: 12px;
  z-index: 3;
  padding: 2px 8px;
  font-size: 10px;
  font-weight: 500;
  letter-spacing: 0.04em;
  border-radius: 999px !important;
  background: rgba(255, 255, 255, 0.86);
  color: #e11d48;
  backdrop-filter: blur(6px);
}
.sk-car-summon {
  position: absolute;
  right: 12px;
  bottom: 12px;
  z-index: 4;
  padding: 4px 10px;
  border: none;
  border-radius: 999px !important;
  background: var(--sk-accent);
  color: #fff;
  font-size: 11px;
  font-weight: 500;
  letter-spacing: 0.02em;
  line-height: 1.4;
  cursor: pointer;
  box-shadow: 0 2px 6px color-mix(in srgb, var(--sk-accent) 35%, transparent);
  opacity: 0.92;
  transition: transform 0.18s ease, box-shadow 0.18s ease, opacity 0.18s ease;
}
.sk-car-summon:hover {
  opacity: 1;
  transform: translateY(-1px);
  box-shadow: 0 4px 10px color-mix(in srgb, var(--sk-accent) 45%, transparent);
}
.sk-car-summon:focus-visible {
  outline: 2px solid #fff;
  outline-offset: 2px;
}
.sk-car-arrow {
  position: absolute;
  top: 50%;
  transform: translateY(-50%);
  width: 28px;
  height: 28px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: none;
  border-radius: 50% !important;
  background: #fff;
  color: #4b5563;
  cursor: pointer;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.14);
  opacity: 0;
  transition: opacity 0.2s ease;
  z-index: 2;
}
.sk-carousel:hover .sk-car-arrow { opacity: 1; }
.sk-car-arrow:hover { color: #111; }
.sk-car-prev { left: -8px; }
.sk-car-next { right: -8px; }

.sk-filter-chip {
  flex: none;
  align-self: flex-start;
  display: inline-flex;
  align-items: center;
  gap: 7px;
  padding: 0 0 6px;
  border: none;
  border-bottom: 1px solid rgba(0, 0, 0, 0.1);
  background: transparent;
  font-size: 12px;
  color: #374151;
}
.sk-filter-chip button {
  display: flex;
  align-items: center;
  border: none;
  background: transparent;
  color: #9ca3af;
  cursor: pointer;
  padding: 0;
}
.sk-filter-chip button:hover { color: #374151; }

.sk-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex: none;
  padding: 16px 0 14px;
}
.sk-header-left {
  display: flex;
  align-items: baseline;
  gap: 10px;
}
.sk-title {
  margin: 0;
  font-size: 18px;
  font-weight: 500;
  color: #1f1f1f;
  letter-spacing: -0.2px;
}
.sk-subtitle {
  font-size: 12px;
  color: #9a9a9a;
}
.sk-header-right {
  display: flex;
  align-items: center;
  gap: 8px;
}
.sk-search {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 34px;
  padding: 0 14px;
  min-width: 260px;
  border-radius: 999px;
  background: #F7F7F8;
  border: 1px solid transparent;
  color: #A1A1AA;
  transition: background 0.18s ease, border-color 0.18s ease, box-shadow 0.18s ease;
}
.sk-search:hover {
  background: #fff;
  border-color: #E8E8EA;
}
.sk-search:focus-within {
  background: #fff;
  border-color: #D4D4D8;
  box-shadow: 0 0 0 3px rgba(0, 0, 0, 0.04);
}
.sk-search:focus-within > svg { color: #71717A; }
.sk-search input {
  flex: 1;
  min-width: 0;
  border: none !important;
  outline: none;
  background: transparent !important;
  box-shadow: none !important;
  font-size: 13px;
  color: #333;
}
.sk-search input::placeholder { color: #B4B4BB; }
.sk-search-clear {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  border: none;
  border-radius: 50%;
  background: #E4E4E7;
  color: #71717A;
  cursor: pointer;
  padding: 0;
  transition: background 0.15s ease, color 0.15s ease;
}
.sk-search-clear:hover { background: #D4D4D8; color: #3F3F46; }
@media (max-width: 720px) {
  .sk-search { min-width: 0; flex: 1; }
}
.sk-refresh {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  border-radius: 10px !important;
  border: 1px solid rgba(0, 0, 0, 0.07);
  background: #fff;
  color: #8a8a8a;
  cursor: pointer;
}
.sk-refresh:hover { color: #333; background: #fafafa; }
.sk-refresh:disabled { opacity: 0.45; cursor: default; }
.sk-refresh.is-loading svg { animation: sk-spin 0.9s linear infinite; }
@keyframes sk-spin { to { transform: rotate(360deg); } }

.sk-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 18px;
  padding-bottom: 12px;
}
.sk-body::-webkit-scrollbar { width: 6px; }
.sk-body::-webkit-scrollbar-thumb { background: rgba(0, 0, 0, 0.1); border-radius: 6px; }

.sk-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  padding: 60px 16px;
  font-size: 13px;
  color: #a0a0a0;
}
.sk-retry {
  border: none;
  background: rgba(0, 0, 0, 0.05);
  color: #555;
  font-size: 12px;
  padding: 6px 16px;
  border-radius: 8px !important;
  cursor: pointer;
}

.sk-group {
  flex: none;
  background: #fff;
  border: 1px solid rgba(0, 0, 0, 0.06);
  border-radius: 14px;
  padding: 14px 16px 16px;
}
.sk-group-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding-bottom: 12px;
}
.sk-group-label {
  font-size: 14px;
  font-weight: 500;
  color: #2a2a2a;
}
.sk-group-count {
  font-size: 11px;
  color: #b0b0b0;
  padding: 0;
  border-radius: 0;
}
.sk-group-key {
  margin-left: auto;
  font-size: 11px;
  color: #c0c0c0;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}

.sk-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: 10px;
}
.sk-card {
  display: flex;
  flex-direction: column;
  gap: 6px;
  text-align: left;
  font-family: inherit;
  padding: 11px 12px;
  border-radius: 10px !important;
  border: 1px solid rgba(0, 0, 0, 0.06);
  background: #fcfcfc;
  cursor: pointer;
  transition: border-color 0.14s ease, background 0.14s ease, transform 0.14s ease;
}
.sk-card:hover {
  background: #fff;
  border-color: rgba(0, 0, 0, 0.14);
  transform: translateY(-1px);
}
.sk-card-top {
  display: flex;
  align-items: center;
  gap: 6px;
}
.sk-card-name {
  flex: 1;
  min-width: 0;
  font-size: 14px;
  font-weight: 500;
  line-height: 1.45;
  color: #262626;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.sk-badge {
  flex: none;
  font-size: 10px;
  line-height: 1;
  padding: 3px 6px;
  border-radius: 5px;
  background: rgba(0, 0, 0, 0.055);
  color: #8a8a8a;
}
.sk-card-desc {
  margin: 0;
  font-size: 12px;
  line-height: 1.5;
  color: #8c8c8c;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.sk-card-foot {
  display: flex;
  align-items: center;
}
.sk-card-key {
  font-size: 11px;
  color: #a8a8a8;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}

.sk-toast {
  position: absolute;
  left: 50%;
  bottom: 24px;
  transform: translateX(-50%);
  padding: 9px 18px;
  border-radius: 10px;
  background: rgba(40, 40, 40, 0.92);
  color: #fff;
  font-size: 13px;
  white-space: nowrap;
  pointer-events: none;
  z-index: 20;
}
.sk-toast-enter-active, .sk-toast-leave-active { transition: opacity 0.18s ease, transform 0.18s ease; }
.sk-toast-enter-from, .sk-toast-leave-to { opacity: 0; transform: translateX(-50%) translateY(6px); }

.sk-modal-mask {
  position: fixed;
  inset: 0;
  z-index: 1000;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 32px 16px;
  background: rgba(20, 20, 22, 0.42);
  backdrop-filter: blur(2px);
}

.sk-modal {
  width: min(680px, 100%);
  max-height: min(78vh, 760px);
  display: flex;
  flex-direction: column;
  background: #fff;
  border-radius: 16px;
  box-shadow: 0 24px 64px rgba(0, 0, 0, 0.22);
  overflow: hidden;
}

.sk-modal-head {
  flex: none;
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  padding: 20px 22px 16px;
  border-bottom: 1px solid rgba(0, 0, 0, 0.06);
}
.sk-modal-head-main { min-width: 0; }
.sk-modal-title {
  margin: 0;
  font-size: 15px;
  font-weight: 500;
  color: #1d1d1f;
  line-height: 1.35;
}
.sk-modal-meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 7px;
  font-size: 12px;
  color: #8a8a8e;
}
.sk-modal-slug {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  color: #6b6b70;
}
.sk-modal-dot { color: #c7c7cc; }
.sk-modal-flag {
  padding: 1px 7px;
  border-radius: 4px;
  background: rgba(0, 0, 0, 0.05);
  color: #6b6b70;
  font-size: 11px;
}
.sk-modal-close {
  flex: none;
  width: 30px;
  height: 30px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: none;
  border-radius: 8px !important;
  background: transparent;
  color: #8a8a8e;
  cursor: pointer;
  transition: background 0.15s ease, color 0.15s ease;
}
.sk-modal-close:hover { background: rgba(0, 0, 0, 0.06); color: #1d1d1f; }

.sk-modal-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 18px 22px 20px;
}
.sk-modal-body::-webkit-scrollbar { width: 6px; }
.sk-modal-body::-webkit-scrollbar-thumb { background: rgba(0, 0, 0, 0.1); border-radius: 6px; }

.sk-modal-loading {
  padding: 32px 0;
  text-align: center;
  font-size: 13px;
  color: #a0a0a0;
}
.sk-modal-err {
  margin: 0 0 14px;
  padding: 10px 12px;
  border-radius: 8px;
  background: rgba(239, 68, 68, 0.08);
  color: #c2410c;
  font-size: 13px;
}

.sk-modal-sec + .sk-modal-sec { margin-top: 20px; }
.sk-modal-h {
  margin: 0 0 8px;
  font-size: 12px;
  font-weight: 500;
  letter-spacing: 0.04em;
  color: #8a8a8e;
}
.sk-modal-desc {
  margin: 0;
  font-size: 14px;
  line-height: 1.75;
  color: #3a3a3c;
  white-space: pre-wrap;
  word-break: break-word;
}

.sk-modal-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.sk-modal-tag {
  padding: 3px 9px;
  border-radius: 4px;
  background: #f4f4f6;
  color: #55555a;
  font-size: 12px;
}

.sk-modal-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
  color: #3a3a3c;
}
.sk-modal-table th {
  text-align: left;
  font-weight: 500;
  color: #8a8a8e;
  font-size: 12px;
  padding: 0 8px 6px;
  border-bottom: 1px solid rgba(0, 0, 0, 0.06);
}
.sk-modal-table td {
  padding: 8px;
  border-bottom: 1px solid rgba(0, 0, 0, 0.04);
  vertical-align: top;
  line-height: 1.6;
}
.sk-modal-table tr:last-child td { border-bottom: none; }
.sk-modal-code {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
  color: #1d1d1f;
}
.sk-modal-req {
  margin-left: 6px;
  font-size: 11px;
  color: #c2410c;
}
.sk-modal-type {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
  color: #8a8a8e;
  white-space: nowrap;
}

.sk-modal-pre {
  margin: 0;
  max-height: 260px;
  overflow: auto;
  padding: 12px 14px;
  border-radius: 10px;
  background: #f7f7f9;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
  line-height: 1.65;
  color: #3a3a3c;
  white-space: pre-wrap;
  word-break: break-word;
}

.sk-modal-foot {
  flex: none;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 10px;
  padding: 14px 22px;
  border-top: 1px solid rgba(0, 0, 0, 0.06);
  background: #fafafa;
}
.sk-modal-copy {
  border: 1px solid rgba(0, 0, 0, 0.1);
  background: #fff;
  color: #55555a;
  font-size: 13px;
  padding: 8px 16px;
  border-radius: 8px !important;
  cursor: pointer;
  transition: background 0.15s ease;
}
.sk-modal-copy:hover { background: rgba(0, 0, 0, 0.04); }
.sk-modal-enable {
  border: none;
  background: #1d1d1f;
  color: #fff;
  font-size: 13px;
  font-weight: 500;
  padding: 8px 20px;
  border-radius: 8px !important;
  cursor: pointer;
  transition: background 0.15s ease;
}
.sk-modal-enable:hover { background: #000; }

@media (prefers-reduced-motion: reduce) {
  .sk-car-card,
  .sk-card,
  .sk-toast-enter-active,
  .sk-toast-leave-active {
    transition: none;
  }
  .sk-car-card:hover,
  .sk-card:hover {
    transform: none;
  }
  .sk-refresh.is-loading svg { animation: none; }
}
</style>