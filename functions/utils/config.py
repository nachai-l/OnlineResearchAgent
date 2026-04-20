"""Typed settings loaded from ``configs/parameters.yaml`` + ``configs/credentials.yaml``.

Both YAML files are **checked in** — they carry no secrets.
- ``parameters.yaml`` holds tunables (top_k, thresholds, timeouts, paths).
- ``credentials.yaml`` holds the *names* of environment variables that carry
  secrets, plus optional non-secret identity fields (e.g. GCP project/location).

Actual secrets live in the environment. ``load_settings`` reads a ``.env`` file
(if present) via ``python-dotenv``, then resolves each ``*_env`` pointer via
``os.environ.get``. Missing env vars yield ``None`` rather than raising — the
downstream search/LLM modules decide for themselves whether a missing key is
fatal.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, ValidationError

from functions.utils.paths import REPO_ROOT


class SearchSettings(BaseModel):
    top_k_results: int = Field(ge=1)
    top_k_to_scrape: int = Field(ge=1)
    timeout_seconds: int = Field(ge=1)
    serpapi_engine: str
    serpapi_hl: str
    serpapi_gl: str
    cse_num: int = Field(ge=1, le=10)


class ScrapeSettings(BaseModel):
    timeout_seconds: int = Field(ge=1)
    max_chars_per_page: int = Field(ge=100)
    concurrency: int = Field(ge=1)
    user_agent: str


class ValidationSettings(BaseModel):
    min_relevance: float = Field(ge=0.0, le=1.0)
    min_trustworthiness: float = Field(ge=0.0, le=1.0)


LlmBackend = Literal["gemini", "litellm"]


class LlmSettings(BaseModel):
    model: str
    temperature: float = Field(ge=0.0, le=2.0)
    max_output_tokens: int = Field(ge=1)
    request_timeout_seconds: int = Field(ge=1)
    retries: int = Field(ge=0)
    # Which provider to talk to. "gemini" goes through the google-genai SDK;
    # "litellm" POSTs to an OpenAI-compatible chat/completions proxy.
    backend: LlmBackend = "gemini"
    # Only consulted when backend == "litellm". Trailing slashes are tolerated.
    litellm_base_url: str | None = None


class CacheSettings(BaseModel):
    enabled: bool = True
    dir: str
    search_file: str
    scrape_file: str
    llm_file: str


class LoggingSettings(BaseModel):
    level: str
    dir: str
    file: str
    max_bytes: int = Field(ge=1024)
    backup_count: int = Field(ge=0)


class ServerSettings(BaseModel):
    host: str
    port: int = Field(ge=1, le=65535)


class Credentials(BaseModel):
    """Resolved credentials — values come from env vars, not from YAML.

    Fields are ``None`` when the corresponding env var is unset.
    """

    serpapi_api_key: str | None = None
    google_cse_api_key: str | None = None
    google_cse_cx: str | None = None
    gemini_api_key: str | None = None
    gemini_gcp_project_id: str | None = None
    gemini_gcp_location: str | None = None
    litellm_api_key: str | None = None


CacheKind = Literal["search", "scrape", "llm"]


class Settings(BaseModel):
    search: SearchSettings
    scrape: ScrapeSettings
    validation: ValidationSettings
    llm: LlmSettings
    cache: CacheSettings
    logging: LoggingSettings
    server: ServerSettings
    credentials: Credentials = Field(default_factory=Credentials)

    def cache_path(self, kind: CacheKind) -> Path:
        """Absolute path to the JSONL cache file for ``kind``."""
        fname = {
            "search": self.cache.search_file,
            "scrape": self.cache.scrape_file,
            "llm": self.cache.llm_file,
        }[kind]
        base = Path(self.cache.dir)
        if not base.is_absolute():
            base = REPO_ROOT / base
        return base / fname

    def log_path(self) -> Path:
        base = Path(self.logging.dir)
        if not base.is_absolute():
            base = REPO_ROOT / base
        return base / self.logging.file


def _read_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"expected YAML mapping at {path}, got {type(data).__name__}")
    return data


def _resolve_credentials(raw: dict[str, Any]) -> dict[str, Any]:
    """Build the flat :class:`Credentials` dict by resolving env-var pointers.

    The YAML schema is intentionally explicit: each secret field names the
    *env var* that carries it (``api_key_env: "GEMINI_API_KEY"``). Non-secret
    identity fields (``gcp_project_id``, ``gcp_location``) are read verbatim.
    """
    serpapi_raw = raw.get("serpapi") or {}
    cse_raw = raw.get("google_cse") or {}
    gemini_raw = raw.get("gemini") or {}
    litellm_raw = raw.get("litellm") or {}

    def _env(name: str | None) -> str | None:
        if not name:
            return None
        val = os.environ.get(name)
        return val if val else None

    return {
        "serpapi_api_key": _env(serpapi_raw.get("api_key_env")),
        "google_cse_api_key": _env(cse_raw.get("api_key_env")),
        "google_cse_cx": _env(cse_raw.get("cx_env")),
        "gemini_api_key": _env(gemini_raw.get("api_key_env")),
        "gemini_gcp_project_id": gemini_raw.get("gcp_project_id"),
        "gemini_gcp_location": gemini_raw.get("gcp_location"),
        "litellm_api_key": _env(litellm_raw.get("api_key_env")),
    }


def load_settings(
    *,
    parameters_path: Path,
    credentials_path: Path,
    dotenv_path: Path | None = None,
) -> Settings:
    """Load + validate ``parameters.yaml`` + optional ``credentials.yaml``.

    Also loads ``.env`` (from ``dotenv_path`` or the repo root) into the
    process environment if present. The loader does **not** overwrite
    variables that are already set — real environment wins, so CI/container
    secrets are never shadowed by a stray ``.env``.

    Raises:
        FileNotFoundError: if ``parameters_path`` is missing.
        ValueError: if validation fails.
    """
    parameters_path = Path(parameters_path)
    credentials_path = Path(credentials_path)

    # Populate env from .env (no-op if absent). `override=False` so real env wins.
    env_file = Path(dotenv_path) if dotenv_path else REPO_ROOT / ".env"
    if env_file.exists():
        load_dotenv(env_file, override=False)

    if not parameters_path.exists():
        raise FileNotFoundError(parameters_path)

    params = _read_yaml(parameters_path)

    creds_flat: dict[str, Any] = {}
    if credentials_path.exists():
        creds_flat = _resolve_credentials(_read_yaml(credentials_path))

    try:
        return Settings(**params, credentials=Credentials(**creds_flat))
    except ValidationError as e:
        raise ValueError(f"invalid settings: {e}") from e
