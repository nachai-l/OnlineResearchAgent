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
