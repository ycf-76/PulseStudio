import type { ContentEditPayload, MyWorkDetail } from '@/api/my_works'

/**
 * 内容层编辑草稿。
 *
 * 采集/生成产物没有 CardDeck 块结构，只能改「内容」这一层：
 * 标题、正文、话题、图片顺序、封面。草稿与基线分开存，
 * 靠比对判断是否有未保存修改，避免给后端塞无意义请求。
 */
export interface WorkContentDraft {
  title: string
  contentText: string
  tags: string[]
  images: string[]
  coverImgUrl: string
}

function readTags(value: unknown): string[] {
  if (Array.isArray(value)) {
    return value
      .filter((tag): tag is string => typeof tag === 'string')
      .map((tag) => tag.trim().replace(/^#+/, ''))
      .filter(Boolean)
  }
  if (typeof value === 'string') {
    return value
      .split(/[\s,，、]+/)
      .map((tag) => tag.trim().replace(/^#+/, ''))
      .filter(Boolean)
  }
  return []
}

/** 图片数组里可能混着视频对象，只有字符串才进草稿。 */
function readImages(value: unknown): string[] {
  if (!Array.isArray(value)) return []
  return value.filter((item): item is string => typeof item === 'string' && item.length > 0)
}

export function draftFromWork(work: Partial<MyWorkDetail> | null | undefined): WorkContentDraft {
  return {
    title: work?.title || '',
    contentText: work?.content_text || '',
    tags: readTags(work?.tags),
    images: readImages(work?.images),
    coverImgUrl: work?.cover_img_url || '',
  }
}

export function draftPayload(draft: WorkContentDraft): ContentEditPayload {
  return {
    title: draft.title.trim(),
    content_text: draft.contentText,
    tags: draft.tags.map((tag) => tag.replace(/^#+/, '').trim()).filter(Boolean),
    images: draft.images,
    cover_img_url: draft.coverImgUrl,
  }
}

export function isDraftDirty(draft: WorkContentDraft, baseline: WorkContentDraft): boolean {
  return JSON.stringify(draft) !== JSON.stringify(baseline)
}

/** 图片顺序变了，封面是按 URL 记的，不会跟着错位。 */
export function moveImage(draft: WorkContentDraft, from: number, to: number): WorkContentDraft {
  if (to < 0 || to >= draft.images.length || from === to) return draft
  const images = [...draft.images]
  const [moved] = images.splice(from, 1)
  images.splice(to, 0, moved)
  return { ...draft, images }
}

export function removeImage(draft: WorkContentDraft, index: number): WorkContentDraft {
  if (index < 0 || index >= draft.images.length) return draft
  const images = draft.images.filter((_, idx) => idx !== index)
  const removed = draft.images[index]
  return {
    ...draft,
    images,
    coverImgUrl: draft.coverImgUrl === removed ? images[0] || '' : draft.coverImgUrl,
  }
}

export function setCover(draft: WorkContentDraft, index: number): WorkContentDraft {
  const url = draft.images[index]
  return url ? { ...draft, coverImgUrl: url } : draft
}