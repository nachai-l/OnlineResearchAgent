"""TDD tests for functions.core.selection."""
from __future__ import annotations

from pathlib import Path

from functions.core.cache import JsonlCache
from functions.core.search import SearchProvider, SearchResult
from functions.core.selection import ResultSelector
from functions.llm.client import GeminiClient
from functions.llm.prompts import PromptTemplate

TEMPLATE = PromptTemplate(
    system="Pick best results.",
    user='Query: {query}\n\nResults:\n{results}\n\nReturn JSON: {{"picks": [indices]}}',
)


def _mk_results(n: int) -> list[SearchResult]:
    return [
        SearchResult(
            title=f"Title {i}",
            url=f"https://example.com/{i}",
            snippet=f"snippet {i}",
            provider=SearchProvider.SERPAPI,
        )
        for i in range(n)
    ]


def _client(responses, cache_path: Path) -> GeminiClient:
    it = iter(responses)

    async def call_fn(**kwargs):
        return next(it)

    return GeminiClient(
        model="m", api_key="k", cache=JsonlCache(cache_path), call_fn=call_fn
    )


class TestResultSelector:
    async def test_picks_results_by_index(self, tmp_cache_dir: Path):
        client = _client(
            ['{"picks": [0, 2]}'],
            tmp_cache_dir / "llm.jsonl",
        )
        sel = ResultSelector(client=client, template=TEMPLATE)
        results = _mk_results(4)
        picked = await sel.select("q", results, top_k=2)
        assert [r.url for r in picked] == [
            "https://example.com/0",
            "https://example.com/2",
        ]

    async def test_empty_input_returns_empty_no_llm_call(self, tmp_cache_dir: Path):
        calls = {"n": 0}

        async def call_fn(**kwargs):
            calls["n"] += 1
            return '{"picks": []}'

        client = GeminiClient(
            model="m",
            api_key="k",
            cache=JsonlCache(tmp_cache_dir / "llm.jsonl"),
            call_fn=call_fn,
        )
        sel = ResultSelector(client=client, template=TEMPLATE)
        picked = await sel.select("q", [], top_k=3)
        assert picked == []
        assert calls["n"] == 0

    async def test_truncates_to_top_k(self, tmp_cache_dir: Path):
        client = _client(
            ['{"picks": [0, 1, 2, 3]}'],
            tmp_cache_dir / "llm.jsonl",
        )
        sel = ResultSelector(client=client, template=TEMPLATE)
        results = _mk_results(5)
        picked = await sel.select("q", results, top_k=2)
        assert len(picked) == 2
        assert [r.url for r in picked] == [
            "https://example.com/0",
            "https://example.com/1",
        ]

    async def test_falls_back_to_head_on_bad_response(self, tmp_cache_dir: Path):
        """If LLM returns garbage, we keep the first top_k results as a safe default."""
        client = _client(
            ["not even json"],
            tmp_cache_dir / "llm.jsonl",
        )
        sel = ResultSelector(client=client, template=TEMPLATE)
        results = _mk_results(4)
        picked = await sel.select("q", results, top_k=2)
        assert [r.url for r in picked] == [
            "https://example.com/0",
            "https://example.com/1",
        ]

    async def test_ignores_out_of_range_indices(self, tmp_cache_dir: Path):
        client = _client(
            ['{"picks": [0, 99, 2]}'],
            tmp_cache_dir / "llm.jsonl",
        )
        sel = ResultSelector(client=client, template=TEMPLATE)
        results = _mk_results(3)
        picked = await sel.select("q", results, top_k=3)
        assert [r.url for r in picked] == [
            "https://example.com/0",
            "https://example.com/2",
        ]

    async def test_prompt_enumerates_results(self, tmp_cache_dir: Path):
        captured: dict = {}

        async def call_fn(*, system, user, **kwargs):
            captured["user"] = user
            return '{"picks": [0]}'

        client = GeminiClient(
            model="m",
            api_key="k",
            cache=JsonlCache(tmp_cache_dir / "llm.jsonl"),
            call_fn=call_fn,
        )
        sel = ResultSelector(client=client, template=TEMPLATE)
        results = _mk_results(3)
        await sel.select("the-query", results, top_k=1)
        assert "the-query" in captured["user"]
        assert "[0]" in captured["user"]
        assert "[1]" in captured["user"]
        assert "[2]" in captured["user"]
        assert "https://example.com/0" in captured["user"]
