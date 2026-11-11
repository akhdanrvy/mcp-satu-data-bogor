"""KT-06: isi berkas data_ref yang diunduh lewat file server = respons tool (Dok 4 bagian 6).

rows cocok row_count; columns, provenance (tanpa served_from, KB-34), officialness,
quality_flags, periods_*, filters_applied, dictionary_validated sama dengan respons tool.
Tanpa jaringan: file server Starlette lewat TestClient, data dari FakeSourceAdapter.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
from starlette.testclient import TestClient

from mcp_bogor.adapters.base import SourceUnavailable
from mcp_bogor.fileserver.app import create_app
from tests.tool_support import ToolEnv

KEMISKINAN = "tingkat-kemiskinan-di-kota-bogor-2"
SPEED = (
    "kecepatan-lalu-lintas-segmen-1-dan-2-berdasarkan-hari-operasional-di-jalan-raya-pajajaran-"
    "kota-bogor"
)
VOLUME = (
    "volume-lalu-lintas-segmen-1-dan-2-berdasarkan-hari-operasional-di-jalan-pajajaran--kota-bogor"
)
KUNJUNGAN = "perkembangan-jumlah-kunjungan-wisatawan-di-kota-bogor"
PAD = "realisasi-pendapatan-asli-daerah-pad-kota-bogor"

CASES: list[tuple[str, dict[str, Any]]] = [
    ("f07", {"slug": KEMISKINAN, "periods": [2025, 2026]}),
    ("semua", {"slug": SPEED}),
    ("filter", {"slug": SPEED, "filters": {"hari_operasional": ["HARI KERJA"]}}),
    (
        "periode+filter",
        {"slug": VOLUME, "periods": [2023], "filters": {"hari_operasional": ["HARI LIBUR"]}},
    ),
    ("teks-angka", {"slug": KUNJUNGAN, "periods": [2024]}),
    ("belum-valid", {"slug": PAD}),
]


def download(env: ToolEnv, url: str) -> dict[str, Any]:
    parts = urlsplit(url)
    client = TestClient(create_app(env.settings, clock=lambda: env.clock.now.timestamp()))
    response = client.get(f"{parts.path}?{parts.query}")
    assert response.status_code == 200, response.text
    return response.json()


def assert_matches_tool(out: dict[str, Any], body: dict[str, Any]) -> None:
    result = out["result"]
    assert len(body["rows"]) == result["data_ref"]["row_count"]
    assert body["columns"] == result["columns"]
    expected_prov = {k: v for k, v in out["provenance"].items() if k != "served_from"}
    assert body["provenance"] == expected_prov
    assert "served_from" not in body["provenance"]
    assert body["officialness"] == out["officialness"]
    assert body["quality_flags"] == out["quality_flags"]
    for key in (
        "periods_requested",
        "periods_found",
        "missing_periods",
        "time_dimension",
        "filters_applied",
        "dictionary_validated",
    ):
        assert body[key] == result[key], key
    names = [c["name"] for c in result["columns"]]
    assert all(list(row) == names for row in body["rows"])


@pytest.mark.parametrize(("label", "args"), CASES, ids=[c[0] for c in CASES])
def test_kt06_file_matches_tool_response(
    catalog_db: Path, tmp_path: Path, label: str, args: dict[str, Any]
) -> None:
    env = ToolEnv(catalog_db, tmp_path)
    out = env.data(**args)
    assert out["status"] in {"ok", "partial"}, out["error"]
    body = download(env, out["result"]["data_ref"]["url"])
    assert_matches_tool(out, body)
    assert body["slug"] == args["slug"] and body["contract_version"] == "0.1"


def test_rows_are_normalized_and_typed(catalog_db: Path, tmp_path: Path) -> None:
    env = ToolEnv(catalog_db, tmp_path)
    body = download(env, env.data(slug=KUNJUNGAN, periods=[2024])["result"]["data_ref"]["url"])
    (row,) = body["rows"]
    assert isinstance(row["perkembangan_kunjungan"], int)  # NB-07: "x.xxx.xxx" → angka
    assert "id" not in row and row["tahun"] == 2024


def test_label_originals_kept_for_nb08(catalog_db: Path, tmp_path: Path) -> None:
    env = ToolEnv(catalog_db, tmp_path)
    body = download(env, env.data(slug=SPEED)["result"]["data_ref"]["url"])
    originals = body["label_originals"]["segmen"]
    assert originals, "label segmen asli berbeda ejaan harus tersimpan (KK-Q)"
    for label, raws in originals.items():
        assert "\n" not in label and all(raw != label for raw in raws)
    assert {row["segmen"] for row in body["rows"]} >= set(originals)


def test_same_file_for_unfiltered_request_and_series(catalog_db: Path, tmp_path: Path) -> None:
    env = ToolEnv(catalog_db, tmp_path)
    first = env.data(slug=SPEED)
    second = env.data(slug=SPEED)
    assert (
        first["result"]["data_ref"]["url"].split("?")[0]
        == second["result"]["data_ref"]["url"].split("?")[0]
    )
    assert second["provenance"]["served_from"] == "cache"
    assert_matches_tool(second, download(env, second["result"]["data_ref"]["url"]))


def test_stale_response_file_has_no_served_from(catalog_db: Path, tmp_path: Path) -> None:
    env = ToolEnv(catalog_db, tmp_path)
    env.data(slug=KEMISKINAN, periods=[2025])
    env.clock.now += timedelta(hours=25)
    env.adapter.fail_with = SourceUnavailable("portal mati")
    out = env.data(slug=KEMISKINAN, periods=[2025])
    assert out["provenance"]["served_from"] == "stale_cache"
    assert_matches_tool(out, download(env, out["result"]["data_ref"]["url"]))
