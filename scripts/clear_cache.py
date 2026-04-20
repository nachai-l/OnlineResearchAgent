"""Truncate every JSONL cache file to zero length.

Useful when a prompt change invalidates cached LLM responses or when you
want to force fresh SERP + scrape hits for a benchmark run.

    python scripts/clear_cache.py
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from functions.utils.config import load_settings  # noqa: E402
from functions.utils.paths import CONFIGS_DIR  # noqa: E402


def main() -> int:
    settings = load_settings(
        parameters_path=CONFIGS_DIR / "parameters.yaml",
        credentials_path=CONFIGS_DIR / "credentials.yaml",
    )
    paths = [settings.cache_path(k) for k in ("search", "scrape", "llm")]
    cleared = 0
    for p in paths:
        if p.exists():
            p.write_text("", encoding="utf-8")
            cleared += 1
            print(f"cleared {p}")
        else:
            print(f"skipped (not present) {p}")
    print(f"done — {cleared} file(s) cleared.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
