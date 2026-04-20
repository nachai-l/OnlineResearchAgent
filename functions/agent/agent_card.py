"""A2A Agent Card builder.

The Agent Card is the machine-readable manifest served at
``/.well-known/agent.json``. It advertises the agent's identity, the
skills it exposes, and which capabilities (streaming, push notifications)
clients can rely on. We keep the card builder a pure function so it can
be rendered without instantiating any server machinery.
"""
from __future__ import annotations

from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentSkill,
)

AGENT_NAME = "Online Research Agent"
AGENT_DESCRIPTION = (
    "Deterministic web-research workflow agent (not a tool-use loop). "
    "On each query it runs a fixed 5-stage pipeline: "
    "SerpAPI search (Google CSE fallback) → LLM-picked URLs → concurrent "
    "scrape → LLM-scored relevance + trustworthiness gate → LLM grounded "
    "summary with inline [n] citations. "
    "Every external call (search, scrape, LLM) is cached in an append-only "
    "JSONL store keyed by canonical-JSON SHA-256, so a repeated query is "
    "served with zero HTTP and zero LLM calls. Every LLM JSON response is "
    "validated against a pydantic schema before reaching business logic; "
    "schema or transport failures share a single retry budget."
)
AGENT_VERSION = "0.2.0"

SKILL_ID = "web_research"
SKILL_NAME = "Web Research"
SKILL_DESCRIPTION = (
    "Accepts a natural-language research query (text/plain) and returns a "
    "markdown answer (text/markdown) with inline [n] citations anchored to "
    "the scraped sources. "
    "Never raises on partial failure — zero search hits or pages rejected by "
    "the validity gate produce a graceful 'no reliable sources found' summary "
    "rather than an error. "
    "Streams progress as TaskStatusUpdate(working) → TaskArtifactUpdate "
    "(summary + sources) → TaskStatusUpdate(completed, final=true)."
)


def build_agent_card(*, url: str) -> AgentCard:
    """Build the :class:`AgentCard` advertised at ``/.well-known/agent.json``.

    Args:
        url: The absolute URL (including scheme + host + port) at which this
            agent's JSON-RPC endpoint is reachable.
    """
    skill = AgentSkill(
        id=SKILL_ID,
        name=SKILL_NAME,
        description=SKILL_DESCRIPTION,
        tags=["research", "search", "summarization", "citations"],
        examples=[
            "What is the Google A2A protocol?",
            "Summarize the latest news on quantum error correction.",
        ],
        input_modes=["text/plain"],
        output_modes=["text/markdown"],
    )
    return AgentCard(
        name=AGENT_NAME,
        description=AGENT_DESCRIPTION,
        version=AGENT_VERSION,
        url=url,
        protocol_version="0.3.0",
        default_input_modes=["text/plain"],
        default_output_modes=["text/markdown"],
        capabilities=AgentCapabilities(
            streaming=True,
            push_notifications=False,
            state_transition_history=False,
        ),
        skills=[skill],
    )
