<p align="center">
  <img src="./assets/readme/hero.svg" width="100%" alt="PulseStudio：多智能体小红书内容运营平台，从热点选题池到智能体流水线，一键发布小红书笔记">
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10%2B-FF2442?style=flat-square" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/FastAPI-0.110%2B-009688?style=flat-square" alt="FastAPI">
  <img src="https://img.shields.io/badge/Vue-3.x-42B883?style=flat-square" alt="Vue 3">
  <img src="https://img.shields.io/badge/License-MIT-FF2442?style=flat-square" alt="License MIT">
</p>

## 解决什么问题

运营一个小红书账号，日常是这样的：

| 痛点 | PulseStudio 的解法 |
| --- | --- |
| 热点分散在多平台，人工刷榜、去重、判断值不值得跟，效率低还容易错过 | **热点选题池**：多平台热点监控 + SimHash 去重 + LLM 价值评分，持续沉淀经过筛选的可跟选题 |
| 一篇笔记要在 5+ 个工具间切换：查资料、写文案、做配图、查违禁词、手动发布 | **智能体流水线**：主对话智能体调度 12 个专职智能体，一条对话走完「选题 → 创作 → 审核 → 发布」 |
| 内容踩雷限流：违禁词、夸大宣传漏查，发布后才后悔 | **双重审核**：独立 `audit` + `final_review` 审核智能体在发布前把关，配图另有合规检查 |
| 锁死单一模型厂商，成本与能力都不可控 | **多模型 BYOK**：DeepSeek / Qwen / OpenAI / Claude / Gemini / Moonshot / GLM，前端切换器即配即用 |
| LLM 应用不稳定：超时、死循环、静默失败，问题查不到 | **工程化编排**：技能白名单控制能力边界，失败自动重试、熔断、降级、切换模型，LangSmith 全链路追踪 |

## 演示

<!-- ▼ 截图占位：上传截图到 assets/readme/ 后，取消注释并把文件名改成实际文件名
<p align="center">
  <img src="./assets/readme/demo-chat.png" width="100%" alt="主对话智能体：一条对话完成选题、创作、审核与发布">
</p>
<p align="center">
  <img src="./assets/readme/demo-pool.png" width="100%" alt="热点选题池：热点去重、价值评分与水位管理">
</p>
-->

<!-- ▼ 视频占位：GitHub README 不能直接播放仓库里的本地 mp4。
     推荐做法：把视频拖进任意 GitHub Issue/PR 的输入框，生成 user-images.githubusercontent.com 链接后取消注释：
<p align="center">
  <video src="https://user-images.githubusercontent.com/xxxx/demo.mp4" controls muted width="100%"></video>
</p>
-->

> 📷 此处预留演示截图与视频位置，素材待上传。

## 这是什么

**PulseStudio** 是一个多智能体小红书内容运营平台：主对话智能体协调搜索、分析、文案、配图、生图、审核、发布等 12 个内置专职智能体，覆盖「热点监控 → 选题分析 → 笔记创作 → 合规审核 → 发布适配」的完整链路，并通过热点选题池持续沉淀经过去重与价值评分的选题。

## 功能特性

- **智能体协作流水线** — 主对话 Agent（小红书运营搭档）按需调度专职子 Agent；技能白名单控制能力边界，失败时自动重试、熔断、降级或切换模型
- **热点选题池** — 多平台热点监控，SimHash 去重 + LLM 分类 + 价值评分，按水位管理选题库存，支持 REST API 手动干预
- **多模型 BYOK** — DeepSeek / Qwen / OpenAI / Claude / Gemini / Moonshot / GLM，前端模型切换器即配即用，未配置的厂商给出明确提示而非静默回退
- **多源检索** — DeepSeek Search API（默认）、Tavily、Serper、HackerNews、Reddit、YouTube、Brave、内置中文话题库，支持代理配置
- **视觉与多媒体** — 通义万相生图 + Qwen-VL 视觉分析、图文卡片 DSL 渲染链路、TTS 语音合成与视频素材处理（可选依赖）
- **自动化与集成** — Playwright 浏览器自动化（域名白名单安全边界，对外提供 MCP Server）、小红书 MCP、飞书 Bot / OAuth / 多维表格、微信 iLink Bot
- **运营支撑** — 多日定时发布任务计划、平台账号绑定与作品数据回收、数据看板、用户反馈闭环、LangSmith 全链路追踪（可选）

## 系统架构

<p align="center">
  <img src="./assets/readme/architecture.svg" width="100%" alt="PulseStudio 五层架构：前端层、网关层、编排层、能力层、数据层，热点选题池旁路接入编排层">
</p>

## 快速开始

### 前置要求

- Python 3.10+（推荐使用 [uv](https://docs.astral.sh/uv/) 管理依赖）
- Node.js 18+（推荐 pnpm）
- 一个 DeepSeek API Key 即可启动；其余服务按需配置

### 启动后端

```bash
cd backend
cp .env.example .env        # Windows PowerShell: Copy-Item .env.example .env
# 编辑 .env，至少填写 DEEPSEEK_API_KEY

pip install -e .            # 或 uv sync
python start_server.py      # http://localhost:8000 · API 文档 /docs
```

默认使用 SQLite，启动时自动建表，零配置即可运行；生产环境可切换 MySQL / PostgreSQL（`DATABASE_URL`）。视频与语音转写等能力需要额外安装可选依赖：`pip install -e ".[media]"`。

### 启动前端

```bash
cd frontend
pnpm install                # 或 npm install
pnpm dev                    # http://localhost:5173
```

## 配置速查

完整配置见 [backend/.env.example](backend/.env.example)，核心项：

| 变量 | 说明 | 必需性 |
| --- | --- | --- |
| `DEEPSEEK_API_KEY` | 主对话与子 Agent 默认模型 | 必填 |
| `DASHSCOPE_API_KEY` | Qwen-VL 视觉分析 + 通义万相生图 | 按需 |
| `XHS_MCP_PLUGIN_URL` / `XHS_MCP_LOCAL_CMD` | 小红书 MCP 发布通道 | 按需 |
| `TAVILY_API_KEY` 等内容源 Key | Tavily / Serper / YouTube / Brave / Reddit | 按需 |
| `OPENAI_API_KEY` 等 | 多厂商对话模型（前端切换器） | 按需 |
| `LANGCHAIN_API_KEY` | LangSmith 运行追踪 | 可选 |
| `REDIS_URL` | 分布式缓存（缺省降级为内存缓存） | 可选 |
| `SMTP_*` | 邮箱验证码（缺省开发模式打印到日志） | 可选 |

## 内置智能体

| Agent | 职责 |
| --- | --- |
| `chat_agent` | 主对话入口「小红书运营搭档」，协调所有子 Agent |
| `search` | 爆款搜索：热点、趋势、爆款笔记与任意网页采集 |
| `analyze` | 爆款分析：规律洞察、选题方向、竞品对比 |
| `copywrite` | 小红书文案创作：标题、正文、标签 |
| `image_plan` | 图片规划：卡片配图方案 |
| `image_gen` | 图片生成：AI 生图方案 |
| `image_review` | 图片审核：配图合规检查 |
| `audit` | 合规审核：独立无偏见的内容审核视角 |
| `final_review` | 终审确认：发布前最终把关 |
| `publish` | 发布：对接平台发布适配器 |
| `explore` | 探索研究：搜索 + 分析 + 文案的全链路深度研究 |

另有 10 个运营团队角色（persona + 技能白名单），定义见 [registry.py](backend/app/agents/registry.py)。

## 目录结构

```text
├── frontend/        # Vue 3 + Vite + Pinia + Tailwind 前端
├── backend/
│   ├── app/         # FastAPI 应用：agents / pool_monitor / 路由与服务
│   ├── capabilities/ # 能力层（热点池、能力服务等）
│   ├── alembic/     # 数据库迁移
│   └── start_server.py
├── docs/            # 设计文档与方案
└── assets/readme/   # README 视觉资产
```

## 文档

- [智能体团队设计](docs/agent-team-design.md)
- [内核能力分层架构方案](docs/内核-能力分层架构方案.md)
- [ROADMAP v2 桌面端](docs/ROADMAP_v2_desktop.md)

## License

本项目基于 MIT 协议开源（见 [pyproject.toml](backend/pyproject.toml) 声明）。
