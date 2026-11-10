"""KK-I: peran kolom dan pengecualian KB-15 (implementasi penuh di Langkah 6).

Tes bertanda xfail(strict=True) adalah target Langkah 6: saat aturannya diterapkan,
tes ini berubah lulus, pytest melaporkan XPASS sebagai gagal, dan penandanya harus
dicabut. Contoh kolom diambil dari rekaman asli 2026-11-10.
"""

from __future__ import annotations

import pytest

from mcp_bogor.models import ColumnInfo, is_numeric_label_column

LANGKAH_6 = pytest.mark.xfail(strict=True, reason="KK-I, diimplementasikan di Langkah 6")

# (nama kolom, tipe menurut deskripsi setelah KK-02, contoh nilai asli di /bigdata)
ALWAYS_DIMENSION = [
    ("kode_provinsi", "number", 32),  # deskripsi: numerik
    ("bps_kode_kabupaten_kota", "number", 3271),  # deskripsi: numerik; bukan berawalan kode_
    ("no_segmen", None, 1),  # tidak terdokumentasi; nilai int (kecepatan Pajajaran)
    ("tahun", "number", 2025),  # deskripsi: numerik
    ("satuan", "string", "KM/JAM"),  # deskripsi: teks
    ("satuan", None, "PERSEN"),  # tidak terdokumentasi di satu dataset
]


# ---------------------------------------------------------------- KB-15 (models)


def test_kb15_current_exceptions_still_hold() -> None:
    assert is_numeric_label_column("tahun")
    assert is_numeric_label_column("kode_provinsi")
    assert not is_numeric_label_column("perkembangan_kunjungan")


@LANGKAH_6
@pytest.mark.parametrize("name", ["bps_kode_kabupaten_kota", "no_segmen", "satuan"])
def test_kb15_exception_extended(name: str) -> None:
    assert is_numeric_label_column(name)


@LANGKAH_6
def test_kb15_formatted_label_allowed_in_bps_code_column() -> None:
    col = ColumnInfo(name="bps_kode_kabupaten_kota", role="dimension", top_values=["3.271"])
    assert col.top_values == ["3.271"]


@pytest.mark.parametrize("name", ["kodepos", "nomor", "nilai_no", "tahunan"])
def test_kb15_extension_must_not_overreach(name: str) -> None:
    """Pola KK-I bukan substring bebas: hanya token `kode`, awalan `no_`, dan nama persis."""
    assert not is_numeric_label_column(name)


# ---------------------------------------------------------------- KK-04 (services/dictionary)


@LANGKAH_6
@pytest.mark.parametrize(("name", "data_type", "example"), ALWAYS_DIMENSION)
def test_kk_i_role_is_dimension_regardless_of_type(
    name: str, data_type: str | None, example: object
) -> None:
    from mcp_bogor.services.dictionary import column_role  # belum ada sampai Langkah 6

    assert column_role(name, data_type) == "dimension", example


@LANGKAH_6
@pytest.mark.parametrize(
    ("name", "data_type"),
    [("perkembangan_kunjungan", "number"), ("kecepatan", "number"), ("persentase", "number")],
)
def test_kk_i_number_columns_stay_measure(name: str, data_type: str) -> None:
    from mcp_bogor.services.dictionary import column_role

    assert column_role(name, data_type) == "measure"
