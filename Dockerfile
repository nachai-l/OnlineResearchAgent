# syntax=docker/dockerfile:1.7
#
# Online Research Agent — production image.
#
# Two-stage build: a `builder` stage compiles Python deps into /opt/venv,
# then `runtime` copies that venv onto a slim base and runs as a non-root
# user. No secrets or environment-specific values are baked in — runtime
# behavior is controlled via env vars (A2A_HOST / A2A_PORT / A2A_PUBLIC_URL
# plus the API-key vars listed in configs/credentials.yaml).

# ---------- builder ----------------------------------------------------------
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Build deps for lxml (trafilatura). Dev headers stay in this stage only.
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

# Runtime shared libs for lxml; create non-root user in the same layer.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libxml2 \
        libxslt1.1 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system app && useradd --system --gid app --home /app app

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app

# Paths listed explicitly so local build context junk (.venv, caches, logs)
# never enters the image even if .dockerignore drifts.
COPY functions /app/functions
COPY prompts   /app/prompts
COPY configs   /app/configs
COPY scripts   /app/scripts

# Writable dirs for rotating logs + JSONL caches. In k8s these are typically
# mounted over with emptyDir / PVC; the baked dirs keep the image runnable
# standalone (docker run) without extra mounts.
RUN mkdir -p /app/artifacts/cache /app/artifacts/logs \
    && chown -R app:app /app

USER app

EXPOSE 8000

CMD ["python", "scripts/run_server.py"]