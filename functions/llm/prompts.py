"""YAML-based prompt templates.

Every prompt file carries two top-level string fields:

.. code-block:: yaml

    system: "..."
    user: |
      ...{placeholder}...

``PromptTemplate.render(**fields)`` does a ``str.format``-style substitution
on the user message. Literal curly braces should be doubled (``{{`` / ``}}``)
exactly as in :func:`str.format`.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class PromptTemplate:
    system: str
    user: str

    def render(self, **fields: object) -> str:
        """Substitute ``{name}`` placeholders in ``user`` with supplied values.

        Unknown ``fields`` kwargs are silently ignored; missing required
        placeholders raise :class:`KeyError`.
        """
        return self.user.format_map(_SafeFormatMap(fields))


class _SafeFormatMap(dict):
    """Dict that raises KeyError on missing keys (same as default), but is
    explicit about intent and keeps :meth:`PromptTemplate.render` readable.
    """

    def __missing__(self, key: str) -> str:  # pragma: no cover - trivial
        raise KeyError(key)


def load_prompt(path: Path) -> PromptTemplate:
    """Load a prompt YAML into a :class:`PromptTemplate`.

    Raises:
        FileNotFoundError: if ``path`` is missing.
        ValueError: if the YAML is missing required sections or has wrong shape.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)

    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    if not isinstance(data, dict):
        raise ValueError(f"prompt YAML must be a mapping, got {type(data).__name__}")
    for required in ("system", "user"):
        if required not in data:
            raise ValueError(f"prompt YAML at {path} missing required key {required!r}")
        if not isinstance(data[required], str):
            raise ValueError(f"prompt field {required!r} in {path} must be a string")

    return PromptTemplate(system=data["system"], user=data["user"])
