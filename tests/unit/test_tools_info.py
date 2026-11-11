"""Tool get_dataset_info (Langkah 8b): katalog, cache kamus, probe, stale, notice, anggaran.

Tanpa jaringan: katalog dari 751 record rekaman, FakeSourceAdapter dengan /bigdata rekaman.
Setiap tes memakai MCP_CACHE_DIR sendiri; katalog SQLite dipakai bersama (baca saja).
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from mcp_bogor.adapters.base import (
    SourceNotFound,
    SourceProtocolError,
    SourceRateLimited,
    SourceUnavailable,
)
from mcp_bogor.models import InfoResult, ToolEnvelope
from mcp_bogor.services.budget import estimate_tokens
from mcp_bogor.tools.common import ToolDeps
from mcp_bogor.tools.info import get_dataset_info
from tests.fakes import FakeSourceAdapter, make_settings
from tests.tool_support import SYNC_AT, FixedClock, synced_settings

ROOT = Path(__file__).parents[2]
KEMISKINAN = "tingkat-kemiskinan-di-kota-bogor-2"
PAD = "realisasi-pendapatan-asli-daerah-pad-kota-bogor"
PAJAJARAN = (
    "kecepatan-lalu-lintas-segmen-1-dan-2-berdasarkan-hari-operasional-di-jalan-raya-pajajaran-"
    "kota-bogor"
)
NO_BIGDATA = "indeks-kepuasan-masyarakat-di-kota-bogor"  # di katalog; /bigdata tidak direkam


@pytest.fixture(scope="module")
def catalog_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return synced_settings(tmp_path_factory.mktemp("katalog-info")).db_path


class Env:
    def __init__(self, db_path: Path, tmp_path: Path, **overrides: Any) -> None:
        values: dict[str, Any] = {
            "db_path": db_path,
            "cache_dir": tmp_path / "cache",
            "overrides_dir": tmp_path / "overrides",
        }
        values.update(overrides)
        self.settings = make_settings(tmp_path, **values)
        self.adapter = FakeSourceAdapter()
        self.clock = FixedClock(SYNC_AT + timedelta(hours=1))
        self.deps = ToolDeps(self.settings, now=self.clock, adapter=self.adapter)

    def call(self, **args: Any) -> dict[str, Any]:
        out = asyncio.run(get_dataset_info(self.deps, args))
        ToolEnvelope[InfoResult].model_validate(out)  # KT-02
        return out


@pytest.fixture
def env(catalog_db: Path, tmp_path: Path) -> Env:
    return Env(catalog_db, tmp_path)


def _col(out: dict[str, Any], name: str) -> dict[str, Any]:
    return next(c for c in out["result"]["columns"] if c["name"] == name)


# ---------------------------------------------------------------- alur dasar


def test_first_call_probes_once_then_cache(env: Env) -> None:
    first = env.call(slug=KEMISKINAN, request_id="rq-1")
    assert first["status"] == "ok" and first["request_id"] == "rq-1"
    assert env.adapter.calls == ["probe"]
    assert first["provenance"]["served_from"] == "live"
    env.clock.now += timedelta(hours=2)
    second = env.call(slug=KEMISKINAN)
    assert env.adapter.calls == ["probe"]  # hit: 0 permintaan
    assert second["provenance"]["served_from"] == "cache"
    assert second["provenance"]["retrieved_at"] == first["provenance"]["retrieved_at"]
    assert second["result"] == first["result"]


def test_result_fields_from_catalog_and_dictionary(env: Env) -> None:
    out = env.call(slug=KEMISKINAN)
    result = out["result"]
    assert result["slug"] == KEMISKINAN and result["name"]
    assert result["satuan"] == "Persen" and result["frekuensi"] == "Tahunan"
    assert result["tingkat_penyajian"] == "Kota Bogor"
    assert result["periods_available"] == [2025]  # N-01: tahun terakhir yang ada
    assert result["row_count"] == 1 and result["dictionary_validated"] is True
    assert len(result["description"]) <= 600
    roles = {c["name"]: c["role"] for c in result["columns"]}
    assert roles["tingkat_kemiskinan"] == "measure" and roles["id"] == "id"
    assert _col(out, "tingkat_kemiskinan")["top_values"] is None
    assert _col(out, "tahun")["top_values"] == ["2025"]
    prov = out["provenance"]
    assert (prov["period_start"], prov["period_end"]) == (2025, 2025)
    assert prov["data_updated_at"] and prov["freshness_source"] in {"approve", "mdate"}
    assert out["officialness"]["status"] in {"resmi", "draf"} and out["officialness"]["basis"]
    assert out["quality_flags"] == [] and out["notices"] == []


def test_source_url_follows_kb40(catalog_db: Path, tmp_path: Path) -> None:
    env = Env(catalog_db, tmp_path, source_base_url="https://opendata.kotabogor.go.id/api/")
    out = env.call(slug=KEMISKINAN)
    assert out["provenance"]["source_url"] == (
        "https://opendata.kotabogor.go.id/dataset/tingkat-kemiskinan-di-kota-bogor-2"
    )


def test_unknown_slug_never_calls_portal(env: Env) -> None:
    out = env.call(slug="slug-yang-tidak-ada")
    assert out["error"]["code"] == "DATASET_NOT_FOUND" and out["result"] is None
    assert "slug-yang-tidak-ada" in out["error"]["message"]
    bad = env.call(slug="Huruf Besar/..")
    assert bad["error"]["code"] == "INVALID_PARAM" and "'slug'" in bad["error"]["message"]
    extra = env.call(slug=KEMISKINAN, periods=[2025])
    assert (
        extra["error"]["code"] == "INVALID_PARAM" and "tidak dikenal" in extra["error"]["message"]
    )
    assert env.adapter.calls == []


def test_longest_real_slug_fits_error_template(env: Env) -> None:
    slug = "x" * 202  # KB-43: slug portal terpanjang 202 karakter
    assert env.call(slug=slug)["error"]["code"] == "DATASET_NOT_FOUND"


# ---------------------------------------------------------------- kamus: validasi dan override


def test_pad_dictionary_not_validated_notice(env: Env) -> None:
    out = env.call(slug=PAD)
    assert out["status"] == "ok"
    assert out["result"]["dictionary_validated"] is False
    assert [(n["code"], n["column"]) for n in out["notices"]] == [
        ("DICTIONARY_NOT_VALIDATED", "total_pendapatan")
    ]
    column = _col(out, "total_pendapatan")
    assert (column["role"], column["meaning"], column["meaning_source"]) == (
        "measure",
        None,
        "none",
    )
    assert column["unit"] == "RUPIAH" and column["top_values"] is None


def test_repository_override_is_applied(catalog_db: Path, tmp_path: Path) -> None:
    env = Env(catalog_db, tmp_path, overrides_dir=ROOT / "overrides")
    column = _col(env.call(slug=PAJAJARAN), "no_segmen")
    assert (column["meaning"], column["meaning_source"], column["role"]) == (
        "nomor segmen ruas jalan",
        "override",
        "dimension",
    )


def _write_override(env: Env, slug: str, columns: dict[str, Any]) -> None:
    folder = env.settings.overrides_dir
    folder.mkdir(parents=True, exist_ok=True)
    lines = [f"slug: {slug}", "columns:"]
    for name, fields in columns.items():
        lines.append(f"  {name}:")
        lines.extend(f"    {key}: {value}" for key, value in fields.items())
    (folder / f"{slug}.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_override_change_rebuilds_dictionary(env: Env) -> None:
    env.call(slug=KEMISKINAN)
    _write_override(env, KEMISKINAN, {"tahun": {"meaning": "tahun data"}})
    out = env.call(slug=KEMISKINAN)
    assert env.adapter.calls == ["probe", "probe"]
    assert _col(out, "tahun")["meaning"] == "tahun data"
    assert out["provenance"]["served_from"] == "live"


def test_data_updated_at_change_rebuilds_dictionary(tmp_path: Path) -> None:
    settings = synced_settings(tmp_path / "katalog")
    env = Env(settings.db_path, tmp_path)
    env.call(slug=KEMISKINAN)
    conn = sqlite3.connect(settings.db_path)
    conn.execute(
        "UPDATE datasets SET data_updated_at = ? WHERE slug = ?",
        ("2026-11-12T08:00:00+07:00", KEMISKINAN),
    )
    conn.commit()
    conn.close()
    env.call(slug=KEMISKINAN)
    assert env.adapter.calls == ["probe", "probe"]


def test_broken_override_is_internal(env: Env) -> None:
    folder = env.settings.overrides_dir
    folder.mkdir(parents=True)
    (folder / f"{KEMISKINAN}.yaml").write_text("slug: [rusak", encoding="utf-8")
    assert env.call(slug=KEMISKINAN)["error"]["code"] == "INTERNAL"
    _write_override(env, KEMISKINAN, {"kolom_tidak_ada": {"meaning": "x"}})
    assert env.call(slug=KEMISKINAN)["error"]["code"] == "INTERNAL"


def test_corrupt_cached_bundle_is_rebuilt(env: Env) -> None:
    env.call(slug=KEMISKINAN)
    path = env.deps.require_dictionary_cache().path_for(KEMISKINAN)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["payload"]["format"] = 99
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert env.call(slug=KEMISKINAN)["status"] == "ok"
    assert env.adapter.calls == ["probe", "probe"]


# ---------------------------------------------------------------- portal gagal (UT-14 sisi info)


def _make_stale(env: Env) -> None:
    """Hangatkan cache, lalu ubah override agar versi tersimpan tidak lagi cocok."""
    env.call(slug=KEMISKINAN)
    _write_override(env, KEMISKINAN, {"tahun": {"meaning": "tahun data"}})


def test_stale_dictionary_when_portal_fails(env: Env) -> None:
    _make_stale(env)
    env.adapter.fail_with = SourceUnavailable("portal mati")
    out = env.call(slug=KEMISKINAN)
    assert out["status"] == "partial" and out["error"]["code"] == "STALE_DATA"
    assert out["provenance"]["served_from"] == "stale_cache"
    assert out["truncated"] is False and out["result"]["slug"] == KEMISKINAN
    env.adapter.fail_with = SourceRateLimited("429")
    assert env.call(slug=KEMISKINAN)["error"]["code"] == "STALE_DATA"


def test_stale_dictionary_too_old_is_unavailable(env: Env) -> None:
    _make_stale(env)
    env.adapter.fail_with = SourceUnavailable("portal mati")
    env.clock.now += timedelta(hours=169)
    out = env.call(slug=KEMISKINAN)
    assert out["error"]["code"] == "SOURCE_UNAVAILABLE" and out["result"] is None


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (SourceUnavailable("mati"), "SOURCE_UNAVAILABLE"),
        (SourceRateLimited("429"), "RATE_LIMITED"),
        (SourceNotFound("404", path="/x"), "DATASET_NOT_FOUND"),
        (SourceProtocolError("rusak"), "INTERNAL"),
    ],
)
def test_portal_errors_without_cache(env: Env, error: Exception, code: str) -> None:
    env.adapter.fail_with = error  # type: ignore[assignment]
    out = env.call(slug=KEMISKINAN)
    assert out["error"]["code"] == code and out["result"] is None
    assert out["error"]["retryable"] is (code in {"SOURCE_UNAVAILABLE", "RATE_LIMITED", "INTERNAL"})


def test_dataset_without_recorded_bigdata_is_unavailable(env: Env) -> None:
    out = env.call(slug=NO_BIGDATA)
    assert out["error"]["code"] == "SOURCE_UNAVAILABLE"  # fake: /bigdata tak direkam


def test_missing_adapter_is_internal(catalog_db: Path, tmp_path: Path) -> None:
    env = Env(catalog_db, tmp_path)
    env.deps.adapter = None
    assert env.call(slug=KEMISKINAN)["error"]["code"] == "INTERNAL"


# ---------------------------------------------------------------- single-flight


def test_concurrent_calls_probe_once(env: Env) -> None:
    async def both() -> list[dict[str, Any]]:
        return await asyncio.gather(
            get_dataset_info(env.deps, {"slug": PAD}), get_dataset_info(env.deps, {"slug": PAD})
        )

    outs = asyncio.run(both())
    assert env.adapter.calls == ["probe"]
    assert sorted(o["provenance"]["served_from"] for o in outs) == ["cache", "live"]
    assert len(env.deps.locks) == 0


# ---------------------------------------------------------------- anggaran token (UT-13 sisi info)


def test_truncation_steps_and_too_large(catalog_db: Path, tmp_path: Path) -> None:
    full = Env(catalog_db, tmp_path / "a").call(slug=PAJAJARAN)
    tokens = estimate_tokens(full)
    seen = set()
    for limit in range(tokens - 1, tokens - 900, -25):
        out = Env(catalog_db, tmp_path / f"l{limit}", max_response_tokens=limit).call(
            slug=PAJAJARAN
        )
        if out["status"] == "error":
            assert out["error"]["code"] == "RESPONSE_TOO_LARGE"
            break
        assert out["status"] == "partial" and out["truncated"] is True
        assert estimate_tokens(out) <= limit
        seen.add(out["truncated_reason"])
    assert {"top_values_dipangkas", "deskripsi_dipangkas"} <= seen
    tiny = Env(catalog_db, tmp_path / "t", max_response_tokens=100).call(slug=PAJAJARAN)
    assert tiny["error"]["code"] == "RESPONSE_TOO_LARGE"
    assert "Batas: 100." in tiny["error"]["message"]


def test_top_values_setting(catalog_db: Path, tmp_path: Path) -> None:
    out = Env(catalog_db, tmp_path, top_values=2).call(slug=PAJAJARAN)
    assert _col(out, "tahun")["top_values"] == ["2015", "2016"]
    none = Env(catalog_db, tmp_path / "z", top_values=0).call(slug=PAJAJARAN)
    assert all(c["top_values"] is None for c in none["result"]["columns"])
