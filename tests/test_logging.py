"""TDD tests for functions.utils.logging."""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from functions.utils.logging import JsonLineFormatter, configure_logging


class TestJsonLineFormatter:
    def test_emits_single_json_line(self):
        fmt = JsonLineFormatter()
        record = logging.LogRecord(
            name="a2a.test",
            level=logging.INFO,
            pathname=__file__,
            lineno=10,
            msg="hello %s",
            args=("world",),
            exc_info=None,
        )
        out = fmt.format(record)
        assert "\n" not in out
        parsed = json.loads(out)
        assert parsed["level"] == "INFO"
        assert parsed["logger"] == "a2a.test"
        assert parsed["msg"] == "hello world"
        assert "ts" in parsed

    def test_includes_extra_fields(self):
        fmt = JsonLineFormatter()
        logger = logging.getLogger("a2a.extra")
        logger.setLevel(logging.INFO)
        handler = logging.Handler()
        handler.setFormatter(fmt)
        logger.addHandler(handler)

        captured: list[str] = []
        handler.emit = lambda r: captured.append(fmt.format(r))  # type: ignore[assignment]
        logger.info("stage done", extra={"stage": "search", "hits": 7})

        parsed = json.loads(captured[0])
        assert parsed["stage"] == "search"
        assert parsed["hits"] == 7

    def test_formats_exception_as_field(self):
        fmt = JsonLineFormatter()
        try:
            raise ValueError("boom")
        except ValueError:
            import sys

            record = logging.LogRecord(
                name="a2a.test",
                level=logging.ERROR,
                pathname=__file__,
                lineno=1,
                msg="failed",
                args=None,
                exc_info=sys.exc_info(),
            )
        parsed = json.loads(fmt.format(record))
        assert parsed["level"] == "ERROR"
        assert "ValueError" in parsed["exc"]
        assert "boom" in parsed["exc"]


class TestConfigureLogging:
    def test_installs_file_and_stream_handlers(self, tmp_path: Path):
        log_file = tmp_path / "agent.log"
        configure_logging(level="DEBUG", log_file=log_file)

        root = logging.getLogger()
        managed = [h for h in root.handlers if getattr(h, "_a2a_managed", False)]
        kinds = {type(h).__name__ for h in managed}
        assert "RotatingFileHandler" in kinds
        assert "StreamHandler" in kinds
        assert root.level == logging.DEBUG

    def test_handlers_use_json_formatter(self, tmp_path: Path):
        log_file = tmp_path / "agent.log"
        configure_logging(level="INFO", log_file=log_file)

        root = logging.getLogger()
        managed = [h for h in root.handlers if getattr(h, "_a2a_managed", False)]
        assert managed, "configure_logging should install at least one managed handler"
        for h in managed:
            assert isinstance(h.formatter, JsonLineFormatter)

    def test_file_handler_writes_json_lines(self, tmp_path: Path):
        log_file = tmp_path / "agent.log"
        configure_logging(level="INFO", log_file=log_file)

        logging.getLogger("a2a.core.search").info(
            "query issued", extra={"stage": "search", "provider": "serpapi"}
        )
        for h in logging.getLogger().handlers:
            h.flush()

        lines = log_file.read_text(encoding="utf-8").splitlines()
        assert lines, "expected at least one log line written"
        parsed = json.loads(lines[-1])
        assert parsed["logger"] == "a2a.core.search"
        assert parsed["stage"] == "search"
        assert parsed["provider"] == "serpapi"

    def test_reinvoking_does_not_duplicate_handlers(self, tmp_path: Path):
        log_file = tmp_path / "agent.log"
        configure_logging(level="INFO", log_file=log_file)
        managed_first = [
            h for h in logging.getLogger().handlers if getattr(h, "_a2a_managed", False)
        ]
        configure_logging(level="INFO", log_file=log_file)
        managed_second = [
            h for h in logging.getLogger().handlers if getattr(h, "_a2a_managed", False)
        ]
        assert len(managed_first) == len(managed_second) == 2

    def test_creates_parent_dir(self, tmp_path: Path):
        log_file = tmp_path / "nested" / "dir" / "agent.log"
        configure_logging(level="INFO", log_file=log_file)
        logging.getLogger("a2a.test").info("hi")
        for h in logging.getLogger().handlers:
            h.flush()
        assert log_file.exists()

    def test_rejects_bogus_level(self, tmp_path: Path):
        with pytest.raises(ValueError):
            configure_logging(level="NOT_A_LEVEL", log_file=tmp_path / "x.log")
