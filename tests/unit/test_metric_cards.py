"""Langkah 10a (KB-49): skema kartu metrik, pemeriksa terhadap katalog + kamus, perintah --check.

Katalog dari 751 record rekaman; kamus dibangun lewat get_dataset_info dengan FakeSourceAdapter
(fixture rekaman), lalu dibaca kembali dari cache lokal seperti perintah --check. Tanpa jaringan.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

from mcp_bogor import metric_cards as cli
from mcp_bogor.services.metric_cards import (
    CheckReport,
    MetricCard,
    MetricCardError,
    cache_lookup,
    check_cards,
    load_cards,
    normalize_unit,
    parse_cards,
    run_check,
    synonym_index,
    synonym_key,
)
from mcp_bogor.storage import db
from mcp_bogor.storage.cache import DictionaryCache
from mcp_bogor.storage.metric_cards import CardFile, MetricCardFileError, read_card_files
from tests.tool_support import ToolEnv

ROOT = Path(__file__).parents[2]
CARDS_DIR = ROOT / "metric_cards"
OVERRIDES_DIR = ROOT / "overrides"
PAD = "realisasi-pendapatan-asli-daerah-pad-kota-bogor"


@pytest.fixture(scope="module")
def cards() -> list[MetricCard]:
    return load_cards(CARDS_DIR)


def _base() -> dict[str, Any]:
    return yaml.safe_load((CARDS_DIR / "KM-06.yaml").read_text(encoding="utf-8"))


def _parse(*items: tuple[str, Any]) -> list[MetricCard]:
    return parse_cards([CardFile(name, data) for name, data in items])


# ---------------------------------------------------------------- skema dan berkas repo


def test_repo_has_twelve_valid_cards(cards: list[MetricCard]) -> None:
    assert [c.id for c in cards] == [f"KM-{i:02d}" for i in range(1, 13)]
    assert len({c.slug for c in cards}) == 12
    assert all(c.walidata == "Diskominfo Kota Bogor" and c.walidata_catatan for c in cards)
    # review kartu terhadap halaman portal 17 Nov 2026
    assert all(c.reviewed_by == "Akhdan" and str(c.reviewed_at) == "2026-11-17" for c in cards)
    by_id = {c.id: c for c in cards}
    assert by_id["KM-01"].status == "aktif"  # override total_pendapatan (TM-01)
    assert by_id["KM-07"].slug == "persentase-kepala-keluarga-miskin-di-kota-bogor"
    assert by_id["KM-12"].slug == (
        "persentase-penanganan-pengaduan-masyarakat-ke-inspektorat-daerah-kota-bogor"
    )
    # R-10: KM-06 dan KM-07 saling menyebut
    assert by_id["KM-06"].dataset_berbeda_definisi == [by_id["KM-07"].slug]
    assert by_id["KM-07"].dataset_berbeda_definisi == [by_id["KM-06"].slug]


def test_rejected_synonyms_are_absent(cards: list[MetricCard]) -> None:
    """Keputusan Akhdan 15 Nov 2026: bukan sinonim (R-10, O-01)."""
    index = synonym_index(cards)
    for text in ("pendapatan daerah", "puskesmas", "pengaduan", "aduan", "aduan warga"):
        assert synonym_key(text) not in index, text
    assert index[synonym_key("PAD")].id == "KM-01"
    assert index[synonym_key("Persentase realisasi PAD")].id == "KM-02"
    assert index[synonym_key("kemiskinan Kota Bogor")].id == "KM-06"  # stopword dibuang


def test_synonym_key_uses_search_sanitizing() -> None:
    assert synonym_key("Pendapatan  Asli-Daerah?") == ("pendapatan", "asli", "daerah")
    assert synonym_key("di Kota Bogor") == ()


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"lain": 1}, "Extra inputs"),
        ({"id": "KM-1"}, "id"),
        ({"slug": "Slug Salah"}, "slug"),
        ({"sinonim": []}, "sinonim"),
        ({"sinonim": ["kota bogor"]}, "kosong setelah sanitasi"),
        ({"sinonim": ["Miskin", "miskin?"]}, "sinonim sama"),
        ({"reviewed_by": "Akhdan", "reviewed_at": None}, "berpasangan"),
        ({"reviewed_by": None, "reviewed_at": "2026-11-17"}, "berpasangan"),
        ({"kolom_dimensi": ["tahun", "tingkat_kemiskinan"]}, "kolom_nilai"),
        ({"kolom_dimensi": ["tahun", "tahun"]}, "duplikat"),
        ({"dataset_berbeda_definisi": ["tingkat-kemiskinan-di-kota-bogor-2"]}, "sendiri"),
        ({"periode_tersedia": {"awal": 2025, "akhir": 2019}}, "awal"),
        ({"status": "resmi"}, "status"),
        ({"kasus_uji": ["N03"]}, "kasus_uji"),
        ({"satuan": "  "}, "satuan"),
    ],
)
def test_schema_rejects(change: dict[str, Any], message: str) -> None:
    data = _base() | change
    with pytest.raises(MetricCardError, match=message):
        _parse(("KM-06", data))


def test_reviewed_pair_accepted() -> None:
    card = _parse(("KM-06", _base() | {"reviewed_by": "Akhdan", "reviewed_at": "2026-11-16"}))[0]
    assert str(card.reviewed_at) == "2026-11-16"


def test_cross_card_rules() -> None:
    other = _base() | {"id": "KM-13", "sinonim": ["angka kemiskinan", "lain sendiri"]}
    with pytest.raises(MetricCardError) as err:
        _parse(("KM-06", _base()), ("KM-13", other), ("KM-14", _base() | {"id": "KM-14"}))
    text = str(err.value)
    assert "slug sama dengan KM-06" in text
    assert "sinonim 'angka kemiskinan' sudah dipakai KM-06" in text
    with pytest.raises(MetricCardError, match="field id berisi"):
        _parse(("KM-99", _base()))
    with pytest.raises(MetricCardError, match="harus objek"):
        _parse(("KM-06", ["bukan", "objek"]))


def test_card_files_io(tmp_path: Path) -> None:
    with pytest.raises(MetricCardFileError, match="tidak ada"):
        read_card_files(tmp_path / "hilang")
    (tmp_path / "KM-01.yaml").write_text("id: [rusak", encoding="utf-8")
    with pytest.raises(MetricCardFileError, match="YAML tidak sah"):
        read_card_files(tmp_path)


def test_normalize_unit() -> None:
    assert normalize_unit(" Persen ") == normalize_unit("PERSEN") == normalize_unit("%")
    assert normalize_unit("KM/JAM") == "km/jam"
    assert normalize_unit(None) is None


# ---------------------------------------------------------------- pemeriksaan (fixture)


@pytest.fixture(scope="module")
def cached_env(
    catalog_db: Path, tmp_path_factory: pytest.TempPathFactory, cards: list[MetricCard]
) -> ToolEnv:
    """Kamus 12 dataset di cache, dibangun lewat get_dataset_info (override repo)."""
    env = ToolEnv(catalog_db, tmp_path_factory.mktemp("kartu"), overrides_dir=OVERRIDES_DIR)
    for card in cards:
        out = env.info(slug=card.slug)
        assert out["status"] in ("ok", "partial"), (card.id, out["error"])
    return env


@pytest.fixture
def conn(catalog_db: Path) -> Iterator[sqlite3.Connection]:
    reader = db.connect_read(catalog_db)
    yield reader
    reader.close()


def _check(env: ToolEnv, conn: sqlite3.Connection, cards: list[MetricCard]) -> CheckReport:
    lookup = cache_lookup(DictionaryCache(env.settings.cache_dir), env.settings.overrides_dir)
    return check_cards(cards, conn, lookup)


def _codes(report: CheckReport, card_id: str) -> set[str]:
    return {f.code for c in report.cards if c.card.id == card_id for f in c.findings}


def test_repo_cards_pass_against_fixture(
    cached_env: ToolEnv, conn: sqlite3.Connection, cards: list[MetricCard]
) -> None:
    """Semua kartu sudah direview dan konsisten tanpa temuan; KM-01 aktif lewat override repo."""
    report = _check(cached_env, conn, cards)
    assert report.exit_code == 0, report.render()
    for check in report.cards:
        assert check.findings == [], report.render()
        assert check.outcome == "ok"
    assert "belum diperiksa" not in report.render()


def test_without_cache_everything_is_unchecked(
    catalog_db: Path, tmp_path: Path, conn: sqlite3.Connection, cards: list[MetricCard]
) -> None:
    env = ToolEnv(catalog_db, tmp_path, overrides_dir=OVERRIDES_DIR)
    report = _check(env, conn, cards)
    assert report.exit_code == 3
    assert all("DICTIONARY_NOT_CACHED" in _codes(report, c.id) for c in cards)
    assert "UNIT_NOT_CATALOG" not in str([_codes(report, c.id) for c in cards])


def _card(cards: list[MetricCard], card_id: str, **update: Any) -> MetricCard:
    card = next(c for c in cards if c.id == card_id)
    return card.model_copy(update=update)


@pytest.mark.parametrize(
    ("card_id", "update", "code", "level"),
    [
        ("KM-01", {"status": "kamus_belum_tervalidasi"}, "STATUS_OUTDATED", "error"),
        ("KM-06", {"reviewed_by": None, "reviewed_at": None}, "NOT_REVIEWED", "warning"),
        ("KM-06", {"status": "kamus_belum_tervalidasi"}, "STATUS_OUTDATED", "error"),
        ("KM-06", {"kolom_nilai": "jumlah"}, "MEASURE_MISSING", "error"),
        ("KM-06", {"kolom_nilai": "satuan"}, "MEASURE_ROLE", "error"),
        ("KM-06", {"kolom_dimensi": ["tahun", "kecamatan"]}, "DIMENSION_MISSING", "error"),
        ("KM-10", {"kolom_dimensi": ["tahun", "kecepatan"]}, "DIMENSION_ROLE", "error"),
        ("KM-06", {"satuan": "RUPIAH"}, "UNIT_MISMATCH", "error"),
        ("KM-06", {"opd": "Dinas Kesehatan"}, "OPD_MISMATCH", "error"),
        ("KM-06", {"slug": "tidak-ada-di-katalog"}, "NOT_IN_CATALOG", "error"),
        ("KM-06", {"dataset_berbeda_definisi": ["hilang"]}, "RELATED_NOT_IN_CATALOG", "error"),
        ("KM-06", {"status": "perlu_konfirmasi"}, "NEEDS_CONFIRMATION", "warning"),
    ],
)
def test_check_findings(
    cached_env: ToolEnv,
    conn: sqlite3.Connection,
    cards: list[MetricCard],
    card_id: str,
    update: dict[str, Any],
    code: str,
    level: str,
) -> None:
    report = _check(cached_env, conn, [_card(cards, card_id, **update)])
    findings = [f for f in report.cards[0].findings if f.code == code]
    assert findings and findings[0].level == level, report.render()
    assert report.exit_code == (1 if level == "error" else 0)


def test_period_differences_are_warnings(
    cached_env: ToolEnv, conn: sqlite3.Connection, cards: list[MetricCard]
) -> None:
    card = _card(cards, "KM-07", periode_tersedia=_card(cards, "KM-06").periode_tersedia)
    report = _check(cached_env, conn, [card])
    assert {"PERIOD_DIFFERS", "PERIOD_DIFFERS_DATA"} <= _codes(report, "KM-07")
    assert report.exit_code == 0


def test_related_cards_should_be_mutual(
    cached_env: ToolEnv, conn: sqlite3.Connection, cards: list[MetricCard]
) -> None:
    km07 = _card(cards, "KM-07", dataset_berbeda_definisi=[])
    report = _check(cached_env, conn, [_card(cards, "KM-06"), km07])
    assert "RELATED_NOT_MUTUAL" in _codes(report, "KM-06")


def test_km01_status_follows_override(
    catalog_db: Path, tmp_path: Path, conn: sqlite3.Connection, cards: list[MetricCard]
) -> None:
    """Tanpa override total_pendapatan, KM-01 tidak boleh aktif; begitu override lengkap masuk,
    kartu wajib dinaikkan statusnya. Override berubah setelah kamus di-cache → belum dicek
    (bukan hasil basi)."""
    overrides = tmp_path / "overrides"
    overrides.mkdir()
    env = ToolEnv(catalog_db, tmp_path, overrides_dir=overrides)
    km01 = [_card(cards, "KM-01")]
    pending = [_card(cards, "KM-01", status="kamus_belum_tervalidasi")]
    env.info(slug=PAD)
    without = _check(env, conn, km01)
    assert "STATUS_NOT_VALIDATED" in _codes(without, "KM-01")
    assert without.exit_code == 1
    assert _check(env, conn, pending).exit_code == 0

    (overrides / f"{PAD}.yaml").write_text(
        yaml.safe_dump(
            {
                "slug": PAD,
                "columns": {
                    "total_pendapatan": {
                        "meaning": "arti uji",
                        "role": "measure",
                        "unit": "rupiah",
                        "reviewed_by": "Uji",
                        "reviewed_at": "2026-11-15",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    stale = _check(env, conn, km01)
    assert "DICTIONARY_OVERRIDE_CHANGED" in _codes(stale, "KM-01")
    assert stale.exit_code == 3

    env.info(slug=PAD)  # kamus dibangun ulang dengan override
    report = _check(env, conn, pending)
    assert "STATUS_OUTDATED" in _codes(report, "KM-01")
    assert report.exit_code == 1
    active = _check(env, conn, km01)
    assert _codes(active, "KM-01") == set() and active.exit_code == 0

    (overrides / f"{PAD}.yaml").write_text("slug: [rusak", encoding="utf-8")
    assert "OVERRIDE_INVALID" in _codes(_check(env, conn, km01), "KM-01")


# ---------------------------------------------------------------- run_check dan perintah


def test_run_check_reports_invalid_cards(tmp_path: Path, catalog_db: Path) -> None:
    (tmp_path / "KM-01.yaml").write_text("id: KM-02\n", encoding="utf-8")
    report = run_check(tmp_path, catalog_db, tmp_path / "cache", OVERRIDES_DIR)
    assert report.exit_code == 1 and report.problems
    assert run_check(tmp_path / "hilang", catalog_db, tmp_path, OVERRIDES_DIR).exit_code == 1


def _cli_env(monkeypatch: pytest.MonkeyPatch, **values: Path) -> None:
    """Dipakai bersama settings_env: cwd tmp dan env MCP_ bersih (tanpa .env laptop)."""
    for key, value in values.items():
        monkeypatch.setenv(f"MCP_{key.upper()}", str(value))


def test_cli_check(
    settings_env: Path,
    cached_env: ToolEnv,
    catalog_db: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _cli_env(
        monkeypatch,
        db_path=catalog_db,
        cache_dir=cached_env.settings.cache_dir,
        overrides_dir=OVERRIDES_DIR,
        metric_cards_dir=CARDS_DIR,
    )
    assert cli.main(["--check"]) == 0
    out = capsys.readouterr().out
    assert "KM-01" in out and "KM-12" in out and "Ringkasan: 12 kartu" in out
    assert cli.main([]) == 2


def test_cli_catalog_missing(
    settings_env: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _cli_env(
        monkeypatch,
        db_path=tmp_path / "tidak-ada.db",
        cache_dir=tmp_path / "cache",
        metric_cards_dir=CARDS_DIR,
    )
    assert cli.main(["--check"]) == 2
    assert "DITOLAK" in capsys.readouterr().out


# ---------------------------------------------------------------- indeks untuk search (KB-50)


def test_card_index_uses_usable_cards(cards: list[MetricCard]) -> None:
    from mcp_bogor.services.metric_cards import card_index

    index = card_index(cards)
    assert len(index.card_of) == 12
    assert index.pins[synonym_key("pendapatan asli daerah")] == PAD  # KM-01 tetap dipasang
    km06 = next(c for c in cards if c.id == "KM-06")
    km07 = next(c for c in cards if c.id == "KM-07")
    assert index.different[km06.slug] == frozenset({km07.slug})
    assert index.different[km07.slug] == frozenset({km06.slug})

    pending = [c.model_copy(update={"status": "perlu_konfirmasi"}) if c.id == "KM-06" else c
               for c in cards]  # fmt: skip
    index = card_index(pending)
    assert km06.slug not in index.card_of
    assert synonym_key("kemiskinan") not in index.pins
    # R-10 tetap dari kartu KM-07 (dua arah) walau KM-06 belum terkonfirmasi
    assert index.different[km06.slug] == frozenset({km07.slug})


def test_card_index_one_sided_pair_is_mutual(cards: list[MetricCard]) -> None:
    from mcp_bogor.services.metric_cards import card_index

    km06 = next(c for c in cards if c.id == "KM-06")
    index = card_index([km06.model_copy(update={"dataset_berbeda_definisi": ["lain"]})])
    assert index.different == {km06.slug: frozenset({"lain"}), "lain": frozenset({km06.slug})}


def test_load_card_index_degrades_without_failing(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    from mcp_bogor.runtime import load_card_index
    from mcp_bogor.services.catalog import EMPTY_CARDS
    from tests.fakes import make_settings

    caplog.set_level("INFO", logger="mcp_bogor.runtime")
    assert load_card_index(make_settings(tmp_path, metric_cards_dir=CARDS_DIR)).card_of
    assert "metric_cards_loaded" in caplog.text
    broken = tmp_path / "kartu"
    broken.mkdir()
    (broken / "KM-01.yaml").write_text("id: KM-02\n", encoding="utf-8")
    for directory in (broken, tmp_path / "hilang"):
        caplog.clear()
        assert load_card_index(make_settings(tmp_path, metric_cards_dir=directory)) is EMPTY_CARDS
        assert "metric_cards_invalid" in caplog.text
