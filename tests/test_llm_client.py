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
from pydantic import BaseModel, Field

from functions.core.cache import JsonlCache
from functions.llm.client import GeminiClient, LlmCallError


class _DemoModel(BaseModel):
    """Tiny pydantic target used by the generate_model tests."""

    n: int = Field(ge=0)
    label: str = ""


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
        # Retry budget is shared with transport — explicitly set to 0 so
        # one bad JSON response raises immediately (no iterator underflow).
        call_fn, _ = _make_call_fn(["not json at all"])
        cache = JsonlCache(tmp_cache_dir / "llm.jsonl")
        client = GeminiClient(
            model="m",
            api_key="k",
            cache=cache,
            call_fn=call_fn,
            retries=0,
            retry_base_delay=0.0,
        )
        with pytest.raises(LlmCallError):
            await client.generate_json(system="s", user="u")

    async def test_generate_json_retries_on_parse_failure(
        self, tmp_cache_dir: Path
    ):
        """Bad JSON goes through the same retry envelope as bad transport.

        The model occasionally emits malformed JSON (missing comma,
        truncated output). Bouncing the same call usually recovers on
        retry — the envelope shouldn't distinguish transport from parse.
        """
        call_fn, calls = _make_call_fn(
            ["not json", "still broken", '{"ok": true}']
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
        parsed = await client.generate_json(system="s", user="u")
        assert parsed == {"ok": True}
        assert len(calls) == 3

    # ---- generate_model (pydantic-validated) ------------------------------

    async def test_generate_model_validates_and_returns_instance(
        self, tmp_cache_dir: Path
    ):
        call_fn, _ = _make_call_fn(['{"n": 3, "label": "ok"}'])
        cache = JsonlCache(tmp_cache_dir / "llm.jsonl")
        client = GeminiClient(model="m", api_key="k", cache=cache, call_fn=call_fn)
        out = await client.generate_model(
            response_model=_DemoModel, system="s", user="u"
        )
        assert isinstance(out, _DemoModel)
        assert out.n == 3
        assert out.label == "ok"

    async def test_generate_model_strips_code_fences(self, tmp_cache_dir: Path):
        fenced = "```json\n{\"n\": 1}\n```"
        call_fn, _ = _make_call_fn([fenced])
        cache = JsonlCache(tmp_cache_dir / "llm.jsonl")
        client = GeminiClient(model="m", api_key="k", cache=cache, call_fn=call_fn)
        out = await client.generate_model(
            response_model=_DemoModel, system="s", user="u"
        )
        assert out.n == 1

    async def test_generate_model_retries_on_schema_violation(
        self, tmp_cache_dir: Path
    ):
        """Schema failures are retried using the same ``retries`` budget.

        First response violates the ``n >= 0`` constraint; second is
        missing ``n`` entirely; third is valid. The retry envelope must
        treat both pydantic ValidationError and JSONDecodeError as
        retryable.
        """
        call_fn, calls = _make_call_fn(
            [
                '{"n": -1}',              # fails Field(ge=0)
                '{"label": "oops"}',     # fails required field
                '{"n": 5, "label": "final"}',
            ]
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
        out = await client.generate_model(
            response_model=_DemoModel, system="s", user="u"
        )
        assert out.n == 5
        assert out.label == "final"
        assert len(calls) == 3

    async def test_generate_model_exhausted_raises(self, tmp_cache_dir: Path):
        """All responses fail validation → surfaces as LlmCallError."""
        call_fn, calls = _make_call_fn(
            ['{"n": -1}', '{"n": -2}', '{"n": -3}']
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
            await client.generate_model(
                response_model=_DemoModel, system="s", user="u"
            )
        assert len(calls) == 3

    async def test_generate_model_caches_only_valid_responses(
        self, tmp_cache_dir: Path
    ):
        """A validated response gets cached; bad attempts do not."""
        call_fn, calls = _make_call_fn(
            ['{"n": -1}', '{"n": 2}']
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
        first = await client.generate_model(
            response_model=_DemoModel, system="s", user="u"
        )
        assert first.n == 2
        # Second call should hit cache — no third call_fn invocation.
        second = await client.generate_model(
            response_model=_DemoModel, system="s", user="u"
        )
        assert second.n == 2
        assert len(calls) == 2  # one bad + one good; cache hit on replay

    async def test_cache_hit_failing_current_schema_is_refetched(
        self, tmp_cache_dir: Path
    ):
        """Schema drift self-heals: a cached response that no longer
        satisfies the (possibly upgraded) response_model is treated as
        a miss, and the fresh response shadows it via last-write-wins.
        """
        # Seed the cache with a loose shape that happened to parse fine
        # in an earlier version of the code.
        class _Looser(BaseModel):
            label: str = ""

        call_fn, calls = _make_call_fn(
            [
                '{"label": "old"}',        # seeds the cache under _Looser
                '{"n": 7, "label": "new"}', # refetch under _DemoModel
            ]
        )
        cache = JsonlCache(tmp_cache_dir / "llm.jsonl")
        loose_client = GeminiClient(
            model="m",
            api_key="k",
            cache=cache,
            call_fn=call_fn,
            retries=0,
            retry_base_delay=0.0,
        )
        loose = await loose_client.generate_model(
            response_model=_Looser, system="s", user="u"
        )
        assert loose.label == "old"

        strict_client = GeminiClient(
            model="m",
            api_key="k",
            cache=cache,
            call_fn=call_fn,
            retries=0,
            retry_base_delay=0.0,
        )
        fresh = await strict_client.generate_model(
            response_model=_DemoModel, system="s", user="u"
        )
        assert fresh.n == 7
        assert fresh.label == "new"
        assert len(calls) == 2  # the old cache entry was refetched, not served
