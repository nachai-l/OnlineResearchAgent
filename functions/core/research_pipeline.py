"""End-to-end web research orchestration.

Wires together the five stages in order:

    search → select → scrape → validate → summarize

Every subcomponent is injected, so the pipeline itself has no direct
dependency on HTTP, the Gemini SDK, or any specific cache backing. That's
what lets the unit tests run entirely offline and also what lets this same
pipeline be plugged into an A2A skill, a CLI script, or a catalogue-style
``async def web_research(query: str, **kwargs) -> str`` entry point.

The pipeline never raises on partial failure — a zero-result search, a
network-errored scrape, or a page the validator rejects all produce a
well-formed :class:`ResearchResult` with a graceful summary. Callers can
decide whether to retry or surface the degraded result as-is.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from functions.core.scraping import WebScraper
from functions.core.search import WebSearcher
from functions.core.selection import ResultSelector
from functions.core.summarization import Summarizer, SummaryResult
from functions.core.validation import PageJudgement, PageValidator

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResearchResult:
    query: str
    summary_markdown: str
    sources: list[str]
    judgements: list[PageJudgement] = field(default_factory=list)
    search_results_count: int = 0
    scraped_count: int = 0
    kept_count: int = 0


class ResearchPipeline:
    def __init__(
        self,
        *,
        searcher: WebSearcher,
        selector: ResultSelector,
        scraper: WebScraper,
        validator: PageValidator,
        summarizer: Summarizer,
        top_k_search: int,
        top_k_to_scrape: int,
    ) -> None:
        self._searcher = searcher
        self._selector = selector
        self._scraper = scraper
        self._validator = validator
        self._summarizer = summarizer
        self._top_k_search = top_k_search
        self._top_k_to_scrape = top_k_to_scrape

    async def run(self, query: str) -> ResearchResult:
        log.info(
            "pipeline start",
            extra={"stage": "pipeline", "query": query},
        )

        # 1. Search
        search_results = await self._searcher.search(
            query, top_k=self._top_k_search
        )
        log.info(
            "search done",
            extra={"stage": "search", "count": len(search_results)},
        )
        if not search_results:
            summary = await self._summarizer.summarize(query, [])
            return self._finalize(query, summary, [], 0, 0, 0)

        # 2. Select
        chosen = await self._selector.select(
            query, search_results, top_k=self._top_k_to_scrape
        )
        if not chosen:
            summary = await self._summarizer.summarize(query, [])
            return self._finalize(query, summary, [], len(search_results), 0, 0)

        # 3. Scrape
        pages = await self._scraper.scrape_many([r.url for r in chosen])

        # 4. Validate
        kept_pages, judgements = await self._validator.validate(query, pages)

        # 5. Summarize
        summary = await self._summarizer.summarize(query, kept_pages)

        return self._finalize(
            query,
            summary,
            judgements,
            search_results_count=len(search_results),
            scraped_count=len(pages),
            kept_count=len(kept_pages),
        )

    # ---- internals --------------------------------------------------------

    def _finalize(
        self,
        query: str,
        summary: SummaryResult,
        judgements: list[PageJudgement],
        search_results_count: int,
        scraped_count: int,
        kept_count: int,
    ) -> ResearchResult:
        log.info(
            "pipeline done",
            extra={
                "stage": "pipeline",
                "search_results": search_results_count,
                "scraped": scraped_count,
                "kept": kept_count,
                "sources": len(summary.sources),
            },
        )
        return ResearchResult(
            query=query,
            summary_markdown=summary.markdown,
            sources=list(summary.sources),
            judgements=list(judgements),
            search_results_count=search_results_count,
            scraped_count=scraped_count,
            kept_count=kept_count,
        )
