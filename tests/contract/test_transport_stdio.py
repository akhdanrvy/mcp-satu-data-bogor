"""Langkah 9: `python -m mcp_bogor` lewat stdio di subprocess sungguhan.

Mode stdio memakai stdout sebagai kanal protokol MCP: setiap baris stdout wajib pesan JSON-RPC
2.0, dan semua log (JSON) ke stderr. Subprocess tidak dilindungi pemblokir jaringan conftest,
jadi tes ini hanya memanggil search (katalog lokal) dan list_tools; portal tidak dihubungi.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from tests.fakes import TEST_USER_AGENT
from tests.tool_support import SECRET


def server_env(catalog_db: Path, tmp_path: Path, **extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("MCP_")}
    env.update(
        {
            "MCP_DB_PATH": str(catalog_db),
            "MCP_CACHE_DIR": str(tmp_path / "cache"),
            "MCP_OVERRIDES_DIR": str(tmp_path / "overrides"),
            "MCP_USER_AGENT": TEST_USER_AGENT,
            "MCP_DATAREF_SECRET": SECRET,
            "MCP_SOURCE_BASE_URL": "http://portal.test/api",  # tidak pernah dihubungi di tes ini
            "MCP_LOG_LEVEL": "INFO",
            "PYTHONIOENCODING": "utf-8",
        }
    )
    env.update(extra)
    return env


def test_stdio_sdk_client_lists_and_searches(catalog_db: Path, tmp_path: Path) -> None:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "mcp_bogor"],
        env=server_env(catalog_db, tmp_path),
        cwd=str(tmp_path),  # tanpa .env pengembang
    )
    errlog_path = tmp_path / "stderr.log"

    async def run() -> tuple[list[str], dict[str, Any]]:
        with errlog_path.open("w", encoding="utf-8") as errlog:
            async with (
                stdio_client(params, errlog=errlog) as (read, write),
                ClientSession(read, write) as session,
            ):
                await session.initialize()
                tools = await session.list_tools()
                call = await session.call_tool(
                    "satudata_search_datasets", {"query": "kemiskinan", "request_id": "rq-s"}
                )
                return [t.name for t in tools.tools], call.structuredContent

    names, search = asyncio.run(asyncio.wait_for(run(), timeout=60))
    assert names == [
        "satudata_search_datasets",
        "satudata_get_dataset_info",
        "satudata_get_dataset_data",
    ]
    assert search["status"] == "ok" and search["request_id"] == "rq-s"


def _rpc(rid: int | None, method: str, params: dict[str, Any] | None = None) -> bytes:
    message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if rid is not None:
        message["id"] = rid
    if params is not None:
        message["params"] = params
    return (json.dumps(message) + "\n").encode("utf-8")


def test_stdout_carries_only_protocol_messages(catalog_db: Path, tmp_path: Path) -> None:
    proc = subprocess.Popen(
        [sys.executable, "-m", "mcp_bogor"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=server_env(catalog_db, tmp_path, MCP_DATAREF_SECRET=""),  # stdio: tetap start
        cwd=str(tmp_path),
    )
    killer = threading.Timer(60, proc.kill)
    killer.start()
    try:
        assert proc.stdin is not None and proc.stdout is not None
        responses: dict[int, dict[str, Any]] = {}

        def ask(rid: int, method: str, params: dict[str, Any]) -> dict[str, Any]:
            assert proc.stdin is not None and proc.stdout is not None
            proc.stdin.write(_rpc(rid, method, params))
            proc.stdin.flush()
            line = proc.stdout.readline()
            message = json.loads(line)
            responses[rid] = message
            return message

        init = ask(
            1,
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "uji", "version": "0"},
            },
        )
        assert init["result"]["serverInfo"]["name"] == "mcp-kota-bogor"
        proc.stdin.write(_rpc(None, "notifications/initialized"))
        proc.stdin.flush()
        tools = ask(2, "tools/list", {})
        assert len(tools["result"]["tools"]) == 3
        search = ask(
            3, "tools/call", {"name": "satudata_search_datasets", "arguments": {"query": "pad"}}
        )
        assert search["result"]["structuredContent"]["status"] == "ok"
        proc.stdin.close()
        rest_out = proc.stdout.read()
        stderr = proc.stderr.read() if proc.stderr else b""
        proc.wait(30)
    finally:
        killer.cancel()
        if proc.poll() is None:
            proc.kill()

    # stdout: hanya JSON-RPC 2.0 (sisa setelah tiga jawaban juga harus kosong atau JSON-RPC)
    for line in rest_out.decode("utf-8").splitlines():
        if line.strip():
            assert json.loads(line)["jsonrpc"] == "2.0", line
    assert all(message["jsonrpc"] == "2.0" for message in responses.values())

    # stderr: log JSON satu baris per kejadian, termasuk peringatan secret dan tool_call
    events = []
    for line in stderr.decode("utf-8").splitlines():
        if line.strip():
            events.append(json.loads(line))
    names = [e.get("event") for e in events]
    assert "server_start" in names and "tool_call" in names
    degraded = [e for e in events if e.get("event") == "startup_degraded"]
    assert [e["component"] for e in degraded] == ["dataref_secret"]
    assert degraded[0]["level"] == "WARNING"
