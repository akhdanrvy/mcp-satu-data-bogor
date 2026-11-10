"""UT-04 (KS-01..05) dan UT-12 parsial (username tidak tersalin)."""

from __future__ import annotations

import copy
import json
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from mcp_bogor.config import WIB
from mcp_bogor.models import Provenance
from mcp_bogor.services.freshness import Freshness, compute_freshness, is_stale

FIXTURE = Path(__file__).parents[1] / "fixtures" / "synthetic" / "history_draft.json"
SCENARIOS: dict[str, Any] = {
    k: v for k, v in json.loads(FIXTURE.read_text(encoding="utf-8")).items() if k[0] != "_"
}


def _as_json(f: Freshness) -> dict[str, Any]:
    out = asdict(f)
    if f.data_updated_at is not None:
        out["data_updated_at"] = f.data_updated_at.isoformat(timespec="seconds")
    return out


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_ut04_scenarios(name: str) -> None:
    case = SCENARIOS[name]
    result = compute_freshness(case["history_draft"], case["mdate"])
    assert _as_json(result) == case["expected"]
    assert result.data_updated_at is not None and result.data_updated_at.tzinfo is WIB


def test_scenarios_cover_ks01_to_ks03() -> None:
    assert set(SCENARIOS) == {"one_approve", "many_approves", "no_approve", "mdate_after_approve"}


def test_privacy_username_never_copied() -> None:
    for case in SCENARIOS.values():
        original = copy.deepcopy(case["history_draft"])
        result = compute_freshness(case["history_draft"], case["mdate"])
        text = repr(result) + json.dumps(_as_json(result))
        assert "rahasia" not in text and "username" not in text
        assert case["history_draft"] == original  # input tidak diubah


def test_freshness_fits_provenance() -> None:
    f = compute_freshness(SCENARIOS["mdate_after_approve"]["history_draft"], "2026-03-20T09:00:00")
    prov = Provenance(
        source_url="https://opendata.kotabogor.go.id/dataset/x",
        data_updated_at=f.data_updated_at,
        freshness_source=f.freshness_source,
        metadata_changed_after_approve=f.metadata_changed_after_approve,
        change_notes=f.change_notes,
        retrieved_at=datetime(2026, 11, 6, 10, 0, tzinfo=WIB),
        served_from="live",
    )
    assert prov.model_dump(mode="json")["data_updated_at"] == "2026-03-11T13:20:00+07:00"


def test_mdate_exactly_one_day_after_is_not_flagged() -> None:
    history = [{"type": "approve", "cdate": "2026-03-11T13:20:00+07:07"}]
    assert not compute_freshness(history, "2026-03-12T13:20:00").metadata_changed_after_approve
    assert compute_freshness(history, "2026-03-12T13:20:01").metadata_changed_after_approve
    assert not compute_freshness(history, None).metadata_changed_after_approve


def test_nothing_known() -> None:
    assert compute_freshness(None, None) == Freshness(None, None, False, None)
    assert compute_freshness([], "-") == Freshness(None, None, False, None)


def test_bad_entries_are_skipped() -> None:
    history = [
        {"type": "approve", "cdate": "bukan-waktu"},
        {"type": "approve", "cdate": None},
        {"type": "approve", "cdate": 12345},
        {"type": None, "cdate": "2026-01-01T00:00:00+07:07", "notes": "  catatan  "},
        {"type": "APPROVE ", "cdate": "2025-01-01T00:00:00+07:07", "notes": 7},
    ]
    result = compute_freshness(history, None)
    assert result.data_updated_at == datetime(2025, 1, 1, tzinfo=WIB)
    assert result.freshness_source == "approve"
    assert result.change_notes == "catatan"


# ---------------------------------------------------------------- KS-05


def test_is_stale() -> None:
    t0 = datetime(2026, 11, 6, 10, 0, tzinfo=WIB)
    assert not is_stale(t0, t0 + timedelta(hours=23, minutes=59), 24)
    assert is_stale(t0, t0 + timedelta(hours=24), 24)
    assert is_stale(t0, t0 + timedelta(hours=2), 1.5)


def test_is_stale_rejects_bad_input() -> None:
    t0 = datetime(2026, 11, 6, 10, 0, tzinfo=WIB)
    with pytest.raises(ValueError, match="ttl_h"):
        is_stale(t0, t0, 0)
    with pytest.raises(ValueError, match="offset"):
        is_stale(datetime(2026, 11, 6), t0, 24)
