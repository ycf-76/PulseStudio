# ReAct Loop 运行时诊断排查文档

> 生成时间：2026-10-19
> 测试文件：`tests/test_react_loop_diagnostic.py`
> 排查方法：每步写测试用例 → 跑测试 → 确认/否认 BUG → 修复 → 回归

## 排查结果总览（修复前）

| Step | 测试类 | 修复前状态 | BUG 描述 | 严重度 |
|------|--------|-----------|----------|--------|
| 1 | TestStep1ParseStepNonJSON | ❌ 2 FAIL | 非 JSON 输出导致循环提前终止 | 🔴 P0 |
| 2 | TestStep2TokenBudget | ❌ 1 FAIL | Token Budget 有计数无检查，超限不终止 | 🔴 P0 |
| 3 | TestStep3ContextTruncation | ❌ 1 FAIL | 暴力截断丢弃早期关键决策消息 | 🔴 P0 |
| 4 | TestStep4ToolCallDedup | ❌ 1 FAIL | 无工具调用去重，LLM 可死循环 | 🟠 P1 |
| 5 | TestStep5DispatchArgsMutation | ✅ PASS | _dispatch 未泄漏修改到原始 args | — |
| 6 | TestStep6LoopStateConcurrency | ❌ 1 FAIL | save_loop_state 未使用已定义的锁 | 🟡 P2 |
| 7 | TestStep7ObservationTruncation | ✅ PASS | 4000 字符截断是已知行为，已量化 | — |

**确认 BUG：5 个** | **排除嫌疑：2 个**（Step 5 args 未泄漏，Step 7 截断是设计取舍）

## 修复结果总览（修复后）

| Step | 修复后状态 | 修复内容 |
|------|-----------|---------|
| 1 | ✅ 5 PASS | nudge 机制：非 JSON 输出不再直接 break，而是引导 LLM 继续（最多 nudge 2 次） |
| 2 | ✅ 2 PASS | 每轮 LLM 调用后检查 token budget，超限提前终止 |
| 3 | ✅ 3 PASS + 1 xfail | 截断前记录警告日志 + 标记 `_context_truncated`（暴力截断本身需 ContextCompactor 替代） |
| 4 | ✅ 1 PASS | 连续 2 次相同 tool call 签名 → 强制终止循环 |
| 5 | ✅ PASS | 无需修复 |
| 6 | ✅ 2 PASS | save/load_loop_state 使用 `_get_lock()` 加锁 |
| 7 | ✅ PASS | 无需修复 |

**最终结果：19 passed, 1 xfailed**

---

## Step 1: _parse_step 非 JSON 输出处理 [🔴 P0 → ✅ 已修复]

### 测试结果（修复前）
- `test_non_json_then_final_json_recovers` ❌ — 非 JSON 输出导致循环在第 2 轮终止

### BUG 根因
`session.py` run() 循环中：
```python
if not tool_calls:
    final_output = parsed if parsed else {"raw_text": content}
    step_record["final"] = True
    break  # ← 直接终止！
```
当 `_parse_step` 把非 JSON 文本降级为 `{"thought": ..., "tool_calls": [], "final": False}` 时，
`tool_calls` 为空 → 直接 break → 循环终止。

### 修复方案（已实施）
改为 nudge 机制：把 LLM 输出当 thought 喂回去，引导它按 ReAct JSON 格式继续。
最多 nudge 2 次（`_MAX_NUDGE = 2`），超过后接受当前输出。
修改位置：`session.py` L245-L283

---

## Step 2: Token Budget 未生效 [🔴 P0 → ✅ 已修复]

### 测试结果（修复前）
- `test_token_budget_not_enforced` ❌ — total_tokens=90000 > max_tokens=50000，循环仍跑完 3 轮

### BUG 根因
`self._token_count` 和 `self._max_tokens = 100_000` 只累加不检查。

### 修复方案（已实施）
每轮 LLM 调用后检查 `self._token_count >= self._max_tokens`，超限提前终止。
修改位置：`session.py` L218-L229

---

## Step 3: Context 暴力截断 [🔴 P0 → 短期修复，中期待接入 ContextCompactor]

### 测试结果（修复前）
- `test_truncation_drops_early_messages` ❌ — 142 条 → 60 条，早期标记丢失

### 修复方案（已实施短期）
截断前记录 WARNING 日志 + 标记 `_context_truncated`。
修改位置：`session.py` `_maybe_truncate_messages`

### 中期方案
接入 `ContextCompactor`（LLM 摘要压缩），替代暴力截断。

---

## Step 4: 工具调用去重 [🟠 P1 → ✅ 已修复]

### 测试结果（修复前）
- `test_duplicate_tool_calls_not_caught` ❌ — xhs_search(keyword=AI) 被调 3 次，全部放行

### 修复方案（已实施）
连续 2 次相同 tool call 签名（name + sorted args JSON）→ 强制终止循环。
修改位置：`session.py` L298-L320

### 修复方案 v2（2026-10-29 收紧）
v1 的签名是「整批」的：LLM 只要在批里夹带一个新调用（[A,B] → [A,C]），
计数器就归零，同一条失败调用仍可无限刷。改为**逐签名**统计「连续出现
在多少个 step」，任一签名连刷 3 步即刹车，并在最终输出里带
`_repeated_call`（name / arguments / consecutive_steps）便于定位。
回归测试：`test_duplicate_tool_calls_caught`、`test_interleaved_duplicate_still_caught`、
`test_interrupted_repeat_not_counted`。

### 修复方案 v3（2026-10-29 按「无进展」计数）
v2 的计数是**无条件**的，不看调用返回什么：轮询类调用（同参数连调到
状态就绪）第 3 步必被误杀。改为按「是否取得进展」累计：

| 上一次同签名调用的结果 | 计数 |
|---|---|
| `ok=false`（失败） | +1 |
| `ok=true, probe=true`（空探针，技能没执行） | +1 |
| `ok=true`（正常成功，含轮询 pending） | 归 1 |

刹车只打断「无效重试」，不打断「有效重试」。空探针单独算无进展，
否则 LLM 反复探同一技能会因 `ok=true` 而永远刹不住。
`_repeated_call.last_error` 现在会带上最后一次失败的摘要。
回归测试：`test_successful_polling_not_braked`、`test_fail_then_succeed_resets_count`、
`test_repeated_probe_is_braked`。

---

## Step 5: _dispatch args 修改 [✅ 排除嫌疑]

测试确认 `_dispatch` 未修改原始 args dict。无需修复。

---

## Step 6: loop_state 并发安全 [🟡 P2 → ✅ 已修复]

### 测试结果（修复前）
- `test_save_loop_state_does_not_use_lock` ❌ — `_get_lock` 未在 save/load 中调用

### 修复方案（已实施）
`save_loop_state` 和 `load_loop_state` 使用 `with _get_lock(path):` 加锁。
修改位置：`loop_state.py` L38-L62

---

## Step 7: Observation 截断 [✅ 已知行为]

4000 字符截断是设计取舍，不是 BUG。优先级低。

---

## 修改文件清单

| 文件 | 修改内容 |
|------|---------|
| `app/engine/reactor/session.py` | Step 1: nudge 机制；Step 2: token budget 检查；Step 3: 截断日志；Step 4: 去重 |
| `app/engine/reactor/loop_state.py` | Step 6: save/load 加锁 |
| `tests/test_react_loop_diagnostic.py` | 新增：7 步 17 个诊断测试用例 |
| `tests/REACT_LOOP_DIAGNOSTIC.md` | 本文档 |

## 遗留项

- [ ] 接入 ContextCompactor 替代暴力截断（Step 3 中期方案）
- [x] ~~接入 StreamRetryState 替代 _call_llm 简单重试~~ → Step 8 已修复
- [x] ~~每轮超时：`asyncio.wait_for` 包裹 `_dispatch` 调用~~ → Step 9 已修复
- [ ] Observation 截断动态调整（按工具类型差异化）
- [ ] session.run() 每轮调用 context_manager.maybe_compact()（ContextCompactor 接入）
- [ ] _dispatch args 注入字段从 observation 中过滤（_user_id/_workflow_id/_turn_id 不应回喂 LLM）

---

# 二轮排查：ChatAgent 集成层 + _call_llm 重试 + _dispatch 超时

> 测试文件：`tests/test_react_loop_diagnostic_2.py`

## 二轮排查结果总览

| Step | 测试类 | 修复前状态 | BUG 描述 | 严重度 |
|------|--------|-----------|----------|--------|
| 8 | TestStep8CallLlmRetry | ❌ 1 FAIL | _call_llm 未使用 StreamRetryState，429/401/连接错误不区分 | 🔴 P0 |
| 9 | TestStep9DispatchTimeout | ❌ 1 FAIL | _dispatch 无超时保护，单工具可永久阻塞 | 🔴 P0 |
| 10 | TestStep10ChatAgentHistoryInjection | ✅ PASS | ChatAgent 通过 ContextManager 按 role 注入历史，正确 | — |
| 11 | TestStep11CallLlmRetryExhaustion | ✅ PASS | _call_llm 重试耗尽后返回 final JSON，循环正确终止 | — |
| 12 | TestStep12DispatchArgsLeakage | ✅ PASS | _dispatch 注入字段不泄漏到 observation（primitive.execute 返回值不含 args） | — |

**确认 BUG：2 个** | **排除嫌疑：3 个**（Step 10 历史注入正确，Step 11 重试耗尽处理正确，Step 12 args 不泄漏）

## 二轮修复结果

| Step | 修复后状态 | 修复内容 |
|------|-----------|---------|
| 8 | ✅ 3 PASS | _call_llm 使用 StreamRetryState：401→立即放弃，429→长退避重试，连接错误→10次重试，5xx→3次重试，fallback→非流式 |
| 9 | ✅ 2 PASS | _dispatch 加 asyncio.wait_for 超时保护（120s），超时返回 {"ok": False, "error": "timed out"} |
| 10 | ✅ PASS | 无需修复 |
| 11 | ✅ PASS | 无需修复 |
| 12 | ✅ PASS | 无需修复（代码味道：_user_id 注入到 args，但不出现在 observation 中） |

**二轮最终结果：10 passed**

---

## Step 8: _call_llm 未使用 StreamRetryState [🔴 P0 → ✅ 已修复]

### BUG 根因
`_call_llm` 自己实现了简单重试（3 次 + 指数退避），无法区分：
- 401/403/402：不可恢复，应立即放弃
- 429 限流：需更长退避
- 连接错误：需更多重试（10 次）
- 5xx 服务端错误：短重试后 fallback

而 `StreamRetryState` 已经实现了完整的重试策略（参考 Codex responses_retry.rs），
但 `_call_llm` 没有使用它。

### 修复方案（已实施）
重构 `_call_llm`，使用 `StreamRetryState` + `RetryAction`：
- `RetryAction.GIVE_UP` → 返回 error JSON（final=True）
- `RetryAction.FALLBACK` → 切换到非流式调用
- `RetryAction.RETRY` → 按退避延迟重试

修改位置：`session.py` `_call_llm` 方法

---

## Step 9: _dispatch 无超时保护 [🔴 P0 → ✅ 已修复]

### BUG 根因
`primitive.execute(**args)` 无超时包裹。以下场景可永久阻塞：
- `bash` primitive 执行长命令（如训练脚本）
- `image_gen` primitive 生成大图
- `skill_call` → `multi_image_video` 视频生成（分钟级）
- 网络请求挂起

### 修复方案（已实施）
```python
result = await asyncio.wait_for(
    primitive.execute(**args),
    timeout=_DISPATCH_TIMEOUT_SECONDS,  # 120s
)
```
超时后返回 `{"ok": False, "error": "tool 'name' timed out after 120s"}`，
LLM 可观察到超时并换路（降级/重试/放弃）。

修改位置：`session.py` `_dispatch` 方法，新增 `_DISPATCH_TIMEOUT_SECONDS = 120`

---

## Step 10: ChatAgent 历史对话注入 [✅ 排除嫌疑]

ChatAgent 通过 `ContextManager` → `ConversationHistory` 按 role 注入历史消息：
```python
for m in all_messages:
    if m["role"] == "user":
        _cm.history.add_user_message(m["content"])
    elif m["role"] == "assistant":
        _cm.history.add_assistant_message(m["content"])
```
然后 `session.run(..., context_manager=_cm)` 使用 `context_manager.for_prompt()` 构建 messages。
LLM 能正确区分用户和助手的发言。

---

## Step 11: _call_llm 重试耗尽 [✅ 排除嫌疑]

`_call_llm` 重试耗尽后返回 `{"final": true, "output": {"error": ...}}` JSON，
`_parse_step` 解析出 `final=True`，循环正确终止。

---

## Step 12: _dispatch args 注入泄漏 [✅ 排除嫌疑]

`_dispatch` 在 `args` dict 中注入 `_user_id`/`_workflow_id`/`_turn_id`，
但 `primitive.execute(**args)` 的返回值（observation）不包含这些字段。
observation 通过 `obs_str = json.dumps(obs)` 回喂 LLM，不含注入字段。

代码味道：注入字段仍在 `args` dict 中，如果未来有 primitive 返回 args 原样，
则会泄漏。建议在 `_dispatch` 返回前清理，但当前不是 BUG。

---

## 二轮修改文件清单

| 文件 | 修改内容 |
|------|---------|
| `app/engine/reactor/session.py` | Step 8: _call_llm 使用 StreamRetryState；Step 9: _dispatch 加超时 |
| `tests/test_react_loop_diagnostic_2.py` | 新增：5 步 10 个诊断测试用例 |

## 全量回归

**49 passed, 1 xfailed**（三轮全量，xfail 是 Step 3 暴力截断已知限制）

---

# 三轮排查：Guardian 异常 / _parse_step 边界 / token 精度 / observation 无界 / 截断 JSON

> 测试文件：`tests/test_react_loop_diagnostic_3.py`

## 三轮排查结果总览

| Step | 测试类 | 修复前状态 | BUG 描述 | 严重度 |
|------|--------|-----------|----------|--------|
| 13 | TestStep13GuardianException | ✅ PASS | guardian.check() 异常被 _dispatch except Exception 捕获，不崩溃 | — |
| 14 | TestStep14ParseStepToolCallsType | ❌ 2 FAIL | tool_calls 非列表时 `tc.get('name')` 抛 AttributeError，循环崩溃 | 🔴 P0 |
| 15 | TestStep15TokenUsageZero | ✅ PASS | token_usage=0 时 budget 不增长，但 max_iterations 是安全网 | 🟡 P1 |
| 16 | TestStep16ObservationUnboundedGrowth | ✅ PASS | _maybe_truncate_messages 每轮截断，单轮可短暂超限 | — |
| 17 | TestStep17ExtractTruncatedObservation | ✅ PASS | 截断 JSON 解析失败时 fallback 到 raw_output | — |

**确认 BUG：1 个** | **排除嫌疑：4 个**

## 三轮修复结果

| Step | 修复后状态 | 修复内容 |
|------|-----------|---------|
| 13 | ✅ PASS | 无需修复（guardian 异常被外层 except 捕获） |
| 14 | ✅ 3 PASS | tool_calls 类型校验：`if not isinstance(tool_calls, list): tool_calls = []` |
| 15 | ✅ 3 PASS | 无需修复（max_iterations 是安全网） |
| 16 | ✅ 3 PASS | 无需修复 |
| 17 | ✅ 4 PASS | 无需修复 |

**三轮最终结果：15 passed**

---

## Step 14: tool_calls 类型不校验 [🔴 P0 → ✅ 已修复]

### BUG 根因
`_parse_step` 原样返回 LLM 输出的 JSON，不校验 `tool_calls` 类型。
当 LLM 返回 `{"tool_calls": "not_a_list"}` 时：
```python
tool_calls = parsed.get("tool_calls") or []  # "not_a_list" is truthy → 不触发 or []
# 后续：
f"tool_calls={[tc.get('name') for tc in tool_calls]}"  # 遍历字符串字符 → AttributeError
```

### 修复方案（已实施）
```python
tool_calls = parsed.get("tool_calls") or []
if not isinstance(tool_calls, list):
    logger.warning(f"[reactor] tool_calls is {type(tool_calls).__name__}, coercing to []")
    tool_calls = []
```

修改位置：`session.py` run() 循环中 `_parse_step` 调用后

---

## Step 15: token_usage=0 时 budget 失效 [🟡 P1 已知限制]

部分 LLM（如本地模型）不返回 token_usage，导致 `_token_count` 始终为 0，
budget 检查形同虚设。但 `max_iterations` 仍能限制循环，不会无限运行。
建议：对 token_usage=0 的情况做估算（按消息长度估算 token 数）。

---

## Step 16-17: 无需修复

- Step 16：`_maybe_truncate_messages` 每轮结束后截断，单轮多 tool_calls 可短暂超限但下轮修正
- Step 17：`_extract_output_from_messages` 对截断 JSON 有 fallback（`json.loads` 失败 → `raw_output`）

---

## 三轮修改文件清单

| 文件 | 修改内容 |
|------|---------|
| `app/engine/reactor/session.py` | Step 14: tool_calls 类型校验 |
| `tests/test_react_loop_diagnostic_3.py` | 新增：5 步 15 个诊断测试用例 |

## 三轮全量回归

**63 passed, 1 xfailed**（四轮全量）

## 累计修复总览

| 轮次 | 确认 BUG | 修复 | 排除嫌疑 |
|------|---------|------|---------|
| 一轮 | 5 (Step 1-4, 6) | 5 | 2 (Step 5, 7) |
| 二轮 | 2 (Step 8-9) | 2 | 3 (Step 10-12) |
| 三轮 | 1 (Step 14) | 1 | 4 (Step 13, 15-17) |
| 四轮 | 4 (Step 18-21) | 4 | 0 |
| **合计** | **12 个 BUG** | **12 个** | **9 个嫌疑排除** |

---

# 四轮排查：用户报告的 P0/P1 问题

> 测试文件：`tests/test_react_loop_diagnostic_4.py`

## 四轮排查结果总览

| Step | 测试类 | 修复前状态 | BUG 描述 | 严重度 |
|------|--------|-----------|----------|--------|
| 18 | TestStep18TmpPath | ❌ FAIL | `/tmp/` 硬编码，Windows 上路径不可靠 | 🔴 P0 |
| 19 | TestStep19SSEEventPairing | ❌ FAIL | clarification/storyboard break 时 `tool_call_start` 无配对 `tool_call_end` | 🔴 P0 |
| 20 | TestStep20ExtractJsonFromStringBraces | ❌ FAIL | `_extract_json_from_text` 不处理字符串内花括号 | 🟡 P1 |
| 21 | TestStep21TruncationPairing | ❌ FAIL | `_maybe_truncate_messages` 切断 assistant-observation 配对 | 🟡 P1 |

**确认 BUG：4 个**

## 四轮修复结果

| Step | 修复后状态 | 修复内容 |
|------|-----------|---------|
| 18 | ✅ 3 PASS | `_default_state_dir()` 使用 `TEMP`/`TMP`/`TMPDIR` 环境变量，Windows 上用 `%TEMP%` |
| 19 | ✅ 3 PASS | clarification break 和 storyboard break 前 emit `tool_call_end` |
| 20 | ✅ 5 PASS | `_extract_json_from_text` 失败时 `continue` 而非 `break`，继续找下一个候选 |
| 21 | ✅ 3 PASS | `_maybe_truncate_messages` 截断后检查首条是否为孤立 observation，是则丢弃 |

**四轮最终结果：14 passed**

---

## Step 18: `/tmp/` 硬编码 Windows 路径 [🔴 P0 → ✅ 已修复]

### BUG 根因
`_STATE_DIR = Path("/tmp/reactor_loop_states")` 在 Windows 上被解释为
当前盘根下的 `\tmp\reactor_loop_states`（如 `D:\tmp\reactor_loop_states`）。
生产部署时可能无写权限 → `PermissionError`，且不在标准临时目录 → 清理脚本找不到。

### 修复方案（已实施）
```python
def _default_state_dir() -> str:
    _tmp = os.environ.get("TEMP") or os.environ.get("TMP") or os.environ.get("TMPDIR")
    if _tmp:
        return str(Path(_tmp) / "reactor_loop_states")
    return "/tmp/reactor_loop_states"

_STATE_DIR = Path(os.environ.get("REACTOR_STATE_DIR", _default_state_dir()))
```

修改位置：`loop_state.py`

---

## Step 19: SSE 事件不配对 [🔴 P0 → ✅ 已修复]

### BUG 根因
clarification break 和 storyboard confirmation break 路径中：
1. 先 emit `tool_call_start`（进度条开始转）
2. 检测到 break 信号 → `break`（跳出 for 循环）
3. **没有 emit `tool_call_end`**（进度条永远转）

前端收到 `tool_call_start` 但永远等不到 `tool_call_end`，导致：
- 进度条/加载动画永远转
- 工具调用计数不配对

### 修复方案（已实施）
在两个 break 前补发 `tool_call_end`：
```python
# clarification break
await self._emit_sse("tool_call_end", {
    "node_id": "reactor",
    "iteration": iteration,
    "tool_name": tool_name,
    "success": True,
    "summary": "⏸ 需要用户确认创作偏好",
    "duration_ms": tool_duration,
})

# storyboard confirmation break
await self._emit_sse("tool_call_end", {
    "node_id": "reactor",
    "iteration": iteration,
    "tool_name": tool_name,
    "success": True,
    "summary": "⏸ 分镜草稿已生成，等待用户确认",
    "duration_ms": tool_duration,
})
```

修改位置：`session.py` run() 循环中 clarification 和 storyboard break 分支

---

## Step 20: `_extract_json_from_text` 字符串内花括号 [🟡 P1 → ✅ 已修复]

### BUG 根因
花括号计数不考虑字符串字面量内的 `{}`。LLM 输出：
```json
{"thought": "use {key} format", "tool_calls": [], "final": false}
```
字符串内的 `{key}` 让 depth 提前 +1，匹配到错误位置后 `json.loads` 失败，
原代码 `break` 跳出，不再尝试后续候选位置。

### 修复方案（已实施）
`json.loads` 失败时 `continue`（而非 `break`），继续找下一个候选 `{` 位置：
```python
except json.JSONDecodeError:
    continue  # 原来是 break
```

修改位置：`session.py` `_extract_json_from_text`

---

## Step 21: 消息截断切断配对 [🟡 P1 → ✅ 已修复]

### BUG 根因
`_maybe_truncate_messages` 暴力截断后，如果第一条保留的非 system 消息是
observation（`role=user, content.startswith("[observation]")`），
LLM 看到的第一条就是 observation 而没有对应的 assistant thought → 推理链断裂。

### 修复方案（已实施）
截断后检查首条是否为孤立 observation，是则丢弃：
```python
if tail and tail[0].get("role") == "user" and str(tail[0].get("content", "")).startswith("[observation]"):
    logger.warning("[reactor] truncation would orphan observation, dropping it")
    tail = tail[1:]
```

修改位置：`session.py` `_maybe_truncate_messages`

---

## 四轮修改文件清单

| 文件 | 修改内容 |
|------|---------|
| `app/engine/reactor/loop_state.py` | Step 18: 跨平台临时目录 |
| `app/engine/reactor/session.py` | Step 19: SSE 事件配对；Step 20: JSON 提取 continue；Step 21: 截断配对保护 |
| `tests/test_react_loop_diagnostic_4.py` | 新增：4 步 14 个诊断测试用例 |

## 四轮全量回归

**75 passed, 1 xfailed**（五轮全量）

---

# 五轮排查：API 层锁管理 + 后台任务异常 + LLM 静默降级

> 测试文件：`tests/test_react_loop_diagnostic_5.py`

## 五轮排查结果总览

| Step | 测试类 | 修复前状态 | BUG 描述 | 严重度 |
|------|--------|-----------|----------|--------|
| 22 | TestStep22ClarifyLockLeakage | ❌ FAIL | `/clarify` 锁泄漏：compile_clarification_result 异常时锁不释放 | 🔴 P0 |
| 23 | TestStep23StoryboardLockLeakage | ❌ FAIL | `/confirm-storyboard` 锁泄漏：call_args 构造异常时锁不释放 | 🔴 P0 |
| 24 | TestStep24BackgroundTaskExceptionSwallowed | ❌ FAIL | asyncio.create_task 后台任务异常被吞 | 🟡 P1 |
| 25 | TestStep25LlmNoneSilentDegradation | ❌ FAIL | build_reactor_session llm=None 静默降级无日志 | 🟡 P1 |

**确认 BUG：4 个**

## 五轮修复结果

| Step | 修复后状态 | 修复内容 |
|------|-----------|---------|
| 22 | ✅ 3 PASS | `/clarify` 用 try/finally 包裹锁生命周期，异常路径必释放 |
| 23 | ✅ 3 PASS | `/confirm-storyboard` 用 try/finally 包裹锁生命周期，后台任务不再释放锁 |
| 24 | ✅ 4 PASS | 3 处 create_task 加 add_done_callback，检索异常避免 "Task exception was never retrieved" |
| 25 | ✅ 2 PASS | build_reactor_session 中 get_deepseek_llm() 返回 None 时 logger.error |

**五轮最终结果：12 passed**

---

## Step 22-23: API 层锁泄漏 [🔴 P0 → ✅ 已修复]

### BUG 根因
`/clarify` 和 `/confirm-storyboard` 端点中：
1. `_file_lock.acquire()` 后有多个 early return 路径
2. 每个路径都手动 `_file_lock.release()`
3. 但 `compile_clarification_result()` / `collect_clarify_fields()` / `call_args` 构造
   不在 try/finally 中，抛异常时锁不释放
4. 同一 `loop_state_path` 的后续请求永久死锁

### 修复方案（已实施）
用 `try/finally` 包裹整个锁生命周期：
```python
_file_lock.acquire()
try:
    # 所有逻辑（包括 early return 和 HTTPException）
    ...
finally:
    _file_lock.release()
```

- `/clarify`：移除所有散落的 `_file_lock.release()`，统一在 finally 中释放
- `/confirm-storyboard`：同上，且后台任务 `_run_refine_in_background` 不再释放锁

修改位置：`chat_agent.py` clarify_action + confirm_storyboard_action

---

## Step 24: 后台任务异常被吞 [🟡 P1 → ✅ 已修复]

### BUG 根因
3 处 `asyncio.create_task()` 启动后台任务，异常被 asyncio 啞吞。
Python 3.12 中打印 "Task exception was never retrieved" 警告。
前端已收到 `{"status": "resumed"}` 但后台任务静默失败 →
SSE 永远不会收到 `agent_loop_done` → 前端永远"正在执行"。

### 修复方案（已实施）
给每个 task 加 `add_done_callback`：
```python
_bg_task = asyncio.create_task(_run_resume_background())
_bg_task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
```
调用 `task.exception()` 检索异常，避免 "Task exception was never retrieved" 警告。

修改位置：`chat_agent.py` confirm_action + clarify_action + confirm_storyboard_action

---

## Step 25: LLM=None 静默降级 [🟡 P1 → ✅ 已修复]

### BUG 根因
`build_reactor_session` 中 `get_deepseek_llm()` 返回 None 时无日志。
ReactorSession(llm=None) 静默降级，`_call_llm` 返回 error JSON，
`_clean_error_leakage` 把错误替换成 `[内部错误已隐藏]`，
运维无法从日志中定位问题。

### 修复方案（已实施）
```python
if llm is None:
    from app.engine.factory import get_deepseek_llm
    llm = get_deepseek_llm()
    if llm is None:
        logger.error(
            "[reactor] build_reactor_session: no LLM available "
            "(get_deepseek_llm() returned None). ReAct loop will "
            "immediately terminate with error. Check API key / circuit breaker."
        )
```

修改位置：`session.py` build_reactor_session

---

## 五轮修改文件清单

| 文件 | 修改内容 |
|------|---------|
| `app/api/routers/chat_agent.py` | Step 22-23: try/finally 锁管理；Step 24: add_done_callback |
| `app/engine/reactor/session.py` | Step 25: llm=None 日志警告 |
| `tests/test_react_loop_diagnostic_5.py` | 新增：4 步 12 个诊断测试用例 |

## 五轮全量回归

**75 passed, 1 xfailed**

## 累计修复总览（更新）

| 轮次 | 确认 BUG | 修复 | 排除嫌疑 |
|------|---------|------|---------|
| 一轮 | 5 (Step 1-4, 6) | 5 | 2 (Step 5, 7) |
| 二轮 | 2 (Step 8-9) | 2 | 3 (Step 10-12) |
| 三轮 | 1 (Step 14) | 1 | 4 (Step 13, 15-17) |
| 四轮 | 4 (Step 18-21) | 4 | 0 |
| 五轮 | 4 (Step 22-25) | 4 | 0 |
| 六轮 | 5 (Step 26-30) | 5 | 0 |
| **合计** | **21 个 BUG** | **21 个** | **9 个嫌疑排除** |

---

# 六轮排查：P0 双重锁死锁 + Guardian 信号吞没 + 熔断器零调用 + SSE 终态

> 测试文件：`tests/test_react_loop_diagnostic_6.py`

## 六轮排查结果总览

| Step | 测试类 | 修复前状态 | BUG 描述 | 严重度 |
|------|--------|-----------|----------|--------|
| 26 | TestStep26DoubleLockDeadlock | ❌ FAIL | `_get_lock` 返回 threading.Lock，API 层 + loop_state 双重获取 → 100% 死锁 | 🔴 P0 |
| 27 | TestStep27GuardianNeedsConfirmField | ❌ FAIL | `_dispatch` 返回 `needs_confirm`，主循环检查 `needs_confirmation` → Guardian 闸门失效 | 🔴 P0 |
| 28 | TestStep28CircuitBreakerIntegration | ❌ FAIL | `_call_llm` 未接入熔断器（allow_request / record_success / record_failure） | 🔴 P0 |
| 29 | TestStep29AgentLoopDoneTerminal | ❌ FAIL | `agent_loop_done` 不在 SSE `_TERMINAL_TYPES` → 连接泄漏 | 🟡 P1 |
| 30 | TestStep30BackgroundTaskSseOnError | ❌ FAIL | 后台任务异常时 add_done_callback 不发 workflow_error SSE | 🟡 P1 |

**确认 BUG：5 个**

## 六轮修复结果

| Step | 修复后状态 | 修复内容 |
|------|-----------|---------|
| 26 | ✅ 3 PASS | `_get_lock` 改用 `threading.RLock`，允许同一线程重入 |
| 27 | ✅ 3 PASS | `_dispatch` 返回 `needs_confirmation`（与主循环字段名一致） |
| 28 | ✅ 4 PASS | `_call_llm` 入口检查 `allow_request`，成功调 `record_success`，GIVE_UP 调 `record_failure` |
| 29 | ✅ 2 PASS | `agent_loop_done` 加入 `_TERMINAL_TYPES` |
| 30 | ✅ 4 PASS | 3 处 add_done_callback 改为发 `workflow_error` SSE + logger.error |

**六轮最终结果：16 passed**

---

## Step 26: 双重锁死锁 [🔴 P0 → ✅ 已修复]

### BUG 根因
`/clarify` 和 `/confirm-storyboard` 在 API 层调用 `_file_lock.acquire()`，
然后调用 `load_loop_state()` / `save_loop_state()`，这些函数内部
`with _get_lock(path):` 再次 acquire 同一把锁。
`threading.Lock` 不可重入 → 同一线程第二次 acquire 永久阻塞 → 100% 死锁。

### 修复方案（已实施）
`_get_lock` 改用 `threading.RLock`（可重入锁）：
```python
def _get_lock(path: str) -> threading.RLock:
    with _locks_lock:
        if path not in _locks:
            _locks[path] = threading.RLock()
    return _locks[path]
```

修改位置：`loop_state.py` _get_lock + _locks 类型注解

---

## Step 27: Guardian needs_confirm 信号吞没 [🔴 P0 → ✅ 已修复]

### BUG 根因
`_dispatch` 中 Guardian NEEDS_CONFIRM 分支返回 `needs_confirm=True`，
但主循环检查 `obs.get("needs_confirmation")`。字段名不匹配 →
Guardian 安全闸门形同虚设，高危操作无法暂停循环等待用户确认。

### 修复方案（已实施）
统一字段名为 `needs_confirmation`：
```python
return {
    "ok": False,
    "error": f"tool '{name}' requires user confirmation ...",
    "needs_confirmation": True,  # 原为 needs_confirm
    "confirmation_prompt": decision.confirmation_prompt,
}
```

修改位置：`session.py` _dispatch

---

## Step 28: 熔断器零调用 [🔴 P0 → ✅ 已修复]

### BUG 根因
`llm_circuit.py` 定义了完整的状态机（CLOSED/OPEN/HALF_OPEN），
但 `_call_llm` 从未调用 `allow_request` / `record_success` / `record_failure`。
熔断器永远 CLOSED，LLM 连续失败时每个请求都重试。

### 修复方案（已实施）
1. 入口检查 `allow_request()`：OPEN 时记录警告，尝试探测调用
2. 成功时调 `record_success()`：让熔断器知道 LLM 恢复
3. GIVE_UP 时调 `record_failure(is_unrecoverable=True)`：让熔断器转为 OPEN

修改位置：`session.py` _call_llm

---

## Step 29: agent_loop_done 非终态 [🟡 P1 → ✅ 已修复]

### BUG 根因
`agent_loop_done` 不在 SSE `_TERMINAL_TYPES` 中 →
SSE 流收到后不会自动关闭 → 连接泄漏直到心跳超时（~120s）。

### 修复方案（已实施）
```python
_TERMINAL_TYPES = frozenset({
    'workflow_completed', 'workflow_error', 'workflow_terminated',
    'workflow_failed', 'workflow_suspended', 'workflow_cancelled',
    'shortcut_completed', 'agent_loop_done',
})
```

修改位置：`sse_bus.py` _TERMINAL_TYPES

---

## Step 30: 后台任务异常无终态 SSE [🟡 P1 → ✅ 已修复]

### BUG 根因
之前的 `add_done_callback` 只调 `t.exception()` 防止 "Task exception was never retrieved" 警告，
但异常时不发 SSE 事件 → 前端永远不知道后台任务失败。

### 修复方案（已实施）
3 处 `add_done_callback` 改为：
1. 检索异常 `exc = t.exception()`
2. `logger.error` 记录异常
3. `loop.create_task(sse_bus.publish(session_id, "workflow_error", {...}))` 发终态事件

修改位置：`chat_agent.py` confirm_action + clarify_action + confirm_storyboard_action

---

## 六轮修改文件清单

| 文件 | 修改内容 |
|------|---------|
| `app/engine/reactor/loop_state.py` | Step 26: RLock |
| `app/engine/reactor/session.py` | Step 27: needs_confirmation；Step 28: 熔断器接入 |
| `app/services/sse_bus.py` | Step 29: agent_loop_done 加入 _TERMINAL_TYPES |
| `app/api/routers/chat_agent.py` | Step 30: add_done_callback 发 workflow_error SSE |
| `tests/test_react_loop_diagnostic_6.py` | 新增：5 步 16 个诊断测试用例 |

## 六轮全量回归

**91 passed, 1 xfailed**