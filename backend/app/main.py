"""FastAPI 应用入口。
仅搭建骨架：CORS 中间件 + 路由注册 + lifespan。
具体业务逻辑在后续 Phase 实现。
"""

import asyncio
import logging
import mimetypes
import os
import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

# Windows 注册表把 .svg 映射为 image/svg（非标准 MIME），浏览器会拒绝渲染 <img> 中的 SVG。
# 显式注册标准 MIME，保证 /outputs 下的矢量图产物可被前端直接展示。
mimetypes.add_type("image/svg+xml", ".svg")

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import inspect as _sa_inspect
from sqlalchemy import text as _sa_text
from starlette.middleware.base import BaseHTTPMiddleware

import app.db.models  # noqa: F401 — ensure all ORM models registered with Base.metadata
from app.api.routers import (
    accounts,
    agents,
    assets,
    auth,
    browser,
    cards,
    chat,
    chat_agent,
    chat_file,
    chat_folder,
    chat_session,
    config,
    creative_artifact,
    dashboard,
    feedback,
    feishu_bot,
    feishu_docs,
    feishu_oauth,
    governance,
    internal,
    mcp_bridge,
    memory,
    models,
    my_works,
    plugins,
    profile,
    proxy,
    quality_gate,
    reactor,
    search,
    side_session,
    skills,
    sse,
    topic_pool,
    video_assets,
    workflows,
    workspace,
)
from app.config import get_settings
from app.db.session import Base, engine

settings = get_settings()

from app.logging_conf import setup_logging

setup_logging(app_level=settings.log_level)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """应用生命周期：启动时初始化DB，关闭时释放资源。"""
    _health: list[tuple[str, bool, str]] = []

    def _hstep(name: str, ok: bool, detail: str = "") -> None:
        _health.append((name, ok, detail))

    from app.banner import print_banner

    print_banner()

    # 启动时自动创建表
    logger.debug("Initializing database tables...")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            try:
                cols = await conn.run_sync(
                    lambda sync: _sa_inspect(sync).get_columns("chat_sessions")
                )
                if not any(c["name"] == "folder_id" for c in cols):
                    await conn.execute(
                        _sa_text(
                            "ALTER TABLE chat_sessions ADD COLUMN folder_id VARCHAR(100) NOT NULL DEFAULT ''"
                        )
                    )
                    logger.info("Added chat_sessions.folder_id column")
            except Exception as e2:
                logger.warning(f"chat_sessions.folder_id column check skipped: {e2}")
        logger.info("Database tables ready")
        _hstep("DB tables", True)
    except Exception as e:
        logger.warning(f"Database init skipped: {e}")
        _hstep("DB tables", False, str(e))

    # 初始化 LangSmith 追踪（设置环境变量，LangGraph 运行自动上报）
    if settings.langchain_tracing_v2 and settings.langchain_api_key:
        import os

        os.environ["LANGCHAIN_TRACING_V2"] = "true"
        os.environ["LANGCHAIN_API_KEY"] = settings.langchain_api_key
        os.environ["LANGCHAIN_PROJECT"] = settings.langchain_project
        os.environ["LANGCHAIN_ENDPOINT"] = settings.langchain_endpoint
        # 国内网络优化：增大超时 + 重试，避免 SSL 写超时
        os.environ.setdefault("LANGCHAIN_TIMEOUT", "60")
        # 限制单次上报大小（5MB），避免大 trace 上传失败
        os.environ.setdefault("LANGCHAIN_MAX_PAYLOAD_SIZE", "5242880")
        # 服务密钥只有写入权限，不做读取验证（list_projects/list_sessions 会 403）
        # LangSmith SDK 会在后台异步上报，失败时自动重试
        logger.info(
            f"LangSmith tracing enabled: project={settings.langchain_project}, "
            f"endpoint={settings.langchain_endpoint}"
        )
        _hstep("LangSmith", True)
    else:
        logger.debug(
            "LangSmith tracing disabled (set LANGCHAIN_TRACING_V2=true + LANGCHAIN_API_KEY to enable)"
        )
        _hstep("LangSmith", True, "disabled")

    logger.debug("[lifespan] step 2: skipped (no MCP clients)")
    _hstep("MCP clients", True, "n/a")

    # 初始化多平台内容源（Reddit / HackerNews / 小红书）
    logger.debug("[lifespan] step 3: init content sources...")
    # 预热共享 SSL 上下文：httpx 新建 client 时会在事件循环里同步加载系统证书库，
    # Windows 上单次要 3~11 秒。启动阶段先建好，避免首次搜索被这段开销拖住。
    try:
        from capabilities.content_search.sources.base import get_ssl_context

        await get_ssl_context()
        _hstep("SSL context", True)
    except Exception as e:
        logger.warning(f"SSL context warm-up failed: {e}")
        _hstep("SSL context", False, str(e))

    try:
        from capabilities.content_search.sources.manager import init_sources, source_manager

        await init_sources()
        logger.info(
            f"Content sources initialized: "
            f"platforms={source_manager.list_platforms()}, "
            f"default={source_manager._default_platform}"
        )
        _hstep("Content sources", True)
    except Exception as e:
        logger.warning(f"Content sources init failed: {e}")
        _hstep("Content sources", False, str(e))

    # 选题池智能监控模块（与主服务同进程，不开独立端口）
    # 建表 + 注册 APScheduler 定时任务 + 启动水位检查（池<50 立即抓取）
    # 路由注册在模块顶层（与其他 router 一起），这里只管生命周期
    logger.debug("[lifespan] step 4: init pool monitor...")
    try:
        from capabilities.pool_monitor.main import pool_startup

        # await pool_startup()  # temporarily disabled: Tavily API rate-limited
        logger.debug("Pool monitor module skipped (Tavily rate-limited)")
        _hstep("Pool monitor", True, "disabled")
    except Exception as e:
        logger.warning(f"Pool monitor init failed: {e}")
        _hstep("Pool monitor", False, str(e))

    # 反馈闭环定时任务（T+7 回采 + 权重校准）
    logger.debug("[lifespan] step 4.5: init performance collector scheduler...")
    try:
        from app.services.performance_collector import (
            calibrate_weights,
            collect_performance_7d,
        )
        from capabilities.pool_monitor.scheduler import scheduler

        scheduler.add_job(
            collect_performance_7d,
            "cron",
            hour=3,
            minute=0,
            id="collect_performance_7d",
            replace_existing=True,
        )
        scheduler.add_job(
            calibrate_weights,
            "cron",
            day_of_week="mon",
            hour=4,
            minute=0,
            id="calibrate_weights",
            replace_existing=True,
        )
        logger.info("Performance collector scheduler registered")
        _hstep("Perf collector", True)
    except Exception as e:
        logger.warning(f"Performance collector scheduler init failed: {e}")
        _hstep("Perf collector", False, str(e))

    # 任务清单定时扫描：到期任务执行 + 挂起工作流处理（审核三档/服务端渲染）
    # 四期：技能热加载 watcher（配置默认关；开启后 .md 增删改无需重启）
    logger.debug("[lifespan] step 4.6: init skill hot reload watcher...")
    try:
        from app.config import get_settings
        from app.services.skill_hot_reload import start as start_skill_watcher

        if getattr(get_settings(), "skill_hot_reload", False):
            start_skill_watcher()
            _hstep("Skill hot reload", True, "watching")
        else:
            _hstep("Skill hot reload", True, "disabled")
    except Exception as e:
        logger.warning(f"Skill hot reload init failed: {e}")
        _hstep("Skill hot reload", False, str(e))

    # 任务清单定时扫描：到期任务执行 + 挂起工作流处理（审核三档/服务端渲染）
    logger.debug("[lifespan] step 4.7: init task plan scheduler...")
    try:
        from apscheduler.triggers.interval import IntervalTrigger

        from app.services.task_plan import recover_on_startup, run_task_scan
        from capabilities.pool_monitor.scheduler import scheduler

        # 启动时收敛重启前 in-flight 的任务
        await recover_on_startup()
        scheduler.add_job(
            run_task_scan,
            IntervalTrigger(minutes=1),
            id="task_plan_scan",
            name="任务清单定时扫描",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        logger.info("Task plan scheduler registered (every 1 min)")
        _hstep("Task plan scan", True)
    except Exception as e:
        logger.warning(f"Task plan scheduler init failed: {e}")
        _hstep("Task plan scan", False, str(e))

    # 初始化 Redis 缓存
    logger.debug("[lifespan] step 5: init Redis cache...")
    try:
        from app.cache import redis_client

        await redis_client.connect()
        mode = "Redis" if redis_client.is_connected else "in-memory fallback"
        logger.info(f"Cache initialized ({mode})")
        _hstep("Redis cache", True, mode)
    except Exception as e:
        logger.warning(f"Redis cache init failed: {e}")
        _hstep("Redis cache", False, str(e))

    # Phase 5.6: 同步插件到数据库（扫描 plugins/builtin/ + plugins/third_party/ → upsert DB）
    logger.debug("[lifespan] step 5.6: sync all plugins (builtin + third_party) to DB...")
    try:
        from app.core.sync_builtin_plugins import sync_all_plugins_to_db

        sync_result = await sync_all_plugins_to_db()
        logger.info(
            f"✅ Plugins synced: "
            f"{sync_result['synced']}/{sync_result['scanned']} upserted, "
            f"{sync_result['skipped']} unchanged"
        )
        _hstep("Plugin sync", True, f"{sync_result['synced']}/{sync_result['scanned']}")
    except Exception as e:
        logger.warning(f"Plugins sync failed: {e} (plugin list may be incomplete)")
        _hstep("Plugin sync", False, str(e))

    # 飞书集成：验证配置并初始化适配器
    logger.debug("[lifespan] step 5.8: init Feishu integration...")
    try:
        if settings.feishu_app_id and settings.feishu_app_secret:
            from capabilities.messaging.adapters.feishu import FeishuClient

            _feishu_client = FeishuClient(
                app_id=settings.feishu_app_id,
                app_secret=settings.feishu_app_secret,
            )
            logger.info(
                f"✅ Feishu integration ready: app_id={settings.feishu_app_id[:6]}..., "
                f"bitable={'configured' if settings.feishu_bitable_app_token else 'not configured'}"
            )
            _hstep("Feishu", True)
        else:
            logger.debug(
                "Feishu integration disabled (set FEISHU_APP_ID + FEISHU_APP_SECRET to enable)"
            )
            _hstep("Feishu", True, "disabled")
    except Exception as e:
        logger.warning(f"Feishu integration init failed: {e}")
        _hstep("Feishu", False, str(e))

    logger.debug("[lifespan] step 5.9: auto-restore WeChat bot engines...")
    try:
        from app.rpa.wechat_bot_engine import _CREDENTIALS_DIR, get_wechat_engine

        _wx_cred_path = Path(_CREDENTIALS_DIR)
        _wx_restored = 0
        for _cred_file in _wx_cred_path.glob(".wechat_credentials_*.json"):
            _wx_uid = _cred_file.stem.replace(".wechat_credentials_", "")
            try:
                _wx_eng = get_wechat_engine(_wx_uid)
                await _wx_eng.start()
                if _wx_eng.is_logged_in:
                    _wx_restored += 1
                    logger.info(f"[lifespan] ✅ WeChat engine restored: user={_wx_uid}")
                else:
                    logger.debug(f"[lifespan] WeChat engine not logged in: user={_wx_uid}")
            except Exception as _wx_e:
                logger.warning(f"[lifespan] WeChat engine restore failed for {_wx_uid}: {_wx_e}")
        if _wx_restored > 0:
            logger.info(f"✅ WeChat bot engines restored: {_wx_restored}")
            _hstep("WeChat restore", True, f"{_wx_restored} engines")
        else:
            logger.debug("No WeChat bot engines to restore (normal if not configured)")
            _hstep("WeChat restore", True, "none")
    except Exception as e:
        logger.warning(f"WeChat auto-restore failed: {e}")
        _hstep("WeChat restore", False, str(e))

    # step 6: 预热 LLM adapter + Skill 注册（消除首次请求 3-4s 冷启动）
    try:
        from app.core.skill_registry import SkillRegistry, ensure_builtin_skills_registered

        ensure_builtin_skills_registered()
        SkillRegistry.instance().scan_third_party()
        logger.debug("[lifespan] step 6: Skill registry warmed up")
        _hstep("Skill warmup", True)
    except Exception as _skill_warm_err:
        logger.warning(f"[lifespan] Skill warmup skipped: {_skill_warm_err}")
        _hstep("Skill warmup", False, str(_skill_warm_err))

    # 模型分配缓存预热（智能体 / 技能的模型选择）
    try:
        from app.services.model_assignment import load_all_assignments

        await load_all_assignments()
        _hstep("Model assignment warmup", True)
    except Exception as _assign_err:
        logger.warning(f"[lifespan] Model assignment warmup skipped: {_assign_err}")
        _hstep("Model assignment warmup", False, str(_assign_err))

    try:
        from app.engine.factory import get_deepseek_llm

        _warm_llm = get_deepseek_llm(model="deepseek-chat")
        if _warm_llm is not None:
            logger.debug("[lifespan] step 6: LLM adapter warmed up (deepseek-chat)")
            _hstep("LLM warmup", True, "deepseek-chat")
        else:
            logger.debug(
                "[lifespan] step 6: LLM adapter warmup skipped (no API key or circuit open)"
            )
            _hstep("LLM warmup", True, "skipped")
    except Exception as _llm_warm_err:
        logger.warning(f"[lifespan] LLM warmup skipped: {_llm_warm_err}")
        _hstep("LLM warmup", False, str(_llm_warm_err))

    try:
        from app.engine.factory import get_deepseek_llm

        _warm_r1 = get_deepseek_llm(model="deepseek-reasoner")
        if _warm_r1 is not None:
            logger.debug("[lifespan] step 6: LLM adapter warmed up (deepseek-reasoner)")
            _hstep("LLM R1 warmup", True)
    except Exception as _llm_r1_warm_err:
        logger.warning(f"[lifespan] LLM R1 warmup skipped: {_llm_r1_warm_err}")
        _hstep("LLM R1 warmup", False, str(_llm_r1_warm_err))

    # step 6.5: AI 视频工坊 Worker（异步任务层消费循环 + 启动恢复）
    # 设计依据：docs/VIDEO_ARCHITECTURE_ALIGNMENT_MIGRATION_PLAN.md §4（D1/D6）
    try:
        from app.services.sse_bus import sse_bus
        from capabilities.video.services.video_handlers import register_default_handlers
        from capabilities.video.services.video_worker import video_worker

        async def _on_video_job_event(outcome: str, job: dict) -> None:
            """Video Worker 任务完成/失败 → 推送 SSE 事件给前端。"""
            workflow_id = job.get("storyboard_id") or job.get("user_id", "")
            event_type = "video_job_succeeded" if outcome == "succeeded" else "video_job_failed"
            payload = {
                "job_id": job["id"],
                "job_type": job.get("job_type"),
                "shot_id": job.get("shot_id"),
                "storyboard_id": job.get("storyboard_id"),
                "status": outcome,
                "result_asset_id": job.get("result_asset_id"),
                "result_url": job.get("result_url"),
                "error": job.get("error_message") if outcome == "failed" else None,
            }
            try:
                await sse_bus.publish(workflow_id, event_type, payload)
                logger.info(f"[video_worker] SSE 推送: {event_type} (job={job['id']})")
            except Exception as e:
                logger.warning(f"[video_worker] SSE 推送失败（忽略）: {e}")

        video_worker.on_event = _on_video_job_event
        register_default_handlers(video_worker)
        await video_worker.start()
        logger.info(f"Video worker started (handlers={video_worker.registered_types})")
        _hstep("Video worker", True, ",".join(video_worker.registered_types))
    except Exception as _vw_err:
        logger.warning(f"[lifespan] video worker init failed: {_vw_err}")
        _hstep("Video worker", False, str(_vw_err))

    # step 7: 内置 Playwright 浏览器客户端（G3）——不再依赖外部 QR Worker 进程。
    # 浏览器功能（navigate/snapshot/click 等）同进程内直接调用 Playwright。
    try:
        from app.tools.browser.client import get_browser_client

        _browser_client = get_browser_client()
        if await _browser_client._ensure_initialized():
            logger.info("Built-in Playwright browser client ready")
            _hstep("Browser (Playwright)", True)
        else:
            logger.warning(
                "[lifespan] ⚠️ Built-in Playwright browser init failed (will lazy-init on first use)"
            )
            _hstep("Browser (Playwright)", True, "lazy")
    except Exception as _browser_init_err:
        logger.warning(f"[lifespan] Built-in browser init failed: {_browser_init_err}")
        _hstep("Browser (Playwright)", False, str(_browser_init_err))

    # step 8: 通用浏览器自动化（G4）——审计 sink 注入 + MCP token 初始化
    try:
        import secrets as _secrets

        from app.api.routers.browser import install_audit_sink, set_generated_mcp_token

        install_audit_sink()
        if not settings.mcp_browser_token:
            _tok = _secrets.token_urlsafe(24)
            set_generated_mcp_token(_tok)
            logger.debug(
                "MCP_BROWSER_TOKEN not set; generated for /api/mcp/browser "
                f"(set MCP_BROWSER_TOKEN in .env to pin): {_tok}"
            )
        _hstep("Browser MCP", True)
    except Exception as _browser_err:
        logger.warning(f"[lifespan] browser automation init failed: {_browser_err}")
        _hstep("Browser MCP", False, str(_browser_err))

    logger.debug("[lifespan] all steps done, entering yield")

    _ok = sum(1 for _, ok, _ in _health if ok)
    _fail = len(_health) - _ok
    _lines = [f"{'✅' if ok else '❌'} {name:<20s} {detail}" for name, ok, detail in _health]
    logger.info(
        f"\n{'='*60}\n"
        f"  STARTUP HEALTH REPORT  {_ok}/{len(_health)} ok, {_fail} failed\n"
        f"{'='*60}\n" + "\n".join(f"  {l}" for l in _lines) + f"\n{'='*60}"
    )

    # 事件循环阻塞看门狗：服务"整体无响应"（连 /health 都超时）时，
    # 靠猜定位成本极高。这里周期性测量事件循环延迟，超阈值就把
    # 所有任务的调用栈打进日志，下次冻结可以直接看到是谁堵住了循环。
    _lag_task = asyncio.create_task(_event_loop_watchdog())

    yield

    _lag_task.cancel()

    # 关闭资源
    # 内置 Playwright 浏览器客户端
    try:
        from app.tools.browser.client import get_browser_client

        _bc = get_browser_client()
        await _bc.shutdown()
        logger.info("Built-in Playwright browser client shutdown")
    except Exception as e:
        logger.warning(f"Browser client shutdown failed: {e}")
    # 关闭 Redis 连接
    try:
        from app.cache import redis_client

        await redis_client.close()
        logger.info("Redis connection closed")
    except Exception as e:
        logger.warning(f"Redis close failed: {e}")
    # 选题池调度器关闭
    try:
        from capabilities.pool_monitor.main import pool_shutdown

        pool_shutdown()
    except Exception as e:
        logger.warning(f"Pool monitor shutdown failed: {e}")
    try:
        from capabilities.content_search.sources.manager import source_manager

        await source_manager.close_all()
    except Exception as e:
        logger.warning(f"Content sources close failed: {e}")
    # AI 视频工坊 Worker 停止（先于引擎 dispose，避免任务写库报连接已关）
    try:
        from capabilities.video.services.video_worker import video_worker

        await video_worker.stop()
    except Exception as e:
        logger.warning(f"Video worker shutdown failed: {e}")
    await engine_dispose()


async def engine_dispose() -> None:
    """关闭数据库引擎连接池。"""
    from app.db.session import engine

    await engine.dispose()


app = FastAPI(
    title=settings.app_name,
    description="多智能体小红书创作平台后端服务",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    import traceback

    tb = traceback.format_exc()
    logger.error(f"Unhandled exception on {request.method} {request.url}: {exc}\n{tb}")
    return JSONResponse(
        status_code=500,
        content={"detail": f"Internal Server Error: {str(exc)}", "traceback": tb},
    )


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """API 限流中间件，基于 Redis 滑动窗口计数器。

    规则：每个 user_id 每分钟最多 RATE_LIMIT_PER_MINUTE 次请求。
    未登录用户按 IP 限流。
    高频接口（chat、SSE、workflow）豁免限流——这些接口有自己的治理层控制。
    """

    RATE_LIMIT_PER_MINUTE = 600
    WINDOW_SECONDS = 60

    RATE_LIMIT_EXEMPT_PATHS = {
        "/health",
        "/docs",
        "/redoc",
        "/openapi.json",
        "/api/v1/plugins",
        "/api/v1/plugins/installed",
        "/api/v1/skills",
        "/api/v1/config",
        "/api/feishu/webhook",
    }
    RATE_LIMIT_EXEMPT_PREFIXES = (
        "/api/v1/plugins",
        "/api/v1/skills",
        "/api/v1/config",
        "/api/v1/reactor",
        "/api/sse/",
        "/api/chat/sessions",
        "/api/chat/completions",
        "/api/v1/chat/agent",
        "/api/v1/chat/sessions",
    )

    async def dispatch(self, request: Request, call_next):
        # DEV: 登录暂停期间跳过限流，避免所有请求共享同一 IP 桶导致误限
        return await call_next(request)

        if request.url.path in self.RATE_LIMIT_EXEMPT_PATHS:
            return await call_next(request)
        for prefix in self.RATE_LIMIT_EXEMPT_PREFIXES:
            if request.url.path.startswith(prefix):
                return await call_next(request)

        from app.cache import redis_client

        user_id = None
        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            try:
                from app.security import verify_jwt

                payload = verify_jwt(auth_header[7:])
                user_id = payload.get("sub")
            except Exception:
                pass

        limit_key = (
            f"ratelimit:user:{user_id}"
            if user_id
            else f"ratelimit:ip:{request.client.host if request.client else 'unknown'}"
        )

        try:
            count = await redis_client.incr(limit_key)
            if count == 1:
                await redis_client.expire(limit_key, self.WINDOW_SECONDS)

            if count > self.RATE_LIMIT_PER_MINUTE:
                return JSONResponse(
                    status_code=429,
                    content={
                        "detail": f"请求过于频繁，请稍后再试（限制：{self.RATE_LIMIT_PER_MINUTE}次/分钟）"
                    },
                )
        except Exception:
            pass

        response = await call_next(request)
        return response


class RequestLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if "feishu" in request.url.path or request.url.path == "/":
            logger.info(
                f"[RequestLog] {request.method} {request.url.path} from {request.client.host if request.client else '?'}"
            )
        return await call_next(request)


app.add_middleware(RequestLogMiddleware)
app.add_middleware(RateLimitMiddleware)


async def _event_loop_watchdog(interval_s: float = 10.0) -> None:
    """周期性测量事件循环延迟；明显卡顿则打印所有任务的调用栈。

    背景：多次出现「后端整体无响应、/health 都超时」的冻结事故，
    根因都是某个同步调用堵住了事件循环。看门狗把"卡在哪一行"
    直接写进日志，避免事后靠猜。
    """
    import traceback

    while True:
        try:
            await asyncio.sleep(interval_s)
            t0 = asyncio.get_running_loop().time()
            # 让出一次循环：回来时的偏差就是事件循环被阻塞的时长
            await asyncio.sleep(0)
            lag = (asyncio.get_running_loop().time() - t0) * 1000
            if lag > 3000:
                logger.warning(f"[watchdog] event loop lag {lag:.0f}ms — 可能发生阻塞")
            if lag > 15000:
                logger.error(f"[watchdog] event loop 严重阻塞 ({lag:.0f}ms)，转储任务栈：")
                import linecache

                dumped = 0
                for task in asyncio.all_tasks():
                    if task is asyncio.current_task() or task.done():
                        continue
                    stack = task.get_stack()
                    if not stack:
                        continue
                    parts = []
                    for f in stack[-6:]:
                        src = linecache.getline(f.f_code.co_filename, f.f_lineno).strip()
                        parts.append(
                            f"    {f.f_code.co_filename}:{f.f_lineno} in {f.f_code.co_name} | {src[:90]}"
                        )
                    if parts:
                        logger.error(
                            f"[watchdog] ── task [{task.get_name()}]:\n" + "\n".join(parts)
                        )
                        dumped += 1
                logger.error(f"[watchdog] 转储完成，共 {dumped} 个挂起任务带栈帧")
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("[watchdog] 异常")
            await asyncio.sleep(interval_s)


@app.get("/health", tags=["系统"])
async def health_check() -> dict[str, str]:
    """健康检查端点。"""
    from app.cache import redis_client

    cache_status = "redis" if redis_client.is_connected else "fallback"
    return {"status": "ok", "app": settings.app_name, "cache": cache_status}


# ===== 路由注册 =====
app.include_router(auth.router)
app.include_router(chat.router)
app.include_router(chat_agent.router)
app.include_router(chat_session.router)
app.include_router(side_session.router)
app.include_router(chat_file.router)
app.include_router(chat_folder.router)
app.include_router(search.router)
app.include_router(sse.router)
app.include_router(profile.router)  # 用户画像 API (D18 创作者画像)
app.include_router(config.router)
app.include_router(skills.router)
app.include_router(agents.router)
app.include_router(assets.router)
app.include_router(video_assets.router)
app.include_router(proxy.router)
app.include_router(topic_pool.router)
app.include_router(memory.router)
app.include_router(my_works.router)
app.include_router(accounts.router)  # 平台账号管理 API（账号绑定 + 作品数据回收）
app.include_router(workspace.router)
app.include_router(plugins.router)  # Plugin system API (v2.0)
app.include_router(governance.router)  # Governance Stats API
from app.api.routers import wechat_bot

app.include_router(wechat_bot.router)  # WeChat Bot API (v6.0 - iLink协议)
from app.api.routers import feishu_bot

app.include_router(feishu_bot.router)  # Feishu Bot API (飞书集成)
app.include_router(feishu_oauth.router)  # Feishu OAuth user account binding
app.include_router(feishu_docs.router)  # 飞书云文档网页组件鉴权（右侧栏活文档展示）
app.include_router(reactor.router)  # Reactor mode API (ReAct loop + thin primitives)
app.include_router(browser.router)  # Generic browser automation API (G4: domains + audit)
app.include_router(browser.mcp_router)  # External MCP Server /api/mcp/browser (G4)
from app.api.routers import task_plan

app.include_router(task_plan.router)  # Task Plan API (多日定时发布)

app.include_router(creative_artifact.router)  # CreativeArtifact API (统一创作对象)
app.include_router(quality_gate.router)  # 共享质量门禁 API (P1-3)
app.include_router(dashboard.router)  # 数据看板 API (P0-2)
app.include_router(cards.router)  # 卡片 DSL HTTP 服务（图文渲染链路）
app.include_router(feedback.router)  # 用户反馈 API（点赞/点踩/重试）
app.include_router(internal.router)  # 内部端点：能力服务进度事件回推（五期-C）
app.include_router(models.router)  # BYOK 动态模型管理 API
app.include_router(workflows.router)  # 平台发布适配器 API（小红书/微博/公众号）
app.include_router(mcp_bridge.router)  # MCP 桥接 API（浏览器扩展中转）

# 挂载 uploads 目录为静态文件服务（选题池封面图/详情图 + 用户头像）
_upload_dir = Path(os.environ.get("UPLOAD_DIR", "uploads"))
_upload_dir.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(_upload_dir)), name="uploads")

# 挂载 outputs 目录：平台产物（卡片图/视频）以 /outputs/<相对路径> 对外暴露
# 与 output_index 使用同一套解析逻辑，保证「扫描到的产物」与「静态可访问路径」指向同一目录
from app.services.output_index import output_root as _resolve_output_root

_outputs_dir = _resolve_output_root()
if _outputs_dir is not None:
    app.mount("/outputs", StaticFiles(directory=str(_outputs_dir)), name="outputs")

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
