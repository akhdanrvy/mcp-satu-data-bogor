"""Adaptor httpx ke API portal: jeda, backoff, paginasi (AK-01..06). Langkah 4.

Sudah ada (Langkah 3, KB-20): fungsi murni yang meratakan bentuk JSON portal sebelum
diteruskan ke services/. Bentuk asli teramati pada rekaman 2026-11-10:

- `/bigdata` → `metadata_filter`: list `{key, type, value}`; `value` = daftar nilai unik
  (teks, mis. tahun "2015"), kosong untuk kolom `type="free text"`.
- `/datasets/{slug}` → `metadata`: list `{id, dataset_id, type, key, value, cdate, mdate, muid}`;
  key yang dipakai antara lain "Satuan Dataset", "Frekuensi Dataset", "Dimensi Dataset Awal",
  "Dimensi Dataset Akhir", "Tingkat Penyajian Dataset", "Cakupan Dataset".
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


def flatten_metadata_filter(raw: Any) -> dict[str, list[Any]]:
    """`metadata_filter` portal → {nama_kolom: nilai_unik} untuk services/periods.

    Menerima list `{key, value}` (bentuk asli) atau dict yang sudah rata. Entri tanpa
    `key` teks dilewati; `value` yang bukan list dianggap kosong.
    """
    if isinstance(raw, Mapping):
        return {str(k): list(v) if isinstance(v, list) else [] for k, v in raw.items()}
    flat: dict[str, list[Any]] = {}
    for entry in raw if isinstance(raw, Iterable) and not isinstance(raw, str | bytes) else ():
        if isinstance(entry, Mapping) and isinstance(entry.get("key"), str):
            value = entry.get("value")
            flat.setdefault(entry["key"], list(value) if isinstance(value, list) else [])
    return flat


def flatten_metadata(raw: Any) -> dict[str, Any]:
    """`metadata[]` record dataset → {key: value}. Key ganda: kemunculan pertama dipakai."""
    flat: dict[str, Any] = {}
    for entry in raw if isinstance(raw, list) else ():
        if isinstance(entry, Mapping) and isinstance(entry.get("key"), str):
            flat.setdefault(entry["key"], entry.get("value"))
    return flat
