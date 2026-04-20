"""Stable hashing for cache keys.

All JSONL caches in this project key records by ``stable_hash(...)`` of the
request payload. Stability means: same logical input → same hex digest across
Python runs, OS, and key insertion order.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


def _canonical_json(obj: Any) -> bytes:
    """Serialize ``obj`` to deterministic UTF-8 JSON bytes.

    Uses ``sort_keys=True`` for key-order invariance and the most compact
    separators so whitespace differences can't affect the digest.
    """
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def stable_hash(obj: Any) -> str:
    """Return a hex SHA-256 digest of ``obj``'s canonical JSON form.

    Raises:
        TypeError: if ``obj`` is not JSON-serializable. Callers should
            pre-coerce exotic types rather than relying on custom encoders.
    """
    return hashlib.sha256(_canonical_json(obj)).hexdigest()
