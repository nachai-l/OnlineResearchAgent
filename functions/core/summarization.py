"""LLM-based grounded summarization.

Takes the surviving ``ScrapedPage`` list and asks Gemini to produce a markdown
summary with inline ``[n]`` citations. When there are no pages, returns a
graceful "no sources" placeholder without ever calling the LLM.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from functions.core.scraping import ScrapedPage
from functions.llm.client import GeminiClient
from functions.llm.prompts import PromptTemplate

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SummaryResult:
    markdown: str
    sources: list[str]


class Summarizer:
    def __init__(
        self,
        *,
        client: GeminiClient,
        template: PromptTemplate,
    ) -> None:
        self._client = client
        self._template = template

    async def summarize(
        self, query: str, pages: list[ScrapedPage]
    ) -> SummaryResult:
        if not pages:
            log.info(
                "summarize no-input",
                extra={"stage": "summarize", "pages": 0},
            )
            return SummaryResult(
                markdown=(
                    "_No sources survived validation for this query._\n\n"
                    "Try rephrasing or broadening the search."
                ),
                sources=[],
            )

        sources_block = _render_sources_block(pages)
        user = self._template.render(query=query, sources=sources_block)
        markdown = await self._client.generate(
            system=self._template.system, user=user
        )
        log.info(
            "summarize ok",
            extra={"stage": "summarize", "pages": len(pages), "chars": len(markdown)},
        )
        return SummaryResult(
            markdown=markdown.strip(),
            sources=[p.url for p in pages],
        )


def _render_sources_block(pages: list[ScrapedPage]) -> str:
    lines: list[str] = []
    for idx, page in enumerate(pages, start=1):
        lines.append(f"[{idx}] {page.url}")
        lines.append(page.content)
        lines.append("")  # blank separator
    return "\n".join(lines).rstrip()
