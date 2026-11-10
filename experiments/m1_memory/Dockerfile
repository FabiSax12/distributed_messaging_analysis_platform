FROM ghcr.io/astral-sh/uv:python3.12-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libgl1 libglib2.0-0 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY services/ services/
RUN uv sync --all-packages --all-extras --frozen
COPY experiments/m1_memory/ experiments/m1_memory/
# Cache downloads at build time so cgroup runs measure models, not installation.
RUN uv run --all-packages --all-extras python -c "from workers.ops.analysis.image import load_model; load_model(); from workers.ops.analysis.audio import load_model; load_model()"
ENTRYPOINT ["uv", "run", "--all-packages", "--all-extras", "python", "experiments/m1_memory/scripts/measure.py"]
