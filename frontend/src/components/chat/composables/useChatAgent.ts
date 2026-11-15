import type { Ref } from 'vue'
import type { Conversation, ChatMessage, TurnPhase } from '../cell-types'
import { sanitizeContent, parseHttpErrorMessage } from '../cell-types'
import { AGENT_NODE_LABELS, formatAgentOutput, isReactProtocolJson } from '../agent-output-formatter'
import { normalizeCardDraft, pickCoverUrl, pickImages } from '@/composables/cardDraft'
import * as chatSessionsApi from '@/api/chatSessions'
import { authFetch } from '@/api/client'
import { genTurnId } from '@/render/turn-runtime'
import { getSessionRuntime, adoptSessionRuntime } from './chatSessionRuntime'

export interface ChatAgentDeps {
  activeWork: Ref<any>
  activeFile: Ref<any>
  workStore: any
  fileStore: any
  workspaceStore: any
  pickers: any
  sse: any
  stream: any
  showWorkDetail: Ref<boolean>
  creativePhase: Ref<string>
  modelSettings: any
  autoApproveEnabled: Ref<boolean>
  sendMessageReal: (conv: Conversation, startTime: number, sid: string | null, assistantMsg: ChatMessage) => Promise<void>
  ensureSession: (title?: string) => Promise<string | null>
  linkSession: (sid: string) => void
  transitionTurn: (phase: string, label?: string) => void
  /** 会话级回合转场：后台会话的收尾只改它自己的运行时 */
  transitionTurnScoped?: (phase: TurnPhase, label: string, sessionId: string | null, msg?: ChatMessage | null) => void
  /** 会话 → 绑定的作品 id（产物隔离：草稿必须落到该会话自己的作品上） */
  resolveWorkIdForSession?: (sessionId: string) => string | null
}

export function useChatAgent(deps: ChatAgentDeps) {
  const {
    activeWork, activeFile, workStore, fileStore, workspaceStore,
    pickers, sse, stream,
    showWorkDetail, creativePhase, modelSettings, autoApproveEnabled,
    sendMessageReal, ensureSession, linkSession, transitionTurn, transitionTurnScoped,
  } = deps

  /**
   * 当前对话所属文件夹的上下文。
   * 只透传「身份」：文件夹名 + 本地绝对路径（= Codex 的 cwd）。
   * 目录里的文件一律不在前端预扫描——模型要用时自己调 glob / grep / bash /
   * file_read 去读（Claude Code / Codex 都是这个范式：环境只给 cwd，
   * 内容靠工具按需取，避免大目录把 token 撑爆）。
   * ws- 前缀文件夹对应本地工作区，文件在真实磁盘上；其余是纯对话文件夹，
   * 文件存在数据库里、工具触达不到，才随清单带上正文（后端有长度上限）。
   */
  function buildFolderContext(): Record<string, any> | undefined {
    const folderId: string = fileStore.activeFolderId || ''
    if (!folderId || folderId === 'chat-files') return undefined

    const folder = (fileStore.folders || []).find((f: any) => f.id === folderId)
    const isWs = folderId.startsWith('ws-')
    const wsId = isWs ? folderId.slice(3) : undefined

    const virtualFiles = isWs
      ? []
      : (fileStore.getFilesForFolder(folderId) || []).map((f: any) => ({
        name: f.name,
        type: f.type || 'file',
        size: f.size,
        contentText: f.contentText || undefined,
      }))

    const wsLocalPath = isWs
      ? ((workspaceStore.workspaces || []).find((w: any) => w.id === wsId)?.local_path || '')
      : ''

    return {
      folderId,
      name: folder?.name || '',
      path: folder?.path || wsLocalPath,
      workspaceId: wsId,
      files: virtualFiles,
    }
  }

  /**
   * 卡片草稿落地（产物隔离）：只写进本会话绑定的作品，
   * 未绑定时新建一条带 sessionId 的草稿，绝不落到别的会话/作品上。
   */
  function landCardDraft(sessionId: string | null, payload: any, assistantMsg?: ChatMessage | null) {
    if (!payload || !payload.pages || payload.pages.length === 0) return
    const normalized = normalizeCardDraft(payload)
    const pngUrls: string[] = payload.png_urls || normalized.pngUrls
    const htmlUrls: string[] = payload.html_urls || normalized.htmlUrls
    normalized.pngUrls = pngUrls
    normalized.htmlUrls = htmlUrls
    const coverUrl = pickCoverUrl(normalized.pages, pngUrls)
    const images = pickImages(normalized.pages, pngUrls)
    const firstPageHtml = workStore.extractFirstPageHtml(normalized)

    const boundWorkId = sessionId ? (deps.resolveWorkIdForSession?.(sessionId) || '') : ''
    const boundWork = boundWorkId
      ? workStore.works.find((w: any) => w.id === boundWorkId) ||
        workStore.outputWorks.find((w: any) => w.id === boundWorkId)
      : null

    let draftWorkId = ''
    if (boundWork && boundWork.isDraft) {
      workStore.updateDraft(boundWork.id, {
        cardDraft: normalized,
        coverUrl: coverUrl || boundWork.coverUrl,
        images,
        firstPageHtml,
      })
      draftWorkId = boundWork.id
    } else {
      const saved = workStore.saveCardAsDraft(normalized, normalized.title, coverUrl, images, {
        sessionId: sessionId || undefined,
        sourceWorkId: boundWorkId || undefined,
      })
      draftWorkId = saved?.id || ''
    }
    if (assistantMsg?.agentMeta) {
      assistantMsg.agentMeta.cardDraft = normalized
      // 记下草稿作品 id：「侧边栏预览」据此在右侧 ContextSidebar 打开（linkWork），
      // 而不是弹临时抽屉。历史消息无此字段时由 ChatCardDraft 走旧抽屉兜底。
      if (draftWorkId) assistantMsg.agentMeta.cardDraftWorkId = draftWorkId
    }
  }

  async function sendAgentMessage(conv: Conversation, text: string, startTime: number, imageAssetIds: string[] = [], videoAssetIds: string[] = []) {
    // 本回合的运行时在发送瞬间锁定：多任务并行下用户随时可能切到别的会话，
    // 后续所有流式收尾必须落回「这一回合自己的」运行时，而不是当时视图的那个。
    // 草稿态运行时被 adoptSessionRuntime 搬到真实 sessionId 名下时对象不变，引用仍有效。
    const rt = getSessionRuntime(sse.activeSessionId.value)
    // 收尾用的 stream 就地指向「本回合自己的运行时」（遮蔽外层那个按视图会话
    // 动态路由的投影）：用户中途切到别的会话时，这一回合的 notify / 中止控制器
    // 仍然落在自己身上，不会去改别人家的流式状态。
    const stream = rt
    // 回合转场也钉在本会话上：切走后到达的 done/error 只改它自己的状态
    let _turnSid = sse.activeSessionId.value
    const _transTurn = (phase: TurnPhase, label?: string) => {
      if (transitionTurnScoped) transitionTurnScoped(phase, label || '', _turnSid, assistantMsg)
      else transitionTurn(phase, label)
    }
    let assistantMsg: ChatMessage = {
      role: 'assistant',
      timestamp: Date.now(),
      content: '',
      reasoning: '',
      decisions: [],
      progress: [],
      errors: [],
      _streamHtml: '',
      agentMeta: {
        sessionId: null,
        steps: [],
        totalPercent: 0,
        workflowStatus: 'running',
        turnPhase: 'running',
        statusIndicator: { header: '思考中…' },
      },
    }
    conv.messages.push(assistantMsg)
    assistantMsg = conv.messages[conv.messages.length - 1]
    rt.streamingMsg.value = assistantMsg
    _transTurn('running', '思考中…')

    let sid = sse.activeSessionId.value
    if (!sid) {
      try {
        const sessRes = await authFetch("/api/chat/sessions", {
          method: "POST",
          body: JSON.stringify({ title: text.slice(0, 30), work_id: activeWork.value?.id || null }),
        })
        if (sessRes.ok) {
          const sessData = await sessRes.json()
          sid = sessData.data?.id || sessData.id || sessData.session_id
          if (sid) {
            // 立刻把草稿态运行时认领到真实 sessionId 名下：
            // 视图层的 watch 是异步（pre-flush）触发的，晚于下面同步进行的
            // subscribeChatSSE，若不在这里先认领，订阅会落到一个新建的空运行时上，
            // 本回合的 isStreaming / streamingMsg / 中止控制器就与 SSE 分家了。
            adoptSessionRuntime(sid)
            sse.activeSessionId.value = sid
            _turnSid = sid
            // 草稿会话对上真实 session_id（进历史、不再丢消息）
            linkSession(sid)
          }
        }
      } catch {
      }
    }

    // 只收本回合的旧连接（rt 是本会话运行时，切到别的会话也不会误杀）
    if (rt.chatSseController.value) {
      rt.chatSseController.value.abort()
      rt.chatSseController.value = null
    }
    if (rt.workflowSseController.value) {
      rt.workflowSseController.value.abort()
      rt.workflowSseController.value = null
    }
    // 递增消息代，防止旧 SSE replay 事件污染新消息
    // 显式传 sid（草稿态为 null）：只作废本会话的旧订阅，不碰后台会话的消息代
    if (sse.advanceMsgEpoch) sse.advanceMsgEpoch(sid)

    // 回合隔离（L1）：发送前生成 turnId，订阅 URL 带参 + POST 体带回，
    // 后端全链路盖章，前端 TurnRuntime 沙箱二次过滤——二回合订阅零泄漏
    const turnId = genTurnId()
    // 把 turnId 随 agentMeta 入库（JSONB），刷新/恢复后可找回，
    // resume 订阅（useChatInteractions 5 处）即可显式传回，不必靠动态兜底
    if (assistantMsg.agentMeta) {
      assistantMsg.agentMeta.turnId = turnId
    }

    if (sid) {
      const chatSseReady = sse.waitForChatSseReady ? sse.waitForChatSseReady(sid) : Promise.resolve()
      sse.subscribeChatSSE(sid, assistantMsg, 0, turnId).catch(() => {})
      try {
        await Promise.race([
          chatSseReady,
          new Promise<void>((resolve) => setTimeout(resolve, 3000)),
        ])
      } catch {
      }
    }

    const shouldInjectWorkContext = !!activeWork.value
    const _forceWorkCtx = workStore.buildWorkContext()
    const workCtx = shouldInjectWorkContext ? (_forceWorkCtx || undefined) : undefined

    // ── POST 出口收尾助手（数据态与渲染态分离）──
    // SSE 已供内容 → finishStream 等播放器落地；POST 一次性全文 → playFinalText
    // 匀速播出。done 转场/落库/notify 一律放进落地回调：状态不先于文字结束，
    // 落库拿到完整正文而非半段。_persisted 守卫防 SSE workflow_completed 与
    // POST 分支双落库。
    const _persistOnce = (msg: ChatMessage) => {
      if (msg._persisted) return
      msg._persisted = true
      if (sid) {
        chatSessionsApi.addMessage(sid, 'assistant', msg.content, msg.agentMeta).catch(() => {})
      }
    }
    const _doneAfterSettle = (msg: ChatMessage, notify: () => void) => {
      if (msg.agentMeta) msg.agentMeta.workflowStatus = 'completed'
      if (sse.finishStream) {
        sse.finishStream(msg, () => {
          _transTurn('done', '')
          _persistOnce(msg)
          notify()
        })
      } else {
        _transTurn('done', '')
        _persistOnce(msg)
        notify()
      }
    }
    const _playFinalAfterSettle = (msg: ChatMessage, finalText: string, notify: () => void) => {
      if (msg.agentMeta) msg.agentMeta.workflowStatus = 'completed'
      if (sse.playFinalText) {
        sse.playFinalText(msg, finalText, {
          onDone: () => {
            _transTurn('done', '')
            _persistOnce(msg)
            notify()
          },
        })
      } else {
        msg.content = finalText
        _transTurn('done', '')
        _persistOnce(msg)
        notify()
      }
    }

    try {
      const response = await authFetch("/api/v1/chat/agent", {
        method: 'POST',
        body: JSON.stringify({
          message: text,
          mode: 'auto',
          session_id: sid || undefined,
          agent_id: pickers.selectedAgentId.value || undefined,
          model: pickers.modelOverride?.value || undefined,
          workspace_id: workspaceStore.activeWorkspaceId || undefined,
          work_context: workCtx,
          analysis_context: shouldInjectWorkContext ? (workStore.buildAnalysisPrompt() || undefined) : undefined,
          file_context: activeFile.value ? { name: activeFile.value.name, type: activeFile.value.type, size: activeFile.value.size, contentText: activeFile.value.contentText || undefined } : undefined,
          folder_context: buildFolderContext(),
          model_settings: modelSettings || undefined,
          creation_type: workStore.activeCreationType || undefined,
          auto_approve: autoApproveEnabled.value,
          image_asset_ids: imageAssetIds.length > 0 ? imageAssetIds : undefined,
          video_asset_ids: videoAssetIds.length > 0 ? videoAssetIds : undefined,
          turn_id: turnId,
        }),
        signal: rt.getAbortController()?.signal,
      })

      if (!response.ok) {
        if (response.status === 429) {
          assistantMsg.content = '当前并发工作流数量已达上限，请稍后再试'
          assistantMsg.isError = true
          assistantMsg.agentMeta!.workflowStatus = 'error'
          if (sid) {
            chatSessionsApi.addMessage(sid, 'assistant', assistantMsg.content, assistantMsg.agentMeta).catch(() => {})
          }
          rt.notifyImmediateComplete()
          return
        }
        // 错误体先解析成可读文案再抛出：FastAPI 的 4xx/5xx 响应体是
        // {"detail": ...} JSON，若直接透出会被 catch 拼进回复文本变成裸 JSON
        const errText = await response.text()
        throw new Error(parseHttpErrorMessage(response.status, errText))
      }

      const data = await response.json()
      // 会话标识只在「本回合就是当前视图」时才回写：后台会话的任务完成时
      // 不能把用户的视图强行拽到它身上（多任务并行）。
      if (data.session_id) {
        if (_turnSid === sse.activeSessionId.value) sse.activeSessionId.value = data.session_id
        _turnSid = data.session_id
      }

      console.log(`[ChatAgent] API response: status=${data.status}, intent=${JSON.stringify(data.intent)}`)

      if (data.status === 'blocked') {
        assistantMsg.content = '输入未通过安全校验'
        assistantMsg.isError = true
        assistantMsg.agentMeta!.workflowStatus = 'error'
        _transTurn('error')
        stream.notifyImmediateComplete()
        return
      }

      if (data.status === 'error') {
        assistantMsg.content = sanitizeContent(data.message || '处理出错，请稍后重试')
        assistantMsg.isError = true
        assistantMsg.agentMeta!.workflowStatus = 'error'
        _transTurn('error')
        stream.notifyImmediateComplete()
        return
      }

      if (data.status === 'awaiting_clarification') {
        if (!assistantMsg.agentMeta) {
          assistantMsg.agentMeta = {
            sessionId: null, steps: [], totalPercent: 0,
            workflowStatus: 'awaiting_clarification', turnPhase: 'running',
            statusIndicator: { header: '等待偏好确认…' },
          } as any
        }
        assistantMsg.content = sanitizeContent(data.message || data.clarification_prompt || '需要确认创作偏好')
        assistantMsg.agentMeta!.workflowStatus = 'awaiting_clarification'
        if (data.loop_state_path) {
          assistantMsg.agentMeta!.loopStatePath = data.loop_state_path
        }
        if (data.clarification_prompt) {
          assistantMsg.agentMeta!.clarificationPrompt = data.clarification_prompt
        }
        if (data.clarification_skill) {
          assistantMsg.agentMeta!.clarificationSkill = data.clarification_skill
        }
        if (data.clarification_batches) {
          assistantMsg.agentMeta!.clarificationBatches = data.clarification_batches
        }
        console.log(
          '[ChatAgent] awaiting clarification:',
          { skill: data.clarification_skill, hasPath: !!data.loop_state_path }
        )
        _transTurn('running', '等待偏好确认…')
        stream.notifyPaused()
        return
      }

      if (data.status === 'awaiting_confirmation') {
        if (!assistantMsg.agentMeta) {
          assistantMsg.agentMeta = {
            sessionId: null, steps: [], totalPercent: 0,
            workflowStatus: 'awaiting_confirmation', turnPhase: 'running',
            statusIndicator: { header: '等待确认…' },
          } as any
        }
        // 2026-11-02：确认 prompt 原文（含 args JSON）只进 agentMeta.confirmationPrompt
        // 给确认卡展示，绝不写进正文——写进正文后它会残留在确认/续跑后的
        // 最终回复顶部（大哥截图实锤「Tool 'skill_call' is about to execute…」
        // 一直挂在回复里）。awaiting 态正文不渲染，等待行+确认卡已承载信息。
        assistantMsg.content = '此操作需要您的确认才能继续，请在确认卡中处理。'
        assistantMsg.agentMeta!.workflowStatus = 'awaiting_confirmation'
        assistantMsg.agentMeta!.intent = data.intent
        if (data.loop_state_path) {
          assistantMsg.agentMeta!.loopStatePath = data.loop_state_path
        }
        if (data.confirmation_prompt) {
          assistantMsg.agentMeta!.confirmationPrompt = data.confirmation_prompt
        }
        if (data.confirmation_skill) {
          assistantMsg.agentMeta!.confirmationSkill = data.confirmation_skill
        }
        console.log(
          '[ChatAgent] awaiting confirmation:',
          { skill: data.confirmation_skill, hasPath: !!data.loop_state_path }
        )
        _transTurn('running', '等待确认…')
        stream.notifyPaused()
        return
      }

      if (data.status === 'awaiting_storyboard_confirmation') {
        if (!assistantMsg.agentMeta) {
          assistantMsg.agentMeta = {
            sessionId: null, steps: [], totalPercent: 0,
            workflowStatus: 'awaiting_storyboard_confirmation', turnPhase: 'running',
            statusIndicator: { header: '等待分镜确认…' },
          } as any
        }
        assistantMsg.content = sanitizeContent(data.message || '分镜草稿已生成，请确认后再生成视频')
        assistantMsg.agentMeta!.workflowStatus = 'awaiting_storyboard_confirmation'
        if (data.loop_state_path) {
          assistantMsg.agentMeta!.loopStatePath = data.loop_state_path
        }
        if (data.storyboard_shots) {
          assistantMsg.agentMeta!.storyboardShots = data.storyboard_shots
        }
        if (data.storyboard_title) {
          assistantMsg.agentMeta!.storyboardTitle = data.storyboard_title
        }
        if (data.storyboard_duration != null) {
          assistantMsg.agentMeta!.storyboardDuration = data.storyboard_duration
        }
        console.log(
          '[ChatAgent] awaiting storyboard confirmation:',
          { hasPath: !!data.loop_state_path, shots: (data.storyboard_shots || []).length }
        )
        _transTurn('running', '等待分镜确认…')
        stream.notifyPaused()
        return
      }

      if (data.status === 'awaiting_cut_plan_confirmation') {
        if (!assistantMsg.agentMeta) {
          assistantMsg.agentMeta = {
            sessionId: null, steps: [], totalPercent: 0,
            workflowStatus: 'awaiting_cut_plan_confirmation', turnPhase: 'running',
            statusIndicator: { header: '等待叠加方案确认…' },
          } as any
        }
        assistantMsg.content = sanitizeContent(data.message || 'AI 口播叠加方案已规划完成，请确认后开始渲染。')
        assistantMsg.agentMeta!.workflowStatus = 'awaiting_cut_plan_confirmation'
        if (data.loop_state_path) {
          assistantMsg.agentMeta!.loopStatePath = data.loop_state_path
        }
        if (data.cut_plan) {
          assistantMsg.agentMeta!.cutPlan = { plan: data.cut_plan, message: data.message || '' }
        }
        console.log(
          '[ChatAgent] awaiting cut plan confirmation:',
          { hasPath: !!data.loop_state_path }
        )
        _transTurn('running', '等待叠加方案确认…')
        stream.notifyPaused()
        return
      }

      if (data.status === 'awaiting_guardian_confirmation') {
        if (!assistantMsg.agentMeta) {
          assistantMsg.agentMeta = {
            sessionId: null, steps: [], totalPercent: 0,
            workflowStatus: 'awaiting_guardian_confirmation', turnPhase: 'running',
            statusIndicator: { header: '等待操作确认…' },
          } as any
        }
        // 2026-11-02：后端这里的 data.message 就是 guardian 确认 prompt 原文
        // （agents/chat_agent.py: message=output_data.get("confirmation_prompt")），
        // 含 args JSON 与「中等风险操作，需要您确认」——进正文就会残留到
        // 最终回复里（截图实锤）。prompt 走 agentMeta.confirmationPrompt 给确认卡。
        assistantMsg.content = '此操作需要您的确认才能继续，请在确认卡中处理。'
        assistantMsg.agentMeta!.workflowStatus = 'awaiting_guardian_confirmation'
        if (data.loop_state_path) {
          assistantMsg.agentMeta!.loopStatePath = data.loop_state_path
        }
        if (data.confirmation_prompt) {
          assistantMsg.agentMeta!.confirmationPrompt = data.confirmation_prompt
        }
        if (data.risk_info) {
          assistantMsg.agentMeta!.riskInfo = data.risk_info
        }
        console.log(
          '[ChatAgent] awaiting guardian confirmation:',
          { hasPath: !!data.loop_state_path, prompt: data.confirmation_prompt }
        )
        _transTurn('running', '等待操作确认…')
        stream.notifyPaused()
        return
      }

      if (data.status === 'chat') {
        if (data.intent) {
          assistantMsg.agentMeta!.intent = data.intent
        }
        if (data.message && data.message.trim()) {
          const finalText = sanitizeContent(data.message)
          const _sseAlreadyProvided = (assistantMsg as any)._contentFromSSE === true
          const _hasStreamContent = (assistantMsg.content || '').length > 10
          if (_sseAlreadyProvided || _hasStreamContent) {
            // SSE 已供内容：等播放器落地再转 done + 落库（8.5 补丁）
            console.log('[ChatAgent] chat status: SSE already provided content, waiting for player settle. SSE content length=', assistantMsg.content?.length, ', POST response length=', finalText.length)
            _doneAfterSettle(assistantMsg, () => stream.notifyAgentLoopDone(10_000))
          } else if (finalText.length > 0) {
            // 无 SSE 内容：POST 全文走播放器匀速播出（旧版直接赋 content 整段蹦出）
            console.log('[ChatAgent] chat status: no SSE content, playing POST response. length=', finalText.length)
            _playFinalAfterSettle(assistantMsg, finalText, () => stream.notifyAgentLoopDone(10_000))
          }
          return
        }
        const _sseAlreadyProvided = (assistantMsg as any)._contentFromSSE === true
        const _hasStreamContent = !!(assistantMsg as any)._streamHtml || (assistantMsg.content && assistantMsg.content.length > 10)
        console.log('[ChatAgent] chat status check:', {
          _sseAlreadyProvided,
          _hasStreamContent,
          contentLength: assistantMsg.content?.length,
          hasStreamHtml: !!(assistantMsg as any)._streamHtml,
        })
        if ((_sseAlreadyProvided || _hasStreamContent) && assistantMsg.content && assistantMsg.content.length > 5) {
          console.log('[ChatAgent] SSE/stream already provided content for chat status, waiting for player settle')
          _doneAfterSettle(assistantMsg, () => stream.notifyAgentLoopDone(10_000))
          return
        }
        console.log('[ChatAgent] no SSE content yet, calling sendMessageReal')
        await sendMessageReal(conv, startTime, sid, assistantMsg)
        return
      }

      if (data.status === "shortcut_completed") {
        let content = data.message || '已完成'
        if (data.wechat_push) {
          if (data.wechat_push === 'sent') {
            content += '\n\n[done] 已推送到微信'
          } else if (data.wechat_push.startsWith('skipped:')) {
            content += `\n\n[warn] 微信推送跳过：${data.wechat_push.slice(8)}`
          } else if (data.wechat_push.startsWith('error:')) {
            content += `\n\n[error] 微信推送失败：${data.wechat_push.slice(6)}`
          }
        }
        assistantMsg.agentMeta!.workflowStatus = "completed"
        // 全文走播放器匀速播出；SSE 事件路径已落地过则不重播（防二次打字动画）
        const _scFinal = sanitizeContent(content)
        if ((assistantMsg as any)._contentFromSSE === true) {
          console.log('[ChatAgent] shortcut_completed: SSE already landed content, settle only')
          if (sse.finishStream) {
            sse.finishStream(assistantMsg, () => { _transTurn('done', '') })
          } else {
            _transTurn('done', '')
          }
        } else if (sse.playFinalText) {
          sse.playFinalText(assistantMsg, _scFinal, {
            onDone: () => { _transTurn('done', '') },
          })
        } else {
          assistantMsg.content = _scFinal
          _transTurn('done', '')
        }
        assistantMsg.agentMeta!.intent = data.intent
        if (data.plan_steps && data.plan_steps.length > 0) {
          assistantMsg.agentMeta!.steps = (data.plan_steps as string[]).map((s: string) => ({
            nodeKey: s,
            nodeLabel: AGENT_NODE_LABELS[s] || s,
            status: 'completed' as const,
            percent: 100,
          }))
        }
        if (data.card_draft && data.card_draft.pages && data.card_draft.pages.length > 0) {
          try {
            landCardDraft(sid, data, assistantMsg)
          } catch (e) {
            console.error('[ChatAgent] draft panel update failed:', e)
          }
        }
        if (data.creative_state) {
          creativePhase.value = data.creative_state.phase || 'idle'
          if (data.creative_state._card_draft && data.creative_state._card_draft.pages && data.creative_state._card_draft.pages.length > 0) {
            try {
              landCardDraft(sid, data.creative_state._card_draft, assistantMsg)
            } catch (e) {
              console.error('[ChatAgent] SSE creative_state._card_draft panel update failed:', e)
            }
          }
        }
        if (data.created_files && Array.isArray(data.created_files)) {
          for (const cf of data.created_files) {
            fileStore.addBackendFile(cf)
          }
          await fileStore.loadFilesForSession(sid || '')
        }
        stream.notifyImmediateComplete()
        return
      }

      if (data.status === "agent_output" && data.output) {
        const output = data.output
        const _sseAlreadyUpdated = (assistantMsg as any)._contentFromSSE === true
        // 纵深防御：SSE 曾把 ReAct 协议 JSON 灌进正文（reactor L550 历史缺陷
        // 的产物，或未来其他发射源回归）——那不是用户内容，是无头僵尸。
        // 命中即丢弃流式残留（停播 + 空引擎重建）并复位 _contentFromSSE，
        // 强制走下面的人话格式化链，杜绝裸 JSON 滞留屏幕（用户最初反馈
        // 「后端产出都不能渲染」正是此形态）。判定只看协议签名键，散文不误伤。
        const _protocolPolluted = isReactProtocolJson(assistantMsg.content || '')
        if (_protocolPolluted) {
          console.log('[ChatAgent] agent_output: discarding ReAct protocol JSON pollution from stream, length=', (assistantMsg.content || '').length)
          if (sse.discardStreamContent) sse.discardStreamContent(assistantMsg)
          ;(assistantMsg as any)._contentFromSSE = false
        }

        if (output.card_draft && output.card_draft.pages && output.card_draft.pages.length > 0) {
          try {
            landCardDraft(sid, output, assistantMsg)
          } catch (e) {
            console.error('[ChatAgent] draft panel update failed:', e)
          }
        }

        if (data.creative_state) {
          creativePhase.value = data.creative_state.phase || 'idle'
          if (data.creative_state._card_draft && data.creative_state._card_draft.pages && data.creative_state._card_draft.pages.length > 0) {
            try {
              landCardDraft(sid, data.creative_state._card_draft, assistantMsg)
            } catch (e) {
              console.error('[ChatAgent] creative_state._card_draft panel update failed:', e)
            }
          }
        }

        if (!_sseAlreadyUpdated || _protocolPolluted) {
          // 污染分支下 discardStreamContent 已把 content 清空，此处读到的
          // 要么是合法流式散文（>10 跳过覆盖），要么是空串（直接走格式化）
          const _existingContent = assistantMsg.content || ''
          let _aoFinal: string | null = null
          if (_existingContent.length > 10) {
            console.log('[ChatAgent] agent_output: existing content from stream, skip overwrite. existing length=', _existingContent.length)
          } else if (output.results && Array.isArray(output.results)) {
            const count = output.results.length
            let content = ''
            if (output.summary && output.summary.length > 10) {
              content = output.summary
            } else if (output.patterns && output.insights) {
              content = `分析完成，共 ${count} 条结果\n\n`
              if (output.patterns.title_patterns?.length) {
                content += `**标题钩子**: ${output.patterns.title_patterns.map((p: any) => p.type || p.template || '').filter(Boolean).join('、')}\n`
              }
              if (output.patterns.content_structures?.length) {
                content += `**内容结构**: ${output.patterns.content_structures.map((s: any) => s.structure || s.description || '').filter(Boolean).join('、')}\n`
              }
              if (output.insights.recommendations?.length) {
                content += `**选题方向**:\n`
                for (const rec of output.insights.recommendations) {
                  content += `- ${rec.topic_direction || ''}${rec.title_template ? ` → ${rec.title_template}` : ''}\n`
                }
              }
            } else {
              content = `搜索完成，找到 ${count} 条结果`
              const top5 = output.results.slice(0, 5)
              for (const r of top5) {
                content += `\n- **${r.title || '无标题'}** likes:${r.likes || 0} comments:${r.comments || 0}`
              }
              if (count > 5) content += `\n…共 ${count} 条`
            }
            _aoFinal = sanitizeContent(content)
          } else if (output._error) {
            _aoFinal = '[warn] 处理过程中遇到问题，请稍后重试'
            assistantMsg.isError = true
          } else if (output.summary && output.summary.length > 0) {
            _aoFinal = sanitizeContent(output.summary)
          } else if (data.message && data.message.trim() && !/没有产出文本结果/.test(data.message)) {
            // 后端 _humanize_output 的人话正文兜底链（summary→_message→content→raw）
            // 已算好结果——output.summary 为空但 data.message 有货时直接用，
            // 根治"最终结果有时不渲染"（LLM final JSON 字段名波动时 summary 落空）
            _aoFinal = sanitizeContent(data.message)
          } else {
            _aoFinal = sanitizeContent(formatAgentOutput(output))
          }
          if (_aoFinal) {
            // POST 出口全文匀速播出，done 收尾等落地（旧版直接赋 content 整段蹦出）
            _playFinalAfterSettle(assistantMsg, _aoFinal, () => stream.notifyAgentLoopDone(10_000))
          } else {
            _doneAfterSettle(assistantMsg, () => stream.notifyAgentLoopDone(10_000))
          }
        } else {
          console.log('[ChatAgent] Skipping content update - SSE already provided content')
          _doneAfterSettle(assistantMsg, () => stream.notifyAgentLoopDone(10_000))
        }

        if (data.intent) {
          assistantMsg.agentMeta!.intent = data.intent
        }
        if (data.thinking) {
          if (!assistantMsg.reasoning) assistantMsg.reasoning = data.thinking
        }
        assistantMsg.agentMeta!.workflowStatus = 'completed'

        if (data.output?.created_files && Array.isArray(data.output.created_files)) {
          for (const cf of data.output.created_files) {
            fileStore.addBackendFile(cf)
          }
          await fileStore.loadFilesForSession(sid || '')
        }
      } else if (!assistantMsg.content) {
        assistantMsg.content = sanitizeContent(data.message || '处理完成')
        assistantMsg.agentMeta!.workflowStatus = 'completed'
        _transTurn('done', '')
        stream.notifyAgentLoopDone(10_000)
      } else {
        stream.notifyAgentLoopDone(10_000)
      }
    } catch (err: any) {
      // err.message 已由 parseHttpErrorMessage 解析为可读文案（见上方 HTTP 失败分支），
      // sanitizeContent 兜底清理残留的 [object Object] 等噪声
      assistantMsg.content = sanitizeContent('Agent 执行失败: ' + (err.message || '未知错误'))
      assistantMsg.isError = true
      assistantMsg.agentMeta!.workflowStatus = 'error'
      if (sid) {
        chatSessionsApi.addMessage(sid, 'assistant', assistantMsg.content, assistantMsg.agentMeta).catch(() => {})
      }
      stream.notifyImmediateComplete()
    }
  }

  return { sendAgentMessage }
}