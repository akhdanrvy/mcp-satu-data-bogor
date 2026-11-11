# Rencana Langkah 8 — Tiga Tool MCP Kota Bogor

Status: **disetujui Akhdan 11 Nov 2026 dengan perubahan** (deskripsi tool, seri tanpa filter, aturan KT-03, KB-40 menunggu verifikasi). Pelaksanaan bertahap: **8a** (search + kerangka) selesai; 8b (info) dan 8c (data) menyusul.

Perubahan dari usulan awal, atas keputusan Akhdan:
1. Teks deskripsi bagian 7 direvisi. Teks final ada di `src/mcp_bogor/tools/descriptions.py` dan dikunci snapshot `tests/contract/snapshots/tool_descriptions.json`; perubahan berikutnya harus disetujui Akhdan.
2. Seri diambil **tanpa filter** lalu disaring lokal selama `total_data` ≤ `MCP_SERIES_LOCAL_MAX_ROWS` (5.000); lewat ambang, filter non-tahun dikirim ke portal (bagian 1.3 dan 4).
3. KT-03 = cek struktural + pencarian nilai khas measure (bagian 8).
4. KB-40 (pola `source_url`) terverifikasi Akhdan 11 Nov 2026.
6. Review 8a: server memakai `mcp.server.lowlevel.Server` dengan handler publik (KB-42 revisi), `MCP_EXPOSE_OUTPUT_SCHEMA`, argumen asing → INVALID_PARAM; dua tambahan deskripsi (notice DICTIONARY_NOT_VALIDATED di data; `request_id` "Diisi oleh sistem pemanggil; biarkan kosong.").
5. Catatan ketergantungan pada backend Citya (ADR-011) di RENCANA_FASE1.

Acuan: Dok 4 bagian 2–7 (kontrak), Dok 2 (CP, QC, KK, NB), Dok 5 (KT-01..06, UT-13, UT-14), KEPUTUSAN_BUILD (KB-10, KB-15, KB-18, KB-27..35, KK-A1, KK-F, KK-M, KK-N), TODO Langkah 1, 2, 4, 5, 6, dan 7 di RENCANA_FASE1.

Penanda: **[Dok]** = sudah ditetapkan dokumen/keputusan; **[Usul]** = usulan rencana ini, perlu persetujuan.

---

## 0. Prinsip yang dijaga

- Arah dependensi: `tools → services → adapters/storage`. Tool tidak memanggil httpx; satu `OpenDataJabarAdapter`, satu `DataCache`, dan satu `DictionaryCache` per proses (KB-26), disuntikkan lewat objek `ToolDeps`.
- Server tidak menulis ke SQLite (ADR-010): katalog dibuka `connect_read` (mode=ro).
- Tidak ada nilai kolom measure di respons tool mana pun (KT-03). Lapis pengaman model (KB-15) tetap aktif.
- Galat tampil sebagai galat: `status=error` → `result=null`, pesan dari templat (KB-10).
- Log per tool call: `request_id`, tool, slug, durasi, status, kode galat, ukuran respons, `served_from`, `match_mode` (search). Tidak ada isi baris, sig, atau nilai filter.

---

## 1. Alur tiap tool

### 1.1 `search_datasets` — katalog saja, tidak pernah ke portal [Dok]

1. Validasi `SearchParams` (konteks `search_max_page_size`). Gagal → `INVALID_PARAM`.
2. `services.catalog.search(conn_ro, params)`. `CatalogNotReady` → `INTERNAL` (pesan templat).
3. `total = 0` → `NO_MATCH`.
4. Bentuk `SearchResult` (sudah ada di models). Pangkas bila melewati anggaran token (bagian 6).
5. **[Usul]** Bila `catalog_synced_at` lebih tua dari 2 × `MCP_CATALOG_SYNC_MIN_INTERVAL_H` → notice `CATALOG_STALE` (TODO Langkah 5).

Permintaan ke portal: **0**.

### 1.2 `get_dataset_info` — katalog + cache kamus; portal hanya bila kamus belum ada

1. Validasi `InfoParams` → `INVALID_PARAM`.
2. Baca record dari katalog (fungsi baru `catalog.get_dataset(conn, slug)`). Tidak ada → **`DATASET_NOT_FOUND` tanpa memanggil portal**.
3. Cari `DictionaryCache.get(slug, data_updated_at=katalog.data_updated_at, override_digest=...)`.
   - **Hit** → `served_from=cache`, **0 permintaan**.
   - **Miss** → satu permintaan `/bigdata/{schema}/{table}?page=1&per_page=MCP_DICTIONARY_SAMPLE_ROWS` (**[Usul]** bawaan 100). Hasilnya: nama kolom, `metadata_filter`, sampel baris, dan `pagination.total_data`. Lalu `build_dictionary(...)` dengan override (`storage.overrides.read_override` + `parse_override`), dan simpan di `DictionaryCache`. `served_from=live`.
   - **[Usul]** Metode adaptor baru `probe(dataset, per_page) -> (ColumnProbe, RowPage)`: satu GET menghasilkan probe dan sampel sekaligus. Saat ini `get_columns` (per_page=1) + `query_rows` = 2 permintaan. Lihat KK-O.
4. Isi yang ikut disimpan di cache kamus (**[Usul]** KB-36): `DictionaryResult.to_dict()`, `periods_available` (dari `metadata_filter["tahun"]`, yang memuat seluruh nilai unik, bukan hanya sampel), dan `row_count` (dari `total_data`). Ketiganya berlaku untuk satu `data_updated_at`.
5. Portal gagal saat miss:
   - Ada kamus lama untuk `data_updated_at` sebelumnya → sajikan sebagai `STALE_DATA` (status `partial`, `served_from=stale_cache`). Butuh `DictionaryCache.get_any(slug)` (**[Usul]**).
   - Tidak ada → `SOURCE_UNAVAILABLE` / `RATE_LIMITED` (bagian 2).
6. Bentuk `InfoResult`. Deskripsi = `deskripsi_text` dipangkas ≤ `MCP_DESCRIPTION_MAX_CHARS` di batas kata. `columns` dari kamus (top_values ≤ `MCP_TOP_VALUES`). `dictionary_validated` beserta alasannya (bagian 5). `provenance` dari katalog (`data_updated_at`, `freshness_source`, `period_start/end` dari metadata) + `retrieved_at` = waktu kamus dibuat. `officialness` dari katalog.

Permintaan ke portal: **0** (hit) atau **1** (miss pertama per `data_updated_at`).

### 1.3 `get_dataset_data` — katalog + kamus + cache data; portal untuk seri

1. Validasi `DataParams` → `INVALID_PARAM`.
2. Katalog → `DATASET_NOT_FOUND` tanpa portal.
3. Kamus: alur yang sama dengan 1.2 langkah 3–5. Kamus dibutuhkan untuk validasi filter/sort dan peran kolom.
4. Validasi parameter terhadap kamus (bagian 3) → `INVALID_PARAM` / `LEVEL_NOT_AVAILABLE`.
5. **Cache dua tingkat** (**[Usul]** KB-37, untuk QC atas seri penuh; bagian 4):
   - **Seri, jalur lokal (bawaan)**: `QueryKey(slug, sort)`, **tanpa filter dan tanpa periods**. `DataCache.get_or_fetch(seri_key, fetch_seri, force_refresh, fallback_errors=(SourceUnavailable, SourceRateLimited))`. `fetch_seri` memanggil `query_rows(where=None, sort, per_page=1000)` per halaman. Baris dinormalisasi NB (bagian 4.3).
   - **Seri, jalur portal**: bila halaman pertama menunjukkan `total_data` > `MCP_SERIES_LOCAL_MAX_ROWS` (env baru, bawaan **5.000**; KB-37), seri diambil ulang dengan filter non-tahun sebagai `where` portal: `QueryKey(slug, filters, sort)`. Batas mutlak tetap `MCP_CACHE_MAX_ROWS` (50.000): lewat batas → berhenti setelah halaman pertama → `RESPONSE_TOO_LARGE`. Pilihan jalur dicatat di meta seri agar permintaan berikutnya langsung memakai jalur yang benar.
   - **Subset**: `QueryKey(slug, periods, filters, sort)`. `fetch_subset` tidak ke portal; ia menyaring baris seri dengan filter (jalur lokal), menghitung QC atas seri yang **sudah disaring** (semua tahun), lalu memotong ke periods yang diminta dan menulis berkas data_ref (isi Dok 4 bagian 6, tanpa `served_from`, KB-34). `FetchedData` diberi `retrieved_at` = `retrieved_at` seri (**[Usul]** perluasan kecil `DataCache`), agar TTL dan provenance mengikuti seri, bukan waktu pemotongan.
   - Tanpa periods dan tanpa filter → data_ref langsung ke versi seri (QC seri dihitung saat seri ditulis).
6. Hitung `periods_found` / `missing_periods` dengan `services.periods.compute(requested, available)`, dengan `available` = tahun di baris seri (setelah filter). Dataset tanpa kolom tahun → `time_dimension=false` (KK-F).
7. `quality_flags` = tanda QC seri yang `period`-nya ada di `periods_found` (atau semua bila periods tidak diminta).
8. `data_ref` = `DataRefSigner.make_data_ref(cache_id, row_count, now)`. Versi sudah ditandai dirujuk oleh `get_or_fetch` (KB-32 (8)).
9. `preview` = ≤ `MCP_PREVIEW_ROWS` baris pertama subset, **hanya kolom dimension**. `filters_applied` = filter efektif (bagian 3.4).
10. Status dan galat sesuai bagian 2. `refresh_throttled` → notice `REFRESH_THROTTLED`.

Permintaan ke portal, kasus khas (semua dataset rekaman ≤ 88 baris: jalur lokal, seri = 1 halaman):

| Kejadian | Jalur lokal (≤ 5.000 baris) | Jalur portal (> 5.000 baris) |
|---|---|---|
| Info pertama untuk slug | 1 | 1 |
| Data pertama untuk slug (kamus sudah ada) | 1 per sort | 1 halaman pertama + ceil(n/1000) per kombinasi filters+sort |
| Data lagi dengan periods lain, filter sama, dalam TTL | 0 | 0 |
| Data dengan filter berbeda, sort sama, dalam TTL | **0** | 1 + halaman berikutnya |
| `force_refresh` dalam 300 dtk | 0 (dibatasi, KB-03) | 0 |
| Data tanpa info lebih dulu | 2 (kamus + seri) | 2 + halaman berikutnya |

Satu seri dipakai bersama oleh semua filter dan periods dari slug + sort yang sama, sehingga pola tanya biasa (tren, lalu per kategori, lalu tahun tertentu) hanya memicu satu pengambilan per hari.

---|---|
| Info pertama untuk slug | 1 |
| Data pertama untuk slug (kamus sudah ada) | 1 per kombinasi filters+sort |
| Data lagi dengan periods lain, filter sama, dalam TTL | 0 |
| Data dengan filter berbeda | 1 |
| `force_refresh` dalam 300 dtk | 0 (dibatasi, KB-03) |
| Data tanpa info lebih dulu | 2 (kamus + seri) |

---

## 2. Pemetaan galat dan status

### 2.1 Kondisi → kode galat

| Kondisi | Tool | Kode | Status | Catatan |
|---|---|---|---|---|
| `ValidationError` parameter (Pydantic) | semua | `INVALID_PARAM` | error | `param` = segmen pertama `loc`; batas → klausa `limit` |
| Kunci `filters` bukan kolom dimension / kolom tidak ada | data | `INVALID_PARAM` | error | `param=filters`, `column=<kunci>` |
| Kolom `sort` tidak ada (atau kolom `id`) | data | `INVALID_PARAM` | error | `param=sort`, `column` |
| Nilai filter tidak cocok satu pun nilai yang dikenal (bagian 3.3) | data | `INVALID_PARAM` | error | `param=filters`, `column` |
| `filters.tahun` dan `periods` diisi bersamaan | data | `INVALID_PARAM` | error | `param=filters` (bagian 3.2) |
| Kolom wilayah (kecamatan/kelurahan/desa) tidak ada dan `tingkat_penyajian` tingkat kota | data | `LEVEL_NOT_AVAILABLE` | error | KK-A1; kota = teks diawali "kota" (563 "Kota Bogor", 82 "Kota") |
| Slug tidak ada di katalog lokal | info, data | `DATASET_NOT_FOUND` | error | **Tanpa memanggil portal** |
| `SourceNotFound` (404) padahal ada di katalog | info, data | `DATASET_NOT_FOUND` | error | Katalog usang; dicatat di log `catalog_mismatch` |
| `total = 0` | search | `NO_MATCH` | error | |
| periods diminta, `time_dimension=true`, `periods_found` kosong | data | `PERIOD_NOT_AVAILABLE` | error | Templat diperluas dengan klausa opsional `latest` (bagian 9) |
| `SourceUnavailable`, ada cache ≤ `MCP_CACHE_STALE_MAX_H` | info, data | `STALE_DATA` | **partial** | `served_from=stale_cache`, result terisi (KB-13) |
| `SourceUnavailable`, tanpa cache / lewat batas | info, data | `SOURCE_UNAVAILABLE` | error | |
| `SourceRateLimited`, ada cache ≤ batas | info, data | `STALE_DATA` | partial | Sama dengan baris stale |
| `SourceRateLimited`, tanpa cache / lewat batas | info, data | `RATE_LIMITED` | error | KB-32: galat asli |
| `InvalidSourceRequest` | info, data | `INVALID_PARAM` | error | `param=filters` (TODO Langkah 4); dicatat sebagai `source_rejected` |
| `SourceProtocolError` | info, data | `INTERNAL` | error | |
| `CacheTooLarge` / `total_data` > `MCP_CACHE_MAX_ROWS` | data | `RESPONSE_TOO_LARGE` | error | `limit` = batas baris |
| Masih > `MCP_MAX_RESPONSE_TOKENS` setelah semua pemangkasan | semua | `RESPONSE_TOO_LARGE` | error | `limit` = batas token |
| `CatalogNotReady`, `OverrideError`, `OverrideFileError`, `ConfigError` (secret), exception lain | semua | `INTERNAL` | error | Detail hanya di log |

### 2.2 Aturan status yang berhasil

| Kondisi (boleh lebih dari satu) | Status | `error` |
|---|---|---|
| Normal | `ok` | null |
| `missing_periods` tidak kosong **dan** `periods_found` tidak kosong | `partial` | null [Dok, TODO Langkah 2] |
| Disajikan dari cache usang | `partial` | `STALE_DATA` |
| Dipangkas karena anggaran token | `partial` | null + `truncated=true` + `truncated_reason` |
| `quality_flags` tidak kosong | tidak mengubah status | — (ADR-007) |
| `dictionary_validated=false` | tidak mengubah status | notice (bagian 5) |
| Dataset tanpa dimensi waktu, periods diminta | `ok` | notice `NO_TIME_DIMENSION` |
| Filter valid tetapi 0 baris (kolom teks bebas) | `ok`, `row_count=0` | notice `EMPTY_RESULT` |

Penjagaan ini ada di tool, bukan di amplop (TODO Langkah 2), dan diuji per baris tabel.

---

## 3. Validasi parameter terhadap kamus (TODO Langkah 1 dan 8)

1. **Kunci filters** harus nama kolom data dengan peran `dimension` menurut kamus. Kolom `measure`/`id`/tidak ada → `INVALID_PARAM` (`column`). Pengecualian: kolom wilayah tingkat bawah → `LEVEL_NOT_AVAILABLE` (tabel 2.1).
2. **`filters.tahun`** [Usul]: dipindah menjadi `periods`, karena model dan golden set (N-02, N-08) sering menulis tahun sebagai filter. Bila `periods` juga diisi → `INVALID_PARAM`.
3. **Nilai filter** [Usul]: dicocokkan ke nilai unik kolom (`metadata_filter`, dari cache kamus) dengan kunci NB-08 (`normalize_label(...)[0].casefold()`). Semua varian asli yang cocok dikirim ke `where`. Contoh: "hari kerja" → "HARI KERJA"; satu segmen bisa punya dua ejaan tanda hubung. Kolom dengan nilai unik diketahui tetapi tidak ada yang cocok → `INVALID_PARAM`. Kolom teks bebas (`metadata_filter` kosong) → dikirim apa adanya.
4. **`filters_applied`** [Usul]: filter efektif atas baris di data_ref, yaitu varian asli yang dikirim ke portal, ditambah `tahun` = `periods_requested` bila ada. Bentuknya mengikuti contoh Dok 4 (`{"tahun": [2025, 2026]}`), walau tahun dipotong lokal (bagian 4). Lihat KK-P.
5. **sort**: kolom harus ada dan bukan `id`. Bawaan `tahun:asc` hanya dipakai bila kolom `tahun` ada; bila tidak ada, sort tidak dikirim. `sort` eksplisit ke kolom yang tidak ada → `INVALID_PARAM`.
6. `filters` dan `sort` divalidasi **sebelum** permintaan seri, sehingga panggilan salah tidak menyentuh portal (kamus boleh diambil lebih dulu).

---

## 4. QC atas seri penuh (usulan Akhdan) dan dampaknya

### 4.1 Aturan
- QC dihitung atas **seri penuh** setelah filter (semua tahun) untuk setiap kolom measure: QC-01 dan QC-02 (KB-18, ambang `MCP_QC_RATIO`). QC-02 butuh ≥ 3 periode; memotong ke periode yang diminta lebih dulu akan menghilangkan tanda.
- Yang dilaporkan di respons dan berkas: tanda yang `period`-nya ∈ `periods_found` (semua tanda bila periods tidak diminta). Contoh: Q-01 diminta 2024 → `suspect_zero_period 2024`. N-02 diminta 2025 → tidak ada tanda, walau 2024 bertanda di seri.
- Tanda dihitung sekali per versi seri dan disimpan di meta/isi seri, bukan dihitung ulang per subset.
- Temuan baru (slug, kolom, periode, kode; **tanpa nilai**) dicatat di log `quality_finding` untuk QC-04 (`services.quality.findings_for_report`).

### 4.2 Dampak
- **QC tetap atas seri yang sudah disaring** (semua tahun, setelah filter). Q-01 dengan `HARI KERJA` tetap bertanda 2024.
- **Cache:** dua tingkat (1.3 langkah 5). Seri = 1 berkas per (slug, sort) di jalur lokal, atau per (slug, filters, sort) di jalur portal. Subset = berkas kecil per kombinasi filters + periods; disaring dan dipotong lokal tanpa portal. Ruang tambahan kecil (dataset rekaman ≤ 88 baris), dan GC KB-32 berlaku sama.
- **Permintaan:** seri tidak memakai `where` tahun (dan di jalur lokal tidak memakai `where` sama sekali), jadi satu permintaan bisa menarik lebih banyak baris. Namun hanya **1 pengambilan per (slug, sort) per TTL 24 jam** untuk dataset ≤ 5.000 baris, apa pun filter dan periods-nya. Seri besar dibatasi `MCP_SERIES_LOCAL_MAX_ROWS` (pindah ke filter portal) dan `MCP_CACHE_MAX_ROWS` (berhenti, `RESPONSE_TOO_LARGE`).
- **Konsistensi:** semua subset dari satu versi seri punya `retrieved_at`, `data_updated_at`, dan dasar QC yang sama.
- **Penyimpangan dari Dok 4:** periods tidak diterjemahkan ke `where` portal, dan filter hanya dikirim ke portal untuk seri > 5.000 baris (Dok 4 bagian 5). Dicatat sebagai KK-P.

### 4.3 Normalisasi baris seri
- NB-04: kolom `id` dibuang dari baris data_ref.
- NB-03/NB-07: kolom measure diurai sebagai number. Gagal → null; jumlah `ParseIssue` per kolom dicatat di log (tanpa nilai).
- NB-02/NB-06: teks kosong → null. NB-05: kolom kode apa adanya.
- NB-08 [Usul]: label kolom dimension diseragamkan di baris (`normalize_label`). Nilai aslinya disimpan di berkas sebagai `label_originals: {kolom: {label_normal: [nilai_asli, ...]}}` (hanya bila ada yang berbeda), agar baris tetap rata untuk backend. Lihat KK-Q.

---

## 5. Menyampaikan `dictionary_validated=false` (contoh nyata: realisasi PAD)

Tanpa angka: hanya nama kolom dan kode.

**[Usul] field amplop baru `notices: list[Notice]`** (KK-R; MINOR, karena konsumen wajib mengabaikan field yang tidak dikenal). `Notice = {code, message, column?}`. `message` diambil dari templat tetap seperti KB-10 (tanpa teks bebas, ditolak bila memuat angka berformat). Kode awal:

| Kode | Tool | Templat |
|---|---|---|
| `DICTIONARY_NOT_VALIDATED` | info, data | "Kolom nilai '{column}' tidak dijelaskan di deskripsi portal; artinya belum terverifikasi. Jangan membuat grafik dari kolom ini tanpa konfirmasi pengguna, dan jangan menebak artinya." |
| `NO_MEASURE_COLUMN` | info, data | "Dataset ini tidak punya kolom nilai yang dikenali; jangan membuat grafik tanpa konfirmasi pengguna." |
| `REFRESH_THROTTLED` | data | "force_refresh diabaikan karena data baru saja diambil; data dari cache yang masih segar." |
| `NO_TIME_DIMENSION` | data | "Dataset ini tidak punya kolom tahun; parameter periods tidak dipakai." |
| `EMPTY_RESULT` | data | "Filter valid, tetapi tidak ada baris yang cocok." |
| `CATALOG_STALE` | search | "Katalog lokal belum disinkron lebih dari dua hari; dataset terbaru mungkin belum tercantum." |
| `UNIT_CONFLICT` | info, data | "Kolom satuan berisi lebih dari satu nilai; satuan kolom '{column}' tidak ditetapkan." |

Contoh realisasi PAD (rekaman 10 Nov):
- `get_dataset_info`: `result.dictionary_validated=false`; kolom `total_pendapatan` dengan `role=measure`, `meaning=null`, `meaning_source=none`, `unit="RUPIAH"`; `notices=[{code: DICTIONARY_NOT_VALIDATED, column: "total_pendapatan", message: ...}]`. Status tetap `ok`.
- `get_dataset_data`: `result.dictionary_validated=false` (**[Usul]** field baru di `DataResult`, KK-R) + notice yang sama. Berkas data_ref memuat `"dictionary_validated": false` agar backend dapat menolak visual tanpa konfirmasi (KK-05).
- Override lengkap (KB-30 (8)) → `dictionary_validated=true` dan notice hilang.

---

## 6. Anggaran token `MCP_MAX_RESPONSE_TOKENS`

**Perkiraan konservatif tanpa tokenizer** [Usul] KB-38: `token ≈ ceil(byte_UTF8(JSON ringkas amplop) / 3)`. Teks Indonesia dan JSON umumnya 3,5–4,5 byte per token pada tokenizer BPE (Qwen/Llama). Pembagi 3 sengaja melebih-lebihkan sekitar 20–40%. Bawaan 2.000 token ≈ 6.000 byte. Pembagi dijadikan konstanta (`BYTES_PER_TOKEN = 3`), dan bisa diganti tokenizer asli setelah model dan `num_ctx` ditentukan.

Pemangkasan berurutan; setiap langkah diukur ulang, dan langkah pertama yang membuat respons muat dicatat sebagai `truncated_reason`. Nilainya kode tetap, tidak berisi angka.

| Tool | Urutan pemangkasan | `truncated_reason` |
|---|---|---|
| search | (1) kurangi `items` dari belakang; `total`, `page`, dan `page_size` tetap jujur, `page_size` diturunkan ke jumlah item | `items_dipangkas` |
| info | (1) `top_values` ≤ 5 per kolom; (2) `top_values` dihapus kecuali kolom `tahun`; (3) `description` ≤ 200 karakter; (4) `meaning` tiap kolom ≤ 120 karakter | `top_values_dipangkas`, `deskripsi_dipangkas`, `arti_kolom_dipangkas` |
| data | `columns` di data tidak membawa `top_values` sejak awal. (1) `preview` 5 → 2 → 0 baris; (2) `meaning` kolom ≤ 120 karakter; (3) `notices` diringkas menjadi satu per kode | `preview_dipangkas`, `arti_kolom_dipangkas`, `notices_dipangkas` |
| semua | Masih melebihi → `RESPONSE_TOO_LARGE` (`limit` = batas token) | — |

Yang tidak pernah dipangkas: `periods_*`, `quality_flags`, `data_ref`, nama dan peran kolom, `provenance`, `officialness`, `error`. Bila pemangkasan terjadi, status menjadi `partial` (KB-13).

---

## 7. Deskripsi ketiga tool (teks yang dibaca LLM) — final

Disetujui Akhdan 11 Nov 2026 dengan revisi berikut terhadap draf:
- search, NO_MATCH: "kata kunci itu tidak cocok dengan dataset mana pun; coba sekali lagi dengan kata yang lebih umum atau sinonim; bila tetap kosong, katakan data tidak tersedia di portal dan jangan memakai dataset lain yang mengukur hal berbeda".
- search: "jangan mengambil angka dari dokumen" dihapus, diganti "Angka statistik sektoral resmi Kota Bogor diambil lewat tool ini, bukan dari ingatanmu". Deskripsi MCP tidak mengatur alur RAG Citya, karena ADR-011 belum diputuskan.
- Contoh kueri "pendapatan asli daerah" → "PAD".
- Rujukan antar-tool memakai nama berawalan `MCP_TOOL_PREFIX` (placeholder `{search}`, `{info}`, `{data}` diisi saat `build_server`).
- data, `quality_flags`: "sertakan catatan mutu itu di narasi; periode yang bertanda tidak digambar sebagai nilai biasa, melainkan sebagai celah dengan catatan".

Teks lengkap: `src/mcp_bogor/tools/descriptions.py`. Dikunci tes `test_kt01_all_descriptions_snapshot` (`tests/contract/snapshots/tool_descriptions.json`) dan `test_descriptions_carry_agreed_rules`. Perubahan berikutnya harus disetujui Akhdan dan diuji ulang dengan golden set.

---

## 8. Rencana tes

Semua tanpa jaringan: `FakeSourceAdapter` + fixture rekaman 10 Nov, katalog dibangun dari `catalog_slim.json` lewat `sync_worker.sync_once` ke `tmp_path`.

| Tes | Berkas | Isi |
|---|---|---|
| **KT-01** | `tests/contract/test_tool_schemas.py` + `tests/contract/snapshots/tool_schemas.json` | JSON Schema input (dan output) ketiga tool dari FastMCP dibandingkan dengan snapshot. Perubahan = tes gagal; snapshot diperbarui sengaja dengan `--update-snapshots`, lalu direview Akhdan |
| **KT-02** | `tests/contract/test_envelope_paths.py` | Setiap jalur (ok, partial ×3, setiap kode galat) menghasilkan amplop yang lolos `ToolEnvelope`: field wajib ada, `result=null` saat error, `STALE_DATA` ⇔ `stale_cache` |
| **KT-03** | `tests/contract/test_no_values.py` | Dua lapis, untuk 12 dataset rekaman × beberapa parameter. (a) **Struktural**: tidak ada field respons yang membawa nilai kolom measure. Preview dan `filters_applied` hanya kolom dimension, `top_values` hanya di dimension, dan model menolak sebaliknya. (b) **Pencarian nilai khas measure** di JSON respons info dan data: nilai measure dari baris asli yang **punya desimal**, **≥ 3 digit**, atau **berbentuk angka berformat Indonesia** ("9.160.885", "1.234,5"), dalam bentuk mentah maupun berformat. Bilangan kecil mentah (mis. 0, 1, 46) **tidak dicari**, karena wajar muncul sebagai `row_count`, `page`, atau tahun; risikonya ditutup oleh lapis (a). Aturan dijelaskan di docstring tes. Pesan galat dan notice juga diperiksa tanpa angka berformat |
| **KT-04** | `tests/contract/test_error_codes.py` | Setiap dari 10 kode dipicu lewat tool nyata, dan `retryable` sesuai `ERROR_SPECS` |
| **KT-05** | `tests/contract/test_tool_names.py` | Nama terdaftar = `MCP_TOOL_PREFIX` + nama dasar; prefix lain lewat settings |
| **KT-06** | `tests/contract/test_data_ref_content.py` | Unduh data_ref lewat TestClient file server: rows = `row_count`, `columns` dan `provenance` = respons tool tanpa `served_from` (KB-34), `quality_flags` dan `periods_*` sama |
| **UT-13** | `tests/unit/test_budget.py` | Setiap langkah pemangkasan per tool memicu `truncated=true` + `truncated_reason` yang benar; tak bisa dipangkas → `RESPONSE_TOO_LARGE` |
| **UT-14** | `tests/unit/test_tools_source_failure.py` | `fail_with(SourceUnavailable)` tanpa cache → `SOURCE_UNAVAILABLE` tanpa angka; dengan cache usang → `STALE_DATA` partial; lewat 168 jam → `SOURCE_UNAVAILABLE`; 429 → `RATE_LIMITED`; 404 → `DATASET_NOT_FOUND` |
| Tabel bagian 2 | `tests/unit/test_tools_data.py`, `test_tools_info.py`, `test_tools_search.py` | Satu tes per baris tabel 2.1/2.2, validasi bagian 3, dua tingkat cache, jumlah permintaan ke fake (tabel 1.3), single-flight, `REFRESH_THROTTLED` |
| End-to-end MCP | `tests/contract/test_mcp_e2e.py` | `mcp.shared.memory.create_connected_server_and_client_session` (SDK 1.30): `list_tools` (nama, deskripsi, schema), `call_tool` ketiga tool, isi respons diparse sebagai amplop. Butuh `server.build_server(settings, deps)` di Langkah 8; transport dan lifespan tetap di Langkah 9 |
| **Golden set level tool** | `tests/contract/test_golden_tools.py` | Lihat tabel di bawah |

**Golden set v0.2 di level tool.** Status golden set adalah status jawaban X-Assistant, jadi dipetakan ke hasil tool yang diharapkan:

| Kasus | Panggilan | Yang dicocokkan |
|---|---|---|
| N-01 | data `periods=[2026]` | `PERIOD_NOT_AVAILABLE`; info `periods_available` ∋ 2025 (`answer_period`) |
| N-02 | data `periods=[2025]`, tanpa filter | ok, found `[2025]`, missing `[]`, tanpa tanda (2024 bertanda di seri, tidak diminta) |
| N-03 | data tanpa periods | ok; found ⊇ golden `periods_found`; tanpa tanda; `dictionary_validated=false` + notice (kolom total_pendapatan, TM-01) |
| N-04, N-06 | data `periods=[2025]`, `[2024]` | ok, found = diminta |
| N-05, N-07 | data tanpa periods | ok, found ⊇ golden |
| N-08 | data `filters={"tahun":[2023,2025],"hari_operasional":["HARI KERJA"]}` | ok, found `[2023, 2025]`; `filters_applied` memuat HARI KERJA (uji juga pemindahan `filters.tahun` → periods) |
| F-02 | `fail_with(SourceUnavailable)`, cache kosong | `SOURCE_UNAVAILABLE`, tanpa angka |
| F-03 | cache dipanaskan, jam maju > 24 jam, portal gagal | `partial` + `STALE_DATA` + `served_from=stale_cache` |
| F-07 | data `periods=[2025, 2026]` | partial, found `[2025]`, missing `[2026]` |
| F-08 | data `periods=[2019..2025]` | partial, found `[2025]`, missing `[2019..2024]` |
| O-06 | data kemiskinan `filters={"kecamatan":[...]}` | `LEVEL_NOT_AVAILABLE` |
| Q-01 | data kecepatan `filters={"hari_operasional":["HARI KERJA"]}` | `quality_flags` = `suspect_zero_period` 2024 |
| Q-02 | data persentase PAD tanpa periods | `suspect_scale` 2021, 2022, 2023 |
| Q-03 | data retribusi tanpa periods | `suspect_outlier` 2022 |
| F-01, O-01..O-05 | search dengan kata kunci pertanyaan | Amplop sah dan tanpa angka. Hasilnya (NO_MATCH atau tidak) **dicatat**, tidak di-assert, karena keputusan "tidak tersedia" ada di LLM (evaluasi golden set ST) |
| F-04, F-05, F-06, O-07 | — | Tidak bisa diuji di level tool (perilaku LLM); ditandai `skip` dengan alasan |

Cakupan target: ≥ 90% `tools/` (kriteria Fase 1 ≥ 80%).

---

## 9. KB/KK baru yang diusulkan

| ID | Isi singkat |
|---|---|
| KB-36 | Cache kamus memuat juga `periods_available` dan `row_count` per `data_updated_at`; env baru `MCP_DICTIONARY_SAMPLE_ROWS` (100); `DictionaryCache.get_any` untuk STALE_DATA di info |
| KB-37 | Cache dua tingkat seri/subset. Seri tanpa filter (kunci slug + sort) selama `total_data` ≤ `MCP_SERIES_LOCAL_MAX_ROWS` (env baru, 5.000); lewat ambang → filter non-tahun ke portal. `FetchedData.retrieved_at` opsional. QC atas seri yang sudah disaring. Seri dihentikan setelah halaman pertama bila `total_data` > `MCP_CACHE_MAX_ROWS` |
| KB-38 | Anggaran token: `ceil(byte/3)`; urutan pemangkasan dan kode `truncated_reason` (bagian 6) |
| KB-39 | Validasi filter: `filters.tahun` → periods; pencocokan nilai NB-08 ke `metadata_filter`; nilai tak dikenal → INVALID_PARAM; sort bawaan hanya bila kolom tahun ada |
| KB-40 | `source_url` = `{MCP_SOURCE_BASE_URL tanpa /api}/dataset/{slug}` (mengikuti contoh Dok 4). Terverifikasi Akhdan 11 Nov 2026 |
| KB-41 | `PERIOD_NOT_AVAILABLE`: klausa opsional "Tahun terakhir yang tersedia: {latest}." (tahun boleh di pesan, KB-10) |
| KK-O | `SourceAdapter` ditambah metode `probe(dataset, per_page)` (satu GET untuk probe + sampel); `get_columns` tetap ada |
| KK-P | Periods dipotong lokal dari seri (bukan `where` portal, Dok 4 bagian 5); `filters_applied` = filter efektif termasuk tahun |
| KK-Q | NB-08 di berkas data_ref: label diseragamkan di baris; nilai asli di `label_originals` |
| KK-R | Field amplop baru `notices` (templat tetap, tanpa angka) dan `DataResult.dictionary_validated`; berkas data_ref memuat `dictionary_validated`. Naik MINOR tetapi tetap `contract_version` 0.1 (belum dirilis) |

## 10. Perkiraan berkas yang berubah

Baru:
- `src/mcp_bogor/tools/common.py`: `ToolDeps`, penangkap galat → amplop, log tool, anggaran token
- `src/mcp_bogor/tools/search.py`, `info.py`, `data.py`, `descriptions.py`
- `src/mcp_bogor/models/notices.py` (atau di `envelope.py`)
- `src/mcp_bogor/services/dataset.py`: penyusun info/seri/subset yang murni (validasi filter, potong periods, saring tanda, preview)
- `src/mcp_bogor/services/budget.py`: perkiraan token dan pemangkasan
- Tes: `tests/contract/` (test_tool_schemas, test_envelope_paths, test_no_values, test_error_codes, test_tool_names, test_data_ref_content, test_mcp_e2e, test_golden_tools, snapshots/), `tests/unit/` (test_tools_search, test_tools_info, test_tools_data, test_tools_source_failure, test_budget, test_dataset_service)

Diubah:
- `src/mcp_bogor/server.py`: `build_server(settings, deps)` mendaftarkan tool (transport di Langkah 9)
- `src/mcp_bogor/models/envelope.py` (`notices`), `results.py` (`DataResult.dictionary_validated`), `errors.py` (klausa `latest`), `params.py` (TODO Langkah 8 dicabut)
- `src/mcp_bogor/services/catalog.py` (`get_dataset`), `services/quality.py` (bila perlu filter per periode)
- `src/mcp_bogor/adapters/base.py`, `adapters/opendata_jabar.py`, `tests/fakes.py` (`probe`)
- `src/mcp_bogor/storage/cache.py` (`FetchedData.retrieved_at`, `DictionaryCache.get_any`)
- `src/mcp_bogor/config.py`, `.env.example` (`MCP_DICTIONARY_SAMPLE_ROWS`)
- `docs/KEPUTUSAN_BUILD.md`, `docs/RENCANA_FASE1.md`

Perkiraan ukuran: ±1.200–1.500 baris kode + ±1.500 baris tes. Usulan pembagian commit: **8a** search + kerangka tool/amplop/notice/anggaran + KT-01/05; **8b** info + kamus/probe; **8c** data + cache dua tingkat + KT-03/06 + golden set + e2e.
