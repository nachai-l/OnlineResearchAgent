"""TDD tests for functions.llm.client.default_litellm_call_fn.

LiteLLM proxies speak the OpenAI chat-completions wire format, so we only
need to verify:

- The POST hits ``{base_url}/chat/completions`` with a Bearer auth header.
- The request body has the canonical OpenAI shape: ``model`` +
  ``messages`` (system + user) + ``temperature`` + ``max_tokens``.
- The response's ``choices[0].message.content`` is returned as-is.
- Non-2xx responses surface as an exception the client's retry envelope
  can catch.

All HTTP is mocked with ``respx``. No network is touched.
"""
from __future__ import annotations

import httpx
import pytest
import respx

from functions.llm.client import LlmCallError, default_litellm_call_fn


BASE_URL = "https://litellm.example.com"


class TestLitellmCallFn:
    @respx.mock
    async def test_posts_openai_chat_completions_shape(self) -> None:
        captured: dict = {}

        def _record_and_reply(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["auth"] = request.headers.get("authorization")
            captured["body"] = request.read().decode("utf-8")
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"message": {"role": "assistant", "content": "hi there"}}
                    ]
                },
            )

        respx.post(f"{BASE_URL}/chat/completions").mock(side_effect=_record_and_reply)

        call_fn = default_litellm_call_fn(base_url=BASE_URL, api_key="sk-test")
        out = await call_fn(
            model="gemini-2.5-flash",
            system="be brief",
            user="hello",
            temperature=0.3,
            max_output_tokens=256,
            timeout=5.0,
        )

        assert out == "hi there"
        assert captured["url"] == f"{BASE_URL}/chat/completions"
        assert captured["auth"] == "Bearer sk-test"

        import json
        body = json.loads(captured["body"])
        assert body["model"] == "gemini-2.5-flash"
        assert body["temperature"] == 0.3
        assert body["max_tokens"] == 256
        assert body["messages"] == [
            {"role": "system", "content": "be brief"},
            {"role": "user", "content": "hello"},
        ]

    @respx.mock
    async def test_handles_base_url_with_trailing_slash(self) -> None:
        respx.post("https://litellm.example.com/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={"choices": [{"message": {"content": "ok"}}]},
            )
        )

        call_fn = default_litellm_call_fn(
            base_url="https://litellm.example.com/",  # trailing slash
            api_key="sk",
        )
        out = await call_fn(
            model="m",
            system="s",
            user="u",
            temperature=0.0,
            max_output_tokens=10,
            timeout=5.0,
        )
        assert out == "ok"

    @respx.mock
    async def test_non_2xx_raises(self) -> None:
        respx.post(f"{BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(500, json={"error": "down"})
        )
        call_fn = default_litellm_call_fn(base_url=BASE_URL, api_key="sk")
        with pytest.raises(httpx.HTTPStatusError):
            await call_fn(
                model="m",
                system="s",
                user="u",
                temperature=0.0,
                max_output_tokens=10,
                timeout=5.0,
            )

    @respx.mock
    async def test_empty_choices_raises_llm_error(self) -> None:
        """Malformed-but-2xx responses should surface as a clear LlmCallError,
        not silently return empty string.
        """
        respx.post(f"{BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(200, json={"choices": []})
        )
        call_fn = default_litellm_call_fn(base_url=BASE_URL, api_key="sk")
        with pytest.raises(LlmCallError):
            await call_fn(
                model="m",
                system="s",
                user="u",
                temperature=0.0,
                max_output_tokens=10,
                timeout=5.0,
            )

    @respx.mock
    async def test_timeout_applied(self) -> None:
        """The ``timeout`` kwarg must reach the httpx request."""
        import asyncio

        async def _slow(request: httpx.Request) -> httpx.Response:
            await asyncio.sleep(2.0)
            return httpx.Response(200, json={"choices": [{"message": {"content": "late"}}]})

        respx.post(f"{BASE_URL}/chat/completions").mock(side_effect=_slow)
        call_fn = default_litellm_call_fn(base_url=BASE_URL, api_key="sk")
        with pytest.raises((httpx.TimeoutException, asyncio.TimeoutError)):
            await call_fn(
                model="m",
                system="s",
                user="u",
                temperature=0.0,
                max_output_tokens=10,
                timeout=0.1,
            )
