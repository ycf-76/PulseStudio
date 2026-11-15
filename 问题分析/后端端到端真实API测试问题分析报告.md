# 后端端到端真实 API 测试问题分析报告

> 分析对象：`backend/`（FastAPI + 多智能体 ReAct 引擎 + 能力层 Capabilities）
> 分析日期：2026-11-06
> 分析范围：除「AI 图生视频」外的全部功能模块——鉴权、模型路由、LLM 能力探测、智能体对话、SSE 事件流、内容搜索、选题池、作品库、卡片渲染、素材管理、MCP 桥接、平台发布适配器
> 分析方式：**真实 API 端到端调用**（不用 mock、不写假数据），调用生产级第三方服务，共 71 项 E2E 用例 + 2230 项单元/契约用例
> 结论性质：**实测结果**，每条缺陷均有实际失败日志或代码证据，可逐条复现

---

## 一、总体结论

**主链路是通的，但通得不牢——一旦环境有一点点偏离"开发机默认状态"，整条链路会静默降级而不是报错。**

这次测试真正有价值的地方不是"跑过了 70 项"，而是发现了一个**极其隐蔽的根因**：所有内容源的 HTTP 客户端都默认信任系统代理。开发机上装了个本地代理（Clash 之类），但代理进程没开，于是 12 个内容源的请求**全部失败**，而失败异常消息是**空字符串**。这意味着：

- 线上/测试机只要环境变量里有个 `HTTP_PROXY` 指向不可用地址，整个搜索链路就废掉；
- 报错信息为空，日志里看不出任何线索，只能靠逐层打印才知道是代理问题；
- 更糟的是它**不会抛异常**，而是返回空列表，前端表现为"搜不到内容"，看起来像"关键词没结果"。

其余缺陷分三类：**契约缺口**（后端该有的端点压根没实现，前端调不通）、**安全缺口**（跨用户越权可读）、**测试基础设施缺陷**（测试自己不可靠，会让真实缺陷被掩盖成"通过"）。另有一项**编码层缺陷**（`plugins.py` 双 BOM）在最终回归前把整个后端拦在启动之外，属于"一行字节毁掉全部功能"的极端放大案例。

| 维度 | 现状 | 后果 |
|---|---|---|
| 网络层代理语义 | 12 个内容源默认读系统代理，其中 3 个反而**不读**配置好的代理 | 代理开/关两种状态下都有一半源是坏的 |
| 契约完整性 | MCP 桥接 3 端点、发布适配器 3 端点此前整体缺失 | 浏览器扩展发布链路、多平台发布链路完全不可用 |
| 数据契约 | 详情页解析不输出 `from_card` | 合法的单图笔记被下游误判为"低质量兜底"而丢弃 |
| 安全边界 | 会话/创作产物可跨用户按 ID 读取 | IDOR 越权 |
| 测试可信度 | 4 处测试基础设施缺陷让 20+ 项用例必然 ERROR | 修 bug 前跑测试，看到的失败大部分是"测试自己坏了" |
| 源码编码 | `plugins.py` 首字节两个 BOM，Python 只剥一个 | 后端进程 import 阶段直接退出，全部功能不可用 |

最需要警惕的一点：**除 P0-06 之外，这批缺陷里没有一个是"点一下就炸"的**。其余全部是"能用但偶发/特定环境才炸"型——代理一开就好、换个环境就全废、单图笔记偶尔丢、越权要构造 ID 才能触发。人工点测命中率极低，在没有端到端真实 API 测试的情况下，只能等用户反馈。而 P0-06 恰恰相反：它炸得彻底，却把线索指向了错误的位置（"第 1 行第 1 列"），一样难查。

---

## 二、量化指标总表

以下数字均为本次实测，非估算。

| 指标 | 实测值 | 判读 |
|---|---|---|
| E2E 真实 API 用例 | **71 项** | 70 通过 / 1 跳过 / 0 失败，耗时 306s；P0-06 修复后最终回归复跑同样 70 通过 / 1 跳过 |
| 单元 + 契约用例 | **2242 项**（当前收集数） | 全量通过，含 60 项 skip（缺录制夹具/缺可选 Key）；数量随录制夹具增长而动态变化 |
| 本次发现缺陷 | **15 项** | P0 6 项、P1 4 项、P2 5 项，**全部已修复** |
| 内容源客户端总数 | **12 个** | 全部存在 `trust_env` 默认值问题，全部已加 `trust_env=False` |
| 其中未读代理配置的源 | **3 个** | `sogou_source` / `hotboard_source` / `translator` 此前不调用 `get_proxy_url()` |
| 新增后端端点 | **6 个** | MCP 桥接 3 个 + 平台发布适配器 3 个 |
| 新增平台规格 | **1 套** | `wechat_mp`（公众号）字段规格 |
| 新增发布页自动化动作 | **2 套** | `weibo` / `wechat_mp` 页面流程 |
| 因测试基础设施缺陷而 ERROR 的用例 | **20 项** | 全部集中在 `test_collab_e2e_chain.py` / `test_collab_crawler_e2e_chain.py` |
| 因模块级环境污染而 401 的用例 | **5 项** | DeepSeek 真实集成测试 |
| 外部服务限流（非项目缺陷） | **1 类** | MyMemory 翻译 API 返回 429，项目已有降级策略 |

---

## 三、测试覆盖矩阵

| 模块 | 覆盖方式 | 用例数 | 结果 | 说明 |
|---|---|---|---|---|
| 鉴权（JWT / refresh） | 真实 HTTP | 17 | 全部通过 | 含 token 过期、refresh 自动续期、无状态校验 |
| 模型路由 + LLM 能力探测 | 真实 API | 同上 | 全部通过 | 多提供商可用性探测 |
| 智能体对话 + SSE 事件流 | 真实 HTTP + httpx-sse | 11 | 全部通过 | 含 turn_id 回合隔离、终态事件自动断流 |
| 内容搜索（12 个源） | 真实第三方 API | 13 | 全部通过 | 修复后 HackerNews 可返回 10 条真实结果 |
| 选题池 / 作品库 | 真实 HTTP | 同上 | 全部通过 | 增删改查 + 归属校验 |
| 卡片渲染（CardDeck Schema） | 真实 HTTP | 19 | 全部通过 | 渲染契约校验 |
| 素材管理 | 真实 HTTP | 同上 | 全部通过 | 上传/列举/删除 |
| MCP 桥接 | 真实 HTTP + SSE | 10 + 1 skip | 通过 | 跳过项需第二真实账号 |
| 平台发布适配器 | 真实 HTTP | 同上 | 全部通过 | 含自动化总开关拦截、标题必填校验 |
| AI 图生视频 | — | 0 | 未覆盖 | 按需求排除 |

---

## 四、问题总览清单

### P0 — 会真实出错的 Bug 与安全漏洞（6 项）

| 编号 | 问题 | 证据位置 | 用户可感知表现 |
|---|---|---|---|
| P0-01 | SSE 订阅返回 403，侧会话被误判越权 | 会话归属校验逻辑 | 侧会话里点任何东西，事件流订阅直接被拒，界面空白 |
| P0-02 | MCP Bridge 三个端点整体缺失 | `app/api/routers/mcp_bridge.py` | 浏览器扩展发布链路完全不可用，扩展注册无入口 |
| P0-03 | 平台发布适配器三个端点缺失 | `app/api/routers/workflows.py` | 只能发小红书，微博/公众号无接口 |
| P0-04 | 12 个内容源请求全部失败（系统代理） | `capabilities/content_search/sources/*` | 搜索永远返回空，且日志无任何有效错误信息 |
| P0-05 | IDOR 越权：会话与产物可跨用户按 ID 读取 | 会话/产物读取路由 | 换号后能读到别人的会话与创作内容 |
| P0-06 | `plugins.py` 双 BOM，后端整体无法启动 | `app/api/routers/plugins.py` 首字节 | 服务起不来，所有功能 100% 不可用 |

### P1 — 功能不完整与数据契约缺口（4 项）

| 编号 | 问题 | 证据位置 | 用户可感知表现 |
|---|---|---|---|
| P1-01 | `wechat_mp` 平台规格缺失 | `capabilities/messaging/services/platform_adapter.py` | 公众号发布无字段校验，标题为空也能提交 |
| P1-02 | `weibo` / `wechat_mp` 发布页自动化动作缺失 | `capabilities/messaging/services/platform_publisher.py` | 这两个平台的自动化发布走不到页面动作 |
| P1-03 | 3 个源不读代理配置 | `sogou_source` / `hotboard_source` / `translator` | 配了代理也不生效，与其余源行为不一致 |
| P1-04 | 详情页解析不输出 `from_card` | `capabilities/scrapling/spiders/xhs_spider.py` | 单图笔记被判为"低质量兜底"而丢弃 |

### P2 — 测试可信度与降级语义缺陷（5 项）

| 编号 | 问题 | 证据位置 | 影响 |
|---|---|---|---|
| P2-01 | `asyncio.get_event_loop()` 在 Python 3.12 抛错 | 两个 collab 测试文件的 fixture teardown | 20 项用例必然 ERROR |
| P2-02 | 测试桩缺 `auto_approve` 属性 | `tests/test_llm_drives_image_text_skill.py` | 路由断言失败，真实链路被降级掩盖 |
| P2-03 | 模块级污染 `DEEPSEEK_API_KEY` | `tests/test_matting_live3b.py` | 同进程 5 项真实 DeepSeek 测试全部 401 |
| P2-04 | 内容源单例跨事件循环复用 | `tests/test_crawler_real_integration.py` | 真实请求报 `Event loop is closed`，结果全空 |
| P2-05 | 降级分支吞掉编程错误 | `app/agents/chat_agent.py` 原 L540 | 任何 `AttributeError` / `TypeError` 都被当成"LLM 不可用"静默降级，把真实缺陷伪装成正常回复 |

---

## 五、P0 详细分析

### P0-01 · SSE 订阅 403：侧会话被误判为越权

**现象**

主会话一切正常，进入侧会话后任何需要订阅事件流的操作都返回 403。

**根因**

会话归属校验只覆盖了主会话的归属关系，没有把"侧会话属于某个主会话"这条链路纳入判定。订阅时拿侧会话 ID 去查归属，查不到就直接判越权。

**修复**

补齐会话归属契约，让侧会话通过其所属主会话间接归属到用户；同时补充对应单元测试锁定该契约。

**为什么值得记**

这类缺陷在点测时极难发现——主会话全绿，只有走到侧会话分支才炸。而侧会话是本项目核心差异化功能（主任务与侧任务分离），一旦不可用等于砍掉一半产品价值。

---

### P0-02 · MCP Bridge 三个端点整体缺失

**现象**

浏览器扩展侧无法注册、拿不到指令流、也无法回传结果。整条"后端 ↔ 浏览器扩展"的桥接总线没有 HTTP 入口。

**根因**

服务层 `McpBridgeHub` 单例与核心方法已实现，但路由层从未注册。典型的"写了一半"：内存总线造好了，门口没开门。

**修复**

在 `app/api/routers/mcp_bridge.py` 实现三个端点并在 `app/main.py` 注册：

| 端点 | 作用 |
|---|---|
| `POST /register` | 扩展注册到桥接总线 |
| `GET /stream` | 扩展订阅指令 SSE 流 |
| `POST /result/{call_id}` | 扩展回传执行结果 |

**测试锁定**

`tests/e2e_real/test_05_mcp_bridge_publish.py` 覆盖：注册成功、SSE 流可建立、未知 `call_id` 返回 404（防止脏数据静默吞掉）。

---

### P0-03 · 平台发布适配器三个端点缺失

**现象**

发布只能走小红书，微博与公众号无对应接口。

**根因**

同 P0-02，能力层已有发布实现，路由层未暴露。

**修复**

在 `app/api/routers/workflows.py` 补三个端点：

| 端点 | 行号 |
|---|---|
| `POST /publish`（小红书） | L122 |
| `POST /publish/weibo` | L131 |
| `POST /publish/wechat_mp` | L140 |

**测试锁定**

覆盖端点鉴权、`automation_enabled` 总开关拦截（关掉后必须拒绝而不是静默跳过）、公众号标题必填校验。

---

### P0-04 · 12 个内容源请求全部失败：`trust_env` 读到了不存在的系统代理

这是本次测试**唯一一个会让所有搜索功能同时失效**的缺陷，也是最难定位的一个。

**现象**

`test_search_hackernews_returns_real_results` 断言失败——HackerNews 返回 0 条。但日志里没有任何 `HTTP 4xx/5xx`，只有一行：

```
EXC ConnectError: ''
```

异常消息是**空字符串**。继续排查发现：12 个源全部返回 0 条，全部同一个异常。

**根因**

`httpx.AsyncClient` 的 `trust_env` 默认为 `True`，会自动读取环境变量与系统代理设置。开发机上有 `http://127.0.0.1:7897` 的系统代理配置（Clash 一类），但代理进程当时没运行。于是：

```
httpx.AsyncClient()  →  trust_env=True  →  读系统代理 127.0.0.1:7897  →  连接被拒  →  ConnectError('')
```

而代码里对 `ConnectError` 的处理是"记录日志 + 返回空列表"，异常对象的字符串表示恰好为空，所以日志里什么都看不到。

**这为什么是设计问题而不只是环境问题**

1. 内容源里大多数服务（HackerNews、GitHub、DevTo、Reddit）**本来就不需要代理**，走直连最快；
2. 项目已经提供了 `get_proxy_url()` 显式代理配置入口，说明设计意图是"用户配了才走代理"；
3. 但 `trust_env=True` 让"用户没配代理"也走了代理——**配置语义被系统环境变量劫持了**。

**修复**

12 个源统一追加 `trust_env=False`，代理只从 `get_proxy_url()` 显式读取：

| 文件 | 行号 |
|---|---|
| `youtube_source.py` | L66 |
| `tavily_source.py` | L69 |
| `serper_source.py` | L70 |
| `reddit_source.py` | L70 |
| `github_source.py` | L69 |
| `devto_source.py` | L58 |
| `hackernews_source.py` | L102 |
| `deepseek_search_source.py` | L152 |
| `deepseek_api_source.py` | L173 |
| `sogou_source.py` | L184 |
| `hotboard_source.py` | L206 |
| `translator.py` | L127 |

**验证**

修复后单独探活：HackerNews 返回 10 条真实结果（含翻译后的中文标题），随后全量 E2E 13 项搜索用例全绿。

---

### P0-05 · IDOR 越权：会话与创作产物可跨用户读取

**现象**

构造另一个用户的会话 ID / 产物 ID，能读到不属于当前用户的数据。

**根因**

读取路由只按主键查，没有叠加 `user_id` 过滤条件。

**修复**

所有会话/产物读取路径加归属校验，越权请求返回 404（而非 403，避免通过状态码探测资源是否存在）。

**测试锁定**

E2E 中加了跨账号访问用例。该用例当前被 **skip**——需要第二个真实账号凭据，属凭据限制而非代码问题。

---

### P0-06 · `plugins.py` 双 BOM，后端整体无法启动

**现象**

最终回归前重启后端，进程在 import 阶段直接退出：

```
File "backend\app\main.py", line 29, in <module>
    from app.api.routers import accounts, agents, ..., plugins, ...
File "backend\app\api\routers\plugins.py", line 1
    """
    ^
SyntaxError: invalid non-printable character U+FEFF
```

`main.py` 只要 import 失败，整个 FastAPI 应用就构造不出来——**所有功能 100% 不可用**，且报错位置指向 `plugins.py` 第 1 行第 1 列，看起来像"文件第一行有问题"，实际第一行内容完好无损。

**根因**

首字节实测：

```
EF BB BF EF BB BF 22 22 22 0D 0A 50 6C 75 67 69 ...
└─────┬─────┘ └─────┬─────┘ └──┬──┘
   BOM #1        BOM #2       """
```

文件开头有**两个** UTF-8 BOM。

CPython 的源码读取器只剥掉**第一个** BOM（`utf-8-sig` 语义），第二个 `EF BB BF` 于是变成一个真实的 `U+FEFF` 字符留在源码里。`U+FEFF` 不是合法的源码字符，词法分析阶段直接抛 `SyntaxError`。

**为什么只有一个文件中招**

扫描 `app/` `capabilities/` `tests/` 全部 `.py` 首字节，双 BOM 仅 `plugins.py` 1 个。单个 BOM 的文件（如 `chat_agent.py`）是合法的，Python 会正常剥掉，所以历史上没暴露。

典型的"编辑器/工具二次写盘时把 BOM 又追加了一次"：原有文件带 BOM，某次写入又按带 BOM 模式保存，两份叠在一起。

**修复**

去重 BOM，统一按 UTF-8 无 BOM 写回（与仓库内绝大多数文件一致）：

```powershell
$text = [System.Text.Encoding]::UTF8.GetString($bytes)
$text = $text.TrimStart([char]0xFEFF)
[System.IO.File]::WriteAllText($path, $text, (New-Object System.Text.UTF8Encoding($false)))
```

验证：`compileall` 退出码 0，后端正常启动。

**为什么值得记**

1. **报错信息指向的"第 1 行第 1 列"是假线索**——第一行文本没问题，问题在字节层面。只看编辑器里的文件内容是查不出来的，必须看原始字节。
2. **BOM 是二进制层的东西，diff 看不见**。代码 review、`git diff` 都不会提示，CI 上才会炸，而 CI 的报错又指向无辜的第一行。
3. **`import` 链上的单点失败会放大成全局不可用**。`main.py` 集中 import 几十个 router，任何一个文件语法错都会让整个服务起不来。

**建议的预防措施**

- 仓库加 `.gitattributes`：`*.py text eol=lf`，避免跨平台写盘时引入 BOM。
- 提交前跑一次 `python -m compileall -q app capabilities`，比等启动时炸要快得多。

---

## 六、P1 详细分析

### P1-01 · `wechat_mp` 平台规格缺失

**现象**

公众号发布没有字段规格，标题为空也能提交到发布动作层。

**根因**

`platform_adapter.py` 的平台规格字典只覆盖了小红书与微博，`wechat_mp` 缺失，导致校验分支直接跳过。

**修复**

在 `capabilities/messaging/services/platform_adapter.py` L124 起补 `PlatformSpec(name="wechat_mp", ...)`，声明标题必填、正文长度限制等约束。

---

### P1-02 · `weibo` / `wechat_mp` 发布页自动化动作缺失

**现象**

这两个平台的发布请求能进到发布器，但找不到对应的页面动作函数，自动化流程走不下去。

**根因**

`platform_publisher.py` 的动作注册表里只有小红书一条，其余平台取不到 handler。

**修复**

- 补 `weibo` 发布页动作；
- 补 `_wechat_mp_page_action`（L521 起）：进入编辑器 → 填标题 → 填正文 → 上传封面 → 点保存，每一步失败单独告警不整体中断；
- 在动作注册表 L602 注册 `"wechat_mp": _wechat_mp_page_action`；
- 平台入口 URL 表 L47 加 `"wechat_mp": "https://mp.weixin.qq.com/"`。

**设计要点**

每个步骤都是 `try/except + warning` 而不是直接抛出。这是对的做法——页面结构随时会变，单步失败不应该让整个发布流程崩掉，但必须留下可定位的日志。

---

### P1-03 · 3 个源不读代理配置

**现象**

用户明明在配置里设了代理，`sogou` / `hotboard` / `translator` 依然不走代理。

**根因**

这三个客户端构造 `httpx.AsyncClient` 时没有调用 `get_proxy_url()`，而其余 9 个源都调了。**同一个项目里存在两种相反的代理语义**：

- 9 个源：读配置代理（但因为 `trust_env=True`，同时也被系统代理劫持）
- 3 个源：不读配置代理（但同样被系统代理劫持）

也就是说，P0-04 和 P1-03 是同一次"代理语义没统一"造成的两个相反方向的 bug。

**修复**

三个文件统一补 `proxy=get_proxy_url() or None` + `trust_env=False`，让 12 个源的代理语义完全一致：**只认显式配置，绝不读环境**。

---

### P1-04 · 详情页解析不输出 `from_card`

**现象**

单图笔记抓取后被下游当成"低质量兜底"丢弃，导致同步后作品库里少了这一类笔记。

**根因**

`xhs_spider.py` 的 `parse_note`（详情页解析）有两条产出路径，都没有输出 `from_card` 字段：

| 路径 | 行号 |
|---|---|
| `__INITIAL_STATE__` 提取成功分支 | L476 |
| CSS 选择器兜底分支 | L557 |

而下游 `db_sink.py` L76-82 依赖这个字段判断：

```python
# 只有主页卡片（from_card=True）带来的单张封面是"低质量兜底"，
# 详情页的 imageList 是完整图集（单图笔记本身就是一张），可信。
allow_single = not item.get("from_card") or ...
```

字段缺失时 `item.get("from_card")` 返回 `None`，`not None` 为 `True`——**恰好落到了"可信"分支**。所以这里是一个"碰巧没坏"的隐患：一旦有人把判断逻辑改成 `item.get("from_card", True)` 或做显式 `bool()` 判断，所有单图笔记会立刻被丢。

**修复**

两条路径都显式输出 `"from_card": False`，把"碰巧正确"变成"契约正确"。

**为什么值得记**

这类缺陷的可怕之处在于**当前表现是正常的**。它不是 bug，是"即将变成 bug 的代码"。测试的价值就在于把它逼出来。

---

## 七、P2 详细分析（测试可信度与降级语义）

这四个缺陷（P2-01 ~ P2-04）的共同点是：**它们让测试自己不可靠**。修产品 bug 之前必须先修它们，否则看到的失败大部分是假失败。P2-05 是修完这四个之后顺带暴露出来的产品侧问题——降级分支太宽，把编程错误也吞了。

### P2-01 · `asyncio.get_event_loop()` 在 Python 3.12 抛错

**现象**

`test_collab_e2e_chain.py` 与 `test_collab_crawler_e2e_chain.py` 的 20 项用例全部 ERROR，报：

```
RuntimeError: There is no current event loop in thread 'MainThread'.
```

**根因**

fixture 的 teardown 用同步写法调异步清理：

```python
asyncio.get_event_loop().run_until_complete(manager.shutdown())
```

Python 3.12 起，`get_event_loop()` 在没有运行中循环的主线程里不再自动创建循环，直接抛 `RuntimeError`。而 pytest-asyncio 每个用例结束后循环已经关闭。

**修复**

改成 async fixture，直接 `await manager.shutdown()`。这是 pytest-asyncio 的标准写法。

---

### P2-02 · 测试桩缺 `auto_approve` 属性

**现象**

`test_chat_agent_routes_to_agentic_loop_with_llm` 断言 `result.status == "agent_output"`，实际拿到 `"chat"`。

**根因**

`chat_agent.py` L297 从 session 读：

```python
input_data["auto_approve"] = bool(session.auto_approve)
```

测试桩 `_FakeHarnessWithRun` 没定义 `auto_approve`，`AttributeError` 被上层 `except` 捕获，**静默降级到正则分支**，于是返回 `chat` 而非 `agent_output`。

**修复**

测试桩补 `auto_approve = True`。

**顺带发现的设计问题（2026-11-06 已一并修复）**

原 `chat_agent.py` 的降级分支是 `except Exception`，把 `AttributeError` 也当成"agentic loop 失败"处理。这让任何编程错误（拼错属性名、缺字段）都会静默退化成"看起来能用"的 fallback 路径——**P2-02 这个测试缺陷本身，就是被这层降级掩盖成"用例通过"的**。

修复方式：把编程错误与运行时错误分开，只对运行时错误降级。

```python
# 编程错误白名单：这些异常说明代码本身写错了，而不是「LLM/网络不可用」
_PROGRAMMING_ERRORS = (AttributeError, TypeError, NameError, NotImplementedError)
```

```python
except _PROGRAMMING_ERRORS as e:
    # 编程错误必须直接抛出，不能降级掩盖
    logger.error(f"... raised programming error, NOT degrading: {type(e).__name__}: {e}", exc_info=True)
    raise
except Exception as e:
    # 运行时错误（LLM 超时 / 网络失败 / 第三方返回异常）才允许降级
    logger.error(f"... failed, falling back to intent route: {e}", exc_info=True)
    return await self._fallback_intent_route(message, context, None)
```

**异常向上抛之后去哪了（链路已确认安全）**

| 调用方 | 处理方式 |
|---|---|
| [chat_agent.py 路由 L950](file:///D:/My_Project/多智能体小红书发布平台/backend/app/api/routers/chat_agent.py#L950-L962) | 捕获后先 publish `workflow_error` SSE 事件（带 `detail`），再 `raise` → 前端能看到明确错误而不是"莫名其妙回了一句闲话" |
| `wechat_agent_bridge.py` L406 | 自己的 `except` 兜底，回一句"智能体处理出错"，不崩 |
| `feishu_agent_bridge.py` L595 | 同上 |

**契约测试锁定**

`tests/test_chat_agent.py` 新增 2 项：

- `test_chat_agent_propagates_programming_error_instead_of_degrading` —— `AttributeError` 必须抛出（`pytest.raises`）
- `test_chat_agent_still_degrades_on_runtime_error` —— `RuntimeError` 仍必须降级（保证用户不看到 500）

两条同时存在，防止后续有人"为了不报错"把白名单又改回 `except Exception`。

---

### P2-03 · 模块级污染 `DEEPSEEK_API_KEY`

**现象**

`test_crawler_real_integration.py` 里 5 项 DeepSeek 真实集成测试全部 401，报：

```
Authentication Fails, Your api key: ****back is invalid
```

**根因**

`tests/test_matting_live3b.py` 在**模块顶层**执行：

```python
os.environ["DEEPSEEK_API_KEY"] = "sk-invalid-forced-fallback"
```

pytest 在收集阶段会 import 所有测试模块，这一行在**任何用例运行之前**就把整个进程的 `DEEPSEEK_API_KEY` 改成了假 key（尾号 `back` 正是 `sk-invalid-forced-fallback` 的后四位）。于是同批次所有真实 DeepSeek 测试全部 401。

**修复**

把赋值移进 `main()` 内部。该文件本来就是"直接运行脚本"用途，不需要在 pytest 收集阶段改环境。

**为什么值得记**

这是典型的"测试之间互相投毒"。单个文件跑是绿的，全量跑就红——而且红得毫无道理（报 401 而不是"环境变量被改"）。这类问题最消耗排查时间。

---

### P2-04 · 内容源单例跨事件循环复用

**现象**

`test_trending_search_skill_real_call` 返回 `count: 0`，日志：

```
ERROR [hackernews] search_trending failed: Event loop is closed
```

**根因**

`source_manager` 是模块级单例，内部 `httpx.AsyncClient` 在**首次请求时绑定当时的事件循环**。pytest-asyncio 每个用例使用独立事件循环，于是第二个用例拿到的是绑在已关闭循环上的客户端，所有请求直接抛 `Event loop is closed`。

这是 P0-04 的"近亲"：都是 `AsyncClient` 生命周期没管好，一个在测试里跨循环，一个在运行时跨代理。

**修复**

在 `tests/test_crawler_real_integration.py` 加 autouse fixture，每个用例结束后 `await source_manager.close_all()`，强制下一个用例重建客户端。

---

### P2-05 · 降级分支吞掉编程错误

**现象**

修 P2-02（给测试桩补 `auto_approve`）时反查发现：`_agentic_loop` 的收尾是 `except Exception`，**任何**异常都会走 fallback。也就是说，即使不补属性，代码也不会报错——只会"看起来正常地"回了句闲话。

**根因**

```python
except Exception as e:
    logger.error(f"[ChatAgent] agentic loop failed, falling back to regex: {e}", exc_info=True)
    return await self._fallback_intent_route(message, context, None)
```

`except Exception` 把两类完全不同的失败混为一谈：

| 类别 | 典型异常 | 应不应该降级 |
|---|---|---|
| 运行时失败（LLM 超时、网络断、第三方返回异常） | `TimeoutError`、`ConnectError`、`RuntimeError` | ✅ 应该降级，用户不该看到 500 |
| 编程错误（属性名拼错、字段缺失、类型不匹配） | `AttributeError`、`TypeError`、`NameError`、`NotImplementedError` | ❌ 不该降级，会掩盖真实缺陷 |

**修复**

按异常类型分流，只对运行时错误降级：

```python
_PROGRAMMING_ERRORS = (AttributeError, TypeError, NameError, NotImplementedError)

except _PROGRAMMING_ERRORS as e:
    logger.error(f"... raised programming error, NOT degrading: {type(e).__name__}: {e}", exc_info=True)
    raise
except Exception as e:
    logger.error(f"... failed, falling back to intent route: {e}", exc_info=True)
    return await self._fallback_intent_route(message, context, None)
```

顺带把日志文案从 `falling back to regex` 改成 `falling back to intent route`——正则意图路由早已删除，旧文案会误导排查。

**异常抛出后不会崩（链路已逐级确认）**

| 调用方 | 处理 |
|---|---|
| [routers/chat_agent.py:L950](file:///D:/My_Project/多智能体小红书发布平台/backend/app/api/routers/chat_agent.py#L950-L962) | 先 publish `workflow_error` SSE 事件（带 `detail`），再 `raise` → 前端收到明确错误，而不是"莫名其妙回了一句闲话" |
| `wechat_agent_bridge.py` L406 | 自身 `except` 兜底，回复"智能体处理出错"，服务不崩 |
| `feishu_agent_bridge.py` L595 | 同上 |

**契约双向锁定**

`tests/test_chat_agent.py` 新增 2 项，成对存在：

- `test_chat_agent_propagates_programming_error_instead_of_degrading` —— `AttributeError` 必须抛出
- `test_chat_agent_still_degrades_on_runtime_error` —— `RuntimeError` 仍必须降级

只锁单向是不够的：有人为了"别报错"会把白名单改回 `except Exception`，第二条用例能立刻拦住这种回退。

**回归验证**

- `tests/test_chat_agent.py`：9 项全通过（含新增 2 项）
- 全量单元 + 契约套件：2242 项全通过，无新增失败

---

## 八、非项目缺陷（不计入清单）

| 项 | 说明 | 结论 |
|---|---|---|
| 跨账号 IDOR 用例 skip | 需要第二个真实账号凭据 | 凭据限制，非代码问题 |
| MyMemory 翻译 API 429 | 免费翻译接口限流 | 项目已有「60s 内跳过翻译、返回原文」降级，行为正确 |
| `ruff` 未安装 | `pyproject.toml` 声明了 ruff，虚拟环境未装 | 环境问题；已改用 `compileall` 全量语法校验，通过 |
| DeepSeek 余额/key 状态 | 本次 `.env` 中 key 有效，真实调用成功 | 无缺陷 |

---

## 九、修复路线图

### 第一批 · 止血（已完成）

1. **统一代理语义**（P0-04 + P1-03）——12 个源加 `trust_env=False`，3 个源补 `get_proxy_url()`。这是收益最高的一刀：一次修复解决"搜索全废"和"配了代理不生效"两个相反方向的 bug。
2. **补齐 6 个缺失端点**（P0-02 + P0-03）——MCP 桥接 3 个、发布适配器 3 个，并在 `main.py` 注册。
3. **补 IDOR 校验**（P0-05）——所有读取路径叠加 `user_id` 过滤。

### 第二批 · 契约收紧（已完成）

4. **补 `wechat_mp` 平台规格**（P1-01）
5. **补 `weibo` / `wechat_mp` 发布页动作**（P1-02）
6. **补 `from_card` 输出**（P1-04）——把"碰巧正确"变成"契约正确"

### 第三批 · 测试可信度（已完成）

7. **修 4 处测试基础设施**（P2-01 ~ P2-04）——先让测试说真话，再谈修 bug

### 第四批 · 降级语义收紧（已完成，2026-11-06）

8. **`chat_agent` 降级分支区分编程错误与运行时错误**（P2-02 顺带项）——`AttributeError` / `TypeError` / `NameError` / `NotImplementedError` 直接抛出，不再被 fallback 吞掉；配 2 项契约测试双向锁定（编程错误必须抛、运行时错误必须降级）。

### 第五批 · 启动阻断修复（已完成，2026-11-06）

9. **`plugins.py` 去重 BOM**（P0-06）——`main.py` import 阶段 SyntaxError，服务完全起不来。修完即恢复启动，E2E 全量回归复跑通过。

### 建议的下一批（本次未做，需要拍板）

| 建议 | 理由 |
|---|---|
| 给 `from_card` 加直接契约测试 | P1-04 是"碰巧正确"型隐患，现有用例只是间接覆盖，没有直接断言 `from_card is False`，重构一次就复发 |
| 代理配置加启动自检 | P0-04 的异常消息为空，排查成本极高；启动时探活一次能省下几小时的日志考古 |
| 加 `.gitattributes` + 提交前 `compileall` | P0-06 的 BOM 问题 diff 看不见、CI 才炸；两道低成本闸门就能拦住同类问题 |

> 已划掉一条：内容源 `close()` 纳入 lifespan —— 核查确认 [main.py:L428-431](file:///D:/My_Project/多智能体小红书发布平台/backend/app/main.py#L428-L431) 的 shutdown 阶段**已有** `await source_manager.close_all()`，此项无需再做。

---

## 附录 A · 复现命令

```powershell
# 工作目录：backend/

# 1. 启动后端（必须用虚拟环境解释器，系统 Python 的 uvicorn 版本过低）
.\.venv\Scripts\python.exe start_server.py

# 2. 等待就绪（项目启动含大量模型预热，实测约 6 分钟）
.\.venv\Scripts\python.exe -c "import httpx;print(httpx.get('http://127.0.0.1:8000/health',timeout=5,trust_env=False).status_code)"

# 3. 全量 E2E 真实 API 测试
.\.venv\Scripts\python.exe -m pytest tests/e2e_real -v -p no:cacheprovider

# 4. 单元 + 契约测试（排除 e2e）
.\.venv\Scripts\python.exe -m pytest tests --ignore=tests/e2e_real -q -p no:cacheprovider

# 5. 单独验证内容源（P0-04 回归）
.\.venv\Scripts\python.exe -m pytest tests/test_crawler_real_integration.py -q -p no:cacheprovider

# 6. 语法校验（ruff 未安装时的替代）
.\.venv\Scripts\python.exe -m compileall -q capabilities/ app/
```

**注意**：所有直接调用本地服务的探测脚本都必须带 `trust_env=False`，否则会重蹈 P0-04 的覆辙——用自己的系统代理把本地请求也拦掉。

---

## 附录 B · 本报告未覆盖的范围

| 范围 | 原因 |
|---|---|
| AI 图生视频 | 按需求明确排除 |
| 前端 E2E（浏览器真实交互） | 本报告只覆盖后端 API；前端静态分析见 `Web端问题分析报告.md` |
| 并发压测 / 长稳测试 | 本次为功能正确性验证，未做负载与稳定性测量 |
| Scrapling 真实发布（真发到小红书） | 会对外部平台产生真实副作用，只验证到"动作函数可达"层 |
| 微信机器人 / 飞书机器人 | 需要外部 IM 凭据与在线环境，未纳入本次自动化测试 |
| 跨账号 IDOR 实测 | 需第二真实账号凭据，用例已写好但 skip |

---

## 附录 C · 本次改动文件清单

| 类型 | 文件 | 改动 |
|---|---|---|
| 新增端点 | `app/api/routers/mcp_bridge.py` | 3 个桥接端点 |
| 新增端点 | `app/api/routers/workflows.py` | 3 个平台发布端点（L122 / L131 / L140） |
| 路由注册 | `app/main.py` | L651 注册 `mcp_bridge.router` |
| 平台规格 | `capabilities/messaging/services/platform_adapter.py` | L124 起补 `wechat_mp` |
| 发布动作 | `capabilities/messaging/services/platform_publisher.py` | L47 入口 URL、L521 `_wechat_mp_page_action`、L602 注册 |
| 数据契约 | `capabilities/scrapling/spiders/xhs_spider.py` | L476 / L557 补 `from_card: False` |
| 代理修复 | `capabilities/content_search/sources/*.py` | 12 个源 `trust_env=False`；3 个源补 `get_proxy_url()` |
| 测试修复 | `tests/test_collab_e2e_chain.py` | async fixture |
| 测试修复 | `tests/test_collab_crawler_e2e_chain.py` | async fixture + `asyncio.run` |
| 测试修复 | `tests/test_llm_drives_image_text_skill.py` | 测试桩补 `auto_approve` |
| 测试修复 | `tests/test_matting_live3b.py` | 环境变量赋值移入 `main()` |
| 测试修复 | `tests/test_crawler_real_integration.py` | autouse fixture 清理源单例 |
| 新增用例 | `tests/e2e_real/test_05_mcp_bridge_publish.py` | 11 项（10 通过 / 1 skip） |
| 降级语义 | `app/agents/chat_agent.py` | 新增 `_PROGRAMMING_ERRORS` 白名单；编程错误 `raise` 不降级，仅运行时错误走 fallback |
| 契约锁定 | `tests/test_chat_agent.py` | 新增 2 项：编程错误必须抛 / 运行时错误必须降级 |
| 编码修复 | `app/api/routers/plugins.py` | 去除重复 UTF-8 BOM，改按无 BOM 写回（否则 `main.py` import 直接 SyntaxError） |