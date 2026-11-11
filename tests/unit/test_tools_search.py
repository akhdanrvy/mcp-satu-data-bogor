"""Tool search_datasets (Langkah 8a): amplop, galat, notice, anggaran token, log.

Tanpa jaringan: katalog SQLite dari 751 record rekaman (FakeSourceAdapter).
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from mcp_bogor.models import SearchResult, ToolEnvelope
from mcp_bogor.services.budget import estimate_tokens
from mcp_bogor.tools import search as search_mod
from mcp_bogor.tools.common import ToolDeps
from mcp_bogor.tools.search import catalog_is_stale, search_datasets
from tests.fakes import make_settings
from tests.tool_support import SYNC_AT, deps_for, synced_settings

KEMISKINAN = "tingkat-kemiskinan-di-kota-bogor-2"


@pytest.fixture(scope="module")
def root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("katalog-tool")
    synced_settings(path)
    return path


def _deps(root: Path, now: datetime = SYNC_AT, **overrides: Any) -> ToolDeps:
    return deps_for(make_settings(root, **overrides), now=now)


def _call(deps: ToolDeps, **args: Any) -> dict[str, Any]:
    payload = asyncio.run(search_datasets(deps, args))
    ToolEnvelope[SearchResult].model_validate(payload)  # KT-02: selalu amplop sah
    return payload


def test_ok_result_and_request_id(root: Path) -> None:
    out = _call(_deps(root), query="kemiskinan", request_id="rq-1")
    assert out["status"] == "ok" and out["error"] is None and out["request_id"] == "rq-1"
    assert out["truncated"] is False and out["notices"] == []
    result = out["result"]
    assert KEMISKINAN in [item["slug"] for item in result["items"]]
    assert result["page_size"] == 10 and result["total"] >= len(result["items"])
    item = next(i for i in result["items"] if i["slug"] == KEMISKINAN)
    assert item["officialness"]["status"] in {"resmi", "draf"}


def test_no_match(root: Path) -> None:
    out = _call(_deps(root), query="zzqxw tidakada", request_id="rq-2")
    assert out["status"] == "error" and out["result"] is None
    assert out["error"]["code"] == "NO_MATCH" and out["request_id"] == "rq-2"


@pytest.mark.parametrize(
    ("args", "param", "limit"),
    [
        ({"query": "a"}, "query", None),
        ({}, "query", None),
        ({"query": "x" * 101}, "query", 100),
        ({"query": "pad", "page_size": 21}, "page_size", 20),
        ({"query": "pad", "page": 0}, "page", None),
        ({"query": "pad", "tahun": 1999}, "tahun", None),
        ({"query": "pad", "tahun": "dua ribu"}, "tahun", None),
        ({"query": "pad", "request_id": "x" * 65}, "request_id", 64),
    ],
)
def test_invalid_params(root: Path, args: dict[str, Any], param: str, limit: int | None) -> None:
    out = _call(_deps(root), **args)
    assert out["error"]["code"] == "INVALID_PARAM" and out["result"] is None
    assert f"Parameter '{param}'" in out["error"]["message"]
    assert (f"Batas: {limit}." in out["error"]["message"]) == (limit is not None)
    assert out["request_id"] is None or len(out["request_id"]) <= 64


def test_page_size_limit_from_settings(root: Path) -> None:
    deps = _deps(root, search_max_page_size=3)
    assert _call(deps, query="kemiskinan")["result"]["page_size"] == 3
    out = _call(deps, query="kemiskinan", page_size=4)
    assert out["error"]["code"] == "INVALID_PARAM" and "Batas: 3." in out["error"]["message"]


@pytest.mark.parametrize(
    ("extra", "named"),
    [("where", "where"), ("9.160.885", "argumen_tidak_dikenal"), ("a b", "argumen_tidak_dikenal")],
)
def test_unknown_args_rejected_as_envelope(root: Path, extra: str, named: str) -> None:
    """KB-42 (revisi): argumen di luar skema tidak dibuang diam-diam."""
    out = _call(_deps(root), query="kemiskinan", request_id="rq-x", **{extra: "x"})
    assert out["error"]["code"] == "INVALID_PARAM" and out["request_id"] == "rq-x"
    assert out["error"]["message"] == (
        f"Parameter '{named}' tidak valid. Argumen ini tidak dikenal."
    )
    assert "9.160.885" not in json.dumps(out)


def test_null_optional_args_are_accepted(root: Path) -> None:
    out = _call(_deps(root), query="kemiskinan", opd=None, tahun=None, request_id=None)
    assert out["status"] == "ok"


def test_catalog_not_ready_is_internal(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("INFO", logger="mcp_bogor.tools.common")
    out = _call(deps_for(make_settings(tmp_path)), query="kemiskinan", request_id="rq-3")
    assert out["error"]["code"] == "INTERNAL" and out["error"]["retryable"] is True
    assert out["request_id"] == "rq-3"
    event = json.loads(caplog.records[-1].getMessage())
    assert event["exception"] == "CatalogNotReady" and event["status"] == "error"


def test_catalog_stale_notice(root: Path) -> None:
    fresh = _call(_deps(root, now=SYNC_AT + timedelta(hours=48)), query="kemiskinan")
    assert fresh["notices"] == []
    old = _call(_deps(root, now=SYNC_AT + timedelta(hours=49)), query="kemiskinan")
    assert [n["code"] for n in old["notices"]] == ["CATALOG_STALE"]
    assert old["status"] == "ok"
    assert catalog_is_stale(None, SYNC_AT, 24)


def test_truncation_lowers_page_size(root: Path) -> None:
    full = _call(_deps(root), query="kemiskinan")
    limit = estimate_tokens(full) - 1  # utuh tidak muat; satu item lebih sedikit pasti muat
    out = _call(_deps(root, max_response_tokens=limit), query="kemiskinan")
    assert out["status"] == "partial" and out["truncated"] is True
    assert out["truncated_reason"] == "items_dipangkas" and out["error"] is None
    items = out["result"]["items"]
    assert 1 <= len(items) < len(full["result"]["items"])
    assert out["result"]["page_size"] == len(items)
    assert out["result"]["total"] == full["result"]["total"]  # total tetap jujur
    assert items == full["result"]["items"][: len(items)]
    assert estimate_tokens(out) <= limit


def test_too_large_even_after_trimming(root: Path) -> None:
    out = _call(_deps(root, max_response_tokens=50), query="kemiskinan")
    assert out["error"]["code"] == "RESPONSE_TOO_LARGE"
    assert "Batas: 50." in out["error"]["message"]


def test_unexpected_error_is_internal_without_detail(
    root: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def boom(conn: Any, params: Any) -> Any:
        raise RuntimeError("rahasia 9.160.885")

    monkeypatch.setattr(search_mod.catalog, "search", boom)
    caplog.set_level("INFO", logger="mcp_bogor.tools.common")
    out = _call(_deps(root), query="kemiskinan")
    assert out["error"]["code"] == "INTERNAL" and "rahasia" not in json.dumps(out)
    assert "rahasia" not in caplog.text and "9.160.885" not in caplog.text
    assert json.loads(caplog.records[-1].getMessage())["exception"] == "RuntimeError"


def test_log_has_no_query_text(root: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("INFO", logger="mcp_bogor.tools.common")
    _call(_deps(root), query="kunjungan wisatawan", request_id="rq-9")
    message = caplog.records[-1].getMessage()
    event = json.loads(message)
    assert event["tool"] == "search_datasets" and event["request_id"] == "rq-9"
    assert event["status"] == "ok" and event["size_bytes"] > 0
    assert "wisatawan" not in message and "kunjungan" not in message
