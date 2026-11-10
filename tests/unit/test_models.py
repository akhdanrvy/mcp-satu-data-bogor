"""Tes unit model: validasi parameter, waktu WIB, bagian amplop, result, round-trip JSON."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import BaseModel, ValidationError

from mcp_bogor.config import WIB, Settings
from mcp_bogor.models import (
    ColumnInfo,
    DataParams,
    DataRef,
    DataResult,
    InfoParams,
    Officialness,
    Provenance,
    QualityFlag,
    SearchItem,
    SearchParams,
    SearchResult,
    ToolError,
    contains_number,
    format_wib,
    mask_numbers,
)
from mcp_bogor.models.params import DEFAULT_SEARCH_MAX_PAGE_SIZE, SEARCH_MAX_PAGE_SIZE_KEY
from tests import model_samples as s

# ---------------------------------------------------------------- SearchParams


@pytest.mark.parametrize("query", ["ab", "x" * 100, "  pad  "])
def test_search_query_within_bounds(query: str) -> None:
    assert SearchParams(query=query).query == query.strip()


@pytest.mark.parametrize("query", ["a", "x" * 101, "   ", " a "])
def test_search_query_out_of_bounds(query: str) -> None:
    with pytest.raises(ValidationError):
        SearchParams(query=query)


def test_search_defaults() -> None:
    p = SearchParams(query="kemiskinan")
    assert (p.page, p.page_size, p.opd, p.topik, p.tahun, p.request_id) == (
        1,
        10,
        None,
        None,
        None,
        None,
    )


@pytest.mark.parametrize("tahun", [2000, 2100, 2024])
def test_search_tahun_within_bounds(tahun: int) -> None:
    assert SearchParams(query="pad", tahun=tahun).tahun == tahun


@pytest.mark.parametrize("tahun", [1999, 2101])
def test_search_tahun_out_of_bounds(tahun: int) -> None:
    with pytest.raises(ValidationError):
        SearchParams(query="pad", tahun=tahun)


def test_search_page_bounds() -> None:
    assert SearchParams(query="pad", page=1).page == 1
    with pytest.raises(ValidationError):
        SearchParams(query="pad", page=0)


@pytest.mark.parametrize("size", [1, DEFAULT_SEARCH_MAX_PAGE_SIZE])
def test_search_page_size_within_default_limit(size: int) -> None:
    assert SearchParams(query="pad", page_size=size).page_size == size


@pytest.mark.parametrize("size", [0, DEFAULT_SEARCH_MAX_PAGE_SIZE + 1])
def test_search_page_size_out_of_default_limit(size: int) -> None:
    with pytest.raises(ValidationError):
        SearchParams(query="pad", page_size=size)


def test_search_page_size_follows_configured_limit() -> None:
    ctx = {SEARCH_MAX_PAGE_SIZE_KEY: 5}
    assert SearchParams.model_validate({"query": "pad", "page_size": 5}, context=ctx).page_size == 5
    with pytest.raises(ValidationError, match="page_size maksimal"):
        SearchParams.model_validate({"query": "pad", "page_size": 6}, context=ctx)
    # Nilai bawaan 10 diturunkan ke batas, bukan ditolak.
    assert SearchParams.model_validate({"query": "pad"}, context=ctx).page_size == 5
    ctx_big = {SEARCH_MAX_PAGE_SIZE_KEY: 50}
    assert SearchParams.model_validate({"query": "pad", "page_size": 50}, context=ctx_big)


def test_default_limit_matches_settings(settings_env) -> None:
    assert Settings(_env_file=None).search_max_page_size == DEFAULT_SEARCH_MAX_PAGE_SIZE


def test_search_blank_opd_topik_become_null() -> None:
    p = SearchParams(query="pad", opd="  ", topik="")
    assert p.opd is None and p.topik is None
    assert SearchParams(query="pad", opd=" Bapenda ").opd == "Bapenda"


@pytest.mark.parametrize("model", [SearchParams, InfoParams, DataParams])
def test_request_id_bounds(model: type[BaseModel]) -> None:
    base = {"query": "pad"} if model is SearchParams else {"slug": "contoh"}
    assert model(**base, request_id="r" * 64).request_id == "r" * 64
    for bad in ("r" * 65, ""):
        with pytest.raises(ValidationError):
            model(**base, request_id=bad)


@pytest.mark.parametrize("model", [SearchParams, InfoParams, DataParams])
def test_params_forbid_extra(model: type[BaseModel]) -> None:
    base = {"query": "pad"} if model is SearchParams else {"slug": "contoh"}
    with pytest.raises(ValidationError, match="extra"):
        model(**base, where={"tahun": [2025]})


# ---------------------------------------------------------------- InfoParams


def test_info_slug_required() -> None:
    assert InfoParams(slug=" contoh ").slug == "contoh"
    for bad in ({}, {"slug": ""}, {"slug": "  "}):
        with pytest.raises(ValidationError):
            InfoParams(**bad)


# ---------------------------------------------------------------- DataParams


def test_data_defaults() -> None:
    p = DataParams(slug="contoh")
    assert p.periods is None and p.filters is None
    assert p.sort == "tahun:asc" and p.force_refresh is False


def test_data_periods_bounds() -> None:
    twenty = list(range(2001, 2021))
    assert DataParams(slug="c", periods=twenty).periods == twenty
    assert DataParams(slug="c", periods=[2100, 2000]).periods == [2000, 2100]
    assert DataParams(slug="c", periods=[2025, 2025, 2024]).periods == [2024, 2025]
    for bad in (list(range(2001, 2022)), [], [1999], [2101]):
        with pytest.raises(ValidationError):
            DataParams(slug="c", periods=bad)


def test_data_filters_bounds() -> None:
    fifty = [f"kel-{i}" for i in range(50)]
    p = DataParams(slug="c", filters={"kecamatan": fifty, "tahun": [2024]})
    assert p.filters == {"kecamatan": fifty, "tahun": [2024]}
    assert DataParams(slug="c", filters={}).filters is None
    for bad in (
        {"kecamatan": [*fifty, "lebih"]},
        {"kecamatan": []},
        {"kecamatan": [""]},
        {"": ["Bogor Barat"]},
    ):
        with pytest.raises(ValidationError):
            DataParams(slug="c", filters=bad)


@pytest.mark.parametrize("sort", ["tahun:asc", "tahun:desc", "nama_kecamatan:asc"])
def test_data_sort_valid(sort: str) -> None:
    assert DataParams(slug="c", sort=sort).sort == sort


@pytest.mark.parametrize(
    "sort", ["tahun", "tahun:ASC", "tahun:up", ":asc", "ta hun:asc", "a:b:asc"]
)
def test_data_sort_invalid(sort: str) -> None:
    with pytest.raises(ValidationError):
        DataParams(slug="c", sort=sort)


# ---------------------------------------------------------------- waktu WIB


def test_wib_serialization_from_utc() -> None:
    ref = DataRef(url="u", expires_at=datetime(2026, 11, 6, 3, 30, 2, 999, tzinfo=UTC), row_count=0)
    assert ref.expires_at.utcoffset() == timedelta(hours=7)
    assert ref.model_dump(mode="json")["expires_at"] == "2026-11-06T10:30:02+07:00"


def test_wib_serialization_from_iso_string() -> None:
    ref = DataRef.model_validate(
        {"url": "u", "expires_at": "2026-11-06T10:30:02+07:00", "row_count": 0}
    )
    assert ref.model_dump_json().count("+07:00") == 1
    assert format_wib(ref.expires_at) == "2026-11-06T10:30:02+07:00"


def test_naive_datetime_rejected() -> None:
    with pytest.raises(ValidationError, match="naive"):
        DataRef(url="u", expires_at=datetime(2026, 11, 6, 10, 30), row_count=0)


def test_plus_0707_not_normalized_here() -> None:
    """+07:07 dari portal belum diurus model (Langkah 2: services/normalize).

    Model hanya mengonversi instan waktu, sehingga +07:07 bergeser 7 menit. Tes ini
    mengunci perilaku itu agar normalisasi tidak diam-diam berpindah ke model.
    """
    odd = timezone(timedelta(hours=7, minutes=7))
    ref = DataRef(url="u", expires_at=datetime(2026, 11, 6, 10, 30, tzinfo=odd), row_count=0)
    assert ref.model_dump(mode="json")["expires_at"] == "2026-11-06T10:23:00+07:00"


def test_empty_values_serialize_as_null() -> None:
    dumped = SearchItem(slug="x", name="X", officialness=Officialness(status="draf")).model_dump(
        mode="json"
    )
    for key in ("opd", "topik", "satuan", "tahun_awal", "data_updated_at"):
        assert key in dumped and dumped[key] is None


# ---------------------------------------------------------------- penjaga angka


def test_mask_numbers() -> None:
    assert mask_numbers("baris 9.160.885 dan 1.234,5 tahun 2024") == (
        "baris [angka] dan [angka] tahun [angka]"
    )
    assert not contains_number(mask_numbers("x 12 y 3"))
    assert mask_numbers("tanpa angka") == "tanpa angka"


# ---------------------------------------------------------------- bagian amplop


def test_provenance_rules() -> None:
    p = s.provenance()
    assert p.metadata_changed_after_approve is False and p.change_notes is None
    data = p.model_dump()
    for bad in (
        {"freshness_source": None},
        {"data_updated_at": None},
        {"freshness_source": "mdate", "metadata_changed_after_approve": True},
        {"period_start": 2026, "period_end": 2025},
        {"served_from": "remote"},
    ):
        with pytest.raises(ValidationError):
            Provenance(**{**data, **bad})
    mdate = Provenance(**{**data, "freshness_source": "mdate"})
    assert mdate.freshness_source == "mdate"
    empty = Provenance(**{**data, "data_updated_at": None, "freshness_source": None})
    assert empty.model_dump(mode="json")["data_updated_at"] is None


def test_officialness_values() -> None:
    assert Officialness(status="draf").basis is None
    with pytest.raises(ValidationError):
        Officialness(status="resmi sekali")


@pytest.mark.parametrize(
    ("code", "rule"),
    [("suspect_zero_period", "QC-01"), ("suspect_scale", "QC-02"), ("suspect_outlier", "QC-02")],
)
def test_quality_flag_valid(code: str, rule: str) -> None:
    flag = QualityFlag(code=code, rule=rule)
    assert flag.column is None and flag.period is None and flag.detail is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"code": "suspect_zero_period", "rule": "QC-02"},
        {"code": "suspect_scale", "rule": "QC-01"},
        {"code": "suspect_other", "rule": "QC-01"},
        {"code": "suspect_scale", "rule": "QC-03"},
        {"code": "suspect_outlier", "rule": "QC-02", "detail": "Rp55,5 juta"},
    ],
)
def test_quality_flag_invalid(kwargs: dict) -> None:
    with pytest.raises(ValidationError):
        QualityFlag(**kwargs)


def test_tool_error_rejects_numbers_and_wrong_retryable() -> None:
    with pytest.raises(ValidationError, match="angka"):
        ToolError(code="INTERNAL", message="gagal 42", retryable=True)
    with pytest.raises(ValidationError, match="retryable"):
        ToolError(code="INTERNAL", message="gagal", retryable=False)
    with pytest.raises(ValidationError):
        ToolError(code="TIDAK_ADA", message="gagal", retryable=False)


# ---------------------------------------------------------------- result


def test_column_info_rules() -> None:
    with pytest.raises(ValidationError, match="KT-03"):
        ColumnInfo(name="nilai", role="measure", top_values=[1, 2])
    with pytest.raises(ValidationError, match="meaning"):
        ColumnInfo(name="x", role="id", meaning="Arti", meaning_source="none")
    with pytest.raises(ValidationError, match="meaning"):
        ColumnInfo(name="x", role="id", meaning_source="template")
    assert ColumnInfo(name="x", role="dimension").meaning_source == "none"


def test_search_result_rules() -> None:
    item = s.search_result().items[0]
    with pytest.raises(ValidationError, match="page_size"):
        SearchResult(total=5, page=1, page_size=1, items=[item, item])
    with pytest.raises(ValidationError, match="total"):
        SearchResult(total=0, page=1, page_size=10, items=[item])


def test_info_result_unique_columns() -> None:
    data = s.info_result().model_dump()
    data["columns"].append(data["columns"][0])
    with pytest.raises(ValidationError, match="unik"):
        type(s.info_result())(**data)


def test_data_result_has_no_measure_value_fields() -> None:
    assert set(DataResult.model_fields) == {
        "data_ref",
        "columns",
        "periods_requested",
        "periods_found",
        "missing_periods",
        "preview",
        "filters_applied",
    }
    assert set(DataRef.model_fields) == {"url", "expires_at", "row_count", "format"}
    with pytest.raises(ValidationError, match="extra"):
        DataResult(**s.data_result().model_dump(), rows=[{"persentase_miskin": 6.5}])


@pytest.mark.parametrize(
    "change",
    [
        {"preview": [{"tahun": 2025, "persentase_miskin": 6}]},  # kolom measure
        {"preview": [{"bps_kode_kabupaten_kota": "3271"}]},  # kolom id
        {"preview": [{"kolom_hantu": "x"}]},
        {"preview": [{"tahun": 2025}] * 6},
        {"preview": [{"tahun": 6.5}]},
        {"filters_applied": {"persentase_miskin": [6]}},
        {"missing_periods": [2027]},
        {"periods_found": [2025, 2026]},
        {"periods_requested": None},
    ],
)
def test_data_result_rejects(change: dict) -> None:
    data = {**s.data_result().model_dump(), **change}
    with pytest.raises(ValidationError):
        DataResult(**data)


def test_data_ref_format_only_json() -> None:
    with pytest.raises(ValidationError):
        DataRef(url="u", expires_at=s.T_RETRIEVED, row_count=1, format="csv")
    with pytest.raises(ValidationError):
        DataRef(url="u", expires_at=s.T_RETRIEVED, row_count=-1)


# ---------------------------------------------------------------- round-trip JSON


ROUND_TRIP_SAMPLES = [
    s.provenance(),
    s.provenance("stale_cache"),
    s.officialness(),
    s.quality_flag(),
    ToolError.from_code("RATE_LIMITED", "Portal membatasi permintaan"),
    *s.columns(),
    s.search_result().items[0],
    s.search_result(),
    s.info_result(),
    s.data_result().data_ref,
    s.data_result(),
    SearchParams(query="pad", opd="Bapenda", topik="Keuangan", tahun=2024, page=2, page_size=5),
    InfoParams(slug="contoh", request_id="rq-1"),
    DataParams(
        slug="contoh",
        periods=[2025, 2026],
        filters={"kecamatan": ["Bogor Barat"], "tahun": [2025]},
        sort="tahun:desc",
        force_refresh=True,
        request_id="rq-2",
    ),
]


@pytest.mark.parametrize("obj", ROUND_TRIP_SAMPLES, ids=lambda o: type(o).__name__)
def test_round_trip_json(obj: BaseModel) -> None:
    again = type(obj).model_validate_json(obj.model_dump_json())
    assert again == obj
    assert again.model_dump_json() == obj.model_dump_json()


def test_wib_constant_used() -> None:
    assert s.data_result().data_ref.expires_at.tzinfo is WIB
