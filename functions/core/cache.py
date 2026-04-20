"""Append-only JSONL cache.

Each ``put`` writes one line ``{"key": ..., "value": ..., "ts": ...}`` to disk.
Reads scan forward and return the **last** matching record for a key, so
subsequent ``put``s naturally shadow earlier ones without requiring a
rewrite. Corrupt (non-JSON) lines are skipped with a WARNING log so a
damaged cache never becomes an outage.

Deliberately simple: this is an example agent; O(n) lookup is fine at
research-session scale. If you need larger scale, swap for SQLite/LMDB but
keep the JSONL audit trail for human-inspection friendliness.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

log = logging.getLogger(__name__)


class JsonlCache:
    """Persistent key-value cache backed by a single JSONL file."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    # ---- reads -------------------------------------------------------------

    def __iter__(self) -> Iterator[dict[str, Any]]:
        if not self._path.exists():
            return
        with self._path.open("r", encoding="utf-8") as f:
            for lineno, raw in enumerate(f, start=1):
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    yield json.loads(raw)
                except json.JSONDecodeError:
                    log.warning(
                        "skipping corrupt cache line",
                        extra={"path": str(self._path), "line_no": lineno},
                    )

    def get(self, key: str) -> dict[str, Any] | None:
        """Return the most-recent value for ``key``, or ``None`` if absent."""
        latest: dict[str, Any] | None = None
        for record in self:
            if record.get("key") == key:
                latest = record.get("value")
        return latest

    # ---- writes ------------------------------------------------------------

    def put(self, key: str, value: dict[str, Any]) -> None:
        """Append a record for ``key`` with payload ``value``.

        Raises:
            TypeError: if ``value`` is not JSON-serializable (fails fast
                rather than writing a half-usable line).
        """
        record = {
            "key": key,
            "value": value,
            "ts": datetime.now(tz=timezone.utc).isoformat(),
        }
        # Serialize first so we never create/touch the file when the payload
        # is unserializable.
        line = json.dumps(record, ensure_ascii=False)

        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def clear(self) -> None:
        """Truncate the cache file (no-op if absent)."""
        if self._path.exists():
            self._path.write_text("", encoding="utf-8")
