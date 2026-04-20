"""A2A skill: wraps :class:`ResearchPipeline` as an :class:`AgentExecutor`.

The executor is the bridge between the A2A server's event-driven protocol
and our pipeline's plain async ``run(query) -> ResearchResult`` contract.
For each request it:

1. Emits ``TaskStatusUpdateEvent(state=working)`` so the client knows the
   task has been accepted.
2. Delegates to :class:`ResearchPipeline.run` — internal stages (search,
   select, scrape, validate, summarize) already log a structured line each,
   so we don't re-emit per-stage A2A events here. Keeping the event stream
   coarse (working → artifact → completed) avoids flooding clients with
   implementation detail.
3. Emits a single ``TaskArtifactUpdateEvent`` carrying the markdown summary
   + citations in a ``TextPart``, and a final
   ``TaskStatusUpdateEvent(state=completed, final=True)``.

Missing/empty queries short-circuit to ``state=failed``; a pipeline
exception does the same. Neither path ever invokes the LLM or the network.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Protocol

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.types import (
    Artifact,
    Part,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
    TextPart,
)

from functions.core.research_pipeline import ResearchResult

log = logging.getLogger(__name__)


class PipelineLike(Protocol):
    async def run(self, query: str) -> ResearchResult: ...


class ResearchAgentExecutor(AgentExecutor):
    """A2A executor that runs the research pipeline for each request."""

    def __init__(self, *, pipeline: PipelineLike) -> None:
        self._pipeline = pipeline

    async def execute(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        task_id = context.task_id or _new_id("task")
        context_id = context.context_id or _new_id("ctx")

        query = _extract_query(context)
        if not query:
            log.info(
                "skill rejected empty query",
                extra={"stage": "skill", "task_id": task_id},
            )
            await _emit_status(
                event_queue,
                task_id=task_id,
                context_id=context_id,
                state=TaskState.failed,
                final=True,
                message="No text query provided in request message.",
            )
            return

        log.info(
            "skill start",
            extra={"stage": "skill", "task_id": task_id, "query": query},
        )
        await _emit_status(
            event_queue,
            task_id=task_id,
            context_id=context_id,
            state=TaskState.working,
            final=False,
            message=f"Researching: {query}",
        )

        try:
            result = await self._pipeline.run(query)
        except Exception as e:  # noqa: BLE001 — surface all failures as task-failed
            log.exception(
                "skill pipeline error",
                extra={"stage": "skill", "task_id": task_id, "exc": repr(e)},
            )
            await _emit_status(
                event_queue,
                task_id=task_id,
                context_id=context_id,
                state=TaskState.failed,
                final=True,
                message=f"Pipeline error: {e!r}",
            )
            return

        # Emit the summary artifact.
        artifact = _build_artifact(result)
        await event_queue.enqueue_event(
            TaskArtifactUpdateEvent(
                task_id=task_id,
                context_id=context_id,
                artifact=artifact,
                append=False,
                last_chunk=True,
            )
        )

        # Mark task complete.
        await _emit_status(
            event_queue,
            task_id=task_id,
            context_id=context_id,
            state=TaskState.completed,
            final=True,
            message=(
                f"Done — {result.kept_count} of {result.scraped_count} scraped "
                f"sources survived validation."
            ),
        )
        log.info(
            "skill done",
            extra={
                "stage": "skill",
                "task_id": task_id,
                "sources": len(result.sources),
                "chars": len(result.summary_markdown),
            },
        )

    async def cancel(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        task_id = context.task_id or _new_id("task")
        context_id = context.context_id or _new_id("ctx")
        log.info(
            "skill canceled",
            extra={"stage": "skill", "task_id": task_id},
        )
        await _emit_status(
            event_queue,
            task_id=task_id,
            context_id=context_id,
            state=TaskState.canceled,
            final=True,
            message="Task canceled by client.",
        )


# ---- helpers --------------------------------------------------------------


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _extract_query(context: RequestContext) -> str:
    msg = context.message
    if msg is None or not msg.parts:
        return ""
    chunks: list[str] = []
    for part in msg.parts:
        # `part` is a RootModel wrapping one of the *Part variants; the
        # concrete variant is on `.root`.
        node = getattr(part, "root", part)
        text = getattr(node, "text", None)
        if isinstance(text, str) and text.strip():
            chunks.append(text)
    return "\n".join(chunks).strip()


async def _emit_status(
    queue: EventQueue,
    *,
    task_id: str,
    context_id: str,
    state: TaskState,
    final: bool,
    message: str | None = None,
) -> None:
    status = TaskStatus(state=state, timestamp=_now_iso())
    event = TaskStatusUpdateEvent(
        task_id=task_id,
        context_id=context_id,
        status=status,
        final=final,
        metadata={"message": message} if message else None,
    )
    await queue.enqueue_event(event)


def _build_artifact(result: ResearchResult) -> Artifact:
    summary_part = Part(root=TextPart(text=result.summary_markdown))
    sources_md = "\n".join(
        f"{i}. {url}" for i, url in enumerate(result.sources, start=1)
    )
    parts: list[Part] = [summary_part]
    if sources_md:
        parts.append(Part(root=TextPart(text="\n\n**Sources**\n" + sources_md)))
    return Artifact(
        artifact_id=_new_id("artifact"),
        name="research-summary",
        description="Grounded research summary with inline [n] citations.",
        parts=parts,
        metadata={
            "sources_count": len(result.sources),
            "search_results_count": result.search_results_count,
            "scraped_count": result.scraped_count,
            "kept_count": result.kept_count,
        },
    )
