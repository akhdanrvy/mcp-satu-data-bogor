"""Konfigurasi pytest bersama.

Aturan 6 CLAUDE.md: tes unit dan kontrak tidak boleh menyentuh jaringan. Fixture
`_block_network` menolak koneksi soket dan resolusi DNS ke host selain localhost
untuk semua tes, kecuali tes bertanda `smoke` (semua tes di tests/smoke otomatis
ditandai).
"""

from __future__ import annotations

import logging
import socket
from collections.abc import Iterator
from pathlib import Path

import pytest

_SMOKE_DIR = Path(__file__).parent / "smoke"
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0", ""}


class NetworkBlockedError(RuntimeError):
    """Tes non-smoke mencoba mengakses jaringan."""


def _host_of(address: object) -> str | None:
    if isinstance(address, tuple) and address:
        return str(address[0])
    return None  # AF_UNIX atau bentuk lain: bukan jaringan


def _check(host: str | None) -> None:
    if host is not None and host not in _LOCAL_HOSTS:
        raise NetworkBlockedError(f"Akses jaringan ke {host!r} diblokir di tes non-smoke.")


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--update-snapshots",
        action="store_true",
        default=False,
        help="tulis ulang snapshot kontrak (KT-01); hasilnya wajib direview Akhdan",
    )


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if _SMOKE_DIR in Path(item.path).parents:
            item.add_marker(pytest.mark.smoke)


@pytest.fixture(autouse=True)
def _block_network(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    if request.node.get_closest_marker("smoke"):
        return

    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_getaddrinfo = socket.getaddrinfo

    def guarded_connect(self: socket.socket, address: object) -> None:
        _check(_host_of(address))
        return real_connect(self, address)

    def guarded_connect_ex(self: socket.socket, address: object) -> int:
        _check(_host_of(address))
        return real_connect_ex(self, address)

    def guarded_getaddrinfo(host: object, *args: object, **kwargs: object) -> list:
        _check(None if host is None else str(host))
        return real_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_connect_ex)
    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)


@pytest.fixture(autouse=True)
def _restore_root_logging() -> Iterator[None]:
    """CLI main() (sync_worker, metric_cards, runtime, fileserver) memanggil configure_logging,
    yang memasang handler JSON di root logger pada sys.stderr saat itu (milik capsys) dan
    mengubah level root. Tanpa pemulihan, tes berikutnya menulis ke stream yang sudah ditutup
    atau kehilangan log INFO, sehingga hasil tes bergantung pada urutan."""
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    for handler in list(root.handlers):
        if handler not in handlers:
            root.removeHandler(handler)
    root.setLevel(level)


@pytest.fixture
def settings_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Arahkan path penyimpanan ke tmp_path dan kosongkan env MCP_ dari luar.

    Juga pindah ke tmp_path agar berkas `.env` lokal pengembang (mis. MCP_USER_AGENT asli)
    tidak terbaca oleh Settings() dan tes tetap deterministik.
    """
    import os

    for key in list(os.environ):
        if key.startswith("MCP_"):
            monkeypatch.delenv(key)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MCP_DB_PATH", str(tmp_path / "var" / "test.db"))
    monkeypatch.setenv("MCP_CACHE_DIR", str(tmp_path / "var" / "cache"))
    return tmp_path


@pytest.fixture
def anyio_backend() -> str:
    """Tes async (pytest.mark.anyio) hanya memakai asyncio, sama seperti FastMCP."""
    return "asyncio"


@pytest.fixture(scope="session")
def catalog_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Katalog SQLite dari 751 record rekaman (sinkron FakeSourceAdapter), dipakai bersama."""
    from tests.tool_support import synced_settings

    return synced_settings(tmp_path_factory.mktemp("katalog-sesi")).db_path
