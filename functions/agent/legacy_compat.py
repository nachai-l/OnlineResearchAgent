"""A2A v0.1/v0.2 <-> v0.3 compatibility shim.

Legacy clients still send ``tasks/send`` JSON-RPC envelopes with
``parts[].type`` and ``sessionId`` fields. This middleware upgrades the
request to the v0.3 shape the SDK understands, then downgrades the
response back so those clients see the field names they expect.

Scope:

* Only ``tasks/send`` is translated (synchronous unary call).
  ``tasks/sendSubscribe`` / SSE streaming is intentionally **not**
  handled — streaming needs per-event rewriting which is materially
  more complex; if a legacy client ever needs it we extend this module.
* Non-legacy requests (method == ``message/send`` etc.) pass through
  untouched, so v0.3-native clients are unaffected.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

_REQ_METHOD_MAP = {"tasks/send": "message/send"}


class LegacyA2ACompatMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") != "POST":
            return await self.app(scope, receive, send)

        # Buffer the request body so we can peek at the JSON-RPC method.
        body = b""
        more = True
        while more:
            msg = await receive()
            body += msg.get("body", b"")
            more = msg.get("more_body", False)

        is_legacy = False
        client_task_id: str | None = None
        try:
            req = json.loads(body)
            if isinstance(req, dict) and req.get("method") in _REQ_METHOD_MAP:
                is_legacy = True
                client_task_id, req = _upgrade_request(req)
                body = json.dumps(req).encode()
        except (ValueError, TypeError):
            # Not JSON or not a dict — let the SDK handle/reject it.
            is_legacy = False

        request_replayed = False

        async def replay_receive() -> Message:
            nonlocal request_replayed
            if request_replayed:
                return {"type": "http.disconnect"}
            request_replayed = True
            return {"type": "http.request", "body": body, "more_body": False}

        if not is_legacy:
            return await self.app(scope, replay_receive, send)

        # Legacy path: buffer the full response body, rewrite v0.3 -> v0.1/v0.2,
        # re-emit with a corrected Content-Length.
        response_start: dict[str, Any] | None = None
        response_body = b""

        async def capture_send(message: Message) -> None:
            nonlocal response_start, response_body
            if message["type"] == "http.response.start":
                response_start = dict(message)
                return
            if message["type"] != "http.response.body":
                await send(message)
                return
            response_body += message.get("body", b"")
            if message.get("more_body", False):
                return
            rewritten = _downgrade_response(response_body, client_task_id)
            headers = _patch_content_length(
                response_start.get("headers", []) if response_start else [],
                len(rewritten),
            )
            await send({
                "type": "http.response.start",
                "status": response_start.get("status", 200) if response_start else 200,
                "headers": headers,
            })
            await send({
                "type": "http.response.body",
                "body": rewritten,
                "more_body": False,
            })

        await self.app(scope, replay_receive, capture_send)


def _upgrade_request(req: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
    req["method"] = _REQ_METHOD_MAP[req["method"]]
    params = req.get("params") or {}
    msg = params.get("message") or {}

    # parts[].type -> parts[].kind
    for part in msg.get("parts") or []:
        if "type" in part and "kind" not in part:
            part["kind"] = part.pop("type")

    # params.sessionId -> params.message.contextId
    if "sessionId" in params and "contextId" not in msg:
        msg["contextId"] = params.pop("sessionId")

    # v0.3 requires messageId; generate one if the client didn't send it.
    msg.setdefault("messageId", f"msg-{uuid.uuid4().hex[:12]}")

    # Preserve the client's requested task id so we can echo it back on
    # the response; the v0.3 server generates its own.
    client_task_id = params.pop("id", None)

    params["message"] = msg
    req["params"] = params
    return client_task_id, req


def _downgrade_response(body: bytes, client_task_id: str | None) -> bytes:
    try:
        resp = json.loads(body)
    except (ValueError, TypeError):
        return body
    if isinstance(resp, dict):
        result = resp.get("result")
        if isinstance(result, dict):
            _downgrade_task(result, client_task_id)
        # JSON-RPC errors pass through unchanged.
    return json.dumps(resp).encode()


def _downgrade_task(task: dict[str, Any], client_task_id: str | None) -> None:
    task.pop("kind", None)
    if "contextId" in task:
        task["sessionId"] = task.pop("contextId")
    if client_task_id is not None:
        task["id"] = client_task_id
    for artifact in task.get("artifacts") or []:
        _downgrade_parts(artifact.get("parts") or [])
    for hist_msg in task.get("history") or []:
        hist_msg.pop("kind", None)
        if "contextId" in hist_msg:
            hist_msg["sessionId"] = hist_msg.pop("contextId")
        _downgrade_parts(hist_msg.get("parts") or [])


def _downgrade_parts(parts: list[dict[str, Any]]) -> None:
    for part in parts:
        if "kind" in part and "type" not in part:
            part["type"] = part.pop("kind")


def _patch_content_length(
    headers: list[tuple[bytes, bytes]], new_len: int
) -> list[tuple[bytes, bytes]]:
    patched = [(k, v) for k, v in headers if k.lower() != b"content-length"]
    patched.append((b"content-length", str(new_len).encode()))
    return patched
