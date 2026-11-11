"""services/catalog.search (Dok 4 bagian 3): UT-10, filter, paginasi, cadangan trigram,
relevansi golden set, NFR-03. Katalog: sinkron FakeSourceAdapter (751 record rekaman)."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import statistics
import time
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import pytest

from mcp_bogor.config import WIB
from mcp_bogor.models import SearchParams, SearchResult
from mcp_bogor.services import catalog
from mcp_bogor.services.stemming import stem_for_index
from mcp_bogor.storage import db
from mcp_bogor.sync_worker import reindex, sync_once
from tests.fakes import FakeSourceAdapter, make_settings

GOLDEN = Path(__file__).parents[2] / "golden_set" / "golden_set_v0.2.jsonl"
SYNC_AT = datetime(2026, 11, 10, 2, 0, tzinfo=WIB)


@pytest.fixture(scope="module")
def conn(tmp_path_factory: pytest.TempPathFactory) -> Iterator[sqlite3.Connection]:
    settings = make_settings(tmp_path_factory.mktemp("katalog"))
    report = asyncio.run(sync_once(settings, adapter=FakeSourceAdapter(), now=lambda: SYNC_AT))
    assert report.status == "complete"
    assert reindex(settings).records == 751  # relevansi diukur setelah jalur --reindex
    reader = db.connect_read(settings.db_path)
    yield reader
    reader.close()


def _search(conn: sqlite3.Connection, query: str, **kwargs: object) -> catalog.CatalogSearch:
    return catalog.search(conn, SearchParams(query=query, **kwargs))


def _slugs(result: SearchResult) -> list[str]:
    return [item.slug for item in result.items]


# ---------------------------------------------------------------- sanitasi kueri (FR-09, UT-10)


@pytest.mark.parametrize(
    ("text", "tokens"),
    [
        ("Kemiskinan", ["kemiskinan"]),
        ("Berapa angka kemiskinan di Kota Bogor tahun 2025?", ["angka", "kemiskinan", "2025"]),
        ("PAD; DROP TABLE datasets;--", ["pad", "drop", "table", "datasets"]),
        ('"wisatawan" OR NEAR(puskesmas) AND *', ["wisatawan", "or", "near", "puskesmas", "and"]),
        ("name:silpa -belanja ^modal", ["name", "silpa", "belanja", "modal"]),
        ("kecepatan_lalu-lintas", ["kecepatan", "lalu", "lintas"]),
        ("Sékolah  dasar  sekolah", ["sékolah", "dasar", "sekolah"]),
        ("a b c ? !!", []),
        ("di dan yang ke dari", []),
    ],
)
def test_query_tokens(text: str, tokens: list[str]) -> None:
    assert catalog.query_tokens(text) == tokens


def test_fts_literal_quotes_everything() -> None:
    assert catalog.fts_literal('ab"c') == '"ab""c"'
    assert catalog.word_match(["pad", "kemiskinan"]) == (
        '({name deskripsi name_stem} : "pad") AND '
        '({name deskripsi} : "kemiskinan"* OR {name name_stem} : "miskin"*)'
    )
    assert catalog.trigram_match(["pad", "ab"]) == '"pad"'
    assert catalog.trigram_match(["ab"]) is None


@pytest.mark.parametrize(
    "query",
    [
        "AND",
        "OR OR OR",
        "NEAR(a b)",
        "kemiskinan*",
        '"kemiskinan',
        "name:kemiskinan",
        "-kemiskinan",
        "^kemiskinan",
        "kemiskinan + pad",
        "(((",
        "!!",
        "  ..  ",
        "di",
        "x" * 100,
        "sekolah'; DROP TABLE datasets; --",
        "{slug} [x] <y> @z #q $r %s",
        "日本語 テスト",
    ],
)
def test_ut10_hostile_queries_never_raise(conn: sqlite3.Connection, query: str) -> None:
    outcome = _search(conn, query)
    assert outcome.result.total >= 0
    assert conn.execute("SELECT count(*) FROM datasets").fetchone()[0] == 751


def test_empty_after_cleaning_gives_empty_result(conn: sqlite3.Connection) -> None:
    outcome = _search(conn, "?? !!")
    assert outcome.match_mode == "none" and outcome.tokens == ()
    assert outcome.result.total == 0 and outcome.result.items == []


def test_operators_are_literals(conn: sqlite3.Connection) -> None:
    """'OR' dari pengguna dicari sebagai kata, bukan operator: hasil menyempit, tidak meluas."""
    both = _search(conn, "wisatawan OR puskesmas").result.total
    assert both < _search(conn, "wisatawan").result.total


# ---------------------------------------------------------------- strategi dan urutan


def test_word_match_mode_and_ranking(conn: sqlite3.Connection) -> None:
    outcome = _search(conn, "wisatawan")
    assert outcome.match_mode == "word"
    assert _slugs(outcome.result)[0] == "perkembangan-jumlah-kunjungan-wisatawan-di-kota-bogor"


def test_prefix_match(conn: sqlite3.Connection) -> None:
    outcome = _search(conn, "wisata")  # prefix dari wisatawan
    assert outcome.match_mode == "word" and outcome.result.total >= 2


def test_trigram_fallback_for_affixes(conn: sqlite3.Connection) -> None:
    outcome = _search(conn, "iskinan")  # bagian dari "kemiskinan", bukan awal kata
    assert outcome.match_mode == "trigram"
    assert "tingkat-kemiskinan-di-kota-bogor-2" in _slugs(outcome.result)


def test_no_match(conn: sqlite3.Connection) -> None:
    outcome = _search(conn, "zzzqqqxxx")
    assert outcome.match_mode == "none" and outcome.result.total == 0


def test_name_outweighs_description(conn: sqlite3.Connection) -> None:
    items = _search(conn, "puskesmas", page_size=20).result.items
    assert all("puskesmas" in item.name.lower() for item in items[:3])


def test_items_carry_catalog_fields(conn: sqlite3.Connection) -> None:
    result = _search(conn, "kemiskinan").result
    item = result.items[0]
    assert item.opd == "Dinas Sosial" and item.topik == "Sosial"
    assert (item.tahun_awal, item.tahun_akhir) == (2025, 2025)
    assert item.officialness.status == "resmi" and item.officialness.basis is None
    assert item.data_updated_at and item.data_updated_at.utcoffset().total_seconds() == 7 * 3600
    assert result.catalog_synced_at == SYNC_AT
    dumped = json.loads(result.model_dump_json())
    assert dumped["catalog_synced_at"] == "2026-11-10T02:00:00+07:00"


# ---------------------------------------------------------------- filter dan paginasi


@pytest.mark.parametrize(
    "opd", ["badan_pendapatan_daerah", "Badan Pendapatan Daerah", "BADAN PENDAPATAN DAERAH"]
)
def test_filter_opd_by_slug_or_name(conn: sqlite3.Connection, opd: str) -> None:
    result = _search(conn, "pendapatan", opd=opd, page_size=20).result
    assert result.total > 0
    assert {item.opd for item in result.items} == {"Badan Pendapatan Daerah"}


def test_filter_topik_and_tahun(conn: sqlite3.Connection) -> None:
    result = _search(conn, "puskesmas", topik="kesehatan", page_size=20).result
    assert result.total > 0 and {i.topik for i in result.items} == {"Kesehatan"}
    by_year = _search(conn, "kemiskinan", tahun=2025).result
    assert _slugs(by_year) == ["tingkat-kemiskinan-di-kota-bogor-2"]
    in_2019 = _search(conn, "kemiskinan", tahun=2019, page_size=20).result.items
    assert N01 not in [i.slug for i in in_2019]  # N-01 hanya 2025
    assert all(i.tahun_awal <= 2019 <= i.tahun_akhir for i in in_2019)
    assert _search(conn, "kemiskinan", opd="Dinas Kesehatan").result.total == 0


def test_filters_apply_to_trigram_fallback(conn: sqlite3.Connection) -> None:
    assert _search(conn, "iskinan", tahun=2025).match_mode == "trigram"
    assert _search(conn, "iskinan", tahun=2001).result.total == 0


def test_pagination(conn: sqlite3.Connection) -> None:
    first = _search(conn, "puskesmas", page=1, page_size=4).result
    second = _search(conn, "puskesmas", page=2, page_size=4).result
    everything = _search(conn, "puskesmas", page=1, page_size=20).result
    assert first.total == second.total == everything.total == 9
    assert _slugs(first) + _slugs(second) == _slugs(everything)[:8]
    assert len(_search(conn, "puskesmas", page=5, page_size=4).result.items) == 0


def test_search_logs_without_query_text(
    conn: sqlite3.Connection, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("INFO", logger="mcp_bogor.services.catalog")
    _search(conn, "rahasia-kueri kemiskinan", opd="Dinas Sosial")
    event = json.loads(caplog.records[-1].getMessage())
    assert event == {
        "event": "catalog_search",
        "match_mode": "none",  # token "rahasia" dan "kueri" ikut di-AND-kan
        "tokens": 3,
        "total": 0,
        "filters": ["opd"],
    }
    assert "rahasia" not in caplog.text


# ---------------------------------------------------------------- relevansi golden set

N01 = "tingkat-kemiskinan-di-kota-bogor-2"
N03 = "realisasi-pendapatan-asli-daerah-pad-kota-bogor"
AMBIGUOUS = (
    "ambigu; diselesaikan sinonim KM-01 di Langkah 10 (peringkat 5: sebelas dataset memuat "
    "frasa 'pendapatan asli daerah'; bobot cakupan tidak dinaikkan untuk satu kasus)"
)

# (kasus golden set / kartu metrik, kueri, slug target atau None = dari golden set, alasan xfail)
RELEVANCE: list[tuple[str, str, str | None, str | None]] = [
    ("N-01", "kemiskinan", N01, None),
    ("N-01", "miskin", N01, None),
    ("N-01", "penduduk miskin", N01, None),
    ("N-02", "kecepatan lalu lintas pajajaran", None, None),
    ("N-03", "PAD", N03, None),
    ("N-03", "pendapatan asli daerah", N03, AMBIGUOUS),
    ("N-04", "belanja modal", None, None),
    ("N-05", "SiLPA", None, None),
    ("N-05", "silpa", None, None),
    ("N-06", "wisatawan", None, None),
    ("N-06", "kunjungan wisatawan", None, None),
    ("N-07", "puskesmas", None, None),
    ("N-07", "pasien puskesmas", None, None),
    ("N-08", "volume lalu lintas pajajaran", None, None),
    ("KM-07", "kepala keluarga miskin", "persentase-kepala-keluarga-miskin-di-kota-bogor", None),
    ("Q-03", "retribusi", None, None),
    ("Q-02", "persentase realisasi PAD", None, None),
    (
        "KM-12",
        "pengaduan",
        "persentase-penanganan-pengaduan-masyarakat-ke-inspektorat-daerah-kota-bogor",
        None,
    ),
]


def _golden_slug(case_id: str) -> str:
    for line in GOLDEN.read_text(encoding="utf-8").splitlines():
        case = json.loads(line)
        if case["id"] == case_id:
            return case["expected"]["dataset"]
    raise KeyError(case_id)


def _target(case_id: str, expected: str | None) -> str:
    if expected is None or case_id.startswith(("N-", "Q-", "F-")):
        golden = _golden_slug(case_id)
        assert expected in (None, golden)
        return golden
    return expected


@pytest.mark.parametrize(
    ("case_id", "query", "expected"),
    [
        pytest.param(
            case_id,
            query,
            expected,
            marks=[pytest.mark.xfail(strict=True, reason=reason)] if reason else [],
            id=f"{case_id}-{query}",
        )
        for case_id, query, expected, reason in RELEVANCE
    ],
)
def test_golden_relevance_top3(
    conn: sqlite3.Connection, case_id: str, query: str, expected: str | None
) -> None:
    slug = _target(case_id, expected)
    top3 = _slugs(_search(conn, query, page_size=3).result)
    assert slug in top3, (query, top3)


def test_relevance_table_is_reported(conn: sqlite3.Connection) -> None:
    """Bukan asersi relevansi; mencetak tabel untuk laporan (pytest -s)."""
    print("\n| Kasus | Kueri | Mode | Total | Peringkat |")
    for case_id, query, expected, _ in RELEVANCE:
        slug = _target(case_id, expected)
        outcome = _search(conn, query, page_size=20)
        slugs = _slugs(outcome.result)
        rank = slugs.index(slug) + 1 if slug in slugs else "-"
        print(f"| {case_id} | {query} | {outcome.match_mode} | {outcome.result.total} | {rank} |")
    assert len(RELEVANCE) == 18


# ---------------------------------------------------------------- KB-29: prefix, cakupan, stem


def test_short_tokens_are_not_prefixes(conn: sqlite3.Connection) -> None:
    """'pad' tidak lagi cocok dengan 'pada' di hampir semua deskripsi (KB-29.1)."""
    outcome = _search(conn, "PAD", page_size=20)
    slugs = _slugs(outcome.result)
    assert 0 < outcome.result.total < 20  # sebelumnya 751
    assert "produktivitas-padi-per-hektar-di-kota-bogor" not in slugs  # dulu peringkat 1
    for slug in slugs:
        name, text = conn.execute(
            "SELECT name, deskripsi_text FROM datasets WHERE slug = ?", [slug]
        ).fetchone()
        assert "pad" in catalog.query_tokens(f"{name} {text}"), slug


@pytest.mark.parametrize(
    ("name", "tokens", "coverage"),
    [
        (
            "Realisasi Pendapatan Asli Daerah (PAD) Kota Bogor",
            ["pendapatan", "asli", "daerah"],
            0.6,
        ),
        ("Jumlah Puskesmas di Kota Bogor", ["puskesmas"], 0.5),
        ("Jumlah Puskesmas di Kota Bogor", ["pusk"], 0.5),  # prefix ≥ 4 huruf
        ("Jumlah Puskesmas di Kota Bogor", ["pus"], 0.0),  # prefix < 4 huruf tidak dihitung
        ("Kota Bogor", ["bogor"], 0.0),  # nama hanya stopword
        ("Tingkat Kemiskinan", [], 0.0),
    ],
)
def test_name_coverage(name: str, tokens: list[str], coverage: float) -> None:
    assert catalog.name_coverage(name, tokens) == pytest.approx(coverage)


def test_name_coverage_substring_for_trigram() -> None:
    assert catalog.name_coverage("Tingkat Kemiskinan", ["iskin"], substring=True) == 0.5
    assert catalog.name_coverage("Tingkat Kemiskinan", ["iskin"]) == 0.0


# ---------------------------------------------------------------- pemotongan berlebih


@pytest.mark.parametrize(
    ("word", "stem"),
    [
        ("kemiskinan", "miskin"),
        ("kesehatan", "sehat"),
        ("kecepatan", "cepat"),
        ("kunjungan", "kunjung"),
        ("mendapat", "dapat"),
        ("diduduki", "duduk"),
        ("pendapatan", None),  # pen-: makna bergeser
        ("penduduk", None),
        ("kependudukan", None),  # awalan ganda ke-pen-
        ("pengaduan", None),
        ("penanganan", None),  # kata dasar luluh (tangan)
        ("penerimaan", None),
        ("perhubungan", None),
        ("realisasi", None),  # tidak berubah: tidak ditambahkan
    ],
)
def test_stem_for_index(word: str, stem: str | None) -> None:
    assert stem_for_index(word) == stem


@pytest.mark.parametrize(
    ("query", "family"), [("dapat", "pendapatan"), ("duduk", "penduduk"), ("adu", "pengaduan")]
)
def test_overstemming_does_not_win_rank_one(
    conn: sqlite3.Connection, query: str, family: str
) -> None:
    """Kueri kata dasar tidak boleh menaruh dataset pendapatan/penduduk/pengaduan di
    peringkat 1 hanya karena stem (KB-29)."""
    outcome = _search(conn, query, page_size=5)
    items = outcome.result.items
    if items:
        top = catalog.query_tokens(items[0].name)
        assert not any(family in word for word in top), items[0].name
    if outcome.match_mode == "word":
        for item in items:
            stems = conn.execute(
                "SELECT name_stem FROM datasets_fts WHERE slug = ?", [item.slug]
            ).fetchone()[0]
            names = catalog.query_tokens(item.name)
            assert family not in " ".join(names) or query not in stems.split(), item.name


def test_affixed_query_still_finds_family(conn: sqlite3.Connection) -> None:
    """Pemotongan dicegah hanya untuk kueri kata dasar; kueri berimbuhan tetap cocok harfiah."""
    assert _slugs(_search(conn, "penduduk", page_size=3).result)
    assert "persentase-penanganan-pengaduan-masyarakat-ke-inspektorat-daerah-kota-bogor" in (
        _slugs(_search(conn, "pengaduan masyarakat", page_size=3).result)
    )


def test_stem_columns_match_query_stems(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Jalur stem (KB-29.2) diuji dengan stemmer mainan, terlepas dari pilihan stemmer."""
    from mcp_bogor import sync_worker
    from mcp_bogor.services import stemming

    def toy_stem(token: str) -> str:
        return token.removeprefix("ke").removesuffix("an") if token.startswith("ke") else token

    def toy_text(text: str | None) -> str:
        return " ".join(toy_stem(t) for t in stemming.tokenize(text))

    monkeypatch.setattr(sync_worker, "stem_text", toy_text)
    monkeypatch.setattr(catalog, "stem_token", toy_stem)
    settings = make_settings(tmp_path)
    asyncio.run(sync_once(settings, adapter=FakeSourceAdapter(), now=lambda: SYNC_AT))
    reader = db.connect_read(settings.db_path)
    stems = reader.execute("SELECT name_stem FROM datasets_fts WHERE slug = ?", [N01]).fetchone()[0]
    assert "miskin" in stems.split()
    outcome = _search(reader, "kemiskinan")  # stem kueri 'miskin' cocok ke kolom stem
    assert outcome.match_mode == "word"
    assert N01 in _slugs(outcome.result)
    assert "persentase-kepala-keluarga-miskin-di-kota-bogor" in _slugs(outcome.result)
    reader.close()


# ---------------------------------------------------------------- kinerja NFR-03


def test_nfr03_p95_under_half_second(conn: sqlite3.Connection) -> None:
    queries = [
        "kemiskinan", "PAD", "pendapatan asli daerah", "wisatawan", "puskesmas", "SiLPA",
        "kecepatan lalu lintas pajajaran", "belanja modal", "iskinan", "zzzqqq",
    ] * 5  # fmt: skip
    timings = []
    for index, query in enumerate(queries):
        params = {"tahun": 2024} if index % 3 == 0 else {}
        start = time.perf_counter()
        catalog.search(conn, SearchParams(query=query, **params))
        timings.append(time.perf_counter() - start)
    p95 = statistics.quantiles(timings, n=20)[18]
    assert len(timings) == 50 and p95 < 0.5, p95
