"""Perintah pemeriksa kartu metrik: `python -m mcp_bogor.metric_cards --check` (KB-49).

Memeriksa metric_cards/ terhadap katalog lokal (MCP_DB_PATH) dan kamus di cache lokal
(MCP_CACHE_DIR) serta override (MCP_OVERRIDES_DIR). Tidak pernah memanggil portal.
Logika ada di `services.metric_cards`.

Kode keluar: 0 lulus (peringatan boleh, mis. "belum diperiksa"), 1 ada galat atau kartu tidak
sah, 2 galat konfigurasi / katalog belum ada, 3 ada kartu yang belum dicek (kamus belum di
cache; jalankan get_dataset_info untuk slug itu).
"""

from __future__ import annotations

import argparse
import sys

from mcp_bogor.config import ConfigError, Settings
from mcp_bogor.logging import configure_logging
from mcp_bogor.services.metric_cards import run_check
from mcp_bogor.storage import db


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pemeriksa kartu metrik MCP Kota Bogor")
    parser.add_argument(
        "--check", action="store_true", help="periksa kartu terhadap katalog dan cache lokal"
    )
    args = parser.parse_args(argv)
    if not args.check:
        parser.print_usage()
        return 2
    try:
        settings = Settings()
        configure_logging(settings.log_level)
        report = run_check(
            settings.metric_cards_dir, settings.db_path, settings.cache_dir, settings.overrides_dir
        )
    except (ConfigError, db.CatalogNotReady) as exc:
        print(f"DITOLAK: {exc}")
        return 2
    print(report.render())
    return report.exit_code


if __name__ == "__main__":
    sys.exit(main())
