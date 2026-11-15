/**
 * 飞书云文档网页组件（Doc Component SDK）鉴权 API。
 * 后端路由：backend/app/api/routers/feishu_docs.py
 */
import apiClient from './client'

export interface FeishuSdkSignature {
  appId: string
  signature: string
  timestamp: number
  nonceStr: string
  url: string
  openId: string | null
  identity: 'user' | 'app'
}

/** 获取组件 SDK 签名（签名 10 分钟有效且单次使用，每次渲染前都要取） */
export async function fetchSdkSignature(pageUrl: string): Promise<FeishuSdkSignature> {
  const res: any = await apiClient.get('/feishu/docs/sdk-signature', {
    params: { page_url: pageUrl },
  })
  return res?.data
}

/** 判断是否为可在云文档组件中渲染的飞书文档链接（聊天触发层与面板共用） */
export function isFeishuDocUrl(url: string): boolean {
  return /^https:\/\/([a-z0-9-]+\.)?(feishu\.cn|larksuite\.com)\/(docx|wiki|doc|docs|sheets|base)\/[A-Za-z0-9]+/i.test(
    url.trim()
  )
}

// ==================== 多维表格（/base/） ====================
// 官方 DocComponentSdk 不支持 bitable 链接（错误码 -100），
// 由后端 OpenAPI 拉取数据，前端自渲染表格视图。

export interface BitableUrlInfo {
  appToken: string
  tableId?: string
}

/** 解析 base 链接，非 base 链接返回 null（docx/wiki 仍走 SDK 组件） */
export function parseBitableUrl(url: string): BitableUrlInfo | null {
  const trimmed = url.trim()
  const m = trimmed.match(/^https:\/\/([a-z0-9-]+\.)?(feishu\.cn|larksuite\.com)\/base\/([A-Za-z0-9]+)/i)
  if (!m) return null
  let tableId: string | undefined
  try {
    tableId = new URL(trimmed).searchParams.get('table') || undefined
  } catch {
    /* 非 URL 格式则忽略 table 参数 */
  }
  return { appToken: m[3], tableId }
}

export interface BitableFieldMeta {
  fieldId: string
  fieldName: string
  type: number
  uiType: string
  isPrimary: boolean
}

export interface BitableData {
  appToken: string
  tableId: string
  tableName: string
  fields: BitableFieldMeta[]
  records: Record<string, string>[]
  truncated: boolean
}

/** 拉取多维表格字段与记录（已扁平化为展示文本） */
export async function fetchBitableData(url: string): Promise<BitableData> {
  const res: any = await apiClient.get('/feishu/docs/bitable-data', {
    params: { url },
  })
  return res?.data
}
