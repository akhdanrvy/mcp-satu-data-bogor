"""Tool get_dataset_data (Dok 4 bagian 5; RENCANA_LANGKAH8 bagian 1.3, 2, 3, 4).

Alur: validasi → katalog lokal (DATASET_NOT_FOUND tanpa portal) → kamus (cache/probe, sama
dengan info) → rencana kueri terhadap kamus (KB-39, KK-A1) → seri (KB-37):
- jalur lokal (bawaan): seri tanpa filter, kunci slug + sort, bila total baris ≤
  MCP_SERIES_LOCAL_MAX_ROWS; filter disaring lokal;
- jalur portal: di atas ambang, filter non-tahun dikirim sebagai `where`;
→ turunan lokal (saring, cakupan periode, QC atas seri tersaring, potong periode) → subset di
cache (retrieved_at = seri) → data_ref bertanda tangan → amplop + anggaran token (KB-38).

Respons tidak pernah memuat nilai measure (KT-03): hanya struktur, periode, tanda mutu,
preview kolom dimension, dan data_ref.
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from dataclasses import asdict
from typing import Any

from mcp_bogor.adapters.base import (
    InvalidSourceRequest,
    SourceNotFound,
    SourceRateLimited,
    SourceUnavailable,
)
from mcp_bogor.logging import get_logger, log_event
from mcp_bogor.models.envelope import ToolEnvelope, ok, partial
from mcp_bogor.models.errors import ErrorCode
from mcp_bogor.models.notices import Notice, NoticeCode
from mcp_bogor.models.params import YEAR_MAX, YEAR_MIN, DataParams
from mcp_bogor.models.results import DataResult
from mcp_bogor.services.budget import TruncatedReason, fit_to_budget, truncate_text
from mcp_bogor.services.catalog import CatalogDataset
from mcp_bogor.services.data import (
    SERIES_PER_PAGE,
    VARIANT_SERIES_LOCAL,
    VARIANT_SERIES_PORTAL,
    QueryPlan,
    QueryRejected,
    data_columns,
    data_ref_payload,
    derive,
    normalize_series,
    plan_query,
    preview_rows,
    response_columns,
)
from mcp_bogor.services.info import (
    InfoBundle,
    build_officialness,
    build_provenance,
    dictionary_notices,
)
from mcp_bogor.services.periods import PERIOD_COLUMN
from mcp_bogor.services.quality import findings_for_report
from mcp_bogor.storage.cache import CacheResult, CacheTooLarge, FetchedData, QueryKey
from mcp_bogor.tools.common import ToolDeps, ToolFailure, run_tool
from mcp_bogor.tools.info import FALLBACK_ERRORS, get_bundle, lookup_dataset

TOOL = "get_dataset_data"
LOGGER = get_logger(__name__)
MEANING_TRIMMED_CHARS = 120


class _SeriesTooLargeForLocal(Exception):
    """Halaman pertama menunjukkan total > MCP_SERIES_LOCAL_MAX_ROWS; pindah ke jalur portal."""


def _plain_where(plan: QueryPlan) -> dict[str, list[Any]]:
    return {k: list(v) for k, v in plan.portal_where.items()}


async def _fetch_series(
    deps: ToolDeps,
    dataset: CatalogDataset,
    bundle: InfoBundle,
    plan: QueryPlan,
    *,
    where: dict[str, list[Any]] | None,
    local_limit: int | None,
) -> FetchedData:
    """Ambil seluruh halaman seri dari portal, normalisasi, QC, dan bentuk isi berkas seri."""
    settings = deps.settings
    adapter = deps.require_adapter()
    raw = dataset.raw_dataset()
    first = await adapter.query_rows(
        raw, where=where or None, sort=plan.sort, page=1, per_page=SERIES_PER_PAGE
    )
    total = first.pagination.total_data
    if total is None:
        total = len(first.rows)
    if local_limit is not None and total > local_limit:
        raise _SeriesTooLargeForLocal
    if total > settings.cache_max_rows:
        raise CacheTooLarge(total, settings.cache_max_rows)
    rows = list(first.rows)
    pages = first.pagination.total_page or math.ceil(total / SERIES_PER_PAGE) or 1
    for page in range(2, pages + 1):
        more = await adapter.query_rows(
            raw, where=where or None, sort=plan.sort, page=page, per_page=SERIES_PER_PAGE
        )
        rows.extend(more.rows)

    dictionary = bundle.dictionary
    series = normalize_series(rows, dictionary)
    if series.parse_issues:
        log_event(LOGGER, "series_parse_issues", slug=dataset.slug, columns=series.parse_issues)
    columns = data_columns(dictionary)
    measures = [c.name for c in columns if c.role == "measure"]
    # Isi berkas seri = turunan tanpa filter lokal dan tanpa periode (dipakai langsung bila
    # permintaan juga tanpa filter dan tanpa periode).
    whole = QueryPlan(
        periods=None,
        keys={},
        display=dict(plan.display) if where else {},
        portal_where={},
        sort=plan.sort,
        time_dimension=plan.time_dimension,
    )
    derived = derive(
        series.rows, series.label_originals, whole, measures, qc_ratio=settings.qc_ratio
    )
    payload = data_ref_payload(
        slug=dataset.slug,
        columns=columns,
        derived=derived,
        provenance=_provenance_dict(deps, dataset, derived.available),
        officialness=build_officialness(dataset).model_dump(mode="json"),
        dictionary_validated=dictionary.dictionary_validated,
    )
    log_event(
        LOGGER,
        "series_fetched",
        slug=dataset.slug,
        rows=len(series.rows),
        pages=pages,
        local=local_limit is not None,
    )
    return FetchedData(payload, data_updated_at=dataset.data_updated_at)


def _provenance_dict(
    deps: ToolDeps, dataset: CatalogDataset, periods: list[int] | None
) -> dict[str, Any]:
    """Provenance berkas: retrieved_at diganti cache saat menulis, served_from dibuang (KB-34)."""
    provenance = build_provenance(
        dataset,
        base_url=deps.settings.source_base_url,
        retrieved_at=deps.now(),
        served_from="live",
        periods=periods,
        mask_text=False,  # berkas data_ref menyimpan teks asli (KB-46)
    )
    return provenance.model_dump(mode="json", exclude={"served_from"})


async def _get_series(
    deps: ToolDeps,
    dataset: CatalogDataset,
    bundle: InfoBundle,
    plan: QueryPlan,
    force_refresh: bool,
) -> tuple[CacheResult, bool]:
    """(hasil cache seri, jalur_lokal). Galat portal dipetakan ke ToolFailure."""
    cache = deps.require_data_cache()
    local_max = deps.settings.series_local_max_rows
    local = bundle.row_count is None or bundle.row_count <= local_max
    try:
        if local:
            key = QueryKey.build(dataset.slug, sort=plan.sort, variant=VARIANT_SERIES_LOCAL)
            try:
                result = await cache.get_or_fetch(
                    key,
                    lambda: _fetch_series(
                        deps, dataset, bundle, plan, where=None, local_limit=local_max
                    ),
                    force_refresh=force_refresh,
                    fallback_errors=FALLBACK_ERRORS,
                )
                return result, True
            except _SeriesTooLargeForLocal:
                log_event(LOGGER, "series_route_portal", slug=dataset.slug)
        where = _plain_where(plan)
        key = QueryKey.build(
            dataset.slug, filters=where, sort=plan.sort, variant=VARIANT_SERIES_PORTAL
        )
        result = await cache.get_or_fetch(
            key,
            lambda: _fetch_series(deps, dataset, bundle, plan, where=where, local_limit=None),
            force_refresh=force_refresh,
            fallback_errors=FALLBACK_ERRORS,
        )
        return result, False
    except CacheTooLarge as exc:
        raise ToolFailure(ErrorCode.RESPONSE_TOO_LARGE, limit=exc.limit) from exc
    except SourceRateLimited as exc:
        raise ToolFailure(ErrorCode.RATE_LIMITED, slug=dataset.slug) from exc
    except SourceUnavailable as exc:
        raise ToolFailure(ErrorCode.SOURCE_UNAVAILABLE, slug=dataset.slug) from exc
    except SourceNotFound as exc:
        log_event(LOGGER, "catalog_mismatch", slug=dataset.slug)
        raise ToolFailure(ErrorCode.DATASET_NOT_FOUND, slug=dataset.slug) from exc
    except InvalidSourceRequest as exc:
        log_event(LOGGER, "source_rejected", slug=dataset.slug)
        raise ToolFailure(ErrorCode.INVALID_PARAM, param="filters") from exc


def _notices(
    bundle: InfoBundle, plan: QueryPlan, series: CacheResult, row_count: int
) -> list[Notice]:
    notices = dictionary_notices(bundle.dictionary)
    if series.refresh_throttled:
        notices.append(Notice.from_template(NoticeCode.REFRESH_THROTTLED))
    if plan.periods is not None and not plan.time_dimension:
        notices.append(Notice.from_template(NoticeCode.NO_TIME_DIMENSION))
    if row_count == 0:
        notices.append(Notice.from_template(NoticeCode.EMPTY_RESULT))
    return notices


def _data_candidates(
    result: DataResult, notices: list[Notice]
) -> Iterator[tuple[TruncatedReason | None, DataResult, list[Notice]]]:
    """Utuh → preview 2 → preview 0 → arti kolom ≤ 120 → satu notice per kode (KB-38)."""
    yield None, result, notices
    for size in (2, 0):
        if len(result.preview) > size:
            result = result.model_copy(update={"preview": result.preview[:size]})
            yield TruncatedReason.PREVIEW, result, notices
    result = result.model_copy(
        update={
            "columns": [
                c.model_copy(update={"meaning": truncate_text(c.meaning, MEANING_TRIMMED_CHARS)})
                for c in result.columns
            ]
        }
    )
    yield TruncatedReason.MEANING, result, notices
    seen: set[NoticeCode] = set()
    unique = [n for n in notices if not (n.code in seen or seen.add(n.code))]
    yield TruncatedReason.NOTICES, result, unique


async def handle_data(params: DataParams, deps: ToolDeps) -> ToolEnvelope[DataResult]:
    settings = deps.settings
    dataset = lookup_dataset(deps, params.slug)
    bundle, _ = await get_bundle(deps, dataset)
    try:
        plan = plan_query(
            params, bundle, slug=dataset.slug, tingkat_penyajian=dataset.tingkat_penyajian
        )
    except QueryRejected as exc:
        raise ToolFailure(exc.code, **exc.args_) from exc
    cache = deps.require_data_cache()
    signer = deps.require_signer()

    series, local = await _get_series(deps, dataset, bundle, plan, params.force_refresh)
    series_payload = cache.read_payload(series.entry.cache_id)
    columns = data_columns(bundle.dictionary)
    measures = [c.name for c in columns if c.role == "measure"]
    derived = derive(
        series_payload["rows"],
        series_payload.get("label_originals") or {},
        plan,
        measures,
        qc_ratio=settings.qc_ratio,
    )
    coverage = derived.coverage
    if plan.time_dimension and plan.periods is not None and not coverage.periods_found:
        latest = max(derived.available) if derived.available else None
        if latest is not None and not YEAR_MIN <= latest <= YEAR_MAX:
            latest = None  # templat hanya menerima tahun 2000..2100 (KB-10)
        raise ToolFailure(
            ErrorCode.PERIOD_NOT_AVAILABLE,
            slug=dataset.slug,
            periods=list(plan.periods),
            latest=latest,
        )
    if derived.flags:
        for finding in findings_for_report(dataset.slug, derived.flags):
            log_event(LOGGER, "quality_finding", **asdict(finding))

    range_periods = coverage.periods_found if plan.periods is not None else derived.available
    if local and not plan.keys and plan.periods is None:
        entry = series.entry  # isi berkas seri = turunan tanpa filter dan tanpa periode
    else:
        payload = data_ref_payload(
            slug=dataset.slug,
            columns=columns,
            derived=derived,
            provenance=_provenance_dict(deps, dataset, range_periods),
            officialness=build_officialness(dataset).model_dump(mode="json"),
            dictionary_validated=bundle.dictionary.dictionary_validated,
        )
        subset_key = QueryKey.build(
            dataset.slug,
            periods=plan.periods or (),
            filters={k: [str(x) for x in v] for k, v in plan.display.items()},
            sort=plan.sort,
            variant=f"subset:{series.entry.cache_id}",
        )

        async def fetch_subset() -> FetchedData:
            return FetchedData(
                payload,
                data_updated_at=dataset.data_updated_at,
                retrieved_at=series.entry.retrieved_at,
            )

        entry = (await cache.get_or_fetch(subset_key, fetch_subset)).entry

    data_ref = signer.make_data_ref(entry.cache_id, entry.row_count, deps.now())
    provenance = build_provenance(
        dataset,
        base_url=settings.source_base_url,
        retrieved_at=series.entry.retrieved_at,
        served_from=series.served_from,
        periods=range_periods,
    )
    result = DataResult(
        data_ref=data_ref,
        columns=response_columns(columns),
        periods_requested=coverage.periods_requested,
        periods_found=coverage.periods_found,
        missing_periods=coverage.missing_periods,
        time_dimension=coverage.time_dimension,
        preview=preview_rows(derived.rows, columns, settings.preview_rows),
        filters_applied=derived.filters_applied
        if plan.time_dimension
        else {k: v for k, v in derived.filters_applied.items() if k != PERIOD_COLUMN},
        dictionary_validated=bundle.dictionary.dictionary_validated,
    )
    notices = _notices(bundle, plan, series, entry.row_count)
    stale = series.served_from == "stale_cache"
    some_missing = bool(coverage.missing_periods) and bool(coverage.periods_found)
    officialness = build_officialness(dataset)

    def envelopes() -> Iterator[tuple[TruncatedReason | None, ToolEnvelope[DataResult]]]:
        for reason, candidate, kept in _data_candidates(result, notices):
            common: dict[str, Any] = {
                "request_id": params.request_id,
                "provenance": provenance,
                "officialness": officialness,
                "quality_flags": derived.flags,
                "notices": kept,
            }
            if reason is None and not stale and not some_missing:
                yield reason, ok(candidate, **common)
            else:
                yield (
                    reason,
                    partial(
                        candidate,
                        truncated_reason=reason.value if reason else None,
                        stale=stale,
                        **common,
                    ),
                )

    chosen = fit_to_budget(
        envelopes(), settings.max_response_tokens, lambda env: env.model_dump(mode="json")
    )
    if chosen is None:
        raise ToolFailure(ErrorCode.RESPONSE_TOO_LARGE, limit=settings.max_response_tokens)
    return chosen[1]


async def get_dataset_data(deps: ToolDeps, raw_args: dict[str, object]) -> dict[str, object]:
    return await run_tool(TOOL, deps, raw_args, DataParams, handle_data, DataResult)
