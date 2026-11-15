# Task 3 报告：工作区脚手架与 timeline.json 校验契约（studio_workspace）

**状态：DONE**
**提交：`562d9fbbfc9086fae1cb359a27ca8306ba1bccc1`**（`feat(studio): 工作区脚手架与 timeline.json 校验契约`，父提交 8350754 = Task 2，2 文件 +291 行）

## 一、做了什么

### 文件清单

| 文件 | 角色 |
| --- | --- |
| `backend/capabilities/video/services/studio_workspace.py` | 实现（新建，187 行） |
| `backend/tests/test_studio_workspace.py` | 测试（新建，104 行，brief 测试逐字 + 8 个防御式补充测试） |

### 对外接口（与任务书逐字一致）

```python
def create_workspace(film_id: str) -> dict[str, Path]
def validate_timeline(tl: dict) -> list[str]
def save_timeline(ws: dict, tl: dict) -> Path
def load_timeline(ws: dict) -> dict
```

- workspace 根：`_STUDIO_ROOT = Path(__file__).resolve().parent.parent.parent.parent / "outputs" / "studio"`，与 talking_head_overlay 的 `_OUTPUT_DIR` 同构同深度（services/ 与 skills/ 同级，parents 链 = backend），即 `backend/outputs/studio`
- `create_workspace` 建 `{inputs,analysis,scene,assets,frames,review,out}` 7 个子目录，`mkdir(parents=True, exist_ok=True)` 幂等，返回 dict（键 = 目录名，恰好 7 键，无多余键）
- `save_timeline`：`json.dumps(tl, ensure_ascii=False, indent=2)` + `write_text(encoding="utf-8")` 落 `ws["analysis"]/timeline.json`；`load_timeline` 用 `read_text(encoding="utf-8")` + `json.loads` 回读
- 无平台层风格常量（颜色/字体/动效参数零出现），只管结构与时间合法性

### validate_timeline 校验规则清单（防御式：所有分支返回错误字符串列表，绝不抛异常；错误信息中文、内嵌字段路径）

| 规则 | 错误消息样例 |
| --- | --- |
| tl 非 dict | `timeline 应为对象(dict)，实际为 list` |
| 顶层必填 | `缺少 fps` / `缺少 duration` / `缺少 canvas` / `缺少 speech` / `缺少 beats` |
| fps/duration 数字、有限、>0 | `fps 应为数字，实际为 bool`；`fps 必须为有限数字（NaN/Inf 不可用）`；`duration 必须 > 0，实际为 -1` |
| canvas{w,h} 数字、有限、>0 | `canvas.w 缺失`；`canvas.w 必须 > 0，实际为 -1920` |
| speech 为列表；每项 dict，含 start/end/text；数字类型；end>start；text 为 str | `speech[0].end 必须 > start（实际 end=1.0, start=1.0）`；`speech[1] 应为对象(dict)，实际为 int` |
| beats 为列表；每项 dict，含 id/t_start/t_end/title/visual_task；id/title/visual_task 为 str；t_start/t_end 数字；t_end>t_start | `beats[2].title 缺失`；`beats[2].id 应为字符串，实际为 int`；`beats[0].t_end 必须 > t_start（实际 t_end=4.0, t_start=5.0）` |
| beats 按 t_start 升序不重叠（相邻 t_start==上一 t_end 允许） | `beats[1].t_start 与上一 beat 重叠（须按 t_start 升序且不重叠，上一 beat t_end=3.0）` |
| trigger 挂在 beat.triggers 下；每项 dict、含 t、t 为数字；t ∈ [t_start,t_end] | `beats[0].triggers[0].t=5.1 超出所属 beat [t_start,t_end]=[0.0,5.0]`；`beats[0].triggers[1].t 缺失` |
| 顶层 triggers = 挂错位置 | `顶层 triggers 挂错位置：trigger 应挂在各 beat 的 triggers 字段下` |
| beat.t_start/t_end 类型坏时跳过该 beat 的 trigger 区间检查与重叠链（类型错已报，避免级联噪音） | — |

防御细节：`_is_num` 排除 bool（`True` 不能当 1 参与时间/帧率校验）且拒绝 NaN/Inf；上一 beat 无效时中断重叠链（`prev=None`），保证垃圾输入只产出有限、可读的错误列表（`test_validate_never_raises_on_garbage` 以 8 种垃圾输入 fuzz 验证）。

### 补充测试（brief 测试之外的 8 个）

`test_create_workspace_dirs_and_idempotent`（7 键集合 + 幂等）、`test_validate_missing_required_keys`、`test_validate_type_errors_and_bool`、`test_validate_rejects_nonfinite_numbers`、`test_validate_speech_item_errors`、`test_validate_beats_item_and_order_errors`、`test_validate_trigger_rules`、`test_validate_never_raises_on_garbage`。

## 二、测试输出关键行原文（TDD 全程）

**RED**（实现前，`backend/.venv/Scripts/python -m pytest backend/tests/test_studio_workspace.py -v`）：

```
backend\tests\test_studio_workspace.py FFFFFFFF                          [100%]
E   ModuleNotFoundError: No module named 'capabilities.video.services.studio_workspace'
FAILED backend\tests\test_studio_workspace.py::test_workspace_and_timeline_contract - ModuleNotFoundError: No module named 'capabilities.video.services.studio_workspace'
============================== 8 failed in 0.63s ==============================
```

**中途失败**（首轮实现后，暴露「t_start/t_end 类型错未报错」缺口，见自审 1）：

```
backend\tests\test_studio_workspace.py .....F..                          [100%]
E   AssertionError: ['beats[1].t_start 与上一 beat 重叠（须按 t_start 升序且不重叠，上一 beat t_end=3.0）', 'beats[2].title 缺失', 'beats[2].id 应为字符串，实际为 int']
assert False
 +  where False = any(<generator object test_validate_beats_item_and_order_errors.<locals>.<genexpr> at 0x00000252656CBED0>)
============================== 1 failed, 7 passed in 0.34s ==============================
```

**自审 RED**（NaN/Inf 微循环，`_is_num` 未拒绝非有限数时）：

```
backend\tests\test_studio_workspace.py F                                 [100%]
E   AssertionError: []
assert False
 +  where False = any(<generator object test_validate_rejects_nonfinite_numbers.<locals>.<genexpr> at 0x000002E24E3E2AC0>)
============================== 1 failed in 0.26s ==============================
```

**GREEN**（最终轮）：

```
backend\tests\test_studio_workspace.py .........                         [100%]
============================== 9 passed in 0.10s ==============================
```

**回归**（Task 1/2 studio 系列共享链路）：

```
backend\tests\test_studio_assets.py .                                    [ 50%]
backend\tests\test_studio_render.py .                                    [100%]
============================= 2 passed in 11.89s ==============================
```

## 三、自审发现

1. **t_start/t_end/speech start/end 类型错无产出（已修）**：初版只用 `_is_num` 做区间/排序守卫，字段为字符串/None 时不产「应为数字」错误。GREEN 首轮测试抓住（上面中途失败原文），补显式类型检查分支后通过。
2. **NaN/Inf 漏洞（已修，自审 TDD 微循环）**：`json.loads` 默认放行 `NaN/Infinity`，而 `nan > 0` 恒 False → `fps=NaN` 会溜过「>0」检查直达渲染层崩溃。`_is_num` 对 float 加 `math.isfinite`（int 不走 isfinite，避免超大 int 转 float OverflowError）；`_type_err` 对非有限 float 单独报「必须为有限数字（NaN/Inf 不可用）」，避免「应为数字，实际为 float」误导自修 agent。
3. **ruff 未跑**：venv 无 ruff（`No module named ruff`），与 Task 1 留档同况，留 Minor。
4. **测试落盘真实目录**：brief 测试 `create_workspace("ws_test1")` 写真实 `backend/outputs/studio/ws_test1/`（含 round-trip 的 timeline.json），幂等可重复运行，无清理需求；未做隔离是 brief 原样设计。
5. **报错不含「空 title/visual_task」检查**：契约只说「含」字段，未要求非空，未加（避免超出任务书的过度校验）。

## 四、偏离任务书的点

1. **补充测试 8 个**：brief Step 1 只含 1 个测试，但硬约束要求防御式校验（缺键/类型错/triggers 挂错位置均不抛异常）+ 中文带字段名错误——这些行为 brief 测试未覆盖，按硬约束补齐（brief 测试本体逐字未动）。
2. **`save_timeline` 加 `indent=2`**：brief 只要求 utf-8 + ensure_ascii=False；indent=2 让 timeline.json 人类/agent 可读（配合「自修指南」定位），数据语义不变。
3. **speech/beats 缺失即报错**（`缺少 speech`/`缺少 beats`）：brief 仅对 fps/duration/canvas 标注「必填」；但缺 beats 的 timeline 无法渲染且静默通过会让下游报错不可读，按「错误信息是创作 agent 自修指南」的定位从严。P2 若有「无 speech 纯视觉片」需求，需回改此条（一行）。
4. **提交方式与 Task 2 相同绕行**：`git add <两文件 pathspec>` + `git commit -F <UTF-8 消息文件> --only <两文件 pathspec>`；树构建阶段仍报 `error: invalid object 100644 12bd4f2... for '_tmp_shot/inspect_shell.py'`（84 缺失 blob 噪音）但提交成功完成，与 Task 2 报告现象一致。提交产物已验证：562d9fb，2 文件 +291 行，父提交 8350754，中文消息无乱码。未跑 git status / add -A / 任何索引全量命令；消息临时文件已删。
