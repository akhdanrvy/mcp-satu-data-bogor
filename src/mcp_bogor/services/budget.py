"""Anggaran token respons tool (FR-15, FR-16, KB-38). Fungsi murni, tanpa tokenizer.

Perkiraan konservatif: token ≈ ceil(byte UTF-8 JSON ringkas / BYTES_PER_TOKEN). Teks
Indonesia dan JSON umumnya 3,5 s.d. 4,5 byte per token pada tokenizer BPE; pembagi 3 sengaja
melebih-lebihkan agar respons tidak melewati num_ctx model. Ganti dengan tokenizer asli
setelah model dan num_ctx ditentukan (Dok 4 bagian 9).
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Iterable
from enum import StrEnum
from typing import Any, TypeVar

BYTES_PER_TOKEN = 3

T = TypeVar("T")


class TruncatedReason(StrEnum):
    """Kode tetap untuk truncated_reason (KB-38); tanpa angka."""

    ITEMS = "items_dipangkas"
    TOP_VALUES = "top_values_dipangkas"
    DESCRIPTION = "deskripsi_dipangkas"
    MEANING = "arti_kolom_dipangkas"
    PREVIEW = "preview_dipangkas"
    NOTICES = "notices_dipangkas"


def compact_json_bytes(payload: Any) -> int:
    return len(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")
    )


def estimate_tokens(payload: Any) -> int:
    return math.ceil(compact_json_bytes(payload) / BYTES_PER_TOKEN)


def fit_to_budget(
    candidates: Iterable[tuple[TruncatedReason | None, T]],
    limit: int,
    measure: Callable[[T], Any],
) -> tuple[TruncatedReason | None, T] | None:
    """Kandidat pertama yang muat dalam `limit` token, atau None bila tidak ada.

    `candidates` diurutkan dari utuh (alasan None) ke paling dipangkas; dibangkitkan malas
    agar kandidat berikutnya hanya dibuat bila perlu. `measure` mengubah kandidat menjadi
    bentuk JSON yang akan dikirim (mis. amplop yang sudah di-dump).
    """
    for reason, candidate in candidates:
        if estimate_tokens(measure(candidate)) <= limit:
            return reason, candidate
    return None


def truncate_text(text: str | None, max_chars: int) -> str | None:
    """Potong di batas kata dan tambahkan "…"; teks pendek dikembalikan apa adanya."""
    if text is None or len(text) <= max_chars:
        return text
    cut = text[: max_chars - 1]
    space = cut.rfind(" ")
    if space >= max_chars // 2:
        cut = cut[:space]
    return cut.rstrip(" ,.;:") + "…"
