"""TDD tests for functions.core.scraping."""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from functions.core.cache import JsonlCache
from functions.core.scraping import ScrapedPage, WebScraper
from functions.utils.hashing import stable_hash


HTML_OK = """
<!doctype html>
<html>
<head><title>Example</title></head>
<body>
    <nav>top nav</nav>
    <article>
      <h1>A Title</h1>
      <p>The main body has useful prose.</p>
      <p>Second paragraph with more content.</p>
    </article>
    <footer>copyright</footer>
</body>
</html>
"""


def _fake_extractor(html: str) -> str:
    """Trivial stand-in for trafilatura: returns inner text of <article>."""
    import re

    m = re.search(r"<article>(.*?)</article>", html, flags=re.DOTALL)
    if not m:
        return ""
    inner = re.sub(r"<[^>]+>", " ", m.group(1))
    return " ".join(inner.split())


@pytest.fixture()
def cache(tmp_cache_dir: Path) -> JsonlCache:
    return JsonlCache(tmp_cache_dir / "scrape.jsonl")


class TestWebScraper:
    @respx.mock
    async def test_fetch_extracts_main_content(self, cache):
        respx.get("https://ex.com/page").mock(
            return_value=httpx.Response(200, text=HTML_OK)
        )
        s = WebScraper(cache=cache, extractor=_fake_extractor)
        [page] = await s.scrape_many(["https://ex.com/page"])
        assert isinstance(page, ScrapedPage)
        assert page.url == "https://ex.com/page"
        assert page.status == 200
        assert "main body" in page.content
        assert "copyright" not in page.content

    @respx.mock
    async def test_truncates_to_max_chars(self, cache):
        long_html = "<article>" + ("word " * 5000) + "</article>"
        respx.get("https://ex.com/long").mock(
            return_value=httpx.Response(200, text=long_html)
        )
        s = WebScraper(
            cache=cache, extractor=_fake_extractor, max_chars_per_page=200
        )
        [page] = await s.scrape_many(["https://ex.com/long"])
        assert len(page.content) == 200

    @respx.mock
    async def test_non_200_recorded_without_raise(self, cache):
        respx.get("https://ex.com/404").mock(
            return_value=httpx.Response(404, text="nope")
        )
        s = WebScraper(cache=cache, extractor=_fake_extractor)
        [page] = await s.scrape_many(["https://ex.com/404"])
        assert page.status == 404
        assert page.content == ""

    @respx.mock
    async def test_network_error_recorded_without_raise(self, cache):
        respx.get("https://dead.example/x").mock(
            side_effect=httpx.ConnectError("boom")
        )
        s = WebScraper(cache=cache, extractor=_fake_extractor)
        [page] = await s.scrape_many(["https://dead.example/x"])
        assert page.status == 0
        assert page.error is not None

    @respx.mock
    async def test_cache_hit_short_circuits(self, cache):
        route = respx.get("https://ex.com/page").mock(
            return_value=httpx.Response(200, text=HTML_OK)
        )
        s = WebScraper(cache=cache, extractor=_fake_extractor)
        await s.scrape_many(["https://ex.com/page"])
        first_calls = route.call_count
        await s.scrape_many(["https://ex.com/page"])
        assert route.call_count == first_calls

    @respx.mock
    async def test_concurrent_fetch_handles_mixed_statuses(self, cache):
        respx.get("https://ex.com/ok").mock(
            return_value=httpx.Response(200, text=HTML_OK)
        )
        respx.get("https://ex.com/404").mock(
            return_value=httpx.Response(404, text="")
        )
        s = WebScraper(cache=cache, extractor=_fake_extractor, concurrency=2)
        results = await s.scrape_many(
            ["https://ex.com/ok", "https://ex.com/404"]
        )
        statuses = {r.url: r.status for r in results}
        assert statuses["https://ex.com/ok"] == 200
        assert statuses["https://ex.com/404"] == 404

    @respx.mock
    async def test_malformed_html_yields_empty_content_not_crash(self, cache):
        respx.get("https://ex.com/bad").mock(
            return_value=httpx.Response(200, text="<<< not html >>>")
        )
        s = WebScraper(cache=cache, extractor=_fake_extractor)
        [page] = await s.scrape_many(["https://ex.com/bad"])
        assert page.status == 200
        assert page.content == ""  # fake extractor returns empty when no <article>

    async def test_empty_url_list_returns_empty(self, cache):
        s = WebScraper(cache=cache, extractor=_fake_extractor)
        assert await s.scrape_many([]) == []

    @respx.mock
    async def test_extraction_error_is_not_cached(self, cache):
        """When the extractor raises, the empty page must NOT be cached.

        Caching zero-content pages pins a broken record forever — a later
        run with a working extractor would still serve the empty record
        from cache and silently fail. The scraper must skip the cache
        write on extraction failure.
        """
        url = "https://ex.com/boom"
        route = respx.get(url).mock(return_value=httpx.Response(200, text=HTML_OK))

        def _broken_extractor(html: str) -> str:
            raise RuntimeError("simulated extractor crash")

        s = WebScraper(cache=cache, extractor=_broken_extractor)
        [page] = await s.scrape_many([url])
        assert page.content == ""
        assert page.error is not None

        # Cache must be empty — nothing appended for this URL.
        assert list(cache) == []

        # Second call with a working extractor should refetch and succeed.
        s_ok = WebScraper(cache=cache, extractor=_fake_extractor)
        [page2] = await s_ok.scrape_many([url])
        assert page2.content  # non-empty this time
        assert route.call_count == 2  # refetched

    @respx.mock
    async def test_zombie_cache_entry_is_evicted_and_refetched(
        self, cache, tmp_cache_dir: Path
    ):
        """Pre-existing zombies (status=200, empty content, no error) are
        evicted on read. This self-heals caches populated by older broken
        runs — the next scrape refetches, and the fresh record appended
        shadows the zombie via last-write-wins.
        """
        url = "https://ex.com/zombie"
        max_chars = 20_000  # WebScraper default
        cache_key = stable_hash({"url": url, "max_chars": max_chars})

        # Pre-seed the cache with a zombie record.
        cache.put(cache_key, {
            "url": url,
            "status": 200,
            "content": "",
            "error": None,
        })

        route = respx.get(url).mock(
            return_value=httpx.Response(200, text=HTML_OK)
        )

        s = WebScraper(cache=cache, extractor=_fake_extractor)
        [page] = await s.scrape_many([url])

        assert route.called, "zombie should have triggered a refetch"
        assert page.status == 200
        assert "main body" in page.content

        # Second run should now hit the fresh (non-zombie) record.
        [page2] = await s.scrape_many([url])
        assert route.call_count == 1  # no further HTTP
        assert "main body" in page2.content
