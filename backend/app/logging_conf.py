"""集中式日志配置 — 启动时调用 setup_logging() 一次即可。

核心策略：
  - 应用自身 (app.*) 保持 INFO，方便看启动进度和关键业务日志
  - 第三方库统一压到 WARNING，避免 httpx/sqlalchemy/apscheduler 等刷屏
  - 支持 LOG_LEVEL 环境变量一键调整应用日志级别
  - uvicorn 的 access log 保留，但 handler log 压到 WARNING
"""

import logging
import os
import sys

_NOISY_THIRD_PARTY_LOGGERS = [
    "sqlalchemy",
    "sqlalchemy.engine",
    "sqlalchemy.pool",
    "sqlalchemy.orm",
    "sqlalchemy.dialects",
    "apscheduler",
    "apscheduler.schedulers",
    "apscheduler.executors",
    "httpx",
    "httpcore",
    "urllib3",
    "openai",
    "openai._base_client",
    "langchain",
    "langchain_core",
    "langchain_community",
    "langsmith",
    "langgraph",
    "playwright",
    "asyncio",
    "aiosqlite",
    "aiomysql",
    "multipart",
    "starlette.middleware",
    "PIL",
    "matplotlib",
    "filelock",
    "tiktoken",
    "http11",
    "trio",
    "anyio",
]

_FORMAT = "%(asctime)s %(levelname)-5s [%(name)s] %(message)s"
_DATE_FORMAT = "%H:%M:%S"


def setup_logging(app_level: str | None = None) -> None:
    """初始化日志系统。应在 main.py 最早处调用（import app 之后、lifespan 之前）。

    Parameters
    ----------
    app_level : str | None
        应用日志级别，默认从 LOG_LEVEL 环境变量读取，未设置则 INFO。
        传 "DEBUG" 可看所有应用日志，传 "WARNING" 可只看警告。
    """
    level_name = (app_level or os.environ.get("LOG_LEVEL", "INFO")).upper()
    app_level_val = getattr(logging, level_name, logging.INFO)

    root = logging.getLogger()
    if root.handlers:
        return

    root.setLevel(logging.WARNING)

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.WARNING)
    console.setFormatter(logging.Formatter(_FORMAT, _DATE_FORMAT))
    root.addHandler(console)

    # 2026-10-22：应用日志常驻文件。此前日志只进 stdout，启动方式一变
    # （没重定向/终端关了）日志就没了——排查「图文任务卡 guardian 确认、
    # skill_call 九连败」时无据可查。滚动文件 10MB × 5 个，位于 logs/app.log。
    _file_handler: logging.Handler | None = None
    try:
        from logging.handlers import RotatingFileHandler

        _log_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
        os.makedirs(_log_dir, exist_ok=True)
        _file_handler = RotatingFileHandler(
            os.path.join(_log_dir, "app.log"),
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        _file_handler.setLevel(logging.INFO)
        _file_handler.setFormatter(logging.Formatter(_FORMAT, _DATE_FORMAT))
        root.addHandler(_file_handler)
    except Exception as _fh_err:
        # 文件日志挂不上（只读盘等）不阻塞启动，stdout 兜底
        print(f"[logging] file handler not installed: {_fh_err}")

    app_handler = logging.StreamHandler(sys.stdout)
    app_handler.setLevel(app_level_val)
    app_handler.setFormatter(logging.Formatter(_FORMAT, _DATE_FORMAT))

    app_logger = logging.getLogger("app")
    app_logger.setLevel(app_level_val)
    app_logger.addHandler(app_handler)
    if _file_handler is not None:
        # root 的文件 handler 收不到 app.*（app_logger.propagate=False 且
        # root logger 级别 WARNING 会拦 INFO）—— 给 app logger 直挂同一
        # 文件 handler 实例，保证 app.* 的 INFO 落盘
        app_logger.addHandler(_file_handler)
    app_logger.propagate = False

    for name in _NOISY_THIRD_PARTY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    uvicorn_access = logging.getLogger("uvicorn.access")
    uvicorn_access.setLevel(logging.INFO)

    uvicorn_error = logging.getLogger("uvicorn.error")
    uvicorn_error.setLevel(logging.INFO)
    if not uvicorn_error.handlers:
        uvicorn_error.addHandler(app_handler)
    uvicorn_error.propagate = False