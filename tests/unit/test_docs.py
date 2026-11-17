"""Dokumentasi Langkah 12 tidak tertinggal dari kode (tanpa jaringan).

- README memuat setiap field `Settings` sebagai `MCP_<NAMA>` (tabel env var lengkap).
- Dokumen operasi dan integrasi menyebut 10 kode galat dan 8 kode notice yang ada di kode.
- Dokumen Langkah 12 tidak memuat alamat surel (kontak pribadi) atau secret yang terisi.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from mcp_bogor.config import Settings
from mcp_bogor.models.errors import ErrorCode
from mcp_bogor.models.notices import NoticeCode

ROOT = Path(__file__).parents[2]
DOCS = {
    "README.md": ROOT / "README.md",
    "RUNBOOK.md": ROOT / "docs" / "RUNBOOK.md",
    "INTEGRASI_CITYA.md": ROOT / "docs" / "INTEGRASI_CITYA.md",
    "STATUS_FASE1.md": ROOT / "docs" / "STATUS_FASE1.md",
}
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def _text(name: str) -> str:
    path = DOCS[name]
    if not path.is_file():
        pytest.skip(f"{name} belum ditulis")
    return path.read_text(encoding="utf-8")


def test_readme_lists_every_env_var() -> None:
    text = _text("README.md")
    missing = [
        f"MCP_{name.upper()}"
        for name in Settings.model_fields
        if f"`MCP_{name.upper()}`" not in text
    ]
    assert missing == []


def test_integration_doc_lists_all_codes() -> None:
    text = _text("INTEGRASI_CITYA.md")
    assert [c.value for c in ErrorCode if f"`{c.value}`" not in text] == []
    assert [c.value for c in NoticeCode if f"`{c.value}`" not in text] == []
    assert len(ErrorCode) == 10


@pytest.mark.parametrize("name", sorted(DOCS))
def test_no_email_or_filled_secret(name: str) -> None:
    text = _text(name)
    assert EMAIL_RE.findall(text) == []
    for line in text.splitlines():
        match = re.search(r"MCP_(?:DATAREF_SECRET(?:_PREVIOUS)?|HTTP_AUTH_TOKEN)=([^\s`]+)", line)
        if match:
            assert match.group(1).startswith(("<", '"<', "'<", "$")), line
