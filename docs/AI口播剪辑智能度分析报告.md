# AI 口播剪辑"为什么总是套模板"——基于代码证据与竞品调研的分析报告

> 调查日期：2026-10-27
> 方法：本项目代码级证据（文件行号 + 四轮 demo 实测）+ 开源竞品联网调研（来源见文末）

---

## 一、一句话结论

**我们的管线把 LLM 关在"16 个预写模板里选一个 + 填参数"的封闭词表中——这个结构正是 Remotion 官方在 LLM 视频生成指南里点名批判的 "template engines with an AI layer"（套壳模板引擎）。** 智能被压缩在参数层，视觉层零自由度、零反馈，所以无论怎么加模板、调 prompt，用户的感受永远是"套模板"。

---

## 二、本项目现状的机制证据（代码级）

### 证据 1：LLM 的决策空间是一个封闭词表
[backend/capabilities/video/services/overlay_orchestrator.py#L205](../backend/capabilities/video/services/overlay_orchestrator.py#L205)
规划 prompt 将 LLM 任务定义为：从 16 个模板（caption_pop / keyword_icon / arrow_point / full_card / split_compare 等）中选模板、填 params、定时间轴。每个模板的参数 schema 是固定的（如 arrow_point 只能传 x/y/direction/label/color）。**LLM 没有任何视觉设计自由度，只有排列组合权。**

### 证据 2：视觉资产全部预写，动画数学写死
[backend/capabilities/video/adapters/overlay_templates.py#L1046](../backend/capabilities/video/adapters/overlay_templates.py#L1046)
TEMPLATES 注册表下 16 个模板函数，每个都是手写 HTML/CSS/JS + 固定动画曲线（easeOutBack 弹入 + sin 微动）。新增一种视觉 = 改代码 + 写测试 + 重跑验证。**这就是"每换风格都要改脚本"的直接原因。**

### 证据 3："智能"只存在于三个薄层
- **时间轴对齐**（真智能，效果好）：[talking_head_overlay.py#L577](../backend/capabilities/video/skills/talking_head_overlay.py#L577) `_words_for_segment` 词级对齐 ASR 时间戳，叠加精准落在说到的词上。
- **密度/节奏自检**（只数数，不理解画面）：[overlay_orchestrator.py#L280](../backend/capabilities/video/services/overlay_orchestrator.py#L280) `overlay_coverage`、[#L433](../backend/capabilities/video/services/overlay_orchestrator.py#L433) `validate_plan_rhythm` 只检查数量/间隔/palette 一致性。
- **参数填充**（信息抽取，不是设计）：LLM 从转录里提取关键词/数字填进模板。

### 证据 4：管线没有眼睛
LLM 规划完直接渲染出片，全程不知道画面长什么样。质量检查全部发生在管线之外——本项目 v4 迭代中的 4 个 bug（参数别名导致文字塌缩、encode 截帧、繁体字幕、null 崩溃）全部是**人肉抽帧**发现的。

### 实证：四轮 demo 的产出永远是同一词表的洗牌
同一支 54s 视频四轮规划产出 17 / 22 / 18 / 20 个叠加，模板分布永远在这 16 种之内（本轮：字幕类 7 + 卡片类 13）。"太单一"的感受边界 = 模板词表的边界，与 prompt 调优无关。

---

## 三、竞品对照（开源项目联网调研）

| 项目 | LLM 决策层 | 视觉产物 | 自动质量闭环 | 智能感来源 |
|---|---|---|---|---|
| **MoneyPrinterTurbo** | 仅文案层（脚本/搜索词/标题） | 素材库（Pexels）下载 + FFmpeg 确定性拼装 | 无 | 文案 |
| **NarratoAI** | 文案 + 选段/时间轴（VLM 每 3s 抽帧看图写解说；两步 LLM 链抽数时间戳） | 字幕烧录 + 片段拼接 | 无（靠 WebUI 人工编辑） | VLM 看得懂画面 |
| **FunClip** | 仅选段（读转写稿返回片段时间戳） | Pillow 烧字幕 | 未查到 | 语义选段 |
| **KrillinAI** | 仅文本层（字幕分段/对齐/翻译） | 字幕样式 + 裁切 | 未查到 | 翻译质量 |
| **Remotion Claude** | **全层**（结构/动画/转场/视觉层级全由 LLM 决策） | **LLM 直接写 React/Remotion 代码**，官方明确 "not template fill-ins" | 半闭环（preview-first 预览+对话式迭代，人确认后渲染） | **视觉层本身** |
| 本项目 | 选模板+填参数+时间轴 | 16 个预写 HTML 模板 | 无（bug 靠人肉抽帧） | 时间轴对齐 |

### 调研得出的三条规律

1. **"套模板感"与 LLM 决策层数量成反比**。四家开源工具都把 LLM 关在文字层（文案/选段/翻译），视觉层是确定性代码——智能只填缝隙，所以都"像模板"。唯一例外是 Remotion Claude：LLM 直接写渲染代码，视觉决策全开放。
2. **全自动审片闭环是行业空白**。没有任何一家开源项目做了"渲染后自动审片→重试"。闭环模式只存在于 agent 框架与论文中（CHIEF 多模态观片打分、Livepeer drift-gate 重生成、frame-level QA watch-loop）。**谁先补上谁形成差异化。**
3. **退化方向明确**：字幕、翻译、素材拼装层的 LLM 化已被做烂且用户不感知；感知最强的两处是"视觉生成"和"质检闭环"。

---

## 四、问题根源诊断

把上面的证据合起来，"总是套模板"有三个根源，按因果顺序：

1. **架构根源**：v1 为了保证逐帧确定性渲染（seekTo 契约），把"确定性"和"预写模板"捆绑了。实际上两者可拆开——契约（seekTo(t)、禁真实时钟、选择器审计）同样能约束 LLM 运行时生成的任意 HTML。当初的稳妥选择变成了今天的天花板。
2. **能力根源**：视觉层零生成能力 → 每种新视觉都要开发者预写 → 模板库永远滞后于需求 → 用户永远觉得"就这几招"。
3. **反馈根源**：无自动审片 → 管线不知道自己产出好坏 → bug（文字塌缩/截帧/错位）直接到用户眼前 → "不智能"的体验被放大。

---

## 五、改进路线（每条有竞品佐证，按性价比排序）

### P0：VLM 自动审片闭环 —— 行业空白，差异化最强，成本最低
渲染完自动抽 3-5 帧 → VLM 看图审查（文字塌缩？箭头指错位置？字幕挡脸？配色撞车？）→ 有问题带反馈重新规划/修参数 → 重渲（上限 1-2 次）。
- 佐证：规律 2（全行业空白）；NarratoAI 证明 VLM 能看懂视频帧；CHIEF/Livepeer 验证了闭环模式。
- 本项目条件齐备：渲染确定性（同输入必出同画面，审查结果可信）、失败降级已有、单次审查成本约 $0.1-0.3、十几秒。
- **本质：把本报告证据 4 中"人肉抽帧"这一步装进管线。**

### P1：模板生成器 —— 消灭"16 个模板"的边界，对标 Remotion Claude
需求匹配不到现有模板时，LLM 运行时生成新模板 HTML（我们的模板本来就是 HTML + seekTo 契约，与 Remotion Claude 生成 React 代码是同一模式）→ 契约校验（逐帧可渲染、无真实时钟、选择器审计——测试里已有现成的审计逻辑）→ 通过则入库复用。
- 佐证：Remotion Claude 官方文档明确 "not template fill-ins"；本项目 v4.2 的选择器审计测试已把生成质量的防线写出来了。
- 每个新生成模板约 30-60s + 一次校验渲染，生成后零边际成本。

### P2：时间轴两步链 —— 参考 NarratoAI
当前单 prompt 直接出时间轴。改为两步：先让 LLM 输出叙事结构摘要（开场/论点/转折/收束 + 每段的视觉意图），再对每段抽数时间戳和模板/参数。时间轴质量上限更高，且为 P0 的审查反馈提供了可解释的中间层。

### 不做：字幕/翻译/素材层继续加码
佐证：规律 3——这些层已被开源项目做烂且用户不感知。

---

## 六、预期形态变化

| | 现状 | P0+P1 落地后 |
|---|---|---|
| 视觉空间 | 16 个封闭模板 | 预写模板 + LLM 运行时生成，无上限 |
| 质量保障 | 管线外人肉抽帧 | 管线内 VLM 审片闭环 |
| 换风格 | 改代码 | 配置/生成，多数零代码 |
| 竞品位置 | "template engine with AI layer" | 开源圈首个带自动审片闭环的口播剪辑管线 |

---

## 附：竞品调研来源
- MoneyPrinterTurbo：[GitHub](https://github.com/harry0703/MoneyPrinterTurbo) / [管线深度拆解](https://wangruofeng007.com/blog/2026-08/moneyprinterturbo-pipeline-deep-dive/)
- NarratoAI：[架构文档](https://deepwiki.com/linyqh/NarratoAI) / [两步 LLM 链](https://deepwiki.com/linyqh/NarratoAI/4.4-short-drama-processing)
- FunClip：[GitHub](https://github.com/modelscope/FunClip) / [v2.1.0 发布说明](https://www.funasr.com/en/blog/funclip-v2-1-0-video-clipping-release.html)
- KrillinAI：[KlicStudio 架构](https://deepwiki.com/krillinai/KlicStudio/1-overview) / [技术解析](https://cloud.tencent.com/developer/article/2514163)
- Remotion Claude：[发布博文](https://www.remotionvideo.com/blog/introducing-remotion-claude) / [官方 coding agents 指南](https://www.remotion.dev/docs/ai/coding-agents)
- 闭环模式研究：[CHIEF（arXiv）](https://arxiv.org/html/2606.18591v1) / [Livepeer agent drift-gate](https://agent.livepeer.org/playbooks/paste-link-get-project.md) / [Vizard watch-loop 分析](https://www.emerging-ai-review.com/autonomous-ai-video-editing-vizard-agent-one-pass-workflow-watch-loop/)
- 剪映图文成片拆解（闭源参照，可信度一般）：[CSDN](https://blog.csdn.net/IterLoom/article/details/163240711)
