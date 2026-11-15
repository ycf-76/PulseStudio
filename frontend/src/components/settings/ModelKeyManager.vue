<template>
  <div class="byok-manager">
    <section class="byok-hero">
      <div class="byok-hero-frame">
        <img class="byok-hero-img" src="/images/setting_images/img_5.webp" alt="模型配置" />
      </div>
      <div class="byok-hero-body">
        <h3 class="byok-hero-title">API Key 管理</h3>
        <p class="byok-hero-desc">为每个模型厂商配置你自己的 API Key，即可解锁对应模型。Key 加密存储，仅你可用。</p>
        <div class="byok-hero-chips">
          <span class="byok-hero-chip" :class="{ 'byok-hero-chip-on': configuredCount > 0 }">
            <span class="byok-hero-chip-dot"></span>已配置 {{ configuredCount }} / {{ providers.length }}
          </span>
        </div>
      </div>
    </section>

    <div v-if="loading" class="byok-state">
      <span class="byok-spinner" />
      正在读取厂商配置…
    </div>

    <div v-else class="byok-provider-list">
      <div class="byok-section-head">
        <span class="byok-section-title">模型厂商</span>
        <span class="byok-section-count">{{ configuredCount }} / {{ providers.length }} 已配置</span>
      </div>
      <div
        v-for="prov in providers"
        :key="prov.provider"
        class="byok-provider-card"
        :class="{ 'byok-provider-active': prov.has_api_key }"
      >
        <div class="byok-provider-row">
          <span class="byok-provider-icon" v-html="getIcon(prov.provider)" />
          <span class="byok-provider-name">{{ prov.provider_name }}</span>
          <span v-if="prov.source === 'user'" class="byok-badge byok-badge-ok">
            <span class="byok-badge-dot" />已配置
          </span>
          <span v-else-if="prov.source === 'global'" class="byok-badge byok-badge-ok">
            <span class="byok-badge-dot" />内置 Key
          </span>
          <span v-else class="byok-badge byok-badge-none">未配置</span>

          <div class="byok-key-info">
            <span v-if="prov.source === 'user'" class="byok-key-masked">sk-****{{ credentialMap[prov.provider]?.maskedKey || '****' }}</span>
            <span v-else-if="prov.source === 'global'" class="byok-key-masked">平台 .env 内置</span>
            <span v-if="prov.has_api_key && credentialMap[prov.provider]?.custom_base_url" class="byok-base-url">
              {{ credentialMap[prov.provider].custom_base_url }}
            </span>
          </div>

          <a
            v-if="prov.docs_url && editingProvider !== prov.provider"
            :href="prov.docs_url"
            target="_blank"
            rel="noopener"
            class="byok-docs-link"
          >获取 Key →</a>

          <div v-if="editingProvider !== prov.provider" class="byok-provider-actions">
            <button
              v-if="prov.has_api_key"
              class="byok-btn byok-btn-ghost"
              @click="startEdit(prov)"
            >编辑</button>
            <button
              v-if="!prov.has_api_key"
              class="byok-btn byok-btn-primary"
              @click="startEdit(prov)"
            >+ 添加 Key</button>
            <button
              v-if="prov.source === 'user'"
              class="byok-btn byok-btn-verify"
              @click="verifyKey(prov.provider)"
              :disabled="verifying === prov.provider"
            >{{ verifying === prov.provider ? '验证中...' : '验证' }}</button>
            <button
              v-if="prov.source === 'user'"
              class="byok-btn byok-btn-danger"
              @click="deleteKey(prov.provider)"
            >删除</button>
          </div>
        </div>

        <div v-if="editingProvider === prov.provider" class="byok-edit-form">
          <div class="byok-field">
            <label>API Key</label>
            <div class="byok-input-row">
              <input
                :type="showKey ? 'text' : 'password'"
                v-model="editForm.api_key"
                :placeholder="prov.key_placeholder || 'sk-...'"
                class="byok-input"
              />
              <button class="byok-btn-icon" @click="showKey = !showKey" :title="showKey ? '隐藏' : '显示'">
                <Eye v-if="!showKey" :size="14" />
                <EyeOff v-else :size="14" />
              </button>
            </div>
          </div>
          <div v-if="prov.allow_custom_base_url" class="byok-field">
            <label>Base URL <span class="byok-optional">(可选，留空用默认)</span></label>
            <input v-model="editForm.custom_base_url" :placeholder="prov.default_base_url" class="byok-input" />
          </div>
          <div v-if="prov.allow_custom_models" class="byok-field">
            <label>自定义模型 <span class="byok-optional">(可选，逗号分隔)</span></label>
            <input v-model="editForm.custom_models_str" placeholder="model-a,model-b" class="byok-input" />
          </div>
          <div class="byok-actions">
            <button class="byok-btn byok-btn-primary" @click="saveCredential(prov.provider)" :disabled="saving || !editForm.api_key">
              {{ saving ? '保存中...' : '保存' }}
            </button>
            <button class="byok-btn byok-btn-ghost" @click="cancelEdit">取消</button>
            <span v-if="editError" class="byok-error">{{ editError }}</span>
          </div>
        </div>
      </div>
    </div>

    <div v-if="verifyResult" class="byok-verify-result" :class="verifyResult.valid ? 'byok-verify-ok' : 'byok-verify-fail'">
      {{ verifyResult.valid ? `✓ Key 有效，可用模型 ${verifyResult.models_count} 个` : `✗ Key 无效：${verifyResult.error}` }}
    </div>

    <section v-if="!loading" class="assign-section">
      <div class="byok-section-head">
        <span class="byok-section-title">模型分配</span>
        <span class="byok-section-count">{{ assignedCount }} 项已单独指定</span>
      </div>

      <div class="assign-card">
        <div class="assign-row">
          <div class="assign-label">
            <span class="assign-name">全局默认模型</span>
            <span class="assign-sub">智能体 / 技能未单独指定时使用</span>
          </div>
          <select
            class="assign-select"
            :value="defaultModel"
            :disabled="savingTarget === '__default__'"
            @change="onSaveDefault($event)"
          >
            <option value="">跟随系统默认</option>
            <option v-for="m in availableModels" :key="m.id" :value="m.id">{{ m.name }}</option>
          </select>
        </div>
      </div>

      <div class="assign-group-head">
        <span class="assign-group-title">智能体</span>
        <span class="assign-group-count">{{ agentTargets.length }}</span>
      </div>
      <div class="assign-card">
        <div v-if="!agentTargets.length" class="assign-empty">暂无可配置的智能体</div>
        <div v-for="t in agentTargets" :key="t.target_key" class="assign-row">
          <div class="assign-label">
            <span class="assign-name">{{ t.label }}</span>
            <span class="assign-sub">{{ t.subtitle }}</span>
          </div>
          <select
            class="assign-select"
            :value="t.assigned_model"
            :disabled="savingTarget === t.target_key"
            @change="onSaveTarget(t, $event)"
          >
            <option value="">跟随默认{{ t.default_model ? `（${t.default_model}）` : '' }}</option>
            <option v-for="m in availableModels" :key="m.id" :value="m.id">{{ m.name }}</option>
          </select>
        </div>
      </div>

      <div class="assign-group-head">
        <span class="assign-group-title">技能</span>
        <span class="assign-group-count">{{ skillTargets.length }}</span>
        <button class="assign-toggle" @click="skillsExpanded = !skillsExpanded">
          {{ skillsExpanded ? '收起' : '展开配置' }}
        </button>
      </div>
      <div v-if="skillsExpanded" class="assign-card">
        <div class="assign-search-row">
          <input
            v-model="skillFilter"
            class="assign-search"
            placeholder="搜索技能名称或类型"
          />
          <span class="assign-search-count">{{ filteredSkills.length }} / {{ skillTargets.length }}</span>
        </div>
        <div v-if="!filteredSkills.length" class="assign-empty">没有匹配的技能</div>
        <div v-for="t in filteredSkills" :key="t.target_key" class="assign-row">
          <div class="assign-label">
            <span class="assign-name">{{ t.label }}</span>
            <span class="assign-sub">{{ t.subtitle }}</span>
          </div>
          <select
            class="assign-select"
            :value="t.assigned_model"
            :disabled="savingTarget === t.target_key"
            @change="onSaveTarget(t, $event)"
          >
            <option value="">跟随默认{{ defaultModel ? `（${defaultModel}）` : '' }}</option>
            <option v-for="m in availableModels" :key="m.id" :value="m.id">{{ m.name }}</option>
          </select>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted, computed } from 'vue'
import { Eye, EyeOff } from 'lucide-vue-next'
import { authFetch } from '@/api/client'
import {
  type ProviderInfo,
  type CredentialStatus,
  type AvailableModel,
  fetchConfiguredModels,
  invalidateAvailableModelsCache,
} from '@/components/chat/composables/modelRegistry'
import { MODEL_REGISTRY } from '@/components/chat/composables/modelRegistry'

interface ProviderWithStatus extends ProviderInfo {
  has_api_key: boolean
  source: 'user' | 'global' | ''
}

interface VerifyResult {
  valid: boolean
  error: string
  models_count: number
}

interface AssignmentTarget {
  target_type: string
  target_key: string
  label: string
  subtitle: string
  default_model: string
  assigned_model: string
}

const loading = ref(true)
const providers = ref<ProviderWithStatus[]>([])
const credentialMap = ref<Record<string, { maskedKey: string; custom_base_url: string; custom_models: string[]; is_active: boolean }>>({})

const availableModels = ref<AvailableModel[]>([])
const assignmentTargets = ref<AssignmentTarget[]>([])
const defaultModel = ref('')
const savingTarget = ref<string | null>(null)
const skillsExpanded = ref(false)
const skillFilter = ref('')

const editingProvider = ref<string | null>(null)
const editForm = reactive({ api_key: '', custom_base_url: '', custom_models_str: '' })
const showKey = ref(false)
const saving = ref(false)
const editError = ref('')
const verifying = ref<string | null>(null)
const verifyResult = ref<VerifyResult | null>(null)

const configuredCount = computed(() => providers.value.filter((p) => p.has_api_key).length)

const agentTargets = computed(() => assignmentTargets.value.filter((t) => t.target_type === 'agent'))
const skillTargets = computed(() => assignmentTargets.value.filter((t) => t.target_type === 'skill'))
const assignedCount = computed(
  () => (defaultModel.value ? 1 : 0) + assignmentTargets.value.filter((t) => t.assigned_model).length,
)

const filteredSkills = computed(() => {
  const kw = skillFilter.value.trim().toLowerCase()
  if (!kw) return skillTargets.value
  return skillTargets.value.filter(
    (t) =>
      t.label.toLowerCase().includes(kw) ||
      t.subtitle.toLowerCase().includes(kw) ||
      t.target_key.toLowerCase().includes(kw),
  )
})

function getIcon(provider: string): string {
  const iconMap: Record<string, string> = {}
  for (const m of MODEL_REGISTRY) {
    if (!iconMap[m.provider]) iconMap[m.provider] = m.icon
  }
  return iconMap[provider] || ''
}

async function loadData() {
  loading.value = true
  try {
    const [provRes, credRes] = await Promise.all([
      authFetch('/api/models/providers'),
      authFetch('/api/models/credentials'),
    ])
    const provJson = await provRes.json()
    const credJson = await credRes.json()

    const provList: ProviderInfo[] = (provJson.success ? provJson.data : []) || []
    const credList: CredentialStatus[] = (credJson.success ? credJson.data : []) || []

    const credByProvider: Record<string, CredentialStatus> = {}
    for (const c of credList) credByProvider[c.provider] = c

    providers.value = provList.map((p) => {
      const hasUserKey = !!credByProvider[p.provider]?.has_api_key
      return {
        ...p,
        has_api_key: hasUserKey || !!p.has_api_key,
        source: hasUserKey ? 'user' : (p.source || ''),
      }
    })

    const newCredMap: typeof credentialMap.value = {}
    for (const c of credList) {
      newCredMap[c.provider] = {
        maskedKey: '****',
        custom_base_url: c.custom_base_url,
        custom_models: c.custom_models,
        is_active: c.is_active,
      }
    }
    credentialMap.value = newCredMap

    await loadAssignments()
  } catch (e) {
    console.error('[BYOK] loadData failed', e)
  } finally {
    loading.value = false
  }
}

async function loadAssignments() {
  try {
    const [models, assignRes] = await Promise.all([
      fetchConfiguredModels(true),
      authFetch('/api/models/assignments'),
    ])
    availableModels.value = models || []

    const json = await assignRes.json()
    if (json.success && json.data) {
      defaultModel.value = json.data.default_model || ''
      assignmentTargets.value = (json.data.targets || []).map((t: AssignmentTarget) => ({
        target_type: t.target_type,
        target_key: t.target_key,
        label: t.label,
        subtitle: t.subtitle,
        default_model: t.default_model,
        assigned_model: t.assigned_model,
      }))
    }
  } catch (e) {
    console.warn('[BYOK] loadAssignments failed', e)
  }
}

async function saveAssignment(targetType: string, targetKey: string, model: string, uiKey: string) {
  savingTarget.value = uiKey
  try {
    const res = await authFetch('/api/models/assignments', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ target_type: targetType, target_key: targetKey, model }),
    })
    const json = await res.json()
    if (!json.success) {
      console.warn('[BYOK] save assignment failed', json)
      return
    }
    await loadAssignments()
    invalidateAvailableModelsCache()
  } catch (e) {
    console.warn('[BYOK] save assignment error', e)
  } finally {
    savingTarget.value = null
  }
}

function onSaveDefault(event: Event) {
  const value = (event.target as HTMLSelectElement).value
  defaultModel.value = value
  saveAssignment('default', '', value, '__default__')
}

function onSaveTarget(target: AssignmentTarget, event: Event) {
  const value = (event.target as HTMLSelectElement).value
  target.assigned_model = value
  saveAssignment(target.target_type, target.target_key, value, target.target_key)
}

function startEdit(prov: ProviderWithStatus) {
  editingProvider.value = prov.provider
  editForm.api_key = ''
  editForm.custom_base_url = credentialMap.value[prov.provider]?.custom_base_url || ''
  editForm.custom_models_str = (credentialMap.value[prov.provider]?.custom_models || []).join(',')
  editError.value = ''
  showKey.value = false
}

function cancelEdit() {
  editingProvider.value = null
  editError.value = ''
}

async function saveCredential(provider: string) {
  saving.value = true
  editError.value = ''
  try {
    const customModels = editForm.custom_models_str
      ? editForm.custom_models_str.split(',').map((s) => s.trim()).filter(Boolean)
      : []
    const res = await authFetch(`/api/models/credentials/${provider}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        api_key: editForm.api_key,
        custom_base_url: editForm.custom_base_url,
        custom_models: customModels,
        is_active: true,
      }),
    })
    const json = await res.json()
    if (json.success) {
      editingProvider.value = null
      invalidateAvailableModelsCache()
      await loadData()
    } else {
      editError.value = json.message || json.detail || '保存失败'
    }
  } catch (e: any) {
    editError.value = e.message || '网络错误'
  } finally {
    saving.value = false
  }
}

async function deleteKey(provider: string) {
  if (!confirm(`确定删除 ${provider} 的 API Key？删除后该 Provider 下的模型将不可用。`)) return
  try {
    await authFetch(`/api/models/credentials/${provider}`, {
      method: 'DELETE',
    })
    invalidateAvailableModelsCache()
    await loadData()
  } catch {}
}

async function verifyKey(provider: string) {
  verifying.value = provider
  verifyResult.value = null
  try {
    const res = await authFetch(`/api/models/credentials/${provider}/verify`, {
      method: 'POST',
    })
    const json = await res.json()
    if (json.success && json.data) {
      verifyResult.value = json.data
    }
  } catch (e: any) {
    verifyResult.value = { valid: false, error: e.message, models_count: 0 }
  } finally {
    verifying.value = null
  }
}

onMounted(loadData)
</script>

<style scoped>
.byok-manager {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
/* 顶部封面 */
.byok-hero {
  position: relative;
  aspect-ratio: 3.2 / 1;
  max-height: 240px;
  border: 1px solid #EFEFEF;
  border-radius: 14px;
  overflow: hidden;
  background: linear-gradient(90deg, #000005 0%, #000A19 24%, #01060F 76%, #01091A 100%);
  box-shadow: 0 6px 18px rgba(17, 24, 39, 0.05);
}
.byok-hero-frame {
  position: absolute;
  top: 0;
  bottom: 0;
  left: 50%;
  transform: translateX(-50%);
  aspect-ratio: 647 / 348;
}
.byok-hero-img {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
  object-position: center center;
}
.byok-hero::after {
  content: '';
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  height: 38%;
  background: linear-gradient(180deg, rgba(0,10,25,0) 0%, rgba(0,10,25,0.62) 40%, rgba(0,10,25,0.9) 100%);
  pointer-events: none;
}
.byok-hero-body {
  position: absolute;
  left: 18px;
  right: 18px;
  bottom: 13px;
  z-index: 1;
  display: flex;
  flex-direction: column;
  gap: 7px;
}
.byok-hero-title {
  margin: 0;
  color: #FFFFFF;
  font-size: 15px;
  font-weight: 500;
  letter-spacing: -0.01em;
}
.byok-hero-desc {
  margin: 0;
  color: rgba(255, 255, 255, 0.66);
  font-size: 13px;
  line-height: 1.5;
}
.byok-hero-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.byok-hero-chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 2px 8px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.12);
  color: rgba(255, 255, 255, 0.72);
  font-size: 12px;
}
.byok-hero-chip-dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.4);
}
.byok-hero-chip-on {
  background: rgba(16, 185, 129, 0.18);
  color: #6EE7B7;
}
.byok-hero-chip-on .byok-hero-chip-dot {
  background: #10B981;
  box-shadow: 0 0 0 3px rgba(16, 185, 129, 0.18);
}
@media (max-width: 560px) {
  .byok-hero {
    aspect-ratio: auto;
    height: 156px;
  }
}
.byok-provider-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.byok-section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 0 2px;
  margin-bottom: 4px;
}
.byok-section-title {
  font-size: 13px;
  font-weight: 500;
  color: #1A1A1A;
  letter-spacing: -0.01em;
}
.byok-section-count {
  font-size: 12px;
  color: #9CA3AF;
  font-variant-numeric: tabular-nums;
}
.byok-state {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 40px 20px;
  border: 1px dashed #E8E9EB;
  border-radius: 14px;
  background: #FCFCFD;
  color: #9CA3AF;
  font-size: 13px;
}
.byok-spinner {
  width: 14px;
  height: 14px;
  border: 2px solid #E5E7EB;
  border-top-color: #3B6CF6;
  border-radius: 50%;
  animation: byok-spin 0.7s linear infinite;
}
@keyframes byok-spin {
  to { transform: rotate(360deg); }
}
.byok-provider-card {
  background: #FFFFFF;
  border: 1px solid #EFEFEF;
  border-radius: 12px;
  padding: 7px 12px;
  box-shadow: 0 6px 18px rgba(17, 24, 39, 0.04);
  transition: border-color 0.18s ease, box-shadow 0.18s ease;
}
.byok-provider-card:hover {
  border-color: #DCE4F7;
  box-shadow: 0 10px 26px rgba(59, 108, 246, 0.07);
}
.byok-provider-active {
  border-color: #C7D6F5;
}
.byok-provider-row {
  display: flex;
  align-items: center;
  gap: 9px;
  min-height: 30px;
}
.byok-provider-icon {
  width: 22px;
  height: 22px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 7px;
  background: #F5F6F8;
  flex-shrink: 0;
}
.byok-provider-name {
  font-size: 13px;
  font-weight: 500;
  color: #1A1A1A;
  letter-spacing: -0.01em;
  flex-shrink: 0;
}
.byok-badge {
  font-size: 11px;
  padding: 1px 7px;
  border-radius: 999px;
  font-weight: 500;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
}
.byok-badge-ok {
  background: #ECFDF5;
  color: #059669;
}
.byok-badge-none {
  background: #FEF3C7;
  color: #D97706;
}
.byok-badge-dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: currentColor;
}
.byok-key-info {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1;
  min-width: 0;
}
.byok-key-masked {
  font-size: 12px;
  color: #9CA3AF;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  letter-spacing: 0.02em;
  flex-shrink: 0;
}
.byok-base-url {
  font-size: 11px;
  color: #C7CBD1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
}
.byok-provider-actions {
  display: flex;
  gap: 5px;
  align-items: center;
  flex-shrink: 0;
}
.byok-docs-link {
  flex-shrink: 0;
  font-size: 11px;
  color: #3B6CF6;
  text-decoration: none;
  transition: color 0.15s ease;
}
.byok-docs-link:hover { color: #2563EB; text-decoration: underline; }

.byok-edit-form {
  display: flex;
  flex-direction: column;
  gap: 9px;
  padding: 10px 0 4px;
  margin-top: 7px;
  border-top: 1px solid #F0F1F3;
}
.byok-field label {
  display: block;
  font-size: 12px;
  font-weight: 500;
  color: #4B4B4B;
  margin-bottom: 5px;
}
.byok-optional {
  font-weight: 400;
  color: #9CA3AF;
}
.byok-input {
  width: 100%;
  padding: 8px 11px;
  border: 1px solid #E3E5E9;
  border-radius: 9px;
  font-size: 13px;
  color: #1A1A1A;
  background: #FCFCFD;
  outline: none;
  transition: border-color 0.15s ease, background 0.15s ease, box-shadow 0.15s ease;
  box-sizing: border-box;
}
.byok-input::placeholder { color: #B4B8BF; }
.byok-input:focus {
  border-color: #3B6CF6;
  background: #FFFFFF;
  box-shadow: 0 0 0 3px rgba(59, 108, 246, 0.12);
}
.byok-input-row {
  display: flex;
  gap: 6px;
  align-items: center;
}
.byok-input-row .byok-input { flex: 1; }
.byok-btn-icon {
  width: 34px;
  height: 34px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 1px solid #E3E5E9;
  border-radius: 9px;
  background: #FCFCFD;
  color: #6B7280;
  cursor: pointer;
  flex-shrink: 0;
  transition: border-color 0.15s ease, background 0.15s ease, color 0.15s ease;
}
.byok-btn-icon:hover {
  background: #FFFFFF;
  border-color: #DCE4F7;
  color: #3B6CF6;
}
.byok-actions {
  display: flex;
  gap: 8px;
  align-items: center;
}
.byok-btn {
  padding: 5px 11px;
  border: none;
  border-radius: 8px;
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  transition: background 0.15s ease, box-shadow 0.15s ease, opacity 0.15s ease;
}
.byok-btn-primary {
  background: #3B6CF6;
  color: #FFF;
  box-shadow: 0 3px 10px rgba(59, 108, 246, 0.22);
}
.byok-btn-primary:hover {
  background: #2563EB;
  box-shadow: 0 5px 14px rgba(59, 108, 246, 0.28);
}
.byok-btn-primary:disabled {
  background: #C7CBD1;
  box-shadow: none;
  cursor: not-allowed;
}
.byok-btn-ghost {
  background: #F5F6F8;
  color: #6B7280;
}
.byok-btn-ghost:hover { background: #EDEFF2; }
.byok-btn-verify {
  background: #EFF6FF;
  color: #3B6CF6;
}
.byok-btn-verify:hover { background: #DBEAFE; }
.byok-btn-verify:disabled { opacity: 0.5; cursor: not-allowed; }
.byok-btn-danger {
  background: #FEF2F2;
  color: #EF4444;
}
.byok-btn-danger:hover { background: #FEE2E2; }
.byok-error {
  font-size: 12px;
  color: #EF4444;
}
.byok-verify-result {
  padding: 11px 14px;
  border-radius: 11px;
  font-size: 13px;
  font-weight: 500;
  line-height: 1.5;
}
.byok-verify-ok {
  background: #ECFDF5;
  color: #059669;
  border: 1px solid rgba(16, 185, 129, 0.18);
}
.byok-verify-fail {
  background: #FEF2F2;
  color: #EF4444;
  border: 1px solid rgba(239, 68, 68, 0.18);
}
/* 模型分配 */
.assign-section {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.assign-group-head {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 0 2px;
  margin: 6px 0 2px;
}
.assign-group-title {
  font-size: 12px;
  font-weight: 500;
  color: #4B4B4B;
}
.assign-group-count {
  font-size: 11px;
  color: #9CA3AF;
  font-variant-numeric: tabular-nums;
}
.assign-toggle {
  margin-left: auto;
  padding: 2px 8px;
  border: 1px solid #E3E5E9;
  border-radius: 7px;
  background: #FFFFFF;
  color: #3B6CF6;
  font-size: 11px;
  cursor: pointer;
  transition: background 0.15s ease, border-color 0.15s ease;
}
.assign-toggle:hover {
  background: #F5F8FF;
  border-color: #C7D6F5;
}
.assign-card {
  display: flex;
  flex-direction: column;
  gap: 2px;
  background: #FFFFFF;
  border: 1px solid #EFEFEF;
  border-radius: 12px;
  padding: 6px 12px;
  box-shadow: 0 6px 18px rgba(17, 24, 39, 0.04);
}
.assign-row {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 32px;
  padding: 2px 0;
}
.assign-row + .assign-row {
  border-top: 1px solid #F5F6F8;
}
.assign-label {
  display: flex;
  flex-direction: column;
  gap: 1px;
  flex: 1;
  min-width: 0;
}
.assign-name {
  font-size: 13px;
  font-weight: 500;
  color: #1A1A1A;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.assign-sub {
  font-size: 11px;
  color: #9CA3AF;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.assign-select {
  flex-shrink: 0;
  width: 190px;
  padding: 5px 9px;
  border: 1px solid #E3E5E9;
  border-radius: 8px;
  background: #FCFCFD;
  color: #1A1A1A;
  font-size: 12px;
  outline: none;
  cursor: pointer;
  transition: border-color 0.15s ease, background 0.15s ease;
}
.assign-select:focus {
  border-color: #3B6CF6;
  background: #FFFFFF;
}
.assign-select:disabled {
  opacity: 0.55;
  cursor: progress;
}
.assign-search-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 3px 0 5px;
  border-bottom: 1px solid #F5F6F8;
}
.assign-search {
  flex: 1;
  padding: 5px 10px;
  border: 1px solid #E3E5E9;
  border-radius: 8px;
  font-size: 12px;
  color: #1A1A1A;
  background: #FCFCFD;
  outline: none;
  box-sizing: border-box;
  transition: border-color 0.15s ease, background 0.15s ease;
}
.assign-search::placeholder { color: #B4B8BF; }
.assign-search:focus {
  border-color: #3B6CF6;
  background: #FFFFFF;
}
.assign-search-count {
  font-size: 11px;
  color: #9CA3AF;
  font-variant-numeric: tabular-nums;
}
.assign-empty {
  padding: 10px 0;
  text-align: center;
  font-size: 12px;
  color: #9CA3AF;
}
@media (max-width: 560px) {
  .assign-select { width: 140px; }
}
</style>