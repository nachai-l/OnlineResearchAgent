"""LLM-based search-result selection.

Given a ranked list of :class:`SearchResult` records, ask Gemini to pick the
indices most worth scraping for the query. Out-of-range picks are ignored;
duplicates are collapsed to preserve order. If the model returns garbage we
fall back to the first ``top_k`` results so the pipeline still makes progress.
"""
from __future__ import annotations

import logging
from typing import Iterable

from functions.core.search import SearchResult
from functions.llm.client import GeminiClient, LlmCallError
from functions.llm.prompts import PromptTemplate

log = logging.getLogger(__name__)


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
            raw = await self._client.generate_json(
                system=self._template.system, user=user
            )
            picks_raw = raw.get("picks", [])
            if not isinstance(picks_raw, Iterable) or isinstance(picks_raw, (str, bytes)):
                raise LlmCallError("'picks' is not a list")
            picked = _materialize(picks_raw, results)
            if not picked:
                raise LlmCallError("no valid indices in 'picks'")
        except (LlmCallError, TypeError, ValueError) as e:
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
    picks_raw: Iterable,
    results: list[SearchResult],
) -> list[SearchResult]:
    seen: set[int] = set()
    out: list[SearchResult] = []
    for item in picks_raw:
        try:
            idx = int(item)
        except (TypeError, ValueError):
            continue
        if idx < 0 or idx >= len(results) or idx in seen:
            continue
        seen.add(idx)
        out.append(results[idx])
    return out
