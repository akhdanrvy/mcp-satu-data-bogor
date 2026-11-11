"""GET /healthz (Langkah 9): status server tanpa data dataset.

:8800 → versi, kesiapan katalog (catalog_synced_at, jumlah record, sinkron terakhir), dan
kesehatan portal = hasil `adapter.health()` terakhir yang tersimpan + umurnya, TANPA memanggil
portal (KB-47 revisi). `deep=True` (hanya `?deep=1` dengan token bearer benar) memanggil
`adapter.health()`, yang tetap di-cache adaptor 60 detik.
:8799 → versi dan keterbacaan folder cache, tanpa portal. 200 bila siap, 503 bila tidak.
"""

from __future__ import annotations

import os
import sqlite3
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from mcp_bogor.config import CONTRACT_VERSION, Settings
from mcp_bogor.services.catalog import catalog_synced_at
from mcp_bogor.storage import db

PACKAGE = "mcp-bogor"


def server_version() -> str:
    try:
        return version(PACKAGE)
    except PackageNotFoundError:  # pragma: no cover - paket selalu terpasang lewat uv
        return "0.0.0"


def catalog_status(db_path: Path) -> dict[str, Any]:
    """Kesiapan katalog dari SQLite baca-saja; tanpa isi dataset."""
    try:
        conn = db.connect_read(db_path)
    except db.CatalogNotReady:
        return {"ready": False, "synced_at": None, "records": 0, "last_sync": None}
    try:
        synced = catalog_synced_at(conn)
        records = conn.execute("SELECT count(*) FROM datasets").fetchone()[0]
        last = conn.execute(
            "SELECT status, started_at, finished_at, error_kind FROM sync_runs "
            "WHERE status != 'skipped' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    except sqlite3.Error:
        return {"ready": False, "synced_at": None, "records": 0, "last_sync": None}
    finally:
        conn.close()
    return {
        "ready": synced is not None and records > 0,
        "synced_at": synced.isoformat() if synced else None,
        "records": records,
        "last_sync": dict(last) if last is not None else None,
    }


def portal_status(adapter: Any | None) -> dict[str, Any] | None:
    """Hasil health() terakhir yang tersimpan di adaptor + umurnya; TANPA permintaan ke portal."""
    if adapter is None:
        return None
    last = adapter.last_health()
    if last is None:
        return {"checked": False}
    health, age = last
    return {"checked": True, "ok": health.ok, "status": health.status, "age_s": round(age, 1)}


async def server_health(
    settings: Settings, adapter: Any | None, *, deep: bool = False
) -> tuple[int, dict[str, Any]]:
    """deep=True memanggil adapter.health() (tetap di-cache adaptor 60 dtk); bawaan tidak."""
    catalog = catalog_status(settings.db_path)
    if deep and adapter is not None:
        await adapter.health()
    portal = portal_status(adapter)
    body = {
        "service": "mcp-kota-bogor",
        "version": server_version(),
        "contract_version": CONTRACT_VERSION,
        "transport": settings.transport,
        "catalog": catalog,
        "portal": portal,
    }
    return (200 if catalog["ready"] else 503), body


def fileserver_health(cache_dir: Path) -> tuple[int, dict[str, Any]]:
    readable = cache_dir.is_dir() and os.access(cache_dir, os.R_OK)
    body = {
        "service": "mcp-kota-bogor-fileserver",
        "version": server_version(),
        "contract_version": CONTRACT_VERSION,
        "cache_readable": readable,
    }
    return (200 if readable else 503), body
