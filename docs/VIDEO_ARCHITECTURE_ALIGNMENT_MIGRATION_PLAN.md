# 架构对齐与迁移方案：AI 视频功能 v1.0

> 日期：2026-10-16 | 状态：已确认方向，可进入阶段 0 实施
> 上游依据：
> 1. `docs/VIDEO_PIPELINE_E2E_ANALYSIS.md` —— 现有链路诊断（三层断裂）
> 2. `xiaojia-ai-video-replica-plan.md`（用户提供，2026-10-15）—— 参考产品复刻策划
> 3. `docs/plan_multi_image_video_pipeline.md` —— 旧方案 B（Phase 1 后端写完、Phase 2 前端未交付）

---

## 0. 核心决策（一句话版）

**不平行重建，移植架构。** 把参考策划书中经过验证的三个核心设计——分镜先行、确认闸门、异步任务层——移植进现有平台（Python/FastAPI + Vue3 + PostgreSQL），复用已有资产（ffmpeg 拼接、三 Provider 适配器、素材库、VL+分镜编排链），视频功能从"对话 Agent 里一个断裂的 Skill"升级为"平台的一级产品流"。

---

## 1. 目标与边界

### 1.1 目标

1. 视频生成从"同步阻塞 5–9 分钟"改为"异步任务 + 轮询 + 刷新恢复"；
2. 分镜先行 + 人工确认闸门成为产品流的核心环节（便宜的脚本/分镜图阶段可反复试错，贵的视频生成在确认之后）；
3. Mock Provider 先行，阶段 0 全链路零模型成本可点通；
4. 复用现有资产，不引入第二套账号/素材/前端栈。

### 1.2 边界（首版明确不做）

多用户权限体系、计费与充值、视频超分、对话式剪辑、跨镜头一致性优化、移动端适配。任务表预留 `user_id` / `credits_est` 字段，后续补。

---

## 2. 目标架构（长在现有平台上的形态）

```
┌─────────────────────────────────────────────────────────────┐
│  UI 层（Vue3，新增）                                          │
│  分镜编辑器（网格 · 单镜重生成）  任务进度视图  确认闸门  成片预览 │
└──────────────────────┬──────────────────────────────────────┘
                       │ REST + 轮询 + SSE（双通道）
┌──────────────────────▼──────────────────────────────────────┐
│  API 层（FastAPI，新增 /api/video/*，见 §5）                   │
└──────────────────────┬──────────────────────────────────────┘
┌──────────────────────▼──────────────────────────────────────┐
│  任务层（新增，见 §4）                                         │
│  video_jobs 状态机（queued/running/succeeded/failed）          │
│  内存 Worker（并发：生图 2 · 视频 1）+ 启动恢复 + 僵尸回收       │
└──────────────────────┬──────────────────────────────────────┘
┌──────────────────────▼──────────────────────────────────────┐
│  编排服务层（从 multi_image_video.py 拆出，见 §3）              │
│  VL 理解 · 分镜编排 · 关键帧 · 视频片段 · ffmpeg 拼接            │
└──────────────────────┬──────────────────────────────────────┘
┌──────────────────────▼──────────────────────────────────────┐
│  Provider 适配层（现有 adapters 翻转为 submit/poll 契约）       │
│  DashScope · Kling · Ark ·【新增】Mock（零成本联调）            │
└──────────────────────┬──────────────────────────────────────┘
┌──────────────────────▼──────────────────────────────────────┐
│  存储层（复用）  uploads/ + video_assets/image_assets 表        │
└─────────────────────────────────────────────────────────────┘
```

### 2.1 关键架构决策（ADR）

| # | 决策 | 选择 | 理由 |
|---|---|---|---|
| D1 | 任务队列 | **进程内 asyncio Worker + DB 状态**，不引 Celery/Redis | 平台无队列基建；单机可跑；JobService 用 PostgreSQL 认领（`FOR UPDATE SKIP LOCKED`），升级独立 Worker/Redis 时表结构与接口不变 |
| D2 | 素材上传 | **复用现有 `POST /api/assets` 直传后端**，不做签名 URL | 现有为本地磁盘存储，签名 URL 直传要配对象存储，收益不抵复杂度 |
| D3 | 会话资源 | **复用 `chat_sessions`**，不新建 conversations 表 | 避免两套会话体系合并问题；视频分镜以 `video_storyboards.user_id + session_id?` 关联 |
| D4 | 进度通道 | **轮询为主（`GET /jobs/{id}`），SSE 为辅** | 轮询天然支持刷新恢复与断线重连；SSE（复用 `sse_bus`）只做实时增强，双发同一 `progress` 数据源 |
| D5 | 入口范式 | **产品流（REST + UI）为主，对话工具为薄封装** | 视频是长耗时重流程，确定性入口比 LLM 自主决策可靠；聊天里说"做视频"后期通过薄工具创建 storyboard 任务 |
| D6 | 任务持久化 | 全部落 `video_jobs` 表，进程重启后 `running → queued` 重置 | 满足策划书"刷新页面任务恢复"与进程级容错 |

---

## 3. 旧资产处置清单

| 旧组件 | 处置 | 说明 |
|---|---|---|
| `_vl_understand_images` / `_generate_storyboard`（multi_image_video.py） | **复用，原样搬入编排服务层** | 质量良好，含降级；`video_pipeline.plan()` 死代码删除后由本服务统一供血 |
| `_generate_shot_frames` | **复用 + 修 P0-2**（`save_asset` 加 `source` 参数） | 修完后关键帧才真正落库 |
| `_generate_shot_clips` | **改造**：从"同步轮询等 bytes"改为"提交作业 + Worker 执行"；修 P1-1（模型名按 provider 解析）；修 P0-3（refine 解包） | 这是同步改异步的核心改造点 |
| `compose_video_concat` / `compose_video_concat_simple`（video_compose.py） | **原样复用**，移入 Worker 执行 | xfade 双链 + acrossfade 已成熟，是平台里质量最高的视频资产 |
| 三家 Provider adapters（video_gen.py） | **改造为作业契约**：把内部已有的 `_submit → _poll → _download` 三段翻出来，暴露 `submit/poll/download` | 现有 DashScope/Kling/Ark 内部本就是异步任务模式，改造是"翻出内部结构"，不是重写 |
| `MultiImageVideoSkill`（@register） | **退化为薄封装**：`execute()` 只做参数校验 + 创建 storyboard/job，返回任务凭据 | 保留 ReAct 入口（对话触发），执行全部走任务层 |
| `preview_only` / `refine_shots` / `storyboard_preview` | **激活**：preview 走新 `confirm` 接口前形态，refine 解包 bug 一并修复 | 后端逻辑已在，缺前端 |
| `adapters/video_pipeline.py`（1200 行） | **删除** | `VideoPipeline`/`ComputeScheduler`/`provider_capability_report` 全仓无实例化且 `plan()` 有解包 bug；仅迁移 `TieredModelRouter` 到 `app/services/model_tier.py` 后删除整文件 |
| `session.py` auto-inject（L590-604） | **简化**：假 ID 判定与 `guard_asset_ids` 统一 | 修 P2-4（`asset_` 前缀误判） |
| `asset_library.py` | **原样复用** | `save_asset`/`save_video_asset`/`url_to_base64`/`url_to_local_path` 不动 |

---

## 4. 任务层数据模型

### 4.1 `video_storyboards`（分镜会话态）

```python
class VideoStoryboard(Base):
    __tablename__ = "video_storyboards"
    id: Mapped[str]            # ULID，即 storyboardId
    user_id: Mapped[str]
    session_id: Mapped[str | None]   # 关联 chat_sessions（可选，对话起源）
    title: Mapped[str]
    prompt: Mapped[str]              # 用户原始描述
    status: Mapped[str]              # draft → confirmed → composing → completed
    ratio: Mapped[str]               # 9:16 / 16:9 / 1:1
    shot_duration: Mapped[float]     # 每镜时长（秒）
    scene_description: Mapped[dict | None]   # VL 输出（JSON，可空=已降级）
    shots: Mapped[list]              # JSON 数组，结构见下
    reference_asset_ids: Mapped[list]        # 上传的参考图（ImageAsset id）
    final_asset_id: Mapped[str | None]       # 成片 VideoAsset id
    degradation: Mapped[list]               # 降级记录（JSON）
    created_at / updated_at
```

`shots` JSON 元素结构（沿用旧 storyboard 字段 + 新增 `shot_id`）：

```json
{
  "shot_id": "01J...",            // ULID，单镜重生成/任务的稳定标识
  "shot_index": 1,
  "caption": "开场引入…",
  "image_prompt": "...",          // 分镜图提示词
  "motion_prompt": "缓慢推进",      // 视频运动指令
  "reference_images": [1],        // 引用第几张参考图
  "duration": 5.0,
  "transition": "cut",
  "frame_asset_id": "01J...",     // 分镜图落库后回写
  "frame_url": "/uploads/assets/...",
  "frame_source": "ai_generated", // ai_generated / reference / mock
  "clip_asset_id": "01J...",      // 视频片段落库后回写
  "clip_mode": "video",           // video / ken_burns
  "clip_error": ""                // 失败原因（有则展示重试入口）
}
```

> 决策：shots 内嵌 JSON 不拆表。理由：分镜编辑是整文档式操作（编辑后整份回传），拆表会让"增删镜头"变成孤儿行清理问题；`shot_id` 已提供任务级稳定标识。边界：单镜像素级历史版本需求出现时再拆。

### 4.2 `video_jobs`（任务状态机）

```python
class VideoJob(Base):
    __tablename__ = "video_jobs"
    id: Mapped[str]              # ULID，即 jobId
    user_id: Mapped[str]
    storyboard_id: Mapped[str]
    shot_id: Mapped[str | None]  # compose 类任务为 NULL
    job_type: Mapped[str]        # shot_image / shot_video / compose
    status: Mapped[str]          # queued / running / succeeded / failed
    progress: Mapped[int]        # 0-100
    provider: Mapped[str]        # dashscope / kling / ark / mock
    model: Mapped[str]           # 实际使用的模型名（按 provider 解析后）
    input: Mapped[dict]          # JSON：prompt/ratio/duration/last_frame_url…
    result_asset_id: Mapped[str | None]
    result_url: Mapped[str | None]
    error: Mapped[str]
    attempts: Mapped[int]        # 重试计数，上限 3
    credits_est: Mapped[float]   # 成本预估（首版占位，阶段 4 启用）
    created_at / updated_at / started_at / finished_at
```

**状态机：**

```
                 ┌──────────── retry(attempts<3) ───────────┐
                 ▼                                          │
[created] → queued → running → succeeded
                 │           │
                 │           └──→ failed ──→（展示重试入口，用户可手动 retry）
                 └──→ canceled（首版不做）
```

**Worker 语义（D1/D6）：**
- 认领：`UPDATE video_jobs SET status='running', started_at=now() WHERE id IN (SELECT id FROM video_jobs WHERE status='queued' ORDER BY created_at LIMIT n FOR UPDATE SKIP LOCKED) RETURNING id`；
- 并发：`shot_image` 最多 2（复用 `multi_image_video_max_concurrent_frames`），`shot_video` 串行 1（API 配额保护），`compose` 独立；
- 启动恢复：Worker 启动时把残留 `running` 重置为 `queued`（attempts+1，超 3 次标记 failed）——进程崩溃/重启不丢任务；
- 进度：Worker 内 `poll` 回调同时更新 `video_jobs.progress` 并经 `sse_bus.publish(session_id, "video_job_progress", {...})` 双发。

---

## 5. 接口清单（对齐参考策划书表 2，改造为平台约定）

前缀 `/api/video`，响应统一 `StandardResponse`，鉴权 `Depends(get_current_user)`。

| # | 方法 | 路径 | 对齐策划书 | 说明 |
|---|---|---|---|---|
| 1 | POST | `/api/video/conversations` | 同左 | 创建分镜会话 → `{storyboardId}`（绑定 user_id + 可选 session_id） |
| 2 | POST | `/api/video/conversations/{id}/stream` | 同左 | SSE：VL 理解 + 分镜编排；事件 `video_storyboard_delta`（content 增量）+ `video_storyboard_ready`（分镜脚本落库） |
| 3 | POST | `/api/video/storyboards/{id}/shots/{shotId}/render` | 同左 | 生成/重生成单镜分镜图 → `{jobId}`；未 confirm 状态也可调用 |
| 4 | GET | `/api/video/jobs/{jobId}` | 同左 | 轮询 → `{status, progress, assetUrl?, error?}`；前端进度视图唯一数据源 |
| 5 | POST | `/api/video/storyboards/{id}/confirm` | 同左 | **闸门**：锁定分镜（status=draft→confirmed）；未确认时接口 6 拒绝 |
| 6 | POST | `/api/video/storyboards/{id}/render` | 同左 | 为全部镜头提交 `shot_video` 任务 → `{jobIds[]}` |
| 7 | POST | `/api/video/storyboards/{id}/compose` | 环节 7（策划书隐含） | 全部 clip 就绪后提交 `compose` 任务 → `{jobId}`；产出成片 VideoAsset |
| 8 | GET | `/api/assets/{assetId}` | 同左 | **复用**：素材经现有 `/uploads/...` 静态路由访问，不新增 |
| +A | GET | `/api/video/storyboards/{id}` | —（策划书缺失） | 分镜当前状态 + 各 shot 素材 + 关联 job 列表；**刷新恢复的核心接口** |
| +B | POST | `/api/video/jobs/{jobId}/retry` | —（策划书缺失） | 单任务重试（failed → queued，attempts+1） |

**SSE 事件格式**（复用 `sse_bus`，channel = session_id 或 storyboardId）：

```
data: {"event_type": "video_storyboard_delta",  "content": "正在解析创作意图…"}
data: {"event_type": "video_storyboard_ready",  "storyboard": {...}, "shots": [...]}
data: {"event_type": "video_job_progress", "jobId": "...", "status": "running", "progress": 42}
```

**接口层纪律：** 所有创建类接口立即返回 `{jobId}`/`{storyboardId}`，HTTP 202 语义；任何接口不得同步等待模型生成。这是治愈"同步阻塞 5–9 分钟"的契约保证。

---

## 6. 阶段 0 实施计划（零模型成本，可立即动工）

### 6.1 工作包拆分

| 包 | 内容 | 产出 | 预估 | 依赖 |
|---|---|---|---|---|
| **W1 任务层** | 两张表 + alembic 迁移（006）；`JobService`（提交/认领/心跳/完成/失败/重试/启动恢复）；进程内 Worker（asyncio 消费循环，按 job_type 并发） | `video_jobs`/`video_storyboards` 可用；`POST/GET/retry` 三个任务接口 | 3–4 天 | — |
| **W2 Mock Provider** | `MediaProvider` 瘦契约（`estimate/submit/poll/download`）；`mock` provider（固定 3–8s 耗时、可配置失败率、返回本地占位素材）；`_resolve_adapter` 增加 `provider="mock"` 分支与 settings 开关 `video_provider_mock` | 无 Key 环境全链路可跑 | 1 天 | — |
| **W3 编排服务化** | `app/services/video_orchestrator.py`：从 `multi_image_video.py` 迁出 VL/分镜/关键帧/片段/拼接五个函数为纯 service（注入 JobService）；**含 P0-2 `save_asset` 修复、P0-3 refine 解包修复、P1-1 provider 模型名解析修复** | 旧 skill 逻辑变成可被 API 与 Agent 双向调用的服务 | 2–3 天 | W1 接口形态 |
| **W4 REST 接口** | §5 表中 1–7、+A、+B 共 10 个接口；confirm 闸门校验；权限校验（user_id 隔离） | 接口契约冻结，可联调 | 2 天 | W1+W3 |
| **W5 前端** | 新增「AI 视频工坊」路由页：① 参考图上传（复用 `uploadAssets`）② 分镜编辑器（镜网格、单镜重生成、caption/prompt 编辑、增删镜头）③ 确认闸门 ④ 任务进度视图（轮询 `GET /jobs/{id}` + SSE 增强 + 刷新恢复）⑤ 成片预览（**补 video 页面渲染分支**，P1-2）⑥ 任务列表（按 storyboard 聚合） | 策划书验收标准 6 条的前端载体 | 4–5 天 | W4 契约（可先按 mock 数据并行 60%） |
| **W6 联调验收** | 策划书 §2 验收标准逐条过；Mock 失败率 30% 压测重试路径；Worker 重启恢复演练 | 阶段 0 签收 | 1 天 | 全部 |

**并行建议：** W1 ∥ W2 立即开工；W5 在 W4 冻结契约后即可并行（先接 mock 数据）；W3 是唯一动旧代码的包，与 W1/W2 无阻塞。

### 6.2 验收标准映射（对齐策划书 §2）

| 策划书验收标准 | 由谁满足 | 验证方式 |
|---|---|---|
| 一句描述 → ≥3 镜分镜脚本 | W3+W4（接口 1/2） | Mock LLM 返回固定 3 镜脚本 |
| 每镜一张分镜图、可单张重生成 | W4（接口 3）+ W5 编辑器 | 重生成只重置该 shot 的 job |
| 确认分镜后才提交视频任务 | W4 接口 5/6 闸门校验 | 未 confirm 调接口 6 返回 409 |
| 任务四态 + 进度百分比可见 | W1+W4（接口 4）+ W5 进度视图 | Mock 任务跑完 queued→running→succeeded 全链 |
| 刷新页面任务恢复 | +A 接口 + W5 恢复逻辑 | 生成中强制刷新，进度条从断点继续 |
| 无网络无 Key 全链路跑通 | W2（mock provider） | 断网跑通接口 1→7 |

---

## 7. 后续阶段（策划书五阶段在本平台的对应）

| 阶段 | 内容 | 与旧链路修复项的合并 |
|---|---|---|
| **1 真 LLM** | `mock → deepseek-chat`（`settings.video_llm_provider`） | 编排链已在 W3 迁好，切Provider即用；**P0-1 SkillPrimitive 同步做**：skill 退化为薄封装（§3），对话 ReAct 入口接通 |
| **2 真生图** | `mock → dashscope wanx` | **P0-2 已在 W3 修复**，此阶段只需把 mock 指到真实 `WanxAdapter.generate`（并发 2 走 JobService 信号量） |
| **3 真视频** | `mock → 三 provider`，adapters 翻转为 submit/poll 作业契约 | **P1-1 已在 W3 修复**（模型名按 provider 解析）；`last_frame_url` 首尾帧连贯逻辑随 adapter 翻转保留 |
| **4 工程化** | 队列持久化升级（Redis/独立 Worker）、计费额度（`credits_est` 启用）、失败重试策略调优、并发参数化 | 计费与 `video_jobs.credits_est` 字段已在模型预留 |

> 与旧诊断报告的时间线关系：原报告中的 P0-1/P0-2/P0-3/P1-1/P1-2 全部在阶段 0–2 内被消化（W3/W5/W6），不再需要独立的"修旧链路"项目。

---

## 8. 风险与对策

| 风险 | 影响 | 对策 |
|---|---|---|
| 内存 Worker 单点（进程重启丢进行中状态） | 任务中断 | D6 启动恢复 + 重启即重置；升级路径（Redis/独立 Worker）契约不变 |
| ffmpeg 拼接跑在 Worker 异步上下文 | 阻塞事件循环 | `compose_video_concat` 本就是 `asyncio.create_subprocess_exec` 异步实现，直接可用；Worker 循环需保证不 await 非协程阻塞调用 |
| 前端轮询频率 | 服务端压力 | 进度视图 2s 轮询 + SSE 实时校正；任务终态停止轮询；`GET /jobs/{id}` 加简单缓存头 |
| 旧 skill 与新接口并存期行为分裂 | 双轨混乱 | 迁移期 `MultiImageVideoSkill.execute` 直接委托 storyboard 服务（§3），不留双实现 |
| 分镜编辑并发冲突（两人同编） | 覆盖丢失 | 首版不做协同，`PUT storyboard` 带 `updated_at` 乐观锁，冲突返回 409 |
| mock 与真实 provider 行为分叉（失败率/耗时模拟失真） | 联调通过上线翻车 | Mock 契约测试：同一组输入在 mock 与真实 provider 上的状态机路径必须一致（CI 加契约用例） |

---

## 9. 阶段 0 完工定义（Definition of Done）

1. 断网、零 API Key 环境下，前端完成"上传参考图 → 流式分镜 → 单镜重生成 → 确认 → 提交视频 → 进度可见 → 刷新恢复 → 成片预览"全流程；
2. 策划书 §2 六条验收标准全部通过并记录截图/录屏；
3. `video_jobs` 表有完整状态机流转记录；Worker 重启恢复演练通过；
4. `multi_image_video.py` 旧实现已退化为薄封装，`video_pipeline.py` 死代码已删除（TieredModelRouter 已迁出）；
5. 接口契约（§5）由 `backend/app/api/routers/` 下的路由 schema 承载，并通过 `/openapi.json` 可查。

---

## 10. 一句话总结

参考策划书验证了"分镜先行 + 确认闸门 + 异步任务"这套范式是对的；本方案把这套范式移植进你已有的平台，复用你最成熟的资产（ffmpeg 拼接、三家适配器、编排链），只新建任务层和前端两个真模块——**阶段 0 约 13–16 人天，之后每接一类真模型只是换一个 Provider 实现**。