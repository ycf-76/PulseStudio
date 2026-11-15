# Task 6 审查报告：P0 spike 集成验证

审查人：任务审查者 ｜ 日期：2026-11-10
对象：commit `dd4cc37`（3 文件：`backend/tests/test_studio_spike.py` + `fixtures/studio_spike/scene/{index.html,play.js}`）
方式：源码逐行核实（场景/worker/review/workspace/matte/preview），未重跑 live 测试（230s，报告有证据）。

---

## 一、四个偏离点逐一核实

### 1. 人物层取帧 `round(bi*personFps/baseFps)` —— ✅ 数学正确

- 底帧 `bi = floor(t*24)` 显示源视频瞬间 `bi/24`；与底帧同瞬的人物帧号 = `(bi/24)*30 = bi*1.25`，`round` 后误差 ≤ 0.5 人物帧 = **16.7ms**。
- 对照原方案 `floor(t*30)`：底帧时刻 `floor(t*24)/24` 与人物帧时刻 `floor(t*30)/30` 的相对差上界 ≈ 1/24 ≈ **41.7ms**——报告「≤41ms 重影」的说法成立。
- 24fps 网格展示 30fps 素材必然跳/复人物帧（pi 序列 0,1,3,4,5,6,8,…），但每一对显示帧时间对齐，这正是「无重影」的正确做法；clamp `[0, count-1]` 兜住边界（t=15 → bi=360 → pi=450 < 452 ✓）。
- 两个序列都以 seg.mp4 的 t=0 对齐（extract_frames `start_number=0`；matte 帧从 `000000.png` 起），基准一致。

### 2. seekTo 返回 decode Promise —— ✅ 生效机制确认

- `studio_render_worker.py:50` 为 `page.evaluate("(t) => window.seekTo(t)", t)`——Playwright 的 evaluate 对返回的 Promise **原生等待 resolve**，worker 未改（commit 仅 3 文件，已核实），偏离点 2 的机制成立。
- `play.js:156-158` 设 src 后立即 `decode()`，按规范 decode 作用于当前（新）src；若加载失败 decode reject → Promise.all reject → evaluate 抛错 → worker 状态文件 `ok:false` → 测试响亮失败。失败模式正确。
- 位置计算仍是纯函数，decode 等待不引入任何时间依赖画面——不违反确定性红线。

### 3. assets_manifest.js 以 script src 注入 —— ✅ 生成与加载链路完整

- 生成：测试步骤 6 `write_text("window.STUDIO_SCENE_MANIFEST = " + json.dumps(manifest, ensure_ascii=False) + ";")`，落在渲染（步骤 8）与预览之前 ✓。
- 加载：`index.html` 中 `<script src="assets_manifest.js">` 先于 `play.js`，play.js IIFE 直接读 `window.STUDIO_SCENE_MANIFEST` ✓。
- file:// 下 fetch/XHR 本地 JSON 确被 Chrome CORS 拦截，script src 可用——方案选择正确。UTF-8 中文内容随文档编码（meta charset=utf-8）正确解析。
- 注：fixture 目录本身无该文件（运行时生成），单独打开 fixture 会因 `M` 未定义失效——见 Minor-3。

### 4. speech 复用既有转录产物 + 段内偏移 —— ✅ 偏移数学正确且基准统一

- `transcript.json` 已核实：段级 `{start,end,text}`，时间基准为 **live_person_talk.mp4 源视频**（如 22.06–24.88「第二大量除汗之後…」）。
- 测试 `SEG_START=22.0` 同时用于：`-ss 22.0` 切段（`-ss` 在 `-i` 前，输出时间基准重置为 0）与偏移 `s0 = start - SEG_START`——**同一常量做切割与换算，单一事实来源**，换算自洽。样例核对：22.06–24.88 → 段内 0.06–2.88，与 beat_cold（0–5s，「别猛灌冰水」+ trigger 1.6s）内容对齐 ✓。
- 残段裁剪 `s1-s0 > 0.05` 合理；validate_timeline 先行通过（测试有 assert）。
- 残留误差见 Minor-2（-c copy 关键帧吸附 ~0.07s 漂移）。

---

## 二、红线逐条核验

| 红线 | 结论 | 证据 |
| --- | --- | --- |
| seekTo 纯函数（禁 random/Date/performance.now 进画面） | ✅ | play.js 的 seekTo 仅用 floor/round/clamp/easeOutCubic；performance.now 只在 `?preview` 的 rAF 循环里（play.js:165） |
| 禁 CSS transition 做渲染态，手写数学插值 | ✅ | index.html 样式无任何 transition/animation；标题 easeOutCubic translateX+opacity（0.4s），卡片 scale/dy/opacity 弹入弹出（0.3s），均按 t 直接置终态 |
| 渲染模式不启动循环；?preview 才 rAF | ✅ | play.js:164 仅 `URLSearchParams.has("preview")` 时启动；渲染模式只暴露 seekTo |
| whisper 禁止进本测试进程 | ✅ | 测试及其导入链（studio_assets/render/review/workspace/preview/person_matting/html_to_video）均无模块级 whisper/ctranslate2 导入（grep 核实）；whisper_transcribe.py 重依赖全为函数内惰性导入；测试只读 json，不调 transcribe |
| matte_person None → skip 非 FAIL | ✅ | `pytest.skip("RVM 人物抠像不可用…不算 FAIL")`，降级契约正确 |
| STUDIO_SPIKE=1 门控默认 skip | ✅ | 模块级 `pytestmark = pytest.mark.skipif(os.environ.get("STUDIO_SPIKE") != "1", reason=…)`；报告有 `1 skipped in 0.08s` 证据 |
| 成片断言 errors==[] / duration≈15±0.05 / 每 beat contact sheet | ✅ | 三个 assert 逐一存在（test 步骤 9）：`review["errors"] == []`、`abs(dur_final-15) <= 0.05`、`len(contact_sheets)==len(beats)` + 逐个 `is_file` |
| backend/outputs 不提交；提交仅测试 + scene fixture | ✅ | `git show --stat dd4cc37`：恰好 3 文件 364 行新增，无 outputs；工作区与提交一致（status 干净） |

---

## 三、四层结构与动效可见性（读源码核实，非仅信报告）

- **DOM 层序即 z 序**（均 absolute 无 z-index，后者覆盖前者）：`#base`（底帧 img）→ `#title`（身后标题条）→ `#person`（人物 img）→ `#card`（前景卡片）。标题条位于人物**之下**、卡片最上——与「身后层/前景层」定义一致 ✓。
- **标题滑入**：`e = easeOutCubic(clamp01((t-t_start)/0.4))`，`translateX(-108%*(1-e))` + opacity=e——t=0.1s 时 e≈0.58（半出），与报告自审的「t=0.1s 半出」吻合，动效真实发生非瞬移 ✓。
- **卡片弹入弹出**：pin（scale 0.8→1、dy 26→0）+ pout（beat 结束前 0.3s 上浮 30px 渐隐），opacity=pin*(1-pout) ✓。
- **beat 切换**：取最后一个 `t_start <= t` 的 beat，三 beat 衔接（0/5/10s）切换正确 ✓。
- trigger 选择 `t >= trig.t && t < trig.until`（until=beat.t_end），beat 间空档卡片隐藏 ✓。

---

## 四、测试断言与 brief 逐条对照

brief 9 步全部落实：①预切（-c copy + 时长漂移 ≤0.3s 校验 + 重编码回退）✓ ②extract_frames fps=24 ✓ ③asyncio.run(matte_person)+统一改名 person_%06d.png（matte 输出 `%06d.png`，`int(f.stem)` 成立）+ None→skip ✓ ④手写 timeline（3 beat 各 1 trigger）+ validate 先行 ✓ ⑤seg→aac ✓ ⑥render_scene 450 帧 assert + final.is_file ✓ ⑦review 三断言 ✓ ⑧PreviewServer start/print/finally stop（`start()` 返回以 `/` 结尾，URL 拼接正确）✓ ⑨contact sheet 存在性 ✓。

---

## 五、发现清单

### Critical（0）

无。

### Important（0）

无。

### Minor（4）

1. **brief 的「均匀占位」回退被移除，未列入偏离清单**。brief 步骤 4 规定转录 json「无则均匀占位 + errors 标注『占位时间轴』」；实现改为硬 assert `TRANSCRIPT.is_file()`，缺文件直接 FAIL。行为结果实际等价（占位会让 errors≠[] 而挂测试），只是失败点提前、信息更明确，且 spike 依赖本机产物（outputs 不提交）本就非全新环境可跑——可接受，但属未声明的契约收窄，报告偏离清单应列出。
2. **-c copy 关键帧吸附带来的 speech 残余偏移**。seg 实测 15.07s，实际起点相对精确 22.0s 有 ~0.07s 量级漂移，而 speech 偏移按精确 `SEG_START=22.0` 计算。speech 不被场景视觉消费（play.js 只读 beats/triggers），仅影响 timeline 元数据精度，spike 可接受；后续若 speech 上画面需换精确重编码切段。
3. **fixture 不可独立打开**。fixture 的 index.html 引用运行时生成的 `assets_manifest.js`，fixture 目录内不存在，单独打开会因 `M` 未定义白屏。已在 HTML 注释中说明用途（拷入工作区后由测试生成），属设计选择，提示后续使用者即可。
4. **`_precut_segment` 以 `except AssertionError` 做控制流**。`_run_ffmpeg` 的 assert 失败消息被吞掉后静默落入重编码分支，行为正确但依赖 AssertionError 当信号较脆；若日后 `_run_ffmpeg` 改抛 RuntimeError，回退路径会失效。

### 观察项（不计问题）

- `#world` cover 缩放（5 行）略超 spike 最小需求，但保证底帧/人物同几何坐标系、对源分辨率变化稳健，属合理防御，非镀金。
- beat 切换时旧标题瞬时消失（无滑出动画）——brief 只要求滑入，符合要求。
- 卡片文案 86% 宽自动换行成竖块——spike 验管线不验美观，报告已自述，可接受。

---

## 六、裁决

1. **规格符合性：✅** —— brief 9 步全部落实，断言逐条对照无缺项；四个偏离点全部核实为正确且必要（防重影/防半解码截图/file:// CORS/复用转录产物），红线七条全守，提交范围精确。
2. **代码质量：通过** —— 0 Critical / 0 Important / 4 Minor；场景从简（159 行）、无镀金，时间轴换算单一事实来源，失败模式响亮。Minor 项均为记录性质，不阻塞进入人工验收（看 final.mp4 / 预览 URL 确认动效与音画对齐）。
