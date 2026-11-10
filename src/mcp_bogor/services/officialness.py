"""Status keresmian dataset (FR-07, Dok 2 bagian 2, UT-05). Fungsi murni."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from mcp_bogor.models import Officialness
from mcp_bogor.services.normalize import clean_empty

OFFICIAL_BASIS = "validate=approve; klasifikasi=public"


def _norm(value: Any) -> str | None:
    value = clean_empty(value)
    return None if value is None else str(value).strip().casefold()


def compute_officialness(validate: Any, klasifikasi: Any) -> Officialness:
    """Resmi hanya bila validate=approve DAN klasifikasi=public; selain itu draf.

    Basis draf menyebut setiap syarat yang tidak terpenuhi.
    """
    v, k = _norm(validate), _norm(klasifikasi)
    if v == "approve" and k == "public":
        return Officialness(status="resmi", basis=OFFICIAL_BASIS)
    reasons = []
    if v != "approve":
        reasons.append(f"validate={v or 'kosong'}, bukan approve")
    if k != "public":
        reasons.append(f"klasifikasi={k or 'kosong'}, bukan public")
    return Officialness(status="draf", basis="; ".join(reasons))


def officialness_from_record(record: Mapping[str, Any]) -> Officialness:
    """Baca field `validate` dan `klasifikasi` dari record dataset portal."""
    return compute_officialness(record.get("validate"), record.get("klasifikasi"))
