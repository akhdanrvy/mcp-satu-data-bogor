"""SQLite (WAL) + FTS5 untuk katalog lokal (ADR-003, ADR-010).

- Koneksi tulis (`connect_write`) hanya untuk sync_worker: membuat berkas, mode WAL,
  menjalankan migrasi. Koneksi baca (`connect_read`) dibuka dengan URI `mode=ro`, sehingga
  server tool secara fisik tidak bisa menulis (ADR-010 dijaga oleh SQLite).
- Versi skema di `PRAGMA user_version`; migrasi berurutan di MIGRATIONS.
- Tanpa kolom kontak, alamat, atau akun apa pun (KK-J, Dok 1 privasi).
- Dua indeks FTS5: kata (unicode61, remove_diacritics; kolom asli + name_stem, KB-29) dan
  trigram (cadangan terakhir). Keduanya dibangun ulang di transaksi sinkron atau lewat
  `sync_worker --reindex`. Fungsi stem diinjeksi ke koneksi tulis sebagai fungsi SQL
  `mcp_stem`, sehingga storage tidak bergantung pada services.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = 3
BUSY_TIMEOUT_MS = 5000
MIN_SQLITE_FOR_TRIGRAM = (3, 34, 0)

DATASET_COLUMNS = (
    "slug",
    "dataset_id",
    "name",
    "organisasi_id",
    "organisasi_name",
    "organisasi_slug",
    "topik_id",
    "topik_name",
    "topik_slug",
    "kategori",
    "klasifikasi",
    "validate",
    "priority_level",
    "data_score_status",
    "kode_wilayah",
    "schema_name",
    "table_name",
    "satuan",
    "frekuensi",
    "tahun_awal",
    "tahun_akhir",
    "tingkat_penyajian",
    "cakupan",
    "deskripsi_html",
    "deskripsi_text",
    "data_updated_at",
    "freshness_source",
    "metadata_changed_after_approve",
    "change_notes",
    "official_status",
    "official_basis",
    "portal_mdate",
    "synced_at",
)

SYNC_STATUSES = ("complete", "incomplete", "failed", "skipped")

_SCHEMA_V1 = f"""
CREATE TABLE datasets (
    slug TEXT PRIMARY KEY,
    dataset_id INTEGER,
    name TEXT NOT NULL,
    organisasi_id INTEGER,
    organisasi_name TEXT,
    organisasi_slug TEXT,
    topik_id INTEGER,
    topik_name TEXT,
    topik_slug TEXT,
    kategori TEXT,
    klasifikasi TEXT,
    validate TEXT,
    priority_level TEXT,
    data_score_status TEXT,
    kode_wilayah TEXT,
    schema_name TEXT,
    table_name TEXT,
    satuan TEXT,
    frekuensi TEXT,
    tahun_awal INTEGER,
    tahun_akhir INTEGER,
    tingkat_penyajian TEXT,
    cakupan TEXT,
    deskripsi_html TEXT,
    deskripsi_text TEXT,
    data_updated_at TEXT,
    freshness_source TEXT CHECK (freshness_source IN ('approve', 'mdate')),
    metadata_changed_after_approve INTEGER NOT NULL DEFAULT 0,
    change_notes TEXT,
    official_status TEXT NOT NULL CHECK (official_status IN ('resmi', 'draf')),
    official_basis TEXT,
    portal_mdate TEXT,
    synced_at TEXT NOT NULL
);
CREATE INDEX datasets_organisasi ON datasets (organisasi_slug);
CREATE INDEX datasets_topik ON datasets (topik_name);

CREATE TABLE organisasi (id INTEGER PRIMARY KEY, name TEXT NOT NULL, slug TEXT);
CREATE TABLE topik (id INTEGER PRIMARY KEY, name TEXT NOT NULL, slug TEXT);

CREATE TABLE sync_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL CHECK (status IN {SYNC_STATUSES!r}),
    pages_read INTEGER NOT NULL DEFAULT 0,
    total_data INTEGER,
    records INTEGER NOT NULL DEFAULT 0,
    inserted INTEGER NOT NULL DEFAULT 0,
    updated INTEGER NOT NULL DEFAULT 0,
    deleted INTEGER NOT NULL DEFAULT 0,
    skipped_records INTEGER NOT NULL DEFAULT 0,
    duplicates INTEGER NOT NULL DEFAULT 0,
    requests_sent INTEGER NOT NULL DEFAULT 0,
    error TEXT
);

CREATE VIRTUAL TABLE datasets_fts USING fts5(
    slug UNINDEXED, name, deskripsi, tokenize = 'unicode61 remove_diacritics 2'
);
CREATE VIRTUAL TABLE datasets_trigram USING fts5(
    slug UNINDEXED, name, deskripsi, tokenize = 'trigram'
);
"""

# v2 (KB-28, KB-29): penyebab galat sinkron; FTS kata dengan kolom stem. Isi FTS baru
# dibangun ulang oleh connect_write (jalur yang sama dengan sync_worker --reindex).
_SCHEMA_V2 = """
ALTER TABLE sync_runs ADD COLUMN error_kind TEXT;
DROP TABLE datasets_fts;
CREATE VIRTUAL TABLE datasets_fts USING fts5(
    slug UNINDEXED, name, deskripsi, name_stem, deskripsi_stem,
    tokenize = 'unicode61 remove_diacritics 2'
);
"""

# v3 (KB-29 revisi): stem hanya di kolom nama. deskripsi_stem dihapus karena pemotongan
# berlebih Sastrawi (pendapatan→dapat, penduduk→duduk) membuat kata umum di deskripsi
# ikut cocok. Menghapus kolom lebih sederhana daripada bobot 0: tidak diindeks sama sekali.
_SCHEMA_V3 = """
DROP TABLE datasets_fts;
CREATE VIRTUAL TABLE datasets_fts USING fts5(
    slug UNINDEXED, name, deskripsi, name_stem,
    tokenize = 'unicode61 remove_diacritics 2'
);
"""

MIGRATIONS: dict[int, str] = {1: _SCHEMA_V1, 2: _SCHEMA_V2, 3: _SCHEMA_V3}
# Migrasi yang mengubah isi FTS; setelahnya indeks dibangun ulang dari tabel datasets.
REINDEX_AFTER = frozenset({2, 3})

StemText = Callable[[str | None], str]


def _identity_stem(text: str | None) -> str:
    return text or ""


class CatalogNotReady(RuntimeError):
    """Berkas katalog belum ada atau versinya tidak cocok; jalankan sync_worker dulu."""


def sqlite_supports_trigram() -> bool:
    return sqlite3.sqlite_version_info >= MIN_SQLITE_FOR_TRIGRAM


def _configure(conn: sqlite3.Connection) -> sqlite3.Connection:
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    return conn


def schema_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def migrate(conn: sqlite3.Connection) -> int:
    """Terapkan migrasi yang belum ada, masing-masing dalam satu transaksi.

    Bila ada migrasi di REINDEX_AFTER, indeks FTS dibangun ulang di transaksi migrasi itu
    (butuh fungsi `mcp_stem` yang didaftarkan connect_write).
    """
    if not sqlite_supports_trigram():
        raise CatalogNotReady(
            f"SQLite {sqlite3.sqlite_version} tidak mendukung tokenizer trigram (butuh ≥ 3.34)"
        )
    current = schema_version(conn)
    for version in sorted(v for v in MIGRATIONS if v > current):
        # executescript() melakukan COMMIT sendiri; pernyataan dijalankan satu per satu agar
        # seluruh migrasi tetap di dalam satu transaksi.
        with transaction(conn):
            for statement in _split_sql(MIGRATIONS[version]):
                conn.execute(statement)
            if version in REINDEX_AFTER:
                rebuild_fts(conn)
            conn.execute(f"PRAGMA user_version = {version}")
    return schema_version(conn)


def _split_sql(script: str) -> list[str]:
    statements, buffer = [], []
    for line in script.splitlines():
        buffer.append(line)
        candidate = "\n".join(buffer).strip()
        if candidate and sqlite3.complete_statement(candidate):
            statements.append(candidate)
            buffer = []
    return statements


def connect_write(path: Path, *, stem_text: StemText | None = None) -> sqlite3.Connection:
    """Koneksi tulis (hanya sync_worker): WAL, busy_timeout, migrasi ke SCHEMA_VERSION.

    `stem_text` dipakai untuk kolom stem FTS (services.stemming.stem_text). Tanpa itu, kolom
    stem berisi teks asli; sync_worker selalu memberikannya.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = _configure(sqlite3.connect(path, isolation_level=None))
    conn.create_function("mcp_stem", 1, stem_text or _identity_stem, deterministic=True)
    mode = conn.execute("PRAGMA journal_mode = WAL").fetchone()[0]
    if str(mode).lower() != "wal":
        conn.close()
        raise CatalogNotReady(f"mode WAL tidak aktif ({mode})")
    migrate(conn)
    return conn


def connect_read(path: Path) -> sqlite3.Connection:
    """Koneksi baca saja (URI mode=ro) untuk server tool; menulis akan gagal di SQLite."""
    if not path.exists():
        raise CatalogNotReady(f"katalog {path} belum ada; jalankan sync_worker --once")
    conn = _configure(sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True))
    version = schema_version(conn)
    if version != SCHEMA_VERSION:
        conn.close()
        raise CatalogNotReady(f"versi skema katalog {version}, diharapkan {SCHEMA_VERSION}")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """BEGIN IMMEDIATE ... COMMIT; ROLLBACK bila ada galat (koneksi isolation_level=None)."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def rebuild_fts(conn: sqlite3.Connection) -> int:
    """Bangun ulang kedua indeks FTS dari tabel datasets (dipanggil di dalam transaksi).

    Kolom name_stem diisi lewat fungsi SQL `mcp_stem`. Mengembalikan jumlah record
    terindeks. Kolom yang disebut juga ada di skema v2, sehingga migrasi v2 tetap jalan.
    """
    conn.execute("DELETE FROM datasets_fts")
    conn.execute(
        "INSERT INTO datasets_fts (slug, name, deskripsi, name_stem) "
        "SELECT slug, name, coalesce(deskripsi_text, ''), mcp_stem(name) FROM datasets"
    )
    conn.execute("DELETE FROM datasets_trigram")
    conn.execute(
        "INSERT INTO datasets_trigram (slug, name, deskripsi) "
        "SELECT slug, name, coalesce(deskripsi_text, '') FROM datasets"
    )
    return int(conn.execute("SELECT count(*) FROM datasets").fetchone()[0])
