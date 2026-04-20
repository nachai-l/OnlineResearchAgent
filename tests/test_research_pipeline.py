"""TDD tests for functions.core.research_pipeline.

All externals (HTTP, LLM) are injected via the subcomponent constructors,
which means the pipeline itself never touches the network. The critical
invariants we verify:

1. Each stage emits a structured log line with ``stage=<name>``.
2. All five stages run in order and produce a :class:`ResearchResult`.
3. Running the same query twice issues **zero** extra HTTP / LLM calls on
   the second run (everything cache-served from the JSONL files).
"""
from __future__ import annotations

import logging
from pathlib import Path

import httpx
import pytest
import respx

from functions.core.cache import JsonlCache
from functions.core.research_pipeline import (
    ResearchPipeline,
    ResearchResult,
)
from functions.core.scraping import WebScraper
from functions.core.search import WebSearcher
from functions.core.selection import ResultSelector
from functions.core.summarization import Summarizer
from functions.core.validation import PageValidator
from functions.llm.client import GeminiClient
from functions.llm.prompts import PromptTemplate

SELECT_TEMPLATE = PromptTemplate(
    system="pick",
    user="Q: {query}\n\n{results}",
)
VALIDATE_TEMPLATE = PromptTemplate(
    system="judge",
    user="Q: {query}\n\n{content}",
)
SUMMARIZE_TEMPLATE = PromptTemplate(
    system="sum",
    user="Q: {query}\n\n{sources}",
)


SERPAPI_RESPONSE = {
    "organic_results": [
        {"title": "T0", "link": "https://site.a/0", "snippet": "s0"},
        {"title": "T1", "link": "https://site.b/1", "snippet": "s1"},
        {"title": "T2", "link": "https://site.c/2", "snippet": "s2"},
    ]
}

HTML_A = "<html><body><p>content A about the query</p></body></html>"
HTML_B = "<html><body><p>content B about the query</p></body></html>"


class _LlmRecorder:
    """Callable that records each LLM invocation and returns canned replies."""

    def __init__(self, replies: list[str]) -> None:
        self._replies = list(replies)
        self._idx = 0
        self.calls: list[dict] = []

    async def __call__(self, **kwargs) -> str:
        self.calls.append(kwargs)
        if self._idx >= len(self._replies):
            raise AssertionError(
                f"LLM called more times than expected; kwargs={kwargs}"
            )
        reply = self._replies[self._idx]
        self._idx += 1
        return reply


def _build_pipeline(
    *,
    cache_dir: Path,
    llm_calls: _LlmRecorder,
) -> ResearchPipeline:
    search_cache = JsonlCache(cache_dir / "search.jsonl")
    scrape_cache = JsonlCache(cache_dir / "scrape.jsonl")
    llm_cache = JsonlCache(cache_dir / "llm.jsonl")

    client = GeminiClient(
        model="m",
        api_key="k",
        cache=llm_cache,
        call_fn=llm_calls,
    )
    searcher = WebSearcher(
        serpapi_key="serp",
        cse_api_key=None,
        cse_cx=None,
        cache=search_cache,
        timeout=5.0,
    )
    selector = ResultSelector(client=client, template=SELECT_TEMPLATE)
    scraper = WebScraper(
        cache=scrape_cache,
        extractor=lambda html: "EXTRACTED:" + html,
        timeout=5.0,
        max_chars_per_page=1000,
        concurrency=2,
        user_agent="test/0.1",
    )
    validator = PageValidator(
        client=client,
        template=VALIDATE_TEMPLATE,
        min_relevance=0.5,
        min_trustworthiness=0.5,
    )
    summarizer = Summarizer(client=client, template=SUMMARIZE_TEMPLATE)

    return ResearchPipeline(
        searcher=searcher,
        selector=selector,
        scraper=scraper,
        validator=validator,
        summarizer=summarizer,
        top_k_search=3,
        top_k_to_scrape=2,
    )


def _mount_http(respx_mock: respx.Router) -> None:
    respx_mock.get("https://serpapi.com/search").mock(
        return_value=httpx.Response(200, json=SERPAPI_RESPONSE)
    )
    respx_mock.get("https://site.a/0").mock(return_value=httpx.Response(200, text=HTML_A))
    respx_mock.get("https://site.b/1").mock(return_value=httpx.Response(200, text=HTML_B))
    respx_mock.get("https://site.c/2").mock(
        return_value=httpx.Response(200, text="<html></html>")
    )


# Canned LLM replies in stage order:
# 1. selection → pick indices 0 and 1
# 2. validator for page-0 → keep
# 3. validator for page-1 → keep
# 4. summarizer → markdown
HAPPY_REPLIES = [
    '{"picks": [0, 1]}',
    '{"relevance": 0.9, "trustworthiness": 0.9, "reason": "ok"}',
    '{"relevance": 0.8, "trustworthiness": 0.8, "reason": "ok"}',
    "Summary with [1] and [2] citations.",
]


class TestResearchPipeline:
    @respx.mock
    async def test_returns_research_result_end_to_end(
        self, tmp_cache_dir: Path
    ) -> None:
        _mount_http(respx.mock)
        llm = _LlmRecorder(HAPPY_REPLIES)
        pipe = _build_pipeline(cache_dir=tmp_cache_dir, llm_calls=llm)

        result = await pipe.run("what is A2A")

        assert isinstance(result, ResearchResult)
        assert result.query == "what is A2A"
        assert "[1]" in result.summary_markdown
        assert result.sources == ["https://site.a/0", "https://site.b/1"]
        assert result.kept_count == 2
        assert result.scraped_count == 2
        assert result.search_results_count == 3
        # exactly 4 LLM calls in the happy path
        assert len(llm.calls) == 4

    @respx.mock
    async def test_each_stage_logs_structured_line(
        self, tmp_cache_dir: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        _mount_http(respx.mock)
        llm = _LlmRecorder(HAPPY_REPLIES)
        pipe = _build_pipeline(cache_dir=tmp_cache_dir, llm_calls=llm)

        caplog.set_level(logging.INFO)
        await pipe.run("q1")

        stages_seen: set[str] = set()
        for rec in caplog.records:
            stage = getattr(rec, "stage", None)
            if stage:
                stages_seen.add(stage)
        for required in {"search", "select", "scrape", "validate", "summarize"}:
            assert required in stages_seen, f"missing stage log: {required}"

    @respx.mock
    async def test_second_run_is_fully_cached(
        self, tmp_cache_dir: Path
    ) -> None:
        """Running the same query twice must issue 0 extra HTTP or LLM calls."""
        _mount_http(respx.mock)
        llm = _LlmRecorder(HAPPY_REPLIES)
        pipe = _build_pipeline(cache_dir=tmp_cache_dir, llm_calls=llm)

        # First run populates all caches.
        await pipe.run("cached query")
        first_http_calls = respx.mock.calls.call_count
        first_llm_calls = len(llm.calls)
        assert first_http_calls >= 1
        assert first_llm_calls == 4

        # Second run: no new HTTP, no new LLM.
        result2 = await pipe.run("cached query")
        assert respx.mock.calls.call_count == first_http_calls
        assert len(llm.calls) == first_llm_calls
        # Result still looks the same.
        assert result2.sources == ["https://site.a/0", "https://site.b/1"]
        assert "[1]" in result2.summary_markdown

    @respx.mock
    async def test_empty_search_results_short_circuits(
        self, tmp_cache_dir: Path
    ) -> None:
        respx.mock.get("https://serpapi.com/search").mock(
            return_value=httpx.Response(200, json={"organic_results": []})
        )
        llm = _LlmRecorder([])  # zero replies — any LLM call would raise
        pipe = _build_pipeline(cache_dir=tmp_cache_dir, llm_calls=llm)

        result = await pipe.run("nothing relevant")

        assert result.search_results_count == 0
        assert result.sources == []
        assert result.kept_count == 0
        assert len(llm.calls) == 0
        assert result.summary_markdown  # graceful placeholder
