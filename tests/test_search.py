"""TDD tests for functions.core.search."""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from functions.core.cache import JsonlCache
from functions.core.search import (
    SearchError,
    SearchProvider,
    SearchResult,
    WebSearcher,
)


SERPAPI_OK = {
    "organic_results": [
        {"title": "A2A Protocol", "link": "https://a2a.example/spec", "snippet": "Spec..."},
        {"title": "A2A Intro", "link": "https://a2a.example/intro", "snippet": "Intro..."},
    ]
}

CSE_OK = {
    "items": [
        {"title": "CSE Result 1", "link": "https://cse.example/1", "snippet": "from cse"},
    ]
}


@pytest.fixture()
def cache(tmp_cache_dir: Path) -> JsonlCache:
    return JsonlCache(tmp_cache_dir / "search.jsonl")


class TestWebSearcher:
    @respx.mock
    async def test_serpapi_happy_path(self, cache):
        route = respx.get("https://serpapi.com/search").mock(
            return_value=httpx.Response(200, json=SERPAPI_OK)
        )
        s = WebSearcher(
            serpapi_key="sp-k",
            cse_api_key=None,
            cse_cx=None,
            cache=cache,
        )
        results = await s.search("a2a protocol", top_k=2)
        assert route.called
        assert len(results) == 2
        assert all(isinstance(r, SearchResult) for r in results)
        assert results[0].url == "https://a2a.example/spec"
        assert results[0].provider == SearchProvider.SERPAPI

    @respx.mock
    async def test_cache_hit_short_circuits_network(self, cache):
        route = respx.get("https://serpapi.com/search").mock(
            return_value=httpx.Response(200, json=SERPAPI_OK)
        )
        s = WebSearcher(serpapi_key="sp-k", cse_api_key=None, cse_cx=None, cache=cache)
        await s.search("a2a protocol", top_k=2)
        first_call_count = route.call_count
        await s.search("a2a protocol", top_k=2)
        assert route.call_count == first_call_count  # no additional call

    @respx.mock
    async def test_falls_back_to_cse_on_serpapi_error(self, cache):
        respx.get("https://serpapi.com/search").mock(
            return_value=httpx.Response(500, json={"error": "boom"})
        )
        cse_route = respx.get("https://www.googleapis.com/customsearch/v1").mock(
            return_value=httpx.Response(200, json=CSE_OK)
        )
        s = WebSearcher(
            serpapi_key="sp-k",
            cse_api_key="cse-k",
            cse_cx="cx-id",
            cache=cache,
        )
        results = await s.search("query", top_k=1)
        assert cse_route.called
        assert len(results) == 1
        assert results[0].provider == SearchProvider.CSE
        assert results[0].url == "https://cse.example/1"

    @respx.mock
    async def test_falls_back_to_cse_when_serpapi_key_missing(self, cache):
        cse_route = respx.get("https://www.googleapis.com/customsearch/v1").mock(
            return_value=httpx.Response(200, json=CSE_OK)
        )
        s = WebSearcher(
            serpapi_key=None,
            cse_api_key="cse-k",
            cse_cx="cx-id",
            cache=cache,
        )
        results = await s.search("q", top_k=1)
        assert cse_route.called
        assert results[0].provider == SearchProvider.CSE

    async def test_no_providers_raises(self, cache):
        s = WebSearcher(
            serpapi_key=None,
            cse_api_key=None,
            cse_cx=None,
            cache=cache,
        )
        with pytest.raises(SearchError):
            await s.search("q", top_k=3)

    @respx.mock
    async def test_both_providers_fail_raises(self, cache):
        respx.get("https://serpapi.com/search").mock(
            return_value=httpx.Response(500, json={})
        )
        respx.get("https://www.googleapis.com/customsearch/v1").mock(
            return_value=httpx.Response(500, json={})
        )
        s = WebSearcher(
            serpapi_key="sp-k",
            cse_api_key="cse-k",
            cse_cx="cx-id",
            cache=cache,
        )
        with pytest.raises(SearchError):
            await s.search("q", top_k=2)

    @respx.mock
    async def test_top_k_truncates_results(self, cache):
        respx.get("https://serpapi.com/search").mock(
            return_value=httpx.Response(200, json=SERPAPI_OK)
        )
        s = WebSearcher(serpapi_key="sp-k", cse_api_key=None, cse_cx=None, cache=cache)
        results = await s.search("q", top_k=1)
        assert len(results) == 1

    @respx.mock
    async def test_cache_key_differs_by_query(self, cache):
        respx.get("https://serpapi.com/search").mock(
            return_value=httpx.Response(200, json=SERPAPI_OK)
        )
        s = WebSearcher(serpapi_key="sp-k", cse_api_key=None, cse_cx=None, cache=cache)
        await s.search("query one", top_k=2)
        await s.search("query two", top_k=2)
        # Two cache entries in the JSONL.
        records = list(cache)
        assert len(records) == 2
