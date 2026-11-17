"""Dry-run smoke test: `uv run python -m tests.smoke`. Tidak pernah menghubungi portal.

Mencetak prasyarat, daftar permintaan per ST (path lengkap dari katalog lokal), total, aturan
akses, dan perintah eksekusi. Kode keluar: 0 siap, 1 prasyarat gagal, 2 konfigurasi tidak sah.
"""

from __future__ import annotations

import sys
from datetime import datetime

from pydantic import ValidationError

from mcp_bogor.config import WIB, Settings
from tests.smoke.harness import check_prerequisites, render_dry_run


def main(settings: Settings | None = None) -> int:
    if settings is None:
        try:
            settings = Settings()
        except ValidationError as exc:
            print(f"Konfigurasi tidak sah: {exc.error_count()} galat")
            return 2
    # Konsol Windows (cp1252) tidak bisa mencetak sebagian karakter; paksa UTF-8.
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(encoding="utf-8", errors="replace")
    pre = check_prerequisites(settings)
    print(render_dry_run(settings, pre, datetime.now(WIB).date()))
    return 0 if pre.ok else 1


if __name__ == "__main__":
    sys.exit(main())
