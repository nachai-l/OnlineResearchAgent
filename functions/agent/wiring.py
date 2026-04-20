"""Shared wiring: build a :class:`ResearchPipeline` from :class:`Settings`.

Both ``scripts/run_local.py`` and ``scripts/run_server.py`` need the same
pipeline (same caches, same prompts, same LLM client). Instead of
duplicating that bootstrap, they both import :func:`build_pipeline`.

No tests target this module directly — every subcomponent has its own
suite, and the A2A skill tests exercise the whole pipeline end-to-end via
a stub. This file is a seam for the real entry points, not new logic.
"""
from __future__ import annotations

from functions.core.cache import JsonlCache
from functions.core.research_pipeline import ResearchPipeline
from functions.core.scraping import WebScraper, default_extractor
from functions.core.search import WebSearcher
from functions.core.selection import ResultSelector
from functions.core.summarization import Summarizer
from functions.core.validation import PageValidator
from functions.llm.client import (
    CallFn,
    GeminiClient,
    default_call_fn,
    default_litellm_call_fn,
)
from functions.llm.prompts import load_prompt
from functions.utils.config import Settings
from functions.utils.paths import PROMPTS_DIR, ensure_dir


def _build_call_fn(settings: Settings) -> CallFn:
    """Pick the right LLM transport based on ``settings.llm.backend``.

    "gemini" → google-genai SDK keyed by ``GEMINI_API_KEY``.
    "litellm" → OpenAI-shaped POST to ``litellm_base_url`` keyed by
    ``LITELLM_API_KEY``. Missing credentials raise immediately rather than
    lazy-failing deep in the first request.
    """
    backend = settings.llm.backend
    if backend == "gemini":
        if not settings.credentials.gemini_api_key:
            raise ValueError(
                "llm.backend is 'gemini' but GEMINI_API_KEY is not set"
            )
        return default_call_fn(settings.credentials.gemini_api_key)
    if backend == "litellm":
        if not settings.llm.litellm_base_url:
            raise ValueError(
                "llm.backend is 'litellm' but llm.litellm_base_url is not set"
            )
        if not settings.credentials.litellm_api_key:
            raise ValueError(
                "llm.backend is 'litellm' but LITELLM_API_KEY is not set"
            )
        return default_litellm_call_fn(
            base_url=settings.llm.litellm_base_url,
            api_key=settings.credentials.litellm_api_key,
        )
    raise ValueError(f"unknown llm.backend: {backend!r}")


def build_pipeline(settings: Settings) -> ResearchPipeline:
    """Construct the full pipeline from ``Settings``."""
    # Ensure cache directory exists.
    ensure_dir(settings.cache_path("search").parent)

    search_cache = JsonlCache(settings.cache_path("search")) if settings.cache.enabled else None
    scrape_cache = JsonlCache(settings.cache_path("scrape")) if settings.cache.enabled else None
    llm_cache = JsonlCache(settings.cache_path("llm")) if settings.cache.enabled else None

    call_fn = _build_call_fn(settings)
    llm_client = GeminiClient(
        model=settings.llm.model,
        # api_key is only consulted when no call_fn is injected — we always
        # inject ours now, so pass None to make the precedence explicit.
        api_key=None,
        call_fn=call_fn,
        cache=llm_cache,
        default_temperature=settings.llm.temperature,
        default_max_output_tokens=settings.llm.max_output_tokens,
        default_timeout=settings.llm.request_timeout_seconds,
        retries=settings.llm.retries,
    )

    select_tpl = load_prompt(PROMPTS_DIR / "result_selection.yaml")
    validate_tpl = load_prompt(PROMPTS_DIR / "validation.yaml")
    summarize_tpl = load_prompt(PROMPTS_DIR / "summarization.yaml")

    searcher = WebSearcher(
        serpapi_key=settings.credentials.serpapi_api_key,
        cse_api_key=settings.credentials.google_cse_api_key,
        cse_cx=settings.credentials.google_cse_cx,
        cache=search_cache,
        timeout=settings.search.timeout_seconds,
        serpapi_engine=settings.search.serpapi_engine,
        serpapi_hl=settings.search.serpapi_hl,
        serpapi_gl=settings.search.serpapi_gl,
    )
    selector = ResultSelector(client=llm_client, template=select_tpl)
    scraper = WebScraper(
        cache=scrape_cache,
        extractor=default_extractor,
        timeout=settings.scrape.timeout_seconds,
        max_chars_per_page=settings.scrape.max_chars_per_page,
        concurrency=settings.scrape.concurrency,
        user_agent=settings.scrape.user_agent,
    )
    validator = PageValidator(
        client=llm_client,
        template=validate_tpl,
        min_relevance=settings.validation.min_relevance,
        min_trustworthiness=settings.validation.min_trustworthiness,
    )
    summarizer = Summarizer(client=llm_client, template=summarize_tpl)

    return ResearchPipeline(
        searcher=searcher,
        selector=selector,
        scraper=scraper,
        validator=validator,
        summarizer=summarizer,
        top_k_search=settings.search.top_k_results,
        top_k_to_scrape=settings.search.top_k_to_scrape,
    )
