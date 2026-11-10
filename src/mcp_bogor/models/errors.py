"""Kode galat kontrak (Dok 4 bagian 7 + KK-A1) dan tabel tunggalnya.

ERROR_SPECS adalah satu-satunya sumber kebenaran untuk retryable dan tool yang boleh
memakai tiap kode; tes KT-04 membandingkan tabel ini dengan Dok 4.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Literal, get_args

from pydantic import model_validator

from mcp_bogor.models.common import ContractModel, NoNumberText, mask_numbers

ToolName = Literal["search_datasets", "get_dataset_info", "get_dataset_data"]
"""Nama dasar tool, tanpa awalan MCP_TOOL_PREFIX."""

ALL_TOOLS: frozenset[str] = frozenset(get_args(ToolName))
_INFO_DATA: frozenset[str] = frozenset({"get_dataset_info", "get_dataset_data"})


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


class ToolError(ContractModel):
    """Isi field `error` di amplop. Pesan tidak pernah memuat angka (FR-10)."""

    code: ErrorCode
    message: NoNumberText
    retryable: bool

    @model_validator(mode="after")
    def _retryable_matches_table(self) -> ToolError:
        expected = ERROR_SPECS[self.code].retryable
        if self.retryable is not expected:
            raise ValueError(f"retryable untuk {self.code} harus {expected}")
        return self

    @classmethod
    def from_code(cls, code: ErrorCode, message: str) -> ToolError:
        """Bangun galat dari tabel; angka di pesan disamarkan, bukan ditolak."""
        return cls(code=code, message=mask_numbers(message), retryable=ERROR_SPECS[code].retryable)
