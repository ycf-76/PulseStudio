# Video Studio P2（方法论装载 + 创作 agent 循环）Implementation Plan

> **For agentic workers:** 按任务顺序逐个执行（推荐 subagent 实现 + 独立审查）。Steps 用 checkbox（`- [ ]`）跟踪。

**Goal:** 在 P0/P1 地基上建成「agent 工作室」：video_studio 入口 skill 驱动带工具的 LLM 创作循环（读打法规则 → 拆内容 → 写场景代码 → 渲染 → 自查 → 修 → 交付），方法论以纯文本装载（内置优先、外部可插），前端可实时预览。验收：两套不同打法文本各出一支片，**平台代码 diff = 0**。

**Architecture:** `video_studio.py`（Skill 入口，接 Reactor skill_call + SSE 进度）→ `studio_agent.py`（轻量工具循环：LLM 多轮 JSON-action，工具白名单闭包绑定 workspace）→ 复用 P0/P1 基建（studio_workspace / studio_assets / studio_render / studio_review / studio_preview / whisper_transcribe / person_matting）。不复用 app/engine Reactor（builder check 已裁决：v1 skill 内自建轻量循环）。

**Tech Stack:** Python 3.12 / FastAPI（backend/.venv）；LLM 走现有 model_router（`resolve_llm()` → `llm.chat(messages, response_format={"type":"json_object"})`，JSON-action 协议不依赖原生 tool-calling）；Playwright/ffmpeg 复用 studio_render；前端 Vue3 + SSE（`VideoTaskCard.vue`）。

**Spec:** docs/superpowers/specs/2026-11-10-agent-video-studio-design.md §5.5/§5.6/§9（已含用户裁决）。

## Global Constraints（红线，审查逐条核对）

1. **平台零风格预设**：`services/`、`skills/video_studio.py`、`skills/studio_agent 相关代码`、adapter 层禁止出现颜色/字体/动效常量；风格只存在于 studio_methods 文本与场景代码。
2. **打法 = 文本输入**：方法论装载机制只做「找到文本 + 拷进 workspace + 供 agent 读」，不解释、不执行、不翻译成代码。
3. **工具白名单**：循环内 write_file 只允许写 `scene/`、`analysis/`、`assets/`；read_file 只允许读 workspace 内路径；拒绝 `..`/绝对路径逃逸。
4. **预算硬顶**：循环总轮数 ≤ 60；渲染尝试 ≤ 3 次；「渲染→审查→修」修复轮 ≤ 3 轮；超限如实报错，不宣称完成。
5. 沿用 P0/P1：ffmpeg 一律 `resolve_ffmpeg`；Playwright 子进程 `os._exit(0)` 硬退出；场景契约 `window.seekTo(t)` 确定性；输出落 `backend/outputs/studio/<film_id>/`；whisper 与 RVM 不同进程（顺序调用即可，均已有子进程隔离）。
6. 每阶段完成即 emit 进度（不吞事件）；事件复用 `video_pipeline_progress` 通道（仿 talking_head_overlay._make_progress_emitter）。
7. 不复制 agent-motion 代码与规则文本；内置方法论为自研自拟文本。

## Review Focus（审查员重点）

1. **风格泄漏**：对新增/改动的平台文件 grep 十六进制色值、`font-family` 写死、具体动效参数——命中即打回（studio_methods/*.md 与测试夹具除外）。
2. **路径逃逸**：write_file/read_file 用绝对路径、`..`、`file:///` 绕过白名单的用例必须被拒。测试须覆盖。
3. **JSON 解析失败死循环**：LLM 输出非 JSON / 未知 action 时计一轮并回喂错误，超预算终止；测试须覆盖。
4. **修复不收敛**：重复「render→review→fix」超 3 轮必须带 review 报告如实失败。测试须覆盖。
5. **进度吞没**：每阶段 emit 一次；失败路径也 emit（fail phase）。测试断言事件序列。
6. **n_frames 对齐**：渲染侧与 review 侧帧数公式必须一致（Task 1 销账）。

---

### Task 1: 销账——渲染帧数公式与 review 对齐

**Files:**
- Edit: `backend/capabilities/video/adapters/studio_render.py`（L68）
- Edit: `backend/tests/test_studio_render.py`

**背景:** `n_frames = max(1, int(duration * fps + 0.999))` 在 `duration*fps` 略高于整数（如 240.0005）时得 240，而 review 侧 `math.ceil(240.0005)=241` → 误报缺帧。

- [ ] Step 1 失败测试：`duration=240.0005/30`（即 duration 使 duration*fps=240.0005）渲染帧数应为 241（现实现 240）
- [ ] Step 2 实现：改用 `math.ceil(duration * fps)`（与 studio_review 完全一致），保留 `max(1, ...)`
- [ ] Step 3 全量 studio 测试回归通过
- [ ] Step 4 Commit

### Task 2: 方法论装载层（studio_methods + load_method）

**Files:**
- Create: `backend/capabilities/video/skills/studio_methods/general_talk/method.md`（自拟「口播叙事打法」文本，含分阶段结构：原则/内容拆解/场景规范/质检标准）
- Create: `backend/capabilities/video/skills/studio_methods/minimal_cards/method.md`（自拟第二套打法，**结构与第一条刻意异构**：数据卡片型 beat 结构、不同入场纪律——供 Task 6 验收泛化）
- Create: `backend/capabilities/video/skills/studio_methods/__init__.py`（`load_method(ref) -> dict`）
- Test: `backend/tests/test_studio_methods.py`

**Interfaces:**
- `load_method(ref: dict) -> dict`：ref 三选一
  - `{"builtin": "general_talk"}` → 读 `studio_methods/<name>/method.md`
  - `{"path": "<用户给的 skill 目录或 md 路径>"}` → 读外部文本（用户 skill 可插入口）
  - `{"text": "<规则原文>"}` → 直接用文本
- 返回 `{"name": str, "text": str, "source": "builtin"|"path"|"text"}`；未知名/路径不存在 → RuntimeError（中文错误）
- 装载动作仅：读文本 → 拷贝到 `ws["inputs"]/rules/method.md`，并在旁写 `rules/MANIFEST.md`（文件清单，供 agent 分阶段按需 read_file，不一次塞爆上下文）

- [ ] Step 1 失败测试：builtin 命中 / 未知 builtin 名报错 / 外部 md 路径读取 / 纯 text 透传 / 拷贝落盘路径正确
- [ ] Step 2 实现 `load_method`
- [ ] Step 3 两份 method.md 文本写完（各 ≥ 60 行，覆盖：beat 划分原则、文字入场纪律、场景代码规范（seekTo 确定性、禁随机）、自查清单）——**这两份是风格唯一来源，写成两种明显不同的打法**
- [ ] Step 4 测试通过 + Commit

### Task 3: 轻量工具循环（studio_agent）

**Files:**
- Create: `backend/capabilities/video/services/studio_agent.py`
- Test: `backend/tests/test_studio_agent.py`

**Interfaces:**
- `class StudioToolLoop`: `__init__(ws, llm, method_text, video_path, emit_progress, max_turns=60)`
- `async def run() -> dict`：返回 `{"ok": bool, "video_path": str|None, "review": {...}|None, "turns": int, "error": str|None}`
- 工具集（JSON-action 协议，LLM 每轮输出 `{"action": "...", "args": {...}}`，循环执行后把结果（截断至 ~4KB）作为下一轮 user 消息回喂）：
  - `read_rules_file(path)` / `list_rules()` — 读 inputs/rules/（分阶段读打法文本）
  - `transcribe()` — whisper_transcribe.transcribe（一次，缓存 analysis/speech.json）+ probe_duration
  - `prepare_assets(kind)` — `frames`（studio_assets.extract_frames 24fps）/ `matting`（person_matting.matte_person → assets/ 透明帧轨）/ `sfx_list`（枚举 SFX 库文件名清单，不选定风格）
  - `save_timeline(timeline)` — validate_timeline 校验，通过则 save_timeline，错误列表回喂
  - `write_scene_file(path, content)` — 限 `scene/` 下（index.html、js/css/资产引用）
  - `render(audio="original")` — studio_render.render_scene（audio 取原片音轨；out_video=ws["out"]/final.mp4）
  - `review()` — studio_review.review_workspace，完整 errors+loudness 回喂
  - `start_preview()` — StudioPreviewServer.start()，URL 经 emit_progress 透传前端（幂等：已启动返回原 URL）
  - `finish(summary)` / `fail(reason)` — 终结动作
- 协议纪律：非 JSON / 未知 action / args 非法 → 计一轮、错误信息回喂；`finish` 前必须已成功 `render` 且最近一次 `review` errors 为空，否则拒绝 finish 并回喂原因
- LLM 获取：构造注入（skill 层负责 resolve）；chat 用 `messages=[system, user...]` + `response_format={"type":"json_object"}`

- [ ] Step 1 失败测试（FakeLLM 按脚本回放动作序列）：
  - a) 正常路径：读规则 → transcribe → save_timeline → write_scene_file → render → review → finish，产出 video_path
  - b) write_scene_file 越界路径（`../x.html`、绝对路径、`assets/` 外）被拒且回喂错误
  - c) 非法 JSON / 未知 action 计轮不崩
  - d) 未 review 通过就 finish 被拒
  - e) 重复 render→review→fix 超过 3 轮 → run 返回 ok=False 且 error 含最近 review 错误
- [ ] Step 2 实现 StudioToolLoop（system prompt 只含：workspace 结构、工具协议、seekTo 契约说明、纪律要求——**零风格内容**）
- [ ] Step 3 测试通过 + Commit

### Task 4: video_studio 入口 skill

**Files:**
- Create: `backend/capabilities/video/skills/video_studio.py`
- Edit: `backend/app/core/skill_registry.py`（内置 skill 模块列表加 `"capabilities.video.skills.video_studio"`）
- Test: `backend/tests/test_video_studio_skill.py`

**Interfaces:**
- `@register class VideoStudioSkill(Skill)`：`node_type="edit"`，`name="video_studio"`，`display_name="AI 口播工作室"`
- Input（pydantic）: `video_path: str`、`method: dict = {"builtin": "general_talk"}`、`brief: str = ""`（用户补充要求，透传给 agent）
- execute（async generator，仿 talking_head_overlay）：
  1. resolve_owner 取 user_id/workflow_id；create_workspace(film_id)
  2. `_make_progress_emitter`（仿现有实现，phase 枚举：`prepare/method/plan/scene/render/review/fix/deliver`，total=8；payload 带 `skill: "video_studio"`）
  3. load_method → LLM resolve（inputs["llm"] 优先，fallback get_model_router().resolve_llm()）
  4. StudioToolLoop.run()，各阶段 emit；preview URL 一经产出随每次 emit 的 payload `preview_url` 字段透传
  5. 交付：yield `{"success": True, "video_path": out/final.mp4, "workspace": film_id 路径, "review_summary": ..., "preview_url": ...}`；失败 yield `{"success": False, "error": ..., "review": ...}`
- 旧流水线 talking_head_overlay 等不动

- [ ] Step 1 失败测试：FakeLLM 注入下端到端（用 2s 测试视频 + 伪打法文本）产出 final.mp4；断言进度事件序列含全部 phase、preview_url 出现在 render 后的事件里；注册表里能查到 video_studio
- [ ] Step 2 实现 + 注册
- [ ] Step 3 全量后端 studio 相关测试回归 + Commit

### Task 5: 前端实时预览接入

**Files:**
- Edit: `frontend/src/components/chat/sse-contract.ts`（`video_pipeline_progress` payload 增可选 `preview_url`）
- Edit: `frontend/src/components/chat/VideoTaskCard.vue`（payload.preview_url 存在时渲染「实时预览」按钮，`window.open(preview_url)`；阶段清单照常）
- Test: `frontend/src/components/chat/composables/useChatSSE.test.ts` 增用例

- [ ] Step 1 失败测试：含 preview_url 的事件 → VideoTaskCard 渲染预览按钮；不含 → 不渲染
- [ ] Step 2 实现两处
- [ ] Step 3 `npm test`（vitest）通过 + Commit

### Task 6: 验收实验（P3 判据并入本次交付验证）

**方式:** 手动真跑（需真 LLM），脚本化于 `backend/tests/test_studio_p2_acceptance.py`，`@pytest.mark.skipif(not os.environ.get("STUDIO_ACCEPTANCE"))` 默认跳过。

- [ ] 用 `live_person_talk.mp4` 前 15s 段落，分别以 `general_talk` 与 `minimal_cards` 各跑一支（STUDIO_ACCEPTANCE=1）
- [ ] 对比两片：timeline.json beats 结构差异真实存在（字段内容非雷同）、场景代码动效差异真实存在、review 报告 errors 为空
- [ ] `git diff` 确认两轮实验之间平台层代码零改动（只有 outputs/studio/<film_id>/ 新增）
- [ ] 用户肉眼验收成片（最终标准：以真实参考视频为唯一效果基准）

## 执行方式

沿用 P0/P1 模式：每任务 subagent 实现 → 独立 subagent 审查（Review Focus 逐条核对）→ 回归测试 → commit。Task 6 跑真实验时现场向用户要确认（消耗真 LLM tokens）。
