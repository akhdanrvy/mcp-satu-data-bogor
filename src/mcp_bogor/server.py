"""Server MCP: tool berawalan MCP_TOOL_PREFIX (KT-05) di atas server low-level SDK resmi.

KB-42 (revisi): memakai `mcp.server.lowlevel.Server` dari SDK MCP resmi (ADR-009), dengan
handler publik `list_tools` dan `call_tool`, tanpa menyentuh atribut privat FastMCP.

- `inputSchema` = JSON Schema model parameter kontrak (`models/params`, extra="forbid").
- `outputSchema` = JSON Schema amplop `ToolEnvelope[<Result>]`, diiklankan hanya bila
  MCP_EXPOSE_OUTPUT_SCHEMA=true. Bila diiklankan, SDK memvalidasi `structuredContent`.
- `call_tool(validate_input=False)`: argumen mentah divalidasi model parameter di `run_tool`,
  sehingga pelanggaran apa pun, termasuk argumen asing, kembali sebagai amplop INVALID_PARAM
  (Dok 4 bagian 7), bukan galat validasi SDK dan bukan argumen yang dibuang diam-diam.
- Amplop dikembalikan sebagai `structuredContent` + teks JSON-nya; `isError` tetap false
  untuk galat kontrak (galatnya ada di amplop).

Langkah 8a: search_datasets. 8b/8c menambah info dan data di `_tool_specs`. Transport
(stdio, streamable-http :8800) di Langkah 9; server low-level ini dipakai langsung oleh
`StreamableHTTPSessionManager(app=...)` dan klien in-memory SDK.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from mcp import types
from mcp.server.lowlevel import Server
from pydantic import BaseModel

from mcp_bogor.config import Settings
from mcp_bogor.models.envelope import ToolEnvelope
from mcp_bogor.models.params import DEFAULT_PAGE_SIZE, SearchParams
from mcp_bogor.models.results import SearchResult
from mcp_bogor.tools.common import ToolDeps
from mcp_bogor.tools.descriptions import descriptions, tool_name
from mcp_bogor.tools.search import search_datasets

SERVER_NAME = "mcp-kota-bogor"
UNKNOWN_TOOL_MESSAGE = "Tool tidak dikenal oleh MCP Kota Bogor."

ToolRunner = Callable[[ToolDeps, dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class ToolSpec:
    base: str
    params_model: type[BaseModel]
    result_type: type[BaseModel]
    runner: ToolRunner
    annotations: types.ToolAnnotations


READ_ONLY_LOCAL = types.ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
)


def _tool_specs() -> list[ToolSpec]:
    return [
        ToolSpec("search_datasets", SearchParams, SearchResult, search_datasets, READ_ONLY_LOCAL),
    ]


def input_schema(model: type[BaseModel], settings: Settings) -> dict[str, Any]:
    schema = model.model_json_schema()
    if model is SearchParams:
        page_size = schema["properties"]["page_size"]
        page_size["maximum"] = settings.search_max_page_size
        page_size["default"] = min(DEFAULT_PAGE_SIZE, settings.search_max_page_size)
    return schema


def output_schema(result_type: type[BaseModel]) -> dict[str, Any]:
    return ToolEnvelope[result_type].model_json_schema(mode="serialization")  # type: ignore[valid-type]


def tool_definitions(settings: Settings) -> list[types.Tool]:
    prefix = settings.tool_prefix
    texts = descriptions(prefix)
    return [
        types.Tool(
            name=tool_name(prefix, spec.base),
            description=texts[spec.base],
            inputSchema=input_schema(spec.params_model, settings),
            outputSchema=output_schema(spec.result_type) if settings.expose_output_schema else None,
            annotations=spec.annotations,
        )
        for spec in _tool_specs()
    ]


def build_server(deps: ToolDeps) -> Server[Any, Any]:
    settings = deps.settings
    definitions = tool_definitions(settings)
    runners = {tool_name(settings.tool_prefix, spec.base): spec.runner for spec in _tool_specs()}
    server: Server[Any, Any] = Server(SERVER_NAME)

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return definitions

    @server.call_tool(validate_input=False)
    async def call_tool(name: str, arguments: dict[str, Any]) -> Any:
        runner = runners.get(name)
        if runner is None:
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=UNKNOWN_TOOL_MESSAGE)],
                isError=True,
            )
        return await runner(deps, dict(arguments or {}))

    return server
