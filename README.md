# MCP Kota Bogor (satu-data-bogor)

Server [MCP](https://modelcontextprotocol.io) yang membaca portal **Satu Data Kota Bogor** (`opendata.kotabogor.go.id`) untuk **X-Assistant Citya AI Bogor**. Pengguna akhir jawabannya adalah Wali Kota dan pimpinan Pemkot Bogor. Portal memuat ±777 dataset per 17 Nov 2026; jumlahnya berubah seiring waktu.

Status: **Fase 1 selesai** (MCP diuji sendiri, tanpa model). Bukti dan sisa pekerjaan ada di [`docs/STATUS_FASE1.md`](docs/STATUS_FASE1.md).

Dokumen lain:
- [`docs/RUNBOOK.md`](docs/RUNBOOK.md): operasi harian (sinkron, portal mati, rotasi secret, cache, kartu metrik, log).
- [`docs/INTEGRASI_CITYA.md`](docs/INTEGRASI_CITYA.md): untuk tim Chat Assistant (menyambung klien, kontrak, data_ref).
- `docs/01..05`: dokumen acuan. Bila bertentangan, urutan prioritasnya Dok 4 > Dok 5 > Dok 3 > Dok 2 > Dok 1. Keputusan build yang menyimpang atau menambah ada di [`docs/KEPUTUSAN_BUILD.md`](docs/KEPUTUSAN_BUILD.md).

## Prinsip

1. **Angka tidak pernah lewat LLM** (ADR-005, KT-03). Tool hanya mengembalikan metadata, struktur kolom, tanda mutu, dan `data_ref`: tautan bertanda tangan yang berumur pendek ke berkas data. Nilai kolom ukuran (measure) tidak pernah ada di respons tool. Backend Citya yang mengambil berkas itu dan menghitung angka atau titik grafik.
2. **Galat tampil sebagai galat.** Respons `status=error` selalu `result=null`, dengan pesan bertemplat tetap tanpa angka.
3. **Tandai, jangan perbaiki** (ADR-007). Data janggal diserahkan apa adanya, disertai `quality_flags`.
4. **Akses portal sopan** (AK-01..06): hanya GET, jeda ≥ 0,2 dtk, backoff pada 429/5xx, tidak pernah memanggil `/view`, sinkron katalog penuh maks 1×/24 jam.
5. **Privasi:** akun petugas portal (`username` dan sejenisnya) serta kontak OPD tidak disimpan dan tidak muncul di respons atau log. Log tidak berisi isi baris data.

## Prasyarat

- Windows 10/11 atau Linux.
- [uv](https://docs.astral.sh/uv/). uv memasang **Python 3.12** otomatis dari berkas `.python-version`; `pyproject.toml` mensyaratkan Python ≥ 3.11.
- Akses internet ke `opendata.kotabogor.go.id`, hanya untuk sinkron katalog dan pengambilan data.
- Opsional: Node.js, untuk MCP Inspector (`npx`).

## Instalasi

```powershell
git clone <url-repo> satu-data-bogor
cd satu-data-bogor
uv sync                         # pasang dependensi (termasuk dev) ke .venv
copy .env.example .env          # Linux: cp .env.example .env
```

Isi minimal di `.env`:

| Variabel | Isi |
|---|---|
| `MCP_USER_AGENT` | Nama aplikasi dan kontak **tim**, mis. `mcp-bogor/0.1 (+<url-atau-surel-tim>)`. Bukan email pribadi (AK-05). Placeholder `<kontak-tim>` ditolak. |
| `MCP_DATAREF_SECRET` | Rahasia acak ≥ 32 byte. Buat dengan `uv run python -c "import secrets; print(secrets.token_urlsafe(48))"` |

`.env` di-gitignore dan tidak boleh di-commit. Tanpa uv, `pip install -e .` juga berjalan di virtualenv Python ≥ 3.11.

## Konfigurasi (semua env var)

Semua nilai dibaca dari env berawalan `MCP_` atau dari berkas `.env` (`src/mcp_bogor/config.py`). Path relatif di-resolve terhadap direktori kerja saat start. "Wajib" berarti harus diisi sebelum dipakai di produksi. Tes `tests/unit/test_docs.py` memastikan tabel ini memuat semua field `Settings`.

**Sumber data (AK-01..06)**

| Variabel | Bawaan | Arti | Wajib? |
|---|---|---|---|
| `MCP_SOURCE_BASE_URL` | `https://opendata.kotabogor.go.id/api` | Base URL API portal | Opsional |
| `MCP_USER_AGENT` | `mcp-bogor/0.1 (+<kontak-tim>)` | User-Agent ke portal; placeholder ditolak adaptor (AK-05) | **Wajib** (server HTTP dan sinkron menolak start) |
| `MCP_REQUEST_DELAY_S` | `0.2` | Jeda minimal antarpermintaan server ke portal (≥ 0,2) | Opsional |
| `MCP_HTTP_TIMEOUT_S` | `20` | Timeout HTTP ke portal (detik) | Opsional |
| `MCP_MAX_RETRIES` | `5` | Total percobaan per permintaan pada 429/5xx/koneksi (1..5) | Opsional |
| `MCP_BACKOFF_MAX_S` | `30` | Bila tunggu (Retry-After/backoff) melebihi ini, langsung gagal tanpa tidur (KB-24) | Opsional |

**Penyimpanan dan cache**

| Variabel | Bawaan | Arti | Wajib? |
|---|---|---|---|
| `MCP_DB_PATH` | `./var/mcp_bogor.db` | Katalog SQLite (FTS5, WAL); hanya ditulis `sync_worker` | Opsional |
| `MCP_CACHE_DIR` | `./var/cache` | Cache data, kamus kolom, dan berkas data_ref | Opsional |
| `MCP_OVERRIDES_DIR` | `./overrides` | Kurasi kamus kolom per slug (KK-07) | Opsional |
| `MCP_METRIC_CARDS_DIR` | `./metric_cards` | 12 kartu metrik YAML (Dok 4 bagian 8) | Opsional |
| `MCP_CACHE_TTL_H` | `24` | Umur cache data yang masih dianggap segar (jam) | Opsional |
| `MCP_CACHE_STALE_MAX_H` | `168` | Cache usang boleh disajikan (`STALE_DATA`) saat portal gagal, hingga umur ini (jam, ≥ TTL) | Opsional |
| `MCP_CACHE_MAX_MB` | `500` | Batas total ukuran cache data; GC menghapus versi tertua yang tidak dirujuk | Opsional |
| `MCP_CACHE_MAX_ROWS` | `50000` | Batas baris per berkas cache; lebih dari ini → `RESPONSE_TOO_LARGE` | Opsional |
| `MCP_DICTIONARY_SAMPLE_ROWS` | `100` | Baris sampel `/bigdata` untuk menentukan peran kolom (1..1000) | Opsional |
| `MCP_SERIES_LOCAL_MAX_ROWS` | `5000` | Seri ≤ ini diambil utuh lalu disaring lokal; di atasnya filter dikirim ke portal (KB-37) | Opsional |
| `MCP_CATALOG_SYNC_MIN_INTERVAL_H` | `24` | Jarak minimal antarsinkron katalog (≥ 24, AK-06) | Opsional |
| `MCP_CATALOG_SYNC_RETRY_AFTER_H` | `6` | Sinkron gagal/tidak lengkap (bukan 429) boleh diulang setelah ini (jam) | Opsional |
| `MCP_SYNC_REQUEST_DELAY_S` | `0.5` | Jeda antarpermintaan `sync_worker` (≥ 0,2; proses terpisah dari server) | Opsional |

**Transport dan file server**

| Variabel | Bawaan | Arti | Wajib? |
|---|---|---|---|
| `MCP_TRANSPORT` | `stdio` | `stdio` (pengembangan, Inspector) atau `streamable-http` (Citya) | Opsional |
| `MCP_HTTP_HOST` | `127.0.0.1` | Alamat bind server HTTP | Opsional |
| `MCP_HTTP_PORT` | `8800` | Port server HTTP (endpoint `/mcp`, `/healthz`) | Opsional |
| `MCP_HTTP_ALLOWED_HOSTS` | kosong | Host tambahan yang diizinkan (proteksi DNS rebinding), dipisah koma; `127.0.0.1:<port>` dan `localhost:<port>` selalu diizinkan | Wajib bila diakses dari mesin lain |
| `MCP_HTTP_ALLOWED_ORIGINS` | kosong | Origin yang diizinkan; kosong = permintaan ber-header Origin ditolak | Opsional |
| `MCP_HTTP_AUTH_TOKEN` | kosong | Bila diisi, `/mcp` wajib `Authorization: Bearer <token>` (`/healthz` dikecualikan) | Disarankan di produksi |
| `MCP_FILESERVER_HOST` | `127.0.0.1` | Alamat bind file server data_ref | Opsional |
| `MCP_FILESERVER_PORT` | `8799` | Port file server data_ref | Opsional |
| `MCP_FILESERVER_PUBLIC_URL` | `http://localhost:8799` | Awal URL di `data_ref.url`; harus bisa dijangkau backend Citya | **Wajib** bila Citya di mesin lain |

**data_ref (ADR-008)**

| Variabel | Bawaan | Arti | Wajib? |
|---|---|---|---|
| `MCP_DATAREF_SECRET` | kosong | Kunci HMAC tanda tangan data_ref, ≥ 32 byte | **Wajib** (server HTTP dan file server menolak start) |
| `MCP_DATAREF_SECRET_PREVIOUS` | kosong | Secret lama, hanya untuk verifikasi saat rotasi (KB-33) | Opsional |
| `MCP_DATAREF_TTL_SECONDS` | `900` | Umur tautan data_ref (detik) | Opsional |

**Kontrak dan batas (Dok 4 bagian 9)**

| Variabel | Bawaan | Arti | Wajib? |
|---|---|---|---|
| `MCP_TOOL_PREFIX` | `satudata_` | Awalan nama tool (KT-05) | Opsional |
| `MCP_EXPOSE_OUTPUT_SCHEMA` | `true` | Iklankan `outputSchema` amplop di `list_tools`; `false` bila klien meneruskannya ke model dan memboroskan token | Opsional |
| `MCP_MAX_RESPONSE_TOKENS` | `2000` | Anggaran token per respons; lewat batas → dipangkas (`truncated`) atau `RESPONSE_TOO_LARGE` | Opsional |
| `MCP_SEARCH_MAX_PAGE_SIZE` | `20` | Batas `page_size` search | Opsional |
| `MCP_TOP_VALUES` | `10` | Jumlah nilai unik kolom dimensi di `get_dataset_info` | Opsional |
| `MCP_PREVIEW_ROWS` | `5` | Baris preview (kolom dimensi saja) di `get_dataset_data` (0..5) | Opsional |
| `MCP_DESCRIPTION_MAX_CHARS` | `600` | Batas panjang deskripsi dataset di respons | Opsional |
| `MCP_INLINE_ROWS_FALLBACK` | `false` | Dicadangkan. Nilai `true` **ditolak saat start** ("tidak didukung di v0.1"): baris data tidak pernah masuk respons tool (KT-03) | Harus `false` |
| `MCP_FORCE_REFRESH_MIN_INTERVAL_S` | `300` | Jarak minimal `force_refresh` per slug + parameter | Opsional |

**Mutu data dan log**

| Variabel | Bawaan | Arti | Wajib? |
|---|---|---|---|
| `MCP_QC_RATIO` | `50` | Ambang rasio antarkelompok periode untuk QC-02 (KB-18) | Opsional |
| `MCP_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, atau `ERROR`; log JSON ke stderr | Opsional |

## Menjalankan

Semua perintah dari akar repo. Contoh memakai PowerShell; di Linux bentuknya sama kecuali cara menyetel env (`MCP_TRANSPORT=streamable-http uv run ...`).

**1. Sinkron katalog** (sekali sebelum server dipakai, lalu terjadwal 1×/hari; lihat RUNBOOK):
```powershell
uv run python -m mcp_bogor.sync_worker --once     # kode keluar 0 complete/incomplete/skipped, 1 failed, 2 konfigurasi
uv run python -m mcp_bogor.sync_worker --reindex  # bangun ulang FTS lokal, tanpa portal
```
Sinkron kedua dalam 24 jam dicatat `skipped` tanpa permintaan ke portal (AK-06).

**2. Server MCP, stdio** (bawaan; untuk MCP Inspector dan pengembangan):
```powershell
uv run python -m mcp_bogor
```

**3. Server MCP, streamable-http** (untuk Citya) di `MCP_HTTP_HOST:MCP_HTTP_PORT`, endpoint `/mcp` (stateless, respons JSON):
```powershell
$env:MCP_TRANSPORT="streamable-http"; uv run python -m mcp_bogor
```

**4. File server data_ref** (proses terpisah, wajib untuk `get_dataset_data`):
```powershell
uv run python -m mcp_bogor.fileserver
```
Server HTTP menolak start (kode keluar 2) bila `MCP_DATAREF_SECRET` atau `MCP_USER_AGENT` tidak sah; file server menolak start bila `MCP_DATAREF_SECRET` tidak sah. Mode stdio hanya memberi peringatan, dan tool yang membutuhkannya mengembalikan `INTERNAL`.

**5. Pemeriksaan kesehatan:**
- `GET http://127.0.0.1:8800/healthz`: versi, `contract_version`, status katalog, dan hasil cek portal terakhir. Tidak pernah memanggil portal. 200 bila katalog siap, selain itu 503.
- `GET /healthz?deep=1` dengan header Bearer yang benar: ikut mengecek portal (di-cache 60 dtk). Hanya aktif bila `MCP_HTTP_AUTH_TOKEN` diisi.
- `GET http://127.0.0.1:8799/healthz`: versi dan keterbacaan folder cache.

**6. MCP Inspector:**
```powershell
npx @modelcontextprotocol/inspector uv run python -m mcp_bogor
```
Untuk HTTP: jalankan langkah 3 dan 4, lalu di Inspector pilih transport "Streamable HTTP" dengan URL `http://127.0.0.1:8800/mcp` (tambahkan header Authorization bila token diisi).

## Tes

```powershell
uv run pytest                          # unit + kontrak, tanpa jaringan (soket non-localhost diblokir)
uv run pytest --cov=mcp_bogor          # cakupan; target ≥ 80% di services/ dan tools/
uv run ruff check . ; uv run ruff format --check .
```

**Smoke test ke portal asli** (ST-01..05; hanya atas izin pemilik repo, maks 60 permintaan, jeda 0,3 dtk, tidak ikut `pytest` bawaan):
```powershell
uv run python -m tests.smoke           # dry-run: daftar permintaan, tanpa jaringan
uv run pytest tests/smoke -m smoke     # eksekusi; menulis docs/SMOKE_REPORT_<tanggal>.md
```

**Pemeriksa kartu metrik** (lokal, tanpa portal):
```powershell
uv run python -m mcp_bogor.metric_cards --check
```
Kode keluar: 0 lulus (peringatan seperti "belum diperiksa" boleh), 1 ada galat atau kartu tidak sah, 2 konfigurasi atau katalog belum ada, 3 ada kartu yang kamusnya belum ada di cache. Setelah smoke, jalankan dengan `$env:MCP_CACHE_DIR="var/smoke/<tanggal>/cache"`.

## Tiga tool

Nama tool diberi awalan `MCP_TOOL_PREFIX` (bawaan `satudata_`). Semua respons memakai amplop yang sama: `contract_version`, `status` (`ok`/`partial`/`error`), `error`, `request_id`, `provenance`, `officialness`, `quality_flags`, `truncated`, `notices`, `result`. Kontrak lengkap ada di Dok 4; ringkasan untuk integrator ada di `docs/INTEGRASI_CITYA.md`.

| Tool | Parameter | `result` | Galat khas |
|---|---|---|---|
| `satudata_search_datasets` | `query` (2–100 karakter), opsional `opd`, `topik`, `tahun`, `page`, `page_size` | Daftar dataset dari katalog lokal: slug, nama, OPD, satuan, rentang tahun, keresmian, `metric_card` bila dataset itu kartu metrik. Tanpa portal. | `NO_MATCH`, `INVALID_PARAM` |
| `satudata_get_dataset_info` | `slug` | Metadata, deskripsi, `periods_available`, kolom (peran dimension/measure/id, arti, satuan, nilai unik untuk dimensi saja), `dictionary_validated` | `DATASET_NOT_FOUND`, `SOURCE_UNAVAILABLE`, `RATE_LIMITED`, `STALE_DATA` (partial) |
| `satudata_get_dataset_data` | `slug`, opsional `periods`, `filters` (kolom dimensi), `sort`, `force_refresh` | `data_ref` (URL bertanda tangan, `expires_at`, `row_count`), kolom, `periods_found`/`missing_periods`, `preview` ≤ 5 baris **kolom dimensi saja**, `filters_applied`, `dictionary_validated` | `PERIOD_NOT_AVAILABLE`, `LEVEL_NOT_AVAILABLE`, `DATASET_NOT_FOUND`, `SOURCE_UNAVAILABLE`, `RESPONSE_TOO_LARGE` |

Ketiganya baca-saja. `search_datasets` hanya membaca katalog lokal; dua lainnya memanggil portal hanya bila cache belum ada atau sudah kedaluwarsa.

## Hasil uji

**Uji manual MCP Inspector, 11 Nov 2026** (dijalankan Akhdan). Transport **stdio** (`npx @modelcontextprotocol/inspector uv run python -m mcp_bogor`), dengan file server terpisah di `localhost:8799`. Lima panggilan lulus:
1. `search_datasets` "kemiskinan".
2. `get_dataset_info` tingkat kemiskinan.
3. `get_dataset_data` tingkat kemiskinan `periods=[2025, 2026]` → `partial`, 2026 di `missing_periods`.
4. `get_dataset_data` tingkat kemiskinan dengan filter kecamatan → galat `LEVEL_NOT_AVAILABLE`.
5. `get_dataset_data` realisasi PAD → notice `DICTIONARY_NOT_VALIDATED`.

Tindak lanjut uji ini: kolom dimensi di respons data diberi `meaning_source="omitted"` (KK-S).

**Smoke test ke portal asli, 17 Nov 2026: LULUS** ([`docs/SMOKE_REPORT_2026-11-17.md`](docs/SMOKE_REPORT_2026-11-17.md), kode di commit `a13c10f`):

| ST | Isi | Hasil |
|---|---|---|
| ST-01 | Katalog lokal = `total_data` portal (777) | Lulus |
| ST-02 | 12 kartu metrik lewat tool sungguhan, termasuk kasus golden F-07, F-08, O-06 | Lulus |
| ST-03 | data_ref diunduh lewat HTTP; isi = respons tool (KT-06); KT-03 nol temuan | Lulus |
| ST-04 | 25 permintaan (= rencana), nol 429, jeda terkecil 453 ms | Lulus |
| ST-05 | p95 search 129 ms (< 500), data dari cache 185 ms (< 1000) | Lulus |

Setelah smoke, `metric_cards --check` atas cache smoke: 12 kartu OK, hanya peringatan "belum diperiksa".

## Struktur repo

```
src/mcp_bogor/
  __main__.py, runtime.py   # entry server: stdio / streamable-http, /healthz
  server.py                 # daftar tool (server low-level SDK MCP)
  sync_worker.py            # sinkron katalog; satu-satunya penulis katalog (ADR-010)
  metric_cards.py           # CLI --check kartu metrik
  config.py, logging.py, health.py
  models/                   # amplop, galat, notice, parameter, result (kontrak Dok 4)
  tools/                    # search, info, data
  services/                 # catalog, dictionary, data, freshness, officialness, normalize, periods, quality, metric_cards
  adapters/                 # SourceAdapter + OpenDataJabarAdapter (satu-satunya pemakai httpx)
  storage/                  # SQLite + FTS5, cache berkas, kartu, override
  fileserver/               # GET /d/{cache_id}?exp=&sig= (:8799)
metric_cards/  overrides/  golden_set/  scripts/record_fixtures.py
tests/unit  tests/contract  tests/smoke  tests/fixtures
docs/
```

Arah dependensi satu arah: tools → services → adapters/storage.
