"""services/data (Langkah 8c): rencana kueri KB-39, normalisasi seri NB/KK-Q, turunan KB-37."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from mcp_bogor.adapters.base import ColumnProbe
from mcp_bogor.config import WIB
from mcp_bogor.models import DataParams, ErrorCode
from mcp_bogor.models.notices import Notice, NoticeCode
from mcp_bogor.services.data import (
    QueryRejected,
    data_columns,
    data_ref_payload,
    derive,
    filter_rows,
    is_city_level,
    label_key,
    normalize_series,
    plan_query,
    preview_rows,
)
from mcp_bogor.services.dictionary import build_dictionary
from mcp_bogor.services.info import build_bundle
from mcp_bogor.tools.data import _data_candidates

NOW = datetime(2026, 11, 11, 9, 0, tzinfo=WIB)
SEG_A = "SIMPANG\n  EKALOKASARI- SIMPANG BARANANGSIANG"
SEG_B = "SIMPANG EKALOKASARI \N{EN DASH} SIMPANG BARANANGSIANG"
ROWS = [
    {"id": 1, "segmen": SEG_A, "kategori": "x", "nilai": "9.160.885", "tahun": 2023},
    {"id": 2, "segmen": SEG_B, "kategori": "y", "nilai": "1.234,5", "tahun": 2024},
    {"id": 3, "segmen": "LAIN", "kategori": "x", "nilai": None, "tahun": "2025"},
]


def _bundle(rows: list[dict[str, Any]] = ROWS, **filters: list[Any]) -> Any:
    columns = list(rows[0])
    probe = ColumnProbe(columns=columns, metadata_filter=dict(filters), total_rows=len(rows))
    dictionary = build_dictionary(description=None, probe=probe, sample_rows=rows)
    return build_bundle(dictionary, probe, rows, NOW)


def _plan(bundle: Any, tingkat: str | None = "Kota Bogor", **params: Any) -> Any:
    return plan_query(DataParams(slug="s", **params), bundle, slug="s", tingkat_penyajian=tingkat)


def test_label_key_and_city_level() -> None:
    assert (
        label_key(SEG_A)
        == label_key(SEG_B)
        == label_key("simpang ekalokasari - simpang baranangsiang")
    )
    assert label_key(2025) == "2025"
    assert is_city_level("Kota Bogor") and is_city_level(" kota ")
    assert not is_city_level("Kecamatan Bogor Barat") and not is_city_level(None)


def test_plan_known_values_send_all_raw_variants() -> None:
    bundle = _bundle(segmen=[SEG_A, SEG_B, "LAIN"], tahun=["2023", "2024", "2025"])
    plan = _plan(bundle, filters={"segmen": ["simpang ekalokasari - simpang baranangsiang"]})
    assert plan.portal_where == {"segmen": [SEG_A, SEG_B]}
    assert plan.display == {"segmen": ["SIMPANG EKALOKASARI - SIMPANG BARANANGSIANG"]}
    assert plan.sort == "tahun:asc" and plan.time_dimension


def test_plan_free_text_column_and_no_period_column() -> None:
    rows = [{"kategori": "x", "nilai": 3}]
    plan = _plan(_bundle(rows), filters={"kategori": ["bebas", "bebas"]})
    assert plan.portal_where == {"kategori": ["bebas"]} and plan.display == {"kategori": ["bebas"]}
    assert plan.sort is None and plan.time_dimension is False  # sort bawaan hanya bila ada tahun


def test_plan_rejections() -> None:
    bundle = _bundle(kategori=["x", "y"])
    too_many = {"tahun": list(range(2000, 2021))}
    with pytest.raises(QueryRejected) as info:
        _plan(bundle, filters=too_many)
    assert (info.value.code, info.value.args_) == (
        ErrorCode.INVALID_PARAM,
        {"param": "periods", "limit": 20},
    )
    with pytest.raises(QueryRejected) as info:
        _plan(bundle, tingkat="Kecamatan Bogor Barat", filters={"kecamatan": ["a"]})
    assert info.value.code is ErrorCode.INVALID_PARAM  # bukan tingkat kota → bukan KK-A1
    with pytest.raises(QueryRejected) as info:
        _plan(bundle, filters={"kecamatan": ["a"]})
    assert info.value.code is ErrorCode.LEVEL_NOT_AVAILABLE
    with pytest.raises(QueryRejected) as info:
        _plan(bundle, sort="kol.lain:asc")
    assert info.value.args_ == {"param": "sort", "column": None}


def test_normalize_series_nb_rules_and_label_originals() -> None:
    bundle = _bundle()
    series = normalize_series(ROWS, bundle.dictionary)
    assert all("id" not in row for row in series.rows)  # NB-04
    assert [row["nilai"] for row in series.rows] == [9160885, 1234.5, None]  # NB-07
    assert series.rows[0]["segmen"] == series.rows[1]["segmen"]  # NB-08 diseragamkan
    assert series.label_originals == {
        "segmen": {"SIMPANG EKALOKASARI - SIMPANG BARANANGSIANG": sorted([SEG_A, SEG_B])}
    }
    assert series.parse_issues == {}


def test_derive_filters_periods_flags_and_originals() -> None:
    bundle = _bundle(segmen=[SEG_A, SEG_B, "LAIN"])
    series = normalize_series(ROWS, bundle.dictionary)
    plan = _plan(bundle, periods=[2024, 2026], filters={"kategori": ["y"]})
    derived = derive(series.rows, series.label_originals, plan, ["nilai"], qc_ratio=50)
    assert derived.available == [2024]
    assert (derived.coverage.periods_found, derived.coverage.missing_periods) == ([2024], [2026])
    assert [r["tahun"] for r in derived.rows] == [2024]
    assert derived.filters_applied == {"tahun": [2024, 2026], "kategori": ["y"]}
    assert derived.label_originals == {
        "segmen": {"SIMPANG EKALOKASARI - SIMPANG BARANANGSIANG": sorted([SEG_A, SEG_B])}
    }
    other = derive(
        series.rows,
        series.label_originals,
        _plan(bundle, filters={"segmen": ["LAIN"]}),
        ["nilai"],
        qc_ratio=50,
    )
    assert other.label_originals == {}  # label asli hanya untuk label yang ada di subset


def test_filter_rows_skips_empty_values() -> None:
    rows = [{"k": None}, {"k": "-"}, {"k": "A"}]
    assert filter_rows(rows, {"k": frozenset({"a"})}) == [{"k": "A"}]


def test_preview_values_are_guarded() -> None:
    bundle = _bundle()
    columns = data_columns(bundle.dictionary)
    rows = [{"segmen": "9.160.885", "kategori": 2.0, "tahun": 2.5, "nilai": 1}]
    (row,) = preview_rows(rows, columns, 5)
    assert row == {"segmen": None, "kategori": 2, "tahun": None}
    assert preview_rows(rows, columns, 0) == []


def test_payload_without_served_from() -> None:
    bundle = _bundle()
    series = normalize_series(ROWS, bundle.dictionary)
    derived = derive(series.rows, {}, _plan(bundle), ["nilai"], qc_ratio=50)
    payload = data_ref_payload(
        slug="s",
        columns=data_columns(bundle.dictionary),
        derived=derived,
        provenance={"source_url": "u", "served_from": "live"},
        officialness={"status": "resmi"},
        dictionary_validated=False,
    )
    assert "served_from" not in payload["provenance"]
    assert payload["dictionary_validated"] is False and payload["label_originals"] == {}
    assert payload["periods_found"] == [2023, 2024, 2025] and payload["time_dimension"] is True


def test_data_candidates_dedupe_notices() -> None:
    from tests import model_samples as s

    notices = [Notice.from_template(NoticeCode.DICTIONARY_NOT_VALIDATED, column=c) for c in "ab"]
    steps = list(_data_candidates(s.data_result(), notices))
    assert [r.value if r else None for r, _, _ in steps][-1] == "notices_dipangkas"
    assert len(steps[-1][2]) == 1 and len(steps[0][2]) == 2
