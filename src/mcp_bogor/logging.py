"""Log JSON: request_id, slug, tool, durasi, status, ukuran; tanpa isi baris data.

Langkah 4 baru memakai `log_event` untuk log permintaan adaptor. Konfigurasi handler dan
format untuk server ditambahkan di Langkah 9.
"""

from __future__ import annotations

import json
import logging
from typing import Any


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def log_event(logger: logging.Logger, event: str, **fields: Any) -> None:
    """Satu baris JSON per kejadian. Pemanggil bertanggung jawab tidak mengirim isi baris."""
    logger.info(json.dumps({"event": event, **fields}, ensure_ascii=False, default=str))
