"""TDD tests for functions.core.cache.JsonlCache."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from functions.core.cache import JsonlCache


class TestJsonlCache:
    def test_file_created_on_first_put(self, tmp_cache_dir: Path):
        path = tmp_cache_dir / "c.jsonl"
        cache = JsonlCache(path)
        assert not path.exists()
        cache.put("k", {"hello": "world"})
        assert path.exists()

    def test_put_then_get_roundtrip(self, tmp_cache_dir: Path):
        cache = JsonlCache(tmp_cache_dir / "c.jsonl")
        cache.put("key1", {"a": 1})
        assert cache.get("key1") == {"a": 1}

    def test_get_miss_returns_none(self, tmp_cache_dir: Path):
        cache = JsonlCache(tmp_cache_dir / "c.jsonl")
        assert cache.get("absent") is None

    def test_last_write_wins(self, tmp_cache_dir: Path):
        cache = JsonlCache(tmp_cache_dir / "c.jsonl")
        cache.put("k", {"v": 1})
        cache.put("k", {"v": 2})
        cache.put("k", {"v": 3})
        assert cache.get("k") == {"v": 3}

    def test_put_appends_one_line_per_call(self, tmp_cache_dir: Path):
        path = tmp_cache_dir / "c.jsonl"
        cache = JsonlCache(path)
        cache.put("a", {"v": 1})
        cache.put("b", {"v": 2})
        cache.put("a", {"v": 3})
        lines = path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 3
        for line in lines:
            parsed = json.loads(line)
            assert set(parsed.keys()) >= {"key", "value", "ts"}

    def test_corrupt_line_skipped(self, tmp_cache_dir: Path, caplog):
        path = tmp_cache_dir / "c.jsonl"
        # Manually write a mix of valid + garbage lines.
        with path.open("w", encoding="utf-8") as f:
            f.write(json.dumps({"key": "k1", "value": {"v": 1}, "ts": "t"}) + "\n")
            f.write("this is not json\n")
            f.write(json.dumps({"key": "k2", "value": {"v": 2}, "ts": "t"}) + "\n")
        cache = JsonlCache(path)
        assert cache.get("k1") == {"v": 1}
        assert cache.get("k2") == {"v": 2}
        assert cache.get("absent") is None

    def test_iter_yields_all_records_in_order(self, tmp_cache_dir: Path):
        cache = JsonlCache(tmp_cache_dir / "c.jsonl")
        cache.put("a", {"v": 1})
        cache.put("b", {"v": 2})
        records = list(cache)
        assert [r["key"] for r in records] == ["a", "b"]
        assert [r["value"] for r in records] == [{"v": 1}, {"v": 2}]

    def test_clear_truncates(self, tmp_cache_dir: Path):
        path = tmp_cache_dir / "c.jsonl"
        cache = JsonlCache(path)
        cache.put("k", {"v": 1})
        cache.clear()
        assert path.exists()
        assert path.read_text(encoding="utf-8") == ""
        assert cache.get("k") is None

    def test_clear_on_missing_file_is_noop(self, tmp_cache_dir: Path):
        cache = JsonlCache(tmp_cache_dir / "never.jsonl")
        cache.clear()  # should not raise

    def test_survives_process_restart(self, tmp_cache_dir: Path):
        path = tmp_cache_dir / "c.jsonl"
        c1 = JsonlCache(path)
        c1.put("persistent", {"v": 42})
        c2 = JsonlCache(path)  # fresh instance, same file
        assert c2.get("persistent") == {"v": 42}

    def test_value_must_be_jsonable(self, tmp_cache_dir: Path):
        cache = JsonlCache(tmp_cache_dir / "c.jsonl")
        with pytest.raises(TypeError):
            cache.put("k", {"bad": object()})

    def test_ts_is_iso8601(self, tmp_cache_dir: Path):
        import re

        path = tmp_cache_dir / "c.jsonl"
        cache = JsonlCache(path)
        cache.put("k", {"v": 1})
        line = path.read_text(encoding="utf-8").splitlines()[0]
        ts = json.loads(line)["ts"]
        # Basic ISO-8601 sanity check (allow Z or +00:00).
        assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", ts)
