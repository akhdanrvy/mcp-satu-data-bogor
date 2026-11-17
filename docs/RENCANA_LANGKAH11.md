# Rencana Langkah 11 — Smoke test ST-01..05 (menunggu persetujuan Akhdan)

Acuan: Dok 5 bagian 3 (ST-01..05) dan bagian 5 (kriteria lulus), Dok 4 bagian 6 (data_ref), golden set v0.2, kartu metrik (KB-49/50), AK-01..06. Disusun 15 Nov 2026 setelah commit 10b (`6a94c8a`). Smoke test **dijalankan Akhdan** di laptop; tidak ada kode yang ditulis sebelum rencana ini disetujui.

## 1. Bentuk dan cara menjalankan

| Perintah | Isi | Jaringan |
|---|---|---|
| `uv run python -m tests.smoke` | **Dry-run (bawaan)**: cek prasyarat, cetak daftar permintaan per ST beserta path lengkap (schema/table dari katalog lokal), total, dan aturan akses. Pola sama dengan `scripts/record_fixtures.py`. | Tidak ada |
| `uv run pytest tests/smoke -m smoke` | **Eksekusi** (atas izin Akhdan). Menolak jalan bila rencana > 60 permintaan atau prasyarat gagal. Menulis `docs/SMOKE_REPORT_<YYYY-MM-DD>.md`. | Portal asli |

Berkas baru (usulan):
- `tests/smoke/harness.py`: rencana permintaan, `GuardedTransport`, pemanggil tool lewat klien MCP, pemeriksa, penulis laporan. Logika di sini supaya bisa diuji offline.
- `tests/smoke/__main__.py`: dry-run.
- `tests/smoke/test_smoke.py`: ST-01..05; satu tes per ST, berurutan dalam satu sesi.
- Tes **offline** di `tests/unit/test_smoke_harness.py`, ikut `pytest` bawaan, tanpa jaringan. Isinya:
  - harness dijalankan ujung ke ujung atas portal tiruan dari fixture rekaman (respx), dan harus menghasilkan tepat jumlah permintaan yang direncanakan;
  - aturan `GuardedTransport`: anggaran, berhenti pada 429, `/view`, dan selain GET ditolak;
  - laporan tanpa nilai data;
  - dry-run.

### Lingkungan eksekusi
- **Katalog:** katalog laptop `var/mcp_bogor.db`, dibaca saja. Smoke tidak pernah menulis katalog dan tidak menjalankan sinkron (AK-06).
- **Settings dari `.env` laptop,** sehingga User-Agent asli dipakai. Ada beberapa penimpaan khusus smoke:
  - `MCP_REQUEST_DELAY_S=0.3`;
  - `MCP_MAX_RETRIES=2`, satu retry untuk 5xx. Retry tidak pernah terjadi untuk 429; lihat bagian 2;
  - `MCP_CACHE_DIR=var/smoke/<tanggal>/cache`, cache bersih sehingga setiap kartu menempuh jalur live;
  - `MCP_DATAREF_SECRET` acak per jalan;
  - `MCP_FILESERVER_PUBLIC_URL=http://127.0.0.1:<port acak>`.
- **Tool sungguhan:** `runtime.build_deps(settings, strict=True, adapter=OpenDataJabarAdapter(settings, transport=GuardedTransport(...)))` lalu `server.build_server(deps)`. Ketiga tool dipanggil lewat klien MCP in-memory SDK, dengan jalur sama seperti tes KT-01. Adaptor dipakai hanya lewat tool, kecuali satu GET ST-01.
- **File server sungguhan:** uvicorn di `127.0.0.1:<port acak>` dalam proses yang sama, memakai cache dan secret yang sama. data_ref diunduh lewat HTTP dengan httpx.
- **Artefak mentah:** respons tool dan berkas data_ref disimpan di `var/smoke/<tanggal>/` (di-gitignore) untuk diperiksa Akhdan. Isinya memuat baris data, jadi tidak pernah di-commit. Laporan di `docs/` tidak memuat nilai data.

## 2. Akses sopan (ST-04) dan `GuardedTransport`

Transport httpx pembungkus, dipasang di adaptor smoke:
- Hanya GET ke pola path yang diizinkan, yaitu `/datasets` (dengan `per_page=1`) dan `/bigdata/{schema}/{table}`. Path yang memuat `/view` selalu ditolak (AK-02).
- **Anggaran keras 60 permintaan.** Permintaan ke-61 ditolak sebelum dikirim. Retry 5xx ikut dihitung.
- **Berhenti pada 429:** begitu menerima 429, semua permintaan berikutnya ditolak tanpa dikirim, termasuk retry adaptor. Harness memeriksa penanda ini setelah setiap panggilan tool dan menghentikan sesi (`pytest.exit`). ST-04 gagal dan laporan tetap ditulis.
- **Pencatatan per permintaan:** waktu kirim monotonic, path tanpa query nilai, status, dan durasi. Jeda terkecil antarpermintaan diukur dan dilaporkan. Syarat ST-04 ≥ 0,2 dtk; yang disetel 0,3 dtk.
- 5xx dua kali berturut-turut membuat kartu itu gagal. Smoke tidak diulang otomatis.

## 3. Daftar permintaan ke portal

Hitungan memakai fixture 10 Nov. Semua dataset kartu ≤ 88 baris, sehingga cukup satu halaman `/bigdata`: `per_page` 100 untuk probe kamus dan 1.000 untuk seri. Seri disimpan di cache per slug (KB-37, ≤ 5.000 baris), sehingga variasi periode atau filter berikutnya tanpa GET.

| ST | Permintaan | Jumlah |
|---|---|---|
| ST-01 | `GET /datasets?page=1&per_page=1` → `pagination.total_data` (lewat metode adaptor baru, lihat bagian 4) | 1 |
| ST-02 | Per kartu: `search_datasets` 0 (katalog lokal); `get_dataset_info` 1 (probe `/bigdata` halaman 1, `per_page=100`); `get_dataset_data` pertama 1 (seri `/bigdata` halaman 1, `per_page=1000`); panggilan data lanjutan untuk kasus golden 0 (cache seri). 12 × 2 | 24 |
| ST-03 | Unduh data_ref dari file server lokal | 0 |
| ST-04 | Hanya mengukur | 0 |
| ST-05 | Pencarian dan data dari cache (lokal) | 0 |
| **Total direncanakan** | | **25** |
| Batas terburuk | 25 + satu retry 5xx per permintaan | 50 (< 60) |

Bila sebuah seri ternyata lebih dari satu halaman, jumlahnya bertambah dan tercetak di dry-run. Harness menghitung halaman dari `total_data`, bukan dari asumsi.

## 4. Rincian per ST

### ST-01: katalog lokal vs portal (tanpa sinkron penuh)
- Tidak memanggil `sync_worker` (AK-06); sinkron sudah dilakukan manual oleh Akhdan.
- Metode adaptor baru `OpenDataJabarAdapter.catalog_total()` mengirim satu GET dan mengembalikan `pagination.total_data`. Adaptor tetap satu-satunya yang tahu bentuk API. Ada tes respx untuk metode ini.
- **Lulus** bila jumlah dataset di katalog lokal sama dengan `total_data`.
- Laporan juga memuat waktu dan status sinkron terakhir dari `sync_runs`. Bila jumlahnya berbeda, ST-01 gagal dengan keterangan "katalog lokal tertinggal; sinkron ulang manual", dan smoke tidak melakukan sinkron sendiri.

### ST-02: 12 kartu lewat tool sungguhan
Untuk setiap kartu, urutannya search → info → data.

**Kriteria umum per kartu (semua wajib):**
1. `search_datasets` dengan sinonim pertama kartu: item pertama = slug kartu, dengan `metric_card` = id kartu.
2. `get_dataset_info`:
   - status `ok`;
   - `kolom_nilai` berperan measure;
   - kolom dimensi ada dan berperan dimension;
   - satuan cocok;
   - `dictionary_validated` cocok dengan status kartu. Kartu `aktif` harus `true`. KM-01 (`kamus_belum_tervalidasi`) harus `false` dengan notice `DICTIONARY_NOT_VALIDATED`.
   - `periods_available` dibandingkan dengan `periode_tersedia`. Bila berbeda, hanya peringatan.
3. `get_dataset_data` tanpa periode: status `ok`; untuk KM-01, `ok` dengan notice `DICTIONARY_NOT_VALIDATED`.
4. **KT-03:** lihat ST-03.

**Kriteria khusus dari golden set:**

| Kartu | Kasus | Panggilan tambahan (0 GET) | Lulus jika |
|---|---|---|---|
| KM-01 | N-03 | search "pendapatan asli daerah" | Peringkat 1 = KM-01 |
| KM-02 | Q-02 | data semua tahun | `quality_flags` QC-02 (skala) pada 2021–2023 |
| KM-03 | Q-03 | data semua tahun | QC-02 (pencilan) pada 2022 |
| KM-04 | N-04 | data `periods=[2025]` | `ok`, `periods_found=[2025]` |
| KM-05 | N-05 | data semua tahun | `ok` |
| KM-06 | N-01, F-07, F-08, O-06 | (a) data `[2025, 2026]`; (b) data 2019–2025; (c) filter `kecamatan` | (a) `partial`, `missing_periods=[2026]`; (b) `partial`, `periods_found=[2025]`; (c) galat `LEVEL_NOT_AVAILABLE` tanpa angka |
| KM-07 | F-08 | search "kemiskinan" | Notice `DIFFERENT_DEFINITION` dengan slug KM-06 dan KM-07 |
| KM-08 | N-06 | data `[2024]` | `ok` |
| KM-09 | N-07 | data semua tahun | `ok` |
| KM-10 | N-02, Q-01 | data `[2025]`; data semua tahun | `ok`; QC-01 pada 2024 |
| KM-11 | N-08 | data `[2023, 2025]`, `hari_operasional=["HARI KERJA"]` | `ok` |
| KM-12 | O-01 | search "aduan warga" | Tidak ada pemasangan kartu (`pinned` tidak ada; KM-12 tidak dipaksa ke peringkat 1) |

"Partial yang beralasan" (Dok 5) berarti partial hanya karena `missing_periods` atau `STALE_DATA` yang tercatat. Partial karena `truncated` dilaporkan sebagai peringatan.

### ST-03: data_ref lewat HTTP, KT-03 atas respons asli, dan pembanding golden set
1. Setiap `data_ref` dari ST-02 (dasar dan varian golden) diunduh lewat file server sebelum `exp`. Status harus 200.
2. Isi berkas dicocokkan dengan respons tool memakai aturan KT-06, dengan asersi yang sama seperti `tests/contract/test_data_ref_content.py`.
3. **KT-03 atas respons asli (nol toleransi):**
   - Nilai measure diambil dari baris berkas data_ref live, untuk kolom `kolom_nilai` kartu ditambah daftar acuan `MEASURES` di `test_no_values.py`.
   - Bentuk khasnya (desimal, ≥ 3 digit, format Indonesia) dicari di JSON **semua** respons search, info, dan data. Fungsinya sama dengan `find_leaks`.
   - Satu temuan saja sudah menggagalkan ST-03.
4. **Pembanding golden set:**
   - Kasus yang punya snapshot: N-01..N-08, Q-02, Q-03, F-07. Nilai di berkas data_ref dibandingkan dengan `snapshot.values`, dengan toleransi pembulatan desimal 1e-9.
   - **Selisih tidak menggagalkan smoke otomatis.** Selisih dilaporkan sebagai "perubahan data portal" bersama `data_updated_at` di snapshot dan di portal, tanpa menulis nilainya.
   - Bila `data_updated_at` sama tetapi nilainya berbeda, laporan menandainya **"perlu diperiksa"**: ini mungkin bug normalisasi, bukan perubahan portal. Statusnya tetap peringatan, dan Akhdan yang memutuskan.

### ST-04: akses sopan
**Lulus** bila tidak ada respons 429, jeda terkecil ≥ 0,2 dtk, tidak ada permintaan ke `/view`, tidak ada selain GET, dan total ≤ 60. Laporan memuat jumlah permintaan nyata dibandingkan rencana.

### ST-05: kinerja (NFR-03, p95)
- **Pencarian:** semua sinonim kartu (± 40 kueri) × 3 putaran lewat klien MCP. Lulus bila p95 < 0,5 dtk.
- **Data dari cache:** `get_dataset_data` tanpa periode untuk 12 kartu × 3 putaran, setelah ST-02 (cache hangat, 0 GET). Lulus bila p95 < 1 dtk.

## 5. Laporan `docs/SMOKE_REPORT_<YYYY-MM-DD>.md` (tanpa nilai data)
- **Ringkasan:** lulus atau gagal per ST, waktu jalan, versi (`contract_version`, commit), User-Agent (tanpa kontak; cukup "terisi").
- **ST-01:** jumlah lokal, `total_data`, waktu sinkron terakhir.
- **ST-02:** tabel per kartu berisi status search, info, dan data; kode notice; kode quality flag beserta tahunnya (tahun bukan nilai); `dictionary_validated`; peringatan periode.
- **ST-03:** jumlah berkas terunduh; hasil KT-06; hasil KT-03 (jumlah temuan; bila ada, hanya path field, tanpa nilai); tabel golden berisi kasus, cocok/berbeda/perlu diperiksa, dan `data_updated_at` snapshot vs portal.
- **ST-04:** jumlah permintaan, jeda terkecil, daftar status HTTP.
- **ST-05:** p50 dan p95.
- **Pelindung isi laporan:** penulis laporan menolak teks yang memuat angka berformat atau nilai measure. Pemeriksaannya sama dengan KT-03, dijalankan atas laporan itu sendiri.

## 6. Setelah smoke: `metric_cards --check`
- Smoke mengisi cache kamus ke-12 kartu di `var/smoke/<tanggal>/cache`.
- Langkah terakhir harness, atau perintah yang dicetak di laporan:
  `$env:MCP_CACHE_DIR="var/smoke/<tanggal>/cache"; uv run python -m mcp_bogor.metric_cards --check`
- Target kode keluar **0**, dengan hanya peringatan "belum diperiksa". Hasilnya disalin ke laporan, dan TODO Langkah 11 di `RENCANA_FASE1.md` dicentang.

## 7. Perlu keputusan Akhdan
1. **Cache smoke terpisah** (`var/smoke/<tanggal>/cache`, usul saya) atau cache laptop `var/cache`?
   - Cache terpisah menjamin setiap kartu menempuh jalur live dan jumlah permintaan pasti 25. Akibatnya `--check` dijalankan dengan `MCP_CACHE_DIR` cache smoke.
   - Cache laptop membuat KM-01 dan KM-06 memakai kamus lama, sehingga probe-nya tidak teruji ulang.
2. **Metode adaptor baru `catalog_total()`** untuk ST-01: setuju? Alternatifnya memakai `health()`, tetapi metode itu tidak mengembalikan `total_data`.
3. **`MCP_MAX_RETRIES=2`** (satu retry untuk 5xx, 429 tetap langsung berhenti), atau `1` (tanpa retry sama sekali)?
4. **Kasus F-08 dan O-06** ikut di ST-02 sebagai kriteria lulus, atau hanya dilaporkan? Usul saya: kriteria lulus, karena keduanya tanpa GET tambahan.
5. **Tanggal di nama laporan:** tanggal WIB saat eksekusi, mis. `docs/SMOKE_REPORT_2026-11-16.md`.

## 8. Keputusan Akhdan (17 Nov 2026) dan penerapannya
1. **Cache smoke terpisah** di `var/smoke/<tanggal>/cache`. `--check` dijalankan dengan `MCP_CACHE_DIR` cache itu.
2. **`OpenDataJabarAdapter.catalog_total()`** untuk ST-01: disetujui. Satu GET `/datasets?page=1&per_page=1`; bukan bagian Protocol `SourceAdapter`.
3. **`MCP_MAX_RETRIES=2`** selama smoke. 429 tetap menghentikan sesi: `GuardedTransport` menolak retry adaptor sebelum dikirim.
4. **F-08 dan O-06 menjadi kriteria lulus.** F-07/F-08 dihitung **relatif** terhadap `periods_available` dari `get_dataset_info` di sesi yang sama: `missing_periods` = tahun diminta yang tidak ada di `periods_available`; status `partial` bila irisannya tidak kosong (dan `ok` bila tidak ada yang hilang). Tidak ada tahun yang ditulis mati. O-06 tetap mutlak (`LEVEL_NOT_AVAILABLE`, tanpa angka). Kasus N-* dengan periode juga dihitung relatif, ditambah syarat semua periode golden masih ada.
5. **Nama laporan** memakai tanggal WIB saat eksekusi; bila laporan atau folder run sudah ada, akhiran `-2`, `-3`, dan seterusnya. Tidak pernah menimpa.

Catatan penerapan:
- Q-01 (KM-10) memakai filter golden `hari_operasional=["HARI KERJA"]` (sama dengan tes kontrak golden), tetap 0 GET tambahan.
- Tanda mutu golden wajib ada; tanda mutu tambahan dilaporkan sebagai peringatan.
- Pelindung laporan menolak per baris dan menyebut nomor barisnya tanpa nilai. Draf yang ditolak disimpan di folder run sebagai `SMOKE_REPORT_DITOLAK.md`.
