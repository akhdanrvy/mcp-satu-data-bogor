"""Worker sinkron katalog; satu-satunya penulis katalog (ADR-010). Langkah 5: `--once` saja.

Alur `sync_once`:
1. AK-06 (KB-28): sinkron terakhir yang mengirim permintaan memblokir selama
   MCP_CATALOG_SYNC_MIN_INTERVAL_H. Pengecualian: bila statusnya failed/incomplete dan
   penyebabnya BUKAN rate_limited (429), sinkron boleh diulang setelah
   MCP_CATALOG_SYNC_RETRY_AFTER_H. Diblokir → status `skipped`, tanpa permintaan apa pun.
2. Satu instans OpenDataJabarAdapter untuk seluruh proses, dengan jeda
   MCP_SYNC_REQUEST_DELAY_S (KB-26). Dibuat sebelum run dicatat, sehingga galat
   konfigurasi (mis. User-Agent placeholder) tidak memblokir sinkron 24 jam.
3. Run dicatat (status sementara `failed`) SEBELUM permintaan pertama, agar proses yang
   mati di tengah tetap terhitung untuk AK-06.
4. list_datasets, list_organisasi, list_topik → service Langkah 2 → SATU transaksi:
   upsert, hapus yang hilang hanya bila snapshot lengkap (KK-K), FTS dibangun ulang,
   baris sync_runs diperbarui. Galat → rollback; katalog lama utuh.

Status: complete (snapshot lengkap dan referensi terbaca) | incomplete (ada record terbaca,
tanpa penghapusan) | failed (tidak ada record terbaca atau penulisan gagal) | skipped.
Kode keluar: 0 untuk complete/incomplete/skipped, 1 untuk failed, 2 untuk galat konfigurasi.

`--reindex`: bangun ulang kedua FTS dari tabel datasets lokal (mis. setelah stemmer diganti
atau migrasi skema FTS). Tanpa portal dan tidak dicatat di sync_runs (tidak dihitung AK-06).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sqlite3
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any

from mcp_bogor.adapters.base import CatalogSnapshot, SourceAdapter, SourceError
from mcp_bogor.adapters.opendata_jabar import OpenDataJabarAdapter
from mcp_bogor.config import WIB, ConfigError, Settings
from mcp_bogor.logging import get_logger, log_event
from mcp_bogor.services.catalog import WriteCounts, build_dataset_row, write_catalog
from mcp_bogor.services.stemming import STEMMER_NAME, stem_text
from mcp_bogor.storage import db

LOGGER = get_logger(__name__)
INTERRUPTED = "sinkron terhenti sebelum selesai"

# Penyebab galat yang disimpan di sync_runs.error_kind (KB-28).
RATE_LIMITED = "rate_limited"
_ERROR_KINDS = (
    ("SourceRateLimited", RATE_LIMITED),
    ("SourceUnavailable", "source_unavailable"),
    ("SourceNotFound", "not_found"),
    ("SourceProtocolError", "protocol_error"),
)


def error_kind_of(*errors: str | None) -> str | None:
    """Penyebab galat dari teks galat adaptor; rate_limited didahulukan bila ada."""
    present = [e for e in errors if e]
    if not present:
        return None
    for marker, kind in _ERROR_KINDS:
        if any(marker in e for e in present):
            return kind
    return "other"


def _now() -> datetime:
    return datetime.now(WIB)


@dataclass
class SyncReport:
    status: str
    started_at: str
    finished_at: str | None = None
    pages_read: int = 0
    total_page: int | None = None
    total_data: int | None = None
    records: int = 0
    inserted: int = 0
    updated: int = 0
    deleted: int = 0
    skipped_records: int = 0
    duplicates: int = 0
    requests_sent: int = 0
    duration_s: float = 0.0
    error: str | None = None
    error_kind: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def summary(self) -> str:
        return (
            f"Sinkron katalog: {self.status} | halaman {self.pages_read}/{self.total_page or '-'} "
            f"| record {self.records}/{self.total_data or '-'} | baru {self.inserted}, "
            f"diperbarui {self.updated}, dihapus {self.deleted}, dilewati {self.skipped_records}"
            f" | permintaan {self.requests_sent} | {self.duration_s:.1f} dtk"
            + (f" | {self.error}" if self.error else "")
        )


@dataclass(frozen=True)
class LastRun:
    started_at: datetime
    status: str
    error_kind: str | None


def last_request_run(conn: sqlite3.Connection) -> LastRun | None:
    """Sinkron terakhir yang mengirim permintaan (status selain skipped)."""
    row = conn.execute(
        "SELECT started_at, status, error_kind FROM sync_runs "
        "WHERE status != 'skipped' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    return LastRun(datetime.fromisoformat(row[0]), row[1], row[2])


def blocking_reason(last: LastRun | None, now: datetime, settings: Settings) -> str | None:
    """Alasan sinkron harus dilewati (AK-06, KB-28), atau None bila boleh jalan."""
    if last is None:
        return None
    age = now - last.started_at
    full = timedelta(hours=settings.catalog_sync_min_interval_h)
    if age >= full:
        return None
    started = last.started_at.isoformat(timespec="seconds")
    retryable = last.status in ("failed", "incomplete") and last.error_kind != RATE_LIMITED
    if retryable:
        if age >= timedelta(hours=settings.catalog_sync_retry_after_h):
            return None
        return (
            f"sinkron {last.status} terakhir {started}; ulang setelah "
            f"{settings.catalog_sync_retry_after_h:g} jam (KB-28)"
        )
    reason = "dibatasi portal (429)" if last.error_kind == RATE_LIMITED else last.status
    return (
        f"sinkron terakhir {started} ({reason}) masih dalam "
        f"{settings.catalog_sync_min_interval_h:g} jam (AK-06)"
    )


def _insert_run(conn: sqlite3.Connection, report: SyncReport) -> int:
    with db.transaction(conn):
        cursor = conn.execute(
            "INSERT INTO sync_runs (started_at, finished_at, status, error, error_kind) "
            "VALUES (?, ?, ?, ?, ?)",
            (report.started_at, report.finished_at, report.status, report.error, report.error_kind),
        )
    return int(cursor.lastrowid)


def _update_run(conn: sqlite3.Connection, run_id: int, report: SyncReport) -> None:
    conn.execute(
        "UPDATE sync_runs SET finished_at = ?, status = ?, pages_read = ?, total_data = ?, "
        "records = ?, inserted = ?, updated = ?, deleted = ?, skipped_records = ?, "
        "duplicates = ?, requests_sent = ?, error = ?, error_kind = ? WHERE id = ?",
        (
            report.finished_at,
            report.status,
            report.pages_read,
            report.total_data,
            report.records,
            report.inserted,
            report.updated,
            report.deleted,
            report.skipped_records,
            report.duplicates,
            report.requests_sent,
            report.error,
            report.error_kind,
            run_id,
        ),
    )


def build_adapter(settings: Settings) -> OpenDataJabarAdapter:
    """Satu adaptor untuk proses sync_worker, dengan jeda MCP_SYNC_REQUEST_DELAY_S (KB-26)."""
    return OpenDataJabarAdapter(
        settings.model_copy(update={"request_delay_s": settings.sync_request_delay_s})
    )


async def sync_once(
    settings: Settings,
    *,
    adapter: SourceAdapter | None = None,
    now: Callable[[], datetime] = _now,
    monotonic: Callable[[], float] = time.monotonic,
) -> SyncReport:
    started_clock = monotonic()
    started = now()
    report = SyncReport(status="failed", started_at=started.isoformat(timespec="seconds"))

    def finish(status: str, error: str | None = None, kind: str | None = None) -> SyncReport:
        report.status = status
        report.error = error
        report.error_kind = kind
        report.finished_at = now().isoformat(timespec="seconds")
        report.duration_s = round(monotonic() - started_clock, 3)
        log_event(LOGGER, "catalog_sync", **report.to_dict())
        return report

    conn = db.connect_write(settings.db_path, stem_text=stem_text)
    try:
        reason = blocking_reason(last_request_run(conn), started, settings)
        if reason is not None:
            finish("skipped", reason)
            _insert_run(conn, report)
            return report

        own_adapter = adapter is None
        source: Any = adapter if adapter is not None else build_adapter(settings)
        run_id = _insert_run(
            conn,
            SyncReport(
                status="failed",
                started_at=report.started_at,
                error=INTERRUPTED,
                error_kind="interrupted",
            ),
        )
        organisasi = topik = None
        refs_error: str | None = None
        try:
            try:
                snapshot = await source.list_datasets()
            except SourceError as exc:  # adaptor asli tidak melempar; jaga-jaga untuk lainnya
                snapshot = CatalogSnapshot(
                    records=[],
                    total_data=None,
                    pages_read=0,
                    total_page=None,
                    complete=False,
                    error=f"{type(exc).__name__}: {exc}",
                )
            try:
                organisasi = await source.list_organisasi()
                topik = await source.list_topik()
            except SourceError as exc:
                organisasi = topik = None
                refs_error = f"referensi: {type(exc).__name__}: {exc}"
        finally:
            report.requests_sent = int(getattr(source, "requests_sent", 0))
            if own_adapter:
                await source.aclose()

        report.pages_read = snapshot.pages_read
        report.total_page = snapshot.total_page
        report.total_data = snapshot.total_data
        report.records = len(snapshot.records)
        report.skipped_records = snapshot.skipped
        report.duplicates = snapshot.duplicates
        if not snapshot.records:
            finish(
                "failed",
                snapshot.error or "katalog kosong",
                error_kind_of(snapshot.error, refs_error) or "empty_catalog",
            )
            with db.transaction(conn):
                _update_run(conn, run_id, report)
            return report

        status = "complete" if snapshot.complete and refs_error is None else "incomplete"
        error = "; ".join(e for e in (snapshot.error, refs_error) if e) or None
        synced_at = now()
        rows = [build_dataset_row(dataset, synced_at) for dataset in snapshot.records]

        def record_run(write_conn: sqlite3.Connection, counts: WriteCounts) -> None:
            report.inserted, report.updated, report.deleted = (
                counts.inserted,
                counts.updated,
                counts.deleted,
            )
            finish(status, error, error_kind_of(snapshot.error, refs_error))
            _update_run(write_conn, run_id, report)

        try:
            write_catalog(
                conn,
                rows,
                delete_missing=snapshot.complete,
                organisasi=organisasi,
                topik=topik,
                before_commit=record_run,
            )
        except Exception as exc:  # rollback sudah terjadi; catat kegagalan secara terpisah
            report.inserted = report.updated = report.deleted = 0
            finish(
                "failed",
                f"penulisan katalog gagal: {type(exc).__name__}: {exc}",
                "write_failed",
            )
            with db.transaction(conn):
                _update_run(conn, run_id, report)
        return report
    finally:
        conn.close()


@dataclass(frozen=True)
class ReindexReport:
    records: int
    stemmer: str
    duration_s: float


def reindex(
    settings: Settings, *, monotonic: Callable[[], float] = time.monotonic
) -> ReindexReport:
    """Bangun ulang kedua FTS dari tabel datasets lokal, tanpa portal dan tanpa sync_runs."""
    if not settings.db_path.exists():
        raise db.CatalogNotReady(f"katalog {settings.db_path} belum ada; jalankan --once dulu")
    started = monotonic()
    conn = db.connect_write(settings.db_path, stem_text=stem_text)
    try:
        with db.transaction(conn):
            records = db.rebuild_fts(conn)
    finally:
        conn.close()
    report = ReindexReport(records, STEMMER_NAME, round(monotonic() - started, 3))
    log_event(LOGGER, "catalog_reindex", **asdict(report))
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sinkron katalog Satu Data Kota Bogor")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="jalankan satu kali lalu keluar")
    mode.add_argument("--reindex", action="store_true", help="bangun ulang FTS lokal tanpa portal")
    args = parser.parse_args(argv)
    if not (args.once or args.reindex):
        print("Mode terjadwal belum ada (Langkah 9); jalankan dengan --once atau --reindex.")
        return 2
    settings = Settings()
    logging.basicConfig(level=settings.log_level, format="%(message)s")
    try:
        if args.reindex:
            result = reindex(settings)
            print(
                f"Reindex katalog: {result.records} record, stemmer {result.stemmer}, "
                f"{result.duration_s:.1f} dtk"
            )
            return 0
        report = asyncio.run(sync_once(settings))
    except (ConfigError, db.CatalogNotReady) as exc:
        print(f"DITOLAK: {exc}")
        return 2
    print(report.summary())
    return 1 if report.status == "failed" else 0


if __name__ == "__main__":
    sys.exit(main())
