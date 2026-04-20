"""Start the A2A ASGI server.

    python scripts/run_server.py

Serves the Agent Card at ``/.well-known/agent-card.json`` and the JSON-RPC
endpoint at ``/``. Host/port come from ``configs/parameters.yaml`` by
default, but can be overridden via environment variables — which is what
the Docker image relies on so the same image works for localhost and for
containerized deployments without rebuilding.

Env-var overrides (all optional):

- ``A2A_HOST``       — bind address (default from parameters.yaml)
- ``A2A_PORT``       — bind port (default from parameters.yaml)
- ``A2A_PUBLIC_URL`` — URL advertised in the Agent Card. Set this when the
  server sits behind a reverse proxy / load balancer / docker bridge so
  clients receive the externally reachable URL, not the in-container
  bind address.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import uvicorn

# Make `functions.*` importable when running this script directly.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from functions.agent.server import build_app  # noqa: E402
from functions.agent.skills import ResearchAgentExecutor  # noqa: E402
from functions.agent.wiring import build_pipeline  # noqa: E402
from functions.utils.config import load_settings  # noqa: E402
from functions.utils.logging import configure_logging  # noqa: E402
from functions.utils.paths import CONFIGS_DIR, ensure_dir  # noqa: E402


def main() -> None:
    settings = load_settings(
        parameters_path=CONFIGS_DIR / "parameters.yaml",
        credentials_path=CONFIGS_DIR / "credentials.yaml",
    )
    ensure_dir(settings.log_path().parent)
    configure_logging(
        level=settings.logging.level,
        log_file=settings.log_path(),
        max_bytes=settings.logging.max_bytes,
        backup_count=settings.logging.backup_count,
    )

    # Env-var overrides let the same image run on localhost, in Docker
    # (bind 0.0.0.0), or behind a proxy (advertise public URL).
    host = os.environ.get("A2A_HOST", settings.server.host)
    port = int(os.environ.get("A2A_PORT", str(settings.server.port)))
    public_url = os.environ.get("A2A_PUBLIC_URL") or f"http://{host}:{port}"

    pipeline = build_pipeline(settings)
    executor = ResearchAgentExecutor(pipeline=pipeline)
    app = build_app(executor=executor, url=public_url)
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_config=None,  # our `configure_logging` owns the root logger
    )


if __name__ == "__main__":
    main()
