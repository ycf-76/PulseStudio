import { listAssets, type ImageAsset } from '@/api/assets'
import type { Conversation } from '../cell-types'
import { peekSessionRuntime } from './chatSessionRuntime'

/**
 * 历史消息同步 — ChatView 拆出
 *
 * 后端只存了正文（含 [本次配图素材] asset_ids=["x","y"]），没有存 attachments。
 * 刷新后要让"图片随消息发出"这点保持一致，就得从正文反解 id，
 * 再拿 /assets 列表把缩略图 URL 查回来。列表按会话只拉一次并缓存。
 */
export function useChatHistorySync(deps: {
  normalizeAgentMeta: (raw: any) => any
}) {
  const { normalizeAgentMeta } = deps

  let _assetIndexPromise: Promise<Map<string, ImageAsset>> | null = null
  function getAssetIndex(): Promise<Map<string, ImageAsset>> {
    if (!_assetIndexPromise) {
      // 只取最近 300 张：回填用的是会话里刚发过的图，倒序取最近的最可能命中，
      // 同时避免把用户整个素材库拖回前端（后端不传 limit 是全表返回）。
      _assetIndexPromise = listAssets(300)
        .then(list => new Map((list || []).map(a => [a.asset_id, a])))
        .catch(() => new Map<string, ImageAsset>())
    }
    return _assetIndexPromise
  }

  function extractAssetIdsFromHistory(content: string): string[] {
    if (typeof content !== 'string') return []
    const m = content.match(/\[本次配图素材\][\s\S]*?asset_ids=(\[[^\]]*\])/)
    if (!m) return []
    try {
      const parsed = JSON.parse(m[1])
      return Array.isArray(parsed) ? parsed.filter((x: unknown) => typeof x === 'string') : []
    } catch {
      return []
    }
  }

  /** 把历史用户消息里的 asset_ids 回填成 attachments（异步，查到后直接改消息对象触发响应） */
  function rehydrateHistoryAttachments(msgs: Array<{ role: string; content: string; [k: string]: any }>) {
    const pending = msgs.filter(
      m => m.role === 'user' && typeof m.content === 'string' && m.content.includes('[本次配图素材]'),
    )
    if (pending.length === 0) return
    void getAssetIndex().then(index => {
      if (index.size === 0) return
      for (const m of pending) {
        const ids = extractAssetIdsFromHistory(m.content)
        if (ids.length === 0) continue
        const atts = ids
          .map(id => index.get(id))
          .filter((a): a is ImageAsset => !!a)
          .map(a => ({
            asset_id: a.asset_id,
            filename: a.filename,
            content_type: a.content_type,
            original_url: a.original_url,
            thumbnail_url: a.thumbnail_url,
            width: a.width,
            height: a.height,
          }))
        if (atts.length > 0) m.attachments = atts
      }
    })
  }

  function mergeLoadedHistory(conv: Conversation, apiMsgs: any[]) {
    const loaded = apiMsgs.map(m => ({
      role: m.role as 'user' | 'assistant' | 'system',
      content: m.content,
      timestamp: m.created_at ? new Date(m.created_at).getTime() : undefined,
      agentMeta: normalizeAgentMeta(m.agent_meta || undefined) as any,
    })).filter(m => m.role !== 'system')
    // 历史里的图片消息：从正文反解 asset_ids → 回填 attachments，刷新后照样能看到图
    rehydrateHistoryAttachments(loaded as any)
    // 防御性降级：历史中残留的 awaiting_* 消息已不可能再交互（后端循环早已
    // 落盘停跑/超时解除），降级为 completed，避免 AssistantMessageCell 的
    // immediate watch 把死确认注册成输入框上方的 dock 弹窗——
    // 此前只降级 awaiting_clarification，guardian/confirm/storyboard 漏网，
    // 导致每次进会话都弹「需要你的确认」。真正活跃的暂停态由 SSE 事件
    // （workflow_paused / guardian_request 等）实时重建，不依赖历史消息。
    const _AWAITING_STATUSES = new Set([
      'awaiting_clarification',
      'awaiting_confirmation',
      'awaiting_guardian_confirmation',
      'awaiting_storyboard_confirmation',
      'awaiting_cut_plan_confirmation',
    ])
    // 多任务并行后要留活口：该会话在本标签页仍有在途运行时（正在跑或暂停
    // 等确认）时，awaiting_* 是活状态，降级会把它的确认入口抹掉 → 服务端空等。
    const _liveRuntime = peekSessionRuntime(conv.id)?.isStreaming.value
    if (!_liveRuntime) {
      for (const m of loaded) {
        if (m.role === 'assistant' && _AWAITING_STATUSES.has(m.agentMeta?.workflowStatus)) {
          m.agentMeta.workflowStatus = 'completed'
        }
      }
    }
    const existing = new Set(conv.messages.map(m => `${m.role}:${m.content}`))
    const fresh = loaded.filter(m => !existing.has(`${m.role}:${m.content}`))
    conv.messages = [
      ...fresh,
      ...conv.messages.filter(m => m.role !== 'system'),
    ]
  }

  return {
    getAssetIndex,
    extractAssetIdsFromHistory,
    rehydrateHistoryAttachments,
    mergeLoadedHistory,
  }
}