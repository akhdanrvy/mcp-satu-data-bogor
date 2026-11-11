"""KB-46: angka di teks bebas portal disamarkan di respons tool; berkas data_ref tetap asli."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from starlette.testclient import TestClient

from mcp_bogor.fileserver.app import create_app
from mcp_bogor.services.normalize import NUMBER_MASK, mask_free_text
from tests.tool_support import ToolEnv, synced_settings

KEMISKINAN = "tingkat-kemiskinan-di-kota-bogor-2"
NOTES = "revisi angka menjadi 5,89 persen tahun 2025"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (NOTES, "revisi angka menjadi [angka] persen tahun 2025"),  # contoh wajib Akhdan
        ("nilai 9.160.885 kunjungan", "nilai [angka] kunjungan"),
        ("Peraturan BPS Nomor 3 Tahun 2019", "Peraturan BPS Nomor [angka] Tahun 2019"),
        ("periode 1899, 1900, 2100, 2101", "periode [angka], 1900, 2100, [angka]"),
        ("kode 3271 dan 12345 serta 0,45", "kode [angka] dan [angka] serta [angka]"),
        ("naik 46% dari 2024 ke 2025", "naik [angka]% dari 2024 ke 2025"),
        ("tanpa angka", "tanpa angka"),
        ("", ""),
        (None, None),
    ],
)
def test_mask_free_text(text: str | None, expected: str | None) -> None:
    assert mask_free_text(text) == expected


def test_mask_is_idempotent() -> None:
    once = mask_free_text(NOTES)
    assert mask_free_text(once) == once and NUMBER_MASK in (once or "")


@pytest.fixture
def env(tmp_path: Path) -> ToolEnv:
    settings = synced_settings(tmp_path / "katalog")
    conn = sqlite3.connect(settings.db_path)
    conn.execute("UPDATE datasets SET change_notes = ? WHERE slug = ?", (NOTES, KEMISKINAN))
    conn.commit()
    conn.close()
    return ToolEnv(settings.db_path, tmp_path)


def test_info_and_data_mask_change_notes_and_meaning(env: ToolEnv) -> None:
    masked = "revisi angka menjadi [angka] persen tahun 2025"
    info = env.info(slug=KEMISKINAN)
    assert info["provenance"]["change_notes"] == masked
    meanings = {c["name"]: c["meaning"] for c in info["result"]["columns"]}
    assert meanings["kode_provinsi"].endswith("Nomor [angka] Tahun 2019")  # info: arti lengkap
    assert "5,89" not in str(info) and "Nomor 3 " not in str(info)
    runs = re.findall(r"\d+(?:[.,]\d+)*", info["result"]["description"] or "")
    assert runs and all(len(r) == 4 and 1900 <= int(r) <= 2100 for r in runs)  # hanya tahun

    data = env.data(slug=KEMISKINAN, periods=[2025])
    assert data["provenance"]["change_notes"] == masked
    columns = {c["name"]: c for c in data["result"]["columns"]}
    assert columns["kode_provinsi"]["meaning"] is None  # dimension: arti tidak dikirim
    assert columns["kode_provinsi"]["meaning_source"] == "template"
    assert columns["tingkat_kemiskinan"]["meaning"]  # measure: arti tetap ada

    parts = urlsplit(data["result"]["data_ref"]["url"])
    client = TestClient(create_app(env.settings, clock=lambda: env.clock.now.timestamp()))
    body = client.get(f"{parts.path}?{parts.query}").json()
    assert body["provenance"]["change_notes"] == NOTES  # berkas: teks asli
