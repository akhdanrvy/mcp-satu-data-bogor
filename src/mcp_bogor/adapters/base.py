"""Kontrak SourceAdapter (Dok 3 bagian 5, ADR-002) dan tipe internalnya.

Tipe di sini adalah tipe internal antar-lapisan, bukan kontrak tool (Dok 4). Semua data
yang keluar dari adaptor sudah disanitasi (tanpa kunci akun, kontak, atau alamat OPD) dan
metadata sudah diratakan (KB-20), tetapi nilainya belum dinormalkan (tugas services/).

Penyimpangan dari Dok 3 (KK-K): semua metode async; list_datasets mengembalikan
CatalogSnapshot (bukan Iterator) agar sinkron katalog tahu apakah hasilnya lengkap
sebelum menghapus record (TODO Langkah 5); query_rows menerima satu `sort`; ditambah
list_organisasi dan list_topik (KK-H).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

# ---------------------------------------------------------------- galat


class SourceError(Exception):
    """Galat sumber data. Pemetaan ke ErrorCode dikerjakan di Langkah 8."""

    def __init__(self, message: str, *, path: str | None = None, attempts: int = 0) -> None:
        super().__init__(message)
        self.path = path
        self.attempts = attempts


class SourceNotFound(SourceError):
    """404 dari portal (mis. slug tidak ada)."""


class SourceUnavailable(SourceError):
    """Koneksi gagal, timeout, atau 5xx setelah retry habis."""


class SourceRateLimited(SourceError):
    """429 setelah backoff habis atau Retry-After melebihi batas."""


class SourceProtocolError(SourceError):
    """Bentuk respons tak terduga, atau 4xx selain 404."""


class InvalidSourceRequest(SourceError, ValueError):
    """Permintaan ditolak sebelum dikirim: segmen path tidak sah, endpoint di luar allowlist."""


# ---------------------------------------------------------------- tipe internal


@dataclass(frozen=True)
class Pagination:
    page: int | None = None
    per_page: int | None = None
    total_page: int | None = None
    total_data: int | None = None
    has_next: bool | None = None


@dataclass(frozen=True)
class RawDataset:
    """Record dataset portal yang sudah disanitasi; `metadata` sudah rata {key: value}."""

    slug: str
    record: Mapping[str, Any]
    metadata: Mapping[str, Any]

    @property
    def schema(self) -> str | None:
        value = self.record.get("schema")
        return value if isinstance(value, str) and value else None

    @property
    def table(self) -> str | None:
        value = self.record.get("table")
        return value if isinstance(value, str) and value else None


@dataclass(frozen=True)
class ColumnProbe:
    """Nama kolom dan nilai unik per kolom (metadata_filter rata, KB-20)."""

    columns: list[str]
    metadata_filter: dict[str, list[Any]]
    total_rows: int | None = None


@dataclass(frozen=True)
class RowPage:
    rows: list[dict[str, Any]]
    columns: list[str]
    pagination: Pagination = field(default_factory=Pagination)


@dataclass(frozen=True)
class SourceHealth:
    ok: bool
    status: int | None = None
    detail: str | None = None
    latency_s: float | None = None


@dataclass(frozen=True)
class CatalogSnapshot:
    """Hasil sinkron katalog penuh (KK-K).

    `complete` True hanya bila semua halaman terbaca dan jumlah slug unik sama dengan
    `total_data`. Sinkron katalog hanya boleh menghapus record lokal bila complete.
    `skipped` = record yang gagal di-parse (mis. tanpa slug); dicatat di log tanpa isi
    record dan disertakan di laporan sinkron (Langkah 5).
    """

    records: list[RawDataset]
    total_data: int | None
    pages_read: int
    total_page: int | None
    complete: bool
    duplicates: int = 0
    skipped: int = 0
    error: str | None = None


# ---------------------------------------------------------------- protokol


@runtime_checkable
class SourceAdapter(Protocol):
    source_id: str

    async def list_datasets(self) -> CatalogSnapshot:
        """Seluruh katalog, sudah dipaginasi dan dideduplikasi oleh adaptor."""
        ...

    async def get_dataset(self, slug: str) -> RawDataset:
        """Satu dataset; SourceNotFound bila slug tidak ada."""
        ...

    async def get_columns(self, dataset: RawDataset) -> ColumnProbe:
        """Nama kolom dan nilai unik per kolom (metadata_filter)."""
        ...

    async def query_rows(
        self,
        dataset: RawDataset,
        where: Mapping[str, Sequence[Any]] | None = None,
        sort: str | None = None,
        page: int = 1,
        per_page: int = 100,
    ) -> RowPage:
        """Baris mentah dengan filter dan urutan dijalankan di sumber."""
        ...

    async def probe(self, dataset: RawDataset, per_page: int = 100) -> tuple[ColumnProbe, RowPage]:
        """KK-O: satu GET halaman 1 → nama kolom + metadata_filter + sampel baris."""
        ...

    async def health(self) -> SourceHealth:
        """Untuk membedakan sumber mati dari dataset kosong."""
        ...

    async def list_organisasi(self) -> list[dict[str, Any]]:
        """Referensi OPD (KK-H), tanpa kontak/alamat (KK-J)."""
        ...

    async def list_topik(self) -> list[dict[str, Any]]:
        """Referensi topik (KK-H)."""
        ...
