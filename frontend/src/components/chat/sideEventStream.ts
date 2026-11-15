/**
 * 侧会话 SSE 事件流（面板内联聊天用）。
 *
 * 与主聊天 useChatSSE 的重渲染管线不同：面板只需要极薄的一层——
 * 正文增量、写确认请求、回合结束三个事件。GET /api/sse/chat/{id}
 * 不带 turn_id 时从订阅时刻起只推新事件，天然覆盖"发出消息"与
 * "批准后后台续跑"两个阶段。
 */
import { authFetch } from '@/api/client'

export interface SideWriteRequestInfo {
  tool_name: string
  description: string
  permissions: string[]
  loop_state_path: string
}

export interface SideStreamHandlers {
  onDelta?: (text: string) => void
  onSideWriteRequired?: (info: SideWriteRequestInfo) => void
  onLoopDone?: (payload: Record<string, unknown>) => void
}

export interface SideStreamHandle {
  close: () => void
}

export function openSideEventStream(sideSessionId: string, handlers: SideStreamHandlers): SideStreamHandle {
  const controller = new AbortController()
  let closed = false

  ;(async () => {
    try {
      const res = await authFetch(`/api/sse/chat/${sideSessionId}`, {
        signal: controller.signal,
      })
      if (!res.ok || !res.body) return
      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      while (!closed) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        // SSE 事件以空行分隔；逐块解析 event:/data: 行
        let sep: number
        while ((sep = buffer.indexOf('\n\n')) !== -1) {
          const block = buffer.slice(0, sep)
          buffer = buffer.slice(sep + 2)
          _dispatch(block, handlers)
        }
      }
    } catch (e: unknown) {
      if (!closed && !(e instanceof Error && e.name === 'AbortError')) {
        console.warn('[sideEventStream] stream error:', e)
      }
    }
  })()

  return {
    close() {
      closed = true
      controller.abort()
    },
  }
}

function _dispatch(block: string, handlers: SideStreamHandlers): void {
  let event = 'message'
  const dataLines: string[] = []
  for (const line of block.split('\n')) {
    if (line.startsWith('event:')) event = line.slice(6).trim()
    else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim())
  }
  if (dataLines.length === 0) return
  let payload: Record<string, unknown> = {}
  try {
    payload = JSON.parse(dataLines.join('\n'))
  } catch {
    return
  }
  if (event === 'agent_message_delta') {
    handlers.onDelta?.(String(payload.delta ?? ''))
  } else if (event === 'side_write_confirmation_required') {
    handlers.onSideWriteRequired?.({
      tool_name: String(payload.tool_name ?? ''),
      description: String(payload.description ?? ''),
      permissions: Array.isArray(payload.permissions) ? (payload.permissions as string[]) : [],
      loop_state_path: String(payload.loop_state_path ?? ''),
    })
  } else if (event === 'agent_loop_done') {
    handlers.onLoopDone?.(payload)
  }
}
