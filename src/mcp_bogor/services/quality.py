"""Pemeriksaan mutu data (Dok 2 bagian 7, QC-01..04; ADR-007). Fungsi murni.

Tandai, jangan perbaiki: fungsi di sini hanya membaca baris dan menghasilkan
QualityFlag; nilai baris tidak pernah diubah.

- Agregat per (kolom measure, periode) = median nilai non-null.
- QC-01 suspect_zero_period: semua nilai non-null satu periode = 0. Periode itu
  dikeluarkan dari QC-02.
- QC-02 (KB-18, usulan pengganti; perlu revisi Dok 2 QC-02): hanya agregat > 0, minimal
  tiga periode. Agregat diurutkan menurut besarnya lalu dipecah menjadi kelompok di
  setiap pasangan bersebelahan dengan rasio ≥ ambang (MCP_QC_RATIO, bawaan 50).
  Kelompok acuan = kelompok yang memuat periode terbaru. Kelompok lain ditandai:
  ≥ 2 periode → suspect_scale, 1 periode → suspect_outlier.
- QC-03: hasilnya QualityFlag (satu per periode per kolom) untuk field quality_flags.
- QC-04: QualityFinding untuk laporan temuan; belum disimpan di langkah ini.
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from mcp_bogor.models import QualityFlag
from mcp_bogor.services.periods import PERIOD_COLUMN, parse_year

DEFAULT_QC_RATIO = 50.0
QC02_MIN_PERIODS = 3

DETAIL_ZERO = "Seluruh nilai periode ini nol; tampilkan sebagai celah, bukan sebagai nol."
DETAIL_SCALE = (
    "Skala nilai beberapa periode berbeda jauh dari periode terbaru; "
    "kemungkinan salah skala di sumber."
)
DETAIL_OUTLIER = "Nilai periode ini menyimpang jauh dari periode lain; kemungkinan pencilan."


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def values_by_period(
    rows: Iterable[Mapping[str, Any]], column: str, period_column: str = PERIOD_COLUMN
) -> dict[int, list[float]]:
    """Nilai angka non-null kolom per periode. Baris tanpa periode sah dilewati."""
    grouped: dict[int, list[float]] = defaultdict(list)
    for row in rows:
        period = parse_year(row.get(period_column))
        value = row.get(column)
        if period is not None and _is_number(value):
            grouped[period].append(value)
    return dict(grouped)


def zero_periods(grouped: Mapping[int, list[float]]) -> list[int]:
    """QC-01: periode yang semua nilai non-null-nya nol."""
    return sorted(p for p, vals in grouped.items() if vals and all(v == 0 for v in vals))


def scale_groups(aggregates: Mapping[int, float], ratio: float) -> list[list[int]]:
    """Kelompokkan periode menurut besarnya agregat; pecah bila rasio bersebelahan ≥ ratio.

    Hanya agregat > 0 yang dipakai. Kelompok diurutkan dari nilai terkecil.
    """
    if ratio <= 1:
        raise ValueError("ratio harus > 1")
    ordered = sorted(((v, p) for p, v in aggregates.items() if v > 0))
    groups: list[list[int]] = []
    previous: float | None = None
    for value, period in ordered:
        if previous is None or value / previous >= ratio:
            groups.append([])
        groups[-1].append(period)
        previous = value
    return groups


def check_column(
    rows: Iterable[Mapping[str, Any]],
    column: str,
    *,
    period_column: str = PERIOD_COLUMN,
    ratio: float = DEFAULT_QC_RATIO,
) -> list[QualityFlag]:
    grouped = values_by_period(rows, column, period_column)
    zeros = zero_periods(grouped)
    flags = [
        QualityFlag(
            code="suspect_zero_period", rule="QC-01", column=column, period=p, detail=DETAIL_ZERO
        )
        for p in zeros
    ]

    aggregates = {
        p: statistics.median(vals) for p, vals in grouped.items() if vals and p not in zeros
    }
    groups = scale_groups(aggregates, ratio)
    considered = [p for g in groups for p in g]
    if len(considered) >= QC02_MIN_PERIODS and len(groups) > 1:
        latest = max(considered)
        for group in groups:
            if latest in group:
                continue
            scale = len(group) >= 2
            flags.extend(
                QualityFlag(
                    code="suspect_scale" if scale else "suspect_outlier",
                    rule="QC-02",
                    column=column,
                    period=p,
                    detail=DETAIL_SCALE if scale else DETAIL_OUTLIER,
                )
                for p in group
            )
    return sorted(flags, key=lambda f: (f.period or 0, f.code))


def check_quality(
    rows: Iterable[Mapping[str, Any]],
    measure_columns: Iterable[str],
    *,
    period_column: str = PERIOD_COLUMN,
    ratio: float = DEFAULT_QC_RATIO,
) -> list[QualityFlag]:
    """QC-01..03 untuk semua kolom measure. Tanpa kolom periode → tidak ada tanda."""
    materialized = list(rows)
    flags: list[QualityFlag] = []
    for column in measure_columns:
        flags.extend(check_column(materialized, column, period_column=period_column, ratio=ratio))
    return flags


# ---------------------------------------------------------------- QC-04


@dataclass(frozen=True, slots=True)
class QualityFinding:
    """Record temuan mutu untuk laporan ke Walidata/OPD. Tanpa nilai baris data."""

    slug: str
    code: str
    rule: str
    column: str | None
    period: int | None


def findings_for_report(slug: str, flags: Iterable[QualityFlag]) -> list[QualityFinding]:
    return [QualityFinding(slug, f.code, f.rule, f.column, f.period) for f in flags]
