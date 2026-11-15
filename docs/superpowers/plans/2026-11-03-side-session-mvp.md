# 侧会话（Side Session）MVP 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在主会话（ChatSession）之下实现可并行的侧会话：创建时按粒度快照主会话上下文、默认只读（写副作用工具逐次确认）、结论以"特殊消息"提炼回写主会话。

**Architecture:** 侧会话复用 `ChatSession`/`ChatMessage` 全套既有管道（新增 `kind`/`parent_session_id` 列区分主/侧），侧会话专属元数据放 `side_meta` JSONB 列，快照存独立 `side_snapshots` 表。写拦截在 Reactor 工具分发循环前置检查 primitive 的 `required_permissions`，暂停/恢复复用既有 clarification 打断-恢复机制。前端 Vue3 + Pinia，特殊消息按 `agent_meta.message_type` 分流渲染。

**Tech Stack:** FastAPI + 异步 SQLAlchemy(PostgreSQL/JSONB) + alembic；Vue 3 + TS + Pinia + vitest；pytest。

**Spec:** `../../PRD-主任务与侧会话-Side-Session.md`（v0.3，本文档所有数值均来自其 §12 决策记录）

## 已勘察的关键事实（实现者必读）

| 事实 | 位置 |
|---|---|
| `ChatSession`(id/user_id/work_id/folder_id/title/creative_state) 与 `ChatMessage`(session_id/role/content/agent_meta) | `backend/app/db/models.py:443-490` |
| 会话服务 `create_session/ensure_session/add_message/list_messages(limit)/get_session_summary/get_creative_state` | `backend/app/services/chat_session.py` |
| 会话 REST 路由（/api/chat/sessions 前缀，含 messages 列表/追加） | `backend/app/api/routers/chat_session.py:31-82` |
| 路由注册（import + include_router） | `backend/app/main.py:29` |
| ChatAgent 上下文构建：`list_messages` + `get_session_summary` + `ContextManager` | `backend/app/agents/chat_agent.py:301-367` |
| Reactor 工具分发循环：`_dispatch` 调用点、`tool_call_start/end` SSE、clarification 打断先例（`_awaiting_clarification` → `save_loop_state`） | `backend/app/engine/reactor/session.py:619-670` |
| 工具注册表 `ALL_PRIMITIVES`/`PRIMITIVES_BY_NAME`，primitive 带 `required_permissions` | `backend/app/engine/reactor/primitives.py:597-611`、`backend/app/engine/protocol/transport.py:70-80` |
| `Permission` 枚举：FILE_READ/FILE_WRITE/BASH_EXEC/NET_HTTP_GET/NET_HTTP_POST/XHS_SEARCH/SKILL_IMPORT/SKILL_MANAGE/BROWSER_AUTOMATE | `backend/app/engine/schemas.py:17-32` |
| alembic 迁移为手写编号版本（最新 021） | `backend/alembic/versions/` |
| 前端：Vue3+TS，api 模块 `api/chatSessions.ts`，Pinia `stores/`，聊天页 `views/ChatViewPage.vue`，消息单元 `components/chat/UserMessageCell.vue`/`AssistantMessageCell.vue` | frontend/src |
| pytest 既有先例：DB 测试 `tests/test_chat_session_db.py`、权限测试 `tests/test_permissions.py`、clarification 集成测试 `tests/test_clarification_integration.py` | backend/tests |

## Global Constraints（来自 PRD v0.3 §12 决策，数值不可改）

- 继承粒度三档：`full` / `recent_n` / `digest`；默认 `recent_n`，**N=10**，可调范围 **5–50**（Q1）
- 快照过期阈值：主会话基准点后新增 **≥5 个用户轮次**提示过期（Q2）
- 并行侧会话上限 **5** 个（活跃状态计）（Q3）
- 特殊消息长度硬约束：**≤ 对应侧会话问答总字符数 × 30%**（AC-04；token 以字符数近似，从宽）
- 写副作用权限集合：`FILE_WRITE, BASH_EXEC, NET_HTTP_POST, SKILL_IMPORT, SKILL_MANAGE, BROWSER_AUTOMATE`；其余（FILE_READ/NET_HTTP_GET/XHS_SEARCH）为只读白名单（Q5/§8 R5）；争议项按写处理
- 开销提示阈值：快照内容超过 **32k tokens（以字符数≈tokens 估算）** 时创建确认处必须提示（PRD §6.1）
- 特殊消息注入：`role="user"` + `agent_meta.message_type="side_session_digest"`；默认展开（Q8）
- 写确认逐次生效，无会话级放行（Q9）；拒绝后以只读方式继续回答，不中断会话（FR-D5）
- 侧会话不可再建侧会话（FR-A 组规则）；侧会话不可跨主会话迁移（FR-C1）
- 数据库为 PostgreSQL；ULID 主键有序可比较，基准点用 `baseline_message_id`（消息 id）表达

## Review Focus（规格隐含但任务测试未直接覆盖的输入）

1. **主会话正在流式执行时创建侧会话**——快照必须只含已持久化消息（FR-A8）；测试钉在 Task 2（`list_messages` 只返回已落库消息，天然满足，测试断言不含未落库内容）。
2. **空主会话 / 只有 1 条消息时创建 recent_n=10**——快照为空或部分返回，不得报错；测试钉在 Task 2。
3. **写确认弹窗期间用户继续发消息**——pending_write 未决时侧会话新请求应被拒绝（409）；测试钉在 Task 5。
4. **同一侧会话多次带回**——每次草稿独立、注入多条特殊消息，互不覆盖；测试钉在 Task 6。
5. **丢弃侧会话后主会话特殊消息仍在**——删除不得级联删除主会话消息（特殊消息 session_id 属于主会话）；测试钉在 Task 3。

---

## File Structure

```
backend/
  alembic/versions/022_side_sessions.py            # 新迁移
  app/db/models.py                                  # 改：ChatSession +3 列；新 SideSnapshot
  app/services/side_session.py                      # 新：快照构建/生命周期/回写校验
  app/engine/reactor/side_guard.py                  # 新：写副作用判定纯函数
  app/engine/reactor/session.py                     # 改：分发前拦截 + pending_write
  app/agents/chat_agent.py                          # 改：kind=side 上下文构建分支
  app/api/routers/side_session.py                   # 新：REST API
  app/main.py                                       # 改：注册路由
  tests/test_side_session_snapshot.py               # 新
  tests/test_side_session_api.py                    # 新
  tests/test_side_guard.py                          # 新
frontend/src/
  api/sideSessions.ts                               # 新
  stores/sideSession.ts                             # 新
  components/chat/SideDigestMessageCell.vue         # 新
  components/chat/SideWriteConfirmDialog.vue        # 新
  views/ChatViewPage.vue                            # 改：侧会话面板/创建弹窗/过期横幅
```

---

### Task 1: 数据模型与迁移

**Files:**
- Modify: `backend/app/db/models.py:443-470`（ChatSession）
- Modify: `backend/app/db/models.py`（文件末尾 chat 段落追加 SideSnapshot）
- Create: `backend/alembic/versions/022_side_sessions.py`
- Test: `backend/tests/test_side_session_api.py`（本任务只建 fixture，断言在 Task 3）

**Interfaces:**
- Produces（后续所有任务依赖）:
  - `ChatSession` 新增列：`kind: Mapped[str]`（String(10)，default `"main"`）、`parent_session_id: Mapped[str | None]`（String(26)，nullable，无外键约束——侧会话删除不影响主会话）、`side_meta: Mapped[dict | None]`（JSONB，结构 `{"status": "active"|"archived", "has_side_effects": bool, "pending_write": {...}|null}`）
  - `SideSnapshot`：`id`(ULID pk)、`session_id`(String(26)，FK chat_sessions.id CASCADE，unique)、`baseline_message_id`(String(26))、`granularity`(String(20))、`recent_n`(int|None)、`content`(JSONB，结构 `{"goal": str, "summary": str, "messages": [{"role","content"}]}`)、`token_level`(String(10)，`"low"|"mid"|"high"`)、`created_at`/`updated_at`

- [x] **Step 1: 写失败测试**——在 `backend/tests/test_side_session_api.py` 顶部按 `tests/test_chat_session_db.py` 的 DB fixture 模式建 fixture，先写一个冒烟断言：

```python
def test_side_snapshot_model_exists():
    from app.db.models import ChatSession, SideSnapshot
    assert hasattr(ChatSession, "kind") and hasattr(ChatSession, "parent_session_id") and hasattr(ChatSession, "side_meta")
    assert SideSnapshot.__tablename__ == "side_snapshots"
```

- [x] **Step 2: 运行确认失败**——`cd backend; python -m pytest tests/test_side_session_api.py::test_side_snapshot_model_exists -v`，预期 FAIL（ImportError/AttributeError）
- [x] **Step 3: 实现**——按 Interfaces 定义改 `models.py`（ChatSession 三列带 server_default 以兼容存量行：kind server_default="main"）；迁移文件照 `021_add_local_video_path.py` 的手写模式：加列 + 建表 + 降级函数
- [x] **Step 4: 运行迁移并验证**——`cd backend; alembic upgrade head`，成功后再跑 Step 2 命令，预期 PASS
- [x] **Step 5: Commit**——`git add ...; git commit -m "feat(side-session): data model for side sessions and snapshots"`

### Task 2: 快照构建服务（纯函数优先）

**Files:**
- Create: `backend/app/services/side_session.py`
- Test: `backend/tests/test_side_session_snapshot.py`

**Interfaces:**
- Consumes: `chat_session.list_messages(session_id, limit=200)`（返回 `{"id","role","content"}` 列表，已落库消息，天然满足 FR-A8）、`chat_session.get_session_summary(session_id)`
- Produces:

```python
WRITE_PERMISSIONS: frozenset[Permission]  # Global Constraints 的 6 项（放 Task 5 的 side_guard.py，此处不定义）

DEFAULT_RECENT_N = 10
EXPIRED_TURN_THRESHOLD = 5
MAX_SIDE_SESSIONS = 5
COST_HINT_CHARS = 32_000 * 2  # 32k tokens 的字符近似，可配置常量

def build_snapshot_content(granularity: str, messages: list[dict], summary: str, recent_n: int = DEFAULT_RECENT_N) -> dict:
    """full -> messages 全量; recent_n -> messages[-recent_n:]; digest -> {"goal": 首条 user 消息, "summary": summary, "messages": []}
    返回 {"goal": str, "summary": str, "messages": [...]}；messages 按 role∈{user,assistant} 过滤"""

def token_level_of(content: dict) -> str:
    """字符总量 > COST_HINT_CHARS -> "high"; > 6_000 字符 -> "mid"; else "low"（供 FR-F2 相对量级提示）"""

async def create_side_session(main_session_id: str, user_id: str, granularity: str, recent_n: int = DEFAULT_RECENT_N) -> dict:
    """校验：主会话存在且 kind=="main"、granularity 合法、5<=recent_n<=50、活跃侧会话数 < MAX_SIDE_SESSIONS（否则 ValueError 带中文信息）。
    落库：ChatSession(kind="side", parent_session_id=main_session_id, user_id, title="侧会话", side_meta={"status":"active","has_side_effects":False,"pending_write":None})
        + SideSnapshot(baseline_message_id=主会话最后一条消息 id（无消息则为""), content, token_level)
    返回 {"id", "title", "granularity", "recent_n", "baseline_message_id", "token_level", "created_at"}"""

async def list_side_sessions(main_session_id: str) -> list[dict]:
    """每项含 id/title/status/has_side_effects/granularity/baseline_message_id/expired_turns/token_level"""

async def compute_expired_turns(main_session_id: str, baseline_message_id: str) -> int:
    """主会话中 id > baseline_message_id 且 role=="user" 的消息数（ULID 有序）；baseline 为 "" 时返回全部 user 消息数"""

async def set_side_status(side_session_id: str, status: str) -> None  # "active"|"archived"
async def delete_side_session(side_session_id: str) -> None  # 删 ChatSession(kind=side) 行，SideSnapshot 级联；不动主会话消息
async def finalize_main_session(main_session_id: str, decisions: dict[str, str]) -> dict:
    """decisions: {side_id: "keep"|"discard"}；keep -> set archived，discard -> delete；返回各侧会话终态"""
```

- [x] **Step 1: 写失败测试**——`build_snapshot_content` 三档各 1 例（full 含全部、recent_n 只含尾部 N 条、digest 的 goal=首条 user 消息且 messages 为空）；空 messages 各档不抛错；`compute_expired_turns` 用手工插入的 3 条 user 消息断言计数；`token_level_of` 边界 1 例
- [x] **Step 2: 运行确认失败**——`cd backend; python -m pytest tests/test_side_session_snapshot.py -v`，预期 FAIL
- [x] **Step 3: 实现**——`build_snapshot_content`/`token_level_of` 为纯函数；异步函数按 `services/chat_session.py` 的 `AsyncSessionLocal` 用法写；digest 档 goal 取 messages 中首条 role=="user" 的 content（截断 2000 字符），summary 用 `get_session_summary`
- [x] **Step 4: 运行确认通过**——同 Step 2 命令，预期 PASS
- [x] **Step 5: Commit**——`git commit -m "feat(side-session): snapshot building and lifecycle service"`

### Task 3: REST API

**Files:**
- Create: `backend/app/api/routers/side_session.py`
- Modify: `backend/app/main.py:29`（import 列表加 `side_session`；对应 `include_router` 处追加，照 `chat_session` 的注册方式）
- Test: `backend/tests/test_side_session_api.py`

**Interfaces:**
- Consumes: Task 2 全部服务函数；既有鉴权依赖（照 `routers/chat_session.py` 取 current_user 的方式）
- Produces:

```
POST /api/chat/sessions/{main_session_id}/side-sessions   body {granularity, recent_n?}   201 -> Task2 create 返回体
GET  /api/chat/sessions/{main_session_id}/side-sessions                                    200 -> list 返回体
PATCH /api/side-sessions/{side_session_id}                  body {title?} 或 {status?}     200 -> {"id","title","status"}
DELETE /api/side-sessions/{side_session_id}                                                204；archived 状态也可删
POST /api/chat/sessions/{main_session_id}/finalize          body {decisions}               200 -> {"results": {side_id: "archived"|"deleted"}}
```

所有端点校验 `current_user.id == 会话 user_id`，否则 403。过期提示不入库，由 `list_side_sessions` 实时计算 `expired_turns`（≥5 前端显示横幅）。

- [x] **Step 1: 写失败测试**——TestClient 风格照 `tests/test_chat_session_db.py`：创建（201 + 默认 recent_n=10）→ 同主会话连建 5 个后第 6 个返回 400 → 侧会话上再建侧会话返回 400（kind=="side" 校验）→ PATCH 改名 → finalize keep 后 status=="archived" 且侧会话消息不可再增（对既有 messages 追加端点传 side id 且 status==archived 时 403——此断言在 Task 5 完成后重跑）
- [x] **Step 2: 运行确认失败**
- [x] **Step 3: 实现**——router 文件照 `routers/chat_session.py` 结构；`archived` 侧会话拒绝新消息写入的逻辑加在既有 messages 追加端点（`routers/chat_session.py`）开头：目标会话 `kind=="side"` 且 `side_meta.status=="archived"` → 403
- [x] **Step 4: 运行确认通过**——`python -m pytest tests/test_side_session_api.py -v`
- [x] **Step 5: Commit**——`git commit -m "feat(side-session): REST API for create/list/rename/delete/finalize"`

### Task 4: 侧会话上下文构建（隔离读方向）

**Files:**
- Modify: `backend/app/agents/chat_agent.py:301-367`（上下文注入段）
- Test: `backend/tests/test_chat_agent.py`（追加用例，沿用其既有 mock 模式）

**Interfaces:**
- Consumes: `SideSnapshot`（Task 1）、`side_session.get_snapshot(side_session_id) -> SideSnapshot | None`（Task 2 需补这个读函数）
- Produces: `ChatAgent.process` 对 `kind=="side"` 的会话：上下文 = 系统提示（新增固定段：`【侧会话背景·基于主任务第 N 轮快照】` + snapshot.goal + snapshot.summary + snapshot.messages）+ 侧会话自身 `list_messages`；**不调用**主会话的 list_messages（FR-B1 隔离）

- [x] **Step 1: 写失败测试**——mock `list_messages` 与 snapshot 读取，断言：构建上下文时快照消息在侧会话消息之前；上下文中不出现主会话快照之外的消息；快照为 None（异常兜底）时上下文仅含侧会话自身消息
- [x] **Step 2: 运行确认失败**——`python -m pytest tests/test_chat_agent.py -v`
- [x] **Step 3: 实现**——在 chat_agent.py 上下文注入分支前判断 `session.kind == "side"` 走新分支；`_cm.set_system_prompt` 传入拼接后的系统提示
- [x] **Step 4: 运行确认通过**
- [x] **Step 5: Commit**——`git commit -m "feat(side-session): snapshot-based context building for side sessions"`

### Task 5: 写副作用拦截与逐次确认

**Files:**
- Create: `backend/app/engine/reactor/side_guard.py`
- Modify: `backend/app/engine/reactor/session.py:619-654`（`_dispatch` 调用前的 for 循环内）
- Modify: `backend/app/api/routers/side_session.py`（追加 confirm 端点）
- Test: `backend/tests/test_side_guard.py`；集成用例追加到 `backend/tests/test_side_session_api.py`

**Interfaces:**
- Consumes: `PRIMITIVES_BY_NAME`、`Permission`（schemas.py）、clarification 打断-恢复先例（session.py:657-670 的 `_awaiting_clarification` break + `save_loop_state`；恢复端点在 clarification 相关路由中，实现者先读 `tests/test_clarification_integration.py` 找到恢复调用链再镜像）
- Produces:

```python
# side_guard.py
WRITE_PERMISSIONS: frozenset[Permission]
def is_write_tool(tool_name: str) -> bool:
    """PRIMITIVES_BY_NAME[tool_name].required_permissions ∩ WRITE_PERMISSIONS 非空 -> True；
    未知工具 -> True（争议从宽，§8 R5）"""
```

session.py 循环内在 `_dispatch` 前插入：`if self._is_side and side_guard.is_write_tool(tool_name)` →
1. 生成 `call_id`，把 `{"call_id","tool_name","arguments"}` 写入该侧会话 `side_meta.pending_write`
2. `await self._emit_sse("side_write_request", {"call_id", "tool_name", "inputs", "message": "侧会话请求写操作，需要确认"})`
3. 照 clarification 先例 break 并保存 loop_state（不算错误终态）

新增端点：

```
POST /api/side-sessions/{side_session_id}/write-confirm   body {"call_id": str, "approved": bool}
```

- `approved=true`：`side_meta.has_side_effects=True`、清空 pending_write，按 clarification 恢复链路恢复执行该次调用
- `approved=false`：清空 pending_write，恢复循环但该调用 observation 固定为 `"用户拒绝了此写操作。请以只读方式继续：说明若允许执行会做什么，但不要执行。"`
- 存在 pending_write 时，该侧会话新的对话请求返回 409

- [x] **Step 1: 写失败测试**——`is_write_tool`：`file_write`/`bash`/`browser` 类真，`file_read`/`xhs_search`/`http_get` 类假，未知名字真。集成：侧会话请求含写工具的调用 → 断言 SSE 事件 `side_write_request` 发出、pending_write 落库；approve 后 has_side_effects 变 True；reject 后 observation 含"拒绝"
- [x] **Step 2: 运行确认失败**——`python -m pytest tests/test_side_guard.py tests/test_side_session_api.py -v`
- [x] **Step 3: 实现**——`ReactorSession` 增加构造参数 `side_session_id: str | None = None`（`ChatAgent.process` 对 side 会话传入）；拦截逻辑放在 session.py:619 的 for 循环内、`_dispatch` 调用前
- [x] **Step 4: 运行确认通过**
- [x] **Step 5: Commit**——`git commit -m "feat(side-session): write-effect interception with per-call confirmation"`

### Task 6: 提炼式回写（带回主任务）

**Files:**
- Modify: `backend/app/services/side_session.py`（追加回写函数）
- Modify: `backend/app/api/routers/side_session.py`（追加两个端点）
- Test: `backend/tests/test_side_session_api.py`

**Interfaces:**
- Consumes: `chat_session.add_message`（支持 agent_meta）；LLM 调用路径与 `chat_session.get_session_summary` 相同（实现者读该函数照抄其 LLM 访问方式）
- Produces:

```python
DIGEST_MAX_RATIO = 0.3

async def generate_digest_draft(side_session_id: str) -> str:
    """以侧会话全部问答为素材，系统提示固定为：生成 300 字以内结论摘要，禁止包含逐轮问答原文；
    LLM 调用失败 -> ValueError"""

def check_digest_cap(digest: str, side_messages: list[dict]) -> bool:
    """len(digest) <= 0.3 * sum(len(m["content"]) for m in side_messages)；侧会话无问答 -> False"""

async def bring_back(side_session_id: str, digest: str) -> dict:
    """校验 check_digest_cap（否则 ValueError）；向主会话 add_message(role="user", content=digest,
    agent_meta={"message_type": "side_session_digest", "side_session_id": ..., "side_session_title": ...})；
    侧会话状态不变（FR-B5）；返回 {"main_message_id", "main_session_id"}"""
```

```
POST /api/side-sessions/{side_session_id}/bring-back/draft   200 -> {"digest": str}
POST /api/side-sessions/{side_session_id}/bring-back         body {"digest": str}   200 -> bring_back 返回体；超限 422
```

- [x] **Step 1: 写失败测试**——`check_digest_cap` 边界（恰 30% 通过、31% 拒绝、空问答拒绝）；mock LLM 后 draft 端点 200；bring_back 注入后断言主会话消息 role/agent_meta/内容；连续两次带回产生两条独立消息
- [x] **Step 2: 运行确认失败**
- [x] **Step 3: 实现**
- [x] **Step 4: 运行确认通过**——`python -m pytest tests/test_side_session_api.py tests/test_side_session_snapshot.py -v`
- [x] **Step 5: Commit**——`git commit -m "feat(side-session): distilled bring-back with 30% hard cap"`

### Task 7: 前端 API 模块与侧会话面板

**Files:**
- Create: `frontend/src/api/sideSessions.ts`（照 `api/chatSessions.ts` 的请求封装方式）
- Create: `frontend/src/stores/sideSession.ts`（Pinia，照 `stores/` 既有 store 模式）
- Modify: `frontend/src/views/ChatViewPage.vue`
- Test: `frontend/src/stores/sideSession.test.ts`（vitest，照 `stores/work.test.ts` 模式）

**Interfaces:**
- Consumes: Task 3/5/6 全部端点
- Produces: store state `{ sides: SideSessionInfo[], activeSideId: string | null }`；actions `create/rename/remove/finalize/loadList`；getter `activeSide`（含 `expired: boolean`，即 `expired_turns >= 5`）

- [x] **Step 1: 写失败测试**——store：create 成功后 sides 长度 +1；expired 计算正确（4→false，5→true）
- [x] **Step 2: 运行确认失败**——`cd frontend; npx vitest run src/stores/sideSession.test.ts`
- [x] **Step 3: 实现**——api 模块 + store；`ChatViewPage.vue`：主会话工具栏"新建侧会话"按钮 → 弹窗（粒度三选一、recent_n 输入框默认 10、token_level 为 high 时红字开销提示）→ 创建后打开侧会话面板；面板列出侧会话（标题/状态/基准点/`已产生写副作用`标记）、支持改名与删除（删除二次确认用既有 `composables/useConfirm.ts`）；侧会话头部徽标（`侧会话 · 基于主任务第 N 轮 · [粒度]`）与主会话视觉区分（面板独立底色/边框，FR-E1）
- [x] **Step 4: 运行确认通过**
- [x] **Step 5: Commit**——`git commit -m "feat(side-session): frontend panel, creation dialog and visual distinction"`

### Task 8: 前端特殊消息、写确认弹窗、过期横幅

**Files:**
- Create: `frontend/src/components/chat/SideDigestMessageCell.vue`
- Create: `frontend/src/components/chat/SideWriteConfirmDialog.vue`
- Modify: `frontend/src/views/ChatViewPage.vue`（消息列表渲染分支 + SSE 事件处理 + 横幅）
- Modify: `frontend/src/composables/useChatHistory.ts`（若消息分流在此层，实现者按实际渲染链路落点插入）

**Interfaces:**
- Consumes: `agent_meta.message_type === "side_session_digest"` 的 user 消息；SSE 事件 `side_write_request`；`write-confirm` 端点
- Produces: 特殊消息渲染（来源标识"来自侧会话 {title}"、可折叠、默认展开、独立样式，与 UserMessageCell 视觉可区分，AC-10）；写确认弹窗（列明 tool_name 与 inputs、明示"将影响主任务共享的工作区环境"、同意/拒绝各调一次 write-confirm）；`expired_turns >= 5` 时侧会话面板顶部横幅"快照已过期，主任务已推进 X 轮"

- [x] **Step 1: 写失败测试**——`SideDigestMessageCell` 挂载测试：含来源标识文案、默认展开、点击折叠切换（照 components 既有 vitest 模式；若无先例则手测 + 截图到 `.screenshots/`）
- [x] **Step 2: 运行确认失败/或按手测清单执行**
- [x] **Step 3: 实现**——消息列表中 user 消息先判 `agent_meta.message_type` 分流到新组件；SSE 处理处监听 `side_write_request` 弹出确认框（定位方式：在 ChatViewPage.vue 或 SSE composable 中搜 `tool_call_start`，在同级注册新事件）
- [x] **Step 4: 验证**——`npx vitest run`；手测清单：主会话发消息→消息普通渲染；侧会话带回→特殊消息样式；侧会话请求写文件→弹窗出现→拒绝后无文件变更
- [x] **Step 5: Commit**——`git commit -m "feat(side-session): digest message cell, write confirm dialog, expiry banner"`

### Task 9（P1，随第一迭代）: 刷新快照

**Files:** Modify `services/side_session.py`（`refresh_snapshot(side_session_id, recent_n?)`：按原 granularity 以当前最新已完成消息为新基准重建 content，保留侧会话问答）、`routers/side_session.py`（`POST /api/side-sessions/{id}/refresh`）、前端横幅加"刷新快照"按钮。**验收 AC-06b**；仅过期横幅出现时可点，刷新前弹确认"将替换现有快照"。

---

## 自检记录（Self-Review）

- **Spec 覆盖**：P0 FR 全部落位——A1/A2/A3/A6→Task 1/2/4；A4→Task 2/3（expired_turns）+ Task 8（横幅）；A7→digest 档（goal+summary，对应 §8 R2 初版思路）；A8→快照仅取已落库消息（Task 2 事实注记）；B1/B2→Task 4 隔离 + 无自动回写路径；B3/B4/B5→Task 6；B8→特殊消息走 add_message，主会话执行循环外落库，天然排队（无需额外机制，已在 Task 6 注明）；C1/C2/C3/C6→Task 2/3；D1/D2/D3/D6→Task 5 + 前端弹窗文案；E1/E2/E4/E5→Task 7/8；F1→默认 recent_n=10。AC-01~AC-05、AC-06a、AC-07~AC-10、AC-13 可由上述任务验收推出
- **类型一致性**：`side_meta` 结构、`content` 三键结构、`expired_turns`、`token_level` 各任务引用一致
- **比例**：计划以签名+测试断言为主，无实现体转录
