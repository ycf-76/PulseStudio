import { describe, expect, it } from 'vitest'
import type { MyWorkItem } from '@/api/my_works'
import {
  formatCount,
  formatWorkDate,
  matchesWorkKind,
  platformLabelOf,
  statusLabelOf,
  summarizeWorkItemMedia,
  summarizeWorkMedia,
} from './workMedia'

function makeWork(patch: Partial<MyWorkItem> = {}): MyWorkItem {
  return {
    id: 'w-1',
    workflow_id: 'wf-1',
    published_note_id: null,
    topic: null,
    title: '示例作品',
    cover_img_url: null,
    images: null,
    video_url: '',
    platform: 'xiaohongshu',
    published_at: null,
    collected_likes: null,
    collected_collects: null,
    collected_comments: null,
    collected_shares: null,
    performance_score: null,
    is_replicated: null,
    predicted_viral_score: null,
    actual_viral_score: null,
    prediction_error: null,
    collected_at: null,
    content_status: 'published',
    title_pattern: null,
    emotion_trigger: null,
    content_structure: null,
    ...patch,
  }
}

describe('summarizeWorkMedia — 覆盖全部产出类型', () => {
  it('图文：多张图片 → image_text + 页数', () => {
    const info = summarizeWorkMedia(makeWork({ images: ['a.jpg', 'b.jpg', 'c.jpg'] }))
    expect(info.kind).toBe('image_text')
    expect(info.label).toBe('图文')
    expect(info.pageCount).toBe(3)
    expect(info.coverUrl).toBe('a.jpg')
  })

  it('视频：video_url 优先判定为 video', () => {
    const info = summarizeWorkMedia(makeWork({ video_url: 'https://cdn/x.mp4', images: ['cover.jpg'] }))
    expect(info.kind).toBe('video')
    expect(info.videoUrl).toBe('https://cdn/x.mp4')
    expect(info.coverUrl).toBe('cover.jpg')
  })

  it('视频：images 里的 video 条目也能识别', () => {
    const info = summarizeWorkMedia(makeWork({
      images: [{ type: 'video', media_url: 'https://cdn/y.mp4' }],
    }))
    expect(info.kind).toBe('video')
    expect(info.videoUrl).toBe('https://cdn/y.mp4')
    expect(info.pageCount).toBe(0)
  })

  it('音频：草稿页里带 audioUrl → audio', () => {
    const info = summarizeWorkMedia(makeWork({
      card_draft: { pages: [{ type: 'audio', audioUrl: 'https://cdn/a.mp3' }] },
    }))
    expect(info.kind).toBe('audio')
    expect(info.audioUrl).toBe('https://cdn/a.mp3')
  })

  it('网页：只有 first_page_html 时判为 html 并带预览', () => {
    const info = summarizeWorkMedia(makeWork({ first_page_html: '<html><body>hi</body></html>' }))
    expect(info.kind).toBe('html')
    expect(info.label).toBe('网页')
    expect(info.previewHtml).toContain('hi')
  })

  it('网页：卡片页只有 HTML 没有截图时仍判为 html（不误判为图文）', () => {
    const info = summarizeWorkMedia(makeWork({
      content_status: 'draft',
      card_draft: {
        pages: [
          { type: 'cover', content: { htmlContent: '<div>封面</div>' } },
          { type: 'content', content: { htmlContent: '<div>正文</div>' } },
        ],
      },
    }))
    expect(info.kind).toBe('html')
    expect(info.coverUrl).toBe('')
    expect(info.previewHtml).toContain('封面')
    expect(info.pageCount).toBe(2)
  })

  it('纯文本：只有 content_text → text 且生成摘要', () => {
    const info = summarizeWorkMedia(makeWork({ content_text: '  这是一段纯文字笔记内容  ' }))
    expect(info.kind).toBe('text')
    expect(info.snippet).toBe('这是一段纯文字笔记内容')
  })

  it('草稿卡页：pngUrls 提供封面与页数', () => {
    const info = summarizeWorkMedia(makeWork({
      content_status: 'draft',
      card_draft: { pages: [{ type: 'cover', pngUrls: ['p1.png'] }, { type: 'content', pngUrls: ['p2.png'] }] },
    }))
    expect(info.kind).toBe('image_text')
    expect(info.pageCount).toBe(2)
    expect(info.coverUrl).toBe('p1.png')
  })

  it('cover_img_url 优先于 images', () => {
    const info = summarizeWorkMedia(makeWork({ cover_img_url: 'official.jpg', images: ['raw.jpg'] }))
    expect(info.coverUrl).toBe('official.jpg')
  })

  it('矢量图：svg_url 优先于同目录 png 判定为 svg', () => {
    const info = summarizeWorkMedia(makeWork({
      images: ['overlay.png', 'vector_render.png'],
      svg_url: '/outputs/pixel2motion/20261103_131638/logo.svg',
      svg_urls: ['/outputs/pixel2motion/20261103_131638/logo.svg'],
    }))
    expect(info.kind).toBe('svg')
    expect(info.label).toBe('矢量图')
    expect(info.svgUrl).toBe('/outputs/pixel2motion/20261103_131638/logo.svg')
    expect(info.pageCount).toBe(1)
  })

  it('矢量图：无 png 时 cover 回退到 svg 路径', () => {
    const info = summarizeWorkMedia(makeWork({
      cover_img_url: '/outputs/only/logo.svg',
      svg_url: '/outputs/only/logo.svg',
    }))
    expect(info.kind).toBe('svg')
    expect(info.coverUrl).toBe('/outputs/only/logo.svg')
  })

  it('矢量图：video 优先级高于 svg', () => {
    const info = summarizeWorkMedia(makeWork({ video_url: 'v.mp4', svg_url: '/outputs/a.svg' }))
    expect(info.kind).toBe('video')
  })

  it('无任何产出信息 → unknown', () => {
    const info = summarizeWorkMedia(makeWork())
    expect(info.kind).toBe('unknown')
    expect(info.label).toBe('其他')
  })

  it('互动数据为 0 时不展示统计', () => {
    expect(summarizeWorkMedia(makeWork({ collected_likes: 0 })).hasMetrics).toBe(false)
    expect(summarizeWorkMedia(makeWork({ collected_likes: 12 })).hasMetrics).toBe(true)
  })
})

describe('matchesWorkKind — 类型筛选', () => {
  it('all 放行全部', () => {
    expect(matchesWorkKind(makeWork(), 'all')).toBe(true)
  })

  it('按类型精确过滤', () => {
    expect(matchesWorkKind(makeWork({ video_url: 'v.mp4' }), 'video')).toBe(true)
    expect(matchesWorkKind(makeWork({ video_url: 'v.mp4' }), 'image_text')).toBe(false)
  })

  it('矢量图筛选：svg 产物不再落入图文分类', () => {
    const svgWork = makeWork({ images: ['p.png'], svg_url: '/outputs/a.svg' })
    expect(matchesWorkKind(svgWork, 'svg')).toBe(true)
    expect(matchesWorkKind(svgWork, 'image_text')).toBe(false)
  })
})

describe('展示格式化', () => {
  it('平台与状态文案', () => {
    expect(platformLabelOf('xiaohongshu')).toBe('小红书')
    expect(platformLabelOf('unknown_platform')).toBe('unknown_platform')
    expect(statusLabelOf('draft')).toBe('草稿')
  })

  it('数字缩写', () => {
    expect(formatCount(0)).toBe('0')
    expect(formatCount(999)).toBe('999')
    expect(formatCount(1500)).toBe('1.5k')
    expect(formatCount(23000)).toBe('2.3w')
  })

  it('日期格式与空值兜底', () => {
    expect(formatWorkDate(null)).toBe('—')
    expect(formatWorkDate('2026-03-05T08:30:00')).toBe('2026-03-05 08:30')
  })
})

describe('summarizeWorkItemMedia — 侧边栏 WorkItem 适配', () => {
  it('采集视频：images 中的对象条目不再被当成封面图', () => {
    const info = summarizeWorkItemMedia({
      coverUrl: 'cover.jpg',
      images: [{ type: 'video', media_url: 'https://cdn/v.mp4' }],
    })
    expect(info.kind).toBe('video')
    expect(info.videoUrl).toBe('https://cdn/v.mp4')
    expect(info.coverUrl).toBe('cover.jpg')
  })

  it('本地缓存的视频优先于远程 CDN', () => {
    const info = summarizeWorkItemMedia({
      videoUrl: 'https://cdn/v.mp4',
      localVideoUrl: '/outputs/local/v.mp4',
    })
    expect(info.kind).toBe('video')
    expect(info.videoUrl).toBe('/outputs/local/v.mp4')
  })

  it('平台生成草稿：cardDraft 的 pngUrls 提供封面与页数', () => {
    const info = summarizeWorkItemMedia({
      cardDraft: { pages: [{ type: 'cover', pngUrls: ['/outputs/a.png'] }, { type: 'content' }] },
    })
    expect(info.kind).toBe('image_text')
    expect(info.coverUrl).toBe('/outputs/a.png')
    expect(info.pageCount).toBe(2)
  })

  it('纯文本作品：仅有 contentText', () => {
    const info = summarizeWorkItemMedia({ contentText: '一段没有配图的文案' })
    expect(info.kind).toBe('text')
    expect(info.snippet).toBe('一段没有配图的文案')
  })

  it('互动数据参与统计标记', () => {
    expect(summarizeWorkItemMedia({ likes: 0, collects: 0 }).hasMetrics).toBe(false)
    expect(summarizeWorkItemMedia({ likes: 3 }).hasMetrics).toBe(true)
  })
})