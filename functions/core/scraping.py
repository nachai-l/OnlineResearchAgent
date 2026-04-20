"""Concurrent async scraping + main-content extraction.

Fetches are issued through ``httpx.AsyncClient`` gated by a
``asyncio.Semaphore(concurrency)`` so we never overload a target host or our
own event loop. Content extraction is injected (``extractor``) so tests can
use a trivial stand-in; production wires ``trafilatura.extract``.

Errors never escape :meth:`WebScraper.scrape_many` — failed fetches produce a
:class:`ScrapedPage` with ``status=0`` (network error) or the actual HTTP
status, plus ``content=""`` and an ``error`` string. Downstream stages can
simply ignore these records rather than wrapping every call in try/except.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, dataclass
from typing import Callable

import httpx

from functions.core.cache import JsonlCache
from functions.utils.hashing import stable_hash

log = logging.getLogger(__name__)

Extractor = Callable[[str], str]


@dataclass(frozen=True)
class ScrapedPage:
    url: str
    status: int
    content: str
    error: str | None = None

    def to_jsonable(self) -> dict:
        return asdict(self)

    @classmethod
    def from_jsonable(cls, data: dict) -> "ScrapedPage":
        return cls(
            url=data["url"],
            status=data["status"],
            content=data["content"],
            error=data.get("error"),
        )


def default_extractor(html: str) -> str:
    """Production extractor backed by trafilatura.

    Imported lazily so unit tests don't need the package installed.
    """
    import trafilatura  # type: ignore[import-untyped]

    return trafilatura.extract(html, include_comments=False, include_tables=False) or ""


class WebScraper:
    """Async concurrent scraper with JSONL cache + injectable extractor."""

    def __init__(
        self,
        *,
        cache: JsonlCache | None,
        extractor: Extractor,
        timeout: float = 20.0,
        max_chars_per_page: int = 20_000,
        concurrency: int = 4,
        user_agent: str = "A2A-WebResearch/0.1",
    ) -> None:
        self._cache = cache
        self._extractor = extractor
        self._timeout = timeout
        self._max_chars = max_chars_per_page
        self._concurrency = concurrency
        self._user_agent = user_agent

    async def scrape_many(self, urls: list[str]) -> list[ScrapedPage]:
        if not urls:
            return []

        sem = asyncio.Semaphore(self._concurrency)
        headers = {"User-Agent": self._user_agent}
        async with httpx.AsyncClient(
            timeout=self._timeout, headers=headers, follow_redirects=True
        ) as client:
            tasks = [self._scrape_one(client, sem, url) for url in urls]
            return await asyncio.gather(*tasks)

    async def _scrape_one(
        self,
        client: httpx.AsyncClient,
        sem: asyncio.Semaphore,
        url: str,
    ) -> ScrapedPage:
        cache_key = stable_hash({"url": url, "max_chars": self._max_chars})
        if self._cache is not None:
            cached = self._cache.get(cache_key)
            if cached is not None:
                page = ScrapedPage.from_jsonable(cached)
                if _is_zombie(page):
                    # Evict: fall through to refetch. The fresh record
                    # appended later will shadow this one via last-write-wins.
                    log.info(
                        "scrape cache zombie evicted",
                        extra={"stage": "scrape", "cache": "evict", "url": url},
                    )
                else:
                    log.info(
                        "scrape cache hit",
                        extra={"stage": "scrape", "cache": "hit", "url": url},
                    )
                    return page

        async with sem:
            try:
                response = await client.get(url)
            except Exception as e:  # noqa: BLE001 — every error surfaces as a page
                log.warning(
                    "scrape network error",
                    extra={"stage": "scrape", "url": url, "exc": repr(e)},
                )
                page = ScrapedPage(url=url, status=0, content="", error=repr(e))
                self._store(cache_key, page)
                return page

        if response.status_code != 200:
            log.info(
                "scrape non-200",
                extra={"stage": "scrape", "url": url, "status": response.status_code},
            )
            page = ScrapedPage(url=url, status=response.status_code, content="")
            self._store(cache_key, page)
            return page

        content = ""
        extraction_failed = False
        try:
            extracted = self._extractor(response.text) or ""
            content = extracted[: self._max_chars]
        except Exception as e:  # noqa: BLE001
            extraction_failed = True
            log.warning(
                "scrape extraction failed",
                extra={"stage": "scrape", "url": url, "exc": repr(e)},
            )

        page = ScrapedPage(
            url=url,
            status=response.status_code,
            content=content,
            error=("extraction failed" if extraction_failed else None),
        )
        log.info(
            "scrape ok",
            extra={"stage": "scrape", "url": url, "chars": len(content)},
        )
        # Intentionally do NOT cache extraction failures — those usually mean
        # the extractor itself is broken (missing dep, parser crash). Caching
        # the empty result would pin a bad record forever. Real empty pages
        # (extractor returned "") still get cached so we don't refetch them.
        if not extraction_failed:
            self._store(cache_key, page)
        return page

    def _store(self, key: str, page: ScrapedPage) -> None:
        if self._cache is not None:
            self._cache.put(key, page.to_jsonable())


def _is_zombie(page: ScrapedPage) -> bool:
    """Detect cached scrape records that should be refetched rather than served.

    A "zombie" is a record with ``status=200`` but no content and no error
    string. That shape can only come from an earlier run where extraction
    silently produced empty output (missing dep, parser crash). The reader
    treats these as misses so the scraper refetches, appending a fresh
    record that shadows the zombie via the JSONL cache's last-write-wins
    semantics. No explicit eviction pass is needed.
    """
    return (
        page.status == 200
        and not page.content
        and page.error is None
    )
