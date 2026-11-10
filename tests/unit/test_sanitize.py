"""adapters/sanitize: mode drop (adaptor), contacts=False (baris data), find_private_keys.

Mode redact (fixture) diuji lewat tests/unit/test_record_fixtures.py.
"""

from __future__ import annotations

from mcp_bogor.adapters.sanitize import (
    REDACTED,
    SanitizeStats,
    find_private_keys,
    sanitize,
)

RAW = {
    "slug": "x",
    "username": "u",
    "organisasi_email": "a@b.test",
    "organisasi_notelp": None,
    "organisasi_alamat": REDACTED,
    "metadata": [
        {"key": "Kontak Produsen", "value": "0812"},
        {"key": "Satuan Dataset", "value": "Persen", "muid": 1},
        {"label": "Kontak Produsen", "teks": "x"},
    ],
    "Kontak Produsen": "langsung",
}


def test_drop_mode_removes_keys_and_contact_entries() -> None:
    stats = SanitizeStats()
    clean = sanitize(RAW, stats, mode="drop")
    assert clean == {"slug": "x", "metadata": [{"key": "Satuan Dataset", "value": "Persen"}]}
    assert find_private_keys(clean) == []
    assert stats.removed_keys == 2
    assert stats.redacted_contacts == 4  # email, Kontak Produsen (kunci), dua entri metadata


def test_drop_mode_idempotent() -> None:
    once = sanitize(RAW, mode="drop")
    assert sanitize(once, mode="drop") == once


def test_redact_output_converts_to_drop() -> None:
    redacted = sanitize(RAW)
    assert find_private_keys(redacted)  # redact masih menyimpan kuncinya
    assert sanitize(redacted, mode="drop") == sanitize(RAW, mode="drop")


def test_contacts_false_only_drops_account_keys() -> None:
    rows = [{"alamat": "Jl. A", "email": "info@sekolah.test", "cuid": 1, "nested": {"username": 2}}]
    assert sanitize(rows, contacts=False) == [
        {"alamat": "Jl. A", "email": "info@sekolah.test", "nested": {}}
    ]


def test_find_private_keys_reports_locations() -> None:
    found = find_private_keys(RAW)
    assert "$.username" in found and "$.organisasi_email" in found
    assert "$.metadata[0] (metadata kontak)" in found
    assert "$.metadata[1].muid" in found
    assert find_private_keys([{"a": [{"b": 1}]}]) == []
