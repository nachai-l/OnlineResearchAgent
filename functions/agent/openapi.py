"""OpenAPI 3.1 spec + Swagger UI bootstrap for the A2A HTTP surface.

A2A is **JSON-RPC over HTTP**, not REST — so a literal OpenAPI spec
can't enumerate every ``method`` as a separate operation. What we
*can* do is document the three real HTTP endpoints the SDK mounts:

- ``GET  /.well-known/agent-card.json`` — the Agent Card manifest.
- ``POST /``                             — JSON-RPC 2.0 (non-streaming).
- ``POST /stream``                       — JSON-RPC 2.0 + SSE streaming.

… and attach realistic ``examples`` for each JSON-RPC method the
SDK handles (``message/send``, ``message/stream``, ``tasks/get``,
``tasks/cancel``). Swagger UI renders the example dropdown on each
endpoint so a human can click **Try it out** and get the right body
prefilled.

The spec is a pure function of the runtime URL, mirroring
:func:`build_agent_card` — keeps it trivially unit-testable.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from a2a.types import AgentCard

from functions.agent.agent_card import AGENT_DESCRIPTION, AGENT_NAME, AGENT_VERSION


def build_openapi_spec(*, url: str) -> dict[str, Any]:
    """Build the OpenAPI 3.1 spec advertised at ``/openapi.json``.

    Args:
        url: Absolute URL (scheme+host+port) the agent is reachable at.
            Drives the ``servers[0].url`` entry so Swagger UI's
            "Try it out" requests hit the right origin.
    """
    agent_card_schema, defs = _agent_card_schema_components()

    return {
        "openapi": "3.1.0",
        "info": {
            "title": AGENT_NAME,
            "version": AGENT_VERSION,
            "description": (
                AGENT_DESCRIPTION
                + "\n\n**Protocol note.** `POST /` and `POST /stream` are "
                "JSON-RPC 2.0 endpoints. Select a concrete method via the "
                "Examples dropdown on each operation below."
            ),
        },
        "servers": [{"url": url}],
        "tags": [
            {"name": "discovery", "description": "Agent Card metadata."},
            {"name": "a2a", "description": "A2A JSON-RPC protocol endpoints."},
        ],
        "paths": {
            "/.well-known/agent-card.json": {
                "get": {
                    "tags": ["discovery"],
                    "summary": "Fetch the Agent Card",
                    "description": (
                        "Returns the machine-readable manifest describing "
                        "this agent's identity, skills, capabilities, and "
                        "the JSON-RPC URL clients should POST to."
                    ),
                    "responses": {
                        "200": {
                            "description": "Agent Card JSON.",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/AgentCard"}
                                }
                            },
                        }
                    },
                }
            },
            "/": {
                "post": {
                    "tags": ["a2a"],
                    "summary": "A2A JSON-RPC endpoint (non-streaming)",
                    "description": (
                        "JSON-RPC 2.0. Supported `method` values: "
                        "`message/send`, `tasks/get`, `tasks/cancel`. "
                        "For the streaming variant use `POST /stream`."
                    ),
                    "requestBody": _jsonrpc_request_body(streaming=False),
                    "responses": {
                        "200": {
                            "description": "JSON-RPC response envelope.",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/JsonRpcResponse"}
                                }
                            },
                        }
                    },
                }
            },
            "/stream": {
                "post": {
                    "tags": ["a2a"],
                    "summary": "A2A JSON-RPC + SSE streaming endpoint",
                    "description": (
                        "Server-Sent Events variant for the `message/stream` "
                        "method. The response is `text/event-stream`; each "
                        "event is a `TaskStatusUpdateEvent` or "
                        "`TaskArtifactUpdateEvent`, and the final event "
                        "carries `final: true`."
                    ),
                    "requestBody": _jsonrpc_request_body(streaming=True),
                    "responses": {
                        "200": {
                            "description": "SSE stream of task updates.",
                            "content": {
                                "text/event-stream": {
                                    "schema": {"type": "string"}
                                }
                            },
                        }
                    },
                }
            },
        },
        "components": {
            "schemas": {
                "AgentCard": agent_card_schema,
                **defs,
                "JsonRpcRequest": _jsonrpc_request_schema(),
                "JsonRpcResponse": _jsonrpc_response_schema(),
            }
        },
    }


# ---- Swagger UI bootstrap -------------------------------------------------

SWAGGER_UI_HTML = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8"/>
    <meta name="viewport" content="width=device-width, initial-scale=1"/>
    <title>Online Research Agent — API docs</title>
    <link rel="stylesheet" href="https://unpkg.com/swagger-ui-dist@5/swagger-ui.css"/>
    <style>body { margin: 0; }</style>
  </head>
  <body>
    <div id="swagger-ui"></div>
    <script src="https://unpkg.com/swagger-ui-dist@5/swagger-ui-bundle.js" crossorigin></script>
    <script>
      window.ui = SwaggerUIBundle({
        url: '/openapi.json',
        dom_id: '#swagger-ui',
        deepLinking: true,
        presets: [SwaggerUIBundle.presets.apis],
        layout: 'BaseLayout',
        tryItOutEnabled: true,
      });
    </script>
  </body>
</html>
"""


# ---- helpers --------------------------------------------------------------


def _agent_card_schema_components() -> tuple[dict[str, Any], dict[str, Any]]:
    """Pull ``AgentCard.model_json_schema()`` and relocate its ``$defs``.

    Pydantic emits the top-level schema plus ``$defs`` at the root. OpenAPI
    wants nested models under ``#/components/schemas/<Name>``. We:

    1. Pop ``$defs`` off the root schema.
    2. Rewrite every ``$ref`` from ``#/$defs/X`` → ``#/components/schemas/X``
       (both in the root and inside every def) so Swagger UI can resolve
       them against our flattened namespace.
    """
    schema = AgentCard.model_json_schema()
    defs = schema.pop("$defs", {})
    schema = _rewrite_refs(schema)
    defs = {name: _rewrite_refs(d) for name, d in defs.items()}
    return schema, defs


def _rewrite_refs(node: Any) -> Any:
    """Recursively rewrite ``$ref: '#/$defs/X'`` → ``'#/components/schemas/X'``."""
    if isinstance(node, dict):
        out: dict[str, Any] = {}
        for k, v in node.items():
            if k == "$ref" and isinstance(v, str) and v.startswith("#/$defs/"):
                out[k] = "#/components/schemas/" + v[len("#/$defs/") :]
            else:
                out[k] = _rewrite_refs(v)
        return out
    if isinstance(node, list):
        return [_rewrite_refs(x) for x in node]
    return node


def _jsonrpc_request_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["jsonrpc", "method"],
        "properties": {
            "jsonrpc": {"type": "string", "const": "2.0"},
            "id": {
                "oneOf": [{"type": "string"}, {"type": "integer"}, {"type": "null"}],
                "description": "Client-generated request id. Echoed back in the response.",
            },
            "method": {
                "type": "string",
                "enum": [
                    "message/send",
                    "message/stream",
                    "tasks/get",
                    "tasks/cancel",
                    "tasks/resubscribe",
                ],
            },
            "params": {"type": "object"},
        },
    }


def _jsonrpc_response_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["jsonrpc"],
        "properties": {
            "jsonrpc": {"type": "string", "const": "2.0"},
            "id": {
                "oneOf": [{"type": "string"}, {"type": "integer"}, {"type": "null"}]
            },
            "result": {
                "description": "Present on success. Shape depends on method.",
            },
            "error": {
                "type": "object",
                "description": "Present on failure.",
                "properties": {
                    "code": {"type": "integer"},
                    "message": {"type": "string"},
                    "data": {},
                },
                "required": ["code", "message"],
            },
        },
    }


def _jsonrpc_request_body(*, streaming: bool) -> dict[str, Any]:
    examples: dict[str, dict[str, Any]] = {}

    send_example = {
        "jsonrpc": "2.0",
        "id": "1",
        "method": "message/stream" if streaming else "message/send",
        "params": {
            "message": {
                "role": "user",
                "parts": [
                    {
                        "kind": "text",
                        "text": "What is the Google A2A protocol?",
                    }
                ],
                "messageId": "msg-1",
            }
        },
    }
    key = "message/stream" if streaming else "message/send"
    examples[key] = {
        "summary": f"Run a research query via `{key}`",
        "value": send_example,
    }

    if not streaming:
        examples["tasks/get"] = {
            "summary": "Fetch a task's current state",
            "value": {
                "jsonrpc": "2.0",
                "id": "2",
                "method": "tasks/get",
                "params": {"id": "task-<uuid>"},
            },
        }
        examples["tasks/cancel"] = {
            "summary": "Cancel an in-flight task",
            "value": {
                "jsonrpc": "2.0",
                "id": "3",
                "method": "tasks/cancel",
                "params": {"id": "task-<uuid>"},
            },
        }

    return {
        "required": True,
        "content": {
            "application/json": {
                "schema": {"$ref": "#/components/schemas/JsonRpcRequest"},
                "examples": deepcopy(examples),
            }
        },
    }
