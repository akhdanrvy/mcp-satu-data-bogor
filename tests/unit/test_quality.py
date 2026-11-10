"""UT-08 (QC-01) dan UT-09 (QC-02 versi KB-18) dengan snapshot golden set v0.2."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from mcp_bogor.models import contains_number
from mcp_bogor.services.quality import (
    DEFAULT_QC_RATIO,
    QualityFinding,
    check_column,
    check_quality,
    findings_for_report,
    scale_groups,
    values_by_period,
    zero_periods,
)

FIXTURE = Path(__file__).parents[1] / "fixtures" / "synthetic" / "quality_golden.json"
CASES: dict[str, Any] = {
    k: v for k, v in json.loads(FIXTURE.read_text(encoding="utf-8")).items() if k[0] != "_"
}
GOLDEN = Path(__file__).parents[2] / "golden_set" / "golden_set_v0.2.jsonl"


@pytest.mark.parametrize("case_id", list(CASES))
def test_golden_cases(case_id: str) -> None:
    case = CASES[case_id]
    rows = case["rows"]
    original = copy.deepcopy(rows)
    flags = check_quality(rows, [case["measure"]])
    got = [{"code": f.code, "rule": f.rule, "period": f.period} for f in flags]
    assert got == case["expected"]
    assert all(f.column == case["measure"] for f in flags)
    assert rows == original  # ADR-007: nilai asli tidak diubah


def test_fixture_matches_golden_snapshot() -> None:
    """Q-02, Q-03, N-03 di fixture harus sama persis dengan snapshot golden set."""
    golden = {}
    for line in GOLDEN.read_text(encoding="utf-8").splitlines():
        if line.strip():
            item = json.loads(line)
            golden[item["id"]] = item
    for case_id in ("Q-02", "Q-03", "N-03"):
        assert CASES[case_id]["rows"] == golden[case_id]["snapshot"]["values"], case_id
    q01 = golden["Q-01"]["expected"]["quality_flags"]
    assert q01 == [{"code": "suspect_zero_period", "period": 2024}]


def test_no_false_flags_on_golden_snapshots() -> None:
    """Semua snapshot golden set berformat baris-per-tahun: hanya Q-02 dan Q-03 bertanda."""
    flagged = set()
    for line in GOLDEN.read_text(encoding="utf-8").splitlines():
        item = json.loads(line)
        values = (item.get("snapshot") or {}).get("values") or []
        if not values or "tahun" not in values[0]:
            continue
        measures = [k for k in values[0] if k != "tahun"]
        if check_quality(values, measures):
            flagged.add(item["id"])
    assert flagged == {"Q-02", "Q-03"}


def test_details_have_no_digits() -> None:
    for case in CASES.values():
        for flag in check_quality(case["rows"], [case["measure"]]):
            assert flag.detail and not contains_number(flag.detail)


# ---------------------------------------------------------------- QC-01


def test_zero_period_needs_all_values_zero() -> None:
    rows = [
        {"tahun": 2023, "v": 10},
        {"tahun": 2024, "v": 0},
        {"tahun": 2024, "v": 0.0},
        {"tahun": 2024, "v": None},
        {"tahun": 2025, "v": 0},
        {"tahun": 2025, "v": 3},
    ]
    assert zero_periods(values_by_period(rows, "v")) == [2024]


def test_zero_period_excluded_from_qc02() -> None:
    rows = [{"tahun": y, "v": v} for y, v in [(2021, 40), (2022, 42), (2023, 0), (2024, 45)]]
    flags = check_column(rows, "v")
    assert [(f.code, f.period) for f in flags] == [("suspect_zero_period", 2023)]


def test_rows_without_valid_period_or_number_are_ignored() -> None:
    rows = [
        {"tahun": "abc", "v": 0},
        {"tahun": None, "v": 0},
        {"tahun": "2024", "v": "0"},
        {"tahun": 2024, "v": True},
        {"tahun": 2024, "v": float("nan")},
        {"v": 0},
    ]
    assert values_by_period(rows, "v") == {}
    assert check_quality(rows, ["v"]) == []


# ---------------------------------------------------------------- QC-02


def test_scale_groups_q02() -> None:
    aggregates = {2020: 35.91, 2021: 0.04, 2022: 0.41, 2023: 0.45, 2024: 46}
    assert scale_groups(aggregates, DEFAULT_QC_RATIO) == [[2021, 2022, 2023], [2020, 2024]]


def test_scale_groups_ignore_non_positive_and_validate_ratio() -> None:
    assert scale_groups({2020: -5, 2021: 0, 2022: 10}, 50) == [[2022]]
    with pytest.raises(ValueError, match="ratio"):
        scale_groups({2020: 1}, 1)


def test_ratio_threshold_is_inclusive_and_configurable() -> None:
    rows = [{"tahun": y, "v": v} for y, v in [(2021, 100), (2022, 2), (2023, 100)]]
    assert [f.code for f in check_column(rows, "v")] == ["suspect_outlier"]  # rasio tepat 50
    assert check_column(rows, "v", ratio=51) == []


def test_needs_three_periods() -> None:
    rows = [{"tahun": 2023, "v": 1}, {"tahun": 2024, "v": 1000}]
    assert check_column(rows, "v") == []


def test_reference_group_follows_latest_period() -> None:
    # Periode terbaru berskala kecil: kelompok besar yang ditandai, bukan sebaliknya.
    rows = [{"tahun": y, "v": v} for y, v in [(2021, 4000), (2022, 4100), (2023, 40), (2024, 41)]]
    flags = check_column(rows, "v")
    assert [(f.code, f.period) for f in flags] == [
        ("suspect_scale", 2021),
        ("suspect_scale", 2022),
    ]


def test_median_aggregate_per_period() -> None:
    # Median 2021 = 100 (bukan rata-rata, dan nilai 1 di dalamnya tidak ditandai sendiri).
    rows = [{"tahun": 2021, "v": v} for v in (1, 100, 120)] + [
        {"tahun": 2022, "v": 1},
        {"tahun": 2023, "v": 110},
    ]
    flags = check_column(rows, "v")
    assert [(f.code, f.period) for f in flags] == [("suspect_outlier", 2022)]


def test_latest_period_outlier_flags_the_rest() -> None:
    """Batas yang disadari dari KB-18: bila periode terbaru sendiri yang menyimpang,
    periode lain yang ditandai suspect_scale. Dicatat untuk revisi Dok 2 QC-02."""
    rows = [{"tahun": y, "v": v} for y, v in [(2021, 100), (2022, 110), (2023, 1)]]
    flags = check_column(rows, "v")
    assert [(f.code, f.period) for f in flags] == [
        ("suspect_scale", 2021),
        ("suspect_scale", 2022),
    ]


def test_multiple_columns_and_custom_period_column() -> None:
    rows = [
        {"th": 2021, "a": 100, "b": 0},
        {"th": 2022, "a": 1, "b": 0},
        {"th": 2023, "a": 120, "b": 5},
    ]
    flags = check_quality(rows, ["a", "b"], period_column="th")
    assert [(f.column, f.code, f.period) for f in flags] == [
        ("a", "suspect_outlier", 2022),
        ("b", "suspect_zero_period", 2021),
        ("b", "suspect_zero_period", 2022),
    ]


# ---------------------------------------------------------------- QC-04


def test_findings_for_report() -> None:
    flags = check_quality(CASES["Q-03"]["rows"], ["realisasi_pad"])
    assert findings_for_report("contoh-retribusi", flags) == [
        QualityFinding("contoh-retribusi", "suspect_outlier", "QC-02", "realisasi_pad", 2022)
    ]
