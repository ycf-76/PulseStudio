<p align="center">
  <img src="./assets/readme/hero.svg?v=2" width="760" alt="PulseStudio: AI Content Creation Workbench · Topic Pool Agent Collaboration Multi-Model Access">
</p>

<p align="center">
  <img src="./assets/readme/pulse-badge.png" width="360" alt="PULSE 1.0">
</p>

<p align="center">
  <strong>Your AI content operations partner</strong>
</p>
<p align="center">
  Start from a trending topic, and carry it through planning, creation, review, publishing, and performance review.
</p>

<p align="center">
  <a href="README.md">简体中文</a> | <a href="README.en.md">English</a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10%2B-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/FastAPI-0.110%2B-009688?style=flat-square&logo=fastapi&logoColor=white" alt="FastAPI">
  <img src="https://img.shields.io/badge/Vue-3.x-42B883?style=flat-square&logo=vue.js&logoColor=white" alt="Vue 3">
  <img src="https://img.shields.io/badge/Pinia-4-F9D807?style=flat-square&logo=pinia&logoColor=black" alt="Pinia 4">
  <img src="https://img.shields.io/badge/Tailwind%20CSS-4-38BDF8?style=flat-square&logo=tailwindcss&logoColor=white" alt="Tailwind CSS 4">
  <a href="backend/app/agents/registry.py"><img src="https://img.shields.io/badge/Agents-12-0F9D8A?style=flat-square" alt="12 Agents"></a>
  <img src="https://img.shields.io/badge/Models-7%20Providers-F6C344?style=flat-square" alt="7 Model Providers">
  <a href="#-license"><img src="https://img.shields.io/badge/License-Apache_2.0-2EA44F?style=flat-square" alt="License: Apache 2.0"></a>
  <a href="https://github.com/ycf-76?tab=packages"><img src="https://img.shields.io/badge/Docker-GHCR-2496ED?style=flat-square&logo=docker&logoColor=white" alt="Docker"></a>
  <img src="https://img.shields.io/badge/Scrapling-1F6E5C?style=flat-square" alt="Scrapling">
  <img src="https://img.shields.io/badge/Playwright-2EAD33?style=flat-square&logo=playwright&logoColor=white" alt="Playwright">
  <img src="https://img.shields.io/badge/SQLAlchemy%202-C63C3C?style=flat-square&logo=sqlalchemy&logoColor=white" alt="SQLAlchemy 2">
  <img src="https://img.shields.io/badge/LangChain-1C3C3C?style=flat-square&logo=langchain&logoColor=white" alt="LangChain">
  <img src="https://img.shields.io/badge/Redis-DC382D?style=flat-square&logo=redis&logoColor=white" alt="Redis">
  <img src="https://img.shields.io/badge/APScheduler-6C7A89?style=flat-square" alt="APScheduler">
  <img src="https://img.shields.io/badge/DashScope-615CED?style=flat-square" alt="DashScope">
</p>

<p align="center">
  <a href="backend/app/agents">Agent Orchestration</a> &nbsp;·&nbsp;
  <a href="backend/app/pool_monitor">Topic Pool</a> &nbsp;·&nbsp;
  <a href="backend/app/skills">Skills Library</a> &nbsp;·&nbsp;
  <a href="backend/app/sandbox">Execution Sandbox</a> &nbsp;·&nbsp;
  <a href="frontend/src">Web Workbench</a> &nbsp;·&nbsp;
  <a href="docs">Design Docs</a>
</p>

<p align="center">
  <img src="assets/readme/poster.jpg" width="100%" alt="PulseStudio: From a sentence to a creation">
</p>

## 🎨 What is PulseStudio

PulseStudio is an open-source AI operations workbench for content creators. It connects a primary conversational agent, 12 specialized sub-agents, a live trending-topic pool, and real publishing tools — so the agent doesn't just tell you *what to do*, but actually produces the content, reviews it, publishes it, and feeds performance data back into the next round of creation.

Think of it as an operations partner that remembers your account: it knows which trends you're chasing, what you've published, and which formats perform best. It stays with you from trend discovery all the way to post-publish review, instead of starting from zero every time.

**PulseStudio demo video**:

<!-- ▼ Video placeholder: GitHub READMEs cannot play mp4 files stored in the repo directly.
     Drag your demo video into any GitHub Issue/PR input box to upload it, then replace the URL below with the user-images.githubusercontent.com link and uncomment:
<p align="center">
  <video src="https://user-images.githubusercontent.com/xxxx/pulsestudio-demo.mp4" controls muted width="100%"></video>
</p>
-->

> 📷 Demo video placeholder — waiting for assets.

## 📌 Usage Notes

> - **The web frontend is the recommended way to use PulseStudio**: it provides the full experience — conversations, trending-topic pool, analytics dashboard, and publishing management. The backend also exposes REST APIs and an MCP Server for standalone integration.
> - **Be cautious about fully automated publishing to Xiaohongshu (RED)**: platforms may detect automation, risking captchas, rate limits, or account flags. PulseStudio enables a **human confirmation gate** (`final_review`) before publishing by default — we recommend keeping it on and confirming each publish yourself.
> - **Bring your own keys (BYOK)**: a single DeepSeek API key unlocks the entire core pipeline; other providers are configured on demand, and missing providers fail with clear messages instead of silent errors.

## ✨ Why PulseStudio

- **One primary agent across the whole pipeline**: trend search, topic selection, copywriting, visuals, compliance review, publishing — all in a single conversation, no juggling 5+ tools.
- **Topic-pool driven, not one-shot generation**: continuous multi-platform trend monitoring with SimHash deduplication and LLM value scoring. Topics are managed like inventory with water-level control, so good ideas don't evaporate after one chat.
- **Reviews are real gates, not formalities**: `audit` (independent, unbiased perspective) plus `final_review` (last stop before publishing), with a dedicated compliance check for visuals — risky content never reaches the publishing stage.
- **One asset, many formats**: the same topic can produce photo-text cards, AI-generated visuals, and talking-head video scripts, adapted per platform.
- **Multi-model BYOK, cost under control**: DeepSeek / Qwen / OpenAI / Claude / Gemini / Moonshot / GLM switchable in the frontend; default keys and per-agent overrides supported.
- **Engineering-grade orchestration, explainable failures**: skill whitelists control capability boundaries; automatic retries, circuit breakers, degradation, and model failover; optional LangSmith end-to-end tracing.
- **OS-level sandbox as a safety net**: native Windows sandbox (Low integrity token + mandatory integrity labels, aligned with Codex's unelevated mode) — entire disk read-only by default, the only writable area is the Low-label workspace, secret files unreadable to sandboxed processes, and a Job Object caps resources and reclaims the whole process tree.

## 🧭 The full content operations loop

1. **Discover**: aggregate multi-platform trends, deduplicate with SimHash, classify and score with LLMs, and settle them into the trending-topic pool.
2. **Plan**: analyze viral patterns and competitors, turning trend opportunities into angles and content plans.
3. **Create**: generate Xiaohongshu copy (title / body / hashtags), photo-text card visuals, and AI-generated images.
4. **Review**: `image_review` checks visuals, `audit` checks compliance, `final_review` is the final gate — three levels.
5. **Publish & review**: adapt and publish per platform (after human confirmation), collect performance data into the dashboard, and distill what works back into the next round of topic selection.

## 🧰 What's already built

| Capability | Implemented features |
| --- | --- |
| **Discovery & planning** | Multi-platform trend monitoring (Scrapling stealth crawlers: Xiaohongshu / Douyin / Bilibili), SimHash dedup, LLM value scoring, topic inventory management, viral-note search & collection, viral-pattern analysis, competitor comparison, topic direction generation |
| **Text & visuals** | Xiaohongshu copy (title / body / hashtags), photo-text card DSL rendering pipeline, Tongyi Wanxiang image generation, Qwen-VL visual analysis, visual compliance checks |
| **Publishing & integrations** | Publishing adapters with confirmation gates, Xiaohongshu MCP channel, Playwright browser automation (domain whitelist), Feishu Bot / OAuth / Bitable, WeChat iLink Bot, external MCP Server |
| **Data & operations** | Platform account binding, post-performance collection, analytics dashboard, multi-day scheduled publishing plans, user feedback loop |
| **Engineering foundation** | 12 built-in agents + 10 ops-team roles, skill whitelists, retry / circuit-breaking / degradation / model failover, LangSmith tracing, zero-config SQLite startup (MySQL / PostgreSQL optional), native Windows execution sandbox (Low integrity + integrity labels + Job Object) |

## 🖥️ Workbench highlights

Four representative screens from the workbench — see the skills library and docs for more.

<table>
  <tr>
    <td width="50%" valign="top">
      <strong>🔥 Trending Topic Pool</strong>

      <sub>Multi-platform trend aggregation, deduplication, and value scoring, with water-level inventory management and a REST API for manual control.</sub>

      <img src="assets/readme/features/pool.png" width="100%" alt="Trending Topic Pool">
    </td>
    <td width="50%" valign="top">
      <strong>🎬 Collection & Creation</strong>

      <sub>Collect viral notes, parse their copy into cards, and preview the rendered result in real time.</sub>

      <img src="assets/readme/features/collect.png" width="100%" alt="Collection & Creation workbench">
    </td>
  </tr>
  <tr>
    <td width="50%" valign="top">
      <strong>💬 Primary Conversational Agent</strong>

      <sub>Topic selection, creation, review, and publishing in one conversation, with deep-thinking traces and sub-agent execution visible end to end.</sub>

      <img src="assets/readme/features/chat.png" width="100%" alt="Primary conversational agent">
    </td>
    <td width="50%" valign="top">
      <strong>🎞️ AI Talking-Head Editing</strong>

      <sub>Smart editing for talking-head videos: overlays and effects layered on the original full-frame footage, with direct export.</sub>

      <img src="assets/readme/features/clip.png" width="100%" alt="AI talking-head editing">
    </td>
  </tr>
  <tr>
    <td width="50%" valign="top">
      <strong>📚 My Library</strong>

      <sub>Unified management of 133+ works — photo-text, video, and audio — with one-click archiving of generated and collected content.</sub>

      <img src="assets/readme/features/library.png" width="100%" alt="My Library">
    </td>
    <td width="50%" valign="top">
      <strong>🚦 Publishing Management</strong>

      <sub>Six publishing channels, three review gates plus human confirmation — the last checkpoint before anything goes live.</sub>

      <img src="assets/readme/features/publish.png" width="100%" alt="Publishing management">
    </td>
  </tr>
</table>

> 📷 Analytics dashboard screenshot to be added.

## 🖥️ From the platform

These examples come from PulseStudio's real workflows.

<p align="center">
  <img src="assets/show/作者画像.png" width="49%" alt="Author profile workbench">
  &nbsp;
  <img src="assets/show/chat_页面.png" width="49%" alt="Primary conversational agent workbench">
</p>
<p align="center">
  <img src="assets/show/我的空间.png" width="49%" alt="My Library">
  &nbsp;
  <img src="assets/show/选题池.png" width="49%" alt="Trending Topic Pool">
</p>

## 🖼️ Real outputs

<!-- ▼ Video placeholder: drag the demo video into any GitHub Issue/PR input box to upload, replace the link and uncomment:

Video (click cover to play, lightweight 1-minute preview):
<p align="center">
  <a href="user-attachments link"><img src="assets/readme/features/clip.png" width="49%" alt="Play demo video preview"></a>
</p>
-->

<p align="center">
  <img src="assets/readme/showcase/real-1.jpg" width="31%" alt="Photo-text card: affect vocabulary card (illustrated style)">
  &nbsp;
  <img src="assets/readme/showcase/real-2.jpg" width="31%" alt="Photo-text card: /rewind one-click rollback (marker handwriting style)">
  &nbsp;
  <img src="assets/readme/showcase/real-3.jpg" width="31%" alt="Photo-text card: brand headline layout">
</p>
<p align="center">
  <img src="assets/readme/showcase/real-4.jpg" width="31%" alt="Photo-text card: weekly outfit checklist (colorful tag layout)">
  &nbsp;
  <img src="assets/readme/showcase/real-5.jpg" width="31%" alt="Photo-text card: Codex tutorial (dark tech style)">
  &nbsp;
  <img src="assets/readme/showcase/real-6.jpg" width="31%" alt="Photo-text card: dessert shop review (photography style)">
</p>
<p align="center"><sub>Photo-text cards · real generation output — different topics × different layouts</sub></p>

<p align="center"><sub>🎨 More generated examples: <a href="assets/showcase-local">illustration series</a> (Ghibli-style railways & sunflowers ×4 · underwater world ×2)</sub></p>

> 📷 Demo video to be uploaded (drag into a GitHub Issue, grab the link, then enable the commented block above).

## 🚀 Quick Start

Requirements: Windows 10/11, macOS, or Linux; Python 3.10+ ([uv](https://docs.astral.sh/uv/) recommended); Node.js 18+ (pnpm recommended). Defaults to SQLite with auto-migration on startup — zero configuration.

### Option 1: One-command Docker start (recommended)

```bash
git clone https://github.com/ycf-76/PulseStudio.git
cd PulseStudio

docker compose up -d          # first run builds images, afterwards it starts instantly
# Open http://localhost:8080 · API docs at http://localhost:8080/docs
# Data persists in ./docker-data/ (db / uploads / outputs)
# Model keys go in backend.environment of docker-compose.yml
```

### Option 2: Local development

```bash
git clone https://github.com/ycf-76/PulseStudio.git
cd PulseStudio

# Start the backend
cd backend
cp .env.example .env          # Windows PowerShell: Copy-Item .env.example .env
# Edit .env — at minimum set DEEPSEEK_API_KEY
pip install -e .              # or uv sync
python start_server.py        # http://localhost:8000 · API docs at /docs

# Start the frontend (new terminal)
cd frontend
pnpm install                  # or npm install
pnpm dev                      # http://localhost:5173
```

Video and speech-transcription capabilities need optional dependencies: `pip install -e ".[media]"`. For production, switch to MySQL / PostgreSQL via `DATABASE_URL`.

### Configuration reference

Full configuration in [backend/.env.example](backend/.env.example). Core entries:

| Variable | Purpose | Required |
| --- | --- | --- |
| `DEEPSEEK_API_KEY` | Default model for the primary agent and sub-agents | Required |
| `DASHSCOPE_API_KEY` | Qwen-VL visual analysis + Tongyi Wanxiang image generation | On demand |
| `XHS_MCP_PLUGIN_URL` / `XHS_MCP_LOCAL_CMD` | Xiaohongshu MCP publishing channel | On demand |
| `TAVILY_API_KEY` and other content sources | Tavily / Serper / YouTube / Brave / Reddit | On demand |
| `OPENAI_API_KEY` etc. | Multi-provider chat models (frontend switcher) | On demand |
| `LANGCHAIN_API_KEY` | LangSmith tracing | Optional |
| `REDIS_URL` | Distributed cache (falls back to in-memory) | Optional |
| `SMTP_*` | Email verification codes (dev mode prints to logs) | Optional |

## 🤖 Built-in agents

| Agent | Responsibility |
| --- | --- |
| `chat_agent` | Primary conversational entry point — the "content operations partner" that coordinates all sub-agents |
| `search` | Viral search: trends, hot topics, viral notes, and general web collection |
| `analyze` | Viral analysis: pattern insights, topic directions, competitor comparison |
| `copywrite` | Copywriting: titles, body text, hashtags |
| `image_plan` | Image planning: card visual schemes |
| `image_gen` | Image generation: AI image schemes |
| `image_review` | Image review: visual compliance checks |
| `audit` | Compliance audit: independent, unbiased content review |
| `final_review` | Final review: last gate before publishing (human confirmation gate) |
| `publish` | Publishing: platform publishing adapters |
| `explore` | Deep research: the full search → analysis → copywriting pipeline |

Plus 10 ops-team roles (persona + skill whitelist), defined in [registry.py](backend/app/agents/registry.py).

## 🏗️ Architecture

<p align="center">
  <img src="./assets/readme/architecture.svg" width="100%" alt="PulseStudio five-layer architecture: frontend, gateway, orchestration, capabilities, data — with the trending-topic pool feeding the orchestration layer">
</p>

## 🗂️ Project structure

```text
├── frontend/         # Vue 3 + Vite + Pinia + Tailwind web workbench
├── backend/
│   ├── app/          # FastAPI application: agents / pool_monitor / routers & services
│   ├── capabilities/ # Capability layer (topic pool, capability services)
│   ├── skills/       # Agent-executable skills
│   ├── alembic/      # Database migrations
│   └── start_server.py
├── docs/             # Design documents (Chinese)
└── assets/readme/    # README visual assets
```

## 📖 Documentation

- [Agent team design](docs/agent-team-design.md)
- [Kernel-capability layered architecture](docs/内核-能力分层架构方案.md) (Chinese)
- [ROADMAP v2 desktop](docs/ROADMAP_v2_desktop.md) (Chinese)

## 🙌 Acknowledgements

The agent architecture and product design of PulseStudio are originally developed by this team. The Skills organization of [ZJU-REAL/Easel](https://github.com/ZJU-REAL/Easel) (Apache 2.0) served as a reference — thanks to the ZJU REAL Lab and PKU OpenDCAI Lab for their open-source work.

## 📄 License

Licensed under the [Apache License 2.0](LICENSE).
