"""Notice: catatan untuk model yang bukan galat (KK-R). Templat tetap, tanpa teks bebas.

Sama seperti pesan galat (KB-10), setiap kode punya templat tetap. Satu-satunya placeholder
adalah `column` (nama kolom) dan, untuk DIFFERENT_DEFINITION, `datasets` (tepat dua slug;
KK-T). Pesan ditolak bila memuat angka berformat Indonesia.
"""

from __future__ import annotations

from enum import StrEnum
from types import MappingProxyType

from pydantic import Field, field_validator, model_validator

from mcp_bogor.models.common import ContractModel, PlaceholderStr, contains_formatted_number


class NoticeCode(StrEnum):
    DICTIONARY_NOT_VALIDATED = "DICTIONARY_NOT_VALIDATED"
    NO_MEASURE_COLUMN = "NO_MEASURE_COLUMN"
    REFRESH_THROTTLED = "REFRESH_THROTTLED"
    NO_TIME_DIMENSION = "NO_TIME_DIMENSION"
    EMPTY_RESULT = "EMPTY_RESULT"
    CATALOG_STALE = "CATALOG_STALE"
    UNIT_CONFLICT = "UNIT_CONFLICT"
    DIFFERENT_DEFINITION = "DIFFERENT_DEFINITION"


NOTICE_TEMPLATES: MappingProxyType[NoticeCode, str] = MappingProxyType(
    {
        NoticeCode.DICTIONARY_NOT_VALIDATED: (
            "Kolom nilai '{column}' tidak dijelaskan di deskripsi portal; artinya belum "
            "terverifikasi. Jangan membuat grafik dari kolom ini tanpa konfirmasi pengguna, "
            "dan jangan menebak artinya."
        ),
        NoticeCode.NO_MEASURE_COLUMN: (
            "Dataset ini tidak punya kolom nilai yang dikenali; jangan membuat grafik tanpa "
            "konfirmasi pengguna."
        ),
        NoticeCode.REFRESH_THROTTLED: (
            "force_refresh diabaikan karena data baru saja diambil; data dari cache yang "
            "masih segar."
        ),
        NoticeCode.NO_TIME_DIMENSION: (
            "Dataset ini tidak punya kolom tahun; parameter periods tidak dipakai."
        ),
        NoticeCode.EMPTY_RESULT: "Filter valid, tetapi tidak ada baris yang cocok.",
        NoticeCode.CATALOG_STALE: (
            "Katalog lokal sudah lama tidak disinkron; dataset terbaru mungkin belum tercantum."
        ),
        NoticeCode.UNIT_CONFLICT: (
            "Kolom satuan berisi lebih dari satu nilai; satuan kolom '{column}' tidak ditetapkan."
        ),
        # KK-T: R-10, dari dataset_berbeda_definisi kartu metrik (KB-49).
        NoticeCode.DIFFERENT_DEFINITION: (
            "Dataset '{dataset}' dan '{other_dataset}' adalah indikator dengan definisi "
            "berbeda walau namanya mirip. Jangan menyambung tren atau membandingkan nilainya "
            "seolah-olah satu seri; pakai satu dataset yang sesuai pertanyaan."
        ),
    }
)


def _needs_column(code: NoticeCode) -> bool:
    return "{column}" in NOTICE_TEMPLATES[code]


def _needs_datasets(code: NoticeCode) -> bool:
    return "{dataset}" in NOTICE_TEMPLATES[code]


class Notice(ContractModel):
    code: NoticeCode
    message: str = Field(min_length=1)
    column: PlaceholderStr | None = None
    # KK-T: dua slug untuk DIFFERENT_DEFINITION; tidak ada di notice lain.
    datasets: list[PlaceholderStr] | None = Field(default=None, min_length=2, max_length=2)

    @field_validator("message")
    @classmethod
    def _no_formatted_number(cls, value: str) -> str:
        if contains_formatted_number(value):
            raise ValueError("notice tidak boleh memuat angka berformat (KT-03)")
        return value

    @model_validator(mode="after")
    def _column_matches_template(self) -> Notice:
        if _needs_column(self.code) != (self.column is not None):
            raise ValueError(f"notice {self.code}: column wajib tepat bila templat memakainya")
        if _needs_datasets(self.code) != (self.datasets is not None):
            raise ValueError(f"notice {self.code}: datasets wajib tepat bila templat memakainya")
        if self.datasets is not None and self.datasets[0] == self.datasets[1]:
            raise ValueError("datasets harus dua slug berbeda")
        return self

    @classmethod
    def from_template(
        cls,
        code: NoticeCode,
        column: str | None = None,
        datasets: tuple[str, str] | None = None,
    ) -> Notice:
        if _needs_column(code) != (column is not None):
            raise ValueError(f"notice {code}: column wajib tepat bila templat memakainya")
        if _needs_datasets(code) != (datasets is not None):
            raise ValueError(f"notice {code}: datasets wajib tepat bila templat memakainya")
        values: dict[str, str] = {}
        if column is not None:
            values["column"] = column
        if datasets is not None:
            values["dataset"], values["other_dataset"] = datasets
        message = NOTICE_TEMPLATES[code].format(**values)
        return cls(
            code=code,
            message=message,
            column=column,
            datasets=list(datasets) if datasets is not None else None,
        )
