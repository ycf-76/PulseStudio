# SDD ledger — plan: docs/superpowers/plans/2026-11-03-side-session-mvp.md

Plan: docs/superpowers/plans/2026-11-03-side-session-mvp.md
Spec: PRD-主任务与侧会话-Side-Session.md (v0.3)
Mode: inline execution (native), chosen by user.
Base repo state: branch codex/design-workflow-orchestration-cards, working directly (user's established workflow; no worktree per prior sessions).
Task 1: complete (tests: pytest tests/test_side_session_api.py::test_side_snapshot_model_exists -> 1 passed). Ruling: 本机 DB 实为 MySQL（.env DATABASE_URL=mysql+aiomysql），计划假设 PostgreSQL——迁移与模型沿用仓库既有方言兼容模式（sa.JSON + JSONB 运行时降级），成本：若未来切 PG，JSON 列仍兼容。
Task 2: complete (commits ea635ec..404a3f8, tests: pytest tests/test_side_session_snapshot.py -> 9 passed). Ruling: 同毫秒生成的 ULID 随机位导致 id 顺序不稳定——baseline 比较在生产中跨真实轮次（间隔远大于 1ms）成立，测试用 from_timestamp 逐毫秒生成有序 ULID；成本：同毫秒内连发两条用户消息时过期计数可能偏差 1。
Task 3: complete (commits 404a3f8..85c4963, tests: pytest tests/test_side_session_api.py -> 10 passed). Ruling: 路由层的 AsyncSessionLocal 改为经服务模块属性访问（side_session.AsyncSessionLocal），使测试 monkeypatch 生效；生产路径等价。Ruling: 侧会话下再建侧会话 -> 400（kind 校验在 create 端点），_get_owned_main 仅做存在性+归属校验。注：pytest 实际 4-10s 完成，但 PowerShell 包装器常无法感知退出导致超时转后台——后续统一用重定向到文件再读取的方式跑测试。
Task 4: complete (tests: pytest side-session x4 files -> 30 passed). Ruling: 侧会话判定用 get_snapshot 非空（快照与侧会话同事务创建），不额外查询 kind；基准轮次 baseline_turn 存入快照 content（结构新增键，向后兼容）。成本：无快照的异常侧会话按主会话处理。
Task 5: complete (tests: pytest test_side_guard + test_side_session_api -> 25 passed). Ruling: skill_call 按 inner skill 声明权限判定，无法判定按写处理（争议项按写，PRD §12）。Ruling: 批准恢复用 allow_once 签名逐次放行，LLM 重发该调用后放行一次；拒绝则注入只读 observation 续跑。Ruling(存量): pytest 在本机收尾阶段挂起为既有现象（clarification 集成测试同样如此），与本次改动无关。
Task 6: complete (tests: pytest test_side_session_api + test_side_session_snapshot -> 24 passed).
Task 7: complete (tests: vitest src/stores/sideSession.test.ts -> 7 passed, commit 35bc6e6). 偏差记录：SideSessionCells.test.ts 纯函数与实现同批落地，未经独立 RED 步骤（TDD 偏差，已承认）。计划中 Task 7 的 ChatViewPage.vue 面板部分因该文件为薄壳（实际 UI 在 ChatView.vue）顺延至 Task 8 一起接线。
Task 8: complete (commit 5724389, tests: vitest 全量 268 passed / 23 files；vue-tsc -b 无错误；pytest 侧会话 4 文件 45 passed). Ruling: 计划写 ChatViewPage.vue，实际渲染链路在 ChatView.vue——面板/创建弹窗/过期横幅/徽标条均落 ChatView.vue（计划 Task 8 已预留"按实际渲染链路落点插入"）。Ruling: 后端 list_sessions 增加 kind 字段、侧会话列表/创建响应增加 baseline_turn，前端据此把侧会话从主会话侧边栏过滤，只进专用面板。面板组件 SideSessionPanel.vue 带 4 个挂载测试（RED→GREEN）。写确认链路：SSE side_write_confirmation_required → agentMeta.sideWrite → AssistantMessageCell 内 SideWriteConfirmDialog（批准/拒绝各调一次 /side-write-confirm）→ sideWriteResolved 回写退场。
Final: review（独立 subagent 审 82146f9..5724389）发现 2 Critical + 4 Important + 4 Minor；Critical/Important 全部 RED→GREEN 修复，commit df88f28：
  #1 finalize 越权（IDOR）→ finalize_main_session 增加 user_id 归属+parent 校验，无关 side_id 结果为 "skipped"；
  #2 并发确认竞态（写副作用重复执行）→ confirm 端点加每侧会话 asyncio.Lock + 锁内重读 pending，后到者 409；
  #3 side_write_paused 落库为 completed（刷新后确认死锁）→ chat_agent 增加 awaiting_side_write_confirmation ChatResult 分支 + 路由层同构落库（loop_state_path/side_write 持久化）；
  #4 /agent 不校验 archived 侧会话 → 1.3 步加 403 只读拦截；
  #5 快照取前 200 条而非最新 → _list_messages_in 倒序取最新再反转，baseline 落最新消息；
  #6 loop_state_path 路径穿越 → load_loop_state 加状态目录包含性校验（../ 与绝对路径均拒）。
  Ruling: RED 测试额外暴露 review 未发现的硬 bug——side_session 路由 include_router 在 confirm 端点定义之前执行，写确认路由从未注册（线上确认必 404）；include 移至文件末尾修复。
  最终测试：pytest 侧会话+clarification 57 passed；回归（chat_agent/chat_session_db/cut_plan_gate/send_latest/storyboard/resume_turn_id/codex_boundary）35 passed 2 skipped；vitest 全量 268 passed；vue-tsc 无错误。
Deferred minors（review Minor #7-10，不阻塞合入）：#7 活跃侧会话上限 5 无锁（并发创建可能超限）；#8 bring-back 422 依赖错误文案匹配；#9 PATCH 改名无空串/长度校验；#10 带回摘要 role=user 计入过期轮次。测试缺口：无「暂停后刷新恢复弹窗」端到端用例。
Post-MVP UX 改版（用户指令：面板本身即迷你对话窗，不再切换聊天区；commit 见 git log）：
  ① 面板弹窗裁剪修复（commit abb8a0e）：.ssp-panel 固定 height:440px（原 max-height 塌缩），创建弹窗内嵌滚动兜底。
  ② SideSessionPanel.vue 重写为 list/chat 双模式：列表模式管理侧会话；点击条目 enterChat 进入面板内对话（拉 listMessages 渲染历史气泡 + 底部输入框），「对话历史」按钮返回列表。输入走 POST /api/v1/chat/agent（sendSideAgentMessage），正文增量走新模块 sideEventStream.ts（openSideEventStream：fetch 流订阅 /api/sse/chat/{id}，分发 agent_message_delta/side_write_confirmation_required/agent_loop_done）；写确认卡内联在气泡流中（批准/拒绝后重开事件流跟随后台续跑）。emits 收敛为 close/brought-back，不再 emit open-session。
  ③ ChatView.vue 清理：删除 openSideSession/backToMain/currentIsSide/currentSideBadge/GRANULARITY_SHORT、模板 rl-side-bar 徽标条、@open-session 监听、watch 中 activeSideId 判断；onBroughtBack 简化为 select(null)+switchToConversation(mainId, mergeLoadedHistory)；chat-view.css 移除 .rl-side-bar 系列样式。
  测试：SideSessionPanel.test.ts 新增 3 用例（面板内进入对话不发 open-session / 发送调 agent 渲染回复 / 对话历史按钮返回列表），7/7 passed；全量 vitest 271 passed / 23 files；vue-tsc -b 0 错误。
