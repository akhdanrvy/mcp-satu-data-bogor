"""/healthz (Langkah 9): status tanpa data; portal maks 1 GET per 60 detik (cache health())."""

from __future__ import annotations

import asyncio
from pathlib import Path

import respx
from starlette.testclient import TestClient

from mcp_bogor.adapters.opendata_jabar import OpenDataJabarAdapter
from mcp_bogor.fileserver.app import create_app
from mcp_bogor.health import catalog_status, fileserver_health, server_health, server_version
from tests.fakes import FakeClock, install_portal_routes, make_settings
from tests.tool_support import SECRET


def test_catalog_status(catalog_db: Path, tmp_path: Path) -> None:
    assert catalog_status(tmp_path / "tidak-ada.db") == {
        "ready": False,
        "synced_at": None,
        "records": 0,
        "last_sync": None,
    }
    ready = catalog_status(catalog_db)
    assert ready["ready"] is True and ready["records"] == 751 and ready["synced_at"]
    assert ready["last_sync"]["status"] == "complete" and ready["last_sync"]["error_kind"] is None


def test_portal_health_never_called_without_deep(catalog_db: Path, tmp_path: Path) -> None:
    """KB-47 revisi: tanpa deep → nol GET ke portal; deep → health() dengan cache 60 dtk."""
    settings = make_settings(tmp_path, db_path=catalog_db)
    clock = FakeClock()

    async def run() -> list[tuple[int, dict]]:
        async with OpenDataJabarAdapter(
            settings, monotonic=clock.monotonic, sleep=clock.sleep
        ) as adapter:
            assert adapter.last_health() is None
            shallow = await server_health(settings, adapter)
            deep1 = await server_health(settings, adapter, deep=True)
            clock.now += 5
            shallow2 = await server_health(settings, adapter)  # hasil tersimpan, umur 5 dtk
            deep2 = await server_health(settings, adapter, deep=True)  # masih cache
            clock.now += 61
            deep3 = await server_health(settings, adapter, deep=True)  # cache habis: 1 GET
            return [shallow, deep1, shallow2, deep2, deep3]

    with respx.mock(assert_all_called=False) as router:
        install_portal_routes(router)
        results = asyncio.run(run())
        portal_calls = [c for c in router.calls if c.request.url.path.endswith("/datasets")]
    assert len(portal_calls) == 2  # hanya deep1 dan deep3
    shallow, deep1, shallow2, _, _ = results
    assert shallow[0] == 200 and shallow[1]["portal"] == {"checked": False}
    assert deep1[1]["portal"] == {"checked": True, "ok": True, "status": 200, "age_s": 0.0}
    assert shallow2[1]["portal"]["age_s"] == 5.0 and shallow2[1]["portal"]["ok"] is True
    assert deep1[1]["version"] == server_version() and deep1[1]["catalog"]["records"] == 751


def test_server_health_without_adapter_or_catalog(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    status, body = asyncio.run(server_health(settings, None))
    assert status == 503 and body["portal"] is None and body["catalog"]["ready"] is False


def test_fileserver_healthz(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, dataref_secret=SECRET)
    client = TestClient(create_app(settings))
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["service"] == "mcp-kota-bogor-fileserver"
    assert response.json()["cache_readable"] is True
    assert response.headers["cache-control"] == "no-store"
    assert fileserver_health(tmp_path / "tidak-ada")[0] == 503
