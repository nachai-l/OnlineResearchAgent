"""Project path helpers.

Keeps file-location concerns in one place so modules don't sprinkle relative
paths. All paths are resolved relative to the repository root, which is
defined as the parent of the ``functions/`` package directory.
"""
from __future__ import annotations

from pathlib import Path

# functions/utils/paths.py -> functions/utils -> functions -> <repo root>
REPO_ROOT: Path = Path(__file__).resolve().parents[2]

CONFIGS_DIR: Path = REPO_ROOT / "configs"
PROMPTS_DIR: Path = REPO_ROOT / "prompts"
ARTIFACTS_DIR: Path = REPO_ROOT / "artifacts"
CACHE_DIR: Path = ARTIFACTS_DIR / "cache"
LOGS_DIR: Path = ARTIFACTS_DIR / "logs"


def ensure_dir(path: Path) -> Path:
    """Create ``path`` (and parents) if missing; return it for chaining."""
    path.mkdir(parents=True, exist_ok=True)
    return path
