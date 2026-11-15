import { computed, ref } from 'vue'

/**
 * 灵感模板（输入框下方展示区）数据源。
 *
 * 与「技能库」的区别：技能 = 能力（可以没有成品），模板 = 成品（必须有明确产出形态）。
 * 分类轴按用户意图（做图文 / 写文案 / 做视频 …）而非后端 node_type，
 * 点选模板只把 prompt 灌进输入框，不自动发送。
 *
 * 封面不依赖图片文件：统一用分类主色 + CSS 绘制的「笔记纸」预览，
 * 避免出现空图占位，也保证任何新增模板都能立刻有像样的视觉。
 */

export type InspirationIconKey = 'layers' | 'pen' | 'play' | 'chart' | 'send' | 'target' | 'sparkles' | 'grid'

export type InspirationCategoryKey = 'graphic' | 'copy' | 'video' | 'analysis' | 'publish' | 'plan'

export type InspirationFilterKey = 'all' | InspirationCategoryKey

export interface InspirationCategory {
  key: InspirationFilterKey
  label: string
  icon: InspirationIconKey
}

export interface InspirationTemplate {
  id: string
  title: string
  subtitle: string
  category: InspirationCategoryKey
  prompt: string
  skill: string
  icon: InspirationIconKey
  hot?: boolean
}

export const INSPIRATION_CATEGORIES: InspirationCategory[] = [
  { key: 'all', label: '全部', icon: 'grid' },
  { key: 'graphic', label: '图文', icon: 'layers' },
  { key: 'copy', label: '文案', icon: 'pen' },
  { key: 'video', label: '视频', icon: 'play' },
  { key: 'analysis', label: '诊断', icon: 'chart' },
  { key: 'publish', label: '发布', icon: 'send' },
  { key: 'plan', label: '策划', icon: 'target' },
]

/** 封面底色（渐变）与主色（纸面上的强调块），按分类固定，保证同类视觉成组 */
export const INSPIRATION_TONE: Record<InspirationCategoryKey, string> = {
  graphic: 'linear-gradient(150deg, #6C5CE7 0%, #A29BFE 100%)',
  copy: 'linear-gradient(150deg, #FF7A59 0%, #FFB199 100%)',
  video: 'linear-gradient(150deg, #2B2F6B 0%, #5B6CFF 100%)',
  analysis: 'linear-gradient(150deg, #0F766E 0%, #34D399 100%)',
  publish: 'linear-gradient(150deg, #7C3AED 0%, #C084FC 100%)',
  plan: 'linear-gradient(150deg, #D97706 0%, #F7C948 100%)',
}

export const INSPIRATION_ACCENT: Record<InspirationCategoryKey, string> = {
  graphic: '#6C5CE7',
  copy: '#FF7A59',
  video: '#5B6CFF',
  analysis: '#0F766E',
  publish: '#7C3AED',
  plan: '#D97706',
}

export const INSPIRATION_TEMPLATES: InspirationTemplate[] = [
  {
    id: 'tutorial_steps',
    title: '教程步骤卡',
    subtitle: '一个主题，4 张步骤卡',
    category: 'graphic',
    skill: 'card_xiaohongshu',
    icon: 'layers',
    hot: true,
    prompt: '把「3 步搞定早八妆容」换成我的主题，做成一组小红书图文教程卡：4 张，每张一个步骤，标题不超过 12 字，瑞士极简风格。',
  },
  {
    id: 'knowledge_list',
    title: '知识清单卡',
    subtitle: '6 条要点，一图看懂',
    category: 'graphic',
    skill: 'card_xiaohongshu',
    icon: 'layers',
    hot: true,
    prompt: '把「新手如何挑防晒」整理成小红书图文知识卡：6 条要点，每条不超过 20 字，奶油温柔风格，最后一页放总结。',
  },
  {
    id: 'data_insight',
    title: '数据洞察卡',
    subtitle: '先给结论，再给数字',
    category: 'graphic',
    skill: 'card_xiaohongshu',
    icon: 'layers',
    prompt: '把这组数据做成小红书图文数据洞察卡：第一页给结论，后面 3 页各讲一个关键数字，高奢黑金风格。',
  },
  {
    id: 'opinion_quote',
    title: '金句观点卡',
    subtitle: '5 张金句，每张一句',
    category: 'graphic',
    skill: 'card_quote',
    icon: 'layers',
    prompt: '把「职场不必讨好所有人」提炼成 5 张金句卡：每张一句不超过 20 字，杂志编辑风格，最后一页留一个互动问题。',
  },
  {
    id: 'compare_pick',
    title: '对比测评卡',
    subtitle: 'A vs B，末页给结论',
    category: 'graphic',
    skill: 'comparison_card',
    icon: 'layers',
    hot: true,
    prompt: '做一组小红书图文对比测评卡：对比「平替」和「大牌」同一件单品，6 个维度打分，最后一页给结论，多巴胺 Y2K 风格。',
  },
  {
    id: 'timeline_story',
    title: '时间线故事卡',
    subtitle: '5 个节点，一页一句',
    category: 'graphic',
    skill: 'card_xiaohongshu',
    icon: 'layers',
    prompt: '把「我 30 天减脂 8 斤」做成分镜时间线故事卡：5 个节点，每页一个时间点加一句话，手账贴纸风格。',
  },
  {
    id: 'theme_swiss',
    title: '瑞士极简风',
    subtitle: '白底黑字 + 克莱因蓝',
    category: 'graphic',
    skill: 'card_xiaohongshu',
    icon: 'layers',
    prompt: '用瑞士极简风格做 4 张小红书图文卡：白底黑字、克莱因蓝点缀、零圆角、大字排版，主题是「如何用 1 小时读完一本书」。',
  },
  {
    id: 'theme_inkwash',
    title: '新中式墨韵',
    subtitle: '宣纸底 + 朱砂印章',
    category: 'graphic',
    skill: 'card_xiaohongshu',
    icon: 'layers',
    prompt: '用新中式墨韵风格做 3 张小红书图文卡：宣纸底、朱砂印章、竖排标题，主题是「二十四节气里的茶」。',
  },
  {
    id: 'theme_journal',
    title: '手账贴纸风',
    subtitle: '胶带条 + 便利贴',
    category: 'graphic',
    skill: 'card_xiaohongshu',
    icon: 'layers',
    prompt: '用手账贴纸风格做 4 张小红书图文卡：奶白底、胶带条、便利贴，主题是「我的 5 件通勤好物」。',
  },
  {
    id: 'theme_luxe',
    title: '高奢黑金风',
    subtitle: '深色底 + 烫金细线',
    category: 'graphic',
    skill: 'card_xiaohongshu',
    icon: 'layers',
    prompt: '用高奢黑金风格做 3 张小红书图文卡：深色底、烫金细线、衬线标题，主题是「一件值得投资的大衣」。',
  },
  {
    id: 'theme_dopamine',
    title: '多巴胺 Y2K',
    subtitle: '高饱和撞色 + 像素贴纸',
    category: 'graphic',
    skill: 'card_xiaohongshu',
    icon: 'layers',
    prompt: '用多巴胺 Y2K 风格做 4 张小红书图文卡：高饱和撞色、像素贴纸、手写体，主题是「周末 citywalk 路线」。',
  },
  {
    id: 'theme_terminal',
    title: '极客终端风',
    subtitle: '深色底 + 终端绿',
    category: 'graphic',
    skill: 'card_xiaohongshu',
    icon: 'layers',
    prompt: '用极客终端风格做 3 张小红书图文卡：深色底、终端绿、等宽字体、代码块边框，主题是「程序员的一天」。',
  },
  {
    id: 'copy_title',
    title: '10 个爆款标题',
    subtitle: '带数字或反常识钩子',
    category: 'copy',
    skill: 'hook_generator',
    icon: 'pen',
    hot: true,
    prompt: '给「通勤穿搭」写 10 个小红书标题：每个不超过 20 字，至少一半带数字或反常识钩子，并标注每条适合的封面类型。',
  },
  {
    id: 'copy_note',
    title: '整篇笔记',
    subtitle: '钩子 + 正文 + 标签',
    category: 'copy',
    skill: 'xhs_note_creator',
    icon: 'pen',
    prompt: '围绕「租房改造预算 500 元」写一篇小红书笔记：开头 3 秒钩子、3 段正文、结尾互动引导、8 个话题标签。',
  },
  {
    id: 'copy_oral',
    title: '45 秒口播稿',
    subtitle: '口语化，短句节奏',
    category: 'copy',
    skill: 'video_script',
    icon: 'pen',
    prompt: '把「3 个显瘦穿搭技巧」写成 45 秒口播脚本：开头钩子、3 个信息点、结尾引导关注，口语化，每句不超过 20 字。',
  },
  {
    id: 'copy_polish',
    title: '去 AI 味改写',
    subtitle: '保留信息量，换成真人语气',
    category: 'copy',
    skill: 'text_polisher',
    icon: 'pen',
    prompt: '把下面这段文案改成小红书口吻，去掉 AI 味，保留全部信息量，长度不超过 300 字：\n\n（把文案贴在这里）',
  },
  {
    id: 'video_storyboard',
    title: '15 秒分镜',
    subtitle: '5 个镜头，标注景别',
    category: 'video',
    skill: 'video_script',
    icon: 'play',
    hot: true,
    prompt: '把「一杯手冲咖啡的 3 分钟」拆成 15 秒短视频分镜：5 个镜头，每个镜头一句画面描述加一句口播，标注景别。',
  },
  {
    id: 'video_script',
    title: '60 秒口播视频',
    subtitle: '分段标注时长与画面',
    category: 'video',
    skill: 'video_script',
    icon: 'play',
    prompt: '给「为什么你总是存不下钱」写一条 60 秒口播视频脚本：分 4 段，每段标注时长、画面和口播。',
  },
  {
    id: 'video_strategy',
    title: '视频栏目策略',
    subtitle: '3 个栏目 + 更新节奏',
    category: 'video',
    skill: 'video_strategy',
    icon: 'play',
    prompt: '给我的账号做一份视频内容策略：3 个视频栏目，每个栏目 5 个选题，说明建议时长和更新节奏。',
  },
  {
    id: 'ana_account',
    title: '账号体检',
    subtitle: '定位 / 结构 / 数据',
    category: 'analysis',
    skill: 'account_diagnosis',
    icon: 'chart',
    hot: true,
    prompt: '帮我做一次小红书账号体检：定位是否清晰、内容结构是否偏科、近 30 天数据表现，最后给 3 条最该先改的动作。',
  },
  {
    id: 'ana_competitor',
    title: '竞品拆解',
    subtitle: '选题 / 钩子 / 封面套路',
    category: 'analysis',
    skill: 'competitor_analysis',
    icon: 'chart',
    prompt: '拆解竞品账号「XXX」最近 10 篇笔记：选题分布、开头钩子类型、封面套路、评论区高频问题，输出 3 个差异化机会。',
  },
  {
    id: 'ana_postmortem',
    title: '单篇复盘',
    subtitle: '为什么爆 / 为什么不爆',
    category: 'analysis',
    skill: 'content_postmortem',
    icon: 'chart',
    prompt: '把这篇笔记复盘一下：为什么数据好或差，钩子、封面、选题各打几分，下次可以怎么复用。',
  },
  {
    id: 'pub_check',
    title: '发布前检查',
    subtitle: '标题 / 封面 / 标签 / 敏感词',
    category: 'publish',
    skill: 'publish_checklist',
    icon: 'send',
    hot: true,
    prompt: '发布前帮我过一遍检查清单：标题、封面、标签、敏感词、发布时间，逐项给结论，有问题直接给修改建议。',
  },
  {
    id: 'pub_schedule',
    title: '7 天排期',
    subtitle: '每天一篇，含发布时间',
    category: 'publish',
    skill: 'content_calendar',
    icon: 'send',
    prompt: '帮我排未来 7 天的小红书发布计划：每天一篇，含选题、内容形式和发布时间，避开同质化。',
  },
  {
    id: 'plan_topic',
    title: '20 个选题',
    subtitle: '按爆款潜力排序',
    category: 'plan',
    skill: 'content_matrix',
    icon: 'target',
    hot: true,
    prompt: '基于我的账号定位，给我 20 个小红书选题：按爆款潜力排序，每个标注适合的形式和预估难度。',
  },
  {
    id: 'plan_matrix',
    title: '内容矩阵',
    subtitle: '3 个支柱 + 占比',
    category: 'plan',
    skill: 'content_strategy',
    icon: 'target',
    prompt: '帮我搭一个内容矩阵：3 个内容支柱，每个支柱下 4 个选题方向，说明各支柱的作用和占比。',
  },
]

export function useInspirationTemplates() {
  const activeCategory = ref<InspirationFilterKey>('all')

  const counts = computed<Record<InspirationFilterKey, number>>(() => {
    const result = { all: INSPIRATION_TEMPLATES.length } as Record<InspirationFilterKey, number>
    for (const cat of INSPIRATION_CATEGORIES) {
      if (cat.key === 'all') continue
      result[cat.key] = INSPIRATION_TEMPLATES.filter(t => t.category === cat.key).length
    }
    return result
  })

  const visibleTemplates = computed<InspirationTemplate[]>(() => {
    if (activeCategory.value === 'all') return INSPIRATION_TEMPLATES
    return INSPIRATION_TEMPLATES.filter(t => t.category === activeCategory.value)
  })

  const groupedTemplates = computed(() =>
    INSPIRATION_CATEGORIES
      .filter(c => c.key !== 'all')
      .map(c => ({
        category: c,
        items: INSPIRATION_TEMPLATES.filter(t => t.category === c.key),
      }))
      .filter(g => g.items.length > 0)
  )

  function selectCategory(key: InspirationFilterKey) {
    activeCategory.value = key
  }

  return { activeCategory, counts, visibleTemplates, groupedTemplates, selectCategory }
}