"""Baca berkas override kamus kolom `overrides/<slug>.yaml` (KK-07).

Hanya I/O dan YAML. Skema divalidasi `services.dictionary.parse_override`, dan kolomnya
dicek terhadap data asli oleh `services.dictionary.build_dictionary`. Modul ini tidak
mengimpor services (arah dependensi tools → services → storage).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

_SLUG = re.compile(r"^[a-z0-9_-]+$")


class OverrideFileError(ValueError):
    """Berkas override tidak terbaca atau YAML-nya rusak."""


def override_path(directory: Path, slug: str) -> Path:
    if not _SLUG.match(slug):
        raise OverrideFileError(f"slug override tidak sah: {slug!r}")
    return directory / f"{slug}.yaml"


def read_override(directory: Path, slug: str) -> Any | None:
    """Isi YAML override untuk slug (belum divalidasi), atau None bila berkasnya tidak ada."""
    path = override_path(directory, slug)
    if not path.is_file():
        return None
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise OverrideFileError(
            f"override {slug}: YAML tidak sah ({exc.__class__.__name__})"
        ) from exc
