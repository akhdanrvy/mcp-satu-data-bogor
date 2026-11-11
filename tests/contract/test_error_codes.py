"""KT-04 (kesepuluh kode galat dipicu lewat tool nyata, retryable sesuai tabel) dan KT-02
(amplop sah dengan semua field wajib di setiap jalur: ok, tiga jenis partial, setiap kode galat).

Tanpa jaringan: katalog rekaman + FakeSourceAdapter; galat portal lewat `fail_with`.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from mcp_bogor.adapters.base import SourceRateLimited, SourceUnavailable
from mcp_bogor.models import ERROR_SPECS, DataResult, ErrorCode, InfoResult, SearchResult
from mcp_bogor.models.envelope import ToolEnvelope
from tests.contract.test_envelope import ENVELOPE_FIELD_TYPES
from tests.tool_support import ToolEnv

KEMISKINAN = "tingkat-kemiskinan-di-kota-bogor-2"
BELANJA = "realisasi-belanja-modal-daerah-di-kota-bogor"


def _stale(env: ToolEnv) -> dict[str, Any]:
    env.data(slug=KEMISKINAN)
    env.clock.now += timedelta(hours=25)
    env.adapter.fail_with = SourceUnavailable("portal mati")
    return env.data(slug=KEMISKINAN)


def _failing(error: Exception) -> Callable[[ToolEnv], dict[str, Any]]:
    def run(env: ToolEnv) -> dict[str, Any]:
        env.adapter.fail_with = error  # type: ignore[assignment]
        return env.data(slug=BELANJA, periods=[2025])  # golden F-02

    return run


TRIGGERS: dict[ErrorCode, tuple[str, Callable[[ToolEnv], dict[str, Any]]]] = {
    ErrorCode.INVALID_PARAM: ("data", lambda e: e.data(slug=KEMISKINAN, filters={"x": ["y"]})),
    ErrorCode.DATASET_NOT_FOUND: ("info", lambda e: e.info(slug="tidak-ada")),
    ErrorCode.NO_MATCH: ("search", lambda e: e.search(query="zzqxw tidakada")),
    ErrorCode.PERIOD_NOT_AVAILABLE: ("data", lambda e: e.data(slug=KEMISKINAN, periods=[2026])),
    ErrorCode.SOURCE_UNAVAILABLE: ("data", _failing(SourceUnavailable("portal mati"))),
    ErrorCode.STALE_DATA: ("data", _stale),
    ErrorCode.RATE_LIMITED: ("data", _failing(SourceRateLimited("429"))),
    ErrorCode.RESPONSE_TOO_LARGE: ("data", lambda e: _too_large(e)),
    ErrorCode.INTERNAL: ("data", lambda e: _internal(e)),
    ErrorCode.LEVEL_NOT_AVAILABLE: (
        "data",
        lambda e: e.data(slug=KEMISKINAN, filters={"kecamatan": ["BOGOR BARAT"]}),
    ),
}


def _too_large(env: ToolEnv) -> dict[str, Any]:
    env.settings.max_response_tokens = 60
    return env.data(slug=KEMISKINAN)


def _internal(env: ToolEnv) -> dict[str, Any]:
    env.deps.adapter = None  # tanpa adaptor: galat tak terduga → INTERNAL
    return env.data(slug=KEMISKINAN)


RESULT_TYPES = {"search": SearchResult, "info": InfoResult, "data": DataResult}


def test_every_code_has_a_trigger() -> None:
    assert set(TRIGGERS) == set(ErrorCode)


@pytest.mark.parametrize("code", list(ErrorCode), ids=[c.value for c in ErrorCode])
def test_kt04_code_triggered_through_tool(
    catalog_db: Path, tmp_path: Path, code: ErrorCode
) -> None:
    tool, trigger = TRIGGERS[code]
    out = trigger(ToolEnv(catalog_db, tmp_path))
    assert out["error"]["code"] == code.value, out["error"]
    assert out["error"]["retryable"] is ERROR_SPECS[code].retryable
    if code is ErrorCode.STALE_DATA:
        assert out["status"] == "partial" and out["result"] is not None
        assert out["provenance"]["served_from"] == "stale_cache"
    else:
        assert out["status"] == "error" and out["result"] is None and out["notices"] == []
    assert_envelope_shape(out, RESULT_TYPES[tool])


# ---------------------------------------------------------------- KT-02


def assert_envelope_shape(out: dict[str, Any], result_type: type) -> None:
    assert set(out) == set(ENVELOPE_FIELD_TYPES)
    for field, types in ENVELOPE_FIELD_TYPES.items():
        assert isinstance(out[field], types), field
    ToolEnvelope[result_type].model_validate(out)  # type: ignore[valid-type]
    assert out["contract_version"] == "0.1"
    if out["status"] == "error":
        assert out["result"] is None and out["error"] is not None
    if out["truncated"]:
        assert out["status"] == "partial" and out["truncated_reason"]


PATHS: dict[str, tuple[str, Callable[[ToolEnv], dict[str, Any]]]] = {
    "search-ok": ("search", lambda e: e.search(query="kemiskinan")),
    "search-truncated": ("search", lambda e: _truncated_search(e)),
    "info-ok": ("info", lambda e: e.info(slug=KEMISKINAN)),
    "data-ok": ("data", lambda e: e.data(slug=KEMISKINAN)),
    "data-partial-missing": ("data", lambda e: e.data(slug=KEMISKINAN, periods=[2025, 2026])),
    "data-partial-stale": ("data", _stale),
    "data-partial-truncated": ("data", lambda e: _truncated_data(e)),
}


def _truncated_search(env: ToolEnv) -> dict[str, Any]:
    env.settings.max_response_tokens = 400
    return env.search(query="kemiskinan")


def _truncated_data(env: ToolEnv) -> dict[str, Any]:
    from mcp_bogor.services.budget import estimate_tokens

    slug = (
        "kecepatan-lalu-lintas-segmen-1-dan-2-berdasarkan-hari-operasional-di-jalan-raya-"
        "pajajaran-kota-bogor"
    )
    env.settings.max_response_tokens = estimate_tokens(env.data(slug=slug)) - 1
    return env.data(
        slug="kecepatan-lalu-lintas-segmen-1-dan-2-berdasarkan-hari-operasional-di-jalan-raya-pajajaran-kota-bogor"
    )


@pytest.mark.parametrize("name", list(PATHS))
def test_kt02_envelope_on_success_paths(catalog_db: Path, tmp_path: Path, name: str) -> None:
    tool, run = PATHS[name]
    out = run(ToolEnv(catalog_db, tmp_path))
    assert_envelope_shape(out, RESULT_TYPES[tool])
    expected = "ok" if name.endswith("-ok") else "partial"
    assert out["status"] == expected, (name, out["error"], out["truncated_reason"])
    if tool != "search":
        assert out["provenance"] and out["officialness"]  # FR-06, FR-07
