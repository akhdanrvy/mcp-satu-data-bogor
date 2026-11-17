# Laporan smoke test 2026-11-17

Dibuat otomatis oleh `tests/smoke/harness.py` (RENCANA_LANGKAH11). Laporan ini tidak memuat nilai data; artefak mentah ada di folder run di bawah `var/smoke/` (di-gitignore).

## Ringkasan

- Hasil keseluruhan: **LULUS**
- Waktu (WIB): 2026-11-17T14:15:57+07:00 s.d. 2026-11-17T14:16:40+07:00
- Versi: `contract_version` 0.1, paket 0.1.0, commit `a13c10f`
- User-Agent: terisi
- Folder run: `var/smoke/2026-11-17`
- Permintaan ke portal: 25 (rencana 25)

| ST    | Judul                                                           | Hasil |
| ----- | --------------------------------------------------------------- | ----- |
| ST-01 | Katalog lokal vs portal                                         | LULUS |
| ST-02 | 12 kartu lewat tool sungguhan                                   | LULUS |
| ST-03 | data_ref lewat HTTP, KT-03 atas respons asli, pembanding golden | LULUS |
| ST-04 | Akses sopan                                                     | LULUS |
| ST-05 | Kinerja (NFR-03, p95)                                           | LULUS |

## ST-01: Katalog lokal vs portal (LULUS)

- Dataset di katalog lokal: 777
- `pagination.total_data` portal: 777
- Sinkron katalog terakhir: 2026-11-17T14:15:40+07:00 (status `complete`, selesai 2026-11-17T14:15:40+07:00)
- Smoke tidak menjalankan sinkron (AK-06).

Semua pemeriksaan lulus.

## ST-02: 12 kartu lewat tool sungguhan (LULUS)

| Kartu | search      | info | data | dictionary_validated | notice                   | quality flag (tahun)             | kasus golden                                                                                                          | peringatan |
| ----- | ----------- | ---- | ---- | -------------------- | ------------------------ | -------------------------------- | --------------------------------------------------------------------------------------------------------------------- | ---------- |
| KM-01 | peringkat 1 | ok   | ok   | false                | DICTIONARY_NOT_VALIDATED | -                                | -                                                                                                                     | -          |
| KM-02 | peringkat 1 | ok   | ok   | true                 | -                        | suspect_scale (2021, 2022, 2023) | -                                                                                                                     | -          |
| KM-03 | peringkat 1 | ok   | ok   | true                 | -                        | suspect_outlier (2022)           | -                                                                                                                     | -          |
| KM-04 | peringkat 1 | ok   | ok   | true                 | -                        | -                                | N-04: ok, missing [-]                                                                                                 | -          |
| KM-05 | peringkat 1 | ok   | ok   | true                 | -                        | -                                | N-05: ok                                                                                                              | -          |
| KM-06 | peringkat 1 | ok   | ok   | true                 | -                        | -                                | F-07: partial, missing [2026]; F-08: partial, missing [2019, 2020, 2021, 2022, 2023, 2024]; O-06: LEVEL_NOT_AVAILABLE | -          |
| KM-07 | peringkat 1 | ok   | ok   | true                 | -                        | -                                | -                                                                                                                     | -          |
| KM-08 | peringkat 1 | ok   | ok   | true                 | -                        | -                                | N-06: ok, missing [-]                                                                                                 | -          |
| KM-09 | peringkat 1 | ok   | ok   | true                 | -                        | -                                | N-07: ok                                                                                                              | -          |
| KM-10 | peringkat 1 | ok   | ok   | true                 | -                        | suspect_zero_period (2024)       | N-02: ok, missing [-]                                                                                                 | -          |
| KM-11 | peringkat 1 | ok   | ok   | true                 | -                        | suspect_zero_period (2024)       | N-08: ok, missing [-]                                                                                                 | -          |
| KM-12 | peringkat 1 | ok   | ok   | true                 | -                        | -                                | O-01: tanpa pin; peringkat KM-12 tidak muncul                                                                         | -          |

Semua pemeriksaan lulus.

## ST-03: data_ref lewat HTTP, KT-03 atas respons asli, pembanding golden (LULUS)

- Berkas data_ref terunduh: 19 dari 19 (HTTP ke file server lokal, sebelum `exp`)
- KT-06 (isi berkas = respons tool): 19 cocok dari 19
- KT-03 atas respons asli: 0 respons dengan temuan

Pembanding golden set (selisih = peringatan; nilai tidak ditulis):

| Kasus | Kartu | Hasil | Titik cocok/beda/ambigu | data_updated_at snapshot | portal     |
| ----- | ----- | ----- | ----------------------- | ------------------------ | ---------- |
| N-01  | KM-06 | cocok | 1/0/0                   | 2026-08-31               | 2026-08-31 |
| N-02  | KM-10 | cocok | 8/0/0                   | 2026-03-11               | 2026-03-11 |
| N-03  | KM-01 | cocok | 5/0/0                   | 2025-06-02               | 2025-06-02 |
| N-04  | KM-04 | cocok | 1/0/0                   | 2026-08-11               | 2026-08-11 |
| N-05  | KM-05 | cocok | 6/0/0                   | 2025-05-26               | 2025-05-26 |
| N-06  | KM-08 | cocok | 1/0/0                   | 2025-04-09               | 2025-04-09 |
| N-07  | KM-09 | cocok | 7/0/0                   | 2026-05-05               | 2026-05-05 |
| N-08  | KM-11 | cocok | 8/0/0                   | 2026-04-13               | 2026-04-13 |
| F-07  | KM-06 | cocok | 1/0/0                   | 2026-08-31               | 2026-08-31 |
| Q-02  | KM-02 | cocok | 5/0/0                   | 2025-05-20               | 2025-05-20 |
| Q-03  | KM-03 | cocok | 3/0/0                   | 2026-11-04               | 2026-11-04 |

Semua pemeriksaan lulus.

## ST-04: Akses sopan (LULUS)

- Permintaan ke portal: 25 (rencana 25, batas 60)
- Jeda terkecil antarpermintaan: 453 ms (disetel 300 ms, syarat ≥ 200 ms)
- Status HTTP: `200` x 25
- Ditolak guard sebelum dikirim: 0

Semua pemeriksaan lulus.

## ST-05: Kinerja (NFR-03, p95) (LULUS)

- search_datasets: semua sinonim kartu x 3 putaran; p50 58 ms, p95 129 ms (syarat < 500 ms)
- get_dataset_data dari cache: semua kartu x 3 putaran; p50 96 ms, p95 185 ms (syarat < 1000 ms); GET tambahan 0

Semua pemeriksaan lulus.

## Setelah smoke: pemeriksa kartu metrik

Jalankan di PowerShell dari akar repo, lalu salin hasilnya ke bawah ini:

```powershell
$env:MCP_CACHE_DIR="var/smoke/2026-11-17/cache"; uv run python -m mcp_bogor.metric_cards --check
```

Target: kode keluar 0, hanya peringatan "belum diperiksa".
OK* KM-01 Realisasi pendapatan asli daerah (PAD) [kamus_belum_tervalidasi] - warning: belum diperiksa (reviewed_by kosong) (NOT_REVIEWED)
OK* KM-02 Persentase realisasi PAD [aktif] - warning: belum diperiksa (reviewed_by kosong) (NOT_REVIEWED)
OK* KM-03 Realisasi retribusi daerah [aktif] - warning: belum diperiksa (reviewed_by kosong) (NOT_REVIEWED)
OK* KM-04 Realisasi belanja modal [aktif] - warning: belum diperiksa (reviewed_by kosong) (NOT_REVIEWED)
OK* KM-05 Persentase SiLPA terhadap APBD [aktif] - warning: belum diperiksa (reviewed_by kosong) (NOT_REVIEWED)
OK* KM-06 Angka kemiskinan Kota Bogor [aktif] - warning: belum diperiksa (reviewed_by kosong) (NOT_REVIEWED)
OK* KM-07 Persentase kepala keluarga miskin [aktif] - warning: belum diperiksa (reviewed_by kosong) (NOT_REVIEWED)
OK* KM-08 Kunjungan wisatawan [aktif] - warning: belum diperiksa (reviewed_by kosong) (NOT_REVIEWED)
OK* KM-09 Kunjungan pasien puskesmas [aktif] - warning: belum diperiksa (reviewed_by kosong) (NOT_REVIEWED)
OK* KM-10 Kecepatan lalu lintas Jl. Pajajaran [aktif] - warning: belum diperiksa (reviewed_by kosong) (NOT_REVIEWED)
OK* KM-11 Volume lalu lintas Jl. Pajajaran [aktif] - warning: belum diperiksa (reviewed_by kosong) (NOT_REVIEWED)
OK* KM-12 Persentase penanganan pengaduan [aktif] - warning: belum diperiksa (reviewed_by kosong) (NOT_REVIEWED)
Ringkasan: 12 kartu; ok 0, ok+peringatan 12, belum dicek 0, galat 0

## Addendum

Pembaruan 17 Nov 2026: setelah review 12 kartu dan override KM-01 (TM-01), metric_cards --check atas cache smoke = 12 OK tanpa peringatan (kode keluar 0).
