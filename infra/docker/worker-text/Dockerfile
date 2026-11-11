# Worker image for the "text" pool. Build from the repo root:
#   docker build -f infra/docker/worker-text/Dockerfile -t worker-text .
# One image per pool on purpose (M5 may need different base images per node).
# Configuration is read from environment variables at run time (see
# services/workers/src/workers/config.py); nothing is baked in except POOL.

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
RUN uv sync --frozen --package workers --no-dev --no-install-workspace
# Text rules use only the base worker dependencies.

# Then the project itself, installed as regular (non-editable) wheels.
COPY services/shared services/shared
COPY services/workers services/workers
COPY services/storage services/storage
RUN uv sync --frozen --package workers --no-dev --no-editable


FROM python:3.12-slim
ARG GIT_SHA=unknown
LABEL org.opencontainers.image.title="worker-text" \
      org.opencontainers.image.revision="${GIT_SHA}"

RUN useradd --system --uid 10001 --create-home --shell /usr/sbin/nologin app
COPY --from=build --chown=app:app /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    POOL=text

USER app
WORKDIR /app

# No HEALTHCHECK: a worker has no listening port. Liveness is its heartbeat row
# in PostgreSQL, which the coordinator's sweeper watches (stale heartbeat =>
# the worker's sub-tasks are requeued).

# SIGTERM lets the current sub-task finish; give the container a long enough
# stop_grace_period (see infra/docker/compose.worker.yml).
CMD ["python", "-m", "workers.main"]
