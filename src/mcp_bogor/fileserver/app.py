"""File server data_ref :8799 (ADR-005, ADR-008, Dok 4 bagian 6). Langkah 7.

Hanya GET/HEAD /d/{cache_id}?exp=&sig=. Urutan cek: tanda tangan (403), kedaluwarsa (410),
berkas (404); galat disk → 503. Tanda tangan dicek lebih dulu agar tautan palsu tidak bisa
memancing informasi kedaluwarsa atau keberadaan berkas.

Log hanya memuat cache_id (bila bentuknya sah), status, ukuran, dan durasi; tidak pernah
sig, exp, atau query string. Akses log uvicorn dimatikan karena mencatat query string.
Tidak ada middleware CORS: file server dipanggil backend, bukan browser.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from pathlib import Path

import anyio
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from mcp_bogor.config import Settings
from mcp_bogor.fileserver.signing import verification_keys, verify
from mcp_bogor.health import fileserver_health
from mcp_bogor.logging import get_logger, log_event
from mcp_bogor.storage.cache import is_valid_cache_id, read_data_file

LOGGER = get_logger(__name__)

SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
}
_ERROR_TEXT = {
    403: "forbidden",
    404: "not_found",
    410: "expired",
    503: "unavailable",
}


def _error(status: int) -> Response:
    return JSONResponse(
        {"error": _ERROR_TEXT[status]}, status_code=status, headers=SECURITY_HEADERS
    )


def create_app(
    settings: Settings,
    *,
    keys: Sequence[bytes] | None = None,
    cache_dir: Path | None = None,
    clock: Callable[[], float] = time.time,
    monotonic: Callable[[], float] = time.monotonic,
) -> Starlette:
    """Aplikasi Starlette. Gagal (ConfigError) bila secret tidak memenuhi KB-06."""
    accepted = list(keys) if keys is not None else verification_keys(settings)
    root = cache_dir or settings.cache_dir

    async def serve(request: Request) -> Response:
        started = monotonic()
        cache_id = request.path_params["cache_id"]
        status, size, response = await _handle(request, cache_id)
        log_event(
            LOGGER,
            "fileserver_request",
            method=request.method,
            cache_id=cache_id if is_valid_cache_id(cache_id) else None,
            status=status,
            size_bytes=size,
            duration_ms=round((monotonic() - started) * 1000, 1),
        )
        return response

    async def _handle(request: Request, cache_id: str) -> tuple[int, int, Response]:
        exp = request.query_params.get("exp", "")
        sig = request.query_params.get("sig", "")
        if not verify(accepted, cache_id, exp, sig):
            return 403, 0, _error(403)
        if clock() >= int(exp):
            return 410, 0, _error(410)
        try:
            body = await anyio.to_thread.run_sync(read_data_file, root, cache_id)
        except FileNotFoundError:
            return 404, 0, _error(404)
        except OSError:
            return 503, 0, _error(503)
        response = Response(body, media_type="application/json", headers=SECURITY_HEADERS)
        return 200, len(body), response

    async def healthz(request: Request) -> Response:
        status, body = fileserver_health(root)
        return JSONResponse(body, status_code=status, headers=SECURITY_HEADERS)

    return Starlette(
        routes=[
            Route("/d/{cache_id}", serve, methods=["GET", "HEAD"]),
            Route("/healthz", healthz, methods=["GET"]),
        ]
    )


def run(settings: Settings) -> None:  # pragma: no cover - dijalankan lewat __main__
    """Jalankan file server di MCP_FILESERVER_HOST:MCP_FILESERVER_PORT (bawaan 127.0.0.1)."""
    import uvicorn

    app = create_app(settings)  # menolak start bila secret tidak sah (KB-06)
    uvicorn.run(
        app,
        host=settings.fileserver_host,
        port=settings.fileserver_port,
        access_log=False,  # access log uvicorn memuat query string (sig, exp)
        server_header=False,
        log_config=None,  # pakai handler JSON stderr (KB-48)
    )
