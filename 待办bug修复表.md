# 🐛 待办 Bug 修复表

> 生成时间：2026-10-23（第二轮实锤验证）
> 来源：全链路诊断（API端点 → ChatAgent → ReactorSession → LLM适配器 → 技能系统 → Guardian → 前端SSE）
> 方法：每个 Bug 均通过编写诊断脚本实际运行验证，非猜测
> 第一轮误报已剔除：BUG-001(user_id)、BUG-005(前端缺confirm)、BUG-009(copywrite) 经实测推翻或降级

---

## 🔍 2026-10-30 第三轮复核（代码走读实据，dsh→rl 重构后）

| Bug | 复核结论 | 代码证据 |
|-----|---------|---------|
| BUG-001 | ⚠️ 存疑待真机 | `.env` 2026-10-27 18:34 被改过，key 前缀仍 `sk-ws-`；10-23 实测 401。离线无法验证有效性，探活需真机（大哥手动跑） |
| BUG-002 | ✅ 基本已修 | `capabilities/content_search/sources/manager.py` L195-198 SogouSource 无条件注册、L224 HackerNews 注册、L289 hotboard（.env HOTBOARD_ENABLED=true）、L275 XhsSpiderSource 按 storage state 注册——`backend/app/storage_states/xiaohongshu.json` 实存。链路已不依赖 XHS_COOKIE 环境变量。残留：登录态时效性无法离线判断 |
| BUG-003 | ✅ 已修 | `agents/registry.py` L368 `skill_instances = _build_skills(agent_def.skills, creation_type)`；skills=None+creation_type → 映射过滤，皆 None → 全量兜底 |
| BUG-004 | ✅ 已消歧（有副作用） | `skills/` 下 attribute 包整个没了，`grep name="comment_insights"` 仅 analyze 一份。副作用：map 里 data_tracker/strategy_advisor/content_postmortem 等名已无对应技能，缓存过滤静默跳过——不崩但白名单是空的 |
| BUG-005 | ❌ 仍成立 | `tools/skill_router.py` L144 `enforce_priority: bool = False`；registry.py build_session 无 SkillRouter 调用。待拍板：creation_type 过滤已上线，token 压力已降，接不接是产品决策 |
| BUG-006 | ❌ 仍成立 | `reactor/session.py` L1228 `stream_fn(messages)`、L1271 `self.llm.chat(messages)` 均未传 response_format；adapter 支持（`adapters/deepseek.py` L71-84/L135-152）。注意：多数 agent 是 deepseek-v3（支持 json_object），2 个 agent 是 deepseek-r1（不支持）——须按模型条件传 |
| BUG-007 | ❌ 仍成立且加重 | `useChatSSE.ts` L1303-1327 AGENT_LOOP_DONE case 完全不读 ended_reason，token_budget/nudge_exhausted/duplicate_calls 静默当正常完成收尾。且后端新增 `FAKE_FINAL`（events.py L37；session.py L980-983 把 max_iterations/token_budget 转成 fake_final）——表里原本没记，实为四缺 |
| BUG-008 | ❌ 仍成立（待拍板） | `governance/guardian.py` L119-123 file_write needs_confirmation=True/risk=high；`reactor/session.py` L690-714 needs_confirmation → GUARDIAN_PAUSED，不看 auto_approve。属设计行为还是 bug 由大哥定 |
| BUG-009 | ✅ 已修 | `chat_agent.py` L1909-1914 已加 localhost 白名单（非 127.0.0.1/::1 → 403） |
| BUG-010 | ✅ 已修 | `skills/creation_type_map.py` live_clip 已无 bash/glob/grep 引用 |
| BUG-011 | ✅ 已失效（重构消除） | `protocol/events.py` L44-77 EventType 全集无 step_thought/tool_result（有契约测试锁定），后端根本不发，前端不补是对的 |

---

## 🔴 P0 — 必然导致功能不可用

### BUG-001：DashScope API Key 无效 → 图片生成全部失败

- **状态**：☐ 待修复
- **验证方式**：实际调用 DashScope API
- **实测结果**：HTTP 401, `{"code":"InvalidApiKey","message":"Invalid API-key provided."}`
- **现象**：所有需要生成图片的技能（generate_card_image、ai-image-gen 等）都会失败
- **根因**：`.env` 中的 `DASHSCOPE_API_KEY` 格式不正确或已过期（前缀 `sk-ws-H...`）
- **影响**：LLM 调用图片生成技能后拿到错误，会浪费迭代次数或反复重试
- **代码位置**：`backend/.env`
- **验证方法**：调用通义万相 API 返回 200

---

### BUG-002：无内容源注册 + 无 XHS Cookie → 搜索全部返回空

- **状态**：✅ 部分已自愈（2026-11-03 核实）+ 一项仍未修
- **验证方式**：本地离线跑 `init_sources()` + `source_manager.list_platforms()`
- **2026-11-03 实测结果**：
  - `Content sources registered`: **23 个平台**（tavily/sogou/hackernews/github/devto/builtin/xiaohongshu + 7 个 hb-* 热榜源等），default=sogou —— 根因1 **已自愈**（hotboard 源落地 + HOTBOARD_ENABLED=true 后恢复）。当时 trending_search 返回 0 条疑似旧进程未重启，重启后端后待真机复验。
  - `XHS Cookie configured: False` —— 根因2 **仍未修**：xiaohongshu 源依赖 storageState 扫码登录，未登录则该平台搜索不可用（其余平台不受影响）。
- **现象**：LLM 调用搜索技能后拿到空结果，会反复搜索或换关键词，浪费迭代次数
- **遗留动作**：重启后端后真机复验 trending_search；需要小红书搜索时先扫码登录
- **验证方法**：`xhs_search`/`trending_search` 返回非空结果

---

### BUG-003：build_session 绕过 _build_skills，139个技能全量注入 → 提示词 30K tokens

- **状态**：✅ 已修复（2026-10-30 核实：registry.py build_session 已走 _build_skills(agent_def.skills, creation_type)）
- **验证方式**：inspect 源码 + 实际运行 collect_reactor_skills()
- **实测结果**：
  - `build_session` 源码中：`collect_reactor_skills()` = YES，`_build_skills()` = NO，`SkillRouter` = NO
  - `collect_reactor_skills()` 返回 139 个技能实例
  - 系统提示词 46,173 字符 ≈ 30,782 tokens
  - 技能目录 41,869 字符 ≈ 27,913 tokens（占 91%）
  - DeepSeek V3 上下文 64K，剩余仅 ~33K 给对话
- **根因**：`AgentRegistry.build_session()` 直接调用 `collect_reactor_skills()` 收集全部 139 个技能实例，完全绕过了 `_build_skills()` 函数——`agent_def.skills` 和 `creation_type` 参数被忽略
- **代码链路**：
  1. `chat_agent` AgentDef 定义 `skills=None`
  2. `build_session()` 直接 `skill_instances = collect_reactor_skills()`，不走 `_build_skills(skill_names, creation_type)`
  3. `get_reactor_system_prompt(skills=139个)` 生成 46K 字符系统提示
- **影响**：LLM 面对 139 个技能选择困难，容易选错或反复试探；多轮对话后 token_budget 截断
- **对比数据**：
  - 当前（全量）：139 技能，~30,782 tokens
  - `enforce_priority=True`：50 技能，~12,000 tokens（估）
  - `creation_type='image_text'`：~30 技能，~7,500 tokens（估）
- **代码位置**：
  - `backend/app/agents/registry.py`（build_session 绕过 _build_skills）
  - `backend/app/engine/reactor/session.py`（collect_reactor_skills 全量收集）
  - `backend/app/engine/reactor/prompt.py`（build_skill_catalog 生成目录）
- **验证方法**：build_session 走 _build_skills 或 SkillRouter，系统提示词 < 15K tokens

---

### BUG-004：_build_skills 有歧义技能名 → 即使接入也会崩溃

- **状态**：☐ 待修复
- **验证方式**：实际调用 `_build_skills()` 对每个 creation_type
- **实测结果**：
  - `image_text`: ValueError - ambiguous skill name 'comment_insights': analyze.comment_insights, attribute.comment_insights
  - `voiceover`: 同上
  - `short_video`: 同上
  - `ai_edit`: 同上
  - `long_article`: 同上
  - `live_clip`: 12 skills OK（唯一不崩溃的）
- **根因**：`CREATION_TYPE_SKILLS` 中引用了裸名 `comment_insights`，但注册表中有两个同名技能：`analyze.comment_insights` 和 `attribute.comment_insights`，`get_skill_class_by_name()` 抛出 ValueError
- **影响**：即使修复 BUG-003 让 build_session 调用 _build_skills，5/6 的 creation_type 仍会崩溃
- **代码位置**：`backend/app/skills/creation_type_map.py`（引用裸名）、`backend/app/core/skill_registry.py`（get_skill_class_by_name 歧义检测）
- **验证方法**：所有 6 个 creation_type 的 `_build_skills()` 不抛异常

---

## 🟡 P1 — 高频导致卡住或体验差

### BUG-005：SkillRouter.enforce_priority=False → 路由形同虚设

- **状态**：☐ 待修复
- **验证方式**：inspect 源码 + 实际运行 select_skills()
- **实测结果**：
  - `enforce_priority=False`：select_skills 返回 139/139（全量）
  - `enforce_priority=True`：select_skills 返回 50/139（按优先级过滤）
  - `build_session` 源码中不引用 SkillRouter
- **根因**：`enforce_priority` 默认 False，且 `build_session` 根本没调用 `SkillRouter.select_skills()`，路由器未被接入主链路
- **影响**：已有的三层路由能力（分类摘要 + trigger_words 预筛 + 分组加载）完全浪费
- **代码位置**：`backend/app/tools/skill_router.py`（enforce_priority=False 默认值）、`backend/app/agents/registry.py`（未调用）
- **验证方法**：build_session 中调用 SkillRouter.select_skills 并传入 user_message

---

### BUG-006：DeepSeek V3 在 JSON 前输出中文文本 → 触发 nudge 机制

- **状态**：✅ 已修复（2026-10-30：_call_llm 按模型条件传 response_format）
- **修复内容**：`reactor/session.py` 新增 `_reactor_response_format()`——deepseek-v3/chat 开 JSON 模式（`response_format=json_object`），deepseek-r1/reasoner 不传（会 400）；流式/非流式两条路径均接入，并用 `_fn_accepts_response_format()` 探测签名，旧版 `(messages)` 测试桩不受影响。ModelRouter（model-router）放行透传。
- **验证方式**：离线 mock 单测 `tests/test_bug006_response_format.py`（8 用例全过，不发真实请求）；reactor 契约/诊断测试 78 个全过
- **实测结果**：3/3 次都在 JSON 前输出中文（如"我来帮你搜索手冲咖啡的热门笔记。\n\n{..."）
- **已有保护**：`_extract_json_from_text()` 能正确提取嵌入的 JSON，`_parse_step` 处理正常
- **残留问题**：每次非纯 JSON 输出都会触发 nudge 逻辑（LLM 输出非结构化文本 → 引导继续按 ReAct JSON 格式输出），浪费 1 次迭代
- **影响**：每次 LLM 调用多消耗 1 次迭代用于 nudge，5 次迭代中可能浪费 2-3 次
- **代码位置**：`backend/app/engine/reactor/session.py`（nudge 机制）、`backend/app/adapters/deepseek.py`（未用 response_format）
- **真机验证方法**（大哥手动）：使用 `response_format=json_object` 强制纯 JSON 输出，nudge 次数降为 0

---

### BUG-007：前端不处理 token_budget / nudge_exhausted / duplicate_calls 三种 ended_reason

- **状态**：✅ 已修复（2026-10-30：前后端联动）
- **修复内容**：
  - 后端：`chat_agent.py` 三处 resume 路径的 `agent_loop_done` 发布点（confirm/guardian/clarify）补齐 `ended_reason` 字段（此前只发 status/message，与契约不一致）
  - 前端：`useChatSSE.ts` 新增 `TRUNCATION_NOTICES` 映射（max_iterations/token_budget/nudge_exhausted/duplicate_calls/fake_final 五种）；`WORKFLOW_COMPLETED` 与 `AGENT_LOOP_DONE` 两个 case 不再把截断静默当正常完成——`meta.endedReason` 记录原因，消息尾部追加 ⚠️ 可见提示（含"发送继续可接续"引导），并随消息落库；`cell-types.ts` AgentMeta 补 `endedReason` 字段
- **验证方式**：`useChatSSE.test.ts` 8 用例全过；`vue-tsc -b` 类型检查通过
- **实测结果**：
  - `completed`: ✅ FOUND
  - `max_iterations`: ✅ FOUND（sse-contract.ts、AgentManager.vue）
  - `clarification_paused`: ✅ FOUND
  - `guardian_paused`: ✅ FOUND
  - `storyboard_paused`: ✅ FOUND
  - `error`: ✅ FOUND
  - `token_budget`: ✅ FOUND（2026-10-30 修复后）
  - `nudge_exhausted`: ✅ FOUND（2026-10-30 修复后）
  - `duplicate_calls`: ✅ FOUND（2026-10-30 修复后）
- **影响**：循环因这 3 种原因终止时，前端无任何反馈，用户可能看到不完整的回复或卡在"思考中"
- **代码位置**：`frontend/src/components/chat/composables/useChatSSE.ts`
- **验证方法**：这 3 种 ended_reason 有对应的 UI 提示

---

### BUG-008：file_write 被 Guardian 标为 high risk → 每次写文件都暂停

- **状态**：☐ 待评估（设计行为 or bug？）
- **验证方式**：实际运行 ReactorSession + Guardian.check()
- **实测结果**：
  - `Guardian.check('file_write', ...)`: action=needs_confirm, risk_level=high
  - ReactorSession: `ended_reason=guardian_paused`（即使 auto_approve=True）
  - 前端有完整确认组件：`ChatGuardianConfirmCard.vue` → 调用 `/confirm-guardian` → 后端恢复循环
- **分析**：
  - 前端确认→后端恢复的链路完整，不是"卡死"
  - 但 auto_approve=True 仍不自动批准 high risk，与用户预期不符
  - 每次写文件都需要用户点击确认，影响流畅度
- **代码位置**：`backend/app/engine/governance/guardian.py`（high risk 不 auto_approve）
- **验证方法**：auto_approve=True 时 high risk 也自动批准，或 file_write 降为 medium risk

---

## 🟢 P2 — 低频或条件触发

### BUG-009：Guardian 暂停后内部端点 internal_reset_llm_circuit 无鉴权

- **状态**：✅ 已修复（2026-10-30 核实：已加 localhost 白名单限制，非 127.0.0.1/::1 返回 403）
- **验证方式**：检查 API 路由鉴权
- **实测结果**：`POST internal_reset_llm_circuit: auth=NO`（其他端点均有 auth）
- **影响**：内部端点无鉴权，可能被外部调用
- **代码位置**：`backend/app/api/routers/chat_agent.py`
- **验证方法**：添加鉴权或限制为内部调用

---

### BUG-010：live_clip creation_type 引用了不存在的技能名

- **状态**：✅ 已修复（2026-10-30 核实：creation_type_map.py 的 live_clip 已无 bash/glob/grep 引用）
- **验证方式**：实际调用 _build_skills('live_clip')
- **实测结果**：`live_clip` 虽然不崩溃，但日志显示 `[registry] unknown skill name: bash`、`[registry] unknown skill name: glob`、`[registry] unknown skill name: grep`
- **根因**：`CREATION_TYPE_SKILLS['live_clip']` 引用了 bash/glob/grep 这些不存在的技能名
- **影响**：live_clip 模式下这些技能不会被加载
- **代码位置**：`backend/app/skills/creation_type_map.py`
- **验证方法**：CREATION_TYPE_SKILLS 中所有引用的技能名都存在于注册表

---

### BUG-011：前端 sse-contract.ts 已定义大部分事件，但 step_thought / tool_result 未覆盖

- **状态**：☐ 待修复
- **验证方式**：搜索前端源码
- **实测结果**：
  - 后端 EventType 枚举 19 个事件
  - 前端 sse-contract.ts 覆盖了 17 个
  - 缺失：`step_thought`、`tool_result`（但这两个可能未实际使用）
- **影响**：低，这两个事件可能未被后端实际发出
- **代码位置**：`frontend/src/components/chat/sse-contract.ts`
- **验证方法**：确认后端是否发出这两个事件，如果是则前端补全

---

## ❌ 第一轮误报（已推翻）

| 原 Bug ID | 原描述 | 推翻原因 | 实测证据 |
|-----------|--------|---------|---------|
| 原 BUG-001 | skill_call 缺 _user_id | API 层 user_id 链路完整 | `get_current_user` 无 token 时返回 `"dev_test_user"`；`_agentic_loop` 有 `input_data["user_id"] = context.user_id`；前端 SSE 和 HTTP 均传 Bearer token |
| 原 BUG-005 | 前端不调 /clarify /confirm | 前端有完整确认组件和 API 调用 | `/clarify` 在 `ChatClarificationCard.vue` + `useChatInteractions.ts`；`/confirm-guardian` 在 `ChatGuardianConfirmCard.vue`；`/confirm-storyboard` 在 `ChatStoryboardConfirmCard.vue` |
| 原 BUG-009 | copywrite 技能找不到 | LLM 看到的技能名是 lively_girl/elegant 等，不是 copywrite | 系统提示词中列出 `lively_girl`、`elegant`、`professional`、`casual`、`poetic`，没有裸名 `copywrite`；LLM 不会调用 `skill_call(skill="copywrite")` |

---

## 📊 修复优先级总览

| 序号 | Bug ID | 优先级 | 一句话描述 | 修复方向 |
|------|--------|--------|-----------|---------|
| 1 | BUG-001 | P0 | DashScope API Key 无效 | 更新 Key 或配备用图片服务 |
| 2 | BUG-002 | P0 | 搜索无内容源/XHS Cookie | 配置内容源和 Cookie |
| 3 | BUG-003 | P0 | build_session 绕过 _build_skills，139技能全量注入 | 接入 _build_skills 或 SkillRouter |
| 4 | BUG-004 | P0 | _build_skills 歧义技能名崩溃 | CREATION_TYPE_SKILLS 用全限定名 |
| 5 | BUG-005 | P1 | SkillRouter 路由未接入主链路 | enforce_priority=True 并在 build_session 调用 |
| 6 | BUG-006 | P1 | DeepSeek JSON前输出中文触发nudge | 用 response_format=json_object |
| 7 | BUG-007 | P1 | 前端缺3种 ended_reason 处理 | 补全 token_budget/nudge_exhausted/duplicate_calls |
| 8 | BUG-008 | P1 | file_write 每次都暂停等确认 | 评估是否降级 risk 或 auto_approve 含 high |
| 9 | BUG-009 | P2 | internal_reset_llm_circuit 无鉴权 | 添加鉴权 |
| 10 | BUG-010 | P2 | live_clip 引用不存在的技能名 | 修 creation_type_map |
| 11 | BUG-011 | P2 | 前端缺 step_thought/tool_result | 确认后端是否发出，是则补全 |

---

## 🔗 依赖关系

```
BUG-003 (build_session绕过) ←── BUG-004 (歧义名崩溃)
     ↓                              ↓
BUG-005 (SkillRouter未接入)     即使修了003，004不修仍崩溃

BUG-001 (API Key) ──→ 图片技能失败 ──→ LLM浪费迭代
BUG-002 (搜索空)  ──→ LLM反复搜索  ──→ 浪费迭代
BUG-006 (nudge)   ──→ 每次多耗1次迭代
```

- **BUG-003 + BUG-004 必须一起修**：只修 003 接入 _build_skills，004 的歧义名会直接导致崩溃
- **BUG-001 + BUG-002 是环境配置问题**：修好后搜索和图片技能才能正常工作
- **BUG-006 是效率问题**：不修不会卡死，但每次多浪费 1 次迭代