> Ekspor otomatis dari Google Docs (versi acuan) pada 10 Nov 2026. Jangan edit di sini; edit di Google Docs lalu ekspor ulang. Diagram tidak ikut terekspor.

# Dokumen 3 — Arsitektur dan Catatan Keputusan (ADR) MCP Kota Bogor
MCP Kota Bogor adalah satu layanan Python (FastMCP) dengan empat bagian: server tool, worker sinkronisasi, penyimpanan lokal (SQLite dan berkas cache), dan file server bertanda tangan. Satu-satunya bagian yang tahu bentuk API portal adalah adaptor sumber data. LLM hanya menerima metadata, handle, dan tanda mutu; baris data penuh diambil backend X-Assistant lewat tautan bertanda tangan yang berumur pendek. Dokumen ini juga mencatat 10 keputusan arsitektur (ADR) beserta alasannya.
## Info dokumen
| Hal | Isi |
|---|---|
| Versi | 0.2, draf untuk diskusi (6 Nov 2026: ADR-011 dan penyesuaian dengan Citya) |
| Pemilik | SCCIC ITB |
| Reviewer | - |
| Dokumen acuan | Dok 1 Ruang Lingkup dan Kebutuhan v0.1; Dok 2 Spesifikasi Data Sumber v0.1 |
| Notasi | Model C4 (konteks, container, komponen) dan ADR ringkas (konteks, keputusan, alternatif, konsekuensi) |

Penanda: [Fakta] sudah dikonfirmasi atau diamati; [Asumsi] dipakai sampai ada konfirmasi; [Belum ditentukan] keputusan yang masih terbuka. ADR berstatus Diterima jika sudah disepakati dalam diskusi atau Dok 1 dan 2, dan Diusulkan jika menunggu persetujuan.
## 1. Konteks sistem (C4 level 1)
MCP Kota Bogor hanya punya dua sambungan data: membaca API publik portal dan melayani X-Assistant. Satu-satunya arus keluar lain adalah laporan temuan mutu ke Diskominfo atau OPD, yang tidak otomatis.

C4 level 1 · konteks sistem
Wali Kota tidak berinteraksi langsung dengan MCP; semua jawaban melewati X-Assistant, yang menghitung angka di backend dan memeriksa narasi. X-Assistant di sini adalah Citya AI: backend TypeScript (framework Bosia, Vercel AI SDK) yang sudah memanggil tool internalnya sendiri dan punya konektor Open Data ke portal yang sama (ADR-011). [Fakta, diamati 6 Nov 2026]
## 2. Container (C4 level 2)
MCP Kota Bogor terdiri atas lima container dalam satu image: server tool, worker sinkron, SQLite, cache berkas, dan file server. Hanya worker yang menulis katalog; server tool membaca katalog dan memanggil /bigdata saat data diminta.

C4 level 2 · container di dalam MCP Kota Bogor
X-Assistant memakai dua pintu: port 8800 untuk tool call (dipakai LLM) dan port 8799 untuk mengambil baris penuh lewat data_ref (dipakai backend). Pemisahan ini yang menjaga baris data tidak masuk konteks LLM (ADR-005).
## 3. Komponen di dalam MCP server (C4 level 3)
Kode dibagi empat lapis: tool (antarmuka ke X-Assistant), service (aturan Dok 2), adaptor (bentuk API portal), dan storage. Aturan dependensinya satu arah: tool → service → adaptor/storage; service tidak pernah memanggil httpx langsung.
| Lapis | Modul | Tanggung jawab | Aturan terkait |
|---|---|---|---|
| Tool | tools/search.py, tools/info.py, tools/data.py | Validasi input (Pydantic), memanggil service, membentuk amplop respons, menjaga batas token | FR-02 s.d. FR-04, FR-14 s.d. FR-16 |
| Service | services/catalog.py | Sinkron katalog, indeks FTS5, penyaringan kueri | FR-01, FR-09, AK-06 |
| Service | services/dictionary.py | Urai dan validasi kamus kolom, peran dimension/measure | KK-01 s.d. KK-08 |
| Service | services/freshness.py | Penanda kesegaran dan status stale | KS-01 s.d. KS-05 |
| Service | services/officialness.py | Status keresmian Resmi/Draf | FR-07 |
| Service | services/normalize.py | Normalisasi baris dan label | NB-01 s.d. NB-08 |
| Service | services/periods.py | periods_found, missing_periods | CP-01 s.d. CP-04, FR-17 |
| Service | services/quality.py | quality_flags dan log temuan mutu | QC-01 s.d. QC-04 |
| Adaptor | adapters/base.py | Kontrak SourceAdapter (bagian 5) | ADR-002 |
| Adaptor | adapters/opendata_jabar.py | Klien httpx ke API portal: jeda, backoff, paginasi | AK-01 s.d. AK-05 |
| Storage | storage/db.py | SQLite: katalog + FTS5, kamus kolom, log mutu | ADR-003 |
| Storage | storage/cache.py | Berkas data per kueri (JSON + meta), TTL | FR-05, KS-05 |
| File server | fileserver/app.py | Menyajikan baris penuh lewat tautan bertanda tangan | ADR-005, ADR-008 |
| Lintas | config.py, logging.py | Konfigurasi dari env (pydantic-settings), log JSON | NFR-07, NFR-10 |

Struktur paket yang diusulkan:
mcp-kota-bogor/├── src/mcp_bogor/│   ├── server.py          # entry FastMCP: daftar tool, transport│   ├── sync_worker.py     # entry sinkron katalog terjadwal│   ├── config.py│   ├── logging.py│   ├── models.py          # Pydantic: DatasetRecord, Column, Provenance, QualityFlag, ToolEnvelope│   ├── tools/             # search.py, info.py, data.py│   ├── services/          # catalog, dictionary, freshness, officialness, normalize, periods, quality│   ├── adapters/          # base.py, opendata_jabar.py│   ├── storage/           # db.py, cache.py│   └── fileserver/        # app.py├── overrides/             # kurasi kamus kolom per slug (KK-07)├── tests/│   ├── fixtures/          # respons API rekaman, tanpa jaringan│   ├── unit/│   └── smoke/             # ke API nyata, dijalankan di laptop├── golden_set/            # golden_set_v0.2.jsonl└── .env.example
## 4. Alur utama
LLM hanya memanggil tiga tool dan menerima metadata serta data_ref; baris penuh berjalan dari file server ke backend (garis tebal), sehingga angka tidak pernah berasal dari LLM.

alur utama · dari pertanyaan sampai visual
Panggilan ke API portal hanya terjadi bila cache kosong atau kedaluwarsa. Langkah "teruskan data_ref" dan seterusnya berada di sisi X-Assistant (fase 2, setelah akses repo Citya); MVP MCP cukup menyediakan data_ref yang valid. Bila portal mati dan cache kosong, get_dataset_data mengembalikan status: error dengan kode source_unavailable, tanpa angka.
## 5. Antarmuka internal
Dua kontrak menjaga arsitektur tetap longgar: SourceAdapter memisahkan service dari bentuk API portal, dan amplop respons membuat ketiga tool menjawab dengan bentuk yang sama. Parameter dan contoh lengkap tiap tool ditetapkan di Dok 4.
Kontrak adaptor sumber data. Adaptor baru (berkas IDS/Urban, Satu Peta) cukup mengimplementasikan lima metode ini; service dan tool tidak berubah.
class SourceAdapter(Protocol):    source_id: str  # "opendata-kotabogor"    def list_datasets(self) -> Iterator[RawDataset]:        """Seluruh katalog, sudah dipaginasi oleh adaptor."""    def get_dataset(self, slug: str) -> RawDataset:        """Satu dataset; NotFound bila slug tidak ada."""    def get_columns(self, dataset: RawDataset) -> ColumnProbe:        """Nama kolom dan nilai unik per kolom (metadata_filter)."""    def query_rows(self, dataset: RawDataset, where: dict, sort: list[str],                   page: int, per_page: int) -> RowPage:        """Baris mentah dengan filter di sumber."""    def health(self) -> SourceHealth:        """Untuk membedakan sumber mati dari dataset kosong."""
Amplop respons tool. Setiap respons memuat field berikut, apa pun tool-nya:
| Field | Isi | Dasar |
|---|---|---|
| status | ok, stale, atau error | FR-10, KS-05 |
| error | code (not_found, source_unavailable, not_in_source, level_not_available, ambiguous_request) dan pesan; tanpa angka | FR-10 |
| provenance | source_url, opd, data_updated_at, freshness_source, period_end, fetched_at, change_notes | FR-06, KS |
| officialness | resmi atau draf | FR-07 |
| quality_flags | Daftar {code, period, column} | QC-03 |
| truncated | true bila respons dipotong karena batas token | FR-16 |
| data_ref | Hanya di get_dataset_data: tautan bertanda tangan, berlaku terbatas, ke baris penuh | ADR-005 |
| preview | Hanya di get_dataset_data: maksimal 5 baris kolom dimensi | FR-14 |
| periods_found, missing_periods | Hanya di get_dataset_data | FR-17 |

## 6. Deployment, konfigurasi, dan keamanan
MCP berjalan sebagai satu container (atau satu proses di laptop saat pengembangan) di jaringan yang sama dengan backend X-Assistant; tidak ada port yang perlu dibuka ke internet. Lokasi server produksi [Belum ditentukan].
| Mode | Transport | Dipakai untuk |
|---|---|---|
| Pengembangan | stdio | Uji lokal dengan MCP Inspector atau Ollama di laptop |
| Integrasi | streamable-http, port 8800 | Dipanggil X-Assistant lewat jaringan internal |
| File server | HTTP, port 8799 | Backend mengambil baris penuh lewat data_ref |

Konfigurasi (variabel lingkungan, tidak ada nilai yang ditulis di kode):
| Variabel | Contoh | Aturan |
|---|---|---|
| MCP_SOURCE_BASE_URL | https://opendata.kotabogor.go.id/api | AK-01 |
| MCP_REQUEST_DELAY_S | 0.2 | AK-03 |
| MCP_CACHE_TTL_H | 24 | KS-05 |
| MCP_MAX_RESPONSE_TOKENS | [Belum ditentukan], menunggu num_ctx | FR-16 |
| MCP_DATA_REF_TTL_MIN | 15 [Asumsi] | ADR-008 |
| MCP_DATA_REF_SECRET | rahasia acak, di luar repo | ADR-008 |
| MCP_FILESERVER_PUBLIC_URL | URL internal yang dikenal backend | ADR-008 |
| MCP_USER_AGENT | nama aplikasi + kontak tim | AK-05 |

Keamanan:
- Tautan data_ref ditandatangani HMAC dengan masa berlaku pendek; tautan yang kedaluwarsa atau diubah ditolak (ADR-008). Ini menutup celah file server tanpa autentikasi di MCP Garut.
- File server hanya mendengarkan jaringan internal; tidak dipublikasikan lewat reverse proxy publik.
- Server MCP tidak menyimpan percakapan, identitas penanya, maupun field akun petugas portal (Dok 1, privasi). Riwayat percakapan, akun pengguna, dan log audit "siapa bertanya apa, data apa yang diakses" disimpan di basis data X-Assistant, milik Tim Chat Assistant.
- Rahasia hanya di variabel lingkungan; .env.example berisi placeholder.
- Log berisi request_id, slug, tool, durasi, kode status, dan ukuran respons; tidak berisi isi baris data. request_id adalah ID acak yang dikirim X-Assistant di setiap tool call (parameter opsional, dirinci di Dok 4), sehingga log MCP bisa disambungkan dengan log audit X-Assistant tanpa MCP mengetahui identitas penanya. Di Citya (Vercel AI SDK), toolCallId dapat dipakai sebagai request_id.
## 7. Catatan keputusan arsitektur (ADR)
Kesepuluh ADR sudah diterima; ADR-005, ADR-008, dan ADR-010 disetujui pada 5 November 2026. ADR-005 adalah keputusan yang paling menentukan bentuk kontrak tool. ADR-011 ditambahkan 6 November 2026 dan masih berstatus Diusulkan.
| ADR | Keputusan | Status |
|---|---|---|
| ADR-001 | MCP sebagai lapisan akses data, bukan panggilan API langsung dari X-Assistant | Diterima |
| ADR-002 | Lapisan adaptor sumber data | Diterima |
| ADR-003 | Katalog lokal SQLite FTS5 dan cache berkas dengan TTL 24 jam | Diterima |
| ADR-004 | Penanda kesegaran = waktu approve terakhir | Diterima |
| ADR-005 | Baris penuh diserahkan lewat tautan bertanda tangan (opsi C) | Diterima |
| ADR-006 | Validasi kamus kolom saat dataset pertama kali diminta | Diterima |
| ADR-007 | Tandai data janggal, jangan perbaiki | Diterima |
| ADR-008 | File server: tautan HMAC berumur pendek, jaringan internal | Diterima |
| ADR-009 | Stack Python: FastMCP, Pydantic v2, httpx, SQLite, pytest | Diterima |
| ADR-010 | Sinkron katalog di worker terpisah dari server tool | Diterima |
| ADR-011 | Hubungan MCP dengan konektor Open Data bawaan Citya: ganti, berdampingan, atau di dalam Citya | Diusulkan |

### ADR-001 — MCP sebagai lapisan akses data
- Konteks: X-Assistant Garut sudah memakai MCP Satu Data. [Fakta] X-Assistant Bogor perlu jalur ke 757 dataset portal.
- Keputusan: akses data lewat server MCP dengan kontrak tool yang sama dengan Garut.
- Alternatif: X-Assistant memanggil API portal langsung. Ditolak karena aturan Dok 2 (kesegaran, kamus, keresmian, mutu) harus ditulis ulang di X-Assistant dan tidak bisa dipakai ulang di kabupaten/kota lain.
- Konsekuensi: satu layanan tambahan untuk dijalankan; aturan data terkumpul di satu tempat.
### ADR-002 — Lapisan adaptor sumber data
- Konteks: sumber bisa bertambah (berkas IDS/Urban, Satu Peta), dan platform portal bisa berganti versi.
- Keputusan: semua akses sumber lewat SourceAdapter (bagian 5). Adaptor pertama: OpenDataJabarAdapter.
- Alternatif: klien API langsung di service, seperti api_client.py Garut. Ditolak karena API_BASE dan bentuk respons menempel di seluruh kode.
- Konsekuensi: sumber baru = satu adaptor baru; tes kontrak adaptor wajib.
### ADR-003 — Katalog lokal SQLite FTS5 dan cache TTL 24 jam
- Konteks: pencarian harus cepat (NFR-03) dan beban ke portal kecil (AK-06). Pola Garut terbukti berjalan. [Fakta]
- Keputusan: katalog dan kamus di SQLite dengan indeks FTS5; isi data di berkas cache per kueri; TTL 24 jam.
- Alternatif: PostgreSQL atau mesin pencari terpisah. Ditolak karena berlebihan untuk 757 dataset dan menambah layanan.
- Konsekuensi: satu berkas basis data, mudah disalin dan diuji; tidak cocok untuk banyak instans yang menulis bersamaan.
### ADR-004 — Penanda kesegaran = waktu approve terakhir
- Konteks: di Garut updated_at ikut berubah karena views. Di Bogor mdate terbukti tidak berubah karena views dan sama dengan approve terakhir pada 754 dari 757 dataset. [Fakta]
- Keputusan: aturan KS-01 s.d. KS-05 di Dok 2.
- Konsekuensi: kesegaran mencerminkan waktu Walidata menyetujui data, bukan waktu MCP mengambilnya.
### ADR-005 — Bentuk kontrak "handle, bukan baris"
- Konteks: baris data tidak boleh masuk konteks LLM (Dok 1, FR-14). Tiga opsi dibandingkan:
| Opsi | Cara | Kelebihan | Kekurangan |
|---|---|---|---|
| A | Satu tool dengan parameter mode (ref atau rows) | Satu tool | LLM tetap bisa memanggil mode=rows; perlu penjaga tambahan |
| B | 3 tool Garut + tool fetch_rows khusus backend | Kontrak Garut utuh | X-Assistant harus menyembunyikan fetch_rows dari LLM; bergantung pada fitur klien [Belum dikonfirmasi] |
| C | get_dataset_data selalu mengembalikan data_ref berupa tautan bertanda tangan ke file server; backend mengambil baris lewat HTTP | Tidak ada tool tambahan; memakai ulang file server Garut (download_url); LLM tidak bisa membaca isi tautan | Backend perlu akses jaringan ke file server; tautan punya masa berlaku |

- Keputusan: opsi C. Garut sudah mengirim download_url untuk hasil di atas 500 baris; Bogor menjadikannya aturan untuk semua ukuran.
- Konsekuensi: kontrak tool hampir sama dengan Garut; keamanan file server menjadi wajib (ADR-008).
- Catatan integrasi (6 Nov 2026): di Citya, render_chart saat ini menerima angka dari model. Agar opsi C ADR-005 bermakna, render_chart perlu menerima data_ref dan nama kolom, lalu server Citya menghitung titik grafiknya (ADR-011).
### ADR-006 — Validasi kamus kolom saat pertama kali diminta
- Keputusan: aturan KK-08 di Dok 2; hasil disimpan di SQLite per TTL.
- Alternatif: validasi 757 dataset saat sinkron. Ditolak karena butuh 757 permintaan tambahan per 24 jam.
- Konsekuensi: permintaan pertama ke suatu dataset sedikit lebih lambat.
### ADR-007 — Tandai data janggal, jangan perbaiki
- Konteks: portal memuat data janggal, misalnya lalu lintas Pajajaran 2024 bernilai 0 di semua segmen. [Fakta] Kewenangan memperbaiki ada di Walidata.
- Keputusan: aturan QC-01 s.d. QC-04 di Dok 2; nilai asli diserahkan apa adanya beserta quality_flags.
- Konsekuensi: X-Assistant wajib membaca dan menyebutkan tanda mutu; renderer menggambar periode bertanda sebagai celah.
### ADR-008 — Keamanan file server
- Konteks: file server Garut tanpa autentikasi. [Fakta] Dengan opsi C, file server menyajikan semua baris.
- Keputusan: data_ref berisi id cache, waktu kedaluwarsa, dan tanda tangan HMAC-SHA256; file server hanya di jaringan internal; masa berlaku 15 menit [Asumsi].
- Alternatif: token statis bersama. Ditolak karena tautan bocor berlaku selamanya.
- Konsekuensi: backend X-Assistant harus mengambil baris segera setelah tool dipanggil.
### ADR-009 — Stack
- Keputusan: Python 3.11+, FastMCP, Pydantic v2, pydantic-settings, httpx, SQLite (FTS5), pytest. Sesuai instruksi project dan pola Garut.
- Konsekuensi: tim yang terbiasa TypeScript/Laravel perlu menyesuaikan; kontrak Visual di prototipe sudah Pydantic.
### ADR-010 — Sinkron katalog di worker terpisah
- Konteks: sinkron penuh mengambil 8 halaman dengan jeda; tidak boleh menahan permintaan tool.
- Keputusan: sync_worker.py dijalankan terjadwal (cron atau loop internal) dan menulis ke SQLite yang sama; server tool hanya membaca.
- Alternatif: sinkron di dalam proses server saat TTL habis, seperti Garut. Lebih sederhana, tetapi permintaan yang memicu sinkron menjadi lambat.
- Konsekuensi: dua proses; SQLite memakai mode WAL agar baca dan tulis tidak saling mengunci.
### ADR-011 — Hubungan MCP dengan konektor Open Data bawaan Citya
- Konteks: Citya AI yang sudah berjalan membaca portal yang sama lewat konektor Open Data bawaannya: ingest manual atau cron, pencarian vektor (search_datasets), get_dataset_rows, dan render_chart. Konektor itu belum memakai MCP dan belum punya status keresmian, penanda kesegaran, missing_periods, maupun tanda mutu; angka di render_chart diisi model. [Fakta, diamati 6 Nov 2026]
- Opsi Ganti: MCP menggantikan jalur Open Data di Citya untuk semua angka; search_datasets Citya tetap dipakai untuk dokumen, SIRUP, dan media sosial.
- Opsi Berdampingan: keduanya berjalan berdampingan dan model memilih tool. Paling cepat dipasang, tetapi dua jalur bisa memberi angka berbeda (R-11 di Dok 1).
- Opsi Di dalam Citya: tanpa MCP; aturan Dok 2 dan handle data dibangun langsung di dalam Citya (TypeScript). Satu aplikasi saja, tetapi pola MCP Garut tidak dipakai ulang dan aturan data terikat ke Citya.
- Usulan: Berdampingan dengan pembagian kerja: RAG Citya untuk teks (dokumen kota, regulasi, media sosial, pengadaan), MCP untuk angka statistik resmi. Dataset Open Data di indeks RAG cukup berupa metadata atau dikeluarkan; instruksi sistem melarang mengutip angka dari hasil pencarian dokumen; render_chart diubah agar titik grafik dihitung server Citya dari data_ref. Opsi Di dalam Citya tidak dipilih karena pola Garut juga menempatkan MCP di luar aplikasi. [Usulan, 6 Nov 2026]
- Status: Diusulkan; menunggu keputusan lead technical dan konfirmasi Pak Ridho bahwa Citya bisa ditambah klien MCP (A-04 di Dok 1).
## 8. Keterlacakan kebutuhan
Setiap kebutuhan wajib di Dok 1 punya komponen penanggung jawab; tidak ada kebutuhan yang belum terpetakan.
| Kebutuhan Dok 1 | Komponen | ADR / aturan Dok 2 |
|---|---|---|
| FR-01 sinkron katalog | sync_worker, services/catalog | ADR-003, ADR-010 |
| FR-02 search_datasets | tools/search, services/catalog | ADR-003 |
| FR-03 get_dataset_info | tools/info, services/dictionary | ADR-006, KK |
| FR-04, FR-14 handle | tools/data, fileserver | ADR-005, ADR-008 |
| FR-05, FR-11 cache dan stale | storage/cache, services/freshness | ADR-003, KS-05 |
| FR-06 provenance | services/freshness, models.Provenance | ADR-004 |
| FR-07 status keresmian | services/officialness | Dok 1 Kewenangan |
| FR-08 normalisasi | services/normalize | NB-01 s.d. NB-08 |
| FR-09 saring kueri | services/catalog | - |
| FR-10 galat terstruktur | models.ToolEnvelope | bagian 5 |
| FR-12, FR-13 kamus kolom | services/dictionary, overrides/ | ADR-006, KK-07 |
| FR-15 metadata dipangkas | tools/info | - |
| FR-16 batas token | tools/* | MCP_MAX_RESPONSE_TOKENS |
| FR-17 cakupan periode | services/periods | CP |
| NFR-01 akses sopan | adapters/opendata_jabar | AK |
| NFR-06 keamanan file server | fileserver | ADR-008 |
| NFR-08 keterujian | adapters/base, tests/fixtures | ADR-002 |
| Mutu data (Dok 2 bagian 7) | services/quality | ADR-007, QC |

## 9. Hal yang belum ditentukan
- ☐ Lokasi server produksi dan apakah MCP satu jaringan dengan backend X-Assistant
- ☐ Nilai MCP_MAX_RESPONSE_TOKENS, menunggu tag model Ollama dan num_ctx
- ☐ Apakah backend X-Assistant bisa mengambil baris lewat data_ref sebelum tautan kedaluwarsa (bergantung pada akses repo Citya)
- ☒ Cara X-Assistant memanggil MCP: terjawab, streamable-http, karena backend Citya TypeScript tidak praktis menjalankan proses Python lewat stdio (6 Nov 2026)
- ☐ Hubungan MCP dengan konektor Open Data bawaan Citya: ganti, berdampingan, atau dibangun di dalam Citya (ADR-011); menunggu keputusan lead technical
## Referensi
- Dokumen 1 — Ruang Lingkup dan Kebutuhan MCP Kota Bogor
- Dokumen 2 — Spesifikasi Data Sumber MCP Kota Bogor
- sccicitb/satu-data-mcp, commit 2ef8cf5, contoh pola (hanya dibaca)
- Model C4 untuk notasi diagram arsitektur
