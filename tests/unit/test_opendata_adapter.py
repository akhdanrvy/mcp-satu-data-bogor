"""OpenDataJabarAdapter: laju, retry, paginasi katalog, galat, health, keamanan, log, privasi.

Tanpa jaringan: respx + jam palsu (FakeClock). Data dari rekaman 2026-11-10.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from mcp_bogor.adapters.base import (
    InvalidSourceRequest,
    RawDataset,
    SourceNotFound,
    SourceProtocolError,
    SourceRateLimited,
    SourceUnavailable,
)
from mcp_bogor.adapters.opendata_jabar import (
    OpenDataJabarAdapter,
    build_path,
    build_query_params,
    check_endpoint,
    parse_dataset,
    parse_reference,
)
from mcp_bogor.adapters.sanitize import find_private_keys
from mcp_bogor.config import ConfigError
from tests.fakes import (
    BASE_URL,
    FakeClock,
    install_portal_routes,
    make_settings,
    recorded_portal,
)

pytestmark = pytest.mark.anyio

KEMISKINAN = "tingkat-kemiskinan-di-kota-bogor-2"
DETAIL_URL = f"{BASE_URL}/datasets/{KEMISKINAN}"
NOW = datetime(2026, 11, 10, 8, 0, tzinfo=UTC)


def _adapter(tmp_path: Path, clock: FakeClock, **settings: Any) -> OpenDataJabarAdapter:
    return OpenDataJabarAdapter(
        make_settings(tmp_path, **settings),
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        wall_clock=lambda: NOW,
        rng=lambda: 0.0,
    )


def _detail_ok() -> httpx.Response:
    record = recorded_portal().details[KEMISKINAN]
    return httpx.Response(200, json={"code": 200, "data": record})


def _page(page: int, slugs: list[str], total_page: int, total_data: int, **extra: Any) -> dict:
    pagination = {
        "page": page,
        "per_page": 100,
        "total_page": total_page,
        "total_data": total_data,
        "has_next": page < total_page,
        "has_previous": page > 1,
    }
    pagination.update(extra)
    return {"code": 200, "data": [{"slug": s, "name": s} for s in slugs], "pagination": pagination}


# ---------------------------------------------------------------- konfigurasi


def test_placeholder_user_agent_refused(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="MCP_USER_AGENT"):
        OpenDataJabarAdapter(make_settings(tmp_path, user_agent="mcp-bogor/0.1 (+<kontak-tim>)"))


async def test_user_agent_header_sent(tmp_path: Path) -> None:
    clock = FakeClock()
    with respx.mock as router:
        route = router.get(DETAIL_URL).mock(return_value=_detail_ok())
        async with _adapter(tmp_path, clock) as adapter:
            await adapter.get_dataset(KEMISKINAN)
    assert route.calls[0].request.headers["user-agent"].startswith("mcp-bogor-test/0.1")


# ---------------------------------------------------------------- laju (AK-03)


async def test_sequential_requests_are_spaced(tmp_path: Path) -> None:
    clock = FakeClock()
    times: list[float] = []

    def record(request: httpx.Request) -> httpx.Response:
        times.append(clock.monotonic())
        return _detail_ok()

    with respx.mock as router:
        router.get(DETAIL_URL).mock(side_effect=record)
        async with _adapter(tmp_path, clock, request_delay_s=0.5) as adapter:
            await adapter.get_dataset(KEMISKINAN)
            await adapter.get_dataset(KEMISKINAN)
    assert times[1] - times[0] >= 0.5
    assert clock.sleeps == [0.5]


async def test_concurrent_requests_are_spaced(tmp_path: Path) -> None:
    clock = FakeClock()
    times: list[float] = []

    def record(request: httpx.Request) -> httpx.Response:
        times.append(clock.monotonic())
        return _detail_ok()

    with respx.mock as router:
        router.get(DETAIL_URL).mock(side_effect=record)
        async with _adapter(tmp_path, clock, request_delay_s=0.2) as adapter:
            await asyncio.gather(*(adapter.get_dataset(KEMISKINAN) for _ in range(5)))
    assert len(times) == 5
    gaps = [b - a for a, b in itertools.pairwise(times)]
    assert all(gap >= 0.2 - 1e-9 for gap in gaps), gaps


# ---------------------------------------------------------------- retry (AK-04)


async def test_429_then_200_honours_retry_after(tmp_path: Path) -> None:
    clock = FakeClock()
    with respx.mock as router:
        route = router.get(DETAIL_URL).mock(
            side_effect=[httpx.Response(429, headers={"Retry-After": "7"}), _detail_ok()]
        )
        async with _adapter(tmp_path, clock) as adapter:
            dataset = await adapter.get_dataset(KEMISKINAN)
    assert dataset.slug == KEMISKINAN and route.call_count == 2
    assert 7.0 in clock.sleeps


async def test_429_forever_stops_at_max_retries(tmp_path: Path) -> None:
    clock = FakeClock()
    with respx.mock as router:
        route = router.get(DETAIL_URL).mock(return_value=httpx.Response(429))
        async with _adapter(tmp_path, clock, max_retries=3) as adapter:
            with pytest.raises(SourceRateLimited) as info:
                await adapter.get_dataset(KEMISKINAN)
    assert route.call_count == 3 and info.value.attempts == 3
    backoffs = [s for s in clock.sleeps if s >= 1.0]
    assert backoffs == [1.0, 2.0]  # eksponensial, jitter 0 (rng=0)


async def test_large_retry_after_fails_without_sleeping(tmp_path: Path) -> None:
    clock = FakeClock()
    with respx.mock as router:
        route = router.get(DETAIL_URL).mock(
            return_value=httpx.Response(429, headers={"Retry-After": "120"})
        )
        async with _adapter(tmp_path, clock, backoff_max_s=30) as adapter:
            with pytest.raises(SourceRateLimited):
                await adapter.get_dataset(KEMISKINAN)
    assert route.call_count == 1 and clock.sleeps == []


async def test_retry_after_http_date(tmp_path: Path) -> None:
    clock = FakeClock()
    later = format_datetime(NOW + timedelta(seconds=10), usegmt=True)
    with respx.mock as router:
        router.get(DETAIL_URL).mock(
            side_effect=[httpx.Response(503, headers={"Retry-After": later}), _detail_ok()]
        )
        async with _adapter(tmp_path, clock) as adapter:
            await adapter.get_dataset(KEMISKINAN)
    assert 10.0 in clock.sleeps


async def test_retry_after_past_date_and_garbage(tmp_path: Path) -> None:
    clock = FakeClock()
    past = format_datetime(NOW - timedelta(seconds=30), usegmt=True)
    with respx.mock as router:
        router.get(DETAIL_URL).mock(
            side_effect=[
                httpx.Response(503, headers={"Retry-After": past}),
                httpx.Response(503, headers={"Retry-After": "nanti saja"}),
                httpx.Response(503, headers={"Retry-After": "Wed, 21 Oct 2015 07:28:00"}),
                _detail_ok(),
            ]
        )
        async with _adapter(tmp_path, clock) as adapter:
            await adapter.get_dataset(KEMISKINAN)
    assert clock.sleeps[0] == pytest.approx(0.2)  # tanggal lampau → hanya jarak biasa
    assert 2.0 in clock.sleeps  # teks rusak → backoff biasa (percobaan ke-2)


async def test_429_blocks_concurrent_calls_globally(tmp_path: Path) -> None:
    """Panggilan kedua tidak mengirim apa pun sebelum Retry-After panggilan pertama habis."""
    clock = FakeClock()
    sent: list[float] = []
    responses = iter([httpx.Response(429, headers={"Retry-After": "5"})])

    def respond(request: httpx.Request) -> httpx.Response:
        sent.append(clock.monotonic())
        return next(responses, None) or _detail_ok()

    with respx.mock as router:
        route = router.get(DETAIL_URL).mock(side_effect=respond)
        async with _adapter(tmp_path, clock) as adapter:
            first, second = await asyncio.gather(
                adapter.get_dataset(KEMISKINAN), adapter.get_dataset(KEMISKINAN)
            )
    assert first.slug == second.slug == KEMISKINAN
    assert route.call_count == 3  # 429, lalu dua permintaan yang berhasil
    assert all(t >= sent[0] + 5 for t in sent[1:]), sent
    assert all(b - a >= 0.2 - 1e-9 for a, b in itertools.pairwise(sent))


async def test_global_block_longer_than_max_fails_without_sending(tmp_path: Path) -> None:
    clock = FakeClock()
    with respx.mock as router:
        route = router.get(DETAIL_URL).mock(
            return_value=httpx.Response(429, headers={"Retry-After": "60"})
        )
        async with _adapter(tmp_path, clock, backoff_max_s=30) as adapter:
            with pytest.raises(SourceRateLimited):
                await adapter.get_dataset(KEMISKINAN)
            assert route.call_count == 1
            with pytest.raises(SourceRateLimited, match="MCP_BACKOFF_MAX_S"):
                await adapter.get_dataset(KEMISKINAN)  # jeda global masih 60 dtk
            assert route.call_count == 1  # tidak ada permintaan baru
            health = await adapter.health()
            assert not health.ok and route.call_count == 1
            clock.now += 61
            route.mock(return_value=_detail_ok())
            assert (await adapter.get_dataset(KEMISKINAN)).slug == KEMISKINAN
    assert clock.sleeps == []


async def test_503_without_retry_after_only_delays_its_own_call(tmp_path: Path) -> None:
    """503 tanpa Retry-After: backoff lokal; panggilan lain tetap jalan dengan jarak biasa."""
    clock = FakeClock()
    sent: list[tuple[str, float]] = []
    other_url = f"{BASE_URL}/datasets/lain"

    def first(request: httpx.Request) -> httpx.Response:
        sent.append(("kemiskinan", clock.monotonic()))
        return httpx.Response(503) if len(sent) == 1 else _detail_ok()

    def other(request: httpx.Request) -> httpx.Response:
        sent.append(("lain", clock.monotonic()))
        return httpx.Response(200, json={"code": 200, "data": {"slug": "lain"}})

    with respx.mock as router:
        router.get(DETAIL_URL).mock(side_effect=first)
        router.get(other_url).mock(side_effect=other)
        async with _adapter(tmp_path, clock) as adapter:
            await asyncio.gather(adapter.get_dataset(KEMISKINAN), adapter.get_dataset("lain"))
            assert adapter._blocked_until is None  # tidak ada jeda global
    lain = next(t for name, t in sent if name == "lain")
    retry = [t for name, t in sent if name == "kemiskinan"][1]
    assert sent[0][0] == "kemiskinan" and lain < retry  # "lain" tidak menunggu retry 503


async def test_503_then_200(tmp_path: Path) -> None:
    clock = FakeClock()
    with respx.mock as router:
        route = router.get(DETAIL_URL).mock(side_effect=[httpx.Response(503), _detail_ok()])
        async with _adapter(tmp_path, clock) as adapter:
            await adapter.get_dataset(KEMISKINAN)
    assert route.call_count == 2 and 1.0 in clock.sleeps


async def test_5xx_forever_is_unavailable(tmp_path: Path) -> None:
    clock = FakeClock()
    with respx.mock as router:
        route = router.get(DETAIL_URL).mock(return_value=httpx.Response(502))
        async with _adapter(tmp_path, clock, max_retries=2) as adapter:
            with pytest.raises(SourceUnavailable):
                await adapter.get_dataset(KEMISKINAN)
    assert route.call_count == 2


@pytest.mark.parametrize("exc", [httpx.ConnectError("refused"), httpx.ReadTimeout("slow")])
async def test_transport_errors_forever(tmp_path: Path, exc: Exception) -> None:
    clock = FakeClock()
    with respx.mock as router:
        route = router.get(DETAIL_URL).mock(side_effect=exc)
        async with _adapter(tmp_path, clock) as adapter:
            with pytest.raises(SourceUnavailable) as info:
                await adapter.get_dataset(KEMISKINAN)
    assert route.call_count == 5 and info.value.attempts == 5
    assert [s for s in clock.sleeps if s >= 1.0] == [1.0, 2.0, 4.0, 8.0]


async def test_backoff_capped_by_backoff_max(tmp_path: Path) -> None:
    clock = FakeClock()
    with respx.mock as router:
        router.get(DETAIL_URL).mock(side_effect=httpx.ConnectError("x"))
        async with _adapter(tmp_path, clock, backoff_max_s=3) as adapter:
            with pytest.raises(SourceUnavailable):
                await adapter.get_dataset(KEMISKINAN)
    assert [s for s in clock.sleeps if s >= 1.0] == [1.0, 2.0, 3.0, 3.0]


async def test_timeout_then_ok(tmp_path: Path) -> None:
    clock = FakeClock()
    with respx.mock as router:
        route = router.get(DETAIL_URL).mock(side_effect=[httpx.ReadTimeout("t"), _detail_ok()])
        async with _adapter(tmp_path, clock) as adapter:
            await adapter.get_dataset(KEMISKINAN)
    assert route.call_count == 2


async def test_404_not_retried(tmp_path: Path) -> None:
    clock = FakeClock()
    with respx.mock as router:
        route = router.get(DETAIL_URL).mock(
            return_value=httpx.Response(404, json={"message": "Dataset not found"})
        )
        async with _adapter(tmp_path, clock) as adapter:
            with pytest.raises(SourceNotFound):
                await adapter.get_dataset(KEMISKINAN)
    assert route.call_count == 1 and clock.sleeps == []


@pytest.mark.parametrize("status", [400, 401, 403, 301, 204])
async def test_other_status_is_protocol_error_without_retry(tmp_path: Path, status: int) -> None:
    clock = FakeClock()
    with respx.mock as router:
        route = router.get(DETAIL_URL).mock(return_value=httpx.Response(status))
        async with _adapter(tmp_path, clock) as adapter:
            with pytest.raises(SourceProtocolError):
                await adapter.get_dataset(KEMISKINAN)
    assert route.call_count == 1


# ---------------------------------------------------------------- bentuk respons


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="<html>bukan json</html>"),
        httpx.Response(200, json=[1, 2]),
        httpx.Response(200, json={"code": 200, "data": None}),
        httpx.Response(200, json={"code": 200, "data": {"name": "tanpa slug"}}),
        httpx.Response(200, json={"code": 200, "data": {"slug": "slug-lain"}}),
    ],
)
async def test_unexpected_detail_shapes(tmp_path: Path, response: httpx.Response) -> None:
    with respx.mock as router:
        router.get(DETAIL_URL).mock(return_value=response)
        async with _adapter(tmp_path, FakeClock()) as adapter:
            with pytest.raises(SourceProtocolError):
                await adapter.get_dataset(KEMISKINAN)


async def test_unexpected_bigdata_shapes(tmp_path: Path) -> None:
    dataset = parse_dataset({"slug": "x", "schema": "s", "table": "t"})
    with respx.mock as router:
        router.get(f"{BASE_URL}/bigdata/s/t").mock(
            return_value=httpx.Response(200, json={"data": "bukan list", "metadata": "x"})
        )
        async with _adapter(tmp_path, FakeClock()) as adapter:
            with pytest.raises(SourceProtocolError):
                await adapter.get_columns(dataset)
            with pytest.raises(SourceProtocolError):
                await adapter.query_rows(dataset)


async def test_dataset_without_schema_never_requests(tmp_path: Path) -> None:
    dataset = parse_dataset({"slug": "x"})
    with respx.mock(assert_all_called=False) as router:
        route = router.get(url__regex=r".*").mock(return_value=httpx.Response(200))
        async with _adapter(tmp_path, FakeClock()) as adapter:
            with pytest.raises(SourceProtocolError, match="schema/table"):
                await adapter.get_columns(dataset)
            bad = RawDataset(slug="x", record={"schema": "../etc", "table": "t"}, metadata={})
            with pytest.raises(InvalidSourceRequest):
                await adapter.query_rows(bad)
    assert route.call_count == 0


def test_reference_with_more_pages_is_protocol_error() -> None:
    with pytest.raises(SourceProtocolError, match="lebih dari satu halaman"):
        parse_reference({"data": [], "pagination": {"has_next": True}}, "/organisasi")


# ---------------------------------------------------------------- paginasi katalog


async def _snapshot(tmp_path: Path, pages: dict[int, Any], **kwargs: Any) -> Any:
    def catalog(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        value = pages.get(page, httpx.Response(200, json={"code": 200, "data": []}))
        return value if isinstance(value, httpx.Response) else httpx.Response(200, json=value)

    with respx.mock as router:
        route = router.get(f"{BASE_URL}/datasets").mock(side_effect=catalog)
        clock = FakeClock()  # satu jam untuk monotonic dan sleep
        adapter = OpenDataJabarAdapter(
            make_settings(tmp_path, max_retries=2),
            monotonic=clock.monotonic,
            sleep=clock.sleep,
            rng=lambda: 0.0,
            **kwargs,
        )
        async with adapter:
            snapshot = await adapter.list_datasets()
    return snapshot, route


async def test_catalog_real_pages_complete(tmp_path: Path) -> None:
    clock = FakeClock()
    with respx.mock(assert_all_called=False) as router:
        install_portal_routes(router)
        async with _adapter(tmp_path, clock) as adapter:
            snapshot = await adapter.list_datasets()
        calls = [c for c in router.calls if c.request.url.path.endswith("/datasets")]
    assert snapshot.complete and snapshot.total_data == 751 and len(snapshot.records) == 751
    assert [int(c.request.url.params["page"]) for c in calls] == list(range(1, 9))
    assert all(c.request.url.params["per_page"] == "100" for c in calls)
    assert all(find_private_keys(dict(r.record)) == [] for r in snapshot.records)


async def test_catalog_missing_middle_page(tmp_path: Path) -> None:
    pages = {1: _page(1, ["a", "b"], 3, 5), 2: httpx.Response(503), 3: _page(3, ["e"], 3, 5)}
    snapshot, route = await _snapshot(tmp_path, pages)
    assert not snapshot.complete and snapshot.pages_read == 1
    assert snapshot.error and snapshot.error.startswith("SourceUnavailable")
    assert [r.slug for r in snapshot.records] == ["a", "b"]
    assert route.call_count == 3  # halaman 1 + dua percobaan halaman 2


async def test_catalog_empty_middle_page(tmp_path: Path) -> None:
    pages = {1: _page(1, ["a"], 3, 3), 2: _page(2, [], 3, 3)}
    snapshot, _ = await _snapshot(tmp_path, pages)
    assert not snapshot.complete and snapshot.pages_read == 2 and snapshot.error is None


async def test_catalog_total_mismatch(tmp_path: Path) -> None:
    pages = {1: _page(1, ["a", "b"], 2, 5), 2: _page(2, ["c"], 2, 5)}
    snapshot, _ = await _snapshot(tmp_path, pages)
    assert snapshot.pages_read == 2 and len(snapshot.records) == 3
    assert not snapshot.complete


async def test_catalog_duplicates_dedup(tmp_path: Path) -> None:
    pages = {1: _page(1, ["a", "b"], 2, 3), 2: _page(2, ["b", "c"], 2, 3)}
    snapshot, _ = await _snapshot(tmp_path, pages)
    assert [r.slug for r in snapshot.records] == ["a", "b", "c"]
    assert snapshot.duplicates == 1 and snapshot.complete


async def test_catalog_duplicates_hide_missing_record(tmp_path: Path) -> None:
    pages = {1: _page(1, ["a", "b"], 2, 4), 2: _page(2, ["b", "c"], 2, 4)}
    snapshot, _ = await _snapshot(tmp_path, pages)
    assert len(snapshot.records) == 3 and not snapshot.complete


async def test_catalog_uses_total_page_without_has_next(tmp_path: Path) -> None:
    first = _page(1, ["a"], 2, 2)
    second = _page(2, ["b"], 2, 2)
    for body in (first, second):
        del body["pagination"]["has_next"]
    snapshot, route = await _snapshot(tmp_path, {1: first, 2: second})
    assert snapshot.complete and route.call_count == 2


async def test_catalog_safety_limit(tmp_path: Path) -> None:
    pages = {p: _page(p, [f"s{p}"], 99, 99) for p in range(1, 30)}
    snapshot, route = await _snapshot(tmp_path, pages, max_catalog_pages=4)
    assert route.call_count == 4 and snapshot.pages_read == 4 and not snapshot.complete


async def test_catalog_skips_records_without_slug_and_bad_data(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="mcp_bogor.adapters.opendata_jabar")
    bad_record = _page(1, ["a"], 1, 3)
    bad_record["data"] += [{"name": "RAHASIA-TANPA-SLUG"}, {"slug": ""}]
    snapshot, _ = await _snapshot(tmp_path, {1: bad_record})
    assert [r.slug for r in snapshot.records] == ["a"] and not snapshot.complete
    assert snapshot.skipped == 2
    summary = [
        json.loads(r.getMessage())
        for r in caplog.records
        if json.loads(r.getMessage())["event"] == "catalog_snapshot"
    ]
    assert summary[0]["skipped"] == 2 and summary[0]["skipped_pages"] == [1]
    assert summary[0]["complete"] is False and summary[0]["records"] == 1
    assert "RAHASIA" not in caplog.text  # isi record tidak dicatat
    snapshot, _ = await _snapshot(tmp_path, {1: {"code": 200, "data": {"bukan": "list"}}})
    assert snapshot.error and "SourceProtocolError" in snapshot.error


# ---------------------------------------------------------------- health


async def test_health_cached_and_single_attempt(tmp_path: Path) -> None:
    clock = FakeClock()
    with respx.mock as router:
        route = router.get(f"{BASE_URL}/datasets").mock(
            side_effect=[httpx.Response(503), httpx.Response(200, json={"data": []})]
        )
        async with _adapter(tmp_path, clock) as adapter:
            first = await adapter.health()
            assert not first.ok and "SourceUnavailable" in (first.detail or "")
            assert (await adapter.health()) is first  # cache 60 detik
            clock.now += 61
            second = await adapter.health()
    assert second.ok and second.status == 200
    assert route.call_count == 2  # tanpa retry: satu permintaan per pemeriksaan
    params = route.calls[0].request.url.params
    assert (params["page"], params["per_page"]) == ("1", "1")


# ---------------------------------------------------------------- keamanan


@pytest.mark.parametrize(
    "path",
    [
        "/datasets/opendata/contoh/view",
        "/datasets/view",
        "/datasets/..",
        "/datasets/a/b",
        "/bigdata/s",
        "/bigdata/s/t/u",
        "/history_datasets/1",
        "/users",
        "/datasets/Huruf",
    ],
)
def test_check_endpoint_rejects(path: str) -> None:
    with pytest.raises(InvalidSourceRequest):
        check_endpoint(path)


@pytest.mark.parametrize(
    "path",
    [
        "/datasets",
        "/datasets/a-b_c",
        "/bigdata/s_1/t-2",
        "/history_datasets",
        "/organisasi",
        "/topik",
    ],
)
def test_check_endpoint_allows_six_endpoints(path: str) -> None:
    assert check_endpoint(path) == path


def test_build_path_encodes_and_validates() -> None:
    assert build_path("datasets", "review-anggaran") == "/datasets/review-anggaran"
    with pytest.raises(InvalidSourceRequest):
        build_path("datasets", "a b")


def test_where_serialized_compact() -> None:
    params = build_query_params(
        {"segmen": ["SIMPANG \u2013 A"], "tahun": [2025, 2026]}, None, 1, 50
    )
    assert params["where"] == '{"segmen":["SIMPANG \u2013 A"],"tahun":[2025,2026]}'
    assert "sort" not in params
    assert build_query_params(None, "tahun:desc", 2, 10) == {
        "page": 2,
        "per_page": 10,
        "sort": "tahun:desc",
    }


def test_adapter_has_no_write_methods() -> None:
    names = {name.lower() for name in dir(OpenDataJabarAdapter)}
    assert not names & {"post", "put", "patch", "delete", "request", "send", "stream"}


async def test_where_sent_to_server(tmp_path: Path) -> None:
    dataset = parse_dataset({"slug": "x", "schema": "s", "table": "t"})
    with respx.mock as router:
        route = router.get(f"{BASE_URL}/bigdata/s/t").mock(
            return_value=httpx.Response(200, json={"data": [], "metadata": []})
        )
        async with _adapter(tmp_path, FakeClock()) as adapter:
            await adapter.query_rows(dataset, where={"tahun": [2025]}, sort="tahun:asc")
    params = route.calls[0].request.url.params
    assert json.loads(params["where"]) == {"tahun": [2025]} and params["sort"] == "tahun:asc"


# ---------------------------------------------------------------- log dan privasi


async def test_request_log_is_json_without_row_values(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="mcp_bogor.adapters.opendata_jabar")
    with respx.mock(assert_all_called=False) as router:
        install_portal_routes(router)
        async with _adapter(tmp_path, FakeClock()) as adapter:
            dataset = await adapter.get_dataset(KEMISKINAN)
            await adapter.query_rows(dataset, where={"tahun": [2025]})
            await adapter.list_organisasi()
    events = [json.loads(r.getMessage()) for r in caplog.records]
    assert [e["path"] for e in events] == [
        f"/datasets/{KEMISKINAN}",
        f"/bigdata/{dataset.schema}/{dataset.table}",
        "/organisasi",
    ]
    for event in events:
        assert set(event) == {"event", "path", "status", "attempt", "duration_ms"}
        assert event["status"] == 200 and event["attempt"] == 1
    text = caplog.text
    assert "5.89" not in text and "2025" not in text and "@" not in text
    assert "kotabogor.go.id" not in text


async def test_failed_request_logged_with_error_type(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="mcp_bogor.adapters.opendata_jabar")
    with respx.mock as router:
        router.get(DETAIL_URL).mock(side_effect=[httpx.ConnectError("x"), _detail_ok()])
        async with _adapter(tmp_path, FakeClock()) as adapter:
            await adapter.get_dataset(KEMISKINAN)
    events = [json.loads(r.getMessage()) for r in caplog.records]
    assert events[0]["status"] is None and events[0]["error"] == "ConnectError"
    assert events[1]["attempt"] == 2 and events[1]["status"] == 200


async def test_rows_keep_public_columns_but_drop_account_keys(tmp_path: Path) -> None:
    """KK-L: kolom data publik bernama alamat/email dipertahankan; kunci akun dibuang."""
    dataset = parse_dataset({"slug": "x", "schema": "s", "table": "t"})
    body = {
        "data": [
            {"nama_sekolah": "SDN 1", "alamat": "Jl. Contoh 1", "username": "u", "tahun": 2025}
        ],
        "metadata": ["nama_sekolah", "alamat", "tahun"],
    }
    with respx.mock as router:
        router.get(f"{BASE_URL}/bigdata/s/t").mock(return_value=httpx.Response(200, json=body))
        async with _adapter(tmp_path, FakeClock()) as adapter:
            page = await adapter.query_rows(dataset)
    assert page.rows == [{"nama_sekolah": "SDN 1", "alamat": "Jl. Contoh 1", "tahun": 2025}]


def test_parse_dataset_drops_private_fields() -> None:
    raw = {
        "slug": "x",
        "username": "u",
        "organisasi_email": "a@b.test",
        "organisasi_notelp": "0251-1",
        "organisasi_alamat": "Jl.",
        "metadata": [
            {"key": "Kontak Produsen", "value": "0812", "muid": 3},
            {"key": "Satuan Dataset", "value": "Persen", "muid": None},
        ],
        "history_draft": [{"type": "approve", "cdate": "2026-01-01", "username": "w"}],
    }
    dataset = parse_dataset(raw)
    assert find_private_keys(dict(dataset.record)) == []
    assert dataset.metadata == {"Satuan Dataset": "Persen"}
    assert dataset.record["history_draft"] == [{"type": "approve", "cdate": "2026-01-01"}]
    assert raw["username"] == "u"  # input tidak diubah
