"""config.py: nilai bawaan sesuai Dok 4 bagian 9, path absolut, validasi secret."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from mcp_bogor.config import (
    CONTRACT_VERSION,
    WIB,
    ConfigError,
    Settings,
    describe_validation_error,
)


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


def test_inline_rows_fallback_true_rejected(
    settings_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """KT-03: tidak ada jalur baris data di respons tool; true ditolak, bukan diabaikan."""
    monkeypatch.setenv("MCP_INLINE_ROWS_FALLBACK", "true")
    with pytest.raises(ValidationError, match=r"tidak didukung di v0.1"):
        Settings()
    monkeypatch.setenv("MCP_INLINE_ROWS_FALLBACK", "false")
    assert Settings().inline_rows_fallback is False


def test_qc_ratio(settings_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """MCP_QC_RATIO (KB-18): bawaan 50, bisa diubah lewat env, harus > 1."""
    assert _settings().qc_ratio == 50
    monkeypatch.setenv("MCP_QC_RATIO", "80")
    assert _settings().qc_ratio == 80
    monkeypatch.setenv("MCP_QC_RATIO", "1")
    with pytest.raises(ValidationError):
        _settings()


def test_http_security_settings(settings_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """KB-47: host lokal selalu diizinkan; daftar tambahan dipisah koma; token rahasia."""
    s = _settings()
    assert s.allowed_hosts() == ["127.0.0.1:8800", "localhost:8800"]
    assert s.allowed_origins() == [] and s.http_auth_token.get_secret_value() == ""
    monkeypatch.setenv("MCP_HTTP_PORT", "9000")
    monkeypatch.setenv("MCP_HTTP_ALLOWED_HOSTS", " mcp-bogor:9000 , ,127.0.0.1:9000")
    monkeypatch.setenv("MCP_HTTP_ALLOWED_ORIGINS", "http://citya.internal")
    monkeypatch.setenv("MCP_HTTP_AUTH_TOKEN", "rahasia-token")
    s = _settings()
    assert s.allowed_hosts() == ["127.0.0.1:9000", "localhost:9000", "mcp-bogor:9000"]
    assert s.allowed_origins() == ["http://citya.internal"]
    assert "rahasia-token" not in repr(s)


# ---------------------------------------------------------------- KB-51 MCP_CLIENT_PROFILE


def test_client_profile_default_backend(settings_env: Path) -> None:
    assert _settings().client_profile == "backend"


@pytest.mark.parametrize("raw", ["sandbox", " Sandbox ", "SANDBOX"])
def test_client_profile_sandbox_accepted(settings_env: Path, raw: str) -> None:
    assert _settings(client_profile=raw).client_profile == "sandbox"


@pytest.mark.parametrize("raw", ["frontend", "", "llm", 1])
def test_client_profile_unknown_rejected(settings_env: Path, raw: object) -> None:
    with pytest.raises(ValidationError, match="MCP_CLIENT_PROFILE"):
        _settings(client_profile=raw)


def test_client_profile_from_env(settings_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_CLIENT_PROFILE", "sandbox")
    assert _settings().client_profile == "sandbox"
    monkeypatch.setenv("MCP_CLIENT_PROFILE", "bukan-profil")
    with pytest.raises(ValidationError) as info:
        _settings()
    text = describe_validation_error(info.value)
    assert "- MCP_CLIENT_PROFILE:" in text and "'backend'" in text and "'sandbox'" in text


def test_startup_error_lists_fields_without_secret_values(
    settings_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mcp_bogor.runtime import main

    monkeypatch.setenv("MCP_CLIENT_PROFILE", "frontend")
    monkeypatch.setenv("MCP_DATAREF_SECRET", "rahasia-yang-tidak-boleh-tercetak-" + "z" * 32)
    monkeypatch.setenv("MCP_REQUEST_DELAY_S", "0.01")
    import io
    from contextlib import redirect_stderr

    err = io.StringIO()
    with redirect_stderr(err):
        assert main() == 2
    text = err.getvalue()
    assert "Konfigurasi tidak sah: 2 galat" in text
    assert "MCP_CLIENT_PROFILE" in text and "MCP_REQUEST_DELAY_S" in text
    assert "rahasia-yang-tidak-boleh-tercetak" not in text and "0.01" not in text
