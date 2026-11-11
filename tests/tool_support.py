"""Bantuan tes tool (Langkah 8): katalog dari 751 record rekaman, ToolDeps."""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any

from mcp_bogor.config import WIB, Settings
from mcp_bogor.sync_worker import sync_once
from mcp_bogor.tools.common import ToolDeps
from tests.fakes import FakeSourceAdapter, make_settings

SYNC_AT = datetime(2026, 11, 10, 2, 0, tzinfo=WIB)


def synced_settings(tmp_path: Path, **overrides: Any) -> Settings:
    """Settings dengan katalog SQLite hasil sinkron FakeSourceAdapter (tanpa jaringan)."""
    settings = make_settings(tmp_path, **overrides)
    report = asyncio.run(sync_once(settings, adapter=FakeSourceAdapter(), now=lambda: SYNC_AT))
    assert report.status == "complete"
    return settings


class FixedClock:
    def __init__(self, now: datetime = SYNC_AT) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def deps_for(settings: Settings, now: datetime = SYNC_AT) -> ToolDeps:
    return ToolDeps(settings, now=FixedClock(now))


SECRET = "rahasia-uji-langkah-8c-" + "x" * 32


class ToolEnv:
    """Satu lingkungan tool: katalog bersama (baca saja), cache/override per tes, fake adapter."""

    def __init__(self, db_path: Path, tmp_path: Path, **overrides: Any) -> None:
        from datetime import timedelta

        values: dict[str, Any] = {
            "db_path": db_path,
            "cache_dir": tmp_path / "cache",
            "overrides_dir": tmp_path / "overrides",
            "dataref_secret": SECRET,
        }
        values.update(overrides)
        self.settings = make_settings(tmp_path, **values)
        self.adapter = FakeSourceAdapter()
        self.clock = FixedClock(SYNC_AT + timedelta(hours=1))
        self.deps = ToolDeps(self.settings, now=self.clock, adapter=self.adapter)

    def _run(self, runner: Any, args: dict[str, Any]) -> dict[str, Any]:
        return asyncio.run(runner(self.deps, args))

    def search(self, **args: Any) -> dict[str, Any]:
        from mcp_bogor.tools.search import search_datasets

        return self._run(search_datasets, args)

    def info(self, **args: Any) -> dict[str, Any]:
        from mcp_bogor.tools.info import get_dataset_info

        return self._run(get_dataset_info, args)

    def data(self, **args: Any) -> dict[str, Any]:
        from mcp_bogor.models import DataResult, ToolEnvelope
        from mcp_bogor.tools.data import get_dataset_data

        out = self._run(get_dataset_data, args)
        ToolEnvelope[DataResult].model_validate(out)  # KT-02: selalu amplop sah
        return out
