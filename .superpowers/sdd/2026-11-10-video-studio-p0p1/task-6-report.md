# Task 6 报告：P0 spike 集成验证（分层场景 + 确定性渲染 + 质检）

状态：**DONE** ｜ 提交：`dd4cc37c9e3ae8eaeda79618e68573f8aa79a667`（分支 codex/design-workflow-orchestration-cards）
测试：`1 passed in 230.20s`（STUDIO_SPIKE=1）；无门控时默认 `1 skipped in 0.08s`

## 1. 产物与验收入口

| 产物 | 路径 |
| --- | --- |
| 成片（15s，2.8MB） | `backend/outputs/studio/spike1/out/final.mp4` |
| 常驻预览 URL（测试外另起，验收用） | http://127.0.0.1:8901/index.html?preview |
| timeline | `backend/outputs/studio/spike1/analysis/timeline.json` |
| contact sheets（每 beat 一张） | `backend/outputs/studio/spike1/review/beat_beat_cold_0.00.png`、`beat_beat_stomach_5.00.png`、`beat_beat_sun_10.00.png` |
| 渲染帧序（450 帧） | `backend/outputs/studio/spike1/frames/frame_%06d.png` |
| 预切段 / 音频 | `backend/outputs/studio/spike1/inputs/seg.mp4`、`out/seg_audio.m4a` |

（backend/outputs 不提交。测试内打印的预览 URL 随测试结束停止；上表 8901 端口是我测试通过后另起的常驻 StudioPreviewServer，供用户立即验收。）

## 2. 场景工程结构（提交件）

```
backend/tests/fixtures/studio_spike/scene/
├── index.html   # 四层 DOM（顺序即 z 序）：#base → #title → #person → #card
└── play.js      # window.seekTo(t) 纯函数驱动；?preview 才启动 rAF 循环
```

运行时组织（测试负责，非提交件）：fixture 拷入 `outputs/studio/spike1/scene/`，帧资产拷入
`scene/assets/base/frame_%06d.jpg`（24fps，362 帧）与 `scene/assets/person/person_%06d.png`
（抠像 30fps，452 帧，来自 `matte_person`），测试生成 `scene/assets_manifest.js`
（`window.STUDIO_SCENE_MANIFEST`：帧目录/数量/尺寸/fps、canvas、beats 标题、triggers 文案+until）。
选直接拷贝方案（Windows 软链需特权，junction 依赖 cmd；拷贝最简且 file:// 可用）。

### seekTo 实现（确定性红线）

- **底帧层**：`index = floor(t*24)`，clamp `[0, count-1]`，src 相对路径换帧。
- **人物层**：`index = round(baseIdx * person.fps / base.fps)` —— 取与底帧**同一视频时刻**的抠像帧，
  两层像素同瞬，对齐无重影（若按 `floor(t*30)` 独立取帧，两层最大差 41ms，口播动嘴必出重影）。
- **身后标题条**：进入 beat 后 `p=clamp((t-t_start)/0.4)`，`easeOutCubic` 手写插值
  `translateX(-108%*(1-e))` + opacity=e，零 CSS transition。顶部布局避开了人物头部（纯背景区，不被人物层遮挡）。
- **前景卡片**：trigger.t 后 0.3s 弹入（scale 0.8→1、dy 26→0），beat 结束前 0.3s 弹出，同手写插值。
- **纯函数**：无 Math.random / Date / performance.now 进入画面计算（performance.now 仅出现在 ?preview 的 rAF 循环里）。
- **截图安全**：seekTo 同步改 DOM 后返回 `Promise.all([base.decode(), person.decode()])`；
  Playwright `page.evaluate` 会 await 返回的 Promise（worker 协议兼容，`studio_render_worker` 未改），
  保证逐帧截图不拍到半解码旧图。这是换帧类场景能跑通 450 帧逐帧截图的关键。
- **渲染模式**：URL 无参数不启动任何循环，只暴露 seekTo；`?preview` 才 rAF 实时循环播放。

## 3. 测试输出关键行原文

```
tests\test_studio_spike.py .
[studio-spike] 预览 URL: http://127.0.0.1:59133/index.html?preview
[studio-spike] 成片: D:\My_Project\多智能体小红书发布平台\backend\outputs\studio\spike1\out\final.mp4
[studio-spike] 响度: {'rms': -13.557944, 'peak': 0.26032, 'duration': 15.0}
[studio-spike] contact_sheets: 3 张
[studio-spike] speech 来源: 既有转录产物 transcript.json（7 段真实时间，非占位）
1 passed in 230.20s (0:03:50)
```

门控验证：无环境变量运行 → `1 skipped in 0.08s`（skipif 原因文案随报告可见）。
渲染 worker 状态文件原文：`{"ok": true, "count": 450, "elapsed": 169.4}`（450 帧实测 169s，
worker 内部超时预算 330s + 测试 shell 超时 10min，余量充足）。

## 4. review errors 内容

`review_workspace` 返回 **errors == []**（空），五个子项全过：
- 每 beat contact sheet 3 张生成 ✓
- 帧完整性 450/450（无缺号/0 字节/越界）✓
- 响度 RMS -13.56 dB / Peak 0.26 dB（真实音轨）✓
- 时长偏差：实测 15.000s vs timeline 15.0（容差 0.083s）✓
- 静音轨回归钉：音轨存在且时长正常 ✓

## 5. 自审发现（成片帧肉眼核查）

- **t=0.1s（frame_000003）**：标题条处于滑入中段（「别猛灌冰水」半出）——动效真实发生，非瞬移。
- **t=2.1s**：标题「第二件事：别猛灌冰水」完整 + 红卡「大量出汗后，别立刻猛灌冰水」弹入完成。
- **t=7.0s**：beat2 标题「肠胃最怕冷刺激」+ 卡片「很容易腹痛、拉肚子」；**t=11.8s**：beat3「第三件事：做好防晒」+「帽子、防晒衣、防晒霜都安排上」。三 beat 标题切换、三卡片触发均正确。
- **人物层对齐**：抽帧核查无重影/双边（同瞬取帧方案有效）；四层（底帧/身后标题/人物/前景卡）全部真实可见。
- 卡片文案在 max-width 86% 内自动换行成 3-4 行竖块——spike 验管线不验美观，可接受。
- 首次运行在最后 print 处 NameError（引用了 `_build_timeline` 内局部变量 `speech`），管线本身已全绿；
  修正为 `len(tl['speech'])` 后重跑全链通过（重跑含重抠像+重渲染，非只补断言）。

## 6. 偏离任务书的点

1. **fixture 含 index.html + play.js，另由测试生成 `assets_manifest.js`**：帧数/节拍是运行时数据，
   固定 fixture 写死会失真；manifest 作为 `<script>` 引入（file:// 下 fetch JSON 会因 CORS 被禁，script src 可用）。
2. **人物层索引取法**：任务书只规定底帧 `floor(t*24)`；人物层改为按底帧同瞬取帧（见 §2），
   否则两层差 ≤41ms 必出重影，直接违反「无重影」验收项。
3. **seekTo 返回 decode Promise**：worker 协议未改、位置计算仍纯函数，仅让 evaluate 等解码完成；
   不加此层，换帧截图存在拍到半解码图的不确定风险。
4. **预切段**：`-c copy` 一次成功（seg 实测 15.07s，漂移 0.07s < 0.3s 容差），未走重编码分支；
   抽帧 362 / 抠像 452 帧（多出的 2 帧来自 15.07s 源），manifest 按实际计数 + clamp 处理，渲染仍精确 450 帧/15.0s。
5. **speech 未用占位**：任务书预设「词级时间找不到就均匀占位」；实际找到本视频既有转录产物
   `backend/outputs/matting_live2/transcript.json`（test_matting_live2 对 live_person_talk.mp4 的 whisper 产物），
   7 段真实分段时间做段内偏移写入 speech——故 review errors 无「占位时间轴」标注。该产物为分段级（无词级），
   spike 场景无词级字幕消费点，不构成缺口。
6. **测试外常驻预览服务**：任务书测试流程外的附加动作（8901 端口），仅为把可用预览 URL 交到用户手上。

## 7. 其他备注

- whisper 全程未在本进程加载（仅读转录 json），RVM 同进程 GPU 推理正常（RTX 4060）。
- git 提交按约束走：具体文件 `git add` + `-F` UTF-8 消息文件 + `--only`；期间出现既有索引损坏噪音
  （`error: invalid object ... for '_tmp_shot/inspect_shell.py'`），未影响本次提交（该路径不在提交集）。
- 复跑方式：`set STUDIO_SPIKE=1 && backend\.venv\Scripts\python -m pytest backend/tests/test_studio_spike.py -v -s`
  （幂等：重抠像+重渲染，约 4 分钟；matte 缓存目录先清后抠，防 seg 变更复用错帧）。
