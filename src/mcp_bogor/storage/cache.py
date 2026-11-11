"""Cache berkas per kueri + meta, TTL, stale, dan kamus kolom (FR-05, KS-05, KK-08). Langkah 7.

Tata letak di MCP_CACHE_DIR:
    data/{cache_id}.json    isi berkas data_ref (Dok 4 bagian 6), disajikan file server apa adanya
    meta/{cache_id}.json    slug, parameter, retrieved_at, data_updated_at, row_count, ukuran,
                            superseded_at, ref_expires_at (tidak pernah disajikan)
    index/{sha256}.json     kunci kueri → cache_id terbaru
    dictionary/{slug}.json  hasil services/dictionary + data_updated_at sumbernya (KK-08)
    tmp/                    berkas sementara sebelum os.replace

Aturan (KB-32):
- Berkas data TIDAK PERNAH ditimpa: versi baru = cache_id baru = berkas baru. Semua tulisan
  lewat tmp + os.replace (atomik di volume yang sama). Di Windows, os.replace ke berkas yang
  sedang dibuka proses lain gagal dengan PermissionError; tulisan indeks/meta/kamus diulang
  beberapa kali dengan jeda pendek.
- cache_id = HMAC-SHA256 (kunci turunan MCP_DATAREF_SECRET) atas kunci kueri + retrieved_at
  + nonce acak, dipotong 128 bit, base32 huruf kecil tanpa padding. Tidak memuat slug.
- Server tidak menulis ke SQLite (ADR-010); cache memakai berkas saja.
- Tidak ada isi baris di log.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from mcp_bogor.config import WIB, Settings
from mcp_bogor.logging import get_logger, log_event

LOGGER = get_logger(__name__)

CACHE_FORMAT = 1
DICTIONARY_FORMAT = 1
CACHE_ID_RE = re.compile(r"^[a-z2-7]{26}$")
_SLUG_RE = re.compile(r"^[a-z0-9_-]+$")
_CACHE_ID_LABEL = b"mcp-bogor:cache-id:v1"

# Kunci akun portal yang tidak boleh tersimpan (Dok 1 privasi; sama dengan adapters.sanitize).
ACCOUNT_KEYS = frozenset({"username", "user_name", "cuid", "muid"})

# KB-32: berkas tmp yatim dan jeda GC oportunistis.
TMP_MAX_AGE = timedelta(hours=1)
GC_INTERVAL = timedelta(minutes=10)
# Versi yang dikembalikan get_or_fetch dilindungi selama MCP_DATAREF_TTL_SECONDS + margin ini,
# karena tool akan menandatangani data_ref untuknya sesaat kemudian.
REF_MARGIN = timedelta(seconds=60)
# Windows: os.replace/unlink ke berkas yang sedang dibuka proses lain → PermissionError.
REPLACE_ATTEMPTS = 5
REPLACE_RETRY_DELAY_S = 0.05

ServedFrom = Literal["live", "cache", "stale_cache"]


def _now() -> datetime:
    return datetime.now(WIB)


# ---------------------------------------------------------------- id dan path


def derive_cache_id_key(secret: bytes) -> bytes:
    """Kunci HMAC cache_id, diturunkan dari MCP_DATAREF_SECRET (tidak sama dengan kunci sig)."""
    return hmac.new(secret, _CACHE_ID_LABEL, hashlib.sha256).digest()


def is_valid_cache_id(value: str) -> bool:
    return CACHE_ID_RE.match(value) is not None


def data_path(cache_dir: Path, cache_id: str) -> Path:
    if not is_valid_cache_id(cache_id):
        raise ValueError("cache_id tidak sah")
    return cache_dir / "data" / f"{cache_id}.json"


def read_data_file(cache_dir: Path, cache_id: str) -> bytes:
    """Baca berkas data untuk file server. FileNotFoundError bila sudah di-GC."""
    return data_path(cache_dir, cache_id).read_bytes()


# ---------------------------------------------------------------- kunci kueri

FilterValue = str | int


def _filter_sort_key(value: FilterValue) -> tuple[int, str]:
    return (1, value) if isinstance(value, str) else (0, f"{value:020d}")


@dataclass(frozen=True)
class QueryKey:
    """Kunci kueri yang dinormalkan: slug + periods terurut + filters terurut + sort."""

    slug: str
    periods: tuple[int, ...] = ()
    filters: tuple[tuple[str, tuple[FilterValue, ...]], ...] = ()
    sort: str | None = None

    @classmethod
    def build(
        cls,
        slug: str,
        periods: Iterable[int] | None = None,
        filters: Mapping[str, Iterable[FilterValue]] | None = None,
        sort: str | None = None,
    ) -> QueryKey:
        if not _SLUG_RE.match(slug):
            raise ValueError("slug tidak sah untuk kunci cache")
        clean_periods: set[int] = set()
        for period in periods or ():
            if isinstance(period, bool) or not isinstance(period, int):
                raise ValueError("periods harus int")
            clean_periods.add(period)
        clean_filters: dict[str, tuple[FilterValue, ...]] = {}
        for name, values in (filters or {}).items():
            column = name.strip()
            seen: set[FilterValue] = set()
            for value in values:
                if isinstance(value, bool) or not isinstance(value, str | int):
                    raise ValueError("nilai filter harus str atau int")
                seen.add(value.strip() if isinstance(value, str) else value)
            if column and seen:
                clean_filters[column] = tuple(sorted(seen, key=_filter_sort_key))
        sort_clean = sort.strip() if sort and sort.strip() else None
        return cls(
            slug=slug,
            periods=tuple(sorted(clean_periods)),
            filters=tuple(sorted(clean_filters.items())),
            sort=sort_clean,
        )

    def params(self) -> dict[str, Any]:
        return {
            "periods": list(self.periods),
            "filters": {name: list(values) for name, values in self.filters},
            "sort": self.sort,
        }

    def canonical(self) -> str:
        return json.dumps(
            {"slug": self.slug, **self.params()},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    def digest(self) -> str:
        """Nama berkas indeks (tidak pernah disajikan)."""
        return hashlib.sha256(self.canonical().encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- model hasil


@dataclass(frozen=True)
class CacheEntry:
    cache_id: str
    slug: str
    params: dict[str, Any]
    retrieved_at: datetime
    data_updated_at: datetime | None
    row_count: int
    size_bytes: int
    superseded_at: datetime | None = None
    ref_expires_at: datetime | None = None

    def to_meta(self) -> dict[str, Any]:
        return {
            "format": CACHE_FORMAT,
            "cache_id": self.cache_id,
            "slug": self.slug,
            "params": self.params,
            "retrieved_at": self.retrieved_at.isoformat(),
            "data_updated_at": _iso(self.data_updated_at),
            "row_count": self.row_count,
            "size_bytes": self.size_bytes,
            "superseded_at": _iso(self.superseded_at),
            "ref_expires_at": _iso(self.ref_expires_at),
        }

    @classmethod
    def from_meta(cls, raw: Mapping[str, Any]) -> CacheEntry:
        return cls(
            cache_id=str(raw["cache_id"]),
            slug=str(raw["slug"]),
            params=dict(raw.get("params") or {}),
            retrieved_at=datetime.fromisoformat(raw["retrieved_at"]),
            data_updated_at=_parse_iso(raw.get("data_updated_at")),
            row_count=int(raw["row_count"]),
            size_bytes=int(raw["size_bytes"]),
            superseded_at=_parse_iso(raw.get("superseded_at")),
            ref_expires_at=_parse_iso(raw.get("ref_expires_at")),
        )


@dataclass(frozen=True)
class FetchedData:
    """Hasil pengambilan dari sumber: isi berkas data_ref + waktu pembaruan data sumber."""

    # Minimal columns, rows, provenance (Dok 4 bagian 6). provenance tanpa served_from;
    # provenance.retrieved_at diisi cache saat menulis (KB-34).
    payload: Mapping[str, Any]
    data_updated_at: datetime | None = None


@dataclass(frozen=True)
class CacheResult:
    entry: CacheEntry
    served_from: ServedFrom
    refresh_throttled: bool = False  # force_refresh diabaikan karena KB-03
    source_error: BaseException | None = None  # terisi bila served_from=stale_cache


@dataclass(frozen=True)
class GcReport:
    deleted: int
    freed_bytes: int
    skipped_locked: int
    total_bytes: int


class CacheError(Exception):
    """Galat cache yang dipetakan tool ke kode galat kontrak."""


class CacheTooLarge(CacheError):
    """Baris melebihi MCP_CACHE_MAX_ROWS → RESPONSE_TOO_LARGE (KB-32)."""

    def __init__(self, row_count: int, limit: int) -> None:
        super().__init__(f"{row_count} baris melebihi batas cache {limit}")
        self.row_count = row_count
        self.limit = limit


class InvalidPayload(CacheError):
    """Isi yang akan di-cache tidak berbentuk {columns, rows, provenance}."""


# ---------------------------------------------------------------- bantuan berkas


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _parse_iso(value: Any) -> datetime | None:
    return datetime.fromisoformat(value) if isinstance(value, str) and value else None


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime | date):
        return value.isoformat()
    raise TypeError(f"tipe {type(value).__name__} tidak bisa ditulis ke JSON")


def _dumps(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        default=_json_default,
    ).encode("utf-8")


def _drop_account_keys(value: Any) -> tuple[Any, int]:
    """Buang kunci akun di mana pun (lapis kedua; adaptor sudah membuangnya)."""
    if isinstance(value, Mapping):
        dropped = 0
        out: dict[str, Any] = {}
        for key, item in value.items():
            if key in ACCOUNT_KEYS:
                dropped += 1
                continue
            out[key], count = _drop_account_keys(item)
            dropped += count
        return out, dropped
    if isinstance(value, list | tuple):
        dropped = 0
        items = []
        for item in value:
            clean, count = _drop_account_keys(item)
            items.append(clean)
            dropped += count
        return items, dropped
    return value, 0


class _Files:
    """Tulis atomik (tmp + os.replace) dan baca JSON, dengan ulang singkat untuk Windows."""

    def __init__(self, root: Path, sleep: Callable[[float], None]) -> None:
        self.root = root
        self.tmp = root / "tmp"
        self._sleep = sleep

    def ensure(self, *names: str) -> None:
        for name in ("tmp", *names):
            (self.root / name).mkdir(parents=True, exist_ok=True)

    def write(self, target: Path, content: bytes) -> None:
        tmp = self.tmp / f"{target.name}.{uuid.uuid4().hex}.tmp"
        with open(tmp, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        for attempt in range(1, REPLACE_ATTEMPTS + 1):
            try:
                os.replace(tmp, target)
                return
            except PermissionError:
                if attempt == REPLACE_ATTEMPTS:
                    tmp.unlink(missing_ok=True)
                    raise
                self._sleep(REPLACE_RETRY_DELAY_S * attempt)

    def read_json(self, path: Path) -> Any | None:
        """JSON berkas, atau None bila tidak ada/rusak (rusak dicatat, dianggap miss)."""
        for attempt in range(1, REPLACE_ATTEMPTS + 1):
            try:
                return json.loads(path.read_bytes())
            except FileNotFoundError:
                return None
            except PermissionError:
                if attempt == REPLACE_ATTEMPTS:
                    raise
                self._sleep(REPLACE_RETRY_DELAY_S * attempt)
            except (ValueError, UnicodeDecodeError):
                log_event(LOGGER, "cache_corrupt_file", file=path.name)
                return None
        return None  # pragma: no cover

    def unlink(self, path: Path) -> bool:
        """True bila terhapus atau sudah tidak ada; False bila terkunci (Windows)."""
        try:
            path.unlink(missing_ok=True)
            return True
        except PermissionError:
            return False


# ---------------------------------------------------------------- cache data


class DataCache:
    """Cache hasil kueri /bigdata yang disajikan lewat data_ref."""

    def __init__(
        self,
        cache_dir: Path,
        *,
        id_key: bytes,
        ttl: timedelta,
        stale_max: timedelta,
        dataref_ttl: timedelta,
        force_refresh_min_interval: timedelta,
        max_rows: int,
        max_bytes: int,
        now: Callable[[], datetime] = _now,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if len(id_key) < 32:
            raise ValueError("id_key cache minimal 32 byte")
        self.root = cache_dir
        self._files = _Files(cache_dir, sleep)
        self._files.ensure("data", "meta", "index")
        self._id_key = id_key
        self.ttl = ttl
        self.stale_max = stale_max
        self.dataref_ttl = dataref_ttl
        self.force_refresh_min_interval = force_refresh_min_interval
        self.max_rows = max_rows
        self.max_bytes = max_bytes
        self._now = now
        self._locks: dict[str, tuple[asyncio.Lock, int]] = {}
        self._last_gc: datetime | None = None

    @classmethod
    def from_settings(cls, settings: Settings, *, now: Callable[[], datetime] = _now) -> DataCache:
        return cls(
            settings.cache_dir,
            id_key=derive_cache_id_key(settings.require_dataref_secret()),
            ttl=timedelta(hours=settings.cache_ttl_h),
            stale_max=timedelta(hours=settings.cache_stale_max_h),
            dataref_ttl=timedelta(seconds=settings.dataref_ttl_seconds),
            force_refresh_min_interval=timedelta(seconds=settings.force_refresh_min_interval_s),
            max_rows=settings.cache_max_rows,
            max_bytes=int(settings.cache_max_mb * 1024 * 1024),
            now=now,
        )

    # ------------------------------------------------ path

    def _meta_path(self, cache_id: str) -> Path:
        return self.root / "meta" / f"{cache_id}.json"

    def _data_path(self, cache_id: str) -> Path:
        return data_path(self.root, cache_id)

    def _index_path(self, key: QueryKey) -> Path:
        return self.root / "index" / f"{key.digest()}.json"

    def _new_cache_id(self, key: QueryKey, retrieved_at: datetime) -> str:
        message = f"{key.canonical()}\n{retrieved_at.isoformat()}\n".encode()
        mac = hmac.new(self._id_key, message + secrets.token_bytes(16), hashlib.sha256)
        return base64.b32encode(mac.digest()[:16]).decode("ascii").rstrip("=").lower()

    # ------------------------------------------------ baca

    def entry(self, cache_id: str) -> CacheEntry | None:
        raw = self._files.read_json(self._meta_path(cache_id))
        if not isinstance(raw, Mapping):
            return None
        try:
            return CacheEntry.from_meta(raw)
        except (KeyError, TypeError, ValueError):
            log_event(LOGGER, "cache_corrupt_file", file=f"{cache_id}.json")
            return None

    def latest(self, key: QueryKey) -> CacheEntry | None:
        """Versi terbaru untuk kunci kueri, atau None bila tidak ada/berkas datanya hilang."""
        index = self._files.read_json(self._index_path(key))
        if not isinstance(index, Mapping) or not isinstance(index.get("cache_id"), str):
            return None
        cache_id = index["cache_id"]
        if not is_valid_cache_id(cache_id) or not self._data_path(cache_id).is_file():
            return None
        return self.entry(cache_id)

    def read_payload(self, cache_id: str) -> Any:
        return json.loads(self._data_path(cache_id).read_bytes())

    def is_fresh(self, entry: CacheEntry, now: datetime | None = None) -> bool:
        return (now or self._now()) - entry.retrieved_at < self.ttl

    # ------------------------------------------------ tulis

    def put(self, key: QueryKey, fetched: FetchedData) -> CacheEntry:
        """Tulis versi baru (berkas baru); versi lama ditandai superseded, tidak ditimpa."""
        payload = fetched.payload
        if not isinstance(payload, Mapping):
            raise InvalidPayload("isi cache harus objek")
        rows = payload.get("rows")
        if not isinstance(rows, list) or not isinstance(payload.get("columns"), list):
            raise InvalidPayload("isi cache wajib punya columns dan rows berupa list")
        provenance = payload.get("provenance")
        if not isinstance(provenance, Mapping):
            raise InvalidPayload("isi cache wajib punya provenance")
        if "served_from" in provenance:
            # KB-34: served_from sifat respons tool (amplop), bukan sifat data.
            raise InvalidPayload("provenance.served_from tidak boleh ada di berkas data_ref")
        if len(rows) > self.max_rows:
            raise CacheTooLarge(len(rows), self.max_rows)

        retrieved_at = self._now()
        clean, dropped = _drop_account_keys(payload)
        if dropped:
            log_event(LOGGER, "cache_account_keys_dropped", slug=key.slug, count=dropped)
        # KB-34: retrieved_at di berkas = retrieved_at versi cache (satu sumber kebenaran).
        clean["provenance"] = {**clean["provenance"], "retrieved_at": retrieved_at.isoformat()}
        content = _dumps(clean)

        previous = self.latest(key)
        cache_id = self._new_cache_id(key, retrieved_at)
        entry = CacheEntry(
            cache_id=cache_id,
            slug=key.slug,
            params=key.params(),
            retrieved_at=retrieved_at,
            data_updated_at=fetched.data_updated_at,
            row_count=len(rows),
            size_bytes=len(content),
        )
        # Urutan: data → meta → indeks. Crash di tengah meninggalkan berkas yatim (di-GC),
        # bukan indeks yang menunjuk ke berkas yang belum ada.
        self._files.write(self._data_path(cache_id), content)
        self._files.write(self._meta_path(cache_id), _dumps(entry.to_meta()))
        self._files.write(
            self._index_path(key),
            _dumps({"format": CACHE_FORMAT, "cache_id": cache_id, "query": key.canonical()}),
        )
        if previous is not None:
            self._write_meta(replace(previous, superseded_at=retrieved_at))
        log_event(
            LOGGER,
            "cache_put",
            slug=key.slug,
            cache_id=cache_id,
            row_count=entry.row_count,
            size_bytes=entry.size_bytes,
        )
        return entry

    def _write_meta(self, entry: CacheEntry) -> None:
        self._files.write(self._meta_path(entry.cache_id), _dumps(entry.to_meta()))

    def mark_referenced(self, cache_id: str, until: datetime) -> None:
        """Catat bahwa data_ref aktif merujuk versi ini sampai `until` (dilindungi dari GC)."""
        entry = self.entry(cache_id)
        if entry is None:
            raise FileNotFoundError("cache_id tidak dikenal")
        if entry.ref_expires_at is None or until > entry.ref_expires_at:
            self._write_meta(replace(entry, ref_expires_at=until))

    # ------------------------------------------------ ambil dengan cache

    @asynccontextmanager
    async def _key_lock(self, digest: str) -> AsyncIterator[None]:
        """Single-flight per kunci kueri di dalam proses."""
        lock, users = self._locks.get(digest, (asyncio.Lock(), 0))
        self._locks[digest] = (lock, users + 1)
        try:
            async with lock:
                yield
        finally:
            lock, users = self._locks[digest]
            if users <= 1:
                del self._locks[digest]
            else:
                self._locks[digest] = (lock, users - 1)

    async def get_or_fetch(
        self,
        key: QueryKey,
        fetch: Callable[[], Awaitable[FetchedData]],
        *,
        force_refresh: bool = False,
        fallback_errors: tuple[type[BaseException], ...] = (),
    ) -> CacheResult:
        """Cache segar → `cache`; selain itu ambil ke sumber → `live`.

        - force_refresh dalam MCP_FORCE_REFRESH_MIN_INTERVAL_S sejak pengambilan terakhir
          diabaikan (refresh_throttled=True) selama cache masih segar (KB-03).
        - Galat bertipe `fallback_errors` saat mengambil: sajikan versi terbaru sebagai
          `stale_cache` bila umurnya ≤ MCP_CACHE_STALE_MAX_H (KS-05 → STALE_DATA); bila tidak
          ada cache atau lebih tua, galat asli dilempar ulang (tool memetakannya,
          mis. SOURCE_UNAVAILABLE).
        - Dua panggilan bersamaan untuk kunci yang sama hanya memicu satu `fetch`.
        """
        async with self._key_lock(key.digest()):
            now = self._now()
            current = self.latest(key)
            if current is not None and self.is_fresh(current, now):
                if not force_refresh:
                    return self._served(CacheResult(current, "cache"), now)
                if now - current.retrieved_at < self.force_refresh_min_interval:
                    return self._served(CacheResult(current, "cache", refresh_throttled=True), now)
            try:
                fetched = await fetch()
            except fallback_errors as exc:
                if current is not None and now - current.retrieved_at <= self.stale_max:
                    log_event(
                        LOGGER,
                        "cache_stale_served",
                        slug=key.slug,
                        cache_id=current.cache_id,
                        error=type(exc).__name__,
                    )
                    return self._served(CacheResult(current, "stale_cache", source_error=exc), now)
                raise
            entry = self.put(key, fetched)
            result = self._served(CacheResult(entry, "live"), now)
        self.maybe_gc()
        return result

    def _served(self, result: CacheResult, now: datetime) -> CacheResult:
        until = now + self.dataref_ttl + REF_MARGIN
        self.mark_referenced(result.entry.cache_id, until)
        return CacheResult(
            replace(result.entry, ref_expires_at=until),
            result.served_from,
            result.refresh_throttled,
            result.source_error,
        )

    # ------------------------------------------------ GC

    def maybe_gc(self) -> GcReport | None:
        now = self._now()
        if self._last_gc is not None and now - self._last_gc < GC_INTERVAL:
            return None
        try:
            return self.gc()
        except OSError as exc:
            log_event(LOGGER, "cache_gc_failed", error=type(exc).__name__)
            return None

    def _protected(self, entry: CacheEntry, latest: bool, now: datetime) -> bool:
        """Mungkin masih dirujuk data_ref aktif → jangan dihapus."""
        if entry.ref_expires_at is not None and now < entry.ref_expires_at:
            return True
        if not latest and entry.superseded_at is not None:
            # data_ref terakhir ke versi ini paling lambat dibuat saat versi baru muncul.
            return now < entry.superseded_at + self.dataref_ttl
        return False

    def gc(self) -> GcReport:
        """Hapus versi lama dan berkas yatim; lalu tegakkan MCP_CACHE_MAX_MB (KB-32).

        Versi dihapus bila bukan terbaru, umurnya > TTL + MCP_DATAREF_TTL_SECONDS, dan tidak
        dilindungi data_ref aktif. Bila total masih > batas, versi tak terlindungi dihapus
        dari yang tertua (versi lama dulu, lalu versi terbaru). Berkas yang terkunci
        (Windows: sedang dibaca) dilewati dan dicoba lagi di GC berikutnya.
        """
        now = self._now()
        self._last_gc = now
        index_files = {
            path: self._files.read_json(path) for path in (self.root / "index").glob("*.json")
        }
        latest_ids = {
            raw["cache_id"]: path
            for path, raw in index_files.items()
            if isinstance(raw, Mapping) and isinstance(raw.get("cache_id"), str)
        }
        entries: dict[str, CacheEntry] = {}
        for path in (self.root / "meta").glob("*.json"):
            entry = self.entry(path.stem)
            if entry is not None:
                entries[entry.cache_id] = entry

        deleted = freed = locked = 0

        def remove(cache_id: str) -> bool:
            nonlocal deleted, freed, locked
            data = self._data_path(cache_id)
            size = data.stat().st_size if data.is_file() else 0
            if not self._files.unlink(data):
                locked += 1
                return False
            self._files.unlink(self._meta_path(cache_id))
            if cache_id in latest_ids:
                self._files.unlink(latest_ids.pop(cache_id))
            deleted += 1
            freed += size
            entries.pop(cache_id, None)
            return True

        horizon = self.ttl + self.dataref_ttl
        for cache_id, entry in list(entries.items()):
            latest = cache_id in latest_ids
            if latest or self._protected(entry, latest, now):
                continue
            if now - entry.retrieved_at > horizon:
                remove(cache_id)

        # Berkas yatim: data tanpa meta, meta tanpa data, tmp lama.
        stamp = now.timestamp()
        for path in (self.root / "data").glob("*.json"):
            if path.stem not in entries and stamp - path.stat().st_mtime > horizon.total_seconds():
                size = path.stat().st_size
                if self._files.unlink(path):
                    deleted += 1
                    freed += size
                else:
                    locked += 1
        for cache_id in list(entries):
            if not self._data_path(cache_id).is_file() and cache_id not in latest_ids:
                self._files.unlink(self._meta_path(cache_id))
                entries.pop(cache_id)
        for path in self._files.tmp.glob("*.tmp"):
            if stamp - path.stat().st_mtime > TMP_MAX_AGE.total_seconds():
                self._files.unlink(path)

        total = sum(e.size_bytes for e in entries.values())
        if total > self.max_bytes:
            candidates = sorted(
                (
                    e
                    for e in entries.values()
                    if not self._protected(e, e.cache_id in latest_ids, now)
                ),
                key=lambda e: (e.cache_id in latest_ids, e.retrieved_at),
            )
            for entry in candidates:
                if total <= self.max_bytes:
                    break
                if remove(entry.cache_id):
                    total -= entry.size_bytes

        report = GcReport(
            deleted=deleted, freed_bytes=freed, skipped_locked=locked, total_bytes=total
        )
        log_event(LOGGER, "cache_gc", **report.__dict__)
        return report


# ---------------------------------------------------------------- kamus kolom (KK-08)


class DictionaryCache:
    """Hasil services/dictionary per slug. Dibuat ulang bila data_updated_at sumber berubah.

    `payload` dibentuk dan dibaca oleh services/dictionary (to_dict/from_dict); modul ini
    tidak mengimpor services. `override_digest` membuat kamus dibuat ulang saat berkas
    override berubah.
    """

    def __init__(self, cache_dir: Path, *, sleep: Callable[[float], None] = time.sleep) -> None:
        self.root = cache_dir
        self._files = _Files(cache_dir, sleep)
        self._files.ensure("dictionary")

    def _path(self, slug: str) -> Path:
        if not _SLUG_RE.match(slug):
            raise ValueError("slug tidak sah untuk cache kamus")
        return self.root / "dictionary" / f"{slug}.json"

    def get(
        self,
        slug: str,
        *,
        data_updated_at: datetime | None,
        override_digest: str | None = None,
    ) -> Mapping[str, Any] | None:
        raw = self._files.read_json(self._path(slug))
        if not isinstance(raw, Mapping) or raw.get("format") != DICTIONARY_FORMAT:
            return None
        if raw.get("data_updated_at") != _iso(data_updated_at):
            return None
        if raw.get("override_digest") != override_digest:
            return None
        payload = raw.get("payload")
        return payload if isinstance(payload, Mapping) else None

    def put(
        self,
        slug: str,
        payload: Mapping[str, Any],
        *,
        data_updated_at: datetime | None,
        override_digest: str | None = None,
        built_at: datetime | None = None,
    ) -> None:
        record = {
            "format": DICTIONARY_FORMAT,
            "slug": slug,
            "data_updated_at": _iso(data_updated_at),
            "override_digest": override_digest,
            "built_at": _iso(built_at),
            "payload": payload,
        }
        self._files.write(self._path(slug), _dumps(record))
