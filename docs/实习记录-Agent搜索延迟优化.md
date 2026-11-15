# 实习记录：Agent 搜索延迟从 70 秒降到 10 秒

> 记录一次真实性能排查：用户反馈"Agent 搜索信息太慢"，从现象复现到根因定位，再到修复与验证的完整过程。

---

## 一、问题现象

用户反馈：让 Agent 搜一条选题素材，要等 70 秒左右才有结果，体验上等于"卡住了"。

一开始的直觉判断是"网络慢"或"源太多"，于是先做了一轮常规优化：提高并发数、收紧超时、把封面抓取移出主链路、把翻译改成结果截断后再做。这一轮确实有效，耗时从 70 秒降到了 12~16 秒。

但**卡在这个区间就再也压不下去了**。继续调参数、加并发，收益趋近于零。这说明瓶颈不在"调度策略"，而在某个更深的地方——必须实测，不能再猜。

---

## 二、怎么定位：抓主线程栈

做法很简单：在多平台搜索运行期间，每 3 秒采一次主线程的调用栈，看它到底卡在哪一行。

结果非常一致，反复命中同一帧：

```
deepseek_search_source.py:149 in _ensure_client
httpx/_client.py:1445 in _init_transport
httpx/_config.py:40 in create_ssl_context
ssl.py:708 in create_default_context      ← 事件循环就卡在这里
```

结论直接摆出来了：**程序不是在等网络，是在等"加载证书"。**

---

## 三、为什么"加载证书"这么慢

### 1. HTTPS 必须验身份

访问 `https://api.tavily.com` 时，数据是加密的。但光加密不够——你怎么知道对面真的是 Tavily，而不是冒充它的中间人？靠的就是**证书**：网站出示一张由权威机构签发的"数字身份证"，你验证这张证是真的，才建立加密通道。

### 2. 证书是"身份证"，那谁来验证身份证

由"发证机构"（CA，证书颁发机构）验证。但发证机构也有很多家，你又怎么知道哪家可信？答案是：操作系统里预装了一份**受信任的根证书清单**，相当于一份"官方认可的发证机构白名单"。Windows 上通常有几百个条目。

### 3. 所以"加载证书"就是在读这份白名单

程序要验证对方证书，就必须先拿到这份白名单，然后拿对方的证书逐级往上追查：这张证是哪个机构签的，那个机构是不是在白名单里。而 Windows 的这份清单存在注册表里，分散在多个存储区（ROOT、CA 等），**读取时要把几百个条目逐条枚举、解析、装进内存对象**——这就是那 3~11 秒的来源。

### 4. 为什么慢得这么离谱

不是每次"访问一个网站"都慢，而是**每次新建 HTTP 客户端对象都慢**。

`httpx.AsyncClient(verify=True)` 在构造时会调用 `ssl.create_default_context()`，而这个函数每次都从头读一遍系统清单，**不做任何缓存**。

关键在于：这个调用是**同步阻塞**的，而它被执行在 asyncio 的**事件循环线程**上。事件循环是所有协程共享的那一条"主干道"——一个源在建 client，其余所有源都得在主干道上排队干等。

24 个源各建一个客户端，就等于把这份几百条的清单读 24 遍，串行排在同一个线程上，累计阻塞 60 秒以上。**这就是 70 秒延迟的真正来源。**

### 5. 一个对照

浏览器为什么不慢？因为浏览器**启动时读一次，之后一直复用**。

我们做的改动，本质就是把浏览器的做法搬过来。

---

## 四、解决方案

### 核心动作：全局只加载一次

在 `backend/capabilities/content_search/sources/base.py` 里新增两个工具：

```python
_SSL_CONTEXT: ssl.SSLContext | None = None
_SSL_LOCK = threading.Lock()

async def get_ssl_context() -> ssl.SSLContext:
    global _SSL_CONTEXT
    if _SSL_CONTEXT is not None:
        return _SSL_CONTEXT          # 有就直接给，不再读清单
    def _build() -> ssl.SSLContext:
        with _SSL_LOCK:
            if _SSL_CONTEXT is None:
                _SSL_CONTEXT = ssl.create_default_context()   # 只在第一次真正读
            return _SSL_CONTEXT
    return await asyncio.to_thread(_build)

async def create_async_client(**kwargs: Any) -> httpx.AsyncClient:
    kwargs.setdefault("verify", await get_ssl_context())
    return httpx.AsyncClient(**kwargs)
```

两个设计要点：

1. **缓存复用**（核心）：24 次加载 → 1 次，省下 60 秒。这是解决延迟的主要手段，属于"别重复做同一件事"。
2. **`asyncio.to_thread`**（辅助）：第一次加载仍然要花 3~11 秒，如果直接在主线程跑，程序首次搜索还是会卡一下。挪到后台线程后，这几秒在"旁边"发生，事件循环可以继续处理其他请求。

> 注意：这里**没有**开线程池去做并行搜索。搜索依然是 asyncio 单线程协程并发。`to_thread` 只是借了一个临时线程干一次性的阻塞活儿（加载证书），干完就还回去。
>
> 为什么必须借这个线程？因为 `ssl.create_default_context()` 是同步阻塞函数，在 asyncio 里直接调用会卡住整个事件循环——而事件循环正是所有 24 个源共享的那条主干道。问题本质是**一个阻塞调用混进了异步流程，堵住了所有人的路**，修复就是把它移出主干道（挪到线程）＋ 让它只发生一次（缓存）。

用 `threading.Lock` 而不是 `asyncio.Lock`，是为了避免锁对象跨事件循环绑定——测试里会新建事件循环，用 `asyncio.Lock` 会直接报错。

### 落地范围

`create_async_client` 替换了 13 处 `httpx.AsyncClient` 创建，覆盖全部内容源：

| 分类 | 涉及源 |
|---|---|
| 主搜索源 | Tavily 主源、Tavily 站内源 |
| LLM 搜索 | DeepSeek 搜索、DeepSeek API |
| 社区源 | Hacker News、GitHub、Dev.to、Reddit、YouTube、Medium 等 |
| 搜索/热榜 | 搜狗搜索、热榜源 |
| 处理层 | 翻译器 |

同时覆盖了搜索链路下游同样受影响的抓取路径，否则"抓网页正文"还会再吃一次这个开销：

- `app/services/web_page_fetcher.py` 的 `_get()`（含代理失败改直连的第二次建 client）
- `app/skills/search/bilibili_search.py`
- `app/main.py` 启动时预热一次，让**首次搜索**也不用等

### 配套的其他优化

根因消除后，又做了几层配套优化，把尾部延迟进一步压下去：

| 优化点 | 做法 | 效果 |
|---|---|---|
| 并发分组限流 | 共享同一 API Key 的内容源按组独立限流，避免同配额自相残杀触发 429 重试 | 消除重试放大 |
| Tavily 原生 `include_domains` | 9 个站内源原本各发 1 次请求（共 9 次），合并为 1 次请求 | 9 次 → 1 次 |
| 搜索链路裁剪 | 封面抓取、翻译移出搜索主链路，只对最终保留的结果做处理 | 尾部延迟大幅下降 |
| 单源超时保护 | 所有搜索路径统一 10~12s 单源上限 | 慢源不再拖垮整条链路 |
| 延迟翻译开关 | 多平台搜索期间关闭源内翻译，结果截断后统一翻译最终 N 条 | 翻译量从 24×N 降到 N |
| 翻译去重 | 相同标题/摘要批量翻译前先去重 | 减少重复请求 |

---

## 五、效果验证

| 场景 | 优化前 | 优化后 |
|---|---|---|
| 24 源并发（裸源层） | 67.8s | **8.1s** |
| 多平台综合搜索「教育」 | 70s | **10.92s** |
| 多平台综合搜索「AI agent」 | 70s | **10.01s** |
| 结果数量 / 平台覆盖 | 20 条 / 24 平台 | 20 条 / 24 平台（无回退） |

优化后没有源再被整体预算砍掉（之前会 cancel 1~3 个源），24 个平台全部参与。剩余尾延迟来自 DeepSeek LLM 搜索本身（约 7.7s），属于上游固有耗时，不是我们可控的部分。

测试验证：

- 端到端链路测试 + HTTP 契约测试：92 项全过
- 搜索/抓取相关 8 个测试文件：160 项全过
- `ruff --select F,E9`：无新增问题
- 应用启动导入正常

---

## 六、一个必须澄清的点

很多人（包括我自己最初）会误解成"缓存了证书就不验证身份了，安全性被削弱了"。这是错的。

两个动作必须分清：

| 动作 | 干什么 | 原来 | 现在 |
|---|---|---|---|
| **加载白名单** | 把系统里几百个可信机构读进内存 | 每源 1 次（24 次） | 只 1 次，共享 |
| **验证对方身份** | 拿对方证书去比对白名单 | 每源 1 次 | **还是每源 1 次** |

打个比方：你要查 24 个人的身份证真假。

- **白名单** = 那本"官方认可的发证机关名录"
- **验证** = 拿每个人的身份证，去名录里核对签发机关

原来的做法：**每查一个人，就重新去档案室搬一次那本厚名录**（搬 24 次，60 秒）
现在的做法：**名录搬一次放桌上，24 个人排队来核对**（搬 1 次，8 秒）

**24 个人的身份证，一张都没少核对。** 省掉的只是"反复搬名录"，不是"核对"本身。

准确的说法是：原来每个源都要**重新加载一遍受信任机构名录**才能开始验证；现在名录只加载一次、全局共享，**验证照旧逐源进行**，只是不用重复搬那本厚册子了。

技术上也验证过：`get_ssl_context()` 返回的 `SSLContext` 创建后不再修改，多个 httpx client 并发复用它做 TLS 握手是安全的（Python 文档明确 SSLContext 创建后线程安全）。它 `check_hostname=True` / `CERT_REQUIRED`，与 httpx 默认行为完全一致，**证书校验强度没有降低**。

---

## 七、这次排查沉淀下来的经验

1. **不要靠猜优化。** 第一轮调参把 70s 压到 12s 是有效的，但之后就失效了。这时候继续调参就是浪费——必须实测。抓调用栈是最廉价、最直接的实测手段。

2. **异步代码里的同步阻塞调用是隐形杀手。** 它不报错、不告警，只是悄悄把所有人的路堵住。判断标准很简单：这个函数会不会做磁盘 IO、网络 IO、系统调用？会的话，在协程里直接调用就要警惕。

3. **"慢"和"卡"是两回事。** 用户感知的是"慢"，但真实原因是"所有人排队等一个同步操作"。区分这两者，决定了你是去优化网络请求，还是去修阻塞点。

4. **性能优化要区分"重复"和"必要"。** 加载白名单是必要的（要验证身份），重复加载 24 次是不必要的。找准"重复"的部分，收益往往远大于微调参数。

5. **优化不能动安全边界。** 缓存的是只读的信任清单，不是校验逻辑本身。这类"看起来像降低安全"的优化，必须在文档里写清楚为什么安全，否则后面维护的人会怀疑。

---

## 八、涉及文件

| 文件 | 改动 |
|---|---|
| `backend/capabilities/content_search/sources/base.py` | 新增全局共享 SSL 上下文、`get_ssl_context()`、`create_async_client()` |
| `backend/capabilities/content_search/sources/tavily_source.py` | 标记并发组与限流；收紧 429 重试 |
| `backend/capabilities/content_search/sources/tavily_site_source.py` | 改用原生 `include_domains`，9 次请求合并为 1 次 |
| `backend/capabilities/content_search/sources/translator.py` | 并发提升、超时收紧、延迟翻译开关、批量去重 |
| `backend/capabilities/content_search/sources/hackernews_source.py` | 封面抓取预算从 20s 收紧到 5s |
| `backend/capabilities/content_search/skills/trending_search.py` | 并发调度器三层限流、延迟翻译、单源/整体超时保护 |
| `backend/app/services/web_page_fetcher.py` | 复用全局 SSL 上下文 |
| `backend/app/skills/search/bilibili_search.py` | 复用全局 SSL 上下文 |
| `backend/app/main.py` | 启动阶段预热 SSL 上下文 |
| 其余 9 个内容源 | `httpx.AsyncClient` → `create_async_client` |