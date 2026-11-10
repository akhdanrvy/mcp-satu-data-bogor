"""Perata bentuk JSON portal di adaptor (KB-20, bentuk asli rekaman 2026-11-10)."""

from __future__ import annotations

from mcp_bogor.adapters.opendata_jabar import flatten_metadata, flatten_metadata_filter
from mcp_bogor.services.periods import periods_available
from tests.http_fixtures import SYNTHETIC_HTTP, load_exchange


def test_flatten_metadata_filter_real_shape() -> None:
    raw = [
        {"key": "tahun", "type": "select", "value": ["2015", "2016"]},
        {"key": "kecepatan", "type": "free text", "value": []},
        {"key": "rusak", "type": "select", "value": "bukan-list"},
        {"type": "select", "value": ["tanpa key"]},
        "bukan-objek",
        {"key": "tahun", "type": "select", "value": ["2099"]},  # ganda: yang pertama dipakai
    ]
    assert flatten_metadata_filter(raw) == {
        "tahun": ["2015", "2016"],
        "kecepatan": [],
        "rusak": [],
    }


def test_flatten_metadata_filter_accepts_flat_and_garbage() -> None:
    assert flatten_metadata_filter({"tahun": [2024], "x": None}) == {"tahun": [2024], "x": []}
    assert flatten_metadata_filter(None) == {}
    assert flatten_metadata_filter("teks") == {}


def test_flatten_metadata() -> None:
    raw = [
        {"id": 1, "type": "bisnis", "key": "Dimensi Dataset Awal", "value": "2019"},
        {"id": 2, "type": "bisnis", "key": "Dimensi Dataset Akhir", "value": "2024"},
        {"id": 3, "type": "bisnis", "key": "Dimensi Dataset Akhir", "value": "2099"},
        {"id": 4, "value": "tanpa key"},
    ]
    assert flatten_metadata(raw) == {
        "Dimensi Dataset Awal": "2019",
        "Dimensi Dataset Akhir": "2024",
    }
    assert flatten_metadata(None) == {}


def test_synthetic_bigdata_uses_real_metadata_filter_shape() -> None:
    body = load_exchange(SYNTHETIC_HTTP / "bigdata_messy.json")["response"]["json"]
    assert isinstance(body["metadata_filter"], list)
    assert periods_available(flatten_metadata_filter(body["metadata_filter"])) == [2024, 2025]
