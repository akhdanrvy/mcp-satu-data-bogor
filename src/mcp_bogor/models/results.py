"""Isi `result` tiap tool (Dok 4 bagian 3 s.d. 6).

KT-03 (nol toleransi): DataResult tidak punya field yang bisa menampung nilai kolom
measure. preview dan filters_applied hanya boleh memakai kolom berperan dimension,
dan top_values hanya ada untuk kolom dimension.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from mcp_bogor.models.common import ContractModel, WibDatetime
from mcp_bogor.models.envelope import Officialness

DimensionValue = str | int | None
ColumnRole = Literal["dimension", "measure", "id"]
MAX_PREVIEW_ROWS = 5


# --- search_datasets ---


class SearchItem(ContractModel):
    # TODO(Langkah 8): Dok 4 bagian 3 mengizinkan provenance/quality_flags per item
    # "seperlunya"; ditambahkan bila tool search memang mengisinya.
    slug: str = Field(min_length=1)
    name: str
    opd: str | None = None
    topik: str | None = None
    kategori: str | None = None
    satuan: str | None = None
    frekuensi: str | None = None
    tahun_awal: int | None = None
    tahun_akhir: int | None = None
    data_updated_at: WibDatetime | None = None
    officialness: Officialness


class SearchResult(ContractModel):
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1)
    items: list[SearchItem]
    catalog_synced_at: WibDatetime | None = None

    @model_validator(mode="after")
    def _items_fit_page(self) -> SearchResult:
        if len(self.items) > self.page_size:
            raise ValueError("jumlah items melebihi page_size")
        if len(self.items) > self.total:
            raise ValueError("jumlah items melebihi total")
        return self


# --- get_dataset_info ---


class ColumnInfo(ContractModel):
    name: str = Field(min_length=1)
    role: ColumnRole
    type: str | None = None
    meaning: str | None = None
    unit: str | None = None
    meaning_source: Literal["template", "inferred", "none"] = "none"
    top_values: list[DimensionValue] | None = None

    @model_validator(mode="after")
    def _check(self) -> ColumnInfo:
        if (self.meaning is None) != (self.meaning_source == "none"):
            raise ValueError("meaning=null tepat bila meaning_source='none' (KK, tanpa tebakan)")
        if self.top_values is not None and self.role != "dimension":
            raise ValueError("top_values hanya untuk kolom dimension (KT-03)")
        return self


def _check_unique_names(columns: list[ColumnInfo]) -> None:
    names = [c.name for c in columns]
    if len(names) != len(set(names)):
        raise ValueError("nama kolom harus unik")


class InfoResult(ContractModel):
    slug: str = Field(min_length=1)
    name: str
    opd: str | None = None
    topik: str | None = None
    kategori: str | None = None
    description: str | None = None
    satuan: str | None = None
    frekuensi: str | None = None
    tingkat_penyajian: str | None = None
    cakupan: str | None = None
    periods_available: list[int] = Field(default_factory=list)
    columns: list[ColumnInfo]
    row_count: int | None = Field(default=None, ge=0)
    dictionary_validated: bool

    @model_validator(mode="after")
    def _check(self) -> InfoResult:
        _check_unique_names(self.columns)
        return self


# --- get_dataset_data ---


class DataRef(ContractModel):
    url: str = Field(min_length=1)
    expires_at: WibDatetime
    row_count: int = Field(ge=0)
    format: Literal["json"] = "json"


class DataResult(ContractModel):
    data_ref: DataRef
    columns: list[ColumnInfo]
    periods_requested: list[int] | None = None
    periods_found: list[int] = Field(default_factory=list)
    missing_periods: list[int] = Field(default_factory=list)
    preview: list[dict[str, DimensionValue]] = Field(
        default_factory=list, max_length=MAX_PREVIEW_ROWS
    )
    filters_applied: dict[str, list[str | int]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check(self) -> DataResult:
        _check_unique_names(self.columns)
        dimensions = {c.name for c in self.columns if c.role == "dimension"}
        for row in self.preview:
            extra = set(row) - dimensions
            if extra:
                raise ValueError(f"preview hanya boleh kolom dimension; ditolak: {sorted(extra)}")
        extra = set(self.filters_applied) - dimensions
        if extra:
            raise ValueError(f"filters_applied hanya boleh kolom dimension: {sorted(extra)}")

        requested = set(self.periods_requested or ())
        missing = set(self.missing_periods)
        if not missing <= requested:
            raise ValueError("missing_periods harus bagian dari periods_requested")
        if missing & set(self.periods_found):
            raise ValueError("periode tidak boleh sekaligus found dan missing")
        return self
