"""Fake dan simulator portal dari fixture rekaman 2026-11-10 (dipakai Langkah 4 s.d. 8).

- `RecordedPortal`: data rekaman yang di-commit (halaman katalog 1 dan 8, catalog_slim.json,
  detail, /bigdata, /organisasi, /topik, 404). Halaman katalog 2 s.d. 7 dibangun dari
  catalog_slim.json karena tidak ada di git (Dok 5).
- `install_portal_routes`: respx meniru portal (where/sort/paginasi di sisi "server").
- `FakeSourceAdapter`: SourceAdapter in-memory dengan perilaku yang sama, memakai fungsi
  parse dan validasi dari adaptor asli agar keluarannya setara.
- `FakeClock`: jam monotonic + sleep palsu untuk tes laju dan retry.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

import httpx
import respx

from mcp_bogor.adapters.base import (
    CatalogSnapshot,
    ColumnProbe,
    RawDataset,
    RowPage,
    SourceError,
    SourceHealth,
    SourceNotFound,
    SourceProtocolError,
    SourceUnavailable,
)
from mcp_bogor.adapters.opendata_jabar import (
    CATALOG_PER_PAGE,
    build_query_params,
    parse_columns,
    parse_dataset,
    parse_reference,
    parse_row_page,
    total_pages,
    validate_segment,
)
from mcp_bogor.config import Settings

RECORDED = Path(__file__).parent / "fixtures" / "recorded" / "2026-11-10"
BASE_URL = "https://portal.test/api"
TEST_USER_AGENT = "mcp-bogor-test/0.1 (+tim-mcp@contoh.test)"
NOT_FOUND_BODY = {"message": "Dataset not found"}


def make_settings(tmp_path: Path, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "source_base_url": BASE_URL,
        "user_agent": TEST_USER_AGENT,
        "db_path": tmp_path / "var" / "test.db",
        "cache_dir": tmp_path / "var" / "cache",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


class FakeClock:
    """Jam monotonic palsu; sleep menambah waktu dan memberi giliran ke task lain."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds
        # Beberapa giliran agar task lain (mis. yang sedang menerima 429) sempat jalan.
        for _ in range(10):
            await asyncio.sleep(0)


# ---------------------------------------------------------------- data rekaman


@dataclass
class RecordedPortal:
    catalog_pages: dict[int, dict[str, Any]]
    slim_records: list[dict[str, Any]]
    details: dict[str, dict[str, Any]]
    bigdata: dict[tuple[str, str], dict[str, Any]]
    organisasi: dict[str, Any]
    topik: dict[str, Any]
    not_found: dict[str, Any] = field(default_factory=lambda: dict(NOT_FOUND_BODY))

    @property
    def total_data(self) -> int:
        return len(self.slim_records)


@cache
def recorded_portal() -> RecordedPortal:
    exchanges = [
        json.loads(p.read_text(encoding="utf-8"))
        for p in sorted(RECORDED.glob("[0-9][0-9][0-9]_*.json"))
    ]
    slim = json.loads((RECORDED / "catalog_slim.json").read_text(encoding="utf-8"))["records"]
    raw_pages: dict[int, dict[str, Any]] = {}
    details: dict[str, dict[str, Any]] = {}
    bigdata: dict[tuple[str, str], dict[str, Any]] = {}
    refs: dict[str, dict[str, Any]] = {}
    not_found = dict(NOT_FOUND_BODY)
    for exchange in exchanges:
        path = exchange["request"]["path"]
        body = exchange["response"]["json"]
        status = exchange["response"]["status"]
        parts = path.strip("/").split("/")
        if path == "/datasets":
            raw_pages[int(exchange["request"]["params"]["page"])] = body
        elif parts[0] == "datasets" and status == 404:
            not_found = body
        elif parts[0] == "datasets":
            details[parts[1]] = body["data"]
        elif parts[0] == "bigdata":
            bigdata[(parts[1], parts[2])] = body
        else:
            refs[parts[0]] = body
    total = len(slim)
    pages = total_pages(total, CATALOG_PER_PAGE)
    catalog_pages = {}
    for page in range(1, pages + 1):
        if page in raw_pages:
            catalog_pages[page] = raw_pages[page]
            continue
        chunk = slim[(page - 1) * CATALOG_PER_PAGE : page * CATALOG_PER_PAGE]
        catalog_pages[page] = {
            "code": 200,
            "message": "OK",
            "data": chunk,
            "error": None,
            "pagination": _pagination(page, CATALOG_PER_PAGE, pages, total),
        }
    return RecordedPortal(
        catalog_pages=catalog_pages,
        slim_records=slim,
        details=details,
        bigdata=bigdata,
        organisasi=refs["organisasi"],
        topik=refs["topik"],
        not_found=not_found,
    )


def _pagination(page: int, per_page: int, total_page: int, total_data: int) -> dict[str, Any]:
    return {
        "page": page,
        "per_page": per_page,
        "total_page": total_page,
        "total_data": total_data,
        "has_next": page < total_page,
        "has_previous": page > 1,
    }


def apply_query(
    body: Mapping[str, Any],
    where: Mapping[str, Sequence[Any]] | None,
    sort: str | None,
    page: int,
    per_page: int,
) -> dict[str, Any]:
    """Tiru /bigdata di sisi server: filter where (perbandingan teks), sort, paginasi."""
    rows = list(body["data"])
    for column, values in (where or {}).items():
        wanted = {str(v) for v in values}
        rows = [r for r in rows if str(r.get(column)) in wanted]
    if sort:
        column, direction = sort.split(":")
        rows.sort(key=lambda r: (r.get(column) is None, r.get(column)), reverse=direction == "desc")
    count = len(rows)
    pages = total_pages(count, per_page)
    out = dict(body)
    out["data"] = rows[(page - 1) * per_page : page * per_page]
    out["pagination"] = _pagination(page, per_page, pages, count)
    return out


# ---------------------------------------------------------------- simulator respx


def install_portal_routes(
    router: respx.MockRouter, portal: RecordedPortal | None = None, base_url: str = BASE_URL
) -> None:
    """Daftarkan route GET yang meniru portal dari data rekaman."""
    portal = portal or recorded_portal()

    def catalog(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("page", 1))
        per_page = int(request.url.params.get("per_page", CATALOG_PER_PAGE))
        if per_page != CATALOG_PER_PAGE:  # health(): page=1, per_page=1
            records = portal.catalog_pages[1]["data"][:per_page]
            pages = total_pages(portal.total_data, per_page)
            body = {
                "code": 200,
                "message": "OK",
                "data": records,
                "error": None,
                "pagination": _pagination(page, per_page, pages, portal.total_data),
            }
            return httpx.Response(200, json=body)
        if page not in portal.catalog_pages:
            return httpx.Response(200, json={"code": 200, "data": [], "pagination": {}})
        return httpx.Response(200, json=portal.catalog_pages[page])

    def detail(request: httpx.Request, slug: str) -> httpx.Response:
        record = portal.details.get(slug)
        if record is None:
            return httpx.Response(404, json=portal.not_found)
        return httpx.Response(
            200, json={"code": 200, "message": "OK", "data": record, "error": None}
        )

    def big(request: httpx.Request, schema: str, table: str) -> httpx.Response:
        body = portal.bigdata.get((schema, table))
        if body is None:
            return httpx.Response(404, json={"message": "Table not found"})
        params = request.url.params
        where = json.loads(params["where"]) if "where" in params else None
        page = int(params.get("page", 1))
        per_page = int(params.get("per_page", 100))
        return httpx.Response(
            200, json=apply_query(body, where, params.get("sort"), page, per_page)
        )

    router.get(f"{base_url}/datasets").mock(side_effect=catalog)
    router.get(url__regex=rf"{base_url}/datasets/(?P<slug>[^/?]+)$").mock(side_effect=detail)
    router.get(url__regex=rf"{base_url}/bigdata/(?P<schema>[^/]+)/(?P<table>[^/?]+)").mock(
        side_effect=big
    )
    router.get(f"{base_url}/organisasi").mock(
        return_value=httpx.Response(200, json=portal.organisasi)
    )
    router.get(f"{base_url}/topik").mock(return_value=httpx.Response(200, json=portal.topik))


# ---------------------------------------------------------------- fake adapter


class FakeSourceAdapter:
    """SourceAdapter in-memory dari fixture rekaman.

    Detail memakai rekaman /datasets/{slug} bila ada, selain itu record catalog_slim.
    `remove(slug)` meniru dataset yang dihapus portal (TODO Langkah 5); `fail_with`
    membuat semua panggilan gagal dengan galat tersebut (UT-14 di Langkah 8).
    """

    source_id = "fake-recorded"

    def __init__(self, portal: RecordedPortal | None = None) -> None:
        self._portal = portal or recorded_portal()
        self._catalog = list(self._portal.slim_records)
        self._removed: set[str] = set()
        self.fail_with: SourceError | None = None
        self.calls: list[str] = []

    @classmethod
    def from_recorded(cls) -> FakeSourceAdapter:
        return cls()

    def remove(self, slug: str) -> None:
        self._removed.add(slug)

    def _enter(self, name: str) -> None:
        self.calls.append(name)
        if self.fail_with is not None:
            raise self.fail_with

    async def list_datasets(self) -> CatalogSnapshot:
        self._enter("list_datasets")
        records = [parse_dataset(r) for r in self._catalog if r["slug"] not in self._removed]
        pages = total_pages(len(records), CATALOG_PER_PAGE)
        return CatalogSnapshot(
            records=records,
            total_data=len(records),
            pages_read=pages,
            total_page=pages,
            complete=True,
        )

    async def get_dataset(self, slug: str) -> RawDataset:
        self._enter("get_dataset")
        validate_segment(slug, "slug")
        if slug in self._removed:
            raise SourceNotFound(f"404 pada /datasets/{slug}", path=f"/datasets/{slug}")
        record = self._portal.details.get(slug) or next(
            (r for r in self._catalog if r["slug"] == slug), None
        )
        if record is None:
            raise SourceNotFound(f"404 pada /datasets/{slug}", path=f"/datasets/{slug}")
        return parse_dataset(record)

    def _body(self, dataset: RawDataset) -> dict[str, Any]:
        if dataset.schema is None or dataset.table is None:
            raise SourceProtocolError(f"dataset {dataset.slug} tanpa schema/table")
        validate_segment(dataset.schema, "schema")
        validate_segment(dataset.table, "table")
        body = self._portal.bigdata.get((dataset.schema, dataset.table))
        if body is None:
            raise SourceUnavailable(f"/bigdata {dataset.slug} tidak ada di fixture rekaman")
        return body

    async def get_columns(self, dataset: RawDataset) -> ColumnProbe:
        self._enter("get_columns")
        return parse_columns(apply_query(self._body(dataset), None, None, 1, 1))

    async def query_rows(
        self,
        dataset: RawDataset,
        where: Mapping[str, Sequence[Any]] | None = None,
        sort: str | None = None,
        page: int = 1,
        per_page: int = 100,
    ) -> RowPage:
        self._enter("query_rows")
        build_query_params(where, sort, page, per_page)  # validasi sama dengan adaptor asli
        return parse_row_page(apply_query(self._body(dataset), where, sort, page, per_page))

    async def health(self) -> SourceHealth:
        self.calls.append("health")
        if self.fail_with is not None:
            return SourceHealth(ok=False, detail=f"{type(self.fail_with).__name__}")
        return SourceHealth(ok=True, status=200, latency_s=0.0)

    async def list_organisasi(self) -> list[dict[str, Any]]:
        self._enter("list_organisasi")
        return parse_reference(self._portal.organisasi, "/organisasi")

    async def list_topik(self) -> list[dict[str, Any]]:
        self._enter("list_topik")
        return parse_reference(self._portal.topik, "/topik")
