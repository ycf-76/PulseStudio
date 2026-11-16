# PulseStudio 后端镜像：FastAPI + uv
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_COMPILE_BYTECODE=1

WORKDIR /app

# 系统依赖（如需 MediaPipe/ffmpeg 可在后续版本追加）
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 安装 uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

# 先拷贝依赖清单，利用 Docker 层缓存
COPY pyproject.toml uv.lock* ./

RUN uv sync --frozen --no-install-project --no-dev || uv sync --no-install-project --no-dev

# 拷贝源码
COPY app ./app

# 运行时目录（建议通过 volume 挂载持久化）
RUN mkdir -p /app/data /app/uploads /app/outputs

ENV DATABASE_URL="sqlite+aiosqlite:////app/data/xhs_agent.db" \
    UPLOAD_DIR="/app/uploads"

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
