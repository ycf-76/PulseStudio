# Agent 驱动口播影片制作环境（Video Studio）— 设计文档

日期：2026-11-10
状态：待用户审批

## 1. 意图与背景

现状：口播成片链路是「LLM 抽要点 → 静态 HTML → 截图 PNG → ffmpeg 硬切拼接 → RVM 抠像小窗 PIP」。产出被用户否决：画面全静态、无交互感、配图与内容不符。修补静态链路（加转场、换模板）无法解决根本问题。

对标：开源项目 agent-motion（https://github.com/erduo1998-cell/agent-motion）证明了口播包装的正确形态——四层空间合成（原视频底轨 → 身后包装层 → 透明人物层 → 前景包装层）、内容随口播分时入场、连续转场、agent 看真实画面自查后交付。其效果来源是「工具链 + 规则文本 + coding agent 按片现场创作」，不是模板。

用户裁决（本设计的核心约束）：**产品必须接得住任意制作方法论**。用户带来任何 skill（agent-motion 或其他打法），agent 都能读规则后现场创作。禁止把某个 skill 翻译成固定脚本/模板——那会让下一次换打法时全部作废。

## 2. 目标 / 非目标 / 反目标

**目标**
1. 通用场景工程宿主：agent 可为每支片子写场景代码（HTML/CSS 动效为主，Three.js 可选），有预览、有确定性渲染出口
2. 确定性渲染管线：场景时钟逐帧驱动 → 帧 PNG → ffmpeg 编码 + 音轨合成，音画帧级对齐；另有快速预览模式
3. 共享信号层：whisper 词级时间轴、抠像人物轨、生图、SFX 作为独立素材交给创作 agent，任何方法论都能消费
4. 自审查闭环：agent 自动抽帧/截图/响度检查，看真实画面后修复，再交付
5. 方法论装载机制：用户的 skill 以文本规则形式分阶段读入；平台只保证工具与质检可用，不预设风格
6. 后端创作循环：带工具的 agent 环节（读规则 → 拆内容 → 写场景代码 → 渲染 → 自查 → 修 → 交付 MP4 + 可编辑工程）

**非目标**
- 不复刻 agent-motion 的代码/规则文本（非商用许可，禁止搬运）
- 不追求全自动一键出片（创作循环本身慢，按片几分钟到几十分钟是接受的代价）
- 不引入重资产依赖（不上 AE/Blender；Three.js 可选不强推）
- 不做自动配音（沿用现有 TTS 能力即可，不在本设计范围）

**反目标（出现即失败）**
- 产出重新变成「整页同时出现后长时间静止」的静态幻灯片
- 风格逻辑再次固化进 Python 流水线（方法论必须以文本规则输入）
- agent 不看真实画面就宣称完成

## 3. 约束

- 平台：Windows 为主，后端 FastAPI + venv（backend/.venv），ffmpeg 用 imageio_ffmpeg 内置二进制
- 现有可复用资产（已验证可用）：
  - whisper 词级转录：backend/capabilities/video/adapters/whisper_transcribe.py（CPU 子进程隔离，见 talking_head_overlay._transcribe_cpu_subprocess 的教训：ct2 与 RVM 不能同进程）
  - RVM 人物抠像：backend/capabilities/video/adapters/person_matting.py
  - 词对齐：backend/capabilities/video/adapters/word_align.py
  - Playwright 渲染基建：backend/capabilities/video/adapters/_render_worker.py、html_to_video.py（含 karaoke 逐词帧渲染先例：talking_head_overlay._render_karaoke_frames）
  - SFX 库：backend/capabilities/video/assets/sfx/（ffmpeg 合成，免版权）
  - 生图：万相（key 已验证有效）
  - 任务框架：app/core/skill_registry、services/video_jobs.py / video_worker.py
- 许可：agent-motion 为 Motion Craft Community License 1.0（非商用）——只借鉴公开方法论思想，不复制其代码与规则文本
- 成本：逐帧截图渲染 30fps 全片（如 60s ≈ 1800 帧 × ~80-120ms/帧 ≈ 2.5-3.5 分钟渲染），可接受；预览走实时录屏或低帧率样段

## 4. 方案取舍

**A. 修补现有模板链路**（分时入场 CSS + xfade）——弃。风格逻辑仍固化在 Python 里，换打法即废；用户明确否决模板化。
**B. 直接引入 agent-motion 作为引擎**——弃。非商用许可；依赖其仓库与 per-film agent 会话，产品无法内嵌；且其规则文本不可搬运。
**C.（选定）自建通用制作环境 + 创作循环**：工具链、信号、质检平台化（这些与风格无关），方法论以文本输入、agent 现场写场景代码。慢但接得住任意打法，效果上限等同对标项目。

## 5. 架构分层

```
用户 skill（文本规则，任意打法）
        │ 分阶段读取
        ▼
┌─ 创作循环（后端 agent，带工具）─────────────────┐
│ ① 读规则 → ② 内容计划（timeline.json）→        │
│ ③ 素材准备（人物轨/生图/SFX）→ ④ 写场景代码 →  │
│ ⑤ 渲染 → ⑥ 自审查（看真实画面/听响度）→        │
│ ⑦ 修复重渲染 → ⑧ 交付 MP4 + 可编辑工程         │
└──────────┬───────────────────────────────┘
           ▼
┌─ 制作环境（平台固定层，与风格无关）─────────────┐
│ 场景宿主 │ 渲染管线 │ 信号层 │ 素材层 │ 质检工具 │
└─────────────────────────────────────────────┘
```

### 5.1 场景工程宿主（studio workspace）

每支片子独立目录 `backend/outputs/studio/<film_id>/`：

```
work/<film_id>/
├── inputs/            # 原片、SRT、用户 brief、skill 规则副本
├── analysis/          # 内容计划 timeline.json、素材清单
├── scene/             # agent 写的场景工程（index.html + js/css/资产）
├── assets/            # 人物轨(透明webm/帧序列)、生图、sfx、字体
├── frames/            # 渲染帧 PNG（中间产物）
├── review/            # 自审查产物：抽帧连播、关键点截图、响度报告
└── out/               # 最终 MP4 + 可编辑工程说明
```

- 场景工程 = 单页 HTML + CSS 动画（默认）/ Three.js（需要 3D 空间感时），由 agent 按方法论要求现场编写
- 场景代码契约：暴露全局时钟接口 `window.seekTo(t)`（设置场景到第 t 秒的确切状态，确定性、无随机、无依赖真实流逝时间），供逐帧渲染与预览共用。命名对齐现有 _render_worker.py 已实现的 `window.seekTo` 契约（零新概念，复用渲染循环）
- 平台提供基础工程脚手架（画布尺寸 1080×1920、字体加载、本地预览服务），不含任何风格预设

### 5.2 确定性渲染管线

**合成契约（builder check 已裁决）**：场景页负责**完整合成**——原片预抽帧（JPEG 序列）与人物透明帧序列（RVM 产物）都作为素材喂进场景页，单页分层绘制（底帧 → 身后层 → 人物 → 前景层），每帧单次截图。ffmpeg 只负责帧序编码 + 混音，不做多层 overlay。理由：单一契约最简、同步偏差只在页内一处、P0 验证面最小。磁盘代价：55s@30fps 原片约 1650 张 JPEG（~250MB），可接受；builder 可按实测改用更低抽帧率（底帧 24fps 也够）。

- 逐帧模式：Playwright 打开场景页 → 循环 `__setTime(i/fps)` → 截图 PNG → ffmpeg 帧序编码 + 混音（原声/音乐 + SFX）
- 预览模式：整段实时录屏（Playwright video 录制）或截取样段低帧率渲染，供创作循环快速自查
- 实现位置：扩展 backend/capabilities/video/adapters/ 下新 adapter（如 studio_render.py），复用 _render_worker.py 的浏览器管理与 imageio_ffmpeg 的 ffmpeg 路径
- 音画对齐：帧时间戳由场景时钟唯一决定；音频以词级时间轴为基准混音，输出前用 ffprobe 校验时长偏差 ≤ 1 帧

### 5.3 共享信号层

- timeline.json（内容计划产物，创作循环 ② 的输出、场景代码的输入）：

```json
{
  "fps": 30, "duration": 55.2, "canvas": {"w": 1080, "h": 1920},
  "speech": [{"start": 0.2, "end": 2.8, "text": "...",
              "words": [{"text": "词", "start": 0.2, "end": 0.4}]}],
  "beats": [{"id": "b1", "type": "claim|quote|stat|compare|...",
             "t_start": 0.0, "t_end": 8.4, "title": "...",
             "visual_task": "该段的视觉表达任务（方法论语境）",
             "assets": ["gen/xxx.png", "sfx/pop.wav"],
             "triggers": [{"text": "触发词", "t": 3.1}]}]
}
```

- speech 来自 whisper_transcribe（词级、繁→简归一化复用现有 _simplify_cn 契约）；beats 的语义划分由创作 agent 按所读方法论产出（字段白名单如上，visual_task 自由文本——这正是「接得住任意打法」的入口）
- 人物轨：person_matting 产出透明帧序列/webm；生图、SFX 为可选素材，由创作循环按需调用

### 5.4 自审查闭环（质检工具，平台固定层）

- 抽帧连播：按 beats 时间点自动抽关键帧 + 样段连续帧，拼 contact sheet 供 agent 视觉检查
- 规则检查（确定性）：帧序完整、时长一致、安全区越界检测（文字/元素出 1080×1920 安全区）、人物轨与底轨同步偏差
- 响度检查：混音后 ffprobe/astats 报峰值与响度，防止 SFX 盖过人声
- agent 必须读 review 产物后才可宣称完成；未完成的缺口如实标注

### 5.5 方法论装载

- v1 内置一份平台方法论（文本规则，存于仓库，如 backend/capabilities/video/skills/studio_methods/<name>/），创作循环分阶段完整读取后再动手；用户指定其他 skill 时，其文本规则同样入库走同一装载机制（内置优先、外部可插）
- 平台不解释、不执行 skill 中的风格规则——它们只约束创作 agent 写场景代码的行为
- 平台固定层兜底的是质量底线（音画对齐、安全区、人物轨同步），与风格无关，任何方法论下都适用

### 5.6 后端创作循环

- 形态：新增 backend/capabilities/video/skills/video_studio.py 作为入口 skill，内部驱动创作 agent（LLM + 工具集：读文件/写文件/跑渲染/跑质检），而非调用固定编排函数
- **基建归属（builder check 已裁决）**：v1 在 video_studio skill 内自建轻量工具循环（LLM 多轮 + 显式工具调用），不复用 app/engine 的 Reactor——video 链路无 Reactor 先例，轻量循环范围小、可控；待跑通后再评估是否上收 Reactor 获得权限体系
- 现有流水线（talking_head_overlay 等）保留不动，studio 是并列的新通道；跑通后再评估是否收敛
- 与任务框架对接：走 video_jobs 异步任务，进度事件逐阶段上报（修复此前吞事件的 P0 问题：每阶段完成即 emit）

## 6. 实施顺序（每阶段有验证出口）

- **P0 渲染 spike**（先证可行）：手工写一个最小场景工程（原片为底 + 身后动效文字分时入场 + 前景触发卡片，用 live_person_talk.mp4 的 55s 素材中一段 ~15s），实现 `__setTime` 契约 + 逐帧渲染 + 混音。验证：输出 MP4 音画对齐、动效真实可见、单帧渲染耗时 ≤ 150ms。
- **P1 环境固化**：workspace 脚手架、studio_render adapter、timeline.json 契约、质检工具（抽帧/响度）。验证：spike 场景改由工具链渲染跑通。
- **P2 创作循环**：video_studio skill + 创作 agent 接入（含分阶段读规则、进度事件）。验证：给定一份文本方法论（自拟简化版口播打法），agent 全自动产出一条完整成片，自审查报告齐全。
- **P3 方法论泛化验证**：换一份不同打法的 skill 输入（模拟「下次换打法」），确认同一环境产出符合新打法的成片，平台层零改动。验证：对比两片 beat 结构与动效风格差异真实存在，且 platform diff 为 0。

## 7. 留给 builder 的决策

- 预览服务形式（本地 HTTP 服务 vs 直接 file:// 打开）——builder 按 Playwright 兼容性定
- 透明人物轨格式（webm alpha vs PNG 帧序列）——按体积与解码稳定性实测取舍
- 创作 agent 的模型与提示词组织——按平台现有 LLM 接入层定
- P0 spike 的场景细节（入场曲线、卡片样式）——spike 目的只在验证管线，样式从简

## 8. 风险

| 风险 | 应对 |
|---|---|
| 逐帧渲染耗时超预期 | 预览先行；全片渲染放异步任务并实时上报进度 |
| 创作 agent 产出场景代码质量不稳 | 场景代码只跑在沙箱工作目录；质检兜底 + 失败重试预算；场景契约（__setTime）简化 agent 负担 |
| whisper 词级时间漂移 | 词对齐已有 word_align 校准链；质检含同步偏差检查 |
| LLM 把方法论又写成硬编码 | code review 红线：平台层禁止出现任何风格常量；P3 泛化验证把关 |
| 许可污染 | 全部自研实现；不读/不抄 agent-motion 仓库代码与规则文本，只引用其公开 README 中的方法论思想 |

## 9. 待用户裁决（builder check 产出）— 已裁决（2026-11-10）

1. **用户 skill 的产品入口**：✅ 已裁决——v1 先用平台内置 skill（方法论文本内置于仓库）实现创作循环；用户有要求时，按其指定的 skill 安装（文本规则入库后走同一装载机制），实现「内置优先、外部可插」。
2. **预览与审查**：✅ 已裁决——**需要实时预览给用户看**。除 review/ 自查产物外，提供预览页面（场景页本地服务 + 播放控制），前端可查看预览与抽帧 contact sheet；范围计入 P1/P2。
3. **对话式修改**：✅ 已裁决——不进 v1（避免污染范围）。v1 交付即止；修改能力留待后续版本。
4. **输入边界**：✅ 已裁决——v1 仅支持「口播原片 + 可抠人物」；纯 TTS / 无人物输入留 v2。
