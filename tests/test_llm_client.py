"""TDD tests for functions.llm.client.GeminiClient.

The client is designed for dependency injection: callers pass a ``call_fn``
that knows how to talk to the real Gemini SDK. Tests supply a fake callable
so no network is hit and behavior (caching, retries, structured output) is
verified in isolation.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from functions.core.cache import JsonlCache
from functions.llm.client import GeminiClient, LlmCallError


def _make_call_fn(responses):
    """Build an async call_fn that yields successive ``responses`` on each call."""
    it = iter(responses)
    calls: list[dict] = []

    async def call_fn(*, model, system, user, temperature, max_output_tokens, timeout):
        calls.append(
            dict(
                model=model,
                system=system,
                user=user,
                temperature=temperature,
                max_output_tokens=max_output_tokens,
                timeout=timeout,
            )
        )
        result = next(it)
        if isinstance(result, Exception):
            raise result
        return result

    return call_fn, calls


class TestGeminiClient:
    async def test_generate_returns_text(self, tmp_cache_dir: Path):
        call_fn, calls = _make_call_fn(["hello world"])
        cache = JsonlCache(tmp_cache_dir / "llm.jsonl")
        client = GeminiClient(
            model="gemini-2.5-flash",
            api_key="dummy",
            cache=cache,
            call_fn=call_fn,
        )
        out = await client.generate(system="s", user="u")
        assert out == "hello world"
        assert len(calls) == 1
        assert calls[0]["model"] == "gemini-2.5-flash"

    async def test_cache_hit_skips_call_fn(self, tmp_cache_dir: Path):
        call_fn, calls = _make_call_fn(["fresh"])
        cache = JsonlCache(tmp_cache_dir / "llm.jsonl")
        client = GeminiClient(
            model="m", api_key="k", cache=cache, call_fn=call_fn
        )
        first = await client.generate(system="s", user="u")
        second = await client.generate(system="s", user="u")
        assert first == second == "fresh"
        assert len(calls) == 1  # second call served from cache

    async def test_cache_key_discriminates_params(self, tmp_cache_dir: Path):
        call_fn, calls = _make_call_fn(["a", "b"])
        cache = JsonlCache(tmp_cache_dir / "llm.jsonl")
        client = GeminiClient(model="m", api_key="k", cache=cache, call_fn=call_fn)
        r1 = await client.generate(system="s", user="u", temperature=0.0)
        r2 = await client.generate(system="s", user="u", temperature=0.7)
        assert r1 == "a"
        assert r2 == "b"
        assert len(calls) == 2

    async def test_retry_on_transient_then_success(self, tmp_cache_dir: Path):
        call_fn, calls = _make_call_fn(
            [TimeoutError("slow"), TimeoutError("slow"), "finally"]
        )
        cache = JsonlCache(tmp_cache_dir / "llm.jsonl")
        client = GeminiClient(
            model="m",
            api_key="k",
            cache=cache,
            call_fn=call_fn,
            retries=2,
            retry_base_delay=0.0,
        )
        out = await client.generate(system="s", user="u")
        assert out == "finally"
        assert len(calls) == 3

    async def test_retry_exhaustion_raises(self, tmp_cache_dir: Path):
        call_fn, calls = _make_call_fn(
            [TimeoutError("1"), TimeoutError("2"), TimeoutError("3")]
        )
        cache = JsonlCache(tmp_cache_dir / "llm.jsonl")
        client = GeminiClient(
            model="m",
            api_key="k",
            cache=cache,
            call_fn=call_fn,
            retries=2,
            retry_base_delay=0.0,
        )
        with pytest.raises(LlmCallError):
            await client.generate(system="s", user="u")
        assert len(calls) == 3  # 1 initial + 2 retries

    async def test_disabled_cache_always_calls(self, tmp_cache_dir: Path):
        call_fn, calls = _make_call_fn(["one", "two"])
        client = GeminiClient(
            model="m", api_key="k", cache=None, call_fn=call_fn
        )
        a = await client.generate(system="s", user="u")
        b = await client.generate(system="s", user="u")
        assert (a, b) == ("one", "two")
        assert len(calls) == 2

    async def test_missing_api_key_when_no_fake_raises(self, tmp_cache_dir: Path):
        # No call_fn and no api_key -> must raise at generate() time, not construct.
        cache = JsonlCache(tmp_cache_dir / "llm.jsonl")
        client = GeminiClient(model="m", api_key=None, cache=cache, call_fn=None)
        with pytest.raises(LlmCallError):
            await client.generate(system="s", user="u")

    async def test_generate_json_parses_response(self, tmp_cache_dir: Path):
        call_fn, calls = _make_call_fn(['{"ok": true, "n": 3}'])
        cache = JsonlCache(tmp_cache_dir / "llm.jsonl")
        client = GeminiClient(model="m", api_key="k", cache=cache, call_fn=call_fn)
        parsed = await client.generate_json(system="s", user="u")
        assert parsed == {"ok": True, "n": 3}

    async def test_generate_json_strips_code_fences(self, tmp_cache_dir: Path):
        """Gemini sometimes wraps JSON in ```json fences; the client must cope."""
        fenced = "```json\n{\n  \"a\": 1\n}\n```"
        call_fn, calls = _make_call_fn([fenced])
        cache = JsonlCache(tmp_cache_dir / "llm.jsonl")
        client = GeminiClient(model="m", api_key="k", cache=cache, call_fn=call_fn)
        parsed = await client.generate_json(system="s", user="u")
        assert parsed == {"a": 1}

    async def test_generate_json_invalid_raises(self, tmp_cache_dir: Path):
        call_fn, _ = _make_call_fn(["not json at all"])
        cache = JsonlCache(tmp_cache_dir / "llm.jsonl")
        client = GeminiClient(model="m", api_key="k", cache=cache, call_fn=call_fn)
        with pytest.raises(LlmCallError):
            await client.generate_json(system="s", user="u")
