"""Service Langkah 2 terhadap fixture asli (rekaman 2026-11-10) dan snapshot golden set.

Hanya memakai berkas yang di-commit: detail, /bigdata, halaman katalog 1 dan 8, dan
catalog_slim.json. Halaman katalog 2 s.d. 7 di-.gitignore dan tidak dipakai di sini.
"""

from __future__ import annotations

import json
import re
from functools import cache
from pathlib import Path
from typing import Any

import pytest

from mcp_bogor.adapters.opendata_jabar import flatten_metadata, flatten_metadata_filter
from mcp_bogor.services.freshness import compute_freshness
from mcp_bogor.services.normalize import normalize_label, normalize_rows
from mcp_bogor.services.officialness import compute_officialness, officialness_from_record
from mcp_bogor.services.periods import compute, period_bounds, periods_available
from mcp_bogor.services.quality import check_quality

ROOT = Path(__file__).parents[2]
RECORDED = ROOT / "tests" / "fixtures" / "recorded" / "2026-11-10"
GOLDEN = ROOT / "golden_set" / "golden_set_v0.2.jsonl"
KM07 = "persentase-kepala-keluarga-miskin-di-kota-bogor"
KM12 = "persentase-penanganan-pengaduan-masyarakat-ke-inspektorat-daerah-kota-bogor"
KEMISKINAN = "tingkat-kemiskinan-di-kota-bogor-2"
PAJAJARAN_SPEED = (
    "kecepatan-lalu-lintas-segmen-1-dan-2-berdasarkan-hari-operasional-di-jalan-raya-pajajaran-"
    "kota-bogor"
)


@cache
def exchanges_by_path() -> dict[str, dict[str, Any]]:
    out = {}
    for path in sorted(RECORDED.glob("[0-9][0-9][0-9]_*.json")):
        exchange = json.loads(path.read_text(encoding="utf-8"))
        if exchange["request"]["path"] != "/datasets":  # halaman katalog tidak semua ada
            out[exchange["request"]["path"]] = exchange
    return out


def record_of(slug: str) -> dict[str, Any]:
    return exchanges_by_path()[f"/datasets/{slug}"]["response"]["json"]["data"]


def bigdata_of(slug: str) -> dict[str, Any]:
    record = record_of(slug)
    return exchanges_by_path()[f"/bigdata/{record['schema']}/{record['table']}"]["response"]["json"]


def rows_of(slug: str) -> list[dict[str, Any]]:
    return bigdata_of(slug)["data"]


@cache
def golden() -> dict[str, dict[str, Any]]:
    cases = {}
    for line in GOLDEN.read_text(encoding="utf-8").splitlines():
        if line.strip():
            case = json.loads(line)
            cases[case["id"]] = case
    return cases


@cache
def catalog_slim() -> dict[str, Any]:
    return json.loads((RECORDED / "catalog_slim.json").read_text(encoding="utf-8"))


def flags_of(rows: list[dict[str, Any]], measure: str) -> list[tuple[str, int | None]]:
    return [(f.code, f.period) for f in check_quality(rows, [measure])]


# ---------------------------------------------------------------- snapshot golden set


ROW_CASES = [
    cid
    for cid, case in golden().items()
    if (case.get("snapshot") or {}).get("values")
    and "tahun" in case["snapshot"]["values"][0]
    and case["expected"].get("dataset")
]


def test_row_cases_cover_expected_ids() -> None:
    assert ROW_CASES == [
        "N-01", "N-03", "N-04", "N-05", "N-06", "N-07", "F-07", "Q-02", "Q-03",
    ]  # fmt: skip


@pytest.mark.parametrize("case_id", ROW_CASES)
def test_snapshot_values_match_fixture(case_id: str) -> None:
    case = golden()[case_id]
    slug = case["expected"]["dataset"]
    first = case["snapshot"]["values"][0]
    measure = case["expected"].get("measure") or next(k for k in first if k != "tahun")
    rows, issues = normalize_rows(rows_of(slug), {measure: "number", "tahun": "number"})
    assert issues == []
    by_year = {row["tahun"]: row[measure] for row in rows}
    for value in case["snapshot"]["values"]:
        assert by_year[value["tahun"]] == value[measure], (case_id, value["tahun"])


@pytest.mark.parametrize(
    "case_id",
    [cid for cid, c in golden().items() if (c.get("snapshot") or {}).get("data_updated_at")],
)
def test_snapshot_data_updated_at_is_last_approve(case_id: str) -> None:
    case = golden()[case_id]
    record = record_of(case["expected"]["dataset"])
    fresh = compute_freshness(record["history_draft"], record["mdate"])
    assert fresh.freshness_source == "approve"
    assert fresh.data_updated_at.date().isoformat() == case["snapshot"]["data_updated_at"]
    assert fresh.data_updated_at.utcoffset().total_seconds() == 7 * 3600


def _by_segment(rows: list[dict[str, Any]], measure: str, **where: Any) -> dict[str, Any]:
    out: dict[tuple[str, ...], Any] = {}
    for row in rows:
        if all(row[k] == v for k, v in where.items()):
            out[normalize_label(row["segmen"])[0]] = row[measure]
    return out


def test_n02_pivot_snapshot_after_label_normalization() -> None:
    rows = rows_of(PAJAJARAN_SPEED)
    kerja = _by_segment(rows, "kecepatan", tahun=2025, hari_operasional="HARI KERJA")
    libur = _by_segment(rows, "kecepatan", tahun=2025, hari_operasional="HARI LIBUR")
    for item in golden()["N-02"]["snapshot"]["values"]:
        assert kerja[item["segmen"]] == item["hari_kerja"]
        assert libur[item["segmen"]] == item["hari_libur"]


def test_n08_pivot_snapshot_after_label_normalization() -> None:
    case = golden()["N-08"]
    rows = rows_of(case["expected"]["dataset"])
    raw_labels = {row["segmen"] for row in rows}
    assert any("\u2013" in label for label in raw_labels)  # en dash asli (NB-08)
    assert len({normalize_label(label)[0] for label in raw_labels}) == 4
    for year in ("2023", "2025"):
        values = _by_segment(rows, "volume", tahun=int(year), hari_operasional="HARI KERJA")
        for item in case["snapshot"]["values"]:
            assert values[item["segmen"]] == item[year], (year, item["segmen"])


# ---------------------------------------------------------------- kasus yang diminta


def test_q01_zero_period_2024_on_real_data() -> None:
    rows = rows_of(PAJAJARAN_SPEED)
    assert {r["kecepatan"] for r in rows if r["tahun"] == 2024} == {0}
    assert flags_of(rows, "kecepatan") == [("suspect_zero_period", 2024)]
    kerja = [r for r in rows if r["hari_operasional"] == "HARI KERJA"]  # filter Q-01
    assert flags_of(kerja, "kecepatan") == [("suspect_zero_period", 2024)]


def test_q02_scale_on_full_real_series() -> None:
    rows = rows_of("persentase-realisasi-pendapatan-asli-daerah-pad-kota-bogor")
    assert sorted(r["tahun"] for r in rows) == list(range(2015, 2025))  # 10 tahun, bukan 5
    assert flags_of(rows, "persentase_pendapatan") == [
        ("suspect_scale", 2021),
        ("suspect_scale", 2022),
        ("suspect_scale", 2023),
    ]


def test_q03_outlier_on_full_real_series() -> None:
    rows = rows_of(
        "realisasi-penerimaan-pendapatan-asli-daerah-pad-bersumber-dari-retribusi-daerah-di-"
        "kota-bogor"
    )
    assert sorted(r["tahun"] for r in rows) == list(range(2015, 2026))  # 11 tahun, bukan 3
    assert flags_of(rows, "realisasi_pad") == [("suspect_outlier", 2022)]


def test_n06_indonesian_number_text() -> None:
    raw = rows_of("perkembangan-jumlah-kunjungan-wisatawan-di-kota-bogor")
    assert all(isinstance(r["perkembangan_kunjungan"], str) for r in raw)
    rows, issues = normalize_rows(raw, {"perkembangan_kunjungan": "number"})
    assert issues == []
    by_year = {r["tahun"]: r["perkembangan_kunjungan"] for r in rows}
    assert by_year[2024] == 8093203 and by_year[2019] == 9160885
    assert all(type(v) is int for v in by_year.values())
    assert all("id" not in r for r in rows)  # NB-04 (KB-17 terkonfirmasi)


def test_f07_periods_and_bounds_from_real_shapes() -> None:
    body = bigdata_of(KEMISKINAN)
    assert isinstance(body["metadata_filter"], list)  # bentuk asli list {key, type, value}
    available = periods_available(flatten_metadata_filter(body["metadata_filter"]))
    assert available == [2025]
    cov = compute([2025, 2026], available)
    assert (cov.periods_found, cov.missing_periods) == ([2025], [2026])
    record = record_of(KEMISKINAN)
    assert period_bounds(flatten_metadata(record["metadata"])) == (2025, 2025)
    fresh = compute_freshness(record["history_draft"], record["mdate"])
    assert fresh.data_updated_at.isoformat(timespec="seconds") == "2026-08-31T16:03:02+07:00"


def test_km07_real_data() -> None:
    record = record_of(KM07)
    rows = rows_of(KM07)
    by_year = {r["tahun"]: r["persentase"] for r in rows}
    assert sorted(by_year) == list(range(2019, 2025))
    assert by_year[2019] == 75.32 and by_year[2024] == 6.53  # golden F-08 (R-10)
    assert flags_of(rows, "persentase") == []  # perubahan definisi bukan wilayah QC
    available = periods_available(flatten_metadata_filter(bigdata_of(KM07)["metadata_filter"]))
    assert available == list(range(2019, 2025))
    assert officialness_from_record(record).status == "resmi"
    assert period_bounds(flatten_metadata(record["metadata"])) == (2019, 2024)


def test_periods_match_metadata_filter_for_all_recorded_bigdata() -> None:
    """metadata_filter (tahun teks) dan baris (tahun int) memberi periode yang sama."""
    for path, exchange in exchanges_by_path().items():
        if not path.startswith("/bigdata/"):
            continue
        body = exchange["response"]["json"]
        from_filter = periods_available(flatten_metadata_filter(body["metadata_filter"]))
        from_rows = sorted({r["tahun"] for r in body["data"]})
        assert from_filter == from_rows, path


# ---------------------------------------------------------------- katalog ramping


def test_catalog_slim_shape() -> None:
    slim = catalog_slim()
    records = slim["records"]
    assert slim["record_count"] == len(records) == slim["pagination_total_data"] == 751
    assert len({r["slug"] for r in records}) == 751
    assert all(set(r) == set(slim["fields"]) for r in records)
    assert all(set(h) == {"type", "cdate", "notes"} for r in records for h in r["history_draft"])
    assert all(set(m) == {"key", "value"} for r in records for m in r["metadata"])
    slugs = {r["slug"] for r in records}
    golden_slugs = {
        c["expected"]["dataset"] for c in golden().values() if c["expected"].get("dataset")
    }
    assert golden_slugs | {KM07, KM12} <= slugs


def test_catalog_slim_services_run_on_every_record() -> None:
    for record in catalog_slim()["records"]:
        fresh = compute_freshness(record["history_draft"], record["mdate"])
        assert fresh.data_updated_at is not None, record["slug"]
        assert fresh.data_updated_at.utcoffset().total_seconds() == 7 * 3600
        assert compute_officialness(record["validate"], record["klasifikasi"]).status == "resmi"
        start, end = period_bounds(flatten_metadata(record["metadata"]))
        assert start is None or end is None or start <= end, record["slug"]


def test_km12_candidate_in_catalog() -> None:
    record = next(r for r in catalog_slim()["records"] if r["slug"] == KM12)
    assert record["organisasi_name"] == "Inspektorat"


# ---------------------------------------------------------------- privasi (grep seluruh folder)

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
ACCOUNT_KEYS = re.compile(r'"(username|user_name|cuid|muid)"\s*:')
CONTACT_VALUE = re.compile(
    r'"(organisasi_email|organisasi_notelp|organisasi_alamat|email|notelp|alamat)"'
    r'\s*:\s*"(?!<redacted>)'
)


@pytest.mark.parametrize("path", sorted(RECORDED.glob("*.json")), ids=lambda p: p.name[:40])
def test_recorded_files_are_sanitized(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert not ACCOUNT_KEYS.search(text)
    assert not EMAIL.search(text)
    assert not CONTACT_VALUE.search(text)
    data = json.loads(text)
    assert all(value == "<redacted>" for value in _contact_metadata_values(data))
    for record in data.get("records", []) if isinstance(data, dict) else []:
        assert not {"organisasi_email", "organisasi_notelp", "organisasi_alamat"} & set(record)


def test_opd_address_redacted_in_catalog_and_reference() -> None:
    """KK-J: alamat OPD ada di respons portal, tetapi nilainya selalu diredaksi."""
    counted = 0
    for path in sorted(RECORDED.glob("[0-9][0-9][0-9]_*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))["response"]["json"]
        items = data.get("data") if isinstance(data, dict) else None
        for item in items if isinstance(items, list) else [items]:
            if not isinstance(item, dict):
                continue
            for key in ("organisasi_alamat", "alamat"):
                if item.get(key) is not None:
                    assert item[key] == "<redacted>", (path.name, key)
                    counted += 1
    assert counted > 0  # field memang ada di rekaman, bukan lolos karena kosong


def _contact_metadata_values(obj: Any) -> list[Any]:
    """Semua nilai metadata berkunci "Kontak Produsen" di mana pun dalam JSON."""
    found: list[Any] = []
    if isinstance(obj, dict):
        if obj.get("key") == "Kontak Produsen":
            found.append(obj.get("value"))
        for value in obj.values():
            found.extend(_contact_metadata_values(value))
    elif isinstance(obj, list):
        for value in obj:
            found.extend(_contact_metadata_values(value))
    return found


def test_contact_metadata_present_and_redacted_in_details() -> None:
    values = _contact_metadata_values(record_of(KEMISKINAN))
    assert values == ["<redacted>"]
