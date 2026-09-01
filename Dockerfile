FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_CACHE_DIR=/root/.cache/uv \
    UV_HTTP_RETRIES=8 \
    UV_HTTP_TIMEOUT=120 \
    PATH="/app/.venv/bin:$PATH" \
    HOME="/home/appuser"

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

FROM python:3.14-slim-bookworm AS runtime

ARG APP_VERSION=0.1.0
ARG GIT_SHA=unknown
ARG BUILD_TIME=unknown

LABEL org.opencontainers.image.title="enterprise-rag-assistant" \
      org.opencontainers.image.version=$APP_VERSION \
      org.opencontainers.image.revision=$GIT_SHA \
      org.opencontainers.image.created=$BUILD_TIME \
      org.opencontainers.image.source="https://github.com/lintao19568886548/enterprise-rag-assistant"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH" \
    HOME="/home/appuser"

WORKDIR /app

RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip uninstall -y setuptools

COPY --from=builder /app/.venv /app/.venv

COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
COPY prompts ./prompts

RUN groupadd --gid 10001 appuser \
    && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin appuser \
    && mkdir -p /app/data /app/logs /app/output /models \
    && chown -R appuser:appuser /app/data /app/logs /app/output /models \
    && chown appuser:appuser /home/appuser

USER 10001:10001

EXPOSE 8000 8001

CMD ["python", "-m", "app.query_process.api.query_service"]
