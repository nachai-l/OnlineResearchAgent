"""LLM-based search-result selection.

Given a ranked list of :class:`SearchResult` records, ask Gemini to pick the
indices most worth scraping for the query. Out-of-range picks are ignored;
duplicates are collapsed to preserve order. If the model returns garbage we
fall back to the first ``top_k`` results so the pipeline still makes progress.
"""
from __future__ import annotations

import logging

from pydantic import BaseModel

from functions.core.search import SearchResult
from functions.llm.client import GeminiClient, LlmCallError
from functions.llm.prompts import PromptTemplate

log = logging.getLogger(__name__)


class _SelectionResponse(BaseModel):
    """Expected shape of the result-selection prompt's JSON reply.

    Pydantic rejects non-list ``picks`` or non-int items up-front, so the
    only fallback path left in ``ResultSelector.select`` is "no indices
    survived out-of-range filtering" — a model that answered the wrong
    question is retried by the LLM client before reaching this layer.
    """

    picks: list[int]


class ResultSelector:
    def __init__(
        self,
        *,
        client: GeminiClient,
        template: PromptTemplate,
    ) -> None:
        self._client = client
        self._template = template

    async def select(
        self,
        query: str,
        results: list[SearchResult],
        *,
        top_k: int,
    ) -> list[SearchResult]:
        if not results or top_k <= 0:
            return []

        results_block = _render_results_block(results)
        user = self._template.render(query=query, results=results_block)

        try:
            response = await self._client.generate_model(
                response_model=_SelectionResponse,
                system=self._template.system,
                user=user,
            )
            picked = _materialize(response.picks, results)
            if not picked:
                raise LlmCallError("no valid indices in 'picks'")
        except LlmCallError as e:
            log.warning(
                "selection fallback to head",
                extra={"stage": "select", "exc": repr(e), "top_k": top_k},
            )
            picked = list(results[:top_k])

        truncated = picked[:top_k]
        log.info(
            "selection done",
            extra={
                "stage": "select",
                "candidates": len(results),
                "picked": len(truncated),
            },
        )
        return truncated


def _render_results_block(results: list[SearchResult]) -> str:
    lines: list[str] = []
    for i, r in enumerate(results):
        lines.append(f"[{i}] {r.title}")
        lines.append(f"    {r.url}")
        if r.snippet:
            lines.append(f"    {r.snippet}")
        lines.append("")
    return "\n".join(lines).rstrip()


def _materialize(
    picks: list[int],
    results: list[SearchResult],
) -> list[SearchResult]:
    """Keep only in-range, first-occurrence picks, preserving order.

    Pydantic already guaranteed the items are ints, so this loop only
    enforces domain rules — range + dedupe.
    """
    seen: set[int] = set()
    out: list[SearchResult] = []
    for idx in picks:
        if idx < 0 or idx >= len(results) or idx in seen:
            continue
        seen.add(idx)
        out.append(results[idx])
    return out
