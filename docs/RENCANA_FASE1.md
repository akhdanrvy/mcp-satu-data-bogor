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
- [ ] **Langkah 6:** peran kolom ditentukan setelah urai NB-07. Kolom string yang nilainya hampir semua angka (selain `kode_*` dan `tahun`) diberi peran `measure`. Ini menutup kebocoran KT-03 lewat `preview`/`top_values` (contoh KM-08 "9.160.885"). Pengaman model KB-15 tetap ada sebagai lapis kedua. Penentuan peran memakai `ParseIssue` dari `services/normalize.normalize_rows`.
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
- [ ] **Langkah 6 (KK-I):** `services/dictionary.column_role` dan perluasan `is_numeric_label_column`; cabut penanda xfail di `tests/unit/test_column_roles.py`.
- [ ] **Langkah 5:** sinkron katalog memakai field `catalog_slim` (KK-J: tanpa kontak); uji dengan `catalog_slim.json`.
- [ ] **Langkah 5 (dataset hilang):** portal bisa menghapus dataset (757 pada 5 Nov → 751 pada 10 Nov 2026). Sinkron katalog penuh harus menghapus dari katalog lokal (tabel + indeks FTS5) setiap record yang tidak lagi ada di hasil sinkron; record itu tidak boleh tetap tersaji di `search_datasets`. Penghapusan hanya dilakukan bila sinkron selesai lengkap (semua halaman terbaca, jumlah = `pagination.total_data`); sinkron gagal/terpotong tidak boleh menghapus apa pun. Tes: katalog lama 3 record, sinkron baru 2 record → record ketiga hilang dari tabel dan FTS5; sinkron terpotong → tidak ada yang dihapus.
- [ ] **Langkah 10:** slug KM-07 = `persentase-kepala-keluarga-miskin-di-kota-bogor`, KM-12 = `persentase-penanganan-pengaduan-masyarakat-ke-inspektorat-daerah-kota-bogor`.

Kriteria lulus Fase 1 (Dok 5 bagian 5): unit + kontrak 100%; cakupan ≥ 80% di `services/` dan `tools/`; smoke 12 kartu 100%; KT-03 nol toleransi; nol respons 429.

Bergantung pada jawaban tim lain (buat bisa dikonfigurasi, jangan menunggu): ADR-011, klien MCP di Citya, perubahan `render_chart`, `num_ctx` (→ `MCP_MAX_RESPONSE_TOKENS`, awal 2.000), lokasi server dan jaringan port 8799.
