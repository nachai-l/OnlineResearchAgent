"""LLM-based validity/relevance gate.

For each scraped page we ask Gemini to score:
- ``relevance``      — does the page actually answer the query? ``[0, 1]``
- ``trustworthiness`` — does the source look authoritative? ``[0, 1]``

Pages below either threshold are dropped. Empty / error pages short-circuit
without burning a Gemini call.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from pydantic import BaseModel, Field

from functions.core.scraping import ScrapedPage
from functions.llm.client import GeminiClient, LlmCallError
from functions.llm.prompts import PromptTemplate

log = logging.getLogger(__name__)


class _ValidationResponse(BaseModel):
    """Expected shape of the validation prompt's JSON reply.

    ``Field(ge=0, le=1)`` lets pydantic do the clamping + bounds check
    in one pass; out-of-range scores fail validation and trigger the LLM
    client's retry envelope rather than being silently clamped to 0/1.
    """

    relevance: float = Field(ge=0.0, le=1.0)
    trustworthiness: float = Field(ge=0.0, le=1.0)
    reason: str = ""


@dataclass(frozen=True)
class PageJudgement:
    url: str
    relevance: float
    trustworthiness: float
    reason: str
    kept: bool


class PageValidator:
    def __init__(
        self,
        *,
        client: GeminiClient,
        template: PromptTemplate,
        min_relevance: float,
        min_trustworthiness: float,
    ) -> None:
        self._client = client
        self._template = template
        self._min_rel = min_relevance
        self._min_trust = min_trustworthiness

    async def validate(
        self,
        query: str,
        pages: list[ScrapedPage],
    ) -> tuple[list[ScrapedPage], list[PageJudgement]]:
        if not pages:
            return [], []

        tasks = [self._judge_one(query, p) for p in pages]
        judgements = await asyncio.gather(*tasks)
        kept_pages = [
            p for p, j in zip(pages, judgements, strict=True) if j.kept
        ]
        log.info(
            "validation done",
            extra={
                "stage": "validate",
                "total": len(pages),
                "kept": len(kept_pages),
            },
        )
        return kept_pages, list(judgements)

    async def _judge_one(self, query: str, page: ScrapedPage) -> PageJudgement:
        # Short-circuit: empty content can't be usefully judged.
        if not page.content or page.status != 200:
            return PageJudgement(
                url=page.url,
                relevance=0.0,
                trustworthiness=0.0,
                reason=f"no content to judge (status={page.status})",
                kept=False,
            )

        user = self._template.render(query=query, content=page.content)
        try:
            response = await self._client.generate_model(
                response_model=_ValidationResponse,
                system=self._template.system,
                user=user,
            )
        except LlmCallError as e:
            # Retries already exhausted upstream — this page gets dropped.
            log.warning(
                "validator parse failed",
                extra={"stage": "validate", "url": page.url, "exc": repr(e)},
            )
            return PageJudgement(
                url=page.url,
                relevance=0.0,
                trustworthiness=0.0,
                reason=f"invalid judge response: {e!r}",
                kept=False,
            )

        kept = (
            response.relevance >= self._min_rel
            and response.trustworthiness >= self._min_trust
        )
        return PageJudgement(
            url=page.url,
            relevance=response.relevance,
            trustworthiness=response.trustworthiness,
            reason=response.reason,
            kept=kept,
        )
