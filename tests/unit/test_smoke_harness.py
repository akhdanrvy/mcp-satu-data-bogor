"""Harness smoke test (Langkah 11) diuji offline, tanpa jaringan.

- Ujung ke ujung: harness asli (tool lewat klien MCP in-memory, file server uvicorn di
  127.0.0.1, adaptor asli + GuardedTransport) di atas portal tiruan respx dari rekaman
  2026-11-10. Jumlah permintaan harus tepat sama dengan rencana dry-run.
- GuardedTransport: anggaran, berhenti pada 429 (termasuk retry adaptor), /view, selain GET.
- Fungsi penilai (periode relatif, snapshot golden), nama laporan, pelindung isi laporan, dry-run.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from mcp_bogor.adapters.opendata_jabar import OpenDataJabarAdapter
from mcp_bogor.config import WIB
from tests.fakes import BASE_URL, FakeClock, install_portal_routes, make_settings
from tests.smoke import __main__ as dry_run
from tests.smoke.harness import (
    MAX_REQUESTS,
    Check,
    GuardedTransport,
    GuardRejected,
    ReportRefused,
    SmokeResult,
    StResult,
    check_prerequisites,
    compare_snapshot,
    expected_periods,
    golden,
    p95,
    report_violations,
    run_slot,
    run_smoke_sync,
    snapshot_points,
    write_report,
)

ROOT = Path(__file__).parents[2]
CARDS = ROOT / "metric_cards"
NOW = datetime(2026, 11, 17, 10, 0, tzinfo=WIB)


def base_settings(root: Path, catalog_db: Path, **overrides: Any) -> Any:
    values: dict[str, Any] = {
        "db_path": catalog_db,
        "metric_cards_dir": CARDS,
        "overrides_dir": ROOT / "overrides",
    }
    values.update(overrides)
    return make_settings(root, **values)


def portal_transport() -> httpx.MockTransport:
    router = respx.MockRouter(assert_all_called=False)
    install_portal_routes(router)
    return httpx.MockTransport(router.async_handler)


# ---------------------------------------------------------------- ujung ke ujung


@pytest.fixture(scope="module")
def e2e(tmp_path_factory: pytest.TempPathFactory, catalog_db: Path) -> SmokeResult:
    root = tmp_path_factory.mktemp("smoke-e2e")
    clock = FakeClock()
    return run_smoke_sync(
        base_settings(root, catalog_db),
        inner_transport=portal_transport(),
        docs_dir=root / "docs",
        smoke_root=root / "var" / "smoke",
        now=lambda: NOW,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )


def _failed(result: SmokeResult) -> list[str]:
    return [
        f"{key} {c.name}: {c.detail}"
        for key, st in result.sts.items()
        for c in st.checks
        if not c.ok
    ]


def test_e2e_all_st_pass(e2e: SmokeResult) -> None:
    assert e2e.aborted is None
    assert _failed(e2e) == []
    assert all(st.ran for st in e2e.sts.values()) and e2e.passed


def test_e2e_requests_equal_plan(e2e: SmokeResult, tmp_path: Path, catalog_db: Path) -> None:
    pre = check_prerequisites(base_settings(tmp_path, catalog_db))
    assert e2e.planned == len(pre.plan) == 1 + 2 * len(pre.cards) == 25
    assert e2e.requests_sent == e2e.planned


def test_e2e_report_written_without_values(e2e: SmokeResult) -> None:
    assert e2e.report_path is not None and e2e.report_path.is_file()
    assert e2e.report_path.name == "SMOKE_REPORT_2026-11-17.md"
    text = e2e.report_path.read_text(encoding="utf-8")
    assert "Hasil keseluruhan: **LULUS**" in text
    assert "metric_cards --check" in text and "var/smoke" in text.replace("\\", "/")
    # pelindung yang sama dijalankan ulang atas berkas jadi, dengan nilai dari berkas data_ref
    values = []
    for path in (e2e.run_dir / "files").glob("*.json"):
        body = json.loads(path.read_text(encoding="utf-8"))
        measures = [c["name"] for c in body["columns"] if c["role"] == "measure"]
        values += [row[m] for row in body["rows"] for m in measures if row[m] is not None]
    assert values and report_violations(text, values) == []


def test_e2e_artifacts_in_run_dir(e2e: SmokeResult) -> None:
    assert e2e.run_dir is not None
    assert (e2e.run_dir / "responses.jsonl").is_file()
    assert len(list((e2e.run_dir / "files").glob("*.json"))) >= 12
    assert (e2e.run_dir / "cache" / "dictionary").is_dir()  # cache smoke terpisah
    assert e2e.check_command.startswith('$env:MCP_CACHE_DIR="')


def test_e2e_st04_gap_and_statuses(e2e: SmokeResult) -> None:
    text = "\n".join(e2e.sts["ST-04"].lines)
    assert "Jeda terkecil antarpermintaan: 300 ms" in text
    assert "`200` x 25" in text


# ---------------------------------------------------------------- GuardedTransport


def _client(transport: httpx.AsyncBaseTransport) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=transport, base_url=BASE_URL)


def _ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"code": 200, "data": [], "pagination": {"total_data": 1}})


async def _get(guard: GuardedTransport, path: str, **params: Any) -> httpx.Response:
    async with _client(guard) as client:
        return await client.get(path, params=params)


def test_guard_budget_rejects_before_sending() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return _ok(request)

    guard = GuardedTransport(httpx.MockTransport(handler), base_path="/api", budget=2)

    async def run() -> None:
        await _get(guard, "/bigdata/a/b", page=1)
        await _get(guard, "/bigdata/a/b", page=2)
        with pytest.raises(GuardRejected, match="anggaran"):
            await _get(guard, "/bigdata/a/b", page=3)

    asyncio.run(run())
    assert len(calls) == guard.sent == 2
    with pytest.raises(ValueError):
        GuardedTransport(httpx.MockTransport(handler), budget=MAX_REQUESTS + 1)


@pytest.mark.parametrize(
    ("method", "path", "params", "reason"),
    [
        ("GET", "/datasets/abc/view", {}, "/view"),
        ("GET", "/bigdata/a/view", {}, "/view"),
        ("POST", "/datasets", {"per_page": 1}, "GET"),
        ("GET", "/datasets", {"page": 1, "per_page": 100}, "per_page=1"),
        ("GET", "/datasets/tingkat-kemiskinan", {}, "allowlist"),
        ("GET", "/organisasi", {}, "allowlist"),
    ],
)
def test_guard_rejects_forbidden(method: str, path: str, params: dict, reason: str) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return _ok(request)

    guard = GuardedTransport(httpx.MockTransport(handler), base_path="/api")

    async def run() -> None:
        async with _client(guard) as client:
            await client.request(method, path, params=params)

    with pytest.raises(GuardRejected, match=reason):
        asyncio.run(run())
    assert calls == [] and guard.sent == 0 and guard.rejections


def test_guard_stops_after_429_including_adapter_retry(tmp_path: Path) -> None:
    """429 → adaptor ingin retry (MCP_MAX_RETRIES=2), tetapi guard menolak tanpa mengirim."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(429, headers={"Retry-After": "1"})

    clock = FakeClock()
    guard = GuardedTransport(
        httpx.MockTransport(handler), base_path="/api", monotonic=clock.monotonic
    )
    adapter = OpenDataJabarAdapter(
        make_settings(tmp_path, max_retries=2, request_delay_s=0.3),
        transport=guard,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    async def run() -> None:
        async with adapter:
            with pytest.raises(GuardRejected, match="429"):
                await adapter.catalog_total()

    asyncio.run(run())
    assert calls == ["/api/datasets"] and guard.rate_limited and guard.sent == 1
    assert guard.statuses() == {"429": 1}


def test_guard_min_gap_and_records() -> None:
    clock = FakeClock()
    guard = GuardedTransport(httpx.MockTransport(_ok), base_path="/api", monotonic=clock.monotonic)

    async def run() -> None:
        await _get(guard, "/datasets", page=1, per_page=1)
        clock.now += 0.25
        await _get(guard, "/bigdata/s/t", page=1)
        clock.now += 0.4
        await _get(guard, "/bigdata/s/t", page=1)

    asyncio.run(run())
    assert guard.min_gap_s() == pytest.approx(0.25)
    assert [r.path for r in guard.records] == ["/datasets", "/bigdata/s/t", "/bigdata/s/t"]


# ---------------------------------------------------------------- penilai


@pytest.mark.parametrize(
    ("requested", "available", "expected"),
    [
        ([2025, 2026], [2025], ("partial", [2025], [2026])),
        ([2025, 2026], [2025, 2026], ("ok", [2025, 2026], [])),  # portal menambah 2026: bukan gagal
        (list(range(2019, 2026)), [2025], ("partial", [2025], list(range(2019, 2025)))),
        ([2026], [2025], ("error", [], [2026])),
    ],
)
def test_expected_periods_relative(requested: list, available: list, expected: tuple) -> None:
    assert expected_periods(requested, available) == expected


def test_snapshot_points_unpivot_n02_n08() -> None:
    n02 = golden()["N-02"]
    points = snapshot_points(n02["snapshot"]["values"], "kecepatan", n02["expected"]["filters"])
    assert len(points) == 2 * len(n02["snapshot"]["values"])
    dims = points[0][0]
    assert dims["tahun"] == 2025 and dims["hari_operasional"] == "HARI KERJA" and "segmen" in dims
    n08 = golden()["N-08"]
    points = snapshot_points(n08["snapshot"]["values"], "volume", n08["expected"]["filters"])
    assert {p[0]["tahun"] for p in points} == {2023, 2025}
    assert {p[0]["hari_operasional"] for p in points} == {"HARI KERJA"}


def test_compare_snapshot_counts() -> None:
    rows = [
        {"tahun": 2024, "nilai": 1.5},
        {"tahun": 2025, "nilai": 2.0},
        {"tahun": 2023, "nilai": 1.0},
        {"tahun": 2023, "nilai": 1.0},
    ]
    points = [({"tahun": 2024}, 1.5), ({"tahun": 2025}, 2.5), ({"tahun": 2022}, 1.0)]
    assert compare_snapshot(points, rows, "nilai") == (1, 2, 0)
    assert compare_snapshot([({"tahun": 2023}, 1.0)], rows, "nilai") == (0, 0, 1)
    assert compare_snapshot([({"tahun": "2024"}, 1.5 + 1e-12)], rows, "nilai") == (1, 0, 0)


def test_run_slot_never_overwrites(tmp_path: Path) -> None:
    docs, smoke = tmp_path / "docs", tmp_path / "smoke"
    day = date(2026, 11, 17)
    report, _run_dir, suffix = run_slot(docs, smoke, day)
    assert report.name == "SMOKE_REPORT_2026-11-17.md" and suffix == "2026-11-17"
    docs.mkdir()
    report.write_text("x", encoding="utf-8")
    assert run_slot(docs, smoke, day)[2] == "2026-11-17-2"
    (smoke / "2026-11-17-2").mkdir(parents=True)
    assert run_slot(docs, smoke, day)[2] == "2026-11-17-3"


def test_p95_nearest_rank() -> None:
    assert p95([float(i) for i in range(1, 21)]) == 19.0
    assert p95([0.1]) == 0.1


# ---------------------------------------------------------------- pelindung laporan


def _result(tmp_path: Path, detail: str) -> SmokeResult:
    st = StResult("ST-02", "kartu", ran=True)
    st.checks.append(Check("contoh", True, detail, warning=True))
    return SmokeResult(
        started_at=NOW,
        finished_at=NOW,
        report_path=tmp_path / "docs" / "SMOKE_REPORT_2026-11-17.md",
        run_dir=tmp_path / "run",
        sts={"ST-02": st},
        check_command="cek",
    )


def test_report_guard_refuses_values(tmp_path: Path) -> None:
    (tmp_path / "run").mkdir()
    leaky = _result(tmp_path, "nilai 1431967467277.22")
    with pytest.raises(ReportRefused, match="bentuk khas nilai measure"):
        write_report(leaky, [1431967467277.22])
    assert not leaky.report_path.exists()
    assert (tmp_path / "run" / "SMOKE_REPORT_DITOLAK.md").is_file()
    with pytest.raises(ReportRefused, match="angka berformat"):
        write_report(_result(tmp_path, "sekitar 9.160.885"), [])
    clean = _result(tmp_path, "periods_available [2024, 2025]")
    assert write_report(clean, [1431967467277.22, 5.89]).is_file()


# ---------------------------------------------------------------- dry-run


def test_dry_run_lists_plan(
    tmp_path: Path, catalog_db: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert dry_run.main(base_settings(tmp_path, catalog_db)) == 0
    out = capsys.readouterr().out
    assert "tidak ada permintaan yang dikirim" in out
    assert "Total direncanakan: 25" in out
    assert out.count("GET /bigdata/") == 24 and "GET /datasets?page=1&per_page=1" in out
    assert "uv run pytest tests/smoke -m smoke" in out and "metric_cards --check" in out


def test_dry_run_refuses_placeholder_user_agent(
    tmp_path: Path, catalog_db: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = base_settings(tmp_path, catalog_db, user_agent="mcp-bogor/0.1 (+<kontak-tim>)")
    assert dry_run.main(settings) == 1
    assert "MASALAH" in capsys.readouterr().out
