"""A2A Starlette ASGI application.

Glues the :class:`AgentCard`, the :class:`ResearchAgentExecutor`, and the
SDK's ``A2AStarletteApplication`` into a single ASGI app you can run with
``uvicorn``. The app exposes:

- ``GET /.well-known/agent-card.json`` — the Agent Card manifest.
- ``POST /`` — JSON-RPC endpoint (``message/send``, ``tasks/cancel``, …).
- ``POST /stream`` — JSON-RPC + SSE streaming endpoint.

Keeping this module thin means tests can build the app with a fake
executor and hit the endpoints via ``starlette.testclient.TestClient``
without any network at all.
"""
from __future__ import annotations

from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from starlette.applications import Starlette

from functions.agent.agent_card import build_agent_card
from functions.agent.skills import ResearchAgentExecutor


def build_app(
    *,
    executor: ResearchAgentExecutor,
    url: str,
) -> Starlette:
    """Build the Starlette ASGI app for the research agent.

    Args:
        executor: The pre-built executor (pipeline already injected).
        url: Absolute URL to advertise in the Agent Card (scheme+host+port).
    """
    agent_card = build_agent_card(url=url)
    request_handler = DefaultRequestHandler(
        agent_executor=executor,
        task_store=InMemoryTaskStore(),
    )
    a2a_app = A2AStarletteApplication(
        agent_card=agent_card,
        http_handler=request_handler,
    )
    return a2a_app.build()
