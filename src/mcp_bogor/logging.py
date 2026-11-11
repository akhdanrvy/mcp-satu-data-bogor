"""Log JSON: request_id, slug, tool, durasi, status, ukuran; tanpa isi baris data.

`log_event` menulis satu baris JSON per kejadian. `configure_logging` (KB-48) memasang SATU
handler ke stderr dengan level MCP_LOG_LEVEL. Mode stdio memakai stdout sebagai kanal protokol
MCP, jadi log tidak boleh pernah ke stdout. Pesan pustaka lain (SDK MCP, uvicorn) dibungkus
`{"event": "log", ...}`; exception hanya dicatat jenisnya, tanpa traceback (bisa memuat nilai).
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any, TextIO

_HANDLER_MARK = "_mcp_bogor_handler"


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def log_event(logger: logging.Logger, event: str, **fields: Any) -> None:
    """Satu baris JSON per kejadian. Pemanggil bertanggung jawab tidak mengirim isi baris."""
    logger.info(json.dumps({"event": event, **fields}, ensure_ascii=False, default=str))


def log_warning(logger: logging.Logger, event: str, **fields: Any) -> None:
    logger.warning(json.dumps({"event": event, **fields}, ensure_ascii=False, default=str))


class JsonFormatter(logging.Formatter):
    """Pesan log_event apa adanya; pesan lain dibungkus objek JSON satu baris."""

    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        if message.startswith("{"):
            try:
                parsed = json.loads(message)
            except ValueError:
                parsed = None
            if isinstance(parsed, dict) and "event" in parsed:
                if record.levelno >= logging.WARNING and "level" not in parsed:
                    parsed = {**parsed, "level": record.levelname}
                    return json.dumps(parsed, ensure_ascii=False, default=str)
                return message
        payload: dict[str, Any] = {
            "event": "log",
            "logger": record.name,
            "level": record.levelname,
            "message": message.replace("\n", " "),
        }
        if record.exc_info and record.exc_info[0] is not None:
            payload["exception"] = record.exc_info[0].__name__
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: str = "INFO", stream: TextIO | None = None) -> logging.Handler:
    """Pasang satu handler JSON di root logger (idempoten). Bawaan ke stderr."""
    root = logging.getLogger()
    for existing in list(root.handlers):
        if getattr(existing, _HANDLER_MARK, False):
            root.removeHandler(existing)
    handler = logging.StreamHandler(stream if stream is not None else sys.stderr)
    handler.setFormatter(JsonFormatter())
    setattr(handler, _HANDLER_MARK, True)
    root.addHandler(handler)
    root.setLevel(level)
    return handler
