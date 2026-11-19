# Integrasi MCP Kota Bogor dengan Citya (untuk tim Chat Assistant)

Dokumen ini untuk tim backend X-Assistant Citya AI. Isinya: cara menyambung klien MCP, bentuk respons, cara mengambil data lewat `data_ref`, dan aturan memakai data untuk jawaban dan grafik. Kontrak resminya adalah **Dok 4** (`docs/04-spesifikasi-tool.md`, `contract_version` 0.1); dokumen ini ringkasan praktisnya, ditambah keputusan build yang belum masuk Dok 4 (lihat `docs/STATUS_FASE1.md`).

## 1. Gambaran alur

```
Pengguna ──► Model (Citya) ──► tool MCP satudata_* ──► amplop JSON TANPA nilai data
                                      │
                                      └─ get_dataset_data → data_ref.url (bertanda tangan, 15 menit)
                                                             │
Backend Citya ◄── GET data_ref.url (file server :8799) ◄────┘
     │  baris data lengkap + kolom + provenance + quality_flags
     └─► hitung angka / titik grafik di backend ──► render_chart / narasi
```

**Penting (ADR-011):** respons tool sengaja **tidak pernah** memuat nilai kolom ukuran (measure). Ini berlaku di respons tool mana pun, termasuk `preview`, yang hanya berisi kolom dimensi. Model tidak boleh, dan tidak bisa, menyebut angka dari respons tool. Angka harus berasal dari berkas `data_ref` yang diambil dan dihitung backend. **Tanpa perubahan backend ini, Citya yang memakai MCP tidak dapat menyebut angka sama sekali.**

## 2. Menyambung klien MCP

| Hal | Nilai |
|---|---|
| Transport | **streamable-http**, endpoint `http://<host-mcp>:8800/mcp`, stateless, respons JSON (tanpa sesi SSE) |
| Autentikasi | `Authorization: Bearer <token>` bila `MCP_HTTP_AUTH_TOKEN` diisi (disarankan di produksi). Salah atau kosong → 401. |
| Header `Host` | Harus ada di daftar izin: `127.0.0.1:8800`, `localhost:8800`, ditambah `MCP_HTTP_ALLOWED_HOSTS` (mis. `10.x.x.x:8800` atau `mcp-bogor.internal:8800`). Host lain ditolak **421** (proteksi DNS rebinding). |
| Header `Origin` | Panggilan server-ke-server biasanya tanpa Origin, jadi aman. Permintaan ber-Origin ditolak 403 kecuali ada di `MCP_HTTP_ALLOWED_ORIGINS`. Tidak ada CORS, karena MCP tidak untuk dipanggil dari browser. |
| Nama tool | Berawalan `MCP_TOOL_PREFIX` (bawaan `satudata_`): `satudata_search_datasets`, `satudata_get_dataset_info`, `satudata_get_dataset_data` |
| Kesehatan | `GET http://<host-mcp>:8800/healthz`, tanpa token: 200 siap, 503 katalog belum siap |

Bila MCP dan Citya ada di mesin berbeda, sisi MCP perlu mengatur:
- `MCP_HTTP_HOST` dan `MCP_FILESERVER_HOST`: alamat antarmuka yang bisa dijangkau backend Citya (bawaannya 127.0.0.1, hanya lokal);
- `MCP_HTTP_ALLOWED_HOSTS`: host:port yang dipakai Citya;
- **`MCP_FILESERVER_PUBLIC_URL`** (lihat bagian 4).

### Contoh klien dengan Vercel AI SDK

> **Pseudo-kode, cocokkan dengan versi AI SDK Citya.** Rujukan: <https://ai-sdk.dev/docs/ai-sdk-core/mcp-tools>. Bentuk di bawah adalah bentuk AI SDK saat ini (`createMCPClient` dari paket `@ai-sdk/mcp`).

```ts
// PSEUDO-KODE — cocokkan dengan versi AI SDK Citya
import { createMCPClient } from "@ai-sdk/mcp";
import { streamText } from "ai";

const mcp = await createMCPClient({
  transport: {
    type: "http",
    url: process.env.MCP_BOGOR_URL!, // mis. http://<host-mcp>:8800/mcp
    headers: { Authorization: `Bearer ${process.env.MCP_BOGOR_TOKEN}` },
    redirect: "error", // tolak redirect; server MCP Kota Bogor tidak pernah me-redirect
  },
});

const tools = await mcp.tools(); // satudata_search_datasets, satudata_get_dataset_info, satudata_get_dataset_data

// Bungkus tool data: model hanya melihat amplop; backend mengambil berkas data_ref.
const dataTool = tools.satudata_get_dataset_data;
tools.satudata_get_dataset_data = {
  ...dataTool,
  execute: async (args, options) => {
    // Tanpa `schemas`, hasilnya CallToolResult mentah: { content, structuredContent?, isError? }
    const out = await dataTool.execute(args, options);
    const envelope = out.structuredContent ?? JSON.parse(out.content[0].text);
    if (envelope.status !== "error" && envelope.result?.data_ref) {
      const file = await fetchDataRef(envelope.result.data_ref.url); // bagian 4
      chartStore.put(envelope.request_id ?? crypto.randomUUID(), { envelope, file });
    }
    return out; // JANGAN menyisipkan baris data ke hasil yang dikirim ke model
  },
};

const result = streamText({
  model,
  tools,
  messages,
  onEnd: async () => {
    await mcp.close(); // atau biarkan terbuka untuk aplikasi berumur panjang, tutup saat berhenti
  },
});
```

AI SDK versi lama memakai `experimental_createMCPClient` dari paket `ai`, dengan transport `new StreamableHTTPClientTransport(new URL(url), { requestInit: { headers } })` dari `@modelcontextprotocol/sdk/client/streamableHttp.js`. Pola pembungkusan tool-nya sama.

Bila Citya memakai opsi `schemas` di `mcp.tools()` dengan `outputSchema`, klien memvalidasi `structuredContent` dan mengembalikan objek amplop langsung, bukan `CallToolResult`. Skema amplop resmi bisa diambil dari `list_tools` (bila `MCP_EXPOSE_OUTPUT_SCHEMA=true`) atau dari snapshot `tests/contract/snapshots/tool_schemas.json`.

Catatan:
- Parameter `request_id` ada di ketiga tool ("diisi oleh sistem pemanggil"). Backend boleh mengisinya, mis. dengan ID percakapan + giliran, agar log MCP (`tool_call.request_id`) bisa dicocokkan dengan log Citya. Model sebaiknya tidak mengisinya.
- Amplop dikirim sebagai `structuredContent` dan juga sebagai teks JSON di `content[0]`. `isError` MCP tetap `false` untuk galat kontrak, karena galatnya ada di amplop.

## 3. Kontrak amplop

Setiap tool mengembalikan amplop yang sama:

| Field | Isi |
|---|---|
| `contract_version` | `"0.1"` |
| `status` | `ok`, `partial` (hasil ada tetapi ada catatan: periode hilang, dipangkas, atau data dari cache usang), `error` |
| `error` | `null` atau `{code, message, retryable}`; `message` berupa templat tetap tanpa angka data |
| `request_id` | Dipantulkan dari parameter |
| `provenance` | `source_url`, `opd`, `data_updated_at`, `freshness_source`, `retrieved_at`, `period_start`/`period_end`, `served_from` (`live`/`cache`/`stale_cache`) |
| `officialness` | `{status, basis}`; `status` = `resmi` hanya bila dataset disetujui (approve) dan publik, selain itu `draf` |
| `quality_flags` | `[{code, rule, column, period, detail}]`; lihat bagian 5 |
| `truncated`, `truncated_reason` | Respons dipangkas agar muat anggaran token |
| `notices` | Catatan non-galat bertemplat tetap untuk model |
| `result` | Isi per tool; **selalu `null` bila `status=error`** |

### 10 kode galat

| Kode | Tool | Retry? | Tindakan yang disarankan untuk Citya |
|---|---|---|---|
| `INVALID_PARAM` | semua | tidak | Model memperbaiki parameter (pesan menyebut parameter dan batasnya) |
| `DATASET_NOT_FOUND` | info, data | tidak | Slug tidak ada di katalog; cari ulang dengan `search_datasets` |
| `NO_MATCH` | search | tidak | Data tidak ada di portal. Katakan tidak tersedia; **jangan** mengarang atau mengambil dari sumber lain |
| `PERIOD_NOT_AVAILABLE` | data | tidak | Tahun diminta belum ada; pesan menyebut tahun terakhir yang tersedia. Tawarkan tahun itu dan sebut tahunnya |
| `LEVEL_NOT_AVAILABLE` | data | tidak | Dataset hanya tingkat kota; tidak bisa dipecah per kecamatan/kelurahan. Tawarkan angka tingkat kota (KK-A1) |
| `SOURCE_UNAVAILABLE` | info, data | ya | Portal tidak terjangkau dan tidak ada cache. Coba lagi nanti; sampaikan apa adanya |
| `RATE_LIMITED` | info, data | ya | Portal membatasi laju. Tunggu; jangan mengulang beruntun |
| `STALE_DATA` | info, data | — | Bukan galat penuh: `status=partial`, data dari cache usang (`served_from=stale_cache`). Sebut bahwa data "belum diperbarui" dan tanggal `data_updated_at` |
| `RESPONSE_TOO_LARGE` | semua | tidak | Persempit `periods`/`filters`, atau kurangi `page_size` |
| `INTERNAL` | semua | ya | Coba sekali lagi; bila berulang, laporkan dengan `request_id` |

### 8 kode notice

| Kode | Arti dan tindakan |
|---|---|
| `DICTIONARY_NOT_VALIDATED` | Arti kolom nilai (`column`) belum terverifikasi. **Jangan buat grafik tanpa konfirmasi pengguna**, dan jangan menebak artinya |
| `NO_MEASURE_COLUMN` | Tidak ada kolom nilai yang dikenali; jangan buat grafik tanpa konfirmasi |
| `UNIT_CONFLICT` | Kolom satuan berisi lebih dari satu nilai; satuan kolom tidak ditetapkan. Jangan menjumlah lintas satuan |
| `DIFFERENT_DEFINITION` | Dua dataset di `datasets` mirip namanya tetapi definisinya berbeda (R-10, mis. tingkat kemiskinan vs persentase KK miskin). **Jangan menyambung tren atau membandingkan keduanya seolah satu seri** |
| `NO_TIME_DIMENSION` | Dataset tanpa kolom tahun; `periods` diabaikan |
| `EMPTY_RESULT` | Filter sah tetapi tidak ada baris yang cocok |
| `REFRESH_THROTTLED` | `force_refresh` diabaikan karena data baru diambil; cache segar dipakai |
| `CATALOG_STALE` | Katalog lokal lama tidak disinkron; dataset terbaru mungkin belum tercantum |

### `MCP_EXPOSE_OUTPUT_SCHEMA`

Bawaannya `true`: `list_tools` mengiklankan `outputSchema` (JSON Schema amplop per tool), dan SDK memvalidasi `structuredContent` terhadapnya. Bila klien Citya meneruskan `outputSchema` ke konteks model, skema itu ikut terkirim **setiap giliran** dan memboroskan token. Dalam kasus itu MCP cukup diset `MCP_EXPOSE_OUTPUT_SCHEMA=false`; respons tetap sama. Lihat pertanyaan terbuka no. 2.

## 4. Mengambil `data_ref` (backend)

`get_dataset_data` mengembalikan:
```json
"data_ref": {
  "url": "http://<MCP_FILESERVER_PUBLIC_URL>/d/<cache_id>?exp=<unix>&sig=<hmac>",
  "expires_at": "2026-11-17T14:31:00+07:00",
  "row_count": 6,
  "format": "json"
}
```

- **`MCP_FILESERVER_PUBLIC_URL` harus berisi alamat yang bisa dijangkau backend Citya.** Bawaannya `http://localhost:8799` hanya benar bila Citya berjalan di mesin yang sama. Bila beda mesin, isi dengan alamat jaringan internal, mis. `http://10.x.x.x:8799` atau `http://mcp-bogor.internal:8799`, dan bind file server ke antarmuka itu (`MCP_FILESERVER_HOST`).
- **Port 8799 tidak perlu dan tidak boleh dibuka ke publik.** Port ini cukup bisa dijangkau dari mesin backend Citya; batasi dengan firewall. Browser pengguna tidak pernah mengambil data_ref langsung, jadi tidak ada CORS.
- Ambil dengan **GET URL apa adanya**, tanpa header tambahan. Backend tidak perlu dan tidak bisa memverifikasi `sig` sendiri: `sig` = HMAC-SHA256 atas `"{cache_id}.{exp}"` dengan secret yang hanya diketahui MCP, dan file server yang memeriksanya. Jangan mengubah, menyusun ulang, atau menyimpan URL untuk dipakai nanti.
- **TTL 15 menit** (`MCP_DATAREF_TTL_SECONDS=900`). Ambil segera setelah tool selesai.

| Status | Arti | Tindakan backend |
|---|---|---|
| 200 | Berkas JSON (`Cache-Control: no-store`) | Pakai |
| 403 | Tanda tangan salah (URL diubah, atau secret dirotasi tanpa masa transisi) | Panggil `get_dataset_data` lagi; jangan mengulang URL yang sama |
| 410 | Kedaluwarsa | Panggil `get_dataset_data` lagi. Biasanya dilayani dari cache, tanpa permintaan ke portal |
| 404 | Berkas cache sudah dihapus (GC atau pembersihan) | Panggil `get_dataset_data` lagi |
| 503 | Galat disk di sisi MCP | Coba sekali lagi setelah jeda singkat, lalu laporkan |

Isi berkas (Dok 4 bagian 6):

| Field | Isi |
|---|---|
| `contract_version`, `slug` | Versi kontrak dan dataset |
| `columns` | Kolom seperti di respons tool, dengan **arti lengkap** untuk semua kolom dan teks asli tanpa penyamaran angka |
| `rows` | Baris data yang sudah dinormalkan (angka teks Indonesia → angka, label dimensi diseragamkan), sesuai `periods`/`filters` |
| `provenance` | Sama dengan respons, tanpa `served_from` |
| `officialness`, `quality_flags` | Sama dengan respons |
| `periods_requested`, `periods_found`, `missing_periods`, `time_dimension`, `filters_applied`, `dictionary_validated` | Sama dengan `result` respons |
| `label_originals` | `{kolom: {label_normal: [ejaan_asli, ...]}}` bila label asli berbeda ejaan (NB-08) |

Berkas berisi data publik portal, tetapi tetap jangan disimpan permanen atau diteruskan apa adanya ke model. Hitung yang diperlukan, lalu kirim hasil hitungan ke `render_chart` atau narasi.

### Profil sandbox (Citya RAG Chat)

Di Citya RAG Chat (backend FastAPI), berkas data_ref **tidak** diambil backend. Model sendiri mengambilnya lewat tool `execute_code` (sandbox pandas per sesi, ada `httpx`) lalu menyebut angka dari stdout sandbox. Untuk uji coba ini alur "angka lewat LLM" diterima (KB-51). Respons tool tetap sama, tanpa nilai kolom measure (KT-03); yang berubah hanya deskripsi `get_dataset_data`, yang mengarahkan model ke `execute_code`.

Env MCP (di `.env` mesin MCP):

```
MCP_CLIENT_PROFILE=sandbox
MCP_TRANSPORT=streamable-http
MCP_FILESERVER_PUBLIC_URL=http://host.docker.internal:8799
```

- `MCP_FILESERVER_PUBLIC_URL` memakai `host.docker.internal` karena URL data_ref dibuka dari **container sandbox**, bukan dari mesin host. `localhost` di dalam container menunjuk ke container itu sendiri.
- Bila container tidak bisa menjangkau file server yang bind ke `127.0.0.1`, set `MCP_FILESERVER_HOST=0.0.0.0` dan batasi port 8799 dengan firewall agar hanya bisa dijangkau dari mesin itu (port ini tidak boleh terbuka ke publik).
- File server tetap dijalankan terpisah (`uv run python -m mcp_bogor.fileserver`) dan `MCP_DATAREF_SECRET` wajib terisi.
- Pada Docker Desktop (Windows), cek dari dalam container sandbox: `httpx.get("http://host.docker.internal:8799/healthz")` harus menjawab 200.

Mendaftarkan server di `/admin` Citya:

| Isian | Nilai |
|---|---|
| Nama | `satudata-bogor` |
| URL | `http://localhost:8800/mcp` |
| Auth | none |
| Status | aktifkan |
| Akses | berikan ke grup pengguna yang ikut uji |

URL `localhost:8800` hanya benar bila backend Citya berjalan langsung di mesin yang sama (bukan di container). Bila backend Citya di container, pakai `http://host.docker.internal:8800/mcp`, jalankan server dengan `MCP_HTTP_HOST=0.0.0.0`, dan tambahkan `MCP_HTTP_ALLOWED_HOSTS=host.docker.internal:8800`; tanpa itu proteksi DNS rebinding menolak permintaan (KB-47). Auth `none` hanya untuk uji lokal; bila server bisa dijangkau dari mesin lain, isi `MCP_HTTP_AUTH_TOKEN`.

Di profil ini aturan 1 bagian 5 berlaku dengan penyesuaian: angka hanya dari hasil `execute_code` atas berkas data_ref, disebut apa adanya dengan satuan dari `columns[].unit`. Aturan 2 s.d. 8 tetap berlaku.

## 5. Aturan memakai data untuk jawaban dan grafik

1. **Angka hanya dari berkas data_ref**, dihitung backend. Model tidak boleh menyebut angka yang tidak berasal dari hasil hitung backend.
2. **`dictionary_validated=false`** (disertai notice `DICTIONARY_NOT_VALIDATED`): arti kolom nilai belum terverifikasi. Tampilkan grafik hanya setelah pengguna mengonfirmasi, dan jangan menamai sumbu dengan arti tebakan.
3. **`quality_flags` wajib disampaikan**, tidak boleh diperbaiki (ADR-007):

   | `code` | Aturan | Arti |
   |---|---|---|
   | `suspect_zero_period` | QC-01 | Semua nilai satu periode = 0 (mungkin data belum diisi) |
   | `suspect_scale` | QC-02 | Skala periode ini tampak berbeda jauh (mis. persen tertulis sebagai pecahan) |
   | `suspect_outlier` | QC-02 | Nilai periode ini menyimpang jauh dari periode lain |

   Tandai titik di grafik dan sebut di narasi ("data tahun X perlu dicek, kemungkinan …"). Jangan menghapus atau mengoreksi nilainya.
4. **`missing_periods` tidak kosong** (status `partial`): sebut bahwa tahun itu belum tersedia, lalu pakai tahun terakhir yang ada dan sebut tahunnya. Jangan mengisi celah dengan interpolasi.
5. **`DIFFERENT_DEFINITION`:** jangan menggabungkan dua dataset itu menjadi satu seri atau tren.
6. **`STALE_DATA` / `served_from=stale_cache`:** sebut bahwa data belum diperbarui. Golden set F-03 melarang ekspor PNG/PDF/PPTX dari data usang.
7. **Sebut sumber:** nama dataset, OPD (`provenance.opd`), `data_updated_at`, dan `source_url`. Bila `officialness.status` = `draf`, sebut bahwa data belum resmi.
8. **`truncated=true`:** respons dipangkas, tetapi berkas data_ref tetap lengkap. Bila perlu, minta pengguna mempersempit pertanyaan.

## 6. Pertanyaan terbuka (untuk Pak Ridho dan lead technical)

1. **ADR-011:** siapa dan kapan backend Citya diubah untuk mengambil `data_ref`, menghitung angka atau titik grafik, dan meneruskannya ke `render_chart`? Tanpa ini Citya tidak bisa menyebut angka dari MCP.
2. **`outputSchema`:** apakah klien MCP di Citya (versi AI SDK yang dipakai) meneruskan `outputSchema` tool ke konteks model? Bila ya, MCP disetel `MCP_EXPOSE_OUTPUT_SCHEMA=false`.
3. **`num_ctx` model:** berapa konteks model yang dipakai? Nilai ini menentukan `MCP_MAX_RESPONSE_TOKENS` (awal 2.000).
4. **`render_chart`:** perubahan apa yang dibutuhkan agar grafik menerima titik dari backend, termasuk penanda `quality_flags` dan label "belum terverifikasi"?
5. **Lokasi server dan jaringan:** apakah MCP satu mesin dengan backend Citya? Bila tidak, alamat internal mana untuk :8800 dan :8799 (`MCP_HTTP_ALLOWED_HOSTS`, `MCP_FILESERVER_PUBLIC_URL`), dan siapa yang mengatur firewall agar 8799 hanya bisa dijangkau dari backend?
6. **Token:** siapa pemegang `MCP_HTTP_AUTH_TOKEN` di sisi Citya, lewat saluran apa token dikirim, dan bagaimana rotasinya dikoordinasikan (server hanya menerima satu token)?
7. **`request_id`:** apakah backend akan mengisinya untuk penelusuran log lintas sistem?
8. **Fase 2:** kapan evaluasi golden set dengan model (Dok 5, target nol toleransi untuk angka karangan) dijadwalkan, dan di lingkungan mana?
