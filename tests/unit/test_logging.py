"""KB-48: satu handler JSON ke stderr, level dari MCP_LOG_LEVEL, tanpa traceback."""

from __future__ import annotations

import io
import json
import logging

import pytest

from mcp_bogor.logging import JsonFormatter, configure_logging, get_logger, log_event, log_warning


def _record(message: str, level: int = logging.INFO, exc: bool = False) -> logging.LogRecord:
    exc_info = None
    if exc:
        try:
            raise ValueError("rahasia 9.160.885")
        except ValueError:
            import sys

            exc_info = sys.exc_info()
    return logging.LogRecord("uji", level, __file__, 1, message, None, exc_info)


def test_formatter_passes_events_and_wraps_others() -> None:
    fmt = JsonFormatter()
    event = json.dumps({"event": "tool_call", "status": "ok"})
    assert fmt.format(_record(event)) == event
    warned = json.loads(fmt.format(_record(event, logging.WARNING)))
    assert warned == {"event": "tool_call", "status": "ok", "level": "WARNING"}
    wrapped = json.loads(fmt.format(_record("Processing request\nof type X")))
    assert wrapped == {
        "event": "log",
        "logger": "uji",
        "level": "INFO",
        "message": "Processing request of type X",
    }
    assert json.loads(fmt.format(_record("{bukan json")))["message"] == "{bukan json"
    assert json.loads(fmt.format(_record('{"tanpa": "event"}')))["event"] == "log"


def test_exception_type_only_no_traceback() -> None:
    line = JsonFormatter().format(_record("gagal", logging.ERROR, exc=True))
    assert json.loads(line)["exception"] == "ValueError"
    assert "rahasia" not in line and "Traceback" not in line


def test_configure_logging_single_handler_and_level(capsys: pytest.CaptureFixture[str]) -> None:
    root = logging.getLogger()
    before = list(root.handlers)
    stream = io.StringIO()
    try:
        configure_logging("WARNING", stream=stream)
        handler = configure_logging("WARNING", stream=stream)  # idempoten
        ours = [h for h in root.handlers if getattr(h, "_mcp_bogor_handler", False)]
        assert ours == [handler] and root.level == logging.WARNING
        logger = get_logger("mcp_bogor.uji")
        log_event(logger, "diam")  # INFO < WARNING
        log_warning(logger, "peringatan", component="x")
        lines = [json.loads(x) for x in stream.getvalue().splitlines()]
        assert lines == [{"event": "peringatan", "component": "x", "level": "WARNING"}]
        assert capsys.readouterr().out == ""  # tidak pernah ke stdout (kanal protokol stdio)
    finally:
        for h in list(root.handlers):
            if h not in before:
                root.removeHandler(h)
        root.setLevel(logging.WARNING)
