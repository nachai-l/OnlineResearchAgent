"""TDD tests for the A2A skill wrapper around :class:`ResearchPipeline`.

We inject a stub pipeline so these tests never go near HTTP or the LLM.
What we're verifying:

- The executor reads the user query from ``context.message.parts``.
- It emits ``TaskStatusUpdateEvent`` with ``state=working`` while the
  pipeline runs, followed by a ``TaskArtifactUpdateEvent`` carrying the
  summary markdown + citations, and finally a ``TaskStatusUpdateEvent``
  with ``state=completed`` and ``final=True``.
- Missing / empty user input surfaces as ``state=failed, final=True``.
- ``cancel`` emits ``state=canceled, final=True``.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest
from a2a.server.agent_execution import RequestContext
from a2a.server.events import EventQueue
from a2a.types import (
    Message,
    MessageSendParams,
    Role,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatusUpdateEvent,
    TextPart,
)

from functions.agent.agent_card import build_agent_card
from functions.agent.skills import ResearchAgentExecutor
from functions.core.research_pipeline import ResearchResult


class _StubPipeline:
    def __init__(self, result: ResearchResult | None = None) -> None:
        self.result = result or ResearchResult(
            query="default",
            summary_markdown="Answer with [1] citation.",
            sources=["https://one.example"],
            judgements=[],
            search_results_count=1,
            scraped_count=1,
            kept_count=1,
        )
        self.calls: list[str] = []

    async def run(self, query: str) -> ResearchResult:
        self.calls.append(query)
        # Clone the stub result with the actual query so tests can assert it.
        return ResearchResult(
            query=query,
            summary_markdown=self.result.summary_markdown,
            sources=list(self.result.sources),
            judgements=list(self.result.judgements),
            search_results_count=self.result.search_results_count,
            scraped_count=self.result.scraped_count,
            kept_count=self.result.kept_count,
        )


def _make_context(text: str | None) -> RequestContext:
    parts: list[Any] = []
    if text is not None:
        parts.append(TextPart(text=text))
    message = Message(
        message_id="msg-1",
        role=Role.user,
        parts=parts,
    )
    params = MessageSendParams(message=message)
    return RequestContext(request=params)


async def _drain(queue: EventQueue, max_events: int = 20) -> list[Any]:
    """Pull everything off the queue until it's empty."""
    out: list[Any] = []
    # Let the producer finish first.
    await asyncio.sleep(0)
    for _ in range(max_events):
        try:
            out.append(await asyncio.wait_for(queue.dequeue_event(no_wait=True), timeout=1.0))
        except (asyncio.QueueEmpty, asyncio.TimeoutError):
            break
    return out


class TestResearchAgentExecutor:
    async def test_happy_path_emits_working_artifact_completed(self) -> None:
        pipe = _StubPipeline()
        executor = ResearchAgentExecutor(pipeline=pipe)
        ctx = _make_context("what is A2A?")
        queue = EventQueue()

        await executor.execute(ctx, queue)
        events = await _drain(queue)

        # Query was forwarded to the pipeline.
        assert pipe.calls == ["what is A2A?"]

        # Classify events.
        status_events = [e for e in events if isinstance(e, TaskStatusUpdateEvent)]
        artifact_events = [e for e in events if isinstance(e, TaskArtifactUpdateEvent)]

        # At least one 'working' update, then a final 'completed'.
        assert any(e.status.state == TaskState.working for e in status_events)
        assert status_events[-1].status.state == TaskState.completed
        assert status_events[-1].final is True

        # Exactly one artifact carrying the summary text.
        assert len(artifact_events) == 1
        artifact = artifact_events[0].artifact
        text_parts = [p.root for p in artifact.parts if hasattr(p, "root")]
        # TextPart has `text`
        joined = "".join(getattr(tp, "text", "") for tp in text_parts)
        assert "Answer with [1]" in joined

    async def test_missing_query_emits_failed(self) -> None:
        pipe = _StubPipeline()
        executor = ResearchAgentExecutor(pipeline=pipe)
        ctx = _make_context(None)  # no parts
        queue = EventQueue()

        await executor.execute(ctx, queue)
        events = await _drain(queue)

        assert pipe.calls == []  # pipeline not invoked
        status_events = [e for e in events if isinstance(e, TaskStatusUpdateEvent)]
        assert status_events, "expected at least one status event"
        last = status_events[-1]
        assert last.status.state == TaskState.failed
        assert last.final is True

    async def test_empty_string_query_emits_failed(self) -> None:
        pipe = _StubPipeline()
        executor = ResearchAgentExecutor(pipeline=pipe)
        ctx = _make_context("   ")
        queue = EventQueue()

        await executor.execute(ctx, queue)
        events = await _drain(queue)

        assert pipe.calls == []
        status_events = [e for e in events if isinstance(e, TaskStatusUpdateEvent)]
        assert status_events[-1].status.state == TaskState.failed
        assert status_events[-1].final is True

    async def test_cancel_emits_canceled(self) -> None:
        pipe = _StubPipeline()
        executor = ResearchAgentExecutor(pipeline=pipe)
        ctx = _make_context("anything")
        queue = EventQueue()

        await executor.cancel(ctx, queue)
        events = await _drain(queue)

        status_events = [e for e in events if isinstance(e, TaskStatusUpdateEvent)]
        assert status_events, "cancel should emit at least one status event"
        assert status_events[-1].status.state == TaskState.canceled
        assert status_events[-1].final is True

    async def test_pipeline_exception_emits_failed(self) -> None:
        class _BoomPipeline:
            async def run(self, query: str) -> ResearchResult:
                raise RuntimeError("boom")

        executor = ResearchAgentExecutor(pipeline=_BoomPipeline())
        ctx = _make_context("anything")
        queue = EventQueue()

        await executor.execute(ctx, queue)
        events = await _drain(queue)
        status_events = [e for e in events if isinstance(e, TaskStatusUpdateEvent)]
        assert status_events[-1].status.state == TaskState.failed
        assert status_events[-1].final is True


class TestAgentCard:
    def test_has_name_description_and_skill(self) -> None:
        card = build_agent_card(url="http://localhost:8000")
        assert card.name
        assert card.description
        assert card.skills, "agent should declare at least one skill"
        assert any("research" in s.id.lower() or "research" in s.name.lower()
                   for s in card.skills)

    def test_url_is_reflected(self) -> None:
        card = build_agent_card(url="http://example.invalid:9000")
        assert card.url == "http://example.invalid:9000"

    def test_capabilities_enable_streaming(self) -> None:
        card = build_agent_card(url="http://localhost:8000")
        assert card.capabilities.streaming is True
