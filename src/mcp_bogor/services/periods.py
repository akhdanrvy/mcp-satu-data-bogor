"""Cakupan periode (Dok 2 bagian 6, CP-01..04; KS-04 untuk period_end). Fungsi murni.

v0.1 hanya mengenal periode tahunan (kolom `tahun`); kolom bulan di luar cakupan (KB-19).

Bentuk masukan: adaptor (Langkah 4) meratakan respons portal lebih dulu, yaitu
`metadata_filter` menjadi {nama_kolom: nilai_unik} dan `metadata[]` dataset menjadi
{nama_metadata: nilai}. Modul ini tidak bergantung pada bentuk JSON portal.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from mcp_bogor.services.normalize import is_empty

PERIOD_COLUMN = "tahun"
YEAR_MIN = 1900
YEAR_MAX = 2100
NO_TIME_DIMENSION_NOTE = "dataset tanpa dimensi waktu"
METADATA_PERIOD_START = "Dimensi Dataset Awal"
METADATA_PERIOD_END = "Dimensi Dataset Akhir"

_YEAR_TEXT = re.compile(r"^\d{4}$")


def parse_year(value: Any) -> int | None:
    """int, float bulat, atau teks empat digit dalam rentang 1900 s.d. 2100; selain itu None."""
    if isinstance(value, bool) or is_empty(value):
        return None
    year: int | None = None
    if isinstance(value, int):
        year = value
    elif isinstance(value, float) and value.is_integer():
        year = int(value)
    elif isinstance(value, str) and _YEAR_TEXT.match(value.strip()):
        year = int(value.strip())
    if year is None or not YEAR_MIN <= year <= YEAR_MAX:
        return None
    return year


def periods_available(
    unique_values: Mapping[str, Iterable[Any]], period_column: str = PERIOD_COLUMN
) -> list[int] | None:
    """CP-01: tahun unik terurut dari nilai unik kolom periode.

    None bila dataset tidak punya kolom periode (CP-03). Nilai yang bukan tahun dibuang.
    """
    if period_column not in unique_values:
        return None
    years = {parse_year(v) for v in unique_values[period_column] or ()}
    return sorted(y for y in years if y is not None)


@dataclass(frozen=True, slots=True)
class PeriodCoverage:
    periods_requested: list[int] | None
    periods_found: list[int] | None
    missing_periods: list[int] | None
    time_dimension: bool
    note: str | None = None


def compute(requested: Iterable[int] | None, available: Iterable[int] | None) -> PeriodCoverage:
    """CP-02/CP-03.

    - available None (tanpa kolom periode) → found dan missing None, dengan catatan.
    - requested None (semua periode) → found = semua yang tersedia, missing kosong.
    - selain itu found = irisan, missing = sisanya; keduanya terurut.
    """
    req = None if requested is None else sorted(set(requested))
    if available is None:
        return PeriodCoverage(req, None, None, time_dimension=False, note=NO_TIME_DIMENSION_NOTE)
    avail = set(available)
    if req is None:
        return PeriodCoverage(None, sorted(avail), [], time_dimension=True)
    return PeriodCoverage(
        req,
        [p for p in req if p in avail],
        [p for p in req if p not in avail],
        time_dimension=True,
    )


def period_bounds(metadata: Mapping[str, Any]) -> tuple[int | None, int | None]:
    """(period_start, period_end) dari metadata "Dimensi Dataset Awal/Akhir" (KS-04).

    Nilai kosong, "None" (NB-06), atau bukan tahun → None.
    """
    return parse_year(metadata.get(METADATA_PERIOD_START)), parse_year(
        metadata.get(METADATA_PERIOD_END)
    )
