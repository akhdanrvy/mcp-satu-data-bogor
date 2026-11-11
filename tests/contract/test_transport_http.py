"""Langkah 9: streamable-http sungguhan (uvicorn, 127.0.0.1, port acak) + klien SDK.

Ketiga tool lewat HTTP dengan FakeSourceAdapter, data_ref diunduh lewat file server, token
bearer, proteksi DNS rebinding/Origin, /healthz, dan penolakan start saat konfigurasi tidak sah.
Hanya localhost; pemblokir jaringan conftest tetap aktif untuk host lain.
"""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
import pytest
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from starlette.testclient import TestClient

from mcp_bogor.config import ConfigError
from mcp_bogor.fileserver.app import create_app
from mcp_bogor.runtime import build_deps, build_http_app, main
from tests.fakes import FakeSourceAdapter
from tests.tool_support import SECRET, ToolEnv

KEMISKINAN = "tingkat-kemiskinan-di-kota-bogor-2"
TOKEN = "token-uji-bearer-langkah-9"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextmanager
def serve(app: Any, port: int) -> Iterator[str]:
    config = uvicorn.Config(
        app, host="127.0.0.1", port=port, log_config=None, access_log=False, lifespan="on"
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started:
        if time.monotonic() > deadline or not thread.is_alive():
            raise RuntimeError("uvicorn gagal start")
        time.sleep(0.02)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(15)


def http_env(catalog_db: Path, tmp_path: Path, **overrides: Any) -> tuple[ToolEnv, int]:
    port = free_port()
    env = ToolEnv(catalog_db, tmp_path, http_port=port, **overrides)
    return env, port


async def _session_calls(url: str, headers: dict[str, str]) -> dict[str, Any]:
    async with (
        httpx.AsyncClient(headers=headers, timeout=30) as http,
        streamable_http_client(f"{url}/mcp", http_client=http) as (read, write, _),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        tools = await session.list_tools()
        search = await session.call_tool("satudata_search_datasets", {"query": "kemiskinan"})
        info = await session.call_tool("satudata_get_dataset_info", {"slug": KEMISKINAN})
        data = await session.call_tool(
            "satudata_get_dataset_data", {"slug": KEMISKINAN, "periods": [2025, 2026]}
        )
        return {
            "tools": [t.name for t in tools.tools],
            "search": search.structuredContent,
            "info": info.structuredContent,
            "data": data.structuredContent,
        }


def test_three_tools_over_real_http(catalog_db: Path, tmp_path: Path) -> None:
    env, port = http_env(catalog_db, tmp_path)
    with serve(build_http_app(env.deps), port) as url:
        out = asyncio.run(_session_calls(url, {}))
    assert out["tools"] == [
        "satudata_search_datasets",
        "satudata_get_dataset_info",
        "satudata_get_dataset_data",
    ]
    assert out["search"]["status"] == "ok"
    assert out["info"]["status"] == "ok" and out["info"]["result"]["periods_available"] == [2025]
    data = out["data"]
    assert data["status"] == "partial" and data["result"]["missing_periods"] == [2026]
    assert env.adapter.calls == ["probe", "query_rows"]
    # data_ref dari server HTTP bisa diunduh file server (proses terpisah di produksi)
    parts = urlsplit(data["result"]["data_ref"]["url"])
    client = TestClient(create_app(env.settings, clock=lambda: env.clock.now.timestamp()))
    body = client.get(f"{parts.path}?{parts.query}").json()
    assert len(body["rows"]) == data["result"]["data_ref"]["row_count"] == 1


def _initialize_body() -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "uji", "version": "0"},
        },
    }


HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def test_bearer_token_required_when_configured(catalog_db: Path, tmp_path: Path) -> None:
    env, port = http_env(catalog_db, tmp_path, http_auth_token=TOKEN)
    with serve(build_http_app(env.deps), port) as url:
        with httpx.Client(base_url=url, timeout=30) as client:
            missing = client.post("/mcp", json=_initialize_body(), headers=HEADERS)
            wrong = client.post(
                "/mcp",
                json=_initialize_body(),
                headers={**HEADERS, "Authorization": "Bearer salah"},
            )
            good = client.post(
                "/mcp",
                json=_initialize_body(),
                headers={**HEADERS, "Authorization": f"Bearer {TOKEN}"},
            )
            health = client.get("/healthz")  # dikecualikan dari token
        out = asyncio.run(_session_calls(url, {"Authorization": f"Bearer {TOKEN}"}))
    assert missing.status_code == wrong.status_code == 401
    assert missing.headers["www-authenticate"] == "Bearer"
    assert missing.json() == {"error": "unauthorized"} and TOKEN not in missing.text
    assert good.status_code == 200 and good.json()["result"]["serverInfo"]["name"]
    assert health.status_code == 200
    assert out["search"]["status"] == "ok"


def test_dns_rebinding_and_origin_protection(catalog_db: Path, tmp_path: Path) -> None:
    env, port = http_env(catalog_db, tmp_path, http_allowed_hosts="mcp-bogor:8800")
    with (
        serve(build_http_app(env.deps), port) as url,
        httpx.Client(base_url=url, timeout=30) as client,
    ):
        evil_host = client.post(
            "/mcp", json=_initialize_body(), headers={**HEADERS, "Host": "evil.example"}
        )
        extra_host = client.post(
            "/mcp", json=_initialize_body(), headers={**HEADERS, "Host": "mcp-bogor:8800"}
        )
        evil_origin = client.post(
            "/mcp",
            json=_initialize_body(),
            headers={**HEADERS, "Origin": "http://evil.example"},
        )
        plain = client.post("/mcp", json=_initialize_body(), headers=HEADERS)
    assert evil_host.status_code == 421
    assert extra_host.status_code == 200  # MCP_HTTP_ALLOWED_HOSTS
    assert evil_origin.status_code == 403  # Origin kosong di konfigurasi = ditolak
    assert plain.status_code == 200
    assert "access-control-allow-origin" not in {k.lower() for k in plain.headers}


def test_healthz(catalog_db: Path, tmp_path: Path) -> None:
    env, port = http_env(catalog_db, tmp_path)
    with serve(build_http_app(env.deps), port) as url:
        response = httpx.get(f"{url}/healthz", timeout=30)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["service"] == "mcp-kota-bogor" and body["contract_version"] == "0.1"
    assert body["version"] and body["transport"] == "stdio"  # settings tes: transport bawaan
    assert body["catalog"]["ready"] is True and body["catalog"]["records"] == 751
    assert body["catalog"]["last_sync"]["status"] == "complete"
    assert body["portal"] == {"checked": False}  # tanpa deep: tidak pernah ke portal
    assert "health" not in env.adapter.calls
    assert "rows" not in json.dumps(body)


def test_healthz_without_catalog_is_503(tmp_path: Path) -> None:
    env, port = http_env(tmp_path / "tidak-ada.db", tmp_path)
    with serve(build_http_app(env.deps), port) as url:
        response = httpx.get(f"{url}/healthz", timeout=30)
    assert response.status_code == 503 and response.json()["catalog"]["ready"] is False


# ---------------------------------------------------------------- pemeriksaan saat start


def test_build_deps_strict_refuses_bad_config(catalog_db: Path, tmp_path: Path) -> None:
    no_secret = ToolEnv(catalog_db, tmp_path, dataref_secret="").settings
    with pytest.raises(ConfigError, match="MCP_DATAREF_SECRET"):
        build_deps(no_secret, strict=True, adapter=FakeSourceAdapter())
    placeholder = ToolEnv(catalog_db, tmp_path, user_agent="mcp-bogor/0.1 (+<kontak-tim>)").settings
    with pytest.raises(ConfigError, match="MCP_USER_AGENT"):
        build_deps(placeholder, strict=True)


def test_build_deps_stdio_degrades_with_warnings(
    catalog_db: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("INFO", logger="mcp_bogor.runtime")
    settings = ToolEnv(
        tmp_path / "belum.db",
        tmp_path,
        dataref_secret="",
        user_agent="mcp-bogor/0.1 (+<kontak-tim>)",
    ).settings
    deps, report = build_deps(settings, strict=False)
    assert set(report.problems) == {"user_agent", "dataref_secret"}
    assert report.catalog_ready is False
    assert deps.adapter is None and deps.data_cache is None and deps.signer is None
    events = [json.loads(r.getMessage())["event"] for r in caplog.records]
    assert events.count("startup_degraded") == 2 and "catalog_not_ready" in events
    assert all(r.levelname == "WARNING" for r in caplog.records)


def test_main_http_refuses_start_without_secret(
    settings_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MCP_TRANSPORT", "streamable-http")
    monkeypatch.setenv("MCP_USER_AGENT", "mcp-bogor-uji/0.1 (+tim-uji@example.invalid)")
    assert main() == 2


def test_main_invalid_settings_exit_2(settings_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_HTTP_PORT", "bukan-angka")
    assert main() == 2


def test_secret_is_accepted_for_http(catalog_db: Path, tmp_path: Path) -> None:
    settings = ToolEnv(catalog_db, tmp_path, dataref_secret=SECRET).settings
    deps, report = build_deps(settings, strict=True, adapter=FakeSourceAdapter())
    assert report.problems == {} and deps.signer is not None and deps.data_cache is not None


def test_fileserver_main_refuses_bad_config(
    settings_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mcp_bogor.fileserver.__main__ import main as fileserver_main

    assert fileserver_main() == 2  # MCP_DATAREF_SECRET kosong (KB-06)
    monkeypatch.setenv("MCP_FILESERVER_PORT", "bukan-angka")
    assert fileserver_main() == 2


def test_close_deps_closes_adapter(catalog_db: Path, tmp_path: Path) -> None:
    from mcp_bogor.runtime import close_deps

    closed: list[bool] = []

    class Closable(FakeSourceAdapter):
        async def aclose(self) -> None:
            closed.append(True)

    deps, _ = build_deps(ToolEnv(catalog_db, tmp_path).settings, strict=True, adapter=Closable())
    asyncio.run(close_deps(deps))
    assert closed == [True]


# ---------------------------------------------------------------- /healthz?deep=1 (KB-47 revisi)


def _healthz(url: str, path: str, token: str | None = None) -> httpx.Response:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return httpx.get(f"{url}{path}", headers=headers, timeout=30)


def test_healthz_without_token_never_calls_portal(catalog_db: Path, tmp_path: Path) -> None:
    env, port = http_env(catalog_db, tmp_path, http_auth_token=TOKEN)
    with serve(build_http_app(env.deps), port) as url:
        plain = _healthz(url, "/healthz")
        plain_with_token = _healthz(url, "/healthz", TOKEN)  # tanpa deep: tetap tanpa portal
    assert plain.status_code == plain_with_token.status_code == 200
    assert plain.json()["portal"] == {"checked": False}
    assert env.adapter.calls.count("health") == 0


def test_healthz_deep_with_valid_token_checks_portal_once(catalog_db: Path, tmp_path: Path) -> None:
    env, port = http_env(catalog_db, tmp_path, http_auth_token=TOKEN)
    with serve(build_http_app(env.deps), port) as url:
        deep = _healthz(url, "/healthz?deep=1", TOKEN)
        after = _healthz(url, "/healthz")  # hasil tersimpan dilaporkan tanpa panggilan baru
    assert deep.status_code == 200
    portal = deep.json()["portal"]
    assert portal["checked"] is True and portal["ok"] is True and portal["status"] == 200
    assert after.json()["portal"]["checked"] is True and after.json()["portal"]["age_s"] >= 0
    assert env.adapter.calls.count("health") == 1


def test_healthz_deep_with_wrong_token_is_401(catalog_db: Path, tmp_path: Path) -> None:
    env, port = http_env(catalog_db, tmp_path, http_auth_token=TOKEN)
    with serve(build_http_app(env.deps), port) as url:
        wrong = _healthz(url, "/healthz?deep=1", "salah")
        missing = _healthz(url, "/healthz?deep=1")
    assert wrong.status_code == missing.status_code == 401
    assert wrong.json() == {"error": "unauthorized"} and TOKEN not in wrong.text
    assert env.adapter.calls.count("health") == 0


def test_healthz_deep_ignored_without_configured_token(catalog_db: Path, tmp_path: Path) -> None:
    env, port = http_env(catalog_db, tmp_path)
    with serve(build_http_app(env.deps), port) as url:
        deep = _healthz(url, "/healthz?deep=1")
        deep_bearer = _healthz(url, "/healthz?deep=1", "apa-saja")
    assert deep.status_code == deep_bearer.status_code == 200
    assert deep.json()["portal"] == {"checked": False}
    assert env.adapter.calls.count("health") == 0
