import { ref, onMounted, onUnmounted, watch, type Ref } from 'vue'

type TimePeriod = 'morning' | 'afternoon' | 'evening' | 'night'
type UserStatus = 'new' | 'active' | 'returning'

export type HeroIconKey =
  | 'radar'
  | 'topic'
  | 'note'
  | 'video'
  | 'publish'
  | 'review'
  | 'diagnose'
  | 'competitor'

export interface HeroCapability {
  icon: HeroIconKey
  label: string
}

export interface HeroStarter {
  icon: HeroIconKey
  label: string
  prompt: string
}

/* 问候语用「操盘手口吻」而不是客服口吻：句句带具体动作和创作者黑话
   （爆款/钩子/排期/回血），避免「你好，有什么可以帮你」这类通用助手腔。
   方括号包住的部分是强调词，渲染层会单独上色，让一句话有视觉记忆点。
   每句控制在 18 字内：标题是单行省略，再长会被截断。 */
const GREETINGS: Record<UserStatus, Record<TimePeriod, string[]>> = {
  new: {
    morning: [
      '早，今天的[爆款]在等你抄作业',
      '早呀，先翻[热搜]再动笔',
      '早上好，[选题池]空着，开个新的？',
      '开工了，[热点]不等人',
    ],
    afternoon: [
      '下午好，赶在[晚高峰]前发一篇',
      '午后好，还差一个[钩子]',
      '下午好，先写还是先[拆爆款]？',
      '这个点发稿，正好接住[流量]',
    ],
    evening: [
      '晚上好，今天的笔记[复盘]了吗',
      '夜色正好，[标题]还能再救一次',
      '晚上好，把明天的稿[排上]',
      '晚上好，[评论区]还没回吧',
    ],
    night: [
      '还没睡？深夜改稿最容易出[爆款]',
      '夜猫子档：安静[复盘]，少走弯路',
      '这个点还在写，[我陪你]',
      '深夜档，适合把[选题]理一理',
    ],
  },
  active: {
    morning: [
      '早，昨天的[数据]还热乎',
      '早安，今天的[热点]我帮你盯着',
      '早呀，昨天那篇[爆了]没',
      '开工：先看[数据]，再定今天写什么',
    ],
    afternoon: [
      '下午好，继续冲还是先[复盘]',
      '午后好，第二篇的[钩子]想到没',
      '下午好，[竞品]今天发了不少',
      '这时候补一篇，[晚高峰]能接住',
    ],
    evening: [
      '晚上好，今天发的[效果]怎么样',
      '今晚把[排期]定了，明天不用现想',
      '晚上好，把[爆款]拆开看看',
      '夜色正好，[标题]再打磨一轮',
    ],
    night: [
      '还在卷？我陪你把[这轮]改完',
      '深夜档：看[数据]、调策略',
      '这个点改稿，容易出[好东西]',
      '夜深了，明天的稿先[排上]',
    ],
  },
  returning: {
    morning: [
      '好久不见，[热点]换了一茬',
      '回来了，账号该[体检]了',
      '早，先看看这段时间的[数据]',
      '欢迎回来，稿子[攒了]不少吧',
    ],
    afternoon: [
      '好久不见，[竞品]又跑前面了',
      '回来了，先做个体检[再动手]',
      '下午好，断更后[第一条]最关键',
      '欢迎回来，从哪篇[接着写]？',
    ],
    evening: [
      '好久不见，今晚补个[复盘]',
      '回来了，[排期表]还空着',
      '晚上好，先把[账号]捡回来',
      '欢迎回来，断更久了得[回血]',
    ],
    night: [
      '好久不见，深夜适合[复盘]',
      '回来了，明天的稿先[备好]',
      '夜猫子模式[重启]',
      '欢迎回来，从[数据]开始找感觉',
    ],
  },
}

/* 「内容」Tab 下的问候语：这一栏的用户已经在稿子堆里，目标是
   分析现有内容、重写/润色、排期与发布，不再从零找选题。 */
const CONTENT_GREETINGS: Record<TimePeriod, string[]> = {
  morning: [
    '早，挑一篇旧稿[改起来]',
    '早呀，攒的稿子该[翻新]了',
    '早上好，今天发哪篇[定一下]',
    '开工，先把稿子[过一遍]',
  ],
  afternoon: [
    '下午好，挑篇稿子[重写]吧',
    '午后好，旧内容还能[再爆]',
    '下午好，发稿前先[改到位]',
    '这个点，正好[排发布]',
  ],
  evening: [
    '晚上好，把稿子[打磨]一遍',
    '夜色正好，旧稿[翻新]试试',
    '晚上好，今天这篇[发出]去',
    '晚上好，先[复盘]再改稿',
  ],
  night: [
    '还没睡？把这篇[改到满意]',
    '深夜档，适合[重写]旧稿',
    '这个点，稿子[再救一次]',
    '夜深了，发布前[最后过一遍]',
  ],
}

export interface GreetingSegment {
  text: string
  accent: boolean
}

/** 把「早，今天的[爆款]在等你抄作业」拆成普通片段 + 强调片段，供渲染层分段上色 */
export function splitGreeting(raw: string): GreetingSegment[] {
  const out: GreetingSegment[] = []
  let rest = raw
  while (rest) {
    const open = rest.indexOf('[')
    const close = rest.indexOf(']', open + 1)
    if (open < 0 || close < 0) {
      out.push({ text: rest, accent: false })
      break
    }
    if (open > 0) out.push({ text: rest.slice(0, open), accent: false })
    out.push({ text: rest.slice(open + 1, close), accent: true })
    rest = rest.slice(close + 1)
  }
  return out
}

/** 去掉强调标记，用于 title 悬浮提示等纯文本场景 */
export function stripGreeting(raw: string): string {
  return raw.replace(/\[|\]/g, '')
}

const CAPABILITIES: HeroCapability[] = [
  { icon: 'radar', label: '热点雷达' },
  { icon: 'topic', label: '选题策划' },
  { icon: 'note', label: '图文成稿' },
  { icon: 'video', label: '智能剪辑' },
  { icon: 'publish', label: '多平台分发' },
  { icon: 'review', label: '数据复盘' },
]

const STARTERS: HeroStarter[] = [
  { icon: 'radar', label: '扒今天的爆款热点', prompt: '扫一遍今天小红书和微博的爆款内容，挑 3 个适合我账号的选题' },
  { icon: 'topic', label: '排一周选题计划', prompt: '帮我做一周的选题规划，围绕我的账号定位给出 7 个可落地的选题' },
  { icon: 'note', label: '写一篇图文笔记', prompt: '我有一个主题，帮我出一篇小红书图文笔记，含标题、正文和 6 张配图方案' },
  { icon: 'video', label: '剪一条竖版短视频', prompt: '把这段素材剪成 30 秒竖版视频，自动加字幕和节奏卡点' },
  { icon: 'publish', label: '一键分发到多平台', prompt: '把这篇笔记适配发布到小红书、抖音和公众号，各自调整标题和正文风格' },
  { icon: 'review', label: '复盘近 30 篇笔记', prompt: '复盘我最近 30 篇笔记的数据，找出下滑原因并给出下一步优化建议' },
  { icon: 'diagnose', label: '给账号做个体检', prompt: '帮我诊断账号的定位、封面和标题，指出最影响涨粉的三个问题' },
  { icon: 'competitor', label: '盯一下竞品在发什么', prompt: '分析三个同类竞品最近一周的爆款内容，拆出可复用的选题和标题套路' },
]

const STARTER_COUNT = 3

function getTimePeriod(hour: number): TimePeriod {
  if (hour >= 5 && hour < 12) return 'morning'
  if (hour >= 12 && hour < 18) return 'afternoon'
  if (hour >= 18 && hour < 22) return 'evening'
  return 'night'
}

function getUserStatus(): UserStatus {
  const firstVisit = localStorage.getItem('mint_first_visit')
  const lastActive = localStorage.getItem('mint_last_active')

  if (!firstVisit) {
    localStorage.setItem('mint_first_visit', String(Date.now()))
    return 'new'
  }

  const daysSinceFirst = (Date.now() - Number(firstVisit)) / (1000 * 60 * 60 * 24)
  const daysSinceLast = lastActive ? (Date.now() - Number(lastActive)) / (1000 * 60 * 60 * 24) : 999

  if (daysSinceFirst <= 7 && daysSinceLast <= 7) return 'new'
  if (daysSinceLast > 7) return 'returning'
  return 'active'
}

function randomPick(arr: string[], exclude?: string): string {
  const available = exclude ? arr.filter(s => s !== exclude) : arr
  const pool = available.length > 0 ? available : arr
  return pool[Math.floor(Math.random() * pool.length)]
}

function shuffle<T>(arr: T[]): T[] {
  const out = arr.slice()
  for (let i = out.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1))
    const tmp = out[i]
    out[i] = out[j]
    out[j] = tmp
  }
  return out
}

function readLastStarters(): string[] {
  try {
    const raw = JSON.parse(localStorage.getItem('mint_last_greeting_starters') || '[]')
    return Array.isArray(raw) ? raw.map(String) : []
  } catch {
    return []
  }
}

/** 每次至少换掉一条起手式，避免同一批示例反复出现 */
function pickStarters(): HeroStarter[] {
  const last = readLastStarters()
  let picked: HeroStarter[] = []
  for (let attempt = 0; attempt < 10; attempt++) {
    picked = shuffle(STARTERS).slice(0, STARTER_COUNT)
    if (!last.length || !picked.every(s => last.includes(s.label))) break
  }
  localStorage.setItem('mint_last_greeting_starters', JSON.stringify(picked.map(s => s.label)))
  return picked
}

export function useGreeting(contentMode?: Ref<boolean>) {
  const title = ref('')
  const starters = ref<HeroStarter[]>([])
  const capabilities = CAPABILITIES

  function isContentMode() {
    return contentMode?.value === true
  }

  function refresh() {
    const hour = new Date().getHours()
    const period = getTimePeriod(hour)
    const status = getUserStatus()
    const content = isContentMode()

    const titleKey = content ? 'mint_last_content_greeting_title' : 'mint_last_greeting_title'
    const lastTitle = localStorage.getItem(titleKey)
    const pool = content ? CONTENT_GREETINGS[period] : GREETINGS[status][period]
    title.value = randomPick(pool, lastTitle || undefined)

    starters.value = content ? [] : pickStarters()

    localStorage.setItem(titleKey, title.value)
    localStorage.setItem('mint_last_active', String(Date.now()))
  }

  let timer: ReturnType<typeof setInterval> | null = null

  onMounted(() => {
    refresh()
    timer = setInterval(refresh, 60 * 60 * 1000)
  })

  if (contentMode) {
    watch(contentMode, () => refresh())
  }

  onUnmounted(() => {
    if (timer) clearInterval(timer)
  })

  return { title, starters, capabilities, refresh }
}