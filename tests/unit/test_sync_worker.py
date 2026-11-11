"""sync_worker --once (Langkah 5): sinkron penuh, dataset hilang, atomik, AK-06, privasi.

Tanpa jaringan: FakeSourceAdapter dari fixture rekaman dan catalog_slim.json.
"""

from __future__ import annotations

import asyncio
import dataclasses
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from mcp_bogor import sync_worker
from mcp_bogor.adapters.base import CatalogSnapshot, SourceRateLimited, SourceUnavailable
from mcp_bogor.adapters.sanitize import EMAIL_RE, PHONE_RE
from mcp_bogor.config import WIB
from mcp_bogor.services.catalog import catalog_synced_at
from mcp_bogor.storage import db
from tests.fakes import FakeSourceAdapter, make_settings, recorded_portal

T0 = datetime(2026, 11, 10, 2, 0, tzinfo=WIB)
KEMISKINAN = "tingkat-kemiskinan-di-kota-bogor-2"
KM07 = "persentase-kepala-keluarga-miskin-di-kota-bogor"
KM12 = "persentase-penanganan-pengaduan-masyarakat-ke-inspektorat-daerah-kota-bogor"


def _clock(start: datetime) -> Any:
    """Jam dinding palsu yang maju 1 detik per panggilan."""
    state = {"now": start}

    def now() -> datetime:
        state["now"] += timedelta(seconds=1)
        return state["now"]

    return now


def _sync(settings: Any, adapter: Any, at: datetime) -> sync_worker.SyncReport:
    return asyncio.run(sync_worker.sync_once(settings, adapter=adapter, now=_clock(at)))


def _small_fake(*slugs: str) -> FakeSourceAdapter:
    portal = recorded_portal()
    records = [r for r in portal.slim_records if r["slug"] in slugs]
    return FakeSourceAdapter(dataclasses.replace(portal, slim_records=records))


def _slugs(conn: sqlite3.Connection, table: str = "datasets") -> set[str]:
    return {row[0] for row in conn.execute(f"SELECT slug FROM {table}")}


def _runs(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM sync_runs ORDER BY id").fetchall()


# ---------------------------------------------------------------- sinkron penuh


def test_full_sync(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    fake = FakeSourceAdapter()
    report = _sync(settings, fake, T0)
    assert report.status == "complete" and report.error is None
    assert (report.pages_read, report.total_data, report.records) == (8, 751, 751)
    assert (report.inserted, report.updated, report.deleted, report.skipped_records) == (
        751,
        0,
        0,
        0,
    )
    assert report.requests_sent == 3 == fake.requests_sent
    conn = db.connect_read(settings.db_path)
    assert conn.execute("SELECT count(*) FROM datasets").fetchone()[0] == 751
    assert conn.execute("SELECT count(*) FROM organisasi").fetchone()[0] == 39
    assert conn.execute("SELECT count(*) FROM topik").fetchone()[0] == 45
    assert len(_slugs(conn, "datasets_fts")) == len(_slugs(conn, "datasets_trigram")) == 751
    run = _runs(conn)[-1]
    assert run["status"] == "complete" and run["records"] == 751 and run["requests_sent"] == 3
    assert catalog_synced_at(conn) == datetime.fromisoformat(run["finished_at"])
    conn.close()


def test_rows_use_step2_services(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    _sync(settings, FakeSourceAdapter(), T0)
    conn = db.connect_read(settings.db_path)
    row = conn.execute("SELECT * FROM datasets WHERE slug = ?", [KEMISKINAN]).fetchone()
    assert (row["tahun_awal"], row["tahun_akhir"]) == (2025, 2025)
    assert row["satuan"] == "Persen" and row["frekuensi"]
    assert row["data_updated_at"] == "2026-08-31T16:03:02+07:00"  # approve, +07:07 → WIB
    assert row["freshness_source"] == "approve"
    assert (row["official_status"], row["official_basis"]) == (
        "resmi",
        "validate=approve; klasifikasi=public",
    )
    assert row["organisasi_name"] == "Dinas Sosial" and row["topik_name"]
    assert "<" not in row["deskripsi_text"] and "<p>" in row["deskripsi_html"]
    assert row["deskripsi_text"].count("\n- ") >= 3  # butir kamus kolom untuk Langkah 6
    assert row["schema_name"] and row["table_name"]
    # NB-06: metadata "None" (teks) menjadi null
    nulls = conn.execute("SELECT count(*) FROM datasets WHERE cakupan = 'None'").fetchone()[0]
    assert nulls == 0
    conn.close()


# ---------------------------------------------------------------- dataset hilang (TODO Langkah 5)


def test_complete_snapshot_deletes_missing(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    assert _sync(settings, _small_fake(KEMISKINAN, KM07, KM12), T0).inserted == 3
    second = _small_fake(KEMISKINAN, KM07, KM12)
    second.remove(KM12)
    report = _sync(settings, second, T0 + timedelta(hours=25))
    assert report.status == "complete"
    assert (report.inserted, report.updated, report.deleted) == (0, 2, 1)
    conn = db.connect_read(settings.db_path)
    for table in ("datasets", "datasets_fts", "datasets_trigram"):
        assert _slugs(conn, table) == {KEMISKINAN, KM07}, table
    conn.close()


def test_incomplete_snapshot_deletes_nothing_but_updates(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    _sync(settings, _small_fake(KEMISKINAN, KM07, KM12), T0)
    second = _small_fake(KEMISKINAN, KM07, KM12)
    second.remove(KM12)
    second.snapshot_complete = False
    later = T0 + timedelta(hours=25)
    report = _sync(settings, second, later)
    assert report.status == "incomplete" and report.deleted == 0 and report.updated == 2
    assert report.error and "simulasi" in report.error
    conn = db.connect_read(settings.db_path)
    assert _slugs(conn) == {KEMISKINAN, KM07, KM12}
    synced = dict(conn.execute("SELECT slug, synced_at FROM datasets").fetchall())
    assert synced[KEMISKINAN] > later.isoformat() > synced[KM12]  # yang terbaca diperbarui
    assert _runs(conn)[-1]["status"] == "incomplete"
    assert catalog_synced_at(conn) < later  # waktu sinkron complete terakhir tetap yang lama
    conn.close()


# ---------------------------------------------------------------- atomik


def test_write_failure_rolls_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = make_settings(tmp_path)
    _sync(settings, _small_fake(KEMISKINAN, KM07, KM12), T0)
    reader = db.connect_read(settings.db_path)
    before = reader.execute("SELECT * FROM datasets ORDER BY slug").fetchall()

    def broken(conn: sqlite3.Connection) -> None:
        raise sqlite3.OperationalError("disk penuh (simulasi)")

    monkeypatch.setattr(db, "rebuild_fts", broken)
    second = _small_fake(KEMISKINAN, KM07, KM12)
    second.remove(KM12)
    report = _sync(settings, second, T0 + timedelta(hours=25))
    assert report.status == "failed" and "disk penuh" in (report.error or "")
    assert (report.inserted, report.updated, report.deleted) == (0, 0, 0)
    after = reader.execute("SELECT * FROM datasets ORDER BY slug").fetchall()
    assert [tuple(r) for r in after] == [tuple(r) for r in before]
    assert _slugs(reader, "datasets_fts") == {KEMISKINAN, KM07, KM12}
    runs = _runs(reader)
    assert [r["status"] for r in runs] == ["complete", "failed"]
    assert runs[-1]["requests_sent"] == 3  # tetap tercatat untuk AK-06
    reader.close()


# ---------------------------------------------------------------- AK-06


def test_second_sync_within_interval_is_skipped(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    _sync(settings, _small_fake(KEMISKINAN), T0)
    fake = _small_fake(KEMISKINAN)
    report = _sync(settings, fake, T0 + timedelta(hours=23))
    assert report.status == "skipped" and "AK-06" in (report.error or "")
    assert fake.calls == [] and report.requests_sent == 0
    third = _small_fake(KEMISKINAN)
    assert _sync(settings, third, T0 + timedelta(hours=24, minutes=1)).status == "complete"
    assert third.requests_sent == 3
    conn = db.connect_read(settings.db_path)
    assert [r["status"] for r in _runs(conn)] == ["complete", "skipped", "complete"]
    conn.close()


def test_failed_run_also_counts_for_interval(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)

    class EmptyPortal(FakeSourceAdapter):
        async def list_datasets(self) -> CatalogSnapshot:
            self.calls.append("list_datasets")
            return CatalogSnapshot([], None, 0, None, False, error="SourceUnavailable: mati")

    failed = _sync(settings, EmptyPortal(), T0)
    assert failed.status == "failed" and failed.error == "SourceUnavailable: mati"
    fake = FakeSourceAdapter()
    assert _sync(settings, fake, T0 + timedelta(hours=1)).status == "skipped"
    assert fake.calls == []


def test_interrupted_run_counts_for_interval(tmp_path: Path) -> None:
    """Proses mati setelah run dicatat: baris 'failed/terhenti' tetap memblokir 24 jam."""
    settings = make_settings(tmp_path)

    class Crash(FakeSourceAdapter):
        async def list_datasets(self) -> CatalogSnapshot:
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        _sync(settings, Crash(), T0)
    conn = db.connect_read(settings.db_path)
    run = _runs(conn)[-1]
    assert run["status"] == "failed" and run["error"] == sync_worker.INTERRUPTED
    conn.close()
    assert _sync(settings, FakeSourceAdapter(), T0 + timedelta(hours=2)).status == "skipped"


# ---------------------------------------------------------------- galat sumber


def test_list_datasets_raising_is_failed(tmp_path: Path) -> None:
    fake = FakeSourceAdapter()
    fake.fail_with = SourceUnavailable("timeout pada /datasets")
    report = _sync(make_settings(tmp_path), fake, T0)
    assert report.status == "failed" and "SourceUnavailable" in (report.error or "")


def test_reference_failure_keeps_old_references(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    _sync(settings, _small_fake(KEMISKINAN, KM07), T0)
    fake = _small_fake(KEMISKINAN)

    async def broken() -> list[dict[str, Any]]:
        raise SourceUnavailable("503 pada /organisasi")

    fake.list_organisasi = broken  # type: ignore[method-assign]
    report = _sync(settings, fake, T0 + timedelta(hours=25))
    assert report.status == "incomplete" and "referensi" in (report.error or "")
    assert report.deleted == 1  # snapshot katalog tetap lengkap → penghapusan boleh
    conn = db.connect_read(settings.db_path)
    assert conn.execute("SELECT count(*) FROM organisasi").fetchone()[0] == 39
    assert conn.execute("SELECT count(*) FROM topik").fetchone()[0] == 45
    conn.close()


# ---------------------------------------------------------------- privasi (UT-12)


def test_db_dump_has_no_private_data(tmp_path: Path) -> None:
    """UT-12: skema dan seluruh nilai teks tabel data bebas akun, kontak, dan alamat OPD.

    Nilai diperiksa per tabel data asli; tabel bayangan FTS5 berisi blob biner turunan
    name/deskripsi_text (sudah ikut diperiksa) sehingga tidak dipindai dengan regex.
    """
    settings = make_settings(tmp_path)
    _sync(settings, FakeSourceAdapter(), T0)
    conn = db.connect_read(settings.db_path)
    schema = " ".join(
        r[0] for r in conn.execute("SELECT sql FROM sqlite_master WHERE sql IS NOT NULL")
    )
    for word in ("username", "user_name", "cuid", "muid", "notelp", "email", "alamat", "kontak"):
        assert word not in schema.lower(), word
    findings = []
    for table in ("datasets", "organisasi", "topik", "sync_runs"):
        for row in conn.execute(f"SELECT * FROM {table}"):
            for column, value in zip(row.keys(), tuple(row), strict=True):
                if not isinstance(value, str):
                    continue
                for word in ("username", "Kontak Produsen", "<redacted>"):
                    if word in value:
                        findings.append((table, column, word))
                if EMAIL_RE.search(value):
                    findings.append((table, column, "email"))
                if PHONE_RE.search(value):
                    findings.append((table, column, "telepon"))
    conn.close()
    assert findings == [], findings[:10]


# ---------------------------------------------------------------- CLI


def test_main_requires_once(capsys: pytest.CaptureFixture[str]) -> None:
    assert sync_worker.main([]) == 2
    assert "--once" in capsys.readouterr().out


def test_main_refuses_placeholder_user_agent(
    settings_env: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert sync_worker.main(["--once"]) == 2
    assert "MCP_USER_AGENT" in capsys.readouterr().out
    conn = db.connect_read(settings_env / "var" / "test.db")
    assert _runs(conn) == []  # galat konfigurasi tidak memblokir sinkron 24 jam
    conn.close()


@pytest.mark.parametrize(("fake_status", "code"), [("ok", 0), ("empty", 1)])
def test_main_reports_and_exit_code(
    settings_env: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    fake_status: str,
    code: int,
) -> None:
    fake = FakeSourceAdapter() if fake_status == "ok" else _small_fake()
    created: list[Any] = []

    def factory(settings: Any) -> FakeSourceAdapter:
        created.append(settings)

        async def aclose() -> None:
            fake.calls.append("aclose")

        fake.aclose = aclose  # type: ignore[attr-defined]
        return fake

    monkeypatch.setenv("MCP_USER_AGENT", "mcp-bogor-test/0.1 (+tim@contoh.test)")
    monkeypatch.setattr(sync_worker, "build_adapter", factory)
    assert sync_worker.main(["--once"]) == code
    out = capsys.readouterr().out
    assert out.startswith("Sinkron katalog: " + ("complete" if code == 0 else "failed"))
    assert fake.calls[-1] == "aclose"  # adaptor milik worker ditutup


def test_build_adapter_uses_sync_delay(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, sync_request_delay_s=0.7, request_delay_s=0.2)
    adapter = sync_worker.build_adapter(settings)
    assert adapter._delay_s == 0.7
    asyncio.run(adapter.aclose())


def test_sync_delay_setting_bounds(tmp_path: Path) -> None:
    assert make_settings(tmp_path).sync_request_delay_s == 0.5
    with pytest.raises(ValueError):
        make_settings(tmp_path, sync_request_delay_s=0.1)


def test_report_summary_text() -> None:
    report = sync_worker.SyncReport(status="incomplete", started_at="x", error="referensi: x")
    assert report.summary().startswith("Sinkron katalog: incomplete")
    assert "referensi: x" in report.summary()


# ---------------------------------------------------------------- KB-28: ulang setelah gagal


class _FailingCatalog(FakeSourceAdapter):
    def __init__(self, error: str) -> None:
        super().__init__()
        self.error = error

    async def list_datasets(self) -> CatalogSnapshot:
        self.calls.append("list_datasets")
        return CatalogSnapshot([], None, 0, None, False, error=self.error)


def test_failed_run_retried_after_retry_interval(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)  # retry 6 jam, interval penuh 24 jam
    failed = _sync(settings, _FailingCatalog("SourceUnavailable: timeout pada /datasets"), T0)
    assert (failed.status, failed.error_kind) == ("failed", "source_unavailable")
    early = _sync(settings, FakeSourceAdapter(), T0 + timedelta(hours=5, minutes=59))
    assert early.status == "skipped" and "KB-28" in (early.error or "")
    retry = FakeSourceAdapter()
    assert _sync(settings, retry, T0 + timedelta(hours=6, minutes=1)).status == "complete"
    assert retry.requests_sent == 3


def test_rate_limited_run_waits_full_interval(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    failed = _sync(settings, _FailingCatalog("SourceRateLimited: 429 pada /datasets"), T0)
    assert (failed.status, failed.error_kind) == ("failed", "rate_limited")
    for hours in (6.5, 23.9):
        fake = FakeSourceAdapter()
        report = _sync(settings, fake, T0 + timedelta(hours=hours))
        assert report.status == "skipped" and "429" in (report.error or "")
        assert fake.calls == []
    assert _sync(settings, FakeSourceAdapter(), T0 + timedelta(hours=24, minutes=1)).status == (
        "complete"
    )


def test_incomplete_run_retry_depends_on_cause(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    fake = _small_fake(KEMISKINAN)
    fake.snapshot_complete = False  # error "SourceUnavailable: simulasi ..."
    report = _sync(settings, fake, T0)
    assert (report.status, report.error_kind) == ("incomplete", "source_unavailable")
    assert _sync(settings, _small_fake(KEMISKINAN), T0 + timedelta(hours=7)).status == "complete"

    other = make_settings(tmp_path / "lain")
    limited = _small_fake(KEMISKINAN)

    async def rate_limited() -> list[dict[str, Any]]:
        raise SourceRateLimited("429 pada /organisasi")

    limited.list_organisasi = rate_limited  # type: ignore[method-assign]
    report = _sync(other, limited, T0)
    assert (report.status, report.error_kind) == ("incomplete", "rate_limited")
    assert _sync(other, _small_fake(KEMISKINAN), T0 + timedelta(hours=7)).status == "skipped"


def test_complete_run_still_blocks_24h(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    assert _sync(settings, _small_fake(KEMISKINAN), T0).error_kind is None
    report = _sync(settings, _small_fake(KEMISKINAN), T0 + timedelta(hours=7))
    assert report.status == "skipped" and "AK-06" in (report.error or "")


def test_error_kind_is_stored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = make_settings(tmp_path)
    _sync(settings, _FailingCatalog("SourceProtocolError: bentuk aneh"), T0)

    def broken(conn: sqlite3.Connection) -> int:
        raise sqlite3.OperationalError("disk penuh")

    monkeypatch.setattr(db, "rebuild_fts", broken)
    _sync(settings, _small_fake(KEMISKINAN), T0 + timedelta(hours=7))
    conn = db.connect_read(settings.db_path)
    kinds = [(r["status"], r["error_kind"]) for r in _runs(conn)]
    conn.close()
    assert kinds == [("failed", "protocol_error"), ("failed", "write_failed")]


@pytest.mark.parametrize(
    ("errors", "kind"),
    [
        ((None, None), None),
        (("SourceUnavailable: x", "referensi: SourceRateLimited: y"), "rate_limited"),
        (("SourceNotFound: x", None), "not_found"),
        (("galat lain", None), "other"),
    ],
)
def test_error_kind_of(errors: tuple[str | None, str | None], kind: str | None) -> None:
    assert sync_worker.error_kind_of(*errors) == kind


def test_retry_setting_cannot_exceed_interval(tmp_path: Path) -> None:
    assert make_settings(tmp_path).catalog_sync_retry_after_h == 6
    with pytest.raises(ValueError, match="RETRY_AFTER"):
        make_settings(tmp_path, catalog_sync_retry_after_h=25)


# ---------------------------------------------------------------- --reindex


def test_reindex_rebuilds_without_portal_or_sync_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = make_settings(tmp_path)
    _sync(settings, _small_fake(KEMISKINAN, KM07), T0)
    writer = db.connect_write(settings.db_path)
    writer.execute("DELETE FROM datasets_fts")
    writer.close()
    monkeypatch.setattr(sync_worker, "stem_text", lambda text: (text or "").upper())
    report = sync_worker.reindex(settings)
    assert report.records == 2 and report.stemmer == "PySastrawi 1.2.1"
    conn = db.connect_read(settings.db_path)
    assert _slugs(conn, "datasets_fts") == {KEMISKINAN, KM07}
    stem = conn.execute("SELECT name_stem FROM datasets_fts WHERE slug = ?", [KEMISKINAN])
    assert stem.fetchone()[0] == "TINGKAT KEMISKINAN DI KOTA BOGOR"
    assert [r["status"] for r in _runs(conn)] == ["complete"]  # tidak dihitung AK-06
    conn.close()


def test_reindex_cli(
    settings_env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert sync_worker.main(["--reindex"]) == 2  # katalog belum ada
    assert "belum ada" in capsys.readouterr().out
    _sync(make_settings(settings_env), _small_fake(KEMISKINAN), T0)
    built: list[object] = []
    monkeypatch.setattr(sync_worker, "build_adapter", lambda s: built.append(s))
    assert sync_worker.main(["--reindex"]) == 0
    assert capsys.readouterr().out.startswith("Reindex katalog: 1 record")
    assert built == []  # adaptor tidak pernah dibuat
