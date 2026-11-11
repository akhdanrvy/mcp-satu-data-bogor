"""Stemming bahasa Indonesia untuk indeks kata katalog (KB-29 butir 2).

Satu sumber tokenisasi dan stem untuk penulisan indeks (kolom name_stem saja)
dan untuk kueri, sehingga keduanya selalu cocok.

Stemmer: PySastrawi 1.2.1 (MIT; versi dikunci karena paket tidak lagi dikembangkan; KB-29).
Hasil di-cache per kata. Setelah stemmer atau aturan stem berubah, jalankan
`sync_worker --reindex` agar kolom name_stem dibangun ulang.

Aturan indeks (KB-29, `stem_for_index`):
- name_stem hanya berisi stem kata nama yang BERUBAH oleh stemmer, agar dataset yang memuat
  kata kueri secara harfiah tidak terhitung dua kali (nama + name_stem).
- Kata dengan awalan pe-/per- di depan kata dasarnya, termasuk awalan ganda
  (pendapatan, penduduk, kependudukan, pengaduan, penanganan, penerimaan, perhubungan),
  tidak di-stem: nominalisasi ini mengubah makna kata dasarnya, sehingga kueri "dapat"
  atau "duduk" tidak boleh menemukannya lewat stem. Kata ke-an/-an/me-/di-
  (kemiskinan, kesehatan, kecepatan, kunjungan, mendapat, diduduki) tetap di-stem.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from functools import lru_cache

from Sastrawi.Stemmer.StemmerFactory import StemmerFactory

TOKEN_RE = re.compile(r"[^\W_]+")
STEMMER_NAME = "PySastrawi 1.2.1"

StemFn = Callable[[str], str]


def tokenize(text: str | None) -> list[str]:
    """Token kata unicode huruf kecil, tanpa tanda baca dan garis bawah."""
    return TOKEN_RE.findall(text.casefold()) if text else []


@lru_cache(maxsize=1)
def _stemmer() -> object:
    return StemmerFactory().create_stemmer()


@lru_cache(maxsize=50_000)
def stem_token(token: str) -> str:
    """Stem satu token huruf kecil. Hasil di-cache (kosakata katalog ± 1.500 kata)."""
    if not token:
        return token
    return _stemmer().stem(token) or token  # type: ignore[attr-defined]


MEANING_SHIFT_PREFIX = "pe"  # pe-, pem-, pen-, peng-, peny-, pel-, per-


def _prefix_part(word: str, stem: str) -> str:
    """Bagian kata di depan kata dasar. Bila kata dasar luluh (penanganan ← tangan), bagian
    depan diperkirakan dari selisih panjang kata dan kata dasar."""
    index = word.find(stem)
    return word[:index] if index >= 0 else word[: max(len(word) - len(stem), 0)]


def stem_for_index(word: str) -> str | None:
    """Stem yang ditambahkan ke indeks untuk satu kata nama, atau None (lihat docstring modul)."""
    stem = stem_token(word)
    if stem == word:
        return None
    if MEANING_SHIFT_PREFIX in _prefix_part(word, stem):
        return None
    return stem


def stem_text(text: str | None) -> str:
    """Nama dataset → stem tambahan untuk kolom name_stem (hanya kata yang berubah)."""
    return " ".join(stem for word in tokenize(text) if (stem := stem_for_index(word)))
