"""Gemini wrapper with retry + JSONL cache.

Designed for **dependency injection**: the real Gemini call is isolated behind
an async ``call_fn`` callable. In tests we pass a fake; in production
:func:`default_call_fn` bridges to the ``google-genai`` SDK. This keeps all
behavioral logic (caching, retries, structured-output parsing) under unit test
without ever hitting the network.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Awaitable, Callable, Protocol

import httpx

from functions.core.cache import JsonlCache
from functions.utils.hashing import stable_hash

log = logging.getLogger(__name__)


class LlmCallError(RuntimeError):
    """Raised when the LLM call fails after all retries or on malformed output."""


class CallFn(Protocol):
    """Signature required of the injected LLM call function."""

    async def __call__(
        self,
        *,
        model: str,
        system: str,
        user: str,
        temperature: float,
        max_output_tokens: int,
        timeout: float,
    ) -> str: ...


def default_call_fn(api_key: str) -> CallFn:
    """Build a production ``call_fn`` that speaks to the Gemini SDK.

    Imports ``google.genai`` lazily so unit tests don't require the package.
    """
    async def _call(
        *,
        model: str,
        system: str,
        user: str,
        temperature: float,
        max_output_tokens: int,
        timeout: float,
    ) -> str:
        from google import genai  # type: ignore[import-untyped]
        from google.genai import types  # type: ignore[import-untyped]

        client = genai.Client(api_key=api_key)
        contents = [{"role": "user", "parts": [{"text": user}]}]
        config = types.GenerateContentConfig(
            system_instruction=system,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
        )
        # The SDK call is sync; offload to a thread so we don't block the loop.
        def _invoke() -> str:
            resp = client.models.generate_content(
                model=model, contents=contents, config=config
            )
            return resp.text or ""

        return await asyncio.wait_for(asyncio.to_thread(_invoke), timeout=timeout)

    return _call


def default_litellm_call_fn(*, base_url: str, api_key: str) -> CallFn:
    """Build a production ``call_fn`` that speaks to a LiteLLM proxy.

    LiteLLM exposes the OpenAI chat-completions wire format, so the body
    shape is the canonical ``{"model", "messages", "temperature",
    "max_tokens"}`` and the response is read from
    ``choices[0].message.content``. The proxy handles routing to the actual
    backend model (Gemini, Anthropic, OpenAI, ...) based on the ``model``
    string.

    Parameters
    ----------
    base_url:
        Root URL of the LiteLLM proxy. Trailing slashes are tolerated.
        ``/chat/completions`` is appended internally.
    api_key:
        Bearer token accepted by the proxy (LiteLLM virtual key).
    """
    normalized_base = base_url.rstrip("/")
    endpoint = f"{normalized_base}/chat/completions"

    async def _call(
        *,
        model: str,
        system: str,
        user: str,
        temperature: float,
        max_output_tokens: int,
        timeout: float,
    ) -> str:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_output_tokens,
        }
        async def _post() -> httpx.Response:
            async with httpx.AsyncClient(timeout=timeout) as client:
                return await client.post(endpoint, json=body, headers=headers)

        # Wrap in wait_for so the outer timeout is enforced even if the
        # transport layer (e.g. a mocked respx route) doesn't raise on its own.
        response = await asyncio.wait_for(_post(), timeout=timeout)
        response.raise_for_status()
        data = response.json()
        choices = data.get("choices") or []
        if not choices:
            raise LlmCallError(
                f"LiteLLM response missing choices: {data!r}"
            )
        message = choices[0].get("message") or {}
        content = message.get("content")
        if content is None:
            raise LlmCallError(
                f"LiteLLM response missing message.content: {data!r}"
            )
        return content

    return _call


_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _strip_code_fences(text: str) -> str:
    return _CODE_FENCE_RE.sub("", text).strip()


class GeminiClient:
    """High-level Gemini wrapper with caching + retry."""

    def __init__(
        self,
        *,
        model: str,
        api_key: str | None,
        cache: JsonlCache | None,
        call_fn: CallFn | None = None,
        default_temperature: float = 0.2,
        default_max_output_tokens: int = 2048,
        default_timeout: float = 60.0,
        retries: int = 2,
        retry_base_delay: float = 0.5,
    ) -> None:
        self._model = model
        self._api_key = api_key
        self._cache = cache
        self._call_fn = call_fn
        self._default_temperature = default_temperature
        self._default_max_output_tokens = default_max_output_tokens
        self._default_timeout = default_timeout
        self._retries = retries
        self._retry_base_delay = retry_base_delay

    # ---- public API --------------------------------------------------------

    async def generate(
        self,
        *,
        system: str,
        user: str,
        temperature: float | None = None,
        max_output_tokens: int | None = None,
        timeout: float | None = None,
    ) -> str:
        temp = self._default_temperature if temperature is None else temperature
        max_tok = (
            self._default_max_output_tokens if max_output_tokens is None else max_output_tokens
        )
        t = self._default_timeout if timeout is None else timeout

        cache_key = stable_hash(
            {
                "model": self._model,
                "system": system,
                "user": user,
                "temperature": temp,
                "max_output_tokens": max_tok,
            }
        )

        if self._cache is not None:
            cached = self._cache.get(cache_key)
            if cached is not None:
                log.info(
                    "llm cache hit",
                    extra={"stage": "llm", "cache": "hit", "model": self._model},
                )
                return cached["text"]

        call_fn = self._resolve_call_fn()
        text = await self._invoke_with_retry(
            call_fn,
            model=self._model,
            system=system,
            user=user,
            temperature=temp,
            max_output_tokens=max_tok,
            timeout=t,
        )

        if self._cache is not None:
            self._cache.put(cache_key, {"text": text})
        log.info(
            "llm call ok",
            extra={"stage": "llm", "cache": "miss", "model": self._model},
        )
        return text

    async def generate_json(self, **kwargs: Any) -> dict[str, Any]:
        raw = await self.generate(**kwargs)
        cleaned = _strip_code_fences(raw)
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError as e:
            raise LlmCallError(f"model did not return valid JSON: {cleaned!r}") from e
        if not isinstance(parsed, dict):
            raise LlmCallError(f"expected JSON object, got {type(parsed).__name__}")
        return parsed

    # ---- internals ---------------------------------------------------------

    def _resolve_call_fn(self) -> CallFn:
        if self._call_fn is not None:
            return self._call_fn
        if not self._api_key:
            raise LlmCallError(
                "no Gemini API key configured and no call_fn injected"
            )
        # Lazily build the real SDK-backed fn and memoize it.
        self._call_fn = default_call_fn(self._api_key)
        return self._call_fn

    async def _invoke_with_retry(
        self,
        call_fn: CallFn,
        **kwargs: Any,
    ) -> str:
        last_exc: BaseException | None = None
        for attempt in range(self._retries + 1):
            try:
                return await call_fn(**kwargs)
            except Exception as e:  # noqa: BLE001 — retry envelope
                last_exc = e
                log.warning(
                    "llm call failed, retrying",
                    extra={"stage": "llm", "attempt": attempt, "exc": repr(e)},
                )
                if attempt < self._retries:
                    await asyncio.sleep(self._retry_base_delay * (2**attempt))
        raise LlmCallError(f"LLM call failed after {self._retries + 1} attempts: {last_exc!r}")
