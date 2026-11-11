"""OpenDataJabarAdapter: satu-satunya pemakai httpx dan satu-satunya yang tahu bentuk API portal.

Aturan akses (Dok 2 AK-01..06, KK-H):
- hanya GET ke enam endpoint: /datasets, /datasets/{slug}, /bigdata/{schema}/{table},
  /history_datasets, /organisasi, /topik; segmen "view" dan ".." ditolak sebelum dikirim;
- segmen slug/schema/table hanya [a-z0-9_-] dan di-URL-encode;
- jarak antarpermintaan ≥ MCP_REQUEST_DELAY_S secara global (asyncio.Lock + jam monotonic);
- total percobaan ≤ MCP_MAX_RETRIES: 429/5xx/koneksi/timeout diulang dengan backoff
  eksponensial + jitter dan menghormati Retry-After; tunggu > MCP_BACKOFF_MAX_S → langsung
  gagal tanpa tidur (KB-24); 404 → SourceNotFound; 4xx lain → SourceProtocolError.

Bentuk respons asli (rekaman 2026-11-10, KB-20):
- amplop `{code, message, data, error, pagination}`; 404 hanya `{message}`;
- `/bigdata`: `metadata` = list nama kolom, `metadata_filter` = list `{key, type, value}`;
- `/datasets/{slug}`: `metadata` = list `{id, dataset_id, type, key, value, ...}`.

Semua keluaran disanitasi (adapters/sanitize, mode "drop"); nilai tidak dinormalkan.
"""

from __future__ import annotations

import asyncio
import json
import math
import random
import re
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote

import httpx

from mcp_bogor.adapters.base import (
    CatalogSnapshot,
    ColumnProbe,
    InvalidSourceRequest,
    Pagination,
    RawDataset,
    RowPage,
    SourceError,
    SourceHealth,
    SourceNotFound,
    SourceProtocolError,
    SourceRateLimited,
    SourceUnavailable,
)
from mcp_bogor.adapters.sanitize import sanitize
from mcp_bogor.config import Settings, require_user_agent
from mcp_bogor.logging import get_logger, log_event

SOURCE_ID = "opendata-kotabogor"
LOGGER = get_logger(__name__)

SEGMENT_RE = re.compile(r"[a-z0-9_-]+")
COLUMN_RE = re.compile(r"[a-z0-9_]+")
SORT_RE = re.compile(r"[a-z0-9_]+:(asc|desc)")
FORBIDDEN_SEGMENTS = frozenset({"view", "..", "."})
ENDPOINTS = (
    re.compile(r"/datasets"),
    re.compile(r"/datasets/[a-z0-9_-]+"),
    re.compile(r"/bigdata/[a-z0-9_-]+/[a-z0-9_-]+"),
    re.compile(r"/history_datasets"),
    re.compile(r"/organisasi"),
    re.compile(r"/topik"),
)

CATALOG_PER_PAGE = 100
MAX_CATALOG_PAGES = 20
MAX_PER_PAGE = 1000
HEALTH_TTL_S = 60.0
BACKOFF_BASE_S = 1.0


# ---------------------------------------------------------------- path dan parameter


def validate_segment(value: Any, name: str) -> str:
    """Segmen path (slug/schema/table): hanya [a-z0-9_-], bukan "view"."""
    if not isinstance(value, str) or not SEGMENT_RE.fullmatch(value) or value in FORBIDDEN_SEGMENTS:
        raise InvalidSourceRequest(f"{name} tidak sah untuk path portal")
    return value


def check_endpoint(path: str) -> str:
    """Allowlist enam endpoint (KK-H); segmen "view"/".." ditolak (AK-02)."""
    segments = path.strip("/").split("/")
    if any(segment in FORBIDDEN_SEGMENTS for segment in segments):
        raise InvalidSourceRequest("path memuat segmen terlarang", path=path)
    if not any(pattern.fullmatch(path) for pattern in ENDPOINTS):
        raise InvalidSourceRequest("endpoint di luar allowlist", path=path)
    return path


def build_path(*segments: str) -> str:
    return check_endpoint("/" + "/".join(quote(segment, safe="") for segment in segments))


def build_query_params(
    where: Mapping[str, Sequence[Any]] | None,
    sort: str | None,
    page: int,
    per_page: int,
) -> dict[str, Any]:
    """Parameter /bigdata. `where` → JSON ringkas di server portal, mis. {"tahun":[2025]}."""
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        raise InvalidSourceRequest("page harus ≥ 1")
    if (
        not isinstance(per_page, int)
        or isinstance(per_page, bool)
        or not 1 <= per_page <= MAX_PER_PAGE
    ):
        raise InvalidSourceRequest(f"per_page harus 1..{MAX_PER_PAGE}")
    params: dict[str, Any] = {"page": page, "per_page": per_page}
    if where:
        clean: dict[str, list[Any]] = {}
        for column, values in where.items():
            if not isinstance(column, str) or not COLUMN_RE.fullmatch(column):
                raise InvalidSourceRequest("nama kolom where tidak sah")
            if isinstance(values, str | bytes) or not isinstance(values, Iterable):
                raise InvalidSourceRequest("nilai where harus daftar")
            items = list(values)
            if not all(isinstance(v, str | int | float) and not isinstance(v, bool) for v in items):
                raise InvalidSourceRequest("nilai where harus teks atau angka")
            clean[column] = items
        params["where"] = json.dumps(clean, ensure_ascii=False, separators=(",", ":"))
    if sort is not None:
        if not isinstance(sort, str) or not SORT_RE.fullmatch(sort):
            raise InvalidSourceRequest("sort harus 'kolom:asc' atau 'kolom:desc'")
        params["sort"] = sort
    return params


# ---------------------------------------------------------------- perata (KB-20)


def flatten_metadata_filter(raw: Any) -> dict[str, list[Any]]:
    """`metadata_filter` portal → {nama_kolom: nilai_unik} untuk services/periods.

    Menerima list `{key, value}` (bentuk asli) atau dict yang sudah rata. Entri tanpa
    `key` teks dilewati; `value` yang bukan list dianggap kosong.
    """
    if isinstance(raw, Mapping):
        return {str(k): list(v) if isinstance(v, list) else [] for k, v in raw.items()}
    flat: dict[str, list[Any]] = {}
    for entry in raw if isinstance(raw, Iterable) and not isinstance(raw, str | bytes) else ():
        if isinstance(entry, Mapping) and isinstance(entry.get("key"), str):
            value = entry.get("value")
            flat.setdefault(entry["key"], list(value) if isinstance(value, list) else [])
    return flat


def flatten_metadata(raw: Any) -> dict[str, Any]:
    """`metadata[]` record dataset → {key: value}. Key ganda: kemunculan pertama dipakai."""
    flat: dict[str, Any] = {}
    for entry in raw if isinstance(raw, list) else ():
        if isinstance(entry, Mapping) and isinstance(entry.get("key"), str):
            flat.setdefault(entry["key"], entry.get("value"))
    return flat


# ---------------------------------------------------------------- parse respons (dipakai fake)


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value)
    return None


def parse_pagination(raw: Any) -> Pagination:
    if not isinstance(raw, Mapping):
        return Pagination()
    has_next = raw.get("has_next")
    return Pagination(
        page=_optional_int(raw.get("page")),
        per_page=_optional_int(raw.get("per_page")),
        total_page=_optional_int(raw.get("total_page")),
        total_data=_optional_int(raw.get("total_data")),
        has_next=has_next if isinstance(has_next, bool) else None,
    )


def _data_list(body: Mapping[str, Any], what: str) -> list[dict[str, Any]]:
    data = body.get("data")
    if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
        raise SourceProtocolError(f"{what}: field data bukan daftar objek")
    return data


def parse_dataset(record: Any) -> RawDataset:
    """Record dataset portal → RawDataset tersanitasi dengan metadata rata."""
    if not isinstance(record, Mapping):
        raise SourceProtocolError("record dataset bukan objek")
    clean = sanitize(dict(record), mode="drop")
    slug = clean.get("slug")
    if not isinstance(slug, str) or not slug:
        raise SourceProtocolError("record dataset tanpa slug")
    return RawDataset(slug=slug, record=clean, metadata=flatten_metadata(clean.get("metadata")))


def parse_columns(body: Mapping[str, Any]) -> ColumnProbe:
    columns = body.get("metadata")
    if not isinstance(columns, list) or not all(isinstance(c, str) for c in columns):
        raise SourceProtocolError("/bigdata: metadata bukan daftar nama kolom")
    return ColumnProbe(
        columns=list(columns),
        metadata_filter=flatten_metadata_filter(body.get("metadata_filter")),
        total_rows=parse_pagination(body.get("pagination")).total_data,
    )


def parse_row_page(body: Mapping[str, Any]) -> RowPage:
    """Baris /bigdata: hanya kunci akun yang dibuang; isi data publik tidak diubah (KK-L)."""
    rows = sanitize(_data_list(body, "/bigdata"), contacts=False)
    columns = body.get("metadata")
    return RowPage(
        rows=rows,
        columns=list(columns) if isinstance(columns, list) else [],
        pagination=parse_pagination(body.get("pagination")),
    )


def parse_reference(body: Mapping[str, Any], what: str) -> list[dict[str, Any]]:
    """Referensi /organisasi atau /topik, tanpa kontak/alamat (KK-J)."""
    if parse_pagination(body.get("pagination")).has_next:
        raise SourceProtocolError(f"{what}: referensi lebih dari satu halaman")
    return sanitize(_data_list(body, what), mode="drop")


# ---------------------------------------------------------------- adaptor


class OpenDataJabarAdapter:
    """SourceAdapter untuk portal Satu Data Kota Bogor (platform Open Data Jabar)."""

    source_id = SOURCE_ID

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Any] = asyncio.sleep,
        wall_clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        rng: Callable[[], float] = random.random,
        health_ttl_s: float = HEALTH_TTL_S,
        max_catalog_pages: int = MAX_CATALOG_PAGES,
        catalog_per_page: int = CATALOG_PER_PAGE,
    ) -> None:
        user_agent = require_user_agent(settings.user_agent)
        self._delay_s = settings.request_delay_s
        self._max_attempts = settings.max_retries
        self._backoff_max_s = settings.backoff_max_s
        self._monotonic = monotonic
        self._sleep = sleep
        self._wall_clock = wall_clock
        self._rng = rng
        self._health_ttl_s = health_ttl_s
        self._max_catalog_pages = max_catalog_pages
        self._catalog_per_page = catalog_per_page
        self._lock = asyncio.Lock()
        self._last_sent: float | None = None
        # Jeda global (monotonic) setelah 429, atau 503 dengan Retry-After: berlaku untuk
        # SEMUA panggilan di instans ini, bukan hanya panggilan yang menerima respons itu.
        self._blocked_until: float | None = None
        # Jumlah permintaan HTTP yang benar-benar dikirim (untuk AK-06 di sync_worker).
        self.requests_sent = 0
        self._health_cache: tuple[float, SourceHealth] | None = None
        self._client = httpx.AsyncClient(
            base_url=settings.source_base_url,
            headers={"User-Agent": user_agent, "Accept": "application/json"},
            timeout=settings.http_timeout_s,
            follow_redirects=False,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> OpenDataJabarAdapter:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    # ------------------------------------------------------------ HTTP

    async def _throttle(self, path: str) -> None:
        """Tunggu giliran kirim: jarak ≥ delay dan tidak sebelum jeda global berakhir.

        Diperiksa ulang setiap selesai tidur, karena panggilan lain bisa memasang jeda
        global (429) selama kita menunggu. Sisa jeda > MCP_BACKOFF_MAX_S → SourceRateLimited
        tanpa mengirim apa pun.
        """
        async with self._lock:
            while True:
                now = self._monotonic()
                wait = 0.0
                if self._blocked_until is not None and self._blocked_until > now:
                    remaining = self._blocked_until - now
                    if remaining > self._backoff_max_s:
                        raise SourceRateLimited(
                            "portal meminta jeda lebih lama dari MCP_BACKOFF_MAX_S", path=path
                        )
                    wait = remaining
                if self._last_sent is not None:
                    wait = max(wait, self._last_sent + self._delay_s - now)
                if wait <= 0:
                    break
                await self._sleep(wait)
            self._last_sent = self._monotonic()

    def _block_for(self, seconds: float) -> None:
        until = self._monotonic() + seconds
        if self._blocked_until is None or until > self._blocked_until:
            self._blocked_until = until

    def _backoff(self, attempt: int) -> float:
        delay = BACKOFF_BASE_S * 2 ** (attempt - 1) + self._rng() * BACKOFF_BASE_S
        return min(delay, self._backoff_max_s)

    def _retry_after(self, response: httpx.Response) -> float | None:
        raw = response.headers.get("retry-after")
        if raw is None:
            return None
        raw = raw.strip()
        if raw.isdigit():
            return float(raw)
        try:
            when = parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        return max(0.0, (when - self._wall_clock()).total_seconds())

    async def _wait_or_raise(
        self,
        failure: SourceError,
        delay: float,
        attempt: int,
        attempts: int,
        *,
        local_sleep: bool = True,
    ) -> None:
        """Gagal bila percobaan habis atau tunggu terlalu lama; selain itu tunggu.

        `local_sleep=False`: tunggu sudah dipasang sebagai jeda global dan dijalankan
        oleh _throttle pada percobaan berikutnya.
        """
        if attempt >= attempts or delay > self._backoff_max_s:
            raise failure
        if local_sleep:
            await self._sleep(delay)

    def _log(
        self, path: str, status: int | None, attempt: int, started: float, **extra: Any
    ) -> None:
        duration_ms = round((self._monotonic() - started) * 1000, 1)
        log_event(
            LOGGER,
            "portal_request",
            path=path,
            status=status,
            attempt=attempt,
            duration_ms=duration_ms,
            **extra,
        )

    async def _get(
        self, path: str, params: Mapping[str, Any] | None = None, *, max_attempts: int | None = None
    ) -> dict[str, Any]:
        check_endpoint(path)
        attempts = max_attempts or self._max_attempts
        for attempt in range(1, attempts + 1):
            await self._throttle(path)
            started = self._monotonic()
            self.requests_sent += 1
            try:
                response = await self._client.get(path, params=dict(params or {}))
            except httpx.TransportError as exc:
                self._log(path, None, attempt, started, error=type(exc).__name__)
                kind = "timeout" if isinstance(exc, httpx.TimeoutException) else "koneksi gagal"
                failure = SourceUnavailable(f"{kind} pada {path}", path=path, attempts=attempt)
                await self._wait_or_raise(failure, self._backoff(attempt), attempt, attempts)
                continue
            status = response.status_code
            self._log(path, status, attempt, started)
            if status == 200:
                return self._json(response, path)
            if status == 404:
                raise SourceNotFound(f"404 pada {path}", path=path, attempts=attempt)
            if status == 429 or 500 <= status < 600:
                cls = SourceRateLimited if status == 429 else SourceUnavailable
                failure = cls(f"{status} pada {path}", path=path, attempts=attempt)
                retry_after = self._retry_after(response)
                wait = self._backoff(attempt) if retry_after is None else retry_after
                # 429 (selalu) dan 503 dengan Retry-After: portal minta SEMUA klien menunggu.
                is_global = status == 429 or (status == 503 and retry_after is not None)
                if is_global:
                    self._block_for(wait)
                await self._wait_or_raise(
                    failure, wait, attempt, attempts, local_sleep=not is_global
                )
                continue
            raise SourceProtocolError(f"status {status} tak terduga pada {path}", path=path)
        raise AssertionError("tidak terjangkau")  # pragma: no cover

    @staticmethod
    def _json(response: httpx.Response, path: str) -> dict[str, Any]:
        try:
            body = response.json()
        except ValueError as exc:
            raise SourceProtocolError(f"respons {path} bukan JSON", path=path) from exc
        if not isinstance(body, dict):
            raise SourceProtocolError(f"respons {path} bukan objek JSON", path=path)
        return body

    # ------------------------------------------------------------ SourceAdapter

    async def list_datasets(self) -> CatalogSnapshot:
        """Seluruh katalog. Galat di tengah jalan tidak dilempar: snapshot complete=False."""
        records: dict[str, RawDataset] = {}
        duplicates = pages_read = skipped = 0
        skipped_pages: list[int] = []
        total_data: int | None = None
        total_page: int | None = None
        finished = False
        error: str | None = None
        for page in range(1, self._max_catalog_pages + 1):
            try:
                body = await self._get(
                    "/datasets", {"page": page, "per_page": self._catalog_per_page}
                )
                data = _data_list(body, "/datasets")
            except SourceError as exc:
                error = f"{type(exc).__name__}: {exc}"
                break
            pages_read += 1
            pagination = parse_pagination(body.get("pagination"))
            total_data = pagination.total_data if pagination.total_data is not None else total_data
            total_page = pagination.total_page if pagination.total_page is not None else total_page
            for item in data:
                try:
                    dataset = parse_dataset(item)
                except SourceProtocolError:
                    skipped += 1
                    if page not in skipped_pages:
                        skipped_pages.append(page)
                    continue
                if dataset.slug in records:
                    duplicates += 1
                else:
                    records[dataset.slug] = dataset
            if not data:
                break
            more = (
                pagination.has_next
                if pagination.has_next is not None
                else (total_page is None or page < total_page)
            )
            if not more:
                finished = True
                break
        complete = (
            error is None
            and finished
            and total_page is not None
            and pages_read == total_page
            and total_data is not None
            and len(records) == total_data
        )
        # Ringkasan tanpa isi record: angka dan nomor halaman saja.
        log_event(
            LOGGER,
            "catalog_snapshot",
            pages_read=pages_read,
            total_page=total_page,
            total_data=total_data,
            records=len(records),
            duplicates=duplicates,
            skipped=skipped,
            skipped_pages=skipped_pages,
            complete=complete,
            error=error,
        )
        return CatalogSnapshot(
            records=list(records.values()),
            total_data=total_data,
            pages_read=pages_read,
            total_page=total_page,
            complete=complete,
            duplicates=duplicates,
            skipped=skipped,
            error=error,
        )

    async def get_dataset(self, slug: str) -> RawDataset:
        path = build_path("datasets", validate_segment(slug, "slug"))
        body = await self._get(path)
        dataset = parse_dataset(body.get("data"))
        if dataset.slug != slug:
            raise SourceProtocolError(
                f"slug respons berbeda dari permintaan pada {path}", path=path
            )
        return dataset

    def _bigdata_path(self, dataset: RawDataset) -> str:
        if dataset.schema is None or dataset.table is None:
            raise SourceProtocolError(f"dataset {dataset.slug} tanpa schema/table")
        return build_path(
            "bigdata",
            validate_segment(dataset.schema, "schema"),
            validate_segment(dataset.table, "table"),
        )

    async def get_columns(self, dataset: RawDataset) -> ColumnProbe:
        """Murah: satu baris saja (per_page=1); nama kolom dan metadata_filter tetap lengkap."""
        body = await self._get(self._bigdata_path(dataset), {"page": 1, "per_page": 1})
        return parse_columns(body)

    async def query_rows(
        self,
        dataset: RawDataset,
        where: Mapping[str, Sequence[Any]] | None = None,
        sort: str | None = None,
        page: int = 1,
        per_page: int = 100,
    ) -> RowPage:
        params = build_query_params(where, sort, page, per_page)
        body = await self._get(self._bigdata_path(dataset), params)
        return parse_row_page(body)

    async def probe(self, dataset: RawDataset, per_page: int = 100) -> tuple[ColumnProbe, RowPage]:
        """KK-O: satu GET halaman 1 untuk kamus (nama kolom, metadata_filter, sampel baris)."""
        params = build_query_params(None, None, 1, per_page)
        body = await self._get(self._bigdata_path(dataset), params)
        return parse_columns(body), parse_row_page(body)

    async def health(self) -> SourceHealth:
        """Satu GET murah tanpa retry; hasil (sehat atau tidak) di-cache health_ttl_s detik."""
        now = self._monotonic()
        if self._health_cache is not None and now - self._health_cache[0] < self._health_ttl_s:
            return self._health_cache[1]
        started = self._monotonic()
        try:
            await self._get("/datasets", {"page": 1, "per_page": 1}, max_attempts=1)
            result = SourceHealth(ok=True, status=200, latency_s=self._monotonic() - started)
        except SourceError as exc:
            result = SourceHealth(
                ok=False,
                detail=f"{type(exc).__name__}: {exc}",
                latency_s=self._monotonic() - started,
            )
        self._health_cache = (self._monotonic(), result)
        return result

    def last_health(self) -> tuple[SourceHealth, float] | None:
        """Baca-saja: hasil health() terakhir + umurnya (detik); tidak pernah mengirim GET."""
        if self._health_cache is None:
            return None
        checked_at, result = self._health_cache
        return result, max(0.0, self._monotonic() - checked_at)

    async def list_organisasi(self) -> list[dict[str, Any]]:
        return parse_reference(await self._get("/organisasi"), "/organisasi")

    async def list_topik(self) -> list[dict[str, Any]]:
        return parse_reference(await self._get("/topik"), "/topik")


def total_pages(count: int, per_page: int) -> int:
    """Jumlah halaman untuk `count` baris (minimal 1); dipakai fake dan simulator tes."""
    return max(1, math.ceil(count / per_page))
