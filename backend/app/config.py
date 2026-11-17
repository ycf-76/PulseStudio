"""应用配置：使用 pydantic-settings 读取环境变量。

所有敏感信息（API Key、数据库连接、加密密钥等）均通过环境变量注入，
严禁硬编码。详见 .env.example。
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """全局配置，从环境变量 / .env 读取。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        populate_by_name=True,
    )

    # ===== 应用 =====
    app_name: str = "multi-agent-xhs-platform"

    # ===== 能力服务（三期外置）=====
    # grid 检测/拆分服务地址；空 = 进程内直调（默认），非空 = 走 capability-service
    grid_service_url: str = ""
    # 五期-A：browser-service 地址；空 = 进程内 Playwright（默认），非空 = 走 browser-service
    browser_service_url: str = ""
    # 五期-D：media-service 地址；空 = 进程内直调（默认），非空 = 走 capability-service
    media_service_url: str = ""
    # 五期-E：spider-service 地址；空 = 进程内直调（默认），非空 = 走 capability-service
    spider_service_url: str = ""
    # Spider 同步子进程等待超时（秒）。
    # 同步逐条笔记都要拉详情页（图集 / 视频直链），笔记多时耗时远超旧的 300s。
    # 子进程结果逐条实时入库，超时只截断尾部（已采集数据不丢），
    # 因此默认放宽到 1800s，让"全量刷新过期图文"能在一轮内跑完。
    spider_sync_timeout: float = Field(
        default=1800.0,
        description="Spider 同步子进程等待超时（秒），超时即终止子进程并保留已入库数据",
    )
    # Spider 单轮详情页抓取预算（条）。详情页要开一次反爬浏览器页面并做图集本地化，
    # 单条 10~25s；不设上限时一轮必然跑满超时被强杀。留出预算后，
    # 过期图集分多轮渐进刷新，单轮稳定在超时线以内。<=0 表示不限制（仅手工修复用）。
    spider_detail_budget: int = Field(
        default=12,
        description="单轮同步最多拉取的详情页条数，<=0 表示不限制",
    )
    # 同一笔记详情页重试冷却（小时）。图集仍是外链的笔记会反复进入候选，
    # 冷却避免每轮都全量重抓。
    spider_detail_cooldown_hours: float = Field(
        default=12.0,
        description="同一笔记详情页两次抓取之间的最小间隔（小时）",
    )
    # 同一笔记详情页最多尝试次数。超过后不再自动重试（避免永久重抓），
    # 需 force 全量刷新或手工修复才会再次抓取。
    spider_detail_max_attempts: int = Field(
        default=3,
        description="同一笔记详情页自动重试次数上限",
    )
    # 四期：技能热加载 watcher（默认关，开启后 skills 目录变更免重启生效）
    skill_hot_reload: bool = False
    # 平台自动化总开关（发文 / 互动）。默认关闭：
    # /publish/submit 与 /interact 一律拒绝，避免无人值守批量操作触发平台风控。
    # 需要人工发文时显式置 true，用完立即改回 false。
    automation_enabled: bool = Field(
        default=False,
        description="平台自动化（自动发文/互动）总开关，默认关闭",
    )
    debug: bool = False
    environment: Literal["dev", "test", "prod"] = "dev"
    log_level: str = Field(
        default="INFO",
        alias="LOG_LEVEL",
        description="应用日志级别：DEBUG / INFO / WARNING / ERROR",
    )

    # ===== 数据库 =====
    database_url: str = Field(
        default="sqlite+aiosqlite:///./data/xhs_agent.db",
        description="异步数据库连接串（dev 默认 SQLite 零依赖；生产换 mysql+aiomysql:// 或 postgresql+asyncpg://）",
    )
    db_pool_size: int = Field(default=20, description="数据库连接池大小")
    db_max_overflow: int = Field(default=40, description="连接池最大溢出")
    db_echo: bool = False

    # ===== LLM: DeepSeek =====
    deepseek_api_key: str = Field(default="", description="DeepSeek API Key")
    deepseek_base_url: str = "https://api.deepseek.com"
    # LLM 请求超时秒数。原 120s × 多层重试导致一次调用最坏 8 分钟静默
    # （2026-11-07「帮我分析视频 3 分钟无回复」根因），降到 60s 让失败更快暴露，
    # 重试上收到 reactor 层（可见、可推送 SSE 进度）。
    deepseek_timeout: float = Field(default=60.0, description="DeepSeek LLM 请求超时秒数")
    deepseek_model_v3: str = "deepseek-v4-flash"
    deepseek_model_r1: str = "deepseek-reasoner"

    # ===== DeepSeek 原生联网搜索（Anthropic 兼容 Messages 端点）=====
    # 与 deepseek_base_url 是两套端点：chat-completions 走 /v1，服务端 web_search 工具走 /anthropic/v1
    # 复用 deepseek_api_key，不需要任何第三方搜索 API Key
    deepseek_search_base_url: str = "https://api.deepseek.com/anthropic/v1"
    deepseek_search_model: str = "deepseek-v4-flash"
    deepseek_search_max_uses: int = Field(
        default=1, description="单次请求允许触发的服务端搜索次数（越多越贵）"
    )
    deepseek_search_max_tokens: int = Field(
        default=1024, description="单次搜索请求的输出 token 上限"
    )
    deepseek_search_cache_ttl: int = Field(
        default=300,
        description=(
            "DeepSeek 搜索结果的进程内缓存秒数（同一关键词在 TTL 内复用，0=禁用）。"
            "每轮搜索都是一次完整模型回合（按 token 计费），定时监控与用户重复搜索复用可显著降本"
        ),
    )

    # ===== DeepSeek Search API（原生检索接口，不走模型回合，零 token）=====
    # base_url 含 /v1/tools 前缀，代码内追加 /search 与 /fetch（与 Messages 版是两套端点）
    deepseek_search_api_base_url: str = "https://api.deepseek.com/v1/tools"
    deepseek_search_api_count: int = Field(
        default=20,
        description="单次检索请求的结果条数（1-50，超过上限按 50 截断；实际取 min(该值, limit)）",
    )
    deepseek_search_api_cache_ttl: int = Field(
        default=600,
        description=(
            "Search API 结果的进程内缓存秒数（同一 query+count 在 TTL 内直接复用，0=禁用）。"
            "检索本身零 token，缓存只为省掉重复网络往返"
        ),
    )
    deepseek_search_api_fetch_enabled: bool = Field(
        default=True,
        description=(
            "是否用 Fetch API 补全正文：检索结果的 summary 短于阈值时，抓取页面 Markdown 覆盖正文"
        ),
    )
    deepseek_search_api_fetch_min_chars: int = Field(
        default=200,
        description="正文短于该字符数时触发 Fetch 补全（0=所有条目都补全）",
    )
    deepseek_search_api_fetch_max_urls: int = Field(
        default=5,
        description="单次检索最多补全几条（按服务端 rerank 顺序取前 N 条，0=不补全）",
    )
    deepseek_search_api_fetch_max_chars: int = Field(
        default=4000,
        description="单条 Fetch 结果写入正文的最大字符数（截断，防止下游 token 爆炸）",
    )

    # ===== 阿里云百炼 (Qwen-VL + 通义万相) =====
    dashscope_api_key: str = Field(default="", description="阿里云百炼 API Key")
    qwen_vl_model: str = "qwen-vl-max"
    # 通义万相文生图模型：
    # - wanx2.1-t2i-turbo：性价比最高，0.14元/张（推荐，新用户有500张免费额度）
    # - wanx2.1-t2i-plus：高质量版，0.20元/张
    # - wanx-v1：旧版，0.16元/张
    # - wan2.2-t2i-flash：2.2极速版，速度提升50%
    wanx_model: str = "wanx2.1-t2i-turbo"
    jimeng_api_key: str = Field(default="", description="即梦 API Key（备用生图）")

    # ===== 视频生成（通义万相 / 可灵 / 火山方舟）=====
    # 默认 provider：dashscope（与万相图片同生态，复用 DASHSCOPE_API_KEY）
    video_provider: str = Field(
        default="dashscope",
        description="默认视频生成 provider（dashscope / kling / ark）",
    )
    dashscope_video_model: str = Field(
        default="wanx2.1-t2v-turbo", description="DashScope 文生视频模型（t2v）"
    )
    dashscope_i2v_model: str = Field(
        default="wanx2.1-i2v-turbo", description="DashScope 图生视频模型（i2v）"
    )
    kling_access_key: str = Field(
        default="", description="可灵 Access Key（video_provider=kling 时用，JWT 鉴权）"
    )
    kling_secret_key: str = Field(
        default="", description="可灵 Secret Key（video_provider=kling 时用，JWT 鉴权）"
    )
    kling_model: str = Field(default="kling-v1", description="可灵模型名（kling-v1 / kling-v1-5）")
    ark_api_key: str = Field(
        default="", description="火山引擎 ARK API Key（video_provider=ark 时用）"
    )
    ark_model: str = Field(
        default="", description="火山方舟视频生成模型 / endpoint id（video_provider=ark 时必填）"
    )

    # ===== 视频生成并发控制 =====
    dashscope_max_concurrent: int = Field(default=2, description="DashScope 视频生成最大并发")
    kling_max_concurrent: int = Field(default=2, description="可灵视频生成最大并发")
    ark_max_concurrent: int = Field(default=3, description="火山方舟视频生成最大并发")
    video_global_max_concurrent: int = Field(default=6, description="视频生成全局最大并发")

    # ===== 离线 TTS（piper）=====
    # PATH 里的 piper 缺 espeak-ng-data 不可用，必须用 om-runtime 自带依赖的完整版
    piper_binary: str = Field(
        default=r"D:\om-runtime\piper\piper.exe", description="piper 可执行路径（离线配音降级）"
    )
    piper_model_path: str = Field(
        default=r"D:\om-runtime\model.onnx", description="piper 语音模型 onnx 路径（cmn 中文）"
    )

    # ===== 多图视频流水线 =====
    multi_image_video_total_timeout: float = Field(
        default=600.0,
        description=(
            "多图视频流水线：逐镜生成视频的总体超时（秒）。串行循环在镜头边界检查，"
            "超时后剩余镜头全部退化为 ken-burns 兜底出片，避免无限卡住。"
            "注意单镜内部轮询上限另有 300s，故最坏耗时约为本值 + 300s。设为 0 表示不限制。"
        ),
    )
    multi_image_video_max_concurrent_frames: int = Field(
        default=2, description="多图视频单镜内并发帧生成上限"
    )

    # ===== AI 视频工坊（任务层 + Mock Provider）=====
    # 设计依据：docs/VIDEO_ARCHITECTURE_ALIGNMENT_MIGRATION_PLAN.md §6（阶段 0 零模型成本）
    video_provider_mode: str = Field(
        default="mock",
        description=(
            "AI 视频工坊 Provider 模式：mock=阶段0零成本占位（默认）；"
            "real=阶段1起按 video_provider 走真实适配器"
        ),
    )
    video_mock_failure_rate: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Mock Provider 失败率（联调重试/降级路径时调高，如 0.3）",
    )
    video_mock_latency_sec: float = Field(
        default=2.0,
        ge=0.0,
        description="Mock Provider 单任务模拟耗时（秒），分镜图/视频同参",
    )
    video_worker_poll_idle: float = Field(
        default=1.5,
        description="视频 Worker 空闲轮询间隔（秒）",
    )
    video_worker_poll_busy: float = Field(
        default=0.3,
        description="视频 Worker 有任务时的轮询间隔（秒）",
    )
    video_job_exec_timeout: float = Field(
        default=900.0,
        description="单任务执行超时（秒）。视频生成最坏约 total_timeout+轮询300s，900 覆盖",
    )

    # ===== 模型分级调度（TieredModelRouter）=====
    # 规划阶段用便宜模型，生成阶段用强模型，实现算力均衡分配
    # 注意：VL 模型名必须与 get_tier_router 的 getattr 兜底保持一致。
    # 2026-10-27 真机探活：当前百炼账号 qwen-vl-plus/qwen-vl-max*/
    # qwen2.5-vl-* 全部 403/404，唯一可用 VL = qwen3-vl-plus
    # （原生 multimodal-generation 与 compatible-mode 双端点验证通过）
    # economy 级：规划阶段（VL理解 + LLM编排），省钱优先
    tier_economy_vl: str = Field(
        default="qwen3-vl-plus", description="economy 级 VL 模型（便宜，用于规划）"
    )
    tier_economy_llm: str = Field(
        default="deepseek-chat", description="economy 级 LLM 模型（便宜，用于编排）"
    )
    tier_economy_t2i: str = Field(default="wanx2.1-t2i-turbo", description="economy 级文生图模型")
    tier_economy_i2v: str = Field(default="wanx2.1-i2v-turbo", description="economy 级图生视频模型")
    # standard 级：后处理阶段，性价比平衡
    tier_standard_vl: str = Field(default="qwen3-vl-plus", description="standard 级 VL 模型")
    tier_standard_llm: str = Field(default="deepseek-chat", description="standard 级 LLM 模型")
    tier_standard_t2i: str = Field(default="wanx2.1-t2i-turbo", description="standard 级文生图模型")
    tier_standard_i2v: str = Field(
        default="wanx2.1-i2v-turbo", description="standard 级图生视频模型"
    )
    # quality 级：核心生成阶段（生图 + 生视频），质量优先
    tier_quality_vl: str = Field(
        default="qwen3-vl-plus", description="quality 级 VL 模型（最强，用于关键生成）"
    )
    tier_quality_llm: str = Field(default="deepseek-chat", description="quality 级 LLM 模型")
    tier_quality_t2i: str = Field(
        default="wanx2.1-t2i-plus", description="quality 级文生图模型（高质量版）"
    )
    tier_quality_i2v: str = Field(
        default="wanx2.1-i2v-plus", description="quality 级图生视频模型（高质量版）"
    )

    # ===== LLM: OpenAI（GPT 系列，官方 API）=====
    openai_api_key: str = Field(default="", description="OpenAI API Key")
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o"

    # ===== LLM: Anthropic（Claude 系列，官方 API）=====
    # 需要 pip install anthropic；未安装时前端选择 Claude 会给出明确提示
    anthropic_api_key: str = Field(default="", description="Anthropic API Key（Claude）")
    anthropic_base_url: str = "https://api.anthropic.com"
    anthropic_model: str = "claude-3-5-sonnet-latest"

    # ===== LLM: Google Gemini（OpenAI 兼容端点）=====
    google_api_key: str = Field(default="", description="Google AI / Gemini API Key")
    google_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai/"
    google_model: str = "gemini-1.5-flash"

    # ===== LLM: Moonshot（Kimi，OpenAI 兼容端点）=====
    moonshot_api_key: str = Field(default="", description="Moonshot / Kimi API Key")
    moonshot_base_url: str = "https://api.moonshot.cn/v1"
    moonshot_model: str = "moonshot-v1-8k"

    # ===== LLM: 智谱 GLM（OpenAI 兼容端点）=====
    zhipu_api_key: str = Field(default="", description="智谱 GLM API Key")
    zhipu_base_url: str = "https://open.bigmodel.cn/api/paas/v4"
    zhipu_model: str = "glm-4-plus"

    # ===== LLM: 通义千问文本（DashScope OpenAI 兼容端点，复用百炼 Key）=====
    qwen_text_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    qwen_text_model: str = "qwen-max"

    # ===== 多平台内容源（ContentSource）=====
    # 数据源优先级：builtin（内置中文话题库，零网络依赖）> Reddit/HackerNews（官方 API，零风控）> 小红书（爬取，有风控风险）
    # 默认平台：决定工作流 search 节点从哪个平台拿数据
    default_source_platform: str = Field(
        default="builtin",
        description="默认数据源平台：deepseek_api / deepseek / tavily / sogou / zhihu / weibo / bilibili / douyin / pinterest / instagram / reddit / hackernews / xiaohongshu",
    )
    hotboard_enabled: bool = Field(
        default=True,
        description="是否启用可插拔热榜源（读 hotboard_sources.json，调 60s/xxapi 公益 API 拿实时热搜）",
    )
    http_proxy: str = Field(
        default="",
        alias="HTTP_PROXY",
        description=(
            "HTTP 代理地址（如 http://127.0.0.1:7897），供 httpx 访问国外 API（Reddit/HN/GitHub/Tavily/Serper）。"
            "为空不走代理。设为 'system' 时自动探测系统代理（HTTP_PROXY/HTTPS_PROXY 环境变量）"
        ),
    )

    # ===== Reddit API（OAuth2 personal use script）=====
    # 申请地址：https://www.reddit.com/prefs/apps（选 script 类型）
    reddit_client_id: str = Field(default="", description="Reddit app client_id")
    reddit_client_secret: str = Field(default="", description="Reddit app client_secret")
    reddit_username: str = Field(
        default="",
        description="Reddit 用户名（script app 模式必需，用于搜索）",
    )
    reddit_password: str = Field(
        default="",
        description="Reddit 密码（script app 模式必需）",
    )

    # ===== Tavily API（全网搜索，专为 AI Agent 设计）=====
    # 申请地址：https://app.tavily.com/（新用户有免费额度）
    # 配置后搜索节点会用 Tavily 做全网搜索（Google/Bing 级覆盖面）
    tavily_api_key: str = Field(default="", description="Tavily API Key")

    # ===== Serper API（Google SERP 数据，低价高质）=====
    # 申请地址：https://serper.dev/（2,500 次免费，无需信用卡）
    # 返回 Google 原始搜索结果（organic/knowledge graph/people-also-ask）
    # 用途：Tavily 额度用完后的降级备选 + 小红书全品类内容搜索
    serper_api_key: str = Field(default="", description="Serper API Key")

    # ===== Brave Search API（独立索引，差异化搜索）=====
    # 申请地址：https://brave.com/search/api/（2,000 次/月免费）
    # 独立爬虫索引，不依赖 Google，结果有差异化
    # 用途：交叉验证，覆盖 Google 未收录的小红书页面
    brave_api_key: str = Field(default="", description="Brave Search API Key")

    # ===== YouTube Data API v3 =====
    # 申请地址：https://console.cloud.google.com/apis/credentials（创建 API Key，启用 YouTube Data API v3）
    # 免费配额：10,000 单位/天（搜索一次 ~100 单位，约 100 次搜索/天）
    youtube_api_key: str = Field(default="", description="YouTube Data API v3 Key（可选）")

    # ===== GitHub API（开源项目/技术工具热点）=====
    # 无 token 时限速 60次/小时（监控10分钟一次足够）；配 token 提升到 5000次/小时
    # 申请地址：https://github.com/settings/tokens（选 classic token，无需任何 scope）
    github_token: str = Field(default="", description="GitHub Personal Access Token（可选）")

    # ===== LangChain tracing =====
    langchain_tracing_v2: bool = Field(default=False, description="是否启用 LangChain v2 tracing")
    langchain_api_key: str = Field(default="", description="LangSmith API Key（tracing 用）")
    langchain_project: str = Field(default="xhs-agent", description="LangSmith 项目名")
    langchain_endpoint: str = Field(
        default="https://api.smith.langchain.com", description="LangSmith API endpoint"
    )

    # ===== 加密 =====
    aes_secret_key: str = Field(
        default="",
        description="AES-GCM 加密密钥（32 字节 base64），用于加密小红书 Token/Session",
    )

    # ===== BYOK 安全 =====
    byok_allow_private_base_url: bool = Field(
        default=False,
        description="是否允许 BYOK 自定义 base_url 指向内网/保留地址（默认关闭，防 SSRF）",
    )
    byok_save_rate_limit: int = Field(
        default=10,
        description="每用户每厂商每分钟保存凭据的次数上限（<=0 不限）",
    )
    byok_verify_rate_limit: int = Field(
        default=5,
        description="每用户每厂商每分钟验证 Key 的次数上限（<=0 不限）",
    )
    jwt_secret_key: str = Field(default="", description="JWT 签名密钥")
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 30
    jwt_refresh_expire_days: int = 7
    admin_user_ids: str = Field(
        default="",
        description=(
            "管理后台管理员用户 ID 白名单（逗号分隔，如 user_abc,user_def）。"
            "为空时管理后台接口一律拒绝（fail-closed）"
        ),
    )

    # ===== Recovery =====
    recovery_max_attempts: int = 3
    recovery_circuit_failure_threshold: int = 5
    recovery_circuit_recovery_timeout: int = 60
    structural_recovery_timeout_seconds: int = 1800

    # ===== SSE =====
    sse_heartbeat_seconds: int = 15
    sse_event_buffer_limit: int = 1000

    # ===== SMTP（邮箱验证码发送）=====
    smtp_host: str = Field(
        default="", description="SMTP 服务器地址（如 smtp.qq.com / smtp.gmail.com）"
    )
    smtp_port: int = Field(default=465, description="SMTP 端口（SSL 默认 465，TLS 默认 587）")
    smtp_user: str = Field(default="", description="SMTP 登录用户名（通常就是邮箱地址）")
    smtp_pass: str = Field(default="", description="SMTP 登录密码或授权码")
    smtp_from: str = Field(default="", description="发件人地址（留空则用 smtp_user）")
    smtp_use_tls: bool = Field(
        default=False, description="是否使用 STARTTLS（端口 587 时通常为 True）"
    )

    # ===== Redis =====
    redis_url: str = Field(
        default="redis://127.0.0.1:6379/0",
        description="Redis 连接地址（留空则使用内存降级缓存）",
    )
    redis_password: str = Field(default="", description="Redis 密码（留空表示无密码）")
    redis_db: int = Field(default=0, description="Redis 数据库编号（0-15）")
    redis_pool_size: int = Field(default=20, description="Redis 连接池大小")

    # ===== 飞书集成 =====
    feishu_app_id: str = Field(default="", description="飞书自建应用 App ID")
    feishu_app_secret: str = Field(default="", description="飞书自建应用 App Secret")
    feishu_verification_token: str = Field(default="", description="飞书事件订阅验证令牌")
    feishu_encrypt_key: str = Field(default="", description="飞书事件订阅加密密钥")
    feishu_bitable_app_token: str = Field(default="", description="飞书多维表格 App Token")
    feishu_bitable_table_id: str = Field(default="", description="飞书多维表格 Table ID")
    feishu_oauth_redirect_uri: str = Field(
        default="http://localhost:8000/api/feishu/oauth/callback",
        description="飞书 OAuth 回调地址，必须与开放平台配置完全一致",
    )
    feishu_oauth_frontend_url: str = Field(
        default="http://localhost:5173",
        description="飞书 OAuth 完成后跳转的前端地址",
    )
    feishu_oauth_scopes: str = Field(
        default="offline_access wiki:wiki docx:document",
        description="飞书用户授权 scope，空格分隔",
    )

    # ===== CORS =====
    cors_origins_str: str = Field(
        default="http://localhost:5173,http://localhost:3000,http://localhost:3001,http://127.0.0.1:3001",
        alias="CORS_ORIGINS",
        description="CORS 允许的来源列表（逗号分隔），可通过 CORS_ORIGINS 环境变量覆盖",
    )

    # ===== High-risk permission allowlist =====
    permissions_allow: str = Field(
        default="",
        description="High-risk permissions allowed (comma separated), e.g. bash:exec,file:write",
    )

    # ===== Generic browser automation (G1-G4) =====
    browser_allowed_domains: str = Field(
        default="xiaohongshu.com",
        alias="BROWSER_ALLOWED_DOMAINS",
        description=(
            "通用浏览器自动化域名白名单（逗号分隔，域后缀匹配：填 a.com 即放行 "
            "a.com 与 *.a.com）。运行时可通过 POST /api/browser/domains 临时放行"
        ),
    )
    mcp_browser_token: str = Field(
        default="",
        alias="MCP_BROWSER_TOKEN",
        description="对外 MCP Server（/api/mcp/browser）的 Bearer token；为空则启动时自动生成并打印日志",
    )
    browser_proxy: str = Field(
        default="",
        alias="BROWSER_PROXY",
        description=(
            "worker Chromium 代理（如 http://127.0.0.1:7897）。为空不走代理。"
            "用于直连被 RST/超时的站点（如 GitHub）；设为 'system' 时自动探测"
            "系统代理。重启后端生效"
        ),
    )

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.cors_origins_str.split(",") if o.strip()]

    @property
    def browser_allowed_domain_list(self) -> list[str]:
        return [d.strip().lower() for d in self.browser_allowed_domains.split(",") if d.strip()]

    @property
    def admin_user_id_list(self) -> list[str]:
        return [u.strip() for u in self.admin_user_ids.split(",") if u.strip()]


@lru_cache
def get_settings() -> Settings:
    """单例配置，避免重复读取环境变量。"""
    return Settings()
