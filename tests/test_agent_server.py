"""TDD tests for functions.agent.server."""
from __future__ import annotations

from starlette.applications import Starlette
from starlette.testclient import TestClient

from functions.agent.agent_card import AGENT_NAME
from functions.agent.server import build_app
from functions.agent.skills import ResearchAgentExecutor


class _NoopPipeline:
    async def run(self, query: str):  # pragma: no cover — never invoked here
        raise AssertionError("pipeline should not run for card fetch")


def _app() -> Starlette:
    return build_app(
        executor=ResearchAgentExecutor(pipeline=_NoopPipeline()),
        url="http://localhost:8000",
    )


class TestServer:
    def test_returns_starlette_app(self) -> None:
        app = _app()
        assert isinstance(app, Starlette)

    def test_agent_card_endpoint_serves_json(self) -> None:
        app = _app()
        with TestClient(app) as c:
            r = c.get("/.well-known/agent-card.json")
            assert r.status_code == 200
            body = r.json()
            assert body["name"] == AGENT_NAME
            assert body["url"] == "http://localhost:8000"
            assert body["capabilities"]["streaming"] is True
            assert body["skills"], "agent card must expose at least one skill"

    def test_openapi_endpoint_serves_spec(self) -> None:
        """`GET /openapi.json` returns a well-formed OpenAPI 3.1 document."""
        app = _app()
        with TestClient(app) as c:
            r = c.get("/openapi.json")
            assert r.status_code == 200
            spec = r.json()
            assert spec["openapi"].startswith("3.")
            # Every HTTP endpoint we mount must be documented.
            assert "/.well-known/agent-card.json" in spec["paths"]
            assert "/" in spec["paths"]
            assert "/stream" in spec["paths"]
            # Server URL must echo the one the app was built with so
            # Swagger UI's "Try it out" hits the right origin.
            assert spec["servers"][0]["url"] == "http://localhost:8000"
            # AgentCard schema must be resolvable against components/schemas
            # (no leftover #/$defs refs from pydantic's default output).
            dumped = r.text
            assert "#/$defs/" not in dumped, (
                "pydantic $defs leaked into OpenAPI spec — Swagger UI "
                "won't resolve them"
            )
            assert "AgentCard" in spec["components"]["schemas"]

    def test_docs_endpoint_serves_swagger_ui_html(self) -> None:
        """`GET /docs` returns the Swagger UI bootstrap pointing at /openapi.json."""
        app = _app()
        with TestClient(app) as c:
            r = c.get("/docs")
            assert r.status_code == 200
            assert r.headers["content-type"].startswith("text/html")
            body = r.text
            assert "swagger-ui" in body.lower()
            assert "/openapi.json" in body
