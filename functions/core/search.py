"""Web search with SerpAPI primary + Google CSE fallback.

Hits the HTTP API directly with ``httpx`` rather than the vendor SDKs so the
wire format is visible in tests (mocked via ``respx``) and the module has no
hidden dependencies on vendor-specific client behavior.

Cache: keyed by ``stable_hash({query, top_k, provider_preference})``. A hit
shorts out the entire provider-selection logic.
"""
from __future__ import annotations

import enum
import logging
from dataclasses import asdict, dataclass
from typing import Any

import httpx

from functions.core.cache import JsonlCache
from functions.utils.hashing import stable_hash

log = logging.getLogger(__name__)


class SearchError(RuntimeError):
    """Raised when no provider can satisfy a search."""


class SearchProvider(str, enum.Enum):
    SERPAPI = "serpapi"
    CSE = "cse"


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str
    provider: SearchProvider

    def to_jsonable(self) -> dict[str, Any]:
        d = asdict(self)
        d["provider"] = self.provider.value
        return d

    @classmethod
    def from_jsonable(cls, data: dict[str, Any]) -> "SearchResult":
        return cls(
            title=data["title"],
            url=data["url"],
            snippet=data["snippet"],
            provider=SearchProvider(data["provider"]),
        )


class WebSearcher:
    """SerpAPI primary → Google Custom Search fallback."""

    SERPAPI_URL = "https://serpapi.com/search"
    CSE_URL = "https://www.googleapis.com/customsearch/v1"

    def __init__(
        self,
        *,
        serpapi_key: str | None,
        cse_api_key: str | None,
        cse_cx: str | None,
        cache: JsonlCache | None,
        timeout: float = 15.0,
        serpapi_engine: str = "google",
        serpapi_hl: str = "en",
        serpapi_gl: str = "us",
    ) -> None:
        self._serpapi_key = serpapi_key
        self._cse_api_key = cse_api_key
        self._cse_cx = cse_cx
        self._cache = cache
        self._timeout = timeout
        self._serpapi_engine = serpapi_engine
        self._serpapi_hl = serpapi_hl
        self._serpapi_gl = serpapi_gl

    async def search(self, query: str, *, top_k: int) -> list[SearchResult]:
        cache_key = stable_hash({"query": query, "top_k": top_k, "providers": self._provider_order()})
        if self._cache is not None:
            cached = self._cache.get(cache_key)
            if cached is not None:
                log.info(
                    "search cache hit",
                    extra={"stage": "search", "cache": "hit", "query_len": len(query)},
                )
                return [SearchResult.from_jsonable(r) for r in cached["results"]]

        errors: list[str] = []

        # Try SerpAPI first (if configured).
        if self._serpapi_key:
            try:
                results = await self._serpapi(query, top_k=top_k)
                self._store(cache_key, results)
                return results
            except Exception as e:  # noqa: BLE001 — fall through to next provider
                log.warning(
                    "serpapi failed, trying fallback",
                    extra={"stage": "search", "provider": "serpapi", "exc": repr(e)},
                )
                errors.append(f"serpapi: {e!r}")

        # Fallback to Google CSE.
        if self._cse_api_key and self._cse_cx:
            try:
                results = await self._cse(query, top_k=top_k)
                self._store(cache_key, results)
                return results
            except Exception as e:  # noqa: BLE001
                log.warning(
                    "cse failed",
                    extra={"stage": "search", "provider": "cse", "exc": repr(e)},
                )
                errors.append(f"cse: {e!r}")

        raise SearchError(
            "no search provider succeeded; "
            + (", ".join(errors) if errors else "no providers configured")
        )

    # ---- providers ---------------------------------------------------------

    def _provider_order(self) -> list[str]:
        order = []
        if self._serpapi_key:
            order.append("serpapi")
        if self._cse_api_key and self._cse_cx:
            order.append("cse")
        return order

    async def _serpapi(self, query: str, *, top_k: int) -> list[SearchResult]:
        params = {
            "q": query,
            "engine": self._serpapi_engine,
            "hl": self._serpapi_hl,
            "gl": self._serpapi_gl,
            "api_key": self._serpapi_key,
            "num": top_k,
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            r = await client.get(self.SERPAPI_URL, params=params)
        if r.status_code != 200:
            raise SearchError(f"serpapi HTTP {r.status_code}: {r.text[:200]}")
        payload = r.json()
        organic = payload.get("organic_results") or []
        results = [
            SearchResult(
                title=item.get("title", ""),
                url=item.get("link", ""),
                snippet=item.get("snippet", ""),
                provider=SearchProvider.SERPAPI,
            )
            for item in organic
            if item.get("link")
        ]
        log.info(
            "serpapi ok",
            extra={"stage": "search", "provider": "serpapi", "count": len(results)},
        )
        return results[:top_k]

    async def _cse(self, query: str, *, top_k: int) -> list[SearchResult]:
        num = min(max(top_k, 1), 10)  # CSE caps at 10 per request
        params = {
            "q": query,
            "key": self._cse_api_key,
            "cx": self._cse_cx,
            "num": num,
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            r = await client.get(self.CSE_URL, params=params)
        if r.status_code != 200:
            raise SearchError(f"cse HTTP {r.status_code}: {r.text[:200]}")
        payload = r.json()
        items = payload.get("items") or []
        results = [
            SearchResult(
                title=item.get("title", ""),
                url=item.get("link", ""),
                snippet=item.get("snippet", ""),
                provider=SearchProvider.CSE,
            )
            for item in items
            if item.get("link")
        ]
        log.info(
            "cse ok",
            extra={"stage": "search", "provider": "cse", "count": len(results)},
        )
        return results[:top_k]

    # ---- cache helper ------------------------------------------------------

    def _store(self, key: str, results: list[SearchResult]) -> None:
        if self._cache is None:
            return
        self._cache.put(key, {"results": [r.to_jsonable() for r in results]})
