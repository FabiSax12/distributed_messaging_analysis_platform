# Coordinator image. Build from the repo root:
#   docker build -f infra/docker/coordinator/Dockerfile -t coordinator .
# Configuration is read from environment variables at run time (see
# services/coordinator/src/coordinator/config.py); nothing is baked in.

FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.12.19 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app

# Dependencies first (cached until a pyproject or the lockfile changes). uv
# needs every workspace member's pyproject.toml to resolve the workspace.
COPY pyproject.toml uv.lock ./
COPY services/shared/pyproject.toml services/shared/
COPY services/coordinator/pyproject.toml services/coordinator/
COPY services/workers/pyproject.toml services/workers/
COPY services/storage/pyproject.toml services/storage/
RUN uv sync --frozen --package coordinator --no-dev --no-install-workspace

# Then the project itself, installed as regular (non-editable) wheels.
COPY services/shared services/shared
COPY services/coordinator services/coordinator
RUN uv sync --frozen --package coordinator --no-dev --no-editable


FROM python:3.12-slim
ARG GIT_SHA=unknown
LABEL org.opencontainers.image.title="coordinator" \
      org.opencontainers.image.revision="${GIT_SHA}"

RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin app
COPY --from=build --chown=app:app /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER app
WORKDIR /app
EXPOSE 8000

# /healthz is outside /api/*, so it does not need X-Origin-Verify. It returns
# 503 when a background thread (outbox relay, sweeper, consumers) has died.
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"]

# One process on purpose: the background threads live inside it (see main.py).
CMD ["uvicorn", "coordinator.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
