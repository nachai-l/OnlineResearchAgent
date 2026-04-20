"""TDD tests for functions.llm.prompts."""
from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from functions.llm.prompts import PromptTemplate, load_prompt


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dedent(content), encoding="utf-8")


class TestLoadPrompt:
    def test_loads_system_and_user_sections(self, tmp_path: Path):
        p = tmp_path / "p.yaml"
        _write(
            p,
            """
            system: "You are a helpful assistant."
            user: |
              Please answer {question} concisely.
            """,
        )
        tmpl = load_prompt(p)
        assert isinstance(tmpl, PromptTemplate)
        assert tmpl.system == "You are a helpful assistant."
        assert "{question}" in tmpl.user

    def test_render_substitutes_named_fields(self, tmp_path: Path):
        p = tmp_path / "p.yaml"
        _write(
            p,
            """
            system: S
            user: "Hello {name}, you are {role}."
            """,
        )
        tmpl = load_prompt(p)
        rendered = tmpl.render(name="Ada", role="engineer")
        assert rendered == "Hello Ada, you are engineer."

    def test_render_rejects_missing_field(self, tmp_path: Path):
        p = tmp_path / "p.yaml"
        _write(p, "system: S\nuser: 'hi {name}'\n")
        tmpl = load_prompt(p)
        with pytest.raises(KeyError):
            tmpl.render()  # missing 'name'

    def test_render_ignores_extra_field(self, tmp_path: Path):
        p = tmp_path / "p.yaml"
        _write(p, "system: S\nuser: 'hi {name}'\n")
        tmpl = load_prompt(p)
        assert tmpl.render(name="x", extra="ignored") == "hi x"

    def test_missing_required_section_raises(self, tmp_path: Path):
        p = tmp_path / "p.yaml"
        _write(p, "system: only_system\n")
        with pytest.raises(ValueError):
            load_prompt(p)

    def test_file_not_found(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            load_prompt(tmp_path / "missing.yaml")

    def test_literal_braces_preserved(self, tmp_path: Path):
        """Curly braces in JSON examples must be escapable with {{ }}."""
        p = tmp_path / "p.yaml"
        _write(
            p,
            """
            system: S
            user: "return: {{\\"ok\\": true}} for query={q}"
            """,
        )
        tmpl = load_prompt(p)
        out = tmpl.render(q="x")
        assert '{"ok": true}' in out
        assert out.endswith("query=x")
