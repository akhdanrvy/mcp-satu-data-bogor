# Rencana Fase 1 — Build MCP Kota Bogor

Turunan dari Handoff Fase 1 (6 Nov 2026) dan Dok 3–5. Kerjakan berurutan; tiap langkah selesai bila tesnya hijau dan sudah di-commit. Centang di sini saat selesai.

| # | Langkah | Isi | Tes / bukti selesai |
|---|---|---|---|
| 0 | Kerangka repo | `pyproject.toml` (hatchling, src layout, Python ≥3.11), `uv.lock`, `.gitignore`, `.env.example`, `config.py` (semua env var Dok 3 bagian 6 + Dok 4 bagian 9, ditambah `MCP_TRANSPORT`, `MCP_HTTP_PORT`=8800, `MCP_FILESERVER_PORT`=8799, `MCP_DB_PATH`, `MCP_CACHE_DIR`), modul kosong sesuai struktur, konfigurasi pytest (marker `smoke` tidak ikut bawaan), ruff | `uv run pytest` jalan; tes kecil memastikan `sqlite3` punya FTS5 |
| 1 | Model dan amplop | `models.py`: `ToolEnvelope`, `Provenance`, `Officialness`, `QualityFlag`, `DataRef`, `ColumnInfo`, enum 9 kode galat + `retryable` | KT-02 (sebagian), KT-04 (tabel kode) |
| 2 | Service murni | `normalize` (NB-01..08), `freshness` (KS-01..05), `officialness`, `periods` (CP-01..04), `quality` (QC-01..03; QC-04 log terpisah) | UT-01, 02, 03, 04, 05, 07, 08, 09 |
| 3 | Fixture | Fixture buatan (portal mati, 429, 404, `+07:07`, "9.160.885", nilai kosong). Skrip `scripts/record_fixtures.py` untuk merekam respons asli (halaman 1 & terakhir katalog, detail + `/bigdata` 12 kartu metrik + 3 dataset bermasalah), dengan jeda 0,3 dtk dan username dibuang | **Perekaman dijalankan Akhdan, atas izin** |
| 4 | Adaptor | `adapters/base.py` (`SourceAdapter` Protocol, 5 metode), `adapters/opendata_jabar.py` (httpx, jeda, backoff, paginasi, 404 → NotFound, `health()`) | Tes kontrak adaptor dengan `respx`; tidak ada panggilan ke `/view` |
| 5 | Katalog | `storage/db.py` (SQLite WAL, FTS5), `services/catalog.py` (sanitasi kueri, filter opd/topik/tahun), `sync_worker.py --once` | UT-10, UT-12 |
| 6 | Kamus kolom | `services/dictionary.py` (urai template, validasi ke kolom asli, peran dimension/measure/id, status documented/undocumented/orphan), `overrides/` | UT-06 |
| 7 | Cache dan file server | `storage/cache.py` (berkas per kueri + meta, TTL 24 jam, stale), `fileserver/app.py` (HMAC, 200/403/410/404/503) | UT-11, KT-06 |
| 8 | Tiga tool | `tools/search.py`, `tools/info.py`, `tools/data.py`; awalan `MCP_TOOL_PREFIX`; batas token dan `truncated`; galat portal mati / stale | KT-01 (snapshot JSON Schema), KT-03, KT-05, UT-13, UT-14 |
| 9 | Server | `server.py` + `__main__.py`: stdio dan streamable-http :8800, file server :8799 | Uji manual dengan MCP Inspector (dicatat di README) |
| 10 | Kartu metrik | 12 berkas YAML di `metric_cards/` (field Dok 4 bagian 8); slug dan kolom diisi dari katalog asli, lalu **diperiksa Akhdan** | Validasi skema kartu |
| 11 | Smoke test | `tests/smoke/` ST-01..05, maks 60 permintaan, jeda 0,3 dtk | **Dijalankan Akhdan di laptop, atas izin** |
| 12 | Dokumentasi | `README.md` (instal, jalan, konfigurasi), `docs/RUNBOOK.md` (sinkron, rotasi secret, portal mati, rekam ulang fixture) | — |

## TODO dari review Langkah 1 (10 Nov 2026)

- [x] **Langkah 2 (UT-01):** offset `+07:07` dari portal dinormalkan dengan `replace(tzinfo=WIB)`, sehingga jam dinding tidak bergeser (bukan `astimezone`). Setelah itu, perbarui tes `test_plus_0707_not_normalized_here` di `tests/unit/test_models.py`, yang saat ini sengaja mengunci pergeseran 7 menit di model.
- [x] **Langkah 6:** peran kolom ditentukan setelah urai NB-07. Kolom string yang nilainya hampir semua angka (selain `kode_*` dan `tahun`) diberi peran `measure`. Ini menutup kebocoran KT-03 lewat `preview`/`top_values` (contoh KM-08 "9.160.885"). Pengaman model KB-15 tetap ada sebagai lapis kedua. Penentuan peran memakai `ParseIssue` dari `services/normalize.normalize_rows`.
- [ ] **Langkah 8:** bila `missing_periods` tidak kosong (dan ada periode yang ditemukan), status = `partial`. Dijaga di tool, bukan di amplop.

## TODO dari Langkah 2 (10 Nov 2026)

- [x] **Langkah 3:** dari fixture asli, verifikasi (a) nama kolom id internal `/bigdata` untuk `INTERNAL_ID_COLUMNS` (KB-17); (b) bentuk `metadata_filter` dan `metadata[]` (KB-20); (c) nilai `type` di `history_draft`; (d) apakah ada angka desimal bertitik bertipe teks (risiko KB-16).
- [x] **Langkah 4:** adaptor meratakan `metadata_filter` → `{kolom: nilai_unik}` dan `metadata[]` → `{nama: nilai}` sebelum memanggil `services/periods` (KB-20).
- [ ] **Langkah 8:** tool membaca `MCP_QC_RATIO` dari settings dan meneruskannya ke `services/quality` (KB-18); `PeriodCoverage.note` disampaikan ke model untuk CP-03 (KK-F).
- [ ] **Dok 2:** usulkan revisi QC-02 sesuai KB-18, AK-01 menjadi enam endpoint sesuai KK-H, KK-04 sesuai KK-I, dan jumlah dataset 757 → 751 (rekaman 10 Nov 2026) (di Google Docs, lalu ekspor ulang).

## TODO dari Langkah 3 (10 Nov 2026)

- [ ] **Akhdan, sebelum perekaman:** ~~konfirmasi KK-H~~ (disetujui); isi `MCP_USER_AGENT` di `.env` dengan kontak tim (bukan placeholder); jalankan `uv run python scripts/record_fixtures.py` (dry-run), lalu `--execute`.
- [x] **Setelah perekaman:** baca `docs/FIXTURE_REPORT.md`, putuskan KB-17 dan KB-20, pilih slug KM-07/KM-12 bila kandidatnya lebih dari satu, dan tinjau temuan email/telepon sebelum commit `tests/fixtures/recorded/`.

## TODO dari review fixture (10 Nov 2026)

- [ ] **Akhdan:** jalankan `uv run python scripts/record_fixtures.py --only persentase-penanganan-pengaduan-masyarakat-ke-inspektorat-daerah-kota-bogor --execute` (KM-12; 2 permintaan, berkas 034 dan 035).
- [x] **Akhdan:** putuskan redaksi `organisasi_alamat`/`alamat` di fixture (KK-J: diredaksi).
- [x] **Langkah 6 (KK-I):** `services/dictionary.column_role` dan perluasan `is_numeric_label_column`; cabut penanda xfail di `tests/unit/test_column_roles.py`.
- [x] **Langkah 5:** sinkron katalog memakai field `catalog_slim` (KK-J: tanpa kontak); uji dengan `catalog_slim.json`.
- [x] **Langkah 5 (dataset hilang):** portal bisa menghapus dataset (757 pada 5 Nov → 751 pada 10 Nov 2026). Sinkron katalog penuh harus menghapus dari katalog lokal (tabel + indeks FTS5) setiap record yang tidak lagi ada di hasil sinkron; record itu tidak boleh tetap tersaji di `search_datasets`. Penghapusan hanya dilakukan bila sinkron selesai lengkap (semua halaman terbaca, jumlah = `pagination.total_data`); sinkron gagal/terpotong tidak boleh menghapus apa pun. Tes: katalog lama 3 record, sinkron baru 2 record → record ketiga hilang dari tabel dan FTS5; sinkron terpotong → tidak ada yang dihapus.
- [ ] **Langkah 10:** slug KM-07 = `persentase-kepala-keluarga-miskin-di-kota-bogor`, KM-12 = `persentase-penanganan-pengaduan-masyarakat-ke-inspektorat-daerah-kota-bogor`.

## TODO dari Langkah 4 (10 Nov 2026)

- [x] **Langkah 5:** `sync_worker` memanggil `list_datasets()` lewat `asyncio.run`; hapus record lokal yang tidak ada di snapshot **hanya bila** `snapshot.complete` (KK-K). Uji dengan `FakeSourceAdapter.remove(slug)` dan snapshot tidak lengkap dari adaptor (respx).
- [x] **Langkah 5:** peta `opd`/`topik` di katalog memakai `list_organisasi()`/`list_topik()` (KK-H).
- [x] **Langkah 5 (KB-26):** `sync_worker` membuat **satu** instans `OpenDataJabarAdapter` untuk seluruh proses, dengan env baru `MCP_SYNC_REQUEST_DELAY_S` (bawaan 0,5, minimal 0,2) sebagai jeda antarpermintaan; tambahkan ke `config.py`, `.env.example`, dan tes config.
- [x] **Langkah 5 (KK-K f):** laporan sinkron memuat `pages_read`, `total_data`, jumlah record, `duplicates`, `skipped`, `complete`, `error`, dan jumlah record yang dihapus.
- [ ] **Langkah 8:** petakan galat adaptor ke `ErrorCode`: `SourceNotFound` → `DATASET_NOT_FOUND`; `SourceUnavailable` → `SOURCE_UNAVAILABLE` (atau `STALE_DATA` bila cache ada); `SourceRateLimited` → `RATE_LIMITED`; `InvalidSourceRequest` → `INVALID_PARAM`; `SourceProtocolError` → `INTERNAL`. Pakai `FakeSourceAdapter.fail_with` untuk UT-14.
- [ ] **Langkah 9:** konfigurasi handler log JSON (`mcp_bogor.logging`) untuk server; tutup adaptor (`aclose`) saat server berhenti.
- [ ] **Langkah 9 (KB-26):** server membuat **satu** instans `OpenDataJabarAdapter` saat start (lifespan FastMCP) dan memakainya bersama untuk semua tool call; jangan membuat adaptor per permintaan, karena pembatas laju dan jeda global 429 (KB-25) hanya berlaku per instans.
- [x] **Akhdan:** putuskan KK-L (kolom `alamat`/`email` di baris data publik): disetujui.

## TODO dari Langkah 5 (10 Nov 2026)

- [ ] **Akhdan:** jalankan `uv run python -m mcp_bogor.sync_worker --once` (±10 permintaan, jeda 0,5 dtk). Kode keluar 0 = complete/incomplete/skipped, 1 = failed, 2 = galat konfigurasi.
- [x] **Akhdan:** putuskan KB-28 (ulang sinkron lebih cepat setelah gagal) dan KB-29 (perbaikan relevansi; dua kasus golden set xfail).
- [x] **Akhdan:** setujui atau tolak dependensi PySastrawi (KB-29.2): disetujui; `pysastrawi==1.2.1` ditambahkan, stem hanya di nama, skema v3.
- [ ] **Akhdan:** tinjau aturan indeks stem KB-29 (a) tanpa hitung ganda dan (b) awalan pe-/per- tidak di-stem.
- [ ] **Langkah 10 (KB-29.4):** sinonim kartu metrik (mis. "miskin" → KM-06, "PAD" → KM-01) dicek sebelum FTS.
- [ ] **Langkah 10 (KB-29):** sinonim KM-01 untuk "pendapatan asli daerah" (kasus relevansi N-03 yang masih xfail); setelah lulus, cabut xfail di `tests/unit/test_catalog_search.py`.
- [ ] **Catatan migrasi:** katalog lokal skema v1 (bila sinkron sudah dijalankan) otomatis dimigrasikan ke v2 dan FTS-nya dibangun ulang saat `sync_worker` berikutnya membuka DB.
- [ ] **Langkah 8:** `search_datasets` memakai `storage.db.connect_read` + `services.catalog.search`; catat `match_mode` di log tool; `CatalogNotReady` → galat INTERNAL dengan pesan templat (katalog belum disinkron).
- [ ] **Langkah 8:** peringatan katalog usang bila `catalog_synced_at` lebih tua dari `MCP_CATALOG_SYNC_MIN_INTERVAL_H` × 2.
- [ ] **Langkah 9:** mode terjadwal `sync_worker` (loop/cron) memakai `sync_once` yang sama; satu adaptor per proses (KB-26).
## TODO dari Langkah 6 (11 Nov 2026)

- [x] **Akhdan:** setujui atau ubah KB-30 dan KK-M (disetujui 11 Nov 2026; override memvalidasi measure hanya bila memuat meaning, unit, reviewed_by, reviewed_at).
- [x] **Akhdan:** kolom `bulan` di aturan (b): diputuskan `bulan`, `triwulan`, `semester` → dimension (11 Nov 2026).
- [ ] **Akhdan:** override `total_pendapatan` untuk `realisasi-pendapatan-asli-daerah-pad-kota-bogor` (meaning, unit, reviewed_by, reviewed_at), **setelah** artinya diverifikasi di portal atau ke Bapenda. Belum dibuat; sampai itu kamus dataset ini `dictionary_validated=false`. Lihat `docs/TEMUAN_MUTU_DATA.md` TM-01.
- [ ] **Akhdan:** tentukan saluran pelaporan `docs/TEMUAN_MUTU_DATA.md` ke Diskominfo/OPD (QC-04, [Belum ditentukan] di Dok 2).
- [ ] **Langkah 7–8 (KK-08):** jalankan `build_dictionary` saat dataset pertama kali diminta (probe + halaman pertama /bigdata), simpan hasil di cache; baca override lewat `storage.overrides.read_override` + `services.dictionary.parse_override`.
- [ ] **Langkah 8 (KK-05):** `dictionary_validated=false` → peringatan di `get_dataset_info` dengan alasan (`reasons`), dan larangan visual tanpa konfirmasi; `OverrideError`/`OverrideFileError` → galat INTERNAL (berkas kurasi rusak), dicatat di log.
- [ ] **Langkah 8:** `preview` dan `filters_applied` memakai peran dari kamus; `top_values` dari `ColumnInfo` hasil kamus (sudah ≤ 10 dan tanpa angka berformat).
- [ ] **Dok 2/Dok 4:** usulkan revisi KK-04 (aturan a–f, termasuk bulan/triwulan/semester), statistik template (751 dataset, 5.811 baris), dan `meaning_source` (KK-M).
## TODO dari Langkah 7 (11 Nov 2026)

- [x] **Akhdan:** setujui atau ubah KB-32, KB-33, dan KK-N (disetujui 11 Nov 2026; `served_from` dihapus dari berkas data_ref, KB-34).
- [x] **Akhdan:** peringatan TestClient/httpx2: tidak menambah dependensi; diredam persis di `pyproject.toml` (KB-35).
- [ ] **Saat Starlette diperbarui:** cabut `filterwarnings` KB-35 di `pyproject.toml` dan cek ulang peringatan TestClient.
- [ ] **Langkah 8 (data):** `get_dataset_data` memakai `DataCache.get_or_fetch(QueryKey.build(...), fetch, force_refresh=..., fallback_errors=(SourceUnavailable, SourceRateLimited))`; `served_from=stale_cache` → status partial + `STALE_DATA`; `CacheTooLarge` → `RESPONSE_TOO_LARGE`; `refresh_throttled` disebut di respons. data_ref dari `DataRefSigner.make_data_ref`. Isi berkas mengikuti Dok 4 bagian 6 (KK-N).
- [ ] **Langkah 8 (info):** kamus lewat `DictionaryCache.get/put` dengan `data_updated_at` dari katalog dan `DictionaryOverride.digest()`; `DictionaryResult.from_dict` gagal → buat ulang.
- [ ] **Langkah 8:** pertimbangkan *negative cache* singkat setelah portal gagal agar permintaan beruntun dengan cache usang tidak terus memanggil portal (saat ini dibatasi oleh backoff dan jeda global adaptor saja).
- [ ] **Langkah 9:** jalankan file server (`fileserver.app.run`) bersama server MCP; lifespan membuat satu `DataCache` per proses.
- [ ] **Langkah 12 (RUNBOOK):** prosedur rotasi `MCP_DATAREF_SECRET` dengan `MCP_DATAREF_SECRET_PREVIOUS` (KB-33 (5)).
## TODO dari Langkah 8a (11 Nov 2026)

- [x] **8a:** search_datasets, kerangka tool (`tools/common.py`), amplop + `notices` (KK-R), anggaran token (KB-38), deskripsi tool final, `server.build_server`, KT-01 dan KT-05.
- [x] **Akhdan:** verifikasi pola `source_url` (KB-40): terverifikasi 11 Nov 2026.
- [x] **Akhdan:** KB-42 diganti: server low-level SDK dengan handler publik; `MCP_EXPOSE_OUTPUT_SCHEMA` (KB-42 revisi). Deskripsi tool disetujui dengan dua tambahan (notice DICTIONARY_NOT_VALIDATED di data; deskripsi `request_id`).
- [x] **8b:** get_dataset_info + `probe` (KK-O) + cache kamus (KB-36); terdaftar di `build_server`; snapshot KT-01 diperbarui (menunggu review Akhdan).
- [ ] **Akhdan:** review snapshot `tool_schemas.json` (tool info) dan setujui KB-43 (slug ≤ 255, placeholder 200 → 255).
- [x] **8c:** get_dataset_data + seri/subset (KB-37), validasi filter (KB-39), KB-41, KK-P, KK-Q, `DataResult.dictionary_validated`; KT-02..04, KT-06, UT-13, UT-14, golden set level tool, e2e. Anotasi tool: readOnly ketiganya; openWorld false (search) / true (info, data).
- [x] **Akhdan:** KB-44, KB-45, dan snapshot data disetujui (11 Nov 2026); tambahan KB-46 (angka di teks bebas disamarkan di respons; arti hanya untuk measure di data) dan asersi golden N-03.
- [ ] **Langkah 9:** `ToolDeps` dibuat di lifespan server (satu adaptor, satu `DataCache`, satu `DataRefSigner`, satu `DictionaryCache`); file server :8799 berjalan bersama.
- [ ] **Dok 2/Dok 4:** revisi Dok 4 bagian 2 (`notices`, KK-R) dan bagian 5 (periods/filter lokal, KK-P).

Kriteria lulus Fase 1 (Dok 5 bagian 5): unit + kontrak 100%; cakupan ≥ 80% di `services/` dan `tools/`; smoke 12 kartu 100%; KT-03 nol toleransi; nol respons 429.

**Catatan untuk lead technical (ADR-011):** tanpa perubahan backend Citya untuk mengambil `data_ref` (port 8799) dan menghitung titik grafik di server, Citya yang memakai MCP **tidak dapat menyebut angka** sama sekali, karena respons tool sengaja tidak memuat nilai measure. Ini disengaja (angka bukan dari LLM; ADR-005, KT-03) dan perlu disampaikan ke lead technical serta Pak Ridho sebelum integrasi.

**Pertanyaan untuk tim Chat Assistant (Pak Ridho):** apakah klien MCP Citya meneruskan `outputSchema` tool ke konteks model? Bila ya, skema amplop memboroskan token tiap giliran; set `MCP_EXPOSE_OUTPUT_SCHEMA=false` (KB-42 revisi).

Bergantung pada jawaban tim lain (buat bisa dikonfigurasi, jangan menunggu): ADR-011, klien MCP di Citya, perubahan `render_chart`, `num_ctx` (→ `MCP_MAX_RESPONSE_TOKENS`, awal 2.000), lokasi server dan jaringan port 8799.
