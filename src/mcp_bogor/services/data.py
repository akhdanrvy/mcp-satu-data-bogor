"""Penyusun get_dataset_data (RENCANA_LANGKAH8 bagian 1.3, 2, 3, 4). Fungsi murni.

- `plan_query`: validasi filter/sort terhadap kamus (KB-39, KK-A1) → rencana kueri.
- `normalize_series`: baris /bigdata → baris data_ref (NB-02..08) + `label_originals` (KK-Q).
- `derive`: saring lokal, cakupan periode (CP), QC atas seri tersaring (KB-37), potong periode,
  dan isi berkas data_ref (Dok 4 bagian 6, tanpa served_from, KB-34).
Tidak ada I/O; nilai measure hanya ada di baris berkas data_ref, tidak di respons tool.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from mcp_bogor.config import CONTRACT_VERSION
from mcp_bogor.models.common import is_numeric_label_column, looks_like_formatted_number
from mcp_bogor.models.envelope import QualityFlag
from mcp_bogor.models.errors import ErrorCode
from mcp_bogor.models.params import MAX_PERIODS, DataParams
from mcp_bogor.models.results import ColumnInfo, DimensionValue
from mcp_bogor.services.dictionary import DictionaryResult
from mcp_bogor.services.info import InfoBundle
from mcp_bogor.services.normalize import (
    INTERNAL_ID_COLUMNS,
    is_code_column,
    is_empty,
    mask_free_text,
    normalize_label,
    normalize_rows,
)
from mcp_bogor.services.periods import PERIOD_COLUMN, PeriodCoverage, compute, parse_year
from mcp_bogor.services.quality import check_quality

# KB-37: per_page maksimum adaptor (KB-24) untuk mengambil seri.
SERIES_PER_PAGE = 1000
VARIANT_SERIES_LOCAL = "seri-lokal"
VARIANT_SERIES_PORTAL = "seri-portal"

# KK-A1: kolom wilayah di bawah kota; dataset tingkat kota tidak bisa dipecah ke sana.
REGION_TOKENS = frozenset({"kecamatan", "kelurahan", "desa"})
_COLUMN_TOKEN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")


class QueryRejected(Exception):
    """Parameter ditolak terhadap kamus; tool mengubahnya menjadi galat kontrak."""

    def __init__(self, code: ErrorCode, **args: Any) -> None:
        super().__init__(code.value)
        self.code = code
        self.args_ = args


def label_key(value: Any) -> str:
    """Kunci pencocokan NB-08: label diseragamkan lalu tanpa beda huruf."""
    return normalize_label(str(value))[0].casefold()


def _safe_column(name: str) -> str | None:
    return name if _COLUMN_TOKEN.match(name) else None


def is_region_column(name: str) -> bool:
    return bool(REGION_TOKENS & set(name.casefold().split("_")))


def is_city_level(tingkat_penyajian: str | None) -> bool:
    return bool(tingkat_penyajian) and str(tingkat_penyajian).strip().casefold().startswith("kota")


# ---------------------------------------------------------------- rencana kueri (KB-39)


@dataclass(frozen=True)
class QueryPlan:
    periods: list[int] | None
    keys: dict[str, frozenset[str]]  # kolom → kunci NB-08 yang diterima (saring lokal)
    display: dict[str, list[DimensionValue]]  # filter efektif, label NB-08 (filters_applied)
    portal_where: dict[str, list[Any]]  # varian asli untuk `where` portal (jalur portal)
    sort: str | None
    time_dimension: bool


def _reject_filters(column: str | None) -> QueryRejected:
    return QueryRejected(ErrorCode.INVALID_PARAM, param="filters", column=column)


def _years_from_filter(values: Sequence[Any]) -> list[int]:
    years = [parse_year(v) for v in values]
    if any(y is None for y in years):
        raise _reject_filters(PERIOD_COLUMN)
    return sorted({y for y in years if y is not None})


def plan_query(
    params: DataParams,
    bundle: InfoBundle,
    *,
    slug: str,
    tingkat_penyajian: str | None,
) -> QueryPlan:
    columns = {c.name: c for c in bundle.dictionary.columns}
    time_dimension = PERIOD_COLUMN in columns
    periods = list(params.periods) if params.periods is not None else None
    filters = dict(params.filters or {})

    if PERIOD_COLUMN in filters:
        if periods is not None:
            raise _reject_filters(PERIOD_COLUMN)
        periods = _years_from_filter(filters.pop(PERIOD_COLUMN))
        if len(periods) > MAX_PERIODS:
            raise QueryRejected(ErrorCode.INVALID_PARAM, param="periods", limit=MAX_PERIODS)

    keys: dict[str, frozenset[str]] = {}
    display: dict[str, list[DimensionValue]] = {}
    where: dict[str, list[Any]] = {}
    for column, values in filters.items():
        info = columns.get(column)
        if info is None:
            safe = _safe_column(column)
            if safe and is_region_column(column) and is_city_level(tingkat_penyajian):
                raise QueryRejected(ErrorCode.LEVEL_NOT_AVAILABLE, slug=slug, column=safe)
            raise _reject_filters(safe)
        if info.role != "dimension":
            raise _reject_filters(column)
        known = bundle.dimension_values.get(column, [])
        if known:
            variants: dict[str, list[Any]] = {}
            for raw in known:
                variants.setdefault(label_key(raw), []).append(raw)
            chosen: list[Any] = []
            shown: list[DimensionValue] = []
            for value in values:
                matched = variants.get(label_key(value))
                if matched is None:
                    raise _reject_filters(column)
                chosen.extend(v for v in matched if v not in chosen)
                label = _display_value(matched[0])
                if label not in shown:
                    shown.append(label)
        else:
            chosen = list(dict.fromkeys(values))
            shown = list(dict.fromkeys(_display_value(v) for v in values))
        keys[column] = frozenset(label_key(v) for v in values)
        display[column] = shown
        where[column] = chosen

    if "sort" in params.model_fields_set:
        column = params.sort.split(":", 1)[0]
        info = columns.get(column)
        if info is None or info.role == "id":
            raise QueryRejected(ErrorCode.INVALID_PARAM, param="sort", column=_safe_column(column))
        sort: str | None = params.sort
    else:
        sort = params.sort if params.sort.split(":", 1)[0] in columns else None

    return QueryPlan(
        periods=periods,
        keys=keys,
        display=display,
        portal_where=where,
        sort=sort,
        time_dimension=time_dimension,
    )


def _display_value(value: Any) -> DimensionValue:
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return value
    return normalize_label(str(value))[0]


# ---------------------------------------------------------------- normalisasi seri (NB, KK-Q)


def data_columns(dictionary: DictionaryResult) -> list[ColumnInfo]:
    """Kolom berkas data_ref dan respons data: tanpa kolom id (NB-04), tanpa top_values."""
    return [c.model_copy(update={"top_values": None}) for c in dictionary.columns if c.role != "id"]


def response_columns(columns: Sequence[ColumnInfo]) -> list[ColumnInfo]:
    """KB-46: kolom di respons get_dataset_data. Arti hanya untuk measure (disamarkan);
    dimension meaning=null dengan meaning_source tetap. Berkas data_ref memuat arti lengkap."""
    return [
        c.model_copy(update={"meaning": mask_free_text(c.meaning) if c.role == "measure" else None})
        for c in columns
    ]


@dataclass(frozen=True)
class NormalizedSeries:
    rows: list[dict[str, Any]]
    label_originals: dict[str, dict[str, list[str]]]
    parse_issues: dict[str, int] = field(default_factory=dict)


def normalize_series(
    raw_rows: Sequence[Mapping[str, Any]], dictionary: DictionaryResult
) -> NormalizedSeries:
    columns = data_columns(dictionary)
    names = [c.name for c in columns]
    measures = {c.name: "number" for c in columns if c.role == "measure"}
    ids = {c.name for c in dictionary.columns if c.role == "id"} | INTERNAL_ID_COLUMNS
    normalized, issues = normalize_rows(raw_rows, measures, drop_columns=ids)
    labels = [c.name for c in columns if c.role == "dimension" and not is_code_column(c.name)]
    originals: dict[str, dict[str, set[str]]] = {}
    rows: list[dict[str, Any]] = []
    for row in normalized:
        out = {name: row.get(name) for name in names}
        for name in labels:
            value = out[name]
            if isinstance(value, str):
                label, original = normalize_label(value)
                if label != original:
                    originals.setdefault(name, {}).setdefault(label, set()).add(original)
                out[name] = label
        rows.append(out)
    counts: dict[str, int] = {}
    for issue in issues:
        counts[issue.column] = counts.get(issue.column, 0) + 1
    return NormalizedSeries(
        rows=rows,
        label_originals={
            col: {label: sorted(vals) for label, vals in sorted(m.items())}
            for col, m in sorted(originals.items())
        },
        parse_issues=counts,
    )


# ---------------------------------------------------------------- turunan (KB-37)


def filter_rows(
    rows: Iterable[Mapping[str, Any]], keys: Mapping[str, frozenset[str]]
) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in rows
        if all(
            not is_empty(row.get(col)) and label_key(row.get(col)) in accepted
            for col, accepted in keys.items()
        )
    ]


def years_of(rows: Iterable[Mapping[str, Any]]) -> list[int]:
    return sorted({y for row in rows if (y := parse_year(row.get(PERIOD_COLUMN))) is not None})


def _preview_value(column: str, value: Any) -> DimensionValue:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    text = str(value)
    if looks_like_formatted_number(text) and not is_numeric_label_column(column):
        return None  # KT-03 lapis kedua; kolom seperti ini mestinya measure (aturan e)
    return text


def preview_rows(
    rows: Sequence[Mapping[str, Any]], columns: Sequence[ColumnInfo], limit: int
) -> list[dict[str, DimensionValue]]:
    dims = [c.name for c in columns if c.role == "dimension"]
    return [{d: _preview_value(d, row.get(d)) for d in dims} for row in rows[: max(limit, 0)]]


@dataclass(frozen=True)
class Derived:
    rows: list[dict[str, Any]]
    coverage: PeriodCoverage
    available: list[int] | None
    flags_all: list[QualityFlag]
    flags: list[QualityFlag]
    label_originals: dict[str, dict[str, list[str]]]
    filters_applied: dict[str, list[DimensionValue]]


def derive(
    series_rows: Sequence[Mapping[str, Any]],
    series_originals: Mapping[str, Mapping[str, Sequence[str]]],
    plan: QueryPlan,
    measures: Sequence[str],
    *,
    qc_ratio: float,
) -> Derived:
    """Saring lokal → cakupan periode → QC atas seri tersaring → potong ke periode diminta."""
    filtered = filter_rows(series_rows, plan.keys)
    available = years_of(filtered) if plan.time_dimension else None
    coverage = compute(plan.periods, available)
    flags_all = check_quality(filtered, measures, ratio=qc_ratio) if plan.time_dimension else []
    if plan.time_dimension and plan.periods is not None:
        found = set(coverage.periods_found or [])
        rows = [r for r in filtered if parse_year(r.get(PERIOD_COLUMN)) in found]
        flags = [f for f in flags_all if f.period is None or f.period in found]
    else:
        rows, flags = filtered, flags_all
    present = {col: {str(r.get(col)) for r in rows} for col in series_originals}
    originals = {
        col: {label: list(vals) for label, vals in labels.items() if label in present[col]}
        for col, labels in series_originals.items()
    }
    applied: dict[str, list[DimensionValue]] = dict(plan.display)
    if plan.time_dimension and plan.periods is not None:
        applied = {PERIOD_COLUMN: list(plan.periods), **applied}
    return Derived(
        rows=rows,
        coverage=coverage,
        available=available,
        flags_all=flags_all,
        flags=flags,
        label_originals={col: m for col, m in originals.items() if m},
        filters_applied=applied,
    )


def data_ref_payload(
    *,
    slug: str,
    columns: Sequence[ColumnInfo],
    derived: Derived,
    provenance: Mapping[str, Any],
    officialness: Mapping[str, Any],
    dictionary_validated: bool,
) -> dict[str, Any]:
    """Isi berkas data_ref (Dok 4 bagian 6 + KK-Q, KK-R). provenance tanpa served_from (KB-34)."""
    cov = derived.coverage
    return {
        "contract_version": CONTRACT_VERSION,
        "slug": slug,
        "columns": [c.model_dump(mode="json") for c in columns],
        "rows": derived.rows,
        "provenance": {k: v for k, v in provenance.items() if k != "served_from"},
        "officialness": dict(officialness),
        "quality_flags": [f.model_dump(mode="json") for f in derived.flags],
        "periods_requested": cov.periods_requested,
        "periods_found": cov.periods_found,
        "missing_periods": cov.missing_periods,
        "time_dimension": cov.time_dimension,
        "filters_applied": derived.filters_applied,
        "dictionary_validated": dictionary_validated,
        "label_originals": derived.label_originals,
    }
