"""TDD tests for functions.utils.config.

Credentials design:
- ``configs/credentials.yaml`` is **committed**. It stores the *names* of env
  vars that carry secrets, plus optional non-secret identity fields
  (e.g. GCP project/location).
- Actual secrets live in the environment (populated from ``.env`` or CI
  secrets), never in files under version control.
"""
from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from functions.utils.config import Settings, load_settings


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dedent(content), encoding="utf-8")


@pytest.fixture()
def config_dir(tmp_path: Path) -> Path:
    params = tmp_path / "parameters.yaml"
    creds = tmp_path / "credentials.yaml"
    _write(
        params,
        """
        search:
          top_k_results: 5
          top_k_to_scrape: 2
          timeout_seconds: 10
          serpapi_engine: google
          serpapi_hl: en
          serpapi_gl: us
          cse_num: 10
        scrape:
          timeout_seconds: 15
          max_chars_per_page: 5000
          concurrency: 2
          user_agent: "A2A/test"
        validation:
          min_relevance: 0.5
          min_trustworthiness: 0.3
        llm:
          model: gemini-2.5-flash
          temperature: 0.1
          max_output_tokens: 1024
          request_timeout_seconds: 30
          retries: 1
        cache:
          enabled: true
          dir: artifacts/cache
          search_file: search_cache.jsonl
          scrape_file: scrape_cache.jsonl
          llm_file: llm_cache.jsonl
        logging:
          level: DEBUG
          dir: artifacts/logs
          file: agent.log
          max_bytes: 1024
          backup_count: 2
        server:
          host: 127.0.0.1
          port: 9000
        """,
    )
    _write(
        creds,
        """
        serpapi:
          api_key_env: TEST_SERPAPI_KEY
        google_cse:
          api_key_env: TEST_CSE_KEY
          cx_env: TEST_CSE_CX
        gemini:
          api_key_env: TEST_GEMINI_KEY
          gcp_project_id: null
          gcp_location: null
        """,
    )
    return tmp_path


class TestLoadSettings:
    def test_returns_settings_instance(self, config_dir: Path, monkeypatch):
        monkeypatch.setenv("TEST_SERPAPI_KEY", "sk-serp")
        monkeypatch.setenv("TEST_CSE_KEY", "key-cse")
        monkeypatch.setenv("TEST_CSE_CX", "cx-123")
        monkeypatch.setenv("TEST_GEMINI_KEY", "gm-xyz")
        s = load_settings(
            parameters_path=config_dir / "parameters.yaml",
            credentials_path=config_dir / "credentials.yaml",
        )
        assert isinstance(s, Settings)

    def test_parameters_merged(self, config_dir: Path, monkeypatch):
        monkeypatch.setenv("TEST_GEMINI_KEY", "gm-xyz")
        s = load_settings(
            parameters_path=config_dir / "parameters.yaml",
            credentials_path=config_dir / "credentials.yaml",
        )
        assert s.search.top_k_results == 5
        assert s.search.top_k_to_scrape == 2
        assert s.llm.model == "gemini-2.5-flash"
        assert s.cache.search_file == "search_cache.jsonl"
        assert s.server.port == 9000

    def test_secrets_resolved_from_env(self, config_dir: Path, monkeypatch):
        monkeypatch.setenv("TEST_SERPAPI_KEY", "sk-serp")
        monkeypatch.setenv("TEST_CSE_KEY", "key-cse")
        monkeypatch.setenv("TEST_CSE_CX", "cx-123")
        monkeypatch.setenv("TEST_GEMINI_KEY", "gm-xyz")
        s = load_settings(
            parameters_path=config_dir / "parameters.yaml",
            credentials_path=config_dir / "credentials.yaml",
        )
        assert s.credentials.serpapi_api_key == "sk-serp"
        assert s.credentials.google_cse_api_key == "key-cse"
        assert s.credentials.google_cse_cx == "cx-123"
        assert s.credentials.gemini_api_key == "gm-xyz"

    def test_unset_env_vars_yield_none(self, config_dir: Path, monkeypatch):
        """If the env var named in credentials.yaml is unset, value is None — not an error."""
        for name in ("TEST_SERPAPI_KEY", "TEST_CSE_KEY", "TEST_CSE_CX", "TEST_GEMINI_KEY"):
            monkeypatch.delenv(name, raising=False)
        s = load_settings(
            parameters_path=config_dir / "parameters.yaml",
            credentials_path=config_dir / "credentials.yaml",
        )
        assert s.credentials.serpapi_api_key is None
        assert s.credentials.google_cse_api_key is None
        assert s.credentials.google_cse_cx is None
        assert s.credentials.gemini_api_key is None

    def test_missing_credentials_file_tolerated(self, config_dir: Path):
        """Credentials YAML is optional; everything resolves to None."""
        s = load_settings(
            parameters_path=config_dir / "parameters.yaml",
            credentials_path=config_dir / "does_not_exist.yaml",
        )
        assert s.credentials.gemini_api_key is None
        assert s.credentials.serpapi_api_key is None

    def test_missing_parameters_file_raises(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            load_settings(
                parameters_path=tmp_path / "does_not_exist.yaml",
                credentials_path=tmp_path / "creds.yaml",
            )

    def test_validation_bounds_on_thresholds(self, tmp_path: Path):
        p = tmp_path / "parameters.yaml"
        _write(
            p,
            """
            search: {top_k_results: 1, top_k_to_scrape: 1, timeout_seconds: 10, serpapi_engine: g, serpapi_hl: en, serpapi_gl: us, cse_num: 10}
            scrape: {timeout_seconds: 10, max_chars_per_page: 100, concurrency: 1, user_agent: x}
            validation: {min_relevance: 1.5, min_trustworthiness: 0.0}
            llm: {model: m, temperature: 0.0, max_output_tokens: 10, request_timeout_seconds: 10, retries: 0}
            cache: {enabled: true, dir: x, search_file: s.jsonl, scrape_file: sc.jsonl, llm_file: l.jsonl}
            logging: {level: INFO, dir: x, file: a.log, max_bytes: 1024, backup_count: 1}
            server: {host: 127.0.0.1, port: 8000}
            """,
        )
        with pytest.raises(ValueError):
            load_settings(parameters_path=p, credentials_path=tmp_path / "_.yaml")

    def test_cache_paths_resolve_absolute(self, config_dir: Path, monkeypatch):
        monkeypatch.setenv("TEST_GEMINI_KEY", "x")
        s = load_settings(
            parameters_path=config_dir / "parameters.yaml",
            credentials_path=config_dir / "credentials.yaml",
        )
        search_path = s.cache_path("search")
        assert search_path.is_absolute()
        assert search_path.name == "search_cache.jsonl"

    def test_gemini_gcp_fields_optional(self, config_dir: Path, monkeypatch):
        """gcp_project_id / gcp_location are optional identity fields (null by default)."""
        s = load_settings(
            parameters_path=config_dir / "parameters.yaml",
            credentials_path=config_dir / "credentials.yaml",
        )
        assert s.credentials.gemini_gcp_project_id is None
        assert s.credentials.gemini_gcp_location is None

    def test_gemini_gcp_fields_loaded_when_set(self, tmp_path: Path):
        params = tmp_path / "parameters.yaml"
        creds = tmp_path / "credentials.yaml"
        _write(
            params,
            """
            search: {top_k_results: 1, top_k_to_scrape: 1, timeout_seconds: 10, serpapi_engine: g, serpapi_hl: en, serpapi_gl: us, cse_num: 10}
            scrape: {timeout_seconds: 10, max_chars_per_page: 100, concurrency: 1, user_agent: x}
            validation: {min_relevance: 0.5, min_trustworthiness: 0.5}
            llm: {model: m, temperature: 0.0, max_output_tokens: 10, request_timeout_seconds: 10, retries: 0}
            cache: {enabled: true, dir: x, search_file: s.jsonl, scrape_file: sc.jsonl, llm_file: l.jsonl}
            logging: {level: INFO, dir: x, file: a.log, max_bytes: 1024, backup_count: 1}
            server: {host: 127.0.0.1, port: 8000}
            """,
        )
        _write(
            creds,
            """
            gemini:
              api_key_env: TEST_GEMINI_KEY
              gcp_project_id: my-project
              gcp_location: us-central1
            """,
        )
        s = load_settings(parameters_path=params, credentials_path=creds)
        assert s.credentials.gemini_gcp_project_id == "my-project"
        assert s.credentials.gemini_gcp_location == "us-central1"
