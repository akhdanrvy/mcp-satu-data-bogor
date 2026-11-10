# Keputusan Build — MCP Kota Bogor

Catatan keputusan yang diambil saat build untuk hal bertanda [Belum ditentukan] atau konflik antar-dokumen. Setiap nilai di sini bisa diubah lewat env kecuali disebut lain. Disetujui Akhdan pada 10 Nov 2026, kecuali disebut lain.

## Nilai awal konfigurasi

| ID | Hal | Keputusan | Env var | Alasan |
|---|---|---|---|---|
| KB-01 | Versi Python pengembangan | 3.12 (`.python-version`); `requires-python >=3.11` | — | Wheel paling lengkap; interpreter sudah terpasang lewat uv |
| KB-02 | Timeout HTTP ke portal | 20 detik | `MCP_HTTP_TIMEOUT_S` | Dok 2/3 tidak menetapkan nilai |
| KB-03 | Batas `force_refresh` (Dok 4 bagian 9) | Minimal 300 detik antar-refresh **per slug + parameter** | `MCP_FORCE_REFRESH_MIN_INTERVAL_S` | Mencegah beban portal (NFR-01) |
| KB-04 | File server :8799 | `starlette` + `uvicorn` | — | Sudah ikut terpasang sebagai dependensi `mcp`; ringan |
| KB-05 | Host bawaan | `127.0.0.1` untuk :8800 dan :8799 | `MCP_HTTP_HOST`, `MCP_FILESERVER_HOST` | Lokasi server produksi belum ditentukan; aman untuk laptop |
| KB-06 | Syarat `MCP_DATAREF_SECRET` | Wajib terisi, bukan placeholder, ≥ 32 byte; dicek saat file server/penanda tangan dibuat | `MCP_DATAREF_SECRET` | ADR-008; mode stdio tetap jalan tanpa secret |
| KB-07 | Zona waktu | Konstanta `WIB = timezone(timedelta(hours=7))`, tanpa `zoneinfo` | — | Windows tidak membawa tzdata |
| KB-08 | Path penyimpanan | `MCP_DB_PATH` dan `MCP_CACHE_DIR` di-resolve menjadi absolut dan foldernya dibuat saat konfigurasi dimuat | `MCP_DB_PATH`, `MCP_CACHE_DIR` | Hindari ketergantungan pada direktori kerja |
| KB-09 | User-Agent | Placeholder `mcp-bogor/0.1 (+<kontak-tim>)`; diisi kontak tim saat deploy, bukan email pribadi | `MCP_USER_AGENT` | AK-05 |
| KB-10 | Angka di pesan galat (FR-10) | `error()` dan `partial(stale_message=...)` menyamarkan setiap deret angka menjadi `[angka]`; `ToolError.message` dan `QualityFlag.detail` yang dibangun langsung **ditolak** bila memuat digit. Akibatnya tahun/batas di pesan juga tersamar. *Usulan Langkah 1, menunggu persetujuan.* | — | Galat tampil sebagai galat, bukan angka |
| KB-11 | Datetime di model | Datetime tanpa offset ditolak; yang ber-offset dikonversi ke WIB dan mikrodetik dibuang (serialisasi `YYYY-MM-DDTHH:MM:SS+07:00`). Normalisasi `+07:07` tetap di `services/normalize` (Langkah 2). *Usulan Langkah 1.* | — | Hindari zona waktu tebakan; round-trip JSON stabil |
| KB-12 | Batas `page_size` | Dibaca dari konteks validasi (`search_max_page_size`, diisi tool dari `MCP_SEARCH_MAX_PAGE_SIZE`); tanpa konteks = 20. Bila batas < 10, bawaan 10 diturunkan ke batas, bukan ditolak. *Usulan Langkah 1.* | `MCP_SEARCH_MAX_PAGE_SIZE` | Model tidak membaca env langsung |
| KB-13 | Invarian amplop tambahan (turunan Dok 4 bagian 2 dan 7) | (a) status ok/partial wajib membawa `result`; (b) `truncated=true` hanya dengan status `partial`; (c) `STALE_DATA` ⇔ `provenance.served_from=stale_cache`; (d) `STALE_DATA` tidak pernah dengan status `error`. *Usulan Langkah 1.* | — | Dok 4: partial = terpotong/kedaluwarsa; STALE_DATA dikirim sebagai partial dengan stale_cache |

## Penyelesaian konflik dokumen

| ID | Konflik | Keputusan |
|---|---|---|
| KK-A1 | Dok 3 punya `level_not_available`; tabel 9 kode Dok 4 tidak, padahal KM-06 Dok 4 memakainya | Tambah kode ke-10 **`LEVEL_NOT_AVAILABLE`** (tool: data; `retryable=false`). Dipicu bila `filters` memakai kolom wilayah (kecamatan/kelurahan) yang tidak ada di dataset dan `tingkat_penyajian` dataset = kota. Masuk `contract_version` 0.1 (belum pernah dirilis). |
| KK-A2 | Kasus `not_in_source` di golden set / `not_in_source` Dok 3 | Dipetakan ke **`NO_MATCH`** dari `search_datasets`; tanpa kode baru. |
| KK-B | Field `provenance` Dok 3 vs Dok 4 | Ikuti Dok 4 (`source_url`, `opd`, `data_updated_at`, `retrieved_at`, `period_start`, `period_end`, `served_from`), ditambah `freshness_source` (`"approve"` \| `"mdate"`) dan `metadata_changed_after_approve` (bool) karena wajib menurut KS-01..03. `change_notes` opsional (string \| null). |
| KK-C | `Column` (CLAUDE.md/Dok 3) vs `ColumnInfo` (RENCANA_FASE1) | Pakai `ColumnInfo`. |
| KK-D | Env var Dok 3 yang tidak ada di Dok 4 (`MCP_CACHE_TTL_H`, `MCP_FILESERVER_PUBLIC_URL`, `MCP_USER_AGENT`, `MCP_SOURCE_BASE_URL`) | Semua dipakai. |
| KK-E | `quality_flags`: Dok 4 menulis `{code, column, period, detail}` dengan "kode QC-01 s.d. QC-03"; Dok 2 memberi nama `suspect_*`, dan QC-03 adalah aturan penyampaian, bukan penanda; Dok 3 hanya `{code, period, column}` | `QualityFlag = {code, rule, column, period, detail}`. `code` ∈ {`suspect_zero_period`, `suspect_scale`, `suspect_outlier`}; `rule` ∈ {`QC-01`, `QC-02`}, dengan pasangan tetap `suspect_zero_period`→QC-01, `suspect_scale`/`suspect_outlier`→QC-02. `column`, `period`, `detail` boleh null; `detail` tanpa angka (KB-10). Disetujui Akhdan 10 Nov 2026 (instruksi Langkah 1). |
