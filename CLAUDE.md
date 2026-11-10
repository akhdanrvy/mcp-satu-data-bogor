# CLAUDE.md — MCP Kota Bogor (satu-data-bogor)

Server MCP yang membaca portal **opendata.kotabogor.go.id** (757 dataset, 39 OPD) dan melayani **X-Assistant Citya AI Bogor**. Pengguna akhir jawaban: Wali Kota dan pimpinan Pemkot Bogor. Pemilik: Akhdan Ravi Andaman (SCCIC ITB).

Jawab dan tulis komentar/commit dalam **bahasa Indonesia yang lugas**; istilah teknis boleh bahasa Inggris. Identifier kode dalam bahasa Inggris, kecuali nama field kontrak yang sudah ditetapkan (mis. `tahun`, `opd`, `topik`).

## Dokumen acuan (baca sebelum menulis kode)
Semua di `docs/`. Urutan prioritas jika ada konflik: **Dok 4 > Dok 5 > Dok 3 > Dok 2 > Dok 1** (yang lebih baru dan lebih spesifik menang). Jika konflik tidak jelas, tanyakan, jangan memilih sendiri.

| Berkas | Isi | Dipakai untuk |
|---|---|---|
| `docs/01-ruang-lingkup.md` | FR/NFR, asumsi A-01..A-04, risiko R-xx, privasi | Apa yang wajib ada |
| `docs/02-spesifikasi-data-sumber.md` | Aturan AK, KS, KK, NB, CP, QC | Adaptor dan services |
| `docs/03-arsitektur-adr.md` | C4, struktur paket, `SourceAdapter`, ADR-001..011 | Struktur kode |
| `docs/04-spesifikasi-tool.md` | Parameter, amplop, 9 kode galat, `data_ref`, 12 kartu metrik, env var | **Kontrak tool (acuan utama)** |
| `docs/05-rencana-uji.md` | UT-01..14, KT-01..06, ST-01..05, kriteria lulus | Tes |
| `golden_set/golden_set_v0.2.jsonl` | 26 kasus dengan snapshot nilai portal | Acuan nilai di fixture/smoke |

Konflik yang sudah diketahui (ikuti Dok 4):
- Amplop: Dok 3 menulis `status` ok/stale/error dan galat huruf kecil; **Dok 4 berlaku**: `contract_version`, `status` ok/partial/error, `request_id`, 9 kode galat HURUF BESAR, `result`.
- Env var: Dok 3 `MCP_DATA_REF_TTL_MIN`/`MCP_DATA_REF_SECRET`; **Dok 4 berlaku**: `MCP_DATAREF_TTL_SECONDS` (900), `MCP_DATAREF_SECRET`.

## Aturan yang tidak boleh dilanggar
1. **Angka tidak pernah berasal dari LLM, dan baris data tidak pernah masuk respons tool.** `get_dataset_data` hanya mengembalikan `data_ref` + struktur + `preview` (≤5 baris, **kolom dimensi saja**). Tidak ada nilai kolom measure di respons tool mana pun (KT-03, nol toleransi). `MCP_INLINE_ROWS_FALLBACK` bawaan mati.
2. **Galat tampil sebagai galat, bukan angka.** Respons `status=error` punya `result=null` dan tanpa angka.
3. **Tandai, jangan perbaiki (ADR-007).** Data janggal diserahkan apa adanya + `quality_flags`. Jangan mengoreksi nilai.
4. **Privasi:** field akun petugas (`username`, `history_draft[].username`, `username` di `/history_datasets`) tidak pernah disimpan ke SQLite maupun muncul di respons/log. Log tidak berisi isi baris data.
5. **Akses portal sopan (AK-01..06):** hanya GET; jeda ≥ `MCP_REQUEST_DELAY_S` (0,2 dtk); backoff eksponensial pada 429/5xx, maks 5 percobaan, hormati `Retry-After`; **jangan pernah panggil `/datasets/opendata/{slug}/view`**; tanpa uji beban; sinkron katalog penuh maks 1×/24 jam; User-Agent = nama aplikasi + kontak tim.
6. **Tes unit dan kontrak tidak boleh menyentuh jaringan.** Pakai fixture di `tests/fixtures/`. Hanya `tests/smoke/` yang boleh ke portal asli (maks 60 permintaan per jalan, jeda 0,3 dtk), dan tidak ikut `pytest` bawaan.
7. **Repo `github.com/sccicitb/satu-data-mcp` (MCP Garut) hanya dibaca** sebagai contoh pola. Jangan push, buka PR, atau mengubah apa pun di sana.
8. **Rahasia hanya di env.** `.env` di-gitignore; `.env.example` berisi placeholder.
9. **Commit dan push hanya dilakukan Akhdan.** Jangan menjalankan `git commit`, `git push`, `git tag`, `git rebase`, `git reset`, atau perintah lain yang mengubah riwayat atau remote, walaupun langkahnya sudah selesai dan tes hijau. Akhdan yang menentukan kapan commit. `git status`, `git diff`, dan `git log` boleh dipakai untuk membaca. Bila diminta, cukup usulkan pesan commit dan daftar berkas yang sebaiknya masuk.
10. Hal bertanda **[Belum ditentukan]** di dokumen dibuat **bisa dikonfigurasi**, jangan diputuskan diam-diam. Catat di `docs/KEPUTUSAN_BUILD.md` bila terpaksa memilih nilai awal.

## Stack (ADR-009)
- Python **3.11+**, dikelola dengan **uv** (`pyproject.toml` + `uv.lock`, build backend `hatchling`, layout `src/`). `pip install -e .` tetap harus jalan.
- `mcp` (SDK resmi; FastMCP diimpor dari `mcp.server.fastmcp`, sama seperti Garut), `pydantic` v2, `pydantic-settings`, `httpx`, `sqlite3` bawaan (FTS5, mode WAL).
- File server :8799: pakai yang ringan dan cocok dengan stack (mis. `starlette`/`uvicorn`, yang sudah ikut terpasang sebagai dependensi `mcp`). Catat pilihannya.
- Dev: `pytest`, `pytest-cov`, `respx` (mock httpx), `ruff`.
- OS pengembangan: **Windows** (PowerShell / Git Bash). Path pakai `pathlib`; jangan mengandalkan perintah khusus Unix di skrip.

## Struktur paket (Dok 3 bagian 3)
```
src/mcp_bogor/
  server.py        # entry FastMCP: daftar tool (nama berawalan MCP_TOOL_PREFIX), transport
  sync_worker.py   # sinkron katalog terjadwal; satu-satunya penulis katalog (ADR-010)
  config.py        # pydantic-settings, semua dari env
  logging.py       # log JSON: request_id, slug, tool, durasi, status, ukuran
  models.py        # Pydantic: DatasetRecord, Column, Provenance, Officialness, QualityFlag, ToolEnvelope, DataRef
  tools/           # search.py, info.py, data.py
  services/        # catalog, dictionary, freshness, officialness, normalize, periods, quality
  adapters/        # base.py (SourceAdapter Protocol), opendata_jabar.py
  storage/         # db.py (SQLite + FTS5, WAL), cache.py (berkas per kueri + meta, TTL)
  fileserver/      # app.py: GET /d/{cache_id}?exp=&sig=
overrides/         # kurasi kamus kolom per slug (KK-07)
metric_cards/      # 12 kartu metrik YAML (Dok 4 bagian 8)
tests/fixtures/  tests/unit/  tests/contract/  tests/smoke/
golden_set/
.env.example
```
Arah dependensi satu arah: **tools → services → adapters/storage**. Service tidak pernah memanggil httpx langsung; hanya adaptor yang tahu bentuk API portal.

## Fakta API portal (sudah diamati, lihat Dok 2)
- Base `https://opendata.kotabogor.go.id/api`, tanpa autentikasi. Respons `{code, message, data, error, pagination}`.
- `GET /datasets?page=&per_page=&search=&where=&sort=` (default 100/halaman, ±8 halaman). `limit`/`skip`/`q` diabaikan server.
- `GET /datasets/{slug}` → record + `metadata[]` (Satuan Dataset, Frekuensi Dataset, Dimensi Dataset Awal/Akhir, dll.).
- `GET /bigdata/{schema}/{table}?page=&per_page=&where=&sort=` → `data[]`, `metadata` (nama kolom), `metadata_filter` (nilai unik per kolom). `schema`/`table` diambil dari record dataset. `?count=true` → `{data:{count}}`. `where` JSON berjalan di server, mis. `{"tahun":[2025]}`; `sort=tahun:desc` bekerja.
- Slug salah → 404. Offset waktu tertulis `+07:07` → dinormalkan ke `+07:00`.
- Kesegaran = `cdate` entri `history_draft` bertipe approve terbaru; cadangan `mdate` (KS-01..05).
- Kamus kolom: urai baris `- nama_kolom: menyatakan <arti> dengan tipe data <tipe>.` dari `deskripsi`, lalu validasi ke kolom asli (KK-01..08).
- Keresmian: `resmi` hanya bila `validate=approve` dan `klasifikasi=public`.

## Kontrak data_ref (Dok 4 bagian 6)
URL `http://<host>:8799/d/{cache_id}?exp={unix}&sig={hmac}`; `sig` = HMAC-SHA256 atas `"{cache_id}.{exp}"` dengan `MCP_DATAREF_SECRET`, base64url tanpa padding; bandingkan dengan `hmac.compare_digest`. `cache_id` bukan slug. Respons file server: 200 / 403 (tanda tangan salah) / 410 (kedaluwarsa) / 404 (cache hilang) / 503.

## Perintah
```
uv sync                                  # pasang dependensi
uv run pytest                            # unit + kontrak (tanpa jaringan)
uv run pytest --cov=mcp_bogor            # cakupan; target ≥ 80% di services/ dan tools/
uv run pytest tests/smoke -m smoke       # HANYA atas permintaan Akhdan; ke portal asli
uv run python -m mcp_bogor.sync_worker --once
uv run python -m mcp_bogor               # stdio (bawaan, untuk MCP Inspector)
$env:MCP_TRANSPORT="streamable-http"; uv run python -m mcp_bogor   # :8800
uv run ruff check . ; uv run ruff format .
```

## Cara kerja dengan Akhdan
- Kerjakan bertahap sesuai urutan di `docs/RENCANA_FASE1.md`; satu langkah = satu perubahan kecil yang tesnya hijau, lalu berhenti dan laporkan agar Akhdan bisa me-review dan commit sendiri.
- Sebelum langkah besar, tulis rencana singkat dan tunggu persetujuan.
- **Jangan menjalankan smoke test atau perekaman fixture ke portal tanpa izin eksplisit** di percakapan.
- Bedakan fakta (dari dokumen/respons API), pengetahuan umum, dan dugaan.
- Akhiri tiap langkah dan sesi dengan ringkasan: yang selesai, tes yang lulus, usulan pesan commit, dan satu langkah berikutnya.
