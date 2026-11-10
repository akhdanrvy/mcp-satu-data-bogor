> Ekspor otomatis dari Google Docs (versi acuan) pada 10 Nov 2026. Jangan edit di sini; edit di Google Docs lalu ekspor ulang. Diagram tidak ikut terekspor.

# Dokumen 1 — Ruang Lingkup dan Kebutuhan MCP Kota Bogor
MCP Kota Bogor adalah server MCP yang membaca 757 dataset resmi dari opendata.kotabogor.go.id dan menyajikannya ke X-Assistant Citya lewat 3 tool, dengan provenance dan status keresmian di setiap respons. Dokumen ini menetapkan batas cakupan dan kebutuhannya sebelum spesifikasi data, arsitektur, dan tool disusun.
## Info dokumen
| Hal | Isi |
|---|---|
| Versi | 0.2, draf untuk diskusi (6 Nov 2026: disesuaikan dengan Citya yang sudah berjalan) |
| Pemilik | Akhdan Ravi Andaman (SCCIC ITB) |
| Reviewer | - |
| Dokumen acuan | Ringkasan X-Assistant MCP Bogor v1.1; Profil API Open Data Kota Bogor v1.0 |
| Dokumen berikutnya | Dok 2 Spesifikasi Data Sumber, Dok 3 Arsitektur dan ADR, Dok 4 Spesifikasi Tool, Dok 5 Rencana Uji |

Penanda yang dipakai: [Fakta] sudah dikonfirmasi tim atau diamati langsung dari API; [Asumsi] dipakai sampai ada konfirmasi; [Belum ditentukan] keputusan yang masih terbuka. Kebutuhan bernomor (FR, NFR) dirujuk oleh dokumen berikutnya.
## Latar belakang dan tujuan
X-Assistant butuh jalur tetap ke data resmi Kota Bogor agar jawabannya ke Wali Kota bisa dipertanggungjawabkan. Pola ini sudah dipakai di Garut: X-Assistant Citya Garut memakai MCP Satu Data yang memindai dan menyimpan cache dataset dari API Satu Data Kabupaten Garut. [Fakta]
Pada 5 November 2026 lead technical memutuskan MCP Bogor mengambil data dari opendata.kotabogor.go.id, terpisah dari data yang sedang dikumpulkan tim IDS dan Urban, agar MCP tidak bergantung pada pengumpulan data tersebut. [Fakta] Portal itu dikelola Diskominfo, berjalan di atas platform Open Data Jabar, dan berisi 757 dataset dari 39 OPD. [Fakta, diamati 5 Nov 2026]
Citya AI yang sudah berjalan (bogora.devserver.web.id) ternyata sudah membaca portal yang sama lewat konektor Open Data bawaannya: data di-ingest manual atau lewat cron, dicari dengan pencarian vektor (search_datasets), diambil dengan get_dataset_rows, dan digambar dengan render_chart. Konektor itu belum memakai MCP dan belum punya status keresmian, penanda kesegaran, missing_periods, maupun tanda mutu. [Fakta, diamati 6 Nov 2026] MCP Kota Bogor mengisi celah tersebut; hubungannya dengan konektor bawaan diputuskan di ADR-011 (Dok 3).
Tujuan MCP Kota Bogor:
- T1. Akses terpadu. X-Assistant dapat menemukan, memahami, dan mengambil dataset portal lewat 3 tool dengan kontrak yang sama dengan MCP Garut.
- T2. Jawaban dapat ditelusuri. Setiap data yang diserahkan membawa sumber, OPD produsen, penanda kesegaran, dan status keresmian.
- T3. Angka bukan dari LLM. MCP menyediakan data dan metadata yang memungkinkan backend menghitung angka; LLM tidak menghasilkan angka sendiri.
- T4. Tahan perubahan sumber. Lapisan adaptor membuat pergantian atau penambahan sumber data hanya menyentuh satu bagian.
## Pemangku kepentingan dan pengguna
Pengguna langsung MCP adalah X-Assistant (mesin), bukan manusia; Wali Kota menerima hasilnya lewat X-Assistant.
| Pihak | Peran terhadap MCP | Kepentingan utama |
|---|---|---|
| Wali Kota Bogor | Pengguna akhir tidak langsung | Jawaban cepat, benar, dan bersumber resmi |
| Tim Chat Assistant (Pak Ridho) | Konsumen MCP: memanggil tool dari X-Assistant | Kontrak tool stabil, respons ringkas sesuai konteks model |
| Lead technical | Penentu keputusan teknis dan reviewer | Arsitektur konsisten dengan pola Garut |
| Diskominfo Kota Bogor | Pengelola portal; Walidata [Asumsi] | Data dipakai sesuai ketentuan, server tidak terbebani |
| OPD produsen data (39 OPD) | Pemilik data di portal | Data mereka dikutip benar beserta sumbernya |
| Tim IDS dan Urban | Di luar cakupan versi ini | Kemungkinan menjadi sumber kedua [Belum ditentukan] |
| SCCIC ITB | Perancang dan pembangun MCP | Dokumen disetujui sebelum build |

## Ruang lingkup
Versi ini hanya membangun MCP yang membaca opendata.kotabogor.go.id; seluruh komponen X-Assistant lain dan sumber data lain berada di luar cakupan.
Dalam cakupan
- Adaptor sumber data untuk API Open Data Kota Bogor (pola platform Open Data Jabar).
- Katalog lokal (SQLite FTS5) yang disinkronkan dari 757 dataset portal.
- Cache isi data dengan TTL dan file server untuk hasil besar.
- Tiga tool: search_datasets, get_dataset_info, get_dataset_data.
- Provenance, penanda kesegaran, dan status keresmian di setiap respons.
- Konfigurasi, logging, smoke test, dan unit test dengan fixture.
- Transport stdio dan streamable-http.
Di luar cakupan
- Portal dan API open data (milik Pemkot Bogor).
- Data IDS/Urban dan adaptor berkas CSV/Excel [Belum ditentukan].
- Satu Peta (geospasial) dan data.kotabogor.go.id [Belum ditentukan].
- Data non-publik: CCTV, ATCS, data yang menyangkut orang.
- Komponen X-Assistant: UI chat, orkestrator LLM, RAG dokumen, renderer visual, autentikasi pengguna.
- Tool tingkat tinggi find_metric dan make_visual (fase 2, setelah akses repo Citya).
- Hosting produksi dan operasional 24/7.
## Konteks sistem dan sumber data
MCP Kota Bogor berada di tengah: ia hanya membaca API publik portal dan hanya melayani X-Assistant, tanpa menulis apa pun ke portal.

konteks sistem MCP Kota Bogor · 3 wilayah tanggung jawab
Data mengalir satu arah dari OPD ke Wali Kota. MCP memakai tiga endpoint portal: /api/datasets untuk katalog, /api/datasets/{slug} untuk detail, dan /api/bigdata/{schema}/{table} untuk isi data dengan filter di server. Rincian struktur respons ada di Profil API Open Data Kota Bogor dan akan ditetapkan di Dok 2.
## Kebutuhan fungsional
Ada 17 kebutuhan fungsional; 14 wajib untuk MVP, 3 sebaiknya. Rincian parameter dan bentuk respons ditetapkan di Dok 4.
| ID | Kebutuhan | Prioritas |
|---|---|---|
| FR-01 | Menyinkronkan seluruh katalog dari /api/datasets (semua halaman) ke katalog lokal secara terjadwal dan manual | Wajib |
| FR-02 | search_datasets: pencarian teks dengan filter OPD, topik, dan tahun; hasil berhalaman | Wajib |
| FR-03 | get_dataset_info: deskripsi, OPD, topik, satuan, frekuensi, rentang tahun, nama kolom, provenance, status keresmian | Wajib |
| FR-04 | get_dataset_data: menyiapkan data dengan filter dan urutan yang diteruskan ke server, lalu mengembalikan referensi data (FR-14), bukan baris penuh | Wajib |
| FR-05 | Cache isi data dengan TTL dan opsi force_refresh | Wajib |
| FR-06 | Setiap respons membawa provenance: URL sumber, OPD, penanda kesegaran, waktu pengambilan, periode data | Wajib |
| FR-07 | Setiap dataset diberi status keresmian (Resmi atau Draf) sesuai aturan bagian Kewenangan | Wajib |
| FR-08 | Normalisasi data: waktu dengan offset tidak baku (+07:07), nilai kosong menjadi null, angka tetap bertipe angka | Wajib |
| FR-09 | Kueri pencarian disaring sebelum diteruskan ke FTS5 agar tanda baca tidak memicu galat | Wajib |
| FR-10 | Galat terstruktur (tidak ditemukan, sumber tidak tersedia, data kedaluwarsa); respons galat tidak memuat angka | Wajib |
| FR-11 | Melayani dari cache saat portal tidak tersedia, dengan penanda kedaluwarsa | Sebaiknya |
| FR-12 | Kamus kolom minimal: nama kolom, satuan dataset, dan nilai unik untuk kolom kategori (dari metadata_filter) | Sebaiknya |
| FR-13 | Kamus kolom terkurasi (arti dan satuan per kolom) untuk dataset prioritas [Belum ditentukan] | Sebaiknya |
| FR-14 | Handle, bukan baris: respons yang dibaca LLM memuat data_ref, jumlah baris, nama kolom, periode, provenance, dan pratinjau maksimal 5 baris yang hanya memuat kolom dimensi (misalnya tahun dan wilayah), tanpa kolom nilai; baris penuh hanya bisa diambil backend lewat data_ref atau download_url | Wajib |
| FR-15 | Metadata dipangkas sebelum dikirim: deskripsi menjadi teks polos, daftar nilai unik dibatasi ke sejumlah nilai teratas | Wajib |
| FR-16 | Batas keras ukuran respons dalam token; respons yang dipotong menyatakannya secara eksplisit | Wajib |
| FR-17 | Cakupan periode: get_dataset_data membandingkan periode yang diminta dengan yang tersedia dan mengembalikan periods_found serta missing_periods (contoh: diminta 2025 dan 2026, tersedia 2025, maka missing_periods = [2026]) | Wajib |

## Kebutuhan non-fungsional
Target angka di bawah adalah usulan awal untuk dibahas, kecuali yang ditandai lain.
| ID | Aspek | Kebutuhan | Target usulan |
|---|---|---|---|
| NFR-01 | Akses sopan | Jeda antarpermintaan, backoff pada 429 dan 5xx, User-Agent yang menyebut nama aplikasi, tanpa uji beban | Jeda ≥ 0,2 detik; maksimal 5 percobaan ulang |
| NFR-02 | Kesegaran | Katalog dan cache data diperbarui berkala | TTL 24 jam, sama dengan Garut |
| NFR-03 | Kinerja | Pencarian dari katalog lokal; data dari cache | Pencarian < 0,5 detik; data dari cache < 1 detik |
| NFR-04 | Ketahanan | Portal tidak tersedia tidak membuat MCP gagal total | Lihat FR-11 |
| NFR-05 | Ukuran respons | Respons tool muat dalam konteks model | Batas baris inline [Belum ditentukan], menunggu tag model dan num_ctx |
| NFR-06 | Keamanan | File server tidak terbuka bebas di jaringan publik | Mekanisme [Belum ditentukan], dibahas di Dok 3 |
| NFR-07 | Observabilitas | Log permintaan ke portal dan pemanggilan tool, tanpa isi percakapan | Log terstruktur JSON |
| NFR-08 | Keterujian | Adaptor dapat diganti fixture rekaman sehingga tes tidak menyentuh server pemerintah | Unit test tanpa jaringan |
| NFR-09 | Portabilitas | Python, FastMCP, Pydantic v2, pytest; stdio dan streamable-http | Python 3.11+ [Asumsi] |
| NFR-10 | Konfigurasi | Base URL, TTL, jeda, batas baris, dan port diatur lewat variabel lingkungan; tidak ada nilai sumber yang ditulis di kode | README dengan contoh .env |

## Kewenangan (W), status keresmian, dan privasi
MCP adalah konsumen data resmi, bukan penerbit: hanya data yang diterbitkan Walidata yang boleh dipakai untuk jawaban ke Wali Kota.
Dasar. Perwali Kota Bogor No. 39 Tahun 2024 menempatkan Walidata sebagai pihak yang memeriksa dan menyajikan data dari Produsen Data. Status Perwali ini masih perlu diverifikasi di JDIH Kota Bogor. [Asumsi]
Status keresmian
| Status | Syarat | Boleh untuk jawaban ke Wali Kota |
|---|---|---|
| Resmi | Terbit di opendata.kotabogor.go.id dengan validate=approve dan klasifikasi=public | Ya |
| Draf | Data di luar portal, belum diverifikasi Walidata | Tidak; hanya untuk pengembangan dan uji, selalu berlabel "Data draf, belum resmi" |

Pada 5 November 2026 seluruh 757 dataset memenuhi syarat Resmi. [Fakta] Penetapan ini bergantung pada asumsi bahwa Diskominfo adalah Walidata. [Asumsi] MCP tetap memeriksa kedua field di setiap sinkronisasi, karena nilainya bisa berubah.
Aturan angka
- MCP mengembalikan data dan metadata apa adanya; MCP tidak membuat, menebak, atau membulatkan angka.
- Data mentah tidak masuk konteks LLM. Arah yang disepakati (5 Nov 2026): handle, bukan baris. LLM hanya menerima referensi data, ringkasan, dan pratinjau; backend yang mengambil baris penuh dan menghitung (FR-14 sampai FR-16).
- Respons galat atau data kedaluwarsa ditandai jelas agar konsumen tidak menampilkan angkanya.
Privasi (prinsip: arus, bukan dossier)
- Field akun petugas (username, user_name, history_draft[].username, cuid) tidak disimpan di katalog dan tidak diteruskan ke konsumen.
- MCP tidak menyimpan isi percakapan atau identitas penanya.
- Data yang menyangkut orang perorangan berada di luar cakupan; jika suatu dataset portal ternyata memuatnya, dataset itu dikecualikan dan dilaporkan ke Diskominfo.
## Batasan, asumsi, dan dependensi
Empat asumsi di bawah diterima sebagai dasar kerja sejak 5 November 2026, karena tim diminta memakai API portal ini; jika salah satunya terbukti keliru, dampaknya ditinjau ulang.
| ID | Jenis | Pernyataan |
|---|---|---|
| B-01 | Batasan | Repo sccicitb/satu-data-mcp hanya dibaca sebagai contoh pola; tidak diubah atau di-push |
| B-02 | Batasan | Portal dan API milik Pemkot; tim tidak bisa mengubah struktur, isi, atau batas aksesnya |
| B-03 | Batasan | API portal tidak bisa diakses dari sandbox Claude; uji dengan API nyata dijalankan di laptop |
| B-04 | Batasan | Stack: Python, FastMCP, Pydantic v2, pytest |
| A-01 | Asumsi | Diskominfo Kota Bogor adalah Walidata menurut Perwali 39/2024 |
| A-02 | Asumsi | Akses otomatis ke API publik portal diizinkan dengan pola akses sopan (NFR-01) |
| A-03 | Asumsi | Struktur API portal stabil selama pengembangan; perubahan hanya menyentuh adaptor |
| A-04 | Asumsi | Citya (backend TypeScript, Vercel AI SDK) dapat ditambah klien MCP dan memanggil tool MCP lewat streamable-http. Per 6 Nov 2026 Citya Bogor belum memakai MCP; tool-nya ditulis di dalam aplikasi. [Fakta] Kesanggupan menambah klien MCP perlu dikonfirmasi Pak Ridho |
| D-01 | Dependensi | Ketersediaan opendata.kotabogor.go.id |
| D-02 | Dependensi | Tag model Ollama dan num_ctx dari tim, untuk menentukan batas ukuran respons (NFR-05) |
| D-03 | Dependensi | Akses repo Citya untuk integrasi fase 2 |

## Risiko dan mitigasi
Risiko terbesar adalah jawaban yang terlihat benar tetapi salah kolom atau salah periode, bukan gangguan teknis; karena itu mitigasi diutamakan pada metadata dan provenance.
| ID | Risiko | Dampak | Mitigasi |
|---|---|---|---|
| R-01 | Arti kolom tidak tersedia di API, sehingga kolom yang dipakai salah | Angka salah ke Wali Kota | Kamus kolom (FR-12, FR-13); golden set menguji pemilihan kolom |
| R-02 | Data lama dijawab seolah kondisi terkini (contoh: tahun data terakhir 2023) | Keputusan berdasar data usang | Periode data wajib di provenance (FR-06); narasi menyebut tahun data |
| R-03 | mdate ikut berubah karena jumlah views, seperti updated_at Garut | Penanda kesegaran menyesatkan | Uji sebelum dipakai; cadangan: waktu approve terakhir di history_draft |
| R-04 | Rate limit atau pemblokiran IP | MCP tidak bisa sinkron | NFR-01, cache (FR-11), koordinasi dengan Diskominfo |
| R-05 | Ketentuan penggunaan tidak mengizinkan akses otomatis | Akses harus dihentikan | Baca ketentuan dan minta izin sebelum sinkron terjadwal berjalan |
| R-06 | Platform Open Data Jabar diperbarui dan struktur API berubah | Adaptor rusak | Tes kontrak dan smoke test berkala; perubahan terbatas di adaptor |
| R-07 | Pertanyaan Wali Kota tidak tercakup 757 dataset | Model menebak jawaban | Tool mengembalikan "tidak ditemukan" secara eksplisit; narasi menyatakan data tidak tersedia |
| R-08 | Diskominfo ternyata bukan Walidata | Status Resmi tidak sah | Konfirmasi A-01; aturan status bisa diubah di konfigurasi |
| R-09 | Data mentah masuk konteks LLM melalui get_dataset_data. Pola ini sudah terlihat di Citya: get_dataset_rows mengembalikan baris dan render_chart menerima angka dari model [Fakta, diamati 6 Nov 2026] | Angka dikarang atau konteks penuh | Arah disepakati: handle, bukan baris (FR-14 sampai FR-16); bentuk kontraknya dikunci di Dok 3 (ADR-005 opsi C) |
| R-10 | Indikator diganti diam-diam: model memakai dataset lain yang mirip untuk menambal periode kosong atau menyambung tren (contoh: Persentase Kepala Keluarga Miskin turun dari 75,32% pada 2019 ke 6,53% pada 2024, kemungkinan karena definisi berubah) | Tren palsu dalam laporan ke Wali Kota | missing_periods (FR-17) membuat periode kosong eksplisit; satu visual hanya memakai satu dataset; indikator lain hanya ditawarkan sebagai saran berlabel "definisi berbeda"; kasus kemiskinan 2025 vs 2026 masuk golden set |
| R-11 | Tumpang tindih dengan konektor Open Data bawaan Citya: dua jalur membaca portal yang sama | Angka berbeda untuk pertanyaan yang sama; beban ganda ke portal | Keputusan ADR-011 (Dok 3); jika berdampingan, angka dari portal hanya lewat MCP; jadwal sinkron MCP tidak bertabrakan dengan ingest Citya |
| R-12 | Model hosted (DeepSeek) atau pengenal suara bawaan browser dipakai untuk Kota Bogor | Pertanyaan pengguna dan metadata keluar dari server Pemkot | Data portal bersifat publik sehingga risikonya rendah; respons MCP tanpa baris penuh (ADR-005); pilihan model untuk Bogor dikonfirmasi ke lead technical |

## Kriteria penerimaan
Dokumen ini disetujui jika lead technical menerima batas cakupan; keempat asumsi A-01 sampai A-04 sudah diterima sebagai dasar kerja.
MVP MCP Kota Bogor dianggap selesai jika:
- ☐ Katalog lokal berisi jumlah dataset yang sama dengan pagination.total_data dari portal saat sinkron
- ☐ Ketiga tool lulus smoke test terhadap API nyata, dijalankan di laptop
- ☐ Setiap respons tool memuat provenance lengkap (FR-06) dan status keresmian (FR-07), dan respons yang dibaca LLM hanya memuat pratinjau baris (FR-14)
- ☐ Portal yang dimatikan (disimulasikan dengan fixture) menghasilkan galat atau data kedaluwarsa yang bertanda, tanpa angka segar palsu
- ☐ Unit test dengan fixture rekaman lulus tanpa akses jaringan
- ☐ Tidak ada field akun petugas di katalog maupun respons
- ☐ Kasus golden set yang relevan dengan dataset portal dipetakan ke dataset yang benar; target persentase [Belum ditentukan], ditetapkan di Dok 5
## Hal yang belum ditentukan
Lima hal masih terbuka. Empat dijawab lewat uji teknis atau dokumen berikutnya; satu menunggu keputusan lead technical (ADR-011).
Uji teknis dan dokumen berikutnya
- ☐ Apakah mdate berubah karena views? Menentukan penanda kesegaran (R-03, Dok 2)
- ☐ Cara membangun kamus kolom terkurasi dan daftar dataset prioritasnya (FR-13, Dok 2)
- ☐ Bentuk kontrak handle: opsi A (satu tool dengan parameter mode) atau opsi B (3 tool Garut tetap, ditambah fetch_rows khusus backend); condong ke B (R-09, Dok 3)
- ☐ Mekanisme pengamanan file server (NFR-06, Dok 3)
- ☐ Hubungan MCP dengan konektor Open Data bawaan Citya: opsi A, B, atau C (ADR-011 di Dok 3); menunggu keputusan lead technical
## Glosarium dan referensi
| Istilah | Arti |
|---|---|
| MCP | Model Context Protocol; protokol standar agar aplikasi LLM memanggil tool dan sumber data |
| Tool | Fungsi yang disediakan server MCP dan dipanggil X-Assistant |
| Walidata | Perangkat daerah yang memeriksa dan menyajikan data dari Produsen Data (Perwali 39/2024) |
| Produsen Data | OPD yang menghasilkan data |
| Provenance | Keterangan asal data: sumber, produsen, waktu, dan periode |
| Penanda kesegaran | Waktu terakhir data diperbarui di sumber, bukan waktu diambil MCP |
| TTL | Batas umur cache sebelum data diambil ulang |
| FTS5 | Fitur pencarian teks penuh SQLite |
| Golden set | Kumpulan pertanyaan uji dengan jawaban yang diharapkan |

Referensi
- Open Data Kota Bogor dan Ketentuan Penggunaan
- Profil API Open Data Kota Bogor v1.0 dan Ringkasan X-Assistant MCP Bogor v1.1, di Project Xassistant Technical
- Perwali Kota Bogor No. 39 Tahun 2024 tentang Pedoman Penyelenggaraan Statistik Sektoral; teks dari pasal.id, belum diverifikasi di JDIH
