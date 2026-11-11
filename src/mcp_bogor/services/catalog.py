"""Katalog lokal: baris dari RawDataset, penulisan atomik, dan pencarian FTS5.

Penulisan (dipanggil sync_worker, satu-satunya penulis, ADR-010):
- `build_dataset_row`: terapkan service Langkah 2 (freshness, officialness, periode,
  normalisasi waktu dan nilai kosong) ke satu RawDataset. Tanpa kontak/alamat/akun (KK-J).
- `write_catalog`: SATU transaksi: upsert semua record, hapus yang hilang hanya bila
  snapshot lengkap (KK-K), ganti referensi, bangun ulang kedua FTS. Galat → rollback.

Pencarian (server tool, koneksi baca saja):
- Kueri disanitasi (FR-09, UT-10): token kata unicode, huruf kecil, tanpa tanda baca,
  tanpa stopword; setiap token dikutip sebagai literal FTS5, sehingga sintaks FTS dari
  pengguna tidak pernah dieksekusi. Kueri kosong → hasil kosong.
- Indeks kata dulu (KB-29): AND antartoken; tiap token cocok ke kata asli (nama dan
  deskripsi, prefix hanya untuk token ≥ 4 huruf) ATAU stem-nya di kolom name_stem (bobot
  sama dengan nama). Deskripsi tidak di-stem karena pemotongan berlebih Sastrawi.
  Bila 0 hasil, cadangan terakhir trigram untuk token ≥ 3 huruf. match_mode dicatat.
- Urutan: bm25 dikurangi NAME_COVERAGE_WEIGHT dikali cakupan nama (proporsi token nama tanpa
  stopword yang cocok dengan kueri), lalu tahun_akhir terbaru. Bobot cakupan kecil sehingga
  berperan sebagai pemecah seri saat skor bm25 berdekatan.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from mcp_bogor.adapters.base import RawDataset
from mcp_bogor.logging import get_logger, log_event
from mcp_bogor.models import Officialness, SearchItem, SearchParams, SearchResult
from mcp_bogor.services.freshness import compute_freshness
from mcp_bogor.services.normalize import clean_empty, html_to_text, parse_portal_datetime
from mcp_bogor.services.officialness import compute_officialness
from mcp_bogor.services.periods import period_bounds
from mcp_bogor.services.stemming import stem_for_index, stem_token, tokenize
from mcp_bogor.storage import db

LOGGER = get_logger(__name__)

# Kata yang terlalu umum untuk pencarian katalog; ubah di sini bila perlu.
STOPWORDS = frozenset({"di", "dan", "yang", "ke", "dari", "tahun", "berapa", "kota", "bogor"})
MIN_TOKEN_CHARS = 2
PREFIX_MIN_CHARS = 4  # KB-29.1: "pad" tidak lagi cocok dengan "pada"
TRIGRAM_MIN_CHARS = 3
NAME_WEIGHT = 10.0
DESCRIPTION_WEIGHT = 1.0
NAME_COVERAGE_WEIGHT = 1.0  # KB-29.3: pemecah seri setelah bm25; ubah di sini

META_SATUAN = "Satuan Dataset"
META_FREKUENSI = "Frekuensi Dataset"
META_TINGKAT = "Tingkat Penyajian Dataset"
META_CAKUPAN = "Cakupan Dataset"

MatchMode = Literal["word", "trigram", "none"]


# ---------------------------------------------------------------- baris katalog


def _text(value: Any) -> str | None:
    value = clean_empty(value)
    return None if value is None else str(value).strip() or None


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat(timespec="seconds")


def _portal_time(value: Any) -> datetime | None:
    try:
        return parse_portal_datetime(value)
    except (TypeError, ValueError):
        return None


def build_dataset_row(dataset: RawDataset, synced_at: datetime) -> dict[str, Any]:
    """RawDataset → baris tabel datasets (kolom db.DATASET_COLUMNS)."""
    record, metadata = dataset.record, dataset.metadata
    fresh = compute_freshness(record.get("history_draft"), _portal_time(record.get("mdate")))
    official = compute_officialness(record.get("validate"), record.get("klasifikasi"))
    tahun_awal, tahun_akhir = period_bounds(metadata)
    deskripsi_html = _text(record.get("deskripsi"))
    row = {
        "slug": dataset.slug,
        "dataset_id": _int(record.get("id")),
        "name": _text(record.get("name")) or dataset.slug,
        "organisasi_id": _int(record.get("organisasi_id")),
        "organisasi_name": _text(record.get("organisasi_name")),
        "organisasi_slug": _text(record.get("organisasi_slug")),
        "topik_id": _int(record.get("topik_id")),
        "topik_name": _text(record.get("topik_name")),
        "topik_slug": _text(record.get("topik_slug")),
        "kategori": _text(record.get("kategori")),
        "klasifikasi": _text(record.get("klasifikasi")),
        "validate": _text(record.get("validate")),
        "priority_level": _text(record.get("priority_level")),
        "data_score_status": _text(record.get("data_score_status")),
        "kode_wilayah": _text(record.get("kode_wilayah")),
        "schema_name": dataset.schema,
        "table_name": dataset.table,
        "satuan": _text(metadata.get(META_SATUAN)),
        "frekuensi": _text(metadata.get(META_FREKUENSI)),
        "tahun_awal": tahun_awal,
        "tahun_akhir": tahun_akhir,
        "tingkat_penyajian": _text(metadata.get(META_TINGKAT)),
        "cakupan": _text(metadata.get(META_CAKUPAN)),
        "deskripsi_html": deskripsi_html,
        "deskripsi_text": html_to_text(deskripsi_html),
        "data_updated_at": _iso(fresh.data_updated_at),
        "freshness_source": fresh.freshness_source,
        "metadata_changed_after_approve": int(fresh.metadata_changed_after_approve),
        "change_notes": fresh.change_notes,
        "official_status": official.status,
        "official_basis": official.basis,
        "portal_mdate": _iso(_portal_time(record.get("mdate"))),
        "synced_at": _iso(synced_at),
    }
    assert tuple(row) == db.DATASET_COLUMNS  # kolom dan urutan dijaga satu sumber
    return row


def reference_rows(items: Iterable[Mapping[str, Any]]) -> list[tuple[int, str, str | None]]:
    """Referensi /organisasi atau /topik → (id, name, slug) saja."""
    rows = []
    for item in items:
        ref_id, name = _int(item.get("id")), _text(item.get("name"))
        if ref_id is not None and name is not None:
            rows.append((ref_id, name, _text(item.get("slug"))))
    return rows


@dataclass(frozen=True)
class WriteCounts:
    inserted: int
    updated: int
    deleted: int
    # Slug baru dan slug yang dihapus (urut abjad), untuk laporan sinkron.
    added_slugs: tuple[str, ...] = ()
    removed_slugs: tuple[str, ...] = ()


def write_catalog(
    conn: sqlite3.Connection,
    rows: Sequence[Mapping[str, Any]],
    *,
    delete_missing: bool,
    organisasi: Iterable[Mapping[str, Any]] | None = None,
    topik: Iterable[Mapping[str, Any]] | None = None,
    before_commit: Callable[[sqlite3.Connection, WriteCounts], None] | None = None,
) -> WriteCounts:
    """Tulis katalog dalam SATU transaksi; pembaca WAL melihat versi lama sampai commit.

    `delete_missing` hanya boleh True bila snapshot lengkap (KK-K). Referensi None = tidak
    diubah (mis. /organisasi gagal diambil). `before_commit` dijalankan di transaksi yang
    sama (mis. memperbarui baris sync_runs).
    """
    columns = db.DATASET_COLUMNS
    updates = ", ".join(f"{c} = excluded.{c}" for c in columns if c != "slug")
    upsert = (
        f"INSERT INTO datasets ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)}) "
        f"ON CONFLICT(slug) DO UPDATE SET {updates}"
    )
    with db.transaction(conn):
        existing = {row[0] for row in conn.execute("SELECT slug FROM datasets")}
        incoming = {row["slug"] for row in rows}
        conn.executemany(upsert, [tuple(row[c] for c in columns) for row in rows])
        missing = sorted(existing - incoming) if delete_missing else []
        conn.executemany("DELETE FROM datasets WHERE slug = ?", [(slug,) for slug in missing])
        for table, items in (("organisasi", organisasi), ("topik", topik)):
            if items is not None:
                conn.execute(f"DELETE FROM {table}")
                conn.executemany(
                    f"INSERT INTO {table} (id, name, slug) VALUES (?, ?, ?)", reference_rows(items)
                )
        db.rebuild_fts(conn)
        counts = WriteCounts(
            inserted=len(incoming - existing),
            updated=len(incoming & existing),
            deleted=len(missing),
            added_slugs=tuple(sorted(incoming - existing)),
            removed_slugs=tuple(missing),
        )
        if before_commit is not None:
            before_commit(conn, counts)
    return counts


# ---------------------------------------------------------------- pencarian


def query_tokens(text: str) -> list[str]:
    """Token kata unicode, huruf kecil, tanpa tanda baca, stopword, dan token 1 huruf."""
    tokens: list[str] = []
    for token in tokenize(text):
        if len(token) >= MIN_TOKEN_CHARS and token not in STOPWORDS and token not in tokens:
            tokens.append(token)
    return tokens


def fts_literal(token: str) -> str:
    """Literal string FTS5: operator, kolom, dan tanda kutip dari input tidak dieksekusi."""
    return '"' + token.replace('"', '""') + '"'


def _term(token: str) -> str:
    literal = fts_literal(token)
    return f"{literal}*" if len(token) >= PREFIX_MIN_CHARS else literal


def word_match(tokens: Sequence[str]) -> str:
    """AND antartoken (KB-29.2).

    Token yang tidak berubah oleh stemmer: satu frasa ke {name deskripsi name_stem}, sehingga
    tidak ada hitung ganda. Token berimbuhan: kata asli ke {name deskripsi} ATAU stem-nya ke
    {name name_stem} (kata dasar di nama atau stem kata nama lain).
    """
    parts = []
    for token in tokens:
        stem = stem_token(token)
        if stem == token:
            parts.append(f"({{name deskripsi name_stem}} : {_term(token)})")
        else:
            parts.append(
                f"({{name deskripsi}} : {_term(token)} OR {{name name_stem}} : {_term(stem)})"
            )
    return " AND ".join(parts)


def trigram_match(tokens: Sequence[str]) -> str | None:
    usable = [t for t in tokens if len(t) >= TRIGRAM_MIN_CHARS]
    return " AND ".join(fts_literal(t) for t in usable) or None


def name_coverage(name: str, tokens: Sequence[str], *, substring: bool = False) -> float:
    """Proporsi token nama (tanpa stopword) yang cocok dengan kueri: kata, stem, prefix.

    `substring=True` untuk hasil trigram (token kueri boleh berada di tengah kata nama).
    """
    name_tokens = query_tokens(name)
    if not name_tokens or not tokens:
        return 0.0
    wanted = set(tokens) | {stem_token(t) for t in tokens}
    prefixes = [t for t in tokens if len(t) >= PREFIX_MIN_CHARS]

    def matches(word: str) -> bool:
        if word in wanted or stem_for_index(word) in wanted:
            return True
        if any(word.startswith(p) for p in prefixes):
            return True
        return substring and any(t in word for t in tokens if len(t) >= TRIGRAM_MIN_CHARS)

    return sum(1 for word in name_tokens if matches(word)) / len(name_tokens)


@dataclass(frozen=True)
class CatalogSearch:
    result: SearchResult
    match_mode: MatchMode
    tokens: tuple[str, ...]


def _filters(params: SearchParams) -> tuple[list[str], list[Any]]:
    clauses: list[str] = []
    args: list[Any] = []
    if params.opd:
        clauses.append(
            "(d.organisasi_slug = ? COLLATE NOCASE OR d.organisasi_name = ? COLLATE NOCASE)"
        )
        args += [params.opd, params.opd]
    if params.topik:
        clauses.append("(d.topik_name = ? COLLATE NOCASE OR d.topik_slug = ? COLLATE NOCASE)")
        args += [params.topik, params.topik]
    if params.tahun is not None:
        clauses.append("d.tahun_awal <= ? AND d.tahun_akhir >= ?")
        args += [params.tahun, params.tahun]
    return clauses, args


_BM25 = {
    "datasets_fts": (
        f"bm25(datasets_fts, 0.0, {NAME_WEIGHT}, {DESCRIPTION_WEIGHT}, {NAME_WEIGHT})"
    ),
    "datasets_trigram": f"bm25(datasets_trigram, 0.0, {NAME_WEIGHT}, {DESCRIPTION_WEIGHT})",
}


def _run(
    conn: sqlite3.Connection, table: str, match: str, params: SearchParams, tokens: Sequence[str]
) -> list[sqlite3.Row]:
    """Semua kandidat terurut (≤ jumlah katalog, ± 751), sebelum paginasi."""
    clauses, args = _filters(params)
    where = " AND ".join([f"{table} MATCH ?", *clauses])
    rows = conn.execute(
        f"SELECT d.*, {_BM25[table]} AS score FROM {table} "
        f"JOIN datasets AS d ON d.slug = {table}.slug WHERE {where}",
        [match, *args],
    ).fetchall()
    substring = table == "datasets_trigram"

    def key(row: sqlite3.Row) -> tuple[Any, ...]:
        coverage = name_coverage(row["name"], tokens, substring=substring)
        year = row["tahun_akhir"]
        return (row["score"] - NAME_COVERAGE_WEIGHT * coverage, year is None, -(year or 0),
                row["slug"])  # fmt: skip

    return sorted(rows, key=key)


def _item(row: sqlite3.Row) -> SearchItem:
    updated = row["data_updated_at"]
    return SearchItem(
        slug=row["slug"],
        name=row["name"],
        opd=row["organisasi_name"],
        topik=row["topik_name"],
        kategori=row["kategori"],
        satuan=row["satuan"],
        frekuensi=row["frekuensi"],
        tahun_awal=row["tahun_awal"],
        tahun_akhir=row["tahun_akhir"],
        data_updated_at=datetime.fromisoformat(updated) if updated else None,
        officialness=Officialness(status=row["official_status"]),
    )


def catalog_synced_at(conn: sqlite3.Connection) -> datetime | None:
    """Waktu selesai sinkron complete terakhir (untuk peringatan katalog usang)."""
    value = conn.execute(
        "SELECT max(finished_at) FROM sync_runs WHERE status = 'complete'"
    ).fetchone()[0]
    return datetime.fromisoformat(value) if value else None


def search(conn: sqlite3.Connection, params: SearchParams) -> CatalogSearch:
    """Cari katalog lokal (Dok 4 bagian 3). Tidak pernah melempar galat sintaks FTS."""
    tokens = query_tokens(params.query)
    rows: list[sqlite3.Row] = []
    mode: MatchMode = "none"
    if tokens:
        rows = _run(conn, "datasets_fts", word_match(tokens), params, tokens)
        mode = "word"
        trigram = trigram_match(tokens)
        if not rows and trigram is not None:
            rows = _run(conn, "datasets_trigram", trigram, params, tokens)
            mode = "trigram"
        if not rows:
            mode = "none"
    offset = (params.page - 1) * params.page_size
    result = SearchResult(
        total=len(rows),
        page=params.page,
        page_size=params.page_size,
        items=[_item(row) for row in rows[offset : offset + params.page_size]],
        catalog_synced_at=catalog_synced_at(conn),
    )
    log_event(
        LOGGER,
        "catalog_search",
        match_mode=mode,
        tokens=len(tokens),
        total=len(rows),
        filters=sorted(k for k in ("opd", "topik", "tahun") if getattr(params, k) is not None),
    )
    return CatalogSearch(result=result, match_mode=mode, tokens=tuple(tokens))
