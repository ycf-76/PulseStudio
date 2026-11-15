import { computed, ref } from 'vue'
import { skillsApi, type SkillMap, type SkillMeta } from '@/api/skills'

export const NODE_TYPE_LABELS: Record<string, string> = {
  produce: '内容生产',
  plan: '策划规划',
  copywrite: '文案创作',
  publish: '发布分发',
  discover: '发现采集',
  search: '搜索',
  attribute: '内容属性',
  analyze: '内容分析',
  edit: '内容编辑',
  audit: '合规审核',
  image_gen: '图片生成',
  image_plan: '图片规划',
  image_review: '图片审核',
  final_review: '终审',
  caption_hashtag: '标题标签',
  polish: '文案润色',
  quality_gate: '质量把关',
  topic_evaluator: '选题评估',
  collab: '多智能体协作',
  file: '文件处理',
  general: '通用',
  evolve: '技能进化',
  email_send: '邮件发送',
  wechat_send_text: '微信发送文本',
  wechat_send_file: '微信发送文件',
  feishu_file_ops: '飞书文件',
  feishu_wiki_doc: '飞书文档',
}

const NODE_TYPE_ORDER = [
  'produce',
  'plan',
  'copywrite',
  'image_gen',
  'image_plan',
  'image_review',
  'analyze',
  'discover',
  'search',
  'attribute',
  'audit',
  'final_review',
  'quality_gate',
  'topic_evaluator',
  'publish',
  'edit',
  'polish',
  'caption_hashtag',
  'collab',
  'file',
  'general',
  'evolve',
]

export interface SkillGroup {
  nodeType: string
  label: string
  skills: SkillMeta[]
}

const skillsMap = ref<SkillMap>({})
const loading = ref(false)
const error = ref('')

let _loaded = false
let _inflight: Promise<void> | null = null

function labelOf(nodeType: string) {
  return NODE_TYPE_LABELS[nodeType] || nodeType
}

const groups = computed<SkillGroup[]>(() => {
  const keys = Object.keys(skillsMap.value)
  const ordered = keys.sort((a, b) => {
    const ia = NODE_TYPE_ORDER.indexOf(a)
    const ib = NODE_TYPE_ORDER.indexOf(b)
    if (ia !== -1 && ib !== -1) return ia - ib
    if (ia !== -1) return -1
    if (ib !== -1) return 1
    return a.localeCompare(b)
  })
  return ordered
    .map(nodeType => ({
      nodeType,
      label: labelOf(nodeType),
      skills: [...(skillsMap.value[nodeType] || [])].sort((a, b) =>
        (a.display_name || a.name).localeCompare(b.display_name || b.name, 'zh-CN')
      ),
    }))
    .filter(g => g.skills.length > 0)
})

const flatSkills = computed<SkillMeta[]>(() => {
  const out: SkillMeta[] = []
  for (const group of groups.value) out.push(...group.skills)
  return out
})

const total = computed(() => flatSkills.value.length)

async function load(force = false): Promise<void> {
  if (_inflight) return _inflight
  if (_loaded && !force) return
  loading.value = true
  error.value = ''
  _inflight = skillsApi
    .list()
    .then(map => {
      skillsMap.value = map
      _loaded = true
    })
    .catch((e: any) => {
      error.value = e?.response?.data?.detail || e?.message || '技能列表加载失败'
      if (!_loaded) skillsMap.value = {}
    })
    .finally(() => {
      loading.value = false
      _inflight = null
    })
  return _inflight
}

export function useSkillsLibrary() {
  return {
    skillsMap,
    groups,
    flatSkills,
    total,
    loading,
    error,
    load,
    labelOf,
  }
}