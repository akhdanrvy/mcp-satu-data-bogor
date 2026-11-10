"""Pengaman jaringan di conftest benar-benar memblokir akses keluar."""

import socket

import pytest

from tests.conftest import NetworkBlockedError


def test_outbound_connect_blocked() -> None:
    # 203.0.113.0/24 = TEST-NET-3; diblokir sebelum paket apa pun dikirim.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s, pytest.raises(NetworkBlockedError):
        s.connect(("203.0.113.1", 443))


def test_dns_lookup_blocked() -> None:
    with pytest.raises(NetworkBlockedError):
        socket.getaddrinfo("opendata.kotabogor.go.id", 443)


def test_localhost_allowed() -> None:
    socket.getaddrinfo("localhost", 8799)
