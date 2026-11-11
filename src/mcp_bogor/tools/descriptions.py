"""Deskripsi ketiga tool yang dibaca LLM (RENCANA_LANGKAH8 bagian 7, disetujui Akhdan 11 Nov 2026).

Teks dikunci tes snapshot (tests/contract/snapshots/tool_descriptions.json). Setiap perubahan
harus disetujui Akhdan dan diuji ulang dengan golden set (Dok 4 bagian 9).

Rujukan antar-tool memakai nama berawalan MCP_TOOL_PREFIX; placeholder {search}, {info},
{data} diisi saat build_server.
"""

from __future__ import annotations

from types import MappingProxyType

BASE_NAMES = ("search_datasets", "get_dataset_info", "get_dataset_data")

_SEARCH = """\
Cari dataset statistik resmi Kota Bogor di portal Satu Data (opendata.kotabogor.go.id). \
Angka statistik sektoral resmi Kota Bogor diambil lewat tool ini, bukan dari ingatanmu. \
Panggil tool ini lebih dulu setiap kali pertanyaan membutuhkan angka statistik Kota Bogor.

Isi `query` dengan kata kunci topik dalam bahasa Indonesia (mis. "kemiskinan", "PAD", \
"kunjungan wisatawan"), bukan kalimat pertanyaan. `opd`, `topik`, dan `tahun` opsional untuk \
mempersempit.

Hasilnya daftar dataset (slug, nama, OPD, satuan, rentang tahun, waktu pembaruan, status \
resmi/draf), tanpa angka data. Pilih satu slug lalu panggil {info}.

Galat NO_MATCH berarti kata kunci itu tidak cocok dengan dataset mana pun; coba sekali lagi \
dengan kata yang lebih umum atau sinonim; bila tetap kosong, katakan data tidak tersedia di \
portal dan jangan memakai dataset lain yang mengukur hal berbeda. Dataset dengan nama mirip \
belum tentu indikator yang sama."""

_INFO = """\
Ambil penjelasan satu dataset berdasarkan slug dari {search}: arti dataset, kolom, satuan, \
OPD, status resmi, dan tahun yang tersedia. Tool ini tidak mengembalikan angka data.

Baca `columns`: `role=measure` adalah kolom nilai, `role=dimension` adalah kolom \
pengelompokan (tahun, wilayah, kategori) yang boleh dipakai sebagai filter. Bila `meaning` \
bernilai null, artinya tidak diketahui; jangan menebak.

`periods_available` adalah tahun yang benar-benar ada. Jangan meminta atau menyebut tahun lain \
seolah-olah ada; bila tahun yang ditanya tidak tercantum, katakan belum tersedia dan sebut tahun \
terakhir yang ada.

Bila `dictionary_validated=false` atau ada notice DICTIONARY_NOT_VALIDATED, kolom nilai belum \
terverifikasi: sampaikan keterbatasan itu dan jangan membuat grafik tanpa konfirmasi pengguna.

`tingkat_penyajian` menunjukkan level wilayah. Dataset tingkat kota tidak bisa dipecah per \
kecamatan atau kelurahan.

Status `partial` dengan galat STALE_DATA berarti portal sedang tidak bisa dihubungi dan \
penjelasan berasal dari simpanan lama; sebutkan tanggal `retrieved_at`."""

_DATA = """\
Siapkan data satu dataset untuk dihitung dan digambar oleh sistem. Respons tool ini TIDAK \
berisi angka: backend mengambil baris lengkap lewat `data_ref` lalu menghitung dan menggambar \
sendiri. Jangan menulis, memperkirakan, atau mengarang angka apa pun dari respons ini. \
`preview` hanya berisi kolom dimensi untuk memastikan filter benar.

Isi `periods` dengan tahun yang ditanyakan pengguna; kosongkan untuk semua tahun. `filters` \
hanya untuk kolom dengan `role=dimension` dari {info} (mis. \
{{"hari_operasional": ["HARI KERJA"]}}). Jangan menulis kolom nilai sebagai filter.

Cara membaca hasil:
- `missing_periods` tidak kosong: tahun itu belum tersedia di portal. Katakan demikian; status \
`partial` berarti sebagian tahun ada. Jangan menambal tahun kosong dengan dataset lain, \
perkiraan, atau tren.
- Galat PERIOD_NOT_AVAILABLE: tidak satu pun tahun yang diminta tersedia; sebut tahun terakhir \
yang ada, tanpa angka.
- `quality_flags` tidak kosong: sebutkan catatan mutunya per tahun (mis. nilai nol mencurigakan, \
kemungkinan salah skala, atau pencilan). Data tidak diubah; sertakan catatan mutu itu di \
narasi; periode yang bertanda tidak digambar sebagai nilai biasa, melainkan sebagai celah \
dengan catatan.
- Status `partial` dengan galat STALE_DATA: data dari simpanan lama karena portal tidak bisa \
dihubungi; sebutkan "data per <retrieved_at>".
- Galat LEVEL_NOT_AVAILABLE: dataset tidak menyajikan wilayah itu; jangan membaginya sendiri.
- Notice DICTIONARY_NOT_VALIDATED atau `dictionary_validated=false`: arti kolom nilai belum \
terverifikasi; sampaikan keterbatasan itu dan jangan membuat grafik tanpa konfirmasi pengguna.

Satu jawaban atau grafik hanya memakai satu dataset untuk satu indikator. Jangan menyambung \
tren dari dataset berbeda walau namanya mirip (mis. "Tingkat Kemiskinan" dan "Persentase \
Kepala Keluarga Miskin" adalah indikator berbeda), dan jangan membandingkan nilainya \
seolah-olah satu seri."""

_TEMPLATES = MappingProxyType(
    {"search_datasets": _SEARCH, "get_dataset_info": _INFO, "get_dataset_data": _DATA}
)


def tool_name(prefix: str, base: str) -> str:
    return f"{prefix}{base}"


def descriptions(prefix: str) -> dict[str, str]:
    """{nama dasar: deskripsi} dengan rujukan antar-tool berawalan `prefix`."""
    names = {
        "search": tool_name(prefix, "search_datasets"),
        "info": tool_name(prefix, "get_dataset_info"),
        "data": tool_name(prefix, "get_dataset_data"),
    }
    return {base: text.format(**names) for base, text in _TEMPLATES.items()}
