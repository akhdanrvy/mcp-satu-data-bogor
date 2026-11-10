# Laporan Fixture Asli (2026-11-10)

Dibuat otomatis oleh `scripts/record_fixtures.py`. Menjawab TODO Langkah 3 di `docs/RENCANA_FASE1.md`. Laporan ini tidak memuat nilai data, email, atau telepon.

## Ringkasan

- Folder: `tests/fixtures/recorded/2026-11-10`
- Permintaan terkirim: 33 dari anggaran 60
- Berkas respons: 33
- Berhenti lebih awal: tidak
- Insiden 5xx: tidak ada
- Sanitasi: 0 kunci akun dibuang (cuid, muid, user_name, username); 801 nilai kontak diredaksi (Kontak Produsen, alamat, email, notelp, organisasi_alamat, organisasi_email, organisasi_notelp)

## KB-17: kolom id internal /bigdata

- Kolom yang ada di semua tabel: bps_kode_kabupaten_kota, bps_nama_kabupaten_kota, id, kode_provinsi, nama_provinsi, satuan, tahun
- Kolom berpola id: id
- Dugaan KB-17 (`{"id"}`): **terkonfirmasi ada**
- `badan_keuangan_dan_aset_daerah/persentase_sisa_lebih_perhitungan_anggaran_silpa_terhadap_angga`: bps_kode_kabupaten_kota, bps_nama_kabupaten_kota, id, kode_provinsi, nama_provinsi, persentase_silpa, satuan, tahun
- `badan_keuangan_dan_aset_daerah/realisasi_belanja_modal_daerah_di_kota_bogor_2`: bps_kode_kabupaten_kota, bps_nama_kabupaten_kota, id, kode_provinsi, nama_provinsi, realisasi_belanja, satuan, tahun
- `badan_pendapatan_daerah/persentase_realisasi_pendapatan_asli_daerah_pad_kota_bogor`: bps_kode_kabupaten_kota, bps_nama_kabupaten_kota, id, kode_provinsi, nama_provinsi, persentase_pendapatan, satuan, tahun
- `badan_pendapatan_daerah/realisasi_pendapatan_asli_daerah_pad_kota_bogor`: bps_kode_kabupaten_kota, bps_nama_kabupaten_kota, id, kode_provinsi, nama_provinsi, satuan, tahun, total_pendapatan
- `badan_pendapatan_daerah/realisasi_penerimaan_pendapatan_asli_daerah_pad_bersu`: bps_kode_kabupaten_kota, bps_nama_kabupaten_kota, id, kode_provinsi, nama_provinsi, realisasi_pad, satuan, tahun
- `dinas_kesehatan/tren_kunjungan_pasien_puskesmas_di_kota_bogor_1`: bps_kode_kabupaten_kota, bps_nama_kabupaten_kota, id, jumlah_pasien, kode_provinsi, nama_provinsi, satuan, tahun
- `dinas_pariwisata_dan_kebudayaan/perkembangan_jumlah_kunjungan_wisatawan_di_kota_bogor`: bps_kode_kabupaten_kota, bps_nama_kabupaten_kota, id, kode_provinsi, nama_provinsi, perkembangan_kunjungan, satuan, tahun
- `dinas_perhubungan/kecepatan_lalu_lintas_segmen_1_dan_2_berdasarkan_hari`: bps_kode_kabupaten_kota, bps_nama_kabupaten_kota, hari_operasional, id, kecepatan, kode_provinsi, nama_provinsi, no_segmen, satuan, segmen, tahun
- `dinas_perhubungan/volume_lalu_lintas_segmen_1_dan_2_berdasarkan_hari_op`: bps_kode_kabupaten_kota, bps_nama_kabupaten_kota, hari_operasional, id, kode_provinsi, nama_provinsi, satuan, segmen, tahun, volume
- `dinas_sosial/persentase_kepala_keluarga_miskin_di_kota_bogor`: bps_kode_kabupaten_kota, bps_nama_kabupaten_kota, id, kode_provinsi, nama_provinsi, persentase, satuan, tahun
- `dinas_sosial/tingkat_kemiskinan_di_kota_bogor_1`: bps_kode_kabupaten_kota, bps_nama_kabupaten_kota, id, kode_provinsi, nama_provinsi, satuan, tahun, tingkat_kemiskinan

## KB-20: bentuk respons

- `bigdata.metadata`: `list[str] (n=10)` | `list[str] (n=11)` | `list[str] (n=8)`
- `bigdata.metadata_filter`: `list[object{key, type, value}] (n=10)` | `list[object{key, type, value}] (n=7)` | `list[object{key, type, value}] (n=9)`
- `bigdata.pagination`: `object{has_next, has_previous, page, per_page, total_data, total_page}`
- `bigdata.top_level (status 200)`: `object{code, data, error, message, metadata, metadata_filter, pagination, source}`
- `detail.metadata`: `list[object{cdate, dataset_id, id, key, mdate, type, value}] (n=10)` | `list[object{cdate, dataset_id, id, key, mdate, type, value}] (n=11)` | `list[object{cdate, dataset_id, id, key, mdate, type, value}] (n=12)` | `list[object{cdate, dataset_id, id, key, mdate, type, value}] (n=13)` | `list[object{cdate, dataset_id, id, key, mdate, type, value}] (n=14)` | `list[object{cdate, dataset_id, id, key, mdate, type, value}] (n=9)`
- `detail.top_level (status 200)`: `object{code, data, error, message}`
- `detail.top_level (status 404)`: `object{message}`
- `katalog.pagination`: `object{has_next, has_previous, page, per_page, total_data, total_page}`
- `katalog.top_level (status 200)`: `object{code, data, error, message, pagination}`
- `referensi.top_level (status 200)`: `object{code, data, error, message, pagination}`
- 404: `/datasets/slug-sengaja-salah-untuk-uji-404 → object{message}`

## Nilai `type` di history_draft

- `preview`: 2239
- `approve`: 2112
- `verification-edit`: 2073
- `verification-new`: 1007
- `draft`: 10
- `revision-new`: 2
- `revision-edit`: 1

## Format waktu (NB-01)

- cdate: +07:07: 762
- history_draft[].cdate: +07:07: 7444
- mdate: +07:07: 762

## Angka bertipe teks (risiko KB-16)

Desimal bertitik bertipe teks (menjadi None menurut KB-16):
- tidak ditemukan

Teks angka berformat Indonesia yang berhasil diurai NB-07:
- `dinas_pariwisata_dan_kebudayaan/perkembangan_jumlah_kunjungan_wisatawan_di_kota_bogor`.`perkembangan_kunjungan`: 6 sel

## Kandidat slug kartu metrik

### KM-07 (1 kandidat, direkam otomatis)
- `persentase-kepala-keluarga-miskin-di-kota-bogor`: Persentase Kepala Keluarga Miskin di Kota Bogor (Dinas Sosial)

### KM-12 (2 kandidat, pilih manual)
- `persentase-penanganan-pengaduan-masyarakat-ke-inspektorat-daerah-kota-bogor`: Persentase Penanganan Pengaduan Masyarakat ke Inspektorat Daerah Kota Bogor (Inspektorat)
- `persentase-pengaduan-masyarakat-akibat-adanya-dugaan-pencemaran-kerusakan-lingkungan-hidup-yang-ditindak-lanjuti-di-kota-bogor`: Persentase Pengaduan Masyarakat Akibat Adanya Dugaan Pencemaran / Kerusakan Lingkungan Hidup Yang Ditindak Lanjuti di Kota Bogor (Dinas Lingkungan Hidup)

## Temuan email/telepon setelah sanitasi

Dikelompokkan per berkas dan pola path (indeks list diringkas menjadi `[]`).

- tidak ada
