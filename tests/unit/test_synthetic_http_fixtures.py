"""Fixture HTTP buatan (Langkah 3 bagian A): bentuk sah dan bisa dipakai respx."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from mcp_bogor.services.freshness import compute_freshness
from mcp_bogor.services.normalize import normalize_label, normalize_rows, parse_portal_datetime
from tests.http_fixtures import (
    SYNTHETIC_HTTP,
    load_exchange,
    mock_sequence,
    to_side_effect,
    validate_exchange,
)

BASE = "https://portal.test/api"
FILES = sorted(SYNTHETIC_HTTP.rglob("*.json"))


def test_expected_cases_exist() -> None:
    names = {p.relative_to(SYNTHETIC_HTTP).as_posix() for p in FILES}
    assert names == {
        "portal_down/connect_error.json",
        "portal_down/timeout.json",
        "rate_limited/01_429.json",
        "rate_limited/02_200.json",
        "server_error/01_503.json",
        "server_error/02_200.json",
        "not_found_slug.json",
        "dataset_record_offsets.json",
        "dataset_no_approve.json",
        "bigdata_messy.json",
    }


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.relative_to(SYNTHETIC_HTTP).as_posix())
def test_fixture_shape(path: Path) -> None:
    exchange = load_exchange(path)
    assert exchange.get("_synthetic") is True
    assert isinstance(exchange.get("_keterangan"), str) and exchange["_keterangan"].strip()
    assert validate_exchange(exchange) == []


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.relative_to(SYNTHETIC_HTTP).as_posix())
def test_fixture_served_by_respx(path: Path) -> None:
    exchange = load_exchange(path)
    request = exchange["request"]
    with respx.mock(assert_all_called=True) as router, httpx.Client(base_url=BASE) as client:
        router.get(BASE + request["path"]).mock(side_effect=[to_side_effect(exchange)])
        if "exception" in exchange:
            with pytest.raises(httpx.TransportError):
                client.get(request["path"], params=request["params"])
        else:
            response = client.get(request["path"], params=request["params"])
            assert response.status_code == exchange["response"]["status"]
            if exchange["response"]["json"] is not None:
                assert response.json() == exchange["response"]["json"]


@pytest.mark.parametrize("scenario", ["rate_limited", "server_error"])
def test_sequences(scenario: str) -> None:
    exchanges = [load_exchange(p) for p in sorted((SYNTHETIC_HTTP / scenario).glob("*.json"))]
    path = exchanges[0]["request"]["path"]
    with respx.mock as router, httpx.Client(base_url=BASE) as client:
        route = mock_sequence(router, BASE, exchanges)
        first = client.get(path)
        second = client.get(path)
    assert first.status_code in (429, 503) and second.status_code == 200
    assert route.call_count == 2
    if scenario == "rate_limited":
        assert first.headers["retry-after"] == "2"


def test_validator_catches_bad_shapes() -> None:
    good = load_exchange(SYNTHETIC_HTTP / "not_found_slug.json")
    assert validate_exchange({"request": "x"}) == ["request bukan objek"]
    bad = {
        "request": {"method": "POST", "path": "/datasets/opendata/x/view", "params": []},
        "response": good["response"],
        "exception": {"type": "Boom"},
    }
    problems = validate_exchange(bad)
    assert len(problems) == 5
    no_body = {"request": good["request"], "response": {"status": 99, "headers": {"a": 1}}}
    assert len(validate_exchange(no_body)) == 3
    assert validate_exchange({"request": {"method": "GET", "path": "x", "params": {}}}) == [
        "path harus diawali '/'",
        "harus punya tepat satu dari response atau exception",
    ]


# ---------------------------------------------------------------- isi kasus cocok dengan service


def test_offsets_record_with_services() -> None:
    record = load_exchange(SYNTHETIC_HTTP / "dataset_record_offsets.json")["response"]["json"][
        "data"
    ]
    assert record["cdate"].endswith("+07:07") and "+" not in record["mdate"]
    assert parse_portal_datetime(record["mdate"]).isoformat() == "2026-08-31T16:03:02+07:00"
    fresh = compute_freshness(record["history_draft"], record["mdate"])
    assert fresh.freshness_source == "approve"
    assert fresh.data_updated_at.isoformat() == "2026-08-31T16:03:02+07:00"


def test_no_approve_record_with_services() -> None:
    record = load_exchange(SYNTHETIC_HTTP / "dataset_no_approve.json")["response"]["json"]["data"]
    assert all(e["type"] != "approve" for e in record["history_draft"])
    assert compute_freshness(record["history_draft"], record["mdate"]).freshness_source == "mdate"


def test_bigdata_messy_with_services() -> None:
    body = load_exchange(SYNTHETIC_HTTP / "bigdata_messy.json")["response"]["json"]
    types = {"kecepatan": "number", "jumlah_kendaraan": "number", "tahun": "number"}
    rows, issues = normalize_rows(body["data"], types)
    assert [r["jumlah_kendaraan"] for r in rows] == [9160885, None, None, 1234.5]
    assert [r["kecepatan"] for r in rows] == [49.23, 36.94, None, None]
    assert [(i.row_index, i.column) for i in issues] == [(3, "kecepatan")]  # "5.89" (KB-16)
    assert all("id" not in r for r in rows)
    assert rows[0]["bps_kode_kabupaten_kota"] == "3271"
    segments = {normalize_label(r["segmen"])[0] for r in rows}
    assert segments == {
        "SIMPANG EKALOKASARI - SIMPANG BARANANGSIANG",
        "SIMPANG BARANANGSIANG - SIMPANG EKALOKASARI",
    }
