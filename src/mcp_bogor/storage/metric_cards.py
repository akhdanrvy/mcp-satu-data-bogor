"""Baca berkas kartu metrik `metric_cards/KM-xx.yaml` (Dok 4 bagian 8, KB-49).

Hanya I/O dan YAML. Skema dan pemeriksaan ada di `services.metric_cards`. Modul ini tidak
mengimpor services (arah dependensi tools → services → storage). Kartu tidak pernah
ditulis oleh kode; isinya dikurasi manusia.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

CARD_GLOB = "*.yaml"


class MetricCardFileError(ValueError):
    """Direktori kartu tidak ada, atau berkas kartu tidak terbaca / YAML-nya rusak."""


@dataclass(frozen=True)
class CardFile:
    name: str  # nama berkas tanpa .yaml; wajib sama dengan field id
    data: Any  # hasil yaml.safe_load, belum divalidasi


def read_card_files(directory: Path) -> list[CardFile]:
    """Semua berkas *.yaml di direktori kartu, urut nama. Galat baca → MetricCardFileError."""
    if not directory.is_dir():
        raise MetricCardFileError(f"direktori kartu metrik {directory} tidak ada")
    out: list[CardFile] = []
    for path in sorted(directory.glob(CARD_GLOB)):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError) as exc:
            raise MetricCardFileError(
                f"kartu {path.name}: tidak terbaca ({exc.__class__.__name__})"
            ) from exc
        except yaml.YAMLError as exc:
            raise MetricCardFileError(
                f"kartu {path.name}: YAML tidak sah ({exc.__class__.__name__})"
            ) from exc
        out.append(CardFile(name=path.stem, data=data))
    return out
