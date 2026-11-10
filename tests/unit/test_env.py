"""Prasyarat lingkungan: SQLite punya FTS5 dan mode WAL (ADR-003, ADR-010)."""

import sqlite3
from pathlib import Path


def test_sqlite_has_fts5() -> None:
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE VIRTUAL TABLE t USING fts5(judul)")
        conn.execute("INSERT INTO t VALUES ('Realisasi Pendapatan Asli Daerah')")
        rows = conn.execute("SELECT judul FROM t WHERE t MATCH 'pendapatan'").fetchall()
    finally:
        conn.close()
    assert rows == [("Realisasi Pendapatan Asli Daerah",)]


def test_sqlite_wal_mode(tmp_path: Path) -> None:
    conn = sqlite3.connect(tmp_path / "wal.db")
    try:
        mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
    finally:
        conn.close()
    assert mode.lower() == "wal"
