"""storage/cache Langkah 7: kunci kueri, cache_id, TTL, stale, force_refresh, single-flight,
GC, tulis atomik (Windows), privasi, dan cache kamus (KK-08). Tanpa jaringan."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from mcp_bogor.adapters.base import SourceNotFound, SourceUnavailable
from mcp_bogor.config import WIB, ConfigError
from mcp_bogor.storage import cache as cache_mod
from mcp_bogor.storage.cache import (
    CACHE_ID_RE,
    CacheTooLarge,
    DataCache,
    DictionaryCache,
    FetchedData,
    InvalidPayload,
    QueryKey,
    data_path,
    derive_cache_id_key,
    read_data_file,
)
from tests.fakes import make_settings

SECRET = "rahasia-uji-langkah-7-" + "x" * 32
SLUG = "tingkat-kemiskinan-di-kota-bogor-2"
T0 = datetime(2026, 11, 11, 9, 0, tzinfo=WIB)
KEY = QueryKey.build(SLUG, periods=[2025, 2024], filters={"satuan": ["PERSEN"]})


class Clock:
    def __init__(self, start: datetime = T0) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)


def make_cache(tmp_path: Path, clock: Clock, **overrides: Any) -> DataCache:
    values: dict[str, Any] = {
        "id_key": derive_cache_id_key(SECRET.encode()),
        "ttl": timedelta(hours=24),
        "stale_max": timedelta(hours=168),
        "dataref_ttl": timedelta(seconds=900),
        "force_refresh_min_interval": timedelta(seconds=300),
        "max_rows": 50,
        "max_bytes": 10_000_000,
        "now": clock,
        "sleep": lambda _s: None,
    }
    values.update(overrides)
    return DataCache(tmp_path / "cache", **values)


def payload(rows: int = 3, slug: str = SLUG, **extra: Any) -> dict[str, Any]:
    return {
        "contract_version": "0.1",
        "slug": slug,
        "columns": [
            {"name": "tahun", "role": "dimension"},
            {"name": "nilai", "role": "measure"},
        ],
        "rows": [{"tahun": 2020 + i, "nilai": 1.5 * i} for i in range(rows)],
        "provenance": {"source_url": "https://contoh/x", "opd": "Contoh"},
        **extra,
    }


class Source:
    """Fetch palsu yang menghitung panggilan; bisa diatur gagal."""

    def __init__(self, rows: int = 3) -> None:
        self.calls = 0
        self.rows = rows
        self.error: BaseException | None = None

    async def __call__(self) -> FetchedData:
        self.calls += 1
        for _ in range(3):  # beri giliran ke task lain (uji single-flight)
            await asyncio.sleep(0)
        if self.error is not None:
            raise self.error
        return FetchedData(payload(self.rows), data_updated_at=T0 - timedelta(days=3))


def fetch(cache: DataCache, source: Source, key: QueryKey = KEY, **kw: Any) -> Any:
    return asyncio.run(cache.get_or_fetch(key, source, **kw))


# ---------------------------------------------------------------- kunci kueri dan cache_id


def test_query_key_is_normalized() -> None:
    a = QueryKey.build(SLUG, periods=[2025, 2024, 2025], filters={"b": ["y", " x"], "a": [2, 1]})
    b = QueryKey.build(SLUG, periods=(2024, 2025), filters={"a": [1, 2], "b": ["x", "y"]})
    assert a == b and a.canonical() == b.canonical() and a.digest() == b.digest()
    assert a.params() == {
        "periods": [2024, 2025],
        "filters": {"a": [1, 2], "b": ["x", "y"]},
        "sort": None,
    }
    mixed = QueryKey.build(SLUG, filters={"k": ["b", 10, 2], "kosong": []}, sort=" tahun:desc ")
    assert mixed.filters == (("k", (2, 10, "b")),) and mixed.sort == "tahun:desc"
    assert QueryKey.build(SLUG, sort="  ").sort is None
    assert QueryKey.build(SLUG).digest() != QueryKey.build(SLUG, sort="tahun:asc").digest()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"slug": "Slug Besar"},
        {"slug": SLUG, "periods": [True]},
        {"slug": SLUG, "periods": ["2024"]},
        {"slug": SLUG, "filters": {"a": [1.5]}},
        {"slug": SLUG, "filters": {"a": [False]}},
    ],
)
def test_query_key_rejects_bad_input(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        QueryKey.build(**kwargs)


def test_cache_id_is_keyed_opaque_and_unique(tmp_path: Path) -> None:
    clock = Clock()
    cache = make_cache(tmp_path, clock)
    first = cache.put(KEY, FetchedData(payload()))
    second = cache.put(KEY, FetchedData(payload()))  # retrieved_at sama, nonce berbeda
    for entry in (first, second):
        assert CACHE_ID_RE.match(entry.cache_id)
        assert "kemiskinan" not in entry.cache_id and SLUG not in entry.cache_id
    assert first.cache_id != second.cache_id
    with pytest.raises(ValueError):
        make_cache(tmp_path, clock, id_key=b"pendek")
    assert derive_cache_id_key(b"a" * 32) != derive_cache_id_key(b"b" * 32)


def test_from_settings_requires_secret(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        DataCache.from_settings(make_settings(tmp_path))
    settings = make_settings(tmp_path, dataref_secret=SECRET, cache_max_mb=1, cache_max_rows=7)
    cache = DataCache.from_settings(settings)
    assert (cache.max_rows, cache.max_bytes, cache.ttl) == (7, 1024 * 1024, timedelta(hours=24))
    assert cache.stale_max == timedelta(hours=168) and cache.root == settings.cache_dir


def test_settings_reject_stale_shorter_than_ttl(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        make_settings(tmp_path, cache_ttl_h=24, cache_stale_max_h=12)


def test_data_path_validates_cache_id(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        data_path(tmp_path, "../meta/x")
    with pytest.raises(FileNotFoundError):
        read_data_file(tmp_path, "a" * 26)


# ---------------------------------------------------------------- isi berkas dan meta


def test_files_and_meta_contents(tmp_path: Path) -> None:
    clock = Clock()
    cache = make_cache(tmp_path, clock)
    entry = cache.put(KEY, FetchedData(payload(4), data_updated_at=T0 - timedelta(days=1)))
    body = read_data_file(cache.root, entry.cache_id)
    stored = json.loads(body)
    assert stored["provenance"] == {**payload(4)["provenance"], "retrieved_at": T0.isoformat()}
    assert {**stored, "provenance": payload(4)["provenance"]} == payload(4)
    meta = json.loads((cache.root / "meta" / f"{entry.cache_id}.json").read_text("utf-8"))
    assert meta["slug"] == SLUG and meta["params"] == KEY.params()
    assert meta["retrieved_at"] == T0.isoformat()
    assert meta["data_updated_at"] == (T0 - timedelta(days=1)).isoformat()
    assert (meta["row_count"], meta["size_bytes"]) == (4, len(body))
    assert cache.latest(KEY) == entry
    assert cache.read_payload(entry.cache_id)["rows"][0] == {"tahun": 2020, "nilai": 0.0}


def test_account_keys_never_written(tmp_path: Path) -> None:
    data = payload(2)
    data["rows"][0]["username"] = "petugas"
    data["provenance"]["history"] = [{"cuid": 7, "muid": 8, "user_name": "x", "type": "approve"}]
    entry = make_cache(tmp_path, Clock()).put(KEY, FetchedData(data))
    text = read_data_file(tmp_path / "cache", entry.cache_id).decode("utf-8")
    for key in ("username", "user_name", "cuid", "muid", "petugas"):
        assert key not in text
    assert '"type":"approve"' in text


@pytest.mark.parametrize(
    "bad",
    [
        [],
        {"columns": [], "provenance": {}},
        {"rows": [], "columns": {}, "provenance": {}},
        {"rows": [], "columns": []},
        {"rows": [{"x": float("nan")}], "columns": [], "provenance": {}},
        {"rows": [], "columns": [], "provenance": {"served_from": "live"}},  # KB-34
    ],
)
def test_invalid_payload_rejected(tmp_path: Path, bad: Any) -> None:
    cache = make_cache(tmp_path, Clock())
    with pytest.raises((InvalidPayload, ValueError)):
        cache.put(KEY, FetchedData(bad))
    assert cache.latest(KEY) is None


def test_too_many_rows(tmp_path: Path) -> None:
    cache = make_cache(tmp_path, Clock(), max_rows=5)
    with pytest.raises(CacheTooLarge) as info:
        fetch(cache, Source(rows=6))
    assert (info.value.row_count, info.value.limit) == (6, 5)
    assert list((cache.root / "data").iterdir()) == [] and cache.latest(KEY) is None


# ---------------------------------------------------------------- hit / miss / TTL / stale


def test_hit_miss_and_ttl(tmp_path: Path) -> None:
    clock = Clock()
    cache = make_cache(tmp_path, clock)
    source = Source()
    first = fetch(cache, source)
    assert (first.served_from, source.calls) == ("live", 1)
    clock.advance(hours=23, minutes=59)
    second = fetch(cache, source)
    assert (second.served_from, second.entry.cache_id, source.calls) == (
        "cache",
        first.entry.cache_id,
        1,
    )
    other = fetch(cache, source, QueryKey.build(SLUG))  # kunci lain = miss
    assert other.served_from == "live" and source.calls == 2
    clock.advance(minutes=1)  # tepat 24 jam: usang
    third = fetch(cache, source)
    assert third.served_from == "live" and source.calls == 3
    assert third.entry.cache_id != first.entry.cache_id
    assert data_path(cache.root, first.entry.cache_id).is_file()  # versi lama tidak ditimpa
    old = cache.entry(first.entry.cache_id)
    assert old is not None and old.superseded_at == clock.now


def test_stale_served_when_source_fails(tmp_path: Path) -> None:
    clock = Clock()
    cache = make_cache(tmp_path, clock)
    source = Source()
    first = fetch(cache, source)
    clock.advance(hours=30)
    source.error = SourceUnavailable("portal mati")
    stale = fetch(cache, source, fallback_errors=(SourceUnavailable,))
    assert stale.served_from == "stale_cache" and stale.entry.cache_id == first.entry.cache_id
    assert isinstance(stale.source_error, SourceUnavailable)
    # galat yang tidak terdaftar sebagai fallback tetap dilempar (mis. 404 = dataset hilang)
    source.error = SourceNotFound("hilang")
    with pytest.raises(SourceNotFound):
        fetch(cache, source, fallback_errors=(SourceUnavailable,))


def test_stale_limit_then_error(tmp_path: Path) -> None:
    clock = Clock()
    cache = make_cache(tmp_path, clock)
    source = Source()
    fetch(cache, source)
    source.error = SourceUnavailable("portal mati")
    clock.advance(hours=168)  # tepat batas: masih boleh
    assert fetch(cache, source, fallback_errors=(SourceUnavailable,)).served_from == "stale_cache"
    clock.advance(seconds=1)
    with pytest.raises(SourceUnavailable):
        fetch(cache, source, fallback_errors=(SourceUnavailable,))


def test_no_cache_and_source_fails(tmp_path: Path) -> None:
    source = Source()
    source.error = SourceUnavailable("portal mati")
    with pytest.raises(SourceUnavailable):
        fetch(make_cache(tmp_path, Clock()), source, fallback_errors=(SourceUnavailable,))


def test_force_refresh_is_throttled(tmp_path: Path) -> None:
    clock = Clock()
    cache = make_cache(tmp_path, clock)
    source = Source()
    first = fetch(cache, source)
    clock.advance(seconds=299)
    throttled = fetch(cache, source, force_refresh=True)
    assert throttled.served_from == "cache" and throttled.refresh_throttled
    assert source.calls == 1 and throttled.entry.cache_id == first.entry.cache_id
    clock.advance(seconds=1)
    refreshed = fetch(cache, source, force_refresh=True)
    assert refreshed.served_from == "live" and not refreshed.refresh_throttled
    assert source.calls == 2


def test_single_flight(tmp_path: Path) -> None:
    cache = make_cache(tmp_path, Clock())
    source = Source()
    other = Source()

    async def run() -> list[Any]:
        return await asyncio.gather(
            cache.get_or_fetch(KEY, source),
            cache.get_or_fetch(KEY, source),
            cache.get_or_fetch(KEY, source, force_refresh=True),
            cache.get_or_fetch(QueryKey.build(SLUG), other),
        )

    results = asyncio.run(run())
    assert source.calls == 1 and other.calls == 1
    assert [r.served_from for r in results] == ["live", "cache", "cache", "live"]
    assert len({r.entry.cache_id for r in results[:3]}) == 1
    assert results[2].refresh_throttled
    assert cache._locks == {}  # kunci dibersihkan setelah selesai


def test_served_entries_are_marked_referenced(tmp_path: Path) -> None:
    clock = Clock()
    cache = make_cache(tmp_path, clock)
    result = fetch(cache, Source())
    stored = cache.entry(result.entry.cache_id)
    assert stored is not None and stored.ref_expires_at == result.entry.ref_expires_at
    assert result.entry.ref_expires_at == T0 + timedelta(seconds=900 + 60)
    cache.mark_referenced(result.entry.cache_id, T0)  # lebih awal: tidak memundurkan
    assert cache.entry(result.entry.cache_id).ref_expires_at == result.entry.ref_expires_at  # type: ignore[union-attr]
    with pytest.raises(FileNotFoundError):
        cache.mark_referenced("a" * 26, T0)


# ---------------------------------------------------------------- GC


def _versions(cache: DataCache, clock: Clock, key: QueryKey, n: int, gap: timedelta) -> list[str]:
    ids = []
    for _ in range(n):
        ids.append(cache.put(key, FetchedData(payload())).cache_id)
        clock.now += gap
    return ids


def test_gc_keeps_latest_and_deletes_old_versions(tmp_path: Path) -> None:
    clock = Clock()
    cache = make_cache(tmp_path, clock)
    old, newest = _versions(cache, clock, KEY, 2, timedelta(hours=25))
    clock.advance(hours=1)  # versi lama: umur 26 jam > 24 jam + 15 menit
    report = cache.gc()
    assert report.deleted == 1 and report.freed_bytes > 0
    assert not data_path(cache.root, old).exists() and cache.entry(old) is None
    assert cache.latest(KEY).cache_id == newest  # type: ignore[union-attr]
    clock.advance(days=30)
    cache.gc()  # versi terbaru tidak pernah dihapus karena umur
    assert data_path(cache.root, newest).is_file()


def test_gc_protects_versions_possibly_referenced(tmp_path: Path) -> None:
    clock = Clock()
    cache = make_cache(tmp_path, clock)
    # versi A disajikan sebagai stale lama setelah dibuat, lalu digantikan B
    a = cache.put(KEY, FetchedData(payload())).cache_id
    clock.advance(days=3)
    cache.mark_referenced(a, clock.now + timedelta(seconds=900))
    clock.advance(minutes=1)
    cache.put(KEY, FetchedData(payload()))
    clock.advance(minutes=5)
    assert cache.gc().deleted == 0  # data_ref ke A masih aktif
    clock.advance(minutes=9, seconds=30)  # ref A habis (+15 m), jendela superseded (+16 m) belum
    assert cache.gc().deleted == 0
    clock.advance(seconds=30)  # tepat superseded + 15 menit
    assert cache.gc().deleted == 1 and cache.entry(a) is None


def test_gc_size_limit_prefers_old_versions_then_oldest_latest(tmp_path: Path) -> None:
    clock = Clock()
    cache = make_cache(tmp_path, clock)
    other = QueryKey.build(SLUG, sort="tahun:asc")
    old = cache.put(KEY, FetchedData(payload(20))).cache_id
    clock.advance(minutes=20)
    latest_other = cache.put(other, FetchedData(payload(20))).cache_id
    clock.advance(minutes=20)
    latest_key = cache.put(KEY, FetchedData(payload(20))).cache_id
    clock.advance(minutes=20)  # semua di luar jendela superseded + 15 menit
    size = cache.entry(latest_key).size_bytes  # type: ignore[union-attr]
    cache.max_bytes = size * 2
    cache.gc()
    assert cache.entry(old) is None
    assert cache.entry(latest_other) and cache.entry(latest_key)
    cache.max_bytes = size  # paksa satu lagi: versi terbaru tertua dihapus beserta indeksnya
    cache.gc()
    assert cache.entry(latest_other) is None and cache.latest(other) is None
    assert cache.latest(KEY).cache_id == latest_key  # type: ignore[union-attr]
    cache.mark_referenced(latest_key, clock.now + timedelta(minutes=5))
    cache.max_bytes = 1
    report = cache.gc()  # satu-satunya versi tersisa dilindungi data_ref aktif
    assert cache.entry(latest_key) is not None and report.total_bytes == size


def test_gc_orphans_tmp_and_locked_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clock = Clock(datetime.now(WIB))
    cache = make_cache(tmp_path, clock)
    orphan = cache.root / "data" / ("b" * 26 + ".json")
    orphan.write_text("{}", encoding="utf-8")
    tmp = cache.root / "tmp" / "x.json.abc.tmp"
    tmp.write_text("{}", encoding="utf-8")
    old = clock.now.timestamp() - 2 * 24 * 3600
    os.utime(orphan, (old, old))
    os.utime(tmp, (old, old))
    fresh_orphan = cache.root / "data" / ("c" * 26 + ".json")
    fresh_orphan.write_text("{}", encoding="utf-8")
    # meta tanpa data (bukan versi terbaru) ikut dibersihkan
    ghost = cache.put(KEY, FetchedData(payload())).cache_id
    cache.put(KEY, FetchedData(payload()))
    data_path(cache.root, ghost).unlink()
    report = cache.gc()
    assert not orphan.exists() and not tmp.exists() and fresh_orphan.exists()
    assert cache.entry(ghost) is None and report.deleted == 1

    # berkas terkunci (Windows: sedang dibaca file server) dilewati, dicoba lagi nanti
    victim = cache.put(QueryKey.build(SLUG), FetchedData(payload())).cache_id
    cache.put(QueryKey.build(SLUG), FetchedData(payload()))
    clock.advance(hours=25)
    real_unlink = cache_mod._Files.unlink

    def locked(self: Any, path: Path) -> bool:
        return (
            False if path.stem == victim and path.parent.name == "data" else real_unlink(self, path)
        )

    monkeypatch.setattr(cache_mod._Files, "unlink", locked)
    assert cache.gc().skipped_locked == 1 and cache.entry(victim) is not None
    monkeypatch.setattr(cache_mod._Files, "unlink", real_unlink)
    assert cache.gc().deleted == 1 and cache.entry(victim) is None


def test_maybe_gc_interval_and_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clock = Clock()
    cache = make_cache(tmp_path, clock)
    assert cache.maybe_gc() is not None
    assert cache.maybe_gc() is None
    clock.advance(minutes=10)

    def broken(self: Any) -> None:
        raise OSError("disk")

    monkeypatch.setattr(DataCache, "gc", broken)
    assert cache.maybe_gc() is None  # galat GC tidak menggagalkan permintaan


# ---------------------------------------------------------------- Windows: tulis atomik


def test_reading_old_version_while_new_one_is_written(tmp_path: Path) -> None:
    clock = Clock()
    cache = make_cache(tmp_path, clock)
    old = cache.put(KEY, FetchedData(payload(2)))
    with open(data_path(cache.root, old.cache_id), "rb") as handle:  # file server membaca
        first_bytes = handle.read(10)
        clock.advance(hours=25)
        new = cache.put(KEY, FetchedData(payload(3)))
        cache.gc()
        rest = handle.read()
    assert json.loads(first_bytes + rest)["rows"] == payload(2)["rows"]
    assert cache.latest(KEY).cache_id == new.cache_id  # type: ignore[union-attr]
    assert json.loads(read_data_file(cache.root, new.cache_id))["rows"] == payload(3)["rows"]


def test_replace_retries_on_permission_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sleeps: list[float] = []
    cache = make_cache(tmp_path, Clock(), sleep=sleeps.append)
    real_replace = os.replace
    failures = {"left": 2}

    def flaky(src: Any, dst: Any) -> None:
        if str(dst).endswith(".json") and Path(dst).parent.name == "index" and failures["left"]:
            failures["left"] -= 1
            raise PermissionError("berkas sedang dibuka")
        real_replace(src, dst)

    monkeypatch.setattr(cache_mod.os, "replace", flaky)
    entry = cache.put(KEY, FetchedData(payload()))
    assert cache.latest(KEY) == entry and sleeps == [0.05, 0.1]

    def always(src: Any, dst: Any) -> None:
        raise PermissionError("terkunci")

    monkeypatch.setattr(cache_mod.os, "replace", always)
    with pytest.raises(PermissionError):
        cache.put(KEY, FetchedData(payload()))
    assert list((cache.root / "tmp").iterdir()) == []  # tmp dibersihkan


def test_corrupt_files_are_misses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache = make_cache(tmp_path, Clock())
    entry = cache.put(KEY, FetchedData(payload()))
    (cache.root / "meta" / f"{entry.cache_id}.json").write_text("{rusak", encoding="utf-8")
    assert cache.latest(KEY) is None
    (cache.root / "meta" / f"{entry.cache_id}.json").write_text('{"cache_id": 1}', "utf-8")
    assert cache.entry(entry.cache_id) is None
    index = cache.root / "index" / f"{KEY.digest()}.json"
    index.write_text('{"cache_id": "../x"}', encoding="utf-8")
    assert cache.latest(KEY) is None

    calls = {"n": 0}
    real = Path.read_bytes

    def locked_read(self: Path) -> bytes:
        if self == index:
            calls["n"] += 1
            raise PermissionError("terkunci")
        return real(self)

    monkeypatch.setattr(Path, "read_bytes", locked_read)
    with pytest.raises(PermissionError):
        cache.latest(KEY)
    assert calls["n"] == cache_mod.REPLACE_ATTEMPTS


# ---------------------------------------------------------------- cache kamus (KK-08)


def test_dictionary_cache_rebuilt_when_source_changes(tmp_path: Path) -> None:
    store = DictionaryCache(tmp_path / "cache")
    updated = T0 - timedelta(days=2)
    assert store.get(SLUG, data_updated_at=updated) is None
    store.put(SLUG, {"format": 1, "x": [1]}, data_updated_at=updated, built_at=T0)
    assert store.get(SLUG, data_updated_at=updated) == {"format": 1, "x": [1]}
    assert store.get(SLUG, data_updated_at=T0) is None  # data_updated_at berubah → buat ulang
    assert store.get(SLUG, data_updated_at=updated, override_digest="abc") is None
    store.put(SLUG, {"y": 2}, data_updated_at=None, override_digest="abc")
    assert store.get(SLUG, data_updated_at=None, override_digest="abc") == {"y": 2}
    path = store.path_for(SLUG)
    raw = json.loads(path.read_text("utf-8"))
    path.write_text(json.dumps({**raw, "format": 99}), encoding="utf-8")
    assert store.get(SLUG, data_updated_at=None, override_digest="abc") is None
    path.write_text(json.dumps({**raw, "payload": [1]}), encoding="utf-8")
    assert store.get(SLUG, data_updated_at=None, override_digest="abc") is None
    with pytest.raises(ValueError):
        store.get("../luar", data_updated_at=None)
