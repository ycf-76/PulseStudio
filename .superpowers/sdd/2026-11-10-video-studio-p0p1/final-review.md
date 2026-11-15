# 最终整体审查 — Video Studio P0+P1（分支 codex/design-workflow-orchestration-cards）

- 日期：2026-11-10
- 范围：BASE `f759e8e` → HEAD `dd4cc37`，7 个提交，14 个新增文件（+1646 行）
- 输入：计划（2026-11-10-video-studio-p0p1.md）、设计 spec（2026-11-10-agent-video-studio-design.md）、台账（progress.md）、全分支审查包（final-review-package.md）+ 仓库源文件逐行核读
- 定位：6 个任务级审查已通过，本审查只看跨任务缝隙（集成、契约漂移、测试卫生、全局约束全分支一致性）

---

## 一、跨任务审查重点逐项结论

### 1. 契约一致性（extract_frames → render_scene → review_workspace → StudioPreviewServer）

- ws dict 七键（inputs/analysis/scene/assets/frames/review/out）：create_workspace 产出、studio_review 消费 `frames`/`review`、studio_preview 消费 `scene`、save/load_timeline 消费 `analysis`、spike 全部使用——**全链一致**。
- fps 语义链：`FrameSeqInfo.fps=24`（底帧）→ spike 写入 manifest `base.fps` → play.js 用 `M.person.fps / M.base.fps` 换算人物帧（无硬编码 24/30 混用）→ 渲染 30fps → timeline.fps=30 供 review 抽帧/完整性。三种 fps（底帧/人物/渲染）在各消费点语义正确。**除发现 #1 的帧数公式漂移外无契约漂移**。
- 帧命名：Task1 产 `frame_%06d.jpg`、Task2 产 `frame_%06d.png`，studio_review `_FRAME_FILE_RE` 双兼容；spike 人物帧重命名 `person_%06d.png` 与 `int(f.stem)` 依赖 MatteResult 的 `{count:06d}.png` 命名——已核 person_matting.py:151 属实，契约成立。
- 混音链：`-shortest` + anullsrc 定长（Task 2 偏离裁决）与 review 静音轨回归钉（Task 4 销 Task 2 欠账）跨任务闭环良好。

### 2. 确定性红线 — **PASS**

全 diff 扫描 random/Date/performance.now/transition/animation/keyframes：
- play.js `seekTo` 为纯函数，`performance.now` 仅存在于 `?preview` rAF 循环（play.js:106），不进渲染路径，且头部注释明确契约；fixture 无任何 CSS transition/animation（动效全手写 easeOutCubic 插值）。
- 平台模块的 `time.time()`（studio_render.py:78,116、studio_render_worker.py:35,59）仅作 elapsed 耗时指标，不参与画面计算；worker 的 `wait_for_timeout(300)` 是计划明文规定的固定资产余量。
- 无随机数进入渲染路径。红点：render_scene 每帧仅 `seekTo` + 截图，seekTo 返回 decode Promise 被 evaluate await——比 _render_worker 原版更强的确定性保障，赞。

### 3. 风格常量红线 — **实质 PASS，字面 1 处触线（发现 #3）**

adapter/service 层唯一视觉常量在 studio_review.contact_sheet（质检拼图自身版式：底色/字色/字号）。属 QC 工具 chrome 而非影片风格，实质无害；场景 fixture 的 #ff2442 等颜色属场景代码，红线明确豁免。

### 4. 测试卫生

- 无跨文件顺序依赖：test_studio_workspace 两个测试共用固定 film_id `ws_test1`（发现 #5），但写入内容兼容、断言幂等，正反序均安全；test_studio_review 的模块级 `_TL` 为只读共享，安全。
- skipif 门控：仅 test_studio_spike 用 `STUDIO_SPIKE=1` 门控，语义正确（GPU+分钟级渲染默认 skip）；门控开启后素材缺失走 assert fail-loud——对 opt-in live 测试是合理取舍（skip 反而掩盖配置错误），不列为发现。
- 确定性断言按计划原文只比对 frame 0 跨 run 字节相等（frame 3 仅像素断言）——与计划一致，不追责实现，但记录：P2 若加随机性风险场景，建议扩为全帧比对。
- 重复造轮子：发现 #8（三组重复 helper，且已实际发生行为分叉——发现 #4 即其后果）。

### 5. 集成缝隙（spike 对前 5 个模块的使用）

- spike 全程通过五模块公开契约调用（create_workspace/extract_frames/render_scene/review_workspace/StudioPreviewServer/matte_person），无绕过契约操作内部状态的行为。`shutil.rmtree(ws["scene"])` 后 copytree 属工作区 scene 目录的合法内容管理（scene 目录本就是场景代码所有）；`assets_manifest.js` 是 spike 场景侧私有契约（场景代码归 agent/fixture 所有），不构成平台契约污染。
- 强制重抠（rmtree person_raw）推翻了计划「复用缓存跳过 GPU」的意图——理由正确（缓存只认目录，seg 变更会复用错帧），但属未留档偏离，见 #6。

---

## 二、发现列表

### Critical（0 条）

无。没有任何发现阻塞 P0 交付物（final.mp4 + review 产物）或红线级违例。

### Important（2 条）

1. **帧数契约漂移：render 用 ceil、review 用 round，P2 任意时长会误报「多余帧」**
   - `backend/capabilities/video/adapters/studio_render.py:68` 与 `backend/capabilities/video/adapters/studio_render_worker.py:30`：`n_frames = max(1, int(duration * fps + 0.999))`（≈ceil）；
   - `backend/capabilities/video/services/studio_review.py:290`：`expected = round(duration * fps)`（banker's rounding）。
   - 理由：当 `duration*fps` 小数部分 ∈ (0, 0.5) 时（如 duration=7.35 @30fps → 220.5：render 产 221 帧、review 期望 220），completeness_check 报「多余帧」→ review errors 非空，P2 创作 agent 会被幽灵错误打断自查闭环。全分支所有测试时长恰好整除（0.5×8、1×8、2×8、15×30），该 seam 零测试覆盖——与台账 Minor「缺号路径无直接测试」叠加在同一链路，故升 Important。P0（15s 整除）不受影响，但契约按此固化将带病进 P2。修法：review 改用与 render 相同的 ceil 公式（并抽成单一常量/函数，消除双份公式），补非整除 duration 边界测试。

2. **计划 Review Focus 3 未落地：无「SFX 段 vs 全程 RMS」对比能力**
   - `backend/capabilities/video/services/studio_review.py:151-175`（loudness_report 仅返回全程 `{rms, peak, duration}`）、`review_workspace`（219-318）无分段响度字段。
   - 理由：计划 Review Focus 3 明文要求「Task 4 响度报告输出 SFX 段与全程 RMS 对比字段，断言可读出差异」——这是设计 §5.4「防止 SFX 盖过人声」的落地载体。注意计划自身矛盾：Task 4 接口定义只有 rms/peak/duration，实现跟了接口，任务级审查按接口放行漏检了 Review Focus。P0 spike 无 SFX 不阻塞本分支验收，但该欠账必须显式进 P2 计划（当前管线连 SFX 混音入口都还没有——`_encode_video` 仅单音频输入），否则反目标「SFX 盖过人声」在 P2 无自动检测闸门。

### Minor（7 条）

3. **QC 工具版式常量字面触线** — `backend/capabilities/video/services/studio_review.py:124`（字号 14）、`:132`（画布底色 `(24,24,24)`）、`:142`（标注字色 `(235,235,235)`）。质检产物自身版式非影片风格，实质无害；但按红线字面「颜色/字体不得写死进 service」属触线，建议提为具名常量并注释豁免理由（QC chrome，非风格），留档即可。
4. **CREATE_NO_WINDOW 约定漂移** — `backend/capabilities/video/adapters/studio_assets.py:123`、`:141-144` 两处 subprocess.run 未带 creationflags，而 studio_render.py:86,165、studio_review.py:62、test_studio_spike.py:56 全带。控制台闪窗仅在 pythonw/GUI 宿主下可见，pytest 场景无感；但同分支同惯例两处遗漏，正是重复实现分叉的实证（见 #8）。
5. **单元测试写真实 outputs 树且固定 film_id 共享** — `backend/tests/test_studio_workspace.py:4,21,25`：`create_workspace("ws_test1")` 落盘 `backend/outputs/studio/ws_test1/`（含 timeline.json），非 tmp_path 隔离、无清理；根 `.gitignore` 无 outputs 条目 → untracked 残留污染 git status（本仓库索引状态本已脆弱，见台账）。计划原文如此（实现照抄，非实现者过错），内容兼容无顺序依赖，风险低。建议 P2 前改 tmp_path 隔离或补 outputs 忽略条目。
6. **spike 三处未留档偏离** — `backend/tests/test_studio_spike.py:130-131`（rmtree person_raw 强制重抠，推翻计划「复用缓存跳过 GPU」意图，理由正确但台账未记）；fixture 场景从计划的 `backend/outputs/studio/spike1/scene/` 迁至 `backend/tests/fixtures/studio_spike/scene/`（方向更对——fixture 不该住产物目录）；计划的 styles.css 并入 index.html `<style>`。三处均属合理偏离，纯记账问题。
7. **Task 1 (0,0) 降级语义下游未防护，且预警名单漏了实际消费者** — `backend/capabilities/video/adapters/studio_assets.py:98-111` 探测失败返回 `(0,0)`；spike 原样写入 manifest（`test_studio_spike.py:162-165`），`play.js:30` `scale = Math.max(M.canvas.w / M.base.w, M.canvas.h / M.base.h)` 在 w=0 时得 Infinity → 场景 transform 崩坏输出黑帧。台账预警「Task 2/4 消费时注意」但真正按宽高做除法的是 Task 6 的 play.js。仅探测双链全败才触发，低概率；建议 manifest 生成侧断言 w/h>0 或 play.js 加护栏。
8. **重复实现三组 helper，且已发生行为分叉** — `_run_ffmpeg` ×3（studio_assets.py:114 / studio_review.py:55 / test_studio_spike.py:45）、`_ffmpeg_i_stderr` ×2（studio_assets.py:133 / studio_review.py:67）、`_is_num` ×2（studio_workspace.py:33 / studio_review.py:46，docstring 自认同语义）。#4 的 creationflags 遗漏即此结构的直接后果。P2 建议收敛为单一 `_ffmpeg_util` 模块。

### Minor 跨任务叠加评估（ mandate 第 6 项）

- **唯一升级组合**：#1（帧数公式漂移）+ 台账 Task 4 Minor「缺号路径无直接测试」落在同一条 render→review seam 上且零覆盖 → 已升 Important #1。
- 其余 Minor 分散在不同链路（QC 版式 / Windows 约定 / 测试卫生 / 记账 / 降级路径），互不叠加，无第二处组合升级。
- 正向销账闭环良好：Task 2「静音轨缺回归钉」已由 Task 4 的 `_audio_pin_errors` + `test_review_workspace_missing_audio_pin` 销账。

---

## 三、全局约束全分支核查表

| 约束 | 结论 |
|---|---|
| ffmpeg 一律经 resolve_ffmpeg | ✅ 全部 4 处调用点（assets/render/review/spike）合规，无硬编码路径 |
| Playwright 独立子进程 + os._exit(0) + CREATE_NO_WINDOW | ✅ worker 合规；父进程 taskkill /T 收尸（正常路径也执行，带走 chromium 孙进程） |
| seekTo 确定性（禁随机/禁真实时间进画面） | ✅ 见「确定性红线 PASS」 |
| 画布 1080×1920 / 渲染 30fps / 底帧 24fps 默认 | ✅ render_scene 默认值与 extract_frames 默认值合规 |
| 平台层禁风格常量 | ⚠️ 字面 1 处（#3，QC chrome），实质合规 |
| whisper 与 RVM 不同进程 / 不新增转录 | ✅ spike 复用 matting_live2/transcript.json，零 whisper 调用 |
| 输出落 outputs/studio/<film_id>/ | ✅ spike 合规；#5 单测残留属测试卫生非产品输出 |
| 不复制 agent-motion 代码/规则文本 | ✅ 全部自研实现 |

---

## 四、总裁决

**通过 —— 分支可合入，进入 P0 人工验收**（用户肉眼验收 final.mp4 动效可见性/音画对齐/人物同步）。

附条件：
1. Important #1（render ceil vs review round 帧数契约漂移）必须在 P2 计划中立为首个销账项——建议修 review 侧公式并补非整除 duration 边界测试，P2 创作循环依赖此契约正确；
2. Important #2（SFX RMS 对比能力）与 Minor #5/#7/#8 一并写入 P2 计划欠账清单；
3. Minor 均不阻塞合并。
