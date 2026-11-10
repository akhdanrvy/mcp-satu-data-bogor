"""Kode galat kontrak (Dok 4 bagian 7 + KK-A1), tabelnya, dan templat pesannya.

ERROR_SPECS adalah satu-satunya sumber kebenaran untuk retryable dan tool yang boleh
memakai tiap kode; tes KT-04 membandingkan tabel ini dengan Dok 4.

Pesan galat (KB-10 revisi) tidak pernah teks bebas: setiap kode punya templat tetap
dengan placeholder bertipe (slug, param, limit, periods, column). Pesan boleh memuat
tahun, batas, dan slug, tetapi tidak pernah nilai dari baris data.
"""

from __future__ import annotations

import string
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Annotated, Literal, get_args

from pydantic import Field, field_validator, model_validator

from mcp_bogor.models.common import ContractModel, PlaceholderStr, contains_formatted_number
from mcp_bogor.models.params import MAX_PERIODS, DataParams, InfoParams, SearchParams, Year

ToolName = Literal["search_datasets", "get_dataset_info", "get_dataset_data"]
"""Nama dasar tool, tanpa awalan MCP_TOOL_PREFIX."""

ALL_TOOLS: frozenset[str] = frozenset(get_args(ToolName))
_INFO_DATA: frozenset[str] = frozenset({"get_dataset_info", "get_dataset_data"})

PARAM_NAMES: frozenset[str] = frozenset(
    {*SearchParams.model_fields, *InfoParams.model_fields, *DataParams.model_fields}
)


class ErrorCode(StrEnum):
    INVALID_PARAM = "INVALID_PARAM"
    DATASET_NOT_FOUND = "DATASET_NOT_FOUND"
    NO_MATCH = "NO_MATCH"
    PERIOD_NOT_AVAILABLE = "PERIOD_NOT_AVAILABLE"
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
    STALE_DATA = "STALE_DATA"
    RATE_LIMITED = "RATE_LIMITED"
    RESPONSE_TOO_LARGE = "RESPONSE_TOO_LARGE"
    INTERNAL = "INTERNAL"
    LEVEL_NOT_AVAILABLE = "LEVEL_NOT_AVAILABLE"  # KK-A1


@dataclass(frozen=True, slots=True)
class ErrorSpec:
    retryable: bool
    tools: frozenset[str]


ERROR_SPECS: MappingProxyType[ErrorCode, ErrorSpec] = MappingProxyType(
    {
        ErrorCode.INVALID_PARAM: ErrorSpec(False, ALL_TOOLS),
        ErrorCode.DATASET_NOT_FOUND: ErrorSpec(False, _INFO_DATA),
        ErrorCode.NO_MATCH: ErrorSpec(False, frozenset({"search_datasets"})),
        ErrorCode.PERIOD_NOT_AVAILABLE: ErrorSpec(False, frozenset({"get_dataset_data"})),
        ErrorCode.SOURCE_UNAVAILABLE: ErrorSpec(True, _INFO_DATA),
        ErrorCode.STALE_DATA: ErrorSpec(False, _INFO_DATA),
        ErrorCode.RATE_LIMITED: ErrorSpec(True, _INFO_DATA),
        ErrorCode.RESPONSE_TOO_LARGE: ErrorSpec(False, ALL_TOOLS),
        ErrorCode.INTERNAL: ErrorSpec(True, ALL_TOOLS),
        ErrorCode.LEVEL_NOT_AVAILABLE: ErrorSpec(False, frozenset({"get_dataset_data"})),
    }
)


def is_code_allowed(code: ErrorCode, tool: str) -> bool:
    return tool in ERROR_SPECS[code].tools


# ---------------------------------------------------------------- templat pesan


class ErrorArgs(ContractModel):
    """Argumen bertipe untuk templat pesan galat."""

    slug: PlaceholderStr | None = None
    param: str | None = None
    limit: int | None = Field(default=None, ge=0)
    periods: Annotated[list[Year], Field(min_length=1, max_length=MAX_PERIODS)] | None = None
    column: PlaceholderStr | None = None

    @field_validator("param")
    @classmethod
    def _known_param(cls, value: str | None) -> str | None:
        if value is not None and value not in PARAM_NAMES:
            raise ValueError(f"param harus salah satu parameter tool: {sorted(PARAM_NAMES)}")
        return value

    def formatted(self) -> dict[str, str]:
        values = self.model_dump(exclude_none=True)
        if "periods" in values:
            values["periods"] = ", ".join(str(p) for p in values["periods"])
        return {k: str(v) for k, v in values.items()}


@dataclass(frozen=True, slots=True)
class MessageTemplate:
    text: str
    optional: frozenset[str] = frozenset()

    @property
    def required(self) -> frozenset[str]:
        return frozenset(f for _, f, _, _ in string.Formatter().parse(self.text) if f)


# Klausa tambahan untuk placeholder opsional, dalam urutan tetap.
OPTIONAL_CLAUSES: dict[str, str] = {
    "column": " Kolom: '{column}'.",
    "limit": " Batas: {limit}.",
    "periods": " Periode: {periods}.",
    "slug": " Dataset: '{slug}'.",
}

MESSAGE_TEMPLATES: MappingProxyType[ErrorCode, MessageTemplate] = MappingProxyType(
    {
        ErrorCode.INVALID_PARAM: MessageTemplate(
            "Parameter '{param}' tidak valid.", frozenset({"column", "limit"})
        ),
        ErrorCode.DATASET_NOT_FOUND: MessageTemplate(
            "Dataset '{slug}' tidak ada di katalog Satu Data Kota Bogor."
        ),
        ErrorCode.NO_MATCH: MessageTemplate(
            "Tidak ada dataset yang cocok di portal Satu Data Kota Bogor."
        ),
        ErrorCode.PERIOD_NOT_AVAILABLE: MessageTemplate(
            "Periode {periods} tidak tersedia di dataset '{slug}'."
        ),
        ErrorCode.SOURCE_UNAVAILABLE: MessageTemplate(
            "Portal Satu Data sedang tidak bisa dihubungi dan belum ada cache.",
            frozenset({"slug"}),
        ),
        ErrorCode.STALE_DATA: MessageTemplate(
            "Portal Satu Data sedang tidak bisa dihubungi; data disajikan dari cache lama."
        ),
        ErrorCode.RATE_LIMITED: MessageTemplate(
            "Portal Satu Data membatasi permintaan; coba lagi nanti.", frozenset({"slug"})
        ),
        ErrorCode.RESPONSE_TOO_LARGE: MessageTemplate(
            "Respons melebihi batas ukuran walau sudah dipangkas; persempit filters atau periods.",
            frozenset({"limit"}),
        ),
        ErrorCode.INTERNAL: MessageTemplate(
            "Terjadi galat internal di MCP; catat request_id untuk tim MCP."
        ),
        ErrorCode.LEVEL_NOT_AVAILABLE: MessageTemplate(
            "Dataset '{slug}' tidak menyajikan data per wilayah; kolom '{column}' tidak ada."
        ),
    }
)


def render_message(code: ErrorCode, args: ErrorArgs) -> str:
    """Bentuk pesan dari templat; placeholder wajib harus ada, yang tak dikenal ditolak."""
    template = MESSAGE_TEMPLATES[code]
    values = args.formatted()
    provided = set(values)
    missing = template.required - provided
    if missing:
        raise ValueError(f"templat {code} butuh argumen {sorted(missing)}")
    unexpected = provided - template.required - template.optional
    if unexpected:
        raise ValueError(f"templat {code} tidak memakai argumen {sorted(unexpected)}")
    clauses = "".join(
        clause.format(**values)
        for name, clause in OPTIONAL_CLAUSES.items()
        if name in provided and name in template.optional
    )
    return template.text.format(**values) + clauses


class ToolError(ContractModel):
    """Isi field `error` di amplop. Dibangun lewat from_template, bukan teks bebas."""

    code: ErrorCode
    message: str = Field(min_length=1)
    retryable: bool

    @field_validator("message")
    @classmethod
    def _no_formatted_number(cls, value: str) -> str:
        # Pengaman lapis kedua: angka berformat Indonesia hampir pasti nilai data.
        if contains_formatted_number(value):
            raise ValueError("pesan galat tidak boleh memuat angka berformat (FR-10)")
        return value

    @model_validator(mode="after")
    def _retryable_matches_table(self) -> ToolError:
        expected = ERROR_SPECS[self.code].retryable
        if self.retryable is not expected:
            raise ValueError(f"retryable untuk {self.code} harus {expected}")
        return self

    @classmethod
    def from_template(
        cls,
        code: ErrorCode,
        *,
        slug: str | None = None,
        param: str | None = None,
        limit: int | None = None,
        periods: list[int] | None = None,
        column: str | None = None,
    ) -> ToolError:
        args = ErrorArgs(slug=slug, param=param, limit=limit, periods=periods, column=column)
        return cls(
            code=code,
            message=render_message(code, args),
            retryable=ERROR_SPECS[code].retryable,
        )
