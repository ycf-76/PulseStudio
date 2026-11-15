# AI 视频链路端到端隐性问题分析

> 日期：2026-10-16 | 分析人：栖墨 | 范围：图片上传 → 视频制作 → 交付预览
> 状态：**只读分析，未改任何代码**
> 分析对象：`multi_image_video` Skill 及其上下游（前端 ChatView/useChatSSE → chat_agent API → ReAct loop → Skill → adapters → SSE）

---

## 0. 结论（TL;DR）

**链路整体不通、未闭环。** 分段结论：

| 段 | 环节 | 状态 |
|---|---|---|
| A | 前端上传图片 → asset_library 落库 | ✅ 通 |
| B | 前端发送 → `/api/v1/chat/agent` 带 `image_asset_ids` | ✅ 通 |
| C | 后端注入提示 + auto-inject 守卫 | ✅ 通（半通，见 P2-4） |
| D | **LLM → Skill 执行入口** | ❌ **断（最致命）** |
| E | Skill 内部 Step 1–6 编排逻辑 | ⚠️ 半通：逻辑完整，但 Step 3 有必爆 bug，Step 4 跨 provider 错配 |
| F | SSE 进度事件 | ❌ 断（后端发了，前端无消费分支） |
| G | 视频草稿 → 前端渲染 | ❌ 断（无 video 页面渲染分支） |

一句话：**用户现在上传图片说"用这些图做视频"，LLM 根本看不到也调不到 `multi_image_video`，整条流水线没有任何生产环境入口。** 即使绕过入口直接触发，Step 3 的关键帧也会 100% 静默降级。

---

## 1. 端到端链路地图（代码级）

```
[前端] ChatView.vue
  triggerImageUpload → uploadAssets() → POST /api/assets → ImageAsset
  pendingImageAssets（发完清空 ✅）
  sendAgentMessage(conv, text, startTime, imageAssetIds)
      │  image_asset_ids
      ▼
[API] POST /api/v1/chat/agent  (api/routers/chat_agent.py)
  · message 追加提示 "[用户附带了 N 张图片，asset_ids: ...]"  (L830-834)
  · ChatAgent.process(image_asset_ids=...)  (agents/chat_agent.py L64/L100)
  · input_data["image_asset_ids"] → session.run(topic, context=input_data)
      ▼
[ReAct] engine/reactor/session.py
  · _dispatch 自动注入 image_asset_ids 到 multi_image_video/auto_short_video  (L590-604)
  · primitives = PRIMITIVES_BY_NAME —— 只有 13 个基础工具，无任何 produce skill  (L57)
  · system prompt = prompt.py 从 ALL_PRIMITIVES 生成 —— LLM 看不到 multi_image_video
      ▼
  ✗✗✗ 断层：primitives.get("multi_image_video") → None → "unknown tool"  ✗✗✗
      ▼
[Skill] skills/produce/multi_image_video.py（若有入口）
  Step1 VL 理解（QwenVL 多图 → JSON）
  Step2 LLM 分镜编排（DeepSeek → shots JSON，失败有默认分镜降级）
  Step3 text2image 关键帧（WanxAdapter 并发生图）      ← P0-2 在此
  Step4 image2video 逐镜串行（tier router 模型名）      ← P1-1 在此
  Step5 ffmpeg 拼接（xfade/concat，音画同步）           ← 实现质量高
  Step6 save_video_asset + SSE card_draft_ready
      ▼
[SSE] services/sse_bus → session channel
  · multi_image_video_progress（6 步进度）              ← P1-2：前端无 case
  · card_draft_ready（template="video"，页面带 videoUrl）← P1-2：前端无渲染分支
```

---

## 2. 断点与隐患清单（按严重度排序）

### P0-1｜Skill 没有可执行入口：整条链路断在最前端

**证据：**

1. `engine/reactor/primitives.py` 的 `ALL_PRIMITIVES` 只有 13 个：file_read / file_write / file_edit / bash / glob / grep / xhs_search / image_gen / image_read / image_analyze / llm_generate / viral_score / content_check。**没有任何 produce skill 的 primitive 包装类。**
2. `session.py:57`：`self.primitives = {p.name: p for p in (primitives or list(PRIMITIVES_BY_NAME.values()))}` —— skill 从头到尾没进过 primitives。
3. `session.py:606-613`：`primitives.get(name)` 查不到即返回 `{"ok": False, "error": "unknown tool: ..."}`。
4. `prompt.py:build_tool_descriptions()` 只遍历 `ALL_PRIMITIVES` 生成系统提示词的工具清单——**LLM 连这个工具的存在都不知道**，不可能发起调用。
5. 全仓搜索 `skill.execute(` 的调用方：只有 primitives.py 里那 13 个硬编码包装、`api/routers/search.py`（搜索 skill）、以及 guardian/hooks 的包装层。**`multi_image_video` / `generate_video` / `auto_short_video` / `card_compose` 等全部 produce skill 零调用点。**
6. `tools/skill_router.py:153` 的注释写着"（`_build_tools_schema` 全量注入）"，`build_compact_catalog` 的 docstring 说"注入 system prompt 的 {skill_routing} 占位符"——**`_build_tools_schema` 和 `{skill_routing}` 占位符在全仓都不存在**，是一层从未落地的半成品设计。
7. 佐证"曾经有计划接通"的孤儿代码：`guardian.py:251` 有 `multi_image_video` 的 GuardianPolicy；`session.py:593` 有针对 `multi_image_video` 的 image_asset_ids 自动注入；`skill_registry.py:67` 注册了模块。全部指向一个不存在的调用层。

**影响：** 上传图片 + 说"做视频"，LLM 只能用文字回复或调 image_gen 等基础工具。视频流水线在生产环境不可达，只能靠测试脚本触发。

**修复方向：** 加一个通用 `SkillPrimitive`（或把 `AgentRegistry.build_session` 里已装配的 `session._skills` 转成 primitives），让 `_dispatch` 能按 skill name 执行，并在执行时注入 `_context`（user_id / session_id / workflow_id）与 `_workflow_id`——skill 代码里 `inputs.get("_context")`、`inputs.get("_workflow_id")` 一直在等这两个注入点，目前无人提供。

---

### P0-2｜Step 3 关键帧生成必定失败：`save_asset()` 签名不匹配（静默降级）

**证据：** `multi_image_video.py:395-401`：

```python
asset = await save_asset(
    user_id=user_id,
    filename=f"frame_{idx:03d}.png",
    content_type="image/png",
    data=img_bytes[0] if isinstance(img_bytes, list) else img_bytes,
    source="ai",          # ← 致命
)
```

而 `services/asset_library.py:165-170` 的真实签名是：

```python
async def save_asset(user_id: str, filename: str, content_type: str, data: bytes) -> dict:
```

**没有 `source` 参数** → 每次调用都抛 `TypeError: save_asset() got an unexpected keyword argument 'source'`。

**为什么是"隐性问题"：** 该调用在 `_gen_one` 的 `try/except Exception` 里，异常被吞掉只打 warning，随后走 `reference_fallback`——用上传的参考原图当关键帧。**每一镜、每一次、100% 触发**，用户和日志读者都只会看到"关键帧 N 生成失败"的 warning，以为偶发。

**影响：**
- "AI 生成分镜关键帧"这一步实际不存在，成片画面永远是用户上传的原图（只是被 image2video 动起来）；
- `frame_asset_id` 永远为空；
- VL 理解、LLM 分镜的价值被压缩到只剩 `motion_prompt`，方案文档里承诺的"分镜图保持场景/角色一致"落空；
- 万相的 t2i 配额白花了（生成了但存不进去，`img_bytes` 下载完就丢）。

**修复方向：** 给 `save_asset` 加 `source: str = "upload"` 参数（落库用），或去掉调用方的 `source="ai"`。

---

### P0-3｜微调（refine）模式双重断裂

**证据 1——解包 bug：** `_resolve_image_urls` 返回 `(urls, local_paths)` 二元组（L161-194），但 `_execute_refine` L997：

```python
image_urls = await _resolve_image_urls(asset_ids, user_id)   # 只接一个值
```

于是 `image_urls` 变成 `([url...], [path...])` 元组。传给 `_generate_shot_frames` 后，fallback 分支 `image_urls[ref_idx]` 拿到的是**列表**，`shot["frame_url"]` 变成 list。进入 Step 4 后 `frame_url.startswith("http")`（L503，在 try 之外）直接 `AttributeError` 向上抛穿 `_generate_shot_clips` → `_dispatch` 兜底成 error observation。**refine 模式必然 500。**

**证据 2——入口缺失：** `refine_shots` / `preview_only` / `storyboard_preview` 模板在前端全仓零引用（grep `frontend/src` 无结果）。计划文档里"Phase 2 前端 待实现"仍然成立：预览编辑 UI 从未交付。

**影响：** 分镜预览 → 用户编辑 → 重新生成 这条产品路径整体不存在；即使做了 UI，后端也会因解包 bug 崩。

---

### P1-1｜provider 与模型名交叉错配：非 dashscope 路径全镜退化

**证据：** `_generate_shot_clips` L464-539：先 `_resolve_adapter(provider)` 拿到**任意 provider** 的适配器，然后无条件用 DashScope 的分级模型名：

```python
i2v_model = router.resolve(PipelineStage.IMAGE2VIDEO, "i2v")  # → "wanx2.1-i2v-plus"
...
video_bytes = await adapter.image2video(..., model=i2v_model, ...)
```

若 `settings.video_provider = "ark"` 或用户传 `provider="kling"`，`wanx2.1-i2v-plus` 这个模型名会被喂给火山方舟/可灵 API → 必然 4xx → 每镜 `image2video 失败` → 全片 ken-burns。而 Ark 的 `last_frame_url`（首尾帧连贯）和 `native_audio` 路径也跟着失效（`multi_image_video` 没走 `video_pipeline.ArkProvider` 那层包装，`generate_audio` 从未被传过）。

另外，即使接通 P0-1 的入口，`ark_model` 配置默认为空字符串，Ark 适配器自身也没有可用的默认模型。

**影响：** 三 provider 里只有 dashscope 一条能跑；另外两条在"生成阶段"静默变图片缓动，用户看到的是"部分镜头是静态图"还以为是模型效果。

---

### P1-2｜交付最后一公里断裂：SSE 进度与视频草稿渲染

**证据：**
1. `multi_image_video_progress` 事件：后端 6 步全程 publish（L744-756），前端 `useChatSSE.ts` 的 switch **没有任何 case**（全仓 grep 无 `multi_image_video_progress`）。5-9 分钟的生成过程在前端完全静默——连"正在生成"的转圈都没有。
2. `card_draft_ready` 前端有处理（useChatSSE.ts:959），`normalizeCardDraft` 也会保留页面全部字段（含 `videoUrl`）。但——
3. **视频页面无渲染分支**：`frontend/src/components/work/**` 与 `frontend/src/card-editor/**` 全仓 grep `videoUrl` / `mediaType` **零命中**。`template: "video"` + `mediaType: "video"` 的页面存得进草稿，但草稿面板/卡片编辑器渲染不出来（大概率只剩标题的空页）。计划文档说的"复用 VideoPreview 组件"没有发生（VideoRenderer 只存在于聊天消息渲染器注册表，走的是另一条消息渲染通道）。
4. `storyboard_preview` 模板同样无前端消费。

**影响：** 即便链路前面全修好，用户仍拿不到可播放的视频——闭环在第 7 段断掉。

---

### P2｜其他隐患（不堵断链路，但会放大故障）

| # | 位置 | 问题 |
|---|---|---|
| 1 | `adapters/video_pipeline.py` 全文件 | **死代码**：`VideoPipeline` / `ComputeScheduler` / `provider_capability_report` 全仓无实例化，只被 import 了 `get_tier_router`/`PipelineStage`。其中 `plan()` L370 有和 P0-3 同款的解包 bug。~1200 行纯维护成本，建议删除或接通。 |
| 2 | `_vl_understand_images` L223-238 | 10 张参考图全部转 base64 进一条 message，DashScope 请求体大小/图片数限制可能超限，失败即静默跳过 VL（有 try 保护，属已知风险）。建议限流降质（缩图后 base64）。 |
| 3 | `_generate_shot_clips` L503-514 | 本地 `/uploads/...` URL 转 base64 data URI 喂 API。DashScope 文档支持 base64；**可灵/方舟对 base64 data URI 的兼容性未验证**，provider≠dashscope 时是潜在失败点，建议改为起临时公网 URL 或让适配器层处理。 |
| 4 | `session.py:599-604` | 假 ID 判定 `not str(_id).startswith("asset_")`，但 `generate_ulid()` 生成的是 26 位 Crockford base32（无 `asset_` 前缀）。LLM 万一传了**真** ID 也会被判定"疑似幻觉"而整体替换为上传列表——用户"只用其中 2 张"的意图被放大成全部。应改为与 `guard_asset_ids` 共用一套校验。 |
| 5 | `multi_image_video.py:713` | `trigger_words` 含裸词"视频""做视频"，与 `generate_video`（"做视频"/"生成视频"）、`auto_short_video` 大面积重叠，`description` 还写着"优先调用此工具"。接通调用层后会出现路由抢占，需要提前设计优先级/仲裁。 |
| 6 | `skill_router.py:178-190` | 触发词匹配有重复分支（两个 if 判断相同条件），且 0.6 汉字重叠阈值很松，"生成视频"可能误命中无关 skill——接通前需要收紧。 |
| 7 | `skill execute` 的 `_context` | skill 里读 `getattr(context, "extra", {}).get("session_id")`，但 chat_agent 注入的 `WorkflowContext.extra` 只有 `work_context`/`model_settings`，**没有 session_id**。接通入口时需补。 |

---

## 3. 各步骤"是否闭环"判定明细

| 步骤 | 闭环？ | 说明 |
|---|---|---:|---|
| 上传图片 | ✅ | `uploadAssets` → ImageAsset → 发完清空 `pendingImageAssets`，无残留 |
| 传参到后端 | ✅ | `image_asset_ids` 三处传递完整（前端 body → router → ChatAgent.process → context） |
| LLM 感知资产 | ✅ | message 追加 asset_ids 提示 + `_run_context.auto-inject` 双保险 |
| LLM 触发 skill | ❌ | **无 primitive、无 prompt 暴露、无 dispatch 路径（P0-1）** |
| asset_id 守卫 | ✅ | `guard_asset_ids` 拦截幻觉 ID 并回带真ID，设计良好 |
| VL 理解 | ✅ | 多图 → JSON，失败降级跳过；返回值 list content 已拉平（有修复痕迹） |
| LLM 分镜 | ✅ | JSON 解析失败有默认分镜兜底 |
| 生关键帧 | ❌ | **P0-2：save_asset TypeError 必失败**，静默降级参考图 |
| 图生视频 | ⚠️ | dashscope 通；跨 provider 模型名错配（P1-1）；总体超时熔断已修 |
| ffmpeg 拼接 | ✅ | xfade 双链（视频 xfade + 音频 acrossfade）+ offset 修复 + 无转场 concat，实现成熟 |
| 存 asset + SSE | ⚠️ | 后端发得出；前端进度无消费、视频草稿无渲染（P1-2） |
| 用户看到成片 | ❌ | 链路第 7 段断 |

---

## 4. 修复优先级建议

**第一批（让链路通，按依赖排序）：**

1. **P0-1 接通执行层**：新增通用 `SkillPrimitive`，把 SkillRegistry 的 produce skills 包成 primitives；`prompt.py` 的工具清单纳入 skills（可先用 `skill_router.build_routing_table` 控制 token）；dispatch 时注入 `_context`（含 session_id）与 `_workflow_id`。
2. **P0-2 `save_asset` 加 `source` 参数**（一行改动，解锁 Step 3）。
3. **P1-1 模型名按 provider 解析**：i2v 模型只在 provider=dashscope 时用 tier router，否则用 provider 自身默认（补 `ark_model` 默认值）。
4. **P1-2 前端补 `multi_image_video_progress` case + video 页面渲染分支**（草稿面板/card-editor 支持 `mediaType:"video"` 页）。

**第二批（打通产品路径）：**

5. P0-3 refine 解包修复 + Phase 2 预览/编辑 UI（`preview_only` → 用户编辑 → `refine_shots` 回传）。
6. 删除或接通 `video_pipeline.py` 死代码（若接通，注意它和 skill 内的实现已重复，先做去冗）。

**第三批（加固）：**

7. P2-4 假 ID 判定与 `guard_asset_ids` 统一；P2-2 VL 图片降规格；P2-5/6 触发词去重叠；P2-7 `_context.session_id` 注入。

---

## 5. 如何验证本分析（复现步骤）

```bash
# 1. 确认无 skill primitive（预期输出只有 13 个基础工具名）
grep -n "class .*Primitive" backend/app/engine/reactor/primitives.py

# 2. 确认 save_asset 无 source 参数（预期签名 4 参）
grep -n "def save_asset" backend/app/services/asset_library.py
grep -n 'source="ai"' backend/app/skills/produce/multi_image_video.py   # 调用方

# 3. 确认前端零消费（预期无输出）
grep -rn "multi_image_video" frontend/src
grep -rn "refine_shots\|preview_only" frontend/src

# 4. 确认 _build_tools_schema 不存在（预期只有注释里那一处）
grep -rn "_build_tools_schema" backend/app
```

---

## 6. 总结

链路不是"某个环节质量差"，而是**三层断裂叠加**：

1. **入口层断裂（P0-1）** —— 设计文档里的 Skill 路由/工具注入层从未落地，LLM 与 skill 之间没有桥；
2. **执行层暗伤（P0-2/P0-3/P1-1）** —— 即使绕过入口，Step 3 被一个参数名 bug 打成永降级，refine 模式会崩，非 dashscope 路径全境塌陷；
3. **交付层断裂（P1-2）** —— SSE 进度和视频草稿渲染都没接，用户侧看不到任何结果。

好在降级策略、超时熔断、ffmpeg 双链拼接、asset 守卫这些"零件"本身质量很高——缺的主要是把它们接起来的那几根线，以及一根接错地方的线（save_asset）。
