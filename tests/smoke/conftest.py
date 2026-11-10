"""Smoke test ke portal asli (ST-01..05).

Semua tes di folder ini otomatis bertanda `smoke` (lihat tests/conftest.py) dan tidak
ikut `uv run pytest` bawaan. Jalankan hanya atas izin Akhdan:
    uv run pytest tests/smoke -m smoke
Batas: maks 60 permintaan per jalan, jeda 0,3 dtk.
"""
