# Task 5 审查：场景本地预览静态服务（studio_preview）

- 审查对象：提交 `efce25c` `feat(studio): 场景本地预览静态服务`，两个新文件
  - `backend/capabilities/video/services/studio_preview.py`（60 行）
  - `backend/tests/test_studio_preview.py`（10 行）
- 已核对磁盘文件与 review-package diff 一致；测试代码与任务书 Step 1 逐字相同；未重跑测试（按指示）。

## 一、规格符合性逐条核对

| 约束 | 实现 | 结论 |
| --- | --- | --- |
| 仅标准库 http.server/functools/threading | `http.server` + `functools` + `threading`，另用 `pathlib`（标准库，仅做目录校验，不引入依赖） | ✅ |
| bind 127.0.0.1 | `ThreadingHTTPServer(("127.0.0.1", self._port), handler)` | ✅ |
| stop() 幂等（未 start 调用不炸） | `if self._httpd is None: return`；未 start / 双重 stop / stop 后再 stop 均直接返回 | ✅ |
| daemon_threads=True | 显式赋值（`ThreadingHTTPServer` 类默认已是 True，冗余但满足硬约束可验证性，实现者自审已声明） | ✅ |
| port=0 从 server_address 读回实际端口 | `self._httpd.server_address[1]`；返回 `http://127.0.0.1:<port>/` 带尾部斜杠 | ✅ |
| 缺 scene 键 / 目录不存在 → ValueError 中文信息 | 两条中文 ValueError 均在 `__init__` fail-fast（任务书未指明放 init 还是 start，语义等价） | ✅ |
| 平台层禁止风格常量 | 模块内无任何颜色/字体/动效常量 | ✅ |
| 接口签名 | `__init__(ws, port=0)` / `start()->str` / `stop()->None` / `url_for(rel)->str` 与任务书一致；测试逐字采用 Step 1 | ✅ |
| 播放循环属场景代码职责 | 服务只做静态托管，docstring 声明边界，无越界代码 | ✅ |

## 二、审查重点分析

**1. url_for 拼接语义** — 正确。
`self._base` 无尾部斜杠，返回 `f"{base}/{rel.lstrip('/')}"`：
- `rel="assets/x.png"` → `http://127.0.0.1:<port>/assets/x.png` ✅
- `rel="/assets/x.png"` → `lstrip('/')` 归一为同上 ✅
- 边界：`rel=""` → 返回根路径（等价 `/`，serve index.html），合理；多前导斜杠同样归一。与测试断言 `startswith(url.rstrip("/"))` 自洽。

**2. 线程生命周期 / 端口释放（测试未覆盖，风险自评）** — 风险低。
stop() 采用规范模式：`shutdown()` 阻塞至 serve_forever 循环真正退出（内部事件同步，join 前线程已近终点）→ `server_close()` 关闭监听 socket（端口随即释放；`HTTPServer.allow_reuse_address=1` 进一步兜底重绑定）→ `join(timeout=5)` 兜底。且 `ThreadingHTTPServer` 的 ThreadingMixIn 在 `block_on_close` 默认开启下于 `server_close()` 内 join 在飞请求线程，stop 返回后不会残留半截响应。主用法是 port=0（系统临时端口），即使存在假想的慢释放也不会与后续 bind 冲突。测试未断言端口释放属可接受缺口，建议后续补一条「stop 后同固定端口可再次 bind」断言即可闭环。

**3. 任意文件读取面** — directory 限定生效，无发现。
`functools.partial(SimpleHTTPRequestHandler, directory=...)` 将 `self.directory` 固定为 scene 目录；`translate_path` 对请求路径做 normpath 并丢弃 `..` 等越界成分（含 Windows 下 `%5C` 反斜杠编码），无法逃出 directory 树。仅支持 GET/HEAD 只读；bind 127.0.0.1 仅本机可达；scene 目录为流水线生成内容。残余理论面（目录内符号链接、本机其他进程访问端口）在本场景不构成风险。

## 三、发现分级

**Critical：0 条**

**Important：0 条**

**Minor：4 条**
1. **url_for 缺前置守卫**：`start()` 之前或 `stop()` 之后调用，`self._base` 为 None，静默返回 `"None/<rel>"` 这样的无效 URL 字符串而非抛错。正常调用序（start→url_for→stop）不受影响，任务书也未规定该行为，属潜在脚枪。建议一行守卫：`if self._base is None: raise RuntimeError("预览服务未启动")`。
2. **测试覆盖缺口（非违规）**：幂等性（未 start 即 stop / 双重 stop）、两条 ValueError 路径、端口释放均未测。任务书 Step 1 只规定单测且实现者逐字照做，不构成规格偏离；实现按检视是规范模式，实际风险低（见上文线程生命周期分析）。
3. **重复 start() 会泄漏**：未 stop 连续两次 start()，第一个 server 的引用被覆盖，线程与监听 socket 悬挂。任务书未要求防护，内部 spike 工具可接受，留意 Task 6 调用方只 start 一次即可。
4. **每请求 stderr 访问日志**：`BaseHTTPRequestHandler.log_message` 默认逐请求写 stderr，长驻预览时可能产生噪音。不影响功能，后续可用静默 log_message 子类收掉。

**观察（不计发现）**：显式 `daemon_threads = True` 与类默认重复，属为满足硬约束可验证性的有意冗余，实现者自审已声明，保留合理。

## 四、裁决

- **规格符合性：✅**（全局约束与任务书接口逐条满足，测试逐字采用，无实质偏离）
- **代码质量：通过**（0 Critical / 0 Important / 4 Minor，均为边界防御与测试补强建议，不阻塞合并）
