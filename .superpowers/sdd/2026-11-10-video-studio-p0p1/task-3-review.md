# Task 3 审查报告：工作区脚手架与 timeline.json 校验契约（studio_workspace）

- 审查对象：commit `562d9fb`（2 文件 +291 行：`backend/capabilities/video/services/studio_workspace.py` 187 行、`backend/tests/test_studio_workspace.py` 104 行）
- 审查方式：对照任务书逐条核对实现与测试；审查包 diff 与实际源码逐行核对一致（187/104 行无出入）；未重跑测试（采信报告 RED→GREEN 证据：9 passed + 回归 2 passed）。

## 一、规格符合性核对

### 1.1 brief 校验规则覆盖表

| brief 规则 | 实现 | 测试 | 结论 |
| --- | --- | --- | --- |
| 必填 fps>0 / duration>0 | 缺失/类型/≤0 三分支（L65-77），bool 被排除（True 不当 1） | brief bad（duration=-1）+ `test_validate_type_errors_and_bool`（fps=True） | ✅ |
| canvas{w,h} 数且 >0 | 非 dict / w·h 缺失 / 类型 / >0（L79-91） | brief bad 隐含 + type_errors 测试（canvas.w="1080"、h=-1920） | ✅ |
| speech[] 每项 start/end/text，end>start | 缺失/类型/end≤start（L93-112） | `test_validate_speech_item_errors`（end==start 钉住严格大于） | ✅ |
| beats[] 每项 id/t_start/t_end/title/visual_task，t_end>t_start | 缺失/str 类型/数字类型/t_end≤t_start（L114-136） | brief bad（5.0→4.0）+ beats[2] 类型错测试 | ✅ |
| beats 升序不重叠 | 相邻对 `beat.t_start < prev.t_end` 报错（L137-139），`<` 严格 → **相邻边界（前 t_end==后 t_start）合法**；传递性成立（每 beat 起点 ≥ 前一终点 ⇒ 终点严格递增 ⇒ 全局不重叠） | 重叠负例已测（b2 与 b1）；**相邻边界正例无测试钉住** | ⚠️ 见 Important-1 |
| triggers[].t ∈ 所属 beat [t_start,t_end]，挂在 beat.triggers 下 | `_check_triggers` 闭区间 `t_start <= t <= t_end`（L149-171），t==边界合法；beat 时间字段坏时跳过区间检查防级联 | 越界 5.1 / 缺 t / 非 dict / 顶层挂错均测；**t 恰在边界的正例无测试钉住** | ⚠️ 见 Important-1 |

### 1.2 全局约束核对（计划原文四条）

| 约束 | 核实 |
| --- | --- |
| workspace 根 = backend/outputs/studio，对齐 `_OUTPUT_DIR = parents[3]/"outputs"` 惯例 | ✅ `_STUDIO_ROOT = Path(__file__).resolve().parent.parent.parent.parent / "outputs" / "studio"`（L16）。services/ 与 skills/ 同在 `backend/capabilities/video/` 下，4 层 parent = backend；grep 证实 talking_head_overlay.py:71 等 6 处 skills 同构同深度 |
| validate_timeline 防御式：畸形输入返回错误列表，绝不抛异常 | ✅ 非 dict 早退（L61-62）；所有比较前置 `_is_num` 守卫；重叠链在 prev 类型坏时中断（`prev=None`，防级联噪音）；`test_validate_never_raises_on_garbage` 7 组垃圾输入 fuzz（None/list/str/int/缺键/嵌套坏类型） |
| 错误信息带字段名、中文 | ✅ 全部消息内嵌字段路径（`beats[2].t_end`、`speech[0].end`、`canvas.w`…），供创作 agent 自读自修 |
| 平台层禁止风格常量 | ✅ 全模块零颜色/字体/动效参数，只有路径与字段名 |
| save_timeline utf-8 ensure_ascii=False | ✅ L181：`json.dumps(tl, ensure_ascii=False, indent=2)` + `write_text(encoding="utf-8")`；`indent=2` 为已申报偏离，仅影响可读性，数据语义不变，接受 |

### 1.3 陷阱项核对

| 陷阱 | 行为 | 判定 |
| --- | --- | --- |
| 空 beats / 空 speech（`[]`） | 放行（逐项规则空满足） | 见 Minor-2，brief 未要求非空，不算违规 |
| triggers 为空列表 | 放行（循环不执行） | ✅ 正确 |
| t 恰在边界（== t_start / t_end） | 闭区间放行，符合 brief `[t_start,t_end]` 记法 | ✅ 实现正确，测试未钉住（并入 Important-1） |
| 非 dict 输入（None/[]/str/int） | 单条错误早退 | ✅ garbage 测试覆盖 |
| 相邻 beat 边界（前 t_end == 后 t_start） | 严格 `<`，合法不报错 | ✅ 实现正确，测试未钉住（Important-1） |

### 1.4 YAGNI 核查

brief 之外出现的代码逐项过筛：

- 顶层 `triggers` 挂错位置检查（L143-144）：brief 括注「trigger 挂在 beat 的 triggers 字段下」定义了放置契约，检测违反是该契约的防御式校验（否则挂错位置被静默吞掉，违背「错误信息是自修指南」定位），非新功能 → 合规。
- `text/id/title/visual_task` 的 str 类型检查：brief 只说「含」字段，类型检查属防御式延伸，与「任何畸形输入返回错误字符串」硬约束一致；且 brief bad 样例的 `title:""`、`visual_task:""` 正确放行（未加非空检查，报告自审 #5 申报）→ 合规。
- 无多余对外接口（恰好 4 个函数）、无多余依赖（仅 json/math/pathlib）→ 合规。

**结论：无 brief 之外的功能性越界。**

## 二、两个偏离点核实

### 偏离 1：speech/beats 缺失也报错 —— 合理，接受

- **设计一致性**：`docs/superpowers/specs/2026-11-10-agent-video-studio-design.md:170` 确认裁决「v1 仅支持口播原片 + 可抠人物；纯 TTS / 无人物输入留 v2」→ v1 下无 speech 的 timeline 本就是非法输入，报错正确。
- **报错可读**：`缺少 speech` / `缺少 beats`，中文 + 字段名，可自修。
- **不误伤**：brief 自带合法样例含 speech/beats，`validate_timeline(ok) == []` 有测试钉住；报错仅命中「键缺失」，不影响任何 brief 合法输入。
- **行为已钉住**：`test_validate_missing_required_keys` 对 5 个缺失字段逐一断言。
- 报告已申报 P2 回改点（一行），痕迹完整。

### 偏离 2：math.isfinite 修复 json.loads 放行 NaN/Inf —— 修复正确，接受

逐点核实（源码 L33-47）：

1. **守卫完备**：json.loads 确实默认放行 `NaN/Infinity`，而 `nan > 0` 恒 False 会绕过 `>0` 检查。模块内**所有**数值比较（fps/duration/canvas.w/h/speech start·end/beats t_start·t_end/重叠链/trigger t）均在 `_is_num` 守卫之后（elif 链或 `if _is_num(...) and ...`），NaN/Inf 无法到达任何比较运算。
2. **bool 顺序正确**：`isinstance(v, bool)` 先于 int 检查——bool 是 int 子类，顺序反了 `True` 会被当 1 放行。
3. **int 不走 isfinite**：避免超大 int 转 float 的 OverflowError（`math.isfinite(10**400)` 会炸；int 路径直接 True，后续 `<= 0` 比较对大 int 也安全）。
4. **消息不误导**：`_type_err` 对非有限 float 单独报「必须为有限数字（NaN/Inf 不可用）」，而非「应为数字，实际为 float」（后者会让自修 agent 困惑）。
5. **已钉住**：`test_validate_rejects_nonfinite_numbers`（fps=NaN、duration=inf），报告含自审 RED（`AssertionError: []`）→ GREEN 证据。

## 三、发现清单

### Critical（0 条）

无。

### Important（1 条）

1. **边界语义实现正确但无测试钉住（审查重点明确要求项）**
   - beats 相邻边界：前 beat `t_end == 后 beat t_start` 应合法——实现用严格 `<`（L138）正确放行，但 9 个测试中无任何正例钉住（现有样例要么单 beat、要么明确重叠的负例）。若后人把 `<` 误改为 `<=`，无测试失败，合法 timeline 会被静默拒收。
   - trigger t 恰在边界：实现为闭区间 `t_start <= t <= t_end`（L170），符合 brief `[a,b]` 记法，但测试只钉了 5.1（界外）与 0.2（界内），`t == t_end`（如 5.0）无测试。若右端 `<=` 被误改为 `<`，同样无测试报警。
   - 建议：补一个正向断言（beats `[0,3],[3,5]` + trigger `t=3.0` → `validate_timeline == []`），两个边界一次钉死。属测试缺口，不阻塞合入。

### Minor（4 条）

1. **顶层 triggers 断言近乎恒真**（test_studio_workspace.py:95）：`any("挂错" in e or "beats[" in e for e in errs)`——因 `or "beats["` 一侧，其他 trigger 错误消息（`beats[0].triggers[0].t=...`）已含 `beats[`，即使删掉 L143-144 的顶层挂错检查该断言仍通过。建议收紧为仅 `any("挂错" in e for e in errs)`。
2. **「缺失报错」与「空列表放行」口径不一致**：`speech: []`、`beats: []` 均通过校验（后者在 `test_validate_rejects_nonfinite_numbers` 中作背景数据出现且未报错）。与偏离 1「缺 beats 无法渲染即报错」的逻辑相比，空 beats 同样无法渲染却放行。brief 未要求非空，v1 渲染层会自然暴露，不算违规；建议留到 P2 与「无口播纯视觉片」一起裁决。
3. **报告数字小误**：报告自审称 garbage fuzz「8 种垃圾输入」，测试实际为 7 组（None/[]/"tl"/42/{fps:None,canvas:[]}/beats 坏项/speech·beats 混合坏项）。不影响代码与结论，属留档准确性。
4. **film_id 未净化**（studio_workspace.py:26）：`root = _STUDIO_ROOT / film_id` 原样拼接，`film_id="../x"` 可逃逸出 outputs/studio。brief 未要求、调用方为内部管线（film_id 由上游生成），按 YAGNI 当前可接受，仅留档；后续若 film_id 可能来自用户输入需加白名单校验。

## 四、裁决

1. **规格符合性：✅ 通过** —— brief 五组校验规则全部实现且各有对应测试（两处边界正例缺测试钉住，属 Important-1 的测试缺口而非实现缺陷）；四条全局约束全部满足；两个偏离点均经核实合理（偏离 1 与设计裁决 L170 一致且报错可读不误伤，偏离 2 修复正确且有 RED→GREEN 证据）；无 YAGNI 越界。
2. **代码质量：通过** —— 防御式校验结构清晰（守卫前置、级联中断、消息带字段路径）、修复严谨（bool/int/float 三路分明），0 Critical / 1 Important（测试钉住缺口，建议下个任务顺手补）/ 4 Minor。不阻塞合入。
