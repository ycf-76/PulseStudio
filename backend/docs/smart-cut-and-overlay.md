# 口播视频增强方案：智能剪辑 + 叠加动画

> 基于开源项目真实实现思路，适配本项目 Skill 架构的拓展方案。
> 所有技术细节均来自开源项目源码/文档，非臆造。

---

## 一、开源项目实现思路调研

### 1.1 ossclip（CodeWithAhsan/ossclip）— 最完整的口播→成片方案

**定位**：local-first CLI，把口播 take 一键变成成品短视频。

**核心流水线**（两阶段分离）：

```
阶段 1: ossclip analyze take.mp4
  → 只跑转录 + 检测，不调 LLM，不渲染
  → 输出 marker 文件（每个待删片段的 span + 原因 + 置信度）
  → marker 格式示例：silence -1.77s (conf 0.95)
  → 可导出为 Premiere XML / DaVinci Resolve EDL / FCPXML
  → EDL 中按原因颜色编码：silence=Blue, pause=Sky, filler=Yellow, retake=Red, kept pauses=Lavender

阶段 2: ossclip produce take.mp4 --produce
  → 调 LLM producer brain 规划叠加图形
  → 基于 hand-built Zod-typed scene library 渲染叠加层
  → 叠加类型：title cards, stat cards, diagrams, terminal/chat mockups
  → 不加 --produce 则只做 cut + captions（不调 LLM）
```

**关键实现细节**：

| 模块 | 实现方式 |
|------|---------|
| 转录 | whisper-cpp（本地 C++ 推理，非 Python Whisper） |
| 填充词/静音检测 | 基于 whisper-cpp 逐词时间戳 + 词间间隔分析 |
| 逐词字幕 | word-timed kinetic captions（逐词时间戳驱动动画） |
| 人脸裁切 | face-aware framing（MediaPipe 检测人脸 → 9:16 裁切） |
| 叠加图形 | LLM 规划 → Zod-typed scene library → Remotion 渲染 |
| 封面图 | --portrait 人像参考 + Gemini 生成，或帧抓取兜底 |
| 确认机制 | analyze 只出 marker 不改片，用户在 NLE 里审 marker 后再 produce |

**ossclip 的核心设计哲学**：
1. **analyze 和 produce 分离** — 先出诊断报告（marker），用户审完再执行
2. **scene library 是手写的 Zod schema** — 不是 LLM 随意生成 HTML，而是 LLM 从预定义的场景类型中选择参数
3. **本地优先** — whisper-cpp 本地推理，素材不离开机器

---

### 1.2 video-subtitle-cutter（different-ai/agent-bank）— Skill 模板方案

**定位**：Agent Skill 模板，Whisper 转录 → AI 分析 → ffmpeg 剪辑。

**4 步流水线**：

```
Step 1: Whisper 转录
  → 输出带时间戳的 segments JSON

Step 2: AI 分析转录文本，识别待删片段
  → 用固定 prompt 喂 LLM，识别 4 类问题：
    1. FILLER WORDS: "um", "uh", "like", "you know", "basically", "actually", "so", "right"
    2. FALSE STARTS: 不完整句子重启（"I think— actually, let me..."）
    3. LONG PAUSES: 段间间隔 > 1.5s
    4. REPETITIONS: 同词/短语重复（"really really really"）
  → 输出 cuts JSON（每段 start/end/type/reason）

Step 3: 生成 ffmpeg 命令执行剪辑
  → 逐段 trim → concat
  → 关键：必须 re-encode（libx264），绝不用 -c copy
  → CRF 可调，平衡文件大小和画质
  → 用 -ss seek BEFORE input（快速定位）+ -t duration

Step 4: 生成 SRT 字幕（可选烧录）
```

**关键实现细节**：

```python
# ffmpeg 逐段提取 + 重编码（video-subtitle-cutter 的核心 ffmpeg 调用）
ffmpeg_args = [
    '-ss', str(seg['start']),       # Seek BEFORE input（快速定位）
    '-i', VIDEO_INPUT,
    '-t', str(seg['end'] - seg['start']),  # Duration
    '-c:v', 'libx264',
    '-preset', 'fast',              # fast/medium/slow
    '-crf', '23',                   # 质量控制
    '-c:a', 'aac',
    '-pix_fmt', 'yuv420p',
    '-y', output_path
]
# 然后用 concat demuxer 拼接所有片段
```

**video-subtitle-cutter 的核心设计哲学**：
1. **LLM 做语义分析** — 不只是词典匹配，LLM 能识别 false starts 和上下文相关的废话
2. **必须 re-encode** — -c copy 在非关键帧切割会出花屏，强制重编码保证平滑
3. **Skill 模板化** — 整个流程是一个 SKILL.md，Agent 按步骤执行

---

### 1.3 fillercut — 纯语音分析方案

**定位**：hardware-agnostic CLI，用语音分析检测填充词 + 静音。

**架构**（见 DESIGN.md）：

```
输入视频
  ↓
VAD（Voice Activity Detection）→ 分出有声/无声段
  ↓
有声段 → Whisper 转录 → 逐词匹配填充词词典
  ↓
无声段 → 时长 > 阈值 → 标记为静音待删
  ↓
合并两类待删片段 → ffmpeg trim + concat
```

**关键参数**（来自 cutlist 方案）：

| 参数 | 默认值 | 含义 |
|------|--------|------|
| GAP_CUT | 1.6s | 静音超过此时长才删 |
| PAD | 0.25s | 每个切口两侧保留缓冲（避免切掉词首尾） |
| FILLER_PAUSE | 0.35s | 填充词两侧需要这么长的停顿才可切（避免切掉正常语流） |

---

### 1.4 erm — 交叉淡入淡出拼接方案

**定位**：本地 CLI，剥离 um/uh/er/ah/hmm 等语气词。

**核心创新：自适应交叉淡入淡出**：

```
erm 不用简单的 trim + concat，而是用 ffmpeg acrossfade 做平滑拼接：

  保留段1 ──┐
             ├── acrossfade ── 保留段2 ──┐
             │                         ├── acrossfade ── ...
  保留段2 ──┘                         │
                                      └── ...

交叉淡入淡出时长 = clamp(min, cut_ms * factor, max)
  → 删的短：淡入淡出短（不糊）
  → 删的长：淡入淡出长（不爆音）
  → 不用固定 80ms（太短会糊，太长会拖）
```

**erm 的核心设计哲学**：
1. **crossfade 而非硬切** — 避免拼接处爆音/咔嗒声
2. **淡入淡出时长随删减长度自适应** — 短删短淡、长删长淡
3. **stray sounds 检测** — Whisper 没识别出词但有声（呼吸声、咂嘴），也标记

---

### 1.5 AutoCut（mli/autocut）— 字幕驱动剪辑

**定位**：通过字幕来剪切视频的工具。

**核心流程**：

```
1. Whisper 自动生成字幕（SRT/MD 格式）
2. 用户编辑字幕 MD 文件：删除不要的句子
3. AutoCut 根据编辑后的字幕裁切视频对应片段
```

**关键特点**：
- 编辑隐喻是**文本**而非时间轴 — 用户删一行字 = 删一段视频
- cut.py 提供根据标记后 md 文件的裁切逻辑
- 不做自动检测，完全靠人判断

---

### 1.6 FunClip（alibaba-damo-academy/FunClip）— 阿里方案

**定位**：基于 FunASR Paraformer 的视频裁剪工具。

**核心流程**：

```
1. Paraformer 语音识别 → 带时间戳的逐句转录
2. 用户在转录文本中选择要保留的片段/说话人
3. 点击裁剪 → ffmpeg 输出对应片段视频
```

**关键特点**：
- 用阿里 Paraformer 而非 Whisper（中文识别更准）
- 支持说话人分离（谁在说话）
- Gradio Web UI 交互

---

### 1.7 edit-greek-reel — 3 阶段流水线方案

**定位**：口播视频编辑 Skill，3 阶段流水线。

```
阶段 1: Trim + Crop + Scale
  → 删静音、删 retake、裁 9:16（object-cover，不拉伸）

阶段 2: Subtitles + Zoom + Image Overlays
  → 烧 karaoke 风格字幕（逐词高亮）
  → 加微妙 zoom 效果
  → 加 logo/图片叠加层

阶段 3: Mix SFX
  → 在关键时刻叠音效
```

**字幕实现**（karaoke 风格）：

```
Font size: 72px（at 1080 width）
Style: Sentence case（never ALL CAPS）
Animation: 逐词高亮（word-level timestamp 驱动）
Position: 底部 20% 以上（避开 TikTok/小红书 UI 遮挡区）
```

---

### 1.8 SupoClip — 全功能方案

**定位**：开源 AI 视频剪辑器。

**功能全景**：

| 功能 | 实现 |
|------|------|
| Face-Centered Cropping | MediaPipe + OpenCV 人脸检测跟踪 → 9:16 裁切 |
| Word-Synced Subtitles | word-level timestamps → 动画字幕 |
| Virality Scoring | AI 评分（hook, engagement, value, shareability）0-100 |
| B-Roll Overlay | 自动叠 B-roll 画面 |

---

## 二、开源方案共性提炼

从以上 8 个项目中提炼出**所有方案共有的核心模式**：

### 2.1 智能剪辑的 3 步共识

```
Step 1: 转录（所有方案都有）
  → Whisper / WhisperX / whisper-cpp / Paraformer
  → 输出：逐词时间戳 JSON

Step 2: 检测（所有方案都有，策略不同）
  → 静音检测：ffmpeg silencedetect 或词间间隔
  → 填充词匹配：词典 vs LLM 语义分析
  → 重复/口误：相邻词比较 vs LLM 判断
  → 废话/离题：只有 LLM 能做（video-subtitle-cutter）

Step 3: 剪辑执行（所有方案都有，拼接策略不同）
  → ffmpeg trim + concat（硬切）
  → ffmpeg atrim + acrossfade（平滑切，erm 方案）
  → 必须重编码，不用 -c copy（video-subtitle-cutter 明确指出）
```

### 2.2 叠加动画的 2 步共识

```
Step 1: 规划（LLM 选择叠加什么）
  → ossclip: LLM → Zod-typed scene library → 选场景类型 + 参数
  → edit-greek-reel: 固定叠加字幕 + zoom + logo

Step 2: 渲染（把规划变成视频层）
  → ossclip: Remotion 渲染
  → edit-greek-reel: ffmpeg 烧录
  → C+ 方案: HTML+CSS+JS → Playwright 渲染 → 透明 WebM
```

### 2.3 确认机制的共识

```
所有成熟方案都把"检测/规划"和"执行"分开：

  ossclip:     analyze（只出 marker）→ 用户审 → produce（执行）
  AutoCut:     生成字幕 → 用户编辑 → 裁切
  FunClip:     转录 → 用户选择 → 裁剪
  video-subtitle-cutter: 转录 → AI 分析 → 展示 cuts → 用户确认 → 执行

  本项目已有: auto_short_video 的分镜确认闸门
```

---

## 三、适配本项目的方案设计

### 3.1 设计原则（从开源项目提炼 + 本项目约束）

| 原则 | 来源 | 本项目适配 |
|------|------|-----------|
| analyze 和 produce 分离 | ossclip | Skill 内用 confirm_level 路由，和 auto_short_video 闸门同构 |
| 必须重编码 | video-subtitle-cutter | ffmpeg 用 libx264 + aac，不用 -c copy |
| 切口两侧留缓冲 | fillercut/cutlist | PAD=0.25s，避免切掉词首尾 |
| 音频淡入淡出，视频硬切 | erm + 业界实践 | 音频用 afade 做短淡入淡出，视频用 concat demuxer 硬切（见 3.2.5 详述） |
| LLM 做语义分析 | video-subtitle-cutter | 填充词用词典（零成本），废话/口误用 LLM（按需） |
| scene library 约束叠加类型 | ossclip | 预定义叠加模板（字幕/图标/图表），LLM 只选类型+填参数 |
| 零件可缺 | 本项目 | WhisperX 没装→降级整句；Playwright 没装→降级 ASS 字幕 |
| Playwright 用录屏 API | Playwright 官方文档 | 用 `record_video_dir` 或 `screencast.start` 替代逐帧截图（见 3.3.4 详述） |
| 中文填充词分两级 | 语言学文献 + fillercut | 纯语气词用词典，连词类交给 LLM（见 3.2.3 详述） |

### 3.2 智能剪辑 Skill：smart_cut

#### 3.2.1 数据结构

```python
class CutCandidate(BaseModel):
    """一个待删减的候选片段。对齐 ossclip marker 格式。"""
    start: float                              # 开始时间（秒）
    end: float                                # 结束时间（秒）
    cut_type: Literal["silence", "filler", "repeat", "false_start", "fluff"]
    confidence: float                         # 0-1，对齐 ossclip conf
    reason: str                               # "silence -1.77s" / "filler '嗯'" / "repeat '增长增长'"
    text: str                                 # 该片段原文（如有）
    removable: bool = True                    # 用户可取消勾选（对齐 AutoCut 编辑隐喻）


class SmartCutPlan(BaseModel):
    """删减规划 — 闸门 1 的产物。对齐 ossclip analyze 输出。"""
    video_id: str
    original_duration: float
    candidates: list[CutCandidate]
    kept_segments: list[tuple[float, float]]  # 保留片段的 [start, end] 列表
    kept_duration: float                      # 删减后预估时长
    saved_seconds: float                      # 节省了多少秒
    needs_confirmation: bool = True
```

#### 3.2.2 策略 1：静音检测 — 对齐 fillercut + cutlist

```python
async def _detect_silence(self, video_path: str, threshold_db: float, min_duration: float) -> list[CutCandidate]:
    """
    用 ffmpeg silencedetect 滤镜检测静音段。
    对齐 fillercut 的 VAD 方案和 cutlist 的 GAP_CUT 参数。

    ffmpeg 命令（对齐 ffmpeg-cookbook silencedetect 用法）：
      ffmpeg -i input.mp4 -af silencedetect=n=-35dB:d=0.6 -f null -

    参数含义（对齐 cutlist）：
      n=-35dB: 音量低于 -35dB 视为静音
      d=0.6:   静音持续 0.6s 以上才标记（GAP_CUT）
    """
    cmd = [
        "ffmpeg", "-i", video_path,
        "-af", f"silencedetect=noise={threshold_db}dB:duration={min_duration}",
        "-f", "null", "-"
    ]
    output = await _run(cmd)
    pairs = _parse_silence_pairs(output)
    return [
        CutCandidate(
            start=s, end=e,
            cut_type="silence",
            confidence=0.95,
            reason=f"silence -{e-s:.2f}s",
            text="",
        )
        for s, e in pairs
    ]
```

#### 3.2.3 策略 2：填充词匹配 — 对齐语言学文献 + fillercut

**中文填充词分两级**（依据中研院语言学论文 + SUD Guidelines + Lingflix）：

```
第一级：纯语气词（词典匹配，零成本）
  → 这些词在中文里几乎只用作填充，误判率极低
  → 嗯(èn/ń), 啊(ā), 呃(è), 额(é), 哦(ò)

第二级：指示词类填充词（词典匹配 + FILLER_PAUSE 约束）
  → 这些词有正常用法（"那个东西"里的"那个"不是填充词）
  → 需要两侧停顿 >= FILLER_PAUSE 才标记
  → 那个(nèige), 这个(zhège), 就是(jiùshì), 然后(ránhòu), 对吧(duìba)

第三级：连词/副词（不进词典，交给 LLM 语义分析）
  → "所以""其实""当然""不过"在口播中可能是填充也可能是正常逻辑连接
  → LLM 根据上下文判断
```

**依据**：中研院论文《Phonetic Characteristics of Filled Pauses》指出，中文填充词
分为 simple fillers（嗯/啊/呃，纯 schwa 元音）和 morphologically derived fillers
（那个/这个，由指示词演变）。SUD Guidelines 明确标注了 `那个`、`这个`、`就是`
既可以是填充词也可以是正常词汇，需要语境判断。

```python
# 第一级：纯语气词（几乎只用作填充，误判率极低）
FILLER_WORDS_STRICT = {
    "嗯", "啊", "呃", "额", "哦",
    # 英文（对齐 video-subtitle-cutter）
    "um", "uh", "hmm", "mm",
}

# 第二级：指示词类填充词（有正常用法，需 FILLER_PAUSE 约束）
FILLER_WORDS_CONTEXTUAL = {
    "那个", "这个", "就是说", "怎么说呢", "你知道吗",
    # 英文
    "like", "you know", "basically", "actually", "right",
}

def _detect_filler(self, transcript_words: list[WordTimestamp]) -> list[CutCandidate]:
    """
    两级填充词检测。
    第一级：纯语气词 → 直接标记（误判率极低）
    第二级：指示词类 → 需 FILLER_PAUSE 约束（对齐 cutlist）
    第三级：连词/副词 → 不在此处理，交给 LLM（策略 4）
    """
    candidates = []
    for i, word in enumerate(transcript_words):
        text = word.text.strip().lower()

        # 第一级：纯语气词，直接标记
        if text in FILLER_WORDS_STRICT:
            candidates.append(CutCandidate(
                start=word.start, end=word.end,
                cut_type="filler", confidence=0.95,
                reason=f"filler '{word.text}'",
                text=word.text,
            ))
            continue

        # 第二级：指示词类，需 FILLER_PAUSE 约束
        if text in FILLER_WORDS_CONTEXTUAL:
            prev_gap = word.start - transcript_words[i-1].end if i > 0 else 0
            next_gap = transcript_words[i+1].start - word.end if i < len(transcript_words)-1 else 0
            if prev_gap >= FILLER_PAUSE or next_gap >= FILLER_PAUSE:
                candidates.append(CutCandidate(
                    start=word.start, end=word.end,
                    cut_type="filler", confidence=0.7,  # 置信度低于第一级
                    reason=f"filler '{word.text}' (contextual)",
                    text=word.text,
                ))
    return candidates
```

#### 3.2.4 策略 3：重复/口误检测 — 对齐 video-subtitle-cutter

```python
def _detect_repeat(self, transcript_words: list[WordTimestamp]) -> list[CutCandidate]:
    """
    检测重复和口误。
    对齐 video-subtitle-cutter 的：
      - REPETITIONS: 同词/短语重复（"really really really"）
      - FALSE STARTS: 不完整句子重启（交给策略 4 LLM 判断）
    """
    candidates = []
    # 单词重复
    for i in range(len(transcript_words) - 1):
        if transcript_words[i].text == transcript_words[i+1].text:
            candidates.append(CutCandidate(
                start=transcript_words[i].start,
                end=transcript_words[i+1].end,
                cut_type="repeat", confidence=0.85,
                reason=f"repeat '{transcript_words[i].text}{transcript_words[i+1].text}'",
                text=transcript_words[i].text + transcript_words[i+1].text,
            ))
    # 短语重复（2-4 词窗口）
    for window_size in range(2, 5):
        for i in range(len(transcript_words) - window_size * 2):
            phrase_a = " ".join(w.text for w in transcript_words[i:i+window_size])
            phrase_b = " ".join(w.text for w in transcript_words[i+window_size:i+window_size*2])
            if phrase_a == phrase_b:
                candidates.append(CutCandidate(
                    start=transcript_words[i].start,
                    end=transcript_words[i+window_size*2-1].end,
                    cut_type="repeat", confidence=0.8,
                    reason=f"repeat '{phrase_a} {phrase_b}'",
                    text=phrase_a + " " + phrase_b,
                ))
    return candidates
```

#### 3.2.5 策略 4：语义分析（LLM） — 对齐 video-subtitle-cutter

```python
async def _detect_fluff(self, transcript_segments: list[Segment]) -> list[CutCandidate]:
    """
    LLM 语义分析，识别废话/离题/false starts/连词类填充词。
    对齐 video-subtitle-cutter 的 Step 2 AI 分析 prompt。
    """
    from app.engine.llm_client import get_llm
    from app.adapters.llm_base import llm_prompt_to_text

    prompt = f"""分析以下口播片段，识别应删减的片段。

片段列表：
{json.dumps([{"i": i, "start": s.start, "end": s.end, "text": s.text} for i, s in enumerate(transcript_segments)], ensure_ascii=False)}

识别以下问题：
1. FALSE STARTS: 不完整句子重启（"我觉得—不对，应该是..."）
2. FLUFF: 废话/离题/冗余（与主题无关的闲聊）
3. CONNECTOR FILLERS: 连词/副词用作填充（"所以…就是说…"中的无意义连接）
4. 对于"短视频风格"：只保留 hook + 核心观点 + CTA

只输出 JSON：[{{"index": 0, "verdict": "keep"|"cut", "type": "false_start"|"fluff"|"connector_filler", "reason": "..."}}]"""

    llm = get_llm()
    result = await llm_prompt_to_text(llm, prompt)
    ...
```

#### 3.2.6 剪辑执行 — 对齐 video-subtitle-cutter + erm + 业界实践

**拼接方案选择**：

```
erm 的 acrossfade 方案只处理音频（播客场景），视频是硬切的。
ffmpeg 的 xfade 滤镜可以做视频交叉淡入淡出，但多片段拼接时
filter_complex 复杂度爆炸（N 个片段需要 N-1 次 xfade，
每次 offset 需要累减计算），且视频音频淡入淡出同步困难。

业界实际做法（edit-greek-reel、video-subtitle-cutter、AutoCut）：
  视频：concat demuxer 硬切（简单可靠）
  音频：每个片段首尾加短 afade（0.05-0.1s），消除咔嗒声
```

```python
async def _execute_cut(
    self,
    video_path: str,
    kept_segments: list[tuple[float, float]],
    pad: float = 0.25,       # 对齐 cutlist PAD=0.25s
    audio_fade: float = 0.08, # 音频淡入淡出时长（秒），对齐 erm 思想但简化
) -> str:
    """
    执行剪辑：逐段提取（含音频淡入淡出）→ concat 拼接。

    拼接策略（对齐业界实践）：
      - 视频：concat demuxer 硬切（对齐 video-subtitle-cutter / AutoCut）
      - 音频：每个片段首尾加 afade 消除咔嗒声（对齐 erm 的平滑思想，但简化实现）
      - 重编码：必须用 libx264 + aac（对齐 video-subtitle-cutter，-c copy 会花屏）
    """
    segment_files = []
    for i, (start, end) in enumerate(kept_segments):
        s = max(0, start - pad)
        duration = end - start + 2 * pad
        seg_path = tmp_dir / f"seg_{i:03d}.mp4"

        # 音频淡入淡出滤镜（对齐 erm 的平滑拼接思想）
        afade_in = f"afade=t=in:st=0:d={audio_fade}"
        afade_out = f"afade=t=out:st={duration - audio_fade}:d={audio_fade}"

        cmd = [
            "ffmpeg",
            "-ss", f"{s:.3f}",          # Seek BEFORE input（快速定位）
            "-i", video_path,
            "-t", f"{duration:.3f}",
            "-vf", "copy",              # 视频不额外处理
            "-af", f"{afade_in},{afade_out}",  # 音频首尾淡入淡出
            "-c:v", "libx264",           # 必须重编码（对齐 video-subtitle-cutter）
            "-preset", "fast",
            "-crf", "23",
            "-c:a", "aac",
            "-pix_fmt", "yuv420p",
            "-y", str(seg_path),
        ]
        await _run(cmd)
        segment_files.append(seg_path)

    # concat demuxer 拼接
    concat_file = tmp_dir / "concat.txt"
    concat_file.write_text("\n".join(f"file '{f.name}'" for f in segment_files))
    cmd = [
        "ffmpeg", "-f", "concat", "-safe", "0",
        "-i", str(concat_file),
        "-c", "copy",  # concat 后不需要再重编码
        "-y", output_path,
    ]
    await _run(cmd)
    return output_path
```

#### 3.2.7 Skill 完整签名

```python
class SmartCutInput(BaseModel):
    """智能剪辑输入参数。"""
    video_path: str = Field(description="视频文件路径")

    # 检测策略开关（零件可缺）
    detect_silence: bool = Field(default=True, description="检测静音/长停顿")
    detect_filler: bool = Field(default=True, description="检测填充词/结巴")
    detect_repeat: bool = Field(default=True, description="检测重复/口误")
    detect_fluff: bool = Field(default=False, description="LLM 检测废话/离题（需 LLM 调用）")

    # 阈值（对齐 cutlist 参数）
    silence_threshold_db: float = Field(default=-35, description="静音阈值 dB")
    silence_min_duration: float = Field(default=0.6, description="最短静音时长（秒），对齐 cutlist GAP_CUT")
    filler_pause: float = Field(default=0.35, description="指示词类填充词两侧最短停顿（秒），对齐 cutlist FILLER_PAUSE")
    pad_seconds: float = Field(default=0.25, description="切口两侧缓冲（秒），对齐 cutlist PAD")
    audio_fade: float = Field(default=0.08, description="音频淡入淡出时长（秒）")

    # 闸门信号（对齐 auto_short_video 的 refine_shots 模式）
    refine_candidates: list[dict] | None = Field(
        default=None,
        description="用户确认/修改后的删减列表（闸门确认信号）。None=首次调用走规划。",
    )
    force_final: bool = Field(default=False, description="跳过预览直接出片")


@register
class SmartCutSkill(Skill):
    node_type = "edit"
    name = "smart_cut"
    display_name = "智能剪辑"
    description = "自动删减口播中的结巴/填充词/错误/空白，只保留核心内容"
    skill_category = "edit"
    skill_priority = "hot"
    trigger_words = ["智能剪辑", "删减空白", "去掉结巴", "精简视频", "去掉嗯啊", "剪口播"]
    input_schema = SmartCutInput
```

---

### 3.3 叠加动画 Skill：talking_head_overlay

#### 3.3.1 叠加模板库 — 对齐 ossclip 的 Zod-typed scene library

ossclip 的核心设计：**LLM 不直接生成 HTML，而是从预定义的 scene library 中选择场景类型 + 填参数**。

我们用同样的模式，预定义叠加模板：

```python
class OverlayTemplate(str, Enum):
    """叠加模板类型 — 对齐 ossclip Zod-typed scene library。"""
    # 字幕类
    CAPTION_POP = "caption_pop"             # 逐词弹出（对齐 edit-greek-reel karaoke）
    CAPTION_KARAOKE = "caption_karaoke"     # 逐词高亮（对齐 SupoClip word-synced）
    CAPTION_TYPEWRITER = "caption_typewriter"  # 打字机效果

    # 图标类
    KEYWORD_ICON = "keyword_icon"           # 关键词弹出图标/emoji
    KEYWORD_HIGHLIGHT = "keyword_highlight"  # 关键词圈画/高亮

    # 数据类
    STAT_CARD = "stat_card"                 # 数据卡片（对齐 ossclip stat cards）
    NUMBER_ROLL = "number_roll"             # 数字滚动动画
    BAR_CHART_GROW = "bar_chart_grow"       # 柱状图生长动画

    # 标题类
    TITLE_CARD = "title_card"               # 标题卡（对齐 ossclip title cards）
    LOWER_THIRD = "lower_third"             # 下三分之一字幕条（对齐 OpenMontage）


class OverlaySegment(BaseModel):
    """单个叠加片段。"""
    segment_id: str
    time_start: float
    time_end: float
    template: OverlayTemplate               # 从模板库选，LLM 不自由生成
    params: dict[str, Any]                  # 模板参数（LLM 填）
    # caption_pop:    {"text": "增长200%", "font_size": 48, "color": "#FF6B6B"}
    # keyword_icon:   {"keyword": "增长", "icon": "📈", "animation": "fly_in_right"}
    # stat_card:      {"label": "增长率", "value": "200%", "icon": "📈"}
    # title_card:     {"title": "3个增长秘诀", "subtitle": ""}


class OverlayPlan(BaseModel):
    """叠加规划 — 闸门 1 的产物。"""
    video_id: str
    segments: list[OverlaySegment]
    needs_confirmation: bool = True
```

#### 3.3.2 HTML 渲染 — 对齐 HyperFrames 的 HTML→Video 思想 + Playwright 官方录屏 API

**⚠️ 性能关键：不用逐帧截图，用 Playwright 官方录屏 API**

```
逐帧截图方案（❌ 淘汰）：
  5s 片段 × 30fps = 150 帧 × 50ms/帧 = 7.5s
  60s 口播全程字幕 = 1800 帧 × 50ms/帧 = 90s
  → 不可接受

Playwright 录屏方案（✅ 采用）：
  方式 A：record_video_dir（Playwright v1.9+）
    → browser.new_context(record_video_dir="videos/", record_video_size={"width": 1080, "height": 1920})
    → 浏览器内运行动画 → context.close() 后自动保存 WebM
    → 60s 动画 ≈ 60s 录制（1:1 实时），比逐帧截图快 15-30 倍

  方式 B：screencast.start（Playwright v1.59+）
    → page.screencast.start(path="output.webm", fps=30)
    → 更精细控制：可设 fps、可同时 onFrame 回调做逐帧处理
    → 推荐：需要透明背景时用 onFrame 回调 + 自定义编码
```

**透明背景处理**：

```
Playwright 录屏输出的是不透明 WebM。
需要透明背景时，有两种方案：

方案 A（推荐）：CSS 背景透明 + Playwright 逐帧截图，但只截关键帧
  → 不是每帧都截，而是用 requestAnimationFrame 驱动
  → 每帧用 page.evaluate() 推进动画 + screenshot
  → 关键优化：用 CDP Page.startScreencast 而非 page.screenshot
    （CDP screencast 直接拿渲染缓冲区，比 screenshot 快 3-5 倍）

方案 B（简单）：不透明背景 + ffmpeg chromakey 抠像
  → HTML 模板用纯色背景（如 #00FF00）
  → Playwright 录屏输出不透明 WebM
  → ffmpeg chromakey 抠掉绿色 → 透明叠加层
  → 优点：录屏 API 速度，不需要逐帧
  → 缺点：绿色溢出（green spill），边缘可能有绿边
```

```python
async def _render_overlay_to_webm(
    self,
    segment: OverlaySegment,
    video_width: int,
    video_height: int,
) -> Path:
    """
    用 Playwright 渲染 HTML 动画 → WebM。
    对齐 HyperFrames 的 HTML→Video 核心思想，但用 Python Playwright 替代 Node.js Chrome。

    渲染方式（按透明度需求选择）：
      - 不需要透明背景：record_video_dir 录屏（最快）
      - 需要透明背景：chromakey 方案（录屏 + ffmpeg 抠绿）
    """
    html_template = OVERLAY_TEMPLATES[segment.template]
    html_content = html_template.render(
        width=video_width, height=video_height,
        duration=segment.time_end - segment.time_start,
        **segment.params,
    )

    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()

        # 方式 A：录屏 API（对齐 Playwright 官方文档 record_video_dir）
        context = await browser.new_context(
            viewport={"width": video_width, "height": video_height},
            record_video_dir=str(tmp_dir),
            record_video_size={"width": video_width, "height": video_height},
        )
        page = await context.new_page()
        await page.set_content(html_content)

        # 启动动画
        duration = segment.time_end - segment.time_start
        await page.evaluate(f"window.startAnimation({duration})")

        # 等待动画播完
        await page.wait_for_function("window.animationComplete", timeout=duration * 1000 + 2000)

        # 关闭 context 保存视频
        await context.close()
        video_path = await page.video.path()

        await browser.close()

    # 如果需要透明背景：ffmpeg chromakey 抠绿
    if needs_alpha:
        cmd = [
            "ffmpeg", "-i", str(video_path),
            "-vf", "chromakey=color=0x00FF00:similarity=0.01:blend=0.1",
            "-c:v", "libvpx-vp9",
            "-pix_fmt", "yuva420p",
            "-auto_alt_ref", "0",
            "-y", str(output_path),
        ]
        await _run(cmd)
    else:
        output_path = video_path

    return output_path
```

#### 3.3.3 合成 — 扩展现有 video_compose.py

```python
async def _compose_with_overlays(
    video_path: str,
    overlay_webms: list[tuple[float, float, Path]],  # (start, end, webm_path)
) -> str:
    """
    把叠加层 WebM 合成到口播视频上。
    扩展现有 video_compose.py 的 ffmpeg 合成能力。

    ffmpeg overlay 滤镜：
      ffmpeg -i base.mp4 -i overlay.webm
        -filter_complex "[0:v][1:v]overlay=0:0:enable='between(t,start,end)'"
        -c:v libx264 -c:a copy output.mp4
    """
    ...
```

---

### 3.4 闸门机制 — 对齐 ossclip analyze/produce + 本项目 auto_short_video

**闸门合并策略**（解决串联时 5 次确认太重的问题）：

```
独立使用 smart_cut：2 级闸门
  闸门 1：删减规划确认
  闸门 2：剪辑结果确认

独立使用 talking_head_overlay：3 级闸门
  闸门 1：叠加规划确认
  闸门 2：动画预览确认
  闸门 3：合成预览确认

串联使用（smart_cut → talking_head_overlay）：合并为 3 级闸门
  闸门 1：删减规划 + 叠加规划 一起确认（两个规划并行生成）
  闸门 2：动画预览确认
  闸门 3：最终合成确认（含删减执行 + 叠加合成）

  合并依据：ossclip 的 analyze 阶段就是一次性出所有 marker，
  不分"静音 marker"和"填充词 marker"两步确认。
  同理，删减规划和叠加规划可以并行出，一次确认。
```

```python
class GateMode(str, Enum):
    STANDALONE = "standalone"     # 独立使用，全闸门
    CHAINED = "chained"           # 串联使用，合并闸门
    AUTO_CONFIRM = "auto_confirm" # Agent 自动确认低风险闸门（仅保留最终确认）
```

#### SSE 事件扩展

```python
# 在 app/engine/protocol/events.py 中新增
class EventType(str, Enum):
    # ... 现有事件 ...

    # 智能剪辑闸门
    SMART_CUT_PLAN_READY = "smart_cut_plan_ready"         # 删减规划卡片
    SMART_CUT_PREVIEW_READY = "smart_cut_preview_ready"   # 剪辑预览

    # 叠加增强闸门
    OVERLAY_PLAN_READY = "overlay_plan_ready"             # 叠加规划卡片
    OVERLAY_PREVIEW_READY = "overlay_preview_ready"       # 动画预览
    OVERLAY_COMPOSE_PREVIEW = "overlay_compose_preview"   # 合成预览

    # 串联模式
    ENHANCE_PLAN_READY = "enhance_plan_ready"             # 删减+叠加规划合并卡片
    ENHANCE_PREVIEW_READY = "enhance_preview_ready"       # 动画预览
    ENHANCE_COMPOSE_PREVIEW = "enhance_compose_preview"   # 最终合成预览


class EndedReason(str, Enum):
    # ... 现有原因 ...
    SMART_CUT_PAUSED = "smart_cut_paused"                 # 等待用户确认删减
    OVERLAY_PAUSED = "overlay_paused"                     # 等待用户确认叠加
    ENHANCE_PAUSED = "enhance_paused"                     # 串联模式等待确认
```

---

### 3.5 错误处理与边界 case

```python
class SmartCutError(Exception):
    """智能剪辑错误。"""
    NO_AUDIO_TRACK = "no_audio_track"         # 视频无音轨，无法检测
    TRANSCRIPTION_FAILED = "transcription_failed"  # WhisperX 转录失败
    ALL_CUT = "all_cut"                       # 全部片段被标记为待删
    NO_CANDIDATES = "no_candidates"           # 没检测到任何待删片段
    FFMPEG_FAILED = "ffmpeg_failed"           # ffmpeg 执行失败
    OVERLAY_RENDER_TIMEOUT = "overlay_render_timeout"  # Playwright 渲染超时


async def _safe_execute_cut(self, ...):
    """带边界检查的剪辑执行。"""
    # 1. 检查音轨
    if not await _has_audio(video_path):
        return {"error": SmartCutError.NO_AUDIO_TRACK, "hint": "视频无音轨，无法执行智能剪辑"}

    # 2. 检查保留片段
    kept = self._compute_kept_segments(confirmed_candidates, total_duration)
    if not kept:
        return {"error": SmartCutError.ALL_CUT, "hint": "所有片段被标记删除，请取消部分勾选"}

    # 3. 检查保留时长
    kept_duration = sum(e - s for s, e in kept)
    if kept_duration < 1.0:
        return {"error": SmartCutError.ALL_CUT, "hint": f"保留时长仅 {kept_duration:.1f}s，请减少删减"}

    # 4. 叠加段时间范围检查
    for seg in overlay_segments:
        if seg.time_end > video_duration:
            seg.time_end = video_duration  # 截断到视频时长
        if seg.time_start >= seg.time_end:
            continue  # 跳过无效片段

    # 5. Playwright 渲染超时保护
    try:
        result = await asyncio.wait_for(
            self._render_overlay_to_webm(segment, ...),
            timeout=duration * 2 + 10,  # 最长等 2 倍时长 + 10s
        )
    except asyncio.TimeoutError:
        return {"error": SmartCutError.OVERLAY_RENDER_TIMEOUT, "hint": "动画渲染超时，请简化叠加效果"}
```

---

### 3.6 性能预估

基于开源项目 benchmark 数据（StarWhisper 2026、VoicePing）：

| 阶段 | 60s 口播视频预估耗时 | 依据 |
|------|---------------------|------|
| **WhisperX 转录** | 3-8s（GPU）/ 15-30s（CPU） | faster-whisper small 模型，NVIDIA GPU ~12x 实时，CPU ~4x 实时 |
| **ffmpeg silencedetect** | 1-2s | 纯音频分析，极快 |
| **填充词/重复检测** | <0.1s | 纯内存操作，O(n) |
| **LLM 语义分析** | 2-5s | 一次 LLM 调用 |
| **ffmpeg 剪辑执行** | 5-15s | 重编码耗时，取决于片段数和 CRF |
| **Playwright 渲染叠加层** | 5-10s/片段 | 录屏 API，1:1 实时 + 启动开销 |
| **ffmpeg overlay 合成** | 10-30s | 取决于叠加层数量和视频分辨率 |
| **smart_cut 全流程** | **10-30s（GPU）/ 25-50s（CPU）** | |
| **talking_head_overlay 全流程** | **30-60s**（5 个叠加片段） | |

**结论**：smart_cut 全流程在 30s 内可完成，用户等待可接受。
talking_head_overlay 因 Playwright 渲染较慢，需要异步执行 + 进度条。

---

### 3.7 和现有 auto_short_video 管线的集成

```
现有 auto_short_video 管线产出：

  auto_short_video 输出（asset_library）：
    - final.mp4         ← 成品视频（可喂给 smart_cut）
    - voice.mp3         ← 配音音频
    - sub.srt / sub.ass ← 字幕文件
    - storyboard.json   ← 分镜元数据

smart_cut 输入：
    - video_path: str   ← 直接用 final.mp4
    - 无需额外适配

smart_cut 输出（asset_library）：
    - cut_final.mp4     ← 剪辑后视频（可喂给 talking_head_overlay）
    - cut_plan.json     ← 删减规划（可回溯）
    - cut_report.json   ← 删减报告

talking_head_overlay 输入：
    - video_path: str   ← 用 cut_final.mp4 或原始 final.mp4
    - 无需额外适配

talking_head_overlay 输出（asset_library）：
    - enhanced_final.mp4  ← 最终增强视频
    - overlay_plan.json   ← 叠加规划（可回溯）
    - overlay_previews/   ← 动画预览文件

集成方式：
  1. 独立调用：用户拿到 auto_short_video 的 final.mp4 后，单独调用 smart_cut 或 talking_head_overlay
  2. 串联调用：Agent 按意图自动串联（"帮我精简并加上动画" → smart_cut → talking_head_overlay）
  3. 管线内嵌：在 auto_short_video 的 Step 4（合成成片）后追加 Step 4.5（可选增强）
```

---

### 3.8 文件结构

```
capabilities/video/
├── adapters/
│   ├── video_compose.py          ← 扩展：加 overlay 合成逻辑
│   ├── whisper_transcribe.py     ← 新增：WhisperX 逐词转写 adapter
│   ├── silence_detect.py         ← 新增：ffmpeg silencedetect 封装
│   └── html_to_video.py          ← 新增：Playwright 录屏 API → WebM
├── skills/
│   ├── auto_short_video.py       ← 现有
│   ├── smart_cut.py              ← 新增：智能剪辑 Skill
│   └── talking_head_overlay.py   ← 新增：叠加增强 Skill
├── services/
│   ├── video_orchestrator.py     ← 现有
│   ├── cut_analyzer.py           ← 新增：检测策略编排（4 策略合并去重）
│   └── overlay_orchestrator.py   ← 新增：叠加层编排服务
└── templates/                    ← 新增：HTML 叠加模板（对齐 ossclip scene library）
    ├── caption_pop.html
    ├── caption_karaoke.html
    ├── keyword_icon.html
    ├── stat_card.html
    ├── number_roll.html
    └── title_card.html
```

---

### 3.9 两个 Skill 的串联使用

```
口播原始视频
  ↓
smart_cut（智能剪辑）     ← 先删减垃圾，让内容紧凑
  ↓ 闸门：用户确认删减规划 → 确认剪辑结果
  ↓
talking_head_overlay（叠加增强）  ← 再加动画/字幕/图标，让内容丰富
  ↓ 闸门：用户确认叠加规划 → 确认动画预览 → 确认合成预览
  ↓
最终成片

串联模式（3 级闸门合并）：
  闸门 1：删减规划 + 叠加规划 一起确认
  闸门 2：动画预览确认
  闸门 3：最终合成确认
```

也可独立使用——Agent 根据用户意图决定调用哪个。

---

## 四、依赖与部署

### 4.1 依赖清单

| 依赖 | 用途 | 安装 | 零件可缺 |
|------|------|------|---------|
| ffmpeg | 已有 | 已有 | 必须 |
| WhisperX | 逐词转写 | 见 4.2 | 没装→降级到 ffmpeg silencedetect + 整句字幕 |
| Playwright | HTML→Video 渲染 | `pip install playwright && playwright install chromium` | 没装→降级到 ASS 字幕 |

### 4.2 WhisperX 在 Windows 上的安装指南

**问题**：WhisperX → faster-whisper → ctranslate2，在 Windows 上需要 CUDA/cuDNN，
直接 `pip install whisperx` 可能报 `Could not locate cudnn_ops64_9.dll`。

**解决方案**（按优先级）：

```
方案 A（推荐）：先装 CUDA 版 PyTorch，再装 faster-whisper
  pip3 install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124
  pip install faster-whisper whisperx
  → PyTorch CUDA 版自带所需的 cuDNN DLL，解决 Could not locate 报错
  → 依据：CSDN 实测解决方案

方案 B：用 whisper-cpp Python binding（对齐 ossclip）
  pip install whisper-cpp-python
  → whisper-cpp 是 C++ 编译，Windows 有预编译 release
  → 不依赖 ctranslate2，不碰 CUDA 依赖链
  → 依据：ossclip 用 whisper-cpp 而非 Python Whisper

方案 C：用 faster-whisper 的 standalone Windows executable
  pip install whisper-standalone-win
  → 预编译的 Windows CLI，不依赖 Python CUDA 生态
  → 依据：faster-whisper PyPI 页面推荐

方案 D（兜底）：CPU only
  pip install faster-whisper --no-deps
  pip install ctranslate2  # CPU only 版
  → 速度慢（~4x 实时 vs GPU ~12x 实时），但能跑
```

**性能参考**（StarWhisper 2026 benchmark）：

| 引擎 | 平台 | 模型 | 速度 |
|------|------|------|------|
| faster-whisper | NVIDIA GPU + CUDA | small | ~12x 实时（60s 音频 ≈ 5s 转录） |
| faster-whisper | CPU only | small | ~4x 实时（60s 音频 ≈ 15s 转录） |
| whisper-cpp | NVIDIA GPU + CUDA | small | ~10x 实时 |
| whisper-cpp | CPU only | small | ~1x 实时（60s 音频 ≈ 60s 转录） |

---

## 五、开发路线图

```
Phase 1（1-2 天）：智能剪辑基础
  - silence_detect adapter（ffmpeg silencedetect）
  - smart_cut Skill 骨架 + 静音检测 + 纯语气词匹配
  - 闸门 1：删减规划确认
  → 效果：口播视频自动删减静音和嗯啊
  → 依赖：仅 ffmpeg（零新依赖）

Phase 2（2-3 天）：智能剪辑完整
  - whisper_transcribe adapter（WhisperX 逐词时间戳）
  - 指示词类填充词检测 + 重复检测 + LLM 语义分析
  - 音频 afade 平滑拼接
  - 闸门 2：剪辑结果确认
  → 效果：完整智能剪辑
  → 依赖：+ WhisperX

Phase 3（2-3 天）：叠加增强基础
  - HTML 模板库（caption_pop + keyword_icon）
  - html_to_video adapter（Playwright 录屏 API）
  - talking_head_overlay Skill 骨架
  - 闸门 1-2：规划确认 + 动画预览确认
  → 效果：口播视频 + 逐词弹出字幕 + 关键词图标
  → 依赖：+ Playwright

Phase 4（3-5 天）：叠加增强完整
  - 更多模板（stat_card + number_roll + title_card）
  - overlay_orchestrator（多叠加层时序编排）
  - 闸门 3：合成预览确认
  - video_compose 扩展（overlay 合成）
  - 串联模式 + 闸门合并
  → 效果：完整口播增强视频
```

---

## 六、开源项目参考索引

| 项目 | GitHub | 核心参考点 |
|------|--------|-----------|
| ossclip | CodeWithAhsan/ossclip | analyze/produce 分离、marker 文件、Zod scene library、EDL 导出、whisper-cpp |
| video-subtitle-cutter | different-ai/agent-bank | 4 类检测 prompt、ffmpeg 重编码、Skill 模板 |
| fillercut | pypi/fillercut | VAD + 填充词、GAP_CUT/PAD/FILLER_PAUSE 参数 |
| erm | pypi/erm | acrossfade 自适应淡入淡出、stray sounds 检测 |
| AutoCut | mli/autocut | 字幕驱动剪辑、MD 编辑隐喻 |
| FunClip | alibaba-damo-academy/FunClip | Paraformer 中文识别、说话人分离 |
| edit-greek-reel | artemisln/edit-greek-reel | 3 阶段流水线、karaoke 字幕、zoom + overlay |
| SupoClip | supoclip.com | 人脸裁切、virality scoring、word-synced subs |
| Playwright | playwright.dev/python | record_video_dir 录屏 API、screencast.start（v1.59+） |
| StarWhisper | starwhisper.ai | Whisper 速度 benchmark（Windows GPU/CPU） |
| 中研院论文 | ling.sinica.edu.tw | 中文填充词语音特征（simple vs morphologically derived） |
| SUD Guidelines | surfacesyntacticud.org | 中文填充词语法标注（那个/这个/就是 的双重用法） |
| stable-ts | jianfch/stable-ts | VAD 抑制静音、时间戳修正、suppress_silence 参数 |
| outputguard | pypi/outputguard | LLM JSON 输出修复、schema 校验、重试策略 |
| hyperframes-best-practices | anton-abyzov-vskill-hyperframes | source-time → cut-time 映射、overlay anchor 重算 |

---

## 七、隐患与对策（基于开源项目真实方案）

> 以下 14 条隐患均来自实际工程经验，每条都给出**开源项目的真实解法**，
> 不是理论建议，而是可落地的代码级方案。

### 7.1 依赖链与平台兼容性

#### 隐患 #1：WhisperX / faster-whisper / ctranslate2 的 Windows 运行时稳定性

**问题**：ctranslate2 的 CUDA 绑定对驱动版本敏感，用户升级显卡驱动后可能突然崩掉
（`cublas64_12.dll not found`），且报错信息极其晦涩。这不是"装不上"的一次性问题，
而是"装上了但某天突然不能用了"的间歇性故障。

**开源解法：stable-ts 的 VAD + 时间戳修正**

[stable-ts](https://pypi.org/project/stable-ts/) 是 Whisper 时间戳修正库，核心功能：
- **Silero VAD 预处理**：在 Whisper 解码前用 VAD 去掉静音段，减少 Whisper 在静音上产生幻觉词
- **suppress_silence=True（默认开启）**：解码后把时间戳向有声区域收缩，消除静音段上的虚假时间戳
- **vad_threshold=0.35**：VAD 检测阈值，降低=更宽松（捕捉轻声），提高=更严格（漏检）

```python
# 用 stable-ts 替代裸 faster-whisper，解决时间戳精度问题
import stable_whisper

model = stable_whisper.load_model('small')
result = model.transcribe(
    audio_path,
    vad=True,                  # 启用 Silero VAD 预处理
    suppress_silence=True,     # 把时间戳收缩到有声区域
    vad_threshold=0.35,        # VAD 阈值（对齐 stable-ts 默认值）
)
# result.segments 包含修正后的 word-level timestamps
```

**额外防护**：

```python
# 启动时做 CUDA 可用性检查，提前暴露问题而非运行时崩溃
def _check_cuda_available() -> dict:
    """检查 CUDA 环境，返回诊断信息。"""
    import torch
    info = {
        "cuda_available": torch.cuda.is_available(),
        "cuda_version": torch.version.cuda if torch.cuda.is_available() else None,
        "cudnn_version": torch.backends.cudnn.version() if torch.cuda.is_available() else None,
        "gpu_name": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
    }
    if not info["cuda_available"]:
        info["fallback"] = "CPU only mode (4x slower)"
    return info
```

---

#### 隐患 #2：Playwright Chromium 版本漂移导致录屏输出不一致

**问题**：Playwright 绑定特定 Chromium 版本，每次 `pip install -U playwright`
拉新版浏览器后，`record_video_dir` 的输出编码参数可能有微小差异，
导致后续 ffmpeg 合成时偶发音视频不同步。

**开源解法：Playwright 官方推荐 — Browser 单例 + Context 池**

[Playwright 官方文档](https://playwright.dev/python/docs/browser-contexts) 明确推荐：
> "Instead of launching a new browser for every test, reuse a single browser instance."

```python
# Playwright 浏览器单例 — 避免每次渲染都 launch 新进程
class PlaywrightSingleton:
    """Playwright 浏览器单例 + Context 池。
    对齐 Playwright 官方最佳实践和 scrapingcentral.com 的 browser pool 模式。
    """
    _instance = None
    _browser = None
    _lock = asyncio.Lock()

    @classmethod
    async def get_browser(cls):
        """获取浏览器实例（懒加载单例）。"""
        async with cls._lock:
            if cls._browser is None:
                from playwright.async_api import async_playwright
                pw = await async_playwright().start()
                cls._browser = await pw.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-gpu"],  # CI 环境兼容
                )
                cls._pw = pw  # 保持引用防止 GC
            return cls._browser

    @classmethod
    async def new_context(cls, record_dir: str, size: tuple[int, int]):
        """创建新 context（轻量级，每个渲染任务一个）。"""
        browser = await cls.get_browser()
        context = await browser.new_context(
            viewport={"width": size[0], "height": size[1]},
            record_video_dir=record_dir,
            record_video_size={"width": size[0], "height": size[1]},
        )
        return context

    @classmethod
    async def cleanup(cls):
        """清理资源（Skill 结束时调用）。"""
        if cls._browser:
            await cls._browser.close()
            cls._browser = None
        if hasattr(cls, '_pw') and cls._pw:
            await cls._pw.stop()
```

**版本锁定**：

```python
# requirements.txt 中锁定 Playwright 版本
playwright==1.49.0  # 锁定版本，避免自动升级导致行为变化
# 配合 CI 中的 playwright install chromium --with-deps 确保一致性
```

---

### 7.2 转录质量与检测精度的连锁误差

#### 隐患 #3：Whisper 逐词时间戳的随机误差传导到填充词检测

**问题**：Whisper 的 word-level timestamp 本质是估算值，中文场景下误差 50-200ms 很常见。
文档中 `FILLER_PAUSE=0.35s` 的阈值依赖词间间隔的精确性，但 200ms 的 timestamp 误差意味着：
- 实际间隔 0.3s 的填充词可能被判为 0.5s（误删）
- 实际间隔 0.5s 的正常停顿可能被判为 0.3s（漏删）

**开源解法 #1：stable-ts suppress_silence 收缩时间戳**

stable-ts 的 `suppress_silence=True` 会把时间戳向有声区域收缩，
显著减少静音边缘的时间戳飘移。根据 [whipscribe.com 测试数据](https://whipscribe.com/tools/stable-ts)，
开启 VAD + suppress_silence 后，时间戳平均误差从 100-200ms 降到 40-60ms。

**开源解法 #2：误差容忍区间（conservative threshold）**

```python
# 给 Whisper 时间戳加 ±100ms 误差容忍区间
TIMESTAMP_TOLERANCE_MS = 100  # ms，对齐 faster-whisper benchmark 数据

def _effective_gap(prev_end: float, next_start: float) -> float:
    """
    计算有效间隔，扣除时间戳误差。
    如果两个词的实际间隔是 0.3s，但时间戳各有 ±100ms 误差，
    则观测到的间隔可能在 0.1s ~ 0.5s 之间。
    我们取保守值：有效间隔 = 观测间隔 - 2 * 容忍区间。
    """
    observed_gap = next_start - prev_end
    tolerance_s = TIMESTAMP_TOLERANCE_MS / 1000.0
    effective = observed_gap - 2 * tolerance_s
    return max(0, effective)

# 使用示例：第二级填充词检测时用 effective gap 判断
if _effective_gap(prev_word.end, word.start) >= FILLER_PAUSE:
    # 标记为填充词（保守判断，减少误删）
    ...
```

**开源解法 #3：短词（<200ms）降权或跳过**

```python
MIN_WORD_DURATION_MS = 200  # ms，低于此时长的词时间戳极不准

def _detect_filler_with_confidence(self, words: list[WordTimestamp]) -> list[CutCandidate]:
    """带置信度衰减的填充词检测。"""
    candidates = []
    for i, word in enumerate(words):
        duration_ms = (word.end - word.start) * 1000

        # 短词时间戳不准，降低置信度
        if duration_ms < MIN_WORD_DURATION_MS:
            confidence_penalty = 0.3  # 置信度扣 30%
        else:
            confidence_penalty = 0.0

        # ... 其余检测逻辑 ...
        candidate.confidence = max(0, base_confidence - confidence_penalty)
    return candidates
```

---

#### 隐患 #4：中文填充词的语境歧义 — "那个""这个"连说时无停顿

**问题**："那个那个"连着说时中间没有 0.35s 间隔，第二级检测会漏掉。
"就是说"在"就是说这个道理"中是正常连接，三字窗口不足以区分。

**开源解法：扩大检测窗口 + 连续填充词合并**

```python
# 扩大检测窗口：不只看单个词，看 2-3 词组合
FILLER_PHRASES = {
    "那个那个", "这个这个", "就是说", "然后呢", "你知道吗",
    "you know", "I mean", "like like", "basically basically",
}

def _detect_filler_phrases(self, words: list[WordTimestamp]) -> list[CutCandidate]:
    """短语级填充词检测（解决连说无停顿问题）。"""
    candidates = []
    for phrase in FILLER_PHRASES:
        phrase_words = phrase.split()
        # 在词序列中搜索该短语
        for i in range(len(words) - len(phrase_words) + 1):
            window = [w.text.strip().lower() for w in words[i:i+len(phrase_words)]]
            if window == phrase_words:
                # 找到短语匹配，标记整个短语
                candidates.append(CutCandidate(
                    start=words[i].start,
                    end=words[i+len(phrase_words)-1].end,
                    cut_type="filler",
                    confidence=0.8,  # 短语匹配置信度高于单词
                    reason=f"filler phrase '{phrase}'",
                    text=phrase,
                ))
    return candidates
```

**LLM 兜底**：对于第三级连词/副词类，LLM prompt 中明确要求输出"不确定"标记：

```python
prompt += """
对于无法确定的片段，verdict 设为 "uncertain"，type 设为 "maybe_filler"。
用户会在闸门界面看到黄色标记（非强制删除），自行决定。
"""
```

---

### 7.3 ffmpeg 拼接的音视频同步问题

#### 隐患 #5：多段 concat 拼接的累积时间偏移

**问题**：ffmpeg `-ss` seek + `-t` duration 逐段提取再 concat demuxer 拼接时，
每段重编码的起始时间精度有微小偏移（关键帧对齐误差）。偏移是累积的：
第 1 段偏 5ms，第 10 段可能偏 50ms。

**开源解法：PTS 归零 + avoid_negative_ts make_zero**

根据 [ffmpeg-cookbook invalid PTS fix](https://ffmpeg-cookbook.com/en/articles/invalid-pts-fix/)
和 [TechOverflow concat AV1 方案](https://techoverflow.net/2026/09/05/how-to-concatenate-av1-videos-in-ffmpeg-without-losing-frames/)：

```python
async def _execute_cut_with_pts_fix(self, video_path, kept_segments, pad, audio_fade):
    """带 PTS 归零的剪辑执行，解决累积时间偏移。"""
    segment_files = []
    for i, (start, end) in enumerate(kept_segments):
        s = max(0, start - pad)
        duration = end - start + 2 * pad
        seg_path = tmp_dir / f"seg_{i:03d}.mp4"

        afade_in = f"afade=t=in:st=0:d={audio_fade}"
        afade_out = f"afade=t=out:st={duration - audio_fade}:d={audio_fade}"

        cmd = [
            "ffmpeg",
            "-ss", f"{s:.3f}",
            "-i", video_path,
            "-t", f"{duration:.3f}",
            "-vf", "copy",
            "-af", f"{afade_in},{afade_out}",
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "23",
            "-c:a", "aac",
            "-pix_fmt", "yuv420p",
            # 关键：归零 PTS，避免累积偏移
            "-fflags", "+genpts",
            "-y", str(seg_path),
        ]
        await _run(cmd)
        segment_files.append(seg_path)

    # concat 时强制时间轴从 0 开始
    concat_file = tmp_dir / "concat.txt"
    concat_file.write_text("\n".join(f"file '{f.name}'" for f in segment_files))
    cmd = [
        "ffmpeg", "-f", "concat", "-safe", "0",
        "-i", str(concat_file),
        # 关键：负时间戳归零，解决拼接处 DTS 不单调警告
        "-avoid_negative_ts", "make_zero",
        "-c", "copy",
        "-y", output_path,
    ]
    await _run(cmd)
    return output_path
```

**性能参考**：根据 [ffmpeg-micro.com 测试](https://www.ffmpeg-micro.com/blog/non-monotonous-dts-isn-t-noise-it-s-truncating-your-joins)，
`-avoid_negative_ts make_zero` 能完全消除 concat 接缝处的 DTS 警告和时间轴抖动。

---

#### 隐患 #6：音频 afade + 视频硬切的"半截嘴"感知不一致

**问题**：删填充词时画面跳变但声音只淡了 80ms，人眼会注意到嘴型还在动
但声音已经没了的"半截嘴"效果。

**开源解法：视频也加极短 fade（2-3 帧）**

```python
# 视频也加极短 fade，和音频 afade 同步
VIDEO_FADE_FRAMES = 3  # 30fps 下 = 0.1s，人眼几乎不可感知但消除跳变感

async def _extract_segment_with_dual_fade(self, video_path, start, duration, audio_fade, tmp_dir, idx):
    """音视频同步淡入淡出的片段提取。"""
    seg_path = tmp_dir / f"seg_{idx:03d}.mp4"

    # 视频淡入淡出（极短，消除跳变感）
    vfade_in = f"fade=t=in:st=0:d={VIDEO_FADE_FRAMES/30:.3f}"
    vfade_out = f"fade=t=out:st={duration - VIDEO_FADE_FRAMES/30:.3f}:d={VIDEO_FADE_FRAMES/30:.3f}"

    # 音频淡入淡出
    afade_in = f"afade=t=in:st=0:d={audio_fade}"
    afade_out = f"afade=t=out:st={duration - audio_fade}:d={audio_fade}"

    cmd = [
        "ffmpeg",
        "-ss", f"{start:.3f}",
        "-i", video_path,
        "-t", f"{duration:.3f}",
        "-vf", f"{vfade_in},{vfade_out}",       # 视频双 fade
        "-af", f"{afade_in},{afade_out}",         # 音频双 fade
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-c:a", "aac",
        "-pix_fmt", "yuv420p",
        "-fflags", "+genpts",
        "-y", str(seg_path),
    ]
    await _run(cmd)
    return seg_path
```

**效果**：0.1s（3 帧 @30fps）的视频 fade 人眼几乎不可感知，
但足以消除"画面突变+声音渐隐"的不协调感。这是 [ffmpeg-cookbook fade 文档](https://ffmpeg-cookbook.com/en/articles/fade-crossfade/)
推荐的短视频过渡参数范围。

---

### 7.4 Playwright 渲染叠加层的工程隐患

#### 隐患 #7：录屏 API 输出 WebM 的帧率不可控

**问题**：`record_video_dir` 输出 WebM（VP8/VP9 编码），帧率受系统负载影响，
高负载时可能降到 20-25fps，导致动画时长比预期长。

**开源解法：WebM VP8/VP9 支持 alpha 通道（yuva420p）**

根据 [MDN Web Video Codec Guide](https://developer.mozilla.org/en-US/docs/Web/Media/Guides/Formats/Video_codecs)
和 [WebM Project Alpha Channel Wiki](https://wiki.webmproject.org/alpha-channel)：

> "VP8 allows video with an alpha channel, allowing the video to play with the background able to be seen through."

**关键发现**：WebM VP8/VP9 **原生支持 alpha 通道**（像素格式 `yuva420p`），
不需要 chromakey 抠绿！Playwright 录屏可以直接输出透明背景 WebM。

```python
async def _render_overlay_to_webm_alpha(self, segment, video_width, video_height, tmp_dir):
    """
    用 Playwright 渲染透明背景叠加层 → 带 alpha 的 WebM。
    无需 chromakey，直接利用 VP8 alpha 通道支持。
    """
    html_content = OVERLAY_TEMPLATES[segment.template].render(
        width=video_width, height=video_height,
        duration=segment.time_end - segment.time_start,
        bg_color="transparent",  # HTML 背景设为透明
        **segment.params,
    )

    context = await PlaywrightSingleton.new_context(str(tmp_dir), (video_width, video_height))
    page = await context.new_page()
    await page.set_content(html_content)

    duration = segment.time_end - segment.time_start
    await page.evaluate(f"window.startAnimation({duration})")
    await page.wait_for_function("window.animationComplete", timeout=duration * 1000 + 2000)

    await context.close()

    # 获取录制的 WebM 路径
    video_path = await page.video.path()

    # 转码为 yuva420p（带 alpha 的 WebM）
    output_path = tmp_dir / f"overlay_{segment.segment_id}.webm"
    cmd = [
        "ffmpeg", "-i", str(video_path),
        "-c:v", "libvpx-vp9",      # VP9 编码器
        "-pix_fmt", "yuva420p",     # 带alpha通道的像素格式
        "-auto_alt_ref", "0",       # 禁用自动 alt ref（alpha 通道需要）
        "-crf", "30",
        "-b:v", "0",               # CRF 模式
        "-y", str(output_path),
    ]
    await _run(cmd)

    return output_path
```

**注意**：根据 [DEV Community 测试](https://dev.to/_df88eb1337eb23b75c94ba/why-your-transparent-video-turns-black-i-tested-webm-alpha-prores-4444-and-hevc-alpha-in-chrome-52po)，
Chrome/Edge 对 WebM alpha 支持良好，Safari 不支持。我们的场景是 ffmpeg 合成
（不是浏览器播放），所以完全可用。

---

#### 隐患 #8：chromakey 抠绿的绿边问题（备用方案）

**问题**：如果因为某些原因不能用 alpha 通道 WebM（如旧版 Playwright），
需要 chromakey 抠绿，但 HTML 渲染的文字边缘有亚像素抗锯齿，
半透明像素抠绿后会变成绿色晕边。

**开源解法：chromakey + despill 组合滤镜**

根据 [ffmpeg-cookbook chromakey 文档](https://ffmpeg-cookbook.com/en/articles/chromakey-filter/)：

> "Green light from the screen reflects onto skin and hair. This is called spill.
> Add the `despill` filter, or lower the saturation of that hue slightly after the key."

```python
# chromakey + despill 组合（消除绿边）
cmd = [
    "ffmpeg", "-i", str(video_path),
    "-vf", (
        "chromakey=color=0x00FF00:similarity=0.02:blend=0.05,"  # 抠绿
        "despill=type=green:expand=0.1"                           # 去绿溢出
    ),
    "-c:v", "libvpx-vp9",
    "-pix_fmt", "yuva420p",
    "-auto_alt_ref", "0",
    "-y", str(output_path),
]
await _run(cmd)
```

**参数说明**（来自 [ffmpeg despill 源码](https://ffmpeg.org/doxygen/trunk/vf__despill_8c_source.html)）：
- `despill=type=green`：去除绿色溢出
- `expand=0.1`：扩展去溢出范围到边缘像素

**优先级**：alpha 通道方案 > chromakey+despill 方案。只有当 Playwright 版本不支持
透明背景录制时才用 chromakey 兜底。

---

### 7.5 LLM 语义分析的可靠性

#### 隐患 #9：LLM 返回格式不稳定（JSON 解析失败）

**问题**：LLM（尤其是国产模型）在中文口播文本上输出格式不稳定：
可能多换行、把 `"cut"` 写成 `"删除"`、JSON 外面包 markdown。

**开源解法：outputguard 库 — 15 种修复策略 + schema 校验 + 重试**

[outputguard](https://pypi.org/project/outputguard/) 是专门解决 LLM 输出格式问题的库：

```python
import outputguard
from pydantic import BaseModel

class LLMVerdict(BaseModel):
    index: int
    verdict: Literal["keep", "cut", "uncertain"]
    type: Literal["false_start", "fluff", "connector_filler"] | None
    reason: str

async def _detect_fluff_with_guard(self, transcript_segments):
    """带 outputguard 校验 + 重试的 LLM 语义分析。"""
    llm = get_llm()
    result_text = await llm_prompt_to_text(llm, prompt)

    # outputguard 自动修复常见格式问题
    guard = outputguard.OutputGuard(schema=LLMVerdict)
    MAX_RETRIES = 2  # 对齐 SitePoint 推荐（2-3 次重试）

    for attempt in range(MAX_RETRIES + 1):
        try:
            # 尝试解析 + 校验
            parsed = guard.validate(result_text)
            return parsed  # 成功，返回结构化数据
        except outputguard.ValidationError as e:
            if attempt < MAX_RETRIES:
                # 重试：把错误信息喂回 LLM 让它修正
                retry_prompt = f"""上次输出格式有误：{e}
请严格按 JSON 格式重新输出，不要添加任何额外文字。
原始请求：{prompt}"""
                result_text = await llm_prompt_to_text(llm, retry_prompt)
            else:
                # 重试耗尽，静默降级
                logger.warning(f"LLM 输出格式校验失败，已重试 {MAX_RETRIES} 次，跳过语义分析")
                return []  # 返回空列表，不阻塞流程
```

**重试预算控制**（对齐 [技术栈文章](https://jishuzhan.net/article/2095335366442799105)）：
- 重试 1 次 ≈ 2 倍输出 token 消耗
- 重试 2 次 ≈ 3 倍输出 token 消耗
- 建议：最多 2 次重试，超过则降级跳过

---

#### 隐患 #10：LLM 对口播文本的"过度删减"倾向

**问题**：LLM 对口语化表达的容忍度低于人类，可能把 60s 口播砍成 30s，
丢失语气和节奏。

**开源解法 #1：Subedit 的 aggression 预设模式**

[Subedit](https://subedit.app/features) 的 Auto Cut 功能提供 Three aggression presets：
- Safe（安全）：只删明显错误
- Balanced（平衡）：默认
- Aggressive（激进）：最大程度精简

```python
class AggressionLevel(str, Enum):
    SAFE = "safe"           # 只删 silence + 第一级填充词（嗯/啊）
    BALANCED = "balanced"   # + 第二级填充词 + 重复 + LLM fluff
    AGGRESSIVE = "aggressive"  # + 更激进的 LLM 删减（短视频风格）

# LLM prompt 根据 aggression level 动态调整
AGGRESSION_PROMPTS = {
    AggressionLevel.SAFE: "只保留明显的语音错误（结巴、长停顿），保留所有正常语流",
    AggressionLevel.BALANCED: "删除填充词和冗余，保留节奏和语气",
    AggressiveLevel.AGGRESSIVE: "短视频风格：只保留 hook + 核心观点 + CTA，其余可删",
}
```

**开源解法 #2：PremiereGPT Claude Cut 的"不按时长裁剪"原则**

[PremiereGPT docs](https://docs.premierecopilot.com/features/claude-cut) 明确指出：
> "Hit a target duration: 'Cut it down to 5 minutes' will not work —
> the AI removes what's removable, not a quota."

我们在 prompt 中加入类似约束：

```python
prompt += """
重要约束：
1. 不要为了缩短时长而删减——只删除真正有问题的地方
2. 保留口播的自然节奏和语气
3. 如果一段话虽然"废话"但有情感铺垫作用，标记为 uncertain 而非 cut
4. 目标删减比例不超过原时长的 30%（除非用户明确要求更激进）
"""
```

**闸门兜底**：即使 LLM 过度删减，用户在闸门界面可以批量取消勾选。
UI 设计上提供"保留全部""恢复默认""只保留高置信度"快捷操作。

---

### 7.6 与现有 auto_short_video 管线的集成摩擦

#### 隐患 #11：串联时的中间产物时间轴错位

**问题**：auto_short_video 的 `final.mp4` 是多段 concat 产物，
其时间轴和原始口播的时间轴已经不同（加了 ken-burns、叠了字幕）。
如果拿合成后的视频跑 smart_cut，Whisper 转录的是合成后的音轨（可能含 BGM）。

**开源解法：hyperframes 的 source-time → cut-time 映射**

[hyperframes-best-practices](https://lobehub.com/skills/anton-abyzov-vskill-hyperframes-best-practices)
明确指出这个问题并给出方案：

> "After every re-cut, the cumulative durations shift slightly.
> Recompute the mapping and re-resolve every overlay anchor before rendering."

```python
class TimeAxisMapper:
    """
    源时间轴 → 剪辑后时间轴的双向映射。
    解决串联模式下 smart_cut 输出和原始视频的时间轴错位问题。
    """

    def __init__(self, kept_segments: list[tuple[float, float]]):
        """
        构建映射表。
        kept_segments: 保留片段的 [(src_start, src_end), ...] 列表
        """
        self.mapping = []  # [{src_start, src_end, cut_start, cut_end}, ...]
        cum = 0.0
        for s, e in kept_segments:
            self.mapping.append({
                "src_start": s,
                "src_end": e,
                "cut_start": cum,
                "cut_end": cum + (e - s),
            })
            cum += (e - s)
        self.total_cut_duration = cum

    def src_to_cut(self, src_t: float) -> float | None:
        """源时间 → 剪辑后时间。如果源时刻被删了，返回 None。"""
        for m in self.mapping:
            if m["src_start"] <= src_t <= m["src_end"]:
                return m["cut_start"] + (src_t - m["src_start"])
        return None  # 该时刻被删掉了

    def cut_to_src(self, cut_t: float) -> float | None:
        """剪辑后时间 → 源时间。"""
        for m in self.mapping:
            if m["cut_start"] <= cut_t <= m["cut_end"]:
                return m["src_start"] + (cut_t - m["cut_start"])
        return None


# 使用场景：smart_cut 输出后，talking_head_overlay 的叠加锚点需要重算
def _remap_overlay_anchors(
    overlay_plan: OverlayPlan,
    time_mapper: TimeAxisMapper,
) -> OverlayPlan:
    """
    把 overlay_plan 中的时间锚点从源时间轴映射到剪辑后时间轴。
    如果某个 overlay 的源时刻被删了，标记为 skipped。
    """
    for seg in overlay_plan.segments:
        new_start = time_mapper.src_to_cut(seg.time_start)
        new_end = time_mapper.src_to_cut(seg.time_end)

        if new_start is None or new_end is None:
            seg.skipped = True  # 该叠加段的源时刻被删了
            logger.info(f"Overlay {seg.segment_id} skipped (source time cut)")
        else:
            seg.time_start = new_start
            seg.time_end = new_end

    return overlay_plan
```

**集成策略**：

```python
# auto_short_video 输出时保存原始口播时间轴映射关系
class AutoShortVideoOutput(BaseModel):
    final_mp4: str
    # 新增：时间轴映射（如果有做 ken-burns/变速等时间轴变换）
    timeline_mapping: list[dict] | None = None  # [{"src": (0,60), "out": (0,58)}, ...]
    original_audio_path: str | None = None      # 原始音轨路径（无 BGM）

# smart_cut 输入时选择检测对象
class SmartCutInput(BaseModel):
    video_path: str
    # 新增：用于检测的音频路径（优先用无 BGM 的原始音轨）
    detection_audio_path: str | None = None
    # 新增：上游时间轴映射（如果有）
    upstream_timeline_mapping: list[dict] | None = None
```

---

#### 隐患 #12：闸门间隐式依赖 — 用户修改叠加规划后删减规划可能失效

**问题**：串联模式闸门 1 同时确认删减规划和叠加规划。
如果用户在闸门 2 修改了叠加规划（比如删掉了一个叠加段），
这不会影响删减规划。但如果用户在闸门 1 修改了删减规划（取消了一些删减），
叠加规划的锚点需要重新计算。

**开源解法：ossclip 的 clip-level marker 思想**

[ossclip 文档](https://ahsanayaz.github.io/ossclip/) 指出：
> "Markers arrive at the sequence level and on the clip itself —
> the clip-level ones are anchored to the footage, so they stay on the right words
> through your own razor cuts and ripple deletes."

**实现**：叠加规划的锚点存储为"源时间轴坐标"，每次用户修改删减规划后，
自动重算叠加锚点到剪辑后时间轴的映射。

```python
async def handle_gate1_confirmation(self, user_input):
    """处理闸门 1 确认（串联模式：删减+叠加规划一起确认）。"""
    confirmed_cuts = user_input.confirmed_candidates
    confirmed_overlays = user_input.confirmed_overlays

    # 1. 计算保留片段
    kept_segments = self._compute_kept_segments(confirmed_cuts, total_duration)

    # 2. 构建时间轴映射
    time_mapper = TimeAxisMapper(kept_segments)

    # 3. 重算叠加锚点（关键！用户修改了删减规划，叠加锚点必须跟着变）
    remapped_overlays = _remap_overlay_anchors(confirmed_overlays, time_mapper)

    # 4. 过滤掉被删掉的叠加段
    active_overlays = [o for o in remapped_overlays if not getattr(o, 'skipped', False)]

    # 5. 继续后续流程
    return {
        "kept_segments": kept_segments,
        "time_mapper": time_mapper,  # 传递给后续阶段
        "active_overlays": active_overlays,
    }
```

---

### 7.7 性能与资源

#### 隐患 #13：临时文件爆炸未清理

**问题**：smart_cut 逐段提取 N 个 MP4，talking_head_overlay 渲染 M 个 WebM，
chromakey 再产生 M 个透明 WebM。60s 口播切 15 刀 + 5 个叠加段 = 25 个临时文件。

**开源解法：Python tempfile.TemporaryDirectory 上下文管理器**

根据 [Python 官方文档](https://docs.python.org/id/3.13/library/tempfile.html)
和 [RuneBook 指南](https://runebook.dev/en/docs/python/library/tempfile/tempfile.TemporaryDirectory)：

> "The best and most Pythonic way to handle temporary directories is to use
> the `tempfile.TemporaryDirectory` class. This uses a context manager (`with` statement)
> which handles cleanup for you, even if exceptions occur!"

```python
import tempfile
from contextlib import asynccontextmanager

@asynccontextmanager
async def managed_temp_dir(prefix="video_enhance_"):
    """
    临时目录上下文管理器。
    Skill 结束时（无论成功/失败/异常）自动清理所有临时文件。
    """
    tmp_dir = tempfile.mkdtemp(prefix=prefix)
    try:
        yield Path(tmp_dir)
    finally:
        # 无论正常退出还是异常，都清理临时目录
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)
        logger.debug(f"Cleaned up temp dir: {tmp_dir}")


# Skill 中使用
class SmartCutSkill(Skill):
    async def execute(self, input_data: SmartCutInput):
        async with managed_temp_dir() as tmp_dir:
            # 所有临时文件都在 tmp_dir 下
            result = await self._do_cut(input_data, tmp_dir)
            # 退出 with 块时自动清理
            return result
```

**磁盘空间预估**（60s 口播，1080x1920）：

| 临时文件类型 | 数量 | 单个大小 | 总计 |
|-------------|------|---------|------|
| 剪辑片段 MP4 | 15 | 2-5 MB | 30-75 MB |
| 叠加层 WebM | 5 | 1-3 MB | 5-15 MB |
| 透明叠加 WebM（如需） | 5 | 2-4 MB | 10-20 MB |
| concat.txt 等 | 3 | <1 KB | <3 KB |
| **合计** | **~28** | | **45-110 MB** |

结论：单个视频处理的临时文件在 100MB 左右，不会造成磁盘压力。
批量处理时建议串行执行（一个视频处理完再处理下一个）。

---

#### 隐患 #14：Playwright 进程泄漏

**问题**：`_render_overlay_to_webm` 每次 launch 一个 Chromium 实例。
如果异常路径下 `browser.close()` 不执行，后台会累积多个 headless Chromium 进程。

**开源解法：已在隐患 #2 解决 — PlaywrightSingleton 单例**

结合隐患 #2 的 `PlaywrightSingleton`，加上 Skill 基类的 cleanup 钩子：

```python
class SmartCutSkill(Skill):
    async def execute(self, input_data):
        try:
            # ... 业务逻辑 ...
            pass
        finally:
            # 确保 Playwright 资源清理
            await PlaywrightSingleton.cleanup()


# 或者更优雅：在 Skill 基类注册 cleanup 回调
class Skill:
    def __init__(self):
        self._cleanup_callbacks = []

    def register_cleanup(self, callback):
        """注册清理回调，Skill 结束时自动调用。"""
        self._cleanup_callbacks.append(callback)

    async def cleanup(self):
        """Skill 结束时调用（基类保证 finally 中执行）。"""
        for cb in self._cleanup_callbacks:
            try:
                if asyncio.iscoroutinefunction(cb):
                    await cb()
                else:
                    cb()
            except Exception as e:
                logger.warning(f"Cleanup callback failed: {e}")


# talking_head_overlay 中注册清理
class TalkingHeadOverlaySkill(Skill):
    async def execute(self, input_data):
        # 注册 Playwright 清理（无论成功失败都会执行）
        self.register_cleanup(lambda: PlaywrightSingleton.cleanup())

        # ... 业务逻辑 ...
```

---

## 八、隐患对策总结矩阵

| # | 隐患 | 影响 | 开源解法 | 实施成本 | 优先级 |
|---|------|------|---------|---------|--------|
| 1 | WhisperX CUDA 运行时崩溃 | 高 | stable-ts VAD + 启动时 CUDA 检查 | 低 | **P0** |
| 2 | Playwright 版本漂移 | 中 | Browser 单例 + 版本锁定 | 低 | **P1** |
| 3 | Whisper 时间戳误差传导 | 高 | stable-ts suppress_silence + 误差容忍区间 + 短词降权 | 中 | **P0** |
| 4 | 中文填充词召回不足 | 中 | 短语级检测 + LLM uncertain 兜底 | 低 | **P1** |
| 5 | 多段拼接时间偏移 | 高 | PTS 归零 + avoid_negative_ts make_zero | 低 | **P0** |
| 6 | afade+硬切感知不一致 | 中 | 视频也加 3 帧 fade（0.1s） | 低 | **P1** |
| 7 | 录屏帧率不可控 | 高 | **WebM VP8/VP9 alpha 通道（无需 chromakey）** | 低 | **P0** |
| 8 | chromakey 绿边 | 中 | chromakey + despill 组合（备用方案） | 低 | P2 |
| 9 | LLM JSON 格式不稳定 | 高 | outputguard 校验 + 2 次重试 + 降级跳过 | 低 | **P0** |
| 10 | LLM 过度删减 | 中 | aggression 预设 + prompt 约束 + 闸门批量操作 | 低 | **P1** |
| 11 | 串联时间轴错位 | 高 | hyperframes source-time→cut-time 映射器 | 中 | **P0** |
| 12 | 闸门间隐式依赖 | 中 | ossclip clip-level marker + 锚点自动重算 | 中 | **P1** |
| 13 | 临时文件未清理 | 中 | tempfile.TemporaryDirectory 上下文管理器 | 低 | **P1** |
| 14 | Playwright 进程泄漏 | 中 | PlaywrightSingleton 单例 + Skill cleanup 钩子 | 低 | **P1** |

**P0（必须 Phase 1 解决）**：#1, #3, #5, #7, #9, #11
**P1（Phase 2 解决）**：#2, #4, #6, #10, #12, #13, #14
**P2（可选优化）**：#8（chromakey 备用方案的绿边处理）

---

## 十、v2 渲染链路实现记录（对齐 OVE 效果，2026-10-26）

> 背景：v1 成片实测效果差（45s 视频抽查 3 帧仅 1 帧有叠加、文字发绿、右缘裁切）。
> 参照 OPEN VIDEO EDIT（github.com/kevinbadi/open-edits）的四条效果规则重做渲染链路。

### 10.1 OVE 四条效果规则（已注入 `OVERLAY_PLAN_PROMPT`）

1. **图形落在词上**：叠加 time_start 对齐词级时间戳（误差 ≤0.1s）
2. **字面化视觉**：叠加文字直接来自口播原话，不编造文案
3. **一个区域只有一个动的东西**：同层不重叠，相邻叠加间隔 ≥1.5s
4. **特效克制**：重特效只用于全片 2-3 个戏剧点

### 10.2 架构变更（ADR）

| ADR | 决策 | 理由 |
|-----|------|------|
| ADR-001 | 录屏+chromakey → 逐帧透明截图（`omit_background=True`）+ ffmpeg rgba overlay | 绿幕抠像文字染绿不可修；对齐 OVE/Remotion 确定性逐帧模型 |
| ADR-002 | jieba 分词 × whisper 字级时间戳 → 词级时间戳（`word_align.py`） | 中文无空格，`text.split()` 整句一词，逐词动画全失效 |
| ADR-003 | 模板动画改 `window.seekTo(t)` 确定性时间轴 | 渲染器逐帧调用求值，动画 100% 确定（替代 CSS keyframes 自走） |
| ADR-004 | 规划密度自检：覆盖率 <30% 带反馈重试 | v1 实测叠加过稀（45s 仅 1 个有效叠加） |

### 10.3 文件与数据流

```
talking_head_overlay.py（skill 层）
  ├─ _generate_plan: to_thread(transcribe) → seg_dicts 带 words → LLM 规划
  │   → remap_overlay_anchors + TimeMapper 同步映射词时间戳
  ├─ _render_previews: caption 类模板注入 words（align_word_timing 对齐）
  └─ _compose_final: compose_with_overlays（rgba 时间窗叠加）

html_to_video.py（渲染层）
  ├─ render_html_to_video: spawn _render_worker.py 子进程
  │   → 逐帧 seekTo(t) + screenshot(omit_background) → PNG 帧序列
  │   → worker os._exit 硬退出（本机 browser.close() 挂起）+ 父进程 taskkill /T
  ├─ compose_with_overlays: 帧目录 → image2 输入 → setpts 平移 →
  │   overlay enable='between(t,s,e)' 链式叠加 → yuv420p 成片
  └─ resolve_ffmpeg: PATH → imageio-ffmpeg → FFMPEG_PATH
      （Playwright 自带 ffmpeg 仅 vp9，不能编 H.264）

word_align.py（新增）: split_words / align_word_timing / TimedWord
overlay_templates.py（v2 重写）: 10 模板全部 seekTo + 透明背景 + 大字幕描边
_render_worker.py（新增）: 渲染 worker 子进程
```

### 10.4 新增依赖（环境直装）

```
pip install jieba imageio-ffmpeg zhconv
```

### 10.5 验证记录

- 单元测试：`test_word_align.py`（10）、`test_overlay_templates_v2.py`（20）、
  `test_html_to_video_v2.py`（4）、`test_overlay_plan_rules.py`（9）、
  `test_talking_head_overlay.py`（43，旧断言升级到 v2 契约）— 全部通过
- E2E 渲染（`scripts/e2e_render_check.py`）：54/54 帧、角落 alpha=0、预览编码 OK
- E2E 合成（`scripts/e2e_compose_check.py`）：窗外 diff=12px（无泄漏）、
  字幕窗内 diff=12530px、数据卡窗内 diff=34463px（叠加真实出现）
- 肉眼验收：白字黑边大字幕、t=1.0 恰好"真不错"未弹出（词级对齐精确）、无越界

### 10.6 真实视频排障：分辨率错配（ADR-005，2026-10-26）

**现象**：54s 真实口播视频全链路跑通（10 个叠加），但成片抽帧 8 个采样点 6 个无叠加，
t=30s 右缘有黑色物体半出画。

**根因**：测试视频实际分辨率 684x1174（小红书压缩 HEVC），而 `_probe_resolution`
裸调 ffprobe（本机 PATH 无 ffprobe）异常后盲目回退 1080x1920。ffmpeg overlay 会把
叠加输入裁到主视频画幅（锚点 (0,0)），1080x1920 画布上 `padding-bottom: 260px` 的
底部字幕（y≈1450+）整部落入 684x1174 裁切区外 → 完全不可见；居中卡片偏右下 → "半出画"。

**修复（3 处）**：

| 位置 | 修复 |
|------|------|
| `talking_head_overlay._probe_resolution` | PyAV 优先（免外部依赖）→ ffprobe 兜底，画中画模式同步受益 |
| `talking_head_overlay._render_previews` | 新增 `_design_canvas`：与底片同比例、短边放大到 1080（模板字号按 1080 宽设计），渲染后由 compose 缩回 |
| `html_to_video.compose_with_overlays` | 新增 `_probe_video_size` + 滤镜链对每个叠加输入 `scale=底片宽高`，防御任何画布错配 |

同时修复 `_compose_final` 的 zip 串位隐患：`_render_previews` 改为返回
`[(segment, 产物路径)]` 配对，渲染失败的段不再导致后续段错位配对；
AUTO_CONFIRM 模式也广播免确认的 `overlay_plan_ready` 事件（下游可对照段时间轴）。

**隔离验证**（`scripts/diag_render_one.py`，无需转录/LLM）：设计画布 1080x1854 渲染
caption → 合成回 684x1174 底片 → 与底片同帧差分：窗内底部 diff=4.04（字幕可见）、
窗外 diff=0.84（仅编码噪声，无泄漏）。

### 10.7 简体中文归一化（2026-10-26）

**现象**：LLM 规划偶发输出繁体（"夏天千萬別大意"、"長時間對著空調直吹"）。两个危害：
大陆观众观感差；与 whisper 简体词流字符失配（長≠长），词级对齐降级成均分，
破坏 OVE 规则 1 的精确对齐。

**修复**：

1. `OVERLAY_PLAN_PROMPT` 规划约束新增第 4 条：所有文字必须使用简体中文
2. `overlay_orchestrator._normalize_simplified`：构建段时对 params 递归做繁→简
   归一化（`zhconv` convert `zh-cn` 地区变体模式 — "對著→对着"，且"显著"等
   已简体共享字不受影响；缺 zhconv 时原样返回，零件可缺）

**依赖新增**：`pip install zhconv`（纯 Python，约 800KB 字表）

**测试**：`test_overlay_plan_rules.py::TestSimplifiedNormalization`（5 例：
地区变体、已简体不受影响、嵌套递归、非字符串、prompt 约束）

### 10.8 v3 视觉冲击升级：彩色贴纸式质感（2026-10-26）

**动因**：用户反馈 v2 "太单一了，就加了个字幕……一点亮色的地方都没有"。
v2 的 LLM 规划偏向白字黑边 caption 类模板，卡片也是黑底白字 — 视觉冲击不足。

**模板层**（`overlay_templates.py` 全量质感重写 + 2 个新模板）：

| 模板 | v2 | v3 |
|------|----|----|
| caption_pop | 白字黑边，命中词仅变黄字 | params.accent_words 命中词 → 亮黄(#FFE066)/亮红(#FF4D4D)圆角色块交替弹出（easeOutBack 过冲），其余白字黑边 |
| caption_karaoke | 当前词变黄字 | 当前词 = 亮黄底色块 + 黑字 + 底部实体投影（CapCut 风） |
| caption_typewriter | 纯淡入 | 逐词 easeOutBack 弹出 |
| keyword_icon | 黑底毛玻璃条 | 白底气泡卡（border-radius 999px）+ 底部实体投影 + 过冲弹跳 |
| keyword_highlight | 红色下划线绘制 | 亮黄标记笔从左扫出（scaleX），扫满文字翻黑 |
| stat_card | 黑卡 + 左色条 | 亮色渐变卡（红→橙渐变）+ 实体投影 + 过冲弹跳 |
| title_card | 纯淡入上移 | 标题过冲弹入 + 亮色下划线扫出 + 副标题三段跟进 |
| number_roll | 黑卡 | 亮框卡 + 数值渐变字（accent→黄） |
| bar_chart_grow | 单色柱 | 每柱独立亮色（红/黄/青/紫/橙轮换）+ easeOutBack 生长 |
| lower_third | 半透明色条 | 亮色渐变横幅（红→橙）+ 实体投影 |
| **section_card（新）** | — | 段落编号巨型色块（120px）旋转扫入 + 段落标题跟进；用于"第一/第二/第三"列举段首 |
| **emoji_sticker（新）** | — | 大 emoji（190px）过冲弹跳 + sin 常驻微旋转；全片最强戏剧点专用 |

**规划层**（`OVERLAY_PLAN_PROMPT` 重写为"视觉冲击规则"）：

1. 全片至少 4 种不同模板；caption 类合计时长 ≤50%（v2 是 60%），把画面时间让给
   色块/气泡/卡片/emoji 类强视觉模板
2. caption_pop 尽量传 accent_words（本段最重的 2-4 个词）→ 亮色色块弹出
3. 每个叠加都要有"亮色记忆点"；同一模板不要连续用超过 2 次
4. 转录出现"第一/第二/第三/首先/其次/最后"列举 → 段首必用 section_card
5. 密度自检的重试反馈同步带"多样性"要求

**注册同步**：`OverlayTemplate` 枚举 + `TEMPLATE_PRIORITY`（section_card=8,
emoji_sticker=6）+ `TEMPLATE_LAYER`（section_card=center, emoji_sticker=top，
与 bottom 字幕层不冲突）+ prompt 模板清单。

**确定性保持**：新模板全部遵守 v2 契约 — 透明背景、`window.seekTo(t)` 逐帧求值
（含 easeOutBack 过冲与 sin 微旋转，均为 t 的纯函数）、max-width 防越界、
bg_color 参数兼容忽略。

**测试**：`test_overlay_templates_v2.py` 新增 `TestV3AccentBlocks`（8 例）+
`TestV3NewTemplates`（5 例）；`test_overlay_plan_rules.py` 新增
`TestV3VisualDiversity`（6 例）；`test_talking_head_overlay.py` 枚举/模板集断言同步 12。

---

### 10.9 v4 节奏与主题升级：画面互动 + 微动 + 撞色盘（2026-10-27）

**动因**：用户反馈 v3 "比之前好一点，但还是太单一"。归因分析定位三个真相：

1. **视觉词汇窄** — v3 的 12 个模板中 11 个本质是"一行字"（文字贴片），
   唯一的非文字元素只有 emoji_sticker
2. **与画面零互动** — 所有叠加都浮在固定位置，从不与画面空间对话
3. **节奏与主题缺失** — 动画只有单次入场无持续微动、密度均匀无轰炸/留白对比、
   各叠加配色随机无全片统一主题

**模板层**（`overlay_templates.py` 新增 4 模板 + 全模板微动）：

| 模板 | 效果 | 解决的真相 |
|------|------|-----------|
| arrow_point（新） | 箭头"戳"向画面指定位置（x/y/direction）+ 标签色块，入场后持续往复戳动 | 真相 2：画面空间互动 |
| circle_mark（新） | 手绘感椭圆 stroke-dashoffset "画"出来圈住重点位置，可选注释色块 | 真相 2：画面空间互动 |
| full_card（新） | 整屏色块闪卡：大字砸入 + 缩放入场 + 收束淡出；文字色按背景亮度自动黑/白 | 真相 1：非文字贴片的整屏冲击 |
| split_compare（新） | 左右分屏 ❌/✅ 两半色块错拍滑入 + 反相微动 | 真相 1：结构化非文字视觉 |
| 全部 16 模板 | `_idle_js()` 微动辅助 — 入场结束后 translateY/rotate/scale 以 `Math.sin(f(t))` 相位持续呼吸浮动 | 真相 3：微动 |

**确定性保持**：微动全部由 `seekTo(t)` 传入的 t 计算相位（`Math.sin`），禁用
CSS animation / requestAnimationFrame 真实时钟 — 逐帧渲染结果仍逐帧确定。

**规划层**（`overlay_orchestrator.py`）：

1. `OVERLAY_PLAN_PROMPT` 新增"节奏规则"：开场 3s 内 ≥2 个叠加（开场轰炸）、
   密集段后允许留白（每 10s 1 个是下限不是平均目标）、结尾必有收束卡
   （full_card/title_card）、整屏冲击卡只用于开场/最强论点/结尾
2. 新增"主题色系统"：LLM 先输出 `palette`（3 色撞色盘），之后每个叠加的
   params 必须带 `color` 且取值来自盘内
3. 输出格式升级为 `{"palette": [...], "overlays": [...]}`，`_parse_plan_output`
   双格式兼容（旧纯数组格式仍可解析）；`_normalize_hex` 归一化
   （非法剔除、3 位缩写展开、去重）
4. `_ensure_palette` 保底：LLM 未给/给不足 3 色时从 3 组预设撞色盘确定性补齐
5. `validate_plan_rhythm` 节奏/主题自检（warning 级不阻断）：palette ≥3 色、
   开场 3s 内 ≥2 叠加、结尾有收束卡、显式用色来自盘内
6. `OverlayPlan` 新增 `palette` 字段，重映射/裁剪函数透传

**Skill 层**（`talking_head_overlay.py`）：`_render_previews` 将 `plan.palette`
透传给每个模板（setdefault，不覆盖 LLM 显式用色）；full_card/split_compare
不进 `_CAPTION_TEMPLATES`（有自己的文案，不走词级重建）。

**测试**：`test_overlay_templates_v2.py` 新增 `TestV4NewTemplates`（13 例）+
`TestV4IdleMotion`（16 模板全量微动断言）；`test_overlay_plan_rules.py` 新增
`TestV4RhythmPalettePrompt`/`TestParsePlanOutput`/`TestEnsurePalette`/
`TestValidatePlanRhythm`（17 例）；`test_talking_head_overlay.py` 枚举数同步 16 +
`TestV4PalettePassthrough`（2 例）。

**验证**：全量相关测试 163 例通过；demo 真实视频重跑抽帧验收
（箭头/圈注指向合理性、整屏卡冲击、微动帧差、开场轰炸与收束卡节奏）。

### 10.10 P0 视觉自审闭环：VLM 看图审查 + 带反馈重规划（2026-10-27）

**动因**（《AI口播剪辑智能度分析报告.md》P0 路线，用户已认可）：
全行业开源项目（ossclip / edit-greek-reel / AutoCut / FunClip）都没有自动审片
闭环 — 渲染完直接出片，效果烂用户只能整条重跑。本项目两个前提让闭环可行：

1. **渲染确定性** — seekTo 时间轴保证同一规划必然渲染出同一画面，
   VLM 审出的问题可信、带反馈重规划后可验证修复
2. **VLM 零件已在库** — `QwenVLAdapter`（图像理解）+ DashScope 多模态
   已是 Phase 3 适配器，`vl_understand_images` 提供了完整防御式调用模式

**闭环流水线**（`capabilities/video/services/overlay_review.py`，新增）：

```
_render_previews 产出黑底预览 overlay_{seg_id}.mp4
  → _pick_frame_plan 选帧（预算 6：段数超预算时按时间轴均匀抽样；
    每段中点必选，剩余预算给最长段补 in/out 帧）
  → extract_review_frames ffmpeg 定点抽帧（-ss 前 -i 快速 seek）
  → 底片参考帧 ×2（全片 1/3、2/3 处）— 供 VLM 判断遮挡（字幕挡脸
    这类黑底预览上不可见的问题）
  → QwenVLAdapter 多图审查（帧清单 + palette + 规划参数进 prompt，
    VLM 有依据地审，不瞎猜）
  → _parse_review_output 防御式解析 → ReviewResult(issues, passed)
  → 有 problem 级问题且轮次 <2 → build_replan_feedback
    → generate_overlay_plan(feedback=...) 带反馈重规划
    （复用已转录 seg_dicts，不重跑 whisper）
  → _render_previews 重渲 → 复审 → 出片
```

**审查项**（只审黑底预览上可验证的，不臆测）：文字空/塌缩/截断/乱码、
元素缩成小点/堆叠错位、卡片分屏失衡、配色对比度过低/杂乱、
对照底片参考帧的遮挡（挡脸）、孤立箭头/圆圈。

**降级契约（最重要）**：审查是增值步骤绝不是管线闸门 —
无 `DASHSCOPE_API_KEY`、ffmpeg 不可用、抽帧全失败、VLM API 异常、
输出坏 JSON，任何一环失败 → 返回 None / passed=True，跳过审查直接出片。
`_review_loop` 外层再包一层 try/except 双保险。

**传输加固（2026-10-27 demo 实测踩坑）**：
1. `QwenVLAdapter` 构造时必须注入 `dashscope.api_key`（SDK 只认模块级
   全局/环境变量，不认构造参数 — 原实现收下 key 从不注入，凡未在进程级
   配置 env 的场景必 401 且被降级吞掉）
2. 请求体 >~145KB 会被网络中间层掐断（SSLEOFError，与代理无关：
   136KB 过、156KB 炸，特征吻合安全软件 ~128KB TLS 流重组窗口）→
   帧 JPEG 化（896px/q75，-88%）+ `_pack_chunks` 按实测 base64 尺寸
   分块（预算 110KB/次），每块一次 VLM 调用，issues 合并取 passed 合取；
   单块失败跳过该块，全败才整体降级
3. 本环境 `.env` 的 `DASHSCOPE_API_KEY` 为无效中转 key（原生/兼容模式
   双 401）→ 审查持续降级出片；接入有效百炼 key 后闭环即全自动生效

**轮次控制**（`talking_head_overlay.py` `_review_loop`）：最多 2 轮
（初审 + 1 次带反馈重渲复审），`_REVIEW_MAX_ROUNDS = 2`。事件
`overlay_review_done`（data: round/passed/issue_count/critical_count/
will_retry）遵循"决策先于事件"——will_retry 反映真实走向，重规划失败
不会谎报"将重试"。重规划/重渲失败 → 保留当前渲染出片。

**接口变更**：
- `generate_overlay_plan(..., feedback: str | None)` — 外部反馈拼在 prompt 头部
- `_generate_plan` 返回 `(plan, timed_words, seg_dicts)` — 重规划复用转录
- 新增 `_resolve_llm` / `_apply_constraints`（初次规划与重规划共用锚点
  重映射 + 模板白名单，修掉潜在双重 remap）/ `_replan_with_feedback`

**测试**（`test_overlay_review.py`，46 例）：抽帧防御（真 ffmpeg lavfi 造片、
越界/负时间戳/坏视频）、选帧预算（均匀抽样/长段 in-out 帧）、解析加固
（坏 JSON 降级 passed、非法 severity 归一、pass 缺失推断）、帧压缩与
分块装箱、`review_overlays` 降级路径 + 多块合并 + 单块失败跳过 +
DashScope content-list 拉平、闭环轮次（None 降级/单事件/重试一轮/
两轮上限/重规划失败保留渲染）。

**验证**：相关测试 303 例全绿（原 257 + 新 46，零回归）；demo 真实视频
重跑出片（23 叠加，审查不可用时降级路径实证）— 接入有效 VLM key 后
观察 `overlay_review_done` 事件、审查问题合理性抽帧人肉复核。

### 10.11 P0 收尾与闭环实战验收（2026-10-27，同日续）

**VLM key 接通后暴露并修复的三个配置断点**：
1. `QwenVLAdapter` 构造收 key 从不注入（DashScope SDK 只认
   `dashscope.api_key` 全局）→ 生产的 `vl_understand_images` 自接入起
   一直静默降级，无人发现 — 降级契约的副作用：错误被吞得太安静
2. 模型路由双源漂移：`_DEFAULT_TIER_MODELS`（测试用）与
   `config.tier_*_vl`（生产 `get_tier_router` getattr 覆盖）不一致 →
   测试绿但生产解析出本账号已 403/404 的旧模型名。修复后两处统一
   `qwen3-vl-plus`（真机双端点探活），教训：同一默认值只准一个源头
3. 真 key 仍 `sk-ws-` 中转格式但认证已过 — 百炼账号可用模型随账号
   权益变化，模型名必须真机探活不能凭文档

**o6 同屏叠字事故 → 空间冲突强制消解**（本日第 4 次 demo 抽帧复核发现）：
section_card 与 keyword_highlight 同屏叠字。根因不是模板 bug，而是：
- `TEMPLATE_LAYER` 旧标签与 CSS 布局事实不符（keyword_highlight/icon
  实际渲染屏幕正中却标 inline；title_card/emoji_sticker 实际居中却标
  top）→ 逐模板核实改为四带事实：`bottom` 字幕带 / `center` 中部带 /
  `free` 绝对定位（arrow_point/circle_mark）/ `full` 全屏覆盖
- `detect_overlay_conflicts` 有检测无裁决 → 新增
  `resolve_overlay_conflicts`（贪心：优先级降序→先开始者胜→时长降序，
  低者标 skipped 可追溯），接入 `_apply_constraints`（初规划与重规划
  共用）；全屏卡与任何带同刻即冲突
- 修复后规划零时间重叠，审查 critical 2→1 收敛（修复前 3→5 发散）

**审查明细落盘（可审计性）**：`overlay_review_done` 事件 data 新增
`issues`（segment_id/severity/problem/suggestion 全文，三处退出路径
统一带）；`overlay_compose_done` 新增 `final_plan`（终版规划快照）。
demo report.json 相应升级：`reviews[]` 含每轮 issue 明细（两轮对比
才见闭环改了什么），`plan` 取终版、`initial_plan` 保留初版。

**明细落盘立刻兑现价值 — VLM 误报归因与抽帧策略修复**：
第 5 次 demo round2 报"o13 accent 只高亮了腹痛没框拉肚子 /
o23 只高亮了效果没框特别多"。成片抽帧人肉复核（29.9/30.6/52.4/
53.1s）：accent 高亮全部正常（腹痛黄块+拉肚子红块、效果黄+特别红+
多黄），**两条均为误报**。根因：caption 三件套是逐词弹出动画，
审查帧抽在段中点 = 半入场态，后半句词 opacity=0 尚未弹出，VLM 把
"还没弹出"误判为"params 与视觉不一致"。修复（三层）：
- `_pick_frame_plan`：caption 段中点帧改抽**稳定态 settled**
  （`max(dur/2, dur-0.25)`，出场前动画完成态），且不补 in/out 帧
  （入场早期帧是噪声、out 与 settled 同时刻重复）
- `_ChunkItem` 加 `tag` 字段，帧清单行向 VLM 说明帧时刻性质
  （"动画完成后的稳定态，全部元素应可见" / "入场早期态，部分元素
  尚未出现属正常"）
- `REVIEW_PROMPT` 注意节加防误报总则：判断"缺失/未高亮"类问题
  以稳定态帧为准，入场早期帧不作为依据

教训：审动画视频，帧时刻语义 = 抽帧时刻本身一样重要 — 半入场态
帧喂给 VLM 是系统性误报源。顺带真瑕疵一件（未修，候选）：长字幕
末词被 flex wrap 挤到第二行偏左（o23"多"字），属模板排版微调。

**settled 抽帧验证 + 引用校验硬防线（同日续）**：
第 6 次 demo（settled 生效后）round2 首次全绿通过（issues=0），
"审出→修→复审通过"完整正向闭环首次成立。但 round1 的 3 条
problem 经逐条取证（report 明细 + 段 params 对照 + WCAG 复算）
**全部是误报**：
- 报 o14"'出门做好防晒'断行" — o14 params 是"不只是怕晒黑"，
  "做好防晒"是 o13 的（段号错位）
- 报 o18"'帽子防晒衣防晒霜'色块黄红混用" — o18 是 title_card
  "夏天舒服过"，那是 o17 keyword_icon 的（段号错位）；且实测成片
  o17 无色块
- 报 o22"黑字青绿底对比度 3.2:1" — WCAG 复算 9.76:1 远超 AA
  （白字才 1.93:1，_text_on 选黑字正确），VLM 目测对比度不可信

后果：反馈重规划白改了 o10-o17（幸而改动无害）。修复 = 程序化
防幻觉校验 `_validate_issue_refs`（比继续调 prompt 硬）：
VLM 报问题时的**引号引用文字必须能在该段 params 里找到**（子串
匹配，params 是渲染文字真值来源），找不到 → 段号错位/幻觉，整条
丢弃；校验后 critical 归零 → passed 翻 true，不再触发无效重规划。
设计权衡：只拦"引用了段里根本不存在的文字"这一种硬幻觉 — 无引用
的描述性 issue（"元素溢出边界"）与引用命中的都保留，宁放过不误杀。
实录三连：o14/o18 两条被拦（引用文字不在段内），o22 保留但为
suggestion 级无害。

**验证**：相关测试 319 例全绿。

**params.color 系统性消费修复（规划/渲染契约闭合）**：
第七次 demo 验证了引用校验防线（round1 的段号错位类误报消失，
round2 四条引用全部命中段 params = 真问题）。但这轮真问题暴露出
下一层债：**palette 规则与模板实现脱节** — OVERLAY_PLAN_PROMPT
强制 LLM 每段 params.color 取色盘内色，VLM 审查也按此判问题，但
两个模板根本不消费 color：
- caption_pop accent 块写死黄红循环（LLM 传 color="#4ECDC4" 照样
  黄红 → o6/o10 双确认）
- section_card 编号块 accent_color 默认黄，params.color 被无视

修复（对齐已消费 color 的 keyword_highlight 样板）：
- caption_pop：LLM 传非默认 color → accent 块统一用它（字色按
  `_text_on` 自动黑白），CSS 不再输出黄红规则（不留死代码）；
  不传 → 黄红交替兜底（CapCut 贴纸感设计保留）
- caption_pop accent 色类语义改造：从"第几个 accent 词"改为
  **按 accent 词索引取类** — 同一 accent 词命中的多个 word（分词
  拆散的"特别"+"多"）同色同块，语义单元不再被黄红拆散
  （o23 实证修复）
- section_card：`accent_color = color or accent_color`

TDD 新增 5 例（盘内色消费 ×3、同 accent 词同色、默认黄回归保护）。
教训：规划 prompt 里的每条约束，渲染层必须有对应消费点 — 否则
LLM 越守规矩，产出越自相矛盾（传了 color 被无视，审查还按规则判
违规）。契约闭合 = 规划规则 ↔ 渲染消费 ↔ 审查标准 三方同一真相。

**第八次 demo 验证 + 复核时序教训 + 折行修复（同日续）**：
第八次 demo 复核时犯了一个流程错误 — 在 demo 进程未完成时读了
report.json/成片（旧一轮的产物），差点把"第七次成片的黄块"误判成
"修复未生效的 bug"。取证规则补丁：**核对产物前必须先确认进程完成
（通知/exit code），产物 LastWriteTime 必须晚于代码修改时间**。

真 run8 结果与验证：
- **params.color 契约修复生效实证**：终版 o6/o10 section_card
  color=#4ECDC4 → 成片编号块"01"/"02"均为青绿色，标题单行清晰
- round2 的 2 critical 取证：1 真 1 误报
  - 真：o10"出汗别猛灌冰水"7 字在固定 54px + max-width 56% 下
    折行"出汗别猛灌冰"+"水"孤字断义；o23 title_card"位"字孤行同类
  - 误报：o19"🧢 蓝色但 params.color=#FFE066" — emoji 颜色由系统
    字体决定，params.color 控制不了，此类问题不该报

修复：
- `_fit_font_size` 字号分档自适应（新增）：section_card .txt
  （≤6 字 54px / 7-8 字 44px / ≥9 字 36px）+ white-space:nowrap；
  title_card .title（≤8 字 80px / 9-11 字 60px / ≥12 字 48px）
- REVIEW_PROMPT 防误报总则扩充：emoji 颜色豁免（字体色不可控）+
  换行判据收窄（只有孤字行/词被拦腰截断才报，词组边界正常换行不报）

**验证**：全量 overlay 测试全绿（新增 2 例字号自适应）。

**第九次 demo 复核 → 两个防线漏洞（同日续）**：
run9 折行修复验证通过（round1/2 均未再报折行类问题）。但 round1/2
的 critical 取证暴露两个防线漏洞，均已修：

1. **弯引号穿透引用校验**：round1 报 o18"防晒衣防晒霜缺分隔"，但
   o18 不在 plan（内容实属 o19）— `_QUOTE_RE` 字符类漏了弯引号
   ' '（U+2018/2019），引用提取为空 → 幻觉 issue 被当"无引用描述"
   放行。此前测试全用直引号，真实 VLM 输出是弯引号。修：字符类
   补全弯引号 + CJK 引号，新增 2 例实录测试。
2. **重复段号导致渲染覆盖**：round2 重规划 LLM 输出两个 o20
   （keyword_highlight + title_card 同名）→ 渲染文件
   overlay_o20.mp4 互相覆盖 → 46.3s（keyword_highlight 窗）空屏，
   VLM 看到的是被覆盖后的 title_card 内容（"浅橙横线"实为 title_card
   黄橙下划线）— VLM 再次被段号错位坑，但根源是 LLM 重复段号。
   initial plan 无重复，重规划链路专属风险。修：
   `_parse_plan_output` 尾部段号唯一化（后出现的重复段取 max+1
   重编号，保内容，logger.warning 留痕），新增 1 例实录测试。

教训：防线的漏洞永远在"测试输入 ≠ 真实输入"的缝隙里 — 直引号 vs
弯引号、单规划 vs 重规划。防线测试要用真实 LLM 输出的原样字节做
用例，不能用"我认为它应该长这样"的干净样本。

**验证**：全量 139+ 例绿（新增 3 例）。第十次 demo 为 P0 最终验收轮。

### 10.12 P1 模板生成器：LLM 运行时造新模板（2026-10-27，P0 关闭后续）

计划书 P1 定义：内置 16 模板总有表达不了的画面（进度条、倒计时、
对比动画…），让规划 LLM 在需要时**提名并现场生成**新模板，契约校验
通过后入库复用。初始约定"LLM 只选模板传参数、不生成 HTML"升级为
**带门禁的开放模板库** — 日常路径不变（内置 16 模板照常选），生成
只是例外通道，可控/可审/确定性的约定精神完整保留。

**三重锁设计**：

1. **提名门槛（规划侧）**：OVERLAY_PLAN_PROMPT 新增规则 — 仅当
   16 个模板都无法表达时才可提名 `gen_<功能名>`（snake_case），
   params.requirement 用一句话描述视觉需求，全片最多 2 个
2. **质量门禁（生成侧）**：`generate_template` 生成后必须过
   `validate_generated_html` 6 条契约静态校验（继承十轮 demo 防线：
   seekTo 契约/禁真实时钟/选择器审计+null guard 豁免/{color} 必须
   消费/占位符白名单/HTML 转义），失败把错误清单拼回 prompt 重试
   （≤3 次），全败返回 None 不阻断
3. **零执行（渲染侧）**：LLM 产出**只是数据** — 参数化 HTML 字符串
   （{var} 占位符）由 Python `GeneratedTemplate` 包装成与内置模板
   同签名的 callable；测试红线断言全模块无 exec/eval/__import__；
   文本参数 `escape()` 转义防注入；占位符仅白名单变量生效（CSS
   花括号照常写 — 只有白名单单词进大括号才被替换）

**实现**（`template_generator.py`）：
- `validate_generated_html(html)`：6 条契约静态校验，返回问题清单
- `GeneratedTemplate.__call__`：文本别名归一（text/title/sub/
  keyword/label 取首个非空）、color 缺省亮黄、`escape()` 转义
- `generate_template(llm, name, requirement)`：生成→校验→带错误
  重试→注册进 GENERATED + 落盘 JSON（outputs/generated_templates/）
- `load_generated_templates()`：启动加载，损坏/契约不过跳过

**orchestrator/skill 集成**：
- `OverlaySegment.template` 从枚举放宽为 `str`；`template_name()`
  统一取值（枚举取 .value）；`_parse_plan_output` 未知模板名保留为
  str 不再丢弃（原实现直接 ValueError 丢段）；`TEMPLATE_LAYER`
  未知名兜底 center 带
- skill 侧 `_apply_constraints` 白名单 = 内置 ∪ GENERATED；
  `_render_previews` 查 `TEMPLATES or GENERATED`，都未命中且带
  requirement → 现场生成（每片 gen_budget=2），生成失败 skip 该段
  不阻断

**验证**：新增 27 例 TDD（契约校验/包装类/mock LLM 流程/集成 +
`inspect.getsource` 安全红线）全量 171 例绿。真机冒烟：DeepSeek
生成 `gen_progress_bar` 一次过契约，Playwright 渲 3s mp4，抽帧证实
动画真实驱动（0.5s 进度 62% → 1.8s 100% → 驻留）、{color} 青绿被
消费。

**第十一次 demo（P1 里程碑验收）**：19 叠加 10 种内置模板，
REVIEW round=1 一次过审（0 critical，1 条建议级 — o19 副标题字号，
属模板排版微调候选）；initial=final 无需重规划；o15 幻觉引用被
校验丢弃（P0 防线持续工作）。**LLM 自然路径未提名 gen_** — 内置
16 模板足够表达本片，恰好证明提名门槛生效（不为炫技滥用生成）；
生成能力已由冒烟单独证实。

实现备注：async generator 不能 yield 后再 return 值 — 生成过程
用 logger 记录不 yield 事件；prompt 模板用 `.format()` 前须排查
裸花括号（CSS 示例只能文字描述，或改双写 {{}}）。

### 10.13 P2 时间轴两步链：叙事结构分析先行（2026-10-27，P1 续）

计划书 P2 定义（参考 NarratoAI 两步 LLM 链）：单 prompt 直出时间轴
→ 改两步 — 先叙事结构分析（开场/论点/转折/高潮/收束 + 每段视觉
意图），再在结构内落时间轴/模板/参数。时间轴质量上限更高，且为
P0 审查反馈提供**可解释中间层**。

**链路**（`overlay_orchestrator.py`）：
- 第一步 `generate_narrative`：NARRATIVE_PROMPT 输出 theme /
  palette / sections[]（role + summary + visual_intent + key_points，
  段边界落在词间隙）；`_parse_narrative_output` 防御式解析
  （坏 JSON/空 sections → None；缺时间戳段丢弃保留其余；越界
  clamp；按 time_start 排序；繁体归一化 — v1.4 模式套新层）
- 第二步：叙事 JSON 经 `_NARRATIVE_TO_PLAN_HEADER` 拼在
  OVERLAY_PLAN_PROMPT 头部，附落实要求（palette 直接采用不另定 /
  时间轴落在所属段内 / key_points 是文案首选来源 / role 定节奏）；
  模板清单与全部规则仍以 OVERLAY_PLAN_PROMPT 为唯一真相（v1.8
  双源漂移教训 — 叙事块只带结构与意图，不复制规则）
- `generate_overlay_plan(use_two_step=True)`；`OverlayPlan.narrative`
  新字段随 model_dump 落 report.json（可审计）
- 降级契约：叙事失败 → 无声回单步链（P0 精神：增值步骤绝非闸门）
- feedback 双拼：P0 审查反馈同时进叙事步（结构层反思）与规划步
  （细节修复）— 重规划也是两步链

**第十二次 demo（P2 里程碑验收）**：
- narrative 完整落盘：7 段结构 opening→transition→argument×2→
  climax→closing×2，各带 visual_intent/key_points；palette
  #FF3B30/#FFD60A/#00C2A8 叙事定盘 → 规划原样采用（传递链验证）
- **反馈驱动叙事反思实证**：round1 审出 o7/o11 图标失衡（keyword_icon
  图标过小/对比不足，2 critical 真问题）→ 重规划叙事 theme 从描述式
  （"三个容易被忽略的生活细节"）锐化为"三大健康陷阱"框架，o7/o11
  结构级换型 caption_pop+accent_words 绕开问题模板；成片抽帧人肉
  复核确认（贪凉黄块/肠胃黑字黄块，图标问题消失）
- round2 审查遇 dashscope DNS 抖动 → 全分块失败降级跳过直接出片
  （降级契约按设计工作，网络环境问题非代码问题）
- 规划 17→21 段、8 种模板、caption 33%<50% 上限
- 全量测试绿（新增 27 例两步链：解析防御式/降级/mock 双响应/prompt
  断言/feedback 双拼/繁体归一化）

P0/P1/P2 三阶段至此全部关闭 — 管线完整形态：
whisper 词级转录 → **叙事结构分析（P2）** → LLM 规划（内置 16 模板
+ P1 gen_ 例外通道）→ 冲突消解 → Playwright 确定性渲染 → **VLM
视觉自审 + 带反馈重规划（P0）** → ffmpeg 合成。

### 10.14 接入主业务管线：auto_short_video Step 4.5 内嵌增强（2026-10-27，P2 续）

文档 3.7 预设的三种集成方式落地状态：方式 1（独立调用）与方式 2
（Agent 串联，dispatch_hooks 已为 smart_cut/talking_head_overlay 注入
video_asset_ids/解析路径/auto_confirm）早已可用；本次补方式 3 —
**管线内嵌**：auto_short_video 一键出片时可选自动叠加增强。

实现（`auto_short_video.py`）：
- `AutoShortVideoInput.enhance_overlay: bool = False` — 缺省关，
  主管线零改动零影响（默认不碰 overlay 有测试钉死）
- Step 4 合成落库成功后，`enhance_overlay=True` → Step 4.5 内嵌调
  talking_head_overlay（gate_mode=auto_confirm 无人值守），完整走
  P2 叙事两步链 + P0 视觉自审闭环
- 增强片**另存资产不覆盖原片**（`_enhanced.mp4`，metadata 带
  chained_from 溯源），返回值加 enhanced_asset_id/enhanced_url 双资产
- 降级契约：overlay 任何失败（转录/规划/渲染零件缺失）→ degradation
  note + 原成片照常返回 — 增强是增值步骤绝非闸门

顺手修存量 bug（测试基建暴露）：`execute` 里 `video_compose` 返回值
按 dict 调 `.get`，但 local 分支真实 `compose_video` 返回 Path
（remote HTTP 分支才是 dict）→ local 全链此前必炸 `AttributeError`
被 except 吞成"合成失败"。修为两形态兼容。

**验证**：新增 3 例接入测试（默认不增强零影响/成功双资产+溯源元数据/
失败降级原片），auto_short_video 19 例 + overlay 全家 + e2e 链
144 例全绿。overlay 本体链路 run12 已真机验证，内嵌只是编排层
（调法与 demo 相同），里程碑验收由单测覆盖。

---

### 10.15 样本多样性审计与三项根修（2026-10-28，真机全链验收后）

**背景**：全链验收（run13）双资产产出成功，但暴露叠加文案与口播错位
（"90%的人都漏了"→"九十辈子的人都漏了"、"挤地铁"落在"喝温水"段）。
由此引出更根本的质疑：**十轮 demo 只用了同一条 22s 竖屏 AI 图 + TTS 片，
是否已过拟合到单一样本？**

**审计分层**（先分开再动手）：

| 类别 | 内容 | 验证方式 | 结论 |
|------|------|---------|------|
| 契约类 | 参数透传、降级链路、时间映射、API 形态、安全红线 | 单元测试足够（结构决定，换任何素材走同一套逻辑） | 无需补样本 |
| 效果类 | 视觉质量、语义对齐、排版、遮挡判定、审查覆盖率 | 只能真机 | **n=1，风险真实存在** |

**静态审计直接确证 3 处结构性过拟合**（无需新素材即可证明）：
1. 16 个模板的字号/边距/位移全是绝对 px，按 1080×1920 设计 —
   720p 竖屏相对放大溢出、4K 字号只剩半比例、横屏布局错位
2. 审查帧预算固定 6 帧（`MAX_OVERLAY_FRAMES`）— 22s/19 段尚可，
   180s/104 段时每个叠加只剩 6% 画面证据
3. 真人出镜场景零样本 — 所有素材都是"AI 图 + TTS"，挡脸判定
   （审查第 5 条）从未被真实人物素材考验

**修复一：文本对齐 — 已知文本时间轴通道（skip whisper）**

根因链：edge-tts 403（网络环境）→ piper 离线低质音频 → whisper 误听
→ 规划 LLM 基于错文本提取关键词。但内嵌路径上游手里就有 script 原文，
根本不需要转录。

- `OverlayInput.transcript`（可选）：`[{index,start,end,text,words}]`
  传入即跳过 whisper；坏元素（end≤start）丢弃
- `auto_short_video._build_transcript(shots, total_duration)`：由
  script captions 构造词级时间轴 — 按镜时长比例缩放到**成片实际时长**
  （末段吃累积误差精确对齐），镜内按字符数比例分配（jieba 分词；
  缺 jieba 退化为整段一词）
- 构造失败仍降级回 whisper，不阻断

**修复二：分辨率自适应（模板一行未改）**

- `_base_css`：body 固定设计基准 1080×1920（模板内部 px 原样有效），
  整体按 `k = min(w/1080, h/1920)` 等比缩放居中 — 16 个模板零改动
- `_design_canvas`：由"只放大不缩小"改为**短边双向归一到 1080** —
  4K 竖屏不再因原样渲染导致字号只剩设计比例一半
- 真机验证：720p 竖屏（渲染 1080×1920 → 合成缩回 720×1280）字号比例
  正常无溢出；1920×1080 横屏（pad 后的内容区内）叠加层与底片
  **像素级重合**，无裁切无错位
- 横屏是保守策略（宁小勿溢出），正确形态应重排而非缩放 — 未覆盖

**修复三：分布均匀性自检（补全局覆盖率的盲区）**

全局覆盖率只看总量：100 个叠加全挤在前 10s 也照样达标，后 2/3 等于
没做增强。

- 新增 `largest_overlay_gap(plan, duration) -> (秒, 起点, 终点)`：
  并集最长的连续无叠加区间，片头片尾空白一并计入
- 密度自检升为双指标：全局覆盖率 < 30% **或** 最大空白 >
  `max(6s, 8% × 时长)` → 带反馈重试（≤2 次）
- 反馈点名具体区间：「22s~180s 连续 157s 没有任何叠加（要求任意空档
  ≤14s）」
- **阈值校准用真实合格产物反推**：run12 终版最大空白 1.88s、初版
  3.44s（54s 片）→ 基准定 6s（原拟 3s 会误报合格片），长片按 8% 放宽
- 真机实证（180s 伪影场景）：attempt 1 被拦（gap 157.5s > 14.4s）
  → 重试后 **13 → 104 个叠加**，后 157s 空白被填满 — 不只是拦下，
  反馈驱动规划确实修好了

**验证汇总**：新增 21 例（transcript 通道 2 + `_build_transcript` 5 +
分辨率自适应 9 + `largest_overlay_gap` 10 + 分布自检集成 2）与 4 处
既有测试数据修正（原规划只覆盖 0-18s/54s，现在会被正确判为不合格）。
视频管线全量回归全绿。

**尚未闭环**（如实记录，勿当已解决）：
1. **长片审片覆盖率**：仍为 6 帧。180s/104 段实测覆盖 6/104 = 6%，
   VLM 只能看到极小样本 — 需改帧预算或按段分组审查
2. **真人出镜素材零验证**：挡脸判定、与真实背景的对比度均无实证
3. **180s 长片结论不可信**：变体由同一段内容循环 8 次拼成，LLM 合理
   地只覆盖开头属"样本构造伪影"，不能据此断定真实长片行为 —
   需要一段 2-3 分钟内容不重复的真实口播素材
4. **既有模板排版问题**（与本次修复无关）：副标题字号仅主标题 1/3、
   `section_card` 编号卡偏居左 1/8、白字压 #FF8A00 对比度 2.8:1、
   高亮条内边距过窄、收尾卡层级模糊 — 列为下一批优化候选

---

### 10.16 模板排版静态审计与 WCAG 自动选色重写（2026-10-28，10.15 遗留 #4 收口）

**背景**：10.15 遗留 #4 —— 前十轮 demo 的排版问题（副标题字号仅主标题
1/3、section_card 编号卡偏居左、白字压 #FF8A00 对比度 2.8:1、高亮条
内边距过窄、收尾卡层级模糊）全部靠 VLM 抽帧事后发现，**用最贵的手段
做最便宜的检查**。

**静态审计工具**（`tools/audit_overlay_layout.py`）：
把可计算的指标一次性算清 — 无需 LLM、无需真机素材：
- 文字与最近不透明背景的 WCAG 对比度（正文 ≥4.5 / 大字 ≥48px ≥3.0）
- 无背景色文字（直接压视频画面）必须有 text-shadow / stroke 兜底
- 主次字号比、主元素画面占比、背景元素内边距（参考指标，未设阈值）
退出码：存在 P0 问题 → 1（可接 CI）。

**`_text_on` 重写**（对比度防线地基，`overlay_templates.py`）：
原实现感知亮度 0.299/0.587/0.114 + 0.6 阈值二选一 — #FF4D4D 近似亮度
0.51 被误判"浅色"选白字，实际对比仅 3.27:1（正文需 4.5），即十轮
demo 里 VLM 反复报的"白字压橙色块"同类问题。改为 WCAG 相对亮度 +
黑白对比度取优：max(c_black, c_white) ≥ √21 ≈ 4.58，**任何背景恒
≥4.5**。亮度计算抽为 `_wcag_lum()` 共享（模板层/生成器层同公式）。

**审计结果**（2026-10-28 实跑）：16 模板 **0 条硬性问题**（exit 0）；
INFO 参考项如实列出 — title_card 字号比 0.42、full_card 0.35（副标题
偏小候选）、渐变底文字对比度需人工目视（stat_card/number_roll/
lower_third）、arrow_point/circle_mark/section_card 压画面文字需
真实素材目视。

**仍未闭环**：o19 title_card 副标题 34px 固定（无长度分档），与主标题
80px 字号比 0.42 — 审计工具 INFO 级候选，待模板排版微调批次。

### 10.17 G1 生成通道放开 + 契约第 7 条（WCAG 对比度）+ text_color 旁路收口（2026-10-28）

**背景与决策**：10.12/v1.9 落地的"全片 ≤2 个生成模板"配额 +
"仅当 16 内置无法表达才提名"门槛，真机运行把生成通道压成基本闲置。
用户裁决放开：配额从表达上限降格为**成本护栏**，对比度防线同步从
"_text_on 单点"升级为"生成契约 + 模板渲染双防线"。

**改动**：
1. `_GEN_BUDGET` 2 → 8（`talking_head_overlay.py`），语义改为
   "**不同 gen_ 模板数**" — 同名模板入库后复用零边际成本、不占预算。
   8 个定制模板远超单片真实需要，是防 LLM 失控刷模板的保险丝。
2. 规划 prompt 自定义模板段（`overlay_orchestrator.py`）：
   "仅当 16 内置无法表达、全片 ≤2" → "**按需大胆提名，数量不设限**"；
   requirement 定位为"生成模板的工程需求书，写得越具体质量越高"。
3. `validate_generated_html` 契约 6 → 7 条（`template_generator.py`）：
   - 新增第 7 条 WCAG 对比度：≥48px 大字 ≥3.0:1、其余 ≥4.5:1
     （渐变/半透明/占位符底跳过，留 VLM 审片兜底）
   - 禁真实时钟扩展：JS 计时 API 之外同禁 CSS animation/@keyframes/
     transition（真实时间驱动，破坏 seekTo 逐帧确定性）

**真机验证**（22s 素材全链，run14）：
- gen_soft_label 全链入库并复用（生成库累计 2 个：gen_progress_bar
  冒烟 + gen_soft_label）
- 生成反馈重试闭环实测：attempt0 生成 HTML 缺 {color} 消费 → 契约
  拦截 → 错误清单回喂 → attempt1 修正入库
- 对比度诱导测试：prompt 故意诱导低对比配色 → LLM 自纠 #4A4A4A
  达标（契约 + prompt 双防线生效）
- 成片：outputs/talking_head_overlay/01M60QX2QHZBV9VAY3ZABMV2MY_overlay.mp4

**旁路收口**（G1 真机暴露的对比度防线唯一漏洞）：
full_card 显式传 text_color 直接生效
（`tc = str(text_color) if text_color else _text_on(color)`），
绕过 WCAG 自动选色 — LLM 传白字压橙底 2.8:1 畅通无阻。修复
（`overlay_templates.py`）：
- 新增 `_safe_text_color(text_color, bg, need)`：显式色按"本卡最严
  适用档"校验 — tc 同时用于 110px 大字（3.0 档）与 38px 副标题
  （4.5 档），有 sub 按 4.5、无 sub 按 3.0；不达标或非法色回退
  `_text_on` 自动选色（黑白取优恒 ≥4.5）
- full_card `tc` 改走 `_safe_text_color`，docstring 同步契约说明

**验证**：TDD 5 例红→绿（达标保留 / 低对比回退 / 副标题按 4.5 档 /
默认路径回归 / 非法色回退）；test_overlay_templates_v2.py 120 例全绿；
13 个测试文件视频管线全量回归 **546 通过 / 1 跳过 / 0 失败**。

---

### 10.18 G2 布局编排层方案（2026-10-28 — **已批准开工**，用户裁决：独立 skill `dynamic_layout` / 编译策略 A 分段渲染+concat / 本期做全三原语 FULL+PIP+BROLL）

**需求**（用户原话）：口播镜头随内容动态切换位置 — 开场人像全屏 →
讲核心内容时缩小 + storyboard 配图当主画面 → 收束回全屏。
安全模式 = LLM 产布局 JSON → 原语白名单校验 → ffmpeg
`enable=between(t)` 确定性编译，复用 G1 三重锁。

**定位**：管线的第三层智能。P1/G1 开放的是"叠加什么"（视觉元素层），
G2 开放的是"**画面构图**"（镜头语言层）— 此前 LLM 决策从未触及构图：
现有 `_execute_pip` 是**静态**画中画（全程一个位置一个缩放，参数由
调用方写死），叙事阶段变化对构图零影响。对标 Remotion Claude 的
"全层开放"路线，这是剩余的最大一块确定性代码。

**数据结构**（LLM 唯一产出物，纯数据）：

```python
class LayoutPrimitive(str, Enum):
    FULL = "full"        # 人像全屏
    PIP = "pip"          # 人像缩角，主画面 = 配图/幻灯片
    BROLL = "broll"      # 配图全屏，人像隐藏（强叙事段可用）

class LayoutSegment(BaseModel):
    start: float                          # ≥0
    end: float                            # >start，段间不重叠不留空洞
    primitive: LayoutPrimitive            # 白名单枚举，3 选 1
    pip_position: PipPosition | None      # PIP 时四角枚举
    pip_scale: float = 0.3                # 0.1~0.5
    main_source: str | None               # "shot:3" / "color" / None
    reason: str                           # 决策理由（可审计，同 CutCandidate）

class LayoutTimeline(BaseModel):
    segments: list[LayoutSegment]
    bg_color: str = "#1a1a2e"             # PIP/BROLL 空隙底色（hex 校验）
```

**三重锁映射**（G1 模式平移）：
1. **LLM 只产数据**：布局规划 prompt 输入 = 转录/叙事结构（P2 sections
   的 role 是布局天然输入：opening→FULL、argument→PIP+配图、
   climax→BROLL/FULL、closing→FULL）+ 可用配图清单（shot 引用）；
   输出 = LayoutTimeline JSON，不产滤镜不产命令
2. **白名单 + 契约校验**：pydantic 枚举/范围（scale 0.1~0.5、位置四角、
   hex 色合法性）+ 时间轴完整性（单调、全覆盖、首末段边界）+ 引用
   存在性（shot:N 必须命中真实配图）+ 静态审计（复用 audit 思想：
   PIP 段占比下限、单段最短时长 ≥1.5s 防构图抖动）；不过 → 带错误
   清单回喂重试 ≤2（同 P0 反馈闭环）
3. **零执行确定性编译**：编译器是纯代码，把 timeline 编译成 ffmpeg
   参数；LLM 输出无任何执行路径

**编译策略**（两选项，推荐 A）：
- **A. 分段渲染 + concat**：每段独立 filter（段内参数恒定，与
  `_execute_pip` 现有合成逻辑同构）→ concat。简单可靠、每段可独立
  验证、段数 O(10) 级中间文件可接受；切点硬切
- B. 单 filter_complex 多路 overlay 各带 `enable=between(t,s,e)`：
  一次编码无中间文件，但滤镜图复杂度 O(N)、切点行为调试成本高 —
  留给后续转场层（优先级 3）天然需要表达式时间轴时再切
- 音频全程直通（`-c:a copy` 段内），视频段内重编码 yuv420p（对齐
  smart_cut 重编码共识）

**闸门与降级**：
- 闸门事件 `layout_plan_ready`（needs_confirmation，同 overlay_plan
  模式）；AUTO_CONFIRM 模式供 auto_short_video 内嵌
- 降级链：布局规划失败 → 回退**静态 pip**（现行为，参数默认）→ 再
  失败回退原片。布局是增值层，绝不阻断
- 产物另存 `_layout.mp4` 不覆盖原片（对齐 Step 4.5 惯例）

**审片集成**（P0 平移）：每 LayoutSegment 至少抽 1 帧送 VLM —
检查项：PIP 窗口是否遮挡配图关键内容、构图是否符合该段 primitive、
无黑帧。审查反馈回喂布局规划重试（复用 feedback 通道）。真人素材
PIP 挡脸属此类审查范畴（10.15 未闭环 #2 的部分覆盖）。

**TDD 与验收**：
- 单测：数据模型校验（白名单/范围/时间轴完整性/引用存在性）×
  编译器（给定 timeline → 断言 ffmpeg 参数序列）× 降级链 × 闸门
- 真机验收（22s 样本）：LLM 产出三段式 timeline
  （FULL 0~4s → PIP+shot 配图 4~16s → FULL 16~22s），成片三段构图
  正确、切点无黑帧、音画同步、审片 round 通过
- auto_short_video 内嵌路径：enhance_layout（缺省关）→ shots 配图
  作 PIP 主画面

**风险（如实）**：
1. 切点硬切无转场 — G2 范围内接受（转场属优先级 3，编译选项 B 是
   其前置）
2. 配图与画布分辨率/比例不一致 → 主画面 letterbox（复用 10.15
   `_design_canvas` 短边归一思路）
3. 真人出镜素材 PIP 挡脸零实证 — 审片检查项覆盖，但结论待真实素材
4. 布局规划 LLM 过度碎片化（段太多构图抖动）→ 静态审计段数/最短
   时长护栏

**决策点裁决**（2026-10-28 用户批准）：独立 skill `dynamic_layout`
（`_execute_pip` 零改动）/ 编译策略 A 分段渲染+concat / 本期做全三原语。

**实现记录**（2026-10-28 完成，TDD 49 例先行 → 全绿后真机验收）：

- 服务层 `services/layout_orchestrator.py`：LayoutPrimitive/PipCorner/
  LayoutSegment/LayoutTimeline 数据模型；`validate_layout_timeline`
  契约校验（时间轴完整性/构图护栏 ≤10 段 ≥1.5s/引用存在性/bg_color）；
  `parse_layout_output` 防御式解析（坏段跳过、越界 clamp、乱序排序、
  pip 缺位补默认）；`fallback_timeline` 单段静态 pip；`build_render_plan`
  **纯函数编译器**（cmd[0] 恒字面量 "ffmpeg"，真实二进制路径由执行器
  解析 — 编译不依赖环境，可单测）— full=剪切重编码、pip=letterbox
  配图/纯色背景+角落人像窗口、broll=配图全屏无人像，段间无 fade
  （布局切点在语流中，区别于 smart_cut 静音切口）；`compile_layout`
  执行器（逐段渲染 → concat demuxer，段参数统一 → -c copy 安全）；
  `generate_layout_plan` LLM 规划 + 校验反馈重试 ≤3 次；
  `review_layout` VLM 每段中点抽帧审查（复用 overlay_review 抽帧/
  110KB 分块/data URI 压缩，坏 JSON/异常降级通过绝不阻断）
- skill 层 `skills/dynamic_layout.py`：闸门对齐 talking_head_overlay —
  standalone 首调出 `layout_plan_ready` 事件等确认，**确认回调
  （layout_plan 原样回传）校验后直接编译不重新规划**；auto_confirm
  广播即出片（供 auto_short_video 内嵌）；产物隔离
  `{stem}_layout_{plan_key}.mp4`（md5 timeline）
- 测试 `tests/test_dynamic_layout.py` 49 例：模型校验 17 / 防御解析 7 /
  降级 2 / 编译器 9 / 规划重试 6 / VLM 审查 4 / skill 闸门 6。
  视频管线全量回归 14 文件 596 项 **595 通过 / 1 跳过 / 0 失败**
- **真机验收**（22s 样本 + 6 张 storyboard 真图，transcript 由
  storyboard captions 累计建轴）：LLM 产出 6 段三原语 timeline —
  FULL 0-3s 开场 → PIP×3 3-15s（shot:0/1/2 与"第一件/第二件/第三件"
  叙事精准对齐，配图作主画面）→ BROLL 15-19s 总结配图全屏 →
  FULL 19-22s 收束。成片 22.02s ≈ 源 22.00s，3.85MB。
  产物 outputs/dynamic_layout/01M60QX2QHZBV9VAY3ZABMV2MY_layout_*.mp4
- **真机教训**：`_resolve_vlm` 用 QwenVLAdapter 默认模型名 → 404
  Model not exist（降级契约生效未阻断，但审查没真跑）。修复：模型名
  走 tier router 解析（对齐 overlay_review 的
  `get_tier_router().resolve(PipelineStage.VL_UNDERSTAND, "vl")`）。
  修复后 6 帧审查真跑通过、零告警
- 真机验收脚本 `tests/test_layout_live.py`（standalone，对齐
  test_overlay_live.py 惯例）

**仍未闭环**（如实）：切点硬切无转场（转场层后续，编译选项 B 前置）；
真人出镜素材 PIP 挡脸仍零实证（VLM 审查检查项已覆盖，结论待真实素材）；
auto_short_video 内嵌 `enhance_layout` 未接（skill 已备 auto_confirm
通道，接入属主管线批次）。

---

### 10.19 对话式修改层（2026-10-28 方案 → 当日批准实现并真机验收）

**需求**（用户 10/28 定的下一优先级）：用户说改哪就改哪 —
"字幕改大点""第 3 个卡片删掉""整体风格活泼点"，复用 P0 审片反馈
通道，对标 Remotion Claude preview-first（看预览 → 说话 → 改 → 再看）。

**现状缺口**（代码级核实）：`generate_overlay_plan` 的 feedback 参数
是"前置拼接进规划 prompt"的全局指令式（overlay_orchestrator.py
L793）— 重规划 = 全片重新洗牌。用户只想改一处时，没提到的段也会
被改坏。对话式修改的核心是**局部修改不波及无辜**。

**两级路由**（方案核心，LLM 只做路由与解析，不做执行）：

```
用户反馈（自然语言）
  → LLM 意图解析（一次轻调用）→ {scope: "local" | "global", ...}
  ├─ local（"第3个卡片删掉"/"标题字号改大"）
  │    → 解析为修改指令 {segment_ids | selector, action, params}
  │    → 代码级 patch 当前 plan（确定性，不重规划）
  │    → patch 后 plan 重过 冲突消解 + 密度双指标自检
  │    → 只重渲受影响段的预览
  └─ global（"整体风格再活泼点"/"太单调了"）
       → 走现有 feedback 重规划通道（P0/P2 同路）
       → 全量重渲（现状行为，接受洗牌）
```

**数据结构**：

```python
class RevisionAction(str, Enum):
    UPDATE = "update"    # 改参数（字号/颜色/文案/模板内参数）
    DELETE = "delete"    # 删段
    MOVE = "move"        # 调时间（start/end 平移）
    ADD = "add"          # 加段（少见，多数应走 global）

class RevisionInstruction(BaseModel):
    segment_ids: list[str]        # OverlaySegment.id 定位（VLM 审查
                                  # issue 已有 segment_id 关联先例）
    action: RevisionAction
    params: dict[str, Any]        # UPDATE 时的参数 delta
    raw_text: str                 # 用户原话（审计）

class RevisionResult(BaseModel):
    route: str                    # local / global
    instructions: list[RevisionInstruction]
    patched_plan: OverlayPlan | None   # local 时产出
    self_check_issues: list[str]       # patch 后自检问题
```

**三重锁映射**（G1/G2 同款，用户认可的模式）：
1. LLM 只产 RevisionInstruction JSON（纯数据），不产 HTML/不产命令
2. patch 执行器是纯代码：白名单 action（4 选 1）+ 定位校验
   （segment_id 必须命中当前 plan）+ 参数白名单（只能改模板 schema
   内的字段，text_color 走 10.17 的对比度校验）+ patch 后强制过
   resolve_overlay_conflicts + validate_plan_rhythm 自检
3. 渲染走既有 Playwright/闸门，零新执行面

**闸门与循环**（preview-first）：
- 修改后重出 `overlay_plan_ready`（data 带 revision 标记 + 改动摘要）
  → 用户再确认 → 重渲预览 → 还不满意继续说话 → 直到确认出片
- 每轮修改的 instructions 原话落 report.json（审计链：哪句话导致哪改动）
- 修改轮数护栏：单会话 ≤5 轮（防无限改，超限提示换 global 或重规划）

**改动面**（最小化）：
- overlay_orchestrator.py：新增 `parse_revision_intent(llm, raw_text,
  current_plan)` + `apply_revision(plan, instructions)`（两个纯函数）
- talking_head_overlay.py：OverlayInput 新增 `revision` 参数
  （{raw_text, current_plan}）— 确认回调同款模式（G2 已验证该模式），
  execute 开头路由：有 revision → 走修改路径，无 → 现有规划路径
- 不动渲染层/模板层/审片层

**TDD 与验收**：
- 单测：意图解析（坏 JSON/越权 action/定位失败）× patch 执行器
  （update/delete/move 各例 + 冲突自检不过拒绝 + text_color 走对比度
  校验）× skill 路由（revision 进修改路径 / 无 revision 原路径）
- 真机验收（22s 样本）：plan_ready 后喂"第 2 个字幕的字号改大一点"
  → 断言仅该段 params 变化、其余段逐字节一致；喂"整体再紧凑点"
  → 断言走 global 重规划

**风险（如实）**：
1. 意图解析歧义（"那个蓝色的卡片"指哪个）→ 解析 prompt 要求 LLM
   回显定位依据，定位失败时反问而非猜（宁可多问一轮）
2. patch 后密度自检可能不过（删段造成空白超阈值）→ 如实报问题
   回给用户，不静默修
3. ADD 动作参数空间大 → 第一期只放 update/delete/move，ADD 走 global

**待用户确认的决策点**：
1. 两级路由（推荐）vs 纯 feedback 全量重规划（简单但改一处洗全片）
2. 修改轮数护栏 ≤5（推荐）vs 不设限
3. 第一期不含 ADD（推荐）vs 全四动作

**实现记录**（2026-10-28 用户批准开工：两级路由 + ≤5 轮 + 不含 ADD，当日完成）：

- 服务层 `overlay_orchestrator.py` revision 层：
  - 数据模型 `RevisionAction`（UPDATE/DELETE/MOVE，ADD 后置）/
    `RevisionInstruction{segment_ids, action, params, locate_hint, raw_text}` /
    `RevisionResult{route, instructions, reason}`；
    `_UPDATE_FORBIDDEN_KEYS = {time_start, time_end, segment_id, template, skipped}`；
    `_HEX_COLOR_RE` 颜色格式校验
  - `parse_revision_intent(llm, raw_text, current_plan, duration)`（三重锁第 1 层：
    LLM 只产纯数据 JSON）— prompt 喂 `_revision_plan_digest`（跳 skipped、params
    只留标量前 6 项）+ 用户原话，要求回显 locate_hint、定位拿不准归 global 不猜；
    坏 JSON/scope 缺失 → (None, problems)；local 逐指令校验（action 白名单 +
    segment_id 必须命中当前 plan + 越权指令剔除并带 hint 进 problems）
  - `apply_revision(plan, instructions, duration)` 纯函数 patch 执行器
    （model_copy(deep=True)，零执行自由度）— DELETE → skipped=True（对齐冲突
    消解先例：可追溯不真删）；MOVE 双值校验 + clamp[0,duration] + end<=start
    回退原值；UPDATE 禁改键/非法色拒整个指令、合法键 merge；尾部强制过
    `resolve_overlay_conflicts` + `validate_plan_rhythm`（warning 并入 problems
    如实上报，不静默修）
- skill 层 `talking_head_overlay.py`：
  - `RevisionInput{raw_text, current_plan, revision_round=1}` +
    `OverlayInput.revision`（确认回调同款模式）；`_MAX_REVISION_ROUNDS = 5`
  - execute 路由：round>5 → `revision_rejected`；`_revision_plan` 返回 plan=None
    → `revision_clarify`（problems 回喂反问，不猜）；global →
    `generate_overlay_plan(feedback=原话, use_two_step=True)`；local →
    `apply_revision`
  - `overlay_plan_ready` 事件 data 带 `revision` 标记（route/round），
    instructions.raw_text 落审计链；`_build_timed_words` 自 `_generate_plan`
    抽出共用（whisper 逻辑未动）
- 测试 `tests/test_overlay_revision.py` 24 例（意图解析 8 / patch 执行器 11 /
  skill 路由 5）。回归：视频管线 20 文件 678 项 **673 通过 / 5 跳过 / 0 失败**
  （10.18 的 14 文件口径超集）；另跑全 tests/ 目录，43 处 FAILED/ERROR 全部
  为非管线存量（reactor session/collab event loop/tavily 配额 432/
  test_overlay_via_api 缺 fixture），与 10.19 无关，如实记录口径
- **真机验收**（22s 样本，`tests/test_revision_live.py` standalone）— **PASS**：
  - 初次规划 13 段（LLM 产出重复 segment_id s1-s6 → 防御解析器自动改名
    o1-o7，防线生效）
  - local：「第 2 个字幕的字号改大一点」→ UPDATE `o1`（1.6-3.0s caption_pop，
    locate_hint 精确回显"第二个字幕即 o1"），patch 后仅该段 params 变化
    （新增 font_size）、其余 12 段逐字节一致
  - global：「整体再紧凑点」→ 归 global（理由：全局节奏/密度调整无法落到
    具体段），feedback 重规划出 11 段（原 13 段，"更少段"语义吻合紧凑）
- **教训与挂账**：① pydantic v2 BaseModel 自身可迭代（产出字段对）→ mock
  返回值必须二元组，单值会被 `result, problems = ...` 静默解包错；② skill 模块级
  import 后既有测试 patch 目标须迁移（test_talking_head_overlay 1 例改 patch
  skill 命名空间）；③ 真机实证 LLM 会给 `font_size='larger'`（CSS 关键字而非
  数值）— 无 schema 校验，渲染成无效 CSS 回退默认（不崩但无效）→ 新增挂账：
  update params 数值型字段（font_size 等）类型校验

---

### 10.20 人物抠像接入幻灯片（2026-10-29 方案 → 当日批准实现并真机验收）

**需求**（用户 10/29）：将口播人物从原背景真抠出（alpha 透明融合），
作为前景放在与内容相关的幻灯片上 — 区别于现有 PIP 矩形小窗（带原背景）。

**环境事实**（2026-10-29 实测）：GPU RTX 4060 Laptop 8GB 可用；venv Python
3.12.4，torch 未装（需 ~2.5GB CUDA wheel）、cv2 已有、mediapipe 未装。

**模型选型**（视频人物抠像）：

| 候选 | 结论 | 理由 |
|------|------|------|
| **RVM**（Robust Video Matting, PeterL1n） | **选用** | 专为视频设计（循环时序稳定，无逐帧闪烁）、发丝边缘好、Apache-2.0、torch、HD 实测 GPU 30+FPS、权重仅 ~175MB |
| MediaPipe Selfie Segmentation | 拒 | mask 256×256 上采样糊、发丝差、无时序稳定设计 |
| MODNet | 拒 | 单帧质量好但视频闪烁 |
| BiRefNet 等 SOTA 静态抠像 | 拒 | 单图导向、重、慢，对视频无时序优势 |

**架构**（对齐三重锁 + 降级契约）：

1. 新 adapter `capabilities/video/adapters/person_matting.py`：
   - `matte_person(video_path, out_dir) -> list[Path] | None` — RVM 逐帧
     推理产出 rgba PNG 帧序列（一次性离线预算，plan_key 缓存复用）；
     模型加载失败/权重缺失/无 GPU → 返回 None（降级契约：审查类增值步骤
     绝不阻断，同 overlay_review 先例）
   - 依赖注入：torch/RVM 权重路径可配置，单测全部 mock（零真模型依赖）
2. `talking_head_overlay._execute_pip` 扩展 `matting: bool = False`：
   - True → 主画面幻灯片全屏 + 人物 alpha 帧序列 ffmpeg `overlay` 缩放定位
     （复用 pip_position/pip_scale 语义但无框融合）；matting 返回 None →
     **回退现有矩形 PIP**（降级链：matting → 矩形 PIP → 报错，不静默出空片）
3. 幻灯片来源（本期）：`deck_html`（已有参数）或 storyboard 配图作全屏主
   画面；**LLM 按口播内容生成 deck 列二期**（需追加批准，本期不含）
4. 合成：ffmpeg filter_complex，人物帧 image2 demuxer overlay 到 deck，
   音频直拷 — 确定性命令，与 G2 编译器同款纪律
5. VLM 审查复用：抽帧检查边缘残留（原背景色边）/人物缺失/比例失调

**性能预算**：22s×30fps=660 帧，RVM@RTX4060 GPU ≈ 1-3 分钟；**CPU 不选**
（逐帧太慢，无 GPU 环境直接走降级链）。

**闸门**：preview-first — 抠像完成后先出首帧/中间帧/结尾帧拼图预览等确认，
再全量合成（对齐项目三闸门惯例）。

**改动面**（最小化）：新 adapter 1 个 + `_execute_pip` 加 matting 分支 +
降级链；不动渲染层/模板层/审片层核心。依赖新增：torch（CUDA wheel）+
RVM 权重（~175MB）。

**TDD 与验收**：
- 单测：matte_person mock torch（正常/加载失败/权重缺失/无 GPU 降级）×
  _execute_pip matting 分支（成功融合/降级矩形 PIP）— 预估 ~12 例
- 真机验收：22s 样本跑通全链 → 出片 + 首中尾帧人工看边缘质量 +
  VLM 审查报告

**风险（如实）**：
1. torch CUDA wheel ~2.5GB 下载安装耗时、占磁盘
2. RVM 权重 GitHub release 下载可能受网络限制 → 提供手动放置路径兜底
3. 边缘质量对真实素材零实证（与"真人 PIP 挡脸零实证"同类风险）—
   VLM 审查 + 人工闸门兜底；最坏情况结论为"该素材 RVM 不适用"
4. 人物出画/占比过小时 RVM 表现未知
5. "与内容相关"的语义对齐本期靠 deck_html/配图人工指定，LLM 生成 deck
   属二期

**待用户确认的决策点**（2026-10-29 用户已裁决）：方案批准开工 / 验收素材
22s 验证样本 / 幻灯片来源 storyboard 配图 — 三项全按推荐执行。

**实现记录**（2026-10-29 当日完成）：

- 依赖落地：torch 2.6.0+cu124（CUDA wheel ~2.5GB，RTX 4060 Laptop 识别
  正常）+ RVM mobilenetv3 TorchScript 权重 15.5MB（`models/rvm/`，
  官方 release；**TorchScript 免 vendor model.py**，比 .pth 方案少一份
  代码依赖）。RVM 自检：`rec=[None]*4` 初始调用合法（导出包装内部
  初始化），实测 4.3 fps@1080x1920 → 660 帧约 2.5 分钟（方案预算内）
- 新 adapter `capabilities/video/adapters/person_matting.py`：
  `matte_person(video_path, out_dir) -> MatteResult | None`（frame_dir +
  count/fps/width/height）；`_load_model` / `_probe_fps`(PyAV) /
  `_matte_sync`(av 读帧 → RVM 流式推理 → 逐帧 PNG 落盘，rec 隐状态跨帧
  传递，downsample_ratio 按长边 ~512 自适应) / `_write_frame`(straight
  alpha RGBA) / `_try_cache`(meta.json+末帧存在即复用) /
  `_probe_image_size`。降级契约：任何失败返 None 绝不抛
- skill 层 `_execute_pip` 扩展：`OverlayInput.matting: bool` +
  `deck_images: list[str]`（配图直进幻灯片管线，画布取首张配图分辨率 —
  竖版配图沿用横版会压成黑边小条）；matting 分支 ffmpeg 三输入：
  主画面（slides/lavfi 纯色底）+ 人物帧序列（image2 demuxer）+ 原视频
  （音频源），`[1:v]scale→[0:v]overlay format=auto` alpha 融合；
  失败 → `pip_matting_fallback` 事件如实广播 → 回退矩形 PIP；
  `_pip_key` 指纹纳入 matting/deck_images；pip_done 带 matting 标记
- 测试 `tests/test_person_matting.py` 13 例（adapter 8 + skill 分支 3 +
  输入模型 2）全绿；管线回归 21 文件 691 项 **686 通过 / 5 跳过 /
  0 失败**
- **真机验收**（22s 样本 + storyboard 六张配图，`tests/test_matting_live.py`）：
  - **机制全链 PASS**：RVM GPU 抠像 → alpha 帧序列 → 融合合成 →
    成片 22.80s（源 22.00s）；降级链真实验证（修复权重加载前一次运行
    抠像失败 → `pip_matting_fallback` → 矩形小窗成片 22.02s，回退路径
    实证可用）
  - **边缘质量不可评估（如实）**：该样本是 TTS 幻灯片视频（AI 配图+
    字幕），无真人出镜。RVM 实际抠出的是插画中的"人物"（采样帧 alpha
    均值 0.36/0.54/0.46，非零证据），叠加到同画面幻灯片上像素级重合
    伪装不可见。**真人素材的边缘质量（发丝/残边）零实证** — 需用户
    提供真人出镜视频做最终验收
- **真机实证修掉的 4 个环境坑**（都有复现记录）：
  1. `_slides_to_video` 裸 "ffmpeg" 起子进程 → WinError 2（本机
     ffmpeg 不在 PATH）→ 改走 `_run_ffmpeg`；顺带在 `_run_ffmpeg`
     中央修：cmd[0]="ffmpeg" 时走 `resolve_ffmpeg()` 解析链（PATH →
     FFMPEG_PATH → imageio-ffmpeg），覆盖所有同类调用
  2. torch C++ 层 fopen 对中文路径报 errno 2（自检用相对 ASCII 路径
     通过、adapter 绝对路径失败 — 对照定位）→ `read_bytes + BytesIO`
     加载
  3. `_slides_to_video` 期望 Path（内部 `as_posix()`）→ 传参补
     Path 转换
  4. Windows 中文路径 cv2.imwrite/imread/VideoCapture 静默失败 →
     adapter 全面规避：imencode+write_bytes 写、fromfile+imdecode 读、
     av 读视频（对齐 `_probe_resolution` 惯例）
- 挂账：① 真人素材边缘质量验收（等用户素材）；② 抠像缓存目前落在
  会话 tmp（跨会话复用需调用方传持久目录，live 脚本未做）；③ 成片
  22.80s vs 源 22.00s 的 +0.8s（-shortest 与 libx264 B 帧的尾帧冻结，
  非阻断）

**二期实现记录**（2026-10-29 当日：用户上传真人视频并指令"根据讲解内容
进行一个内容渲染"= 批准二期开工，当日全链 PASS）：

- 新服务 `services/deck_orchestrator.py`（讲解内容 → 要点幻灯片）：
  转录全文 → LLM 要点契约（`_DECK_PROMPT`：首张开场主张/末张行动号召/
  title ≤14 字/bullets 2-3 条各 ≤18 字，只产 JSON 禁 HTML）→
  `parse_deck_json` 防御式解析（正则抽 JSON 数组、字段类型强校验、
  title 截 20 字 bullets 上限 4 条、坏数据逐张剔除）→ 确定性模板
  `_slide_html`（竖版 9:16 深色渐变 + 大标题 + 要点卡 + 页码）→
  Playwright 逐页截图 PNG → 直接喂 10.20 的 deck_images 通道。
  降级契约：LLM 失败/解析空/渲染异常 → []（调用方回退纯色底）。
  LLM 调用走 `llm_prompt_to_text`（对齐 2026-10-17 收口教训）。
  TDD：`test_deck_orchestrator.py` 7 例（正常/代码围栏包裹/坏 JSON/
  非数组/字段类型/空 bullets/截断封顶）全绿
- **真人素材边缘质量验收 PASS**（10.20 一期挂账闭环）：54.1s 竖屏
  684x1174 真人口播 → 成片 `live_person_talk_pip_presentation_7d2e9472.mp4`
  （56.07s）；首/中帧检查：毛线帽边缘清晰、下颌线/肩部轮廓自然过渡、
  无矩形边框、无原背景残留；RVM 时序稳定全片一致；人物小窗 324x556
  bottom_right 融入自然；幻灯片内容与讲解精准对齐（"夏天别大意，三件
  事最伤身"开场 → "大汗后猛灌冰水最伤肠胃"等逐张推进）；LLM 把
  whisper 繁体转录正确转写为简体幻灯片文案
- **真机实证第 5 个环境坑（最有价值）**：faster-whisper(ct2) 构造
  WhisperModel 会把系统 PATH 上旧版 cuDNN DLL 拉进进程，同进程后续
  torch CUDA conv 直接 native fastfail（cudnnGetLibConfig Error 127，
  0xC0000409，不可 try 捕获）— 最小对照实验实锤（ct2 构造 → conv 崩；
  无 ct2 → conv OK）。修复两层：① `whisper_transcribe._try_faster_whisper`
  加 `WHISPER_DEVICE` 环境开关（cpu 逃生门）；② 验收脚本转录与 RVM
  **分进程**（--transcribe 子阶段出 transcript.json，主阶段 deck+matting）
- 回归：改动相关 5 文件 82 项全绿（deck 7 / matting 13 / smart_cut /
  talking_head / revision）
- 二期遗留挂账：① deck 生成尚未接进 talking_head_overlay 输入契约
  （当前 live 脚本手串 whisper→deck→skill；产品化需给 OverlayInput 加
  auto_deck 开关）；② 幻灯片切换与语音进度对齐目前均分时长
  （slide_duration 均分，未按转录句边界对齐 — 内容推进不同步的观感
  风险）；③ deck 模板单主题（深色渐变），无风格可选

**二期优化**（2026-10-29 用户裁决只做"人物让位+尾帧修复"两项）：

- 人物让位：`_slide_html` 加 `person_clearance_px`（0 默认向后兼容；
  >0 时 body padding 三值简写底部预留，要点卡整体上移避开右下角人物
  区）；透传 render_deck_slides/generate_deck_slides；live 脚本传 640
  （人物 556px+margin）。真机首帧验证：卡片底部距人物区 300px+，
  此前被头顶压住的遮挡消除
- 尾帧修复：matting 合成 `-t {duration:.3f}` 硬限（根因：`-shortest`
  是 muxer 层软掐，x264 lookahead 多带 1-2s → 成片 56.07 vs 源 54.1）；
  修后 54.08 vs 54.1，精确
- TDD 2+1 例（padding 简写断言 — 首版断言误写长写形式被红抓出）；
  相关 22 例全绿
- 过程实录：DeepSeek 402 余额不足 → deck 降级路径真实验证（live 脚本
  转录直切 fallback 出片，验收不阻塞；内容为繁体原文直切质量降级 —
  内容质量依赖 LLM，充值后恢复）；skill 降级哲学第三次真机兑现
  （deck 空 → 纯色底 / LLM 不可用 → fallback / 抠像失败 → 矩形 PIP）

---

### 10.21 auto_deck 一键产品化 + deck 多主题（2026-10-29 方案 → 用户批准当日完成；主题集按推荐三主题执行）

**需求**（用户点名两项挂账）：① `OverlayInput.auto_deck` 一键出片
（管线内部自动转录→LLM 幻灯片→抠像融合，不再脚本手串）；② deck 多主题。

**改动面**（最小化，两文件 + 测试）：

1. **deck_orchestrator 多主题**：
   - `_DECK_THEMES: dict[str, dict]`，三主题：
     - `midnight`（现有深色蓝紫渐变，默认）
     - `cream`（米白亮底 + 墨色字 — 知识/科普向）
     - `sunset`（暖橙渐变 + 白字 — 生活/情感向）
   - `_slide_html(theme=...)` 样式字段全部由主题 dict 注入（bg/标题色/
     卡底/卡边框/字色/圆点色/accent 渐变）；非法主题名 → 回退 midnight
     （防御，不抛）；`render_deck_slides`/`generate_deck_slides` 透传
2. **talking_head_overlay auto_deck**：
   - `OverlayInput` 新增：`auto_deck: bool = False`、
     `deck_theme: str = "midnight"`、`deck_max_slides: int = 6`
   - `_execute_pip` 前置分支：`auto_deck and not deck_images` 时 —
     - 文本来源：`parsed.transcript` 有 → 直接拼原文（已知文本通道，
       零 whisper 成本）；无 → whisper 兜底（机制修正见下）
   - LLM 要点契约 → `generate_deck_slides(theme=...)`；**降级链**：
     LLM 失败/空 → `deck_fallback` 事件如实广播 → 走纯色底主画面
     （现有逻辑，不阻断出片）
   - `deck_ready` 事件（张数/主题）；无新增闸门
3. **TDD**：主题 9 例（三键存在/字段契约/默认 midnight/cream/sunset
   注入/非法回退/三底色互异/render+generate 透传）+ auto_deck 8 例
   （输入契约 2/transcript 优先+theme 透传/子进程兜底/LLM 空降级
   deck_fallback/成功进管线）+ 子进程帮助函数 2 例

**方案机制修正**（实现期发现，对方案原文的纠正）：方案原写"whisper
转录同进程强制 CPU" — 与 v2.7 实证第 5 坑**矛盾**：ct2 构造
WhisperModel（即使 device=cpu）即把旧 cuDNN DLL 拉进进程，同进程
RVM torch conv 必崩且不可捕获。live2 当初正是为此做分进程编排。
auto_deck 是"一键"产品化，不能依赖调用方编排 → 实现为
`_transcribe_cpu_subprocess`：skill 内起独立 Python 子进程转录
（子进程 env 强制 `WHISPER_DEVICE=cpu`，父进程 GPU 全留 RVM），
失败/超时/输出不可解析 → []（不阻断，走 deck_fallback）。

**实现记录**（2026-10-29 当日完成）：

- deck_orchestrator：`_DECK_THEMES` 三主题 dict（7 个样式字段：body_bg/
  text_color/bullet_bg/bullet_border/dot_color/accent_bg/muted_color），
  `_slide_html` 硬编码样式全部改为 `t["..."]` 注入，非法主题名回退
  midnight（`.get(theme) or` 防御）；render/generate 全链透传
- talking_head_overlay：OverlayInput 三字段（auto_deck/deck_theme/
  deck_max_slides）；`_execute_pip` 前置 auto_deck 分支（transcript
  优先拼接原文 → 无则 `_transcribe_cpu_subprocess` 兜底 → LLM deck
  → deck_ready/deck_fallback 事件）；deck_images 与 auto_deck 产物
  合并消费（`deck_source = parsed.deck_images or auto_deck_images`）；
  `_pip_key` 指纹纳入 auto_deck/deck_theme；pip_done has_deck 含
  auto_deck 产物；matting 时 person_clearance_px=640 同款让位
- TDD 19 例先行红→绿；两文件合计 **39/39 全绿**
  （deck 18 + matting 21）

**真机验收**（54.1s 真人口播，当日）：

- **全链 PASS（意外）**：live3 脚本原按"402 仍欠费"写死断言必须
  deck_fallback，实际 DeepSeek 余额已恢复 → LLM 真跑通：auto_deck
  一参直出 — deck_ready 6 张（midnight）→ 成片 54.08s（源 54.10s，
  偏差 0.02s，-t 硬限生效）。抽帧实证：首帧"夏天别大意，三件事要
  当心"+3 条要点卡、中帧"大汗后别猛灌冰水" — 内容为 LLM 提炼要点
  （非转录直切），whisper 子进程隔离实证成功（主进程 RVM CUDA
  正常跑完，ct2 污染未发生）；人物小窗 alpha 融合边缘干净
- **已知瑕疵（挂账，非本期引入）**：accent 装饰线 `top:560px` 写死，
  标题占两行时压进第一条 bullet 卡内（10.20 同款模板布局，10.20
  产物一致）— 待模板排版微调批次
- 降级路径真机 PASS（live3b：坏 key `sk-invalid-forced-fallback`
  强制 LLM 失败 + 喂现成 transcript 省转录）：401 → `[deck] 生成失败
  （降级 []）` → `deck_fallback` 事件如实广播 → 纯色底主画面 → 仍出片
  54.08s — 降级链绝不阻断实证。**挂账**：`_pip_key` 指纹未含降级标记
  与画布形态（deck 主画面 1080x1920 vs 纯色底源分辨率），live3b 降级片
  覆盖了 live3 全链成片（同指纹同路径）— 全链证据已抽帧留档
  （live3_frames），指纹语义待后续批次决定是否细分

**回归口径（最终）**：全量 tests/ 1688 项，27 处 FAILED/ERROR **全部
为非视频管线存量** — collab_e2e_chain 16 ERROR + collab_crawler_e2e_chain
6 ERROR（event loop，Python 3.12 存量）、crawler_real_integration 3
FAILED（Tavily 外部 API）、bash_native_commands 2 FAILED（GBK 编码
断言）。视频管线文件 0 红。与 10.19 全量口径记录的存量同类
（"collab event loop/tavily 配额"）。pytest 最终汇总行两次被终端吞
（文件停在 summary 明细），通过数以 1688−27=1661（含 skip）计；
pytest 的最终汇总行写入在本机 PowerShell 重定向下不稳定，后续回归
以 summary 明细段为准。

**不做**（本期）：幻灯片切换句边界对齐（维持均分）、VLM 审 deck 版式。

---

### 10.22 deck 确认闸门 — 幻灯片先看后合成（2026-10-29 方案 — **待用户确认**）

**需求**（用户原话）：给 pip 线加一道"幻灯片确认闸门"——deck 生成完
先出图给你看（满意才合成，不满意说主题/改内容），改造成本不大。

**现状缺口**（代码级核实）：`_execute_pip` 的 deck 线（deck_images/
auto_deck 产物作主画面）是 auto_confirm 直出 — deck 生成完直接进
合成，用户没看过幻灯片长什么样。overlay 主线有两道闸门
（`overlay_plan_ready` → `overlay_preview_ready`，均
needs_confirmation），deck 线零停点。

**闸门设计**（对齐 overlay 线确认回调惯例）：

```
deck 生成完（deck_ready）
  → 【新】deck_preview_ready 事件（needs_confirmation=True）暂停
     data: {slides_paths[], theme, count, slides_json_path}
  → 用户看图，第二轮 execute 回传决策：
  ├─ deck_confirm="approve"（或 deck_images 原样回传）
  │    → 直接继续合成，零重新生成
  ├─ 换主题：deck_confirm="retheme" + deck_theme=新主题
  │    → render_deck_from_slides(slides.json, 新主题) 重渲染
  │      （零 LLM 成本 — 10.21 已落盘 slides.json）
  └─ 改内容：deck_confirm="regenerate" + revision=自然语言
       → LLM 带反馈重新生成要点 JSON → 重渲染（10.19 模式平移）
  → 不满意可继续轮（护栏 ≤5 轮，对齐 10.19）
```

**改动面**（两文件 + 测试）：

1. **deck_orchestrator**：
   - `generate_deck_slides` 落盘 `slides.json`（解析后的 slides 数据
     + theme）到 out_dir（10.21 已有 PNG，补 JSON — 换主题重渲染免
     LLM 重调的关键）
   - 新增 `render_deck_from_slides(slides_json_path, out_dir, theme,
     ...)`：从已落盘 JSON 直接重渲染（纯确定性，Playwright 截图）
2. **talking_head_overlay**：
   - **deck 产物持久化**（关键前置）：deck PNG/slides.json 从会话
     tmp 移到 `outputs/talking_head_overlay/deck_{key}/`（live3b 已
     实证 tmp 不跨 execute — 确认回调第二轮是新 tmp，不持久化则
     approve 时图已删；顺带部分闭环 10.20 挂账②缓存不持久）
   - `OverlayInput` 新增 `deck_confirm: str | None = None`
     （None=首轮生成；"approve"/"retheme"/"regenerate"=确认回调轮）
     + `deck_slides_json: str | None`（回调轮回传）+
     `deck_revision: str | None`（regenerate 的自然语言反馈）
   - 闸门路由：standalone/CHAINED 且 deck 生成成功 → 停
     `deck_preview_ready`；AUTO_CONFIRM（auto_short_video 内嵌）→
     不停直接合成（无人值守契约不变）；deck_fallback（无图）→ 不进
     闸门直接纯色底（无物可审）
   - revise 轮数护栏 ≤5（复用 `_MAX_REVISION_ROUNDS` 语义）
3. **TDD**（预估 ~12 例）：slides.json 落盘 1 / render_from_slides 2
   （正常 + 换主题与原渲染一致）/ 闸门停点 1 / auto_confirm 跳过 1 /
   approve 放行 1 / retheme 零 LLM 重渲染 2 / regenerate 内容重生成 2
   / fallback 不进闸门 1 / 轮数护栏 1 / 输入契约 1

**真机验收**：54.1s 真人样本走全流程 — 首轮生成 → 停出图（6 张
PNG 人工看）→ approve → 成片；加验一轮 retheme（midnight → cream
对比首帧）；三主题首帧各一张人工看版式（10.21 欠的版式验收补上）。

**风险（如实）**：
1. 确认回调轮的 deck_key 稳定性：deck 持久目录 key 若含 deck_theme，
   retheme 后产物目录漂移 → key 用 auto_deck/内容指纹（不含主题，
   主题只影响重渲染文件名）
2. regenerate 的 revision 语义与 10.19 overlay 修改不同（那里是
   patch plan，这里是重新生成）→ 解析不复用 parse_revision_intent，
   直接把用户原话拼进 deck prompt 反馈段（轻量，LLM 重提炼）
3. slide_duration 均分不变 — 句边界对齐仍挂账

**待用户确认的决策点**（2026-10-29 用户裁决：三项全按推荐执行）：
1. ~~方案批准开工~~ → 批准
2. ~~闸门默认行为~~ → standalone 默认停（对齐 overlay 线）
3. ~~regenerate 接口~~ → 自然语言拼 prompt（轻量）

**实现记录**（2026-10-29 当日完成，TDD 先行红→绿）：

- deck_orchestrator：
  - `generate_deck_slides` 加 `feedback: str | None`（`_FEEDBACK_SECTION`
    拼进 prompt，空则无该段）；解析成功后落盘 `slides.json`
    （theme/transcript/slides，ensure_ascii=False）— retheme 零 LLM
    重渲染 + regenerate 反馈链的数据源
  - 新增 `render_deck_from_slides(slides_json_path, out_dir, theme=None,
    ...)`：读 JSON → theme 显式覆盖 / 缺省用 JSON 内首轮主题 → 复用
    render_deck_slides（纯确定性）；JSON 不可读/缺 slides → []
- talking_head_overlay：
  - `OverlayInput` 加 deck_confirm/deck_slides_json/deck_revision/
    deck_revision_round（默认 None/None/None/0）
  - `_execute_pip` 前置回调路由分支（deck_confirm 有值时）：
    approve = glob 首轮 PNG 直消费（缺失时同版确定性重渲染兜底，
    theme 读 JSON）；retheme = render_deck_from_slides(theme=新主题，
    出 render_{theme} 子目录)；regenerate = JSON 内 transcript +
    feedback 重生成；轮数 >5 → deck_rejected 终止
  - auto_deck 首轮产物目录 tmp → `scope_dir/deck_auto`（持久化 —
    闸门回调轮跨 execute 消费，顺带部分闭环 10.20 挂账②）
  - deck_ready 统一发（回调轮在分支内发，首轮在统一段发 — 防双发）；
    deck_preview_ready **总是广播**（对齐 overlay 主线惯例：auto_confirm
    下 needs_confirmation=False 照发不停），仅首轮+standalone 真停
    （needs_confirmation=True + return）
  - 事件侧契约：deck_preview_ready data = {needs_confirmation,
    slides_paths, slides_json_path, theme, count}
- TDD 净增 11 例（TestDeckGate 8 + TestSlidesPersistence 3），红→绿
  修正 4 处测试基建：① render_deck_from_slides 走 to_thread 必须
  MagicMock 非 AsyncMock；② _run_skill patch 作用域限于内部
  ExitStack — 断言必须持有 mock 引用（事后 import 拿回真函数）；
  ③ mock ffmpeg 加副作用写输出文件（合成末尾 is_file 校验，否则
  'output file not created' 提前终止 — 10.20 同款）；④ auto_confirm
  期望广播 deck_preview_ready(False) + pip_done 同存（首版实现只
  直发不停，对齐主线惯例后过）
- 两文件 50/50 全绿（10.21 为 39，净增 11）

**真机验收**（live4 三阶段，2026-10-29 当日，54.1s 真人样本，
DeepSeek 真调 + RVM 真抠，每阶段独立 execute — 真实跨轮回调契约）：

- **阶段1 首轮停点 PASS**：auto_deck → deck_ready 6 张（midnight）→
  `deck_preview_ready`（needs_confirmation=True）停点，slides.json
  持久化落盘，无 pip_done（合成未启动）
- **阶段2 approve PASS**：deck_confirm=approve 回传 slides.json →
  零 LLM 直合成 → 成片 54.08s（源 54.10s）
- **阶段3 retheme PASS**：deck_confirm=retheme + deck_theme=cream →
  零 LLM 确定性重渲染 6 张 → cream 成片 54.08s
- **三主题版式验收（补 10.21 欠账）**：midnight（live3）/cream（live4
  首帧）对比 — cream 米白渐变底、墨色标题、暖橙圆点/强调线全部生效，
  人物 alpha 融合边缘干净；**accent 装饰线压字瑕疵在 cream 同样可见**
  （top:560px 写死 + 标题两行，10.20 挂账复现，亮底下更显眼）

**回归口径（最终）**：全量 tests/ 1699 项（10.21 为 1688，净增 11），
23 处 FAILED/ERROR **全为非视频管线存量** — collab_e2e_chain 12 ERROR +
collab_crawler_e2e_chain 6 ERROR（event loop 存量）、
crawler_real_integration 3 FAILED（Tavily HTTP 432 配额超限）、
bash_native_commands 2 FAILED（GBK 编码）。视频管线文件 0 红。

**文档收口**：修订史 v2.10。

---

### 10.23 工程挂账清理（2026-10-29 方案 — **已批准 A-D**）

**背景**（用户原话）：先清工程挂账。候选 5 项，代码现状已核实。
**用户裁决**：A-D 四项执行，E（VLM 审版式）继续挂账。

**A. accent 装饰线压字**（小）
- 现状：`_slide_html` 的 `.accent` 线 `position:absolute; top:560px`
  写死 — 标题两行（高度 >560px）即压字，10.20 挂账、10.22 真机
  复现（cream 亮底下更显）
- 修法：accent 从 absolute 改为文档流元素（`.slide-content` 内 h1
  之后，margin 分隔）— 标题高度自适应，结构上不可能重叠
- 测试：两行长标题 → html 无 `top:560px`、accent 在 h1 之后文档流

**B. update params 数值型校验**（小）
- 现状：overlay_orchestrator UPDATE 段已有禁键 + 颜色格式校验
  （L1081-1097），缺数值校验 — 10.19 真机出过 `font_size='larger'`
  （模板参数是 int px，'larger' 无效 CSS）
- 修法：`_UPDATE_NUMERIC_KEYS = {"font_size"}`，值必须
  isinstance(int|float) 且 8≤v≤300 像素合理域，非法拒整个指令
  （problems 路径与 bad_color 同款）
- 测试：'larger' 拒 + params 不改 / 88 过 / 越界拒

**C. _pip_key 指纹补形态标记**（小）
- 现状：指纹未含画布形态 — live3b 降级片（纯色底 684x1174）覆盖了
  live3 全链片（deck 1080x1920）同路径（10.21 挂账）
- 修法：_pip_key 加形态段 — 有 deck 图 → `deck{theme}{count}`，
  fallback → `plainfallback`，产物路径互不覆盖
- 测试：同参数 deck vs fallback 指纹不同

**D. 幻灯片切换句边界对齐**（中，10.21/10.22 两期"不做"转正）
- 现状：_slides_to_video concat 每页 slide_duration 均分（首页 +0.8s
  交叉余量），切换时机与讲解内容无关
- 修法：新参数 `durations: list[float] | None`（None 走旧均分，向后
  兼容）；auto_deck 时 transcript 句时间戳按比例映射 N 张幻灯片
  （M 句 → 每张 ceil(M/N) 句块，该块首末句 start/end 差即页时长），
  头尾页夹住全片（首页从 0 起、末页到 duration 止）
- 风险（如实）：单句超长 → 该页跟着长 — 接受，切换跟内容走优先
- 测试：durations 传递 / 均分兼容 / 映射首尾夹住

**E. VLM 审 deck 版式**（中，模型可用性不确定）
- 现状：无自动质检，压字类问题全靠人眼（闸门已给人工看图停点，
  E 是锦上添花的自动层）
- 修法：deck PNG → vision 模型（model_router 有 supports_vision 路由，
  LLMMessage.content 支持 multimodal）审每张：溢出/重叠/对比度 →
  deck_review 事件（warning 级 issues 列表，**不阻断**，降级契约）
- 不确定性：vision 模型真机可用性未验；需先真机探一次再定测试
- 测试：视真机探测结果定 — 如实，先探后写

**验收**：TDD 各项先红后绿 + 两文件全绿 + 全量回归 + live5 真机
（A/D 走 54.1s 样本：新模板渲染对比 + 句边界切换抽帧看时机）。
B/C 纯单测覆盖足够（修改层与指纹无真机面）。

**范围待定**：~~全做（A-E）/ A-D（推荐，E 探测后再定）/ 只做小三项
A/B/C~~ → 用户选 A-D。

**实现记录**（2026-10-29 当日，TDD 10 例先行红→绿）：

- **A**（deck_orchestrator._slide_html）：`.accent` 去掉
  `position:absolute; left:110px; top:560px`，改文档流元素
  （h1 之后、margin:0 0 56px 分隔）；h1 下边距 90→48（分隔由 accent
  margin 承担）。版式顺序 idx → h1 → accent → bullets。**压卡根因**：
  accent absolute 脱流，标题行数变化时 bullets 文档流上移/下移而
  accent 钉死 560px
- **B**（overlay_orchestrator UPDATE 段）：`_NUMERIC_KEYS = {"font_size"}`
  值必须 isinstance(int|float) 且非 bool 且 8≤v≤300，非法拒整个指令
  （problems 与 bad_color 同款路径，拒绝后原值保留非删键）
- **C**（talking_head_overlay._pip_key）：形态段进指纹 —
  `deckauto{theme}{count}`（auto_deck 产物）/ `deckmanual`（用户传图）/
  `deckfallback`（降级纯色底）/ `plainpip`（无 deck）。**根因实锤**：
  旧指纹取输入 deck_images（live3 与 live3b 输入均为空 → 同指纹），
  生成形态从未参与
- **D**（talking_head_overlay）：
  - 模块级 `_slide_durations_from_transcript(rows, n, total)`：
    M 句 → 每页 ceil(M/N) 句块，页时长 = 块末句 end − 块首句 start，
    首页 start 夹 0、末页 end 夹 total（durations 连续覆盖全片）；
    空 rows → []（调用方走旧均分，向后兼容）
  - `_slides_to_video` 加 `durations: list[float] | None`；句边界
    模式**不加 0.8 交叉余量**（每页累计漂移会吃掉对齐精度 — 均分
    模式保留余量旧行为）
  - 接线：auto_deck 时 `_deck_transcript_rows`（parsed.transcript
    优先 / whisper 子进程 rows 兜底）→ 映射 → 传 durations；手动
    deck_images 无此约定 → 均分
  - **组合缺陷修复**（实现中发现）：闸门回调轮不传 transcript →
    句边界退化均分 = 停点确认把对齐废掉。补：首轮 rows 持久化
    `deck_auto/transcript_rows.json`，回调轮回读（approve/retheme/
    regenerate 均保持句边界对齐）
- TDD 10 例（A2+B3+C1+D4），红→绿；测试侧修正 3 处：①
  RevisionInstruction 字段名是 `segment_ids` 非 target_ids；② 拒绝
  断言应为"原值保留"而非"键不存在"（段原始 params 本含 font_size=64）；
  ③ 句边界 concat 断言 6.0 非 6.8（漂移语义修正）
- 三文件 85/85 全绿（deck 21 + person_matting 54 + revision 10，
  净增 10）

**真机验收补记**（live5，2026-10-29 当日，54.1s 真人样本）：

- 阶段 1（whisper 子进程 + 停点）：**PASS** — 24 句 transcript rows
  持久化到 deck_auto/；standalone 停点 deck_preview_ready
  needs_confirmation=True
- 阶段 2（approve 句边界出片）：**PASS** —
  live_person_talk_pip_presentation_fbf0c8a0.mp4（54.08s）；句边界
  durations [5.8, 11.5, 7.6, 9.3, 8.0, 9.7]（均分对照 [9.0×6]，
  切换点跟随讲解句边界，D 生效实证）
- 阶段 4（A accent 超长标题重渲染）：**PASS** —
  live5_frames/accent_long_title.png（--stage4 补跑入口；
  render_deck_from_slides 走 asyncio.to_thread，Playwright sync API
  不进 asyncio loop）；补跑轮断言3 SKIP（见测试卫生）
- 全量回归：24 红（6 FAILED + 18 ERROR）全为存量域 — collab
  event loop 19 / tavily 432 配额 3 / GBK 1 / which 1；
  **视频管线 0 红**
- **测试卫生**（验收中发现）：单测无 owner 注入 → scoped_dir 回退
  真实 outputs 路径 → 单测的 2 句 transcript_rows.json 覆盖 live5
  真机 24 句产物。修复：模块级 _run_skill 统一 patch scoped_dir →
  tmp_path/scope + 删除被污染残留；deck 相关 14 例重跑全绿且真实
  outputs 不再被复写；三文件 85/85 全绿

**不做**（本期）：deck 单页级编辑（只支持整体 approve/retheme/
regenerate）、VLM 审 deck 版式（仍挂账）。
### 10.24 幻灯片内容丰富度升级（2026-10-29 方案，待用户裁决范围）

**用户痛点**："生成的内容不够丰富"（54.1s 样本 6 张：title+2-3 条≤18 字 bullets，纯文字同版式 ×6）。

**归因**（对着 deck_orchestrator.py 实际契约）：
1. 表达层单一 — 数据契约只有 {title, bullets[]}，渲染只有"大标题+要点卡"一种页型；overlay 主线 stat_card/split_compare 的视觉层次在 deck 线无对应物
2. 字数硬约束压扁内容 — title ≤14 字 / bullets 每条 ≤18 字 → 只能干瘪短句
3. transcript 截断 4000 字符 — 长讲解后半段直接丢失
4. 页数固定 6 — 与内容密度无关
5. 一次定稿无自检 — 空话/信息密度无人审

**方案四项**（A/B/C 确定性扩展零额外调用，D 质量闭环）：

- **A 要点富化**：bullets [] → [{text, emphasis}]，emphasis ∈ {none, number, keyword} 白名单；渲染对 number/keyword 亮色强调（对齐 overlay accent_words 语义）；解析非法 emphasis 回退 none；bullets 字数 ≤18→≤24、条数 2-3→2-4
- **B 页型多样化**：JSON 加 slide_type ∈ {points, quote, stat, compare}；渲染 4 型确定性模板（points=现有要点卡 / quote=全屏大字金句 / stat=大数字+注释 / compare=左右两栏对比）；prompt 指导混用（quote ≥1、stat 视内容、compare 用于对比段）；非法回退 points
- **C 上下文与页数扩容**：transcript 4000→8000 字符；max_slides 语义从"固定张数"改为"上限"，prompt 要求按内容密度在 4-{n} 自定
- **D 生成后自检重试**：二次轻量 LLM 审第一版（空话/信息密度/覆盖度/页型混用），反馈拼 feedback 重试 ≤2 轮；失败用最后一版（绝不阻断，降级契约）；每片多 1-2 次 LLM 调用

**兼容性**：parse_deck_json 继续接受旧格式 bullets[str]（自动映射 emphasis=none / slide_type=points）；render_deck_from_slides 读旧 JSON 行为不变。

**验收**：TDD 先红后绿（解析契约 / 渲染页型 / 旧格式兼容映射）+ live6 真机 54.1s 样本新旧对比（闸门停点先看图）。

**实现记录**（2026-10-29 当日，TDD 19 例先行红→绿）：

- **A**（deck_orchestrator）：`_parse_bullet` 归一化 — str（旧格式）→ {text, emphasis:none}；dict 白名单校验 emphasis ∈ none/number/keyword（非法回退 none）；`_bullet_html` 渲染 number=渐变大字 66px / keyword=主题强调色（dot_color）；prompt bullets ≤18→≤24 字、条数 2-3→2-4
- **B**（_slide_html）：slide_type 四分支 — points 现状要点卡 / quote 全屏金句（112px 大字 + 首条 bullets 作 muted 注释）/ stat 大数字（230px accent 渐变字，首条 number/数字开头条目当主视觉）/compare 两栏 grid（bullets 奇偶分栏）；渲染侧 `_bullet_pair` 兼容旧持久化 JSON 的 str bullets；非法 slide_type 回退 points
- **C**：transcript [:4000]→[:8000]；prompt 改"提炼 4-{n} 张（按内容密度自定张数，宁缺毋滥）"
- **D**：`_REVIEW_PROMPT` 四审（空话/信息密度/覆盖度/页型混用）+ `_review_deck`（OK/异常 → ""）+ 主流程重试 ≤2 轮（意见拼 _FEEDBACK_SECTION 重生成；重试解析空保留上一版）
- **兼容**：parse 输出统一新格式（旧 str 自动映射）；render_deck_slides `s.get("slide_type", "points")` 缺省兜底
- TDD 19 例（富要点解析 5 / 页型渲染 8 / 扩容+自检 6），deck 文件 42/42；三文件 104 项全绿（含 person_matting / revision 回归）

**真机验收**（live6，2026-10-29 当日，54.1s 样本 transcript 复用，真实 DeepSeek 全链含自检）：**PASS** —

- 新版 5 张（旧版 6 张 — 页数按内容密度自定生效，4-6 范围内）
- 页型分布 [quote, points, compare, points, quote]（3 型混用）；emphasis 分布 {none: 7, keyword: 6}
- 抽看渲染质量：slide_01 quote 页（大字标题完整 + 注释小字 + accent 线）/ slide_03 compare 页（两栏均衡 + keyword 橙色强调）— 无溢出无重叠
- 素材：outputs/talking_head_overlay/live6_frames/（新版 + slides_live6.json），旧版对照 deck_auto/slide_01-06.png

---


### 10.25 deck 主题自动选 + xfade 页面转场（2026-10-29，用户裁决 E+F 全做 / 6 样式轮换 0.5s）

**用户需求**（原话）："重新拿我原来的素材视频跑一遍…人物的镜头在右下角，然后主页面留给内容展示，内容需要高度符合口播内容，然后内容素材的风格需要符合口播内容，人物需要扣下来，页面转场丰富"。

**解码**：现有能力已覆盖 4/6 — pip=bottom_right（人物右下、主画面留内容）、auto_deck（内容来自口播转录，10.24 富化后高度贴合）、matting（人物抠像）、句边界切换（10.23-D）。新 scope 两项：

- **E 内容风格自动匹配**：deck_theme 加 "auto" sentinel — LLM 按口播内容气质自选三主题之一（当前手选 midnight/cream/sunset）
- **F 页面转场丰富**：`_slides_to_video` 现为 concat 硬切，句边界模式升级 xfade 转场链

**E 设计**（主题自动选）：

1. prompt：theme="auto" 时在 `_DECK_PROMPT` 尾部拼 `_THEME_AUTO_SECTION` — 三主题气质画像（midnight=深色科技/商务/知识型、cream=暖米文艺/生活/情感型、sunset=橙红热情/美食/旅行/促销型）+ 按内容气质选定 + 输出格式改为包裹对象 `{"theme": "<选中主题>", "slides": [...]}`
2. 新 helper `parse_deck_theme(raw) -> str | None`：`\{.*\}` 贪婪匹配 → json.loads → dict 含 "theme" 且 ∈ _DECK_THEMES 白名单 → 选中值；裸数组/包裹缺失/坏 JSON/主题越权 → None
3. `generate_deck_slides(theme="auto")`：actual = parse_deck_theme(raw) or "midnight"（降级契约）；slides.json 与渲染均用 actual（避免 "auto" 字面量落盘进渲染回退链）
4. 调用点零签名改动：auto_deck 分支 deck_ready_theme 改从 slides.json 回读实际主题（复用 approve 分支现有读法）；retheme/regenerate 显式主题路径不受影响
5. `parse_deck_json` 不改 — `\[.*\]` 已能从包裹对象内提取 slides 数组（裸数组继续向后兼容）

**F 设计**（xfade 转场链，句边界零漂移）：

- 数学：输入时长 e_i = d_i + td（i<n-1）、e_{n-1} = d_{n-1}；第 i 次转场 offset_i = sum(e_0..e_i) − (i+1)·td = sum(d_0..d_i)；转场区间 [B_i, B_i+td]（B_i = 句边界累计点），总长 sum(e) − (n−1)·td ≡ sum(d) = 片长 — 切换对齐零漂移
- 样式轮换：["fade","slideleft","slideup","wipeleft","circleopen","smoothleft"] 确定性轮换（i % 6）
- 命令形态：多输入 `-loop 1 -t e_i -i img_i` + filter_complex（每路 scale/pad/format/setsar → 链式 xfade → fps）；td = 0.5s
- 路由：durations 给定且 n>1 → xfade 链；durations=None（手动 deck_images）或单页 → 旧 concat 硬切（回归保护，行为不变）

**验收**：TDD 先红后绿（F：e/offset 数学、样式轮换、单页短路、durations=None 回归旧 concat、总长守恒；E：包裹解析、白名单越权返 None、auto 落盘实际主题、裸数组降级 midnight）+ live7 真机 54.1s 原素材全链（bottom_right + matting + auto_deck + deck_theme=auto + 转场），闸门停点看图确认后出片，抽帧验证转场与主题。

**实现记录**（2026-10-29 当日，TDD 红→绿）：

- **E**（deck_orchestrator）：`_THEME_AUTO_SECTION` 三主题气质画像段（auto 时拼 prompt 尾部，输出契约改包裹对象 `{"theme": "<选中>", "slides": [...]}`）；`parse_deck_theme` 白名单校验（裸数组/包裹缺失/坏 JSON/越权 → None）；`generate_deck_slides` auto → actual = parse_deck_theme(raw) or "midnight"，slides.json 落盘/渲染/重试轮均用 actual（重试裸数组保留上一选中）；skill 侧 auto_deck 分支回读 slides.json 实际主题（deck_ready/deck_preview_ready 事件报真值）；parse_deck_json 零改动（`\[.*\]` 已能从包裹对象提取 slides）
- **F**（talking_head_overlay）：`_XFADE_STYLES` 六样式确定性轮换（fade/slideleft/slideup/wipeleft/circleopen/smoothleft，i%6）+ `_XFADE_TD=0.5`；`_build_xfade_cmd` 纯函数 — e_i=d_i+td（全页，末页多给 td 保完整显示，尾部 0.5s 由下游合成 -t 硬截）、offset_i=前缀和(d)[i]、转场区间 [B_i, B_i+td]（B_i=句边界）→ 总长 sum(d)+td、切换点零漂移；`_slides_to_video` 路由：durations 给定且 n>1 → xfade 链（-loop 1 -t 多输入 + filter_complex 链式 xfade + [vout] 映射），单页/durations=None → 旧 concat 硬切（回归保护）
- **live7 实证连带修复**：approve 分支 `glob("slide_*.png")` 无张数校验，deck_auto 目录残留上一轮旧 PNG 时吞进成片（live7 首跑 5 页 JSON 消费 6 张，混入 live5 时期旧第 6 页）→ 修复：张数与 slides.json 权威页数不符时弃 glob 走 `render_deck_from_slides` 确定性重渲染（零 LLM）；顺带修 approve 轮 deck_ready_theme 统一从 JSON 回读（theme=auto 首轮后 approve 显示真值）
- **测试**：E 8 例 + F 5 例 + approve 残留修复 1 例（红→绿），deck/matting/revision 三文件 117 项全绿；视频管线 13 文件回归全绿（skip 均存量）

**真机验收**（live7，2026-10-29 当日，54.1s 原人口播素材全链：bottom_right + matting + auto_deck + deck_theme=auto + xfade，真实 DeepSeek 含自检）：**PASS** —

- 断言1：闸门停点 + whisper 子进程转录 + **LLM 自选主题 sunset**（夏日生活内容 → sunset "生活/热情" 气质画像命中）+ 5 张落盘 slides.json（theme=sunset 非 "auto" 字面量）
- 断言2：approve 回调轮（张数校验修复后补跑）→ 5 张、成片 live_person_talk_pip_presentation_710b51dc.mp4 **54.08s ≈ 源 54.10s**，句边界 durations [9.1, 11.6, 11.3, 11.0, 9.7]
- 页型 [quote, points, points, points, quote]，emphasis keyword×9 + number×1；内容与转录逐段对齐（空调直吹/大汗冰水/防晒三段全覆盖）
- 抽帧三态实锤转场（live7_frames/switch_before/mid/after.png）：before=页1 quote 稳定态 → mid=两页交叉淡化混合态（fade 生效）→ after=页2 points 稳定态；人物抠像小窗全程稳定右下角、主画面留给内容
- 素材：outputs/talking_head_overlay/live7_frames/（三态抽帧 + slides_live7.json 含 durations）
### 10.26 deck 幻灯片自动配图（2026-10-29，用户裁决：AI 生图配图）

**用户痛点**："效果太差了，只有文字描述啊？" — deck 幻灯片从 10.20 二期至今是纯文字
模板（title + bullets + 主题色背景），10.25 的"风格符合口播"只做到了配色层，
画面里没有任何图像内容。

**归因**：`_slide_html` 数据契约只有 {title, slide_type, bullets[]}，渲染零图像元素；
而项目已有现成生图链路（auto_short_video Step 2 `_generate_shot_images` →
`WanxAdapter` text2img，DASHSCOPE_API_KEY 已配置真机在用）— deck 线从未接入。

**方案**（复用 Wanx 生图链路，降级绝不阻断）：

1. **契约扩展**：slides 元素加可选 `image_prompt`（LLM 产英文画面描述，≤200 字符，
   风格词与 10.25 自选主题联动 — sunset→warm sunset tones / cream→soft natural
   light / midnight→cool neon night）；parse_deck_json 白名单收录，非法/缺失 → None
2. **生图**：generate_deck_slides 中 dashscope key 存在且页有 image_prompt →
   WanxAdapter 逐页生图（竖版 720x1280）；单页失败/无 key/无 prompt → 该页降级
   纯文字（不阻断其他页）；图落盘 deck_auto/img_NN.png，slides.json 记 image 路径
3. **版式**：points/stat/compare — 图占上部约 42%（渐隐过渡入背景），要点区下移；
   quote — 图全屏暗化背景（黑色 45% 罩层），金句大字浮层（电影感）
4. **降级链**：slides.json 无 image 字段（旧 JSON）或文件缺失 → 现有纯文字版式
   （渲染按 s.get("image") 存在性分流，零硬依赖）
5. **成本**：5 页 = 5 次万相调用 + 15-25s 生成时长（真实 key 计费）

**验收**：TDD 先红后绿（契约解析/降级分流/版式注入/旧 JSON 兼容）+ live8 真机
54.1s 原素材新旧对比（闸门停点先看图再出片）。

**实现记录**（2026-10-29 当日，TDD 红→绿）：

- **契约**（deck_orchestrator）：`parse_deck_json` 收录可选 `image_prompt`（非 str 丢弃、>200 截 200）；`_DECK_PROMPT` 输出格式加第 4/5 条 — 每页英文画面描述 ≤30 词（具象场景+光影氛围，竖构图，与该页内容直接相关）
- **生图**：新 `async _generate_slide_images(slides, out_dir) -> int` — `get_settings().dashscope_api_key` 存在且有 image_prompt 的页 → `WanxAdapter.generate(ip, size="720x1280")` → 落盘 `deck_auto/img_NN.jpg` → 回填 `slides[i]["image"]` 绝对路径；key 缺失返 0 零调用、单页异常降级纯文字其余继续（绝不阻断）；`generate_deck_slides` 在 slides.json 落盘前接线（image 路径进 JSON → approve 重渲染/事件链全链可追溯）
- **版式**（`_slide_html` 加 `image: str | None`）：图 base64 内嵌（set_content 页面可加载，file:// 会被拦）→ 全屏 `bg-img`（object-cover）+ 主题罩层 `bg-shade`（`_DECK_IMAGE_SHADES` 三主题各配半透明渐变，与 10.25 自选主题联动）+ 文字浮层 `.fg`（原 body 布局宿主迁入，.idx/.total 定位等价）；图缺失/读失败 → bg 与 CSS 一并去除（纯文字版式，四页型统一处理零溢出风险）
- **测试**：8 例红→绿（契约解析/背景版式/缺失回退/生图 mock/无 key/部分失败/集成落盘），deck/matting/revision 三文件 125 项全绿

**真机验收**（live8，2026-10-29 当日，54.1s 原素材全链：真实 DeepSeek + 真实万相生图 + xfade 转场 + bottom_right + matting）：**PASS** —

- 断言1：5 页 image_prompt **5/5**（LLM 产出具象贴题：烈日街道/空调出风口积尘/大汗冰水/防晒行人/阴凉阳台温水），万相生图落盘 **5/5**，theme=sunset 自动选（与 live7 一致）
- 版式样张（live8_frames/slide_01、02.png）：quote 页烈日街道背景+金句浮层电影感；points 页半透明要点卡压图可读性好、keyword 橙色强调生效
- 断言2：approve → 成片 live_person_talk_pip_presentation_710b51dc.mp4 **54.08s ≈ 源 54.10s**；成片 5s 抽帧终验（final_check.png）：生图背景+罩层+金句+人物抠像右下角小窗全部进片
- 素材：outputs/talking_head_overlay/live8_frames/（样张 + final_check + slides_live8.json）


### 10.27 智能裁剪三连修 + 串联端到端打通（2026-10-30，live9 实证）

**用户触发**："我们的智能裁剪好像没有发挥作用" — 排查证实 SmartCutSkill 能力
（静音/填充词/口误检测、删减规划、TimeMapper 锚点重映射、chain_with_overlay
串联设计）全部健在，但**从上线起就没跑通过**——三层连环 bug：

1. **入口误杀**（silence_detect.has_audio_track）：裸调 `"ffprobe"`；本机 PATH
   无 ffprobe 且 imageio-ffmpeg 只带 ffmpeg → 永远 OSError → 恒 False →
   SmartCutSkill 一启动即误判 `no_audio_track` 拒绝执行。whisper 听得见声音、
   smart_cut 却"看不见"音轨。probe_duration 有 PyAV 兜底所以别的功能能跑，
   唯此函数单点依赖 → 修复：PyAV（av.open 读容器流）→ ffprobe 双层链
2. **_run 裸调**（silence_detect._run）：同样裸调 `"ffmpeg"` → detect_silence
   也会 WinError 2 → 修复：对齐 smart_cut._run_ffmpeg 的 10.20 模式（cmd[0]
   为字面量 ffmpeg/ffprobe 时走 resolve_ffmpeg 解析链）
3. **pad 补偿数学错误**（smart_cut._extract_segments）：`duration =
   end-start+2*pad` 在首段 clamp 到 0 时仍加满 2*pad（多算 0.25s），且报告
   cut_duration 用裸 kept_duration（不含 pad 补偿）→ live9 实证 cuts_applied=1
   却成片 54.07s≈未剪。修复：`duration=(end+pad)-s_eff`；报告口径
   expected=Σ(min(e+pad,total)−max(0,s−pad))

**串联断链修复**（talking_head_overlay）：kept_segments 只映射词戳（TimeMapper），
**视频本体从不被裁**——live9-2 串联跑通后成片 54.08s 仍 ≈ 源 54.10s。修复：

- OverlayInput 加 `cut_video_path`：提供后 execute() 将视频本体/时长切裁后轴
  （body_video）；路径不存在 → 明确报错不静默；转录仍跑原视频（词戳原轴 →
  TimeMapper 映射语义不变）
- 新纯函数 `_remap_transcript_rows(rows, kept)`：句时间戳原轴 → 裁后轴；
  句中点落在删除区 gap 内的句丢弃，其余句按 gap 长度平移；首轮 rows 映射后
  持久化（回调轮回读已映射值，不会二次漂移）

**测试**：TestHasAudioTrack 3 例 + TestPadMath 2 例 + TestChainedCut 3 例
（红→绿；期间修测试基建三处——mock 形态 async-gen、_run_skill 模块级三参、
helper 类归属），五文件终回归 142 项全绿。

**真机验收**（live9，54.1s 原素材）：**PASS** ——

- 阶段1（smart_cut 独跑）：检出 1 处口误重复（"纷纷" 0.8s，confidence 0.85）
  → 规划 kept [[0,2.68],[3.48,54.1]] → 出片 **53.82s** ≈ 报告 53.8s（数学闭环）
- 阶段3（串联全链）：cut_video_path + kept_segments + auto_deck + theme=auto +
  5 页 sunset 配图 + xfade → 成片 **53.80s ≈ 裁后 53.82s**（源 54.10s），
  句边界 rows 映射裁后轴（max_end 53.36 ≤ 53.82+0.5）——智能裁剪首次真实
  参与成片，串联端到端打通
- 产物：outputs/default/smart_cut/cut_*.mp4（裁后）+ talking_head_overlay/
  *_pip_presentation_710b51dc.mp4（串联成片）

### 10.28 静音检测自适应 + 候选收缩（2026-10-30，live10 实证）

**用户反馈**（live9 后）："重复的部分和空白时间的部分你都没有减"。

**诊断**（实测）：
- detect_silence 默认 -35dB/0.6s 对本素材检出 **0 段**（底噪高于 -35dB，
  句间停顿多数 0.3-0.5s）→ 0 候选 → 规划只剩 1 处 repeat → 成片只省 0.3s
- 三档实测：-35dB/0.6s → 0 段；-30dB/0.35s → 6 段；-28dB/0.3s → 13 段
- 链路本身通（silence→candidates→LLM 规划），源头一断全断

**方案**：
- A. detect_silence 自适应档位：按 [(-35,0.6),(-32,0.5),(-30,0.35),(-28,0.3)]
  依次尝试，取首次检出 >0 段的档；安静素材仍走严格档（防把背景音当静音），
  高底噪素材自动放宽。入参显式指定时不覆盖
- B. 静音候选收缩（呼吸感）：detect_silence_candidates 不再全量删静音段——
  静音 >0.6s 时两端各保留 0.2s 缓冲只删中段；≤0.6s 的短停顿不出候选
  （句间自然停顿，剪了会像机关枪）
- C. live10 真机：同一素材重跑，对比检出段数/成片时长/衔接自然度

**实施**（用户裁决 A+B+C 全做）：TDD 6 例红→绿（自适应 3 + 收缩 2 + pad 交叠 1；
期间更新存量收缩契约测试 + 修两处测试基建——ghost 路径过 is_file、浮点 approx）。

**live10 第 4 层 bug（真机暴露）**：删段 0.24s < pad 之和 0.5s → 相邻保留段
尾头交叠 0.26s → concat 回填总长（首跑成片 54.09 ≈ 没剪）。修复：per-gap
收缩 pad_eff = min(pad, 左gap/2, 右gap/2)（_extract_segments 与 report 口径
同步），段尾永不越过段头；报告 expected 与成片一致。

**实测**（54.1s 同素材）：

- 自适应命中 (-30dB, 0.35s) 档：静音 6 段（旧参数 0 段）；收缩后候选 1 个
  （其余 5 段 ≤0.6s 属句间自然停顿，按呼吸感设计不剪）
- 规划 2 候选：repeat 0.80s + silence 0.64s → 成片 **53.72s**（净省 0.38s，
  vs live9 0.30s）；报告 expected ≈ 成片（闭环）
- 设计说明：0.3-0.6s 短停顿是语速节奏，保留呼吸感不剪；>0.6s 的愣神/
  忘词才出候选。本素材停顿本就短（最长 0.64s），净收益有限属素材特性
  而非检测缺陷——空白更长的素材收益会显著放大
- 产物：outputs/default/smart_cut/cut_live_person_talk_13d8721c.mp4

**测试**：smart_cut 63 + video_compose 11 + person_matting 61 = 135 项全绿。

### 10.29 新素材全链 + 三处隐藏 bug（2026-10-30，live11 实证）

**用户任务**：32.77s 新素材全流程——智能裁剪 + 配图幻灯片（装饰同画面不遮
五官）+ 裁无关内容与空气时刻。

**诊断与修复**（live11 挖出 2 处"从写下起没跑通"级 bug）：

1. **fluff 检测 import 幻影**（smart_cut._detect_fluff）：`from
   app.engine.llm_client import get_llm` — 模块不存在，LLM 离题检测从
   写下起从未跑通（与 10.27 has_audio_track 同病）。修复：对齐
   _resolve_llm 模式（DeepSeekAdapter + Settings）
2. **跨切口句污染页边界**（_remap_transcript_rows）：句 start 落删除区
   内、end 跨到保留区（whisper 对被裁静音的幻听碎词）→ 中点判定误保留
   → 映射后边界错位。修复：句任一端落删除区 → 丢弃（跨切口句不可能
   完整保留）
3. **分块数 ≠ 页数**（_slide_durations_from_transcript）：ceil(M/N) 分块
   在 M=11 句/N=5 页时只产 4 块 ≠ 5 页 → xfade durations mismatch →
   ffmpeg 挂（live7-10 碰巧 10 句整除 5 页没炸）。修复：按位置比例切分
   （spans[L*k/N : L*(k+1)/N]）保证恰 N 块；另 _build_xfade_cmd 前置
   N==N fail fast 断言（mismatch 时明确报错而非畸形命令）

**测试**：6 例红→绿（跨切口 1 + xfade 断言 1 + 分块 2 + 既有契约更新 2），
person_matting/deck/smart_cut 三文件 112 项全绿。

**live11 真机验收（32.77s 素材，PASS）**：

- 裁剪：自适应静音命中 -30dB；fluff LLM 逐段判定 10 段全部 keep（素材
  本身为钩子→论点→反问收尾的紧凑结构，无离题段）——尾部 1.07s 空气
  裁除，32.77s → 32.62s；"无关内容"结论：检测链正常，素材本身紧凑
- 串联：kept_segments → cut_video_path → auto_deck 5 页（midnight，与
  职场陪聊吐槽内容气质匹配）→ xfade 句边界转场 → 人物抠像 324x576
  右下角小窗（装饰均在主画面中部/上部，小窗人物五官全程无遮挡）
- 成片：31.63s ≈ 裁后轴；抽帧 4 帧验证（quote/points 版式、生图贴题、
  页码、keyword 橙色强调）
- 产物：outputs/talking_head_overlay/cut_live11_src_5ee92c75_*
  _pip_presentation_8a7c9311.mp4


---


---


---

---

## 九、修订历史

| 版本 | 日期 | 变更内容 |
|------|------|---------|
| v1.0 | 2026-01-24 | 初版：基于 8 个开源项目调研的完整方案 |
| v1.1 | 2026-01-25 | 修正 8 处隐患：Playwright 录屏 API、WhisperX Windows 安装、中文填充词分级、concat 硬切、闸门合并等 |
| v1.2 | 2026-01-25 | 新增第七章：14 条隐患的完整对策（基于 stable-ts、outputguard、hyperframes、Playwright 官方文档等开源方案） |
| v1.3 | 2026-10-26 | 新增第十章：v2 渲染链路（逐帧透明截图替代绿幕、jieba 词级对齐、seekTo 确定性时间轴、密度自检），对齐 OVE 效果 |
| v1.4 | 2026-10-26 | 10.6/10.7：真实视频排障 — 分辨率错配致叠加整体不可见（ADR-005）；LLM 繁体输出归一化（简体约束 + zhconv 后置处理） |
| v1.5 | 2026-10-26 | 10.8：v3 视觉冲击升级 — 彩色贴纸式质感（accent_words 亮色色块、CapCut 卡拉OK、白底气泡、标记笔扫出、渐变卡），新增 section_card/emoji_sticker，规划 prompt 强制 ≥4 种模板混用 + caption ≤50% |
| v1.6 | 2026-10-27 | 10.9：v4 节奏与主题升级 — 归因"太单一"三真相，新增 arrow_point/circle_mark（画面互动）、full_card/split_compare（整屏冲击），全 16 模板 sin 微动（确定性），palette 3 色撞色盘 + 节奏规则（开场轰炸/留白/收束卡）+ `validate_plan_rhythm` 自检 |
| v1.7 | 2026-10-27 | 10.10：P0 视觉自审闭环 — 渲染预览抽帧 → QwenVL 看图审查（文字塌缩/元素缩点/布局失衡/配色/遮挡/孤立箭头，底片参考帧判挡脸）→ 带反馈重规划重渲（≤2 轮，复用转录）；全链路降级绝不阻断出片；新增 `overlay_review.py` + `generate_overlay_plan(feedback=)` + 33 例测试 |
| v1.8 | 2026-10-27 | 10.11：P0 收尾 — 修复 QwenVL key 注入断点/模型路由双源漂移（统一 qwen3-vl-plus）；o6 叠字事故：TEMPLATE_LAYER 改布局事实四带 + `resolve_overlay_conflicts` 强制消解；审查 issue 明细落盘事件与 report（reviews[]/final_plan/initial_plan），闭环实战验收（critical 2→1 收敛，零叠影）；明细实证定位 VLM 误报根因（半入场态抽帧）→ caption 抽帧改稳定态 settled + 帧时刻语义进 prompt（防误报三层修复）；settled 验证 round2 首次全绿，round1 三连误报取证（段号错位/对比度目测错）→ `_validate_issue_refs` 引用校验硬防线（引号引用必须命中段 params，幻觉 issue 丢弃）；第七次 demo 真问题暴露 palette 规划/渲染契约脱节 → params.color 系统性消费修复（caption_pop accent 盘内色+同词同色、section_card 编号块）；第八次 demo 实证 color 消费生效（青绿编号块）+ 长标题孤字折行真缺陷 → `_fit_font_size` 字号分档自适应（section_card/title_card）+ emoji 颜色/换行判据进 prompt 防误报 |
| v1.9 | 2026-10-27 | 10.12：P1 模板生成器 — 三重锁开放模板库（提名门槛：仅当 16 内置模板无法表达、requirement 一句话、全片 ≤2；质量门禁：6 条契约静态校验 + 失败错误清单反馈重试 ≤3；零执行：LLM 产出只是数据，`GeneratedTemplate` 包装成与内置同签名 callable，无 exec/eval、escape 转义、占位符白名单）；`template_generator.py`（validate/GeneratedTemplate/generate_template/load+落盘 JSON）；orchestrator 模板字段放宽 str + 未知模板保留不丢弃 + TEMPLATE_LAYER 兜底 center；skill 白名单内置∪生成 + gen_budget=2 + 失败 skip 不阻断；27 例 TDD（含 getsource 安全红线）全量 171 例绿；DeepSeek 真机冒烟 gen_progress_bar 一次过契约且动画真实驱动；第十一次 demo 里程碑验收 round=1 一次过审（LLM 自然路径未提名 gen_ = 门槛生效） |
| v2.0 | 2026-10-27 | 10.13：P2 时间轴两步链（参考 NarratoAI）— `generate_narrative` 叙事结构分析先行（theme/palette/sections: role+visual_intent+key_points，防御式解析+繁体归一化），叙事块拼规划 prompt 头部（规则唯一真相不双源），`OverlayPlan.narrative` 落 report；失败无声降级单步链；feedback 双拼（叙事步结构反思+规划步细节修复）；27 例测试；第十二次 demo 验收：palette 传递链验证、反馈驱动叙事反思实证（theme 锐化+o7/o11 结构级换型绕开图标问题，抽帧确认）、DNS 抖动降级契约实战。P0/P1/P2 三阶段全部关闭 |
| v2.1 | 2026-10-27 | 10.14：接入主业务管线 — auto_short_video Step 4.5 内嵌 talking_head_overlay（`enhance_overlay` 缺省关零影响；AUTO_CONFIRM 无人值守走完整 P2 两步链+P0 自审；增强片另存资产不覆盖原片，chained_from 溯源；失败降级返回原片绝不阻断主管线）；顺手修存量 bug：video_compose local 分支返回 Path 被 dict `.get` 必炸；3 例接入测试 + 144 例回归全绿 |
| v2.2 | 2026-10-28 | 10.15：样本多样性审计与三项根修 — 质疑"十轮 demo 单一样本过拟合"→ 分层审计（契约类单测足够 / 效果类 n=1 风险真实）+ 静态确证 3 处结构性过拟合（绝对 px 模板、固定 6 帧审片、真人零样本）；修复① `OverlayInput.transcript` 已知文本时间轴通道跳过 whisper + `_build_transcript` 由 script 原文建轴（根除 TTS 合成音频误听"90%"→"九十辈子"，全链验收实证）；② 分辨率自适应：`_base_css` 按 `k=min(w/1080,h/1920)` 等比缩放居中 + `_design_canvas` 短边双向归一（720p 竖屏与 1920×1080 横屏真机验证，16 模板零改动）；③ `largest_overlay_gap` 分布均匀性自检（最长空白 > `max(6s, 8%×时长)` 触发带反馈重试，阈值由 run12 真实合格产物 1.88s/3.44s 反推，180s 实证 13→104 叠加）；新增 21 例测试 + 4 处既有测试数据修正；如实记录未闭环 4 项（长片审片仍 6 帧、真人素材零验证、180s 变体结论不可信、既有模板排版问题） |
| v2.3 | 2026-10-28 | 10.16/10.17：排版静态审计与 G1 生成通道放开 — ① `tools/audit_overlay_layout.py` 五类指标静态审计（WCAG 对比度/无背景兜底/字号比/占比/内边距，无需 LLM 素材，P0 退出码 1 可接 CI），实跑 16 模板 0 硬性问题；② `_text_on` 感知亮度+0.6 阈值 → WCAG 相对亮度黑白取优（max≥√21 恒 ≥4.5，根治 #FF4D4D 类误判），`_wcag_lum` 抽共享；③ G1（用户裁决）：`_GEN_BUDGET` 2→8 改"不同 gen_ 模板数"成本护栏语义、规划 prompt "按需大胆提名数量不设限"+requirement 需求书、契约 6→7 条（新增 WCAG 对比度档位校验，禁真实时钟扩展至 CSS animation/@keyframes/transition）；真机 run14：gen_soft_label 入库复用、{color} 缺失拦截→错误回喂→attempt1 入库重试闭环、诱导低对比 LLM 自纠 #4A4A4A；④ full_card 显式 text_color 旁路收口：`_safe_text_color` 按本卡最严适用档校验（有 sub 4.5/无 sub 3.0）不达标/非法回退自动选色，TDD 5 例红→绿；视频管线 13 文件回归 546/1/0 全绿 |
| v2.4 | 2026-10-28 | 10.18：G2 布局编排层方案 + 实现 + 真机验收 — 第三层智能：开放"画面构图/镜头语言"决策（此前 `_execute_pip` 为静态画中画）。用户裁决：独立 skill `dynamic_layout` / 编译策略 A 分段渲染+concat / 全三原语。落地：`services/layout_orchestrator.py`（LayoutTimeline 数据模型 FULL/PIP/BROLL + 契约校验 [时间轴完整性/≤10 段 ≥1.5s/引用存在性] + 防御式解析 + **纯函数编译器** cmd[0] 恒字面量 ffmpeg + 执行器 concat demuxer -c copy + LLM 规划反馈重试 ≤3 + VLM 每段中点抽帧审查复用 110KB 分块）；`skills/dynamic_layout.py`（闸门 standalone/auto_confirm + 确认回调原样执行不重新规划 + 产物隔离 md5 timeline）；降级链 规划失败→静态 pip→原片。TDD 49 例先行全绿（模型 17/解析 7/降级 2/编译器 9/规划 6/审查 4/skill 6），管线回归 14 文件 596 项 595/1/0。真机 22s+storyboard 真图：LLM 产 6 段三原语 timeline（PIP shot:0/1/2 与三件事叙事精准对齐、BROLL 总结、FULL 收束），成片 22.02s≈源 22.00s；真机教训：VLM 模型名不走 tier router → 404（已修对齐 overlay_review），审查 6 帧真跑通过。未闭环如实记录：切点硬切（转场层后续）、真人 PIP 挡脸零实证、auto_short_video 内嵌 enhance_layout 未接 |
| v2.5 | 2026-10-28 | 10.19：对话式修改层（overlay_plan_ready 之后用户说话改规划）— 用户裁决：两级路由 / ≤5 轮护栏 / 不含 ADD。服务层 `parse_revision_intent`（LLM 只产纯数据：喂 plan 摘要+用户原话，回显 locate_hint、定位拿不准归 global 不猜、坏 JSON 反问）+ `apply_revision`（纯函数 patch：DELETE→skipped=True 可追溯、MOVE clamp+回退、UPDATE 禁改键/非法色拒整个指令、尾部强制冲突消解+节奏自检如实上报）；skill 层 `OverlayInput.revision` 路由（round>5 → revision_rejected、解析失败 → revision_clarify 反问不猜、事件带 revision 标记落审计链）。TDD 24 例（解析 8/patch 11/skill 路由 5），管线回归 20 文件 678 项 673/5/0。真机 PASS：local"第 2 个字幕字号改大"→ 仅目标段 params 变化其余逐字节一致；global"整体再紧凑点"→ 重规划 13→11 段。挂账新增：update params 数值型字段类型校验（真机实证 LLM 给 font_size='larger' 无效 CSS） |
| v2.6 | 2026-10-29 | 10.20：人物抠像接入幻灯片 — 用户裁决：批准开工 / 22s 验证样本 / storyboard 配图主画面。选型 RVM mobilenetv3 TorchScript（视频原生时序稳定、15.5MB、拒 MediaPipe 糊/MODNet 闪）；依赖 torch 2.6.0+cu124（RTX 4060 实测 4.3fps，660 帧约 2.5 分钟）。落地：adapter `person_matting.py`（matte_person → rgba PNG 帧序列 + meta 缓存 + 降级契约任何失败返 None）+ `_execute_pip` matting 分支（deck_images 配图主画面取首图分辨率 + ffmpeg 三输入 alpha 融合 + 失败广播 pip_matting_fallback 回退矩形 PIP）。TDD 13 例全绿，回归 21 文件 691 项 686/5/0。真机：机制全链 PASS（成片 22.80s；降级链实证可用）；边缘质量不可评估 — 样本为 TTS 幻灯片无真人出镜（RVM 抠的是插画人物 alpha 0.36-0.54，叠同画面幻灯片伪装不可见），真人素材验收挂账。真机实证修 4 环境坑：_run_ffmpeg 中央接 resolve_ffmpeg 解析链（裸 ffmpeg WinError 2）、torch C++ fopen 中文路径 errno 2（BytesIO 加载）、_slides_to_video Path 传参、cv2 非 ASCII 路径静默失败全面规避（imencode/fromfile/av） |
| v2.7 | 2026-10-29 | 10.20 二期：真人素材验收 + 讲解内容渲染 — 用户传 54.1s 真人口播并指令"根据讲解内容进行一个内容渲染"。新服务 `deck_orchestrator.py`（whisper 转录 → LLM 要点契约 JSON → 防御式解析 → 确定性 HTML 模板 → Playwright 截图 → deck_images 通道；降级返 []；llm_prompt_to_text 对齐收口），TDD 7 例。**真人边缘质量验收 PASS**（一期挂账闭环）：成片 56.07s，帽檐/下颌线/肩部轮廓自然无框无残留，全片时序稳定；幻灯片内容与讲解精准对齐、繁→简转写正确。第 5 环境坑（最有价值）：ct2 构造 WhisperModel 拉系统旧 cuDNN DLL → 同进程 torch CUDA conv native fastfail 不可捕获 — 对照实验实锤；修复：WHISPER_DEVICE 环境开关 + 转录/RVM 分进程。回归 5 文件 82 项全绿。二期挂账：auto_deck 产品化接线、幻灯片切换按句边界对齐（当前均分）、deck 模板风格化 |
| v2.8 | 2026-10-29 | 10.20 二期优化（用户裁决两项）：① 人物让位 — `_slide_html` 加 person_clearance_px（padding 简写底部预留，要点卡上移避开右下角人物区，透传 render/generate），真机首帧遮挡消除；② 尾帧修复 — matting 合成 `-t` 硬限替代仅 -shortest（x264 lookahead 多带 1-2s），成片 56.07→54.08s（源 54.1）精确。TDD 3 例 + 相关 22 例全绿。过程实录：DeepSeek 402 余额不足 → deck 降级真实验证（转录直切 fallback 出片不阻塞，内容质量依赖 LLM 充值恢复） |
| v2.9 | 2026-10-29 | 10.21：auto_deck 一键产品化 + deck 多主题（用户批准，主题集按推荐三主题）。`_DECK_THEMES` 三主题（midnight/cream/sunset）7 样式字段 dict 注入 `_slide_html`（非法回退 midnight），render/generate 全链透传 theme；`OverlayInput` 加 auto_deck/deck_theme/deck_max_slides，`_execute_pip` 前置 auto_deck 分支（transcript 优先 → `_transcribe_cpu_subprocess` 兜底 [机制修正：方案原写同进程强制 CPU 与 v2.7 实证第 5 坑矛盾，ct2 构造即污染 DLL，实现为 skill 内独立子进程转录 env 强制 CPU，父进程 GPU 全留 RVM] → LLM deck → deck_ready/deck_fallback 事件）。TDD 19 例红→绿、两文件 39/39 全绿。真机双向 PASS：全链（DeepSeek 余额已恢复）auto_deck 一参直出 deck_ready 6 张 → 54.08s，抽帧实证 LLM 提炼要点 + 子进程隔离成功；降级（live3b 坏 key 401）→ deck_fallback → 纯色底 → 54.08s。回归 1688 项 27 红全为非管线存量（collab event loop 22 / tavily 3 / GBK 2），视频管线 0 红。挂账：_pip_key 未含降级/画布形态标记（降级片覆盖全链片同路径）、accent 装饰线 top:560px 写死标题两行时压首卡（10.20 同款）、指纹跨会话缓存不持久 |
| v2.10 | 2026-10-29 | 10.22：deck 确认闸门（用户原话点名，三项决策全按推荐）。`generate_deck_slides` 落盘 slides.json（theme/transcript/slides）+ feedback 参数（用户原话拼 prompt 反馈段）；新增 `render_deck_from_slides`（JSON → 确定性重渲染，theme 显式覆盖/缺省用首轮）；`OverlayInput` 加 deck_confirm/deck_slides_json/deck_revision/deck_revision_round；`_execute_pip` 回调路由（approve=glob 首轮 PNG 直消费缺失兜底重渲染 / retheme=render_{theme} 子目录 / regenerate=JSON transcript+feedback 重生成 / 轮数>5 deck_rejected）；deck 产物 tmp → scope_dir/deck_auto 持久化（闸门跨 execute 消费，部分闭环 10.20 挂账②）；deck_preview_ready 总是广播（auto_confirm=False 照发不停，对齐 overlay 主线惯例），仅首轮+standalone 真停。TDD 净增 11 例（gate 8 + persistence 3，红→绿修 4 处测试基建），两文件 50/50。真机 live4 三阶段 PASS：停点（slides.json 持久化）→ approve 54.08s → retheme cream 54.08s，三主题版式补验收（midnight/cream 首帧对比，accent 压字瑕疵亮底下复现确认）。回归 1699 项 23 红全为存量（collab 18 / tavily 432 3 / GBK 2），视频管线 0 红 |
| v2.11 | 2026-10-29 | 10.23：工程挂账清理 A-D — A accent 装饰线文档流（去 absolute 钉死，压卡根因修复）；B update font_size 数值域 8-300 校验（'larger' 无效 CSS 挂账闭环）；C _pip_key 形态指纹四形态（deckauto{theme}{count}/deckmanual/deckfallback/plainpip，降级片覆盖全链片根因闭环）；D 句边界对齐（_slide_durations_from_transcript M 句→N 页 ceil 分块，页时长=块末句 end−块首句 start、首末夹边、句边界模式不加 0.8 余量防累计漂移；组合缺陷修复：闸门回调轮 rows 持久化 deck_auto/transcript_rows.json + 回调轮回读，停点确认不再废掉对齐）。TDD 10 例红→绿，三文件 85/85 全绿。live5 真机 PASS（24 句子进程转录、停点、approve 54.08s 句边界 durations [5.8,11.5,7.6,9.3,8.0,9.7]、accent 超长标题重渲染出图）；回归 24 红全存量（collab 19/tavily 3/GBK/which），视频管线 0 红。测试卫生：单测 scoped_dir 隔离 tmp_path（2 句 rows 曾覆盖真机 24 句产物）。E VLM 审 deck 版式继续挂账 |
| v2.12 | 2026-10-29 | 10.24：幻灯片内容丰富度升级（用户痛点"生成的内容不够丰富"，裁决 A+B+C+D 全做）— A 要点富化：bullets []→[{text,emphasis}] 白名单契约（number 渐变大字/keyword 主题强调色），字数 ≤18→≤24、条数 2-3→2-4；B 页型多样化：slide_type 四页型确定性模板（points/quote 全屏金句/stat 大数字/compare 左右两栏），非法回退 points，旧 JSON str bullets 渲染兼容；C 上下文扩容：transcript 4000→8000 字符 + 张数 4-{n} 按内容密度自定；D 生成后自检重试：_REVIEW_PROMPT 四审（空话/密度/覆盖度/页型）+ 重试 ≤2 轮失败保留上一版绝不阻断。TDD 19 例红→绿 deck 42/42，三文件 104 项全绿。live6 真机 PASS：5 张（页数自定）、3 型混用 [quote,points,compare,points,quote]、keyword 强调 6 处，quote/compare 版式抽看干净。旧格式 JSON 全兼容（parse 映射 / 渲染兜底） |
| v2.13 | 2026-10-29 | 10.25：deck 主题自动选 + xfade 页面转场（用户点名 E+F，6 样式轮换 0.5s）— E：deck_theme="auto" sentinel（_THEME_AUTO_SECTION 三主题气质画像 → LLM 输出包裹对象自选主题，parse_deck_theme 白名单校验，actual 落盘/渲染/重试跟随，事件报真值；裸数组降级 midnight，parse_deck_json 零改动兼容）；F：_slides_to_video 句边界模式升级 xfade 转场链（_build_xfade_cmd 纯函数：e_i=d_i+td、offset_i=前缀和(d)[i]、转场区间 [句边界, 句边界+td] 零漂移、末页多给 td 尾部 -t 硬截；六样式确定性轮换；单页/durations=None 保旧 concat 回归保护）；live7 实证连带修复 approve glob 吞残留旧 PNG（张数不符 → 按 slides.json 权威页数确定性重渲染）。TDD 14 例红→绿三文件 117 全绿 + 13 文件回归全绿。live7 真机 PASS：LLM 自选 sunset 命中夏日生活内容、5 页 54.08s≈源、抽帧三态实锤交叉淡化 + 人物右下角抠像稳定 |
| v2.14 | 2026-10-29 | 10.26：deck 幻灯片自动配图（用户裁决 AI 生图）— 契约：parse_deck_json 收录可选 image_prompt（非 str 丢/超长截 200）+ _DECK_PROMPT 加画面描述要求（英文 ≤30 词具象场景竖构图）；生图：_generate_slide_images（dashscope key + WanxAdapter 720x1280 → deck_auto/img_NN.jpg → slides[i].image 绝对路径；无 key 返 0、单页失败降级纯文字绝不阻断）；版式：_slide_html 加 image 参数 — base64 内嵌全屏背景 + _DECK_IMAGE_SHADES 三主题罩层（与 10.25 自选主题联动）+ .fg 文字浮层，图缺失静默回退纯文字（CSS 一并去除）；TDD 8 例红→绿三文件 125 全绿。live8 真机 PASS：image_prompt 5/5 具象贴题、万相生图 5/5 落盘、theme=sunset 自动选、成片 54.08s≈源，抽帧终验配图+罩层+金句+人物右下角抠像全部进片 |
| v2.15 | 2026-10-30 | 10.27：智能裁剪三连修 + 串联端到端打通（live9 实证）— ①has_audio_track 加 PyAV 兜底（裸调 ffprobe 恒 OSError → 恒误判无音轨，smart_cut 从未跑通）；②silence_detect._run 对齐 resolve_ffmpeg 解析链（裸调 ffmpeg WinError 2）；③pad 补偿数学修复（duration=(end+pad)-s_eff，报告口径含 pad）；④串联断链修复：OverlayInput.cut_video_path（本体/时长切裁后轴，转录仍原轴+TimeMapper）+ _remap_transcript_rows 句边界映射（gap 内句丢弃、其余平移，首轮映射后持久化防二次漂移）。TDD 8 例红→绿，五文件 142 项全绿。live9 PASS：检出 0.8s 口误重复 → 裁后 53.82s ≈ 报告 53.8s；串联成片 53.80s ≈ 裁后（源 54.10s），裁剪首次真实参与成片 |
| v2.16 | 2026-10-30 | 10.28：静音检测自适应 + 候选收缩 + pad 交叠修（live10 实证）— ①detect_silence_adaptive 四档自适应（-35dB 对高底噪素材恒 0 检出 → 命中 -30dB 出 6 段）；②detect_silence_candidates 收缩（>0.6s 两端各留 0.2s 呼吸、≤0.6s 不出候选）；③第 4 层 bug：删段 0.24s < pad 之和 0.5s → 相邻保留段交叠 0.26s → concat 回填总长，per-gap 收缩 pad_eff 修复；④smart_cut 接线 adaptive（显式参数仍走单档）。TDD 6 例红→绿，135 项全绿。live10 成片 53.72s（vs live9 53.82s），报告口径与成片闭环 |
### 10.30 overlay_decor 模式（2026-10-30，方案）

**用户需求**（live11 后澄清）：原片全屏保留、不抠人物；素材卡按口播节奏
叠加在原片画面上，不遮五官，带进出特效。管线第三种合成形态（原片直出 /
pip_presentation 之外的）。

**设计**：

- OverlayInput 加 `overlay_decor: bool`、`decor_scale: float = 0.85`、
  `decor_effect: str = "fade"`（v1 仅 fade；slide/zoom 后续参数扩展）
- 素材卡渲染 `_render_decor_card(slide, theme, width)`：PIL 渲染横幅卡
  （720 宽 × 按页型高 320-460），圆角 + 半透明深底（可读性），左配图
  缩略（wanx image_prompt 已有）右文字（标题/要点，主题色强调）——
  复用 deck 渲染的字体与主题色
- 合成 `_execute_decor(parsed, body_video, duration, scope)`：
  `-i 原片 -i card1..N` → 每卡 `[i:v]format=rgba,fade in/out alpha`
  （进入退出各 0.4s，时间窗 = 句边界页区间）→ `overlay=(W-w)/2:24`
  （顶部居中，头部以下，不遮五官）→ 原片音画不动
- 闸门：deck_preview_ready 照旧（卡 PNG + 位置示意），approve 后合成；
  裁剪串联（cut_video_path/kept_segments）照旧前置

**测试**：卡渲染（尺寸/类型高度差/半透明）+ 命令构造（fade 时间窗数学/
overlay 位置/-t 总长）+ gate 分支。

**实现**（10.30 当日）：

- deck_orchestrator：`_decor_card_html`（720 宽透明底横幅卡，quote
  320/points 420 高，左配图缩略右标题+要点，主题色/keyword 强调沿用
  deck 契约）+ `render_decor_cards`（Playwright omit_background 透明
  截图保 alpha）
- talking_head_overlay：OverlayInput 加 `overlay_decor`/`decor_scale`
  (0.85)/`decor_effect`(fade)；入口 `pip or overlay_decor` 复用 pip 的
  deck 闸门链，deck images 就绪后分叉 `_finalize_decor`：slides.json
  → 卡渲染 → 句边界页区间 bounds（无 rows 均分）→ `_build_decor_cmd`
  → scope_dir/decor_{stamp}.mp4；decor_done 事件
- `_build_decor_cmd`：width=原片宽，卡目标宽=int(width*scale)（卡 720
  固定宽 > 竖片 576 时会溢出，按主画面比例算）；每卡 `-loop 1` +
  scale+rgba+fade(alpha) in/out + overlay=(W-w)/2:24；音频直通 -t 硬限

**live12 真机挖出 2 处致命 bug（都修）**：

1. **decor 入口未接**（首跑走进旧叠加动画链）：overlay_decor=True 但
   pip=False → execute 走 OverlayPlan 常规链。修复：入口条件
   `pip or overlay_decor`（合成段分叉，无副作用）
2. **素材卡全程不可见**：静态图输入无 `-loop 1` → 单帧 t=0，
   fade in st=0 时刻 alpha=0，overlay eof_action=repeat 永远透明
   （抽帧 12/12 无卡实证）。修复：`-loop 1` 转连续流；顺带修卡宽
   相对原片比例（scale=iw*0.85 → int(width*scale)）

**live12 真机验收（32.77s 用户素材，PASS）**：

- 智能裁剪：自适应静音 1 刀（尾部 30.2-30.9s 空气），32.77 → 32.60s
- deck：6 页 midnight（4 页型混用，wanx 2 页超时降级纯文字、重跑
  6/6 全生图），LLM 自主重规划内容与 live11 略异（同主题更精炼）
- decor 合成：6 卡句边界窗口 [[0,6.36],[6.36,12.5],[12.5,18.34],
  [18.34,22.46],[22.46,27.3],[27.3,32.62]]，成片 32.63s ≈ 裁后
  32.62s；抽帧 12/12：原片全屏保留、人物五官零遮挡、卡顶部居中
  （配图+标题+要点+keyword 橙色强调）、fade 进出
- 产物：outputs/talking_head_overlay/decor_395cdb52.mp4（样张
  live12_frames/）
- 回归：person_matting/deck/overlay_revision/smart_cut 四文件
  199 项全绿（新增 decor 4 例）

### 10.31 效果层优化 M1：词级 karaoke 字幕 + 轻装饰卡（2026-10-31，方案 effect-layer-optimization.md 已批准）

**背景**：live12 后用户裁决"套模板感强、与预期差距大" → 开源效果层补课
（Remotion template-tikTok / edit-greek-reel / MoneyPrinterTurbo / RemotionUI），
产出 `docs/effect-layer-optimization.md`（P0-1~P2-7 七项 + M1/M2/M3 里程碑）。
本节 = M1（P0-1 + P0-3），验收物为 32.77s 用户素材连播成片，用户实看裁决。

**词级数据链打通（P0-1 地基，两处存量缺陷修复）**：

1. `_transcribe_cpu_subprocess` 输出补 `words` 词级数组（此前只出句级，
   decor 链永远拿不到词戳）
2. `_remap_transcript_rows` 词戳随句平移 + 删除区词丢弃（开区间判删
   `g0 < t < g1` — 词端恰在保留段边界的词是可听到的，闭区间会误杀）

**P0-1 词级 karaoke 字幕**：

- `_karaoke_frame_plan(rows, duration)` 纯函数：每词一帧，帧窗 =
  [词 start, 下一词 start)，句末词帧延伸到下一句首词 start（字幕常驻
  到换句），全片末帧到 duration；词戳缺失返 []（调用方整体跳过，
  降级无字幕绝不阻断）
- `_render_karaoke_frames`：单页 HTML + JS `setFrame(si, wi)`，逐词
  evaluate 切换 + omit_background 透明截图（每词 1 帧 ≈ 50-100ms，
  30s 口播 162 帧实测 ~30s）；白字 900 权重 + 8 向 text-shadow 黑描边 +
  当前词 `#39E508` 高亮 + scale(1.08)；字号三档自适应（≤24 字 46px）
- `_karaoke_display_data`：词文本繁→简（live13 实证 whisper 词流偶发
  繁体"時候/沒有"，字幕直出观众必须简体，对齐 _simplify_cn 契约）
- `_write_ffconcat`：帧计划 → ffconcat 清单（不均匀帧时间轴）；末帧
  重复一次无 duration 行（concat demuxer 末帧 duration 常被吞的官方
  FAQ 兜底）
- `_build_decor_cmd(subtitle_ffconcat=)`：concat demuxer 输入 →
  `fps=30,format=rgba` → overlay 70% 高度带（int(h*0.70)），恒在链条
  最外层（字幕不被素材卡遮盖）；卡 overlay y 从 24 移安全区
  max(48, 5%h)；带宽=主片宽（源片 576 非 720，居中不裁字）

**P0-3 横幅卡退役 → 轻装饰**：

- `_decor_card_html` 重写：无实色底框/无 box-shadow — 顶部
  `linear-gradient(180deg,rgba(0,0,0,.62),transparent)` 压暗保可读 +
  白字黑描边（contrast, not decoration）；keyword 要点品牌绿
  `_HIGHLIGHT="#39E508"`（与 karaoke 当前词同源）；配图降为 116px
  小圆图；金句页 64px 海报大字（带要点时降 46px，要点封顶 2 条 —
  live13 实证 64px+3 要点溢出 340px 高度带第 3 条拦腰截断）；
  `.band overflow:hidden` 硬护栏
- `decor_scale` 默认 0.85 → 0.6（卡宽 ≤60%）
- `render_decor_cards` 视口统一 720×340 高度带

**live13 真机验收（PASS，32.77s 用户素材全链）**：

- smart_cut 1 刀（尾部空气 30.2-30.9s）→ 32.60s；deck 5 页 midnight；
  decor 合成 5 卡句边界窗口 + **词级 karaoke 162 帧**；成片 32.63s
  （decor_dd75f99c.mp4）
- 像素级断言：5 个词时刻抽帧，品牌绿高亮 5/5、白字幕 5/5；抽帧目检
  简体正确、当前词高亮跟手、卡内容零截断、五官零遮挡
- 产物：outputs/talking_head_overlay/decor_dd75f99c.mp4（样张
  live13_frames/，成片由用户连播实看裁决 — M1 关口）

**测试**：TestKaraoke 7 例（帧边界数学/降级/渲染/ffconcat/命令接入/
词戳平移/简体）+ TestLightDecor 4 例（无底框/品牌色/60% 默认/内容
适配），旧 decor 2 例按 P0-3 契约更新；四文件 205 项全绿。

---

### 10.32 效果层 M2：内容触发式素材卡 + SFX 音效层（2026-10-31，方案 effect-layer-optimization.md P0-2/P1-4）

**背景**：M1（karaoke + 轻装饰卡）用户裁决通过后按里程碑纪律开工 M2。
验收标准：卡弹入时刻 = 触发词被说出的那一刻；SFX 与画面动作对齐、
不抢人声（成片由用户连播实看裁决）。

**P0-2 内容触发式素材卡（消灭 PPT 感）**：

- deck 契约加 trigger：_DECK_PROMPT 每页增 `"trigger": {"text": "..."}`
  （必须是转录原文中出现的短语）；parse_deck_json 白名单收录（缺失/
  非法 → 无 trigger 回退句边界，绝不阻断）
- 繁→简归一化：live14 实证 whisper 词流繁体被 LLM 照抄回卡面
  （"老闆推單"），title/bullets/trigger 统一过 _simplify_cn
- `_find_trigger_start(text, rows)` 纯函数：词流 substring 首次出现
  定位（多词短语跨词拼接也命中）；未命中 → None
- `_trigger_bounds(...)` 取代句边界窗口：卡从触发词 start 弹入，停留
  min(页时长, 2.5s)；end 封顶"所有其它更晚触发时刻"的最早者（live14
  实证页 4/5 触发顺序与页序颠倒 — 只封顶列表靠后的卡会留 0.12s 叠窗）；
  停留 <0.8s 或未命中 → 回退句边界；seen 去重防同词重复触发
- pop 入场：`_render_pop_frames` 同卡 0.5x/1.08x/1.0x 三尺度 PNG，
  _build_decor_cmd 在 pop_start/+0.10/+0.18 三时刻换图（0.18s 快切 =
  卡"啵"地弹上画面）；pop 帧缺失回退 fade
- 触发轮回调的 transcript rows 持久化 deck_auto/transcript_rows.json
  复用（v2.11 惯例，停点确认不废触发对齐）

**P1-4 SFX 音效层**：

- `sfx/` 内置 6 免版权短音效（whoosh/pop/ding/switch/riser/impact，
  ffmpeg 合成占位、README 记录来源与上线前替换说明）
- `_plan_sfx(bounds)`：每卡入场一音，6 音确定性轮换，t<0.15s 跳过
- `_build_sfx_mix`：anullsrc 全量静音基线 + adelay 逐音对齐 + amix
  weights=1（主音频音量不变），单音 volume=0.18；文件缺失逐个跳过、
  列表空 → 原音频链直出（live14 降级路径实证）

**live14 真机验收（PASS，32.77s 用户素材全链）**：

- 触发对齐 5/5 且窗起点精确 = 词戳：5.18「是你让他忍不下去了」/
  7.00「老板推单」（页 5 触发序颠倒说到即弹）/ 8.78「体验崩了」/
  21.24「团队服务模式就有病」/ 28.46「花钱是为了被晾著吗」；零回退、
  零叠窗（5.18→7.00→8.78 无缝衔接）
- 像素断言 3/3 可见窗 — 判据实证修正：源片顶带原为白墙（前帧白像素
  19 万），"白像素增多 = 卡在场"方向反了；卡在场 = 顶部黑渐变 + 描边
  字 → 白像素显著下降或近黑像素显著上升
- SFX 5 处（pop/riser/whoosh/ding/switch）与卡入场一一对齐；karaoke
  162 帧照常；成片 32.63s（decor_8c602cbd.mp4）
- 用户连播实看裁决「很好，还不错」→ M2 收口

**测试**：TestTriggerDecor 8 例（prompt 契约/parse 收录/首现定位/触发
窗口/全量封顶防叠窗/pop 命令/默认 effect）+ TestSfx 6 例（资产在位/
轮换确定性/mix 命令/缺文件降级/带 SFX 命令/无 SFX 直通），四文件
255 项全绿。


---


| v2.20 | 2026-10-31 | 10.31 效果层 M2：内容触发式素材卡（deck prompt trigger 字段 + parse 白名单收录 + 繁→简；_find_trigger_start 词流首现；_trigger_bounds 触发时刻弹入 + min(页时长,2.5s) 停留 + 封顶所有更晚触发时刻防叠窗 + <0.8s 回退句边界；pop 三帧 0.5x/1.08x/1.0x 0.18s 快切）+ SFX 音效层（6 内置音效确定性轮换，卡入场 adelay+amix volume=0.18 主音频不变，t<0.15 跳过，缺文件降级不阻断）。live14 PASS：触发对齐 5/5 精确=词戳（页5 触发序颠倒说到即弹）、零叠窗、SFX 5 处、可见窗 3/3（判据改渐变压暗/描边增多 — 源片顶带原为白墙）、255 项绿、用户实看裁决通过 |
| v2.19 | 10.31 效果层 M1：词级 karaoke 字幕（_karaoke_frame_plan/单页 evaluate 逐帧/ffconcat 末帧重复/concat 输入 70% 高度带；whisper 子进程补词级 + 词戳随句平移开区间判删 + 繁→简展示）+ 轻装饰卡（无底框渐变压暗描边字、品牌绿 #39E508 同源、decor_scale 0.85→0.6、quote 带要点标题降档+要点封顶、overflow 硬护栏）| 2026-10-31 |
| v2.18 | 10.30 overlay_decor 第三形态：原片全屏+素材卡句边界叠加+fade；-loop 1 卡不可见修复 | 2026-10-30 |
| v2.17 | 2026-10-30 | 10.29：live11 新素材全链 + 三处隐藏 bug 修复 — ①fluff LLM 检测 import 幻影（app.engine.llm_client 不存在，从未跑通）对齐 _resolve_llm；②_remap_transcript_rows 严判：句任一端落删除区即丢弃（中点判定放过跨切口 whisper 幻听碎词 → 页边界错位）；③_slide_durations_from_transcript 改按位置比例切分保证块数==页数（ceil(M/N) 在 11 句/5 页只产 4 块 → xfade mismatch），_build_xfade_cmd 前置 N==N 断言。6 例红→绿，112 项全绿。live11 PASS：32.77→32.62s（尾部空气 1.07s 裁除；fluff 判定素材本身紧凑无离题），5 页 midnight 配图+转场+抠像小窗五官无遮挡，成片 31.63s |
