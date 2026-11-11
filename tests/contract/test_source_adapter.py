"""Tes kontrak SourceAdapter (ADR-002): suite yang sama untuk OpenDataJabarAdapter (respx
meniru portal dari rekaman 2026-11-10) dan FakeSourceAdapter. Tanpa jaringan."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import respx

from mcp_bogor.adapters.base import (
    CatalogSnapshot,
    ColumnProbe,
    InvalidSourceRequest,
    RawDataset,
    RowPage,
    SourceAdapter,
    SourceNotFound,
)
from mcp_bogor.adapters.opendata_jabar import OpenDataJabarAdapter
from mcp_bogor.adapters.sanitize import find_private_keys
from tests.fakes import (
    FakeClock,
    FakeSourceAdapter,
    install_portal_routes,
    make_settings,
)

pytestmark = pytest.mark.anyio

KEMISKINAN = "tingkat-kemiskinan-di-kota-bogor-2"
PAJAJARAN = (
    "kecepatan-lalu-lintas-segmen-1-dan-2-berdasarkan-hari-operasional-di-jalan-raya-pajajaran-"
    "kota-bogor"
)
KM12 = "persentase-penanganan-pengaduan-masyarakat-ke-inspektorat-daerah-kota-bogor"


@pytest.fixture(params=["opendata_jabar", "fake"])
async def adapter(request: pytest.FixtureRequest, tmp_path: Path) -> AsyncIterator[Any]:
    if request.param == "fake":
        yield FakeSourceAdapter.from_recorded()
        return
    clock = FakeClock()
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as router:
        install_portal_routes(router)
        writes = router.route(method__in=["POST", "PUT", "PATCH", "DELETE"])
        async with OpenDataJabarAdapter(
            make_settings(tmp_path), monotonic=clock.monotonic, sleep=clock.sleep
        ) as real:
            yield real
        assert writes.call_count == 0
        assert all(call.request.method == "GET" for call in router.calls)
        assert not any("view" in call.request.url.path.split("/") for call in router.calls)


def _private(obj: Any) -> list[str]:
    if isinstance(obj, RawDataset):
        return find_private_keys(dict(obj.record)) + find_private_keys(dict(obj.metadata))
    return find_private_keys(obj)


async def test_is_source_adapter(adapter: Any) -> None:
    assert isinstance(adapter, SourceAdapter)
    assert isinstance(adapter.source_id, str) and adapter.source_id


async def test_list_datasets_complete(adapter: Any) -> None:
    snapshot = await adapter.list_datasets()
    assert isinstance(snapshot, CatalogSnapshot)
    assert snapshot.complete and snapshot.error is None
    assert snapshot.skipped == 0 and snapshot.duplicates == 0
    assert snapshot.total_data == len(snapshot.records) == 751
    assert snapshot.pages_read == snapshot.total_page == 8
    slugs = [r.slug for r in snapshot.records]
    assert len(set(slugs)) == 751 and {KEMISKINAN, PAJAJARAN, KM12} <= set(slugs)
    assert all(isinstance(r, RawDataset) and r.schema and r.table for r in snapshot.records)
    assert all(_private(r) == [] for r in snapshot.records)


async def test_get_dataset(adapter: Any) -> None:
    dataset = await adapter.get_dataset(KEMISKINAN)
    assert dataset.slug == KEMISKINAN
    assert dataset.metadata["Dimensi Dataset Akhir"] == "2025"
    assert dataset.metadata["Satuan Dataset"] == "Persen"
    assert "Kontak Produsen" not in dataset.metadata
    assert dataset.record["validate"] == "approve"
    assert _private(dataset) == []


async def test_get_dataset_not_found(adapter: Any) -> None:
    with pytest.raises(SourceNotFound):
        await adapter.get_dataset("slug-yang-tidak-ada-di-portal")


@pytest.mark.parametrize("slug", ["a/b", "..", "view", "Huruf-Besar", "spasi kosong", ""])
async def test_get_dataset_rejects_unsafe_slug(adapter: Any, slug: str) -> None:
    with pytest.raises(InvalidSourceRequest):
        await adapter.get_dataset(slug)


async def test_get_columns(adapter: Any) -> None:
    dataset = await adapter.get_dataset(PAJAJARAN)
    probe = await adapter.get_columns(dataset)
    assert isinstance(probe, ColumnProbe)
    assert probe.columns[-1] == "tahun" and "no_segmen" in probe.columns
    assert probe.metadata_filter["tahun"][0] == "2015"  # tahun teks, belum dinormalkan
    assert probe.metadata_filter["kecepatan"] == []  # free text
    assert probe.total_rows == 88


async def test_query_rows_where_sort_page(adapter: Any) -> None:
    dataset = await adapter.get_dataset(PAJAJARAN)
    page = await adapter.query_rows(
        dataset, where={"tahun": [2025], "hari_operasional": ["HARI KERJA"]}, sort="kecepatan:desc"
    )
    assert isinstance(page, RowPage)
    assert len(page.rows) == 4 and page.pagination.total_data == 4
    speeds = [r["kecepatan"] for r in page.rows]
    assert speeds == sorted(speeds, reverse=True) and speeds[0] == 49.23
    assert page.rows[0]["segmen"].startswith("SIMPANG\n")  # label asli, belum dinormalkan
    second = await adapter.query_rows(dataset, sort="tahun:asc", page=2, per_page=10)
    assert len(second.rows) == 10 and second.pagination.page == 2
    assert second.pagination.total_page == 9 and second.pagination.has_next is True


async def test_query_rows_keeps_raw_values(adapter: Any) -> None:
    dataset = await adapter.get_dataset("perkembangan-jumlah-kunjungan-wisatawan-di-kota-bogor")
    page = await adapter.query_rows(dataset, where={"tahun": [2024]})
    assert page.rows[0]["perkembangan_kunjungan"] == "8.093.203"  # NB-07 di services, bukan di sini


@pytest.mark.parametrize(
    "kwargs",
    [
        {"sort": "tahun"},
        {"sort": "tahun:ASC"},
        {"where": {"tahun; drop": [1]}},
        {"where": {"tahun": "2024"}},
        {"where": {"tahun": [None]}},
        {"page": 0},
        {"per_page": 0},
        {"per_page": 1001},
    ],
)
async def test_query_rows_rejects_bad_params(adapter: Any, kwargs: dict[str, Any]) -> None:
    dataset = await adapter.get_dataset(KEMISKINAN)
    with pytest.raises(InvalidSourceRequest):
        await adapter.query_rows(dataset, **kwargs)


async def test_health(adapter: Any) -> None:
    health = await adapter.health()
    assert health.ok


async def test_references(adapter: Any) -> None:
    organisasi = await adapter.list_organisasi()
    topik = await adapter.list_topik()
    assert len(organisasi) == 39 and len(topik) == 45
    assert {"id", "name", "slug"} <= set(organisasi[0]) and {"id", "name", "slug"} <= set(topik[0])
    assert any(o["name"] == "Inspektorat" for o in organisasi)
    assert _private(organisasi) == [] and _private(topik) == []


async def test_probe_one_request_columns_and_sample(adapter: Any) -> None:
    """KK-O: satu GET halaman 1 memberi nama kolom, metadata_filter, dan sampel baris."""
    dataset = await adapter.get_dataset(PAJAJARAN)
    before = adapter.requests_sent
    probe, page = await adapter.probe(dataset, per_page=10)
    assert adapter.requests_sent == before + 1
    assert isinstance(probe, ColumnProbe) and isinstance(page, RowPage)
    assert probe.columns[-1] == "tahun" and probe.total_rows == 88
    assert probe.metadata_filter["hari_operasional"] == ["HARI KERJA", "HARI LIBUR"]
    assert len(page.rows) == 10 and page.columns == probe.columns
    assert _private(page.rows) == []
    with pytest.raises(InvalidSourceRequest):
        await adapter.probe(dataset, per_page=1001)
