import apiClient from './client'

export interface SkillMeta {
  node_type: string
  name: string
  display_name: string
  description?: string
  default_config?: Record<string, unknown>
  is_third_party?: boolean
  is_prompt_skill?: boolean
  skill_category?: string
  skill_priority?: number
  owner_user_id?: string
}

export type SkillMap = Record<string, SkillMeta[]>

export interface SkillInputField {
  name: string
  type: string
  description?: string
  required?: boolean
}

export interface SkillDetail extends SkillMeta {
  trigger_words: string[]
  prompt_guidance?: string
  platform?: string
  input_fields: SkillInputField[]
  reference_paths: string[]
  prompt_content?: string
}

export const skillsApi = {
  async list(nodeType?: string): Promise<SkillMap> {
    const resp: any = await apiClient.get('/skills', {
      params: nodeType ? { node_type: nodeType } : undefined,
    })
    return ((resp?.data ?? resp) || {}) as SkillMap
  },

  async detail(nodeType: string, skillName: string): Promise<SkillDetail> {
    const resp: any = await apiClient.get(
      `/skills/${encodeURIComponent(nodeType)}/${encodeURIComponent(skillName)}`
    )
    return ((resp?.data ?? resp) || {}) as SkillDetail
  },

  async remove(nodeType: string, skillName: string): Promise<{ success: boolean; message?: string }> {
    const resp: any = await apiClient.delete(
      `/skills/${encodeURIComponent(nodeType)}/${encodeURIComponent(skillName)}`
    )
    return resp
  },

  async removePrompt(nodeType: string, skillName: string): Promise<{ success: boolean; message?: string }> {
    const resp: any = await apiClient.delete(
      `/skills/prompt/${encodeURIComponent(nodeType)}/${encodeURIComponent(skillName)}`
    )
    return resp
  },
}