"""Shared pytest fixtures.

Importantly: add the repo root to ``sys.path`` so the ``functions`` package is
importable in tests without requiring an editable install.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Iterator

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture()
def tmp_cache_dir(tmp_path: Path) -> Path:
    """Writable cache dir isolated per-test."""
    d = tmp_path / "cache"
    d.mkdir()
    return d


@pytest.fixture(autouse=True)
def _reset_root_logger() -> Iterator[None]:
    """Prevent log handlers installed by one test from leaking into the next."""
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    yield
    # Remove anything the test added, then restore original state.
    for h in root.handlers[:]:
        root.removeHandler(h)
        try:
            h.close()
        except Exception:
            pass
    for h in saved_handlers:
        root.addHandler(h)
    root.setLevel(saved_level)
