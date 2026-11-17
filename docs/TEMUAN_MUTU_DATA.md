# Temuan Mutu Data — Portal Satu Data Kota Bogor

Catatan temuan mutu (Dok 2 QC-04) sebagai bahan laporan ke Diskominfo atau OPD produsen. Saluran pelaporan masih [Belum ditentukan] (Dok 2 bagian 7).

Aturan penulisan:
- Hanya slug, kolom, dan jenis temuannya. **Tidak ada nilai data** di dokumen ini.
- MCP tidak mengoreksi data (ADR-007). Temuan ditandai lewat `quality_flags` atau `dictionary_validated=false`, lalu dilaporkan di sini.
- Sumber bukti: rekaman portal 10 Nov 2026 (`tests/fixtures/recorded/2026-11-10/`) dan tes yang disebut per temuan.

## A. Kamus kolom (Langkah 6, KK-03)

| ID | Dataset (slug) | OPD | Kolom | Temuan | Dampak di MCP | Bukti |
|---|---|---|---|---|---|---|
| TM-01 (**selesai** 17 Nov 2026) | `realisasi-pendapatan-asli-daerah-pad-kota-bogor` | Badan Pendapatan Daerah | `total_pendapatan` (data), `cakupan_pelayanan` (deskripsi) | Deskripsi menjelaskan kolom `cakupan_pelayanan`, yang tidak ada di data. Kolom nilai utama di data, `total_pendapatan`, tidak dijelaskan di deskripsi. | Kolom measure undocumented, sehingga `dictionary_validated=false`. Arti kolom kosong sampai ada override yang sudah diverifikasi. **Selesai:** override `overrides/realisasi-pendapatan-asli-daerah-pad-kota-bogor.yaml` (meaning, unit RUPIAH, reviewed_by Akhdan, reviewed_at 2026-11-17); KM-01 menjadi `aktif`. Deskripsi portal memuat arti ini di bawah nama kolom `cakupan_pelayanan` (salah label); diverifikasi dari halaman dataset portal 17 Nov 2026; konfirmasi Bapenda belum ada. | `tests/unit/test_dictionary.py::test_recorded_main_value_undocumented_pad` |
| TM-02 | `tren-kunjungan-pasien-puskesmas-di-kota-bogor` | Dinas Kesehatan | `jumlah_pasien` | Deskripsi menyebut tipe data teks, padahal semua nilai di data berupa angka. | Peran ditetapkan dari nilai data (aturan e, KB-30), yaitu measure. `type` tetap ditulis sesuai deskripsi (`string`). | `tests/unit/test_dictionary.py::test_recorded_roles` |
| TM-06 | `realisasi-penerimaan-pendapatan-asli-daerah-pad-bersumber-dari-retribusi-daerah-di-kota-bogor` (KM-03) | Badan Pendapatan Daerah | `realisasi_pad` | Deskripsi portal menyebut satuan "persen", sedangkan data (kolom `satuan`) berisi "RUPIAH". | Keluaran MCP tidak terdampak: satuan kolom measure mengikuti data/Satuan Dataset (RUPIAH), sama dengan kartu KM-03. Dicatat untuk dilaporkan ke OPD. | Review kartu metrik terhadap halaman portal 17 Nov 2026 |

## B. Penanda mutu nilai (Langkah 2, QC-01/QC-02)

| ID | Kasus golden set | Dataset (slug) | OPD | Kolom | Temuan | Penanda |
|---|---|---|---|---|---|---|
| TM-03 | Q-01 | `kecepatan-lalu-lintas-segmen-1-dan-2-berdasarkan-hari-operasional-di-jalan-raya-pajajaran-kota-bogor` | Dinas Perhubungan | `kecepatan` | Seluruh baris periode 2024 bernilai nol di semua segmen dan hari operasional, sehingga kemungkinan data belum diisi. | `suspect_zero_period` (QC-01), periode 2024 |
| TM-04 | Q-02 | `persentase-realisasi-pendapatan-asli-daerah-pad-kota-bogor` | Badan Pendapatan Daerah | `persentase_pendapatan` | Periode 2021, 2022, dan 2023 berbeda skala jauh (melewati ambang `MCP_QC_RATIO`) dari periode lain. Kemungkinan salah skala di sumber. | `suspect_scale` (QC-02, KB-18), periode 2021–2023 |
| TM-05 | Q-03 | `realisasi-penerimaan-pendapatan-asli-daerah-pad-bersumber-dari-retribusi-daerah-di-kota-bogor` | Badan Pendapatan Daerah | `realisasi_pad` | Periode 2022 menyimpang jauh dari periode di sekitarnya (melewati ambang `MCP_QC_RATIO`). | `suspect_outlier` (QC-02, KB-18), periode 2022 |

Bukti TM-03 s.d. TM-05: `tests/unit/test_services_recorded.py::test_q01_zero_period_2024_on_real_data`, `test_q02_scale_on_full_real_series`, `test_q03_outlier_on_full_real_series`.

## C. Tindak lanjut

- TM-01: **selesai** 17 Nov 2026. Deskripsi portal memuat arti ini di bawah nama kolom `cakupan_pelayanan` (salah label); diverifikasi dari halaman dataset portal 17 Nov 2026; konfirmasi Bapenda belum ada. Override sudah dibuat; sisa tindak lanjut: konfirmasi ke Bapenda dan meminta OPD memperbaiki deskripsi.
- TM-02: minta OPD menyamakan tipe data di deskripsi dengan isi data.
- TM-06: minta OPD memperbaiki satuan di deskripsi ("persen" → rupiah) agar sama dengan data.
- TM-03 s.d. TM-05: minta OPD memeriksa nilai pada periode bertanda. MCP tetap menyerahkan nilai apa adanya beserta penanda.
