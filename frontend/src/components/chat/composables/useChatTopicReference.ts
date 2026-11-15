import { ref, computed } from 'vue'

/**
 * 选题参考条 — ChatView 拆出
 *
 * 选题池「发起工作流」把热点写进 sessionStorage.pending_topic_reference 后跳 /chat，
 * 历史上只写不读（参考被静默丢弃）。这里在挂载时消费：渲染参考条 + 随首条消息注入，
 * 读到的同时 removeItem，导航残留不会二次生效。
 */

export interface TopicReference {
  title: string
  summary?: string
  url?: string
  platform?: string
  author?: string
  likes?: number
  comments?: number
  collects?: number
  shares?: number
  source_keyword?: string
}

export function useChatTopicReference() {
  const topicReference = ref<TopicReference | null>(null)

  const topicRefMetrics = computed(() => {
    const r = topicReference.value
    if (!r) return ''
    const parts: string[] = []
    if (r.likes) parts.push(`赞${r.likes}`)
    if (r.comments) parts.push(`评${r.comments}`)
    if (r.collects) parts.push(`藏${r.collects}`)
    if (r.shares) parts.push(`转${r.shares}`)
    return parts.join(' · ')
  })

  /** 注入给模型用的干净载荷：去掉 undefined，字段顺序固定，JSON 可控 */
  const topicRefPayload = computed(() => {
    const r = topicReference.value
    if (!r) return null
    return {
      title: r.title,
      summary: r.summary || '',
      url: r.url || '',
      platform: r.platform || '',
      author: r.author || '',
      likes: r.likes || 0,
      comments: r.comments || 0,
      collects: r.collects || 0,
      shares: r.shares || 0,
      source_keyword: r.source_keyword || '',
    }
  })

  function readPendingTopicReference() {
    let raw: string | null = null
    try {
      raw = sessionStorage.getItem('pending_topic_reference')
    } catch {
      return
    }
    if (!raw) return
    // 一次性消费：无论解析成败都移除，避免损坏条目在后续挂载反复重放
    try { sessionStorage.removeItem('pending_topic_reference') } catch {}
    try {
      const data = JSON.parse(raw)
      if (data && typeof data.title === 'string' && data.title.trim()) {
        topicReference.value = data as TopicReference
        console.log('[ChatView] 选题参考已带入:', data.title)
      }
    } catch {
      console.warn('[ChatView] pending_topic_reference 解析失败，已丢弃')
    }
  }

  return {
    topicReference,
    topicRefMetrics,
    topicRefPayload,
    readPendingTopicReference,
  }
}
