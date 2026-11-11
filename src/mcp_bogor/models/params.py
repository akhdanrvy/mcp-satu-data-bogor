"""Parameter ketiga tool (Dok 4 bagian 3 s.d. 5). Pelanggaran validasi → INVALID_PARAM.

Batas page_size mengikuti MCP_SEARCH_MAX_PAGE_SIZE lewat konteks validasi:
    SearchParams.model_validate(args, context={SEARCH_MAX_PAGE_SIZE_KEY: n})
Tanpa konteks dipakai nilai awal Dok 4 bagian 9 (20).
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import (
    ConfigDict,
    Field,
    StringConstraints,
    ValidationInfo,
    field_validator,
    model_validator,
)
from pydantic_core import PydanticCustomError

from mcp_bogor.models.common import ContractModel, RequestId

YEAR_MIN = 2000
YEAR_MAX = 2100
QUERY_MIN_CHARS = 2
QUERY_MAX_CHARS = 100
DEFAULT_PAGE_SIZE = 10
DEFAULT_SEARCH_MAX_PAGE_SIZE = 20
SEARCH_MAX_PAGE_SIZE_KEY = "search_max_page_size"
MAX_PERIODS = 20
MAX_FILTER_VALUES = 50
DEFAULT_SORT = "tahun:asc"
SORT_PATTERN = r"^[^:\s]+:(asc|desc)$"
# Bentuk slug portal (rekaman 10 Nov: 751 slug, terpanjang 202 karakter; KB-43).
SLUG_PATTERN = r"^[a-z0-9_-]+$"
SLUG_MAX_CHARS = 255
# Disetujui Akhdan 11 Nov 2026 (review 8a); sama untuk ketiga tool.
REQUEST_ID_DESCRIPTION = "Diisi oleh sistem pemanggil; biarkan kosong."

Year = Annotated[int, Field(ge=YEAR_MIN, le=YEAR_MAX)]
NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
FilterValues = Annotated[list[NonEmptyStr | int], Field(min_length=1, max_length=MAX_FILTER_VALUES)]


class ParamsModel(ContractModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def _blank_to_none(value: Any) -> Any:
    if isinstance(value, str) and not value.strip():
        return None
    return value


class SearchParams(ParamsModel):
    query: str = Field(
        min_length=QUERY_MIN_CHARS,
        max_length=QUERY_MAX_CHARS,
        description='Kata kunci topik dalam bahasa Indonesia, mis. "kemiskinan" atau "PAD".',
    )
    opd: str | None = Field(default=None, description="Slug atau nama OPD (opsional).")
    topik: str | None = Field(default=None, description="Nama topik portal (opsional).")
    tahun: Year | None = Field(
        default=None, description="Hanya dataset yang rentang tahunnya mencakup tahun ini."
    )
    page: int = Field(default=1, ge=1, description="Halaman hasil, mulai 1.")
    page_size: int = Field(
        default=DEFAULT_PAGE_SIZE, ge=1, description="Jumlah dataset per halaman."
    )
    request_id: RequestId | None = Field(default=None, description=REQUEST_ID_DESCRIPTION)

    _blank_optional = field_validator("opd", "topik", mode="before")(_blank_to_none)

    @field_validator("page_size")
    @classmethod
    def _page_size_limit(cls, value: int, info: ValidationInfo) -> int:
        # Hanya berjalan bila page_size diisi pemanggil (nilai bawaan tidak divalidasi).
        limit = (info.context or {}).get(SEARCH_MAX_PAGE_SIZE_KEY, DEFAULT_SEARCH_MAX_PAGE_SIZE)
        if value > limit:
            raise PydanticCustomError("less_than_equal", "page_size maksimal {le}", {"le": limit})
        return value

    @model_validator(mode="after")
    def _default_page_size(self, info: ValidationInfo) -> SearchParams:
        limit = (info.context or {}).get(SEARCH_MAX_PAGE_SIZE_KEY, DEFAULT_SEARCH_MAX_PAGE_SIZE)
        if "page_size" not in self.model_fields_set:
            # Nilai bawaan diturunkan bila batas yang dikonfigurasi lebih kecil (KB-12).
            self.page_size = min(self.page_size, limit)
        return self


class InfoParams(ParamsModel):
    # Slug tidak ada di katalog lokal → DATASET_NOT_FOUND di tool, tanpa memanggil portal.
    slug: str = Field(
        min_length=1,
        max_length=SLUG_MAX_CHARS,
        pattern=SLUG_PATTERN,
        description="Slug dataset dari hasil pencarian dataset.",
    )
    request_id: RequestId | None = Field(default=None, description=REQUEST_ID_DESCRIPTION)


class DataParams(ParamsModel):
    # Slug tidak ada di katalog lokal → DATASET_NOT_FOUND di tool, tanpa memanggil portal.
    slug: str = Field(
        min_length=1,
        max_length=SLUG_MAX_CHARS,
        pattern=SLUG_PATTERN,
        description="Slug dataset dari hasil pencarian dataset.",
    )
    periods: Annotated[list[Year], Field(min_length=1, max_length=MAX_PERIODS)] | None = Field(
        default=None,
        description="Tahun yang ditanyakan pengguna; kosongkan untuk semua tahun.",
    )
    # Kunci harus kolom dimension menurut kamus; diperiksa di tool (KB-39) → INVALID_PARAM
    # atau LEVEL_NOT_AVAILABLE. Kunci "tahun" dipindah ke periods.
    filters: dict[NonEmptyStr, FilterValues] | None = Field(
        default=None,
        description=(
            "Filter kolom dimensi: {nama_kolom: [nilai, ...]}, mis. "
            '{"hari_operasional": ["HARI KERJA"]}. Hanya kolom role=dimension.'
        ),
    )
    # Kolom sort harus ada dan bukan id; diperiksa di tool (KB-39) → INVALID_PARAM.
    sort: str = Field(
        default=DEFAULT_SORT,
        pattern=SORT_PATTERN,
        description="Urutan baris, format kolom:asc atau kolom:desc.",
    )
    force_refresh: bool = Field(
        default=False,
        description="Ambil ulang dari portal; dibatasi, biasanya tidak perlu.",
    )
    request_id: RequestId | None = Field(default=None, description=REQUEST_ID_DESCRIPTION)

    @field_validator("periods")
    @classmethod
    def _sorted_unique(cls, value: list[int] | None) -> list[int] | None:
        return None if value is None else sorted(set(value))

    @field_validator("filters")
    @classmethod
    def _empty_filters_to_none(cls, value: dict[str, list[str | int]] | None) -> Any:
        return value or None
