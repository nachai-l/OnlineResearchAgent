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
    "Performs grounded web research on a user query by chaining "
    "search → result selection → scraping → validity check → summarization, "
    "returning a markdown answer with inline [n] citations."
)
AGENT_VERSION = "0.1.0"

SKILL_ID = "web_research"
SKILL_NAME = "Web Research"
SKILL_DESCRIPTION = (
    "Given a natural-language query, search the web, pick the best sources, "
    "scrape them, filter for validity, and return a grounded markdown summary "
    "with citations."
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
