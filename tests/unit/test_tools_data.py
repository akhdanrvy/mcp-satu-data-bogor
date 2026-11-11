"""Tool get_dataset_data (Langkah 8c): status 2.2, validasi KB-39, seri KB-37, jumlah
permintaan (tabel 1.3), UT-13, UT-14. Tanpa jaringan: FakeSourceAdapter + rekaman."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from mcp_bogor.adapters.base import (
    InvalidSourceRequest,
    SourceNotFound,
    SourceProtocolError,
    SourceRateLimited,
    SourceUnavailable,
)
from mcp_bogor.services.budget import estimate_tokens
from mcp_bogor.tools.data import get_dataset_data
from tests.tool_support import ToolEnv

KEMISKINAN = "tingkat-kemiskinan-di-kota-bogor-2"
PAD = "realisasi-pendapatan-asli-daerah-pad-kota-bogor"
SPEED = (
    "kecepatan-lalu-lintas-segmen-1-dan-2-berdasarkan-hari-operasional-di-jalan-raya-pajajaran-"
    "kota-bogor"
)
KUNJUNGAN = "perkembangan-jumlah-kunjungan-wisatawan-di-kota-bogor"


@pytest.fixture
def env(catalog_db: Path, tmp_path: Path) -> ToolEnv:
    return ToolEnv(catalog_db, tmp_path)


def _code(out: dict[str, Any]) -> str | None:
    return out["error"]["code"] if out["error"] else None


# ---------------------------------------------------------------- status (bagian 2.2)


def test_partial_when_some_periods_missing(env: ToolEnv) -> None:
    out = env.data(slug=KEMISKINAN, periods=[2025, 2026], request_id="rq-f07")
    assert out["status"] == "partial" and out["error"] is None and out["truncated"] is False
    result = out["result"]
    assert (result["periods_requested"], result["periods_found"], result["missing_periods"]) == (
        [2025, 2026],
        [2025],
        [2026],
    )
    assert result["filters_applied"] == {"tahun": [2025, 2026]}
    assert result["data_ref"]["row_count"] == 1 and result["dictionary_validated"] is True
    assert out["provenance"]["served_from"] == "live"
    assert (out["provenance"]["period_start"], out["provenance"]["period_end"]) == (2025, 2025)


def test_period_not_available_with_latest(env: ToolEnv) -> None:
    out = env.data(slug=KEMISKINAN, periods=[2026])
    assert _code(out) == "PERIOD_NOT_AVAILABLE" and out["result"] is None
    assert out["error"]["message"] == (
        "Periode 2026 tidak tersedia di dataset 'tingkat-kemiskinan-di-kota-bogor-2'. "
        "Tahun terakhir yang tersedia: 2025."
    )


def test_ok_all_periods_and_preview_dimensions_only(env: ToolEnv) -> None:
    out = env.data(slug=SPEED)
    assert out["status"] == "ok"
    result = out["result"]
    assert result["periods_requested"] is None and result["missing_periods"] == []
    assert result["periods_found"][0] == 2015 and result["periods_found"][-1] == 2025
    dims = {c["name"] for c in result["columns"] if c["role"] == "dimension"}
    assert "id" not in {c["name"] for c in result["columns"]}
    assert len(result["preview"]) == 5
    assert all(set(row) == dims for row in result["preview"])
    assert all("kecepatan" not in row for row in result["preview"])
    # QC atas seri penuh: 2024 nol di semua segmen; dilaporkan karena semua periode diminta
    assert [(f["code"], f["period"]) for f in out["quality_flags"]] == [
        ("suspect_zero_period", 2024)
    ]


def test_flags_only_for_requested_periods(env: ToolEnv) -> None:
    assert env.data(slug=SPEED, periods=[2025])["quality_flags"] == []
    flagged = env.data(slug=SPEED, periods=[2024, 2025])["quality_flags"]
    assert [(f["code"], f["period"]) for f in flagged] == [("suspect_zero_period", 2024)]


def test_dictionary_not_validated_reaches_data(env: ToolEnv) -> None:
    out = env.data(slug=PAD, periods=[2024])
    assert out["status"] == "ok" and out["result"]["dictionary_validated"] is False
    assert [(n["code"], n["column"]) for n in out["notices"]] == [
        ("DICTIONARY_NOT_VALIDATED", "total_pendapatan")
    ]


def test_text_numbers_become_measure_and_never_preview(env: ToolEnv) -> None:
    out = env.data(slug=KUNJUNGAN, periods=[2024])
    roles = {c["name"]: c["role"] for c in out["result"]["columns"]}
    assert roles["perkembangan_kunjungan"] == "measure"
    assert all("perkembangan_kunjungan" not in row for row in out["result"]["preview"])


# ---------------------------------------------------------------- validasi KB-39 / KK-A1


def test_filter_tahun_moves_to_periods(env: ToolEnv) -> None:
    out = env.data(
        slug=SPEED, filters={"tahun": ["2023", 2025], "hari_operasional": ["HARI KERJA"]}
    )
    assert out["result"]["periods_requested"] == [2023, 2025]
    assert out["result"]["filters_applied"] == {
        "tahun": [2023, 2025],
        "hari_operasional": ["HARI KERJA"],
    }
    both = env.data(slug=SPEED, periods=[2025], filters={"tahun": [2024]})
    assert _code(both) == "INVALID_PARAM" and "'filters'" in both["error"]["message"]
    bad = env.data(slug=SPEED, filters={"tahun": ["dua ribu"]})
    assert _code(bad) == "INVALID_PARAM" and "Kolom: 'tahun'" in bad["error"]["message"]


def test_filter_value_matched_with_nb08(env: ToolEnv) -> None:
    out = env.data(slug=SPEED, periods=[2025], filters={"hari_operasional": ["hari  kerja"]})
    assert out["status"] == "ok"
    assert out["result"]["filters_applied"]["hari_operasional"] == ["HARI KERJA"]
    assert {row["hari_operasional"] for row in out["result"]["preview"]} == {"HARI KERJA"}
    assert out["result"]["data_ref"]["row_count"] == 4


@pytest.mark.parametrize(
    ("filters", "column"),
    [
        ({"hari_operasional": ["HARI RAYA"]}, "hari_operasional"),  # nilai tak dikenal
        ({"kecepatan": [40]}, "kecepatan"),  # kolom measure
        ({"id": [1]}, "id"),  # kolom id
        ({"jenis_kendaraan": ["x"]}, "jenis_kendaraan"),  # kolom tidak ada
    ],
)
def test_invalid_filters(env: ToolEnv, filters: dict[str, Any], column: str) -> None:
    out = env.data(slug=SPEED, filters=filters)
    assert _code(out) == "INVALID_PARAM"
    assert out["error"]["message"] == f"Parameter 'filters' tidak valid. Kolom: '{column}'."


def test_level_not_available_only_for_city_level(env: ToolEnv) -> None:
    out = env.data(slug=KEMISKINAN, filters={"kemendagri_nama_kecamatan": ["BOGOR BARAT"]})
    assert _code(out) == "LEVEL_NOT_AVAILABLE"
    assert "'kemendagri_nama_kecamatan'" in out["error"]["message"]
    odd = env.data(slug=KEMISKINAN, filters={"nama kecamatan": ["x"]})  # bukan token kolom
    assert _code(odd) == "INVALID_PARAM" and "Kolom:" not in odd["error"]["message"]


def test_sort_rules(env: ToolEnv) -> None:
    good = env.data(slug=SPEED, periods=[2025], sort="kecepatan:desc")
    assert good["status"] == "ok"
    missing = env.data(slug=SPEED, sort="kolom_lain:asc")
    assert _code(missing) == "INVALID_PARAM" and "Parameter 'sort'" in missing["error"]["message"]
    by_id = env.data(slug=SPEED, sort="id:asc")
    assert _code(by_id) == "INVALID_PARAM"


def test_param_errors_never_touch_portal(env: ToolEnv) -> None:
    assert _code(env.data(slug="tidak-ada-di-katalog")) == "DATASET_NOT_FOUND"
    assert _code(env.data(slug=KEMISKINAN, periods=[1999])) == "INVALID_PARAM"
    assert _code(env.data(slug=KEMISKINAN, where={"tahun": [2025]})) == "INVALID_PARAM"
    assert env.adapter.calls == []


# ---------------------------------------------------------------- jumlah permintaan (tabel 1.3)


def test_request_counts_local_route(env: ToolEnv) -> None:
    env.info(slug=SPEED)
    assert env.adapter.calls == ["probe"]  # info pertama: 1
    env.data(slug=SPEED, periods=[2025])
    assert env.adapter.calls == ["probe", "query_rows"]  # data pertama: 1 per sort
    env.data(slug=SPEED, periods=[2020, 2021])
    env.data(slug=SPEED, filters={"hari_operasional": ["HARI LIBUR"]})  # filter lain: 0
    env.data(slug=SPEED)
    assert env.adapter.calls == ["probe", "query_rows"]
    env.data(slug=SPEED, sort="kecepatan:desc")  # sort lain: 1
    assert env.adapter.calls == ["probe", "query_rows", "query_rows"]


def test_request_counts_without_info_first(env: ToolEnv) -> None:
    env.data(slug=KEMISKINAN)
    assert env.adapter.calls == ["probe", "query_rows"]  # kamus + seri


def test_force_refresh_throttled_then_allowed(env: ToolEnv) -> None:
    env.data(slug=KEMISKINAN)
    env.clock.now += timedelta(seconds=299)
    throttled = env.data(slug=KEMISKINAN, force_refresh=True)
    assert [n["code"] for n in throttled["notices"]] == ["REFRESH_THROTTLED"]
    assert throttled["provenance"]["served_from"] == "cache"
    assert env.adapter.calls.count("query_rows") == 1
    env.clock.now += timedelta(seconds=1)
    fresh = env.data(slug=KEMISKINAN, force_refresh=True)
    assert fresh["provenance"]["served_from"] == "live" and fresh["notices"] == []
    assert env.adapter.calls.count("query_rows") == 2


def test_portal_route_above_local_threshold(catalog_db: Path, tmp_path: Path) -> None:
    env = ToolEnv(catalog_db, tmp_path, series_local_max_rows=10)
    out = env.data(slug=SPEED, periods=[2025], filters={"hari_operasional": ["HARI KERJA"]})
    assert out["status"] == "ok" and out["result"]["data_ref"]["row_count"] == 4
    assert env.adapter.calls == [
        "probe",
        "query_rows",
    ]  # row_count kamus (88) > 10: langsung portal
    env.data(slug=SPEED, filters={"hari_operasional": ["HARI LIBUR"]})
    assert env.adapter.calls == ["probe", "query_rows", "query_rows"]  # filter lain: 1


def test_local_route_switches_when_first_page_too_big(catalog_db: Path, tmp_path: Path) -> None:
    env = ToolEnv(catalog_db, tmp_path, series_local_max_rows=10)
    from mcp_bogor.services.info import InfoBundle

    env.info(slug=SPEED)
    path = env.deps.require_dictionary_cache().path_for(SPEED)
    import json

    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["payload"]["row_count"] = None  # kamus tanpa row_count → coba jalur lokal dulu
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert InfoBundle.from_dict(raw["payload"]).row_count is None
    out = env.data(slug=SPEED, filters={"hari_operasional": ["HARI KERJA"]})
    assert out["status"] == "ok"
    assert env.adapter.calls == ["probe", "query_rows", "query_rows"]


def test_cache_max_rows_is_response_too_large(catalog_db: Path, tmp_path: Path) -> None:
    env = ToolEnv(catalog_db, tmp_path, cache_max_rows=50)
    out = env.data(slug=SPEED)
    assert _code(out) == "RESPONSE_TOO_LARGE" and "Batas: 50." in out["error"]["message"]


def test_multi_page_series(
    catalog_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mcp_bogor.tools import data as data_mod

    monkeypatch.setattr(data_mod, "SERIES_PER_PAGE", 30)
    env = ToolEnv(catalog_db, tmp_path)
    out = env.data(slug=SPEED)
    assert out["result"]["data_ref"]["row_count"] == 88
    assert env.adapter.calls == ["probe", "query_rows", "query_rows", "query_rows"]


# ---------------------------------------------------------------- UT-14: portal gagal


def test_stale_series_when_portal_fails(env: ToolEnv) -> None:
    env.data(slug=KEMISKINAN)
    env.clock.now += timedelta(hours=25)
    env.adapter.fail_with = SourceUnavailable("portal mati")
    out = env.data(slug=KEMISKINAN, periods=[2025])
    assert out["status"] == "partial" and _code(out) == "STALE_DATA"
    assert out["provenance"]["served_from"] == "stale_cache"
    assert out["result"]["periods_found"] == [2025]
    env.clock.now += timedelta(hours=168)
    gone = env.data(slug=KEMISKINAN, periods=[2025])
    assert _code(gone) == "SOURCE_UNAVAILABLE" and gone["result"] is None


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (SourceUnavailable("mati"), "SOURCE_UNAVAILABLE"),
        (SourceRateLimited("429"), "RATE_LIMITED"),
        (SourceNotFound("404", path="/x"), "DATASET_NOT_FOUND"),
        (InvalidSourceRequest("ditolak"), "INVALID_PARAM"),
        (SourceProtocolError("rusak"), "INTERNAL"),
    ],
)
def test_series_errors_without_cache(env: ToolEnv, error: Exception, code: str) -> None:
    env.info(slug=KEMISKINAN)  # kamus ada; yang gagal pengambilan seri
    env.adapter.fail_with = error  # type: ignore[assignment]
    out = env.data(slug=KEMISKINAN)
    assert _code(out) == code and out["result"] is None


def test_missing_secret_is_internal(catalog_db: Path, tmp_path: Path) -> None:
    env = ToolEnv(catalog_db, tmp_path, dataref_secret="")
    assert _code(env.data(slug=KEMISKINAN)) == "INTERNAL"


# ---------------------------------------------------------------- UT-13: anggaran token


def test_truncation_steps(catalog_db: Path, tmp_path: Path) -> None:
    full = ToolEnv(catalog_db, tmp_path / "a").data(slug=KEMISKINAN)
    tokens = estimate_tokens(full)
    seen = []
    for limit in range(tokens - 1, 0, -5):
        out = ToolEnv(catalog_db, tmp_path / f"l{limit}", max_response_tokens=limit).data(
            slug=KEMISKINAN
        )
        if out["status"] == "error":
            assert _code(out) == "RESPONSE_TOO_LARGE"
            assert f"Batas: {limit}." in out["error"]["message"]
            break
        assert out["status"] == "partial" and out["truncated"] is True
        assert estimate_tokens(out) <= limit
        seen.append(out["truncated_reason"])
        # yang tidak pernah dipangkas
        assert out["result"]["data_ref"] == full["result"]["data_ref"] | {
            "url": out["result"]["data_ref"]["url"],
            "expires_at": out["result"]["data_ref"]["expires_at"],
        }
        assert out["result"]["periods_found"] == full["result"]["periods_found"]
    assert seen[0] == "preview_dipangkas"
    assert set(seen) <= {"preview_dipangkas", "arti_kolom_dipangkas", "notices_dipangkas"}


def test_single_flight_series(env: ToolEnv) -> None:
    async def both() -> list[dict[str, Any]]:
        return await asyncio.gather(
            get_dataset_data(env.deps, {"slug": SPEED, "periods": [2025]}),
            get_dataset_data(env.deps, {"slug": SPEED, "periods": [2024]}),
        )

    outs = asyncio.run(both())
    assert env.adapter.calls == ["probe", "query_rows"]
    assert [o["result"]["periods_found"] for o in outs] == [[2025], [2024]]
