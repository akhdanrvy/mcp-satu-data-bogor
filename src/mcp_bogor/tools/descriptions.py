"""Deskripsi ketiga tool yang dibaca LLM (RENCANA_LANGKAH8 bagian 7, disetujui Akhdan 11 Nov 2026).

Teks dikunci tes snapshot (tests/contract/snapshots/tool_descriptions.json). Setiap perubahan
harus disetujui Akhdan dan diuji ulang dengan golden set (Dok 4 bagian 9).

Rujukan antar-tool memakai nama berawalan MCP_TOOL_PREFIX; placeholder {search}, {info},
{data} diisi saat build_server.

MCP_CLIENT_PROFILE (KB-51) hanya mengganti paragraf pembuka get_dataset_data: `backend`
(bawaan, snapshot tool_descriptions.json) atau `sandbox` (tool_descriptions_sandbox.json).
Isi respons tool tidak berubah di profil mana pun (KT-03).
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

Item dengan `metric_card` terisi adalah indikator utama yang sudah dikurasi; utamakan itu bila \
sesuai pertanyaan. Notice DIFFERENT_DEFINITION berarti dua dataset dalam hasil mengukur hal \
berbeda walau namanya mirip: pakai satu yang sesuai pertanyaan dan jangan menyambung atau \
membandingkan keduanya sebagai satu seri.

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

# Paragraf pembuka get_dataset_data per profil klien (KB-51). Sisanya sama untuk semua profil.
_DATA_INTRO_BACKEND = """\
Siapkan data satu dataset untuk dihitung dan digambar oleh sistem. Respons tool ini TIDAK \
berisi angka: backend mengambil baris lengkap lewat `data_ref` lalu menghitung dan menggambar \
sendiri. Jangan menulis, memperkirakan, atau mengarang angka apa pun dari respons ini. \
`preview` hanya berisi kolom dimensi untuk memastikan filter benar. Arti kolom dimensi tidak \
diulang di sini (meaning_source=omitted); lihat {info}.

"""

# Profil sandbox (Citya RAG Chat): model mengambil berkas data_ref sendiri lewat execute_code.
# Kunci `rows` (daftar objek per baris) dan `columns` (name, role, unit, meaning) mengikuti
# services.data.data_ref_payload. {ttl} diisi dari MCP_DATAREF_TTL_SECONDS.
_DATA_INTRO_SANDBOX = """\
Siapkan data satu dataset. Respons tool ini TIDAK berisi angka. Untuk mendapatkan angka, \
ambil berkas `data_ref.url` lewat tool `execute_code`, mis.:
    import httpx, pandas as pd
    r = httpx.get("<isi data_ref.url>", timeout=60).json()
    df = pd.DataFrame(r["rows"])
Ganti "<isi data_ref.url>" dengan nilai `data_ref.url` dari respons ini. `r["columns"]` \
memuat `name`, `role`, `unit`, dan `meaning` tiap kolom. Hitung di execute_code; cetak \
hanya hasil hitungan atau baris yang diperlukan, bukan seluruh tabel, karena keluaran \
execute_code dipotong bila panjang. Sebut angka HANYA dari hasil execute_code, apa adanya, \
dengan satuan dari kamus kolom; jangan mengarang, memperkirakan, atau membulatkan sendiri. \
`data_ref` berlaku {ttl}; bila URL menjawab 410 atau 403, panggil {data} lagi. \
`preview` hanya berisi kolom dimensi untuk memastikan filter benar; arti kolom dimensi \
ada di {info}.

"""

_DATA_BODY = """\
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

CLIENT_PROFILES = ("backend", "sandbox")

_DATA_INTROS = MappingProxyType({"backend": _DATA_INTRO_BACKEND, "sandbox": _DATA_INTRO_SANDBOX})


def tool_name(prefix: str, base: str) -> str:
    return f"{prefix}{base}"


def ttl_text(seconds: int) -> str:
    """TTL data_ref untuk teks deskripsi: menit bila bulat, selain itu detik."""
    if seconds % 60 == 0:
        return f"{seconds // 60} menit"
    return f"{seconds} detik"


def _templates(profile: str) -> dict[str, str]:
    if profile not in _DATA_INTROS:
        raise ValueError(f"profil klien tidak dikenal: {profile!r}")
    return {
        "search_datasets": _SEARCH,
        "get_dataset_info": _INFO,
        "get_dataset_data": _DATA_INTROS[profile] + _DATA_BODY,
    }


def descriptions(
    prefix: str, profile: str = "backend", *, dataref_ttl_seconds: int = 900
) -> dict[str, str]:
    """{nama dasar: deskripsi} dengan rujukan antar-tool berawalan `prefix`.

    `profile` = MCP_CLIENT_PROFILE (KB-51). Profil backend tidak memakai TTL; teksnya
    identik dengan sebelum profil diperkenalkan (snapshot tool_descriptions.json).
    """
    names = {
        "search": tool_name(prefix, "search_datasets"),
        "info": tool_name(prefix, "get_dataset_info"),
        "data": tool_name(prefix, "get_dataset_data"),
        "ttl": ttl_text(dataref_ttl_seconds),
    }
    return {base: text.format(**names) for base, text in _templates(profile).items()}
