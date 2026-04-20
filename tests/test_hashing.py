"""TDD tests for functions.utils.hashing."""
from __future__ import annotations

import hashlib
import json

import pytest

from functions.utils.hashing import stable_hash


class TestStableHash:
    def test_returns_hex_sha256_digest(self):
        h = stable_hash({"q": "hello"})
        assert isinstance(h, str)
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_deterministic_across_calls(self):
        payload = {"q": "hello", "k": 3}
        assert stable_hash(payload) == stable_hash(payload)

    def test_key_order_invariant(self):
        """Stability must be key-order-agnostic (canonical JSON)."""
        assert stable_hash({"a": 1, "b": 2}) == stable_hash({"b": 2, "a": 1})

    def test_different_values_differ(self):
        assert stable_hash({"q": "hello"}) != stable_hash({"q": "world"})

    def test_matches_manual_canonical_sha256(self):
        payload = {"a": [1, 2, 3], "b": "x"}
        expected = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        assert stable_hash(payload) == expected

    def test_accepts_strings_directly(self):
        # Strings should be hashed as their canonical JSON representation.
        assert stable_hash("hello") == stable_hash("hello")
        assert stable_hash("hello") != stable_hash("hello ")

    def test_unicode_is_stable(self):
        payload = {"q": "café — naïve"}
        h1 = stable_hash(payload)
        h2 = stable_hash(payload)
        assert h1 == h2

    def test_rejects_non_json_serializable(self):
        with pytest.raises(TypeError):
            stable_hash({"bad": object()})
