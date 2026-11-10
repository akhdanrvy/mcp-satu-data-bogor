> Ekspor otomatis dari Google Docs (versi acuan) pada 10 Nov 2026. Jangan edit di sini; edit di Google Docs lalu ekspor ulang. Diagram tidak ikut terekspor.

# Dokumen 5 — Rencana Uji MCP Kota Bogor
MCP Kota Bogor dianggap selesai jika lulus lima lapis uji: unit, kontrak, smoke, integrasi, dan golden set. Dua syarat tidak boleh ditawar: tidak ada angka yang dikarang (100% kasus fail-safe dan di luar sumber lulus), dan tidak ada baris data penuh di respons yang dibaca LLM. Dokumen ini juga mengisi target persentase golden set yang masih "belum ditentukan" di Dok 1, dan menaikkan golden set dari 26 ke 41 kasus.
## Info dokumen
| Hal | Isi |
|---|---|
| Versi | 0.1, draf untuk diskusi |
| Pemilik | SCCIC ITB |
| Pembaca utama | Lead technical, Tim Chat Assistant (Pak Ridho) |
| Dokumen acuan | Dok 1 v0.2 (kebutuhan dan kriteria penerimaan), Dok 2 (aturan data), Dok 3 v0.2 (arsitektur), Dok 4 v0.2 (kontrak tool), golden set v0.2 |
| Dokumen sesudahnya | Laporan hasil uji, setelah Fase 2 |

Penanda: [Fakta] sudah dikonfirmasi atau diamati; [Asumsi] dipakai sampai ada konfirmasi; [Usulan] rancangan dokumen ini; [Belum ditentukan] keputusan yang masih terbuka.
## 1. Strategi uji
Sebagian besar kesalahan ditangkap di lapis kiri yang murah dan berjalan tanpa jaringan. Lapis kanan hanya memastikan semuanya tetap benar saat bertemu portal asli, Citya, dan model.

strategi uji · 5 lapis, Fase 1 dan Fase 2
Unit test dan uji kontrak ditulis bersama kode di Fase 1 dan tidak menyentuh server pemerintah. Smoke test adalah satu-satunya uji ke portal asli. Uji integrasi dan golden set menunggu Citya bisa memanggil MCP (Fase 2); golden set menjadi gerbang terakhir sebelum rilis.
## 2. Lingkungan dan data uji
Uji dijalankan di tiga tempat, karena API portal hanya bisa diakses dari laptop dan Citya hanya ada di server tim Chat Assistant.
| Lingkungan | Akses portal | Dipakai untuk | Status |
|---|---|---|---|
| Sandbox pengembangan (Claude) | Tidak ada | Unit test dan uji kontrak dengan fixture rekaman | [Fakta] |
| Laptop Akhdan | Ada, lewat internet | Smoke test, perekaman fixture, MCP Inspector | [Fakta] |
| Staging Citya + MCP satu jaringan | Ada | Uji integrasi dan golden set ujung ke ujung | [Belum ditentukan] |

Data uji
- Fixture rekaman: respons asli /api/datasets (halaman 1 dan halaman terakhir), /api/datasets/{slug}, dan /api/bigdata untuk 12 kartu metrik, ditambah dataset bermasalah (lalu lintas Pajajaran, % realisasi PAD, retribusi 2022). Disimpan di tests/fixtures/ dan direkam ulang bila struktur API berubah (R-06).
- Fixture buatan: portal mati, 429, 404, offset +07:07, angka teks "9.160.885", nilai kosong. Dibuat tangan karena sulit dipicu dari portal asli.
- Snapshot nilai golden set: diambil dari portal saat golden set disusun, beserta data_updated_at. Bila penanda kesegaran berubah, snapshot diperbarui, bukan tesnya dilonggarkan.
- Akses sopan saat uji (NFR-01): smoke test maksimal 60 permintaan per jalan dengan jeda 0,3 detik; tidak ada uji beban; tidak dijalankan terjadwal lebih sering dari sekali sehari. [Usulan]
## 3. Kasus uji per lapis
Setiap kebutuhan wajib di Dok 1, aturan di Dok 2, dan kode galat di Dok 4 punya minimal satu kasus uji. Kolom "Acuan" adalah jejak keterlacakannya.
Unit test (UT) — sandbox, tanpa jaringan
| ID | Yang diuji | Acuan | Lulus jika |
|---|---|---|---|
| UT-01 | Waktu dengan offset +07:07 dinormalkan ke +07:00 | FR-08 | Semua waktu keluaran ISO 8601 baku |
| UT-02 | Angka teks "9.160.885" dan "1.234,5" diurai | NB-07 | Menjadi 9160885 dan 1234.5 bertipe angka |
| UT-03 | Label segmen beda spasi dan tanda hubung diseragamkan | NB-08 | Satu kelompok per segmen; nilai asli tetap tersimpan |
| UT-04 | Penanda kesegaran dari approve terakhir, cadangan mdate | KS-01 s.d. KS-05 | data_updated_at sesuai aturan pada 4 skenario riwayat |
| UT-05 | Status keresmian | FR-07 | resmi hanya bila validate=approve dan klasifikasi=public |
| UT-06 | Kamus kolom dari template deskripsi, divalidasi ke kolom asli | KK-01 s.d. KK-08 | Kolom tanpa arti diberi meaning: null, bukan tebakan |
| UT-07 | periods_found dan missing_periods | FR-17, CP | Diminta 2025 dan 2026, tersedia 2025 → missing_periods: [2026] |
| UT-08 | Periode bernilai nol semua (Pajajaran 2024) | QC-01 | Ditandai suspect_zero_period; nilai asli tidak diubah |
| UT-09 | Skala dan pencilan (% realisasi PAD, retribusi 2022) | QC-02 | Ditandai suspect_scale atau suspect_outlier |
| UT-10 | Kueri dengan tanda baca dan kata pendek | FR-09 | Tidak ada galat FTS5 |
| UT-11 | Tanda tangan data_ref: sah, diubah, kedaluwarsa | ADR-008 | 200, 403, dan 410 sesuai Dok 4 bagian 6 |
| UT-12 | Field akun petugas | Dok 1 privasi | username dan history_draft[].username tidak ada di katalog maupun respons |
| UT-13 | Respons melebihi batas token | FR-15, FR-16 | Dipangkas dan truncated: true beserta alasannya |
| UT-14 | Portal mati, dengan dan tanpa cache | FR-10, FR-11 | SOURCE_UNAVAILABLE tanpa angka, atau STALE_DATA berlabel |

Uji kontrak (KT) — sandbox, tanpa jaringan
| ID | Yang diuji | Acuan | Lulus jika |
|---|---|---|---|
| KT-01 | Skema parameter ketiga tool | Dok 4 bagian 3–5 | JSON Schema sama dengan snapshot yang disetujui |
| KT-02 | Amplop respons di semua jalur, termasuk galat | Dok 4 bagian 2 | Semua field wajib ada dan bertipe benar |
| KT-03 | Tidak ada nilai di respons get_dataset_data | FR-14, ADR-005 | Tidak satu pun nilai kolom measure muncul di respons; pratinjau hanya kolom dimensi |
| KT-04 | Sembilan kode galat | Dok 4 bagian 7 | Setiap kode bisa dipicu dan retryable sesuai tabel |
| KT-05 | Awalan nama tool | Dok 4 bagian 1 | Nama mengikuti MCP_TOOL_PREFIX |
| KT-06 | Isi berkas data_ref | Dok 4 bagian 6 | rows cocok row_count; kolom dan provenance sama dengan respons tool |

Smoke test (ST) — laptop, API portal asli
| ID | Yang diuji | Acuan | Lulus jika |
|---|---|---|---|
| ST-01 | Sinkron katalog penuh | FR-01 | Jumlah dataset di katalog = pagination.total_data portal |
| ST-02 | Tiga tool untuk 12 kartu metrik | FR-02 s.d. FR-04 | Semua kartu mengembalikan status ok atau partial yang beralasan |
| ST-03 | Unduh data_ref lewat HTTP | ADR-005 | Berkas terunduh sebelum kedaluwarsa dan cocok dengan KT-06 |
| ST-04 | Akses sopan | NFR-01 | Nol respons 429; jeda antarpermintaan ≥ 0,2 detik |
| ST-05 | Kinerja | NFR-03 | Pencarian < 0,5 detik; data dari cache < 1 detik (p95) |

Uji integrasi (IT) — staging Citya
| ID | Yang diuji | Acuan | Lulus jika |
|---|---|---|---|
| IT-01 | Citya memanggil tool MCP lewat streamable-http | A-04 | Ketiga tool terdaftar dan bisa dipanggil model |
| IT-02 | Backend mengambil data_ref | ADR-005, ADR-008 | Berhasil dalam masa berlaku pada 20 panggilan berturut-turut |
| IT-03 | render_chart menghitung titik dari data_ref | Dok 4 bagian 1, ADR-011 | Setiap titik sama dengan nilai di sumber; periode kosong tampil sebagai celah |
| IT-04 | request_id = toolCallId | Dok 3 bagian 6 | Satu panggilan bisa dilacak di log MCP dan audit Citya |
| IT-05 | Pembagian kerja RAG dan MCP | ADR-011 | Pertanyaan teks memanggil RAG; pertanyaan angka memanggil MCP |

## 4. Golden set v0.3
Golden set naik dari 26 ke 41 kasus. Dua tipe baru menguji pembagian kerja RAG dan MCP (ADR-011): kasus campuran dan kasus jebakan. [Usulan]
| Tipe | v0.2 | Tambahan | v0.3 | Yang diuji |
|---|---|---|---|---|
| normal | 8 | +4 | 12 | Satu kasus per kartu metrik; dataset, kolom, dan periode benar |
| fail_safe | 8 | +2 | 10 | Galat, data kosong, periode tidak tersedia; tanpa angka karangan |
| out_of_source | 7 | 0 | 7 | Pertanyaan operasional; jawaban not_in_source |
| data_quality | 3 | +1 | 4 | Data bertanda QC disebut catatannya, tidak digambar sebagai 0 |
| campuran (baru) | 0 | +5 | 5 | Teks dari RAG dan angka dari MCP dalam satu jawaban |
| jebakan (baru) | 0 | +3 | 3 | Angka yang juga muncul di dokumen RAG harus diambil dari MCP |
| Jumlah | 26 | +15 | 41 |  |

Contoh kasus baru
| ID | Pertanyaan | Sumber yang benar | Jawaban yang benar |
|---|---|---|---|
| C-01 | Apakah realisasi PAD 2024 sudah sesuai target di RPJMD? | RAG (target) + MCP (KM-01) | Target dikutip dari dokumen; realisasi dari MCP; selisih dihitung kode |
| C-02 | Bagaimana angka kemiskinan dibanding target pengentasan kemiskinan Kota Bogor? | RAG + MCP (KM-06) | Hanya tahun yang tersedia; sebut definisi indikator |
| C-03 | Apa program pariwisata tahun ini dan berapa kunjungan wisatawan 2024? | RAG + MCP (KM-08) | Program dari dokumen; kunjungan dari MCP |
| C-04 | Apa dasar aturan retribusi daerah dan bagaimana realisasinya? | RAG + MCP (KM-03) | Realisasi 2022 disebut bertanda mutu QC-02 |
| C-05 | Apa kata akun Pemkot soal Jl. Pajajaran, dan bagaimana volume lalu lintasnya? | RAG (media sosial) + MCP (KM-11) | 2024 tidak digambar sebagai 0 |
| J-01 | Berapa PAD Kota Bogor 2023? (angka juga tertulis di dokumen LKPJ yang ter-index) | MCP (KM-01) | Angka dari MCP beserta sumber portal, bukan kutipan dokumen |
| J-02 | Berapa jumlah kunjungan wisatawan? (tanpa tahun) | MCP (KM-08) | Tahun data terakhir disebut eksplisit |
| J-03 | Berapa volume lalu lintas Pajajaran hari ini? | MCP, lalu tolak | Data hanya tahunan; sebut tahun terakhir, tanpa angka "hari ini" |

Setiap kasus disimpan di golden_set_v0.3.jsonl dengan field: id, tipe, pertanyaan, sumber_benar (rag | mcp | keduanya | tidak_ada), kartu_metrik, periode, nilai_acuan (snapshot dari portal, bukan dikarang), perilaku_benar, dan visual_diharapkan. Kasus campuran membutuhkan dokumen RPJMD dan LKPJ di indeks RAG Citya. [Belum ditentukan]
Lima kasus normal sebaiknya diganti dengan pertanyaan nyata Wali Kota begitu tersedia, agar golden set mencerminkan kebutuhan sungguhan.
## 5. Kriteria lulus
Kriteria Fase 1 menilai MCP sendiri, tanpa model; Fase 2 menilai Citya dan MCP bersama model. Tiga baris bertanda nol toleransi memblokir rilis bila gagal sekali pun. [Usulan]
| Ukuran | Target Fase 1 (MCP sendiri) | Target Fase 2 (Citya + MCP + model) |
|---|---|---|
| Unit test dan uji kontrak lulus | 100% | 100% |
| Cakupan kode di services/ dan tools/ | ≥ 80% | ≥ 80% |
| Smoke test 12 kartu metrik | 100% | 100% |
| Tidak ada nilai di respons yang dibaca LLM (KT-03) | 100%, nol toleransi | 100%, nol toleransi |
| Kasus fail_safe dan out_of_source tanpa angka karangan | Tidak diukur | 100%, nol toleransi |
| Angka di jawaban sama dengan sumber (check_narrative) | Tidak diukur | 100%, nol toleransi |
| Pemilihan dataset benar pada kasus normal | 100% lewat kartu metrik | ≥ 90% oleh model |
| Pemilihan sumber benar pada kasus campuran dan jebakan | Tidak diukur | ≥ 90% |
| Kasus data_quality menyebut catatan mutu | 100% quality_flags terisi | ≥ 90% disebut di narasi |
| Kinerja (NFR-03, p95) | Pencarian < 0,5 detik; cache < 1 detik | Sama, diukur dari Citya |
| Respons 429 dari portal saat uji | 0 | 0 |

Target 90% untuk pilihan model dipilih karena model bisa salah memilih, tetapi kesalahan itu tetap aman: dataset yang salah tetap membawa sumber dan judul yang bisa dikenali pengguna. Kesalahan yang tidak aman, yaitu angka karangan atau baris bocor ke model, diberi nol toleransi.
Kriteria penerimaan MVP di Dok 1 yang masih "target persentase belum ditentukan" diisi dengan baris "Pemilihan dataset benar pada kasus normal" di atas.
## 6. Peran, jadwal, dan pelaporan cacat
| Lapis | Ditulis oleh | Dijalankan oleh | Kapan |
|---|---|---|---|
| Unit test dan uji kontrak | SCCIC ITB (bersama Claude) | Otomatis (pytest) | Setiap perubahan kode, Fase 1 |
| Smoke test | SCCIC ITB | Akhdan, di laptop | Setiap rilis MCP dan setelah rekam ulang fixture |
| Uji integrasi | SCCIC ITB dan Tim Chat Assistant | Akhdan dan Pak Ridho, di staging | Fase 2, setelah klien MCP terpasang di Citya |
| Golden set | SCCIC ITB | Penguji otomatis di staging | Fase 2; diulang setiap model, prompt, atau deskripsi tool berubah |

Pelaporan cacat [Usulan]
| Tingkat | Contoh | Akibat |
|---|---|---|
| S1 Kritis | Angka karangan, baris penuh masuk respons LLM, data_ref bisa dibuka tanpa tanda tangan sah | Rilis diblokir sampai diperbaiki |
| S2 Mayor | Kode galat salah, periode hilang tidak ditandai, tanda mutu tidak muncul | Diperbaiki sebelum Fase 2 selesai |
| S3 Minor | Teks deskripsi kurang jelas, kinerja sedikit di atas target | Dicatat dan dijadwalkan |

Cacat dicatat sebagai issue di repo MCP dengan label tingkat, ID kasus uji, dan request_id bila ada. Hasil akhir dirangkum di Laporan Hasil Uji setelah Fase 2.
Hal yang belum ditentukan
- ☐ Lokasi repo MCP Kota Bogor (folder laptop atau repo GitHub baru)
- ☐ Staging Citya yang satu jaringan dengan MCP (lead technical, Pak Ridho)
- ☐ Dokumen RPJMD dan LKPJ di indeks RAG Citya untuk kasus campuran (Pak Ridho)
- ☐ 3–5 pertanyaan nyata Wali Kota untuk mengganti sebagian kasus normal
- ☐ Persetujuan target 90% dan 80% cakupan kode (lead technical)
- ☐ Model yang dipakai saat golden set Fase 2 (sama dengan model produksi Bogor)
