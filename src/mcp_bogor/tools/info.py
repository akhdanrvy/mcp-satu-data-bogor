"""Tool get_dataset_info (Dok 4 bagian 4; RENCANA_LANGKAH8 bagian 1.2).

Alur: validasi → katalog lokal (slug tidak ada → DATASET_NOT_FOUND tanpa portal) →
override (KK-07) → cache kamus per `data_updated_at` + digest override (hit: 0 permintaan) →
miss: satu `probe` /bigdata (KK-O) di bawah kunci per slug (single-flight) → kamus + bundel
disimpan (KB-36) → InfoResult + provenance + notice → anggaran token (KB-38).

Portal gagal saat miss: bundel versi lama (≤ MCP_CACHE_STALE_MAX_H) disajikan sebagai
STALE_DATA (partial, stale_cache); tanpa itu → SOURCE_UNAVAILABLE / RATE_LIMITED.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta

from mcp_bogor.adapters.base import SourceNotFound, SourceRateLimited, SourceUnavailable
from mcp_bogor.logging import get_logger, log_event
from mcp_bogor.models.envelope import ServedFrom, ToolEnvelope, ok, partial
from mcp_bogor.models.errors import ErrorCode
from mcp_bogor.models.params import InfoParams
from mcp_bogor.models.results import InfoResult
from mcp_bogor.services import catalog
from mcp_bogor.services.budget import TruncatedReason, fit_to_budget
from mcp_bogor.services.catalog import CatalogDataset
from mcp_bogor.services.dictionary import DictionaryOverride, build_dictionary, parse_override
from mcp_bogor.services.info import (
    InfoBundle,
    build_bundle,
    build_info_result,
    build_officialness,
    build_provenance,
    dictionary_notices,
    info_candidates,
)
from mcp_bogor.storage.overrides import read_override
from mcp_bogor.tools.common import ToolDeps, ToolFailure, run_tool

TOOL = "get_dataset_info"
LOGGER = get_logger(__name__)

# Galat portal yang boleh dijawab dengan cache lama (KS-05, KB-32 (4)).
FALLBACK_ERRORS: tuple[type[Exception], ...] = (SourceUnavailable, SourceRateLimited)


def lookup_dataset(deps: ToolDeps, slug: str) -> CatalogDataset:
    conn = deps.open_catalog()
    try:
        dataset = catalog.get_dataset(conn, slug)
    finally:
        conn.close()
    if dataset is None:
        raise ToolFailure(ErrorCode.DATASET_NOT_FOUND, slug=slug)
    return dataset


def load_override(deps: ToolDeps, slug: str) -> DictionaryOverride | None:
    """Berkas rusak → OverrideFileError/OverrideError → INTERNAL (rinciannya hanya di log)."""
    data = read_override(deps.settings.overrides_dir, slug)
    return None if data is None else parse_override(data, slug)


def _cached_bundle(
    deps: ToolDeps, dataset: CatalogDataset, override_digest: str | None
) -> InfoBundle | None:
    raw = deps.require_dictionary_cache().get(
        dataset.slug, data_updated_at=dataset.data_updated_at, override_digest=override_digest
    )
    if raw is None:
        return None
    try:
        return InfoBundle.from_dict(raw)
    except ValueError:
        log_event(LOGGER, "dictionary_cache_invalid", slug=dataset.slug)
        return None


def _stale_bundle(deps: ToolDeps, dataset: CatalogDataset, now: datetime) -> InfoBundle | None:
    record = deps.require_dictionary_cache().get_any(dataset.slug)
    if record is None:
        return None
    try:
        bundle = InfoBundle.from_dict(record.payload)
    except ValueError:
        return None
    if now - bundle.retrieved_at > timedelta(hours=deps.settings.cache_stale_max_h):
        return None
    return bundle


def _failure_for(exc: Exception, slug: str) -> ToolFailure:
    code = (
        ErrorCode.RATE_LIMITED
        if isinstance(exc, SourceRateLimited)
        else ErrorCode.SOURCE_UNAVAILABLE
    )
    return ToolFailure(code, slug=slug)


async def get_bundle(deps: ToolDeps, dataset: CatalogDataset) -> tuple[InfoBundle, ServedFrom]:
    """Bundel kamus dari cache, atau dibuat dari satu probe (dipakai juga oleh get_dataset_data)."""
    override = load_override(deps, dataset.slug)
    digest = override.digest() if override is not None else None
    cached = _cached_bundle(deps, dataset, digest)
    if cached is not None:
        return cached, "cache"
    async with deps.locks.hold(f"kamus:{dataset.slug}"):
        cached = _cached_bundle(deps, dataset, digest)  # dibuat panggilan lain selagi menunggu
        if cached is not None:
            return cached, "cache"
        try:
            probe, page = await deps.require_adapter().probe(
                dataset.raw_dataset(), per_page=deps.settings.dictionary_sample_rows
            )
        except FALLBACK_ERRORS as exc:
            stale = _stale_bundle(deps, dataset, deps.now())
            log_event(
                LOGGER,
                "dictionary_source_failed",
                slug=dataset.slug,
                error=type(exc).__name__,
                stale=stale is not None,
            )
            if stale is not None:
                return stale, "stale_cache"
            raise _failure_for(exc, dataset.slug) from exc
        except SourceNotFound as exc:
            log_event(LOGGER, "catalog_mismatch", slug=dataset.slug)
            raise ToolFailure(ErrorCode.DATASET_NOT_FOUND, slug=dataset.slug) from exc
        dictionary = build_dictionary(
            description=dataset.deskripsi_html,
            probe=probe,
            sample_rows=page.rows,
            dataset_metadata={"Satuan Dataset": dataset.satuan},
            override=override,
        )
        now = deps.now()
        bundle = build_bundle(dictionary, probe, page.rows, now)
        deps.require_dictionary_cache().put(
            dataset.slug,
            bundle.to_dict(),
            data_updated_at=dataset.data_updated_at,
            override_digest=digest,
            built_at=now,
        )
        log_event(
            LOGGER,
            "dictionary_built",
            slug=dataset.slug,
            columns=len(dictionary.columns),
            validated=dictionary.dictionary_validated,
            orphans=len(dictionary.orphans),
        )
        return bundle, "live"


async def handle_info(params: InfoParams, deps: ToolDeps) -> ToolEnvelope[InfoResult]:
    settings = deps.settings
    dataset = lookup_dataset(deps, params.slug)
    bundle, served_from = await get_bundle(deps, dataset)
    result = build_info_result(
        dataset,
        bundle,
        description_max_chars=settings.description_max_chars,
        top_values=settings.top_values,
    )
    provenance = build_provenance(
        dataset,
        base_url=settings.source_base_url,
        retrieved_at=bundle.retrieved_at,
        served_from=served_from,
        periods=bundle.periods_available,
    )
    officialness = build_officialness(dataset)
    notices = dictionary_notices(bundle.dictionary)
    stale = served_from == "stale_cache"

    def envelopes() -> Iterator[tuple[TruncatedReason | None, ToolEnvelope[InfoResult]]]:
        for reason, candidate in info_candidates(result):
            common = {
                "request_id": params.request_id,
                "provenance": provenance,
                "officialness": officialness,
                "notices": notices,
            }
            if reason is None and not stale:
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


async def get_dataset_info(deps: ToolDeps, raw_args: dict[str, object]) -> dict[str, object]:
    return await run_tool(TOOL, deps, raw_args, InfoParams, handle_info, InfoResult)
