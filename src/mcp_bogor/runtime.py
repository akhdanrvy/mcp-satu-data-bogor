"""Menjalankan server MCP (Langkah 9): satu ToolDeps per proses, stdio atau streamable-http.

- `python -m mcp_bogor` → stdio (bawaan); `MCP_TRANSPORT=streamable-http` → HTTP di
  MCP_HTTP_HOST:MCP_HTTP_PORT, endpoint /mcp (stateless, respons JSON; KB-47) + GET /healthz.
- File server data_ref adalah proses terpisah: `python -m mcp_bogor.fileserver`.
- Mode stdio: stdout adalah kanal protokol. Semua log ke stderr (KB-48), tanpa print.
- Pemeriksaan saat start: katalog belum disinkron → peringatan; secret data_ref atau
  User-Agent tidak sah → HTTP menolak start (kode 2), stdio memberi peringatan keras dan tool
  yang membutuhkannya mengembalikan INTERNAL.
- HTTP: proteksi DNS rebinding SDK, token bearer opsional (MCP_HTTP_AUTH_TOKEN; /healthz
  dikecualikan), tanpa CORS, tanpa access log.
"""

from __future__ import annotations

import hmac
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import anyio
from mcp.server.stdio import stdio_server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import ValidationError
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from starlette.types import Receive, Scope, Send

from mcp_bogor.adapters.base import SourceAdapter
from mcp_bogor.adapters.opendata_jabar import OpenDataJabarAdapter
from mcp_bogor.config import ConfigError, Settings
from mcp_bogor.fileserver.signing import DataRefSigner
from mcp_bogor.health import catalog_status, server_health, server_version
from mcp_bogor.logging import configure_logging, get_logger, log_event, log_warning
from mcp_bogor.server import build_server
from mcp_bogor.storage.cache import DataCache
from mcp_bogor.tools.common import ToolDeps

LOGGER = get_logger(__name__)
NO_STORE = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}


@dataclass
class StartupReport:
    problems: dict[str, str] = field(default_factory=dict)  # komponen → alasan
    catalog_ready: bool = False


def build_deps(
    settings: Settings, *, strict: bool, adapter: SourceAdapter | None = None
) -> tuple[ToolDeps, StartupReport]:
    """Satu ToolDeps per proses (KB-26). strict=True (HTTP): masalah konfigurasi → ConfigError."""
    report = StartupReport()
    if adapter is None:
        try:
            adapter = OpenDataJabarAdapter(settings)
        except ConfigError as exc:
            report.problems["user_agent"] = str(exc)
    data_cache: DataCache | None = None
    signer: DataRefSigner | None = None
    try:
        data_cache = DataCache.from_settings(settings)
        signer = DataRefSigner(settings)
    except ConfigError as exc:
        report.problems["dataref_secret"] = str(exc)
    report.catalog_ready = bool(catalog_status(settings.db_path)["ready"])

    if strict and report.problems:
        raise ConfigError("; ".join(report.problems.values()))
    for component, reason in report.problems.items():
        log_warning(LOGGER, "startup_degraded", component=component, reason=reason)
    if not report.catalog_ready:
        log_warning(
            LOGGER,
            "catalog_not_ready",
            reason="katalog belum disinkron; jalankan python -m mcp_bogor.sync_worker --once",
        )
    deps = ToolDeps(settings, adapter=adapter, data_cache=data_cache, signer=signer)
    return deps, report


async def close_deps(deps: ToolDeps) -> None:
    adapter = deps.adapter
    close = getattr(adapter, "aclose", None)
    if close is not None:
        await close()


# ---------------------------------------------------------------- stdio


async def run_stdio(deps: ToolDeps) -> None:
    server = build_server(deps)
    try:
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())
    finally:
        await close_deps(deps)


# ---------------------------------------------------------------- streamable-http


class McpEndpoint:
    """ASGI /mcp: cek token bearer (bila dikonfigurasi), lalu serahkan ke session manager SDK."""

    def __init__(self, manager: StreamableHTTPSessionManager, token: str) -> None:
        self._manager = manager
        self._expected = f"Bearer {token}".encode() if token else None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if self._expected is not None and scope["type"] == "http":
            given = dict(scope.get("headers") or []).get(b"authorization", b"")
            if not hmac.compare_digest(given, self._expected):
                log_event(LOGGER, "http_unauthorized", path=scope.get("path"))
                response = JSONResponse(
                    {"error": "unauthorized"},
                    status_code=401,
                    headers={**NO_STORE, "WWW-Authenticate": "Bearer"},
                )
                await response(scope, receive, send)
                return
        await self._manager.handle_request(scope, receive, send)


def build_http_app(deps: ToolDeps) -> Starlette:
    settings = deps.settings
    manager = StreamableHTTPSessionManager(
        app=build_server(deps),
        json_response=True,
        stateless=True,
        security_settings=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=settings.allowed_hosts(),
            allowed_origins=settings.allowed_origins(),
        ),
    )

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        async with manager.run():
            try:
                yield
            finally:
                await close_deps(deps)

    token = settings.http_auth_token.get_secret_value()
    expected = f"Bearer {token}".encode() if token else None

    async def healthz(request: Request) -> Response:
        """Tanpa deep: tidak pernah ke portal. ?deep=1: hanya bila token dikonfigurasi dan
        header Bearer benar (salah/kosong → 401); tanpa token dikonfigurasi, deep diabaikan."""
        deep = request.query_params.get("deep", "").lower() in {"1", "true", "yes"}
        if deep and expected is None:
            deep = False
        if deep and expected is not None:
            given = request.headers.get("authorization", "").encode()
            if not hmac.compare_digest(given, expected):
                log_event(LOGGER, "http_unauthorized", path="/healthz")
                return JSONResponse(
                    {"error": "unauthorized"},
                    status_code=401,
                    headers={**NO_STORE, "WWW-Authenticate": "Bearer"},
                )
        status, body = await server_health(settings, deps.adapter, deep=deep)
        return JSONResponse(body, status_code=status, headers=NO_STORE)

    return Starlette(
        routes=[
            Route("/mcp", endpoint=McpEndpoint(manager, token)),
            Route("/healthz", healthz, methods=["GET"]),
        ],
        lifespan=lifespan,
    )


def run_http(deps: ToolDeps) -> None:  # pragma: no cover - uji lewat build_http_app + uvicorn
    import uvicorn

    settings = deps.settings
    uvicorn.run(
        build_http_app(deps),
        host=settings.http_host,
        port=settings.http_port,
        access_log=False,
        server_header=False,
        log_config=None,  # handler JSON stderr (KB-48)
    )


# ---------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> int:
    try:
        settings = Settings()
    except ValidationError as exc:
        print(f"Konfigurasi tidak sah: {exc.error_count()} galat", file=sys.stderr)
        return 2
    configure_logging(settings.log_level)
    http = settings.transport == "streamable-http"
    try:
        deps, report = build_deps(settings, strict=http)
    except ConfigError as exc:
        log_warning(LOGGER, "startup_refused", transport=settings.transport, reason=str(exc))
        return 2
    info: dict[str, Any] = {
        "transport": settings.transport,
        "version": server_version(),
        "tool_prefix": settings.tool_prefix,
        "catalog_ready": report.catalog_ready,
    }
    if http:
        info.update(host=settings.http_host, port=settings.http_port)
    log_event(LOGGER, "server_start", **info)
    try:
        if http:
            run_http(deps)
        else:
            anyio.run(run_stdio, deps)
    except KeyboardInterrupt:  # pragma: no cover - interaktif
        pass
    log_event(LOGGER, "server_stop", transport=settings.transport)
    return 0
