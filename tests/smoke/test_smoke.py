"""Smoke test ST-01..05 ke portal asli (Dok 5 bagian 3; RENCANA_LANGKAH11).

HANYA atas izin Akhdan:  uv run pytest tests/smoke -m smoke
Dry-run dulu:           uv run python -m tests.smoke

Satu sesi untuk kelima ST (fixture modul): harness menjalankan ST-01..05 berurutan, menulis
docs/SMOKE_REPORT_<tanggal WIB>.md, lalu tiap tes menilai satu ST. Bila portal membalas 429,
sesi dihentikan, laporan tetap ditulis, dan pytest berhenti (pytest.exit).
"""

from __future__ import annotations

import pytest

from mcp_bogor.config import Settings
from tests.smoke.harness import (
    ReportRefused,
    SmokePrerequisiteError,
    SmokeResult,
    run_smoke_sync,
)


@pytest.fixture(scope="module")
def smoke() -> SmokeResult:
    settings = Settings()  # .env laptop: User-Agent asli, katalog var/mcp_bogor.db
    try:
        result = run_smoke_sync(settings)
    except SmokePrerequisiteError as exc:
        pytest.exit(f"Prasyarat smoke gagal, tidak ada permintaan dikirim: {exc}", returncode=2)
    except ReportRefused as exc:
        pytest.exit(f"Smoke selesai tetapi laporan ditolak pelindung isi: {exc}", returncode=1)
    print(f"\nLaporan: {result.report_path}\nSesudahnya: {result.check_command}")
    if result.aborted:
        pytest.exit(f"Smoke dihentikan: {result.aborted}. Laporan: {result.report_path}", 1)
    return result


def _assert_st(result: SmokeResult, key: str) -> None:
    st = result.sts[key]
    failed = [f"{c.name}: {c.detail}" if c.detail else c.name for c in st.checks if not c.ok]
    assert st.ran, f"{key} tidak dijalankan"
    assert not failed, f"{key} gagal:\n" + "\n".join(failed)


def test_st01_catalog(smoke: SmokeResult) -> None:
    _assert_st(smoke, "ST-01")


def test_st02_cards(smoke: SmokeResult) -> None:
    _assert_st(smoke, "ST-02")


def test_st03_data_ref(smoke: SmokeResult) -> None:
    _assert_st(smoke, "ST-03")


def test_st04_polite_access(smoke: SmokeResult) -> None:
    _assert_st(smoke, "ST-04")


def test_st05_performance(smoke: SmokeResult) -> None:
    _assert_st(smoke, "ST-05")
