"""Bagian bersama model kontrak: basis model, tipe waktu WIB, dan penjaga angka.

Waktu (Dok 4 bagian 2, KB-07): semua datetime keluaran ditulis ISO 8601 dengan
offset +07:00 memakai konstanta WIB dari config. Datetime tanpa zona waktu ditolak.
Normalisasi offset +07:07 dari portal adalah tugas services/normalize (Langkah 2);
model ini hanya mengonversi waktu yang sudah ber-offset benar.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, PlainSerializer, StringConstraints

from mcp_bogor.config import WIB

# Deret digit, termasuk pemisah ribuan/desimal: "9.160.885", "1.234,5", "2024".
_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)*")
_DIGIT_RE = re.compile(r"\d")
NUMBER_MASK = "[angka]"


class ContractModel(BaseModel):
    """Basis semua model kontrak: field tak dikenal ditolak."""

    model_config = ConfigDict(extra="forbid")


def contains_number(text: str) -> bool:
    return _DIGIT_RE.search(text) is not None


def mask_numbers(text: str) -> str:
    """Ganti setiap deret angka dengan NUMBER_MASK (aturan 2 CLAUDE.md, FR-10)."""
    return _NUMBER_RE.sub(NUMBER_MASK, text)


def reject_numbers(text: str) -> str:
    if contains_number(text):
        raise ValueError("teks ini tidak boleh memuat angka (FR-10)")
    return text


def to_wib(value: datetime) -> datetime:
    """Konversi datetime ber-offset ke WIB; buang mikrodetik agar round-trip JSON sama."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime wajib punya offset zona waktu; datetime naive ditolak")
    return value.astimezone(WIB).replace(microsecond=0)


def format_wib(value: datetime) -> str:
    return to_wib(value).isoformat(timespec="seconds")


WibDatetime = Annotated[
    datetime,
    AfterValidator(to_wib),
    PlainSerializer(format_wib, return_type=str, when_used="json"),
]
"""Datetime yang selalu disimpan di WIB dan diserialisasi 'YYYY-MM-DDTHH:MM:SS+07:00'."""

RequestId = Annotated[str, StringConstraints(min_length=1, max_length=64)]
NoNumberText = Annotated[str, StringConstraints(min_length=1), AfterValidator(reject_numbers)]
