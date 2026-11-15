export interface AccountResponse { account_id: string; status: string; login_method: string }
export interface SSEEvent { event_id: string; type: string; payload: any; timestamp: string }

export type { Plugin, PluginCategory, PluginStatus, PricingModel, PluginStats, PluginVersion, PluginConfig, PluginReview, PluginListParams } from '@/api/plugins'

// ─── ContentPack：创作层与发布层的统一中间格式 ───

/** 创作模式 */
export type CreationMode = 'ai_full' | 'template_overlay' | 'direct' | 'diy'

/** 目标平台 */
export type TargetPlatform = 'xiaohongshu' | 'weibo' | 'wechat_mp' | 'instagram' | 'zhihu' | 'douyin'

/** 图片素材（含可选文字叠加） */
export interface ContentImage {
  url: string
  base64?: string
  overlays?: Array<{
    id: string
    text: string
    x: number
    y: number
    width: number
    fontSize: number
    color: string
    fontWeight: string
    textAlign: string
    lineHeight?: number
    backgroundColor?: string
    borderRadius?: number
    padding?: number
    letterSpacing?: number
    textShadow?: string
  }>
}

/** 结构化文案 */
export interface ContentCopy {
  title: string
  body: string
  tags: string[]
  ctaText?: string
}

/** 内容包：创作层产出 → 适配层输入 */
export interface ContentPack {
  images: ContentImage[]
  copy: ContentCopy
  cardPages?: any[]
  meta: {
    mode: CreationMode
    category?: string
    targetPlatforms: TargetPlatform[]
    sourceWorkflowId?: string
  }
}

/** 平台适配器接口 */
export interface PlatformAdapter {
  platform: TargetPlatform
  transform(pack: ContentPack): Promise<PlatformPayload>
  publish(payload: PlatformPayload, accountId: string): Promise<PlatformPublishResult>
}

/** 适配后的平台载荷（各平台不同，由适配器自行定义子类型） */
export interface PlatformPayload {
  platform: TargetPlatform
  [key: string]: any
}

/** 平台发布结果 */
export interface PlatformPublishResult {
  success: boolean
  postId?: string
  message?: string
  platform: TargetPlatform
}