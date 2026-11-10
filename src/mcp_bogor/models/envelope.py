"""Amplop respons bersama ketiga tool (Dok 4 bagian 2) beserta bagian-bagiannya.

Konstruktor dibuat sebagai fungsi modul `ok()`, `partial()`, `error()`, bukan
classmethod, karena nama `error` sudah dipakai sebagai field amplop.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Generic, Literal, TypeVar

from pydantic import Field, model_validator

from mcp_bogor.config import CONTRACT_VERSION
from mcp_bogor.models.common import ContractModel, NoNumberText, RequestId, WibDatetime
from mcp_bogor.models.errors import ErrorCode, ToolError, is_code_allowed

ResultT = TypeVar("ResultT")

ServedFrom = Literal["live", "cache", "stale_cache"]
QualityCode = Literal["suspect_zero_period", "suspect_scale", "suspect_outlier"]
QualityRule = Literal["QC-01", "QC-02"]

# KK-E: pasangan kode → aturan Dok 2 bagian 7.
QUALITY_RULE_OF: dict[str, str] = {
    "suspect_zero_period": "QC-01",
    "suspect_scale": "QC-02",
    "suspect_outlier": "QC-02",
}


class Provenance(ContractModel):
    """Asal data (FR-06, KS-01..05, KK-B)."""

    source_url: str = Field(min_length=1)
    opd: str | None = None
    data_updated_at: WibDatetime | None = None
    freshness_source: Literal["approve", "mdate"] | None = None
    metadata_changed_after_approve: bool = False
    change_notes: str | None = None
    retrieved_at: WibDatetime
    period_start: int | None = None
    period_end: int | None = None
    served_from: ServedFrom

    @model_validator(mode="after")
    def _check(self) -> Provenance:
        if (self.data_updated_at is None) != (self.freshness_source is None):
            raise ValueError("data_updated_at dan freshness_source harus sama-sama terisi/null")
        if self.metadata_changed_after_approve and self.freshness_source != "approve":
            raise ValueError("metadata_changed_after_approve hanya bermakna bila sumber approve")
        if (
            self.period_start is not None
            and self.period_end is not None
            and self.period_start > self.period_end
        ):
            raise ValueError("period_start tidak boleh setelah period_end")
        return self


class Officialness(ContractModel):
    """Keresmian (FR-07). basis boleh null di ringkasan item search_datasets."""

    status: Literal["resmi", "draf"]
    basis: str | None = None


class QualityFlag(ContractModel):
    """Tanda mutu (ADR-007, KK-E). Nilai data asli tidak pernah ikut di sini."""

    code: QualityCode
    rule: QualityRule
    column: str | None = None
    period: int | None = None
    detail: NoNumberText | None = None

    @model_validator(mode="after")
    def _rule_matches_code(self) -> QualityFlag:
        if QUALITY_RULE_OF[self.code] != self.rule:
            raise ValueError(f"{self.code} harus memakai aturan {QUALITY_RULE_OF[self.code]}")
        return self


class ToolEnvelope(ContractModel, Generic[ResultT]):
    """Amplop respons. Invarian dijaga validator; lihat _check_invariants."""

    contract_version: Literal["0.1"] = CONTRACT_VERSION
    status: Literal["ok", "partial", "error"]
    error: ToolError | None = None
    request_id: RequestId | None = None
    provenance: Provenance | None = None
    officialness: Officialness | None = None
    quality_flags: list[QualityFlag] = Field(default_factory=list)
    truncated: bool = False
    truncated_reason: str | None = Field(default=None, min_length=1)
    result: ResultT | None = None

    @model_validator(mode="after")
    def _check_invariants(self) -> ToolEnvelope[ResultT]:
        code = self.error.code if self.error is not None else None
        stale = code is ErrorCode.STALE_DATA

        if self.status == "error":
            if self.error is None:
                raise ValueError("status=error wajib disertai error")
            if self.result is not None:
                raise ValueError("status=error wajib result=null")
            if stale:
                raise ValueError("STALE_DATA dikirim dengan status=partial, bukan error")
        else:
            if self.result is None:
                raise ValueError(f"status={self.status} wajib membawa result")
            if self.status == "ok" and self.error is not None:
                raise ValueError("status=ok wajib error=null")
            if self.status == "partial" and self.error is not None and not stale:
                raise ValueError("status=partial hanya boleh membawa error STALE_DATA")
            if self.status == "ok" and self.truncated:
                raise ValueError("respons terpotong harus status=partial")

        served_stale = self.provenance is not None and self.provenance.served_from == "stale_cache"
        if stale != served_stale:
            raise ValueError("STALE_DATA dan served_from=stale_cache harus selalu berpasangan")

        if self.truncated and self.truncated_reason is None:
            raise ValueError("truncated=true wajib disertai truncated_reason")
        if not self.truncated and self.truncated_reason is not None:
            raise ValueError("truncated_reason hanya diisi bila truncated=true")
        return self


def _envelope_cls(result: Any) -> type[ToolEnvelope[Any]]:
    return ToolEnvelope[type(result)]  # type: ignore[misc]


def ok(
    result: ResultT,
    *,
    request_id: str | None = None,
    provenance: Provenance | None = None,
    officialness: Officialness | None = None,
    quality_flags: Iterable[QualityFlag] = (),
) -> ToolEnvelope[ResultT]:
    return _envelope_cls(result)(
        status="ok",
        result=result,
        request_id=request_id,
        provenance=provenance,
        officialness=officialness,
        quality_flags=list(quality_flags),
    )


def partial(
    result: ResultT,
    *,
    request_id: str | None = None,
    provenance: Provenance | None = None,
    officialness: Officialness | None = None,
    quality_flags: Iterable[QualityFlag] = (),
    truncated_reason: str | None = None,
    stale_message: str | None = None,
) -> ToolEnvelope[ResultT]:
    """Hasil sebagian: terpotong (truncated_reason), dari cache usang (stale_message
    → error STALE_DATA, provenance.served_from wajib stale_cache), atau sebagian periode
    kosong (cukup result.missing_periods)."""
    return _envelope_cls(result)(
        status="partial",
        result=result,
        error=(
            ToolError.from_code(ErrorCode.STALE_DATA, stale_message)
            if stale_message is not None
            else None
        ),
        request_id=request_id,
        provenance=provenance,
        officialness=officialness,
        quality_flags=list(quality_flags),
        truncated=truncated_reason is not None,
        truncated_reason=truncated_reason,
    )


def error(
    code: ErrorCode,
    message: str,
    *,
    request_id: str | None = None,
    tool: str | None = None,
    result_type: type[ResultT] | None = None,
) -> ToolEnvelope[ResultT]:
    """Respons galat: result=null dan angka di pesan disamarkan (FR-10).

    Bila `tool` diberikan, kode diperiksa terhadap ERROR_SPECS.
    """
    if code is ErrorCode.STALE_DATA:
        raise ValueError("STALE_DATA bukan galat penuh; pakai partial(stale_message=...)")
    if tool is not None and not is_code_allowed(code, tool):
        raise ValueError(f"kode {code} tidak dipakai oleh tool {tool}")
    cls: type[ToolEnvelope[Any]] = (
        ToolEnvelope if result_type is None else ToolEnvelope[result_type]
    )
    return cls(status="error", error=ToolError.from_code(code, message), request_id=request_id)
