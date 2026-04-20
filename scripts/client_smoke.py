"""Smoke-test the running A2A server with the official SDK client.

Usage (after `python scripts/run_server.py` in another terminal):

    python scripts/client_smoke.py "what is the Google A2A protocol?"

Fetches the Agent Card, sends one ``message/send`` request, and prints
the task status + any text artifacts the agent emits.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import httpx
from a2a.client import A2ACardResolver, ClientFactory, ClientConfig
from a2a.types import Message, MessageSendParams, Part, Role, TextPart


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="A2A client smoke test.")
    p.add_argument("query", help="Research query to send.")
    p.add_argument(
        "--url",
        default="http://127.0.0.1:8000",
        help="Base URL of the running A2A server.",
    )
    return p.parse_args()


async def _amain(query: str, base_url: str) -> int:
    async with httpx.AsyncClient(timeout=120.0) as http:
        # 1. Fetch Agent Card
        resolver = A2ACardResolver(httpx_client=http, base_url=base_url)
        card = await resolver.get_agent_card()
        print(f"Agent: {card.name} @ {card.url}")
        print(f"Skills: {[s.id for s in card.skills]}")
        print()

        # 2. Build a client and send one message
        client = ClientFactory(ClientConfig(httpx_client=http)).create(card)
        message = Message(
            message_id=f"msg-{uuid.uuid4().hex[:8]}",
            role=Role.user,
            parts=[Part(root=TextPart(text=query))],
        )

        print(f"> {query}\n")
        async for event in client.send_message(message):
            # Each event is either a Message or a (Task, UpdateEvent|None) tuple
            # depending on SDK version. Print whatever text we can find.
            _pretty_print(event)

    return 0


def _pretty_print(event) -> None:
    # Task with status
    task = getattr(event, "status", None) and event
    if task is None and isinstance(event, tuple):
        task = event[0]
    if task is not None:
        state = getattr(getattr(task, "status", None), "state", None)
        print(f"[status] {state}")
        for art in getattr(task, "artifacts", None) or []:
            for part in art.parts:
                node = getattr(part, "root", part)
                text = getattr(node, "text", None)
                if text:
                    print(text)
        return
    # Plain Message event
    for part in getattr(event, "parts", None) or []:
        node = getattr(part, "root", part)
        text = getattr(node, "text", None)
        if text:
            print(text)


def main() -> int:
    args = _parse_args()
    return asyncio.run(_amain(args.query, args.url))


if __name__ == "__main__":
    raise SystemExit(main())
