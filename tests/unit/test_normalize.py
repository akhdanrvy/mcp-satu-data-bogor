"""UT-01 (NB-01), UT-02 (NB-07/NB-03), UT-03 (NB-08), NB-02/04/05/06."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from mcp_bogor.config import WIB
from mcp_bogor.models import DataRef
from mcp_bogor.services.normalize import (
    ParseIssue,
    clean_empty,
    html_to_text,
    is_code_column,
    normalize_label,
    normalize_row,
    normalize_rows,
    parse_id_number,
    parse_portal_datetime,
)

ODD = timezone(timedelta(hours=7, minutes=7))

# ---------------------------------------------------------------- UT-01 / NB-01


@pytest.mark.parametrize(
    "raw",
    ["2026-08-31T16:03:02+07:07", "2026-08-31 16:03:02+07:07", "2026-08-31T16:03:02.000+07:07"],
)
def test_plus_0707_keeps_wall_clock(raw: str) -> None:
    parsed = parse_portal_datetime(raw)
    assert parsed is not None
    assert parsed.utcoffset() == timedelta(hours=7)
    assert (parsed.hour, parsed.minute, parsed.second) == (16, 3, 2)
    assert parsed.isoformat(timespec="seconds") == "2026-08-31T16:03:02+07:00"


def test_plus_0707_datetime_object() -> None:
    parsed = parse_portal_datetime(datetime(2026, 8, 31, 16, 3, 2, tzinfo=ODD))
    assert parsed == datetime(2026, 8, 31, 16, 3, 2, tzinfo=WIB)


def test_plus_0707_through_model_serialization() -> None:
    """Rantai lengkap: portal → normalize → model → JSON, tanpa pergeseran 7 menit."""
    ref = DataRef(
        url="u", expires_at=parse_portal_datetime("2026-11-06T10:30:00+07:07"), row_count=0
    )
    assert ref.model_dump(mode="json")["expires_at"] == "2026-11-06T10:30:00+07:00"


def test_naive_portal_time_is_wib() -> None:
    parsed = parse_portal_datetime("2026-08-31T16:03:02")
    assert parsed == datetime(2026, 8, 31, 16, 3, 2, tzinfo=WIB)
    assert parse_portal_datetime(datetime(2026, 8, 31, 16, 3, 2)) == parsed


def test_real_offsets_are_converted() -> None:
    assert parse_portal_datetime("2026-08-31T09:03:02Z") == datetime(
        2026, 8, 31, 16, 3, 2, tzinfo=WIB
    )
    utc = parse_portal_datetime(datetime(2026, 8, 31, 9, 3, 2, tzinfo=UTC))
    assert utc is not None and utc.utcoffset() == timedelta(hours=7) and utc.hour == 16
    assert parse_portal_datetime("2026-08-31T16:03:02+07:00").tzinfo is WIB


@pytest.mark.parametrize("raw", [None, "", " ", "-", "None", "null"])
def test_empty_portal_time(raw: str | None) -> None:
    assert parse_portal_datetime(raw) is None


def test_bad_portal_time() -> None:
    with pytest.raises(ValueError, match="ISO 8601"):
        parse_portal_datetime("31/08/2026")
    with pytest.raises(TypeError):
        parse_portal_datetime(20260831)  # type: ignore[arg-type]


# ---------------------------------------------------------------- NB-02 / NB-06


@pytest.mark.parametrize("raw", ["", "  ", "-", " - ", "None", "none", "null", "NULL", None])
def test_clean_empty(raw: object) -> None:
    assert clean_empty(raw) is None


@pytest.mark.parametrize("raw", ["0", 0, "Bogor", "--", "n/a", 0.0, False])
def test_clean_empty_keeps_values(raw: object) -> None:
    assert clean_empty(raw) == raw


# ---------------------------------------------------------------- UT-02 / NB-07


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("9.160.885", 9160885),
        ("8.093.203", 8093203),
        ("1.234,5", 1234.5),
        ("0,45", 0.45),
        ("1234,5", 1234.5),
        ("-1.000", -1000),
        ("2024", 2024),
        (" 9.160.885 ", 9160885),
        ("1.000.000,25", 1000000.25),
    ],
)
def test_parse_id_number(raw: str, expected: float) -> None:
    result = parse_id_number(raw)
    assert result == expected
    assert type(result) is type(expected)


@pytest.mark.parametrize("raw", [5.89, 46, 1431967467277.22, 0, -3])
def test_numbers_not_reparsed(raw: float) -> None:
    assert parse_id_number(raw) is raw


@pytest.mark.parametrize(
    "raw", ["5.89", "1.2.3", "12.34", "1,234.5", "1.23,4.5", "abc", "Rp 1.000", "1.0000", True]
)
def test_ambiguous_or_invalid_numbers(raw: object) -> None:
    assert parse_id_number(raw) is None


@pytest.mark.parametrize("raw", ["", "-", "None", None])
def test_empty_numbers(raw: object) -> None:
    assert parse_id_number(raw) is None


# ---------------------------------------------------------------- NB-03 / NB-04 / NB-05


TYPES = {
    "tahun": "number",
    "perkembangan_kunjungan": "number",
    "satuan": "string",
    "bps_kode_kabupaten_kota": "number",
    "kode_kecamatan": "string",
}


def test_normalize_row_km08() -> None:
    raw = {
        "id": 17,
        "bps_kode_kabupaten_kota": "3271",
        "kode_kecamatan": "32.71.010",
        "tahun": "2024",
        "perkembangan_kunjungan": "8.093.203",
        "satuan": "kunjungan",
    }
    row, issues = normalize_row(raw, TYPES, row_index=3)
    assert row == {
        "bps_kode_kabupaten_kota": "3271",  # NB-05: tetap teks apa adanya
        "kode_kecamatan": "32.71.010",
        "tahun": 2024,
        "perkembangan_kunjungan": 8093203,
        "satuan": "kunjungan",
    }
    assert list(row) == [k for k in raw if k != "id"]  # NB-04, urutan dipertahankan
    assert issues == []
    assert raw["perkembangan_kunjungan"] == "8.093.203"  # input tidak diubah


def test_normalize_row_failures_are_none_with_issue() -> None:
    raw = {"tahun": 2025, "perkembangan_kunjungan": "5.89", "satuan": "-"}
    row, issues = normalize_row(raw, TYPES, row_index=0)
    assert row == {"tahun": 2025, "perkembangan_kunjungan": None, "satuan": None}
    assert issues == [
        ParseIssue(
            column="perkembangan_kunjungan", row_index=0, reason="format_angka_tidak_dikenal"
        )
    ]
    assert "5.89" not in repr(issues)  # temuan tidak membawa nilai mentah


def test_normalize_row_number_column_with_unknown_type() -> None:
    row, issues = normalize_row({"tahun": True}, {"tahun": "number"})
    assert row == {"tahun": None}
    assert issues[0].reason == "tipe_bukan_angka"
    row, issues = normalize_row({"tahun": ["2024"]}, {"tahun": "number"})
    assert row == {"tahun": None} and issues[0].reason == "tipe_bukan_angka"


def test_normalize_row_empty_number_without_issue() -> None:
    row, issues = normalize_row({"perkembangan_kunjungan": "-"}, TYPES)
    assert row == {"perkembangan_kunjungan": None} and issues == []


def test_normalize_row_custom_drop_columns() -> None:
    row, _ = normalize_row({"_id": 1, "id": 2, "x": "a"}, {}, drop_columns={"_id"})
    assert row == {"id": 2, "x": "a"}


def test_normalize_rows_indexes_issues() -> None:
    rows, issues = normalize_rows(
        [{"perkembangan_kunjungan": "1.000"}, {"perkembangan_kunjungan": "1.0"}], TYPES
    )
    assert rows == [{"perkembangan_kunjungan": 1000}, {"perkembangan_kunjungan": None}]
    assert [(i.row_index, i.column) for i in issues] == [(1, "perkembangan_kunjungan")]


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("kode_kecamatan", True),
        ("bps_kode_kabupaten_kota", True),
        ("kode", True),
        ("kodepos", False),
        ("tahun", False),
    ],
)
def test_is_code_column(name: str, expected: bool) -> None:
    assert is_code_column(name) is expected


# ---------------------------------------------------------------- UT-03 / NB-08


def test_segment_labels_grouped() -> None:
    variants = [
        "SIMPANG EKALOKASARI- SIMPANG BARANANGSIANG",
        "SIMPANG EKALOKASARI \u2013 SIMPANG BARANANGSIANG",
        "SIMPANG EKALOKASARI\u2014SIMPANG BARANANGSIANG",
        "  SIMPANG  EKALOKASARI -\nSIMPANG BARANANGSIANG ",
        "SIMPANG EKALOKASARI - SIMPANG BARANANGSIANG",
    ]
    results = [normalize_label(v) for v in variants]
    assert {normal for normal, _ in results} == {"SIMPANG EKALOKASARI - SIMPANG BARANANGSIANG"}
    assert [original for _, original in results] == variants  # nilai asli tersimpan


def test_label_without_dash() -> None:
    assert normalize_label(" Bogor\tBarat ") == ("Bogor Barat", " Bogor\tBarat ")


# ---------------------------------------------------------------- deskripsi HTML


def test_html_to_text_keeps_list_items_and_entities() -> None:
    html = (
        "<p>Dataset ini&nbsp;berisi data <span>kemiskinan</span> &amp; lainnya.</p>"
        "<p>Penjelasan:</p><ul><li>tahun: menyatakan tahun</li>"
        "<li>nilai:  menyatakan   nilai</li></ul>"
    )
    assert html_to_text(html) == (
        "Dataset ini berisi data kemiskinan & lainnya.\n"
        "Penjelasan:\n"
        "- tahun: menyatakan tahun\n"
        "- nilai: menyatakan nilai"
    )


@pytest.mark.parametrize("raw", [None, "", "-", "None", "<p></p>", "<br/><p> </p>"])
def test_html_to_text_empty(raw: str | None) -> None:
    assert html_to_text(raw) is None
