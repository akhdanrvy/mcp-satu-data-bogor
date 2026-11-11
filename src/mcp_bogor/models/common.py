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

_DIGIT_RE = re.compile(r"\d")
# Angka berformat Indonesia: pemisah ribuan titik dan/atau desimal koma.
# Contoh: "9.160.885", "1.234,5", "12,5". Angka polos ("2024", "3271") tidak termasuk.
_ID_NUMBER = r"-?(?:\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+,\d+)"
_ID_NUMBER_FULL = re.compile(rf"^{_ID_NUMBER}$")
_ID_NUMBER_ANYWHERE = re.compile(_ID_NUMBER)


class ContractModel(BaseModel):
    """Basis semua model kontrak: field tak dikenal ditolak."""

    model_config = ConfigDict(extra="forbid")


def contains_number(text: str) -> bool:
    return _DIGIT_RE.search(text) is not None


def reject_numbers(text: str) -> str:
    if contains_number(text):
        raise ValueError("teks ini tidak boleh memuat angka (FR-10)")
    return text


def looks_like_formatted_number(value: str) -> bool:
    """True bila seluruh nilai berupa angka berformat Indonesia (mis. "9.160.885")."""
    return _ID_NUMBER_FULL.match(value.strip()) is not None


def contains_formatted_number(text: str) -> bool:
    return _ID_NUMBER_ANYWHERE.search(text) is not None


# KK-I: nama kolom yang selalu dimension apa pun tipenya, dan sah berisi label angka.
# Langkah 6 (keputusan Akhdan 11 Nov 2026): bulan, triwulan, semester ditambahkan.
LABEL_COLUMN_NAMES = frozenset({"tahun", "bulan", "triwulan", "semester", "satuan"})


def is_numeric_label_column(name: str) -> bool:
    """KK-I/KB-15: kolom yang sah berisi label berbentuk angka.

    Nama memuat token `kode` (`kode_*`, `bps_kode_*`, `kemendagri_kode_*`), berawalan `no_`,
    atau bernama persis `tahun`/`bulan`/`triwulan`/`semester`/`satuan`. Bukan substring
    bebas: `kodepos`, `nomor`,
    `nilai_no`, `tahunan` tidak termasuk.
    """
    folded = name.casefold()
    return folded in LABEL_COLUMN_NAMES or "kode" in folded.split("_") or folded.startswith("no_")


def reject_formatted_number(value: str) -> str:
    if looks_like_formatted_number(value):
        raise ValueError(f"nilai {value!r} berbentuk angka berformat Indonesia (KT-03)")
    return value


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
PlaceholderStr = Annotated[
    str,
    StringConstraints(pattern=r"^\S+$", max_length=200),
    AfterValidator(reject_formatted_number),
]
"""Nilai placeholder pesan galat (slug, column): satu token, bukan angka berformat."""
