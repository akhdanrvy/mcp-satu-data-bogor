# RUNBOOK: MCP Kota Bogor

Panduan operasi untuk server MCP Kota Bogor. Instalasi dan daftar env var ada di [`README.md`](../README.md). Setiap bagian ditulis sebagai **gejala → periksa → tindakan**.

Semua perintah dijalankan dari akar repo. Contoh path Windows memakai `C:\mcp\satu-data-bogor`, dan contoh Linux memakai `/opt/satu-data-bogor`; sesuaikan dengan lokasi sebenarnya. Ganti `<...>` dengan nilai asli. Jangan menyalin secret ke tiket, chat, atau log.

Aturan yang berlaku di semua prosedur:
- Akses portal hanya GET, tanpa uji beban, dan jangan pernah ke `/datasets/opendata/{slug}/view` (AK-01..06).
- Sinkron katalog penuh maks 1×/24 jam (AK-06). Worker sudah menjaganya sendiri.
- Smoke test dan perekaman fixture ke portal hanya atas izin pemilik repo.

---

## 1. Menjalankan sebagai layanan

Ada tiga proses yang membaca berkas `.env` yang sama:

| Proses | Perintah | Port | Sifat |
|---|---|---|---|
| Server MCP (HTTP) | `uv run python -m mcp_bogor` dengan `MCP_TRANSPORT=streamable-http` | 8800 | Selalu jalan |
| File server data_ref | `uv run python -m mcp_bogor.fileserver` | 8799 | Selalu jalan |
| Sinkron katalog | `uv run python -m mcp_bogor.sync_worker --once` | — | Terjadwal 1×/hari (bagian 2) |

Pastikan `MCP_TRANSPORT=streamable-http` ada di `.env` (atau di env layanan), supaya server tidak jalan sebagai stdio. Log JSON ditulis ke **stderr**, jadi arahkan stderr ke berkas log.

### Windows: Task Scheduler bawaan

Buat skrip pembungkus `C:\mcp\run-server.cmd`:
```bat
@echo off
cd /d C:\mcp\satu-data-bogor
set MCP_TRANSPORT=streamable-http
"C:\Users\<akun-layanan>\.local\bin\uv.exe" run python -m mcp_bogor 2>> var\log\server.log
```
Buat juga `run-fileserver.cmd` dengan isi serupa (perintah `-m mcp_bogor.fileserver`, log `var\log\fileserver.log`). Buat folder `var\log` lebih dulu.

Daftarkan keduanya agar jalan saat mesin menyala, di PowerShell sebagai administrator:
```powershell
schtasks /Create /TN "MCP Bogor Server" /TR "C:\mcp\run-server.cmd" /SC ONSTART /RU "<akun-layanan>" /RP * /RL LIMITED
schtasks /Create /TN "MCP Bogor FileServer" /TR "C:\mcp\run-fileserver.cmd" /SC ONSTART /RU "<akun-layanan>" /RP * /RL LIMITED
schtasks /Run /TN "MCP Bogor Server"
```
Di properti tugas, matikan "Stop the task if it runs longer than ...". Aktifkan juga "If the task fails, restart every 1 minute". Pakai path lengkap `uv.exe` milik akun layanan, karena PATH tugas terjadwal bisa berbeda dengan PATH di terminal.

Alternatif: NSSM bisa membungkus kedua perintah sebagai Windows Service sungguhan, tetapi harus dipasang terpisah.

### Linux: systemd

`/etc/systemd/system/mcp-bogor.service`:
```ini
[Unit]
Description=MCP Kota Bogor (streamable-http :8800)
After=network-online.target

[Service]
User=mcp
WorkingDirectory=/opt/satu-data-bogor
Environment=MCP_TRANSPORT=streamable-http
ExecStart=/usr/local/bin/uv run python -m mcp_bogor
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```
`mcp-bogor-fileserver.service` sama, dengan `ExecStart=/usr/local/bin/uv run python -m mcp_bogor.fileserver` dan tanpa baris `MCP_TRANSPORT`. Aktifkan keduanya dengan `sudo systemctl enable --now mcp-bogor mcp-bogor-fileserver`. Log bisa dibaca dengan `journalctl -u mcp-bogor -o cat`.

### Pemantauan `/healthz`

| Endpoint | 200 | 503 |
|---|---|---|
| `GET http://<host>:8800/healthz` | Katalog siap | Katalog belum disinkron |
| `GET http://<host>:8799/healthz` | Folder cache terbaca | Folder cache tidak terbaca |

- `/healthz` di :8800 **tidak pernah** memanggil portal. Field `portal` hanya melaporkan hasil cek terakhir beserta umurnya (`{"checked": false}` bila belum pernah dicek).
- `GET /healthz?deep=1` dengan `Authorization: Bearer <token>` ikut mengecek portal (di-cache 60 dtk). Ini hanya aktif bila `MCP_HTTP_AUTH_TOKEN` diisi; token salah → 401. Jangan dijalankan lebih sering dari 1×/menit.
- Pantau juga `catalog.synced_at`. Bila lebih tua dari 48 jam, sinkron terjadwal tidak berjalan (bagian 2).

---

## 2. Sinkron katalog terjadwal

**Perilaku worker (AK-06, KB-28):**
- Sinkron yang mengirim permintaan memblokir sinkron berikutnya selama `MCP_CATALOG_SYNC_MIN_INTERVAL_H` (24 jam). Sinkron yang diblokir dicatat `skipped`, tanpa satu permintaan pun.
- Pengecualian: sinkron `failed` atau `incomplete` yang **bukan** karena 429 boleh diulang setelah `MCP_CATALOG_SYNC_RETRY_AFTER_H` (6 jam).
- Karena itu menjadwalkan worker 1×/hari aman. Menjalankannya lebih sering pun tidak melanggar AK-06, karena hasilnya hanya `skipped`.
- Kode keluar: 0 untuk complete/incomplete/skipped, 1 untuk failed, 2 untuk galat konfigurasi (mis. User-Agent placeholder).

**Windows (Task Scheduler)**, setiap hari pukul 02.00. Skrip `C:\mcp\run-sync.cmd`:
```bat
@echo off
cd /d C:\mcp\satu-data-bogor
"C:\Users\<akun-layanan>\.local\bin\uv.exe" run python -m mcp_bogor.sync_worker --once >> var\log\sync.log 2>&1
```
```powershell
schtasks /Create /TN "MCP Bogor Sinkron" /TR "C:\mcp\run-sync.cmd" /SC DAILY /ST 02:00 /RU "<akun-layanan>" /RP *
```

**Linux (cron)**, di `crontab -e` milik user `mcp`:
```cron
0 2 * * * cd /opt/satu-data-bogor && /usr/local/bin/uv run python -m mcp_bogor.sync_worker --once >> var/log/sync.log 2>&1
```
Bila memakai systemd timer, pakai `OnCalendar=*-*-* 02:00:00` dan `Persistent=false`, supaya sinkron yang terlewat tidak menumpuk.

**Memeriksa hasil:**
- Baris ringkasan di `sync.log`: status, halaman, record, baru/diperbarui/dihapus, jumlah permintaan, serta daftar "Dataset hilang" dan "Dataset baru".
- Riwayat lengkap ada di tabel `sync_runs`:
  ```powershell
  uv run python -c "import sqlite3; c=sqlite3.connect('var/mcp_bogor.db'); [print(r) for r in c.execute('SELECT id, started_at, status, records, total_data, inserted, deleted, requests_sent, error_kind FROM sync_runs ORDER BY id DESC LIMIT 5')]"
  ```
- Bila sinkron baru saja gagal karena konfigurasi, perbaiki `.env` lalu jalankan `--once` lagi. Galat konfigurasi tidak dicatat sebagai run, jadi tidak memblokir 24 jam.

---

## 3. Portal mati atau 429

**Gejala:**
- Tool mengembalikan `SOURCE_UNAVAILABLE` atau `RATE_LIMITED`, atau `partial` dengan `STALE_DATA`.
- Log `portal_request` berstatus 5xx, `null` (koneksi atau timeout), atau 429.
- `/healthz?deep=1` → `portal.ok=false`.

**Perilaku bawaan (tidak perlu tindakan):**
- Adaptor mengulang 429/5xx/timeout dengan backoff eksponensial, maks `MCP_MAX_RETRIES` percobaan, dan menghormati `Retry-After`. Bila tunggunya > `MCP_BACKOFF_MAX_S` (30 dtk), permintaan langsung gagal tanpa tidur.
- Setelah 429, **semua** permintaan dari proses itu ditahan sampai jeda berakhir (jeda global, KB-25).
- Dataset yang sudah ada di cache tetap disajikan sebagai `partial` + `STALE_DATA` (`served_from=stale_cache`) selama umurnya ≤ `MCP_CACHE_STALE_MAX_H` (168 jam). Lewat batas itu, galat asli dikembalikan.
- `search_datasets` tidak terpengaruh, karena hanya membaca katalog lokal.

**Tindakan:**
1. Pastikan sumber masalahnya di portal: buka `https://opendata.kotabogor.go.id` di browser. Jangan menguji dengan skrip berulang.
2. **Jangan** me-restart server berulang-ulang untuk "memaksa" ulang, dan jangan menaikkan `MCP_MAX_RETRIES` atau menurunkan jeda.
3. Pada 429: tunggu. Sinkron yang gagal karena 429 **tidak** diulang setelah 6 jam; ia menunggu 24 jam penuh (KB-28). Bila 429 berulang di hari yang sama, catat waktunya dan laporkan ke tim, karena mungkin ada klien lain yang terlalu agresif.
4. Bila portal mati lebih dari 7 hari, cache usang tidak lagi disajikan. Sampaikan ke pengguna Citya bahwa data portal tidak tersedia; jangan mengisi data dari sumber lain.

---

## 4. Dataset hilang atau bertambah

**Gejala:**
- Ringkasan sinkron memuat "Dataset hilang (n)" atau "Dataset baru (n)".
- Tool mengembalikan `DATASET_NOT_FOUND` untuk slug yang dulu ada.
- `metric_cards --check` menghasilkan galat `NOT_IN_CATALOG`.
- `search_datasets` memberi notice `CATALOG_STALE` (sinkron terakhir lebih tua dari 48 jam).

**Perilaku:**
- Sinkron hanya **menghapus** dataset lokal bila snapshot katalog lengkap: semua halaman terbaca dan jumlah slug = `total_data`. Snapshot yang tidak lengkap hanya menambah dan memperbarui (status `incomplete`).
- Contoh nyata: katalog laptop 745 dataset (15 Nov 2026) menjadi 777 setelah sinkron 17 Nov 2026. Portal menambah dataset; itu bukan galat.

**Tindakan:**
1. Dataset baru tidak perlu tindakan; dataset itu langsung bisa dicari setelah sinkron.
2. Dataset hilang yang merupakan **kartu metrik**:
   - Cek di portal apakah slug-nya berganti (sering terjadi saat OPD mengunggah ulang).
   - Bila berganti, perbarui `slug` kartu (bagian 7), jalankan `--check`, lalu restart server.
   - Bila benar-benar dihapus, ubah status kartu menjadi `perlu_konfirmasi` dan laporkan ke Diskominfo/OPD.
3. Untuk memastikan katalog lokal sama dengan portal tanpa sinkron penuh, ST-01 di smoke test membandingkan jumlahnya (bagian 9).

---

## 5. Rotasi secret

### `MCP_DATAREF_SECRET` (KB-33)

Secret ini menandatangani tautan data_ref. `MCP_DATAREF_SECRET_PREVIOUS` hanya dipakai untuk **memverifikasi** tautan lama dan tidak pernah dipakai untuk menandatangani. Nama berkas indeks cache tidak bergantung pada secret, sehingga rotasi tidak mengosongkan cache.

1. Buat secret baru:
   `uv run python -c "import secrets; print(secrets.token_urlsafe(48))"`
2. Di `.env`: pindahkan nilai lama ke `MCP_DATAREF_SECRET_PREVIOUS=<secret-lama>`, lalu isi `MCP_DATAREF_SECRET=<secret-baru>`.
3. Restart **file server lebih dulu** (sekarang menerima tanda tangan lama dan baru), lalu restart server MCP (mulai menandatangani dengan secret baru).
4. Tunggu minimal `MCP_DATAREF_TTL_SECONDS` (900 dtk = 15 menit) agar semua tautan lama kedaluwarsa.
5. Kosongkan `MCP_DATAREF_SECRET_PREVIOUS=`, lalu restart file server.
6. Cek: `GET :8799/healthz` → 200, dan satu panggilan `get_dataset_data` lewat Inspector menghasilkan `data_ref` yang bisa diunduh (200).

Kedua secret harus ≥ 32 byte; bila tidak, proses menolak start. Bila secret bocor, lakukan langkah yang sama tetapi **lewati** `PREVIOUS` (biarkan kosong). Akibatnya tautan yang sedang beredar langsung ditolak dengan 403, dan Citya cukup memanggil `get_dataset_data` lagi.

### `MCP_HTTP_AUTH_TOKEN`

Server hanya menerima satu token, jadi rotasi harus dikoordinasikan dengan tim Citya:
1. Sepakati waktu, lalu ganti token di `.env` server dan di konfigurasi backend Citya.
2. Restart server MCP. Selama jeda antara keduanya, Citya menerima 401.
3. Kirim token lewat saluran rahasia tim, jangan lewat chat atau tiket.

---

## 6. Cache: ukuran, GC, dan membersihkan

Isi `MCP_CACHE_DIR` (bawaan `var/cache`):

| Folder | Isi |
|---|---|
| `data/` | Berkas data_ref (`{cache_id}.json`), disajikan file server apa adanya. **Berisi baris data**; jangan disalin ke luar server. |
| `meta/` | Metadata per versi (slug, parameter, waktu, ukuran) |
| `index/` | Kunci kueri → versi terbaru |
| `dictionary/` | Kamus kolom per slug |
| `tmp/` | Berkas sementara sebelum ditulis atomik |

**GC otomatis (KB-32):**
- Berjalan maks 1×/10 menit, setelah ada pengambilan data.
- Menghapus versi lama yang umurnya > TTL + TTL data_ref dan tidak sedang dirujuk tautan aktif.
- Bila total masih di atas `MCP_CACHE_MAX_MB` (500), versi tak terlindungi dihapus mulai dari yang tertua.
- Berkas yang terkunci (Windows: sedang dibaca) dilewati dan dicoba lagi nanti. Log `cache_gc` mencatat jumlah yang dihapus dan dibebaskan.

**Gejala dan tindakan:**
- Disk penuh, atau log `cache_gc_failed`: cek ukuran dengan `Get-ChildItem var\cache -Recurse | Measure-Object Length -Sum` (Linux: `du -sh var/cache`). Turunkan `MCP_CACHE_MAX_MB` bila perlu, lalu restart.
- `RESPONSE_TOO_LARGE` untuk satu dataset: barisnya melebihi `MCP_CACHE_MAX_ROWS`. Ini disengaja; minta pengguna mempersempit `periods` atau `filters`.
- Membersihkan cache sepenuhnya, mis. setelah perubahan format: hentikan server dan file server, hapus isi `var/cache`, lalu start lagi. Akibatnya setiap dataset yang diminta berikutnya memicu GET ke portal (kamus + seri), jadi lakukan di luar jam sibuk dan jangan sering-sering.

---

## 7. Kartu metrik dan override kamus

### Kartu metrik (`metric_cards/KM-xx.yaml`, KB-49)

Satu berkas per kartu, dengan skema `services/metric_cards.MetricCard` (field asing ditolak). Field utama:
- `id`, `nama`, `definisi`, `slug`;
- `kolom_nilai` (measure), `kolom_dimensi`, `satuan`, `opd`, `periode_tersedia`;
- `sinonim`: harus unik antarkartu; kueri yang persis sama dengan sinonim memasang kartu di peringkat 1 search;
- `dataset_berbeda_definisi`: memicu notice `DIFFERENT_DEFINITION` (R-10);
- `status`, `kasus_uji`, `catatan_mutu` (untuk manusia saja; tidak pernah dikirim ke model);
- `reviewed_by`, `reviewed_at`.

Status kartu:

| Status | Arti |
|---|---|
| `aktif` | Dipakai search; kamus harus tervalidasi |
| `kamus_belum_tervalidasi` | Dipakai search; tool memberi notice `DICTIONARY_NOT_VALIDATED` (mis. KM-01 sampai override `total_pendapatan` masuk) |
| `perlu_konfirmasi` | Tidak dipakai sama sekali (tanpa pemasangan, tanpa `metric_card`) |

Prosedur mengubah atau menambah kartu:
1. Edit atau buat YAML. Isi slug dan nama kolom dari `get_dataset_info`, bukan dari ingatan.
2. Setelah memeriksa slug, definisi, kolom, satuan, dan sinonim, isi `reviewed_by` (nama pemeriksa, **bukan** email) dan `reviewed_at: YYYY-MM-DD`.
3. Jalankan `uv run python -m mcp_bogor.metric_cards --check` dan perbaiki sampai kode keluar 0. Galat yang umum: `NOT_IN_CATALOG`, `UNIT_MISMATCH`, `MEASURE_ROLE`, `STATUS_OUTDATED`. Kode 3 (`DICTIONARY_NOT_CACHED`) berarti kamusnya belum ada di cache; panggil `get_dataset_info` slug itu sekali lewat Inspector.
4. Jalankan `uv run pytest tests/unit/test_metric_cards.py tests/contract` (tanpa jaringan).
5. **Restart server MCP**, karena kartu hanya dimuat saat start. Kartu yang rusak membuat server tetap jalan tanpa kartu (log `metric_cards_invalid`).

### Override kamus (`overrides/<slug>.yaml`, KK-07)

Override dipakai bila arti, peran, atau satuan kolom tidak terdokumentasi di deskripsi portal. Nama berkas = slug.

```yaml
slug: <slug>
note: <alasan singkat>
columns:
  <nama_kolom>:          # harus ada di /bigdata dataset ini
    meaning: <arti kolom>
    role: measure        # dimension | measure | id
    unit: <satuan>
    reviewed_by: <nama pemeriksa>
    reviewed_at: 2026-11-20
```

- Kolom measure yang tidak terdokumentasi baru dianggap tervalidasi (`dictionary_validated=true`) bila keempat field `meaning`, `unit`, `reviewed_by`, dan `reviewed_at` terisi (KB-30).
- Override dibaca setiap kali kamus diminta, jadi **tidak perlu restart**. Perubahan isi mengubah sidiknya, sehingga kamus di cache dibuat ulang otomatis; itu memicu satu probe `/bigdata` ke portal.
- Berkas yang tidak sah membuat tool untuk slug itu mengembalikan `INTERNAL`. Rinciannya hanya ada di log.
- Setelah override sebuah kartu `kamus_belum_tervalidasi` masuk, `--check` memberi `STATUS_OUTDATED`. Ubah status kartu menjadi `aktif`, lalu restart.

---

## 8. Rekam ulang fixture

Fixture rekaman (`tests/fixtures/recorded/<tanggal>/`) adalah acuan tes tanpa jaringan. Rekam ulang hanya bila bentuk API portal berubah, dan **hanya atas izin pemilik repo**.

```powershell
uv run python scripts/record_fixtures.py                    # dry-run: daftar permintaan + jumlah
uv run python scripts/record_fixtures.py --execute          # merekam (maks 60 permintaan, jeda 0,3 dtk)
uv run python scripts/record_fixtures.py --execute --only <slug>   # satu dataset ke folder terbaru
uv run python scripts/record_fixtures.py --resanitize        # sanitasi ulang rekaman (lokal)
uv run python scripts/record_fixtures.py --slim-catalog     # bangun catalog_slim.json (lokal)
```

- Skrip menolak jalan bila User-Agent masih placeholder. 429 menghentikan perekaman, dan 5xx diulang sekali.
- Akun petugas dibuang dan kontak OPD diganti `<redacted>` sebelum ditulis. Ringkasannya ada di `docs/FIXTURE_REPORT.md`; baca laporan itu sebelum commit.
- Hanya halaman katalog 1 dan terakhir yang di-commit. Halaman tengah di-gitignore dan terwakili `catalog_slim.json`.
- Tes memakai folder `2026-11-10` (konstanta `RECORDED` di `tests/fakes.py` dan tes kontrak). Folder baru tidak otomatis dipakai; pindahkan konstanta itu dalam perubahan tersendiri, lalu jalankan seluruh suite.
- Bila skema atau deskripsi tool berubah, snapshot KT-01 ditulis ulang dengan `uv run pytest tests/contract --update-snapshots`, dan hasilnya **wajib direview**.

---

## 9. Smoke test ke portal asli

Kapan: setelah perubahan adaptor atau tool, sebelum rilis, atau bila ada keraguan apakah portal berubah. Hanya atas izin pemilik repo.

```powershell
uv run python -m tests.smoke           # dry-run: prasyarat + daftar permintaan (±25), tanpa jaringan
uv run pytest tests/smoke -m smoke     # eksekusi ST-01..05
$env:MCP_CACHE_DIR="var/smoke/<tanggal>/cache"; uv run python -m mcp_bogor.metric_cards --check
```

- Batasnya: maks 60 permintaan (retry ikut dihitung), jeda 0,3 dtk, hanya `/datasets?per_page=1` dan `/bigdata/...`. 429 menghentikan sesi.
- Smoke **tidak** menyinkron katalog. Bila ST-01 gagal dengan "katalog lokal tertinggal", sinkron manual dulu (bagian 2), lalu ulangi.
- Laporan ditulis ke `docs/SMOKE_REPORT_<tanggal WIB>.md`, dengan akhiran `-2`, `-3` bila sudah ada. Laporan tidak memuat nilai data. Respons mentah dan berkas data_ref ada di `var/smoke/<tanggal>/` (di-gitignore) dan **berisi baris data**.
- Bila muncul `SMOKE_REPORT_DITOLAK.md`, pelindung laporan menemukan angka berformat atau bentuk yang sama dengan nilai measure pada baris yang disebut. Periksa baris itu. Bila ternyata positif palsu (mis. durasi yang kebetulan sama dengan sebuah nilai), pemilik repo memutuskan apakah laporan boleh dipindah ke `docs/`.
- `--check` sesudahnya harus keluar 0. Salin hasilnya ke laporan.

---

## 10. Membaca log JSON

Setiap proses menulis **satu baris JSON per kejadian ke stderr**, level `MCP_LOG_LEVEL`. Log pustaka lain (SDK MCP, uvicorn) dibungkus sebagai `{"event": "log", ...}`.

| `event` | Dari | Field penting |
|---|---|---|
| `tool_call` | Server | `tool`, `request_id`, `slug`, `status`, `error_code`, `exception`, `served_from`, `truncated`, `notices`, `size_bytes`, `duration_ms` |
| `portal_request` | Adaptor | `path`, `status` (`null` = koneksi/timeout), `attempt`, `duration_ms` |
| `catalog_sync`, `catalog_snapshot` | Sinkron | Status, halaman, record, slug baru/hilang |
| `fileserver_request` | File server | `method`, `cache_id`, `status`, `size_bytes`, `duration_ms` |
| `cache_gc`, `cache_gc_failed`, `cache_corrupt_file` | Cache | Jumlah dihapus/terkunci, jenis galat |
| `quality_finding` | Mutu data | Kode QC, slug, periode (untuk laporan ke OPD) |
| `server_start`, `server_stop`, `startup_degraded`, `startup_refused`, `catalog_not_ready` | Start | Transport, versi, alasan |
| `metric_cards_loaded`, `metric_cards_invalid` | Start | Jumlah kartu, alasan |
| `http_unauthorized` | HTTP | Path (token tidak pernah dicatat) |

Yang **sengaja tidak dicatat**: isi baris data, teks kueri, nilai filter, `sig`/`exp`/query string data_ref, token, secret, dan akun petugas portal. Exception hanya dicatat jenisnya, tanpa traceback.

Contoh menyaring log:
```powershell
# PowerShell: panggilan tool yang galat
Get-Content var\log\server.log | ForEach-Object { $_ | ConvertFrom-Json } | Where-Object { $_.event -eq "tool_call" -and $_.status -eq "error" } | Select-Object tool, slug, error_code, duration_ms
```
```bash
# Linux: 429/5xx dari portal, dan p95 kasar durasi tool
jq -c 'select(.event=="portal_request" and (.status==429 or .status>=500))' var/log/server.log
jq -r 'select(.event=="tool_call") | .duration_ms' var/log/server.log | sort -n | awk '{a[NR]=$1} END {print a[int(NR*0.95)]}'
```
