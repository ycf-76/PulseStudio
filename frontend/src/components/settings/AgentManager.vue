<template>
  <div class="am-wrap">
    <section class="am-hero">
      <img
        class="am-hero-img"
        src="/images/setting_images/img.webp"
        alt="智能体管理"
        @error="($event.target as HTMLElement).style.display='none'"
      />
      <div class="am-hero-body">
        <h3 class="am-hero-title">把你的工作交给一支 AI 团队</h3>
        <p class="am-hero-desc">每个智能体负责一类任务，模型、技能与提示词都可以单独配置</p>
        <div class="am-hero-chips">
          <span class="am-hero-chip">模型可切换</span>
          <span class="am-hero-chip">技能可装配</span>
          <span class="am-hero-chip">提示词可调</span>
        </div>
      </div>
    </section>

    <div class="am-create">
      <header class="am-create-head">
        <div class="am-create-head-text">
          <h3 class="am-create-name">创建智能体</h3>
          <p class="am-create-desc">配置模型、执行器与技能，接入一个新的 Agent</p>
        </div>
        <button
          @click="showCreateForm = !showCreateForm"
          class="am-toggle-btn"
          :class="{ 'am-toggle-open': showCreateForm }"
        >
          <ChevronUp v-if="showCreateForm" :size="14" />
          <ChevronDown v-else :size="14" />
          <span>{{ showCreateForm ? '收起' : '新建' }}</span>
        </button>
      </header>

      <transition name="am-slide">
        <div v-if="showCreateForm" class="am-form">
          <div class="am-grid-2">
            <div class="am-field">
              <label class="am-label">ID <span class="am-required">*</span></label>
              <input v-model="form.agent_id" class="am-input" placeholder="如：my_expert" />
            </div>
            <div class="am-field">
              <label class="am-label">角色</label>
              <input v-model="form.role" class="am-input" placeholder="如：美食分析专家" />
            </div>
          </div>
          <div class="am-grid-2">
            <div class="am-field">
              <label class="am-label">LLM 模型</label>
              <select v-model="form.llm_model" class="am-select">
                <option v-for="m in llmSelectOptions" :key="m.id" :value="m.id">{{ m.name }}</option>
              </select>
              <p class="am-hint">也可在「模型配置」页统一为智能体指定模型，运行时优先生效。</p>
            </div>
            <div class="am-field">
              <label class="am-label">执行器</label>
              <select v-model="form.executor" class="am-select">
                <option value="loop">Loop (多轮思考)</option>
                <option value="single_shot">Single Shot (一次出结果)</option>
              </select>
            </div>
          </div>
          <div class="am-field am-field-inline" v-if="form.executor === 'loop'">
            <label class="am-label">最大迭代</label>
            <input v-model.number="form.max_iterations" type="number" min="1" max="20" class="am-input am-input-sm" />
          </div>
          <div class="am-field">
            <div class="am-label-row">
              <label class="am-label">选择技能</label>
              <span class="am-label-meta">已选 {{ form.skills.length }}</span>
            </div>
            <div v-if="availableSkills.length" class="am-skill-grid">
              <button
                v-for="s in availableSkills"
                :key="s.name"
                type="button"
                class="am-skill-chip"
                :class="{ 'am-skill-chip-on': form.skills.includes(s.name) }"
                @click="toggleSkill(form.skills, s.name)"
              >
                {{ s.display_name || s.name }}
              </button>
            </div>
            <p v-else class="am-hint">暂无可选技能</p>
          </div>
          <div class="am-field">
            <label class="am-label">系统提示词</label>
            <textarea v-model="form.prompt_template" class="am-textarea" rows="4" placeholder="你是一个xxx专家，请..."></textarea>
          </div>
          <div class="am-form-actions">
            <button @click="handleCreate" class="am-primary-btn" :disabled="!canCreate">
              <Check :size="14" />
              <span>创建</span>
            </button>
            <button @click="resetForm" class="am-cancel-btn">重置</button>
          </div>
        </div>
      </transition>
    </div>

    <div class="am-list-section">
      <div class="am-section-header">
        <div class="am-section-head-text">
          <div class="am-section-title">已注册智能体</div>
          <p class="am-section-desc">内置与自定义智能体都会在这里展示，可直接编辑模型、技能与提示词</p>
        </div>
        <span class="am-section-count">{{ agents.length }} 个在线</span>
      </div>

      <div v-if="loading" class="am-loading">
        <div class="am-spinner"></div>
        <span>扫描智能体网络...</span>
      </div>

      <div v-else-if="errorMsg" class="am-error">
        <AlertCircle :size="18" />
        <span>{{ errorMsg }}</span>
        <button @click="loadAgents" class="am-retry-btn">重试</button>
      </div>

      <div v-else-if="agents.length === 0" class="am-empty">
        <div class="am-empty-orb">
          <Bot :size="28" />
        </div>
        <p>暂无智能体</p>
        <span class="am-empty-hint">在左侧创建你的第一个 AI Agent</span>
      </div>

      <div v-else class="am-grid">
        <article
          v-for="a in agents"
          :key="a.agent_id"
          class="am-card"
        >
          <header class="am-card-header">
            <div class="am-card-info">
              <div class="am-card-title-row">
                <span class="am-id-text">{{ a.agent_id }}</span>
                <span class="am-card-badge" :class="a.is_builtin ? 'am-badge-builtin' : 'am-badge-custom'">
                  {{ a.is_builtin ? '内置' : '自定义' }}
                </span>
              </div>
              <p class="am-card-role" :class="{ 'am-card-role-empty': !a.role }">{{ a.role || '无角色描述' }}</p>
            </div>
            <div class="am-card-actions">
              <button @click="openEdit(a)" class="am-card-action" title="编辑">
                <Pencil :size="13" />
              </button>
              <button
                v-if="!a.is_builtin"
                @click="handleDelete(a.agent_id)"
                class="am-card-action am-card-action-danger"
                title="删除"
              >
                <Trash2 :size="13" />
              </button>
            </div>
          </header>

          <div class="am-card-meta">
            <span class="am-meta-chip">
              <Cpu :size="11" />
              {{ a.llm_model || 'default' }}
            </span>
            <span class="am-meta-chip">
              <Zap :size="11" />
              {{ a.executor === 'loop' ? '多轮思考' : '单次执行' }}
            </span>
            <span class="am-meta-chip" v-if="a.executor === 'loop'">
              <RefreshCw :size="11" />
              ≤{{ a.max_iterations }} 轮
            </span>
          </div>

          <div class="am-card-skills">
            <span v-for="s in a.skills.slice(0, 4)" :key="s" class="am-skill-tag">{{ s }}</span>
            <span v-if="a.skills.length > 4" class="am-skill-tag am-more-tag">+{{ a.skills.length - 4 }}</span>
            <span v-if="a.skills.length === 0" class="am-skill-tag am-empty-tag">无技能</span>
          </div>
        </article>
      </div>
    </div>

    <teleport to="body">
      <div v-if="editingAgent" class="am-modal-overlay" @click.self="closeEdit">
        <div class="am-modal">
          <div class="am-modal-header">
            <div class="am-modal-title-group">
              <h3>编辑智能体</h3>
              <span class="am-modal-id">{{ editingAgent.agent_id }}</span>
            </div>
            <button @click="closeEdit" class="am-modal-close"><X :size="16" /></button>
          </div>
          <div class="am-modal-body">
            <div class="am-field">
              <label class="am-label">角色</label>
              <input v-model="editForm.role" class="am-input" />
            </div>
            <div class="am-grid-2">
              <div class="am-field">
                <label class="am-label">LLM 模型</label>
                <select v-model="editForm.llm_model" class="am-select">
                  <option v-for="m in llmSelectOptions" :key="m.id" :value="m.id">{{ m.name }}</option>
                </select>
              </div>
              <div class="am-field">
                <label class="am-label">执行器</label>
                <select v-model="editForm.executor" class="am-select">
                  <option value="loop">Loop (多轮思考)</option>
                  <option value="single_shot">Single Shot (一次出结果)</option>
                </select>
              </div>
            </div>
            <div class="am-field am-field-inline" v-if="editForm.executor === 'loop'">
              <label class="am-label">最大迭代</label>
              <input v-model.number="editForm.max_iterations" type="number" min="1" max="20" class="am-input am-input-sm" />
            </div>
            <div class="am-field">
              <div class="am-label-row">
                <label class="am-label">选择技能</label>
                <span class="am-label-meta">已选 {{ editForm.skills.length }}</span>
              </div>
              <div v-if="availableSkills.length" class="am-skill-grid">
                <button
                  v-for="s in availableSkills"
                  :key="s.name"
                  type="button"
                  class="am-skill-chip"
                  :class="{ 'am-skill-chip-on': editForm.skills.includes(s.name) }"
                  @click="toggleSkill(editForm.skills, s.name)"
                >
                  {{ s.display_name || s.name }}
                </button>
              </div>
              <p v-else class="am-hint">暂无可选技能</p>
            </div>
            <div class="am-field">
              <label class="am-label">系统提示词</label>
              <textarea v-model="editForm.prompt_template" class="am-textarea" rows="6"></textarea>
            </div>
          </div>
          <div class="am-modal-footer">
            <button @click="handleUpdate" class="am-primary-btn" :disabled="saving">
              <Check :size="14" />
              <span>{{ saving ? '保存中...' : '保存' }}</span>
            </button>
            <button @click="handleSwitchLlm" class="am-switch-btn" :disabled="saving" v-if="editForm.llm_model !== editingAgent?.llm_model">
              <Zap :size="14" />
              <span>切换 LLM</span>
            </button>
            <button @click="closeEdit" class="am-cancel-btn">取消</button>
          </div>
        </div>
      </div>
    </teleport>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import {
  ChevronUp, ChevronDown, X, Check,
  Pencil, Trash2, Bot, AlertCircle, Cpu, Zap, RefreshCw,
} from 'lucide-vue-next'
import { agentsApi, type AgentSummary, type AgentUpdateRequest } from '@/api/agents'
import { fetchConfiguredModels, type AvailableModel } from '@/components/chat/composables/modelRegistry'
interface SkillMeta { node_type: string; name: string; display_name: string; description?: string; tags?: string[]; is_third_party?: boolean; is_prompt_skill?: boolean }

interface FlatSkill {
  name: string
  display_name: string
  node_type: string
}

const agents = ref<AgentSummary[]>([])
const loading = ref(true)
const errorMsg = ref('')
const saving = ref(false)
const showCreateForm = ref(true)
const editingAgent = ref<AgentSummary | null>(null)
const availableSkills = ref<FlatSkill[]>([])
const llmOptions = ref<AvailableModel[]>([])

const FALLBACK_LLM_OPTIONS: AvailableModel[] = [
  { id: 'deepseek-v3', name: 'DeepSeek V3', provider: 'deepseek', providerName: 'DeepSeek', description: '', icon: '', has_api_key: true, is_active: true, source: 'global' },
  { id: 'deepseek-r1', name: 'DeepSeek R1', provider: 'deepseek', providerName: 'DeepSeek', description: '', icon: '', has_api_key: true, is_active: true, source: 'global' },
  { id: 'mock-test', name: 'Mock (测试)', provider: 'mock', providerName: 'Mock', description: '', icon: '', has_api_key: true, is_active: true, source: 'global' },
]

const llmSelectOptions = computed(() => {
  const base = llmOptions.value.length ? llmOptions.value : FALLBACK_LLM_OPTIONS
  const extra: AvailableModel[] = []
  for (const value of [form.value.llm_model, editForm.value.llm_model]) {
    if (value && !base.some((m) => m.id === value) && !extra.some((m) => m.id === value)) {
      extra.push({
        id: value,
        name: value,
        provider: '',
        providerName: '',
        description: '',
        icon: '',
        has_api_key: true,
        is_active: true,
        source: 'global',
      })
    }
  }
  return [...base, ...extra]
})

const form = ref({
  agent_id: '',
  role: '',
  llm_model: 'deepseek-v3',
  executor: 'loop',
  max_iterations: 5,
  skills: [] as string[],
  prompt_template: '',
})

const editForm = ref<AgentUpdateRequest & { skills: string[]; executor: string; max_iterations: number; llm_model: string; prompt_template: string }>({
  role: '',
  llm_model: 'deepseek-v3',
  skills: [],
  executor: 'loop',
  max_iterations: 5,
  prompt_template: '',
})

const canCreate = computed(() => form.value.agent_id.trim() && form.value.skills.length > 0)

function resetForm() {
  form.value = { agent_id: '', role: '', llm_model: 'deepseek-v3', executor: 'loop', max_iterations: 5, skills: [], prompt_template: '' }
}

function toggleSkill(list: string[], name: string) {
  const i = list.indexOf(name)
  if (i >= 0) list.splice(i, 1)
  else list.push(name)
}

async function loadAgents() {
  loading.value = true
  errorMsg.value = ''
  try {
    agents.value = await agentsApi.list()
  } catch (e: any) {
    errorMsg.value = e?.message || '加载失败'
  } finally {
    loading.value = false
  }
}

async function loadSkills() {
  try {
    availableSkills.value = []
  } catch {
    // ignore
  }
}

async function handleCreate() {
  if (!canCreate.value) return
  try {
    const resp = await agentsApi.create({
      agent_id: form.value.agent_id.trim(),
      role: form.value.role.trim() || undefined,
      llm_model: form.value.llm_model,
      skills: form.value.skills,
      executor: form.value.executor,
      max_iterations: form.value.max_iterations,
      prompt_template: form.value.prompt_template.trim() || undefined,
    })
    if (resp.success) {
      resetForm()
      showCreateForm.value = false
      await loadAgents()
    } else {
      alert(resp.message || '创建失败')
    }
  } catch (e: any) {
    alert(e?.response?.data?.detail || e?.message || '创建失败')
  }
}

function openEdit(a: AgentSummary) {
  editingAgent.value = a
  editForm.value = {
    role: a.role,
    llm_model: a.llm_model || 'deepseek-v3',
    skills: [...a.skills],
    executor: a.executor,
    max_iterations: a.max_iterations,
    prompt_template: a.prompt_template,
  }
}

function closeEdit() {
  editingAgent.value = null
}

async function handleUpdate() {
  if (!editingAgent.value) return
  saving.value = true
  try {
    const data: AgentUpdateRequest = {}
    if (editForm.value.role !== undefined) data.role = editForm.value.role
    if (editForm.value.llm_model !== undefined) data.llm_model = editForm.value.llm_model
    if (editForm.value.skills !== undefined) data.skills = editForm.value.skills
    if (editForm.value.executor !== undefined) data.executor = editForm.value.executor
    if (editForm.value.max_iterations !== undefined) data.max_iterations = editForm.value.max_iterations
    if (editForm.value.prompt_template !== undefined) data.prompt_template = editForm.value.prompt_template

    const resp = await agentsApi.update(editingAgent.value.agent_id, data)
    if (resp.success) {
      closeEdit()
      await loadAgents()
    } else {
      alert(resp.message || '更新失败')
    }
  } catch (e: any) {
    alert(e?.response?.data?.detail || e?.message || '更新失败')
  } finally {
    saving.value = false
  }
}

async function handleSwitchLlm() {
  if (!editingAgent.value || !editForm.value.llm_model) return
  saving.value = true
  try {
    const resp = await agentsApi.switchLlm(editingAgent.value.agent_id, editForm.value.llm_model)
    if (resp.success) {
      closeEdit()
      await loadAgents()
    } else {
      alert(resp.message || '切换失败')
    }
  } catch (e: any) {
    alert(e?.response?.data?.detail || e?.message || '切换失败')
  } finally {
    saving.value = false
  }
}

async function handleDelete(agentId: string) {
  if (!confirm(`确定删除智能体「${agentId}」？`)) return
  try {
    await agentsApi.delete(agentId)
    await loadAgents()
  } catch (e: any) {
    alert(e?.response?.data?.detail || e?.message || '删除失败')
  }
}

async function loadLlmOptions() {
  try {
    llmOptions.value = await fetchConfiguredModels()
  } catch {
    llmOptions.value = []
  }
}

onMounted(() => {
  loadAgents()
  loadSkills()
  loadLlmOptions()
})
</script>

<style scoped>
.am-wrap {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 2px 0;
  font-size: 13px;
  color: var(--rl-text-2);
}

/* 顶部介绍：整图铺底，文案压在下缘 */
.am-hero {
  position: relative;
  aspect-ratio: 3.2 / 1;
  max-height: 240px;
  border: 1px solid #EFEFEF;
  border-radius: 14px;
  overflow: hidden;
  background: #F7F8FA;
  box-shadow: 0 6px 18px rgba(17, 24, 39, 0.05);
}
.am-hero-img {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
  object-position: center 65%;
}
.am-hero::after {
  content: '';
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  height: 116px;
  background: linear-gradient(180deg, rgba(255, 255, 255, 0) 0%, rgba(255, 255, 255, 0.92) 30%, #FFFFFF 100%);
  pointer-events: none;
}
.am-hero-body {
  position: absolute;
  left: 18px;
  right: 18px;
  bottom: 13px;
  z-index: 1;
  display: flex;
  flex-direction: column;
  gap: 7px;
}
.am-hero-title {
  margin: 0;
  color: #1A1A1A;
  font-size: 15px;
  font-weight: 500;
  letter-spacing: -0.01em;
}
.am-hero-desc {
  margin: 0;
  color: #6B6B6B;
  font-size: 13px;
  line-height: 1.5;
}
.am-hero-chips { display: flex; flex-wrap: wrap; gap: 6px; }
@media (max-width: 560px) {
  .am-hero { aspect-ratio: auto; height: 156px; }
}
.am-hero-chip {
  display: inline-flex;
  align-items: center;
  padding: 3px 9px;
  border-radius: 999px;
  background: #EEF3FE;
  color: #2F5BE0;
  font-size: 12px;
  font-weight: 500;
}

/* 创建区 */
.am-create {
  border: 1px solid #EFEFEF;
  border-radius: 14px;
  background: #FFFFFF;
  overflow: hidden;
}
.am-create-head {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 14px 15px;
}
.am-create-head-text {
  flex: 1;
  min-width: 0;
}
.am-create-name {
  margin: 0;
  font-size: 13px;
  font-weight: 500;
  color: #1A1A1A;
  line-height: 1.3;
}
.am-create-desc {
  margin: 1px 0 0;
  font-size: 12px;
  color: #9A9A9A;
  line-height: 1.4;
}
.am-toggle-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  flex: none;
  height: 28px;
  padding: 0 10px;
  border: 1px solid #EFEFEF;
  border-radius: 9px;
  background: #FAFAFB;
  color: #6B7280;
  font-size: 12px;
  font-family: inherit;
  cursor: pointer;
  transition: background-color 0.16s ease, color 0.16s ease, border-color 0.16s ease;
}
.am-toggle-btn:hover { background: #F1F3F7; color: #1A1A1A; }
.am-toggle-open { border-color: rgba(59, 108, 246, 0.35); background: #EEF3FE; color: #2F5BE0; }

.am-form {
  display: flex;
  flex-direction: column;
  gap: 14px;
  margin: 0 15px 15px;
  padding-top: 14px;
  border-top: 1px dashed #EFEFEF;
}
.am-grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.am-field { display: flex; flex-direction: column; gap: 6px; min-width: 0; }
.am-field-inline { flex-direction: row; align-items: center; gap: 10px; }
.am-label-row { display: flex; align-items: center; gap: 8px; }
.am-label-meta {
  margin-left: auto;
  font-size: 11px;
  font-weight: 400;
  color: #B2B2B7;
}
.am-label { font-size: 13px; color: #1A1A1A; font-weight: 500; line-height: 1.3; }
.am-required { color: #E5484D; font-weight: 500; }
.am-hint { margin: 0; font-size: 12px; color: #9A9A9A; }

.am-input,
.am-select,
.am-textarea {
  width: 100%;
  padding: 0 11px;
  height: 36px;
  border: 1px solid #E8E8E8;
  border-radius: 9px;
  font-size: 13px;
  font-family: inherit;
  color: #1A1A1A;
  background: #FAFAFA;
  outline: none;
  transition: border-color 0.16s ease, background-color 0.16s ease, box-shadow 0.16s ease;
}
.am-input:hover,
.am-select:hover,
.am-textarea:hover { border-color: #DCDCDC; }
.am-input::placeholder,
.am-textarea::placeholder { color: #B8B8BD; }
.am-input:focus,
.am-select:focus,
.am-textarea:focus {
  background: #FFFFFF;
  border-color: #3B6CF6;
  box-shadow: 0 0 0 3px rgba(59, 108, 246, 0.12);
}
.am-select {
  padding-right: 30px;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='%239A9A9A' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpolyline points='6 9 12 15 18 9'/%3E%3C/svg%3E");
  background-repeat: no-repeat;
  background-position: right 10px center;
  appearance: none;
  -webkit-appearance: none;
  cursor: pointer;
}
.am-input-sm { width: 84px; }
.am-textarea {
  height: auto;
  min-height: 88px;
  padding: 9px 11px;
  line-height: 1.6;
  resize: vertical;
}

/* 技能选择 */
.am-skill-grid { display: flex; flex-wrap: wrap; gap: 6px; }
.am-skill-chip {
  padding: 5px 11px;
  border: 1px solid #E8E8E8;
  border-radius: 8px;
  background: #FAFAFA;
  color: #4B4B4B;
  font-size: 12px;
  font-family: inherit;
  white-space: nowrap;
  cursor: pointer;
  transition: color 0.16s ease, border-color 0.16s ease, background-color 0.16s ease;
}
.am-skill-chip:hover {
  border-color: rgba(59, 108, 246, 0.4);
  background: #F7F9FE;
  color: #2F5BE0;
}
.am-skill-chip-on,
.am-skill-chip-on:hover {
  border-color: #3B6CF6;
  background: #3B6CF6;
  color: #FFFFFF;
  font-weight: 500;
}

/* 按钮 */
.am-form-actions { display: flex; align-items: center; gap: 8px; padding-top: 2px; }
.am-primary-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  height: 34px;
  padding: 0 18px;
  border: none;
  border-radius: 9px;
  background: #3B6CF6;
  color: #FFFFFF;
  font-size: 13px;
  font-weight: 500;
  font-family: inherit;
  box-shadow: 0 3px 10px rgba(59, 108, 246, 0.22);
  cursor: pointer;
  transition: background-color 0.16s ease, box-shadow 0.16s ease;
}
.am-primary-btn:hover:not(:disabled) {
  background: #2F5BE0;
  box-shadow: 0 5px 14px rgba(59, 108, 246, 0.28);
}
.am-primary-btn:disabled { background: #EDEDED; color: #A1A1A6; box-shadow: none; cursor: not-allowed; }
.am-cancel-btn {
  height: 34px;
  padding: 0 16px;
  border: 1px solid #E8E8E8;
  border-radius: 9px;
  background: #FFFFFF;
  color: #6B7280;
  font-size: 13px;
  font-family: inherit;
  cursor: pointer;
  transition: background-color 0.16s ease, color 0.16s ease;
}
.am-cancel-btn:hover { background: #F5F5F5; color: #1A1A1A; }
.am-switch-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  height: 36px;
  padding: 0 16px;
  border: 1px solid #F0E0C0;
  border-radius: 9px;
  background: #FDF8EC;
  color: #8A6216;
  font-size: 13px;
  font-weight: 500;
  font-family: inherit;
  cursor: pointer;
  transition: background-color 0.16s ease;
}
.am-switch-btn:hover:not(:disabled) { background: #FAF0D8; }
.am-switch-btn:disabled { opacity: 0.6; cursor: not-allowed; }

/* 列表区 */
.am-list-section {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.am-section-header {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 10px;
}
.am-section-head-text { min-width: 0; }
.am-section-title {
  font-size: 13px;
  font-weight: 500;
  color: #1A1A1A;
  line-height: 1.3;
}
.am-section-desc {
  margin: 1px 0 0;
  font-size: 12px;
  color: #9A9A9A;
  line-height: 1.4;
}
.am-section-count {
  flex: none;
  padding: 3px 9px;
  border-radius: 999px;
  background: #EEF3FE;
  color: #2F5BE0;
  font-size: 12px;
  font-weight: 500;
}

.am-loading,
.am-error,
.am-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 36px 20px;
  color: #9A9A9A;
  font-size: 13px;
}
.am-spinner {
  width: 18px;
  height: 18px;
  border: 2px solid #EDEDED;
  border-top-color: #3B6CF6;
  border-radius: 50%;
  animation: am-spin 0.7s linear infinite;
}
@keyframes am-spin { to { transform: rotate(360deg); } }
.am-error {
  flex-direction: row;
  justify-content: center;
  color: #B42318;
  background: #FDF4F4;
  border: 1px solid #F4DCDC;
  border-radius: 12px;
  padding: 12px 14px;
}
.am-retry-btn {
  margin-left: auto;
  padding: 4px 12px;
  border: 1px solid #E3C0BC;
  border-radius: 8px;
  background: #FFFFFF;
  color: #B42318;
  font-size: 12px;
  font-family: inherit;
  cursor: pointer;
  transition: background-color 0.15s ease;
}
.am-retry-btn:hover { background: #FBEDEC; }

.am-empty-orb {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 44px;
  height: 44px;
  margin-bottom: 2px;
  border-radius: 50%;
  background: #F5F5F6;
  color: #B2B2B7;
}
.am-empty p { margin: 0; font-size: 13px; color: #4B4B4B; }
.am-empty-hint { font-size: 12px; color: #B2B2B7; }

.am-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: 10px;
}

.am-card {
  padding: 13px 14px;
  border: 1px solid #EFEFEF;
  border-radius: 14px;
  background: #FFFFFF;
  transition: border-color 0.18s ease, box-shadow 0.18s ease, transform 0.18s ease;
}
.am-card:hover {
  border-color: #DCE4F7;
  box-shadow: 0 10px 26px rgba(59, 108, 246, 0.08);
  transform: translateY(-1px);
}

.am-card-header { display: flex; align-items: flex-start; gap: 10px; margin-bottom: 10px; }

.am-card-info { flex: 1; min-width: 0; }
.am-card-title-row { display: flex; align-items: center; gap: 6px; margin-bottom: 3px; }
.am-id-text {
  overflow: hidden;
  font-size: 13px;
  font-weight: 500;
  color: #1A1A1A;
  font-family: 'SF Mono', 'Fira Code', 'Consolas', monospace;
  letter-spacing: -0.01em;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.am-card-badge {
  flex: none;
  padding: 1px 6px;
  border-radius: 20px;
  font-size: 10px;
  font-weight: 500;
  line-height: 1.5;
}
.am-badge-builtin { background: #EEF3FE; color: #2F5BE0; }
.am-badge-custom { background: #F5F5F6; color: #6B7280; }
.am-card-role {
  margin: 0;
  overflow: hidden;
  font-size: 12px;
  color: #6B7280;
  line-height: 1.4;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.am-card-role-empty { color: #B2B2B7; }

.am-card-actions { display: flex; gap: 2px; flex: none; opacity: 0; transition: opacity 0.15s ease; }
.am-card:hover .am-card-actions { opacity: 1; }
.am-card-action {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  padding: 0;
  border: none;
  border-radius: 7px;
  background: transparent;
  color: #9A9A9A;
  cursor: pointer;
  transition: background-color 0.15s ease, color 0.15s ease;
}
.am-card-action:hover { background: #F5F5F5; color: #4B4B4B; }
.am-card-action-danger:hover { background: #FBEDEC; color: #B42318; }

.am-card-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
  padding-top: 9px;
  margin-bottom: 9px;
  border-top: 1px solid #F5F5F6;
}
.am-meta-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 7px;
  border-radius: 7px;
  background: #FAFAFA;
  color: #6B7280;
  font-size: 11px;
  line-height: 1.6;
}

.am-card-skills { display: flex; flex-wrap: wrap; gap: 4px; }
.am-skill-tag {
  padding: 2px 7px;
  border-radius: 20px;
  background: #F5F5F6;
  color: #6B7280;
  font-size: 11px;
  line-height: 1.7;
}
.am-more-tag { background: #EEF3FE; color: #2F5BE0; }
.am-empty-tag { background: transparent; color: #C4C4C9; }

/* 编辑弹窗 */
.am-modal-overlay {
  position: fixed;
  inset: 0;
  z-index: 10002;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(0, 0, 0, 0.28);
  backdrop-filter: blur(3px);
}
.am-modal {
  display: flex;
  flex-direction: column;
  width: 560px;
  max-width: 92vw;
  max-height: 80vh;
  border-radius: 16px;
  background: #FFFFFF;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.16);
}
.am-modal-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 18px;
  border-bottom: 1px solid #EFEFEF;
}
.am-modal-title-group { display: flex; align-items: center; gap: 8px; }
.am-modal-header h3 { margin: 0; font-size: 14px; font-weight: 500; color: #1A1A1A; }
.am-modal-id {
  padding: 2px 8px;
  border-radius: 7px;
  background: #F5F5F6;
  color: #6B7280;
  font-size: 12px;
  font-family: 'SF Mono', 'Fira Code', 'Consolas', monospace;
}
.am-modal-close {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  padding: 0;
  border: none;
  border-radius: 7px;
  background: transparent;
  color: #9A9A9A;
  cursor: pointer;
  transition: background-color 0.15s ease, color 0.15s ease;
}
.am-modal-close:hover { background: #F5F5F5; color: #1A1A1A; }
.am-modal-body {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 14px;
  overflow-y: auto;
  padding: 16px 18px;
}
.am-modal-footer {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 18px;
  border-top: 1px solid #EFEFEF;
}

.am-slide-enter-active, .am-slide-leave-active { transition: all 0.2s ease; overflow: hidden; }
.am-slide-enter-from, .am-slide-leave-to { opacity: 0; max-height: 0; }
.am-slide-enter-to, .am-slide-leave-from { opacity: 1; max-height: 1400px; }
</style>