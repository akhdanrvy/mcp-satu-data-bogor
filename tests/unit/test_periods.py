"""UT-07 (CP-01..04, FR-17) dan KS-04 (period_end dari metadata)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from mcp_bogor.models import DataResult
from mcp_bogor.services.periods import (
    NO_TIME_DIMENSION_NOTE,
    PeriodCoverage,
    compute,
    parse_year,
    period_bounds,
    periods_available,
)
from tests import model_samples as s


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (2025, 2025),
        ("2025", 2025),
        (" 2019 ", 2019),
        (2024.0, 2024),
        (1900, 1900),
        (2100, 2100),
        (1899, None),
        (2101, None),
        ("2024.0", None),
        ("Tahun 2024", None),
        (2024.5, None),
        (True, None),
        ("None", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_year(raw: object, expected: int | None) -> None:
    assert parse_year(raw) == expected


def test_periods_available_mixed_types() -> None:
    unique = {"tahun": ["2023", 2021, "2021", " 2022 ", "-", None, "abc", 2025.0], "satuan": ["%"]}
    assert periods_available(unique) == [2021, 2022, 2023, 2025]


def test_periods_available_without_time_column() -> None:
    assert periods_available({"segmen": ["A"]}) is None
    assert periods_available({"tahun": None}) == []
    assert periods_available({"periode": [2024]}, period_column="periode") == [2024]


def test_cp04_kemiskinan() -> None:
    cov = compute([2025, 2026], [2019, 2020, 2021, 2022, 2023, 2024, 2025])
    assert cov == PeriodCoverage([2025, 2026], [2025], [2026], time_dimension=True)


def test_n01_nothing_found() -> None:
    cov = compute([2026], [2024, 2025])
    assert (cov.periods_found, cov.missing_periods) == ([], [2026])


def test_requested_all_and_dedup() -> None:
    assert compute(None, [2022, 2020]) == PeriodCoverage(None, [2020, 2022], [], True)
    cov = compute([2026, 2025, 2025], [2025])
    assert cov.periods_requested == [2025, 2026]


def test_cp03_no_time_dimension() -> None:
    cov = compute([2025], None)
    assert cov == PeriodCoverage([2025], None, None, False, NO_TIME_DIMENSION_NOTE)
    assert cov.note == "dataset tanpa dimensi waktu"


def test_coverage_fits_data_result() -> None:
    base = s.data_result().model_dump()
    for cov in (compute([2025, 2026], [2025]), compute([2025], None), compute(None, None)):
        result = DataResult(
            **{
                **base,
                "periods_requested": cov.periods_requested,
                "periods_found": cov.periods_found,
                "missing_periods": cov.missing_periods,
                "time_dimension": cov.time_dimension,
                "filters_applied": {},
            }
        )
        assert result.time_dimension is cov.time_dimension


@pytest.mark.parametrize(
    "change",
    [
        {"time_dimension": False},  # found/missing masih list
        {"time_dimension": False, "periods_found": None},
        {"periods_found": None, "missing_periods": None},  # null tanpa time_dimension=false
        {"missing_periods": None},
    ],
)
def test_kk_f_validator(change: dict) -> None:
    with pytest.raises(ValidationError):
        DataResult(**{**s.data_result().model_dump(), **change})


def test_period_bounds_ks04() -> None:
    meta = {"Dimensi Dataset Awal": "2019", "Dimensi Dataset Akhir": 2025, "Satuan Dataset": "%"}
    assert period_bounds(meta) == (2019, 2025)
    assert period_bounds({"Dimensi Dataset Akhir": "None"}) == (None, None)
    assert period_bounds({}) == (None, None)
