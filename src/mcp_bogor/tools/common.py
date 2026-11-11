"""Kerangka bersama ketiga tool: dependensi, validasi → amplop, galat → amplop, log.

Setiap tool dijalankan lewat `run_tool`, yang menjamin:
- Argumen mentah divalidasi model kontrak (`models/params`, extra="forbid"); pelanggaran,
  termasuk argumen di luar skema, → INVALID_PARAM berbentuk amplop, bukan galat SDK.
- Galat yang diketahui (`ToolFailure`) → amplop status=error dengan pesan templat (KB-10).
- Galat tak terduga → INTERNAL; rinciannya hanya jenis exception di log (tanpa isi baris).
- Satu baris log `tool_call` per panggilan: tool, request_id, slug, status, kode galat,
  durasi, ukuran respons, truncated, kode notice. Tidak ada kueri, nilai filter, atau baris.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from mcp_bogor.config import WIB, Settings
from mcp_bogor.logging import get_logger, log_event
from mcp_bogor.models.envelope import ToolEnvelope, error
from mcp_bogor.models.errors import (
    PARAM_NAMES,
    UNKNOWN_ARGUMENT_FALLBACK,
    UNKNOWN_ARGUMENT_RE,
    ErrorCode,
    ToolName,
)
from mcp_bogor.services.budget import compact_json_bytes
from mcp_bogor.storage import db

LOGGER = get_logger(__name__)

ParamsT = TypeVar("ParamsT", bound=BaseModel)

# Parameter yang dilaporkan bila galat validasi tidak menunjuk parameter tertentu.
PRIMARY_PARAM: dict[str, str] = {
    "search_datasets": "query",
    "get_dataset_info": "slug",
    "get_dataset_data": "slug",
}
_LIMIT_KEYS = ("le", "lt", "max_length")


def _now() -> datetime:
    return datetime.now(WIB)


@dataclass
class ToolDeps:
    """Dependensi tool satu proses (KB-26). Langkah 8b/8c menambah adaptor dan cache."""

    settings: Settings
    now: Callable[[], datetime] = _now
    monotonic: Callable[[], float] = field(default=time.monotonic)

    def open_catalog(self) -> sqlite3.Connection:
        """Katalog baca saja (ADR-010). CatalogNotReady → INTERNAL di run_tool."""
        return db.connect_read(self.settings.db_path)


class ToolFailure(Exception):
    """Galat yang diketahui; diubah run_tool menjadi amplop dengan pesan templat."""

    def __init__(self, code: ErrorCode, **args: Any) -> None:
        super().__init__(code.value)
        self.code = code
        self.args_ = args


def request_id_of(raw: Mapping[str, Any]) -> str | None:
    value = raw.get("request_id")
    if isinstance(value, str) and 1 <= len(value) <= 64:
        return value
    return None


def invalid_param_envelope(
    tool: ToolName,
    exc: ValidationError,
    request_id: str | None,
    result_type: type[Any],
) -> ToolEnvelope[Any]:
    """INVALID_PARAM untuk galat validasi pertama; `limit` diambil dari batas Pydantic."""
    first = exc.errors()[0]
    loc = first.get("loc") or ()
    head = loc[0] if loc else None
    if first.get("type") == "extra_forbidden":
        # Argumen di luar skema: sebut namanya bila berbentuk identifier (KB-42 revisi).
        name = head if isinstance(head, str) and UNKNOWN_ARGUMENT_RE.match(head) else None
        return error(
            ErrorCode.INVALID_PARAM,
            param=name or UNKNOWN_ARGUMENT_FALLBACK,
            unknown=True,
            request_id=request_id,
            tool=tool,
            result_type=result_type,
        )
    param = head if isinstance(head, str) and head in PARAM_NAMES else PRIMARY_PARAM[tool]
    ctx = first.get("ctx") or {}
    limit = next((ctx[k] for k in _LIMIT_KEYS if isinstance(ctx.get(k), int) and ctx[k] >= 0), None)
    return error(
        ErrorCode.INVALID_PARAM,
        param=param,
        limit=limit,
        request_id=request_id,
        tool=tool,
        result_type=result_type,
    )


async def run_tool(
    tool: ToolName,
    deps: ToolDeps,
    raw_args: Mapping[str, Any],
    params_model: type[ParamsT],
    handler: Callable[[ParamsT, ToolDeps], Awaitable[ToolEnvelope[Any]]],
    result_type: type[Any],
    *,
    context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Jalankan satu tool dan kembalikan amplop sebagai dict JSON."""
    started = deps.monotonic()
    request_id = request_id_of(raw_args)
    slug = raw_args.get("slug") if isinstance(raw_args.get("slug"), str) else None
    error_kind: str | None = None
    try:
        params = params_model.model_validate(dict(raw_args), context=dict(context or {}))
    except ValidationError as exc:
        envelope = invalid_param_envelope(tool, exc, request_id, result_type)
    else:
        try:
            envelope = await handler(params, deps)
        except ToolFailure as failure:
            envelope = error(
                failure.code,
                request_id=request_id,
                tool=tool,
                result_type=result_type,
                **failure.args_,
            )
        except Exception as exc:  # galat tak terduga → INTERNAL, rincian hanya di log
            error_kind = type(exc).__name__
            envelope = error(
                ErrorCode.INTERNAL, request_id=request_id, tool=tool, result_type=result_type
            )
    payload = envelope.model_dump(mode="json")
    log_event(
        LOGGER,
        "tool_call",
        tool=tool,
        request_id=request_id,
        slug=slug,
        status=envelope.status,
        error_code=envelope.error.code.value if envelope.error else None,
        exception=error_kind,
        served_from=envelope.provenance.served_from if envelope.provenance else None,
        truncated=envelope.truncated,
        notices=[n.code.value for n in envelope.notices],
        size_bytes=compact_json_bytes(payload),
        duration_ms=round((deps.monotonic() - started) * 1000, 1),
    )
    return payload
