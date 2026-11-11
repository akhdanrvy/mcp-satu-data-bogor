"""Golden set v0.2 di level tool (RENCANA_LANGKAH8 bagian 8).

Status golden set adalah status JAWABAN X-Assistant, jadi dipetakan ke hasil tool yang
diharapkan. Tiga golongan:
- LULUS: tool dipanggil dengan parameter yang diharapkan (FakeSourceAdapter + rekaman), lalu
  status/kode, periods_found/missing_periods, dan quality_flags dicocokkan.
- DICATAT: search dengan kata kunci pertanyaan; amplop harus sah dan tanpa angka berformat,
  tetapi hasilnya (NO_MATCH atau tidak) tidak di-assert karena keputusan "tidak tersedia"
  ada di LLM (dievaluasi di ST/evaluasi golden set).
- SKIP: perilaku LLM murni (klarifikasi, penolakan data perorangan, prakiraan).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import timedelta
from functools import cache
from pathlib import Path
from typing import Any

import pytest

from mcp_bogor.adapters.base import SourceUnavailable
from mcp_bogor.models.common import contains_formatted_number
from tests.tool_support import ToolEnv

GOLDEN = Path(__file__).parents[2] / "golden_set" / "golden_set_v0.2.jsonl"


@cache
def golden() -> dict[str, dict[str, Any]]:
    cases = {}
    for line in GOLDEN.read_text(encoding="utf-8").splitlines():
        if line.strip():
            case = json.loads(line)
            cases[case["id"]] = case
    return cases


def _expected(case_id: str) -> dict[str, Any]:
    return golden()[case_id]["expected"]


def _filters(case_id: str) -> dict[str, list[Any]]:
    raw = _expected(case_id).get("filters") or {}
    return {k: v if isinstance(v, list) else [v] for k, v in raw.items()}


def _flags(out: dict[str, Any]) -> list[tuple[str, int]]:
    return [(f["code"], f["period"]) for f in out["quality_flags"]]


def _expected_flags(case_id: str) -> list[tuple[str, int]]:
    flags = []
    for flag in _expected(case_id).get("quality_flags", []):
        for period in flag.get("periods", [flag.get("period")]):
            flags.append((flag["code"], period))
    return sorted(flags, key=lambda f: (f[1], f[0]))


def _measure_is_measure(env: ToolEnv, case_id: str) -> None:
    expected = _expected(case_id)
    info = env.info(slug=expected["dataset"])
    roles = {c["name"]: c["role"] for c in info["result"]["columns"]}
    assert roles[expected["measure"]] == "measure"


# ---------------------------------------------------------------- kasus LULUS


def n01(env: ToolEnv) -> None:
    exp = _expected("N-01")
    out = env.data(slug=exp["dataset"], periods=exp["periods_requested"])
    assert out["error"]["code"] == "PERIOD_NOT_AVAILABLE"
    assert f"Tahun terakhir yang tersedia: {exp['answer_period']}." in out["error"]["message"]
    info = env.info(slug=exp["dataset"])
    assert exp["answer_period"] in info["result"]["periods_available"]
    _measure_is_measure(env, "N-01")


def _ok_with_periods(case_id: str) -> Callable[[ToolEnv], None]:
    def run(env: ToolEnv) -> None:
        exp = _expected(case_id)
        filters = _filters(case_id)
        periods = exp.get("periods_requested")
        if "tahun" in filters and periods is None:
            periods = filters.pop("tahun")
        out = env.data(slug=exp["dataset"], periods=periods, filters=filters or None)
        assert out["status"] == "ok", out["error"]
        if periods is not None:
            assert out["result"]["periods_found"] == exp["periods_found"]
        else:
            assert set(exp["periods_found"]) <= set(out["result"]["periods_found"])
        assert out["result"]["missing_periods"] == exp["missing_periods"]
        assert _flags(out) == []  # golden N-*: tanpa catatan mutu untuk periode diminta
        _measure_is_measure(env, case_id)

    return run


def n08(env: ToolEnv) -> None:
    exp = _expected("N-08")
    out = env.data(slug=exp["dataset"], filters=_filters("N-08"))  # filters.tahun → periods
    assert out["status"] == "ok"
    assert out["result"]["periods_found"] == exp["periods_found"]
    assert out["result"]["missing_periods"] == exp["missing_periods"]
    assert out["result"]["filters_applied"]["hari_operasional"] == ["HARI KERJA"]
    _measure_is_measure(env, "N-08")


def f02(env: ToolEnv) -> None:
    env.adapter.fail_with = SourceUnavailable("portal mati")
    out = env.data(slug="realisasi-belanja-modal-daerah-di-kota-bogor", periods=[2025])
    assert out["status"] == "error" and out["error"]["code"] == "SOURCE_UNAVAILABLE"
    assert not contains_formatted_number(json.dumps(out))


def f03(env: ToolEnv) -> None:
    slug = "tingkat-kemiskinan-di-kota-bogor-2"
    env.data(slug=slug)
    env.clock.now += timedelta(hours=25)
    env.adapter.fail_with = SourceUnavailable("portal mati")
    out = env.data(slug=slug)
    assert out["status"] == "partial" and out["error"]["code"] == "STALE_DATA"
    assert out["provenance"]["served_from"] == "stale_cache"


def _partial_missing(case_id: str) -> Callable[[ToolEnv], None]:
    def run(env: ToolEnv) -> None:
        exp = _expected(case_id)
        out = env.data(slug=exp["dataset"], periods=exp["periods_requested"])
        assert out["status"] == "partial" and out["error"] is None
        assert out["result"]["periods_found"] == exp["periods_found"]
        assert out["result"]["missing_periods"] == exp["missing_periods"]

    return run


def o06(env: ToolEnv) -> None:
    slug = _expected("O-06")["may_offer"][0]["dataset"]
    out = env.data(slug=slug, filters={"kecamatan": ["BOGOR BARAT"]})
    assert out["error"]["code"] == "LEVEL_NOT_AVAILABLE"
    assert not contains_formatted_number(json.dumps(out))


def _quality(case_id: str) -> Callable[[ToolEnv], None]:
    def run(env: ToolEnv) -> None:
        exp = _expected(case_id)
        out = env.data(slug=exp["dataset"], filters=_filters(case_id) or None)
        assert out["status"] == "ok"
        assert _flags(out) == _expected_flags(case_id)
        _measure_is_measure(env, case_id)

    return run


PASS: dict[str, Callable[[ToolEnv], None]] = {
    "N-01": n01,
    "N-02": _ok_with_periods("N-02"),
    "N-03": _ok_with_periods("N-03"),
    "N-04": _ok_with_periods("N-04"),
    "N-05": _ok_with_periods("N-05"),
    "N-06": _ok_with_periods("N-06"),
    "N-07": _ok_with_periods("N-07"),
    "N-08": n08,
    "F-02": f02,
    "F-03": f03,
    "F-07": _partial_missing("F-07"),
    "F-08": _partial_missing("F-08"),
    "O-06": o06,
    "Q-01": _quality("Q-01"),
    "Q-02": _quality("Q-02"),
    "Q-03": _quality("Q-03"),
}

# Kata kunci search yang dipakai model untuk kasus "tidak ada di sumber" (dicatat saja).
RECORDED: dict[str, str] = {
    "F-01": "wisatawan kebun raya",
    "O-01": "aduan warga",
    "O-02": "kemacetan ruas jalan",
    "O-03": "retribusi parkir",
    "O-04": "zona pkl",
    "O-05": "serapan anggaran opd",
}

SKIP: dict[str, str] = {
    "F-04": "penolakan data perorangan adalah perilaku LLM; MCP tidak punya data individu",
    "F-05": "permintaan ambigu → klarifikasi oleh LLM, tanpa panggilan tool",
    "F-06": "prakiraan tahun depan ditolak LLM; level tool sama dengan N-01 (PERIOD_NOT_AVAILABLE)",
    "O-07": "laporan kondisi kota mingguan → klarifikasi oleh LLM, tanpa panggilan tool",
}


def test_every_golden_case_is_classified() -> None:
    assert set(PASS) | set(RECORDED) | set(SKIP) == set(golden())
    assert not (set(PASS) & set(RECORDED)) and not (set(PASS) & set(SKIP))


@pytest.mark.parametrize("case_id", sorted(PASS))
def test_golden_tool_level(catalog_db: Path, tmp_path: Path, case_id: str) -> None:
    PASS[case_id](ToolEnv(catalog_db, tmp_path))


@pytest.mark.parametrize("case_id", sorted(RECORDED))
def test_golden_recorded_search(
    catalog_db: Path, tmp_path: Path, case_id: str, record_property: Any
) -> None:
    out = ToolEnv(catalog_db, tmp_path).search(query=RECORDED[case_id])
    assert out["status"] in {"ok", "error"}
    assert out["error"] is None or out["error"]["code"] == "NO_MATCH"
    assert not contains_formatted_number(json.dumps(out["error"] or {}))
    record_property(
        "hasil",
        out["error"]["code"] if out["error"] else f"{out['result']['total']} dataset",
    )


@pytest.mark.parametrize("case_id", sorted(SKIP))
def test_golden_llm_only(case_id: str) -> None:
    pytest.skip(SKIP[case_id])
