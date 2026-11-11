"""Tanda tangan data_ref (Dok 4 bagian 6, ADR-008). Dipakai tool dan file server.

sig = base64url tanpa padding dari HMAC-SHA256(MCP_DATAREF_SECRET, "{cache_id}.{exp}").
Rotasi (KB-33): MCP_DATAREF_SECRET_PREVIOUS hanya diterima saat verifikasi, tidak pernah
dipakai menandatangani. Kosongkan setelah satu MCP_DATAREF_TTL_SECONDS sejak rotasi.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
from collections.abc import Sequence
from datetime import datetime, timedelta

from mcp_bogor.config import WIB, Settings
from mcp_bogor.models.results import DataRef
from mcp_bogor.storage.cache import is_valid_cache_id

_EXP_RE = re.compile(r"^[0-9]{1,12}$")
_SIG_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")  # 32 byte HMAC, base64url tanpa padding


def sign(key: bytes, cache_id: str, exp: int | str) -> str:
    message = f"{cache_id}.{exp}".encode("ascii")
    digest = hmac.new(key, message, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def verify(keys: Sequence[bytes], cache_id: str, exp: str, sig: str) -> bool:
    """True bila sig cocok dengan salah satu kunci. Bentuk input dicek dulu, lalu compare_digest."""
    if not (is_valid_cache_id(cache_id) and _EXP_RE.match(exp) and _SIG_RE.match(sig)):
        return False
    given = sig.encode("ascii")
    matched = False
    for key in keys:  # semua kunci dicek, agar waktu tidak membedakan kunci mana yang cocok
        matched |= hmac.compare_digest(sign(key, cache_id, exp).encode("ascii"), given)
    return matched


class DataRefSigner:
    """Membuat DataRef bertanda tangan. Gagal (ConfigError) bila secret tidak memenuhi KB-06."""

    def __init__(self, settings: Settings) -> None:
        self._key = settings.require_dataref_secret()
        self._base_url = settings.fileserver_public_url
        self._ttl = timedelta(seconds=settings.dataref_ttl_seconds)

    def make_data_ref(self, cache_id: str, row_count: int, now: datetime) -> DataRef:
        if not is_valid_cache_id(cache_id):
            raise ValueError("cache_id tidak sah")
        exp = int((now + self._ttl).timestamp())
        sig = sign(self._key, cache_id, exp)
        return DataRef(
            url=f"{self._base_url}/d/{cache_id}?exp={exp}&sig={sig}",
            expires_at=datetime.fromtimestamp(exp, WIB),
            row_count=row_count,
        )


def verification_keys(settings: Settings) -> list[bytes]:
    """Kunci yang diterima file server: secret aktif, lalu secret lama bila terisi (KB-33)."""
    keys = [settings.require_dataref_secret()]
    previous = settings.dataref_previous_secret()
    if previous is not None and previous != keys[0]:
        keys.append(previous)
    return keys
