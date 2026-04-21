"""A2A Starlette ASGI application.

Glues the :class:`AgentCard`, the :class:`ResearchAgentExecutor`, and the
SDK's ``A2AStarletteApplication`` into a single ASGI app you can run with
``uvicorn``. The app exposes:

- ``GET /.well-known/agent-card.json`` — the Agent Card manifest.
- ``POST /`` — JSON-RPC endpoint (``message/send``, ``tasks/cancel``, …).
- ``POST /stream`` — JSON-RPC + SSE streaming endpoint.
- ``GET /openapi.json`` — OpenAPI 3.1 spec describing the three HTTP
  endpoints above (with JSON-RPC examples per method).
- ``GET /docs`` — Swagger UI rendering of the spec for interactive
  exploration / "Try it out".

Keeping this module thin means tests can build the app with a fake
executor and hit the endpoints via ``starlette.testclient.TestClient``
without any network at all.
"""
from __future__ import annotations

from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse

from functions.agent.agent_card import build_agent_card
from functions.agent.legacy_compat import LegacyA2ACompatMiddleware
from functions.agent.openapi import SWAGGER_UI_HTML, build_openapi_spec
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
    app = a2a_app.build()

    # Attach OpenAPI + Swagger UI routes. The spec is built once at
    # startup (pure function of ``url``); rebuilding per request would
    # be wasteful. ``/docs`` is plain HTML pointing at ``/openapi.json``.
    openapi_spec = build_openapi_spec(url=url)

    async def openapi_handler(_request: Request) -> JSONResponse:
        return JSONResponse(openapi_spec)

    async def docs_handler(_request: Request) -> HTMLResponse:
        return HTMLResponse(SWAGGER_UI_HTML)

    app.router.add_route("/openapi.json", openapi_handler, methods=["GET"])
    app.router.add_route("/docs", docs_handler, methods=["GET"])

    # Accept v0.1/v0.2 JSON-RPC envelopes (tasks/send, parts[].type,
    # sessionId) from legacy clients that can't be updated to v0.3.
    app.add_middleware(LegacyA2ACompatMiddleware)

    return app
