"""CLI smoke test: run the research pipeline against live APIs.

Bypasses the A2A wire — useful for eyeballing the output, debugging the
prompts, and populating the JSONL caches before wiring the server up.

    python scripts/run_local.py "what is the A2A protocol"

The script loads ``configs/parameters.yaml`` + ``configs/credentials.yaml``,
pulls secrets from ``.env``, configures logging to ``artifacts/logs/agent.log``
+ stderr, and prints the markdown summary to stdout.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# Make `functions.*` importable when running this script directly.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from functions.agent.wiring import build_pipeline  # noqa: E402
from functions.utils.config import load_settings  # noqa: E402
from functions.utils.logging import configure_logging  # noqa: E402
from functions.utils.paths import CONFIGS_DIR, ensure_dir  # noqa: E402


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Local smoke test for the research pipeline.")
    p.add_argument("query", help="Research query.")
    return p.parse_args()


async def _amain(query: str) -> int:
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

    pipeline = build_pipeline(settings)
    result = await pipeline.run(query)

    print("=" * 72)
    print(f"Query:   {result.query}")
    print(f"Sources: {result.kept_count} kept / "
          f"{result.scraped_count} scraped / "
          f"{result.search_results_count} returned by search")
    print("=" * 72)
    print(result.summary_markdown)
    print()
    if result.sources:
        print("Sources:")
        for i, url in enumerate(result.sources, start=1):
            print(f"  [{i}] {url}")
    return 0


def main() -> int:
    args = _parse_args()
    return asyncio.run(_amain(args.query))


if __name__ == "__main__":
    raise SystemExit(main())
