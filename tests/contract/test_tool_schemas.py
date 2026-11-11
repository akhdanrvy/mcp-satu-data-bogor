"""KT-01 (skema + deskripsi tool, snapshot) dan KT-05 (awalan nama), lewat klien MCP in-memory.

Snapshot di tests/contract/snapshots/. Perubahan skema atau teks deskripsi membuat tes gagal;
snapshot hanya ditulis ulang dengan `uv run pytest tests/contract --update-snapshots`, dan
hasilnya wajib direview serta disetujui Akhdan (deskripsi dibaca LLM; Dok 4 bagian 9).
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from mcp_bogor.config import Settings
from mcp_bogor.models import SearchResult, ToolEnvelope
from mcp_bogor.server import build_server
from mcp_bogor.tools.common import ToolDeps
from mcp_bogor.tools.descriptions import BASE_NAMES, descriptions
from tests.fakes import make_settings
from tests.tool_support import deps_for, synced_settings

SNAPSHOTS = Path(__file__).parent / "snapshots"
REGISTERED_8A = ["search_datasets"]  # 8b: get_dataset_info, 8c: get_dataset_data


def check_snapshot(request: pytest.FixtureRequest, name: str, data: Any) -> None:
    path = SNAPSHOTS / name
    text = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if request.config.getoption("--update-snapshots"):
        SNAPSHOTS.mkdir(exist_ok=True)
        path.write_text(text, encoding="utf-8")
    assert path.is_file(), f"snapshot {name} belum ada; jalankan --update-snapshots"
    assert json.loads(path.read_text(encoding="utf-8")) == data, (
        f"{name} berubah; bila disengaja, perbarui dengan --update-snapshots dan minta review"
    )


async def _with_client(deps: ToolDeps, action: Any) -> Any:
    async with create_connected_server_and_client_session(build_server(deps)) as client:
        return await action(client)


def _list_tools(settings: Settings) -> list[Any]:
    async def action(client: Any) -> Any:
        return (await client.list_tools()).tools

    return asyncio.run(_with_client(ToolDeps(settings), action))


@pytest.fixture(scope="module")
def catalog_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("katalog-mcp")
    synced_settings(path)
    return path


# ---------------------------------------------------------------- KT-01


def test_kt01_tool_schemas_snapshot(request: pytest.FixtureRequest, tmp_path: Path) -> None:
    tools = _list_tools(make_settings(tmp_path))
    data = {
        tool.name: {
            "description": tool.description,
            "inputSchema": tool.inputSchema,
            "outputSchema": tool.outputSchema,
            "annotations": tool.annotations.model_dump(exclude_none=True),
        }
        for tool in tools
    }
    check_snapshot(request, "tool_schemas.json", data)


def test_kt01_all_descriptions_snapshot(request: pytest.FixtureRequest) -> None:
    """Ketiga deskripsi dikunci sejak 8a, termasuk yang tool-nya didaftarkan di 8b/8c."""
    check_snapshot(request, "tool_descriptions.json", descriptions("satudata_"))


def test_input_schema_follows_settings(tmp_path: Path) -> None:
    (tool,) = _list_tools(make_settings(tmp_path, search_max_page_size=5))
    page_size = tool.inputSchema["properties"]["page_size"]
    assert (page_size["maximum"], page_size["default"]) == (5, 5)
    assert tool.inputSchema["required"] == ["query"]
    assert tool.inputSchema["additionalProperties"] is False
    assert all("description" in prop for prop in tool.inputSchema["properties"].values())
    request_id = tool.inputSchema["properties"]["request_id"]
    assert request_id["description"] == "Diisi oleh sistem pemanggil; biarkan kosong."
    assert tool.annotations.readOnlyHint is True and tool.annotations.openWorldHint is False


def test_descriptions_reference_prefixed_names_only() -> None:
    for prefix in ("satudata_", "uji_"):
        texts = descriptions(prefix)
        assert set(texts) == set(BASE_NAMES)
        joined = "\n".join(texts.values())
        bare = re.findall(
            rf"(?<!{prefix})\b(search_datasets|get_dataset_info|get_dataset_data)", joined
        )
        assert bare == [], prefix
        assert f"{prefix}get_dataset_info" in texts["search_datasets"]
        assert f"{prefix}search_datasets" in texts["get_dataset_info"]
        assert "{" not in texts["search_datasets"] and "}" not in texts["get_dataset_info"]


def test_descriptions_carry_agreed_rules() -> None:
    texts = descriptions("satudata_")
    assert "bukan dari ingatanmu" in texts["search_datasets"]
    assert "dari dokumen" not in texts["search_datasets"]
    assert (
        '"PAD"' in texts["search_datasets"]
        and "pendapatan asli daerah" not in texts["search_datasets"]
    )
    assert "coba sekali lagi dengan kata yang lebih umum atau sinonim" in texts["search_datasets"]
    data = texts["get_dataset_data"]
    assert "TIDAK berisi angka" in data and "data_ref" in data
    assert "celah dengan catatan" in data and "sebagai fakta" not in data
    assert "Persentase Kepala Keluarga Miskin" in data  # R-10
    assert (
        "Notice DICTIONARY_NOT_VALIDATED atau `dictionary_validated=false`: arti kolom nilai"
        in data
    )
    assert '{"hari_operasional": ["HARI KERJA"]}' in data
    for text in texts.values():
        assert not re.search(r"\d", text.replace("R-10", "")), "deskripsi tanpa angka"


# ---------------------------------------------------------------- KT-05


@pytest.mark.parametrize("prefix", ["satudata_", "uji_"])
def test_kt05_tool_names_use_prefix(tmp_path: Path, prefix: str) -> None:
    tools = _list_tools(make_settings(tmp_path, tool_prefix=prefix))
    assert [t.name for t in tools] == [prefix + base for base in REGISTERED_8A]


def test_default_prefix_is_satudata(tmp_path: Path) -> None:
    assert [t.name for t in _list_tools(make_settings(tmp_path))] == ["satudata_search_datasets"]


# ---------------------------------------------------------------- e2e lewat MCP


def test_e2e_search_returns_envelope_as_structured_content(catalog_root: Path) -> None:
    deps = deps_for(make_settings(catalog_root))

    async def action(client: Any) -> Any:
        ok_call = await client.call_tool(
            "satudata_search_datasets", {"query": "kemiskinan", "request_id": "rq-e2e"}
        )
        bad_call = await client.call_tool("satudata_search_datasets", {"query": "x"})
        return ok_call, bad_call

    ok_call, bad_call = asyncio.run(_with_client(deps, action))
    for call in (ok_call, bad_call):
        assert call.isError is False  # galat kontrak dikirim sebagai amplop, bukan galat MCP
        envelope = ToolEnvelope[SearchResult].model_validate(call.structuredContent)
        assert json.loads(call.content[0].text) == call.structuredContent
        assert envelope.contract_version == "0.1"
    assert ok_call.structuredContent["status"] == "ok"
    assert ok_call.structuredContent["request_id"] == "rq-e2e"
    assert bad_call.structuredContent["error"]["code"] == "INVALID_PARAM"


# ---------------------------------------------------------------- KB-42 (revisi): server low-level


def test_output_schema_can_be_hidden(tmp_path: Path) -> None:
    (shown,) = _list_tools(make_settings(tmp_path))
    (hidden,) = _list_tools(make_settings(tmp_path, expose_output_schema=False))
    assert shown.outputSchema is not None and hidden.outputSchema is None
    assert hidden.inputSchema == shown.inputSchema


def test_e2e_without_output_schema_still_returns_envelope(catalog_root: Path) -> None:
    deps = deps_for(make_settings(catalog_root, expose_output_schema=False))

    async def action(client: Any) -> Any:
        await client.list_tools()
        return await client.call_tool("satudata_search_datasets", {"query": "kemiskinan"})

    call = asyncio.run(_with_client(deps, action))
    assert call.isError is False and call.structuredContent["status"] == "ok"


def test_e2e_unknown_argument_is_invalid_param_envelope(catalog_root: Path) -> None:
    """Argumen asing tidak dibuang SDK; run_tool menolaknya sebagai amplop INVALID_PARAM."""
    deps = deps_for(make_settings(catalog_root))

    async def action(client: Any) -> Any:
        return await client.call_tool(
            "satudata_search_datasets", {"query": "kemiskinan", "where": {"tahun": [2025]}}
        )

    call = asyncio.run(_with_client(deps, action))
    assert call.isError is False
    error = call.structuredContent["error"]
    assert error["code"] == "INVALID_PARAM"
    assert error["message"] == "Parameter 'where' tidak valid. Argumen ini tidak dikenal."


def test_e2e_unknown_tool_is_mcp_error(tmp_path: Path) -> None:
    async def action(client: Any) -> Any:
        return await client.call_tool("search_datasets", {"query": "kemiskinan"})

    call = asyncio.run(_with_client(ToolDeps(make_settings(tmp_path)), action))
    assert call.isError is True and "tidak dikenal" in call.content[0].text


def test_streamable_http_transport_supported(catalog_root: Path) -> None:
    """Kesiapan Langkah 9: server low-level dilayani StreamableHTTPSessionManager (SDK 1.30)."""
    import contextlib

    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
    from starlette.applications import Starlette
    from starlette.routing import Mount
    from starlette.testclient import TestClient

    manager = StreamableHTTPSessionManager(
        app=build_server(deps_for(make_settings(catalog_root))),
        json_response=True,
        stateless=True,
    )

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette) -> Any:
        async with manager.run():
            yield

    app = Starlette(routes=[Mount("/mcp", app=manager.handle_request)], lifespan=lifespan)
    headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}

    def rpc(client: TestClient, method: str, params: dict[str, Any], rid: int) -> Any:
        body = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params}
        response = client.post("/mcp/", json=body, headers=headers)
        assert response.status_code == 200, response.text
        return response.json()["result"]

    with TestClient(app) as client:
        init = rpc(
            client,
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "uji", "version": "0"},
            },
            1,
        )
        assert init["serverInfo"]["name"] == "mcp-kota-bogor"
        tools = rpc(client, "tools/list", {}, 2)["tools"]
        assert [t["name"] for t in tools] == ["satudata_search_datasets"]
        call = rpc(
            client,
            "tools/call",
            {"name": "satudata_search_datasets", "arguments": {"query": "kemiskinan"}},
            3,
        )
        assert call["isError"] is False and call["structuredContent"]["status"] == "ok"
