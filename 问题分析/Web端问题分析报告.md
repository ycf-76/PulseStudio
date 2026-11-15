# Web 端（Frontend）问题分析报告

> 分析对象：`frontend/`（Vue 3 + TypeScript + Vite + Pinia + Tailwind 4）
> 分析日期：2026-11-05
> 分析范围：84 个 `.vue` 组件、117 个 `.ts` 文件、23 个测试文件、约 71,884 行（vue/ts/css）
> 分析方式：源码走读 + 全量模式扫描（可复现命令见文末附录）+ 构建产物实测
> 结论性质：**静态代码分析与产物测量**，未做真机压测；涉及运行时行为的问题均标注了代码证据行号，可逐条核对

---

## 一、总体结论

**产品层是精装修，工程层是毛坯，两者已经开始互相拖累。**

这个项目在"看起来"的层面完成度很高：聊天渲染层有双游标稳定区/活跃区、思考链独立播放器、回合沙箱（TurnRuntime）、SSE 事件闸门，这些设计思路在同类产品里属于偏成熟的做法。但支撑这套渲染逻辑的地基存在几处**结构性缺口**：

| 维度 | 现状 | 后果 |
|---|---|---|
| SSE 解析 | 4 套独立实现，其中 1 套缺跨 chunk 缓冲 | 偶发丢字，且静默无日志 |
| 类型系统 | `any` 458 处 | TS 严格模式失去"重构时会告诉你哪里炸"的核心价值 |
| 布局层 | 9 个页面各复制一套侧边栏壳 | 改一处要动 9 个文件，已出现不一致 |
| 状态管理 | Pinia + 模块级 ref + 120 处裸 localStorage + window 事件 四通道并行 | 同一概念（如"主题"）存在多份互不相干的真相 |
| 质量闸门 | 无 ESLint、无前端 CI、`frontend/tests/` 为空 | 84 个组件改坏了 CI 依然是绿的 |
| 静态资源 | `dist/` 29.5MB，其中 `public/images` 占 23MB | 首屏/带宽成本远超必要水平 |

最需要警惕的一点：**已经暴露的问题几乎全是"能用但偶发才炸"型**——丢字、消息串台、换号后身份不刷新、token 失效后卡死。这类问题在人工点测时命中率低，在没有测试和监控的情况下，只能等用户反馈。

---

## 二、量化指标总表

以下数字均为本次实测（命令见附录 A），非估算。

| 指标 | 实测值 | 判读 |
|---|---|---|
| `any` 断言（`as any` / `: any`） | **458 处 / 84 文件** | 最高：`useChatSSE.ts` 50、`useChatAgent.ts` 30、`CardDeckEditor.vue` 27、`ChatView.vue` 17、`plugin.ts` 15、`work.ts` 14 |
| `console.*` 调用 | **152 处 / 37 文件** | 其中 `console.log` 49 处；`useChatSSE.ts` 19 处（每收到一条 SSE 事件就打印一次对象） |
| `alert()` 原生弹窗 | **40 处 / 12 文件** | 最高：`AgentManager.vue` 7、`SettingsView.vue` 6、`TaskPlanView.vue` 5 |
| 裸 `localStorage` 读写 | **120 处 / 26 文件**（含 4 个测试文件 35 处，生产代码约 85 处） | key 全部为裸字符串常量，无命名空间、无版本、无序列化封装 |
| `!important` | **230 处 / 33 文件** | 样式层叠已失控的量化指标；`_nav.css` 单文件 44 处 |
| `v-html` | **19 处 / 13 文件** | 其中 1 处渲染不可信后端 HTML（见 P0-05） |
| iframe `sandbox="allow-same-origin"` | **12 处**，其中 **5 处同时给 `allow-scripts`** | 等于没有沙箱（见 P0-05） |
| `@media` 查询 | **55 处 / 26 文件** | 84 个组件里只有 26 个有响应式处理，整体桌面优先 |
| `aria-*` / `role=` | **79 处 / 20 文件** | 有意识但覆盖不足；无 focus trap |
| `TODO/FIXME/HACK` | **2 处** | 遗留标记很少，说明问题不是"知道但没修"，而是"没被识别" |
| 前端测试文件 | **23 个** | 但 `frontend/tests/` 目录为**空**；SSE 解析、StreamEngine、TurnRuntime 等核心纯逻辑**无测试** |
| ESLint / Prettier 配置 | **无** | `package.json` 无 `lint` 脚本 |
| 前端 CI 步骤 | **0 步** | `.github/workflows/ci.yml` 只有 Python lint / pytest / build |
| `dist/assets` | **2.09 MB** | 最大：`ChatViewPage.js` 232KB、`chat-content-renderer.js` 197KB、`thinking.json` 148KB、`index.css` 134KB |
| `dist/` 总大小 | **29.51 MB** | `images` 22.95MB + `fonts` 2.50MB + `icons` 1.96MB + `assets` 2.09MB |
| `public/images` | **23 个文件**（13 PNG + 8 JPG + 2 SVG），单文件最大 **3.8MB** | 未压缩、未转 WebP/AVIF、未按需加载 |

---

## 三、问题总览清单

### P0 — 会真实出错的 Bug 与安全漏洞（7 项）

| 编号 | 问题 | 证据位置 | 用户可感知表现 |
|---|---|---|---|
| P0-01 | SSE 解析器分裂，`useChatSend` 缺跨 chunk 缓冲 | `composables/useChatSend.ts:113-114` | 直连 chat 路径偶发"字少一截"，无日志 |
| P0-02 | 消息列表用索引做 `key` | `components/chat/ChatView.vue:143-144` | 消息串台、播放器错位、思考链张冠李戴 |
| P0-03 | `_userFetched` 模块级锁导致换号后身份不刷新 | `router/index.ts:107,123-129` | 切换账号后仍显示上一账号的头像/昵称 |
| P0-04 | Token 刷新两套独立实现 + 失败后不跳登录 | `api/client.ts:41-119` / `:121-159` | token 过期后页面永久卡死、并发刷新可能被后端判为 token 重放 |
| P0-05 | XSS 面：未过滤 `v-html` + iframe 沙箱形同虚设 | `renderers/AudioLightbox.vue:16` 等 5 处 sandbox | 恶意 Agent 输出可执行脚本、可读取 localStorage 内 token |
| P0-06 | token 存 localStorage，且 cookie/header 双认证并行 | `stores/auth.ts`、`api/client.ts:5,32-36` | XSS 一旦成立，token 直接可外传 |
| P0-07 | 40 处原生 `alert()` 阻塞主线程 | 12 个文件，见上表 | 关闭弹窗后文字瞬间喷出一大段，观感断层 |

### P1 — 架构与性能的结构性问题（9 项）

| 编号 | 问题 | 证据位置 |
|---|---|---|
| P1-01 | 无布局壳，9 个 view 各复制一套侧边栏（37 处引用） | `views/HomeView.vue:1-20` 等 9 个文件 |
| P1-02 | `ChatView.vue` 上帝组件（1986 行 / 20+ composable / expose 15+ 命令式方法） | `components/chat/ChatView.vue`、`views/ChatViewPage.vue` |
| P1-03 | 状态四通道并行 + 85 处裸 localStorage 散落组件 | 全局 |
| P1-04 | `any` 458 处，隐藏字段挂载在消息对象上 | `useChatSSE.ts`、`useChatAgent.ts` 等 |
| P1-05 | 无虚拟滚动；流式期每帧全量重建 innerHTML | `ChatView.vue:143`、`useChatSSE.ts`（作者自注释） |
| P1-06 | 无代码分割策略；`public/images` 23MB 直发 | `vite.config.ts`、`dist/` 实测 |
| P1-07 | 工程化零闸门：无 ESLint / 无 lint 脚本 / 前端 CI 空白 / `tests/` 空目录 | `package.json`、`ci.yml` |
| P1-08 | 无全局错误处理、无 404 catch-all 路由 | `main.ts`、`router/index.ts` |
| P1-09 | 遗留物污染 + `public/icons/` 里 2 个 Python 脚本会被拷进 `dist/` | `public/icons/_fetch_icons.py`、`_render_pngs.py` |

### P2 — 设计系统与体验（7 项）

| 编号 | 问题 | 证据位置 |
|---|---|---|
| P2-01 | 三套设计 token 并存，其中 `index.css` 是**从未被 import 的死文件** | `styles/main.css:26` → `index.css` |
| P2-02 | Tailwind 4 基本没在用（全项目仅 9 处工具类）+ `!important` 230 处 | `styles/*.css`、9 个 view |
| P2-03 | 暗色主题半成品：`ChatView` 写死 `ref(false)`，但聊天域已有 96 处暗色规则 | `ChatView.vue:823`、`chat-view.css` |
| P2-04 | 字体全部走境外 CDN，国内首屏可能阻塞 10s+ | `index.html:9-11`、`main.css:3-5` |
| P2-05 | 响应式与可访问性偏弱：无 focus trap、部分图标按钮无 `aria-label`、粒子/Lottie 未接 `prefers-reduced-motion` | 全局 |
| P2-06 | 152 处 `console.*` 残留，生产环境日志噪音 | 37 个文件 |
| P2-07 | 中英混排无 i18n（`"Thinking…"` 与中文提示并存） | `ChatView.vue` 等 |

---

## 四、P0 详细分析

### P0-01 · SSE 解析器分裂，其中一套会丢字

**现象**

项目内存在 **4 套互不复用的 SSE 解析实现**：

| 位置 | 跨 chunk 缓冲 | 状态 |
|---|---|---|
| `components/chat/composables/useChatSSE.ts:852-857` | ✅ `buffer = lines.pop() \|\| ""` | 正确 |
| `components/chat/composables/useChatSend.ts:113-114` | ❌ 直接 `chunk.split('\n')` | **有缺陷** |
| `components/chat/composables/useNotificationSSE.ts:33` | 未统一 | 待核 |
| `components/chat/sideEventStream.ts:38` | 未统一 | 待核 |

**证据**（`frontend/src/components/chat/composables/useChatSend.ts:109-114`）：

```ts
while (true) {
  const { done, value } = await reader.read()
  if (done) break

  const chunk = decoder.decode(value, { stream: true })
  const lines = chunk.split('\n')      // ← 没有 buffer = lines.pop()
```

对照正确实现（`useChatSSE.ts:855-857`）：

```ts
buffer += decoder.decode(value, {stream: true})
const lines = buffer.split("\n")
buffer = lines.pop() || ""            // ← 保留最后一个不完整行，等下一 chunk 拼
```

**根因**

SSE 的传输单位是网络 chunk，不是行。一个 `data: {"choices":[{"delta":{"content":"你好"}}]}` 行完全可能被切在两个 chunk 中间。缺 buffer 时，前半段和后半段都会 `JSON.parse` 失败，然后被同一个空 `catch {}`（`useChatSend.ts:163-164`）**静默吞掉**。

**影响**

- 直连 `/chat/completions` 路径下，长 JSON（含 `reasoning_content`、`tool_calls`）命中率显著上升
- 表现为"回复突然少了一截"，无异常、无日志、无上报
- 与另一条 agent SSE 路径行为不一致，导致"同样是聊天，有时正常有时不正常"的不可复现 bug 报告

**修法**

1. 抽 `utils/sseParser.ts`：

```ts
export function createSseParser(onEvent: (evt: { event?: string; data: string }) => void) {
  let buffer = ''
  return (chunk: string, flush = false) => {
    buffer += chunk
    const lines = buffer.split('\n')
    buffer = flush ? '' : (lines.pop() ?? '')
    // ...按 event:/data: 归组后回调
  }
}
```

2. 四个调用点全部换成它，删除三份重复实现
3. 把 `catch {}` 改成 `catch (e) { console.warn('[SSE] parse failed', raw, e) }` —— 至少让问题可见
4. 给这个 parser 写单测：模拟"行被 chunk 切断"的输入序列（这是**成本最低、收益最高**的一个测试）

**验收标准**：`useChatSend` 与 `useChatSSE` 走同一个 parser 实例；新增单测覆盖 `data:` 行跨 2 个 chunk、跨 3 个 chunk、`\r\n` 换行三种情况。

---

### P0-02 · 消息列表用索引做 `key`

**证据**（`frontend/src/components/chat/ChatView.vue:143-144`）：

```vue
v-for="(msg, idx) in currentMessages"
:key="idx"
```

**根因**

索引不是身份。而消息列表会**插入（新消息）、删除（回滚/重试）、整表替换（切换会话）**，三种操作都会让索引与内容错位。

**影响**

`AssistantMessageCell` 内部持有大量有状态资源：`_turnRt`（TurnRuntime 事件闸）、`_streamEngine`（播放器）、`_thinkState`、iframe 实例、Lottie 实例。索引 key 导致 Vue 复用**错误的组件实例**，产生：

- 上一条消息的思考链串到这一条
- 播放器还在匀速输出，但 `msg.content` 已被换成新消息内容
- `_persisted` 守卫状态错位 → 消息重复落库或漏落库

这类 bug 的特征是"偶发、不可稳定复现、清空缓存就好"，是线上最难查的一类。

**修法**

1. 消息创建处生成稳定 id：`id: msg.turn_id ?? crypto.randomUUID()`
2. `:key="msg.id"`（若历史数据缺 id，用 `key = id ?? (role + created_at + index)` 兜底，但必须记录缺失率）
3. 在 `AssistantMessage` 类型里把 `id: string` 声明为必填，靠 TS 强制

---

### P0-03 · `_userFetched` 导致换号后身份不刷新

**证据**（`frontend/src/router/index.ts:107-129`）：

```ts
let _userFetched = false          // 模块级锁，跨路由/跨登录周期存活

router.beforeEach(async (to, _from, next) => {
  const token = localStorage.getItem('token')
  // ...
  if (token && to.meta.requiresAuth && !_userFetched) {
    _userFetched = true
    try {
      const authStore = useAuthStore()
      await authStore.fetchCurrentUser()
    } catch {
      _userFetched = false        // ← 只有失败才重置
      // ...
```

**根因**

这个锁只在**拉取失败**时重置为 `false`。`authStore.logout()` 清的是 store 和 localStorage，**不会碰这个模块级变量**。

**复现路径**

1. A 用户登录 → 守卫拉取 `/auth/me` → `_userFetched = true`
2. A 用户登出 → store 清空，`_userFetched` 仍为 `true`
3. B 用户登录（同一 tab）→ 守卫判断 `!_userFetched` 为 `false` → **跳过拉取**
4. 界面显示 B 的 token 生效，但头像/昵称/权限仍是 A 的

这个项目本身带"账号状态/平台授权"概念（`stores/account.ts`、`PlatformAccounts.vue`），多账号切换是高频场景。

**修法**

```ts
// auth.ts
state: () => ({ user: null, _fetched: false }),
actions: {
  async fetchCurrentUser() { ...; this._fetched = true },
  logout() { ...; this._fetched = false }
}
```

守卫里改成 `if (token && to.meta.requiresAuth && !authStore._fetched)`。锁必须**跟生命周期走**，不能放在模块作用域。

---

### P0-04 · Token 刷新两套实现，失败后不跳登录

**证据一：两套队列各自维护**

`frontend/src/api/client.ts` 里有两条完全独立的刷新链路：

| 链路 | 变量 | 行号 | 触发场景 |
|---|---|---|---|
| axios 拦截器 | `_isRefreshing` / `_refreshSubscribers` | `:36-119` | 普通 API 请求 |
| fetch 独立函数 | `_fetchIsRefreshing` / `_fetchRefreshSubscribers` | `:121-159` | SSE（`useChatSSE` / `useChatSend`） |

两者互不知道对方在刷新。并发场景（页面加载时同时发起普通请求 + 建立 SSE）会让**同一个 `refresh_token` 被消费两次**。很多后端 refresh token 是一次性的，第二次必然 401 → 该链路被强制登出。

**证据二：失败后静默**

```ts
} catch (refreshError) {
  localStorage.removeItem('token')
  localStorage.removeItem('refresh_token')
  // DEV: 登录验证已暂停，refresh 失败不跳登录页
  return Promise.reject(refreshError)
}
```

生产环境下 token 过期 + refresh 失效 → **不跳转、不清用户态、不提示**，用户看到的是"一直转圈 / 加载失败"，没有任何引导。这条注释是开发期遗留，但已随代码进入主干。

**修法**

1. 单一 `refreshPromise` 单例：

```ts
let inflight: Promise<string> | null = null
export function ensureToken(): Promise<string> {
  if (!inflight) inflight = doRefresh().finally(() => { inflight = null })
  return inflight
}
```

axios 拦截器与 SSE 的 `tryRefreshToken` 都 `await ensureToken()`，删除 `_fetchRefreshSubscribers` 那一套
2. refresh 失败统一走 `authStore.forceLogout()`：清 store → 清 storage → `router.replace('/login?redirect=...')`
3. 区分"网络失败"与"refresh 无效"：网络抖动应重试，refresh 明确无效才登出

---

### P0-05 · XSS 面：未过滤 `v-html` + iframe 沙箱形同虚设

**证据一：不可信 HTML 直接 `v-html`**

`frontend/src/components/chat/renderers/AudioLightbox.vue:16`：

```vue
v-html="page.htmlContent"
```

`htmlContent` 来自后端/Agent 生成，属于**不可信输入**。全项目 19 处 `v-html` 里，这一处风险最高（其余多为 markdown-it 渲染产物，且 markdown-it 配置了 `html: false`，见 `markdown-renderer.ts`）。

**证据二：`allow-same-origin` + `allow-scripts` = 没有沙箱**

共 5 处同时给了这两个 flag：

| 文件 | 行号 |
|---|---|
| `components/chat/renderers/HtmlRenderer.vue` | 4 |
| `components/chat/renderers/HtmlLightbox.vue` | 4 |
| `views/PortfolioDetailView.vue` | 223 / 257 |
| `components/chat/ChatView.vue` | 333 |

MDN 对这两个 flag 组合有明确警告：**iframe 内脚本可以直接 `document.body.removeAttribute('sandbox')` 把自己解放出来**，之后即可访问父窗口同源资源。

**影响**

一旦 Agent 输出（或用户提交的作品 HTML）含恶意脚本：

1. 读取 `localStorage.getItem('token')` → 外传 → 账号被接管（配合 P0-06 放大）
2. 以当前登录用户身份调用后端 API
3. 修改页面 DOM 钓鱼

这个项目的核心能力就是"Agent 生成 HTML 卡片/页面并渲染预览"，**这条链路是产品的必经之路，风险不是假设性的**。

**修法**

1. `AudioLightbox` 的 `v-html` 改走 `dompurify` 白名单，或同样改成 `srcdoc` + iframe
2. 5 处 sandbox 去掉 `allow-same-origin`（保留 `allow-scripts` 即可运行脚本，但拿不到父窗口同源权限）
3. 若必须保留同源（如需读写 iframe 内 DOM 做截图/测量），改为**独立子域**部署预览内容（如 `preview.example.com`），靠域名隔离而非 sandbox
4. 后端侧加输出过滤作为第二道防线

---

### P0-06 · token 存 localStorage + 双认证并行

**证据**

- `stores/auth.ts`：token / refresh_token / `mint_user_info` 全部写 localStorage
- `api/client.ts:20` 附近：`withCredentials: true`（cookie 通道同时开着）
- SSE 走 fetch 时手动拼 `Authorization: Bearer ...`

**根因**

安全模型不清晰：header 认证与 cookie 认证并行，且高价值凭证放在 JS 可读的存储里。结合 P0-05 的 XSS 面，形成完整的"注入 → 取 token → 接管"链条。

**修法（分阶段）**

- 短期：token 迁到内存 + 用 httpOnly refresh cookie；localStorage 只留非敏感标记
- 中期：全量 httpOnly cookie + CSRF token，移除 header 认证分支
- 长期：预览内容独立域（配合 P0-05）

---

### P0-07 · 40 处原生 `alert()`

**分布**

| 文件 | 处数 |
|---|---|
| `components/settings/AgentManager.vue` | 7 |
| `views/SettingsView.vue` | 6 |
| `views/TaskPlanView.vue` | 5 |
| `components/workbench/ConfigPage.vue` | 4 |
| `components/settings/PluginManagerInline.vue` | 4 |
| `components/settings/PluginDetailView.vue` | 4 |
| `components/chat/composables/useChatVideos.ts` | 3 |
| 其余 5 个文件 | 17 |

**影响**

`alert` 是**同步阻塞**的，会冻结整个主线程。而此时：

- SSE `reader.read()` 循环被挂起
- 播放器 `requestAnimationFrame` 循环被挂起
- 用户点掉 alert 后，累积的 delta 一次性灌入 → 文字瞬间喷出一大段

同时 `alert` 无法样式化、无法键盘导航、无法被测试、无法上报埋点。

**修法**

实现 `useToast()`（或复用已有的 `ConfirmDialog.vue` 模式扩展出轻量 toast），40 处全量替换。`useChatVideos` / `useChatImages` 里的 `alert` 应改为**内联错误态**（这两个是 composable，本来就不该弹窗）。

---

## 五、P1 详细分析

### P1-01 · 没有布局壳，9 个页面各复制一套

**证据**

`frontend/src/views/HomeView.vue:1-20`：

```vue
<div class="min-h-screen" style="position: relative;">
  <div class="mint-shell da-shell" :class="{ 'mint-collapsed': isSidebarCollapsed }"
       :style="{ '--left-sidebar-width': leftSidebarWidth + 'px' }">
    <SidebarNav :current-page="'home'" :is-collapsed="isSidebarCollapsed"
      @nav-click="handleNavClick" @toggle-sidebar="toggleSidebar" @go-eco="goToEco"
      @open-settings="openSettings" @new-workflow="goToWorkbench" @new-chat="goToChat" />
    <div v-if="!isSidebarCollapsed" class="da-sidebar-resizer" @mousedown="startLeftResize">
      <div class="da-sidebar-resizer-line"></div>
    </div>
```

这段（含 6 个事件绑定、拖拽分隔条、宽度/折叠状态）在 **9 个 view 里共出现 37 次相关引用**：

`HomeView`、`ChatViewPage`、`MyWorksView`、`PortfolioView`、`PortfolioDetailView`、`TaskPlanView`、`SkillsView`、`TopicPoolView`、`TopicPoolDetailView`

**代价**

1. 改一次侧边栏宽度/折叠逻辑 → 动 9 个文件
2. `handleNavClick` 里的路由映射每个页面各写一遍，**已经出现不一致**
3. 折叠状态、宽度持久化、resize 监听在每个页面各注册一份（潜在监听器泄漏）
4. 每个页面都要 import `SidebarNav` + 一组 composable，重复打包

**修法**

```
src/layouts/AppShell.vue      ← 侧边栏 + resizer + 折叠状态（唯一实现）
src/router/index.ts           ← 嵌套路由：{ path: '/', component: AppShell, children: [...] }
```

页面组件只保留业务内容，`<RouterView>` 挂在 shell 内。预计可删掉 **~1500 行**重复代码。

**验收标准**：`grep -r "da-sidebar-resizer" src/views` 结果为 0；新增页面不再需要复制任何布局代码。

---

### P1-02 · `ChatView.vue` 是上帝组件

**证据**

- 文件 **1986 行**
- 注入 **20+ 个 composable**
- `defineExpose` 向父页面暴露 **15+ 个命令式方法**：`newConversation / switchToConversation / deleteConversation / activateSkill / openSettings / openEco / handleEcoSwitchToChat / goEco ...`
- 父页面 `views/ChatViewPage.vue` 里用 `chatRef.value?.xxx()` 调用，且存在 `setTimeout(() => chatRef.value?.xxx(), 300)` 这类**时序魔法**

**问题**

Vue 里"父组件持 ref 命令式调子组件"是明确的反模式（等价 Vue 2 `$refs` 满天飞）。它导致：

1. **状态归属不清**：会话列表、当前会话、侧栏折叠到底归谁管？结果是 `useUIState`（模块级 ref）、`stores/chatContext.ts`、ChatView 本地 ref、父页面 ref 四处各存一份
2. **时序不可控**：`setTimeout(300)` 是竞态的直接来源——子组件还没 mount 完就调用，静默失败
3. **不可测试**：1986 行的组件没有任何测试入口

**修法**

1. 会话域状态全部进 `useChatStore`（Pinia），组件只读不持
2. 父子之间只走 `props` + `emit`；跨层用 `provide/inject` 或 store，**不再 expose 一坨方法**
3. 把 `setTimeout` 调用改成"等待就绪的 promise"（`await chatReady`）或事件驱动

---

### P1-03 · 状态管理四通道并行

| 通道 | 规模 | 问题 |
|---|---|---|
| Pinia | 9 个 store | 正常 |
| 模块级 `ref` | `composables/useUIState.ts`（32 行） | 单例但不可测试、不可重置 |
| 裸 `localStorage` | **120 处 / 26 文件**（生产约 85） | key 是裸字符串（`mint_dark_theme`、`pending_topic_reference`、`workbench_ui_state_v2`…），无命名空间、无版本、无 try/catch、无序列化封装 |
| `window` 自定义事件 + sessionStorage | `app:show-welcome-ad`、`works:updated`、`settings:open`、`redirect_after_login` | 隐式契约，改名即静默失效 |

**典型症状**：同一个"主题"概念存在两套互不相干的真相

- `views/EcoView.vue:58` → `ref(localStorage.getItem(SK_DARK) === '1')`（有持久化）
- `components/chat/ChatView.vue:823` → `const darkTheme = ref(false)`（写死，无持久化）

**修法**

1. 建 `utils/storage.ts`：

```ts
const NS = 'pulse'
export const storage = {
  get<T>(key: string, fallback: T, schema?: (v: unknown) => T | null): T { /* try/catch + JSON + 版本 */ },
  set<T>(key: string, value: T): void { /* 配额满/隐私模式兜底 */ },
  remove(key: string): void
}
```

2. 85 处生产代码的 localStorage 全部收口，key 统一为 `pulse:<domain>:<field>`
3. 主题进 `useThemeStore`，`EcoView` / `ChatView` 都消费同一个 store

---

### P1-04 · `any` 458 处

**分布（Top）**

| 文件 | 处数 |
|---|---|
| `composables/useChatSSE.ts` | 50 |
| `composables/useChatAgent.ts` | 30 |
| `components/card-editor/CardDeckEditor.vue` | 27 |
| `components/chat/ChatView.vue` | 17 |
| `stores/plugin.ts` | 15 |
| `stores/work.ts` | 14 |
| `components/chat/AssistantMessageCell.vue` | 13 |
| `views/PortfolioDetailView.vue` | 12 |

**关键观察：不是"懒得写类型"，而是"类型定义不描述真实数据"**

大量 `any` 挂在**消息对象的私有字段**上：

```ts
(assistantMsg as any)._turnRt      // TurnRuntime 事件闸
(assistantMsg as any)._thinkState  // 思考链状态机
(assistantMsg as any)._persisted   // 落库幂等守卫
(assistantMsg as any)._streamEngine// 播放器实例
```

这说明 `AssistantMessage` 类型已经**落后于真实数据结构**。类型系统在这里失去它最大的价值：重构时告诉你哪里会炸。

项目里已有 `components/chat/sse-contract.ts`（契约意识是有的），但没落到消费端。

**修法（三步，增量可行）**

1. **补全类型**：把 `_turnRt` 等隐藏字段正式声明进 `AssistantMessage`（标 `@internal`），这一步就能消掉 30+ 处 `any`
2. **SSE 事件做判别联合**：

```ts
type ChatSseEvent =
  | { type: 'chat_turn_started'; turn_id: string; payload: ... }
  | { type: 'workflow_started'; payload: ... }
  | { type: 'agent_loop_done'; payload: { ended_reason: EndedReason } }
```

替代 `parsed: any` + 层层 `?.` 猜测
3. **卡住增量**：ESLint `no-explicit-any: warn`，CI 统计数量，只许降不许升；再逐步开 `noUncheckedIndexedAccess`

---

### P1-05 · 无虚拟滚动 + 流式期每帧全量重建

**证据一：消息列表全量渲染**

`ChatView.vue:143` 直接 `v-for` 全部 `currentMessages`，每条 `AssistantMessageCell` 内含 Markdown AST、highlight.js、iframe、Lottie、执行轨迹面板。

一个 50 轮的会话 = 50 个完整富文本实例**常驻 DOM + 常驻 watcher**。滚动掉帧、内存爬升会随会话长度线性恶化。

**而"长会话"恰恰是这个产品的核心场景**（多智能体流水线对话天然轮次多），所以这条不能当作"以后再说"。

**证据二：稳定区每帧 innerHTML 重建**

`useChatSSE.ts` 内作者自注释："稳定区每帧整体 innerHTML 重建"。流式期每秒 30-60 帧 × 整段已稳定文本重新解析 + 重建 DOM，长回复时 CPU 占用明显。

**修法**

1. 引入 `vue-virtual-scroller`（可变高度模式）或自研可变高度虚拟列表；关键是**流式中的那条消息必须固定可见**，不能被回收
2. 稳定区改为"增量追加节点"而非"整段重建"：维护节点引用，只 append 新块
3. 流式结束后再跑一次完整 markdown 解析做最终修正（现有双游标设计已为此留了空间）

---

### P1-06 · 打包与静态资源

**产物实测**

| 项 | 大小 |
|---|---|
| `dist/assets` | 2.09 MB |
| `dist/images` | **22.95 MB** |
| `dist/fonts` | 2.50 MB |
| `dist/icons` | 1.96 MB |
| **`dist/` 合计** | **29.51 MB** |

**单个 JS chunk Top 5**

| 文件 | 大小 |
|---|---|
| `ChatViewPage-*.js` | 232 KB |
| `chat-content-renderer-*.js` | 197 KB |
| `thinking-*.json`（Lottie） | 148 KB |
| `index-*.css` | 134 KB |
| `ChatViewPage-*.css` | 116 KB |

**问题一：`public/images` 23MB 直发**

`public/` 下资源**不过构建管线**，原样拷进 `dist/`：

- 23 个图片文件：13 PNG + 8 JPG + 2 SVG
- 单文件最大 **3.8MB**（`img_4.png`），其次 3.5MB、3.5MB、2.3MB
- 未压缩、未转 WebP/AVIF、未按需加载、未设尺寸占位

对一个以"生成图片卡片"为核心的产品，这些图很可能是首屏展示素材 —— 直接拖慢 LCP。

**问题二：`vite.config.ts` 无构建优化配置**

当前只有 `plugins / assetsInclude / resolve.alias / css / server.proxy / test`，**没有**：

- `build.rollupOptions.output.manualChunks`
- `build.assetsInlineLimit` 调整
- CDN 外置（three.js / tiptap / vue-flow）
- `build.chunkSizeWarningLimit`（默认 500KB 时这些 chunk 已在边缘）

**问题三：重依赖全量**

`package.json` 里的重量级依赖：`three`、`@tsparticles/vue3` + `tsparticles` + `tsparticles-slim`（三个包）、`@vue-flow/core` + `minimap` + `controls` + `background`、`@tiptap/*`（11 个包）、`html2canvas`、**两套 Lottie**（`lottie-web` 和 `@lottiefiles/dotlottie-vue`）。

**修法**

1. 图片：转 WebP/AVIF（质量 80），加 `width/height` 防 CLS，首屏外的 `loading="lazy"`，目标 23MB → 3MB 以内
2. `manualChunks` 拆分：`three` / `tiptap` / `vue-flow` / `lottie` / `markdown` 各自成 chunk，或对 three 走 CDN
3. 移除重复 Lottie 实现（保留一个）
4. 检查 `tsparticles` 三个包是否都需要（`tsparticles-slim` 与 `tsparticles` 常可合并）

---

### P1-07 · 工程化零闸门

**现状**

| 检查项 | 状态 |
|---|---|
| ESLint 配置 | ❌ 无 |
| Prettier 配置 | ❌ 无 |
| stylelint | ❌ 无 |
| `package.json` 的 `lint` 脚本 | ❌ 无（只有 `dev / build / preview / test / test:watch`） |
| 前端 CI | ❌ `.github/workflows/ci.yml` 只有 Python：`black` / `isort` / `flake8` / `pytest` / 后端构建 |
| `frontend/tests/` | ❌ 目录存在但**为空** |
| 前端测试覆盖 | 23 个测试文件，但**核心逻辑无覆盖** |
| `.env.example` | ❌ 前端无（本地只有 `.env`） |

**最该补测试的四个模块（都是纯逻辑，测试成本极低）**

| 模块 | 为什么必须测 |
|---|---|
| SSE parser（P0-01 抽出后） | 跨 chunk 边界是纯字符串状态机，单测 5 行代码即可覆盖 |
| `StreamEngine` 双游标播放器 | 速率/截断/收尾时序，全是可枚举状态 |
| `TurnRuntime` 事件闸 | `accepts()` 是纯函数，事件序列可穷举 |
| 平台适配器 `adapters/*.ts` | 发布格式转换，错了会发错内容到线上账号 |

**修法**

1. 加 ESLint（`@typescript-eslint` + `eslint-plugin-vue`）+ Prettier，`package.json` 补 `"lint": "eslint . --max-warnings 0"` 与 `"format"`
2. CI 加前端 job：`npm ci && npm run lint && npm run test && npm run build`
3. 补上述 4 个模块的单测（目标：新增 30+ 用例）
4. 前端补 `.env.example`，列出 `VITE_API_BASE_URL` 等变量

---

### P1-08 · 无全局错误处理、无 404 路由

**证据**

`main.ts`（38 行）只有：`createApp` → `use(Pinia)` → `use(router)` → `mount`。**没有**：

- `app.config.errorHandler`
- `app.config.warnHandler`
- `window.onunhandledrejection`
- 任何性能/异常上报 SDK

`router/index.ts` 路由表**没有 catch-all**，访问未知路径时 Vue Router 会告警但页面空白。

全项目只有 `views/SettingsView.vue` 一处局部 `onErrorCaptured`。

**影响**

线上白屏/组件崩溃时**只能靠用户截图**。SSE 播放器里那个 `catch {}` 吞掉的异常同样进不了任何上报通道。

**修法**

```ts
app.config.errorHandler = (err, instance, info) => reportError(err, { info, component: instance?.$options.name })
window.addEventListener('unhandledrejection', e => reportError(e.reason))
```

加 catch-all 路由：`{ path: '/:pathMatch(.*)*', component: NotFoundView }`

---

### P1-09 · 遗留物污染 + `public/` 里的 Python 脚本会被发布

**仓库根目录遗留**

`tmp_recon.bin`、`_eval_workflow.py`、`stream_sim.mjs`、`vt.log`、`dev.log`、`.tmp_*.log`、`prototype/time-lane-canvas.html`、`scripts/dev-safe.mjs`、`scripts/convert-lucide.mjs`、根目录 3 份设计 `.md`

**更严重：`public/icons/` 下的两个 Python 脚本**

```
frontend/public/icons/_fetch_icons.py
frontend/public/icons/_render_pngs.py
```

`public/` 目录内容会**原样拷贝到 `dist/` 并可通过 URL 访问**。也就是说构建产物里存在可下载的后端工具脚本 —— 至少是信息泄露，属于应当立刻清理的低成本风险。

**修法**

1. 两个 `.py` 移到 `scripts/icons/`（不参与构建）
2. `.gitignore` 补 `*.log`、`.tmp_*`、`tmp_*`（部分已有，`tmp_recon.bin` 这类无扩展名需单独加）
3. `prototype/` 移出 `frontend/`（或加入 `.gitignore`）
4. 根目录设计文档归入 `docs/`

---

## 六、P2 详细分析

### P2-01 · 三套设计 token，其中一套是死文件

| 体系 | 定义处 | 前缀 | 状态 |
|---|---|---|---|
| 语义 token | `styles/main.css:10-52` | `--text-*` / `--bg-*` / `--color-*` | 在用 |
| 组件库 token | `index.css` | `--ma-*` | **全项目无任何文件 import** |
| 聊天渲染层 | `render-layer.css` / `chat-view.css` | `--rl-*` | 在用 |

**死引用链条**

`styles/main.css:26`：

```css
--bg-page: var(--ma-bg-base, #EBEEF2);
```

而 `--ma-bg-base` **只在从未被引入的 `index.css` 里定义**（值 `#F5F6F7`）。`main.ts` 只 import 了 `main.css` / `workbench.css` / `render-layer.css`。

结论：**线上永远走 fallback `#EBEEF2`**，而设计意图里写的是 `#F5F6F7`。这类"看起来有、实际没有"的 token 是最难排查的样式 bug 来源（改 `index.css` 里的值不会有任何效果）。

**修法**：二选一 —— ① 删掉 `index.css`（或把 `--ma-*` 并入 `main.css` 并真正 import）；② 让 `main.css` 显式 `@import` 它。无论哪种，**必须消除"定义存在但永不生效"的 token**。

---

### P2-02 · Tailwind 形同虚设 + `!important` 230 处

**证据**

- `@tailwindcss/vite` + `tailwindcss@4.3` 已装已配（`vite.config.ts:6`）
- 但全项目**仅 9 个 view 各用 1 处**工具类（基本都是 `min-h-screen`）
- 手写 CSS 三大文件：`main.css` 2556 行、`chat-view.css` 2394 行、`render-layer.css` 1232 行
- `!important` **230 处 / 33 文件**（`styles/workbench/_nav.css` 单文件 44 处）

**判读**

Tailwind 装了但没用 = **白付构建成本**（v4 的 Vite 插件要扫描全量模板）。而 230 处 `!important` 是层叠失控的量化证据 —— 说明样式覆盖是靠"加权重"而不是靠结构。

命名前缀也碎片化：`rl-*`、`mint-*`、`hp-*`、`da-*`、`eco-*`、`ctx-*`、`wdp-*`、`ma-*` 八套，看不出层级关系。

**修法**：明确二选一 ——

- **A**：认真用 Tailwind（design token 走 `@theme`，逐步替换手写 CSS，`!important` 只允许出现在第三方库覆盖处）
- **B**：删掉 Tailwind 依赖，专心 scoped CSS / CSS Modules

现在"两头都付成本、两头都没收益"是唯一不该保留的选项。

---

### P2-03 · 暗色主题是半成品

| 位置 | 状态 |
|---|---|
| `views/EcoView.vue:58` | ✅ 完整：切换 UI + `mint_dark_theme` 持久化 |
| `components/chat/ChatView.vue:823` | ❌ `const darkTheme = ref(false)` —— 写死，无 UI、无持久化 |
| 聊天域 CSS | 已写 **96 处**暗色相关规则/引用：`ContextSidebar.vue` 47、`chat-view.css` 15、`chat-view-global.css` 11、`FileManagerPanel.vue` 10、`AssistantMessageCell.vue` 6、`render-layer.css` 3、`InspirationRail.vue` 2、`ChatView.vue` 2 |
| `styles/workbench/_dark-theme.css` | 存在 |

付了暗色样式的**编写与维护成本**，但用户永远看不到聊天区的暗色 —— 因为开关变量永远是 `false`（96 处规则中有相当一部分属于"永不命中"的死规则）。

**修法**：要么接入 `useThemeStore` 打通（推荐，样式已经写好了），要么删除死规则避免误导后来者。**不要保留中间态**。

---

### P2-04 · 字体全部走境外 CDN

**证据**

`index.html:9-11`：

```html
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans...&family=Fraunces...&family=LXGW+WenKai...&display=swap" rel="stylesheet">
```

`styles/main.css:3-5`：

```css
@import url('https://fonts.googleapis.com/css2?family=DM+Sans...');
@import url('https://fonts.googleapis.com/css2?family=Host+Grotesk...');
@import url('https://cdn.jsdelivr.net/npm/lxgw-wenkai-web-font@1.7.0/style.css');
```

**问题**

1. **`fonts.googleapis.com` 在中国大陆基本不可达** —— 而这是个中文小红书创作工具，用户主体在国内
2. CSS 顶部的 `@import` 是**渲染阻塞**的，请求超时前页面可能长时间空白（10s+ 量级）
3. 同一字族在 `index.html`（`link`）和 `main.css`（`@import`）**重复引入**
4. 只有 `AlimamaDaoLiTi` 是本地 woff2（`public/fonts/`，2.5MB）

**修法**

1. 字体全部本地化（`public/fonts/` 已有 2.5MB 基础），`font-display: swap`
2. 中文用 `LXGW WenKai` 这类可自托管的开源字体，做**子集化**（只保留用到的字符）
3. 移除 CSS 里的 `@import`，统一改 `<link rel="preconnect">` + `<link rel="stylesheet">` 放 `<head>`
4. 去掉 `index.html` 与 `main.css` 的重复字族声明

---

### P2-05 · 响应式与可访问性

| 指标 | 实测 | 判读 |
|---|---|---|
| `@media` 查询 | 55 处 / 26 文件（共 84 个组件） | 仅约 1/3 组件有响应式处理，整体桌面优先 |
| `aria-*` / `role=` | 79 处 / 20 文件 | 部分自定义控件做了 `role=button` + 键盘处理（有意识），但大量图标按钮只有 `title` 无 `aria-label` |
| focus trap | **无** | Teleport 出来的 drawer / modal / lightbox 打开后 Tab 会跑到背景，关闭后焦点不回收到触发按钮 |
| `prefers-reduced-motion` | 14 处，但**局部** | `SkillsView` / `LoginView` / `chat-view.css` / `render-layer.css` 有；`SpongeBobCompanion` / `CrabCompanion` / `CatCompanion` 的 Lottie 与 tsparticles 粒子**无降级开关** |

**响应式侧的具体隐患**：侧边栏宽度靠拖拽（`da-sidebar-resizer`），折叠状态是布尔值，**移动端路径不明确**——窄屏下 `--left-sidebar-width` 是固定 px，没有断点降级策略。

**修法**

1. 定义 3 个断点（`≥1440` / `1024–1439` / `<1024`），侧边栏在 `<1024` 自动折叠为抽屉
2. 做一个 `useFocusTrap(ref)`，所有 Teleport 浮层接入
3. 所有纯图标按钮补 `aria-label`；图标 + 文字的补 `aria-hidden` 到图标
4. 粒子/Lottie 组件统一读 `prefers-reduced-motion`，命中则渲染静态帧

---

### P2-06 · 152 处 `console.*`

**分布 Top**

| 文件 | 处数 | 其中 `console.log` |
|---|---|---|
| `useChatAgent.ts` | 19 | 15 |
| `useChatSSE.ts` | 19 | 7 |
| `useNotificationSSE.ts` | 10 | 4 |
| `useChatStream.ts` | 7 | — |
| `ChatView.vue` | 6 | — |

**关键问题**：`useChatSSE.ts:869-873` 在**每收到一条 SSE 事件**就打印一个对象（含 `epochMatch`、`turnId` 等）。一次 30 轮的 Agent 会话 = 数百次对象序列化，DevTools 内存占用 + 主线程抖动。

**修法**

1. 建 `utils/logger.ts`：`dev` 环境输出，生产静默或只保留 `error`
2. 用构建期剔除：`esbuild.drop: ['console', 'debugger']`（保留 `error` 需自定义插件）
3. `useChatSSE` 里那条逐事件日志改为"仅当 `epoch` 不匹配（异常路径）时打印"

---

### P2-07 · 中英混排无 i18n

`ChatView.vue` 的 `isChatMode` 分支里 `"Thinking…"` / `"Writing…"` 与中文提示并存；状态文案散落在各组件内，无统一文案表。

即使不做完整 i18n，也应有一份 `constants/copy.ts` 集中管理，至少保证**同一语义在全站文案一致**。

---

## 七、修复路线图

### 第一批 · 止血（1–2 天，低风险高收益）

| # | 动作 | 对应问题 | 验收 |
|---|---|---|---|
| 1 | 抽 `createSseParser`，四处调用点统一，**修掉 `useChatSend` 缺 buffer** | P0-01 | 新增单测：跨 2/3 个 chunk + `\r\n` |
| 2 | 消息 `key` 换稳定 id | P0-02 | `grep ':key="idx"'` = 0 |
| 3 | `_userFetched` 收进 auth store，`logout()` 重置 | P0-03 | 换号后 `/auth/me` 必被调用 |
| 4 | 单一 `refreshPromise`，失败统一 `forceLogout` | P0-04 | 并发 2 请求只触发 1 次 refresh |
| 5 | 5 处 sandbox 去掉 `allow-same-origin`；`AudioLightbox` 接 dompurify | P0-05 | 恶意 `<script>` 不执行 |
| 6 | 字体本地化，去掉 CSS `@import` | P2-04 | 断网仍能正常渲染 |
| 7 | 加 ESLint + Prettier + `lint` 脚本，**CI 加前端 job** | P1-07 | CI 跑 `lint && test && build` |

### 第二批 · 结构性减负（约 1 周）

| # | 动作 | 对应问题 |
|---|---|---|
| 8 | 抽 `layouts/AppShell.vue` + 嵌套路由，删 9 份重复壳 | P1-01 |
| 9 | 建 `utils/storage.ts` 收口 85 处 localStorage；`useThemeStore` 统一主题 | P1-03 / P2-03 |
| 10 | `alert()` → `useToast()`，40 处全量替换 | P0-07 |
| 11 | 消息列表虚拟滚动（流式消息固定可见） | P1-05 |
| 12 | `app.config.errorHandler` + `unhandledrejection` + 404 路由 | P1-08 |
| 13 | `public/images` 转 WebP + 懒加载（23MB → ≤3MB） | P1-06 |
| 14 | 清理遗留物，`public/icons/*.py` 移出 | P1-09 |

### 第三批 · 防劣化（持续）

| # | 动作 | 对应问题 |
|---|---|---|
| 15 | `any` 递减：先补 `AssistantMessage` 隐藏字段 → SSE 事件判别联合 → ESLint `no-explicit-any: warn` 卡增量 | P1-04 |
| 16 | 补 4 个纯逻辑模块单测（SSE parser / StreamEngine / TurnRuntime / adapters） | P1-07 |
| 17 | `manualChunks` 拆分 + CDN 外置（three / tiptap / vue-flow / lottie） | P1-06 |
| 18 | 收敛 CSS：删 `index.css` 或真正引入；定 `!important` 上限；统一前缀 | P2-01 / P2-02 |
| 19 | `useFocusTrap` + 图标按钮 `aria-label` + 粒子/Lottie 接 `prefers-reduced-motion` | P2-05 |
| 20 | token 迁内存 + httpOnly cookie；预览内容独立域 | P0-06 / P0-05 |

---

## 附录 A · 自查命令

以下命令可在 `frontend/` 目录复现本报告全部数字（PowerShell）。

```powershell
# 规模
(Get-ChildItem src -Recurse -Include *.vue).Count
(Get-ChildItem src -Recurse -Include *.ts).Count
(Get-ChildItem src -Recurse -Include *.vue,*.ts,*.css | Get-Content | Measure-Object -Line).Lines

# 类型安全
Select-String -Path (Get-ChildItem src -Recurse -Include *.ts,*.vue) -Pattern 'as any|: any|<any>' | Measure-Object

# 日志 / 弹窗
Select-String -Path (Get-ChildItem src -Recurse -Include *.ts,*.vue) -Pattern 'console\.(log|warn|error|info|debug)' | Measure-Object
Select-String -Path (Get-ChildItem src -Recurse -Include *.ts,*.vue) -Pattern 'alert\(' | Measure-Object

# 存储 / 样式
Select-String -Path (Get-ChildItem src -Recurse -Include *.ts,*.vue) -Pattern 'localStorage\.' | Measure-Object
Select-String -Path (Get-ChildItem src -Recurse -Include *.css,*.vue) -Pattern '!important' | Measure-Object

# 安全面
Select-String -Path (Get-ChildItem src -Recurse -Include *.vue,*.ts) -Pattern 'v-html' | Measure-Object
Select-String -Path (Get-ChildItem src -Recurse -Include *.vue) -Pattern 'sandbox=' | Measure-Object

# 响应式 / 可访问性
Select-String -Path (Get-ChildItem src -Recurse -Include *.css,*.vue) -Pattern '@media' | Measure-Object
Select-String -Path (Get-ChildItem src -Recurse -Include *.vue) -Pattern 'aria-|role="' | Measure-Object

# 产物
[math]::Round(((Get-ChildItem dist -Recurse -File | Measure-Object Length -Sum).Sum)/1MB, 2)
Get-ChildItem dist\assets -File | Sort-Object Length -Descending | Select-Object -First 10 Name, @{n='KB';e={[math]::Round($_.Length/1KB)}}
```

---

## 附录 B · 本报告未覆盖的范围

以下维度本次**未做**，如需要可另开分析：

1. **真机性能**：LCP / INP / CLS、长会话滚动帧率、内存增长曲线（需在浏览器 Performance 面板实测）
2. **后端联动**：SSE 契约（`sse-contract.ts`）与后端 `protocol/events.py` 的一致性校验
3. **业务正确性**：发布适配器（`adapters/xiaohongshu.ts` 等）的格式转换准确性
4. **依赖安全**：`npm audit` 与许可证合规
5. **多浏览器兼容**：Safari 的 SSE / iframe srcdoc / WebGL 表现

---

> 说明：本报告所有"问题"均标注了可核对的代码位置；若某条与你的设计意图冲突（例如 `allow-same-origin` 是为截图功能刻意保留、`_userFetched` 是为减少请求刻意加的锁），请以业务意图为准，报告提供的是**风险提示与替代方案**，不是强制结论。