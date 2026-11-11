"""services/info (Langkah 8b) dan DictionaryCache.get_any: cabang yang tidak muncul di rekaman."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from mcp_bogor.adapters.base import ColumnProbe
from mcp_bogor.config import WIB
from mcp_bogor.services.catalog import CatalogDataset
from mcp_bogor.services.dictionary import build_dictionary
from mcp_bogor.services.info import (
    InfoBundle,
    build_bundle,
    build_info_result,
    build_provenance,
    dictionary_notices,
    info_candidates,
    source_url,
)
from mcp_bogor.storage.cache import DictionaryCache

NOW = datetime(2026, 11, 11, 9, 0, tzinfo=WIB)

DATASET = CatalogDataset(
    slug="contoh",
    name="Contoh",
    opd="OPD",
    topik=None,
    kategori=None,
    schema="s",
    table="t",
    satuan="Orang",
    frekuensi="Tahunan",
    tahun_awal=2020,
    tahun_akhir=2022,
    tingkat_penyajian="Kota Bogor",
    cakupan=None,
    deskripsi_html=None,
    deskripsi_text="kata " * 300,
    data_updated_at=None,
    freshness_source=None,
    metadata_changed_after_approve=False,
    change_notes=None,
    official_status="draf",
    official_basis="validate=draft",
)


def _dictionary(columns: list[str], rows: list[dict[str, Any]], **filters: list[Any]) -> Any:
    probe = ColumnProbe(columns=columns, metadata_filter=dict(filters), total_rows=len(rows))
    return build_dictionary(description=None, probe=probe, sample_rows=rows), probe


@pytest.mark.parametrize(
    ("base", "expected"),
    [
        ("https://opendata.kotabogor.go.id/api", "https://opendata.kotabogor.go.id/dataset/x"),
        ("https://opendata.kotabogor.go.id/api/", "https://opendata.kotabogor.go.id/dataset/x"),
        ("http://portal.test", "http://portal.test/dataset/x"),
    ],
)
def test_source_url(base: str, expected: str) -> None:
    assert source_url(base, "x") == expected


def test_bundle_without_period_column_uses_catalog_bounds() -> None:
    dictionary, probe = _dictionary(["nama", "jumlah"], [{"nama": "A", "jumlah": 3}], nama=["A"])
    bundle = build_bundle(dictionary, probe, [{"nama": "A", "jumlah": 3}], NOW)
    assert bundle.periods_available is None
    assert bundle.dimension_values == {"nama": ["A"]}  # nilai measure tidak pernah disimpan
    result = build_info_result(DATASET, bundle, description_max_chars=600, top_values=10)
    assert result.periods_available == []
    prov = build_provenance(
        DATASET, base_url="http://p/api", retrieved_at=NOW, served_from="live", periods=None
    )
    assert (prov.period_start, prov.period_end) == (2020, 2022)
    inverted = replace(DATASET, tahun_awal=2023, tahun_akhir=2021)
    prov = build_provenance(
        inverted, base_url="http://p/api", retrieved_at=NOW, served_from="live", periods=[]
    )
    assert (prov.period_start, prov.period_end) == (None, None)


def test_period_values_fall_back_to_sample_rows() -> None:
    rows = [{"tahun": 2021, "jumlah": 1}, {"tahun": "2022", "jumlah": 2}]
    dictionary, probe = _dictionary(["tahun", "jumlah"], rows)
    assert build_bundle(dictionary, probe, rows, NOW).periods_available == [2021, 2022]


def test_notices_for_no_measure_and_unit_conflict() -> None:
    no_measure, _ = _dictionary(["nama"], [{"nama": "A"}])
    assert [n.code.value for n in dictionary_notices(no_measure)] == ["NO_MEASURE_COLUMN"]
    rows = [{"jumlah": 1, "satuan": "ORANG"}, {"jumlah": 2, "satuan": "UNIT"}]
    conflict, _ = _dictionary(["jumlah", "satuan"], rows)
    codes = [(n.code.value, n.column) for n in dictionary_notices(conflict)]
    assert ("UNIT_CONFLICT", "jumlah") in codes


def test_bundle_round_trip_and_rejects_unknown_shape() -> None:
    rows = [{"tahun": 2021, "jumlah": 1}]
    dictionary, probe = _dictionary(["tahun", "jumlah"], rows, tahun=["2021"])
    bundle = build_bundle(dictionary, probe, rows, NOW)
    assert InfoBundle.from_dict(bundle.to_dict()) == bundle
    data = bundle.to_dict()
    with pytest.raises(ValueError):
        InfoBundle.from_dict({**data, "format": 2})
    with pytest.raises(ValueError):
        InfoBundle.from_dict({k: v for k, v in data.items() if k != "retrieved_at"})


def test_info_candidates_are_cumulative() -> None:
    rows = [{"tahun": 2020 + i, "kategori": f"k{i}", "jumlah": i} for i in range(8)]
    dictionary, probe = _dictionary(
        ["tahun", "kategori", "jumlah"],
        rows,
        tahun=[str(2020 + i) for i in range(8)],
        kategori=[f"k{i}" for i in range(8)],
    )
    bundle = build_bundle(dictionary, probe, rows, NOW)
    result = build_info_result(DATASET, bundle, description_max_chars=600, top_values=10)
    steps = list(info_candidates(result))
    reasons = [r.value if r else None for r, _ in steps]
    assert reasons == [
        None,
        "top_values_dipangkas",
        "top_values_dipangkas",
        "deskripsi_dipangkas",
        "arti_kolom_dipangkas",
    ]
    by_name = {c.name: c for c in steps[1][1].columns}
    assert len(by_name["kategori"].top_values or []) == 5
    by_name = {c.name: c for c in steps[2][1].columns}
    assert by_name["kategori"].top_values is None and by_name["tahun"].top_values
    assert len(steps[3][1].description or "") <= 200 < len(result.description or "")


def test_dictionary_cache_get_any(tmp_path: Path) -> None:
    store = DictionaryCache(tmp_path)
    assert store.get_any("contoh") is None
    store.put(
        "contoh",
        {"a": 1},
        data_updated_at=NOW - timedelta(days=1),
        override_digest="d",
        built_at=NOW,
    )
    record = store.get_any("contoh")
    assert record is not None and record.payload == {"a": 1}
    assert record.data_updated_at == NOW - timedelta(days=1)
    assert (record.override_digest, record.built_at) == ("d", NOW)
    path = store.path_for("contoh")
    path.write_text('{"format": 1, "payload": [1]}', encoding="utf-8")
    assert store.get_any("contoh") is None
    path.write_text('{"format": 9, "payload": {}}', encoding="utf-8")
    assert store.get_any("contoh") is None
    path.write_text('{"format": 1, "payload": {}, "built_at": "bukan-waktu"}', encoding="utf-8")
    assert store.get_any("contoh") is None


def test_dictionary_cache_long_slug_short_path(tmp_path: Path) -> None:
    """KB-44: slug portal sampai 202 karakter; nama berkas tetap pendek (MAX_PATH Windows)."""
    slug = "a" * 202
    deep = tmp_path / ("d" * 30)
    store = DictionaryCache(deep)
    store.put(slug, {"ok": True}, data_updated_at=None, built_at=NOW)
    assert store.get(slug, data_updated_at=None) == {"ok": True}
    path = store.path_for(slug)
    assert len(path.name) == len("x" * 40 + ".json") and slug not in str(path)
    assert path.parent == deep / "dictionary"
    assert list((deep / "tmp").iterdir()) == []
