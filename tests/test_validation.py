"""TDD tests for functions.core.validation."""
from __future__ import annotations

from pathlib import Path

import pytest

from functions.core.cache import JsonlCache
from functions.core.scraping import ScrapedPage
from functions.core.validation import PageJudgement, PageValidator
from functions.llm.client import GeminiClient
from functions.llm.prompts import PromptTemplate


TEMPLATE = PromptTemplate(
    system="You judge research pages.",
    user="Query: {query}\n\nPage content:\n{content}\n\nReturn JSON.",
)


def _client_with_responses(responses: list[str], cache_path: Path) -> GeminiClient:
    it = iter(responses)

    async def call_fn(**kwargs):
        return next(it)

    return GeminiClient(
        model="m",
        api_key="k",
        cache=JsonlCache(cache_path),
        call_fn=call_fn,
    )


class TestPageValidator:
    async def test_drops_pages_below_threshold(self, tmp_cache_dir: Path):
        client = _client_with_responses(
            [
                '{"relevance": 0.9, "trustworthiness": 0.8, "reason": "spot on"}',
                '{"relevance": 0.2, "trustworthiness": 0.4, "reason": "off topic"}',
            ],
            tmp_cache_dir / "llm.jsonl",
        )
        v = PageValidator(
            client=client,
            template=TEMPLATE,
            min_relevance=0.6,
            min_trustworthiness=0.5,
        )
        pages = [
            ScrapedPage(url="https://a", status=200, content="relevant stuff"),
            ScrapedPage(url="https://b", status=200, content="unrelated stuff"),
        ]
        kept, judgements = await v.validate("query", pages)
        assert [p.url for p in kept] == ["https://a"]
        assert len(judgements) == 2
        assert judgements[0].kept is True
        assert judgements[1].kept is False

    async def test_skips_pages_with_no_content(self, tmp_cache_dir: Path):
        # No LLM calls expected for empty-content pages.
        client = _client_with_responses([], tmp_cache_dir / "llm.jsonl")
        v = PageValidator(
            client=client,
            template=TEMPLATE,
            min_relevance=0.5,
            min_trustworthiness=0.5,
        )
        pages = [
            ScrapedPage(url="https://x", status=404, content=""),
            ScrapedPage(url="https://y", status=0, content="", error="boom"),
        ]
        kept, judgements = await v.validate("q", pages)
        assert kept == []
        assert all(j.kept is False for j in judgements)
        assert all(j.reason for j in judgements)

    async def test_malformed_llm_response_treated_as_reject(self, tmp_cache_dir: Path):
        client = _client_with_responses(
            ["definitely not json"],
            tmp_cache_dir / "llm.jsonl",
        )
        v = PageValidator(
            client=client,
            template=TEMPLATE,
            min_relevance=0.5,
            min_trustworthiness=0.5,
        )
        pages = [ScrapedPage(url="https://a", status=200, content="hi")]
        kept, judgements = await v.validate("q", pages)
        assert kept == []
        assert judgements[0].kept is False
        assert "parse" in judgements[0].reason.lower() or "invalid" in judgements[0].reason.lower()

    async def test_keeps_all_when_thresholds_zero(self, tmp_cache_dir: Path):
        client = _client_with_responses(
            [
                '{"relevance": 0.1, "trustworthiness": 0.1, "reason": "meh"}',
                '{"relevance": 0.0, "trustworthiness": 0.0, "reason": "bad"}',
            ],
            tmp_cache_dir / "llm.jsonl",
        )
        v = PageValidator(
            client=client,
            template=TEMPLATE,
            min_relevance=0.0,
            min_trustworthiness=0.0,
        )
        pages = [
            ScrapedPage(url="https://a", status=200, content="x"),
            ScrapedPage(url="https://b", status=200, content="y"),
        ]
        kept, _ = await v.validate("q", pages)
        assert [p.url for p in kept] == ["https://a", "https://b"]

    async def test_empty_input_returns_empty(self, tmp_cache_dir: Path):
        client = _client_with_responses([], tmp_cache_dir / "llm.jsonl")
        v = PageValidator(
            client=client, template=TEMPLATE, min_relevance=0.5, min_trustworthiness=0.5
        )
        kept, judgements = await v.validate("q", [])
        assert kept == []
        assert judgements == []

    async def test_judgement_shape(self, tmp_cache_dir: Path):
        client = _client_with_responses(
            ['{"relevance": 0.8, "trustworthiness": 0.9, "reason": "legit"}'],
            tmp_cache_dir / "llm.jsonl",
        )
        v = PageValidator(
            client=client, template=TEMPLATE, min_relevance=0.5, min_trustworthiness=0.5
        )
        pages = [ScrapedPage(url="https://a", status=200, content="hi")]
        _, judgements = await v.validate("q", pages)
        j = judgements[0]
        assert isinstance(j, PageJudgement)
        assert j.url == "https://a"
        assert j.relevance == 0.8
        assert j.trustworthiness == 0.9
        assert j.reason == "legit"
        assert j.kept is True
