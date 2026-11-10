"""Penanda kesegaran (Dok 2 bagian 3, KS-01..05). Fungsi murni.

Dari setiap entri history_draft hanya `type`, `cdate`, dan `notes` yang dibaca.
Field akun petugas (username dan sejenisnya) tidak pernah disalin (Dok 1 privasi, UT-12).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

from mcp_bogor.services.normalize import clean_empty, parse_portal_datetime

APPROVE = "approve"
# KS-03: mdate dianggap berubah setelah approve bila selisihnya lebih dari ini.
METADATA_CHANGE_TOLERANCE = timedelta(days=1)


@dataclass(frozen=True, slots=True)
class Freshness:
    data_updated_at: datetime | None
    freshness_source: Literal["approve", "mdate"] | None
    metadata_changed_after_approve: bool
    change_notes: str | None


@dataclass(frozen=True, slots=True)
class _Entry:
    type: str
    cdate: datetime
    notes: str | None


def _read_entries(history_draft: Iterable[Mapping[str, Any]] | None) -> list[_Entry]:
    """Ambil type/cdate/notes saja; entri dengan cdate kosong atau rusak dilewati."""
    entries: list[_Entry] = []
    for raw in history_draft or ():
        try:
            cdate = parse_portal_datetime(raw.get("cdate"))
        except (TypeError, ValueError):
            continue
        if cdate is None:
            continue
        kind = clean_empty(raw.get("type"))
        notes = clean_empty(raw.get("notes"))
        entries.append(
            _Entry(
                type=str(kind).strip().casefold() if kind is not None else "",
                cdate=cdate,
                notes=notes.strip() if isinstance(notes, str) else None,
            )
        )
    return entries


def compute_freshness(
    history_draft: Iterable[Mapping[str, Any]] | None,
    mdate: str | datetime | None,
) -> Freshness:
    """KS-01..03.

    - KS-01: data_updated_at = cdate approve terbaru; sumber "approve".
    - KS-02: tanpa approve → mdate; sumber "mdate". Keduanya kosong → None/None.
    - KS-03: mdate > approve terbaru + 1 hari → tetap approve, flag True.
    - change_notes (Dok 2 bagian 2): notes tidak kosong dari entri terbaru yang punya notes.
    """
    entries = sorted(_read_entries(history_draft), key=lambda e: e.cdate)
    mdate_parsed = parse_portal_datetime(mdate)

    approves = [e for e in entries if e.type == APPROVE]
    if approves:
        updated_at: datetime | None = approves[-1].cdate
        source: Literal["approve", "mdate"] | None = "approve"
        changed = (
            mdate_parsed is not None
            and mdate_parsed - approves[-1].cdate > METADATA_CHANGE_TOLERANCE
        )
    else:
        updated_at = mdate_parsed
        source = "mdate" if mdate_parsed is not None else None
        changed = False

    notes = [e.notes for e in entries if e.notes]
    return Freshness(
        data_updated_at=updated_at,
        freshness_source=source,
        metadata_changed_after_approve=changed,
        change_notes=notes[-1] if notes else None,
    )


def is_stale(retrieved_at: datetime, now: datetime, ttl_h: float) -> bool:
    """KS-05: True bila umur cache ≥ ttl_h jam. Kedua waktu wajib ber-offset."""
    if ttl_h <= 0:
        raise ValueError("ttl_h harus > 0")
    if retrieved_at.utcoffset() is None or now.utcoffset() is None:
        raise ValueError("retrieved_at dan now wajib ber-offset zona waktu")
    return now - retrieved_at >= timedelta(hours=ttl_h)
