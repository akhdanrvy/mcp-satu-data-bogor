"""Kamus kolom Langkah 6 (UT-06; Dok 2 bagian 4 KK-01..08, KK-I, KB-15, KB-16, KB-30).

Tanpa jaringan: rekaman asli 2026-11-10 (detail + /bigdata 12 dataset, catalog_slim.json)
dan fixture sintetis kecil di berkas ini.
"""

from __future__ import annotations

import json
from datetime import date
from functools import cache
from pathlib import Path
from typing import Any

import pytest

from mcp_bogor.adapters.base import ColumnProbe, RawDataset
from mcp_bogor.adapters.opendata_jabar import parse_columns, parse_dataset, parse_row_page
from mcp_bogor.models import ColumnInfo, is_numeric_label_column
from mcp_bogor.services.dictionary import (
    NUMERIC_SHARE_THRESHOLD,
    DictionaryOverride,
    DictionaryResult,
    OverrideError,
    build_dictionary,
    column_role,
    normalize_column_name,
    normalize_type,
    numeric_shares,
    parse_override,
    parse_template,
    parse_template_line,
)
from mcp_bogor.services.normalize import parse_id_number
from mcp_bogor.storage.overrides import OverrideFileError, read_override

ROOT = Path(__file__).parents[2]
RECORDED = ROOT / "tests" / "fixtures" / "recorded" / "2026-11-10"
OVERRIDES = ROOT / "overrides"
PAJAJARAN_SPEED = (
    "kecepatan-lalu-lintas-segmen-1-dan-2-berdasarkan-hari-operasional-di-jalan-raya-pajajaran-"
    "kota-bogor"
)
KUNJUNGAN = "perkembangan-jumlah-kunjungan-wisatawan-di-kota-bogor"
PAD = "realisasi-pendapatan-asli-daerah-pad-kota-bogor"
REGION = {
    "kode_provinsi": "dimension",
    "nama_provinsi": "dimension",
    "bps_kode_kabupaten_kota": "dimension",
    "bps_nama_kabupaten_kota": "dimension",
}

# Peran kolom yang diharapkan untuk ke-12 /bigdata rekaman (10 golden set + KM-07 + KM-12).
EXPECTED_ROLES: dict[str, dict[str, str]] = {
    "tingkat-kemiskinan-di-kota-bogor-2": {"tingkat_kemiskinan": "measure"},
    PAJAJARAN_SPEED: {
        "no_segmen": "dimension",
        "segmen": "dimension",
        "hari_operasional": "dimension",
        "kecepatan": "measure",
    },
    PAD: {"total_pendapatan": "measure"},
    "realisasi-belanja-modal-daerah-di-kota-bogor": {"realisasi_belanja": "measure"},
    (
        "persentase-sisa-lebih-perhitungan-anggaran-silpa-terhadap-anggaran-pendapatan-dan-"
        "belanja-daerah-apbd-di-kota-bogor"
    ): {"persentase_silpa": "measure"},
    KUNJUNGAN: {"perkembangan_kunjungan": "measure"},
    "tren-kunjungan-pasien-puskesmas-di-kota-bogor": {"jumlah_pasien": "measure"},
    (
        "volume-lalu-lintas-segmen-1-dan-2-berdasarkan-hari-operasional-di-jalan-pajajaran--"
        "kota-bogor"
    ): {
        "segmen": "dimension",
        "hari_operasional": "dimension",
        "volume": "measure",
    },
    "persentase-realisasi-pendapatan-asli-daerah-pad-kota-bogor": {
        "persentase_pendapatan": "measure"
    },
    (
        "realisasi-penerimaan-pendapatan-asli-daerah-pad-bersumber-dari-retribusi-daerah-di-"
        "kota-bogor"
    ): {"realisasi_pad": "measure"},
    "persentase-kepala-keluarga-miskin-di-kota-bogor": {"persentase": "measure"},
    "persentase-penanganan-pengaduan-masyarakat-ke-inspektorat-daerah-kota-bogor": {
        "persentase_penanganan": "measure"
    },
}


# ---------------------------------------------------------------- bantuan rekaman


@cache
def _exchanges() -> dict[str, dict[str, Any]]:
    out = {}
    for path in sorted(RECORDED.glob("[0-9][0-9][0-9]_*.json")):
        exchange = json.loads(path.read_text(encoding="utf-8"))
        if exchange["request"]["path"] != "/datasets":
            out[exchange["request"]["path"]] = exchange
    return out


def _recorded(slug: str) -> tuple[RawDataset, dict[str, Any]]:
    dataset = parse_dataset(_exchanges()[f"/datasets/{slug}"]["response"]["json"]["data"])
    body = _exchanges()[f"/bigdata/{dataset.schema}/{dataset.table}"]["response"]["json"]
    return dataset, body


def _build_recorded(
    slug: str, *, description: Any = ..., override: DictionaryOverride | None = None
) -> DictionaryResult:
    dataset, body = _recorded(slug)
    return build_dictionary(
        description=dataset.record.get("deskripsi") if description is ... else description,
        probe=parse_columns(body),
        sample_rows=parse_row_page(body).rows,
        dataset_metadata=dataset.metadata,
        override=override,
    )


def _roles(result: DictionaryResult) -> dict[str, str]:
    return {c.name: c.role for c in result.columns}


def _col(result: DictionaryResult, name: str) -> ColumnInfo:
    return next(c for c in result.columns if c.name == name)


@cache
def _catalog() -> list[dict[str, Any]]:
    return json.loads((RECORDED / "catalog_slim.json").read_text(encoding="utf-8"))["records"]


def _probe(*columns: str, **filters: list[Any]) -> ColumnProbe:
    return ColumnProbe(columns=list(columns), metadata_filter=dict(filters))


def _desc(*lines: str) -> str:
    return "<p>Variabel:</p><ul>" + "".join(f"<li>{line}</li>" for line in lines) + "</ul>"


# ---------------------------------------------------------------- KK-01 / KK-02: urai template


def test_parse_standard_line_from_html_with_nbsp() -> None:
    parsed = parse_template(
        "<ul><li>tingkat_kemiskinan:&nbsp;menyatakan Persentase penduduk miskin. "
        "dengan tipe data numerik.</li><li>satuan: menyatakan satuan dengan tipe data teks."
        "</li></ul>"
    )
    first, second = parsed.entries
    assert (first.name, first.meaning, first.type) == (
        "tingkat_kemiskinan",
        "Persentase penduduk miskin",
        "number",
    )
    assert first.strict and second.type == "string"
    assert (parsed.variable_lines, parsed.unparsed_lines) == (2, 0)


@pytest.mark.parametrize(
    ("line", "name", "meaning", "data_type"),
    [
        ("- no_segmen : menyatakan nomor segmen jalan", "no_segmen", "nomor segmen jalan", None),
        (
            "- kapasitas jalan : Menyatakan kemampuan ruas jalan",
            "kapasitas_jalan",
            "kemampuan ruas jalan",
            None,
        ),
        (
            "- segmen: nama segmen jalan dengan tipe data text.",
            "segmen",
            "nama segmen jalan",
            "string",
        ),
        ("- hari : menyatakankategori hari", "hari", "kategori hari", None),
        (
            "- satuan; menyatakan satuan unit dengan tipe data teks.",
            "satuan",
            "satuan unit",
            "string",
        ),
        (
            "- bps_nama_ kabupaten_kota: menyatakan x dengan tipe data teks",
            "bps_nama_kabupaten_kota",
            "x",
            "string",
        ),
        (
            "- nilai_aspek+penerapan: menyatakan x dengan tipe data numerik",
            "nilai_aspek_penerapan",
            "x",
            "number",
        ),
        ("- jenis: menyatakan x dengan tipe data kategorik.", "jenis", "x", "category"),
        ("- jumlah: menyatakan x dengan tipe data angka.", "jumlah", "x", "unknown"),
        ("- satuan: dengan tipe data teks.", "satuan", None, "string"),
    ],
)
def test_parse_tolerant_variants(
    line: str, name: str, meaning: str | None, data_type: str | None
) -> None:
    entry = parse_template_line(line)
    assert entry is not None
    assert (entry.name, entry.meaning, entry.type) == (name, meaning, data_type)


def test_parse_skips_non_variable_lines() -> None:
    parsed = parse_template("<p>Dataset ini.</p><ul><li></li><li>tanpa titik dua</li></ul>")
    assert parsed.entries == [] and (parsed.variable_lines, parsed.unparsed_lines) == (1, 1)
    assert parse_template(None).variable_lines == 0
    assert parse_template_line("bukan butir") is None
    assert parse_template_line("- : arti tanpa nama") is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("numerik", "number"),
        (" Teks ", "string"),
        ("text.", "string"),
        ("kategorik", "category"),
        ('teks (misalnya, "unit" atau "buah")', "string"),
        ("tanggal", "unknown"),
        ("", None),
        (None, None),
    ],
)
def test_normalize_type(raw: str | None, expected: str | None) -> None:
    assert normalize_type(raw) == expected


def test_normalize_column_name_only_unifies_separators() -> None:
    assert normalize_column_name(" Kode_provinsi ") == "kode_provinsi"
    assert normalize_column_name("nilai_evaluasi Reformasi Birokrasi _Perangkat") == (
        "nilai_evaluasi_reformasi_birokrasi_perangkat"
    )
    assert normalize_column_name("jumlah_pendudk") == "jumlah_pendudk"  # salah ketik tetap


def test_template_statistics_over_catalog_slim() -> None:
    """Statistik urai atas 751 deskripsi (rekaman 10 Nov) vs Hasil Uji 5 Nov 2026.

    5 Nov (757 dataset): 5.755/5.854 baris (98,3%) cocok template; 699/757 (92,3%) penuh.
    "Cocok" di sini = baris terurai lengkap dengan tipe yang dikenali (number/string/category).
    """
    records = _catalog()
    lines = parsed = typed = strict = full_typed = full_parsed = 0
    for record in records:
        result = parse_template(record.get("deskripsi"))
        n_typed = sum(e.type in {"number", "string", "category"} for e in result.entries)
        lines += result.variable_lines
        parsed += len(result.entries)
        typed += n_typed
        strict += sum(e.strict for e in result.entries)
        full_typed += result.variable_lines > 0 and n_typed == result.variable_lines
        full_parsed += result.variable_lines > 0 and result.unparsed_lines == 0
    assert len(records) == 751 and lines == 5811
    assert parsed == lines and full_parsed == 751  # semua baris "nama: arti" terbaca
    assert typed == 5725 and full_typed == 703  # 98,52% baris; 93,61% dataset
    assert strict == 5560  # 95,68% persis template baku
    assert typed / lines > 0.983 and full_typed / len(records) > 0.923


# ---------------------------------------------------------------- KK-04 + KK-I: peran


def test_role_rules_in_order() -> None:
    assert column_role("id", "number") == "id"  # (a)
    assert column_role("kode_kecamatan", "number", 1.0) == "dimension"  # (b)
    assert column_role("kemendagri_kode_desa_kelurahan", None, 1.0) == "dimension"  # (b)
    for period in ("bulan", "triwulan", "semester"):
        assert column_role(period, "number", 1.0) == "dimension", period  # (b)
    assert column_role("bulanan", "number") == "measure"  # nama persis, bukan substring
    assert column_role("bps_nama_kecamatan", "number") == "dimension"  # (c)
    assert column_role("kemendagri_nama_desa_kelurahan", None, 1.0) == "dimension"  # (c)
    assert column_role("jumlah", "number", 0.0) == "measure"  # (d)
    assert column_role("jumlah", "string", NUMERIC_SHARE_THRESHOLD) == "measure"  # (e)
    assert column_role("jumlah", "string", 0.89) == "dimension"  # (f)
    assert column_role("keterangan", None, None) == "dimension"  # (f)
    assert column_role("id", None, id_columns=()) == "dimension"


def test_numeric_shares_use_nb07() -> None:
    rows = [{"a": "9.160.885", "b": "5.89", "c": 12, "d": None}] * 9 + [
        {"a": "-", "b": "x", "c": True, "d": ""}
    ]
    shares = numeric_shares(rows, ["a", "b", "c", "d"])
    assert shares["a"] == 1.0  # "-" kosong (NB-02), tidak dihitung
    assert shares["b"] == 0.0  # "5.89" ambigu (KB-16), tidak ditebak
    assert shares["c"] == 0.9  # bool bukan angka
    assert shares["d"] is None


@pytest.mark.parametrize("slug", sorted(EXPECTED_ROLES))
def test_recorded_roles(slug: str) -> None:
    result = _build_recorded(slug)
    expected = {"id": "id", **REGION, "satuan": "dimension", "tahun": "dimension"}
    expected.update(EXPECTED_ROLES[slug])
    assert _roles(result) == expected


def test_kk_i_cases_on_recorded_data() -> None:
    speed = _build_recorded(PAJAJARAN_SPEED)
    assert _col(speed, "no_segmen").role == "dimension"
    assert _col(speed, "bps_kode_kabupaten_kota").role == "dimension"
    assert _col(speed, "id").role == "id" and _col(speed, "id").top_values is None


def test_rule_e_text_numbers_become_measure_without_template() -> None:
    """KM-08: "9.160.885" bertipe teks. Tanpa deskripsi pun tetap measure (aturan e)."""
    result = _build_recorded(KUNJUNGAN, description=None)
    column = _col(result, "perkembangan_kunjungan")
    assert column.role == "measure" and column.top_values is None and column.type is None
    report = next(r for r in result.reports if r.name == "perkembangan_kunjungan")
    assert report.status == "undocumented" and report.numeric_share == 1.0
    assert not result.dictionary_validated  # measure undocumented


@pytest.mark.parametrize("slug", sorted(EXPECTED_ROLES))
def test_kt03_second_layer_on_recorded_dictionaries(slug: str) -> None:
    """KT-03: top_values hanya untuk dimension; tidak ada angka di dimension selain label."""
    for column in _build_recorded(slug).columns:
        if column.role != "dimension":
            assert column.top_values is None, column.name
            continue
        if is_numeric_label_column(column.name):
            continue
        for value in column.top_values or []:
            assert parse_id_number(value) is None, (column.name, value)


# ---------------------------------------------------------------- KK-03: validasi


def test_recorded_dictionary_details() -> None:
    result = _build_recorded("tingkat-kemiskinan-di-kota-bogor-2")
    assert result.dictionary_validated and result.reasons == []
    measure = _col(result, "tingkat_kemiskinan")
    assert (measure.type, measure.meaning_source, measure.unit) == ("number", "template", "PERSEN")
    assert measure.meaning and measure.meaning.startswith("Persentase penduduk")
    assert _col(result, "satuan").unit is None and _col(result, "tahun").top_values == ["2025"]
    assert result.status_of("tahun") == "documented" and result.status_of("x") is None
    assert result.status_of("id") == "undocumented" and result.orphans == []


def test_recorded_main_value_undocumented_pad() -> None:
    """Rekaman asli: deskripsi menulis cakupan_pelayanan, data punya total_pendapatan."""
    result = _build_recorded(PAD)
    assert not result.dictionary_validated
    assert [(r.code, r.column) for r in result.reasons] == [
        ("measure_undocumented", "total_pendapatan")
    ]
    assert result.orphans == ["cakupan_pelayanan"]
    column = _col(result, "total_pendapatan")
    assert (column.meaning, column.meaning_source, column.unit) == (None, "none", "RUPIAH")


@pytest.mark.parametrize("measure", ["nilai_aset", "persentase_sarpras_penyedia_pangan"])
def test_synthetic_main_value_undocumented(measure: str) -> None:
    """Kasus Hasil Uji 5 Nov 2026 (tidak ada di rekaman, jadi sintetis)."""
    result = build_dictionary(
        description=_desc(
            "tahun: menyatakan tahun dengan tipe data numerik.",
            "satuan: menyatakan satuan dengan tipe data teks.",
        ),
        probe=_probe("id", measure, "satuan", "tahun"),
        sample_rows=[{"id": 1, measure: "1.234,5", "satuan": "RUPIAH", "tahun": 2024}],
    )
    assert _col(result, measure).role == "measure"
    assert not result.dictionary_validated
    assert [(r.code, r.column) for r in result.reasons] == [("measure_undocumented", measure)]


def test_no_measure_is_not_validated() -> None:
    result = build_dictionary(
        description=_desc("nama_sekolah: menyatakan nama sekolah dengan tipe data teks."),
        probe=_probe("nama_sekolah"),
        sample_rows=[{"nama_sekolah": "SDN 1"}],
    )
    assert [(r.code, r.column) for r in result.reasons] == [("no_measure", None)]
    assert not result.dictionary_validated and result.unit_source == "none"


def test_region_alias_and_typo_match() -> None:
    result = build_dictionary(
        description=_desc(
            "kode_kota: menyatakan kode kota dengan tipe data numerik.",
            "bps_nama_kabupatean_kota: menyatakan nama kota dengan tipe data teks.",
            "jumlah: menyatakan jumlah dengan tipe data numerik.",
        ),
        probe=_probe("bps_kode_kabupaten_kota", "bps_nama_kabupaten_kota", "jumlah"),
        sample_rows=[{"bps_kode_kabupaten_kota": 3271, "bps_nama_kabupaten_kota": "KOTA BOGOR"}],
    )
    by_name = {r.name: r for r in result.reports}
    assert by_name["bps_kode_kabupaten_kota"].matched_by == "alias"
    assert by_name["bps_kode_kabupaten_kota"].template_name == "kode_kota"
    assert by_name["bps_nama_kabupaten_kota"].status == "documented"
    assert by_name["jumlah"].matched_by == "exact"
    assert result.orphans == [] and result.dictionary_validated


def test_no_fuzzy_matching_only_suggestions() -> None:
    result = build_dictionary(
        description=_desc("jumlah_pendudk: menyatakan jumlah penduduk dengan tipe data numerik."),
        probe=_probe("jumlah_penduduk"),
        sample_rows=[{"jumlah_penduduk": 100}],
    )
    assert result.status_of("jumlah_penduduk") == "undocumented"
    assert _col(result, "jumlah_penduduk").meaning is None  # tidak diambil dari orphan
    assert result.orphans == ["jumlah_pendudk"]
    assert [(s.template_name, s.column) for s in result.suggestions] == [
        ("jumlah_pendudk", "jumlah_penduduk")
    ]
    assert not result.dictionary_validated


def test_value_labels_are_not_orphans() -> None:
    result = _build_recorded(PAJAJARAN_SPEED)
    assert result.value_labels == ["HARI KERJA", "HARI LIBUR"] and result.orphans == []


def test_duplicate_template_names_keep_first() -> None:
    result = build_dictionary(
        description=_desc(
            "nilai: menyatakan arti pertama dengan tipe data numerik.",
            "nilai: menyatakan arti kedua dengan tipe data numerik.",
        ),
        probe=_probe("nilai"),
        sample_rows=[],
    )
    assert result.duplicates == ["nilai"] and _col(result, "nilai").meaning == "arti pertama"


def test_columns_fall_back_to_row_keys() -> None:
    result = build_dictionary(
        description=None, probe=_probe(), sample_rows=[{"id": 1, "jumlah": 3, "tahun": 2024}]
    )
    assert [c.name for c in result.columns] == ["id", "jumlah", "tahun"]


def test_top_values_withheld_when_formatted_numbers_in_dimension() -> None:
    values = ["9.160.885"] + [f"kategori {i}" for i in range(9)]
    result = build_dictionary(
        description=None,
        probe=_probe("label", "jumlah", label=values),
        sample_rows=[{"label": v, "jumlah": 1} for v in values],
    )
    assert _col(result, "label").role == "dimension"  # 10% angka < ambang
    assert _col(result, "label").top_values is None and result.top_values_withheld == ["label"]


def test_top_values_capped_and_empty_skipped() -> None:
    values = ["-", ""] + [f"k{i}" for i in range(15)]
    result = build_dictionary(description=None, probe=_probe("k", k=values), sample_rows=[])
    assert _col(result, "k").top_values == [f"k{i}" for i in range(10)]


# ---------------------------------------------------------------- KK-06: satuan


def _unit_case(satuan_values: list[Any], dataset_unit: Any) -> DictionaryResult:
    return build_dictionary(
        description=_desc("jumlah: menyatakan jumlah dengan tipe data numerik."),
        probe=_probe("jumlah", "satuan", satuan=satuan_values),
        sample_rows=[],
        dataset_metadata={"Satuan Dataset": dataset_unit},
    )


def test_unit_from_single_satuan_value_and_difference_flag() -> None:
    same = _unit_case(["PERSEN"], "Persen")
    assert _col(same, "jumlah").unit == "PERSEN" and same.unit_source == "column"
    assert not same.unit_differs_from_dataset
    differs = _unit_case(["ORANG"], "Persen")
    assert _col(differs, "jumlah").unit == "ORANG" and differs.unit_differs_from_dataset


def test_unit_conflict_gives_null() -> None:
    result = _unit_case(["PERSEN", "persen", "ORANG"], "Persen")
    assert _col(result, "jumlah").unit is None
    assert (result.unit_source, result.unit_values) == ("conflict", ["PERSEN", "ORANG"])


def test_unit_from_dataset_when_no_satuan_values() -> None:
    result = _unit_case([], " RUPIAH ")
    assert _col(result, "jumlah").unit == "RUPIAH" and result.unit_source == "dataset"
    assert _unit_case([], None).unit_source == "none"


# ---------------------------------------------------------------- KK-07: override


def test_example_override_file_is_valid_and_used() -> None:
    data = read_override(OVERRIDES, PAJAJARAN_SPEED)
    override = parse_override(data, PAJAJARAN_SPEED)
    result = _build_recorded(PAJAJARAN_SPEED, override=override)
    column = _col(result, "no_segmen")
    assert (column.meaning, column.meaning_source, column.role) == (
        "nomor segmen ruas jalan",
        "override",
        "dimension",
    )
    report = next(r for r in result.reports if r.name == "no_segmen")
    assert report.overridden and not report.curated  # dimension; tanpa unit tidak perlu
    entry = override.columns["no_segmen"]
    assert (entry.reviewed_by, entry.reviewed_at) == ("Akhdan", date(2026, 11, 11))
    assert result.dictionary_validated


def test_override_unknown_column_is_clear_error() -> None:
    override = parse_override(
        {"slug": PAJAJARAN_SPEED, "columns": {"no_ruas": {"meaning": "x"}}}, PAJAJARAN_SPEED
    )
    with pytest.raises(OverrideError, match="no_ruas tidak ada di data"):
        _build_recorded(PAJAJARAN_SPEED, override=override)


REVIEW = {"reviewed_by": "Akhdan", "reviewed_at": "2026-11-11"}


def _pad_override(**fields: Any) -> DictionaryOverride:
    return parse_override(
        {
            "slug": PAD,
            "columns": {"total_pendapatan": fields, "satuan": {"role": "dimension"}},
        },
        PAD,
    )


def test_complete_override_validates_undocumented_measure() -> None:
    """KB-30 (8): meaning + unit + reviewed_by + reviewed_at → measure dianggap valid."""
    result = _build_recorded(PAD, override=_pad_override(meaning="x", unit="rupiah", **REVIEW))
    column = _col(result, "total_pendapatan")
    assert (column.meaning_source, column.unit) == ("override", "rupiah")
    report = next(r for r in result.reports if r.name == "total_pendapatan")
    assert report.status == "undocumented" and report.curated  # status tetap jujur
    assert result.dictionary_validated and result.reasons == []


@pytest.mark.parametrize(
    "fields",
    [
        {"meaning": "x", "unit": "rupiah"},  # tanpa review
        {"meaning": "x", **REVIEW},  # tanpa unit
        {"unit": "rupiah", **REVIEW},  # tanpa meaning
    ],
)
def test_incomplete_override_does_not_validate(fields: dict[str, Any]) -> None:
    result = _build_recorded(PAD, override=_pad_override(**fields))
    assert not result.dictionary_validated
    assert [(r.code, r.column) for r in result.reasons] == [
        ("measure_undocumented", "total_pendapatan")
    ]
    assert not next(r for r in result.reports if r.name == "total_pendapatan").curated


def test_override_review_fields_parsed() -> None:
    override = _pad_override(meaning="x", unit="rupiah", **REVIEW)
    entry = override.columns["total_pendapatan"]
    assert entry.reviewed_at == date(2026, 11, 11) and entry.validates_measure
    as_measure = parse_override({"slug": PAD, "columns": {"tahun": {"role": "measure"}}}, PAD)
    assert _col(_build_recorded(PAD, override=as_measure), "tahun").role == "measure"


@pytest.mark.parametrize(
    "data",
    [
        "bukan objek",
        {"slug": PAD},
        {"slug": PAD, "columns": {}},
        {"slug": PAD, "columns": {"x": {}}},
        {"slug": PAD, "columns": {" ": {"meaning": "x"}}},
        {"slug": PAD, "columns": {"x": {"role": "kunci"}}},
        {"slug": PAD, "columns": {"x": {"meaning": "x", "tebakan": True}}},
        {"slug": PAD, "columns": {"x": {"meaning": ""}}},
        {"slug": "slug-lain", "columns": {"x": {"meaning": "x"}}},
        {"slug": PAD, "columns": {"x": {"meaning": "x", "reviewed_by": "Akhdan"}}},
        {"slug": PAD, "columns": {"x": {"meaning": "x", "reviewed_at": "2026-11-11"}}},
        {
            "slug": PAD,
            "columns": {"x": {"meaning": "x", "reviewed_by": "A", "reviewed_at": "kemarin"}},
        },
        {
            "slug": PAD,
            "columns": {"x": {"meaning": "x", "reviewed_by": "", "reviewed_at": "2026-11-11"}},
        },
    ],
)
def test_override_schema_rejected(data: Any) -> None:
    with pytest.raises(OverrideError):
        parse_override(data, PAD)


def test_read_override_file_errors(tmp_path: Path) -> None:
    assert read_override(tmp_path, "tidak-ada") is None
    (tmp_path / "rusak.yaml").write_text("slug: [tidak ditutup", encoding="utf-8")
    with pytest.raises(OverrideFileError):
        read_override(tmp_path, "rusak")
    with pytest.raises(OverrideFileError):
        read_override(tmp_path, "../keluar")


# ---------------------------------------------------------------- KK-08: bentuk cache


@pytest.mark.parametrize("slug", [PAD, PAJAJARAN_SPEED, KUNJUNGAN])
def test_dictionary_result_round_trip(slug: str, tmp_path: Path) -> None:
    from mcp_bogor.storage.cache import DictionaryCache

    result = _build_recorded(slug)
    store = DictionaryCache(tmp_path)
    store.put(slug, result.to_dict(), data_updated_at=None)
    cached = store.get(slug, data_updated_at=None)
    assert cached is not None
    assert DictionaryResult.from_dict(cached) == result


def test_dictionary_result_from_dict_rejects_unknown_shape() -> None:
    data = _build_recorded(PAD).to_dict()
    with pytest.raises(ValueError):
        DictionaryResult.from_dict({**data, "format": 99})
    broken = dict(data)
    del broken["reports"]
    with pytest.raises(ValueError):
        DictionaryResult.from_dict(broken)


def test_override_digest_changes_with_content() -> None:
    first = parse_override({"slug": PAD, "columns": {"tahun": {"role": "dimension"}}}, PAD)
    same = parse_override({"slug": PAD, "columns": {"tahun": {"role": "dimension"}}}, PAD)
    other = parse_override({"slug": PAD, "columns": {"tahun": {"meaning": "tahun"}}}, PAD)
    assert first.digest() == same.digest() != other.digest()
