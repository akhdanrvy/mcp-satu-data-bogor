"""config.py: nilai bawaan sesuai Dok 4 bagian 9, path absolut, validasi secret."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from mcp_bogor.config import CONTRACT_VERSION, WIB, ConfigError, Settings


def _settings(**kwargs: object) -> Settings:
    return Settings(_env_file=None, **kwargs)


def test_defaults_match_contract(settings_env: Path) -> None:
    s = _settings()
    assert s.source_base_url == "https://opendata.kotabogor.go.id/api"
    assert s.request_delay_s == 0.2
    assert s.http_timeout_s == 20
    assert s.max_retries == 5
    assert s.cache_ttl_h == 24
    assert s.transport == "stdio"
    assert (s.http_port, s.fileserver_port) == (8800, 8799)
    assert s.dataref_ttl_seconds == 900
    assert s.tool_prefix == "satudata_"
    assert s.max_response_tokens == 2000
    assert s.search_max_page_size == 20
    assert s.top_values == 10
    assert s.preview_rows == 5
    assert s.description_max_chars == 600
    assert s.inline_rows_fallback is False
    assert s.force_refresh_min_interval_s == 300
    # KB-32 / KB-33 (Langkah 7)
    assert (s.cache_stale_max_h, s.cache_max_mb, s.cache_max_rows) == (168, 500, 50_000)
    assert s.dataref_previous_secret() is None
    assert CONTRACT_VERSION == "0.1"


def test_wib_is_fixed_plus_seven() -> None:
    from datetime import datetime, timedelta

    assert datetime(2026, 11, 10, tzinfo=WIB).utcoffset() == timedelta(hours=7)


def test_paths_resolved_and_created(settings_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(settings_env)
    monkeypatch.setenv("MCP_DB_PATH", "./data/x.db")
    monkeypatch.setenv("MCP_CACHE_DIR", "./data/cache")
    s = _settings()
    assert s.db_path.is_absolute()
    assert s.db_path == (settings_env / "data" / "x.db").resolve()
    assert s.db_path.parent.is_dir()
    assert s.cache_dir.is_absolute() and s.cache_dir.is_dir()
    # KK-07: folder override di-resolve tetapi tidak dibuat otomatis.
    assert s.overrides_dir == (settings_env / "overrides").resolve()
    assert not s.overrides_dir.exists()


def test_env_overrides(settings_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_TRANSPORT", "streamable-http")
    monkeypatch.setenv("MCP_TOOL_PREFIX", "bogor_")
    monkeypatch.setenv("MCP_SOURCE_BASE_URL", "https://contoh.invalid/api/")
    s = _settings()
    assert s.transport == "streamable-http"
    assert s.tool_prefix == "bogor_"
    assert s.source_base_url == "https://contoh.invalid/api"


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("MCP_REQUEST_DELAY_S", "0.1"),  # AK-03: jeda di bawah 0,2 dtk
        ("MCP_MAX_RETRIES", "6"),  # AK-04: maks 5
        ("MCP_CATALOG_SYNC_MIN_INTERVAL_H", "12"),  # AK-06: maks 1x/24 jam
        ("MCP_PREVIEW_ROWS", "6"),  # FR-14: maks 5 baris
        ("MCP_TRANSPORT", "sse"),
    ],
)
def test_rejects_values_breaking_rules(
    settings_env: Path, monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv(name, value)
    with pytest.raises(ValidationError):
        _settings()


@pytest.mark.parametrize("secret", ["", "ganti-dengan-rahasia-acak", "pendek"])
def test_dataref_secret_rejected(
    settings_env: Path, monkeypatch: pytest.MonkeyPatch, secret: str
) -> None:
    monkeypatch.setenv("MCP_DATAREF_SECRET", secret)
    with pytest.raises(ConfigError):
        _settings().require_dataref_secret()


def test_dataref_secret_accepted_and_hidden(
    settings_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = "x" * 48
    monkeypatch.setenv("MCP_DATAREF_SECRET", secret)
    s = _settings()
    assert s.require_dataref_secret() == secret.encode()
    assert secret not in repr(s)


def test_qc_ratio(settings_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """MCP_QC_RATIO (KB-18): bawaan 50, bisa diubah lewat env, harus > 1."""
    assert _settings().qc_ratio == 50
    monkeypatch.setenv("MCP_QC_RATIO", "80")
    assert _settings().qc_ratio == 80
    monkeypatch.setenv("MCP_QC_RATIO", "1")
    with pytest.raises(ValidationError):
        _settings()
