"""Contoh objek model yang sah, dipakai bersama tes unit dan kontrak (tanpa jaringan)."""

from __future__ import annotations

from datetime import datetime

from mcp_bogor.config import WIB
from mcp_bogor.models import (
    ColumnInfo,
    DataRef,
    DataResult,
    InfoResult,
    Officialness,
    Provenance,
    QualityFlag,
    SearchItem,
    SearchResult,
)

T_RETRIEVED = datetime(2026, 11, 6, 10, 15, 2, tzinfo=WIB)
T_APPROVE = datetime(2025, 6, 2, 10, 14, tzinfo=WIB)


def provenance(served_from: str = "live") -> Provenance:
    return Provenance(
        source_url="https://opendata.kotabogor.go.id/dataset/contoh-kemiskinan",
        opd="Bappeda",
        data_updated_at=T_APPROVE,
        freshness_source="approve",
        retrieved_at=T_RETRIEVED,
        period_start=2025,
        period_end=2025,
        served_from=served_from,
    )


def officialness() -> Officialness:
    return Officialness(status="resmi", basis="validate=approve; klasifikasi=public")


def quality_flag() -> QualityFlag:
    return QualityFlag(
        code="suspect_zero_period",
        rule="QC-01",
        column="kecepatan",
        period=2024,
        detail="Seluruh nilai periode ini nol",
    )


def columns() -> list[ColumnInfo]:
    return [
        ColumnInfo(
            name="tahun",
            role="dimension",
            type="integer",
            meaning="Tahun data",
            meaning_source="template",
            top_values=[2024, 2025],
        ),
        ColumnInfo(name="kecamatan", role="dimension", type="string", top_values=["Bogor Barat"]),
        ColumnInfo(
            name="persentase_miskin",
            role="measure",
            type="number",
            meaning="Persentase penduduk miskin",
            unit="%",
            meaning_source="template",
        ),
        ColumnInfo(name="bps_kode_kabupaten_kota", role="id", type="string"),
    ]


def search_result() -> SearchResult:
    return SearchResult(
        total=3,
        page=1,
        page_size=10,
        catalog_synced_at=datetime(2026, 11, 6, 2, 0, tzinfo=WIB),
        items=[
            SearchItem(
                slug="contoh-pad",
                name="Realisasi Pendapatan Asli Daerah",
                opd="Bapenda",
                topik="Keuangan Daerah",
                kategori="indikator",
                satuan="Rupiah",
                frekuensi="Tahunan",
                tahun_awal=2020,
                tahun_akhir=2024,
                data_updated_at=T_APPROVE,
                officialness=Officialness(status="resmi"),
            )
        ],
    )


def info_result() -> InfoResult:
    return InfoResult(
        slug="contoh-kemiskinan",
        name="Persentase Penduduk Miskin",
        opd="Bappeda",
        topik="Sosial",
        kategori="indikator",
        description="Deskripsi polos.",
        satuan="%",
        frekuensi="Tahunan",
        tingkat_penyajian="Kota",
        cakupan="Kota Bogor",
        periods_available=[2020, 2021, 2022, 2023, 2024, 2025],
        columns=columns(),
        row_count=6,
        dictionary_validated=True,
    )


def data_result() -> DataResult:
    return DataResult(
        data_ref=DataRef(
            url="http://mcp-bogor:8799/d/abc123?exp=1793942402&sig=xyz",
            expires_at=datetime(2026, 11, 6, 10, 30, 2, tzinfo=WIB),
            row_count=1,
        ),
        columns=columns(),
        periods_requested=[2025, 2026],
        periods_found=[2025],
        missing_periods=[2026],
        preview=[{"tahun": 2025, "kecamatan": None}],
        filters_applied={"tahun": [2025, 2026]},
        dictionary_validated=True,
    )
