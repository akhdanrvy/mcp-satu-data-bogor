"""Entry `python -m mcp_bogor.fileserver`: file server data_ref :8799 (proses terpisah).

Mode stdio server MCP tidak bisa melayani HTTP, jadi file server selalu proses sendiri.
Kode keluar 2 bila konfigurasi tidak sah (mis. MCP_DATAREF_SECRET, KB-06).
"""

from __future__ import annotations

import sys

from pydantic import ValidationError

from mcp_bogor.config import ConfigError, Settings
from mcp_bogor.fileserver.app import run
from mcp_bogor.logging import configure_logging, get_logger, log_warning

LOGGER = get_logger("mcp_bogor.fileserver")


def main() -> int:
    try:
        settings = Settings()
    except ValidationError as exc:
        print(f"Konfigurasi tidak sah: {exc.error_count()} galat", file=sys.stderr)
        return 2
    configure_logging(settings.log_level)
    try:
        run(settings)
    except ConfigError as exc:
        log_warning(LOGGER, "startup_refused", reason=str(exc))
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
