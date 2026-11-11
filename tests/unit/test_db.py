"""storage/db.py: WAL, migrasi user_version, koneksi baca mode=ro (ADR-010), transaksi, FTS."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from mcp_bogor.storage import db

PRIVATE_WORDS = ("username", "cuid", "muid", "email", "notelp", "alamat", "kontak")


def test_sqlite_supports_trigram() -> None:
    assert db.sqlite_supports_trigram(), sqlite3.sqlite_version


def test_connect_write_creates_wal_and_schema(tmp_path: Path) -> None:
    path = tmp_path / "sub dir" / "katalog.db"  # spasi di path (Windows) tetap aman
    conn = db.connect_write(path)
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert int(conn.execute("PRAGMA busy_timeout").fetchone()[0]) == db.BUSY_TIMEOUT_MS
    assert db.schema_version(conn) == db.SCHEMA_VERSION
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {
        "datasets",
        "organisasi",
        "topik",
        "sync_runs",
        "datasets_fts",
        "datasets_trigram",
    } <= tables
    conn.close()
    again = db.connect_write(path)  # migrasi idempoten
    assert db.schema_version(again) == db.SCHEMA_VERSION
    again.close()


def test_schema_has_no_private_columns(tmp_path: Path) -> None:
    conn = db.connect_write(tmp_path / "k.db")
    for table in ("datasets", "organisasi", "topik", "sync_runs"):
        columns = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
        assert not [c for c in columns if any(w in c for w in PRIVATE_WORDS)], table
    assert [r[1] for r in conn.execute("PRAGMA table_info(organisasi)")] == ["id", "name", "slug"]
    assert list(db.DATASET_COLUMNS) == [r[1] for r in conn.execute("PRAGMA table_info(datasets)")]
    conn.close()


def test_read_connection_is_read_only(tmp_path: Path) -> None:
    path = tmp_path / "k.db"
    db.connect_write(path).close()
    reader = db.connect_read(path)
    assert reader.execute("SELECT count(*) FROM datasets").fetchone()[0] == 0
    for statement in (
        "INSERT INTO organisasi (id, name) VALUES (1, 'x')",
        "UPDATE datasets SET name = 'x'",
        "DELETE FROM sync_runs",
        "CREATE TABLE curang (x)",
        "PRAGMA user_version = 99",
    ):
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            reader.execute(statement)
    reader.close()


def test_read_connection_requires_existing_catalog(tmp_path: Path) -> None:
    with pytest.raises(db.CatalogNotReady, match="belum ada"):
        db.connect_read(tmp_path / "tidak-ada.db")
    assert not (tmp_path / "tidak-ada.db").exists()


def test_read_connection_rejects_other_schema_version(tmp_path: Path) -> None:
    path = tmp_path / "k.db"
    conn = db.connect_write(path)
    conn.execute("PRAGMA user_version = 7")
    conn.close()
    with pytest.raises(db.CatalogNotReady, match="versi skema"):
        db.connect_read(path)


def test_reader_sees_old_data_until_commit(tmp_path: Path) -> None:
    path = tmp_path / "k.db"
    writer = db.connect_write(path)
    with db.transaction(writer):
        writer.execute("INSERT INTO organisasi VALUES (1, 'Lama', 'lama')")
    reader = db.connect_read(path)
    writer.execute("BEGIN IMMEDIATE")
    writer.execute("UPDATE organisasi SET name = 'Baru'")
    assert reader.execute("SELECT name FROM organisasi").fetchone()[0] == "Lama"
    writer.execute("COMMIT")
    assert reader.execute("SELECT name FROM organisasi").fetchone()[0] == "Baru"
    reader.close()
    writer.close()


def test_transaction_rolls_back_on_error(tmp_path: Path) -> None:
    conn = db.connect_write(tmp_path / "k.db")
    with pytest.raises(RuntimeError), db.transaction(conn):
        conn.execute("INSERT INTO organisasi VALUES (1, 'X', 'x')")
        raise RuntimeError("gagal di tengah")
    assert conn.execute("SELECT count(*) FROM organisasi").fetchone()[0] == 0
    assert not conn.in_transaction
    conn.close()


def test_migrate_refuses_without_trigram(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(db, "sqlite_supports_trigram", lambda: False)
    with pytest.raises(db.CatalogNotReady, match="trigram"):
        db.connect_write(tmp_path / "k.db")


def test_split_sql_keeps_statements_whole() -> None:
    statements = db._split_sql("CREATE TABLE a (x);\n\nCREATE TABLE b (\n  y\n);\n")
    assert statements == ["CREATE TABLE a (x);", "CREATE TABLE b (\n  y\n);"]


def test_rebuild_fts_mirrors_datasets(tmp_path: Path) -> None:
    conn = db.connect_write(tmp_path / "k.db")
    columns = ", ".join(db.DATASET_COLUMNS)
    values = {c: None for c in db.DATASET_COLUMNS}
    values.update(
        slug="a",
        name="Tingkat Kemiskinan",
        deskripsi_text="persentase penduduk",
        official_status="resmi",
        metadata_changed_after_approve=0,
        synced_at="2026-11-10T08:00:00+07:00",
    )
    with db.transaction(conn):
        conn.execute(
            f"INSERT INTO datasets ({columns}) VALUES ({', '.join('?' for _ in values)})",
            [values[c] for c in db.DATASET_COLUMNS],
        )
        db.rebuild_fts(conn)
        db.rebuild_fts(conn)  # idempoten: tidak menggandakan
    for table, query in (("datasets_fts", '"kemiskinan"'), ("datasets_trigram", '"miskin"')):
        rows = conn.execute(f"SELECT slug FROM {table} WHERE {table} MATCH ?", [query]).fetchall()
        assert [r[0] for r in rows] == ["a"], table
    conn.close()


def test_migration_v1_to_latest_reindexes_existing_catalog(tmp_path: Path) -> None:
    """Katalog v1 berisi data → v3: error_kind ditambah, FTS kata punya name_stem terisi."""
    path = tmp_path / "lama.db"
    old = sqlite3.connect(path, isolation_level=None)
    for statement in db._split_sql(db.MIGRATIONS[1]):
        old.execute(statement)
    old.execute("PRAGMA user_version = 1")
    old.execute(
        "INSERT INTO datasets (slug, name, deskripsi_text, official_status, synced_at) "
        "VALUES ('a', 'Tingkat Kemiskinan', 'penduduk miskin', 'resmi', 'x')"
    )
    old.close()
    conn = db.connect_write(path, stem_text=lambda text: (text or "").upper())
    assert db.schema_version(conn) == 3
    assert "error_kind" in [r[1] for r in conn.execute("PRAGMA table_info(sync_runs)")]
    columns = [r[1] for r in conn.execute("PRAGMA table_info(datasets_fts)")]
    assert columns == ["slug", "name", "deskripsi", "name_stem"]  # tanpa deskripsi_stem
    row = conn.execute("SELECT name_stem, deskripsi FROM datasets_fts").fetchone()
    assert tuple(row) == ("TINGKAT KEMISKINAN", "penduduk miskin")
    assert conn.execute("SELECT count(*) FROM datasets_trigram").fetchone()[0] == 1
    conn.close()
    reader = db.connect_read(path)
    assert db.schema_version(reader) == db.SCHEMA_VERSION == 3
    reader.close()


def test_rebuild_fts_uses_injected_stemmer(tmp_path: Path) -> None:
    conn = db.connect_write(tmp_path / "k.db", stem_text=lambda text: "STEM")
    conn.execute(
        "INSERT INTO datasets (slug, name, official_status, synced_at) "
        "VALUES ('a', 'Nama', 'resmi', 'x')"
    )
    with db.transaction(conn):
        assert db.rebuild_fts(conn) == 1
    row = conn.execute("SELECT name, name_stem, deskripsi FROM datasets_fts")
    assert tuple(row.fetchone()) == ("Nama", "STEM", "")
    conn.close()
