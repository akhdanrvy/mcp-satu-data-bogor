"""Penyusun get_dataset_info (RENCANA_LANGKAH8 bagian 1.2, 5, 6). Fungsi murni.

Bundel kamus (KB-36) adalah yang disimpan di cache kamus per `data_updated_at` katalog:
hasil services/dictionary, `periods_available`, `row_count`, nilai unik kolom dimension
(untuk validasi filter di 8c), dan `retrieved_at` (waktu portal dihubungi). Nilai kolom
measure tidak pernah masuk bundel.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from mcp_bogor.adapters.base import ColumnProbe
from mcp_bogor.models.envelope import Officialness, Provenance, ServedFrom
from mcp_bogor.models.notices import Notice, NoticeCode
from mcp_bogor.models.results import ColumnInfo, InfoResult
from mcp_bogor.services.budget import TruncatedReason, truncate_text
from mcp_bogor.services.catalog import CatalogDataset
from mcp_bogor.services.dictionary import DictionaryResult
from mcp_bogor.services.periods import PERIOD_COLUMN, periods_available

INFO_BUNDLE_FORMAT = 1

# KB-38: langkah pemangkasan info.
TOP_VALUES_TRIMMED = 5
DESCRIPTION_TRIMMED_CHARS = 200
MEANING_TRIMMED_CHARS = 120

DimensionValue = str | int


# ---------------------------------------------------------------- bundel kamus (KB-36)


@dataclass(frozen=True)
class InfoBundle:
    dictionary: DictionaryResult
    periods_available: list[int] | None  # None = dataset tanpa kolom tahun (CP-03)
    row_count: int | None
    retrieved_at: datetime
    dimension_values: dict[str, list[DimensionValue]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": INFO_BUNDLE_FORMAT,
            "dictionary": self.dictionary.to_dict(),
            "periods_available": self.periods_available,
            "row_count": self.row_count,
            "retrieved_at": self.retrieved_at.isoformat(),
            "dimension_values": self.dimension_values,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> InfoBundle:
        """Kebalikan to_dict; bentuk lain → ValueError (pemanggil membuat ulang)."""
        if raw.get("format") != INFO_BUNDLE_FORMAT:
            raise ValueError("format bundel kamus tidak dikenal")
        try:
            periods = raw["periods_available"]
            row_count = raw["row_count"]
            return cls(
                dictionary=DictionaryResult.from_dict(raw["dictionary"]),
                periods_available=None if periods is None else [int(p) for p in periods],
                row_count=None if row_count is None else int(row_count),
                retrieved_at=datetime.fromisoformat(raw["retrieved_at"]),
                dimension_values={
                    str(k): list(v) for k, v in dict(raw.get("dimension_values") or {}).items()
                },
            )
        except (KeyError, TypeError) as exc:
            raise ValueError("isi bundel kamus tidak sah") from exc


def _dimension_values(values: Sequence[Any]) -> list[DimensionValue]:
    return [v for v in values if isinstance(v, str | int) and not isinstance(v, bool)]


def build_bundle(
    dictionary: DictionaryResult,
    probe: ColumnProbe,
    sample_rows: Sequence[Mapping[str, Any]],
    retrieved_at: datetime,
) -> InfoBundle:
    """Bundel dari hasil kamus + probe. Nilai unik hanya untuk kolom dimension."""
    dimensions = [c.name for c in dictionary.columns if c.role == "dimension"]
    unique: dict[str, Sequence[Any]] = {}
    if PERIOD_COLUMN in probe.columns or any(PERIOD_COLUMN in row for row in sample_rows):
        unique[PERIOD_COLUMN] = probe.metadata_filter.get(PERIOD_COLUMN) or [
            row.get(PERIOD_COLUMN) for row in sample_rows
        ]
    return InfoBundle(
        dictionary=dictionary,
        periods_available=periods_available(unique),
        row_count=probe.total_rows,
        retrieved_at=retrieved_at,
        dimension_values={
            name: _dimension_values(probe.metadata_filter.get(name, [])) for name in dimensions
        },
    )


# ---------------------------------------------------------------- provenance dan keresmian


def source_url(base_url: str, slug: str) -> str:
    """KB-40 (terverifikasi 11 Nov 2026): halaman dataset portal."""
    base = base_url.rstrip("/")
    if base.endswith("/api"):
        base = base[: -len("/api")]
    return f"{base}/dataset/{slug}"


def build_provenance(
    dataset: CatalogDataset,
    *,
    base_url: str,
    retrieved_at: datetime,
    served_from: ServedFrom,
    periods: Sequence[int] | None,
) -> Provenance:
    start, end = (min(periods), max(periods)) if periods else (None, None)
    if start is None:
        start, end = dataset.tahun_awal, dataset.tahun_akhir
        if start is not None and end is not None and start > end:
            start = end = None
    return Provenance(
        source_url=source_url(base_url, dataset.slug),
        opd=dataset.opd,
        data_updated_at=dataset.data_updated_at,
        freshness_source=dataset.freshness_source,  # type: ignore[arg-type]
        metadata_changed_after_approve=dataset.metadata_changed_after_approve,
        change_notes=dataset.change_notes,
        retrieved_at=retrieved_at,
        period_start=start,
        period_end=end,
        served_from=served_from,
    )


def build_officialness(dataset: CatalogDataset) -> Officialness:
    return Officialness(status=dataset.official_status, basis=dataset.official_basis)  # type: ignore[arg-type]


# ---------------------------------------------------------------- notice (bagian 5)


def dictionary_notices(dictionary: DictionaryResult) -> list[Notice]:
    """Catatan kamus untuk model: kolom nilai belum terverifikasi, tanpa measure, satuan ganda."""
    notices: list[Notice] = []
    for reason in dictionary.reasons:
        if reason.code == "measure_undocumented" and reason.column:
            notices.append(
                Notice.from_template(NoticeCode.DICTIONARY_NOT_VALIDATED, column=reason.column)
            )
        elif reason.code == "no_measure":
            notices.append(Notice.from_template(NoticeCode.NO_MEASURE_COLUMN))
    if dictionary.unit_source == "conflict":
        for column in dictionary.columns:
            if column.role == "measure" and column.unit is None:
                notices.append(Notice.from_template(NoticeCode.UNIT_CONFLICT, column=column.name))
    return notices


# ---------------------------------------------------------------- result + pemangkasan (KB-38)


def _cap_top_values(column: ColumnInfo, limit: int) -> ColumnInfo:
    if column.top_values is None:
        return column
    values = column.top_values[:limit] if limit > 0 else None
    return column.model_copy(update={"top_values": values or None})


def build_info_result(
    dataset: CatalogDataset,
    bundle: InfoBundle,
    *,
    description_max_chars: int,
    top_values: int,
) -> InfoResult:
    return InfoResult(
        slug=dataset.slug,
        name=dataset.name,
        opd=dataset.opd,
        topik=dataset.topik,
        kategori=dataset.kategori,
        description=truncate_text(dataset.deskripsi_text, description_max_chars),
        satuan=dataset.satuan,
        frekuensi=dataset.frekuensi,
        tingkat_penyajian=dataset.tingkat_penyajian,
        cakupan=dataset.cakupan,
        periods_available=bundle.periods_available or [],
        columns=[_cap_top_values(c, top_values) for c in bundle.dictionary.columns],
        row_count=bundle.row_count,
        dictionary_validated=bundle.dictionary.dictionary_validated,
    )


def info_candidates(result: InfoResult) -> Iterator[tuple[TruncatedReason | None, InfoResult]]:
    """Utuh, lalu pemangkasan kumulatif: top_values ≤ 5 → hanya tahun → deskripsi → arti."""
    yield None, result
    step = result.model_copy(
        update={"columns": [_cap_top_values(c, TOP_VALUES_TRIMMED) for c in result.columns]}
    )
    yield TruncatedReason.TOP_VALUES, step
    step = step.model_copy(
        update={
            "columns": [
                c if c.name == PERIOD_COLUMN else c.model_copy(update={"top_values": None})
                for c in step.columns
            ]
        }
    )
    yield TruncatedReason.TOP_VALUES, step
    step = step.model_copy(
        update={"description": truncate_text(step.description, DESCRIPTION_TRIMMED_CHARS)}
    )
    yield TruncatedReason.DESCRIPTION, step
    step = step.model_copy(
        update={
            "columns": [
                c.model_copy(update={"meaning": truncate_text(c.meaning, MEANING_TRIMMED_CHARS)})
                for c in step.columns
            ]
        }
    )
    yield TruncatedReason.MEANING, step
