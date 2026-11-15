<template>
  <div class="cp-wrap">
    <!-- 加载态 -->
    <div v-if="loading" class="cp-loading">
      <div class="cp-spinner"></div>
      <span>加载画像...</span>
    </div>

    <!-- 错误态 -->
    <div v-else-if="errorMsg" class="cp-error">
      <AlertCircle :size="18" />
      <span>{{ errorMsg }}</span>
      <button @click="loadProfile" class="cp-retry-btn">重试</button>
    </div>

    <!-- 表单 -->
    <div v-else class="cp-form">
      <section class="cp-hero">
        <div class="cp-hero-frame">
          <img
            class="cp-hero-img"
            src="/images/setting_images/3.jpg"
            alt="创作者画像"
            @error="($event.target as HTMLElement).style.display='none'"
          />
        </div>
        <div class="cp-hero-body">
          <h3 class="cp-hero-title">让 AI 更懂你</h3>
          <p class="cp-hero-desc">这些信息会写入智能体的系统提示词，影响选题方向、表达语气与内容边界</p>
          <div class="cp-hero-chips">
            <span v-for="c in headChips" :key="c" class="cp-chip-info">{{ c }}</span>
          </div>
        </div>
      </section>

      <!-- 定位与领域 -->
      <section class="cp-card">
        <header class="cp-card-head">
          <span class="cp-card-icon"><Target :size="15" :stroke-width="1.8" /></span>
          <div class="cp-card-head-text">
            <h3 class="cp-card-title">定位与领域</h3>
            <p class="cp-card-desc">决定 AI 选题与写稿的大方向</p>
          </div>
        </header>
        <div class="cp-card-body">
          <div class="cp-grid-2">
            <div class="cp-field">
              <label class="cp-label">主领域 <span class="cp-required">*</span></label>
              <select v-model="form.primary_domain" class="cp-select">
                <option v-for="d in domainOptions" :key="d.value" :value="d.value">
                  {{ d.label }}
                </option>
              </select>
            </div>
            <div class="cp-field">
              <label class="cp-label">子领域</label>
              <input
                v-model="form.sub_domain"
                class="cp-input"
                placeholder="如：AI 编程"
                maxlength="50"
              />
            </div>
          </div>
          <div class="cp-field">
            <label class="cp-label">内容调性</label>
            <div class="cp-tag-group">
              <button
                v-for="t in toneOptions"
                :key="t.value"
                type="button"
                class="cp-tag-btn"
                :class="{ 'cp-tag-active': form.tone === t.value }"
                @click="form.tone = t.value"
              >
                {{ t.label }}
              </button>
            </div>
          </div>
          <div class="cp-field">
            <label class="cp-label">视觉风格</label>
            <div class="cp-tag-group">
              <button
                v-for="v in visualOptions"
                :key="v.value"
                type="button"
                class="cp-tag-btn"
                :class="{ 'cp-tag-active': form.visual_style === v.value }"
                @click="form.visual_style = v.value"
              >
                {{ v.label }}
              </button>
            </div>
          </div>
        </div>
      </section>

      <!-- 身份与定位 -->
      <section class="cp-card">
        <header class="cp-card-head">
          <span class="cp-card-icon"><UserRound :size="15" :stroke-width="1.8" /></span>
          <div class="cp-card-head-text">
            <h3 class="cp-card-title">身份与定位</h3>
            <p class="cp-card-desc">你是谁，和别人有什么不一样</p>
          </div>
        </header>
        <div class="cp-card-body">
          <div class="cp-field">
            <label class="cp-label">身份定位</label>
            <input
              v-model="form.identity"
              class="cp-input"
              placeholder="一句话描述你的核心定位，如：AI编程领域的实战派博主"
              maxlength="200"
            />
          </div>
          <div class="cp-field">
            <label class="cp-label">差异化</label>
            <input
              v-model="form.differentiation"
              class="cp-input"
              placeholder="跟同类账号比，你的独特之处"
              maxlength="200"
            />
          </div>
          <div class="cp-field">
            <label class="cp-label">内容方向</label>
            <input
              v-model="form.content_direction"
              class="cp-input"
              placeholder="主要做什么类型的内容，如：教程+实战+工具推荐"
              maxlength="200"
            />
          </div>
        </div>
      </section>

      <!-- 受众画像 -->
      <section class="cp-card">
        <header class="cp-card-head">
          <span class="cp-card-icon"><Users :size="15" :stroke-width="1.8" /></span>
          <div class="cp-card-head-text">
            <h3 class="cp-card-title">受众画像</h3>
            <p class="cp-card-desc">内容写给谁看</p>
          </div>
        </header>
        <div class="cp-card-body">
          <div class="cp-field">
            <label class="cp-label">核心人群</label>
            <input
              v-model="form.target_audience"
              class="cp-input"
              placeholder="年龄段/职业/特征，如：25-35岁互联网从业者"
              maxlength="200"
            />
          </div>
          <div class="cp-field">
            <label class="cp-label">受众痛点</label>
            <input
              v-model="form.audience_pain_points"
              class="cp-input"
              placeholder="什么场景下会搜索/刷到你的内容"
              maxlength="200"
            />
          </div>
        </div>
      </section>

      <!-- 写作风格 -->
      <section class="cp-card">
        <header class="cp-card-head">
          <span class="cp-card-icon"><PenLine :size="15" :stroke-width="1.8" /></span>
          <div class="cp-card-head-text">
            <h3 class="cp-card-title">写作风格</h3>
            <p class="cp-card-desc">AI 落笔时的行文习惯</p>
          </div>
        </header>
        <div class="cp-card-body">
          <div class="cp-field">
            <label class="cp-label">开头结构</label>
            <div class="cp-tag-group">
              <button
                v-for="o in openingOptions"
                :key="o.value"
                type="button"
                class="cp-tag-btn"
                :class="{ 'cp-tag-active': form.opening_style === o.value }"
                @click="form.opening_style = o.value"
              >
                {{ o.label }}
              </button>
            </div>
          </div>
          <div class="cp-field">
            <label class="cp-label">内容节奏</label>
            <div class="cp-tag-group">
              <button
                v-for="r in rhythmOptions"
                :key="r.value"
                type="button"
                class="cp-tag-btn"
                :class="{ 'cp-tag-active': form.content_rhythm === r.value }"
                @click="form.content_rhythm = r.value"
              >
                {{ r.label }}
              </button>
            </div>
          </div>
          <div class="cp-field">
            <label class="cp-label">标志元素</label>
            <input
              v-model="form.signature_elements"
              class="cp-input"
              placeholder="口头禅/固定栏目/BGM等辨识度元素"
              maxlength="200"
            />
          </div>
        </div>
      </section>

      <!-- AI 人格 -->
      <section class="cp-card">
        <header class="cp-card-head">
          <span class="cp-card-icon"><Sparkles :size="15" :stroke-width="1.8" /></span>
          <div class="cp-card-head-text">
            <h3 class="cp-card-title">AI 人格</h3>
            <p class="cp-card-desc">AI 和你说话时的性格与语气</p>
          </div>
        </header>
        <div class="cp-card-body">
          <div class="cp-field">
            <label class="cp-label">人格风格</label>
            <div class="cp-tag-group">
              <button
                v-for="p in personalityOptions"
                :key="p.id"
                type="button"
                class="cp-tag-btn"
                :class="{ 'cp-tag-active': form.ai_personality === p.id }"
                @click="form.ai_personality = p.id"
              >
                {{ p.label }}
              </button>
            </div>
            <p class="cp-hint cp-hint-quote">{{ currentPersonalityDesc }}</p>
          </div>
          <div v-if="form.ai_personality === 'custom'" class="cp-field">
            <div class="cp-label-row">
              <label class="cp-label">自定义人格设定</label>
              <span class="cp-label-meta">最多 2000 字</span>
            </div>
            <textarea
              v-model="form.ai_personality_custom"
              class="cp-textarea"
              placeholder="描述你希望 AI 怎么说话、什么性格、什么语气……&#10;&#10;例如：你是一个赛博朋克风的黑客，说话带点中二但技术靠谱。喜欢用代码隐喻解释问题，偶尔蹦英文术语但会翻译。"
              maxlength="2000"
              rows="6"
            ></textarea>
          </div>
        </div>
      </section>

      <!-- 内容边界 -->
      <section class="cp-card">
        <header class="cp-card-head">
          <span class="cp-card-icon"><ShieldBan :size="15" :stroke-width="1.8" /></span>
          <div class="cp-card-head-text">
            <h3 class="cp-card-title">内容边界</h3>
            <p class="cp-card-desc">AI 不该碰的话题和用词</p>
          </div>
        </header>
        <div class="cp-card-body">
          <div class="cp-field">
            <div class="cp-label-row">
              <label class="cp-label">禁忌话题</label>
              <span class="cp-label-meta">回车添加</span>
            </div>
            <div class="cp-input-tags">
              <span v-for="(t, i) in form.taboo_topics" :key="'tp' + i" class="cp-chip cp-chip-topic">
                {{ t }}
                <button type="button" class="cp-chip-remove" @click="removeItem('taboo_topics', i)">
                  <X :size="11" />
                </button>
              </span>
              <input
                v-model="topicInput"
                class="cp-input-tag"
                placeholder="输入后回车"
                @keydown.enter.prevent="addItem('taboo_topics', topicInput); topicInput = ''"
              />
            </div>
          </div>
          <div class="cp-field">
            <div class="cp-label-row">
              <label class="cp-label">禁忌用词</label>
              <span class="cp-label-meta">回车添加</span>
            </div>
            <div class="cp-input-tags">
              <span v-for="(w, i) in form.taboo_words" :key="'tw' + i" class="cp-chip cp-chip-word">
                {{ w }}
                <button type="button" class="cp-chip-remove" @click="removeItem('taboo_words', i)">
                  <X :size="11" />
                </button>
              </span>
              <input
                v-model="wordInput"
                class="cp-input-tag"
                placeholder="输入后回车"
                @keydown.enter.prevent="addItem('taboo_words', wordInput); wordInput = ''"
              />
            </div>
          </div>
        </div>
      </section>

      <!-- 保存结果提示 -->
      <transition name="cp-fade">
        <div v-if="saveMsg" class="cp-toast" :class="{ 'cp-toast-error': saveError }">
          <Check v-if="!saveError" :size="14" />
          <AlertCircle v-else :size="14" />
          <span>{{ saveMsg }}</span>
        </div>
      </transition>

      <!-- 操作按钮（滚动时吸底） -->
      <div class="cp-actions">
        <button
          @click="handleSave"
          class="cp-save-btn"
          :disabled="!canSave || saving"
        >
          <Check :size="14" />
          <span>{{ saving ? '保存中...' : '保存画像' }}</span>
        </button>
        <button @click="resetForm" class="cp-cancel-btn">重置</button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { Check, X, AlertCircle, Target, UserRound, Users, PenLine, ShieldBan, Sparkles } from 'lucide-vue-next'
import {
  profileApi,
  type UserProfile,
  type UpdateProfileRequest,
  type PrimaryDomain,
  type CreatorTone,
  type VisualStyle,
  type PersonalityOption,
} from '@/api/profile'

const loading = ref(true)
const errorMsg = ref('')
const saving = ref(false)
const saveMsg = ref('')
const saveError = ref(false)
const topicInput = ref('')
const wordInput = ref('')

const personalityOptions = ref<PersonalityOption[]>([
  { id: 'default', label: '默认（专业简洁）', description: '靠谱、专业、不废话的 AI 搭档' },
  { id: 'spicy', label: '毒舌损友', description: '嘴毒但靠谱的朋友——损你是真损，帮你也是真帮' },
  { id: 'dramatic', label: '话痨戏精', description: '话痨戏精，情绪拉满，热情是真的专业也是真的' },
  { id: 'deadpan', label: '冷幽默', description: '一本正经地搞笑，表面严肃内核腹黑' },
  { id: 'netslang', label: '网感型', description: '网感拉满，热梗信手拈来，真的在网上冲浪的专业选手' },
  { id: 'custom', label: '自定义', description: '自己写人格设定，想怎么玩怎么玩' },
])

const domainOptions = [
  { value: 'tech' as PrimaryDomain, label: '科技' },
  { value: 'beauty' as PrimaryDomain, label: '美妆' },
  { value: 'food' as PrimaryDomain, label: '美食' },
  { value: 'travel' as PrimaryDomain, label: '旅行' },
  { value: 'education' as PrimaryDomain, label: '教育' },
  { value: 'parenting' as PrimaryDomain, label: '母婴' },
  { value: 'fitness' as PrimaryDomain, label: '健身' },
  { value: 'finance' as PrimaryDomain, label: '财经' },
  { value: 'other' as PrimaryDomain, label: '其他' },
]

const toneOptions = [
  { value: 'professional' as CreatorTone, label: '专业' },
  { value: 'friendly' as CreatorTone, label: '亲和' },
  { value: 'lively' as CreatorTone, label: '活泼' },
  { value: 'serious' as CreatorTone, label: '严肃' },
  { value: 'humorous' as CreatorTone, label: '幽默' },
]

const visualOptions = [
  { value: 'warm' as VisualStyle, label: '暖色调' },
  { value: 'cool' as VisualStyle, label: '冷色调' },
  { value: 'minimal' as VisualStyle, label: '极简' },
  { value: 'rich' as VisualStyle, label: '丰富' },
]

const openingOptions = [
  { value: 'question', label: '提问式' },
  { value: 'conflict', label: '冲突式' },
  { value: 'story', label: '故事式' },
  { value: 'direct', label: '直入主题' },
  { value: 'data', label: '数据开场' },
]

const rhythmOptions = [
  { value: 'short_fast', label: '短平快' },
  { value: 'medium', label: '中等' },
  { value: 'deep_long', label: '深度长内容' },
  { value: 'image_text', label: '图文为主' },
]

const form = ref<UpdateProfileRequest>({
  primary_domain: 'tech',
  sub_domain: null,
  tone: 'professional',
  visual_style: 'warm',
  taboo_topics: [],
  taboo_words: [],
  identity: null,
  differentiation: null,
  content_direction: null,
  target_audience: null,
  audience_pain_points: null,
  opening_style: null,
  content_rhythm: null,
  signature_elements: null,
  ai_personality: 'default',
  ai_personality_custom: null,
})

const canSave = computed(() => {
  const d = form.value.primary_domain
  return d !== null && d !== undefined && String(d).trim() !== ''
})

const currentPersonalityDesc = computed(() => {
  const cur = personalityOptions.value.find(p => p.id === form.value.ai_personality)
  return cur?.description || ''
})

const headChips = computed(() => {
  const domain = domainOptions.find(d => d.value === form.value.primary_domain)?.label
  const tone = toneOptions.find(t => t.value === form.value.tone)?.label
  const personality = personalityOptions.value.find(p => p.id === form.value.ai_personality)?.label
  return [domain && `领域 · ${domain}`, tone && `调性 · ${tone}`, personality && `人格 · ${personality}`]
    .filter((v): v is string => !!v)
})

async function loadProfile() {
  loading.value = true
  errorMsg.value = ''
  try {
    const profile = await profileApi.get()
    if (profile) {
      form.value = {
        primary_domain: profile.primary_domain,
        sub_domain: profile.sub_domain,
        tone: profile.tone,
        visual_style: profile.visual_style,
        taboo_topics: [...profile.taboo_topics],
        taboo_words: [...profile.taboo_words],
        identity: profile.identity,
        differentiation: profile.differentiation,
        content_direction: profile.content_direction,
        target_audience: profile.target_audience,
        audience_pain_points: profile.audience_pain_points,
        opening_style: profile.opening_style,
        content_rhythm: profile.content_rhythm,
        signature_elements: profile.signature_elements,
        ai_personality: profile.ai_personality || 'default',
        ai_personality_custom: profile.ai_personality_custom,
      }
    }
  } catch (e: any) {
    // 404 = 尚未设置画像，不报错
    if (e?.response?.status !== 404) {
      errorMsg.value = e?.message || '加载画像失败'
    }
  } finally {
    loading.value = false
  }
}

function addItem(field: 'taboo_topics' | 'taboo_words', value: string) {
  const v = value.trim()
  if (!v) return
  const arr = form.value[field]
  if (arr) {
    const seen = new Set(arr.map((s: string) => s.toLowerCase()))
    if (!seen.has(v.toLowerCase())) {
      arr.push(v.slice(0, 100))
    }
  }
}

function removeItem(field: 'taboo_topics' | 'taboo_words', index: number) {
  const arr = form.value[field]
  if (arr) arr.splice(index, 1)
}

function resetForm() {
  form.value = {
    primary_domain: 'tech',
    sub_domain: null,
    tone: 'professional',
    visual_style: 'warm',
    taboo_topics: [],
    taboo_words: [],
    identity: null,
    differentiation: null,
    content_direction: null,
    target_audience: null,
    audience_pain_points: null,
    opening_style: null,
    content_rhythm: null,
    signature_elements: null,
    ai_personality: 'default',
    ai_personality_custom: null,
  }
  topicInput.value = ''
  wordInput.value = ''
  saveMsg.value = ''
}

let toastTimer: ReturnType<typeof setTimeout> | null = null
function showToast(msg: string, isError = false) {
  saveMsg.value = msg
  saveError.value = isError
  if (toastTimer) clearTimeout(toastTimer)
  toastTimer = setTimeout(() => { saveMsg.value = '' }, 3000)
}

async function handleSave() {
  if (!canSave.value) return
  saving.value = true
  try {
    const payload: UpdateProfileRequest = {
      primary_domain: form.value.primary_domain,
      sub_domain: form.value.sub_domain?.trim() || null,
      tone: form.value.tone,
      visual_style: form.value.visual_style,
      taboo_topics: form.value.taboo_topics || [],
      taboo_words: form.value.taboo_words || [],
      identity: form.value.identity?.trim() || null,
      differentiation: form.value.differentiation?.trim() || null,
      content_direction: form.value.content_direction?.trim() || null,
      target_audience: form.value.target_audience?.trim() || null,
      audience_pain_points: form.value.audience_pain_points?.trim() || null,
      opening_style: form.value.opening_style || null,
      content_rhythm: form.value.content_rhythm || null,
      signature_elements: form.value.signature_elements?.trim() || null,
      ai_personality: form.value.ai_personality || 'default',
      ai_personality_custom: form.value.ai_personality === 'custom'
        ? (form.value.ai_personality_custom?.trim() || null)
        : null,
    }
    await profileApi.update(payload)
    showToast('画像已保存，后续生成将自动应用')
  } catch (e: any) {
    const detail = e?.response?.data?.detail || e?.response?.data?.message
    showToast(detail || '保存失败，请重试', true)
  } finally {
    saving.value = false
  }
}

onMounted(loadProfile)
</script>

<style scoped>
.cp-wrap {
  font-size: 13px;
  color: var(--rl-text-2);
}

/* loading */
.cp-loading {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 48px 0;
  justify-content: center;
  color: #9A9A9A;
  font-size: 13px;
}
.cp-spinner {
  width: 18px;
  height: 18px;
  border: 2px solid #EDEDED;
  border-top-color: #3B6CF6;
  border-radius: 50%;
  animation: cp-spin 0.7s linear infinite;
}
@keyframes cp-spin { to { transform: rotate(360deg); } }

/* error */
.cp-error {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 14px;
  background: #FDF4F4;
  border: 1px solid #F4DCDC;
  border-radius: 12px;
  color: #B42318;
  font-size: 13px;
}
.cp-retry-btn {
  margin-left: auto;
  padding: 4px 12px;
  border: 1px solid #E3C0BC;
  background: #FFFFFF;
  color: #B42318;
  border-radius: 8px;
  font-size: 12px;
  cursor: pointer;
  transition: background 0.15s ease;
}
.cp-retry-btn:hover { background: #FBEDEC; }

/* form */
.cp-form {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding-bottom: 2px;
}

/* 顶部封面 */
.cp-hero {
  position: relative;
  aspect-ratio: 3.2 / 1;
  max-height: 240px;
  border: 1px solid #EFEFEF;
  border-radius: 14px;
  overflow: hidden;
  background: #F3F2F0;
  box-shadow: 0 6px 18px rgba(17, 24, 39, 0.05);
}
.cp-hero-frame {
  position: absolute;
  top: -18%;
  height: 125%;
  left: 50%;
  transform: translateX(-50%);
  aspect-ratio: 1365 / 768;
}
.cp-hero-img {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: cover;
  object-position: center top;
}
.cp-hero::after {
  content: '';
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  height: 52%;
  background: linear-gradient(180deg, rgba(255,255,255,0) 0%, rgba(255,255,255,0.72) 42%, rgba(255,255,255,0.97) 100%);
  pointer-events: none;
}
.cp-hero-body {
  position: absolute;
  left: 18px;
  right: 18px;
  bottom: 13px;
  z-index: 1;
  display: flex;
  flex-direction: column;
  gap: 7px;
}
.cp-hero-title {
  margin: 0;
  color: #1A1A1A;
  font-size: 15px;
  font-weight: 500;
  letter-spacing: -0.01em;
}
.cp-hero-desc {
  margin: 0;
  color: #6B6B6B;
  font-size: 13px;
  line-height: 1.5;
}
.cp-hero-chips { display: flex; flex-wrap: wrap; gap: 6px; }
.cp-chip-info {
  display: inline-flex;
  align-items: center;
  padding: 3px 9px;
  border-radius: 999px;
  background: #EEF3FE;
  color: #2F5BE0;
  font-size: 12px;
  font-weight: 500;
}

/* 分组卡片 */
.cp-card {
  border: 1px solid #EFEFEF;
  border-radius: 14px;
  background: #FFFFFF;
  overflow: hidden;
}
.cp-card-head {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 13px 14px 11px;
}
.cp-card-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  flex: none;
  border-radius: 8px;
  background: #EEF3FE;
  color: #3B6CF6;
}
.cp-card-head-text { min-width: 0; }
.cp-card-title {
  margin: 0;
  font-size: 13px;
  font-weight: 500;
  color: #1A1A1A;
  line-height: 1.3;
}
.cp-card-desc {
  margin: 1px 0 0;
  font-size: 12px;
  color: #9A9A9A;
  line-height: 1.4;
}
.cp-card-body {
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 0 14px 14px;
}

/* 字段 */
.cp-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}
.cp-grid-2 {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}
.cp-label {
  font-size: 13px;
  font-weight: 500;
  color: #1A1A1A;
  line-height: 1.3;
}
.cp-label-row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.cp-label-meta {
  margin-left: auto;
  font-size: 11px;
  font-weight: 400;
  color: #B2B2B7;
}
.cp-required {
  color: #E5484D;
  font-weight: 500;
}
.cp-hint {
  margin: 0;
  font-size: 12px;
  color: #9A9A9A;
  line-height: 1.5;
}
.cp-hint-quote {
  display: flex;
  align-items: flex-start;
  gap: 7px;
  padding: 7px 10px;
  border-radius: 8px;
  background: #F7F7F8;
  color: #6E6E73;
}
.cp-hint-quote::before {
  content: '';
  flex: none;
  align-self: stretch;
  width: 2px;
  border-radius: 2px;
  background: #3B6CF6;
}

/* select */
.cp-select {
  height: 36px;
  width: 100%;
  padding: 0 30px 0 11px;
  border: 1px solid #E8E8E8;
  border-radius: 9px;
  font-size: 13px;
  font-family: inherit;
  color: #1A1A1A;
  background-color: #FAFAFA;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='%239A9A9A' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpolyline points='6 9 12 15 18 9'/%3E%3C/svg%3E");
  background-repeat: no-repeat;
  background-position: right 10px center;
  appearance: none;
  -webkit-appearance: none;
  cursor: pointer;
  outline: none;
  transition: border-color 0.16s ease, background-color 0.16s ease, box-shadow 0.16s ease;
}
.cp-select:hover { border-color: #DCDCDC; }

/* input */
.cp-input {
  height: 36px;
  width: 100%;
  padding: 0 11px;
  border: 1px solid #E8E8E8;
  border-radius: 9px;
  font-size: 13px;
  font-family: inherit;
  color: #1A1A1A;
  background: #FAFAFA;
  outline: none;
  transition: border-color 0.16s ease, background-color 0.16s ease, box-shadow 0.16s ease;
}
.cp-input:hover,
.cp-textarea:hover { border-color: #DCDCDC; }
.cp-input::placeholder,
.cp-textarea::placeholder { color: #B8B8BD; }
.cp-select:focus,
.cp-input:focus,
.cp-textarea:focus {
  background: #FFFFFF;
  border-color: #3B6CF6;
  box-shadow: 0 0 0 3px rgba(59, 108, 246, 0.12);
}

/* textarea */
.cp-textarea {
  width: 100%;
  padding: 9px 11px;
  border: 1px solid #E8E8E8;
  border-radius: 9px;
  font-size: 13px;
  font-family: inherit;
  color: #1A1A1A;
  background: #FAFAFA;
  outline: none;
  resize: vertical;
  min-height: 96px;
  line-height: 1.6;
  transition: border-color 0.16s ease, background-color 0.16s ease, box-shadow 0.16s ease;
}

/* tag group */
.cp-tag-group {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.cp-tag-btn {
  padding: 5px 11px;
  border: 1px solid #E8E8E8;
  background: #FAFAFA;
  color: #4B4B4B;
  border-radius: 8px;
  font-size: 12px;
  font-family: inherit;
  white-space: nowrap;
  cursor: pointer;
  transition: color 0.16s ease, border-color 0.16s ease, background-color 0.16s ease;
}
.cp-tag-btn:hover {
  border-color: rgba(59, 108, 246, 0.4);
  background: #F7F9FE;
  color: #2F5BE0;
}
.cp-tag-active,
.cp-tag-active:hover {
  border-color: #3B6CF6;
  background: #3B6CF6;
  color: #FFFFFF;
  font-weight: 500;
}

/* input with chips */
.cp-input-tags {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  min-height: 36px;
  padding: 5px 8px;
  border: 1px solid #E8E8E8;
  border-radius: 9px;
  background: #FAFAFA;
  transition: border-color 0.16s ease, background-color 0.16s ease, box-shadow 0.16s ease;
}
.cp-input-tags:hover { border-color: #DCDCDC; }
.cp-input-tags:focus-within {
  background: #FFFFFF;
  border-color: #3B6CF6;
  box-shadow: 0 0 0 3px rgba(59, 108, 246, 0.12);
}
.cp-input-tag {
  flex: 1;
  min-width: 96px;
  padding: 3px 4px;
  border: none;
  outline: none;
  font-size: 13px;
  font-family: inherit;
  color: #1A1A1A;
  background: transparent;
}
.cp-input-tag::placeholder { color: #B8B8BD; }
.cp-chip {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  padding: 3px 5px 3px 8px;
  border-radius: 7px;
  font-size: 12px;
  font-weight: 500;
  line-height: 1.5;
}
.cp-chip-topic {
  background: #FDF4E5;
  color: #8A6216;
}
.cp-chip-word {
  background: #FBEDEC;
  color: #9B2C2C;
}
.cp-chip-remove {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 15px;
  height: 15px;
  padding: 0;
  border: none;
  border-radius: 4px;
  background: transparent;
  color: inherit;
  cursor: pointer;
  opacity: 0.55;
  transition: opacity 0.15s ease, background-color 0.15s ease;
}
.cp-chip-remove:hover {
  opacity: 1;
  background: rgba(0, 0, 0, 0.07);
}

/* actions：滚动时吸底 */
.cp-actions {
  position: sticky;
  bottom: 6px;
  z-index: 2;
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 2px;
  padding: 9px 10px;
  border: 1px solid #EFEFEF;
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.9);
  backdrop-filter: blur(10px) saturate(140%);
  box-shadow: 0 -2px 14px rgba(0, 0, 0, 0.05);
}
.cp-save-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  flex: 1;
  height: 34px;
  padding: 0 18px;
  border: none;
  background: #3B6CF6;
  color: #FFFFFF;
  font-size: 13px;
  font-weight: 500;
  font-family: inherit;
  border-radius: 9px;
  box-shadow: 0 3px 10px rgba(59, 108, 246, 0.22);
  cursor: pointer;
  transition: background-color 0.16s ease, box-shadow 0.16s ease;
}
.cp-save-btn:hover { background: #2F5BE0; box-shadow: 0 5px 14px rgba(59, 108, 246, 0.28); }
.cp-save-btn:disabled {
  background: #EDEDED;
  color: #A1A1A6;
  box-shadow: none;
  cursor: not-allowed;
}
.cp-cancel-btn {
  height: 34px;
  padding: 0 16px;
  border: 1px solid #E8E8E8;
  background: #FFFFFF;
  color: #6B7280;
  font-size: 13px;
  font-family: inherit;
  border-radius: 9px;
  cursor: pointer;
  transition: background-color 0.16s ease, color 0.16s ease;
}
.cp-cancel-btn:hover {
  background: #F5F5F5;
  color: #1A1A1A;
}

/* toast */
.cp-toast {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 9px 12px;
  background: #EEF3FE;
  color: #2F5BE0;
  border-radius: 10px;
  font-size: 12px;
}
.cp-toast-error {
  background: #FDF4F4;
  color: #B42318;
}
.cp-fade-enter-active,
.cp-fade-leave-active {
  transition: opacity 0.2s ease;
}
.cp-fade-enter-from,
.cp-fade-leave-to {
  opacity: 0;
}
</style>