"""TDD tests for functions.core.summarization."""
from __future__ import annotations

from pathlib import Path

from functions.core.cache import JsonlCache
from functions.core.scraping import ScrapedPage
from functions.core.summarization import Summarizer, SummaryResult
from functions.llm.client import GeminiClient
from functions.llm.prompts import PromptTemplate

TEMPLATE = PromptTemplate(
    system="Summarize with citations.",
    user="Query: {query}\n\nSources:\n{sources}\n\nReturn markdown.",
)


def _client(responses, cache_path: Path) -> GeminiClient:
    it = iter(responses)

    async def call_fn(**kwargs):
        return next(it)

    return GeminiClient(model="m", api_key="k", cache=JsonlCache(cache_path), call_fn=call_fn)


class TestSummarizer:
    async def test_returns_markdown_with_citations(self, tmp_cache_dir: Path):
        client = _client(
            ["The A2A protocol is defined by Google [1][2]."],
            tmp_cache_dir / "llm.jsonl",
        )
        s = Summarizer(client=client, template=TEMPLATE)
        pages = [
            ScrapedPage(url="https://a.example", status=200, content="A2A is ..."),
            ScrapedPage(url="https://b.example", status=200, content="More on A2A..."),
        ]
        result = await s.summarize("what is A2A", pages)
        assert isinstance(result, SummaryResult)
        assert "[1]" in result.markdown and "[2]" in result.markdown
        assert result.sources == ["https://a.example", "https://b.example"]

    async def test_empty_pages_returns_graceful_summary(self, tmp_cache_dir: Path):
        """No LLM call when there is nothing to summarize."""
        client = _client([], tmp_cache_dir / "llm.jsonl")
        s = Summarizer(client=client, template=TEMPLATE)
        result = await s.summarize("anything", [])
        assert isinstance(result, SummaryResult)
        assert result.sources == []
        # Markdown must be non-empty and mention no sources explicitly.
        assert result.markdown.strip()
        assert "no sources" in result.markdown.lower() or "no results" in result.markdown.lower()

    async def test_prompt_includes_query_and_enumerated_sources(self, tmp_cache_dir: Path):
        captured: dict = {}

        async def call_fn(*, system, user, **kwargs):
            captured["system"] = system
            captured["user"] = user
            return "ok [1]"

        client = GeminiClient(
            model="m",
            api_key="k",
            cache=JsonlCache(tmp_cache_dir / "llm.jsonl"),
            call_fn=call_fn,
        )
        s = Summarizer(client=client, template=TEMPLATE)
        pages = [
            ScrapedPage(url="https://one", status=200, content="content one"),
            ScrapedPage(url="https://two", status=200, content="content two"),
        ]
        await s.summarize("my query", pages)
        assert "my query" in captured["user"]
        assert "[1]" in captured["user"]
        assert "[2]" in captured["user"]
        assert "https://one" in captured["user"]
        assert "https://two" in captured["user"]

    async def test_cites_full_source_list_in_result(self, tmp_cache_dir: Path):
        client = _client(["summary"], tmp_cache_dir / "llm.jsonl")
        s = Summarizer(client=client, template=TEMPLATE)
        pages = [
            ScrapedPage(url=f"https://{i}", status=200, content=f"c{i}") for i in range(3)
        ]
        result = await s.summarize("q", pages)
        assert result.sources == ["https://0", "https://1", "https://2"]
