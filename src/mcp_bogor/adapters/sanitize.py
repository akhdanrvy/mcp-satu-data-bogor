"""Sanitasi respons portal: satu sumber untuk adaptor dan scripts/record_fixtures.py.

- Kunci akun petugas (Dok 1 privasi, Dok 2 bagian 2, KB-22/KB-23) selalu dibuang.
- Field kontak dan alamat OPD (KK-J) serta metadata "Kontak Produsen":
  * mode "redact" (fixture): kunci tetap ada, nilainya diganti REDACTED agar bentuk sama;
  * mode "drop" (keluaran adaptor): kunci dan entri metadata kontak dihilangkan.
- `contacts=False` hanya membuang kunci akun. Dipakai untuk baris /bigdata, karena kolom
  bernama `alamat`/`email` di dataset publik adalah isi data, bukan kontak OPD (KK-L).

Fungsi murni dan idempoten; input tidak diubah.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

ACCOUNT_KEYS = frozenset({"username", "user_name", "cuid", "muid"})
CONTACT_FIELDS = frozenset(
    {"organisasi_email", "organisasi_notelp", "organisasi_alamat", "email", "notelp", "alamat"}
)
CONTACT_LABEL = "kontak produsen"
VALUE_KEYS = ("value", "nilai", "isi", "content")
REDACTED = "<redacted>"
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?<!\d)(?:\+62|62|0)[\s.-]?\(?\d{2,4}\)?[\s.-]?\d{3,4}[\s.-]?\d{3,5}(?!\d)")

Mode = Literal["redact", "drop"]


@dataclass
class SanitizeStats:
    removed_keys: int = 0
    redacted_contacts: int = 0


def _is_contact_label(value: Any) -> bool:
    return isinstance(value, str) and value.strip().casefold() == CONTACT_LABEL


def _is_contact_entry(obj: dict[str, Any]) -> bool:
    return any(_is_contact_label(v) for v in obj.values())


def sanitize(
    obj: Any,
    stats: SanitizeStats | None = None,
    *,
    mode: Mode = "redact",
    contacts: bool = True,
) -> Any:
    """Salinan bersih dari `obj` (lihat docstring modul)."""
    stats = stats if stats is not None else SanitizeStats()
    if isinstance(obj, list):
        out_list = []
        for item in obj:
            if contacts and mode == "drop" and isinstance(item, dict) and _is_contact_entry(item):
                stats.redacted_contacts += 1
                continue
            out_list.append(sanitize(item, stats, mode=mode, contacts=contacts))
        return out_list
    if not isinstance(obj, dict):
        return obj
    contact_entry = contacts and _is_contact_entry(obj)
    has_value_key = any(k in obj for k in VALUE_KEYS)
    out: dict[str, Any] = {}
    for key, value in obj.items():
        if key in ACCOUNT_KEYS:
            stats.removed_keys += 1
            continue
        if not contacts:
            out[key] = sanitize(value, stats, mode=mode, contacts=False)
            continue
        is_contact_key = key in CONTACT_FIELDS or _is_contact_label(key)
        if mode == "drop" and is_contact_key:
            stats.redacted_contacts += value not in (None, REDACTED)
            continue
        if is_contact_key and value is not None and value != REDACTED:
            out[key] = REDACTED
            stats.redacted_contacts += 1
            continue
        if value == REDACTED:
            out[key] = value
            continue
        if (
            contact_entry
            and not _is_contact_label(value)
            and (key in VALUE_KEYS or not has_value_key)
        ):
            out[key] = REDACTED
            stats.redacted_contacts += 1
            continue
        out[key] = sanitize(value, stats, mode=mode, contacts=contacts)
    return out


@dataclass(frozen=True)
class PiiFinding:
    file: str
    json_path: str
    kind: str


def scan_pii(obj: Any, file: str, path: str = "$") -> list[PiiFinding]:
    """Pola email/telepon di nilai teks. Hanya lokasi yang dilaporkan, bukan nilainya.

    Field gambar (`image`, `*_image`) dilewati: nama berkas hash sering mirip nomor telepon.
    """
    found: list[PiiFinding] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key == "image" or str(key).endswith("_image"):
                continue
            found.extend(scan_pii(value, file, f"{path}.{key}"))
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            found.extend(scan_pii(value, file, f"{path}[{index}]"))
    elif isinstance(obj, str) and obj != REDACTED:
        if EMAIL_RE.search(obj):
            found.append(PiiFinding(file, path, "email"))
        if PHONE_RE.search(obj):
            found.append(PiiFinding(file, path, "telepon"))
    return found


def find_private_keys(obj: Any, path: str = "$") -> list[str]:
    """Lokasi kunci akun atau kontak/alamat yang masih ada (untuk tes privasi)."""
    found: list[str] = []
    if isinstance(obj, dict):
        if _is_contact_entry(obj):
            found.append(f"{path} (metadata kontak)")
        for key, value in obj.items():
            if key in ACCOUNT_KEYS or key in CONTACT_FIELDS or _is_contact_label(key):
                found.append(f"{path}.{key}")
            found.extend(find_private_keys(value, f"{path}.{key}"))
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            found.extend(find_private_keys(value, f"{path}[{index}]"))
    return found
