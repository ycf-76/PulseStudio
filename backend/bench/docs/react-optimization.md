# ReAct 范式幻觉抑制优化方案

> 版本: v1.0 | 日期: 2026-10-23 | 模型: deepseek-chat

---

## 1. 问题定义

ReAct 范式下 LLM 作为控制器调度工具调用，存在两类幻觉：

| 类型 | 名称 | 行为模式 | 根因 |
|------|------|----------|------|
| **Type-1** | 假调用 (fake_final) | LLM 声称已调用工具完成，实际零工具零产物 | LLM 格式遵从失败（nudge 耗尽）、跳步、重复调用放弃 |
| **Type-2** | 脑补产物 (fabricated_artifact) | LLM 调了错误工具，却在 output 中声明正确工具的产物 key | LLM 对工具产出语义理解不足，用文本补全惯性脑补 |

**核心矛盾**：软语义强化（prompt 约束）容易被 LLM 忽略，必须在代码层面添加不可绕过的硬校验。

---

## 2. 解决方案：双 Gate 架构

```
┌─────────────────────────────────────────────────────────────┐
│                    ReAct Loop                               │
│                                                             │
│  LLM → thought → tool_calls → observation                   │
│         ↑                        │                          │
│         └── nudge ←─────────────┘                          │
│                                                             │
│  每次工具执行后：                                             │
│    → Receipt Ledger 自动建账（不可伪造回执）                    │
│    → _observed_artifacts 累积产物字段                         │
└─────────────┬───────────────────────────────┬───────────────┘
              │                               │
              ▼                               ▼
┌─────────────────────────┐   ┌───────────────────────────────┐
│   Output Gate           │   │   Execution Gate              │
│   (Type-2 拦截)         │   │   (Type-1 拦截)               │
│                         │   │                               │
│ 1. 提取 ok=True 回执    │   │ 条件：非暂停出口               │
│    背书的产物 key 集合   │   │   + receipt_ledger 为空       │
│ 2. 删除 final_output    │   │   + observed_artifacts 为空   │
│    中无回执背书的        │   │   + 非 pure_qa（thought 含   │
│    产物 key             │   │     "不需要工具"等信号时放行） │
│ 3. 补全 ledger 有但     │   │                               │
│    output 漏掉的产物    │   │ 动作：                        │
│                         │   │  _fake_final = True           │
│ 输出：_gate_removed 列表│   │  _execution_gate 诊断信息     │
│                         │   │  ended_reason → fake_final    │
│                         │   │ （资源截断类不改写）           │
└─────────────────────────┘   └───────────────────────────────┘
              │                               │
              └───────────┬───────────────────┘
                          ▼
              ┌───────────────────────┐
              │  _format_final_output │
              │  白名单过滤内部 key    │
              │  → 前端渲染           │
              └───────────────────────┘
```

### 2.1 Receipt Ledger（回执账本）

**位置**：`session.py` `__init__` 初始化 + 工具执行循环内

每次工具执行后自动记录一条不可伪造的回执：

```python
self._receipt_ledger.append({
    "call_id": f"call_{iteration}_{tool_index}",
    "tool": tool_name,
    "ok": obs_ok,                          # 工具是否执行成功
    "artifacts": {                          # 成功时提取的产物 key-value
        "card_draft": {...},
        "title": "...",
        "pages": 3,
    },
    "iter": iteration,
})
```

**设计原则**：
- 回执由系统侧自动生成，LLM 无法伪造（不经过 LLM 手）
- `ok=False` 的工具执行也记录，但 artifacts 为空，不背书任何产物
- 产物 key 列表与 `_PRODUCT_ARTIFACT_KEYS` 对齐

### 2.2 Output Gate（产物校验门）

**位置**：`session.py` ReAct 循环结束后、`_format_final_output` 之前

**触发条件**：非暂停出口（`not _paused_reason(ended_reason)`）

**三步逻辑**：

| 步骤 | 操作 | 目的 |
|------|------|------|
| 1. 提取背书 | 遍历 ledger，收集所有 `ok=True` 的 receipt 背书的产物 key | 建立可信产物集合 |
| 2. 删除无背书 | 遍历 `final_output`，删除在 `_PRODUCT_ARTIFACT_KEYS` 中但不在背书集合中的 key | 拦截 Type-2 幻觉 |
| 3. 补全遗漏 | 从 `_observed_artifacts` 注入 ledger 背书但 output 漏掉的产物 | 防止 LLM 搬运丢失 |

**产物 key 白名单**：
```python
_PRODUCT_ARTIFACT_KEYS = frozenset({
    "card_draft", "asset_ids", "file_path",
    "html_path", "image_url", "title", "pages", "deck",
})
```

**输出**：`_gate_removed` 列表（被删除的无背书 key，供诊断）

### 2.3 Execution Gate（执行校验门）

**位置**：Output Gate 之后、`_format_final_output` 之前

**触发条件**：非暂停出口 + `receipt_ledger` 为空 + `observed_artifacts` 为空 + **非 pure_qa**

**pure_qa 检测**：当 LLM 的 thought 中包含"不需要调用工具""纯问答""闲聊"等信号词时，判定为纯问答场景，直接放行不触发 nudge。这避免了把正确行为（纯问答零工具终止）误判为幻觉。

**含义**：LLM 从未真正执行任何工作就终止了（format nudge 失败、duplicate call 放弃等），且不是纯问答场景

**动作**：

| 字段 | 值 | 说明 |
|------|-----|------|
| `_fake_final` | `True` | 标记假完成 |
| `_execution_gate` | `{"trigger": ..., "receipt_count": 0, "artifact_count": 0}` | 诊断信息 |
| `ended_reason` | `"fake_final"` | 改写终止原因（资源截断类不改写） |

**不改写规则**：`MAX_ITERATIONS` / `TOKEN_BUDGET` 属于资源截断而非幻觉，保留原 `ended_reason`。

### 2.4 纯问答放行（pure_qa bypass）

> 2026-10 修复：fake_final nudge 的假阳性问题

**问题**：fake_final nudge 不区分场景，把纯问答的零工具终止（正确行为）误判为幻觉，强制 LLM 无意义地调用工具。

**根因**：prompt 中"调了总比不调安全"指令 + nudge 不区分场景 + nudge 措辞过于强硬，三重叠加把 LLM 推向"无脑调工具"。

**修复**：
1. **session.py**：fake_final nudge 条件增加 `not _is_pure_qa`，通过 thought 信号词检测纯问答意图
2. **prompt.py**：删除"调了总比不调安全"，改为"先判断用户是否要求产出内容，是→调工具，否→直接 final 并在 thought 说明理由"

### 2.5 Format Nudge 纯问答放行

> 2026-10 修复：format nudge 路径的假阳性问题

**问题**：纯问答场景下 LLM 输出自然语言文本（非 ReAct JSON 格式），format nudge 将其视为"格式错误"并强制推入 ReAct JSON 格式，而 ReAct JSON 格式天然倾向 tool_calls，导致纯问答被强制调工具。

**因果链**：
```
用户："你好" → LLM 输出自然语言 → format nudge 触发 → nudge 推向 JSON → LLM 调工具（错误）
```

**修复**：
1. **自然语言直出**：如果 LLM 输出 >20 字符的自然语言文本，且用户消息无创作信号且无操作信号，直接接受为 final output
2. **format nudge 给三类出口**：第 1 次 nudge 消息改为引导式，给 LLM 三个出口（纯问答→final:true / 传输操作→调对应工具 / 创作→调创作工具）
3. **安全阀**：已执行过工具（`_any_tool_executed=True`）后不触发自然语言直出，避免工具失败后被误放行

### 2.6 三类任务区分

> 2026-10 修复：传输/操作类任务被误判为创作类，导致"发到我邮箱"先做卡片再发邮件

**问题**：prompt 只区分"创作类"和"纯问答"两类，漏了第三类——传输/操作类（发邮件、下载、分享等）。LLM 把"发到我邮箱"当成创作类任务，先调 compose_card_deck 再调 email_send。

**修复**：prompt 将任务分为三类，判断优先级从高到低：

| 类型 | 示例 | 工具需求 |
|------|------|----------|
| 1. 纯问答/闲聊 | "你好""介绍一下自己" | 无，直接 final |
| 2. 传输/操作类 | "发到我邮箱""下载""分享" | 调对应工具，**不需要先创作** |
| 3. 创作类 | "做卡片""写文案""生成封面" | 必须调创作工具 |

session.py 的自然语言直出逻辑同步增加操作信号检测（`_operation_signals`），避免"发到我邮箱"被当成纯问答放行。

---

## 3. 评测指标

### 3.1 架构保证 vs 评测结果

> **关键区分**：双 Gate 的拦截是**代码级确定性保证**（非概率级），但 0% 是对当前 37 种子测试集的评测结果，不是对所有可能输入的绝对承诺。

| 层面 | 性质 | 说明 |
|------|------|------|
| **架构保证** | 确定性 | Output Gate：无 ok=True 回执 → 产物 key **必定**被删除（`del final_output[key]`，不经过 LLM）。Execution Gate：零回执零产物 → **必定**标记 `_fake_final`。这是数学确定性的，与 prompt 约束有本质区别 |
| **评测结果** | 统计性 | 0/37 的 95% 置信区间上界 ≈ 8.1%（Rule of Three：3/n）。即有 95% 把握说真实幻觉率 < 8.1%，但不能断言对所有输入永远为零 |
| **未覆盖边界** | 已知 | Gate 校验产物 key 存在性，不校验 value 质量；不拦截自然语言中的幻觉声明（如"已保存到 X 路径"）；不覆盖 Type-1/Type-2 之外的新幻觉模式 |

### 3.2 基线 vs 修复对比

| 指标 | 基线 (baseline_v2) | 修复 (execution_gate) | HSC |
|------|-------------------|----------------------|-----|
| **总体幻觉率** | 2.7% (1/37) | **0.0%** (0/37) | **1.00** |
| 95% 置信区间 | ±5.2% | 上界 ≈ 8.1% | — |
| Type-1 假调用 | 0 | 0 | 1.00 |
| Type-2 脑补产物 | 1 | **0** | **1.00** |
| 假阳性率 | 0.0% | 0.0% | — |
| 幻觉时平均浪费轮数 | 4.0 | 0.0 | — |
| 首次工具调用平均轮次 | 1.0 | 1.0 | — |

### 3.3 按触发条件分解

| 触发条件 | 基线率 | 修复率 | HSC |
|----------|--------|--------|-----|
| creation_with_asset_style_confirmed | 25.0% | 0.0% | 1.00 |
| 其余 13 个触发条件 | 0.0% | 0.0% | 1.00 |

**基线中唯一的幻觉**：种子 H2-01（`creation_with_asset_style_confirmed`），用户输入"把刚才的访谈做成3页金句卡"，LLM 调了 4 次工具但 `observed_artifacts` 为空，却在 output 中声明了 `pages` key。

### 3.4 HSC（幻觉抑制系数）判定标准

| HSC 范围 | 含义 |
|----------|------|
| < 0 | 系统在放大幻觉 |
| = 0 | 无影响 |
| 0–0.3 | 弱抑制 |
| 0.3–0.5 | 中等抑制 |
| 0.5–0.8 | 较强抑制 |
| 0.8–1.0 | 强抑制 |
| **= 1.0** | **完全抑制** |

---

## 4. 评测方法

### 4.1 种子集

- 文件：`backend/bench/hallucination_seeds.jsonl`
- 总数：37 个种子（28 high_risk + 9 normal）
- 多轮续跑种子：9 个（`multi_turn_*` 前缀）

种子结构：
```json
{
  "seed_id": "H2-01",
  "category": "high_risk",
  "trigger": "creation_with_asset_style_confirmed",
  "hallucination_type": "fake_final",
  "user_message": "把刚才的访谈做成3页金句卡",
  "context": {
    "has_assets": true,
    "asset_ids": ["mock_a1"],
    "style_confirmed": true,
    "creation_type": "card"
  },
  "expected_tools": ["compose_card_deck"],
  "notes": "澄清完成+创作指令，最典型的假final场景"
}
```

### 4.2 触发条件覆盖

| 触发条件 | 种子数 | 风险等级 |
|----------|--------|----------|
| ambiguous_request | 3 | high_risk |
| creation_no_asset | 3 | high_risk |
| creation_with_asset_no_style | 4 | high_risk |
| creation_with_asset_style_confirmed | 4 | high_risk |
| full_creation_flow | 2 | high_risk |
| multi_step_chain | 3 | high_risk |
| multi_turn_after_search | 2 | high_risk |
| multi_turn_creation | 3 | high_risk |
| multi_turn_style_confirm | 2 | high_risk |
| multi_turn_then_create | 2 | high_risk |
| normal_creation | 2 | normal |
| normal_search | 2 | normal |
| pure_qa | 3 | normal |
| text_task_fabricate_card | 2 | high_risk |

### 4.3 评测脚本

| 脚本 | 用途 |
|------|------|
| `hallucination_bench.py` | 全量评测，`--full` 跑 37 种子，`--version-label` 标记版本 |
| `compare_reports.py` | A/B 对比，计算 HSC |
| `validate_bench.py` | dry-run 验证种子格式 |
| `test_output_gate.py` | 单元测试（12 个用例，覆盖双 Gate 全场景） |

---

## 5. 代码改动清单

| 文件 | 改动 | 行号 |
|------|------|------|
| `backend/app/engine/protocol/events.py` | `EndedReason` 枚举新增 `FAKE_FINAL = "fake_final"` | L38 |
| `backend/app/engine/reactor/session.py` | 初始化 `_receipt_ledger` | L161 |
| `backend/app/engine/reactor/session.py` | 工具执行后 Receipt Ledger 建账 | L720-738 |
| `backend/app/engine/reactor/session.py` | Output Gate 三步逻辑 | L762-795 |
| `backend/app/engine/reactor/session.py` | Execution Gate 零执行检测 | L797-825 |
| `backend/app/engine/reactor/session.py` | `_format_final_output` 白名单扩展 | L920-925 |
| `frontend/.../agent-output-formatter.ts` | `INTERNAL_KEYS` 加入 `_gate_removed`, `_execution_gate` | L82 |
| `backend/bench/test_output_gate.py` | 12 个单元测试（7 Output Gate + 5 Execution Gate） | 全文件 |

---

## 6. 向后兼容性

| 维度 | 影响 |
|------|------|
| 工具接口 | **零改动** —— 回执由 session 层自动从 observation 提取，不要求工具返回格式变更 |
| 现有流程 | **零影响** —— 正常完成的 session 路径不受双 Gate 影响 |
| 前端渲染 | **零影响** —— 诊断 key 通过白名单过滤，不渲染到用户界面 |
| API 响应 | **新增字段** —— `ended_reason` 新增 `fake_final` 枚举值，`_gate_removed` / `_execution_gate` 为可选诊断字段 |

---

## 7. 后续扩展方向

> 以下模块为预留扩展点，当前未实现，按需追加。

### 7.1 Call Sequence Gate（调用序列校验）

**目标**：校验工具调用顺序是否符合预期依赖关系

**场景**：LLM 先调 `render_html` 再调 `compose_card_deck`（顺序反了），当前双 Gate 不拦截

**方案**：
- 定义工具依赖图（DAG）：`compose_card_deck → render_html → save_asset`
- 在 Receipt Ledger 建账时检查调用顺序是否满足拓扑序
- 违反依赖序的 receipt 标记 `ok=False`，产物不背书

**涉及文件**：`session.py` Receipt Ledger 建账处、新增 `tool_dependency.py`

### 7.2 Argument Integrity Gate（参数完整性校验）

**目标**：校验工具调用参数是否满足必填要求

**场景**：LLM 调 `compose_card_deck` 但缺少 `_user_id`，工具返回错误，LLM 脑补参数重试

**方案**：
- 从工具 schema 提取 `required` 字段
- 调用前校验必填参数是否齐全
- 缺参时直接拦截，不走 LLM 重试

**涉及文件**：`session.py` 工具调用前、工具 schema 定义文件

### 7.3 Cross-Turn Consistency Gate（跨轮一致性校验）

**目标**：校验多轮对话中 LLM 的声明是否与历史回执一致

**场景**：第 1 轮 LLM 说"已保存到 A 路径"，第 2 轮用户问"刚才保存到哪了"，LLM 回答"B 路径"

**方案**：
- Receipt Ledger 持久化到 loop_state
- 续跑时加载历史 ledger
- 新轮次中 LLM 声明的路径/ID 与历史 ledger 比对

**涉及文件**：`session.py`、`loop_state.py`

### 7.4 Adaptive Nudge Strategy（自适应 Nudge 策略）

**目标**：根据 nudge 失败模式动态调整 nudge 内容和次数

**场景**：H1-01 中 format nudge 2 次失败后直接放弃，可以换更强的格式约束

**方案**：
- 记录 nudge 失败模式（format_error / duplicate_call / fake_final）
- 根据模式选择不同 nudge 模板
- 允许关键场景（creation_with_asset_style_confirmed）nudge 3-4 次

**涉及文件**：`session.py` nudge 逻辑、新增 `nudge_strategies.py`

### 7.5 Prompt 模板模块化（已完成 2026-10-24）

**目标**：避免功能增长时继续堆砌提示词，改为 Codex 式模板文件架构

**现状**：
- `prompt.py` 中 ~280 行巨型字符串 `REACTOR_SYSTEM_PROMPT_TEMPLATE` 已拆为 4 个独立 `.md` 文件
- `session.py` 中硬编码信号元组 `_creation_signals` / `_operation_signals` 已替换为 `TaskClassifier`

**架构**：
```
templates/
├── base.md        ← 身份 + 工具 + 工作方式 + 输出格式 + 重要规则
├── domain_xhs.md  ← 小红书领域知识（可替换为其他领域）
├── discipline.md  ← 澄清纪律 + 幻觉禁止 + 三类任务区分
└── skills.md      ← 技能目录（{skill_catalog} 占位符）
```

**新增文件**：
- `task_classifier.py`：`TaskClassifier` + `SkillManifest` + `DEFAULT_MANIFESTS`
- `templates/*.md`：4 个模板文件

**效果**：
- 加新领域知识 = 加新 `.md` 文件 + 一行 append
- 加新技能 = 加 `SkillManifest` 条目（或技能自带 `task_type` + `trigger_words` 属性）
- session.py / prompt.py 零修改

**后续（第 3 步）**：当技能数量 >20 时，新增 `tool_search` primitive，技能描述建索引，模型按需搜索而非全量注入 prompt

### 7.6 Runtime Metrics Dashboard（运行时指标面板）

**目标**：实时展示双 Gate 拦截情况

**方案**：
- SSE 推送 `_gate_removed` / `_execution_gate` 事件
- 前端 ContextSidebar 展示拦截计数
- 按触发条件聚合统计

**涉及文件**：`session.py` SSE 发射、`ContextSidebar.vue`

---

## 8. 评测结果存档

| 版本 | 文件 | 幻觉率 | 95% CI | 日期 |
|------|------|--------|--------|------|
| baseline_v2 | `hallucination_20261023_193644.json` | 2.7% (1/37) | ±5.2% | 2026-10-23 |
| output_gate | `hallucination_20261023_200711.json` | 0.0% (0/37) | 上界 ≈ 8.1% | 2026-10-23 |
| execution_gate | `hallucination_20261023_212554.json` | 0.0% (0/37) | 上界 ≈ 8.1% | 2026-10-23 |
| A/B 对比 | `comparison_20261023_212605.md` | HSC = 1.00 | — | 2026-10-23 |

---

## 附录：EndedReason 枚举完整定义

```python
class EndedReason(str, Enum):
    COMPLETED = "completed"              # 正常完成
    MAX_ITERATIONS = "max_iterations"    # 迭代上限截断
    TOKEN_BUDGET = "token_budget"        # Token 预算耗尽
    NUDGE_EXHAUSTED = "nudge_exhausted"  # Nudge 次数耗尽
    DUPLICATE_CALLS = "duplicate_calls"  # 重复调用放弃
    FAKE_FINAL = "fake_final"            # Execution Gate 拦截
    CLARIFICATION_PAUSED = "clarification_paused"  # 澄清暂停
    GUARDIAN_PAUSED = "guardian_paused"            # 守卫暂停
    STORYBOARD_PAUSED = "storyboard_paused"        # 故事板暂停
    ERROR = "error"                      # 异常兜底
```

---

## 附录：简历/项目描述参考

> 以下提供三个粒度的写法，按需选用。措辞区分了**架构保证**（确定性）和**评测结果**（统计性），避免过度承诺。

### 一句话版（项目描述行）

> 设计并实现 **Receipt Ledger + 双 Gate（Output Gate / Execution Gate）硬校验架构**，在 ReAct 范式下对已知两类幻觉出口实现代码级确定性拦截，37 场景评测幻觉率 **2.7% → 零检出**（HSC = 1.0，零假阳性）。

### 两行版（项目 bullet point）

> - 针对 ReAct 范式下 LLM 两类幻觉（假调用 / 脑补产物），设计 **Receipt Ledger + 双 Gate 硬校验架构**：Output Gate 拦截无回执背书的产物声明（Type-2），Execution Gate 标记零执行假完成（Type-1）
> - 37 场景全量评测：幻觉率 **2.7% → 零检出**，HSC = 1.0（完全抑制），假阳性率 0%

### 详细版（技术面试准备 / 项目详情）

> **ReAct 幻觉抑制 — Receipt Ledger + 双 Gate 架构**
>
> - **问题**：LLM 在 ReAct 工具调度中存在两类幻觉 — Type-1 假调用（声称完成但零工具执行）、Type-2 脑补产物（调错工具却声明正确产物），软 prompt 约束易被 LLM 忽略
> - **方案**：设计系统侧硬校验架构，不依赖 LLM 自觉：
>   - **Receipt Ledger**：每次工具执行自动建不可伪造回执（call_id / ok / artifacts），LLM 无法篡改
>   - **Output Gate**：比对 final output 产物声明与回执账本，删除无背书 key，补全遗漏产物
>   - **Execution Gate**：检测零回执零产物的非正常终止，标记 `_fake_final` 并改写终止原因
> - **结果**：37 场景全量评测零检出，基线幻觉率 2.7%，HSC = 1.0，假阳性率 0%，零工具接口改动
> - **边界**：架构对 Type-1/Type-2 出口提供确定性拦截，不覆盖自然语言幻觉声明和 value 质量校验

### 指标写法规范

| 写法 | 评价 | 建议 |
|------|------|------|
| 幻觉率 2.7% → 零检出（37 场景） | ✅ 准确且严谨 | **首选**，"零检出"是事实，"37 场景"限定范围 |
| 幻觉率从 2.7% 降至 0% | ⚠️ 易被追问"真的永远零吗" | 可用但需准备好解释置信区间 |
| HSC = 1.0（完全抑制） | ✅ 有学术感 | 作为补充指标，体现方法论严谨 |
| 幻觉抑制率 100% | ❌ 数学上 2.7→0 不是 100% 抑制 | 避免这样写 |
| 完全消除幻觉 | ❌ 过度承诺 | 避免这样写 |

**推荐组合**：

```
对已知两类幻觉出口实现代码级确定性拦截，37 场景评测幻觉率 2.7% → 零检出（HSC = 1.0），假阳性率 0%
```

信息密度最高且严谨：
- **代码级确定性拦截**：区分了 prompt 约束和硬校验
- **已知两类幻觉出口**：限定了架构覆盖范围，不过度承诺
- **2.7% → 零检出**：有基线有结果，"零检出"是评测事实而非绝对承诺
- **HSC = 1.0**：有方法论名称，不是拍脑袋的数字
- **假阳性率 0%**：证明不是暴力过滤换来的
- **37 场景**：证明评测覆盖面，不是 cherry-pick