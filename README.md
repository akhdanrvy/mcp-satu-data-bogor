# MCP Kota Bogor

Server MCP yang membaca portal Satu Data Kota Bogor (opendata.kotabogor.go.id) untuk X-Assistant Citya AI. Status: Fase 1, dalam pengembangan (lihat `docs/RENCANA_FASE1.md`).

## Pasang dan uji

```
uv sync
copy .env.example .env      # lalu isi MCP_DATAREF_SECRET
uv run pytest               # unit + kontrak, tanpa jaringan
uv run ruff check .
```

Dokumentasi lengkap (menjalankan server, konfigurasi, runbook) ditulis di Langkah 12.
