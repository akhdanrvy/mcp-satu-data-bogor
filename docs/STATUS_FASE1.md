# Status Fase 1: MCP Kota Bogor

Per 17 Nov 2026. Fase 1 menilai MCP sendiri, tanpa model (Dok 5 bagian 5). Rencana dan riwayat langkah ada di [`RENCANA_FASE1.md`](RENCANA_FASE1.md); keputusan build ada di [`KEPUTUSAN_BUILD.md`](KEPUTUSAN_BUILD.md).

Bukti utama:
- Kode smoke dan perbaikan KT-06: commit `a13c10f` (Langkah 11a).
- Laporan smoke: [`SMOKE_REPORT_2026-11-17.md`](SMOKE_REPORT_2026-11-17.md), commit `000639b`.
- Uji manual MCP Inspector 11 Nov 2026 (transport stdio, file server terpisah di `localhost:8799`): ringkasannya di README, bukti di RENCANA_FASE1 (TODO Langkah 9).
- Tes dan cakupan di bawah diukur di pohon kerja Langkah 12 (kode sama dengan `000639b`, ditambah dokumentasi dan `tests/unit/test_docs.py`).

## 1. Kriteria lulus Dok 5 bagian 5 (kolom Fase 1)

| Ukuran | Target Fase 1 | Hasil | Bukti | Status |
|---|---|---|---|---|
| Unit test dan uji kontrak lulus | 100% | 1.310 lulus, 0 gagal. Dilewati: 4 kasus golden yang murni perilaku LLM (F-04, F-05, F-06, O-07) dan 10 varian KT-03 "filter `hari_operasional`" untuk dataset tanpa kolom itu | `uv run pytest` | ✅ |
| Cakupan kode `services/` dan `tools/` | ≥ 80% | `services/` 95–100% per modul; `tools/` 95–100% per modul; total paket 98% | `uv run pytest --cov=mcp_bogor` | ✅ |
| Smoke test 12 kartu metrik | 100% | ST-02 12/12 kartu lulus (search → info → data), termasuk F-07, F-08, O-06, N-03..N-08, Q-01..Q-03; ST-01..05 lulus | Laporan smoke 17 Nov | ✅ |
| Tidak ada nilai di respons yang dibaca LLM (KT-03) | 100%, nol toleransi | Kontrak: lapis struktural + pencarian nilai khas untuk 12 dataset (info dan data, 3 varian). Smoke: nilai measure dari berkas data_ref live dicari di semua respons asli → **0 temuan** | `tests/contract/test_no_values.py`; ST-03 | ✅ |
| Kasus fail_safe dan out_of_source tanpa angka karangan | Tidak diukur | Level tool sudah diuji: `NO_MATCH`, `SOURCE_UNAVAILABLE`, `LEVEL_NOT_AVAILABLE`, tanpa angka berformat | `tests/contract/test_golden_tools.py` | Fase 2 |
| Angka di jawaban sama dengan sumber (check_narrative) | Tidak diukur | — | — | Fase 2 |
| Pemilihan dataset benar pada kasus normal | 100% lewat kartu metrik | Golden set: 18/18 di top-3 (setelah pemasangan sinonim, Langkah 10b). Smoke: sinonim pertama tiap kartu → kartu itu peringkat 1, 12/12; N-03 "pendapatan asli daerah" → KM-01 | Tes search + golden; ST-02 | ✅ |
| Pemilihan sumber pada kasus campuran dan jebakan | Tidak diukur | O-01 "aduan warga": tanpa pemasangan kartu (dicatat, bukan dinilai) | ST-02 KM-12 | Fase 2 |
| Kasus data_quality menyebut catatan mutu | 100% `quality_flags` terisi | Q-01 (`suspect_zero_period` 2024), Q-02 (`suspect_scale` 2021–2023), dan Q-03 (`suspect_outlier` 2022) terisi, di fixture maupun portal asli | Tes golden; ST-02 | ✅ |
| Kinerja (NFR-03, p95) | Pencarian < 0,5 dtk; cache < 1 dtk | p95 search 129 ms; p95 data dari cache 185 ms (3 putaran, lewat klien MCP) | ST-05 | ✅ |
| Respons 429 dari portal saat uji | 0 | 0 dari 25 permintaan (jeda terkecil 453 ms) | ST-04 | ✅ |

Tambahan di luar tabel Dok 5:
- `metric_cards --check` atas cache smoke keluar 0: 12 OK, hanya peringatan NOT_REVIEWED.
- KT-06 (isi berkas data_ref = respons tool) lulus di kontrak dan atas respons asli (ST-03).
- Temuan smoke: katalog laptop 745 dataset (15 Nov) tertinggal dari portal. Setelah sinkron manual 17 Nov jumlahnya 777, sama dengan `total_data` portal.

## 2. Keputusan build yang menyimpang dari atau menambah Dok 1–5

Satu baris per entri; rincian dan alasan ada di `KEPUTUSAN_BUILD.md` pada ID yang sama. Nilai awal untuk hal [Belum ditentukan] (mis. timeout, host bawaan) tidak diulang di sini. Daftar lengkap env var ada di README.

**Kontrak tool (Dok 4)**

| ID | Dok | Ringkasan |
|---|---|---|
| KK-A1 | Dok 4 bagian 7 | Kode galat ke-10 `LEVEL_NOT_AVAILABLE` (tool data): filter wilayah di bawah kota pada dataset tingkat kota |
| KK-A2 | Dok 3, golden set | `not_in_source` dipetakan ke `NO_MATCH`, tanpa kode baru |
| KK-B | Dok 4 bagian 2 | `provenance` ditambah `freshness_source` dan `metadata_changed_after_approve` (KS-01..03) |
| KK-E | Dok 4 bagian 2, Dok 2 bagian 7 | `quality_flags` = `{code, rule, column, period, detail}`; `code` = `suspect_*`, `rule` = QC-01/QC-02 |
| KK-F | Dok 4 bagian 5 | `time_dimension` baru; `periods_found`/`missing_periods` boleh `null` untuk dataset tanpa tahun (CP-03) |
| KK-M | Dok 4 bagian 5 | `meaning_source` ∈ {template, override, none}; `inferred` dihapus (arti tidak pernah ditebak) |
| KK-S | Dok 4 bagian 5 | `meaning_source="omitted"` untuk kolom dimensi di respons `get_dataset_data` |
| KK-P, KB-37 | Dok 4 bagian 5 | Periode dipotong lokal dari seri; filter disaring lokal untuk seri ≤ `MCP_SERIES_LOCAL_MAX_ROWS` (5.000), di atasnya dikirim ke portal |
| KB-39 | Dok 4 bagian 5 | `filters.tahun` dipindah ke `periods`; nilai filter dicocokkan ke nilai unik kolom (NB-08) |
| KK-N, KB-34 | Dok 4 bagian 6 | Isi berkas data_ref mengikuti Dok 4; `served_from` hanya di amplop, tidak di berkas |
| KK-Q | Dok 4 bagian 6, NB-08 | Label asli disimpan di `label_originals` berkas data_ref |
| KK-R | Dok 4 bagian 2 | Field amplop baru `notices` (templat tetap, tanpa angka) dan `dictionary_validated` di result data dan berkas |
| KK-T, KB-50 | Dok 4 bagian 3 dan 8 | `SearchItem.metric_card`; pemasangan sinonim kartu di peringkat 1; notice `DIFFERENT_DEFINITION` + field `datasets` (R-10) |
| KB-10 | Dok 4 bagian 7 | Pesan galat bertemplat tetap per kode, placeholder bertipe, tanpa teks bebas |
| KB-11 | Dok 4 | Datetime selalu WIB `+07:00`, presisi detik |
| KB-13 | Dok 4 bagian 2 | Invarian amplop tambahan (ok/partial wajib `result`, `truncated` hanya partial, `STALE_DATA` ⇔ `stale_cache`, dst.) |
| KB-15 | Dok 4, KT-03 | `top_values` dan `preview` menolak teks berbentuk angka berformat (lapis kedua KT-03) |
| KB-32 (7) | Dok 4 bagian 7 | `RESPONSE_TOO_LARGE` juga dipakai saat baris melebihi `MCP_CACHE_MAX_ROWS` |
| KB-38 | Dok 4 bagian 9 | Anggaran token ≈ byte JSON / 3, pemangkasan bertahap dengan `truncated_reason` berkode |
| KB-40 | Dok 4 | Pola `source_url` = `<situs>/dataset/{slug}` (terverifikasi di browser) |
| KB-41 | Dok 4 bagian 7 | `PERIOD_NOT_AVAILABLE` menyebut tahun terakhir yang tersedia |
| KB-43 | Dok 4 bagian 4–5 | Parameter `slug` dibatasi `^[a-z0-9_-]+$`, ≤ 255 karakter |
| KB-46 | Dok 4, KT-03 | Deret angka di teks bebas portal disamarkan "[angka]" di respons (kecuali tahun); arti kolom di data hanya untuk measure |

**Data sumber (Dok 2)**

| ID | Dok | Ringkasan |
|---|---|---|
| KB-18 | Dok 2 QC-02 | Rumus QC-02 diganti (median per periode, rasio antarkelompok `MCP_QC_RATIO`), karena rumus Dok 2 gagal pada Q-02 |
| KB-19 | Dok 2 CP-01 | Hanya kolom `tahun` sebagai periode; kolom bulan di luar cakupan v0.1 |
| KK-H, KB-24 | Dok 2 AK-01 | Allowlist enam endpoint (tambah `/organisasi`, `/topik`); segmen `view`/`..` ditolak sebelum dikirim |
| KB-25 | Dok 2 AK-04 | Jeda global per proses setelah 429 (dan 503 ber-Retry-After) |
| KB-26 | Dok 2 AK-03 | Satu adaptor per proses; sinkron memakai jeda tersendiri `MCP_SYNC_REQUEST_DELAY_S` (0,5 dtk) |
| KB-28 | Dok 2 AK-06 | Sinkron gagal/tidak lengkap (bukan karena 429) boleh diulang setelah 6 jam; selain itu 24 jam |
| KK-I, KB-30 | Dok 2 KK-04 | Aturan peran kolom diganti (kode, `no_*`, bulan/triwulan/semester sebagai dimensi); kamus toleran pemisah `;` |
| KK-J, KK-L | Dok 2 bagian 2 | Kontak OPD tidak disimpan di mana pun; kolom data publik bernama `alamat`/`email` tetap dipertahankan |
| KK-G | Dok 2 bagian 2 | `change_notes` = notes tidak kosong dari entri terbaru |

**Arsitektur (Dok 3)**

| ID | Dok | Ringkasan |
|---|---|---|
| KB-42 | Dok 3, ADR-009 | Server low-level SDK MCP resmi (bukan dekorator FastMCP); `MCP_EXPOSE_OUTPUT_SCHEMA` |
| KK-K, KK-O | Dok 3 bagian 5 | `SourceAdapter` async, `list_datasets` → `CatalogSnapshot`, ditambah `probe`, `list_organisasi`, `list_topik`, `last_health` |
| KB-29 | Dok 3 | Pencarian FTS5 + stemmer PySastrawi + trigram; prefix hanya untuk token ≥ 4 huruf |
| KB-32 | Dok 3 ADR-003 | Cache berkas: stale ≤ 168 jam, batas MB/baris, GC, `cache_id` HMAC, single-flight |
| KB-33 | Dok 3 ADR-008 | File server: urutan cek 403→410→404→503; rotasi dengan `MCP_DATAREF_SECRET_PREVIOUS` |
| KB-36 | Dok 3 | Cache kamus menyimpan `periods_available` dan `row_count`; sampel `MCP_DICTIONARY_SAMPLE_ROWS` |
| KB-47 | Dok 3 bagian 6 | Transport stateless + JSON; proteksi DNS rebinding; token bearer opsional; `/healthz` tanpa portal |
| KB-48 | Dok 3 | Log JSON satu baris per kejadian ke stderr |
| KB-49 | Dok 4 bagian 8 | Skema YAML kartu metrik, status kartu, dan pemeriksa `--check` |
| Langkah 11 | Dok 5 bagian 3 | Smoke: `catalog_total()` di adaptor, `GuardedTransport`, F-07/F-08 dinilai relatif terhadap `periods_available` (RENCANA_LANGKAH11 bagian 8) |

## 3. TODO revisi dokumen acuan

Dikumpulkan dari RENCANA_FASE1. Revisi dilakukan di Google Docs, lalu diekspor ulang ke `docs/`.

**Dok 2**
- [ ] QC-02 diganti sesuai KB-18.
- [ ] AK-01 menjadi enam endpoint (KK-H).
- [ ] KK-04 aturan peran kolom sesuai KK-I, termasuk bulan/triwulan/semester.
- [ ] Statistik template kamus dan larangan `inferred` (KK-M).
- [ ] Jumlah dataset: ganti angka tetap 757 menjadi "±777 per 17 Nov 2026, berubah seiring waktu" (juga di Dok 1).

**Dok 4**
- [ ] Bagian 2: field `notices` (KK-R), kode `DIFFERENT_DEFINITION` + field `datasets` (KK-T), field `provenance` tambahan (KK-B), dan bentuk `quality_flags` (KK-E).
- [ ] Bagian 3: `metric_card` di item search (KK-T).
- [ ] Bagian 5: `meaning_source` {template, override, omitted, none} (KK-M, KK-S); periods/filter lokal (KK-P); `filters.tahun` → `periods` (KB-39); `time_dimension` (KK-F).
- [ ] Bagian 6: isi berkas data_ref (tanpa `served_from`, plus `label_originals`, `dictionary_validated`, `time_dimension`).
- [ ] Bagian 7: tabel menjadi **10 kode** (`LEVEL_NOT_AVAILABLE`, KK-A1); `RESPONSE_TOO_LARGE` juga untuk batas baris cache.
- [ ] Bagian 8: KM-04 "realisasi belanja modal"; KM-05 "persentase SiLPA terhadap APBD" (PERSEN); KM-08 satuan KUNJUNGAN; periode KM-01 2019–2024 dan KM-11 2022–2025; kolom OPD diisi nama katalog.
- [ ] Bagian 9: cocokkan daftar env var dengan tabel README (env tambahan sejak Langkah 0, mis. `MCP_CACHE_STALE_MAX_H`, `MCP_SERIES_LOCAL_MAX_ROWS`, `MCP_HTTP_AUTH_TOKEN`, `MCP_DATAREF_SECRET_PREVIOUS`, `MCP_EXPOSE_OUTPUT_SCHEMA`, `MCP_METRIC_CARDS_DIR`).

**Dok 3**
- [ ] Catat KB-42 (server low-level) di ADR-009; konflik amplop dan env var Dok 3 vs Dok 4 diselesaikan mengikuti Dok 4.

## 4. Yang belum selesai

| Hal | Pemilik | Catatan |
|---|---|---|
| Review 12 kartu metrik, lalu isi `reviewed_by`/`reviewed_at` | Akhdan | `--check` kini hanya memberi peringatan NOT_REVIEWED |
| Override `total_pendapatan` (KM-01, TM-01), lalu status KM-01 → `aktif` | Akhdan | Sampai itu KM-01 memberi `DICTIONARY_NOT_VALIDATED` |
| Review snapshot `tests/contract/snapshots/tool_schemas.json` (tool info, `metric_card`, `DIFFERENT_DEFINITION`) dan persetujuan KB-43 | Akhdan | |
| Saluran pelaporan `TEMUAN_MUTU_DATA.md` ke Diskominfo/OPD (QC-04) | Akhdan | [Belum ditentukan] di Dok 2 |
| Tinjau aturan indeks stem KB-29 (tanpa hitung ganda; awalan pe-/per- tidak di-stem) | Akhdan | |
| Mode loop di dalam `sync_worker` (opsional) | Tim | Saat ini dipenuhi penjadwal OS (`RUNBOOK.md` bagian 2) |
| Pertanyaan terbuka integrasi (ADR-011, `outputSchema`, `num_ctx`, `render_chart`, jaringan 8799, token) | Pak Ridho, lead technical | `INTEGRASI_CITYA.md` bagian 6 |
| Cabut `filterwarnings` KB-35 saat Starlette diperbarui | Tim | |
| Pertimbangkan *negative cache* singkat setelah portal gagal | Tim | Saat ini dibatasi backoff dan jeda global |
| Fase 2: evaluasi golden set bersama model (angka karangan, check_narrative, pemilihan sumber ≥ 90%) | Tim Citya + Akhdan | Dok 5 bagian 5, kolom Fase 2 |
