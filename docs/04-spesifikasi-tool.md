> Ekspor otomatis dari Google Docs (versi acuan) pada 10 Nov 2026. Jangan edit di sini; edit di Google Docs lalu ekspor ulang. Diagram tidak ikut terekspor.

# Dokumen 4 — Spesifikasi Tool MCP Kota Bogor
MCP Kota Bogor menyediakan tiga tool untuk Citya AI: search_datasets, get_dataset_info, dan get_dataset_data. Saat dipasang, namanya diberi awalan satudata_ agar tidak bentrok dengan tool bawaan Citya. MCP berjalan di luar Citya, berdampingan dengan tool bawaannya dengan pembagian kerja: RAG Citya untuk teks, MCP untuk angka statistik resmi. Baris data penuh tidak pernah masuk respons tool; backend Citya mengambilnya lewat data_ref di port 8799, lalu menghitung titik grafik untuk render_chart. Dokumen ini menetapkan parameter, bentuk respons, kode galat, kontrak data_ref, dan 12 kartu metrik prioritas yang menutup tahap 0 menuju Tier 2.
## Info dokumen
| Hal | Isi |
|---|---|
| Versi | 0.2, draf untuk diskusi (6 Nov 2026: disesuaikan dengan Citya yang sudah berjalan) |
| Pemilik | SCCIC ITB |
| Pembaca utama | Tim Chat Assistant (Pak Ridho), lead technical |
| Dokumen acuan | Dok 1 Ruang Lingkup v0.1; Dok 2 Spesifikasi Data Sumber v0.1; Dok 3 Arsitektur dan ADR v0.1 |
| Status kontrak | contract_version 0.1 (berubah tanpa pemberitahuan sampai 1.0) |

Penanda: [Fakta] sudah dikonfirmasi atau diamati; [Asumsi] dipakai sampai ada konfirmasi; [Usulan] rancangan dokumen ini yang menunggu persetujuan; [Belum ditentukan] keputusan yang masih terbuka.
## 1. Alur pemanggilan
Satu pertanyaan berangka butuh tiga tool call berurutan, lalu satu pengambilan data oleh backend. LLM membaca metadata dan memilih; backend membaca baris dan menghitung.

alur pemanggilan · 3 tool call, 1 pengambilan data_ref
Langkah 1–3 dipanggil model lewat MCP di port 8800. Langkah 4 dijalankan kode backend, bukan model, sehingga baris penuh tidak pernah masuk konteks LLM. Langkah 5 dan 6 milik X-Assistant: kontrak Visual, choose_chart, dan check_narrative di prototipe. Model boleh melewati langkah 2 jika slug dan kolom sudah diketahui dari kartu metrik (bagian 8).
### Pembagian kerja dengan tool Citya
Citya AI sudah punya tiga tool bawaan [Fakta, diamati 6 Nov 2026]. MCP tidak menggantikannya; masing-masing punya wilayah sendiri. [Usulan, ADR-011 Dok 3]
| Kebutuhan | Tool | Sumber |
|---|---|---|
| Teks: dokumen kota, regulasi, konten media sosial, pengadaan | search_datasets milik Citya (RAG) | Indeks hasil ingest Citya |
| Angka statistik resmi Kota Bogor | satudata_search_datasets, satudata_get_dataset_info, satudata_get_dataset_data | MCP Kota Bogor → portal Satu Data |
| Grafik | render_chart milik Citya | Titik dihitung server Citya dari data_ref |
| Pertanyaan campuran, mis. realisasi PAD vs target RPJMD | Keduanya, berurutan | Target dari RAG, realisasi dari MCP; dibandingkan di kode |

Angka statistik hanya boleh datang dari tool MCP. Dataset Open Data di indeks RAG Citya cukup berupa metadata (judul dan deskripsi) atau dikeluarkan dari indeks, agar model tidak mengutip angka dari potongan teks. Instruksi sistem Citya memuat aturan: "Untuk angka statistik Kota Bogor, selalu gunakan tool satudata_. Jangan mengutip angka dari hasil pencarian dokumen." Golden set ditambah kasus campuran dan kasus jebakan, yaitu angka yang juga muncul di teks dokumen.
### Nama tool
Nama dasar mengikuti Garut. Saat dipasang, nama diberi awalan dari MCP_TOOL_PREFIX (bawaan satudata_), sehingga model melihat satudata_search_datasets dan tidak tertukar dengan search_datasets milik Citya. Bagian 3–5 menulis tool dengan nama dasarnya. [Usulan]
### Perubahan di render_chart Citya
Saat ini angka di render_chart diisi model [dugaan kuat, dari cara tampilan merender]. Usulannya: model hanya mengisi rujukan data dan pilihan tampilan; server Citya mengambil data_ref, menghitung titik, lalu membentuk spesifikasi grafik dengan format yang sudah ada. [Usulan, perlu persetujuan Pak Ridho]
| Field render_chart (format sekarang) | Diisi oleh | Sumber nilai |
|---|---|---|
| type (bar \| line) | Model | Pilihan tampilan |
| title | Model | Teks judul, tanpa angka |
| data[{label, value}], 2–60 titik | Server Citya | Baris data_ref: label dari kolom dimensi, value dari kolom nilai |
| unit | Server Citya | columns[].unit |
| xLabel, yLabel | Server Citya | columns[].meaning |
| source{title, url} | Server Citya | Nama dataset dan provenance.source_url |

Masukan baru dari model ke render_chart: {data_ref, x_column, y_column, type, title}. Periode di missing_periods digambar sebagai celah, dan periode bertanda QC-01 tidak digambar sebagai 0.
## 2. Amplop respons bersama
Setiap tool mengembalikan objek JSON dengan field di bawah ini, termasuk saat gagal. Field khusus tiap tool ada di dalam result. [Usulan]
| Field | Tipe | Isi | Aturan |
|---|---|---|---|
| contract_version | string | Versi kontrak, mis. "0.1" | Selalu ada |
| status | "ok" \| "partial" \| "error" | partial = ada hasil, tetapi terpotong, kedaluwarsa, atau sebagian periode kosong | Selalu ada |
| error | objek atau null | {code, message, retryable}; kode di bagian 7 | Wajib saat status=error; respons galat tidak memuat angka (FR-10) |
| request_id | string atau null | Dikembalikan apa adanya dari parameter | Untuk menautkan log MCP dengan audit X-Assistant |
| provenance | objek | source_url, opd, data_updated_at, retrieved_at, period_start, period_end, served_from (live \| cache \| stale_cache) | Wajib untuk tool yang menyentuh satu dataset (FR-06) |
| officialness | objek | {status: "resmi" \| "draf", basis}; basis mis. "validate=approve; klasifikasi=public" | Wajib per dataset (FR-07) |
| quality_flags | daftar | {code, column, period, detail}; kode QC-01 s.d. QC-03 dari Dok 2 | Daftar kosong jika tidak ada temuan (ADR-007) |
| truncated | boolean | true jika respons dipangkas karena batas token | Disertai truncated_reason (FR-16) |
| result | objek atau null | Isi khusus tiap tool | Null saat status=error |

Aturan yang berlaku untuk semua tool:
- Waktu ditulis ISO 8601 dengan offset baku +07:00; offset +07:07 dari portal dinormalkan (FR-08).
- Nilai kosong menjadi null, tidak pernah "-" atau 0.
- Field akun petugas (username, history_draft[].username) tidak pernah muncul.
- data_updated_at = waktu approve terakhir di history_draft; cadangannya mdate (KS-01 s.d. KS-05).
## 3. Tool search_datasets
Mencari dataset di katalog lokal (SQLite FTS5) tanpa menyentuh portal. Hasilnya daftar ringkas untuk dipilih model, tanpa angka (FR-02, FR-09).
Deskripsi yang dibaca model [Usulan]
Cari dataset resmi Kota Bogor di portal Satu Data. Panggil tool ini lebih dulu setiap kali pertanyaan butuh angka. Isi query dengan kata kunci topik dalam bahasa Indonesia (mis. "kemiskinan", "pendapatan asli daerah"), bukan kalimat pertanyaan. Hasil berisi slug untuk get_dataset_info. Jika hasilnya galat NO_MATCH, katakan data tidak tersedia; jangan menebak.
Parameter
| Nama | Tipe | Wajib | Default | Validasi |
|---|---|---|---|---|
| query | string | Ya |  | 2–100 karakter; tanda baca dibuang sebelum ke FTS5 |
| opd | string | Tidak | null | Slug atau nama OPD; dicocokkan ke organisasi_id |
| topik | string | Tidak | null | Nama topik portal (45 topik); dicocokkan ke topik_id |
| tahun | integer | Tidak | null | 2000–2100; dataset yang rentang tahunnya mencakup tahun ini |
| page | integer | Tidak | 1 | ≥ 1 |
| page_size | integer | Tidak | 10 | 1–20 |
| request_id | string | Tidak | null | ≤ 64 karakter |

Isi result
| Field | Isi |
|---|---|
| total, page, page_size | Jumlah dataset cocok dan posisi halaman |
| items[] | slug, name, opd, topik, kategori, satuan, frekuensi, tahun_awal, tahun_akhir, data_updated_at, officialness.status |
| catalog_synced_at | Waktu sinkron katalog terakhir; dipakai untuk peringatan katalog usang |

Untuk tool ini provenance dan quality_flags diisi di tiap item seperlunya, bukan di level amplop. Urutan hasil: relevansi FTS5, lalu tahun_akhir terbaru.
Contoh
{"query": "pendapatan asli daerah", "tahun": 2024, "request_id": "rq-7f3a"}

{  "contract_version": "0.1", "status": "ok", "error": null, "request_id": "rq-7f3a",  "truncated": false, "quality_flags": [],  "result": {    "total": 3, "page": 1, "page_size": 10,    "catalog_synced_at": "2026-11-06T02:00:00+07:00",    "items": [{      "slug": "<slug-dataset-realisasi-pad>", "name": "Realisasi Pendapatan Asli Daerah",      "opd": "Bapenda", "topik": "Keuangan Daerah", "kategori": "indikator",      "satuan": "Rupiah", "frekuensi": "Tahunan", "tahun_awal": 2020, "tahun_akhir": 2024,      "data_updated_at": "2025-06-02T10:14:00+07:00", "officialness": {"status": "resmi"}    }]  }}
Slug, topik, dan rentang tahun pada contoh adalah ilustrasi; nilai nyata diambil dari katalog saat build.
## 4. Tool get_dataset_info
Mengembalikan penjelasan satu dataset: apa artinya, kolom mana yang dimensi dan mana yang nilai, dan periode apa yang tersedia. Model memakai ini untuk memilih kolom dan filter sebelum meminta data (FR-03, FR-12, FR-15).
Deskripsi yang dibaca model [Usulan]
Ambil penjelasan satu dataset berdasarkan slug dari search_datasets. Baca columns untuk tahu kolom nilai dan kolom dimensi, serta periods_available untuk tahu tahun yang ada. Jangan meminta tahun yang tidak tercantum seolah-olah ada. Tool ini tidak mengembalikan angka data.
Parameter
| Nama | Tipe | Wajib | Default | Validasi |
|---|---|---|---|---|
| slug | string | Ya |  | Harus ada di katalog lokal; jika tidak, galat DATASET_NOT_FOUND |
| request_id | string | Tidak | null | ≤ 64 karakter |

Isi result
| Field | Isi |
|---|---|
| slug, name, opd, topik, kategori | Identitas dataset |
| description | Deskripsi portal sebagai teks polos, dipangkas ≤ 600 karakter (FR-15) |
| satuan, frekuensi, tingkat_penyajian, cakupan | Dari metadata[] portal |
| periods_available | Daftar tahun yang benar-benar ada di data, mis. [2020, 2021, 2022, 2023, 2024] |
| columns[] | name, role (dimension \| measure \| id), type, meaning, unit, meaning_source (template \| inferred \| none), top_values (≤ 10 nilai unik, khusus kolom dimensi) |
| row_count | Jumlah baris dari count=true |
| dictionary_validated | true jika kamus kolom dari deskripsi cocok dengan kolom asli (KK-01 s.d. KK-08) |

Kamus kolom dibuat saat dataset pertama kali diminta, lalu disimpan (ADR-006). Kolom yang artinya tidak ditemukan tetap dikirim dengan meaning: null dan meaning_source: "none"; model tidak boleh menebak artinya.
Contoh potongan result.columns
[  {"name": "tahun", "role": "dimension", "type": "integer", "meaning": "Tahun anggaran", "unit": null, "meaning_source": "template", "top_values": [2020, 2021, 2022, 2023, 2024]},  {"name": "realisasi_pad", "role": "measure", "type": "number", "meaning": "Realisasi pendapatan asli daerah", "unit": "Rupiah", "meaning_source": "template", "top_values": null},  {"name": "bps_kode_kabupaten_kota", "role": "id", "type": "string", "meaning": null, "unit": null, "meaning_source": "none", "top_values": null}]
Nama kolom pada contoh adalah ilustrasi.
## 5. Tool get_dataset_data
Menyiapkan data satu dataset dengan filter yang diteruskan ke server portal, lalu mengembalikan data_ref, bukan baris (ADR-005 opsi C). Respons hanya memuat struktur, periode, dan pratinjau kolom dimensi; angka diambil backend (FR-04, FR-14, FR-17).
Deskripsi yang dibaca model [Usulan]
Siapkan data satu dataset untuk dihitung dan digambar oleh sistem. Isi periods dengan tahun yang ditanyakan pengguna. Tool ini tidak mengembalikan angka: jangan menulis angka apa pun dari respons ini. Jika missing_periods tidak kosong, katakan tahun itu belum tersedia; jangan menambalnya dengan dataset lain. Jika quality_flags tidak kosong, sebutkan catatan mutunya.
Parameter
| Nama | Tipe | Wajib | Default | Validasi |
|---|---|---|---|---|
| slug | string | Ya |  | Ada di katalog lokal |
| periods | daftar integer | Tidak | null (semua) | ≤ 20 tahun; dipakai untuk periods_found dan missing_periods |
| filters | objek | Tidak | null | Kunci = kolom berperan dimension; nilai = daftar ≤ 50; kolom lain ditolak INVALID_PARAM |
| sort | string | Tidak | "tahun:asc" | Format kolom:asc\|desc, kolom harus ada |
| force_refresh | boolean | Tidak | false | Lewati cache (FR-05); dibatasi agar tidak membebani portal |
| request_id | string | Tidak | null | ≤ 64 karakter |

periods dan filters diterjemahkan ke where JSON portal, mis. {"tahun": [2025, 2026]}. Model tidak pernah menulis where sendiri.
Isi result
| Field | Isi |
|---|---|
| data_ref | {url, expires_at, row_count, format}; rinci di bagian 6 |
| columns | Nama dan peran kolom, sama dengan get_dataset_info |
| periods_requested, periods_found, missing_periods | Contoh: diminta [2025, 2026], tersedia [2025] → missing_periods: [2026] (FR-17) |
| preview | ≤ 5 baris berisi kolom dimensi saja, tanpa kolom nilai (FR-14) |
| filters_applied | Filter yang benar-benar dikirim ke portal, setelah normalisasi label (NB-08) |

Jika missing_periods tidak kosong tetapi ada periode lain yang ditemukan, status = partial. Jika tidak ada satu pun periode yang ditemukan, status = error dengan kode PERIOD_NOT_AVAILABLE.
Contoh: kemiskinan 2025 vs 2026 (golden set F-07)
{"slug": "<slug-dataset-kemiskinan>", "periods": [2025, 2026], "request_id": "rq-91c2"}

{  "contract_version": "0.1", "status": "partial", "error": null, "request_id": "rq-91c2",  "provenance": {"source_url": "https://opendata.kotabogor.go.id/dataset/<slug-dataset-kemiskinan>", "opd": "<OPD produsen>",    "data_updated_at": "<waktu approve terakhir>", "retrieved_at": "2026-11-06T10:15:02+07:00",    "period_start": 2025, "period_end": 2025, "served_from": "live"},  "officialness": {"status": "resmi", "basis": "validate=approve; klasifikasi=public"},  "quality_flags": [], "truncated": false,  "result": {    "data_ref": {"url": "http://mcp-bogor:8799/d/<cache_id>?exp=<unix>&sig=<hmac>", "expires_at": "2026-11-06T10:30:02+07:00", "row_count": 1, "format": "json"},    "columns": [{"name": "tahun", "role": "dimension"}, {"name": "<kolom_nilai>", "role": "measure"}],    "periods_requested": [2025, 2026], "periods_found": [2025], "missing_periods": [2026],    "preview": [{"tahun": 2025}],    "filters_applied": {"tahun": [2025, 2026]}  }}
Jawaban yang benar dari X-Assistant untuk contoh ini: grafik atau kartu KPI hanya untuk 2025, ditambah kalimat bahwa data 2026 belum tersedia di portal.
## 6. Kontrak data_ref dan file server
Backend X-Assistant mengambil baris penuh dengan satu GET ke URL di data_ref, paling lambat 15 menit setelah tool dipanggil. File server hanya di jaringan internal dan hanya menerima tautan bertanda tangan (ADR-005, ADR-008).
Bentuk URL [Usulan]
http://<host-mcp>:8799/d/{cache_id}?exp={unix_detik}&sig={hmac}
- cache_id: id hasil kueri di cache, bukan slug, agar URL tidak membocorkan isi kueri.
- exp: waktu kedaluwarsa (Unix, detik); default sekarang + 900 detik [Asumsi], diatur lewat MCP_DATAREF_TTL_SECONDS.
- sig: HMAC-SHA256 atas "{cache_id}.{exp}" dengan kunci rahasia MCP_DATAREF_SECRET, dikodekan base64url tanpa padding.
- Kunci rahasia tidak dibagikan ke X-Assistant; backend cukup memanggil URL apa adanya.
Isi berkas (format json)
{  "contract_version": "0.1",  "slug": "<slug>",  "columns": [{"name": "tahun", "role": "dimension", "type": "integer", "unit": null},              {"name": "<kolom_nilai>", "role": "measure", "type": "number", "unit": "Rupiah"}],  "rows": [{"tahun": 2024, "<kolom_nilai>": 1432000000000}],  "provenance": {"...": "sama dengan respons tool"},  "officialness": {"status": "resmi"},  "quality_flags": [],  "periods_found": [2024], "missing_periods": []}
Baris sudah dinormalisasi: angka bertipe angka (NB-07), nilai kosong null, label kategori seragam dengan nilai aslinya tetap tersimpan (NB-08). Nilai yang ditandai mutu tidak diubah. Format csv boleh ditambah nanti; untuk versi 0.1 hanya json.
Respons HTTP file server
| Kode | Kapan | Yang dilakukan backend |
|---|---|---|
| 200 | Tanda tangan sah dan belum kedaluwarsa | Hitung dan gambar dari rows |
| 403 | Tanda tangan salah | Catat sebagai galat integrasi; jangan coba ulang |
| 410 | Tautan kedaluwarsa | Panggil get_dataset_data lagi dengan parameter yang sama; data kemungkinan dari cache |
| 404 | Cache sudah dibersihkan | Sama dengan 410 |
| 503 | MCP sedang tidak siap | Coba ulang sekali, lalu tampilkan galat tanpa angka |

Prasyarat yang perlu dikonfirmasi Pak Ridho: backend bisa menjangkau port 8799 di jaringan yang sama, dan mengambil data_ref segera setelah tool call selesai. [Belum ditentukan]
## 7. Kode galat
Setiap galat punya kode tetap, dan X-Assistant menanggapinya dengan perilaku tetap. Prinsipnya: gagal tampil sebagai gagal, bukan angka (FR-10). [Usulan]
| Kode | Tool | Kapan | retryable | Perilaku X-Assistant |
|---|---|---|---|---|
| INVALID_PARAM | Semua | Parameter melanggar validasi, mis. kolom filter bukan dimensi | false | Perbaiki panggilan sekali; jika gagal lagi, minta pengguna memperjelas |
| DATASET_NOT_FOUND | info, data | Slug tidak ada di katalog atau 404 dari portal | false | Kembali ke search_datasets; jangan mengarang slug |
| NO_MATCH | search | total = 0 | false | Katakan data tidak tersedia di portal; tawarkan topik terdekat bila ada (R-07) |
| PERIOD_NOT_AVAILABLE | data | Tidak satu pun periode yang diminta tersedia | false | Sebut periode terakhir yang ada (dari get_dataset_info); tanpa angka |
| SOURCE_UNAVAILABLE | info, data | Portal gagal atau timeout dan tidak ada cache | true | Katakan sumber sedang tidak bisa dihubungi; jangan menampilkan angka |
| STALE_DATA | info, data | Portal gagal, tetapi cache lama ada; dikirim sebagai status=partial dengan served_from=stale_cache | false | Tampilkan dengan label "data per <retrieved_at>" (FR-11) |
| RATE_LIMITED | info, data | Portal mengembalikan 429 setelah backoff habis (NFR-01) | true | Coba lagi nanti; jangan memanggil berulang |
| RESPONSE_TOO_LARGE | Semua | Metadata tetap melebihi batas token setelah dipangkas | false | Persempit filters atau periods |
| INTERNAL | Semua | Galat tak terduga di MCP | true | Coba sekali; catat request_id untuk tim MCP |

STALE_DATA ditulis sebagai peringatan di error walaupun status = partial, sehingga konsumen tetap bisa memakai datanya dengan label. Kode lain selalu datang dengan status = error dan result = null.
## 8. Kartu metrik prioritas
Dua belas metrik ini adalah daftar awal yang paling mungkin ditanyakan Wali Kota, diambil dari golden set v0.2. Kartu metrik memastikan satu pertanyaan selalu mengarah ke dataset, kolom, dan satuan yang sama. Daftar ini menutup syarat tahap 0 menuju Tier 2 (10–15 kartu). [Usulan]
| ID | Metrik | OPD | Satuan | Periode di portal | Kasus uji | Catatan mutu dan aturan |
|---|---|---|---|---|---|---|
| KM-01 | Realisasi pendapatan asli daerah | Bapenda | Rupiah | 2020–2024 | N-03 | Tidak ada temuan |
| KM-02 | Persentase realisasi PAD | Bapenda | % | Dicek saat build | Mutu data | 2021–2023 bernilai 0,04–0,45 → QC-02 skala |
| KM-03 | Realisasi retribusi daerah | Bapenda | Rupiah | Dicek saat build | Mutu data | 2022 = Rp55,5 juta → QC-02 pencilan |
| KM-04 | Realisasi belanja per jenis | Dicek saat build | Rupiah | Tahunan | N-04 | Pagu tidak tersedia; hanya realisasi |
| KM-05 | Sisa lebih pembiayaan anggaran (SiLPA) | Dicek saat build | Rupiah | Tahunan | N-05 | Tidak ada temuan |
| KM-06 | Angka kemiskinan Kota Bogor | Dicek saat build | % | Terakhir 2025 | N-01, F-07 | Hanya tingkat kota; permintaan kecamatan = level_not_available |
| KM-07 | Persentase kepala keluarga miskin | Dicek saat build | % | 2019–2024 | F-08 | Definisi kemungkinan berubah (75,32% → 6,53%); jangan disambung dengan KM-06 (R-10) |
| KM-08 | Kunjungan wisatawan | Disparbud | Orang | 2024 tersedia | N-06 | Angka tersimpan sebagai teks "9.160.885" → diurai NB-07 |
| KM-09 | Kunjungan pasien puskesmas | Dinkes | Orang | Dicek saat build | N-07 | Tidak ada temuan |
| KM-10 | Kecepatan lalu lintas Jl. Pajajaran (hari kerja vs libur) | Dishub | Dicek saat build | 2015–2025 | N-02 | 2024 = 0 di semua segmen → QC-01; label segmen diseragamkan NB-08 |
| KM-11 | Volume lalu lintas Jl. Pajajaran | Dishub | Dicek saat build | 2015–2025 | N-08 | 2024 = 0 → QC-01; volume 2022 dan 2023 identik di beberapa segmen (dicatat saja) |
| KM-12 | Persentase penanganan pengaduan | Inspektorat | % | Dicek saat build | O-01 | Bukan jumlah aduan warga; jangan dipakai untuk menjawab "berapa aduan hari ini" |

Setiap kartu di implementasi disimpan sebagai berkas YAML dengan field: id, nama, definisi, slug, kolom_nilai, kolom_dimensi, satuan, opd, walidata, periode_tersedia, catatan_mutu, sinonim (kata yang dipakai pengguna, mis. "PAD", "pendapatan daerah"), dan dataset_berbeda_definisi (untuk aturan R-10).
Slug, kolom nilai, dan isian "dicek saat build" diisi saat build lewat search_datasets dan get_dataset_info, lalu diperiksa manusia sebelum kartu dipakai. Daftar ini perlu divalidasi dengan 3–5 pertanyaan nyata Wali Kota. [Belum ditentukan]
## 9. Versi kontrak, batas ukuran, dan hal terbuka
Kontrak memakai nomor versi MAJOR.MINOR di setiap respons dan berkas data_ref. Selama 0.x, perubahan apa pun diumumkan ke tim Chat Assistant sebelum dirilis.
- Menambah field atau kode galat = naik MINOR; konsumen wajib mengabaikan field yang tidak dikenal.
- Menghapus, mengganti nama, atau mengubah arti field = naik MAJOR; versi lama dilayani berdampingan selama satu siklus rilis.
- Teks deskripsi tool boleh diubah tanpa naik versi, tetapi setiap perubahan diuji ulang dengan golden set.
Batas ukuran respons (FR-15, FR-16, NFR-05) [Asumsi]
| Batas | Nilai awal | Variabel lingkungan |
|---|---|---|
| Token per respons tool | 2.000 | MCP_MAX_RESPONSE_TOKENS |
| Item per halaman search_datasets | 20 | MCP_SEARCH_MAX_PAGE_SIZE |
| Nilai unik per kolom (top_values) | 10 | MCP_TOP_VALUES |
| Baris pratinjau | 5 | MCP_PREVIEW_ROWS |
| Panjang deskripsi dataset | 600 karakter | MCP_DESCRIPTION_MAX_CHARS |
| Masa berlaku data_ref | 900 detik | MCP_DATAREF_TTL_SECONDS |
| Awalan nama tool | satudata_ | MCP_TOOL_PREFIX |
| Mode cadangan baris langsung di respons | Mati; hanya untuk uji integrasi | MCP_INLINE_ROWS_FALLBACK |

Angka token ditetapkan ulang setelah tag model Qwen dan num_ctx diketahui.
Hal yang belum ditentukan
- ☐ Hubungan dengan tool Citya: usulan berdampingan dengan pembagian kerja, RAG untuk teks dan MCP untuk angka (ADR-011; lead technical)
- ☐ Citya bisa ditambah klien MCP, dan versi Vercel AI SDK yang dipakai (Pak Ridho)
- ☐ render_chart diubah agar titik grafik dihitung server dari data_ref (Pak Ridho)
- ☐ Sumber data get_dataset_rows saat ini, dan apakah barisnya dikirim ke model (Pak Ridho)
- ☐ Dataset Open Data di indeks RAG Citya: hanya metadata atau dikeluarkan (Pak Ridho)
- ☐ toolCallId disimpan bersama percakapan agar bisa dipakai sebagai request_id (Pak Ridho)
- ☐ Model untuk Kota Bogor (Qwen atau DeepSeek), tag model, dan num_ctx, untuk batas token (lead technical)
- ☐ Backend Citya dapat menjangkau port 8799 di jaringan yang sama (lead technical)
- ☐ Validasi 12 kartu metrik dengan 3–5 pertanyaan nyata Wali Kota
- ☐ Slug dan kolom nilai tiap kartu metrik, diisi saat build lalu diperiksa manusia
- ☐ Format csv untuk data_ref: perlu atau tidak
- ☐ Batas pemakaian force_refresh agar portal tidak terbebani (NFR-01)
