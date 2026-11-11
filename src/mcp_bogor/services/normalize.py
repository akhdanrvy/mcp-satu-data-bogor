"""Normalisasi nilai, baris, dan label portal (Dok 2 bagian 5, NB-01..08).

Semua fungsi murni: tanpa jaringan, berkas, atau jam sistem. Nilai asli dari portal
tidak pernah dikoreksi (ADR-007); yang gagal diurai menjadi None dan dicatat sebagai
ParseIssue tanpa menyalin nilai mentahnya (log tidak berisi isi baris data).
"""

from __future__ import annotations

import html
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from mcp_bogor.config import WIB

# NB-01: offset tidak baku dari portal; dibaca sebagai WIB tanpa menggeser jam dinding.
NONSTANDARD_PORTAL_OFFSET = timedelta(hours=7, minutes=7)

# NB-02 / NB-06: teks yang berarti kosong (dibandingkan setelah strip, tanpa beda huruf).
EMPTY_TEXTS = frozenset({"", "-", "none", "null"})

# NB-04 (KB-17): kolom id internal portal yang dibuang dari baris yang diserahkan.
# Dugaan; diverifikasi saat fixture /bigdata asli direkam (Langkah 3).
INTERNAL_ID_COLUMNS = frozenset({"id"})

# NB-07 (KB-16): format angka Indonesia ketat.
_INT_PLAIN = re.compile(r"^-?\d+$")
_INT_GROUPED = re.compile(r"^-?\d{1,3}(?:\.\d{3})+$")
_DECIMAL = re.compile(r"^-?(?:\d{1,3}(?:\.\d{3})+|\d+),\d+$")

_WHITESPACE = re.compile(r"\s+")
_DASH = re.compile(r"\s*[-\u2013\u2014]\s*")  # -, en dash, em dash

Number = int | float


# ---------------------------------------------------------------- NB-02 / NB-06


def is_empty(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip().casefold() in EMPTY_TEXTS)


def clean_empty(value: Any) -> Any:
    """NB-02/NB-06: "", "-", "None", "null" (teks) dan None menjadi None; lainnya apa adanya."""
    return None if is_empty(value) else value


# ---------------------------------------------------------------- NB-01


def parse_portal_datetime(value: str | datetime | None) -> datetime | None:
    """Waktu portal → datetime ber-offset WIB (+07:00).

    - Offset +07:07 diganti WIB dengan replace(tzinfo=WIB); jam dinding tidak bergeser.
    - Waktu tanpa offset dianggap WIB.
    - Offset lain (mis. "Z") dikonversi sungguhan ke WIB.
    - Nilai kosong (NB-02) → None. Teks yang bukan waktu ISO 8601 → ValueError.
    """
    if is_empty(value):
        return None
    if isinstance(value, str):
        text = value.strip().replace(" ", "T", 1)
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ValueError("waktu portal tidak berformat ISO 8601") from exc
    elif isinstance(value, datetime):
        parsed = value
    else:
        raise TypeError(f"waktu portal harus str atau datetime, bukan {type(value).__name__}")

    offset = parsed.utcoffset()
    if offset is None or offset == NONSTANDARD_PORTAL_OFFSET:
        return parsed.replace(tzinfo=WIB)
    return parsed.astimezone(WIB)


# ---------------------------------------------------------------- NB-07


def _parse_number(value: Any) -> tuple[Number | None, str | None]:
    """(angka, alasan_gagal). alasan_gagal None bila sukses atau nilainya kosong."""
    if isinstance(value, bool):
        return None, "tipe_bukan_angka"
    if isinstance(value, int | float):
        return value, None  # NB-07: yang sudah bertipe angka tidak diurai ulang
    if is_empty(value):
        return None, None
    if not isinstance(value, str):
        return None, "tipe_bukan_angka"
    text = value.strip()
    if _INT_PLAIN.match(text):
        return int(text), None
    if _INT_GROUPED.match(text):
        return int(text.replace(".", "")), None
    if _DECIMAL.match(text):
        return float(text.replace(".", "").replace(",", ".")), None
    return None, "format_angka_tidak_dikenal"


def parse_id_number(value: Any) -> Number | None:
    """NB-07: teks angka format Indonesia → int/float; ambigu atau bukan angka → None.

    Titik hanya pemisah ribuan dalam grup tiga digit; koma hanya desimal.
    "9.160.885" → 9160885, "1.234,5" → 1234.5, "0,45" → 0.45. "5.89" dan "1.2.3"
    tidak cocok pola sehingga None (tidak ditebak). int/float dikembalikan apa adanya.
    """
    return _parse_number(value)[0]


# ---------------------------------------------------------------- NB-03 / NB-04 / NB-05


@dataclass(frozen=True, slots=True)
class ParseIssue:
    """Temuan urai NB-03. Sengaja tanpa nilai mentah agar log bebas isi baris data."""

    column: str
    row_index: int | None
    reason: str


def is_code_column(name: str) -> bool:
    """NB-05: kolom kode (kode_*, bps_kode_*, ...) dibiarkan apa adanya."""
    return "kode" in name.casefold().split("_")


def normalize_row(
    row: Mapping[str, Any],
    column_types: Mapping[str, str | None],
    *,
    row_index: int | None = None,
    drop_columns: Iterable[str] = INTERNAL_ID_COLUMNS,
) -> tuple[dict[str, Any], list[ParseIssue]]:
    """Normalisasi satu baris /bigdata.

    - NB-04: kolom di drop_columns dibuang.
    - NB-05: kolom kode dipertahankan apa adanya, termasuk tipe dan teksnya.
    - NB-02: teks kosong menjadi None di semua kolom lain.
    - NB-03 + NB-07: kolom bertipe "number" diurai; yang gagal → None + ParseIssue.
    Kolom lain (string, category, tanpa tipe) tidak diubah selain NB-02. Label tidak
    diseragamkan di sini (NB-08 menyimpan nilai asli; lihat normalize_label).
    """
    dropped = set(drop_columns)
    out: dict[str, Any] = {}
    issues: list[ParseIssue] = []
    for name, value in row.items():
        if name in dropped:
            continue
        if is_code_column(name):
            out[name] = value
            continue
        if column_types.get(name) == "number":
            number, reason = _parse_number(value)
            if reason is not None:
                issues.append(ParseIssue(column=name, row_index=row_index, reason=reason))
            out[name] = number
        else:
            out[name] = clean_empty(value)
    return out, issues


def normalize_rows(
    rows: Iterable[Mapping[str, Any]],
    column_types: Mapping[str, str | None],
    *,
    drop_columns: Iterable[str] = INTERNAL_ID_COLUMNS,
) -> tuple[list[dict[str, Any]], list[ParseIssue]]:
    dropped = frozenset(drop_columns)
    out: list[dict[str, Any]] = []
    issues: list[ParseIssue] = []
    for index, row in enumerate(rows):
        normalized, row_issues = normalize_row(
            row, column_types, row_index=index, drop_columns=dropped
        )
        out.append(normalized)
        issues.extend(row_issues)
    return out, issues


# ---------------------------------------------------------------- NB-08


def normalize_label(value: str) -> tuple[str, str]:
    """NB-08: (label_normal, label_asli).

    Spasi dan baris baru dirapatkan; tanda hubung (-), en dash (U+2013), em dash (U+2014) beserta
    spasi di sekitarnya diseragamkan menjadi " - "; ujung di-strip.
    """
    collapsed = _WHITESPACE.sub(" ", value)
    return _DASH.sub(" - ", collapsed).strip(), value


# ---------------------------------------------------------------- KB-46: teks bebas di respons

NUMBER_MASK = "[angka]"
_NUMBER_RUN = re.compile(r"\d+(?:[.,]\d+)*")
_YEAR_MIN, _YEAR_MAX = 1900, 2100


def mask_free_text(text: str | None) -> str | None:
    """KB-46: deret angka di teks bebas portal → "[angka]", kecuali tahun 4 digit 1900 s.d. 2100.

    Dipakai hanya untuk respons tool (change_notes, description, meaning); berkas data_ref
    dan cache menyimpan teks aslinya. Menutup celah KT-03 lewat metadata teks bebas.
    """
    if text is None:
        return None

    def replace(match: re.Match[str]) -> str:
        token = match.group(0)
        if len(token) == 4 and token.isdigit() and _YEAR_MIN <= int(token) <= _YEAR_MAX:
            return token
        return NUMBER_MASK

    return _NUMBER_RUN.sub(replace, text)


# ---------------------------------------------------------------- deskripsi HTML (Dok 2 bagian 2)

_BLOCK_TAGS = re.compile(r"<\s*(br|/p|/li|/ul|/ol|/h[1-6]|/div)\s*/?\s*>", re.IGNORECASE)
_LIST_ITEM = re.compile(r"<\s*li[^>]*>", re.IGNORECASE)
_ANY_TAG = re.compile(r"<[^>]+>")
_SPACES = re.compile(r"[ \t\r\f\v\u00a0]+")  # termasuk NBSP
_BLANK_LINES = re.compile(r"\n\s*\n+")


def html_to_text(value: str | None) -> str | None:
    """Deskripsi HTML portal → teks polos: tag dibuang, entitas diurai, spasi dirapatkan.

    Paragraf dan butir daftar menjadi baris baru (butir diawali "- "), agar template kamus
    kolom "- nama_kolom: menyatakan ..." tetap terbaca di Langkah 6. Kosong → None.
    """
    if is_empty(value):
        return None
    text = _LIST_ITEM.sub("\n- ", str(value))
    text = _BLOCK_TAGS.sub("\n", text)
    text = html.unescape(_ANY_TAG.sub("", text))
    lines = [_SPACES.sub(" ", line).strip() for line in text.splitlines()]
    text = _BLANK_LINES.sub("\n", "\n".join(line for line in lines if line)).strip()
    return text or None
