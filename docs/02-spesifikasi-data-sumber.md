> Ekspor otomatis dari Google Docs (versi acuan) pada 10 Nov 2026. Jangan edit di sini; edit di Google Docs lalu ekspor ulang. Diagram tidak ikut terekspor.

# Dokumen 2 — Spesifikasi Data Sumber MCP Kota Bogor
MCP Kota Bogor membaca 757 dataset dari API publik opendata.kotabogor.go.id lewat empat endpoint GET. Datanya disimpan sebagai katalog dengan metadata yang dinormalisasi, kamus kolom yang divalidasi, dan penanda kesegaran dari waktu approve terakhir. Dokumen ini menetapkan aturan pengambilan dan pengolahan data tersebut sebagai dasar Dok 3 (Arsitektur) dan Dok 4 (Spesifikasi Tool).
## Info dokumen
| Hal | Isi |
|---|---|
| Versi | 0.1, draf untuk diskusi |
| Pemilik | SCCIC ITB |
| Reviewer | - |
| Dokumen acuan | Dok 1 Ruang Lingkup dan Kebutuhan v0.1; Profil API Open Data Kota Bogor v1.0; Hasil Uji API 5 November 2026 |
| Kebutuhan Dok 1 yang dipenuhi | FR-01, FR-03, FR-06, FR-07, FR-08, FR-12, FR-13, FR-14, FR-15, FR-17, NFR-01, NFR-02 |

Penanda: [Fakta] diamati langsung dari API pada 5 November 2026; [Asumsi] dipakai sampai ada konfirmasi; [Belum ditentukan] keputusan yang masih terbuka. Aturan bernomor (AK, KS, KK, NB, CP, QC) dirujuk oleh Dok 3 dan Dok 4.
## 1. Sumber data dan aturan akses
Satu-satunya sumber versi ini adalah API publik portal, tanpa autentikasi, di platform Open Data Jabar; MCP hanya memakai empat endpoint GET. [Fakta]
Base URL: https://opendata.kotabogor.go.id/api
| Fungsi | Endpoint | Parameter yang dipakai | Catatan |
|---|---|---|---|
| Katalog | GET /datasets | page, per_page, search, where | 757 dataset dalam 8 halaman × 100. Setiap item sudah memuat deskripsi, metadata, dan riwayat approve, jadi sinkron katalog tidak perlu memanggil detail |
| Detail | GET /datasets/{slug} | - | Menyegarkan satu dataset. Slug salah menghasilkan 404 |
| Isi data | GET /bigdata/{schema}/{table} | page, per_page, where, where_or, sort, count | schema dan table diambil dari record dataset. Respons memuat data, nama kolom, nilai unik per kolom, dan jumlah baris |
| Riwayat | GET /history_datasets | where={"dataset_id":…} | Opsional; isinya sama dengan history_draft di katalog |


| ID | Aturan akses |
|---|---|
| AK-01 | Hanya metode GET ke empat endpoint di atas |
| AK-02 | Endpoint penghitung kunjungan GET /datasets/opendata/{slug}/view tidak pernah dipanggil, agar statistik portal tidak tercemar. GET detail biasa terbukti tidak menambah views [Fakta] |
| AK-03 | Jeda minimal 0,2 detik antarpermintaan; backoff eksponensial pada 429 dan 5xx; maksimal 5 percobaan; menghormati Retry-After |
| AK-04 | Paginasi memakai page dan per_page; limit, skip, dan q diabaikan server [Fakta] |
| AK-05 | User-Agent menyebut nama aplikasi dan kontak tim, tanpa data pribadi |
| AK-06 | Tanpa uji beban; sinkron katalog penuh paling sering sekali per 24 jam |

## 2. Model data katalog
Setiap dataset disimpan sebagai satu record katalog dengan 21 kelompok field; field portal yang tidak tercantum di tabel ini tidak disimpan.
| Field MCP | Sumber di portal | Aturan |
|---|---|---|
| dataset_id | id | Kunci internal untuk riwayat |
| slug | slug | Kunci utama yang dipakai tool |
| title | name | Apa adanya |
| description | deskripsi | HTML diubah ke teks polos; bagian "Penjelasan mengenai variabel" dipisah ke kamus kolom (FR-15) |
| opd | organisasi_id, organisasi_name, organisasi_slug | Produsen data |
| topik | topik_id, topik_name, topik_slug | Dipakai sebagai filter grup |
| kategori | kategori | agregat, indikator, atau master |
| klasifikasi | klasifikasi | Saat ini semua public [Fakta] |
| status_keresmian | validate + klasifikasi | Resmi jika validate = approve dan klasifikasi = public; selain itu tidak dilayani untuk jawaban (Dok 1) |
| priority_level | priority_level | Dinormalisasi: non_priority menjadi non-priority |
| data_quality | data_score_status | sangat_baik, baik, atau null |
| unit | metadata "Satuan Dataset" | Field satuan level dataset kosong pada 751 dataset, jadi metadata yang dipakai |
| frequency | metadata "Frekuensi Dataset" | Teks bebas, misalnya "Tahunan" atau "1 tahun sekali" |
| period_start, period_end | metadata "Dimensi Dataset Awal" dan "Akhir" | Tahun; period_end menjadi periode data di provenance |
| region_code | kode_wilayah | Format Kemendagri, misalnya 32.71 |
| source | schema, table | Untuk memanggil /bigdata; tidak ditampilkan ke LLM |
| source_url | dibentuk | https://opendata.kotabogor.go.id/dataset/{slug} |
| data_updated_at, freshness_source | history_draft, mdate | Lihat bagian 3 |
| change_notes | history_draft[].notes | Catatan terakhir yang tidak kosong, misalnya "penambahan data tahun 2025" |
| columns | deskripsi + nama kolom dari /bigdata | Lihat bagian 4 |
| fetched_at | dibentuk | Waktu MCP mengambil data |

Tidak disimpan: username, user_name, cuid, history_draft[].username, kontak OPD (telepon, alamat, email), hitungan view, download, dan share, serta path gambar. Alasannya privasi petugas (Dok 1) dan tidak diperlukan untuk jawaban.
## 3. Penanda kesegaran
Penanda kesegaran utama adalah waktu approve terakhir: pada 754 dari 757 dataset waktunya sama dengan mdate, dan mdate terbukti tidak berubah saat views bertambah. [Fakta]
| ID | Aturan |
|---|---|
| KS-01 | data_updated_at = cdate entri history_draft bertipe approve yang terbaru; freshness_source = "approve" |
| KS-02 | Jika tidak ada entri approve (1 dataset saat ini), data_updated_at = mdate; freshness_source = "mdate" |
| KS-03 | Jika mdate lebih baru dari approve terakhir lebih dari 1 hari (2 dataset saat ini), tetap pakai approve dan isi metadata_changed_after_approve = true |
| KS-04 | Periode data (period_end) dilaporkan terpisah dari data_updated_at. Contoh: diperbarui Agustus 2026, periode data hanya sampai 2025 |
| KS-05 | Cache katalog dan isi data kedaluwarsa setelah 24 jam (NFR-02); respons dari cache kedaluwarsa diberi status stale |

## 4. Kamus kolom
Kamus kolom diurai otomatis dari deskripsi, divalidasi terhadap nama kolom asli, dan dikurasi manual hanya untuk dataset yang gagal validasi atau berprioritas.
Seluruh 757 deskripsi memuat daftar variabel dengan template - nama_kolom: menyatakan <arti> dengan tipe data <tipe>. Sebanyak 5.755 dari 5.854 baris (98,3%) cocok dengan template, dan 699 dari 757 dataset (92,3%) terurai penuh. [Fakta] Namun pada sampel 15 dataset, 5 memiliki kolom yang tidak terdokumentasi: nama kolom berbeda, salah ketik, atau kolom nilai utama tidak dijelaskan. [Fakta]
| ID | Aturan |
|---|---|
| KK-01 | Urai baris variabel menjadi nama kolom, arti, dan tipe data. Baris tanpa "dengan tipe data" tetap diambil artinya dengan tipe = null |
| KK-02 | Normalisasi tipe: numerik → number; teks dan text → string; kategorik → category; spasi di ujung dibuang |
| KK-03 | Validasi terhadap nama kolom dari /bigdata. Status per kolom: documented (ada di keduanya), undocumented (ada di data, tidak di deskripsi), orphan (ada di deskripsi, tidak di data) |
| KK-04 | Peran kolom: dimension (kode_*, nama_*, tahun, bulan, satuan, kecamatan, kelurahan, dan kolom string) atau measure (kolom number selain kode dan tahun). Kolom id diabaikan |
| KK-05 | Jika ada kolom measure berstatus undocumented, get_dataset_info memberi peringatan dan dataset tidak dipakai untuk visual tanpa konfirmasi |
| KK-06 | Satuan kolom measure memakai unit dataset; nilai kolom satuan di data menjadi pembanding |
| KK-07 | Kurasi manual (berkas override per slug) untuk dataset prioritas dan dataset yang gagal KK-03. Daftar dataset prioritas [Belum ditentukan] |
| KK-08 | Validasi KK-03 dijalankan saat dataset pertama kali diminta, lalu disimpan di cache, agar sinkron katalog tidak memanggil /bigdata 757 kali |

Contoh hasil untuk dataset Tingkat Kemiskinan di Kota Bogor:
| Kolom | Arti | Tipe | Peran | Status |
|---|---|---|---|---|
| bps_kode_kabupaten_kota | Kode kabupaten/kota sesuai BPS | number | dimension | documented |
| tingkat_kemiskinan | Persentase penduduk di bawah garis kemiskinan di Kota Bogor | number | measure | documented |
| satuan | Satuan ukur (Persen) | string | dimension | documented |
| tahun | Tahun produksi data | number | dimension | documented |

## 5. Normalisasi baris data
Delapan aturan menjamin data yang diserahkan MCP bertipe konsisten, apa pun bentuk mentahnya di portal.
| ID | Aturan |
|---|---|
| NB-01 | Offset waktu tidak baku +07:07 dibaca sebagai +07:00 (WIB) [Asumsi]; disimpan dalam ISO 8601 |
| NB-02 | Nilai kosong ("", "-", "None", null) menjadi null |
| NB-03 | Angka tetap bertipe angka. Kolom number yang berisi teks angka dikonversi; yang gagal menjadi null dan dicatat di log |
| NB-04 | Kolom id internal portal dibuang dari data yang diserahkan |
| NB-05 | Kode wilayah di baris (format BPS, misalnya 3271) dipertahankan apa adanya; padanan Kemendagri (32.71) ada di level dataset. Pemetaan antarkeduanya [Belum ditentukan] |
| NB-06 | Nilai metadata "None" (teks) dianggap null |
| NB-07 | Angka berformat teks Indonesia diurai sebelum NB-03: titik sebagai pemisah ribuan, koma sebagai desimal. Contoh: "9.160.885" menjadi 9160885, bukan 9,16. Kolom yang sudah bertipe angka tidak diurai ulang |
| NB-08 | Label kategori dinormalisasi untuk pengelompokan: spasi dan baris baru dirapatkan, tanda hubung (- dan –) diseragamkan. Contoh: "SIMPANG EKALOKASARI- SIMPANG BARANANGSIANG" dan "SIMPANG EKALOKASARI – SIMPANG BARANANGSIANG" menjadi satu kategori. Nilai asli tetap disimpan |

## 6. Cakupan periode
MCP menghitung periode yang tersedia tanpa menarik seluruh baris, memakai nilai unik kolom tahun dari metadata_filter di respons /bigdata (FR-17).
| ID | Aturan |
|---|---|
| CP-01 | periods_available = nilai unik kolom tahun (dan bulan bila ada) dari metadata_filter |
| CP-02 | periods_found = irisan periode yang diminta dengan periods_available; missing_periods = sisanya |
| CP-03 | Jika dataset tidak punya kolom periode, missing_periods = null dan respons menyatakan "dataset tanpa dimensi waktu" |
| CP-04 | Contoh: Tingkat Kemiskinan, diminta 2025 dan 2026 → periods_found = [2025], missing_periods = [2026] |

## 7. Pemeriksaan mutu data
MCP tidak pernah mengubah angka dari portal; MCP hanya memberi tanda mutu pada data yang janggal, karena memperbaiki data adalah kewenangan Walidata. Aturan di bawah berasal dari temuan saat memetakan golden set v0.2 pada 5 November 2026. [Fakta]
| ID | Aturan |
|---|---|
| QC-01 | suspect_zero_period: jika seluruh nilai kolom measure dalam satu periode bernilai 0, periode itu ditandai dan visual tidak boleh menggambarnya sebagai 0, melainkan sebagai celah dengan catatan. Contoh: kecepatan dan volume lalu lintas Jl. Pajajaran 2024 |
| QC-02 | suspect_scale dan suspect_outlier: nilai yang berbeda 50 kali atau lebih dari median periode lain dalam dataset yang sama ditandai. Contoh: persentase realisasi PAD 2021–2023 (0,04–0,45; tahun lain sekitar 40) dan retribusi daerah 2022 (Rp55,5 juta; tahun lain ratusan miliar). Ambang 50 kali [Asumsi] |
| QC-03 | Setiap tanda masuk ke field quality_flags di respons get_dataset_info dan get_dataset_data (kode, periode, kolom), sehingga X-Assistant dapat menyebutnya di narasi. Nilai asli tetap diserahkan apa adanya |
| QC-04 | Temuan mutu dicatat di log terpisah untuk dilaporkan ke Diskominfo atau OPD produsen. Saluran pelaporan [Belum ditentukan] |

## 8. Sinkronisasi
Beban ke portal kecil: hanya sinkron katalog yang berjalan terjadwal (8 permintaan per 24 jam); dua jalur lain dipicu tool dan hasilnya disimpan di cache.

tiga jalur pengambilan data · portal ke tool
Validasi kamus kolom berjalan saat dataset pertama kali diminta (KK-08), sehingga 757 dataset tidak perlu diperiksa sekaligus. Ketiga jalur mematuhi aturan akses AK-01 sampai AK-06.
## 9. Hal yang belum ditentukan
Lima hal masih terbuka; tidak ada yang menghalangi penyusunan Dok 3.
- ☐ Daftar dataset prioritas untuk kurasi kamus kolom (KK-07)
- ☐ Pemetaan kode wilayah BPS ke Kemendagri (NB-05)
- ☐ Makna offset +07:07; dikonfirmasi ke pengelola portal (NB-01)
- ☐ Arti catatan "Generate by AI Agent" pada 114 entri riwayat, termasuk dataset kemiskinan. Jika deskripsi dan kamus kolom disusun AI lalu di-approve petugas, validasi KK-03 menjadi wajib untuk semua dataset yang dipakai
- ☐ Saluran pelaporan temuan mutu data ke Diskominfo atau OPD produsen (QC-04)
## Referensi
- Open Data Kota Bogor
- Dokumen 1 — Ruang Lingkup dan Kebutuhan MCP Kota Bogor
- Profil API Open Data Kota Bogor v1.0 dan Hasil Uji API 5 November 2026, di Project Xassistant Technical
