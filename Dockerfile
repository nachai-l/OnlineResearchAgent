# syntax=docker/dockerfile:1.7
#
# A2A Web Research Agent — deployable image.
#
# Two-stage build:
#   1. `builder` installs Python deps into /opt/venv so compilation artifacts
#      (lxml wheels, trafilatura's binaries) don't bloat the final image.
#   2. `runtime` copies the populated venv + source tree onto a slim base
#      and runs as an unprivileged user.
#
# The image is configuration-driven: no secrets or environment-specific
# values are baked in. Runtime behavior is controlled via:
#   - `.env` file mounted / passed with `--env-file`
#   - `A2A_HOST` / `A2A_PORT` / `A2A_PUBLIC_URL` env-var overrides (honored
#     by scripts/run_server.py)
#   - volume mounts for `/app/artifacts/cache` (JSONL cache) and
#     `/app/artifacts/logs` (rotating log file) so data survives container
#     restarts and is inspectable from the host.

# ---------- builder ----------------------------------------------------------
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Build deps for trafilatura (lxml) + httpx. Kept only in the builder stage.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libxml2-dev \
        libxslt1-dev \
        zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

COPY requirements.txt /tmp/requirements.txt
RUN pip install --upgrade pip && pip install -r /tmp/requirements.txt


# ---------- runtime ----------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:${PATH}" \
    PYTHONPATH="/app" \
    A2A_HOST=0.0.0.0 \
    A2A_PORT=8000

# Runtime-only shared libs for lxml (trafilatura). Dev headers stay behind.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libxml2 \
        libxslt1.1 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system app && useradd --system --gid app --home /app app

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app

# Copy source. Paths listed explicitly so stray local artifacts (`.venv`,
# `.pytest_cache`, `artifacts/cache/*.jsonl`) never enter the image even
# if `.dockerignore` is incomplete.
COPY functions /app/functions
COPY prompts   /app/prompts
COPY configs   /app/configs
COPY scripts   /app/scripts

# Create writable artifact dirs. Chown so the non-root user can write logs
# and JSONL caches at runtime. These are typically bind-mounted over, but
# the baked directories mean the image also works unmounted.
RUN mkdir -p /app/artifacts/cache /app/artifacts/logs \
    && chown -R app:app /app

USER app

EXPOSE 8000

# Simple TCP liveness check against the Agent Card endpoint.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request,sys; \
urllib.request.urlopen('http://127.0.0.1:${A2A_PORT}/.well-known/agent-card.json', timeout=3); \
sys.exit(0)" || exit 1

CMD ["python", "scripts/run_server.py"]
