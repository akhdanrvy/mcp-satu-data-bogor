"""Tool search_datasets (Dok 4 bagian 3). Katalog lokal saja; tidak pernah ke portal.

Alur (RENCANA_LANGKAH8 bagian 1.1): validasi → FTS katalog baca-saja → NO_MATCH bila
total 0 → notice CATALOG_STALE bila katalog lama tidak disinkron → anggaran token
(item dipangkas dari belakang, page_size diturunkan ke jumlah item, status partial).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta

from mcp_bogor.models.envelope import ToolEnvelope, ok, partial
from mcp_bogor.models.errors import ErrorCode
from mcp_bogor.models.notices import Notice, NoticeCode
from mcp_bogor.models.params import SEARCH_MAX_PAGE_SIZE_KEY, SearchParams
from mcp_bogor.models.results import SearchResult
from mcp_bogor.services import catalog
from mcp_bogor.services.budget import TruncatedReason, fit_to_budget
from mcp_bogor.tools.common import ToolDeps, ToolFailure, run_tool

TOOL = "search_datasets"
# Katalog dianggap usang bila sinkron terakhir lebih tua dari kelipatan interval ini.
CATALOG_STALE_FACTOR = 2


def catalog_is_stale(synced_at: datetime | None, now: datetime, interval_h: float) -> bool:
    if synced_at is None:
        return True
    return now - synced_at > timedelta(hours=interval_h * CATALOG_STALE_FACTOR)


async def handle_search(params: SearchParams, deps: ToolDeps) -> ToolEnvelope[SearchResult]:
    conn = deps.open_catalog()
    try:
        found = catalog.search(conn, params)
    finally:
        conn.close()
    result = found.result
    if result.total == 0:
        raise ToolFailure(ErrorCode.NO_MATCH)

    settings = deps.settings
    notices = []
    if catalog_is_stale(result.catalog_synced_at, deps.now(), settings.catalog_sync_min_interval_h):
        notices.append(Notice.from_template(NoticeCode.CATALOG_STALE))
    request_id = params.request_id

    def candidates() -> Iterator[tuple[TruncatedReason | None, ToolEnvelope[SearchResult]]]:
        yield None, ok(result, request_id=request_id, notices=notices)
        for count in range(len(result.items) - 1, 0, -1):
            trimmed = result.model_copy(update={"items": result.items[:count], "page_size": count})
            yield (
                TruncatedReason.ITEMS,
                partial(
                    trimmed,
                    request_id=request_id,
                    truncated_reason=TruncatedReason.ITEMS.value,
                    notices=notices,
                ),
            )

    chosen = fit_to_budget(
        candidates(), settings.max_response_tokens, lambda env: env.model_dump(mode="json")
    )
    if chosen is None:
        raise ToolFailure(ErrorCode.RESPONSE_TOO_LARGE, limit=settings.max_response_tokens)
    return chosen[1]


async def search_datasets(deps: ToolDeps, raw_args: dict[str, object]) -> dict[str, object]:
    return await run_tool(
        TOOL,
        deps,
        raw_args,
        SearchParams,
        handle_search,
        SearchResult,
        context={SEARCH_MAX_PAGE_SIZE_KEY: deps.settings.search_max_page_size},
    )
